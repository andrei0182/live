"""Oferta live Superbet pentru tenis.

CONFIRMAT (2026-10-17) via curl: același endpoint ca în repo-ul `tenis`
(tenis_scraper/events.py), dar cu `index=active-live`, întoarce meciurile în
desfășurare cu:
- `inplay_stats`: seturi câștigate (`home_team_score` / `away_team_score`),
  game-uri pe set în `periods[]` (`num` = numărul setului), punctele din
  game-ul curent și `team_serving`;
- `inplay_stats_metadata.status`: "STARTED" / "FINISHED", `period_status`
  ("S1", "S2", ...);
- market-ul "Final" (id 521) cu cotele live "1"/"2". `status` 1 = activ,
  2 = suspendat (ex. în timpul unui punct).
`home` e jucătorul din stânga lui "·" în `event_name`.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

EVENTS_URL = "https://production-superbet-offer-ro.freetls.fastly.net/v3/ro-RO/events"
TENNIS_SPORT_ID = 2
MARKET_ID_FINAL = 521
ODD_STATUS_ACTIVE = 1

_session = requests.Session()
_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
})
_retry = Retry(total=3, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504])
_session.mount("https://", HTTPAdapter(max_retries=_retry))


@dataclass
class LiveMatch:
    event_id: int
    home: str
    away: str
    status: str  # "STARTED" / "FINISHED" / ...
    sets_home: int = 0
    sets_away: int = 0
    # game-uri pe set, în ordinea setului: [(home, away), ...]
    set_games: list[tuple[int, int]] = field(default_factory=list)
    odds_home: Optional[float] = None  # None = lipsă sau suspendată
    odds_away: Optional[float] = None
    start_time: str = ""

    @property
    def url(self) -> str:
        return f"https://superbet.ro/cote/tenis/{self.event_id}"

    def score_text(self) -> str:
        sets = " ".join(f"{h}-{a}" for h, a in self.set_games)
        return f"{self.sets_home}-{self.sets_away} ({sets})" if sets else f"{self.sets_home}-{self.sets_away}"


def _iso_utc(d: dt.date) -> str:
    return f"{d.isoformat()}T00:00:00.000Z"


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_live_event(event: dict) -> LiveMatch:
    fixture = event.get("fixture", {})
    name = fixture.get("event_name", "")
    home, _, away = name.partition("·")

    stats = event.get("inplay_stats") or {}
    periods = sorted(stats.get("periods") or [], key=lambda p: _int(p.get("num")))
    set_games = [(_int(p.get("home_team_score")), _int(p.get("away_team_score"))) for p in periods]

    odds_home = odds_away = None
    for market in event.get("markets") or []:
        if market.get("id") != MARKET_ID_FINAL and market.get("name") != "Final":
            continue
        for odd in market.get("odds") or []:
            if odd.get("status", ODD_STATUS_ACTIVE) != ODD_STATUS_ACTIVE:
                continue
            side = (odd.get("metadata") or {}).get("name")
            if side == "1":
                odds_home = odd.get("price")
            elif side == "2":
                odds_away = odd.get("price")

    return LiveMatch(
        event_id=event.get("event_id"),
        home=home.strip(),
        away=away.strip(),
        status=(event.get("inplay_stats_metadata") or {}).get("status", ""),
        sets_home=_int(stats.get("home_team_score")),
        sets_away=_int(stats.get("away_team_score")),
        set_games=set_games,
        odds_home=odds_home,
        odds_away=odds_away,
        start_time=fixture.get("event_date", ""),
    )


def fetch_events(index: str, today: dt.date, timeout: float = 15.0) -> list[dict]:
    """Toate meciurile de tenis din `index` ("active-live" / "active-prematch"),
    de ieri până mâine inclusiv (prinde și meciurile începute înainte de miezul
    nopții UTC)."""
    params = {
        "startDate": _iso_utc(today - dt.timedelta(days=1)),
        "endDate": _iso_utc(today + dt.timedelta(days=2)),
        "index": index,
        "sports": TENNIS_SPORT_ID,
    }
    try:
        resp = _session.get(EVENTS_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("events", [])
    except (requests.RequestException, ValueError) as exc:
        logger.warning("fetch_events(%s) a eșuat: %s", index, exc)
        return []


def fetch_live_matches(today: dt.date) -> list[LiveMatch]:
    return [parse_live_event(e) for e in fetch_events("active-live", today)]


def fetch_prematch_matches(today: dt.date) -> list[LiveMatch]:
    return [parse_live_event(e) for e in fetch_events("active-prematch", today)]
