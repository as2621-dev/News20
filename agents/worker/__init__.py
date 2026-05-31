"""News20 Python worker (FastAPI) — server-side endpoints holding the LLM key.

The static-export SPA cannot hold the Gemini key or run verification client-side
(``plans/phase-2b-m2-grounded-interrogation.md`` prereq b), so the grounded Q&A
endpoint runs here. Phase 2b SP2 stands up the minimal app + the
``POST /api/story/{story_id}/question`` route.
"""
