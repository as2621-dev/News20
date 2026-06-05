"""Per-archetype curated content-source catalog seeder (Phase 5b SP3).

Ported from TL;DW (``scripts/seed_catalog/``) per reference/sources-reuse-map.md
§2. Reads ``data/{type}.{archetype}.json`` (file position = popularity rank),
resolves channels (YouTube), podcasts (iTunes), and personalities (Wikipedia
photo), and upserts them into the News20 ``content_sources`` / ``personalities``
tables (migration 0009) tagged with the archetype ``personas`` they appear under
and the 8-category ``topic_tags``.

Divergences from the donor (surfaced per Rule 7):
  - Target tables renamed ``sources`` → ``content_sources`` and the column
    ``source_type`` → ``content_source_type`` (News20 naming-collision guard).
  - Personas re-authored from the donor's 6 to News20's 12 SP2 archetype slugs.
  - X handles are stored as ``x_account`` rows WITHOUT live resolution (no
    resolver until Phase 5c/5d).
"""
