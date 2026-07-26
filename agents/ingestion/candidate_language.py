r"""The English-only admission gate — deterministic language filtering at ingestion.

blip is an English-language product. The first SHORTLIST_ONLY run (2026-07-20)
shortlisted **10 of 19** stories with non-English headlines — German, Albanian,
Chinese, Portuguese, Polish, Azerbaijani — each one a burned reel slot. This module
rejects them at GDELT admission with **zero LLM and zero API cost** (founder
credit-frugality rule): no language-ID service, no model call, just two signals.

**Signal 1 (authoritative): GDELT's own source-language stamp.** A GKG record that
GDELT machine-translated carries ``TranslationInfo`` = ``srclc:<iso639-3>;eng:<engine>``
(blank for documents already in English). A non-``eng`` code is a definitive reject —
the *body* we later extract from that URL is in the source language regardless of how
fluent the translated title reads. This is the half mirrored into ``_BATCH_SQL``
(:func:`build_language_filter_sql`), so those rows never even consume a per-interest
top-K slot.

**Signal 2 (backstop): the title itself.** GDELT's English feed is not perfectly
clean, and the stamp can be missing entirely, so the title is checked directly:

  1. **Non-Latin script → reject.** A letter outside the Latin blocks (CJK, Cyrillic,
     Arabic, Hebrew, Greek, the Azerbaijani schwa ``ə``…) cannot be an English story.
     Titles arrive both HTML-entity-encoded (``&#x767E;``) and as literal UTF-8, so
     they are entity-decoded first — the encoding itself is not the signal.
  2. **Two or more distinct foreign function words → reject.** Latin-script foreign
     headlines ("… wo das Klimagerät aktuell verfügbar ist") have no diacritic tell at
     all in some languages, so a tight, evidence-driven list of *function* words per
     observed language is the only deterministic catch. Two distinct hits are required
     so a single loan word or place name ("Dos Santos", "Sono Motors") never rejects.
  3. **Two or more distinct LOWERCASE accented words AND no English evidence →
     reject.** Polish ("programy zaczęły raportować") trips this where the word list
     does not, while "Beyoncé, Céline win Grammys" does not — accents confined to
     capitalized (proper-noun) positions are not evidence of foreign prose.

**Fail-open is the contract.** Ties admit. A missing stamp, an unparseable stamp, an
empty title, a headline with no function words in any language — all admit, and the
verdict records ``language_evidence = "none"`` so the caller can log how much of the
feed was admitted blind (Rule 12: visible, never silent). Over-rejection is the
expensive failure here: a dropped English story leaves no error anywhere, it just
shrinks the pool — which is why every rule above is thresholded or gated by positive
English evidence, and why an English headline naming Zürich still admits.

This module is **pure** (no logging, no I/O), mirroring ``interest_lexical``; the
caller (``gdelt_bigquery._rows_to_candidates``) owns the aggregate logging.

Example:
    >>> evaluate_language_admission("Fed holds rates steady", None).is_admitted
    True
    >>> evaluate_language_admission("Valve warnt: PC-Hardware k&#xF6;nnte noch "
    ...                             "teurer werden", None).rejection_reason
    'foreign_function_words'
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

# --- verdict vocabulary (shared with the tests and the caller's log fields) ------

REJECT_NON_ENGLISH_SOURCE_LANGUAGE = "non_english_source_language"
REJECT_NON_LATIN_SCRIPT = "non_latin_script"
REJECT_FOREIGN_FUNCTION_WORDS = "foreign_function_words"
REJECT_FOREIGN_DIACRITIC_DENSITY = "foreign_diacritic_density"

LANGUAGE_EVIDENCE_GDELT_STAMP = "gdelt_translation_info"
LANGUAGE_EVIDENCE_ENGLISH_TERMS = "english_function_words"
LANGUAGE_EVIDENCE_NONE = "none"

# Reason: GDELT writes ``srclc:fra;eng:Moses 2.1.1 / …`` (ISO 639-3, occasionally
# spaced/upper-cased across GKG generations). The SQL twin extracts with the SAME
# pattern — see build_language_filter_sql.
_SRCLC_PATTERN = r"srclc:\s*([a-z]{2,3})"
_SRCLC_RE = re.compile(_SRCLC_PATTERN, re.IGNORECASE)

# Reason: GDELT's ISO 639-3 code for English; the ISO 639-1 form is accepted too
# because the field's code set has not been stable across GKG generations.
_ENGLISH_SOURCE_CODES: frozenset[str] = frozenset({"eng", "en"})

# Reason: the last Latin codepoint (end of Latin Extended-B). Anything alphabetic
# ABOVE it is a different script for our purposes — CJK, Cyrillic, Greek, Arabic,
# Hebrew, Devanagari, Thai, and the IPA-block schwa ``ə`` (U+0259) that Azerbaijani
# uses. Everything a European Latin-alphabet language needs (ä ö ü ç ł ę ş ı ğ) sits
# at or below it, so accented ENGLISH text is never caught here.
_LATIN_MAX_CODEPOINT = 0x24F

# Reason: symmetric thresholds — one foreign function word can be a place name and one
# English function word can be a coincidence, so both sides need two distinct hits
# before they count. Asymmetry here would bias the gate toward over-rejection.
_MIN_DISTINCT_FUNCTION_WORDS = 2

# Reason: two distinct accented words is the density at which "accented English proper
# noun" stops being the likely explanation ("Zürich and Genève" is still English, so
# this rule additionally yields to positive English evidence — see the caller below).
_MIN_DISTINCT_ACCENTED_WORDS = 2

# Reason: high-frequency ENGLISH function words. Presence of two distinct ones is the
# positive evidence that gates the diacritic rule and marks an admission as evidenced
# rather than blind. Headline-ese often has none ("PlayStation 6 Release Date Leak"),
# which is exactly why their ABSENCE is never a rejection.
# Reason: these two literals stay grouped/wrapped by hand — the foreign list is read
# and extended PER LANGUAGE (its comments delimit the groups), which one word per line
# would bury under ~200 lines of noise.
# fmt: off
_ENGLISH_FUNCTION_WORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "from", "that", "this", "these", "those",
        "its", "his", "her", "their", "our", "your", "not", "more", "than",
        "but", "how", "why", "what", "who", "when", "where", "which", "after",
        "before", "over", "under", "into", "about", "against", "between",
        "during", "without", "while", "says", "said", "will", "would", "could",
        "should", "have", "has", "had", "are", "was", "were", "been", "being",
        "is", "be", "of", "to", "in", "on", "at", "by", "as", "up", "out",
        "off", "if", "all", "amid", "ahead", "still", "first", "new", "an", "a",
    }
)

# Reason: FOREIGN function words — the only deterministic tell for a Latin-script
# non-English headline. Evidence-driven and kept tight (the languages actually seen in
# the 2026-07-20 shortlist, plus their high-volume GDELT neighbours), with every word
# that is ALSO an English word or a common English-headline token deliberately
# excluded: "mit" (MIT), "dem" (Dem.), "von" (von Neumann), "des" (Des Moines), "los"/
# "las"/"del" (Los Angeles, Las Vegas), "con", "die", "war", "man", "also", "plus",
# "over", "pod", "duke", "van", "dan", "ve", "bu". Two distinct hits are required, so
# a lone survivor cannot reject an English story on its own. Extend on evidence only —
# the caller's rejection log is what surfaces the next needed entry.
_FOREIGN_FUNCTION_WORDS: frozenset[str] = frozenset(
    {
        # German
        "und", "für", "das", "nicht", "noch", "wird", "wurde", "werden",
        "könnte", "auch", "aber", "oder", "nach", "über", "zum", "zur", "sich",
        "eine", "einen", "ist", "sind", "wie", "wo", "durch", "gegen", "schon",
        "sehr", "dass", "weil", "beim", "vom", "wir", "mehr", "seine", "ihre",
        "diese", "wieder", "zwischen",
        # Spanish / Portuguese
        "dos", "das", "em", "sobre", "que", "não", "uma", "mais", "por",
        "para", "pelo", "pela", "seu", "sua", "como", "está", "são", "según",
        "también", "pero", "más", "este", "esta", "sus", "muy", "porque",
        "desde", "hasta", "donde", "cuando", "sem", "ainda",
        # French
        "les", "pour", "dans", "avec", "cette", "être", "sont", "aussi",
        "leur", "tout", "très", "chez", "ainsi", "selon", "depuis", "alors",
        "après", "qui", "sur", "nous", "vous", "encore", "était",
        # Italian
        "della", "delle", "degli", "anche", "questo", "nella", "dopo", "più",
        "perché", "tra", "gli", "alla", "dalla", "sulla", "essere",
        # Polish
        "nie", "się", "jest", "oraz", "przez", "które", "który", "tego", "tym",
        "jak", "dla", "już", "tylko", "może", "bardzo", "jako", "także",
        "przed", "gdzie", "będzie",
        # Albanian
        "më", "në", "dhe", "për", "është", "nga", "një", "kjo", "ose", "sipas",
        "janë", "edhe", "shumë",
        # Turkish / Azerbaijani
        "için", "olarak", "daha", "çok", "ile", "ancak", "sonra", "üzere",
        "olan", "bir", "və", "ilə", "göre", "kadar", "oldu",
        # Dutch
        "het", "een", "niet", "maar", "ook", "zijn", "werd", "wordt", "deze",
        "naar", "voor", "heeft", "veel", "tegen", "waar",
        # Indonesian / Malay
        "yang", "untuk", "dengan", "dari", "akan", "pada", "tidak", "adalah",
        "atau", "juga", "telah", "dalam",
    }
)
# fmt: on

# Reason: letters only (Unicode-aware), so "cnn.com" → ["cnn", "com"] and
# "PC-Hardware" → ["pc", "hardware"]. Digits/punctuation never carry a language.
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True)
class LanguageVerdict:
    """The admission decision for one candidate's title.

    Attributes:
        is_admitted: True when the candidate may enter the pool (fail-open default).
        rejection_reason: The ``REJECT_*`` constant that fired, else None. Reported in
            the caller's aggregate log so the mix of rejections is inspectable.
        language_evidence: Which positive signal backed an admission —
            :data:`LANGUAGE_EVIDENCE_GDELT_STAMP`,
            :data:`LANGUAGE_EVIDENCE_ENGLISH_TERMS`, or
            :data:`LANGUAGE_EVIDENCE_NONE` for a blind (fail-open) admission.
        source_language_code: The ISO code parsed from ``TranslationInfo``, else None
            (None means "no stamp", never "English" — the caller must not conflate).
    """

    is_admitted: bool
    rejection_reason: str | None
    language_evidence: str
    source_language_code: str | None


def extract_source_language_code(translation_info: str | None) -> str | None:
    """Parse the GDELT ``TranslationInfo`` source-language code (lowercased).

    The Python twin of the SQL's ``REGEXP_EXTRACT(LOWER(TranslationInfo), …)``.

    Args:
        translation_info: The raw GKG ``TranslationInfo`` value (may be None/empty —
            blank is GDELT's shape for a document that was already in English).

    Returns:
        The ISO 639 code (e.g. ``"deu"``), or None when there is no parseable stamp.

    Example:
        >>> extract_source_language_code("srclc:deu;eng:Moses 2.1.1")
        'deu'
        >>> extract_source_language_code("") is None
        True
    """
    if not translation_info:
        return None
    match = _SRCLC_RE.search(translation_info)
    return match.group(1).lower() if match else None


def build_language_filter_sql() -> str:
    """Return the English-only predicate for the ``raw`` CTE (pure string builder).

    Emits the SQL twin of the :func:`extract_source_language_code` rule:
    extract the ``srclc`` code, default a MISSING stamp to ``'eng'`` (fail open), and
    keep only English-sourced rows. Kept a pure builder — mirroring
    ``build_domain_filter_sql`` — so the predicate is unit-testable offline (BigQuery
    cannot run in tests) and is written in exactly one place.

    Note this is the *authoritative half only*: the title-side backstop rules
    (script / function words / diacritics) run in Python over the returned rows, so
    the SQL admits a strict SUPERSET of what :func:`evaluate_language_admission` does.
    Adding a rule here without adding it there would be a silent over-rejection.

    Returns:
        The SQL predicate fragment.

    Example:
        >>> build_language_filter_sql().startswith("AND IFNULL(REGEXP_EXTRACT")
        True
    """
    # Reason: the accepted-code list is generated from _ENGLISH_SOURCE_CODES (sorted
    # for a stable string) rather than written out — if the two ever diverged the SQL
    # would reject a code Python admits, i.e. silently drop English rows.
    accepted_codes = ", ".join(f"'{code}'" for code in sorted(_ENGLISH_SOURCE_CODES))
    return (
        "AND IFNULL(REGEXP_EXTRACT(LOWER(TranslationInfo), "
        f"r'{_SRCLC_PATTERN}'), 'eng') IN ({accepted_codes})"
    )


def _has_non_latin_letter(decoded_title: str) -> bool:
    """True when any alphabetic character sits outside the Latin blocks."""
    return any(
        character.isalpha() and ord(character) > _LATIN_MAX_CODEPOINT
        for character in decoded_title
    )


def evaluate_language_admission(
    candidate_title: str, translation_info: str | None = None
) -> LanguageVerdict:
    """Decide whether a GDELT candidate is admissible to an English-language feed.

    Rules are applied in confidence order — GDELT's stamp, then non-Latin script, then
    foreign function words, then accented-word density (which yields to positive
    English evidence). Anything else ADMITS: fail-open is deliberate, and the returned
    ``language_evidence`` tells the caller whether an admission was evidenced or blind.

    Args:
        candidate_title: The article title exactly as GDELT delivered it (HTML-entity
            encoded or literal UTF-8 — both are handled).
        translation_info: The raw GKG ``TranslationInfo`` value, when available.

    Returns:
        A :class:`LanguageVerdict`.

    Example:
        >>> evaluate_language_admission("S&#xE3;o Paulo airport reopens").is_admitted
        True
        >>> evaluate_language_admission("Rally dos chips de IA entra em xeque com "
        ...                             "temor sobre capex").rejection_reason
        'foreign_function_words'
    """
    source_language_code = extract_source_language_code(translation_info)
    if source_language_code and source_language_code not in _ENGLISH_SOURCE_CODES:
        return LanguageVerdict(
            is_admitted=False,
            rejection_reason=REJECT_NON_ENGLISH_SOURCE_LANGUAGE,
            language_evidence=LANGUAGE_EVIDENCE_GDELT_STAMP,
            source_language_code=source_language_code,
        )

    decoded_title = html.unescape(candidate_title or "")
    raw_words = _WORD_RE.findall(decoded_title)
    words = {word.lower() for word in raw_words}
    english_hits = words & _ENGLISH_FUNCTION_WORDS
    has_english_evidence = len(english_hits) >= _MIN_DISTINCT_FUNCTION_WORDS

    if source_language_code:
        language_evidence = LANGUAGE_EVIDENCE_GDELT_STAMP
    elif has_english_evidence:
        language_evidence = LANGUAGE_EVIDENCE_ENGLISH_TERMS
    else:
        language_evidence = LANGUAGE_EVIDENCE_NONE

    def _reject(reason: str) -> LanguageVerdict:
        return LanguageVerdict(
            is_admitted=False,
            rejection_reason=reason,
            language_evidence=language_evidence,
            source_language_code=source_language_code,
        )

    if _has_non_latin_letter(decoded_title):
        return _reject(REJECT_NON_LATIN_SCRIPT)

    foreign_hits = words & _FOREIGN_FUNCTION_WORDS
    if len(foreign_hits) >= _MIN_DISTINCT_FUNCTION_WORDS:
        return _reject(REJECT_FOREIGN_FUNCTION_WORDS)

    # Reason: accent density is the weakest signal, so it is doubly constrained.
    # (a) It fires only when nothing says "English" — otherwise "Zürich and Genève
    # sign a deal" would be dropped. (b) Only LOWERCASE accented words count: an
    # accented word in a non-proper-noun position ("programy zaczęły raportować") is
    # foreign prose, whereas "Beyoncé, Céline win Grammys" is an English headline
    # whose accents live entirely in names. Capitalization is the cheapest available
    # proper-noun discriminator and costs nothing.
    if not has_english_evidence:
        accented_words = {
            word.lower()
            for word in raw_words
            if not word.isascii() and not word[0].isupper()
        }
        if len(accented_words) >= _MIN_DISTINCT_ACCENTED_WORDS:
            return _reject(REJECT_FOREIGN_DIACRITIC_DENSITY)

    return LanguageVerdict(
        is_admitted=True,
        rejection_reason=None,
        language_evidence=language_evidence,
        source_language_code=source_language_code,
    )
