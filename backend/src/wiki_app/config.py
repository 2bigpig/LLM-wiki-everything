"""公共 YAML + 私密环境配置；所有入口复用校验，不回显密钥。"""

import os
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv.parser import parse_stream
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

ROOT = Path(__file__).resolve().parents[3]
Positive = Annotated[int, Field(gt=0)]
Seconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Port = Annotated[int, Field(ge=1, le=65535)]
SECRET_KEYS = ("POSTGRES_PASSWORD", "LLM_API_KEY", "API_TOKEN", "MODEL_CREDENTIAL_KEY")


class UniqueLoader(yaml.SafeLoader):
    """重复键通常是配置粘贴错误，不能让后一个值静默覆盖前一个值。"""

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        try:
            if len(keys) != len(set(keys)):
                raise yaml.YAMLError()
        except TypeError:
            raise yaml.YAMLError() from None
        return super().construct_mapping(node, deep=deep)


class Section(BaseModel):
    """拒绝未知项和字符串伪装的数字，防止拼写错误静默失效。"""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Services(Section):
    api_host: str
    api_port: Port
    frontend_host: str
    frontend_port: Port
    extra_cors_origins: list[str]
    model_bind_host: str
    ocr_port: Port
    asr_port: Port
    embedding_port: Port
    container_host: str
    model_http_port: Port


class Database(Section):
    db_host: str
    db_port: Port
    db_name: str
    db_user: str
    db_container_host: str
    db_container_port: Port
    db_connect_timeout_seconds: Annotated[int, Field(ge=1, le=60)]
    db_options: str


class Paths(Section):
    data_dir: str
    log_dir: str
    ffmpeg_dir: str
    container_data_dir: str
    hf_cache_dir: str
    ocr_cache_dir: str
    asr_cache_dir: str

    @field_validator("hf_cache_dir", "ocr_cache_dir", "asr_cache_dir")
    @classmethod
    def persistent_cache(cls, value):
        """模型卷固定挂载 /models，防止配置指向容器临时层后丢失缓存。"""
        path = PurePosixPath(value)
        if not path.is_relative_to("/models") or ".." in path.parts:
            raise ValueError("模型缓存目录必须位于 /models 卷内")
        return value

    @field_validator("container_data_dir")
    @classmethod
    def container_data_path(cls, value):
        """资料挂载必须为独立绝对目录，不能覆盖根目录或应用源码。"""
        path = PurePosixPath(value)
        if not path.is_absolute() or path == PurePosixPath("/") or ".." in path.parts \
                or path.is_relative_to("/app") or PurePosixPath("/app").is_relative_to(path):
            raise ValueError("容器资料目录必须是应用目录外的绝对路径")
        return value


class Launcher(Section):
    startup_timeout_seconds: Seconds
    stop_timeout_seconds: Seconds
    health_timeout_seconds: Seconds
    health_poll_seconds: Seconds
    process_poll_seconds: Seconds
    output_join_seconds: Seconds


class Worker(Section):
    worker_concurrency: Annotated[int, Field(ge=1, le=32)]
    worker_max_attempts: Positive
    worker_retry_wait_seconds: Seconds
    dispatch_interval_seconds: Seconds
    dispatch_batch_size: Positive
    worker_shutdown_seconds: Seconds
    worker_heartbeat_seconds: Seconds
    worker_stalled_seconds: Seconds


class Containers(Section):
    postgres_health_interval_seconds: Positive
    postgres_health_timeout_seconds: Positive
    postgres_health_retries: Positive
    model_health_interval_seconds: Positive
    model_health_timeout_seconds: Positive
    model_health_retries: Positive
    model_health_request_seconds: Seconds


class LLM(Section):
    llm_base_url: str
    llm_model: str
    llm_max_output_tokens: Annotated[int, Field(ge=256, le=16000)]
    monthly_token_limit: Positive
    max_calls_per_run: Annotated[int, Field(ge=1, le=100)]
    llm_timeout_seconds: Seconds


class Models(Section):
    ocr_enabled: bool
    asr_enabled: bool
    embeddings_enabled: bool
    inference_device: Literal["cuda", "cpu"]
    inference_timeout_seconds: Seconds
    inference_client_timeout_seconds: Seconds
    model_connect_timeout_seconds: Seconds
    ocr_detection_model: str
    ocr_recognition_model: str
    ocr_doc_orientation: bool
    ocr_doc_unwarping: bool
    ocr_textline_orientation: bool
    asr_model: str
    asr_compute_type: str
    asr_beam_size: Positive
    asr_vad_filter: bool
    asr_condition_on_previous_text: bool
    embedding_model: str
    embedding_device: Literal["cpu"]
    embedding_batch_size: Positive
    embedding_max_seq_length: Positive
    embedding_timeout_seconds: Seconds
    embedding_max_batch: Positive
    embedding_max_text_chars: Positive


class Processing(Section):
    max_upload_mb: Annotated[int, Field(ge=1, le=4096)]
    max_media_minutes: Annotated[int, Field(ge=1, le=1440)]
    max_video_frames: Annotated[int, Field(ge=1, le=30)]
    chunk_chars: Positive
    text_max_bytes: Positive
    docx_max_entries: Positive
    docx_max_unpacked_bytes: Positive
    pdf_max_pages: Positive
    pdf_ocr_min_chars: Positive
    pdf_render_dpi: Positive
    image_max_pixels: Positive
    video_frame_interval_seconds: Seconds
    video_frame_width: Positive
    media_command_timeout_seconds: Seconds


class Web(Section):
    browser_enabled: bool
    web_max_bytes: Positive
    web_redirect_attempts: Positive
    web_timeout_seconds: Seconds
    web_min_text_chars: Positive
    browser_timeout_seconds: Seconds
    downloader_socket_timeout_seconds: Seconds
    downloader_retries: Annotated[int, Field(ge=0)]
    subtitle_timeout_seconds: Seconds
    media_download_timeout_seconds: Seconds
    subtitle_languages: str
    platform_video_height: Positive


class Search(Section):
    search_default_limit: Positive
    search_api_limit: Positive
    search_max_terms: Positive
    search_candidates: Positive
    search_rrf_k: Positive
    index_batch_size: Positive
    relation_candidates: Positive
    relation_summary_chars: Positive
    wiki_scan_limit: Positive
    wiki_existing_limit: Positive
    wiki_context_chars: Positive
    graph_recursion_limit: Positive
    wiki_list_limit: Positive
    wiki_revision_limit: Positive
    record_history_limit: Positive
    usage_calls_limit: Positive


class Logging(Section):
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    log_max_bytes: Positive
    log_backup_count: Annotated[int, Field(ge=0)]


class Tools(Section):
    ffmpeg_download_url: str
    ocr_download_base_url: str
    model_download_timeout_seconds: Seconds
    model_download_workers: Positive
    ffmpeg_download_timeout_seconds: Positive


class Capture(Section):
    capture_max_links: Annotated[int, Field(ge=1, le=100)]
    capture_max_attachments: Annotated[int, Field(ge=1, le=100)]


class Graph(Section):
    graph_max_nodes: Annotated[int, Field(ge=10, le=200)]
    graph_max_edges: Annotated[int, Field(ge=10, le=500)]
    graph_default_hops: Annotated[int, Field(ge=1, le=3)]
    graph_max_hops: Annotated[int, Field(ge=1, le=3)]


class Review(Section):
    review_enabled: bool
    review_timezone: str
    review_daily_time: str
    review_daily_limit: Annotated[int, Field(ge=1, le=20)]
    review_context_limit: Annotated[int, Field(ge=1, le=10)]
    review_recent_days: Positive
    review_min_age_days: Positive
    review_cooldown_days: Positive
    review_pause_days: Positive
    review_check_interval_seconds: Seconds
    review_insights_enabled: bool

    @field_validator("review_timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("回顾时区无效") from None
        return value

    @field_validator("review_daily_time")
    @classmethod
    def valid_daily_time(cls, value):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("回顾时间必须为 HH:MM")
        return value


class ProviderPreset(Section):
    id: str
    name: str
    protocol: Literal["openai", "azure", "anthropic", "google", "deepseek", "bge"]
    base_url: str


class LLMScheduleSettings(Section):
    """公共文件提供首次默认值，页面修改后的调度策略保存在数据库。"""

    enabled: bool
    start: str
    end: str
    timezone: str
    end_policy: Literal["finish_batch", "wait_next_window"]

    @field_validator("start", "end")
    @classmethod
    def clock_time(cls, value):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("时间必须为 HH:mm")
        return value

    @field_validator("timezone")
    @classmethod
    def schedule_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("时区必须是有效的 IANA 名称") from None
        return value

    @model_validator(mode="after")
    def different_times(self):
        if self.enabled and self.start == self.end:
            raise ValueError("开始和结束时间不能相同；全天执行请关闭调度")
        return self


class ModelManagement(Section):
    # 公共预设只含地址和协议；用户密钥及实际选择保存数据库。
    provider_presets: list[ProviderPreset]
    probe_timeout_seconds: Seconds
    discovery_limit: Positive
    schedule_defaults: LLMScheduleSettings


class PublicConfig(Section):
    model_management: ModelManagement
    capture: Capture
    graph: Graph
    review: Review
    services: Services
    database: Database
    paths: Paths
    launcher: Launcher
    containers: Containers
    worker: Worker
    llm: LLM
    models: Models
    processing: Processing
    web: Web
    search: Search
    logging: Logging
    tools: Tools

    @model_validator(mode="after")
    def check_relationships(self):
        """避免心跳误判、提前清理消费者，以及客户端提前放弃推理。"""
        checks = [
            (self.graph.graph_default_hops <= self.graph.graph_max_hops,
             "graph.graph_default_hops 不得超过 graph.graph_max_hops"),
            (self.worker.worker_heartbeat_seconds < self.worker.worker_stalled_seconds,
             "worker.worker_stalled_seconds 必须大于 worker.worker_heartbeat_seconds"),
            (self.launcher.stop_timeout_seconds > self.worker.worker_shutdown_seconds,
             "launcher.stop_timeout_seconds 必须大于 worker.worker_shutdown_seconds"),
            (self.models.inference_client_timeout_seconds > self.models.inference_timeout_seconds,
             "models.inference_client_timeout_seconds 必须大于 models.inference_timeout_seconds"),
            (self.search.index_batch_size <= self.models.embedding_max_batch,
             "search.index_batch_size 不得超过 models.embedding_max_batch"),
        ]
        for valid, message in checks:
            if not valid:
                raise ValueError(message)
        return self


class Settings(PublicConfig):
    """保留 settings.xxx 接口；普通字段只来自已校验的 YAML。"""

    config_file: Path = Field(exclude=True)
    runtime: Literal["host", "ocr", "asr", "bge"] = "host"
    postgres_password: SecretStr = Field(default=SecretStr(""), exclude=True)
    llm_api_key: SecretStr = Field(default=SecretStr(""), exclude=True)
    # 仅兼容旧 .env，开发阶段不再参与鉴权；后续使用独立用户登录与 RBAC。
    api_token: SecretStr = Field(default=SecretStr(""), exclude=True)
    model_credential_key: SecretStr = Field(default=SecretStr(""), exclude=True)

    def __getattr__(self, name):
        # 兼容已有扁平调用，业务模块无需关心 YAML 如何分组。
        for group in PublicConfig.model_fields:
            section = self.__dict__.get(group)
            if section is not None and name in type(section).model_fields:
                return getattr(section, name)
        return super().__getattr__(name)

    def local_path(self, value: str) -> Path:
        """相对路径固定基于配置文件目录，不受终端 cwd 影响。"""
        path = Path(value)
        return path.resolve() if path.is_absolute() else (self.config_file.parent / path).resolve()

    @property
    def data_dir(self) -> Path:
        if self.runtime != "host":
            return Path(self.paths.container_data_dir)
        return self.local_path(self.paths.data_dir)

    @property
    def log_dir(self) -> Path:
        return self.local_path(self.paths.log_dir)

    @property
    def ffmpeg_dir(self) -> Path:
        return self.local_path(self.paths.ffmpeg_dir)

    @property
    def database_url(self) -> str:
        host = self.db_host if self.runtime == "host" else self.db_container_host
        port = self.db_port if self.runtime == "host" else self.db_container_port
        host = f"[{host}]" if ":" in host else host
        password = quote(self.postgres_password.get_secret_value(), safe="")
        # 同一个连接超时同时用于 SQLAlchemy、队列、检查点和启动检查。
        options = {"connect_timeout": str(self.db_connect_timeout_seconds)}
        if self.db_options:
            options["options"] = self.db_options
        query = "?" + urlencode(options)
        return (f"postgresql+psycopg://{quote(self.db_user, safe='')}:{password}@"
                f"{host}:{port}/{quote(self.db_name, safe='')}{query}")

    @property
    def postgres_dsn(self) -> str:
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    @staticmethod
    def http_url(host: str, port: int) -> str:
        """通配监听地址转成本机可连接地址，兼容 IPv6。"""
        host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
        return f"http://{'[' + host + ']' if ':' in host else host}:{port}"

    @property
    def api_url(self) -> str:
        return self.http_url(self.api_host, self.api_port)

    @property
    def ocr_url(self) -> str:
        return self.http_url(self.model_bind_host, self.ocr_port) if self.ocr_enabled else ""

    @property
    def asr_url(self) -> str:
        return self.http_url(self.model_bind_host, self.asr_port) if self.asr_enabled else ""

    @property
    def embedding_url(self) -> str:
        return self.http_url(self.model_bind_host, self.embedding_port)

    @property
    def cors_origins(self) -> str:
        origins = [self.http_url(host, self.frontend_port)
                   for host in ("localhost", "127.0.0.1", self.frontend_host)]
        return ",".join(dict.fromkeys(origins + self.extra_cors_origins))

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_model and self.llm_api_key.get_secret_value())


def read_public(path: Path) -> PublicConfig:
    """不回显配置内容或解析器原始异常，避免误填的密钥进入日志。"""
    try:
        raw = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=UniqueLoader)
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ValueError("无法读取公共配置，请检查 config.yaml 是否存在及 YAML 格式。") from None
    try:
        return PublicConfig.model_validate(raw)
    except ValidationError as exc:
        fields = [".".join(map(str, error["loc"])) or "参数关系" for error in exc.errors()]
        relations = [str(error.get("ctx", {}).get("error", ""))
                     for error in exc.errors() if not error["loc"]]
        raise ValueError("config.yaml 配置错误：" + "; ".join(fields + relations)) from None


def read_private(path: Path) -> dict[str, str]:
    """保留 dotenv 转义语义，但拒绝错误行和重复键，避免迁移时静默丢失配置。"""
    if not path.exists():
        return {}
    result = {}
    try:
        with path.open(encoding="utf-8-sig") as source:
            for binding in parse_stream(source):
                if binding.error or binding.key in result:
                    raise ValueError(".env 格式错误或含重复键，请检查文件；未输出配置值。")
                if binding.key:
                    result[binding.key] = binding.value or ""
    except (OSError, UnicodeError):
        raise ValueError("无法读取 .env，请检查编码和文件权限。") from None
    return result


@lru_cache
def get_settings() -> Settings:
    """WIKI_CONFIG_FILE / WIKI_RUNTIME 仅用于入口定位与测试隔离。"""
    path = Path(os.environ.get("WIKI_CONFIG_FILE", ROOT / "config.yaml")).resolve()
    public = read_public(path)
    private = read_private(path.parent / ".env")
    legacy = {key.upper() for section in public.model_dump().values() for key in section}
    legacy.update({"DATABASE_URL", "OCR_URL", "ASR_URL", "EMBEDDING_URL", "CORS_ORIGINS"})
    old_keys = sorted((set(private) - set(SECRET_KEYS)) | (set(os.environ) & legacy))
    if old_keys:
        raise ValueError("环境含旧普通配置键 " + ", ".join(old_keys)
                         + "；请运行 python -m wiki_app.configuration migrate-env。")
    secrets = {name.lower(): SecretStr(os.environ.get(name, private.get(name) or ""))
               for name in SECRET_KEYS}
    try:
        return Settings(**public.model_dump(), config_file=path,
                        runtime=os.environ.get("WIKI_RUNTIME", "host"), **secrets)
    except ValidationError:
        raise ValueError("配置入口 WIKI_RUNTIME 无效。") from None
