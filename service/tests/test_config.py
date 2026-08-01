from app.config import Settings


def test_defaults(monkeypatch):
    # Isolate from environment when testing hardcoded defaults
    monkeypatch.delenv("FORECASTER", raising=False)
    monkeypatch.delenv("MODE", raising=False)
    monkeypatch.delenv("HORIZON", raising=False)
    monkeypatch.delenv("CONFIRM_THRESHOLD", raising=False)
    monkeypatch.delenv("CHRONOS_MODEL", raising=False)
    monkeypatch.delenv("CONTEXT_LEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("SCREENSHOT_DIR", raising=False)
    s = Settings(_env_file=None)
    assert s.forecaster == "chronos"
    assert s.mode == "grading"
    assert s.horizon == 16
    assert s.confirm_threshold == 0.6


def test_env_override(monkeypatch):
    monkeypatch.setenv("FORECASTER", "fake")
    assert Settings(_env_file=None).forecaster == "fake"
