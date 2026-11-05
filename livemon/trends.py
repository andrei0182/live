"""Tendințe de >= 90% urmărite pe hârtie (cercetarea din 04–05.11.2026).

Dimineața se aleg meciurile zilei pentru 10 reguli (5 tenis, 5 fotbal), se
salvează în data/trends_log.csv (comis de workflow, ca să nu se piardă) și a
doua zi se completează rezultatul. Ținta e cât de des se repetă rezultatul,
nu profitul: la 90–96% cotele sunt ~1,02–1,10.

Tenis (oferta prematch Superbet; p = probabilitatea favoritului din cote, fără marjă):
- T1  p < 0,70 (meci echilibrat)        -> outsiderul face >= 4 game-uri   (~94,5%)
- T2  p >= 0,85                         -> favoritul câștigă la −1,5 game-uri (~92,4%)
- T3  p >= 0,80                         -> favoritul câștigă meciul       (~91,2%)
- T4  bărbați, outsiderul are >= 10 meciuri în istoric și face în medie
      >= 9 game-uri când pierde        -> outsiderul face >= 5 game-uri   (~91,7%)
- T5  p >= 0,90                         -> favoritul câștigă și sub 26,5 game-uri (~93,1%)
Fotbal (programul și istoricul BetExplorer; fără amicale și cupe; ambele echipe
cu >= 5 meciuri în ultimele 10, liga cu >= 30 de meciuri în istoric):
- F1  golaveraj/meci în ligă gazdă − oaspete >= 1,5 și goluri așteptate gazdă >= 1,8
                                        -> gazda marchează (~94,2%)
- F2  golaveraj în ligă >= 2 și gazda cu >= 0,6 puncte/meci mai mult (ultimele 10)
                                        -> gazda nu pierde, 1X (~93,6%)
- F3  liga >= 2,15 goluri/meci și goluri așteptate în meci >= 4,2
                                        -> gol în repriza a doua (~90,8%)
- F4  liga cu >= 91% meciuri sub 4.5 (ultimele 150) -> sub 4.5 goluri (~90,0%)
- F5  liga >= 74% peste 2.5, ambele echipe <= 50% sub 3.5 și echipa mai slabă
      primește >= 2,8 goluri/meci       -> peste 1.5 goluri (~96,4%)
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import html
import io
import logging
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from .superbet import LiveMatch, parse_live_event
from .ticket import HISTORY_URL, short_name

logger = logging.getLogger(__name__)

RO_TZ = ZoneInfo("Europe/Bucharest")
LOG_PATH = Path("data") / "trends_log.csv"
HISTORY_PATH = Path("data") / "football_history.csv"
HISTORY_DAYS = 400  # ligile au nevoie de ultimele 150 de meciuri, echipele de ultimele 40 din ligă
SHOW = 6  # câte meciuri afișăm pe regulă (le urmărim pe toate)
WON, LOST, VOID = "won", "lost", "void"
LOG_COLUMNS = ["date", "rule", "ref", "start_utc", "home", "away", "info", "result", "score"]

RULES = {
    "T1": ("🎾", "Meci echilibrat → cel mai slab face ≥4 game-uri", 94.5),
    "T2": ("🎾", "Favorit sub ~1,13 → câștigă la handicap −1,5 game-uri", 92.4),
    "T3": ("🎾", "Favorit sub ~1,20 → câștigă meciul", 91.2),
    "T4": ("🎾", "Bărbați, cel mai slab pierde strâns → face ≥5 game-uri", 91.7),
    "T5": ("🎾", "Favorit sub ~1,07 → câștigă și sub 26,5 game-uri", 93.1),
    "F1": ("⚽", "Gazda mult mai bună → gazda marchează", 94.2),
    "F2": ("⚽", "Gazda mult mai bună → gazda nu pierde (1X)", 93.6),
    "F3": ("⚽", "Ligă și echipe ofensive → gol în repriza a 2-a", 90.8),
    "F4": ("⚽", "Ligă „închisă” → sub 4.5 goluri", 90.0),
    "F5": ("⚽", "Varianta strictă → peste 1.5 goluri", 96.4),
}

_session = requests.Session()
_session.headers["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


# ============================================================ tenis

def _set_done(a: int, b: int, stb: bool) -> bool:
    hi, lo = max(a, b), min(a, b)
    if stb:
        return hi >= 10 and hi - lo >= 2
    return (hi == 6 and lo <= 4) or (hi == 7 and lo in (5, 6))


def match_games(sets: list[tuple[int, int]]) -> Optional[tuple[bool, int, int]]:
    """(primul a câștigat?, game-uri primul, game-uri al doilea) pentru un meci
    terminat normal (2 seturi câștigate, super tiebreak-ul = 1 game); None la
    abandon / scor incomplet."""
    if not sets or len(sets) > 3:
        return None
    wa = wb = ga = gb = 0
    for i, (a, b) in enumerate(sets):
        stb = i == 2 and max(a, b) >= 10
        if not _set_done(a, b, stb):
            return None
        wa, wb = wa + (a > b), wb + (b > a)
        ga += int(a > b) if stb else a
        gb += int(b > a) if stb else b
    if max(wa, wb) != 2 or wa == wb:
        return None
    return wa > wb, ga, gb


def _parse_sets(score: str) -> list[tuple[int, int]]:
    out = []
    for tok in (score or "").split():
        m = re.match(r"^(\d+)-(\d+)", tok)
        if not m:
            return []
        out.append((int(m.group(1)), int(m.group(2))))
    return out


@dataclass
class PlayerStats:
    n: int          # meciuri terminate normal, tot istoricul
    male: bool
    lose_games: Optional[float]  # media game-urilor când pierde (ultimele 30, >= 3 înfrângeri)


def tennis_stats(rows: list[dict]) -> dict[str, PlayerStats]:
    """Din istoricul TennisExplorer (results.csv.gz), pe nume scurt ("visker n.")."""
    n: dict[str, int] = defaultdict(int)
    last: dict[str, deque] = defaultdict(lambda: deque(maxlen=30))  # (a câștigat, game-uri proprii)
    male: dict[str, bool] = {}
    for r in sorted(rows, key=lambda r: (r["date"], r.get("match_id", ""))):
        g = match_games(_parse_sets(r.get("score", "")))
        if g is None or not g[0]:
            continue
        w, lo = r["winner_name"].lower(), r["loser_name"].lower()
        for name, won, games in ((w, True, g[1]), (lo, False, g[2])):
            n[name] += 1
            last[name].append((won, games))
            male[name] = r.get("tour_type") == "atp-single"
    out = {}
    for name, cnt in n.items():
        h = list(last[name])
        lost = [x[1] for x in h if not x[0]]
        out[name] = PlayerStats(cnt, male[name], sum(lost) / len(lost) if len(h) >= 5 and len(lost) >= 3 else None)
    return out


def fetch_tennis_rows(timeout: float = 60.0) -> list[dict]:
    try:
        resp = requests.get(HISTORY_URL, timeout=timeout)
        resp.raise_for_status()
        return list(csv.DictReader(io.StringIO(gzip.decompress(resp.content).decode())))
    except (requests.RequestException, OSError, ValueError) as exc:
        logger.warning("Tendințe: nu pot citi istoricul de tenis: %s", exc)
        return []


def _start(m: LiveMatch) -> Optional[dt.datetime]:
    try:
        return dt.datetime.strptime(m.start_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def tennis_selections(offer: list[LiveMatch], stats: dict[str, PlayerStats], today: dt.date,
                      now: dt.datetime) -> list[dict]:
    out, seen = [], set()
    for m in offer:
        start = _start(m)
        if (m.event_id in seen or not start or start <= now or start.astimezone(RO_TZ).date() != today
                or m.status not in ("", "NOT_STARTED") or not m.odds_home or not m.odds_away
                or "/" in m.home + m.away or m.odds_home == m.odds_away):
            continue
        seen.add(m.event_id)
        fav_home = m.odds_home < m.odds_away
        p = (1 / min(m.odds_home, m.odds_away)) / (1 / m.odds_home + 1 / m.odds_away)
        fav, dog = (m.home, m.away) if fav_home else (m.away, m.home)
        side = "home" if fav_home else "away"
        base = {"date": today.isoformat(), "ref": str(m.event_id), "start_utc": start.strftime("%Y-%m-%d %H:%M"),
                "home": m.home, "away": m.away, "result": "", "score": ""}
        odds = f"{m.odds_home:.2f}/{m.odds_away:.2f}"
        rules = []
        if p < 0.70:
            rules.append(("T1", f"fav={side};p={p:.3f};{dog} ≥4 game-uri;{odds}"))
        if p >= 0.85:
            rules.append(("T2", f"fav={side};p={p:.3f};{fav} −1,5 game-uri;{odds}"))
        if p >= 0.80:
            rules.append(("T3", f"fav={side};p={p:.3f};{fav} câștigă;{odds}"))
        if p >= 0.90:
            rules.append(("T5", f"fav={side};p={p:.3f};{fav} câștigă și sub 26,5;{odds}"))
        st = stats.get(short_name(dog))
        if st and st.male and st.n >= 10 and st.lose_games is not None and st.lose_games >= 9:
            rules.append(("T4", f"fav={side};p={p:.3f};{dog} ≥5 game-uri (media {st.lose_games:.1f} când pierde);{odds}"))
        out += [dict(base, rule=r, info=i) for r, i in rules]
    return out


def _info(row: dict) -> dict[str, str]:
    parts = row["info"].split(";")
    out = dict(p.split("=", 1) for p in parts if "=" in p)
    out["text"] = next((p for p in parts if "=" not in p), "")
    return out


def tennis_outcome(row: dict, event: dict) -> Optional[tuple[str, str]]:
    m = parse_live_event(event)
    score = " ".join(f"{h}-{a}" for h, a in m.set_games)
    g = match_games(m.set_games)
    if g is None:
        return VOID, score or "abandon"
    home_won, gh, ga = g
    fav_home = _info(row).get("fav") == "home"
    fav_won = home_won == fav_home
    gf, gd = (gh, ga) if fav_home else (ga, gh)
    ok = {"T1": gd >= 4, "T2": fav_won and gf - gd > 1.5, "T3": fav_won,
          "T4": gd >= 5, "T5": fav_won and gf + gd < 26.5}[row["rule"]]
    return (WON if ok else LOST), score


# ============================================================ fotbal (BetExplorer)

BE_DAY_URL = "https://www.betexplorer.com/football/results/?year={y}&month={m:02d}&day={d:02d}"
_LEAGUE_RE = re.compile(r'<a href="/football/([^/"]+)/[^"]*" class="table-main__tournament">.*?</i>([^<]+)</a>', re.S)
_ROW_RE = re.compile(r'<tr([^>]*\bdata-dt="[^"]*"[^>]*)>(.*?)</tr>', re.S)
_DT_RE = re.compile(r'data-dt="(\d+),(\d+),(\d+),(\d+),(\d+)"')
_DT_NOW_RE = re.compile(r'data-dt-now="(\d+),(\d+),(\d+),(\d+),(\d+)"')
_LINK_RE = re.compile(r'<td class="table-main__tt">.*?<a href="(/football/[^"]+)"[^>]*>(.*?)</a>', re.S)
_RESULT_RE = re.compile(r'class="table-main__result">(.*?)</td>', re.S)
_PARTIAL_RE = re.compile(r'class="table-main__partial"[^>]*>([^<]*)<')
_SCORE_RE = re.compile(r"(\d+)\s*:\s*(\d+)")
_TAG_RE = re.compile(r"<[^>]+>")
_FRIENDLY_RE = re.compile(r"Friendl", re.I)
_CUP_RE = re.compile(r"Cup|Copa|Coupe|Pokal|Coppa|Taca|Beker|Kupa|Puchar|Trophy|Shield|Libertadores|Sudamericana|"
                     r"Champions|Europa|Conference|Qualification|World|Euro|Nations|Play Off|Playoff", re.I)


@dataclass
class Fixture:
    match_id: str
    kickoff_utc: dt.datetime
    country: str
    league: str
    home: str
    away: str
    status: str = "scheduled"  # finished / live / scheduled
    ht_home: Optional[int] = None
    ht_away: Optional[int] = None
    ft_home: Optional[int] = None
    ft_away: Optional[int] = None


def _dt(m: re.Match) -> dt.datetime:
    d, mo, y, h, mi = (int(x) for x in m.groups())
    return dt.datetime(y, mo, d, h, mi)


def _text(fragment: str) -> str:
    return html.unescape(_TAG_RE.sub("", fragment)).strip()


def _offset(page: str, date_header: Optional[str]) -> dt.timedelta:
    now_m = _DT_NOW_RE.search(page)
    if now_m and date_header:
        try:
            utc_now = parsedate_to_datetime(date_header).astimezone(dt.timezone.utc).replace(tzinfo=None)
            hours = round((_dt(now_m) - utc_now).total_seconds() / 3600)
            if -12 <= hours <= 14:
                return dt.timedelta(hours=hours)
        except (TypeError, ValueError):
            pass
    return dt.timedelta(hours=1)


def parse_day_page(page: str, offset: dt.timedelta) -> list[Fixture]:
    events = sorted([(m.start(), "league", m) for m in _LEAGUE_RE.finditer(page)]
                    + [(m.start(), "row", m) for m in _ROW_RE.finditer(page)], key=lambda e: e[0])
    out, country, league = [], "", ""
    for _, kind, m in events:
        if kind == "league":
            country, league = m.group(1), html.unescape(m.group(2)).strip()
            continue
        attrs, body = m.group(1), m.group(2)
        dtm, link = _DT_RE.search(attrs), _LINK_RE.search(body)
        if not dtm or not link or not league:
            continue
        home, sep, away = _text(link.group(2)).partition(" - ")
        if not sep:
            continue
        fx = Fixture(link.group(1).rstrip("/").rsplit("/", 1)[-1],
                     (_dt(dtm) - offset).replace(tzinfo=dt.timezone.utc), country, league, home.strip(), away.strip())
        result = _RESULT_RE.search(body)
        if "data-live=" in attrs:
            fx.status = "live"
        elif result:
            ft = _SCORE_RE.fullmatch(_text(result.group(1)))
            if not ft:  # amânat, anulat...
                continue
            partial = _PARTIAL_RE.search(body)
            ht = _SCORE_RE.search(partial.group(1)) if partial else None
            fx.status = "finished"
            fx.ft_home, fx.ft_away = int(ft.group(1)), int(ft.group(2))
            if ht:
                fx.ht_home, fx.ht_away = int(ht.group(1)), int(ht.group(2))
        out.append(fx)
    return out


def fetch_day(day: dt.date, timeout: float = 40.0) -> Optional[list[Fixture]]:
    try:
        resp = _session.get(BE_DAY_URL.format(y=day.year, m=day.month, d=day.day), timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Tendințe: nu pot citi BetExplorer %s: %s", day, exc)
        return None
    return parse_day_page(resp.text, _offset(resp.text, resp.headers.get("Date")))


HIST_COLUMNS = ["match_id", "kickoff_utc", "country", "league", "home", "away", "ht_home", "ht_away", "ft_home",
                "ft_away"]


def _usable(fx: Fixture) -> bool:
    return (fx.status == "finished" and None not in (fx.ht_home, fx.ht_away, fx.ft_home, fx.ft_away)
            and fx.ht_home <= fx.ft_home and fx.ht_away <= fx.ft_away and not _FRIENDLY_RE.search(fx.league))


def load_history(path: Path = HISTORY_PATH) -> dict[str, Fixture]:
    if not path.exists():
        return {}
    out = {}
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out[r["match_id"]] = Fixture(r["match_id"], dt.datetime.fromisoformat(r["kickoff_utc"]), r["country"],
                                             r["league"], r["home"], r["away"], "finished", int(r["ht_home"]),
                                             int(r["ht_away"]), int(r["ft_home"]), int(r["ft_away"]))
            except (KeyError, ValueError):
                continue
    return out


def save_history(hist: dict[str, Fixture], today: dt.date, path: Path = HISTORY_PATH) -> None:
    cutoff = dt.datetime.combine(today - dt.timedelta(days=HISTORY_DAYS), dt.time(), tzinfo=dt.timezone.utc)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HIST_COLUMNS)
        for fx in sorted(hist.values(), key=lambda x: (x.kickoff_utc, x.match_id)):
            if fx.kickoff_utc >= cutoff:
                w.writerow([fx.match_id, fx.kickoff_utc.isoformat(), fx.country, fx.league, fx.home, fx.away,
                            fx.ht_home, fx.ht_away, fx.ft_home, fx.ft_away])


def _mean(xs) -> float:
    return sum(xs) / len(xs)


def football_features(history: list[Fixture], targets: list[Fixture]) -> dict[str, dict]:
    """Feature-urile pre-meci ale meciurilor `targets`, din `history` (doar meciuri de dinainte)."""
    team = defaultdict(lambda: deque(maxlen=10))     # (gf, ga, pts)
    team_lg = defaultdict(lambda: deque(maxlen=40))  # (pts, gf, ga) în liga respectivă
    lg = defaultdict(lambda: deque(maxlen=150))      # total goluri
    for fx in sorted(history, key=lambda x: x.kickoff_utc):
        h, a = fx.ft_home, fx.ft_away
        ph, pa = (3, 0) if h > a else ((1, 1) if h == a else (0, 3))
        team[(fx.country, fx.home)].append((h, a, ph))
        team[(fx.country, fx.away)].append((a, h, pa))
        team_lg[(fx.league, fx.country, fx.home)].append((ph, h, a))
        team_lg[(fx.league, fx.country, fx.away)].append((pa, a, h))
        lg[fx.league].append(h + a)
    out = {}
    for fx in targets:
        th, ta = team.get((fx.country, fx.home)), team.get((fx.country, fx.away))
        goals = lg.get(fx.league)
        if not th or not ta or len(th) < 5 or len(ta) < 5 or not goals or len(goals) < 30:
            continue

        def st(d):
            return {"gf": _mean([x[0] for x in d]), "ga": _mean([x[1] for x in d]), "ppg": _mean([x[2] for x in d]),
                    "u35": _mean([x[0] + x[1] <= 3 for x in d])}
        sh, sa = st(th), st(ta)
        lh, la = team_lg.get((fx.league, fx.country, fx.home)), team_lg.get((fx.league, fx.country, fx.away))
        f = {"cup": bool(_CUP_RE.search(fx.league)), "ln5": bool(lh and la and len(lh) >= 5 and len(la) >= 5),
             "ppg_gap": sh["ppg"] - sa["ppg"], "xg_h": (sh["gf"] + sa["ga"]) / 2,
             "xg": (sh["gf"] + sa["ga"] + sa["gf"] + sh["ga"]) / 2, "max_u35": max(sh["u35"], sa["u35"]),
             "lg_tg": _mean(goals), "lg_o25": _mean([g >= 3 for g in goals]), "lg_u45": _mean([g <= 4 for g in goals])}
        f["dog_ga"] = sa["ga"] if f["ppg_gap"] >= 0 else sh["ga"]
        f["lgd_gap"] = (_mean([x[1] - x[2] for x in lh]) - _mean([x[1] - x[2] for x in la])) if f["ln5"] else 0.0
        out[fx.match_id] = f
    return out


def football_rules(f: dict) -> list[tuple[str, str]]:
    if f["cup"]:
        return []
    out = []
    if f["ln5"] and f["lgd_gap"] >= 1.5 and f["xg_h"] >= 1.8:
        out.append(("F1", f"gazda marchează (golaveraj +{f['lgd_gap']:.1f})"))
    if f["ln5"] and f["lgd_gap"] >= 2 and f["ppg_gap"] >= 0.6:
        out.append(("F2", f"1X (golaveraj +{f['lgd_gap']:.1f})"))
    if f["lg_tg"] >= 2.15 and f["xg"] >= 4.2:
        out.append(("F3", f"gol în repriza 2 (goluri așteptate {f['xg']:.1f})"))
    if f["lg_u45"] >= 0.91:
        out.append(("F4", f"sub 4.5 (liga {f['lg_u45']:.0%} sub 4.5)"))
    if f["lg_o25"] >= 0.74 and f["max_u35"] <= 0.5 and f["dog_ga"] >= 2.8:
        out.append(("F5", f"peste 1.5 (liga {f['lg_o25']:.0%} peste 2.5)"))
    return out


def football_selections(page: list[Fixture], history: dict[str, Fixture], day: dt.date, now: dt.datetime) -> list[dict]:
    targets = [fx for fx in page if fx.status == "scheduled" and fx.kickoff_utc > now
               and not _FRIENDLY_RE.search(fx.league)]
    feats = football_features(list(history.values()), targets)
    out = []
    for fx in targets:
        f = feats.get(fx.match_id)
        for rule, info in (football_rules(f) if f else []):
            out.append({"date": day.isoformat(), "rule": rule, "ref": fx.match_id,
                        "start_utc": fx.kickoff_utc.strftime("%Y-%m-%d %H:%M"), "home": fx.home, "away": fx.away,
                        "info": f"{fx.league};{info}", "result": "", "score": ""})
    return out


def football_outcome(row: dict, fx: Fixture) -> Optional[tuple[str, str]]:
    if fx.status != "finished" or fx.ft_home is None:
        return None
    h, a = fx.ft_home, fx.ft_away
    score = f"{h}-{a}" + (f" (pauză {fx.ht_home}-{fx.ht_away})" if fx.ht_home is not None else "")
    if row["rule"] == "F3" and fx.ht_home is None:
        return VOID, score
    ok = {"F1": h > 0, "F2": h >= a, "F3": h + a > (fx.ht_home or 0) + (fx.ht_away or 0),
          "F4": h + a <= 4, "F5": h + a >= 2}[row["rule"]]
    return (WON if ok else LOST), score


# ============================================================ jurnal + texte

def load_log(path: Path = LOG_PATH) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_log(rows: list[dict], path: Path = LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in LOG_COLUMNS} for r in rows)


def resolve_tennis(rows: list[dict], finished_tennis: dict[int, dict]) -> int:
    n = 0
    for r in rows:
        if r["rule"].startswith("T") and not r["result"]:
            event = finished_tennis.get(int(r["ref"]))
            res = tennis_outcome(r, event) if event else None
            if res:
                r["result"], r["score"] = res
                n += 1
    return n


def resolve_football(rows: list[dict], fixtures: dict[str, Fixture]) -> int:
    n = 0
    for r in rows:
        if r["rule"].startswith("F") and not r["result"] and r["ref"] in fixtures:
            res = football_outcome(r, fixtures[r["ref"]])
            if res:
                r["result"], r["score"] = res
                n += 1
    return n


def _ro(start_utc: str) -> str:
    try:
        t = dt.datetime.strptime(start_utc, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return "--:--"
    return t.astimezone(RO_TZ).strftime("%H:%M")


def _pct(k: int, n: int) -> str:
    return f"{k / n:.0%}" if n else "–"


def _sort_key(r: dict):
    info = _info(r) if r["rule"].startswith("T") else {}
    p = float(info.get("p", 0) or 0)
    return (p if r["rule"] == "T1" else -p, r["start_utc"])


def results_text(rows: list[dict], day: str) -> str:
    """Rezultatele zilei `day` + totalul de la început, pe regulă."""
    lines = [f"📊 Rezultatele tendințelor din {day[8:]}.{day[5:7]} (total de la început între paranteze)"]
    for rule, (icon, name, exp) in RULES.items():
        mine = [r for r in rows if r["rule"] == rule and r["date"] == day]
        if not mine:
            continue
        done = [r for r in mine if r["result"] in (WON, LOST)]
        k = sum(r["result"] == WON for r in done)
        allr = [r for r in rows if r["rule"] == rule and r["result"] in (WON, LOST)]
        ka = sum(r["result"] == WON for r in allr)
        wait = sum(not r["result"] for r in mine)
        flag = " ⚠️" if len(allr) >= 100 and ka / len(allr) * 100 < 88 else ""
        lines.append(f"{icon} {rule} {name}: {k}/{len(done)} {_pct(k, len(done))}"
                     + (f", ⏳ {wait} fără rezultat" if wait else "")
                     + f" (total {ka}/{len(allr)} {_pct(ka, len(allr))}, așteptat ~{exp:.0f}%){flag}")
        lost = [r for r in done if r["result"] == LOST]
        for r in lost[:3]:
            lines.append(f"   ❌ {r['home']} – {r['away']}: {r['score']}")
        if len(lost) > 3:
            lines.append(f"   ❌ și încă {len(lost) - 3}")
    return "\n".join(lines) if len(lines) > 1 else ""


def today_text(rows: list[dict], day: dt.date) -> str:
    key = day.isoformat()
    lines = [f"📈 Tendințe de ≥90% – meciurile de azi, {day:%d.%m} (urmărire pe hârtie)"]
    for rule, (icon, name, exp) in RULES.items():
        mine = sorted([r for r in rows if r["rule"] == rule and r["date"] == key], key=_sort_key)
        if not mine:
            lines.append(f"\n{icon} {rule} {name} (~{exp:.0f}%): azi niciun meci")
            continue
        lines.append(f"\n{icon} {rule} {name} (~{exp:.0f}%): {len(mine)} meciuri")
        for r in sorted(mine[:SHOW], key=lambda r: r["start_utc"]):
            if rule.startswith("T"):
                info = _info(r)
                odds = r["info"].rsplit(";", 1)[-1]
                lines.append(f"  {_ro(r['start_utc'])} {r['home']} – {r['away']} ({odds}) → {info['text']}")
            else:
                league, _, what = r["info"].partition(";")
                lines.append(f"  {_ro(r['start_utc'])} {r['home']} – {r['away']} ({league}) → {what}")
        if len(mine) > SHOW:
            lines.append(f"  … și încă {len(mine) - SHOW} (le urmăresc pe toate)")
    lines.append("\nRezultatele vin mâine dimineață. La 90–96% cotele sunt ~1,02–1,10: e un test de "
                 "cât de des se repetă, nu un pont de pariat.")
    return "\n".join(lines)


def split_message(text: str, limit: int = 3900) -> list[str]:
    parts, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    return parts + ([cur] if cur else [])


def _page(day: dt.date, pages: dict) -> list[Fixture]:
    if day not in pages:
        pages[day] = fetch_day(day)
    return pages[day] or []


def day_fixtures(day: dt.date, pages: dict) -> list[Fixture]:
    """Meciurile cu data (ora RO) `day`. Pagina BetExplorer a unei zile are și
    meciuri din ziua dinainte / de după, așa că le combinăm pe două."""
    out = {fx.match_id: fx for d in (day, day + dt.timedelta(days=1)) for fx in _page(d, pages)}
    return [fx for fx in out.values() if fx.kickoff_utc.astimezone(RO_TZ).date() == day]


def update_football_history(hist: dict[str, Fixture], today: dt.date, pages: dict) -> None:
    """Adaugă rezultatele terminate din zilele de la ultimul meci din istoric
    (cel mult 5 zile în urmă) până azi."""
    last = max((fx.kickoff_utc for fx in hist.values()), default=None)
    day = today - dt.timedelta(days=5)
    if last:
        day = max(day, last.astimezone(RO_TZ).date() - dt.timedelta(days=1))
    while day <= today:
        for fx in _page(day, pages):
            if _usable(fx) and fx.kickoff_utc.astimezone(RO_TZ).date() < today:
                hist[fx.match_id] = fx
        day += dt.timedelta(days=1)


def morning(now: dt.datetime, offer: list[LiveMatch], finished_tennis: dict[int, dict]) -> list[str]:
    """Rezolvă ziua de ieri, alege meciurile de azi, salvează jurnalul și
    istoricul de fotbal; întoarce mesajele de trimis."""
    today = now.astimezone(RO_TZ).date()
    yesterday = today - dt.timedelta(days=1)
    rows = load_log()
    resolve_tennis(rows, finished_tennis)
    pages: dict[dt.date, Optional[list[Fixture]]] = {}
    for day in sorted({r["date"] for r in rows if r["rule"].startswith("F") and not r["result"]}):
        d = dt.date.fromisoformat(day)
        if today - dt.timedelta(days=4) <= d < today:
            resolve_football(rows, {fx.match_id: fx for fx in day_fixtures(d, pages)})
    hist = load_history()
    update_football_history(hist, today, pages)
    if not any(r["date"] == today.isoformat() for r in rows):
        stats = tennis_stats(fetch_tennis_rows())
        rows += tennis_selections(offer, stats, today, now)
        rows += football_selections(day_fixtures(today, pages), hist, today, now)
    save_history(hist, today)
    save_log(rows)
    msgs = []
    res = results_text(rows, yesterday.isoformat())
    if res:
        msgs.append(res)
    msgs.append(today_text(rows, today))
    return [part for m in msgs for part in split_message(m)]


def evening(finished_tennis: dict[int, dict]) -> None:
    """Completează rezultatele de tenis deja terminate (fără mesaj)."""
    rows = load_log()
    if resolve_tennis(rows, finished_tennis):
        save_log(rows)
