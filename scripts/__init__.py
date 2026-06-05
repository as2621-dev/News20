"""Server-side operational scripts for News20 (run at seed/admin time).

Not part of the deployed app or the agent runtime — these scripts run with
service-role credentials at seed time (e.g. ``scripts.seed_catalog`` populates
the curated content-source catalog). Kept as a package so ``python -m
scripts.seed_catalog.seed_catalog`` and the test suite can import them.
"""
