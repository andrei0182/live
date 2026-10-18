"""Pick-urile modelelor, citite din repo-ul `pariuri` (tennis/stats/):

- `model90_log.csv`: "Model 90%"; probabilitatea e în `probability`
  (fracție, ex. 0.902). E singura sursă urmărită.
- `picks_log.csv`: raportul zilnic (probabilitatea în `comp_pct`, procente);
  nu se joacă, dar `parse_picks` îl poate citi în continuare.

Comune: `recommended_player` = jucătorul ales de model, `opponent`,
`rec_odds` = cota Superbet pre-meci, `result` ("pending" până la verificare),
`date` = data din România la care a rulat raportul (~03:00).
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

_STATS_URL = "https://raw.githubusercontent.com/andrei0182/pariuri/main/tennis/stats"


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    pct_column: str
    pct_scale: float  # înmulțitor până la procente


# Se joacă doar Model 90%; raportul zilnic (picks_log.csv, coloana comp_pct) nu mai e urmărit.
MODEL90 = Source("Model 90%", f"{_STATS_URL}/model90_log.csv", "probability", 100.0)
SOURCES = (MODEL90,)


@dataclass
class Pick:
    date: str
    player: str  # recommended_player
    opponent: str
    rec_odds: Optional[float]
    comp_pct: Optional[float]
    games_line: Optional[float]
    source: str = ""

    @property
    def key(self) -> str:
        return f"{self.date}|{self.player}|{self.opponent}"


def _float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _scale(value: Optional[float], factor: float) -> Optional[float]:
    return None if value is None else round(value * factor, 1)


def parse_picks(text: str, dates: set[str], pct_column: str = "comp_pct", pct_scale: float = 1.0,
                source: str = "") -> list[Pick]:
    picks = []
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("date") not in dates or row.get("result", "") != "pending":
            continue
        picks.append(Pick(
            date=row["date"],
            player=row.get("recommended_player", ""),
            opponent=row.get("opponent", ""),
            rec_odds=_float(row.get("rec_odds")),
            comp_pct=_scale(_float(row.get(pct_column)), pct_scale),
            games_line=_float(row.get("games_line")),
            source=source,
        ))
    return picks


def fetch_pending_picks(today: dt.date, sources: tuple[Source, ...] = SOURCES,
                        timeout: float = 15.0) -> list[Pick]:
    """Pick-urile `pending` de azi și de ieri (un meci de seară poate trece de
    miezul nopții), din toate sursele. Un meci prezent în mai multe surse (sau
    pe două piețe în Model 90%) se urmărește o singură dată: rămâne primul."""
    dates = {today.isoformat(), (today - dt.timedelta(days=1)).isoformat()}
    picks: dict[str, Pick] = {}
    for src in sources:
        try:
            resp = requests.get(src.url, timeout=timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Nu pot citi %s: %s", src.url, exc)
            continue
        for p in parse_picks(resp.text, dates, src.pct_column, src.pct_scale, src.name):
            picks.setdefault(p.key, p)
    return list(picks.values())


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
    singles = "/" not in pick.player and "/" not in pick.opponent
    for m in matches:
        # CONFIRMAT (2026-10-17): fără verificarea asta, pick-ul de simplu
        # "Dorofeeva-Rybas vs Gamretkaia" se potrivea cu dublul
        # "E.Agureeva/F.Dorofeeva-Rybas vs A.Gamretkaia/L.Belibova".
        if singles and ("/" in m.home or "/" in m.away):
            continue
        if _same_player(pick.player, m.home) and _same_player(pick.opponent, m.away):
            return m, "home"
        if _same_player(pick.player, m.away) and _same_player(pick.opponent, m.home):
            return m, "away"
    return None
