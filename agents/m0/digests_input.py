"""The 5 M0 quality-spike digest scripts as typed, ordered, speaker-tagged turns.

Transcribed once, by hand, from ``documents/m0-digests.md`` (the phase decided
programmatic markdown parsing is brittle and out of scope). This module is the
single typed source of the scripts and is reused by:
  - sub-phase 1 (this phase): rendered to audio via Gemini multi-speaker TTS
  - sub-phase 2: time-sliced against the real audio for word-by-word captions
  - sub-phase 4: headline-card text for the render manifest

Each digest exposes an id, headline, category, source citation, and an ordered
list of ALEX/JORDAN ``DialogueTurn``s. ALEX renders as Gemini voice ``Leda``,
JORDAN as ``Sadaltager`` (mapping lives in ``agents.voice.gemini_tts``).

Example:
    >>> from agents.m0.digests_input import DIGESTS, get_digest_by_id
    >>> len(DIGESTS)
    5
    >>> get_digest_by_id("digest-1").turns[0].speaker
    'ALEX'
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agents.voice.models import DialogueTurn


class Digest(BaseModel):
    """One M0 digest: identity metadata plus its ordered speaker-tagged script.

    Attributes:
        digest_id: Stable identifier ("digest-1" .. "digest-5"); also the
            audio/caption/video output filename stem used across sub-phases.
        digest_headline: Short headline for the headline card (cut 1).
        digest_category: Editorial category label.
        digest_source: Human-readable source citation from the brief.
        digest_source_url: Machine-readable source URL (None when unknown).
            A YouTube video/podcast URL here makes the poster pipeline seed
            from the video's own thumbnail.
        turns: Ordered ALEX/JORDAN dialogue turns (the spoken script).

    Example:
        >>> d = DIGESTS[0]
        >>> d.digest_id
        'digest-1'
    """

    digest_id: str = Field(
        ..., description="Stable id and output filename stem, e.g. 'digest-1'"
    )
    digest_headline: str = Field(
        ..., description="Short headline for the cut-1 headline card"
    )
    digest_category: str = Field(..., description="Editorial category label")
    digest_source: str = Field(..., description="Human-readable source citation")
    digest_source_url: str | None = Field(
        default=None,
        description="Machine-readable source URL; a YouTube URL seeds the poster "
        "from the video thumbnail",
    )
    turns: list[DialogueTurn] = Field(
        ...,
        min_length=1,
        description="Ordered ALEX/JORDAN dialogue turns transcribed from the script",
    )


# ---------------------------------------------------------------------------
# Digest 1 — GEOPOLITICS: US & Iran
# ---------------------------------------------------------------------------

_DIGEST_1 = Digest(
    digest_id="digest-1",
    digest_headline="US strikes Iran again; Trump won't rush a deal",
    digest_category="Geopolitics",
    digest_source='CNN live, "Trump says he won\'t rush Iran deal," 2026-05-27',
    turns=[
        DialogueTurn(
            speaker="ALEX",
            text=(
                "The U.S. military hit another target inside Iran overnight — a site Washington says "
                "threatened American forces and commercial shipping."
            ),
        ),
        DialogueTurn(
            speaker="JORDAN",
            text=(
                "And President Trump says he's confident a deal to end the fighting is close. "
                'But "close" isn\'t "done."'
            ),
        ),
        DialogueTurn(
            speaker="ALEX",
            text=(
                "Right — he made clear he's not satisfied with the terms yet. And he's willing to "
                "restart strikes if Iran doesn't meet U.S. demands."
            ),
        ),
        DialogueTurn(
            speaker="JORDAN",
            text=(
                "Meanwhile Tehran is pushing back hard. It just issued new rules for any ship trying "
                "to pass through the Strait of Hormuz."
            ),
        ),
        DialogueTurn(
            speaker="ALEX",
            text=(
                "That's the chokepoint where about a fifth of the world's oil moves. Iran's trying to "
                "formalize control over it — defying U.S. warnings."
            ),
        ),
        DialogueTurn(
            speaker="JORDAN",
            text="So a ceasefire is being talked up, and a flashpoint is heating up — at the same time.",
        ),
        DialogueTurn(speaker="ALEX", text="We'll see which one wins."),
    ],
)


# ---------------------------------------------------------------------------
# Digest 2 — SPORTS: Travis Kelce buys into the Guardians
# ---------------------------------------------------------------------------

_DIGEST_2 = Digest(
    digest_id="digest-2",
    digest_headline="Travis Kelce buys a minority stake in the Cleveland Guardians",
    digest_category="Sports",
    digest_source='ESPN, "Chiefs\' Travis Kelce purchases minority stake in Guardians," 2026-05-27',
    turns=[
        DialogueTurn(
            speaker="JORDAN", text="Travis Kelce is now part-owner of a baseball team."
        ),
        DialogueTurn(
            speaker="ALEX",
            text=(
                "The Chiefs tight end just bought a minority stake in the Cleveland Guardians — "
                "the team he grew up watching."
            ),
        ),
        DialogueTurn(
            speaker="JORDAN",
            text=(
                "And this is a hometown story. Kelce's from Cleveland Heights — as a kid he'd ride the "
                "light rail downtown with his dad to catch games."
            ),
        ),
        DialogueTurn(
            speaker="ALEX",
            text="Before football, he was actually one of the best baseball players in the whole Cleveland area.",
        ),
        DialogueTurn(
            speaker="JORDAN",
            text=(
                "He joins a growing club of active stars buying into the MLB — LeBron with the Red Sox, "
                "Giannis with the Brewers..."
            ),
        ),
        DialogueTurn(
            speaker="ALEX",
            text="...and his own teammate Patrick Mahomes, who's already part of the Royals.",
        ),
        DialogueTurn(
            speaker="JORDAN", text="The size of Kelce's stake? Still under wraps."
        ),
        DialogueTurn(
            speaker="ALEX",
            text=(
                "But the message is clear — top athletes aren't just playing the game anymore. "
                "They're buying it."
            ),
        ),
    ],
)


# ---------------------------------------------------------------------------
# Digest 3 — TECH & SCIENCE: 30-year superconductivity record falls
# ---------------------------------------------------------------------------

_DIGEST_3 = Digest(
    digest_id="digest-3",
    digest_headline="University of Houston breaks a 30-year superconductivity record",
    digest_category="Tech & Science",
    digest_source=(
        'ScienceDaily, "Scientists break 30-year superconductivity record at normal pressure," '
        "2026-05-27 (University of Houston)"
    ),
    turns=[
        DialogueTurn(
            speaker="ALEX",
            text="Physicists in Houston just broke a record that stood for more than thirty years.",
        ),
        DialogueTurn(
            speaker="JORDAN",
            text=(
                "We're talking superconductivity — materials that carry electricity with zero resistance, "
                "no energy lost at all."
            ),
        ),
        DialogueTurn(
            speaker="ALEX",
            text=(
                "The catch has always been the cold. You needed extreme temperatures to make it work. "
                "The old record, set back in 1993, was 133 Kelvin."
            ),
        ),
        DialogueTurn(
            speaker="JORDAN",
            text=(
                "The University of Houston team just pushed that to 151 Kelvin — the highest ever at "
                "normal, everyday pressure."
            ),
        ),
        DialogueTurn(
            speaker="ALEX",
            text=(
                'They used a trick called "pressure quenching" — squeeze the material, then lock in the '
                "new properties after the pressure is gone."
            ),
        ),
        DialogueTurn(
            speaker="JORDAN",
            text=(
                "It's still really cold, about minus 122 Celsius — but every degree closer to room "
                "temperature matters."
            ),
        ),
        DialogueTurn(
            speaker="ALEX",
            text="Because if we ever get there: lossless power grids, faster electronics, better fusion and medical scanners.",
        ),
        DialogueTurn(speaker="JORDAN", text="A thirty-year wall, finally moved."),
    ],
)


# ---------------------------------------------------------------------------
# Digest 4 — COMPANY PERFORMANCE: Nvidia's blowout — and the stock slips
# ---------------------------------------------------------------------------

_DIGEST_4 = Digest(
    digest_id="digest-4",
    digest_headline="Nvidia's blowout quarter — but the stock slips",
    digest_category="Company performance",
    digest_source="CNBC / Kiplinger, Nvidia fiscal-Q1-2027 earnings, reported 2026-05-20",
    turns=[
        DialogueTurn(
            speaker="JORDAN",
            text="Nvidia just reported earnings — and the AI boom shows no sign of cooling.",
        ),
        DialogueTurn(
            speaker="ALEX",
            text=(
                "The chipmaker pulled in 81.6 billion dollars in revenue for the quarter. "
                "Wall Street had expected about 79."
            ),
        ),
        DialogueTurn(
            speaker="JORDAN",
            text=(
                "The engine is the data-center business — revenue there nearly doubled from a year ago. "
                "That's the AI gold rush in a single number."
            ),
        ),
        DialogueTurn(
            speaker="ALEX",
            text="Profit beat too: a dollar eighty-seven a share, against forecasts of a dollar seventy-eight.",
        ),
        DialogueTurn(
            speaker="JORDAN",
            text=(
                "And they're not slowing down — Nvidia guided to 91 billion dollars next quarter, "
                "well above estimates."
            ),
        ),
        DialogueTurn(
            speaker="ALEX",
            text=(
                "They also rewarded shareholders: an 80 billion dollar buyback, and a dividend hiked "
                "from a single penny to twenty-five cents."
            ),
        ),
        DialogueTurn(
            speaker="JORDAN",
            text="So — a blowout. And yet the stock actually slipped afterward.",
        ),
        DialogueTurn(
            speaker="ALEX",
            text="When you're priced for perfection, even great isn't always good enough.",
        ),
    ],
)


# ---------------------------------------------------------------------------
# Digest 5 — DIVERSIFIED / WILDCARD: Pope Leo XIV's AI warning
# ---------------------------------------------------------------------------

_DIGEST_5 = Digest(
    digest_id="digest-5",
    digest_headline="Pope Leo XIV's strongest-yet moral warning on AI",
    digest_category="Diversified / wildcard",
    digest_source='TechStartups "Top Tech News Today," 2026-05-27',
    turns=[
        DialogueTurn(
            speaker="ALEX",
            text="The Pope just issued one of his strongest warnings yet — about artificial intelligence.",
        ),
        DialogueTurn(
            speaker="JORDAN",
            text=(
                "Pope Leo the Fourteenth urged world leaders to slow down the race to deploy AI, "
                "and to agree on international safeguards."
            ),
        ),
        DialogueTurn(
            speaker="ALEX",
            text="His concern? That unchecked AI could deepen misinformation, destabilize societies...",
        ),
        DialogueTurn(
            speaker="JORDAN",
            text=(
                "...and push autonomous weapons past meaningful human control. "
                "That last one is the line a lot of people quietly fear."
            ),
        ),
        DialogueTurn(
            speaker="ALEX",
            text=(
                "It's a striking moment — a moral authority stepping into a debate usually led by "
                "engineers and CEOs."
            ),
        ),
        DialogueTurn(
            speaker="JORDAN",
            text=(
                "And it lands the same week a major report said AI will soon be the single biggest force "
                "shaping global cybersecurity."
            ),
        ),
        DialogueTurn(
            speaker="ALEX", text="Two very different voices. The same message."
        ),
        DialogueTurn(
            speaker="JORDAN", text="Slow down — before the technology gets ahead of us."
        ),
    ],
)


DIGESTS: list[Digest] = [_DIGEST_1, _DIGEST_2, _DIGEST_3, _DIGEST_4, _DIGEST_5]


def get_digest_by_id(digest_id: str) -> Digest:
    """Return the digest with the given id.

    Args:
        digest_id: One of "digest-1" .. "digest-5".

    Returns:
        The matching Digest.

    Raises:
        KeyError: If no digest has that id.

    Example:
        >>> get_digest_by_id("digest-3").digest_category
        'Tech & Science'
    """
    for digest in DIGESTS:
        if digest.digest_id == digest_id:
            return digest
    raise KeyError(
        f"No digest with id {digest_id!r}; known ids: {[d.digest_id for d in DIGESTS]}"
    )
