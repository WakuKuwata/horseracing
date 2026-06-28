"""Win-odds JSON adapter (Feature 022, US3).

Fetches netkeiba's win-odds JSON **no-cache** (odds are single-latest, constitution V — a cached
value could be stale) and parses it into ScrapedOdds. Required-key validation / fail-close lives in
``parse_odds``. The race_id is supplied by the caller (the JSON has none).
"""

from __future__ import annotations

from .fetch import PoliteFetcher
from .models import ScrapedOdds
from .parse.odds import parse_odds
from .urls import win_odds_url


def fetch_win_odds(fetcher: PoliteFetcher, race_id: str) -> ScrapedOdds:
    payload = fetcher.get(win_odds_url(race_id), use_cache=False)
    return parse_odds(payload, race_id)
