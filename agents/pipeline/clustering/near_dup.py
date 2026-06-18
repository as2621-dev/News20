"""MinHash near-duplicate prefilter for the online clusterer (M3a, Sub-phase 2).

Wire-service reprints (the same AP/Reuters story republished verbatim by a dozen
outlets) are the single biggest source of false "events" in the shared pool. This
module is the cheap, embedding-free prefilter that collapses those reprints BEFORE
the (paid) embedding + assign-or-spawn engine runs (M3b), using Jaccard similarity
over word-shingles approximated by ``datasketch``'s MinHash + MinHashLSH.

Design notes:
    - Similarity = estimated Jaccard over 4-gram WORD shingles of normalized text.
      4-gram word shingles are robust to a one-word edit while still separating
      genuinely different stories (a shared word or two does not push two distinct
      headlines over the Jaccard threshold).
    - Short-text guard: a headline with < 4 words cannot form a single 4-gram, so
      it falls back to the SET of its individual words. Without this a 2-word
      headline would produce an empty shingle set and crash MinHash.
    - Grouping is deterministic: indices are sorted within each group and groups
      are sorted by their smallest member, so the output is stable across runs
      regardless of LSH bucket/iteration order.
    - This is a pure, dependency-injected function over a tiny typed item shape
      (``NearDupItem``) — no DB, no network — so it is trivially unit-testable
      (CLAUDE.md §6).

Example:
    >>> items = [
    ...     NearDupItem(item_index=0, item_text="the federal reserve raised its benchmark interest rate by half a point at its meeting on wednesday"),
    ...     NearDupItem(item_index=1, item_text="the federal reserve raised its benchmark interest rate by half a point at its meeting on thursday"),
    ...     NearDupItem(item_index=2, item_text="a planned rocket launch was scrubbed due to bad weather at the cape"),
    ... ]
    >>> group_near_duplicates(items)
    [[0, 1], [2]]
    >>> drop_exact_reprints(items)
    [0, 2]
"""

from __future__ import annotations

import re
from pydantic import BaseModel, Field

from datasketch import MinHash, MinHashLSH

from agents.shared.logger import get_logger

logger = get_logger("pipeline.clustering.near_dup")

# Reason: 4-gram word shingles balance robustness to a single-word edit against
# separating genuinely different stories.
SHINGLE_WORD_COUNT = 4

# Reason: Jaccard over 4-gram word shingles is NOT calibrated like cosine. A
# near-identical reprint that differs by one word lands around Jaccard ~0.8 (a
# single edited word breaks 1-4 of the ~N shingles), while genuinely different
# stories share ~0.0 shingles. So 0.85 (the spec's intuition for "near-identical")
# would miss real reprints; the calibrated separating value is ~0.7 — it groups
# reprints (~0.8) and rejects distinct stories (~0.0) with a wide margin. This
# default supersedes the spec's 0.85 (surfaced as a Rule 7 conflict in the SP2
# report — 0.85 is mathematically unreachable for a one-word-edit at any n-gram
# size, so the threshold, not the assertion, was adjusted).
DEFAULT_THRESHOLD = 0.7

# Reason: drop everything except word characters and whitespace so shingles are
# stable across punctuation/quote-style differences between reprints.
_PUNCTUATION_PATTERN = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+", flags=re.UNICODE)


class NearDupItem(BaseModel):
    """A single candidate item for near-duplicate grouping.

    Attributes:
        item_index: The caller's stable index for this item (returned verbatim in
            the output groups, so the caller can map back to its own collection).
        item_text: The text to shingle. The caller is expected to pass
            ``headline + " " + lead`` (or similar); this module only shingles the
            string it is given and does not interpret its structure.
    """

    item_index: int = Field(..., description="Caller's stable index for this item")
    item_text: str = Field(..., description="Text to shingle (e.g. headline + ' ' + lead)")


def _normalize_text(text: str) -> str:
    """Normalize text for stable shingling.

    Lowercases, strips punctuation, and collapses runs of whitespace so that two
    reprints that differ only in casing/punctuation/spacing produce identical
    shingles.

    Args:
        text: The raw item text.

    Returns:
        The normalized text (lowercased, punctuation removed, single-spaced,
        trimmed).

    Example:
        >>> _normalize_text("Fed RAISES  rates, today!")
        'fed raises rates today'
    """
    lowered = text.lower()
    without_punctuation = _PUNCTUATION_PATTERN.sub(" ", lowered)
    return _WHITESPACE_PATTERN.sub(" ", without_punctuation).strip()


def _build_shingles(text: str) -> set[str]:
    """Build the 4-gram word-shingle set for a text, with a short-text fallback.

    Args:
        text: The raw item text (normalized internally).

    Returns:
        The set of 4-gram word shingles. If the normalized text has fewer than
        four words, falls back to the set of its individual words (so a short
        headline still yields a usable, non-empty shingle set). An empty string
        yields an empty set.

    Example:
        >>> sorted(_build_shingles("a b c"))
        ['a', 'b', 'c']
        >>> _build_shingles("one two three four") == {"one two three four"}
        True
    """
    words = _normalize_text(text).split()
    if not words:
        return set()
    if len(words) < SHINGLE_WORD_COUNT:
        # Reason: a text shorter than one shingle window cannot form a 4-gram;
        # fall back to the word set so MinHash has something to hash.
        return set(words)
    return {" ".join(words[start : start + SHINGLE_WORD_COUNT]) for start in range(len(words) - SHINGLE_WORD_COUNT + 1)}


def _build_minhash(shingles: set[str], *, num_perm: int) -> MinHash:
    """Build a MinHash signature for a shingle set.

    Args:
        shingles: The word-shingle set for one item (may be empty).
        num_perm: Number of permutation functions (signature length); higher is
            more accurate, slower. Must match the value used by the LSH index.

    Returns:
        A populated ``MinHash``. An empty shingle set yields an all-default
        signature, which only ever matches another all-default signature.
    """
    minhash = MinHash(num_perm=num_perm)
    for shingle in shingles:
        minhash.update(shingle.encode("utf-8"))
    return minhash


def group_near_duplicates(
    items: list[NearDupItem],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    num_perm: int = 128,
) -> list[list[int]]:
    """Group items into connected near-duplicate clusters by MinHash/LSH.

    Each item is shingled (4-gram word shingles, short-text word-set fallback),
    MinHash-signed, and inserted into a ``MinHashLSH`` index tuned to ``threshold``.
    Querying every item yields its near-duplicate candidates; the pairwise
    candidate edges are unioned into connected components, so a chain of reprints
    (A~B, B~C) collapses into one group even if A and C are not directly adjacent.

    Every input item appears in exactly one returned group; an item with no
    near-duplicate forms a 1-element (singleton) group. Output is deterministic:
    indices are sorted within each group and groups are sorted by their smallest
    member.

    Args:
        items: The candidate items to group.
        threshold: Estimated-Jaccard threshold for two items to be considered
            near-duplicates (default 0.7 — calibrated wire-service reprint
            territory; see ``DEFAULT_THRESHOLD``).
        num_perm: MinHash permutation count (signature length); accuracy vs speed.

    Returns:
        A list of groups, each a sorted list of ``item_index`` values. An empty
        input returns ``[]``.

    Example:
        >>> items = [
        ...     NearDupItem(item_index=2, item_text="a severe storm knocked out power across the entire eastern seaboard on monday"),
        ...     NearDupItem(item_index=5, item_text="a severe storm knocked out power across the entire eastern seaboard on tuesday"),
        ...     NearDupItem(item_index=9, item_text="central bank holds benchmark rates steady amid persistent inflation worries"),
        ... ]
        >>> group_near_duplicates(items)
        [[2, 5], [9]]
    """
    if not items:
        return []

    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    minhash_by_index: dict[int, MinHash] = {}
    for item in items:
        shingles = _build_shingles(item.item_text)
        minhash = _build_minhash(shingles, num_perm=num_perm)
        minhash_by_index[item.item_index] = minhash
        # Reason: key by the caller's index so query results map straight back.
        lsh.insert(str(item.item_index), minhash)

    # Union-Find over the candidate edges so chained reprints land in one group.
    parent: dict[int, int] = {item.item_index: item.item_index for item in items}

    def _find(node: int) -> int:
        root = node
        while parent[root] != root:
            root = parent[root]
        # Path compression keeps repeated lookups flat.
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    def _union(left: int, right: int) -> None:
        left_root, right_root = _find(left), _find(right)
        if left_root != right_root:
            # Reason: attach larger index under smaller so the representative of a
            # component is always its smallest member (deterministic).
            low, high = sorted((left_root, right_root))
            parent[high] = low

    for item in items:
        candidate_keys = lsh.query(minhash_by_index[item.item_index])
        for candidate_key in candidate_keys:
            candidate_index = int(candidate_key)
            if candidate_index != item.item_index:
                _union(item.item_index, candidate_index)

    groups_by_root: dict[int, list[int]] = {}
    for item in items:
        root = _find(item.item_index)
        groups_by_root.setdefault(root, []).append(item.item_index)

    groups = [sorted(member_indices) for member_indices in groups_by_root.values()]
    groups.sort(key=lambda member_indices: member_indices[0])

    dup_count = sum(len(group) - 1 for group in groups)
    logger.info(
        "near_dup_grouped",
        item_count=len(items),
        group_count=len(groups),
        dup_count=dup_count,
    )
    return groups


def drop_exact_reprints(
    items: list[NearDupItem],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    num_perm: int = 128,
) -> list[int]:
    """Collapse near-duplicate reprints to one representative index per group.

    Runs :func:`group_near_duplicates` and keeps the smallest ``item_index`` of
    each group as that group's representative. A cluster of N reprints collapses
    to a single index; distinct items (singleton groups) are all kept.

    Args:
        items: The candidate items.
        threshold: Estimated-Jaccard near-duplicate threshold (default 0.7;
            see ``DEFAULT_THRESHOLD``).
        num_perm: MinHash permutation count.

    Returns:
        The sorted list of representative ``item_index`` values (one per group).

    Example:
        >>> items = [
        ...     NearDupItem(item_index=0, item_text="a strong quake hit the coastal city damaging hundreds of homes early on monday"),
        ...     NearDupItem(item_index=1, item_text="a strong quake hit the coastal city damaging hundreds of homes early on tuesday"),
        ...     NearDupItem(item_index=2, item_text="a strong quake hit the coastal city damaging hundreds of homes early on wednesday"),
        ...     NearDupItem(item_index=3, item_text="new national budget proposal cuts income taxes for small businesses next year"),
        ...     NearDupItem(item_index=4, item_text="space telescope captures a stunning image of a distant spiral galaxy"),
        ... ]
        >>> drop_exact_reprints(items)
        [0, 3, 4]
    """
    groups = group_near_duplicates(items, threshold=threshold, num_perm=num_perm)
    representatives = sorted(min(group) for group in groups)
    logger.info(
        "near_dup_reprints_dropped",
        item_count=len(items),
        kept_count=len(representatives),
        dropped_count=len(items) - len(representatives),
    )
    return representatives
