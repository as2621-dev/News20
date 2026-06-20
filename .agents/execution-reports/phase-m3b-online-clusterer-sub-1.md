# Phase M3b — Sub-phase 1 execution report: Engine I/O models + time-window blocking

## What I implemented
- **`engine_models.py`** — pydantic `ClusterInput` (`input_index`, `input_text`, `input_url`,
  `input_outlet: str | None`, `input_published_utc: datetime`, `input_provisional_category`) and
  `ClusterRun` (`clusters: list[StoryCluster]`, `members: list[ClusterMember]`,
  `input_cluster_map: dict[int, str]`). `StoryCluster`/`ClusterMember` imported from
  `agents.pipeline.clustering.models`. `ClusterRun` fields use `default_factory` so an empty run
  validates.
- **`blocking.py`** — `DEFAULT_WINDOW_HOURS = 48` and pure `select_block(active_clusters, *,
  candidate_published_utc, window_hours=DEFAULT_WINDOW_HOURS, category=None)`. Keeps a cluster when
  `abs(candidate_published_utc - cluster.cluster_last_seen_utc) <= timedelta(hours=window_hours)`
  and (when `category` given) `cluster_category == category`. Input order preserved.
- **`test_blocking.py`** — 10 pure tests (no mocks needed).

## Files created
- `agents/pipeline/clustering/engine_models.py`
- `agents/pipeline/clustering/blocking.py`
- `tests/agents/pipeline/clustering/test_blocking.py`

## Key design decision (documented)
**Boundary at the window edge is INCLUSIVE.** A cluster whose `last_seen` is exactly
`window_hours` from the candidate is kept; one a microsecond past is dropped. Documented in the
module docstring, the function docstring, and asserted explicitly by
`test_exactly_48h_edge_is_inclusive_and_just_past_is_excluded`.

Other notes: window uses `abs()` so a cluster `last_seen` slightly AFTER the candidate (future)
is still in-window — covered by `test_window_is_symmetric_for_future_candidate`. Timestamps are
assumed tz-aware UTC; mixing a naive datetime would raise loudly (desired, per Rule 12).

## Divergences
- None from spec. `window_hours` default is bound to the `DEFAULT_WINDOW_HOURS` constant rather
  than the literal `48` in the signature, so the constant is the single source of truth.

## Review findings + fixes
- Self-review via reading the diff. One LOW issue found and fixed: a sloppy "Half-open... no —
  INCLUSIVE" fragment left in the `window_hours` arg docstring; rewritten to "Inclusive window
  half-width in hours." No other critical/high/medium/low issues.

## Validation results
- `pytest tests/agents/pipeline/clustering/test_blocking.py -q` → **10 passed**.
- `ruff check engine_models.py blocking.py test_blocking.py` → **All checks passed!**
- Bonus: `pytest --doctest-modules blocking.py engine_models.py` → 4 passed (docstring examples run).
- Note: the sub-worktree has no `.venv`; used the main worktree venv
  `/Users/asheshsrivastava/News20/News20/.venv/bin/python` with
  `PYTHONPATH=/Users/asheshsrivastava/News20/News20-sub-1`. No files outside the sub-worktree were
  touched.

## Definition of done: PASS
- (a) 47h IN / 49h OUT — `test_cluster_47h_before_is_in_block_and_49h_is_out` ✓
- (b) `category="tech"` excludes in-window `"sport"` cluster — `test_category_filter_excludes_in_window_other_category_cluster` ✓
- (c) exact 48h edge inclusive, just past excluded — `test_exactly_48h_edge_is_inclusive_and_just_past_is_excluded` ✓
- (d) `ClusterInput`/`ClusterRun` validate minimal examples — `test_cluster_input_validates_minimal_example`, `test_cluster_run_validates_minimal_example` ✓

## Concerns for orchestrator
- The instructed venv path `/Users/asheshsrivastava/News20/News20-sub-1/.venv` does not exist;
  validation used the main worktree's venv. If the orchestrator re-validates per-sub-phase, point
  it at the main venv with `PYTHONPATH` set to the sub-worktree.
- `ClusterRun` shape is now frozen for SP3/SP4 to build against (`input_cluster_map` maps a KEPT
  input_index → cluster_id; dropped reprints get no entry, per spec).
