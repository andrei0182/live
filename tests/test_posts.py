import argparse
import datetime as dt

import live_monitor as lm
from livemon.posts import Finished, evening_post, morning_post, prune_alerts, record_alert, resolve_alert


def tennis_event(eid, name, sets, periods):
    return {"event_id": eid, "fixture": {"event_name": name},
            "inplay_stats": {"home_team_score": str(sets[0]), "away_team_score": str(sets[1]),
                             "periods": [{"num": i + 1, "home_team_score": str(h), "away_team_score": str(a)}
                                         for i, (h, a) in enumerate(periods)]},
            "inplay_stats_metadata": {"status": "FINISHED"}}


def football_event(eid, name, periods):
    total = (sum(h for h, _ in periods), sum(a for _, a in periods))
    e = tennis_event(eid, name, total, periods)
    return e


# Rezultatele reale de pe 2026-10-17
FIN = Finished(
    tennis={1: tennis_event(1, "Gabriel Diallo·Harold Mayot", (2, 0), [(7, 6), (6, 2)])},
    football={2: football_event(2, "Chrudim B·Letohrad", [(1, 0), (0, 1)])},
)


def alerts():
    st = {"alerts": []}
    record_alert(st, date="2026-10-17", kind="set1", event_id=1, home="Gabriel Diallo", away="Harold Mayot",
                 player="Harold Mayot", side="away", value=50)
    record_alert(st, date="2026-10-17", kind="games", event_id=1, home="Gabriel Diallo", away="Harold Mayot",
                 player="Harold Mayot", side="away", value=7.5)
    record_alert(st, date="2026-10-17", kind="fh", event_id=2, home="Chrudim B", away="Letohrad")
    record_alert(st, date="2026-10-17", kind="fh", event_id=99, home="X", away="Y")  # încă neterminat
    return st


def test_resolve_alerts():
    st = alerts()
    assert [resolve_alert(a, FIN) for a in st["alerts"]] == [True, True, True, False]
    set1, games, fh, pending = st["alerts"]
    assert set1["outcome"] == "lost" and "7-6 6-2" in set1["detail"]
    assert games["outcome"] == "won" and "8 game-uri" in games["detail"]  # 6 + 2 > 7.5
    assert fh["outcome"] == "won" and fh["detail"] == "pauză 1-0, final 1-1"
    assert pending["outcome"] is None
    assert resolve_alert(set1, FIN) is False  # deja rezolvată


def test_evening_post_shows_losses_and_pending():
    st = alerts()
    for a in st["alerts"]:
        resolve_alert(a, FIN)
    text = evening_post(dt.date(2026, 10, 17), st["alerts"], [], [], week=st["alerts"])
    assert "2 ✅ · 1 ❌ · 1 ⏳" in text
    assert "❌ 🎾 Set 1 pierdut" in text and "meciul nu s-a terminat încă" in text
    assert "Săptămâna aceasta: 4 alerte" in text and "18+" in text
    assert "Azi n-a fost nicio alertă." in evening_post(dt.date(2026, 10, 17), [], [], [])


def test_morning_post_empty_day():
    text = morning_post(dt.date(2026, 10, 18), [], [], [], 20)
    assert "Azi nu avem meciuri în program." in text and "minutul 20" in text


def test_prune_alerts():
    st = alerts()
    st["alerts"][0]["date"] = "2026-10-01"
    prune_alerts(st, dt.date(2026, 10, 18))
    assert len(st["alerts"]) == 3


def test_maybe_post_once_per_day(monkeypatch):
    sent = []
    monkeypatch.setattr(lm.telegram, "send", lambda t: sent.append(t) or True)
    monkeypatch.setattr(lm.Finished, "fetch", classmethod(lambda cls, d: Finished({}, {})))
    for name in ("fetch_pending_picks", "fetch_football_picks", "fetch_live_matches", "fetch_prematch_matches"):
        monkeypatch.setattr(lm, name, lambda *a, **k: [])
    monkeypatch.setattr(lm.tk.History, "fetch", classmethod(lambda cls, d: None))
    args = argparse.Namespace(post_morning="09:00", post_evening="23:30", min_model_pct=55, fh_minute=20)
    state = {"done": {}, "prematch_lines": {}, "alerts": [], "posted": {}, "tickets": {}}

    early = dt.datetime(2026, 10, 18, 5, 0, tzinfo=dt.timezone.utc)  # 08:00 RO
    lm.maybe_post(state, args, early)
    assert sent == []
    morning = dt.datetime(2026, 10, 18, 6, 7, tzinfo=dt.timezone.utc)  # 09:07 RO
    lm.maybe_post(state, args, morning)
    lm.maybe_post(state, args, morning + dt.timedelta(minutes=20))
    assert len(sent) == 1 and sent[0].startswith("📅")
    assert "azi nu e bilet" in sent[0] and state["tickets"]["2026-10-18"]["legs"] == []
    night = dt.datetime(2026, 10, 18, 20, 47, tzinfo=dt.timezone.utc)  # 23:47 RO, duminică
    lm.maybe_post(state, args, night)
    assert len(sent) == 2 and sent[1].startswith("📊") and "Săptămâna aceasta" in sent[1]


def test_morning_post_without_tennis_alerts():
    text = morning_post(dt.date(2026, 10, 31), [], [], [], 20, tennis_alerts=False)
    assert "Alertă live: ⚽ e încă 0-0 în minutul 20." in text and "setul 1" not in text
