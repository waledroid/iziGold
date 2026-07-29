from app.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.forecaster == "chronos"
    assert s.mode == "grading"
    assert s.horizon == 16
    assert s.confirm_threshold == 0.6


def test_env_override(monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    assert Settings(_env_file=None).forecaster == "fake"
