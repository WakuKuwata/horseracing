"""horseracing-scrape: polite netkeiba ingestion of entries / odds / results.

netkeiba IDs map to JRA-VAN only via id_mappings (no guess-join): mapped -> canonical_id,
unmapped -> a unique ``nk:{id}`` surrogate + UNMAPPED queue (debut/leak-safe). Future race_id
must be a valid JRA-VAN 12-digit or no row is written. Results backfill is INSERT-ONLY (never
overwrites JRA-VAN); pre-race odds overwrite ONLY result-pending races. Idempotent + audited.
"""

from __future__ import annotations

SCRAPE_PARSER_VERSION = "scrape-0.1.0"
SURROGATE_PREFIX = "nk:"

__all__ = ["SCRAPE_PARSER_VERSION", "SURROGATE_PREFIX"]
