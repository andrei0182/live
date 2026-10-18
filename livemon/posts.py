"""Postările zilnice în canal: programul de dimineață și bilanțul de seară
(duminică seara și bilanțul săptămânii).

Alertele trimise se țin în `state["alerts"]` (cu `event_id`-ul Superbet), iar
rezultatele se iau din indexul `live` al Superbet, care păstrează și meciurile
terminate cu scorul complet. CONFIRMAT (2026-10-18) via curl: meciurile de ieri
(ex. "Chrudim B·Letohrad", FINISHED, periods 1-0 / 0-1) încă apar acolo.
O alertă al cărei meci nu s-a terminat la postarea de seară primește
rezultatul în postarea de dimineață.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Optional
from zoneinfo import ZoneInfo

from .football import FootballPick, match_football, parse_football_event
from .picks import Pick, match_pick
from .superbet import FOOTBALL_SPORT_ID, TENNIS_SPORT_ID, LiveMatch, fetch_events, parse_live_event

logger = logging.getLogger(__name__)

ALERTS_KEEP_DAYS = 8  # destul pentru bilanțul săptămânal
FINISHED = "FINISHED"
WON, LOST = "won", "lost"


# ---------- jurnalul alertelor ----------

def record_alert(state: dict, *, date: str, kind: str, event_id: int, home: str, away: str,
                 player: str = "", side: str = "", value: Optional[float] = None,
                 time: str = "") -> None:
    """`kind`: "set1" / "games" (tenis) sau "fh" (fotbal, 0-0 în prima repriză).
    `value`: cota live (set1) sau linia live de game-uri (games)."""
    state.setdefault("alerts", []).append({
        "date": date, "kind": kind, "event_id": event_id, "home": home, "away": away,
        "player": player, "side": side, "value": value, "time": time, "outcome": None, "detail": "",
    })


def prune_alerts(state: dict, today: dt.date) -> None:
    cutoff = (today - dt.timedelta(days=ALERTS_KEEP_DAYS)).isoformat()
    state["alerts"] = [a for a in state.get("alerts", []) if a["date"] >= cutoff]


# ---------- rezultate din indexul `live` ----------

@dataclass
class Finished:
    tennis: dict[int, dict]  # event_id -> event brut, doar FINISHED
    football: dict[int, dict]

    @classmethod
    def fetch(cls, today_utc: dt.date) -> "Finished":
        def done(sport: int) -> dict[int, dict]:
            return {e["event_id"]: e for e in fetch_events("live", today_utc, sport=sport)
                    if (e.get("inplay_stats_metadata") or {}).get("status") == FINISHED}
        return cls(tennis=done(TENNIS_SPORT_ID), football=done(FOOTBALL_SPORT_ID))


def _periods(event: dict) -> list[tuple[int, int]]:
    periods = sorted((event.get("inplay_stats") or {}).get("periods") or [], key=lambda p: int(p.get("num", 0)))
    return [(int(p.get("home_team_score") or 0), int(p.get("away_team_score") or 0)) for p in periods]


def _score_text(event: dict) -> str:
    return " ".join(f"{h}-{a}" for h, a in _periods(event))


def resolve_alert(alert: dict, finished: Finished) -> bool:
    """Completează `outcome` / `detail` dacă meciul s-a terminat. Întoarce True
    dacă alerta a primit acum un rezultat."""
    if alert.get("outcome"):
        return False
    if alert["kind"] in ("set1", "games"):
        event = finished.tennis.get(alert["event_id"])
        if event is None:
            return False
        m = parse_live_event(event)
        mine = m.sets_home if alert["side"] == "home" else m.sets_away
        theirs = m.sets_away if alert["side"] == "home" else m.sets_home
        if alert["kind"] == "set1":
            alert["outcome"] = WON if mine > theirs else LOST
            alert["detail"] = f"{alert['player']} {'a câștigat' if mine > theirs else 'a pierdut'} ({_score_text(event)})"
        else:
            idx = 0 if alert["side"] == "home" else 1
            games = sum(s[idx] for s in m.set_games)
            line = alert["value"]
            alert["outcome"] = WON if line is not None and games > line else LOST
            alert["detail"] = f"{alert['player']}: {games} game-uri, linia live era {line} ({_score_text(event)})"
        return True

    event = finished.football.get(alert["event_id"])
    if event is None:
        return False
    periods = _periods(event)
    fh_goals = sum(periods[0]) if periods else 0
    m = parse_football_event(event)
    alert["outcome"] = WON if fh_goals > 0 else LOST
    final = f"final {m.home_goals}-{m.away_goals}"
    alert["detail"] = f"pauză {periods[0][0]}-{periods[0][1]}, {final}" if periods else final
    return True


# ---------- textele ----------

_KIND = {"set1": "🎾 Set 1 pierdut", "games": "📉 Linia de game-uri", "fh": "⚽ 0-0 în prima repriză"}
_MARK = {WON: "✅", LOST: "❌", None: "⏳"}


def _alert_line(a: dict) -> str:
    who = f"{a['home']} vs {a['away']}"
    extra = a["detail"] or "meciul nu s-a terminat încă"
    return f"{_MARK[a['outcome']]} {_KIND[a['kind']]} · {who}\n    {extra}"


def _tally(alerts: list[dict]) -> str:
    won = sum(a["outcome"] == WON for a in alerts)
    lost = sum(a["outcome"] == LOST for a in alerts)
    pending = sum(a["outcome"] is None for a in alerts)
    text = f"{won} ✅ · {lost} ❌"
    return text + (f" · {pending} ⏳" if pending else "")


def morning_post(today: dt.date, tennis: list[tuple[Pick, Optional[LiveMatch]]], football: list[FootballPick],
                 late: list[dict], min_minute: int) -> str:
    lines = [f"📅 Programul de azi, {today.strftime('%d.%m.%Y')}", ""]
    if not tennis and not football:
        lines.append("Azi nu avem meciuri în program.")
    if tennis:
        lines.append("🎾 Tenis")
        for p, m in tennis:
            when = _ro_time(m.start_time) if m and m.start_time else "?"
            lines.append(f"• {when} {p.player} vs {p.opponent} (model {p.comp_pct:.0f}% · {p.source})")
        lines.append("")
    if football:
        lines.append("⚽ Fotbal, pronostic Peste 2.5")
        for p in football:
            lines.append(f"• {p.kickoff_local} {p.home} vs {p.away} ({p.league})")
        lines.append("")
    lines.append("Alerte live: 🎾 favoritul pierde setul 1 · 📉 linia lui de game-uri scade · "
                 f"⚽ e încă 0-0 în minutul {min_minute}.")
    if late:
        lines += ["", "Rezultatele alertelor de ieri, terminate după postarea de seară:"]
        lines += [_alert_line(a) for a in late]
    lines += ["", "18+ · Joacă responsabil."]
    return "\n".join(lines)


def evening_post(today: dt.date, alerts: list[dict], tennis_results: list[str], football_results: list[str],
                 week: Optional[list[dict]] = None) -> str:
    lines = [f"📊 Bilanțul zilei, {today.strftime('%d.%m.%Y')}", ""]
    if alerts:
        lines.append(f"Alertele de azi: {_tally(alerts)}")
        lines += [_alert_line(a) for a in alerts]
    else:
        lines.append("Azi n-a fost nicio alertă.")
    if tennis_results or football_results:
        lines += ["", "Pronosticurile zilei:"]
        lines += tennis_results + football_results
    if week is not None:
        lines += ["", f"🗓 Săptămâna aceasta: {len(week)} alerte · {_tally(week)}" if week
                  else "🗓 Săptămâna aceasta n-a fost nicio alertă."]
    lines += ["", "Publicăm toate rezultatele, inclusiv pierderile. 18+ · Joacă responsabil."]
    return "\n".join(lines)


def _ro_time(start_utc: str) -> str:
    try:
        t = dt.datetime.strptime(start_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return "?"
    return t.astimezone(ZoneInfo("Europe/Bucharest")).strftime("%H:%M")


def tennis_pick_result(p: Pick, finished: list[LiveMatch], raw: dict[int, dict]) -> str:
    found = match_pick(p, finished)
    if not found:
        return f"⏳ {p.player} vs {p.opponent}: fără rezultat încă"
    m, side = found
    mine = m.sets_home if side == "home" else m.sets_away
    theirs = m.sets_away if side == "home" else m.sets_home
    mark = "✅" if mine > theirs else "❌"
    return f"{mark} 🎾 {p.player} vs {p.opponent} ({_score_text(raw[m.event_id])})"


def football_pick_result(p: FootballPick, raw: dict[int, dict]) -> str:
    matches = [parse_football_event(e) for e in raw.values()]
    m = match_football(p, matches)
    if m is None:
        return f"⏳ {p.home} vs {p.away}: fără rezultat încă"
    mark = "✅" if m.goals > 2.5 else "❌"
    return f"{mark} ⚽ {p.home} vs {p.away}: {m.home_goals}-{m.away_goals} (Peste 2.5)"
