import datetime as dt

from livemon import clv
from livemon.superbet import LiveMatch

NOW = dt.datetime(2026, 11, 5, 9, 0, tzinfo=dt.timezone.utc)
PH = ("date,tournament,tour,surface,player1,player2,recommended_player,opponent,verdict,rec_odds,odds_pass,"
      "elo_pct,details,games_line,games_odds,te_match_id,result,score,checked_at,set_scores,rec_games,games_result\n"
      "2026-11-05,,itf-f,Clay,A,B,Ana Pop,Maria Ionescu,toti de acord,2.10,True,70,,,,1,pending,,,,,\n"
      "2026-11-05,,itf-f,Clay,A,B,Ioana Lup,Elena Rus,toti de acord,1.20,False,70,,,,2,pending,,,,,\n"
      "2026-11-05,,itf-f,Clay,A,B,Dana Mir,Ilinca Dor,mai ales da,2.50,True,70,,,,3,pending,,,,,\n")
FB = ("date,league,home_team,away_team,odds_over,kickoff_local,result,total_goals,checked_at,excluded\n"
      "2026-11-05,Liga,Chrudim B,Letohrad,1.65,11:30,pending,,,\n"
      "2026-11-05,Liga,Alpha,Beta,2.40,11:30,pending,,,\n")


def test_pick_filters():
    dates = {"2026-11-05"}
    assert [p.player for p in clv.tennis_picks(PH, dates)] == ["Ana Pop"]
    assert [p.home for p in clv.football_picks(FB, dates)] == ["Chrudim B"]


def test_collect_records_odds_near_start():
    dates = {"2026-11-05"}
    offer = [LiveMatch(event_id=7, home="Maria Ionescu", away="Ana Pop", status="NOT_STARTED",
                       odds_home=1.9, odds_away=1.95, start_time="2026-11-05 09:30:00")]
    fb_events = [{"event_id": 9, "fixture": {"event_name": "Chrudim B·Letohrad", "event_date": "2026-11-05 09:30:00"},
                  "inplay_stats": {}, "inplay_stats_metadata": {"status": "NOT_STARTED"}}]
    markets = lambda eid: [{"name": "Total goluri", "odds": [
        {"price": 1.55, "status": 1, "metadata": {"name": "Peste 2.5"}}]}]
    rows = clv.collect(NOW, clv.tennis_picks(PH, dates), clv.football_picks(FB, dates), offer, fb_events, markets)
    tennis, football = rows
    assert (tennis["selection"], tennis["pick_odds"], tennis["close_odds"], tennis["clv_pct"]) == \
        ("Ana Pop", "2.10", "1.95", "+7.7")
    assert (football["strategy"], football["close_odds"], football["clv_pct"]) == ("peste 2.5", "1.55", "+6.5")
    # prea departe de start: nu se notează încă
    assert clv.collect(NOW - dt.timedelta(hours=2), clv.tennis_picks(PH, dates), [], offer, [], markets) == []


def test_summary():
    rows = [{"strategy": "peste 2.5", "clv_pct": "+6.0"}, {"strategy": "peste 2.5", "clv_pct": "-2.0"}]
    assert clv.summary(rows) == {"peste 2.5": (2, 2.0, 50.0)}
