"""协议调用只发一次请求；统一计量由调用方负责，SDK 不接管重试或预算。"""

import math
from functools import cached_property

import httpx

from wiki_app.config import get_settings
from wiki_app.errors import AppError


def model_error(exc):
    """不回显供应商异常正文；正文和 URL 可能含密钥或用户资料。"""
    status = getattr(exc, "status_code", None)
    code = type(exc).__name__
    reason = {401: "密钥无效", 403: "无权限或余额不足", 404: "模型或地址不存在",
              429: "限流或额度不足"}.get(status, "网络、参数或模型服务异常")
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)) or "Timeout" in code:
        reason = "请求超时"
    return AppError(f"模型调用失败：{reason}（{code}）。请检查连接和模型参数。", "model_error", 502)


def client():
    # 即使供应商 SDK 默认允许跳转，也不让敏感头被送往另一个目标。
    return httpx.Client(follow_redirects=False, trust_env=False)


def chat(config, key, messages, schema=None):
    """返回校验结果及规范化 usage；保留原消息方便处理校验失败时的已发生费用。"""
    protocol = config["protocol"]
    kwargs = {"model": config["model"], "api_key": key or "local-no-auth",
              "timeout": config["timeout_seconds"], "max_retries": 0}
    if config.get("temperature") is not None:
        kwargs["temperature"] = config["temperature"]
    if protocol in ("openai", "deepseek", "azure"):
        from langchain_openai import AzureChatOpenAI, ChatOpenAI
        with client() as http:
            kwargs.update(http_client=http, max_tokens=config["max_output_tokens"])
            extra = {} if config["thinking"] == "default" else {"thinking": {"type": config["thinking"]}}
            if extra:
                kwargs["extra_body"] = extra
            if protocol == "azure":
                kwargs.pop("model")
                model = AzureChatOpenAI(azure_endpoint=config["base_url"],
                    azure_deployment=config["model"], api_version=config["api_version"], **kwargs)
            elif protocol == "deepseek":
                from langchain_deepseek import ChatDeepSeek
                model = ChatDeepSeek(api_base=config["base_url"], **kwargs)
            else:
                model = ChatOpenAI(base_url=config["base_url"], use_responses_api=False, **kwargs)
            if schema:
                model = model.bind(response_format={"type": "json_object"})
            raw = model.invoke(messages)
    elif protocol == "anthropic":
        import anthropic
        from langchain_anthropic import ChatAnthropic

        class DirectAnthropic(ChatAnthropic):
            # 当前集成未暴露同步传输参数；覆写集中在协议层，契约测试锁定行为。
            @cached_property
            def _client(self):
                return anthropic.Client(**self._client_params,
                    http_client=anthropic.DefaultHttpxClient(follow_redirects=False, trust_env=False))

        extra = {} if config["thinking"] == "default" else {"thinking":
            {"type": "enabled", "budget_tokens": 1024} if config["thinking"] == "enabled" else {"type": "disabled"}}
        model = DirectAnthropic(base_url=config["base_url"], max_tokens=config["max_output_tokens"], **extra, **kwargs)
        try:
            if schema:
                return structured(model.with_structured_output(schema, include_raw=True).invoke(messages))
            raw = model.invoke(messages)
        finally:
            model._client.close()
    elif protocol == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        extra = {} if config["thinking"] == "default" else {"thinking_config":
            {"thinking_budget": 1024 if config["thinking"] == "enabled" else 0}}
        model = ChatGoogleGenerativeAI(**kwargs, max_output_tokens=config["max_output_tokens"],
            base_url=config["base_url"], client_args={"follow_redirects": False, "trust_env": False}, **extra)
        try:
            if schema:
                return structured(model.with_structured_output(schema, method="json_schema", include_raw=True).invoke(
                    messages, http_options={"retry_options": {"attempts": 1}}))
            raw = model.invoke(messages, http_options={"retry_options": {"attempts": 1}})
        finally:
            model.client.close()
    else:
        raise AppError("此连接不支持聊天模型")
    content = raw.content
    if isinstance(content, list):
        content = "".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
    return {"text": content or "", "usage": raw.usage_metadata or {}, "parsed": None,
            "incomplete": incomplete(raw)}


def incomplete(message):
    reason = str(message.response_metadata.get("finish_reason", message.response_metadata.get("stop_reason", ""))).lower()
    return reason in ("length", "max_tokens", "max_output_tokens")


def structured(result):
    """即使结构化解析失败，也将 raw usage 交回计量层，不能把已发生的调用当免费。"""
    parsed = result.get("parsed")
    return {"text": parsed.model_dump_json() if parsed is not None else "",
            "parsed": parsed, "usage": result["raw"].usage_metadata or {},
            "incomplete": incomplete(result["raw"])}


def validate_vectors(vectors, count):
    if len(vectors) != count or any(not isinstance(v, list) or len(v) != 1024 or any(
        not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x)
        for x in v) for v in vectors):
        raise AppError("Embedding 必须为 1024 维有限数值，且与输入数量一致。", "embedding_failed", 502)
    return vectors


def embedding(config, key, texts):
    """BGE 复用现有 /embed；云端用原始响应保留 usage，不由适配器虚构用量。"""
    with client() as http:
        if config["protocol"] == "bge":
            response = http.post(config["base_url"] + "/embed", json={"texts": texts, "model": config["model"]},
                                 timeout=config["timeout_seconds"])
            response.raise_for_status()
            data = response.json()
            if data.get("model") != config["model"]:
                raise AppError("向量服务返回的模型不匹配", "embedding_failed", 502)
            return validate_vectors(data["embeddings"], len(texts)), None
        if config["protocol"] in ("openai", "azure"):
            from openai import AzureOpenAI, OpenAI
            args = {"api_key": key or "local-no-auth", "max_retries": 0, "http_client": http,
                    "timeout": config["timeout_seconds"]}
            if config["protocol"] == "azure":
                sdk = AzureOpenAI(azure_endpoint=config["base_url"], api_version=config["api_version"], **args)
            else:
                sdk = OpenAI(base_url=config["base_url"], **args)
            response = sdk.embeddings.create(model=config["model"], input=texts)
            vectors = [x.embedding for x in sorted(response.data, key=lambda x: x.index)]
            usage = {"input_tokens": response.usage.prompt_tokens, "output_tokens": 0,
                     "total_tokens": response.usage.total_tokens} if response.usage else {}
        elif config["protocol"] == "google":
            from google import genai
            with genai.Client(api_key=key, http_options={"base_url": config["base_url"],
                "timeout": int(config["timeout_seconds"] * 1000),
                "retry_options": {"attempts": 1},
                "client_args": {"follow_redirects": False, "trust_env": False}}) as sdk:
                result = sdk.models.embed_content(model=config["model"], contents=texts,
                    config={"output_dimensionality": 1024})
                vectors = [list(v.values) for v in result.embeddings]
                usage = {}
        else:
            raise AppError("该协议不支持 Embedding")
    return validate_vectors(vectors, len(texts)), usage


def discover(config, key):
    if config["protocol"] == "bge":
        return [get_settings().embedding_model]
    endpoint = config["base_url"].rstrip("/") + "/models"
    headers = {"Authorization": "Bearer " + key} if key else {}
    if config["protocol"] == "anthropic":
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif config["protocol"] == "google":
        endpoint = config["base_url"].rstrip("/") + "/v1beta/models"
        headers = {"x-goog-api-key": key}
    with client() as http:
        response = http.get(endpoint, headers=headers, timeout=get_settings().model_management.probe_timeout_seconds)
        response.raise_for_status()
        data = response.json()
    return list(dict.fromkeys(x.get("id") or x.get("name", "").removeprefix("models/")
        for x in data.get("data", data.get("models", []))))[:get_settings().model_management.discovery_limit]
