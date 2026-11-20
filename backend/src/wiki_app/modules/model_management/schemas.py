"""管理接口边界校验：拒绝隐藏字段、未知协议和可能泄密的地址。"""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from wiki_app.config import get_settings

Protocol = Literal["openai", "azure", "anthropic", "google", "deepseek", "bge"]
STAGES = ("default", "analyze", "relate", "wiki", "revisit")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ConnectionInput(Input):
    name: str = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=50)
    protocol: Protocol = "openai"
    base_url: str = Field(min_length=1, max_length=500)
    api_key: SecretStr | None = None
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def target(cls, value):
        """本机和私网合法；URL 内禁止密码、查询串及 fragment，密钥走单独字段。"""
        try:
            parts = urlsplit(value.strip())
            if parts.scheme not in ("http", "https") or not parts.hostname or parts.username \
                    or parts.password or parts.query or parts.fragment or not parts.port and ":" == parts.netloc[-1:]:
                raise ValueError
            _ = parts.port
        except ValueError:
            raise ValueError("服务地址必须是无凭据和查询参数的 HTTP/HTTPS 地址") from None
        return value.strip().rstrip("/")


class ModelInput(Input):
    connection_id: str
    name: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    kind: Literal["llm", "embedding"] = "llm"
    enabled: bool = True
    timeout_seconds: float = Field(default_factory=lambda: get_settings().llm_timeout_seconds,
                                   gt=0, le=7200, allow_inf_nan=False)
    max_output_tokens: int = Field(default_factory=lambda: get_settings().llm_max_output_tokens,
                                    ge=16, le=200000)
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    thinking: Literal["default", "enabled", "disabled"] = "default"
    api_version: str = Field(default="2024-10-21", max_length=50)


class BindingsInput(Input):
    bindings: dict[str, str | None]

    @field_validator("bindings")
    @classmethod
    def stages(cls, value):
        if set(value) - set(STAGES):
            raise ValueError("未知用途")
        return value


class ProbeInput(Input):
    kind: Literal["connection", "json", "embedding"] = "connection"


class ProbeDraft(Input):
    ok: bool
