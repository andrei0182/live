"""Regulile de alertă.

1. Set 1 pierdut (`evaluate`): favoritul modelului pierde setul 1 și cota lui
live crește. Se declanșează când:
- pick-ul are `comp_pct >= min_model_pct` (jucătorul e favorit în model);
- setul 1 s-a terminat și l-a câștigat adversarul;
- meciul nu e terminat și favoritul nu a egalat deja la seturi;
- cota live (activă, nesuspendată) a favoritului >= `max(rec_odds * odds_factor, min_odds)`.

2. Linia de game-uri (`evaluate_games`): linia principală live la "Total
game-uri <favorit>" scade cu cel puțin `games_drop` față de linia principală
pre-meci (salvată de monitor înainte de start).
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
    games_drop: float = 2.0


class Outcome(Enum):
    WAIT = "wait"  # setul 1 în desfășurare sau cota sub prag: mai verificăm
    ALERT = "alert"
    DONE = "done"  # regula nu se mai poate declanșa pe acest meci


@dataclass
class Decision:
    outcome: Outcome
    reason: str
    live_value: Optional[float] = None  # cota live (set 1) sau linia live (game-uri)
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


def _source(pick: Pick) -> str:
    return f" · {pick.source}" if pick.source else ""


def format_alert(pick: Pick, match: LiveMatch, decision: Decision) -> str:
    return "\n".join([
        "🎾 Favoritul modelului a pierdut setul 1",
        f"{match.home} vs {match.away}",
        f"Scor: {match.score_text()}",
        f"Pick: {pick.player} (model {pick.comp_pct:.0f}%, cotă pre-meci {pick.rec_odds}){_source(pick)}",
        f"Cotă live acum: {decision.live_value} (prag {decision.threshold:.2f})",
        f"📱 Aplicație: {match.app_url}",
        f"🌐 Site: {match.url}",
    ])


def evaluate_games(pick: Pick, match: LiveMatch, prematch_line: Optional[float],
                   live_line: Optional[float], cfg: RuleConfig) -> Decision:
    if pick.comp_pct is None or pick.comp_pct < cfg.min_model_pct:
        return Decision(Outcome.DONE, f"nu e favorit în model (comp_pct={pick.comp_pct})")
    if prematch_line is None:
        return Decision(Outcome.DONE, "linia pre-meci nu a fost salvată")
    if match.status == "FINISHED":
        return Decision(Outcome.DONE, "meci terminat")

    threshold = prematch_line - cfg.games_drop
    if live_line is None:
        return Decision(Outcome.WAIT, "linie live indisponibilă", threshold=threshold)
    if live_line > threshold:
        return Decision(Outcome.WAIT, f"linia {live_line} > prag {threshold}", live_line, threshold)
    return Decision(Outcome.ALERT, f"linia {live_line} <= prag {threshold}", live_line, threshold)


def format_games_alert(pick: Pick, match: LiveMatch, prematch_line: float, decision: Decision) -> str:
    return "\n".join([
        "📉 Linia de game-uri a favoritului a scăzut",
        f"{match.home} vs {match.away}",
        f"Scor: {match.score_text()}",
        f"Pick: {pick.player} (model {pick.comp_pct:.0f}%){_source(pick)}",
        f"Total game-uri {pick.player}: pre-meci {prematch_line}, live acum {decision.live_value}",
        f"📱 Aplicație: {match.app_url}",
        f"🌐 Site: {match.url}",
    ])
