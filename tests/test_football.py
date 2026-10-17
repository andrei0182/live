import datetime as dt

from livemon.football import (FootballMatch, FootballPick, evaluate_first_half, first_half_over,
                              format_first_half_alert, in_watch_window, match_football, parse_football_event,
                              parse_football_picks)
from livemon.rules import Outcome
from livemon.superbet import market_odds

CSV = (
    "date,league,home_team,away_team,odds_over,kickoff_local,result,total_goals,checked_at,excluded\n"
    "2026-10-17,ISRAEL: LIGAT HA'AL,Maccabi Petah Tikva,Maccabi Haifa,1.4,19:30,pending,,,\n"
    "2026-10-17,ITALY: PRIMAVERA 2,Pescara U19,Salernitana U19,1.32,12:00,pending,,,odds<1.4\n"
    "2026-10-16,EUROPE: EUROPA LEAGUE,Ferencvaros,Plzen,1.67,22:00,over,3,2026-10-17,\n"
)

# Event real (prescurtat) din active-live, sports=5, 2026-10-17.
RAW = {
    "event_id": 15500001,
    "fixture": {"event_name": "Kashiwa Reysol·Nagoya Grampus", "sport_id": 5},
    "inplay_stats": {"away_team_score": "0", "home_team_score": "0", "minutes": "27",
                     "periods": [{"away_team_score": "0", "home_team_score": "0", "num": 1, "type": 1}]},
    "inplay_stats_metadata": {"event_status_label": "1H", "period_status": "R1", "status": "STARTED"},
}


def fb_match(goals=(0, 0), minute=27, label="1H", status="STARTED"):
    return FootballMatch(event_id=1, home="Maccabi Petah Tikva", away="Maccabi Haifa", status=status,
                         label=label, minute=minute, home_goals=goals[0], away_goals=goals[1])


def test_parse_picks_keeps_only_pending_recommendations():
    picks = parse_football_picks(CSV, {"2026-10-16", "2026-10-17"})
    assert [(p.home, p.away) for p in picks] == [("Maccabi Petah Tikva", "Maccabi Haifa")]
    assert picks[0].odds_over == 1.4
    assert picks[0].kickoff_utc() == dt.datetime(2026, 10, 17, 16, 30, tzinfo=dt.timezone.utc)  # EEST


def test_parse_event():
    m = parse_football_event(RAW)
    assert (m.home, m.away, m.label, m.minute, m.goals) == ("Kashiwa Reysol", "Nagoya Grampus", "1H", 27, 0)
    assert parse_football_event({**RAW, "inplay_stats": {"minutes": "45+2"}}).minute == 45


def test_first_half_rule():
    assert evaluate_first_half(fb_match(minute=20), 25).outcome is Outcome.WAIT
    assert evaluate_first_half(fb_match(minute=25), 25).outcome is Outcome.ALERT
    assert evaluate_first_half(fb_match(goals=(1, 0), minute=12), 25).outcome is Outcome.DONE
    assert evaluate_first_half(fb_match(label="2H", minute=50), 25).outcome is Outcome.DONE
    assert evaluate_first_half(fb_match(label="", status="NOT_STARTED", minute=None), 25).outcome is Outcome.WAIT
    assert evaluate_first_half(fb_match(status="FINISHED", label="END"), 25).outcome is Outcome.DONE


def test_match_names_ignore_generic_words():
    pick = FootballPick("2026-10-17", "", "Manchester City U21", "Bristol City U21", 1.5, "20:00")
    other = FootballMatch(1, "Hull City U21", "Stoke City U21", "STARTED", "1H", 10)
    right = FootballMatch(2, "Manchester City U21", "Bristol City U21", "STARTED", "1H", 10)
    assert match_football(pick, [other]) is None
    assert match_football(pick, [other, right]) is right


def test_watch_window():
    pick = FootballPick("2026-10-17", "", "A", "B", 1.5, "19:30")  # 16:30 UTC
    kickoff = dt.datetime(2026, 10, 17, 16, 30, tzinfo=dt.timezone.utc)
    assert not in_watch_window(pick, kickoff - dt.timedelta(hours=2), kickoff - dt.timedelta(minutes=30))
    assert in_watch_window(pick, kickoff - dt.timedelta(minutes=10), kickoff + dt.timedelta(minutes=8))
    assert in_watch_window(pick, kickoff + dt.timedelta(minutes=30), kickoff + dt.timedelta(minutes=48))
    assert not first_half_over(pick, kickoff + dt.timedelta(minutes=30))
    assert first_half_over(pick, kickoff + dt.timedelta(minutes=60))


def test_alert_text_and_market_odds():
    markets = [
        {"name": "Prima repriză - Total goluri", "odds": [
            {"price": 1.9, "status": 1, "metadata": {"name": "Peste 0.5"}},
            {"price": 1.8, "status": 1, "metadata": {"name": "Sub 0.5"}}]},
        {"name": "Total goluri", "odds": [{"price": 2.4, "status": 2, "metadata": {"name": "Peste 2.5"}}]},
    ]
    fh, o25 = market_odds(markets, "Prima repriză - Total goluri", "Peste 0.5"), market_odds(markets, "Total goluri", "Peste 2.5")
    assert (fh, o25) == (1.9, None)  # suspendată -> None
    pick = parse_football_picks(CSV, {"2026-10-17"})[0]
    text = format_first_half_alert(pick, fb_match(), fh, o25)
    assert "minutul 27" in text and "Peste 0.5: 1.9" in text and "Peste 2.5: indisponibilă" in text


def test_match_picks_best_and_respects_youth():
    pick = FootballPick("2026-10-17", "", "Maccabi Petah Tikva", "Maccabi Haifa", 1.4, "19:30")
    wrong = FootballMatch(1, "Maccabi Netanya U19", "Hapoel Haifa U19", "", "", None)
    near = FootballMatch(2, "Maccabi Tel Aviv U19", "Hapoel Petah Tikva U19", "", "", None)
    right = FootballMatch(3, "Maccabi Petah Tikva", "Maccabi Haifa", "", "", None)
    assert match_football(pick, [wrong, near]) is None
    assert match_football(pick, [wrong, near, right]) is right
    da_nang = FootballPick("2026-10-17", "", "Da Nang", "Hai Phong", 1.8, "14:00")
    assert match_football(da_nang, [FootballMatch(4, "SHB Da Nang", "Hai Phong", "", "", None)]).event_id == 4
    youth = FootballPick("2026-10-17", "", "Pescara U19", "Salernitana U19", 1.3, "12:00")
    assert match_football(youth, [FootballMatch(5, "Pescara", "Salernitana", "", "", None)]) is None
