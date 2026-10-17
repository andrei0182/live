"""Urmărește live pick-urile de azi și trimite alerte pe Telegram:

- set 1: favoritul modelului pierde setul 1 și cota lui live trece de prag;
- game-uri: linia live la "Total game-uri <favorit>" scade cu cel puțin
  --games-drop față de linia principală pre-meci.

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
from livemon.picks import Pick, fetch_pending_picks, match_pick
from livemon.rules import (Decision, Outcome, RuleConfig, evaluate, evaluate_games, format_alert,
                           format_games_alert)
from livemon.superbet import (LiveMatch, fetch_event_markets, fetch_live_matches, fetch_prematch_matches,
                              player_games_main_line)

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
    return state


def save_state(path: Path, state: dict, today: dt.date) -> None:
    # păstrăm doar ultimele 3 zile, ca fișierul să nu crească la nesfârșit
    cutoff = (today - dt.timedelta(days=3)).isoformat()
    for section in ("done", "prematch_lines"):
        state[section] = {k: v for k, v in state[section].items() if k.split("|", 1)[0] >= cutoff}
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


def run(args: argparse.Namespace) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    today_ro = now.astimezone(RO_TZ).date()
    deadline = now + dt.timedelta(minutes=args.max_minutes)
    cfg = RuleConfig(odds_factor=args.odds_factor, min_odds=args.min_odds, min_model_pct=args.min_model_pct,
                     games_drop=args.games_drop)
    state_path = Path(args.state)
    state = load_state(state_path)

    picks = [p for p in fetch_pending_picks(today_ro) if pending_rules(p, state)]
    if not picks:
        logger.info("Niciun pick pending netratat pentru %s.", today_ro)
        return

    live = fetch_live_matches(now.date())
    prematch = fetch_prematch_matches(now.date())
    # Pick-urile care nu sunt nici live, nici nu încep înainte de deadline
    # rămân pentru rularea următoare. Linia de game-uri pre-meci o salvăm
    # pentru toate pick-urile încă neîncepute, ca să existe referința chiar
    # dacă o rulare întârzie și meciul pornește între două rulări.
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
    save_state(state_path, state, today_ro)

    while watch:
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
                if decision.outcome is Outcome.ALERT and not telegram.send(text):
                    continue  # încercăm din nou la iterația următoare
                if decision.outcome in (Outcome.ALERT, Outcome.DONE):
                    state["done"][rule_key(p, rule)] = decision.reason
            if not pending_rules(p, state):
                watch.remove(p)
        save_state(state_path, state, today_ro)

        if not watch or args.once or dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=args.interval) > deadline:
            break
        time.sleep(args.interval)
        today_utc = dt.datetime.now(dt.timezone.utc).date()
        live = fetch_live_matches(today_utc)
        if any(not match_pick(p, live) for p in watch):
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
    parser.add_argument("--state", default="state/alerted.json")
    parser.add_argument("--once", action="store_true", help="o singură verificare, fără buclă")
    parser.add_argument("--test-telegram", action="store_true", help="trimite un mesaj de test și ieși")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.test_telegram:
        ok = telegram.send("✅ Monitorul live de tenis e conectat la Telegram.")
        raise SystemExit(0 if ok else 1)
    run(args)


if __name__ == "__main__":
    main()
