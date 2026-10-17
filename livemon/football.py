"""Fotbal: alertă când un meci cu pronostic Peste 2.5 e încă 0-0 în prima
repriză după minutul `min_minute`.

Pronosticurile vin din `football/recommendations_log.csv` din repo-ul
`pariuri`: doar rândurile `pending` de azi/ieri, fără `excluded` (cele
excluse pentru cotă nu apar ca recomandări în email). `kickoff_local` e ora
din România, `odds_over` cota pre-meci la Peste 2.5.

CONFIRMAT (2026-10-17) via curl pe meciuri live (`sports=5`,
`index=active-live`): `inplay_stats.home_team_score` / `away_team_score` =
golurile, `inplay_stats.minutes` = minutul, iar
`inplay_stats_metadata.event_status_label` e "1H" în prima repriză și "2H" în
a doua. Piețele live "Prima repriză - Total goluri" (id 200735) și "Total
goluri" (id 200734) vin prin endpoint-ul SSE (`fetch_event_markets`).
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
from zoneinfo import ZoneInfo

import requests

from .rules import Decision, Outcome
from .superbet import FOOTBALL_SPORT_ID, fetch_events

logger = logging.getLogger(__name__)

PICKS_URL = "https://raw.githubusercontent.com/andrei0182/pariuri/main/football/recommendations_log.csv"
RO_TZ = ZoneInfo("Europe/Bucharest")
FIRST_HALF_LABEL = "1H"
# O primă repriză durează ~50 de minute cu prelungirile; după atât nu mai are
# rost să căutăm meciul în oferta live.
FIRST_HALF_WINDOW = dt.timedelta(minutes=55)

FIRST_HALF_MARKET = "Prima repriză - Total goluri"
MATCH_TOTAL_MARKET = "Total goluri"

# Cuvinte prea comune în numele echipelor ca să identifice singure o echipă
# ("Manchester City" vs "Bristol City").
_STOPWORDS = {
    "fc", "cf", "sc", "ac", "afc", "fk", "sk", "nk", "club", "city", "united", "utd", "town",
    "real", "sporting", "athletic", "atletico", "dinamo", "dynamo", "union", "u19", "u20",
    "u21", "u23", "women", "reserves",
}


@dataclass
class FootballPick:
    date: str
    league: str
    home: str
    away: str
    odds_over: Optional[float]
    kickoff_local: str  # "HH:MM", ora României

    @property
    def key(self) -> str:
        return f"{self.date}|fb|{self.home}|{self.away}"

    def kickoff_utc(self) -> Optional[dt.datetime]:
        try:
            local = dt.datetime.strptime(f"{self.date} {self.kickoff_local}", "%Y-%m-%d %H:%M")
        except ValueError:
            return None
        return local.replace(tzinfo=RO_TZ).astimezone(dt.timezone.utc)


@dataclass
class FootballMatch:
    event_id: int
    home: str
    away: str
    status: str  # "STARTED" / "FINISHED" / ...
    label: str  # "1H", "2H", ...
    minute: Optional[int]
    home_goals: int = 0
    away_goals: int = 0

    @property
    def url(self) -> str:
        return f"https://superbet.ro/cote/fotbal/{self.event_id}"

    @property
    def goals(self) -> int:
        return self.home_goals + self.away_goals


def _float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> Optional[int]:
    m = re.match(r"\d+", str(value or ""))  # "45+2" -> 45
    return int(m.group()) if m else None


def parse_football_picks(text: str, dates: set[str]) -> list[FootballPick]:
    picks = []
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("date") not in dates or row.get("result", "") != "pending" or row.get("excluded"):
            continue
        picks.append(FootballPick(
            date=row["date"],
            league=row.get("league", ""),
            home=row.get("home_team", ""),
            away=row.get("away_team", ""),
            odds_over=_float(row.get("odds_over")),
            kickoff_local=row.get("kickoff_local", ""),
        ))
    return picks


def fetch_football_picks(today: dt.date, url: str = PICKS_URL, timeout: float = 15.0) -> list[FootballPick]:
    dates = {today.isoformat(), (today - dt.timedelta(days=1)).isoformat()}
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Nu pot citi %s: %s", url, exc)
        return []
    return parse_football_picks(resp.text, dates)


def parse_football_event(event: dict) -> FootballMatch:
    fixture = event.get("fixture", {})
    home, _, away = fixture.get("event_name", "").partition("·")
    stats = event.get("inplay_stats") or {}
    meta = event.get("inplay_stats_metadata") or {}
    return FootballMatch(
        event_id=event.get("event_id"),
        home=home.strip(),
        away=away.strip(),
        status=meta.get("status", ""),
        label=meta.get("event_status_label", ""),
        minute=_int(stats.get("minutes")),
        home_goals=_int(stats.get("home_team_score")) or 0,
        away_goals=_int(stats.get("away_team_score")) or 0,
    )


def fetch_football_live(today: dt.date) -> list[FootballMatch]:
    return [parse_football_event(e) for e in fetch_events("active-live", today, sport=FOOTBALL_SPORT_ID)]


_YOUTH = re.compile(r"^u(1[5-9]|2[0-3])$")
_WOMEN = {"f", "w", "women", "fem", "feminin"}


def _words(name: str) -> list[str]:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return [t for t in re.split(r"[^a-z0-9]+", ascii_name) if t]


def _tokens(name: str) -> set[str]:
    return {t for t in _words(name) if len(t) >= 3 and t not in _STOPWORDS and not _YOUTH.match(t)}


def _category(name: str) -> tuple[frozenset, bool]:
    """(vârstele de juniori din nume, e echipă de fete) -- ex. "Pescara U19" -> ({"u19"}, False)."""
    words = _words(name)
    return frozenset(w for w in words if _YOUTH.match(w)), any(w in _WOMEN for w in words)


def _team_score(pick_name: str, live_name: str) -> float:
    """Ce parte din cuvintele specifice ale numelui din pronostic apar în numele
    din Superbet; 0 dacă diferă categoria (juniori / fete)."""
    if _category(pick_name) != _category(live_name):
        return 0.0
    wanted = _tokens(pick_name)
    if not wanted:
        return 0.0
    return len(wanted & _tokens(live_name)) / len(wanted)


def match_football(pick: FootballPick, matches: list[FootballMatch]) -> Optional[FootballMatch]:
    """Meciul Superbet al pronosticului: cel mai bine potrivit, cu cel puțin
    jumătate din cuvintele specifice ale fiecărei echipe regăsite (fără
    diacritice și fără cuvinte generice ca "City"/"FC") și aceeași categorie.
    CONFIRMAT (2026-10-17): o potrivire pe un singur cuvânt comun lua
    "Maccabi Netanya U19 vs Hapoel Haifa U19" drept "Maccabi Petah Tikva vs
    Maccabi Haifa"."""
    best, best_score = None, 0.0
    for m in matches:
        home, away = _team_score(pick.home, m.home), _team_score(pick.away, m.away)
        if home >= 0.5 and away >= 0.5 and home + away > best_score:
            best, best_score = m, home + away
    return best


def in_watch_window(pick: FootballPick, now: dt.datetime, deadline: dt.datetime) -> bool:
    """Meciul începe înainte de `deadline` și prima repriză nu s-a terminat deja."""
    kickoff = pick.kickoff_utc()
    if kickoff is None:
        return True
    return kickoff <= deadline and now <= kickoff + FIRST_HALF_WINDOW


def first_half_over(pick: FootballPick, now: dt.datetime) -> bool:
    """A trecut fereastra primei reprize (după ora de start din log)."""
    kickoff = pick.kickoff_utc()
    return kickoff is not None and now > kickoff + FIRST_HALF_WINDOW


def evaluate_first_half(match: FootballMatch, min_minute: int) -> Decision:
    if match.status == "FINISHED":
        return Decision(Outcome.DONE, "meci terminat")
    if match.goals > 0:
        return Decision(Outcome.DONE, f"gol înainte de prag ({match.home_goals}-{match.away_goals})")
    if match.label != FIRST_HALF_LABEL:
        if match.status == "STARTED":
            return Decision(Outcome.DONE, f"prima repriză s-a încheiat 0-0 ({match.label})")
        return Decision(Outcome.WAIT, "meciul nu a început")
    if match.minute is None or match.minute < min_minute:
        return Decision(Outcome.WAIT, f"0-0, minutul {match.minute}")
    return Decision(Outcome.ALERT, f"0-0 în minutul {match.minute}", threshold=min_minute)


def format_first_half_alert(pick: FootballPick, match: FootballMatch,
                            fh_over05: Optional[float], over25: Optional[float]) -> str:
    def odds(v: Optional[float]) -> str:
        return str(v) if v is not None else "indisponibilă"

    return "\n".join([
        f"⚽ 0-0 în prima repriză, minutul {match.minute}",
        f"{match.home} vs {match.away}",
        pick.league,
        f"Pronostic: Peste 2.5 (cotă pre-meci {pick.odds_over})",
        f"Prima repriză Peste 0.5: {odds(fh_over05)}",
        f"Meci Peste 2.5: {odds(over25)}",
        f"🌐 {match.url}",
    ])
