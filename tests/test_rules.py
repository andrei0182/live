import json

from livemon.picks import Pick, match_pick, parse_picks
from livemon.rules import Outcome, RuleConfig, evaluate, evaluate_games, set1_winner
from livemon.superbet import LiveMatch, parse_live_event, player_games_main_line

# Event real (prescurtat) din răspunsul active-live, 2026-10-17.
RAW_EVENT = json.loads("""
{"event_id": 15468069,
 "fixture": {"event_name": "Neon Kinoshita·Sayuri Hata", "event_date": "2026-10-17 03:00:00", "sport_id": 2},
 "inplay_stats": {"away_team_game_score": "30", "away_team_score": "1", "home_team_game_score": "15",
                  "home_team_score": "1",
                  "periods": [{"away_team_score": "4", "home_team_score": "6", "num": 2, "type": 4},
                              {"away_team_score": "6", "home_team_score": "2", "num": 1, "type": 4},
                              {"away_team_score": "3", "home_team_score": "5", "num": 3, "type": 4}],
                  "team_serving": 1},
 "inplay_stats_metadata": {"status": "STARTED", "period_status": "S3"},
 "markets": [{"id": 521, "name": "Final", "odds": [
     {"price": 1.08, "status": 1, "metadata": {"name": "1"}},
     {"price": 5.5, "status": 2, "metadata": {"name": "2"}}]}]}
""")


def make_match(set_games, sets=(0, 0), odds=(1.5, 2.5), status="STARTED"):
    return LiveMatch(event_id=1, home="Ana Pop", away="Maria Ionescu", status=status,
                     sets_home=sets[0], sets_away=sets[1], set_games=list(set_games),
                     odds_home=odds[0], odds_away=odds[1])


PICK = Pick(date="2026-10-17", player="Ana Pop", opponent="Maria Ionescu",
            rec_odds=1.6, comp_pct=65.0, games_line=21.5)
CFG = RuleConfig(odds_factor=1.5, min_odds=1.8, min_model_pct=50)


def test_parse_live_event():
    m = parse_live_event(RAW_EVENT)
    assert (m.home, m.away) == ("Neon Kinoshita", "Sayuri Hata")
    assert m.set_games == [(2, 6), (6, 4), (5, 3)]  # sortate după num
    assert (m.sets_home, m.sets_away) == (1, 1)
    assert m.odds_home == 1.08
    assert m.odds_away is None  # status 2 = suspendată


def test_set1_winner():
    assert set1_winner(make_match([(3, 2)])) is None
    assert set1_winner(make_match([(6, 6)])) is None  # tiebreak în curs
    assert set1_winner(make_match([(4, 6)], sets=(0, 1))) == "away"
    assert set1_winner(make_match([(7, 6), (0, 0)], sets=(1, 0))) == "home"


def test_alert_when_favorite_loses_set1_and_odds_rise():
    m = make_match([(4, 6), (1, 1)], sets=(0, 1), odds=(2.6, 1.45))
    d = evaluate(PICK, m, "home", CFG)
    assert d.outcome is Outcome.ALERT
    assert d.threshold == 1.6 * 1.5


def test_wait_when_odds_below_threshold():
    m = make_match([(4, 6), (1, 1)], sets=(0, 1), odds=(2.2, 1.6))
    assert evaluate(PICK, m, "home", CFG).outcome is Outcome.WAIT


def test_min_odds_floor():
    low = Pick(**{**PICK.__dict__, "rec_odds": 1.1})
    m = make_match([(4, 6)], sets=(0, 1), odds=(1.7, 2.0))
    d = evaluate(low, m, "home", CFG)
    assert d.outcome is Outcome.WAIT and d.threshold == 1.8


def test_done_cases():
    assert evaluate(PICK, make_match([(6, 4)], sets=(1, 0)), "home", CFG).outcome is Outcome.DONE
    assert evaluate(PICK, make_match([(4, 6), (6, 2), (1, 0)], sets=(1, 1), odds=(3, 1.3)),
                    "home", CFG).outcome is Outcome.DONE
    underdog = Pick(**{**PICK.__dict__, "comp_pct": 45.0})
    assert evaluate(underdog, make_match([(4, 6)], sets=(0, 1)), "home", CFG).outcome is Outcome.DONE


def test_favorite_on_away_side():
    m = make_match([(6, 3)], sets=(1, 0), odds=(1.3, 3.4))
    pick = Pick(**{**PICK.__dict__, "player": "Maria Ionescu", "opponent": "Ana Pop"})
    assert evaluate(pick, m, "away", CFG).outcome is Outcome.ALERT


def test_match_pick_tolerates_name_formats():
    m = LiveMatch(event_id=1, home="Milica Stošović", away="Xenia Bandurowska", status="STARTED")
    pick = Pick(date="2026-10-16", player="Stosovic M.", opponent="Bandurowska X.",
                rec_odds=2.95, comp_pct=70, games_line=8.5)
    assert match_pick(pick, [m]) == (m, "home")
    assert match_pick(Pick(**{**pick.__dict__, "opponent": "Someone Else"}), [m]) is None


def test_parse_picks_filters_pending_and_dates():
    csv_text = (
        "date,tournament,player1,player2,recommended_player,opponent,edge_pp,rec_odds,comp_pct,"
        "games_line,games_odds,te_match_id,result,score,checked_at,adj_pct,ev\n"
        "2026-10-16,,A B,C D,A B,C D,10,2.1,60,9.5,1.5,1,pending,,,55,0.05\n"
        "2026-10-16,,E F,G H,E F,G H,10,2.1,60,9.5,1.5,2,won,2:0,2026-10-17,55,0.05\n"
        "2026-10-10,,I J,K L,I J,K L,10,2.1,60,9.5,1.5,3,pending,,,55,0.05\n"
    )
    picks = parse_picks(csv_text, {"2026-10-16", "2026-10-17"})
    assert [p.player for p in picks] == ["A B"]
    assert picks[0].rec_odds == 2.1 and picks[0].comp_pct == 60


def _games_market(player, lines, status=1):
    odds = []
    for total, under, over in lines:
        odds.append({"price": under, "status": status, "metadata": {"name": f"Sub {total}", "specifiers": {"total": str(total)}}})
        odds.append({"price": over, "status": status, "metadata": {"name": f"Peste {total}", "specifiers": {"total": str(total)}}})
    return {"id": 999, "name": f"Total game-uri {player}", "odds": odds}


def test_player_games_main_line():
    markets = [
        _games_market("Ana Pop", [(11.5, 3.25, 1.28), (12.5, 2.65, 1.4), (13.5, 1.57, 2.2), (14.5, 1.3, 3.1)]),
        # piața pe set are alt nume și nu trebuie să intre în calcul
        {"id": 230656, "name": "Setul 1 - Total game-uri Ana Pop", "odds": [
            {"price": 1.9, "status": 1, "metadata": {"name": "Sub 5.5", "specifiers": {"total": "5.5", "setnr": "1"}}},
            {"price": 1.9, "status": 1, "metadata": {"name": "Peste 5.5", "specifiers": {"total": "5.5", "setnr": "1"}}}]},
    ]
    assert player_games_main_line(markets, "Ana Pop") == 13.5
    assert player_games_main_line(markets, "Maria Ionescu") is None
    suspended = [_games_market("Ana Pop", [(13.5, 1.57, 2.2)], status=2)]
    assert player_games_main_line(suspended, "Ana Pop") is None


def test_games_rule():
    m = make_match([(3, 5)])
    assert evaluate_games(PICK, m, 12.5, 11.5, CFG).outcome is Outcome.WAIT
    d = evaluate_games(PICK, m, 12.5, 10.5, CFG)
    assert d.outcome is Outcome.ALERT and d.threshold == 10.5
    assert evaluate_games(PICK, m, 12.5, None, CFG).outcome is Outcome.WAIT
    assert evaluate_games(PICK, m, None, 8.5, CFG).outcome is Outcome.DONE
    assert evaluate_games(PICK, make_match([(6, 4), (6, 3)], status="FINISHED"), 12.5, 8.5, CFG).outcome is Outcome.DONE
    underdog = Pick(**{**PICK.__dict__, "comp_pct": 40.0})
    assert evaluate_games(underdog, m, 12.5, 8.5, CFG).outcome is Outcome.DONE


def test_parse_model90_probability_scaled_to_pct():
    csv_text = (
        "date,tournament,tour,player1,player2,market,recommended_player,opponent,probability,threshold,"
        "rec_odds,games_bet,games_line,games_odds,te_match_id,result,score,checked_at,set_scores,rec_games,games_result\n"
        "2026-10-17,,,Ondrej Hujo,Sebastian Bozemoj,castiga meciul,Ondrej Hujo,Sebastian Bozemoj,0.902,0.815,"
        "1.12,,8.5,,1,pending,,,,,\n"
    )
    [pick] = parse_picks(csv_text, {"2026-10-17"}, "probability", 100.0, "Model 90%")
    assert pick.comp_pct == 90.2 and pick.rec_odds == 1.12 and pick.source == "Model 90%"
    # cotă 1.12 × 1.5 = 1.68 < 1.8: pragul rămâne minimul
    m = make_match([(4, 6)], sets=(0, 1), odds=(1.9, 1.8))
    m.home, m.away = "Ondrej Hujo", "Sebastian Bozemoj"
    d = evaluate(pick, m, "home", CFG)
    assert d.outcome is Outcome.ALERT and d.threshold == 1.8


def test_singles_pick_never_matches_doubles():
    pick = Pick(date="2026-10-16", player="Felitsata Dorofeeva-Rybas", opponent="Arina Gamretkaia",
                rec_odds=1.01, comp_pct=86.3, games_line=8.5)
    doubles = LiveMatch(event_id=1, home="E.Agureeva/F.Dorofeeva-Rybas", away="A.Gamretkaia/L.Belibova",
                        status="STARTED")
    singles = LiveMatch(event_id=2, home="Felitsata Dorofeeva-Rybas", away="Arina Gamretkaia", status="STARTED")
    assert match_pick(pick, [doubles]) is None
    assert match_pick(pick, [doubles, singles]) == (singles, "home")
