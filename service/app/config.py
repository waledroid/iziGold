from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    forecaster: str = "chronos"          # chronos | timemoe | fake
    chronos_model: str = "amazon/chronos-bolt-small"
    horizon: int = 16
    context_len: int = 200
    mode: str = "grading"                # grading | veto
    confirm_threshold: float = 0.6
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    db_path: str = "xau_assistant.db"
    screenshot_dir: str = "screenshots"
    feed_key: str = ""
    # Mini-app HTTP port. Single source of truth for the live-chart
    # service, the Windows bridge push target, the ngrok forward
    # target and the watchdog probes. Default moved 9001 -> 9101 on
    # 2026-08-19: 9001 is a popular default (a Docker mosquitto
    # WebSocket listener owns it on the owner's machine) and the
    # collision cost the chart, the tunnel AND the watchdog.
    miniapp_port: int = 9101
    miniapp_dev_bypass: bool = False
    miniapp_auth_max_age_s: int = 3600
    miniapp_public_url: str = ""
    # BotFather-registered direct link (t.me/<bot>/<shortname>) — works in
    # channels where web_app buttons cannot; falls back to the raw public URL.
    miniapp_direct_link: str = ""


settings = Settings()
