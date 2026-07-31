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


settings = Settings()
