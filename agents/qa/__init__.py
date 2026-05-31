"""News20 grounded Q&A (Phase 2b — M2 interrogation).

The active story's small per-story grounding corpus is loaded **whole** into the
LLM context (no vector store / retrieval — see
``plans/phase-2b-m2-grounded-interrogation.md`` re-scope), the model is
constrained to answer only from it, and a verification stage gates every claim
before it surfaces.

Sub-phase 1 (this commit) ships only the corpus loader:
    load_grounding_corpus(story_id) -> GroundingCorpus

SP2 (the grounded answer endpoint + verification) consumes the assembled,
bounded, citeable :class:`GroundingCorpus` produced here.
"""
