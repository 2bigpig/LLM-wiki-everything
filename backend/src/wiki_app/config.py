"""配置只从服务端环境读取，密钥不会通过状态接口返回。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 宿主机用回环地址连接 Docker；模型容器的数据库地址由 Compose 单独覆盖。
    database_url: str = "postgresql+psycopg://wiki:wiki-local-change-me@127.0.0.1:5432/wiki"
    data_dir: Path = Path("data")
    log_dir: Path = Path("data/logs")
    log_level: str = "INFO"
    api_token: SecretStr = SecretStr("")
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_max_output_tokens: int = Field(default=2400, ge=256, le=16000)
    monthly_token_limit: int = Field(default=1_000_000, ge=1)
    max_calls_per_run: int = Field(default=20, ge=1, le=100)
    max_upload_mb: int = Field(default=500, ge=1, le=4096)
    max_media_minutes: int = Field(default=180, ge=1, le=1440)
    ocr_url: str = ""
    asr_url: str = ""
    embeddings_enabled: bool = False
    browser_enabled: bool = False
    embedding_model: str = "BAAI/bge-m3"
    # 向量生成通过独立容器执行，本地后端不导入 Torch / SentenceTransformer。
    embedding_url: str = "http://127.0.0.1:8103"
    asr_model: str = "medium"
    asr_compute_type: str = "int8_float16"
    inference_device: str = "cuda"
    max_video_frames: int = Field(default=8, ge=1, le=30)

    @property
    def postgres_dsn(self) -> str:
        """框架外的 psycopg 连接使用标准 PostgreSQL DSN。"""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_model and self.llm_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
