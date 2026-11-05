import datetime as dt

from livemon import trends as T
from livemon.superbet import LiveMatch

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 5, 7, 0, tzinfo=UTC)
TODAY = dt.date(2026, 11, 5)


def test_match_games():
    assert T.match_games([(6, 4), (6, 3)]) == (True, 12, 7)
    assert T.match_games([(4, 6), (7, 6), (10, 8)]) == (True, 12, 12)   # super tiebreak = 1 game
    assert T.match_games([(3, 6), (2, 6)]) == (False, 5, 12)
    assert T.match_games([(6, 4), (2, 1)]) is None                      # abandon
    assert T.match_games([]) is None


def _hrow(date, w, lo, score, tour="atp-single"):
    return {"date": date, "tour_type": tour, "winner_name": w, "loser_name": lo, "score": score, "match_id": date + w}


def test_tennis_stats_games_when_losing():
    rows = [_hrow(f"2026-10-{i:02d}", "Other A.", "Visker N.", "7-5 7-6") for i in range(1, 11)]
    rows.append(_hrow("2026-10-11", "Visker N.", "Other A.", "6-4 6-4"))
    st = T.tennis_stats(rows)["visker n."]
    assert st.n == 11 and st.male and st.lose_games == 11.0
    assert T.tennis_stats(rows)["other a."].lose_games is None  # o singură înfrângere


def _m(eid, home, away, oh, oa, start="2026-11-05 12:00:00", status=""):
    return LiveMatch(event_id=eid, home=home, away=away, status=status, odds_home=oh, odds_away=oa, start_time=start)


def test_tennis_selections_rules():
    stats = {"visker n.": T.PlayerStats(20, True, 9.5)}
    offer = [_m(1, "Ana Pop", "Ioana Ene", 1.80, 1.95),          # T1
             _m(2, "Big Fav", "Niels Visker", 1.01, 12.0),       # T2, T3, T5, T4 (Visker)
             _m(3, "Big Fav", "Niels Visker", 1.05, 9.0, start="2026-11-05 06:00:00"),  # deja început
             _m(4, "A / B", "C / D", 1.5, 2.5),                  # dublu
             _m(5, "X Y", "Z W", 1.30, 3.4)]                     # p ~0,72: nicio regulă
    rows = T.tennis_selections(offer, stats, TODAY, NOW)
    got = sorted((r["ref"], r["rule"]) for r in rows)
    assert got == [("1", "T1"), ("2", "T2"), ("2", "T3"), ("2", "T4"), ("2", "T5")]


def _event(home_games, away_games):
    return {"event_id": 2, "fixture": {"event_name": "Big Fav·Niels Visker"},
            "inplay_stats": {"periods": [{"num": i + 1, "home_team_score": h, "away_team_score": a}
                                         for i, (h, a) in enumerate(zip(home_games, away_games))]},
            "inplay_stats_metadata": {"status": "FINISHED"}}


def test_tennis_outcome():
    stats = {"visker n.": T.PlayerStats(20, True, 9.5)}
    rows = {r["rule"]: r for r in T.tennis_selections([_m(2, "Big Fav", "Niels Visker", 1.05, 9.0)], stats, TODAY, NOW)}
    ev = _event([6, 7], [4, 5])  # favoritul 2-0, outsiderul 9 game-uri
    assert T.tennis_outcome(rows["T2"], ev) == (T.WON, "6-4 7-5")
    assert T.tennis_outcome(rows["T4"], ev)[0] == T.WON
    assert T.tennis_outcome(rows["T4"], _event([6, 6], [1, 2]))[0] == T.LOST
    assert T.tennis_outcome(rows["T3"], _event([4, 3], [6, 6]))[0] == T.LOST
    assert T.tennis_outcome(rows["T3"], _event([6, 2], [4, 1]))[0] == T.VOID  # abandon


def _fx(mid, day, home, away, h, a, league="Ruritania: Liga 1", hour=12):
    return T.Fixture(mid, dt.datetime(2026, 10, 1, hour, tzinfo=UTC) + dt.timedelta(days=day), "ruritania", league,
                     home, away, "finished", 0, 0, h, a)


def _history():
    hist, i = [], 0
    for d in range(40):
        hist.append(_fx(f"s{i}", d, "Strong", f"Weak{d}", 3, 0)); i += 1
        hist.append(_fx(f"w{i}", d, f"Other{d}", "Weak", 2, 0)); i += 1
    return hist


def test_football_features_and_rules():
    target = T.Fixture("t", dt.datetime(2026, 11, 5, 18, tzinfo=UTC), "ruritania", "Ruritania: Liga 1",
                       "Strong", "Weak")
    f = T.football_features(_history(), [target])["t"]
    assert f["ln5"] and f["lgd_gap"] == 5.0 and f["ppg_gap"] == 3.0
    rules = [r for r, _ in T.football_rules(f)]
    assert "F1" in rules and "F2" in rules and "F4" in rules
    cup = T.Fixture("c", target.kickoff_utc, "ruritania", "Ruritania: Cup", "Strong", "Weak")
    hist = _history() + [_fx(f"c{d}", d, f"Cup{d}", "Strong", 1, 1, league="Ruritania: Cup") for d in range(30)]
    assert T.football_rules(T.football_features(hist, [cup])["c"]) == []
    newbie = T.Fixture("n", target.kickoff_utc, "ruritania", "Ruritania: Liga 1", "Strong", "Nobody")
    assert T.football_features(_history(), [newbie]) == {}


def test_football_outcome():
    fx = T.Fixture("t", NOW, "x", "X: L", "A", "B", "finished", 1, 0, 1, 1)
    assert T.football_outcome({"rule": "F1"}, fx)[0] == T.WON
    assert T.football_outcome({"rule": "F2"}, fx)[0] == T.WON
    assert T.football_outcome({"rule": "F3"}, fx)[0] == T.WON
    assert T.football_outcome({"rule": "F4"}, fx) == (T.WON, "1-1 (pauză 1-0)")
    fx.ht_away, fx.ft_away = 0, 0
    assert T.football_outcome({"rule": "F3"}, fx)[0] == T.LOST
    assert T.football_outcome({"rule": "F5"}, fx)[0] == T.LOST
    fx.status = "scheduled"
    assert T.football_outcome({"rule": "F1"}, fx) is None


def test_day_fixtures_merges_pages_by_ro_date():
    late = T.Fixture("a", dt.datetime(2026, 11, 4, 22, 30, tzinfo=UTC), "x", "X: L", "A", "B")   # 05.11 00:30 RO
    other = T.Fixture("b", dt.datetime(2026, 11, 5, 23, 0, tzinfo=UTC), "x", "X: L", "C", "D")   # 06.11 RO
    pages = {TODAY: [late], TODAY + dt.timedelta(days=1): [other]}
    assert [fx.match_id for fx in T.day_fixtures(TODAY, pages)] == ["a"]


def test_texts_and_split():
    rows = [{"date": "2026-11-04", "rule": "F1", "ref": str(i), "start_utc": "2026-11-04 18:00", "home": f"H{i}",
             "away": "A", "info": "X: L;gazda marchează", "result": T.WON if i else T.LOST, "score": "0-1"}
            for i in range(10)]
    rows.append(dict(rows[0], date="2026-11-05", ref="99", result="", score="", start_utc="2026-11-05 18:00"))
    res = T.results_text(rows, "2026-11-04")
    assert "F1" in res and "9/10 90%" in res and "❌ H0 – A: 0-1" in res
    today = T.today_text(rows, TODAY)
    assert "20:00 H0 – A (X: L) → gazda marchează" in today and "T1" in today and "niciun meci" in today
    parts = T.split_message("\n".join(["x" * 100] * 100), limit=1000)
    assert len(parts) > 1 and all(len(p) <= 1000 for p in parts)


def test_log_roundtrip(tmp_path):
    path = tmp_path / "log.csv"
    rows = [{"date": "2026-11-05", "rule": "T1", "ref": "1", "start_utc": "x", "home": "a", "away": "b",
             "info": "fav=home;p=0.6;b ≥4;1.8/2.0", "result": "", "score": ""}]
    T.save_log(rows, path)
    assert T.load_log(path) == rows
