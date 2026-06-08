"""Prompt templates for the LLM candidate generator (Phase 5f SP1).

The generator (``scripts/seed_catalog/generate_candidates.py``) over-generates a
ranked list of real, well-known entities per ``(entry_type, archetype)`` cell and
writes them to ``data/{type}.{archetype}.json``. These are PROPOSALS only — the
existing resolvers (YouTube / iTunes / Wikipedia) verify each one and drop any
that does not resolve, so the prompt's job is to maximise the share of real,
correctly-identified entities (high handles/slugs accuracy → high resolve rate).

Convention: the big prompt strings live here as constants (not inline in the
generator), mirroring the repo's ``prompts.py`` pattern (agents/.../prompts.py).

Two layers compose every prompt:
  - ``CANDIDATE_SYSTEM_PROMPT``        — the shared identity/output contract.
  - ``AXIS_INSTRUCTIONS[entry_type]``  — the per-axis JSON schema + identity field.
  - ``ARCHETYPE_GUIDANCE[archetype]``  — the domain + culturally-diverse coverage.

``build_candidate_prompt`` stitches them into the final user prompt.
"""

from __future__ import annotations

# ── Shared system prompt (identity + anti-hallucination + output contract) ─────

CANDIDATE_SYSTEM_PROMPT = (
    "You are a meticulous media-catalog curator assembling a directory of REAL, "
    "well-known public sources for a news-digest app. You only ever propose "
    "entities you are confident actually exist, with their correct, canonical "
    "identifiers (exact YouTube @handles, exact X/Twitter @handles, exact "
    "Wikipedia article slugs, exact podcast titles as they appear in Apple "
    "Podcasts). A downstream system independently verifies every proposal against "
    "live APIs and silently drops anything that does not resolve, so a wrong "
    "handle or invented entity is wasted effort — favour precision over volume. "
    "You always respond with STRICT JSON only: a single JSON array, no prose, no "
    "markdown code fences, no trailing commentary."
)

# ── Per-axis instructions (JSON schema + identity field) ───────────────────────
# Each must mirror data/{type}.{archetype}.json exactly (the seeder's contract).

_CHANNELS_INSTRUCTIONS = (
    "Each array element is an object describing one YouTube CHANNEL:\n"
    '  {"youtube_handle": "<exact @handle WITHOUT the leading @>", '
    '"topic_tags": ["<key>", "<secondary>"]}\n'
    "- youtube_handle is the channel's exact handle as it appears in its URL "
    'youtube.com/@<handle> (e.g. "lexfridman", "mkbhd", "veritasium"). '
    "Do NOT include the @. Do NOT invent handles — if unsure of the exact "
    "handle, omit that channel.\n"
    "- Prefer the channel's primary handle over alternate/clip channels."
)

_PODCASTS_INSTRUCTIONS = (
    "Each array element is an object describing one PODCAST:\n"
    '  {"search_term": "<exact podcast title>", '
    '"topic_tags": ["<key>", "<secondary>"]}\n'
    "- search_term is the podcast's exact title as listed in Apple Podcasts "
    '(e.g. "Lex Fridman Podcast", "The Daily", "Acquired"). It is used as '
    "an iTunes search term, so accuracy of the title matters."
)

_X_INSTRUCTIONS = (
    "Each array element is an object describing one X (Twitter) ACCOUNT:\n"
    '  {"handle": "@<exact handle>", "source_name": "<person or org display '
    'name>", "topic_tags": ["<key>", "<secondary>"]}\n'
    '- handle is the exact X handle INCLUDING the leading @ (e.g. "@karpathy", '
    '"@sama", "@elonmusk"). Do NOT invent handles.\n'
    "- source_name is the human-readable name behind the account."
)

_PERSONALITIES_INSTRUCTIONS = (
    "Each array element is an object describing one notable PERSON:\n"
    '  {"display_name": "<full name>", "wikipedia_slug": "<exact Wikipedia '
    'article slug>", "aliases": ["<short name>"], '
    '"topic_tags": ["<key>", "<secondary>"]}\n'
    "- wikipedia_slug is the exact slug from the person's English Wikipedia URL "
    "en.wikipedia.org/wiki/<slug>, using underscores for spaces "
    '(e.g. "Andrej_Karpathy", "Fei-Fei_Li", "Sundar_Pichai"). The slug is '
    "used to fetch the person's photo, so it must be the exact article title.\n"
    "- aliases is an optional short list of common short names; omit or use [] "
    "when none apply.\n"
    "- Only include people who have an English Wikipedia article."
)

AXIS_INSTRUCTIONS: dict[str, str] = {
    "channels": _CHANNELS_INSTRUCTIONS,
    "podcasts": _PODCASTS_INSTRUCTIONS,
    "x": _X_INSTRUCTIONS,
    "personalities": _PERSONALITIES_INSTRUCTIONS,
}

# ── Per-archetype domain guidance (with explicit cultural diversity) ───────────
# Open Q1 decision: keep the 12 archetypes; achieve breadth via INTRA-archetype
# diversity. Every guidance string names the domain AND the diversity expectation.

ARCHETYPE_GUIDANCE: dict[str, str] = {
    "ai-frontier-tech": (
        "Artificial intelligence and frontier technology: AI researchers, "
        "ML/LLM labs and engineers, robotics, semiconductors, and deep-tech "
        "builders. Include voices from across the US, Europe, China, India, and "
        "elsewhere — not only Silicon Valley figures."
    ),
    "markets-macro": (
        "Markets and macroeconomics: investing, equities, bonds, central banks, "
        "macro analysts, and financial journalism. Include global markets "
        "coverage (US, Europe, Asia, emerging markets), not just Wall Street."
    ),
    "startup-operator": (
        "Startups and operating: founders, operators, venture capital, growth, "
        "and product. Include founders and ecosystems beyond Silicon Valley "
        "(Europe, India, Southeast Asia, Latin America, Africa)."
    ),
    "crypto-fintech": (
        "Crypto and fintech: blockchain, digital assets, DeFi, payments, and "
        "financial technology. Include builders and analysts from global hubs "
        "(US, Europe, Asia, Middle East), spanning both bull and skeptic views."
    ),
    "geopolitics-world": (
        "Geopolitics and world affairs: international relations, conflict, "
        "diplomacy, and regional politics. Include genuinely global coverage — "
        "Asia, Africa, the Middle East, Latin America, and Europe — and a range "
        "of analytical perspectives, not a single national viewpoint."
    ),
    "us-politics-policy": (
        "US politics and policy: elections, Congress, the courts, and domestic "
        "policy. Include a balanced spread across the political spectrum "
        "(left, center, right) and both reporters and analysts."
    ),
    "climate-energy": (
        "Climate and energy: climate science, the energy transition, renewables, "
        "policy, and cleantech. Include scientists, journalists, and energy "
        "analysts from multiple regions (not only US/EU-centric voices)."
    ),
    "sports-fan": (
        "Sports: athletes, analysts, leagues, and sports journalism. Be "
        "genuinely global — include football/soccer, cricket, basketball, "
        "tennis, Formula 1, and more (e.g. cricket and football for global "
        "audiences, not only US sports like the NFL/NBA/MLB)."
    ),
    "arts-culture": (
        "Arts and culture: film, music, literature, visual art, and cultural "
        "criticism. Be globally inclusive — include Hollywood AND Bollywood and "
        "other global cinema (Korean, Nigerian, European), plus world music and "
        "international literary voices."
    ),
    "creator-media": (
        "Creators and new media: YouTubers, podcasters, streamers, and the "
        "creator economy. Include top creators across regions and languages, "
        "spanning tech, entertainment, education, and commentary."
    ),
    "tech-generalist": (
        "General technology: consumer tech, gadgets, software, the internet, and "
        "broad tech journalism. Include reviewers, journalists, and explainers "
        "from multiple regions, covering both products and the industry."
    ),
    "balanced-generalist": (
        "A balanced general-interest mix for a reader with broad curiosity: the "
        "most widely-followed, high-trust voices spanning news, business, "
        "science, technology, and culture. Favour broad reach and credibility "
        "and keep the set globally and topically diverse."
    ),
}

# The 8 pinned topic-tag keys (must mirror ALLOWED_TOPIC_TAGS in seed_catalog).
TOPIC_TAG_KEYS: tuple[str, ...] = (
    "ai",
    "geopolitics",
    "business",
    "environment",
    "politics",
    "tech",
    "sport",
    "arts",
)


def build_candidate_prompt(entry_type: str, archetype: str, count: int) -> str:
    """Compose the user prompt for one ``(entry_type, archetype)`` candidate cell.

    Args:
        entry_type: One of ``channels`` / ``podcasts`` / ``x`` / ``personalities``.
        archetype: One of the 12 archetype slugs (domain guidance key).
        count: How many candidates to ask the model to over-generate.

    Returns:
        The fully-stitched user prompt string (system prompt is passed separately).

    Example:
        >>> prompt = build_candidate_prompt("channels", "ai-frontier-tech", 75)
        >>> "youtube_handle" in prompt
        True
    """
    axis_instructions = AXIS_INSTRUCTIONS[entry_type]
    domain_guidance = ARCHETYPE_GUIDANCE[archetype]
    topic_keys_csv = ", ".join(TOPIC_TAG_KEYS)
    return (
        f"Propose {count} of the most popular, well-known, REAL sources for the "
        f'"{archetype}" interest profile.\n\n'
        f"DOMAIN: {domain_guidance}\n\n"
        f"OUTPUT SHAPE:\n{axis_instructions}\n\n"
        "TOPIC TAGS RULE:\n"
        f"- topic_tags is a non-empty array. Its FIRST element MUST be exactly one "
        f"of these 8 keys: {topic_keys_csv}.\n"
        "- The first key is the single best-fitting category for the source. You "
        "may add 1-2 more keys from the same 8-key set as secondary tags.\n\n"
        "RANKING:\n"
        "- Order the array best-first: array position 0 is the single most "
        "popular / most influential source, descending from there.\n\n"
        "DIVERSITY:\n"
        "- Within this profile, deliberately include a culturally and "
        "geographically diverse set of sources where the domain allows it.\n\n"
        f"Return ONLY a JSON array of {count} objects. No prose. No code fences."
    )
