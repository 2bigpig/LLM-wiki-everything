# 统一配置说明

普通参数修改项目根目录 `config.yaml`，密钥修改 `.env`。代码中的 `settings.xxx` 是经过类型校验的配置引用，Compose 中的 `${WIKI_...}` 由启动工具注入，不需要手工填写。公共 YAML 可以随源码提交；密钥与迁移备份必须留在本机。

## 参数分组

- `services`：API、前端和模型服务的宿主机端口、监听地址以及额外 CORS。API 健康检查、Vite 代理和默认 CORS 自动使用同一组端口。
- `database`：本机/容器内连接地址、端口、数据库名、用户和连接超时；DSN 自动组合并转义密码。
- `paths`：本机资料、日志、便携 FFmpeg 目录及容器内资料和模型缓存目录。本机相对路径基于配置文件所在目录；容器缓存需要位于持久化卷 `/models` 下。
- `launcher`、`worker`：启动等待、整组退出、健康检查、并发、重试、派发批量和心跳。停止预算须大于 Worker 优雅退出时间，停滞阈值须大于心跳间隔。
- `containers`：Docker 健康检查的间隔、超时和重试次数。
- `llm`：OpenAI 兼容接口地址、模型、Token 额度和调用超时；`llm_model` 为空表示尚未配置。SDK 内部自动重试固定关闭，避免预算重复消耗。
- `models`：服务开关、OCR 检测/识别模型、Whisper 模型与解码选项、推理超时，以及 BGE 批量、文本和序列长度。BGE 继续使用 CPU；OCR/ASR 沿用单卡串行执行。
- `processing`：上传、时长、分段、PDF、DOCX、图片和视频采样限制。增大资源上限需要评估本机内存和 8GB 显存。
- `web`：网页读取、浏览器回退、字幕、平台下载、重定向和网络超时。
- `search`：检索数量、关键词/向量召回、RRF 融合、索引批量、关联候选、Wiki 扫描与上下文限制，以及结果历史数量。
- `logging`、`tools`：日志级别、轮转、FFmpeg 下载源、OCR 下载源和模型下载等待参数。
- `capture`：单次自动读取链接与附件数，默认各 10 个；取消读取的链接仍完整保留在原始输入中。上传总大小复用 `processing.max_upload_mb`。
- `graph`：节点/边容量（可下调，硬上限 200/500）、默认一跳、最多三跳。不向浏览器加载整库。
- `review`：启停、时区、每日时间、数量、详情提示数量、近期窗口、旧资料门槛、冷却与暂停天数、Worker 检查间隔及可选深层解释。默认上海 09:00、每天至多 3 条、旧资料至少 14 天、展示后冷却 7 天。关闭 `review_insights_enabled` 可禁用额外解释模型调用，事实提示和每日卡片照常可用。

向量维度固定为 1024，修改模型必须匹配该维度，并重新分析资料重建向量。方案 C 需要执行 0002 数据库迁移，操作见 [统一输入与发现](CAPTURE_DISCOVERY.md)。状态枚举、API 协议、持久化任务名称、提示词、界面样式、依赖版本和测试夹具继续由源码或各自清单维护。

## 私密配置与旧环境迁移

`.env` 仅接受 `POSTGRES_PASSWORD`、`LLM_API_KEY`、`API_TOKEN`。同名进程环境变量优先于文件，普通环境变量不能覆盖 YAML；发现旧普通键会列出键名并停止启动，不打印值。`TEST_DATABASE_URL`、Playwright 浏览器路径等独立测试入口不受影响。

首次准备环境使用 `scripts/setup-local.ps1`，它会在安装依赖后执行迁移。已有环境可单独执行：

```powershell
.\.venv\Scripts\python.exe -m wiki_app.configuration migrate-env
.\.venv\Scripts\python.exe -m wiki_app.configuration check
```

迁移以旧 `.env` 中的显式参数为准更新 YAML，保留中文注释和密钥原值；冲突或未知键会停止且不修改文件。成功前备份原 `.env` 和 YAML 为 `.env.backup.<时间戳>`、`.env.backup.<时间戳>.yaml`，两者均被 Git 和 Docker 忽略。写入失败会恢复原文件。

`WIKI_CONFIG_FILE` 是配置入口定位参数，可为测试选择另一份完整 YAML，不作为业务参数覆盖；其同目录 `.env` 也是独立的。`WIKI_RUNTIME` 仅由容器入口选择 host/ocr/asr/bge，普通使用无需设置。测试通过临时 YAML 和独立数据库隔离，不读取或清理个人资料。

## 启动与变更生效

在项目根目录使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-services.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1
# 另开终端
npm --prefix frontend run dev
```

服务脚本支持 `up`、`stop`、`ps`、`logs`、`build`，默认 `up -d`。例如 `start-services.ps1 logs --follow ocr asr bge` 查看日志。原生 Compose 无法自动读取公共 YAML，请通过脚本，或 `.venv/Scripts/python.exe -m wiki_app.configuration compose <命令>` 使用同一解析入口；不要将解析后的完整 Compose 配置输出到公共日志，因为其中含数据库密码。

- 修改后端参数或密钥：Ctrl+C 停止并重启后端；`-Reload` 仅用于 API 源码，不负责重载配置或 Worker。
- 修改 `capture`、`graph`、`review`：重启 API/Worker。回顾偏好页的启停、暂停和反馈即时写入 PostgreSQL，无需改 YAML；已生成的当日批次不会因改配置重新生成。修改回顾时区可能生成该新时区的当日批次，建议次日再调整。
- 修改前端地址或 API 端口：同时重启后端和 Vite，代理随配置更新；端口被占用会明确失败，不自动换端口。
- 修改模型参数：执行 `start-services.ps1 up --force-recreate ocr asr bge`，并重启后端。改变模型名称后先运行模型准备脚本下载/加载对应权重。
- 修改宿主机端口、资料目录或容器挂载：重新运行默认服务脚本并重启后端，现有命名卷继续复用；修改资料目录不会自动移动旧文件。
- 修改数据库用户、库名或密码不会修改已有数据库；应先显式完成数据库管理操作，再同步配置。
- 修改镜像依赖：执行 `start-services.ps1 build`，完成后再启动服务。旧镜像在未挂载配置模块时不兼容新入口，第一次更新至少重新构建 BGE 镜像。

模型准备仍使用 `scripts/prepare-models.ps1 -SkipBuild -IncludeEmbeddings`；没有匹配镜像时去掉 `-SkipBuild`。所有进程都在启动时校验配置，不增加后台配置管理服务，不支持配置热更新。实际原件、数据库数据与模型权重不写入 YAML 或 Git。
## 运行时模型配置（0003 起）

厂商预设位于 `model_management.provider_presets`，只包含公共连接模板。管理页编辑的连接、模型参数、阶段选择和向量空间保存 PostgreSQL，YAML 不再覆盖它们。原 `llm` 参数只用于首次导入，预算上限仍生效。

`.env` 新增 `MODEL_CREDENTIAL_KEY`，由 `scripts/prepare-model-settings.ps1` 生成；它是凭据加密主密钥，必须与数据库备份配对保管。`LLM_API_KEY` 仅兼容首次迁移。运行时配置无需重启；实例口令、主密钥或部署配置变化需重启后端。

Embedding 本轮固定 1024 维；改变模型需要独立空间重建并切换，不能仅改容器模型后继续使用旧向量。完整说明见 [模型管理](MODEL_MANAGEMENT.md)。
