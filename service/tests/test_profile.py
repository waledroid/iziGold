from app.db import SignalDb, profile_completion


def test_profile_absent_then_created(tmp_path):
    db = SignalDb(str(tmp_path / "p.db"))
    assert db.get_profile() is None
    row = db.save_profile({})               # Skip: creates empty row
    assert row["id"] == 1 and db.get_profile() is not None


def test_partial_update_only_touches_sent_fields(tmp_path):
    db = SignalDb(str(tmp_path / "p.db"))
    db.save_profile({"name": "Wale", "email": "w@x.com"})
    row = db.save_profile({"phone": "+33 6 00"})
    assert row["name"] == "Wale" and row["email"] == "w@x.com"
    assert row["phone"] == "+33 6 00"
    assert db.save_profile({"bogus_key": 1})["name"] == "Wale"  # unknown ignored


def test_risk_ack_ts_set_once(tmp_path):
    db = SignalDb(str(tmp_path / "p.db"))
    assert db.save_profile({})["risk_ack_ts"] is None
    first = db.save_profile({"risk_ack": 1})["risk_ack_ts"]
    assert first is not None
    assert db.save_profile({"risk_ack": 1})["risk_ack_ts"] == first


def test_completion_percent(tmp_path):
    db = SignalDb(str(tmp_path / "p.db"))
    assert profile_completion(None) == 0
    assert profile_completion(db.save_profile({})) == 0
    row = db.save_profile({"name": "W", "email": "e", "phone": "p"})
    assert profile_completion(row) == 20            # 3 of 15
    assert profile_completion(db.save_profile({"name": ""})) == 13  # empty string unsets → 2/15
