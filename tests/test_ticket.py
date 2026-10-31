import datetime as dt

from livemon import ticket as tk
from livemon.picks import Pick
from livemon.superbet import LiveMatch

NOW = dt.datetime(2026, 10, 27, 7, 0, tzinfo=dt.timezone.utc)
HIST = tk.History([
    {"date": "2026-10-20", "winner_name": "Gadamauri T.", "loser_name": "X A."},
] * 6 + [
    {"date": "2026-10-20", "winner_name": "Mpetshi Perricard G.", "loser_name": "Y B."},
] * 6 + [
    {"date": "2026-10-26", "winner_name": "Tired P.", "loser_name": "Z C."},
] * 2 + [
    {"date": "2026-10-20", "winner_name": "Strong O.", "loser_name": "W D."},
] * 6, dt.date(2026, 10, 27))


def pick(player, opp, pct, market="castiga meciul", tour="itf-f"):
    return Pick(date="2026-10-27", player=player, opponent=opp, rec_odds=None, comp_pct=pct,
                games_line=None, market=market, tour=tour)


def match(eid, home, away, oh, oa, start="2026-10-27 11:00:00", status="NOT_STARTED"):
    return LiveMatch(event_id=eid, home=home, away=away, status=status, odds_home=oh, odds_away=oa, start_time=start)


OFFER = [
    match(1, "Tamila Gadamauri", "Isabella Abendroth", 2.3, 1.50),
    match(2, "Giovanni Mpetshi Perricard", "Arthur Fils", 3.35, 1.29, "2026-10-27 14:10:00"),
    match(3, "Paul Tired", "Oscar Strong", 1.40, 2.9),                           # obosit
    match(4, "Anna Known", "Nobody New", 1.45, 2.6),                              # adversar fără istoric
    match(5, "Lea Early", "Tamila Gadamauri", 1.30, 3.4, "2026-10-27 06:00:00"),  # a început deja
    match(6, "Lea Early", "Oscar Strong", 1.45, 2.6),
    match(7, "Low Prob", "Tamila Gadamauri", 1.45, 2.6),
]
PICKS = [
    pick("Isabella Abendroth", "Tamila Gadamauri", 85.8),
    pick("Arthur Fils", "Giovanni Mpetshi Perricard", 85.6, tour="atp"),
    pick("Paul Tired", "Oscar Strong", 90.0),
    pick("Anna Known", "Nobody New", 90.0),
    pick("Lea Early", "Tamila Gadamauri", 90.0),
    pick("Arthur Fils", "Giovanni Mpetshi Perricard", 85.0, market="adversarul sub 6.5 game-uri"),
    pick("Low Prob", "Gadamauri T.", 83.9),                                    # sub pragul de 84%
    pick("Lea Early", "Oscar Strong", 95.0, tour="itf-m"),                     # ITF masculin
]


def test_short_name():
    assert tk.short_name("Niels Visker") == "visker n."
    assert tk.short_name("Giovanni Mpetshi Perricard") == "mpetshi perricard g."


def test_candidates_apply_filters_and_choose_pair():
    cands = tk.candidates(PICKS, OFFER, HIST, NOW)
    assert [c["player"] for c in cands] == ["Isabella Abendroth", "Arthur Fils"]
    [best] = tk.choose(cands)
    t = {"legs": best}
    assert tk.total_odds(t) == 1.94


def test_resolve_and_texts():
    legs = tk.choose(tk.candidates(PICKS, OFFER, HIST, NOW))[0]
    t = {"date": "2026-10-27", "legs": legs, "outcome": None}
    tickets = {"2026-10-27": t}
    assert "1.94" in tk.morning_text(t) and "Arthur Fils" in tk.morning_text(t)
    fin = {1: {"event_id": 1, "fixture": {"event_name": "Tamila Gadamauri·Isabella Abendroth"},
               "inplay_stats": {"home_team_score": "0", "away_team_score": "2",
                                "periods": [{"num": 1, "home_team_score": "3", "away_team_score": "6"},
                                            {"num": 2, "home_team_score": "4", "away_team_score": "6"}]},
               "inplay_stats_metadata": {"status": "FINISHED"}}}
    assert tk.resolve(t, fin) is False  # Fils n-a jucat încă
    assert legs[0]["result"] == "won" and legs[0]["score"] == "6-3 6-4"
    assert "⏳" in tk.result_text(t, tickets)
    fin[2] = {"event_id": 2, "fixture": {"event_name": "Giovanni Mpetshi Perricard·Arthur Fils"},
              "inplay_stats": {"home_team_score": "2", "away_team_score": "0", "periods": []},
              "inplay_stats_metadata": {"status": "FINISHED"}}
    assert tk.resolve(t, fin) is True and t["outcome"] == "lost"
    text = tk.result_text(t, tickets)
    assert "❌ pierdut" in text and "0 ✅ · 1 ❌ din 1" in text and "-1.00 unități" in text


def test_no_ticket_text():
    assert "azi nu e bilet" in tk.morning_text({"legs": []})
    assert tk.result_text({"legs": []}, {}) == ""  # încă niciun bilet jucat: nimic în postarea de seară
