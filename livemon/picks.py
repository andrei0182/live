"""Pick-urile modelului, citite din `stats/picks_log.csv` din repo-ul `tenis`.

`recommended_player` = jucătorul ales de model, `comp_pct` = probabilitatea
modelului (%), `rec_odds` = cota Superbet pre-meci la care a fost recomandat.
`date` e data din România la care a rulat raportul (~03:00).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

import requests

from .superbet import LiveMatch

logger = logging.getLogger(__name__)

PICKS_URL = "https://raw.githubusercontent.com/andrei0182/tenis/main/stats/picks_log.csv"


@dataclass
class Pick:
    date: str
    player: str  # recommended_player
    opponent: str
    rec_odds: Optional[float]
    comp_pct: Optional[float]
    games_line: Optional[float]

    @property
    def key(self) -> str:
        return f"{self.date}|{self.player}|{self.opponent}"


def _float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_picks(text: str, dates: set[str]) -> list[Pick]:
    picks = []
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("date") not in dates or row.get("result", "") != "pending":
            continue
        picks.append(Pick(
            date=row["date"],
            player=row.get("recommended_player", ""),
            opponent=row.get("opponent", ""),
            rec_odds=_float(row.get("rec_odds")),
            comp_pct=_float(row.get("comp_pct")),
            games_line=_float(row.get("games_line")),
        ))
    return picks


def fetch_pending_picks(today: dt.date, url: str = PICKS_URL, timeout: float = 15.0) -> list[Pick]:
    """Pick-urile `pending` de azi și de ieri (un meci de seară poate trece de miezul nopții)."""
    dates = {today.isoformat(), (today - dt.timedelta(days=1)).isoformat()}
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Nu pot citi picks_log.csv: %s", exc)
        return []
    return parse_picks(resp.text, dates)


def _tokens(name: str) -> set[str]:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return {t for t in re.split(r"[^a-z]+", ascii_name) if len(t) >= 3}


def _same_player(a: str, b: str) -> bool:
    return bool(_tokens(a) & _tokens(b))


def match_pick(pick: Pick, matches: list[LiveMatch]) -> Optional[tuple[LiveMatch, str]]:
    """Găsește meciul Superbet al pick-ului. Întoarce (meci, "home"/"away") =
    partea pe care e `recommended_player`. Potrivire pe cuvinte comune din nume
    (fără diacritice, minim 3 litere), pentru ambii jucători, ca să tolereze
    diferențele de format între surse (ex. "Stosovic M." vs "Milica Stosovic")."""
    for m in matches:
        if _same_player(pick.player, m.home) and _same_player(pick.opponent, m.away):
            return m, "home"
        if _same_player(pick.player, m.away) and _same_player(pick.opponent, m.home):
            return m, "away"
    return None
