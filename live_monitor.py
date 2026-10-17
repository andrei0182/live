"""Urmărește live pick-urile de azi și trimite alertă pe Telegram când
favoritul modelului pierde setul 1 și cota lui live trece de prag.

Rulează cel mult --max-minutes, verificând la fiecare --interval secunde, și
se oprește mai devreme dacă niciun pick nu e live sau nu începe în fereastra
asta. Pick-urile deja tratate (alertă trimisă sau regulă imposibilă) se țin
în --state, ca rulările următoare să nu retrimită alerta.

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
from livemon.rules import Outcome, RuleConfig, evaluate, format_alert
from livemon.superbet import LiveMatch, fetch_live_matches, fetch_prematch_matches

logger = logging.getLogger("live_monitor")

RO_TZ = ZoneInfo("Europe/Bucharest")


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"done": {}}


def save_state(path: Path, state: dict, today: dt.date) -> None:
    # păstrăm doar ultimele 3 zile, ca fișierul să nu crească la nesfârșit
    cutoff = (today - dt.timedelta(days=3)).isoformat()
    state["done"] = {k: v for k, v in state["done"].items() if k.split("|", 1)[0] >= cutoff}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def starts_before(match: LiveMatch, deadline: dt.datetime) -> bool:
    """`event_date` de la Superbet e în UTC, format "YYYY-MM-DD HH:MM:SS"."""
    try:
        start = dt.datetime.strptime(match.start_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return True  # format necunoscut: mai bine așteptăm decât să-l ratăm
    return start <= deadline


def run(args: argparse.Namespace) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    today_ro = now.astimezone(RO_TZ).date()
    deadline = now + dt.timedelta(minutes=args.max_minutes)
    cfg = RuleConfig(odds_factor=args.odds_factor, min_odds=args.min_odds, min_model_pct=args.min_model_pct)
    state_path = Path(args.state)
    state = load_state(state_path)

    picks = [p for p in fetch_pending_picks(today_ro) if p.key not in state["done"]]
    if not picks:
        logger.info("Niciun pick pending netratat pentru %s.", today_ro)
        return

    # Pick-urile care nu sunt nici live, nici nu încep înainte de deadline
    # rămân pentru rularea următoare.
    live = fetch_live_matches(now.date())
    prematch = fetch_prematch_matches(now.date())
    watch: list[Pick] = []
    for p in picks:
        if match_pick(p, live):
            watch.append(p)
            continue
        found = match_pick(p, prematch)
        if found and starts_before(found[0], deadline):
            watch.append(p)
        elif found:
            logger.info("%s începe la %s UTC, după fereastra acestei rulări.", p.player, found[0].start_time)
        else:
            logger.info("%s vs %s nu e în oferta Superbet live/prematch.", p.player, p.opponent)

    while watch:
        for p in list(watch):
            found = match_pick(p, live)
            if not found:
                continue
            match, side = found
            decision = evaluate(p, match, side, cfg)
            logger.info("%s (%s) %s: %s", p.player, match.score_text(), decision.outcome.value, decision.reason)
            if decision.outcome is Outcome.ALERT:
                if not telegram.send(format_alert(p, match, decision)):
                    continue  # încercăm din nou la iterația următoare
            if decision.outcome in (Outcome.ALERT, Outcome.DONE):
                state["done"][p.key] = decision.reason
                save_state(state_path, state, today_ro)
                watch.remove(p)

        if not watch or args.once or dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=args.interval) > deadline:
            break
        time.sleep(args.interval)
        live = fetch_live_matches(dt.datetime.now(dt.timezone.utc).date())

    save_state(state_path, state, today_ro)


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
