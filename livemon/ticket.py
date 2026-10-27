"""Biletul zilei: 2 meciuri din pick-urile Model 90% ("castiga meciul"), cu cota
totală 1.80–2.30, publicat dimineața în canal, cu rezultatul și bilanțul seara.

Reguli (alese după testul pe istoric din oct. 2026):
- probabilitatea modelului >= MIN_PROB;
- jucătorul ales e odihnit: cel mult 1 meci în ultimele 3 zile;
- adversarul are cel puțin MIN_OPP_MATCHES meciuri în istoric (altfel modelul
  ghicește: Atay vs Dmitruk, Visker vs Bouquier);
- cota de acum din Superbet <= MAX_ODDS (peste, modelul contrazice total piața);
- meciul nu a început încă.
Dintre perechile rămase se alege cea cu produsul probabilităților cel mai mare.
Probabilitatea modelului e optimistă: în test, biletele de acest tip au ieșit
~55–60%, nu cât spune produsul.

Biletele stau în `state["tickets"]` (data -> bilet), fără curățare, ca bilanțul
să cuprindă toate biletele publicate.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import itertools
import logging
from collections import Counter
from typing import Optional

import requests

from .picks import WIN_MARKET, Pick, match_pick
from .superbet import LiveMatch, parse_live_event

logger = logging.getLogger(__name__)

HISTORY_URL = "https://raw.githubusercontent.com/andrei0182/pariuri/main/tennis/data/results.csv.gz"
MIN_PROB = 80.0
MIN_OPP_MATCHES = 5
MAX_LOAD = 1          # meciuri în ultimele 3 zile
MAX_ODDS = 3.0
TOTAL_ODDS = (1.80, 2.30)
WON, LOST = "won", "lost"


def short_name(name: str) -> str:
    """"Niels Visker" -> "visker n." (formatul din istoricul TennisExplorer)."""
    parts = name.split()
    return (f"{' '.join(parts[1:])} {parts[0][0]}." if len(parts) > 1 else name).lower()


class History:
    """Câte meciuri are fiecare jucător în istoric și câte în ultimele 3 zile."""

    def __init__(self, rows: list[dict], today: dt.date):
        since = (today - dt.timedelta(days=3)).isoformat()
        self.total: Counter = Counter()
        self.recent: Counter = Counter()
        for r in rows:
            for n in (r["winner_name"].lower(), r["loser_name"].lower()):
                self.total[n] += 1
                if r["date"] >= since:
                    self.recent[n] += 1

    @classmethod
    def fetch(cls, today: dt.date, timeout: float = 60.0) -> Optional["History"]:
        try:
            resp = requests.get(HISTORY_URL, timeout=timeout)
            resp.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(gzip.decompress(resp.content).decode())))
        except (requests.RequestException, OSError, ValueError) as exc:
            logger.warning("Nu pot citi istoricul pentru biletul zilei: %s", exc)
            return None
        return cls(rows, today)

    def matches(self, name: str) -> int:
        return self.total[short_name(name)]

    def load(self, name: str) -> int:
        return self.recent[short_name(name)]


def candidates(picks: list[Pick], offer: list[LiveMatch], hist: History, now: dt.datetime) -> list[dict]:
    out, seen = [], set()
    for p in picks:
        if p.market not in ("", WIN_MARKET) or (p.comp_pct or 0) < MIN_PROB:
            continue
        found = match_pick(p, offer)
        if not found:
            continue
        m, side = found
        odds = m.odds_home if side == "home" else m.odds_away
        if m.event_id in seen or m.status not in ("", "NOT_STARTED") or odds is None or odds > MAX_ODDS:
            continue
        try:
            start = dt.datetime.strptime(m.start_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        if start <= now or hist.load(p.player) > MAX_LOAD or hist.matches(p.opponent) < MIN_OPP_MATCHES:
            continue
        seen.add(m.event_id)
        out.append({"player": p.player, "opp": p.opponent, "prob": p.comp_pct, "odds": odds,
                    "event_id": m.event_id, "side": side, "start": m.start_time, "result": None, "score": ""})
    return out


def choose(cands: list[dict]) -> list[list[dict]]:
    """Perechile valide, cea mai bună prima."""
    lo, hi = TOTAL_ODDS
    pairs = [(a["prob"] * b["prob"], [a, b]) for a, b in itertools.combinations(cands, 2)
             if lo <= a["odds"] * b["odds"] <= hi]
    return [legs for _, legs in sorted(pairs, key=lambda x: -x[0])]


def total_odds(ticket: dict) -> float:
    o = 1.0
    for leg in ticket["legs"]:
        o *= leg["odds"]
    return round(o, 2)


def resolve(ticket: dict, finished_tennis: dict[int, dict]) -> bool:
    """Completează rezultatele meciurilor terminate; True dacă biletul a primit acum un rezultat."""
    if ticket.get("outcome"):
        return False
    for leg in ticket["legs"]:
        event = finished_tennis.get(leg["event_id"]) if leg["result"] is None else None
        if event is None:
            continue
        m = parse_live_event(event)
        mine, theirs = (m.sets_home, m.sets_away) if leg["side"] == "home" else (m.sets_away, m.sets_home)
        leg["result"] = WON if mine > theirs else LOST
        leg["score"] = " ".join(f"{h}-{a}" if leg["side"] == "home" else f"{a}-{h}" for h, a in m.set_games)
    results = [leg["result"] for leg in ticket["legs"]]
    if LOST in results:
        ticket["outcome"] = LOST
    elif all(r == WON for r in results):
        ticket["outcome"] = WON
    return bool(ticket.get("outcome"))


def profit(ticket: dict) -> float:
    if ticket.get("outcome") == WON:
        return total_odds(ticket) - 1
    return -1.0 if ticket.get("outcome") == LOST else 0.0


def _ro(start_utc: str) -> str:
    from zoneinfo import ZoneInfo
    t = dt.datetime.strptime(start_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
    return t.astimezone(ZoneInfo("Europe/Bucharest")).strftime("%H:%M")


def stats_line(tickets: dict) -> str:
    done = [t for t in tickets.values() if t.get("legs") and t.get("outcome")]
    if not done:
        return "Bilanț bilete: primul bilet abia a fost publicat."
    won = sum(t["outcome"] == WON for t in done)
    units = sum(profit(t) for t in done)
    return (f"Bilanț bilete: {won} ✅ · {len(done) - won} ❌ din {len(done)} "
            f"({won / len(done) * 100:.0f}%) · {units:+.2f} unități la miză 1")


def morning_text(ticket: Optional[dict]) -> str:
    if not ticket or not ticket.get("legs"):
        return "🎟 Biletul zilei: azi nu e bilet (nicio pereche nu trece filtrele)."
    lines = [f"🎟 Biletul zilei (cota totală ≈ {total_odds(ticket):.2f})"]
    for leg in ticket["legs"]:
        lines.append(f"• {_ro(leg['start'])} {leg['player']} câștigă vs {leg['opp']} @ {leg['odds']:.2f}")
    lines.append("Model 90%, filtrat: jucători odihniți, adversari cu istoric. "
                 "Realist ~55–60% șanse, nu o garanție.")
    return "\n".join(lines)


def result_text(ticket: Optional[dict], tickets: dict) -> str:
    if not ticket or not ticket.get("legs"):
        has_done = any(t.get("legs") and t.get("outcome") for t in tickets.values())
        return stats_line(tickets) if has_done else ""
    mark = {WON: "✅", LOST: "❌", None: "⏳"}
    head = {WON: f"🎟 Biletul zilei: ✅ câștigat ({total_odds(ticket):.2f}, {profit(ticket):+.2f} u)",
            LOST: "🎟 Biletul zilei: ❌ pierdut (−1 u)", None: "🎟 Biletul zilei: ⏳ în desfășurare"}
    lines = [head[ticket.get("outcome")]]
    for leg in ticket["legs"]:
        lines.append(f"  {mark[leg['result']]} {leg['player']} @ {leg['odds']:.2f} {leg['score']}".rstrip())
    lines.append(stats_line(tickets))
    return "\n".join(lines)
