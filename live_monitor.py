"""Urmărește live pick-urile de azi și trimite alerte pe Telegram:

- set 1: favoritul modelului pierde setul 1 și cota lui live trece de prag;
- game-uri: linia live la "Total game-uri <favorit>" scade cu cel puțin
  --games-drop față de linia principală pre-meci;
- fotbal: un meci cu pronostic Peste 2.5 e încă 0-0 în prima repriză după
  minutul --fh-minute.

Tot el publică, o dată pe zi, programul de dimineață (după --post-morning) și
bilanțul de seară (după --post-evening; duminica și bilanțul săptămânii).

Rulează cel mult --max-minutes, verificând la fiecare --interval secunde, și
se oprește mai devreme dacă niciun pick nu e live sau nu începe în fereastra
asta. În --state se țin regulile deja tratate per pick (alertă trimisă sau
regulă imposibilă), ca rulările următoare să nu retrimită alerta, și liniile
de game-uri pre-meci.

    python live_monitor.py                  # o rulare normală
    python live_monitor.py --test-telegram  # verifică doar botul
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from livemon import telegram
from livemon.football import (FIRST_HALF_MARKET, MATCH_TOTAL_MARKET, FootballPick, evaluate_first_half,
                              fetch_football_live, fetch_football_picks, first_half_over,
                              format_first_half_alert, in_watch_window, match_football)
from livemon.picks import Pick, fetch_pending_picks, match_pick
from livemon.posts import (Finished, evening_post, football_pick_result, morning_post, prune_alerts, record_alert,
                           resolve_alert, tennis_pick_result)
from livemon import ticket as tk
from livemon.rules import (Decision, Outcome, RuleConfig, evaluate, evaluate_games, format_alert,
                           format_games_alert)
from livemon.superbet import (LiveMatch, fetch_event_markets, fetch_live_matches, fetch_prematch_matches,
                              market_odds, parse_live_event, player_games_main_line)

logger = logging.getLogger("live_monitor")

RO_TZ = ZoneInfo("Europe/Bucharest")

RULES = ("set1", "games")


def rule_key(pick: Pick, rule: str) -> str:
    # regula set 1 păstrează cheia veche (fără sufix), ca starea salvată să rămână validă
    return pick.key if rule == "set1" else f"{pick.key}|{rule}"


def pending_rules(pick: Pick, state: dict) -> list[str]:
    return [r for r in RULES if rule_key(pick, r) not in state["done"]]


def load_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    state.setdefault("done", {})
    state.setdefault("prematch_lines", {})
    state.setdefault("alerts", [])
    state.setdefault("posted", {})
    state.setdefault("tickets", {})  # biletul zilei, păstrat pentru bilanț
    return state


def save_state(path: Path, state: dict, today: dt.date) -> None:
    # păstrăm doar ultimele 3 zile, ca fișierul să nu crească la nesfârșit
    cutoff = (today - dt.timedelta(days=3)).isoformat()
    for section in ("done", "prematch_lines", "posted"):
        state[section] = {k: v for k, v in state[section].items() if k.split("|", 1)[0] >= cutoff}
    prune_alerts(state, today)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def starts_before(match: LiveMatch, deadline: dt.datetime) -> bool:
    """`event_date` de la Superbet e în UTC, format "YYYY-MM-DD HH:MM:SS"."""
    try:
        start = dt.datetime.strptime(match.start_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return True  # format necunoscut: mai bine așteptăm decât să-l ratăm
    return start <= deadline


def player_name(match: LiveMatch, side: str) -> str:
    return match.home if side == "home" else match.away


def save_prematch_line(pick: Pick, match: LiveMatch, side: str, state: dict) -> None:
    """Salvează (suprascrie) linia principală pre-meci a favoritului; ultima
    citire înainte de start rămâne referința pentru regula de game-uri."""
    markets = fetch_event_markets(match.event_id)
    line = player_games_main_line(markets or [], player_name(match, side))
    if line is not None:
        state["prematch_lines"][pick.key] = line


def check_rule(rule: str, pick: Pick, match: LiveMatch, side: str, state: dict,
               cfg: RuleConfig) -> tuple[Decision, str]:
    if rule == "set1":
        decision = evaluate(pick, match, side, cfg)
        return decision, format_alert(pick, match, decision) if decision.outcome is Outcome.ALERT else ""
    prematch_line = state["prematch_lines"].get(pick.key)
    live_line = None
    if prematch_line is not None and match.status != "FINISHED":
        markets = fetch_event_markets(match.event_id)
        live_line = player_games_main_line(markets or [], player_name(match, side))
    decision = evaluate_games(pick, match, prematch_line, live_line, cfg)
    text = format_games_alert(pick, match, prematch_line, decision) if decision.outcome is Outcome.ALERT else ""
    return decision, text


def select_tennis(picks: list[Pick], live: list[LiveMatch], prematch: list[LiveMatch],
                  deadline: dt.datetime, state: dict) -> list[Pick]:
    """Pick-urile de tenis de urmărit în rularea asta. Cele care nu sunt nici
    live, nici nu încep înainte de deadline rămân pentru rularea următoare.
    Linia de game-uri pre-meci o salvăm pentru toate pick-urile încă neîncepute,
    ca să existe referința chiar dacă o rulare întârzie și meciul pornește
    între două rulări."""
    watch: list[Pick] = []
    for p in picks:
        if match_pick(p, live):
            watch.append(p)
            continue
        found = match_pick(p, prematch)
        if found:
            save_prematch_line(p, *found, state)
        if found and starts_before(found[0], deadline):
            watch.append(p)
        elif found:
            logger.info("%s începe la %s UTC, după fereastra acestei rulări.", p.player, found[0].start_time)
        else:
            logger.info("%s vs %s nu e în oferta Superbet live/prematch.", p.player, p.opponent)
    return watch


def ro_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).astimezone(RO_TZ)


def check_tennis(watch: list[Pick], live: list[LiveMatch], prematch: list[LiveMatch], state: dict,
                 cfg: RuleConfig) -> None:
    for p in list(watch):
        found = match_pick(p, live)
        if not found:
            pre = match_pick(p, prematch)
            if pre:
                save_prematch_line(p, *pre, state)
            continue
        match, side = found
        for rule in pending_rules(p, state):
            decision, text = check_rule(rule, p, match, side, state, cfg)
            logger.info("%s [%s] (%s) %s: %s", p.player, rule, match.score_text(),
                        decision.outcome.value, decision.reason)
            if decision.outcome is Outcome.ALERT:
                if not telegram.send(text):
                    continue  # încercăm din nou la iterația următoare
                now = ro_now()
                record_alert(state, date=now.date().isoformat(), kind=rule, event_id=match.event_id,
                             home=match.home, away=match.away, player=p.player, side=side,
                             value=decision.live_value, time=now.strftime("%H:%M"))
            if decision.outcome in (Outcome.ALERT, Outcome.DONE):
                state["done"][rule_key(p, rule)] = decision.reason
        if not pending_rules(p, state):
            watch.remove(p)


def check_football(watch: list[FootballPick], now: dt.datetime, state: dict, min_minute: int) -> None:
    live = fetch_football_live(now.date())
    for p in list(watch):
        match = match_football(p, live)
        if match is None:
            if first_half_over(p, now):
                logger.info("%s vs %s: prima repriză a trecut și meciul nu e live.", p.home, p.away)
                watch.remove(p)
            continue
        decision = evaluate_first_half(match, min_minute)
        logger.info("%s vs %s [fotbal] (%s-%s, %s' %s) %s: %s", p.home, p.away, match.home_goals,
                    match.away_goals, match.minute, match.label, decision.outcome.value, decision.reason)
        if decision.outcome is Outcome.ALERT:
            markets = fetch_event_markets(match.event_id) or []
            text = format_first_half_alert(p, match, market_odds(markets, FIRST_HALF_MARKET, "Peste 0.5"),
                                           market_odds(markets, MATCH_TOTAL_MARKET, "Peste 2.5"))
            if not telegram.send(text):
                continue  # încercăm din nou la iterația următoare
            ro = ro_now()
            record_alert(state, date=ro.date().isoformat(), kind="fh", event_id=match.event_id,
                         home=match.home, away=match.away, time=ro.strftime("%H:%M"))
        if decision.outcome in (Outcome.ALERT, Outcome.DONE):
            state["done"][p.key] = decision.reason
            watch.remove(p)


def morning_ticket(state: dict, today: dt.date, yesterday: str, picks: list[Pick], offer: list[LiveMatch],
                   finished: Finished, now: dt.datetime) -> str:
    """Rezultatul întârziat al biletului de ieri + biletul de azi (ales o singură dată pe zi)."""
    parts = []
    prev = state["tickets"].get(yesterday)
    if prev and prev.get("legs") and tk.resolve(prev, finished.tennis):
        parts.append("Biletul de ieri, terminat după postarea de seară:\n" + tk.result_text(prev, state["tickets"]))
    key = today.isoformat()
    if key not in state["tickets"]:
        hist = tk.History.fetch(today)
        pairs = tk.choose(tk.candidates(picks, offer, hist, now)) if hist else []
        state["tickets"][key] = {"date": key, "legs": pairs[0] if pairs else [], "outcome": None}
        logger.info("Biletul zilei: %s", [leg["player"] for leg in state["tickets"][key]["legs"]] or "fără bilet")
    parts.append(tk.morning_text(state["tickets"][key]))
    return "\n\n".join(parts)


def maybe_post(state: dict, args: argparse.Namespace, now: dt.datetime) -> None:
    """Programul de dimineață și bilanțul de seară, fiecare o singură dată pe zi,
    la prima rulare de după ora configurată."""
    now_ro = now.astimezone(RO_TZ)
    today = now_ro.date()
    hhmm = now_ro.strftime("%H:%M")
    posted = state["posted"]
    key_m, key_e = f"{today}|morning", f"{today}|evening"
    need_m = hhmm >= args.post_morning and key_m not in posted
    need_e = hhmm >= args.post_evening and key_e not in posted
    if not (need_m or need_e):
        return

    finished = Finished.fetch(now.date())
    tennis_today = [p for p in fetch_pending_picks(today) if p.date == today.isoformat()]
    football_today = [p for p in fetch_football_picks(today) if p.date == today.isoformat()]

    if need_m:
        yesterday = (today - dt.timedelta(days=1)).isoformat()
        late = [a for a in state["alerts"] if a["date"] == yesterday and resolve_alert(a, finished)]
        offer = fetch_live_matches(now.date()) + fetch_prematch_matches(now.date())
        program = []
        for p in tennis_today:
            found = match_pick(p, offer)
            program.append((p, found[0] if found else None))
        program.sort(key=lambda x: x[1].start_time if x[1] else "")
        ticket_text = morning_ticket(state, today, yesterday, tennis_today, offer, finished, now)
        text = morning_post(today, program, sorted(football_today, key=lambda p: p.kickoff_local), late,
                            args.fh_minute, ticket_text)
        if telegram.send(text):
            posted[key_m] = hhmm
            logger.info("Postarea de dimineață trimisă.")

    if need_e:
        alerts = [a for a in state["alerts"] if a["date"] == today.isoformat()]
        for a in alerts:
            resolve_alert(a, finished)
        finished_tennis = [parse_live_event(e) for e in finished.tennis.values()]
        tennis_results = [tennis_pick_result(p, finished_tennis, finished.tennis) for p in tennis_today]
        football_results = [football_pick_result(p, finished.football) for p in football_today]
        week = None
        if today.weekday() == 6:  # duminică
            start = (today - dt.timedelta(days=6)).isoformat()
            week = [a for a in state["alerts"] if a["date"] >= start]
            for a in week:
                resolve_alert(a, finished)
        today_ticket = state["tickets"].get(today.isoformat())
        if today_ticket:
            tk.resolve(today_ticket, finished.tennis)
        text = evening_post(today, alerts, tennis_results, football_results, week,
                            tk.result_text(today_ticket, state["tickets"]))
        if telegram.send(text):
            posted[key_e] = hhmm
            logger.info("Postarea de seară trimisă.")


def run(args: argparse.Namespace) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    today_ro = now.astimezone(RO_TZ).date()
    deadline = now + dt.timedelta(minutes=args.max_minutes)
    cfg = RuleConfig(odds_factor=args.odds_factor, min_odds=args.min_odds, min_model_pct=args.min_model_pct,
                     games_drop=args.games_drop)
    state_path = Path(args.state)
    state = load_state(state_path)
    if args.posts:
        maybe_post(state, args, now)
        save_state(state_path, state, today_ro)

    tennis_picks = [p for p in fetch_pending_picks(today_ro) if pending_rules(p, state)]
    live: list[LiveMatch] = []
    prematch: list[LiveMatch] = []
    tennis: list[Pick] = []
    if tennis_picks:
        live = fetch_live_matches(now.date())
        prematch = fetch_prematch_matches(now.date())
        tennis = select_tennis(tennis_picks, live, prematch, deadline, state)

    football = [p for p in fetch_football_picks(today_ro)
                if p.key not in state["done"] and in_watch_window(p, now, deadline)]
    save_state(state_path, state, today_ro)
    if not tennis and not football:
        logger.info("Niciun pick de urmărit în fereastra asta (%s).", today_ro)
        return

    while tennis or football:
        if tennis:
            check_tennis(tennis, live, prematch, state, cfg)
        if football:
            check_football(football, dt.datetime.now(dt.timezone.utc), state, args.fh_minute)
        save_state(state_path, state, today_ro)

        if not (tennis or football) or args.once or \
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=args.interval) > deadline:
            break
        time.sleep(args.interval)
        if tennis:
            today_utc = dt.datetime.now(dt.timezone.utc).date()
            live = fetch_live_matches(today_utc)
            if any(not match_pick(p, live) for p in tennis):
                prematch = fetch_prematch_matches(today_utc)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-minutes", type=float, default=float(os.environ.get("MAX_MINUTES", 55)))
    parser.add_argument("--interval", type=int, default=int(os.environ.get("POLL_SECONDS", 60)))
    parser.add_argument("--odds-factor", type=float, default=float(os.environ.get("ODDS_FACTOR", 1.5)),
                        help="prag = cota pre-meci × factor (implicit 1.5)")
    parser.add_argument("--min-odds", type=float, default=float(os.environ.get("MIN_ODDS", 1.8)),
                        help="pragul nu coboară sub această cotă (implicit 1.8)")
    parser.add_argument("--min-model-pct", type=float, default=float(os.environ.get("MIN_MODEL_PCT", 50)),
                        help="comp_pct minim ca jucătorul să fie considerat favorit (implicit 50)")
    parser.add_argument("--games-drop", type=float, default=float(os.environ.get("GAMES_DROP", 2)),
                        help="cu câte game-uri trebuie să scadă linia live sub cea pre-meci (implicit 2)")
    parser.add_argument("--fh-minute", type=int, default=int(os.environ.get("FH_MINUTE", 25)),
                        help="fotbal: alertă dacă e 0-0 în prima repriză din acest minut (implicit 25)")
    parser.add_argument("--post-morning", default=os.environ.get("POST_MORNING", "09:00"),
                        help="ora (RO, HH:MM) de la care se publică programul zilei (implicit 09:00)")
    parser.add_argument("--post-evening", default=os.environ.get("POST_EVENING", "23:30"),
                        help="ora (RO, HH:MM) de la care se publică bilanțul zilei (implicit 23:30)")
    parser.add_argument("--no-posts", dest="posts", action="store_false",
                        default=os.environ.get("POSTS", "on").lower() != "off",
                        help="fără postările de dimineață/seară (sau POSTS=off)")
    parser.add_argument("--state", default="state/alerted.json")
    parser.add_argument("--once", action="store_true", help="o singură verificare, fără buclă")
    parser.add_argument("--test-telegram", action="store_true", help="trimite un mesaj de test și ieși")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.test_telegram:
        ok = telegram.send("✅ Monitorul live (tenis + fotbal) e conectat la Telegram.")
        raise SystemExit(0 if ok else 1)
    run(args)


if __name__ == "__main__":
    main()
