"""Cota de închidere (CLV) pentru strategiile urmărite pe hârtie.

Pentru fiecare pick al zilei, când meciul începe în cel mult WINDOW, se notează
cota Superbet de atunci (≈ cota de închidere) lângă cota de la alegere, în
data/clv_log.csv (comis de workflow). Dacă alegem constant la cote mai mari
decât cea de închidere (CLV > 0), strategia are un avantaj real față de piață,
chiar înainte să se vadă în rezultate.

Strategii:
- "toti de acord": tennis/stats/player_history_log.csv, verdict "toti de acord",
  odds_pass True (cota >= 1.50), pariu pe câștigător;
- "peste 2.5": football/recommendations_log.csv, fără `excluded`, cota Peste 2.5
  între 1.40 și 2.00, pariu pe "Total goluri" Peste 2.5.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
from pathlib import Path
from typing import Optional

import requests

from .football import (MATCH_TOTAL_MARKET, FootballPick, match_football, parse_football_event,
                       parse_football_picks)
from .picks import Pick, match_pick
from .superbet import FOOTBALL_SPORT_ID, fetch_event_markets, fetch_events, market_odds

logger = logging.getLogger(__name__)

PLAYER_HISTORY_URL = "https://raw.githubusercontent.com/andrei0182/pariuri/main/tennis/stats/player_history_log.csv"
FOOTBALL_URL = "https://raw.githubusercontent.com/andrei0182/pariuri/main/football/recommendations_log.csv"
LOG_PATH = Path("data") / "clv_log.csv"
COLUMNS = ["date", "strategy", "selection", "match", "start_utc", "pick_odds", "close_odds", "clv_pct",
           "recorded_at", "event_id"]
WINDOW = dt.timedelta(minutes=40)  # monitorul rulează la 20 de minute: prindem fiecare meci o dată
FOOTBALL_ODDS = (1.40, 2.00)


def _float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _get(url: str) -> str:
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        logger.warning("CLV: nu pot citi %s: %s", url, exc)
        return ""


def tennis_picks(text: str, dates: set[str]) -> list[Pick]:
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        if (r.get("date") in dates and r.get("verdict") == "toti de acord" and r.get("odds_pass") == "True"
                and r.get("result") == "pending" and _float(r.get("rec_odds"))):
            out.append(Pick(date=r["date"], player=r["recommended_player"], opponent=r["opponent"],
                            rec_odds=_float(r["rec_odds"]), comp_pct=None, games_line=None))
    return out


def football_picks(text: str, dates: set[str]) -> list[FootballPick]:
    lo, hi = FOOTBALL_ODDS
    return [p for p in parse_football_picks(text, dates) if p.odds_over and lo <= p.odds_over <= hi]


def _start(value: str) -> Optional[dt.datetime]:
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def load_log(path: Path = LOG_PATH) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _row(date, strategy, selection, match, start, pick_odds, close_odds, now, event_id) -> dict:
    clv = (pick_odds / close_odds - 1) * 100
    return {"date": date, "strategy": strategy, "selection": selection, "match": match,
            "start_utc": start.strftime("%Y-%m-%d %H:%M"), "pick_odds": f"{pick_odds:.2f}",
            "close_odds": f"{close_odds:.2f}", "clv_pct": f"{clv:+.1f}",
            "recorded_at": now.strftime("%Y-%m-%d %H:%M"), "event_id": event_id}


def collect(now: dt.datetime, tennis: list[Pick], football: list[FootballPick],
            tennis_offer: list, football_events: list[dict], markets=fetch_event_markets) -> list[dict]:
    """Rândurile noi: pick-urile al căror meci începe în (now, now + WINDOW]."""
    rows = []
    for p in tennis:
        found = match_pick(p, tennis_offer)
        if not found:
            continue
        m, side = found
        start, odds = _start(m.start_time), (m.odds_home if side == "home" else m.odds_away)
        if start and odds and now < start <= now + WINDOW:
            rows.append(_row(p.date, "toti de acord", p.player, f"{m.home} vs {m.away}", start,
                             p.rec_odds, odds, now, m.event_id))
    by_id = {e.get("event_id"): e for e in football_events}
    parsed = [parse_football_event(e) for e in football_events]
    for p in football:
        m = match_football(p, parsed)
        if not m:
            continue
        start = _start((by_id.get(m.event_id) or {}).get("fixture", {}).get("event_date"))
        if not (start and now < start <= now + WINDOW):
            continue
        odds = market_odds(markets(m.event_id) or [], MATCH_TOTAL_MARKET, "Peste 2.5")
        if odds:
            rows.append(_row(p.date, "peste 2.5", "Peste 2.5", f"{m.home} vs {m.away}", start,
                             p.odds_over, odds, now, m.event_id))
    return rows


def _key(row: dict) -> str:
    sel = row["selection"] if row["strategy"] == "toti de acord" else row["match"]
    return f"{row['strategy']}|{row['date']}|{sel}"


def update(now: dt.datetime, tennis_offer: list, path: Path = LOG_PATH) -> int:
    """Adaugă în log cotele de închidere noi; întoarce câte rânduri a scris."""
    today = now.date()
    dates = {today.isoformat(), (today - dt.timedelta(days=1)).isoformat()}
    tennis = tennis_picks(_get(PLAYER_HISTORY_URL), dates)
    football = football_picks(_get(FOOTBALL_URL), dates)
    if not tennis and not football:
        return 0
    existing = load_log(path)
    done = {_key(r) for r in existing}
    fb_events = fetch_events("active-prematch", today, sport=FOOTBALL_SPORT_ID) if football else []
    rows = [r for r in collect(now, tennis, football, tennis_offer, fb_events) if _key(r) not in done]
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new_file:
            w.writeheader()
        w.writerows(rows)
    logger.info("CLV: %d cote de închidere notate.", len(rows))
    return len(rows)


def summary(rows: list[dict]) -> dict[str, tuple[int, float, float]]:
    """strategie -> (pariuri, CLV mediu %, cât la sută au bătut închiderea)."""
    out = {}
    for s in sorted({r["strategy"] for r in rows}):
        vals = [float(r["clv_pct"]) for r in rows if r["strategy"] == s]
        out[s] = (len(vals), sum(vals) / len(vals), sum(v > 0 for v in vals) / len(vals) * 100)
    return out

