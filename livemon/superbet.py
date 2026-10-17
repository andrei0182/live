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
import json
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


# ============================================================
# Piețele extinse ale unui meci (total game-uri pe jucător etc.).
# CONFIRMAT (2026-10-17) via curl, pe un meci live: endpoint-ul de
# subscription din `tenis` (SSE, text/event-stream) întoarce, pe prima linie
# "data:[...]", toate piețele meciului, atât prematch cât și live. Stream-ul
# nu se închide singur, așa că citim până la primul sfârșit de linie.
# "Total game-uri <Nume>" (id 999 pentru home, 996 pentru away) are câte o
# pereche "Sub X" / "Peste X" pentru mai multe linii X.
# ============================================================

MARKETS_URL = "https://production-superbet-offer-ro.freetls.fastly.net/v3/subscription/ro-RO/events"


def fetch_event_markets(event_id: int, timeout: float = 10.0, max_bytes: int = 2_000_000) -> Optional[list[dict]]:
    try:
        resp = _session.get(MARKETS_URL, params={"events": str(event_id)}, timeout=timeout, stream=True)
    except requests.RequestException as exc:
        logger.warning("fetch_event_markets(%s) a eșuat: %s", event_id, exc)
        return None

    buf = b""
    try:
        for chunk in resp.iter_content(chunk_size=16384):
            buf += chunk
            if b"\n" in buf or len(buf) > max_bytes:
                break
    except Exception as exc:  # stream întrerupt: parsăm ce avem
        logger.debug("fetch_event_markets(%s): stream oprit: %s", event_id, exc)
    finally:
        resp.close()

    line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace")
    if line.startswith("data:"):
        line = line[len("data:"):]
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        logger.warning("fetch_event_markets(%s): JSON incomplet", event_id)
        return None
    return data[0].get("markets", []) if data else None


def player_games_main_line(markets: list[dict], player_name: str) -> Optional[float]:
    """Linia principală la "Total game-uri <player_name>" (pe tot meciul, nu pe
    set): linia cu cotele Sub/Peste cele mai apropiate, doar dintre perechile
    cu ambele cote active. `player_name` trebuie să fie numele exact din
    Superbet (LiveMatch.home / .away)."""
    lines: dict[float, dict[str, float]] = {}
    for market in markets:
        if market.get("name") != f"Total game-uri {player_name}":
            continue
        for odd in market.get("odds") or []:
            if odd.get("status", ODD_STATUS_ACTIVE) != ODD_STATUS_ACTIVE or not odd.get("price"):
                continue
            meta = odd.get("metadata") or {}
            total = (meta.get("specifiers") or {}).get("total")
            name = meta.get("name", "")
            if total is None:
                continue
            side = "under" if name.startswith("Sub") else "over" if name.startswith("Peste") else None
            if side:
                lines.setdefault(float(total), {})[side] = odd["price"]
    pairs = {line: p for line, p in lines.items() if "under" in p and "over" in p}
    if not pairs:
        return None
    return min(pairs, key=lambda line: (abs(pairs[line]["under"] - pairs[line]["over"]), line))
