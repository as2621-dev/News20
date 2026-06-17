"""Source-type-specific news adapters for the News20 ingestion pipeline.

Each adapter implements ``BaseNewsAdapter`` (base.py): a two-phase contract of
``search()`` (discover candidate articles for a query) and ``extract_body()``
(fetch + extract the article body). The GDELT DOC adapter (gdelt_doc.py) is the
v1 source — keyless, global, fresh — chosen because no NewsAPI key is available
(see plans/phase-1d-daily-content-pipeline-progress.md Step 0).

Source-keyed adapters (Phase 5d) poll one followed source rather than a query:
the YouTube adapter (youtube.py) detects a channel's fresh uploads via its keyless
RSS feed and transcribes them with yt-dlp, and the X adapter (x_account.py)
discovers a followed handle's recent posts via xAI/Grok Live Search and screenshots
each tweet card. Both expose a source-keyed ``fetch_new_items(external_id, since)``
entry point in addition to the base contract.
"""

from agents.ingestion.adapters.base import BaseNewsAdapter
from agents.ingestion.adapters.x_account import XAccountAdapter
from agents.ingestion.adapters.youtube import YouTubeAdapter

__all__ = ["BaseNewsAdapter", "XAccountAdapter", "YouTubeAdapter"]
