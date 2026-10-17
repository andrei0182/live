"""Regula de alertă: favoritul modelului pierde setul 1 și cota lui live crește.

Se declanșează când:
- pick-ul are `comp_pct >= min_model_pct` (jucătorul e favorit în model);
- setul 1 s-a terminat și l-a câștigat adversarul;
- meciul nu e terminat și favoritul nu a egalat deja la seturi;
- cota live (activă, nesuspendată) a favoritului >= `max(rec_odds * odds_factor, min_odds)`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .picks import Pick
from .superbet import LiveMatch


@dataclass
class RuleConfig:
    odds_factor: float = 1.5
    min_odds: float = 1.8
    min_model_pct: float = 50.0


class Outcome(Enum):
    WAIT = "wait"  # setul 1 în desfășurare sau cota sub prag: mai verificăm
    ALERT = "alert"
    DONE = "done"  # regula nu se mai poate declanșa pe acest meci


@dataclass
class Decision:
    outcome: Outcome
    reason: str
    live_odds: Optional[float] = None
    threshold: Optional[float] = None


def set1_winner(match: LiveMatch) -> Optional[str]:
    """"home"/"away" dacă setul 1 e încheiat, altfel None. Setul 1 e încheiat
    când a început setul 2 sau s-a câștigat cel puțin un set."""
    if not match.set_games:
        return None
    if len(match.set_games) < 2 and match.sets_home + match.sets_away == 0:
        return None
    h, a = match.set_games[0]
    if h == a:
        return None
    return "home" if h > a else "away"


def evaluate(pick: Pick, match: LiveMatch, side: str, cfg: RuleConfig) -> Decision:
    if pick.comp_pct is None or pick.comp_pct < cfg.min_model_pct:
        return Decision(Outcome.DONE, f"nu e favorit în model (comp_pct={pick.comp_pct})")
    if match.status == "FINISHED":
        return Decision(Outcome.DONE, "meci terminat")

    winner = set1_winner(match)
    if winner is None:
        return Decision(Outcome.WAIT, "setul 1 în desfășurare")
    if winner == side:
        return Decision(Outcome.DONE, "favoritul a câștigat setul 1")

    fav_sets = match.sets_home if side == "home" else match.sets_away
    opp_sets = match.sets_away if side == "home" else match.sets_home
    if fav_sets >= opp_sets:
        return Decision(Outcome.DONE, "favoritul a egalat la seturi")

    threshold = max((pick.rec_odds or 0) * cfg.odds_factor, cfg.min_odds)
    live_odds = match.odds_home if side == "home" else match.odds_away
    if live_odds is None:
        return Decision(Outcome.WAIT, "cotă suspendată", threshold=threshold)
    if live_odds < threshold:
        return Decision(Outcome.WAIT, f"cota {live_odds} < prag {threshold:.2f}", live_odds, threshold)
    return Decision(Outcome.ALERT, f"cota {live_odds} >= prag {threshold:.2f}", live_odds, threshold)


def format_alert(pick: Pick, match: LiveMatch, decision: Decision) -> str:
    return "\n".join([
        "🎾 Favoritul modelului a pierdut setul 1",
        f"{match.home} vs {match.away}",
        f"Scor: {match.score_text()}",
        f"Pick: {pick.player} (model {pick.comp_pct:.0f}%, cotă pre-meci {pick.rec_odds})",
        f"Cotă live acum: {decision.live_odds} (prag {decision.threshold:.2f})",
        match.url,
    ])
