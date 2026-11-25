import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent
_env_bin_dir = (
    os.environ.get("LOST_HEAVEN_BIN_DIR", "").strip()
    or os.environ.get("CLIPSHIP_BIN_DIR", "").strip()
)
WORKSPACE_BIN = Path(_env_bin_dir) if _env_bin_dir else (PROJECT_ROOT / "bin")
_env_data_dir = (
    os.environ.get("LOST_HEAVEN_DATA_DIR", "").strip()
    or os.environ.get("CLIPSHIP_DATA_DIR", "").strip()
)
DEFAULT_STORAGE_DIR = str(Path(_env_data_dir)) if _env_data_dir else str(BASE_DIR / "storage")

# Automatically ensure workspace/env bin (FFmpeg) is prioritized on PATH
if WORKSPACE_BIN.exists():
    os.environ["PATH"] = f"{WORKSPACE_BIN};{os.environ.get('PATH', '')}"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"
    GEMINI_FALLBACK_MODEL: str = "gemini-3.6-flash"
    GEMINI_ANALYSIS_MODE: str = "transcript" # "transcript" or "multimodal"
    AUTO_FALLBACK_OFFLINE: bool = True
    AI_PROVIDER: str = "gemini" # "gemini", "ollama", or "offline"
    OLLAMA_ENDPOINT: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b"

    WHISPER_MODEL: str = "base"
    WHISPER_DEVICE: str = "auto"
    WHISPER_COMPUTE_TYPE: str = "auto"

    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    STORAGE_DIR: str = DEFAULT_STORAGE_DIR

    YOUTUBE_CLIENT_SECRETS_FILE: str = str(BASE_DIR / "client_secrets.json")
    YOUTUBE_TOKEN_FILE: str = str(BASE_DIR / "youtube_token.json")

    HARDWARE_ACCELERATION: str = "auto"

    @property
    def storage_path(self) -> Path:
        p = Path(self.STORAGE_DIR)
        p.mkdir(parents=True, exist_ok=True)
        (p / "masters").mkdir(parents=True, exist_ok=True)
        (p / "proxies").mkdir(parents=True, exist_ok=True)
        (p / "renders").mkdir(parents=True, exist_ok=True)
        (p / "captions").mkdir(parents=True, exist_ok=True)
        (p / "temp").mkdir(parents=True, exist_ok=True)
        return p

settings = Settings()
