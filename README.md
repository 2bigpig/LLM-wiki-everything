# everything

个人知识库：Vue 3 + TypeScript、FastAPI、PostgreSQL + pgvector、LangGraph、Procrastinate。产品范围见 [PRODUCT_REQUIREMENTS.md](PRODUCT_REQUIREMENTS.md)。 界面视觉与可访问性约定见 [UI 设计说明](docs/UI_DESIGN.md)。

## 启动：业务后端本地运行

FastAPI 和 Worker 是两个 Windows 本地 Python 进程，前端使用本地 Vite。Docker 只运行 PostgreSQL + pgvector、OCR、ASR、BGE-M3。

第一次在项目根目录准备环境和基础服务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-local.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start-services.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1 -Service migrate
powershell -ExecutionPolicy Bypass -File .\scripts\prepare-model-settings.ps1
```

准备脚本只在缺少 `.env` 时复制 `.env.example`，依赖安装到 `.venv`，便携 FFmpeg 解压到 `data/tools/ffmpeg`，只加入启动进程的 PATH。仅处理文字时可用 `-SkipFFmpeg` 跳过下载。

使用一条命令启动本地 API 和 Worker：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1
```

默认不启用热重载。需要时添加 `-Reload`，仅重载 API；修改 Worker 代码后统一停止并重新启动。

启动器检查配置的 API 端口（默认 8000）、数据库连接及必要表，API 健康检查通过后启动 Worker。控制台日志标注 `[API]`、`[Worker]`，按 Ctrl+C 统一停止，最多等待 25 秒后回收本次进程树。任一进程意外退出都会停止整组并报错，不自动重启，也不影响其他终端启动的服务。Docker 和前端不会随之停止。

排错时仍可在不同终端单独启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1 -Service api -Reload
powershell -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1 -Service worker
```

另开终端启动前端：

```powershell
npm --prefix frontend ci
npm --prefix frontend run dev
```

默认访问 **http://127.0.0.1:5173**，API 文档为 **http://127.0.0.1:8000/docs**。API 热重载只监视 `backend/src`；Worker 修改代码后需手动重启。启动脚本固定工作目录，避免 `.env`、资料目录和迁移路径随终端位置改变。

部署参数统一修改根目录 `config.yaml`。LLM 连接、模型、密钥及阶段选择在**设置页**管理，运行配置存 PostgreSQL；模型密钥经 Fernet 加密，主密钥留在 `.env`。

首次准备后直接打开“设置与用量”，选择厂商、填写厂商 API Key 和模型 ID，点击“测试并启用”。LLM 多连接和阶段覆写在“高级设置”中。Embedding 固定使用 BGE-M3，OCR 固定使用 PaddleOCR，用户无需更换或配置。开发阶段取消临时访问口令，用户登录与 RBAC 下一步统一实现。页面修改对新任务生效，无需重启；已有任务按修订恢复。未配置 LLM 仍可保存和读取资料，配置后可继续处理。

“LLM 执行时间”可按每日优惠时段统一安排分析、关联、Wiki 和深层回顾。支持跨午夜与时区，结束时默认完成本批，也可选择等待下次时段；默认不开启调度。保存与本地模型处理照常进行，手动测试即时执行，具体折扣以厂商规则为准。

支持 OpenAI、Claude、Gemini、DeepSeek 专用适配、Azure，以及 GLM 等国内厂商兼容连接。模型列表可读取或手动填写。测试可能产生费用；Token 额度不是货币账单，未知用量保守预留。Embedding 固定使用项目内置 BGE-M3（1024 维），页面和接口不提供模型替换。详细升级与排错见 [模型管理](docs/MODEL_MANAGEMENT.md)。

已有旧 `.env` 时，环境准备脚本会先校验并迁移普通参数，保留 `.env.backup.*` 备份；也可单独运行：

```powershell
.\.venv\Scripts\python.exe -m wiki_app.configuration migrate-env
.\.venv\Scripts\python.exe -m wiki_app.configuration check
```

`.env` 接受 `POSTGRES_PASSWORD`、`MODEL_CREDENTIAL_KEY`，以及仅供旧配置导入的 `LLM_API_KEY`；旧 `API_TOKEN` 仅为兼容保留，不参与鉴权。旧普通环境变量不再覆盖 YAML，检测到后会提示迁移。配置说明与重启规则见 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)。

## 数据库与模型服务

Windows GPU 容器使用已告知的 Docker Desktop WSL2 后端，无需在 Ubuntu 中另装 Python 或模型。

- PostgreSQL + pgvector：`127.0.0.1:5432`。当前复用本机已有的 PostgreSQL 16.14 镜像。
- OCR：`http://127.0.0.1:8101`，PaddleOCR 3.3.2 / Paddle GPU 3.2.2 / PP-OCRv5 mobile。
- ASR：`http://127.0.0.1:8102`，faster-whisper 1.2.1 / PyAV 16.1.0 / Whisper medium，`int8_float16`。
- BGE-M3：`http://127.0.0.1:8103`，独立 CPU 容器，生成 1024 维向量。本地后端不需要安装 Torch 或 sentence-transformers。
- OCR/ASR 通过 PostgreSQL 会话锁串行使用单卡，每次任务结束后子进程退出，释放显存。

公共 YAML 已配置本地服务端口，地址由统一加载器派生：

```yaml
services:
  ocr_port: 8101
  asr_port: 8102
  embedding_port: 8103
paths:
  data_dir: data
models:
  embeddings_enabled: true
  embedding_model: BAAI/bge-m3
```

以上是字段位置示例，请修改现有 YAML 中的对应分组，不要用片段覆盖完整配置。本地后端把资料写入 `paths.data_dir`，OCR/ASR 通过只读 bind mount 访问同一目录。两边只传相对 `storage_key`，避免 Windows/Linux 绝对路径差异。相对路径以配置文件目录为准；改变目录后重新运行服务脚本更新容器挂载，并重启后端。

BGE 第一次编码时加载模型，`/healthz` 的 `model_loaded=false` 仅表示尚未加载。停用语义检索可设置 `models.embeddings_enabled: false`，关键词检索仍可使用。新处理资料会建立向量，旧记录可重新分析补齐；内置向量模型由项目维护，不提供用户替换入口。

模型缓存保存在命名卷 `llm-wiki_ocr-models`、`llm-wiki_asr-models`、`llm-wiki_embeddings`。单独预下载/检查模型：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prepare-models.ps1 -SkipBuild -IncludeEmbeddings
```

没有镜像时去掉 `-SkipBuild`。Silero VAD 随 ASR 镜像安装。已有模型卷和镜像会复用，原 `llm-wiki-backend` 镜像不再参与启动流程。

2026-11-19 的 RTX 4060 8GB 样本验证：OCR 正确识别两行中英文并返回位置框；Whisper medium 中文转写及时间戳通过；BGE-M3 离线 CPU 生成 1024 维向量通过。基础样本不代表个人长视频、复杂文档的质量和性能已全面验收。

## 已实现的使用流程

1. 同一个输入框提交文字、多个链接、截图及附件；标题、备注和用途折叠选填。默认每次最多 10 个自动读取链接、10 个附件，支持取消个别链接读取。
2. 保存原件与持久化待办，后台自动读取和分段分析。
3. 依据候选旧资料判断具体联系，保存两侧逐字证据；无充分依据时允许没有联系。
4. 自动创建或更新带引用的 Wiki，展示更新历史。
5. 编辑个人 Wiki 笔记、查看历史、恢复版本、导出 Markdown。
6. 再次主动收藏保留独立时间和备注，通过内容资源连接时间线；失败来源可单独重试。
7. 在“知识图谱”探索收藏、资源、主题与 Wiki，在“发现与回顾”查看每日最多三条有依据的旧资料。

统一输入、图谱、回顾的使用、升级、API 与排错见 [docs/CAPTURE_DISCOVERY.md](docs/CAPTURE_DISCOVERY.md)。已有安装需要停止原 API/Worker、备份后执行 `start-backend.ps1 -Service migrate`，再重启；更新源码不会自动升级运行中的旧进程或数据库。

支持的本地素材扩展名：TXT、Markdown、PDF、DOCX、PNG、JPEG、WebP、MP3、WAV、M4A、FLAC、OGG、MP4、WebM、MKV、MOV。浏览器能否直接播放取决于原文件编码；原件始终可获取。

读取范围会明确显示：

- 网页：Trafilatura 提取正文；不宣称已读评论、所有内嵌附件或整个站点。
- 图片：OCR 文字与位置框；不宣称理解了非文字画面。
- PDF：文字提取，扫描页回退 OCR；首版单次最多 200 页。
- DOCX：段落与表格；嵌图、公式和批注暂未单独分析。
- 音频：语音转写与原始时间位置；首版不自动确认说话人身份。
- 视频：语音转写和采样帧 OCR；保留采样数量，画面覆盖为部分读取。
- YouTube / Bilibili 具体视频链接：yt-dlp 优先复用中文或英文字幕，无字幕时尝试在时长、文件大小上限内下载。平台可用性需实测，失败可上传本地素材。

“重新分析”复用已读取的证据；“重新读取原件”创建新的来源版本。旧引用保留并标记是否过期。归档属于可恢复操作，不执行永久删除。

## 动态网页回退

需要时安装本地 Playwright 扩展与浏览器，再设置 YAML 的 `web.browser_enabled: true`：

```powershell
.\.venv\Scripts\python.exe -m pip install -c backend/constraints.txt -e './backend[browser]'
.\.venv\Scripts\python.exe -m playwright install chromium
```

浏览器限制请求到已校验的公网原始站点，跨站资源、登录页面可能仍需手动补充。

## 日志与排错

本地日志默认为 `data/logs/api.log` 和 `data/logs/worker.log`，每份最多约 5MB，保留三份历史。在 YAML 的 `paths.log_dir`、`logging` 分组修改目录、级别和轮转参数。

```powershell
Get-Content data/logs/worker.log -Encoding UTF8 -Tail 80 -Wait
powershell -ExecutionPolicy Bypass -File .\scripts\start-services.ps1 logs --follow ocr asr bge
```

- 响应头 `X-Request-ID` 对应 API 日志的 `request_id`，不记录鉴权头、查询参数或用户正文。
- 页面任务 ID 对应 Worker 的 `run_id`；`stage` 指明 extract/analyze/relate/wiki，方便定位最后执行阶段。
- OCR/ASR 客户端与容器使用相同 `request_id`，记录文件键、等待 GPU、耗时和子进程退出信息。
- “共享文件不存在”：核对 YAML 的 `paths.data_dir` 和容器挂载，不能传 Windows 绝对路径作为 `storage_key`。
- 连接失败：先运行 `powershell -ExecutionPolicy Bypass -File .\scripts\start-services.ps1 ps`，再检查对应服务的 `/healthz`。
- 修改 YAML 后重启后端和前端；涉及模型参数时用 `start-services.ps1 up --force-recreate ocr asr bge` 重新创建模型容器。涉及端口、挂载时重新运行默认服务脚本。只修改推理源码时同样可使用 force-recreate。
- 数据库主版本不能直接切换后复用旧卷；`.env` 的 `POSTGRES_PASSWORD` 需与数据库初始化密码一致，修改文件不会自动修改库内密码。

## 本地验证

验证命令：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe -m ruff check backend
cd frontend
npm run build
npm run test:config
npm run test:e2e
```

业务测试默认使用独立 SQLite 内存库，并注入离线模型替身。可选 PostgreSQL 集成测试使用 `TEST_DATABASE_URL`，创建随机测试 schema，并只清理该 schema；目标数据库需要 pgvector 和建表权限。

浏览器测试会启动独立测试 API、自动处理线程与 Vite，测试模型只存在于 `backend/tests/serve_app.py`。测试数据写入 `data/browser-tests/`；测试拒绝复用已占用端口，默认需要 8000/5173 空闲，也可通过独立临时 YAML 调整。Windows 启动器验收另外使用独立临时端口和数据库。Windows 自动使用 Program Files 下的 Chrome，其他环境可先运行 `npx playwright install chromium`；也可设置 `PLAYWRIGHT_EXECUTABLE_PATH`。

测试关注：重复提交、断点恢复、无密钥保存、预算预留、无效引用拒绝、关联纠正保留、Wiki 人工编辑保护、版本冲突、历史恢复、旧引用追溯、文件清理和公网访问边界。离线模型测试不代表真实模型的整理质量已通过验收。

方案 C 验收：101 项后端、8 项桌面/手机浏览器通过，构建与静态检查通过。覆盖旧数据迁移、跨年与幂等收藏、部分失败和来源重试、证据去重、图谱容量、时区/冷却/暂停、原生 PostgreSQL 并发及 Windows 启停。详情见 [功能与验收说明](docs/CAPTURE_DISCOVERY.md)。离线模型通过不等同于真实云模型质量验收。

## 数据与目录

普通参数集中到 YAML；方案 C 增加 `capture`、`graph`、`review` 配置与 0002 数据库迁移。原 PostgreSQL 和模型缓存卷继续复用，密钥仅留在 `.env`。

项目采用模块化单体后端：API 和 Worker 共享业务模块与数据库，以两个进程承担交互和后台任务。模型在独立容器中运行，通过 HTTP 调用。

```text
llm_wiki_everything/
├─ backend/                         # Python 后端
│  ├─ src/wiki_app/
│  │  ├─ modules/
│  │  │  ├─ capture/                # 统一识别、资源、逐来源读取与主题证据
│  │  │  ├─ graph/                  # 有界图谱与时间线查询
│  │  │  ├─ review/                 # 每日回顾、用户偏好与可选解释
│  │  │  ├─ library/                # 原始记录、附件、归档和重新处理
│  │  │  ├─ knowledge/              # Wiki、版本、引用和内容联系
│  │  │  ├─ processing/             # LangGraph 自动整理流程
│  │  │  └─ search/                 # 关键词、向量检索及结果融合
│  │  ├─ integrations/              # 网页、媒体、LLM、文件存储和资源锁
│  │  ├─ main.py                    # HTTP API 入口
│  │  ├─ worker.py                  # 持久化任务消费者
│  │  ├─ launcher.py                # API/Worker 统一启动与停止
│  │  ├─ windows_job.py             # Windows 子进程树清理
│  │  ├─ models.py                  # 数据库表结构定义，不存放实际数据
│  │  ├─ schemas.py                 # API 输入和模型输出校验
│  │  ├─ config.py、db.py            # 环境配置、数据库连接和事务
│  │  └─ migrate.py                 # 业务表、队列及检查点初始化
│  ├─ migrations/                   # Alembic 迁移和 SQL，属于源码
│  ├─ tests/                        # 后端、数据库、队列和启动器测试
│  ├─ pyproject.toml                # Python 依赖和工具配置
│  └─ constraints.txt               # 已验证的依赖版本约束
├─ frontend/
│  ├─ src/features/                 # 收藏、图谱、回顾、Wiki、搜索、任务和设置页面
│  ├─ src/components/               # 共享界面组件
│  ├─ src/api.ts                    # API 类型与请求封装
│  ├─ tests/                        # 桌面和移动端浏览器测试
│  └─ package-lock.json             # 前端依赖锁文件，需要提交
├─ deploy/                          # 模型服务、Dockerfile 与下载脚本
├─ scripts/                         # 本地环境、后端启动和模型准备脚本
├─ docs/                            # 公共架构文档
├─ .github/                         # GitHub Actions CI
├─ data/                            # 本机资料、日志、工具和测试产物，不提交
├─ .venv/                           # 本地 Python 环境，不提交
├─ .env                             # 本机密钥，不提交
├─ .env.example                     # 可公开的配置模板
├─ config.yaml                      # 公共参数唯一入口，不含密钥
├─ compose.yaml、compose.gpu.yaml    # 数据库和模型服务编排
└─ README.md、LICENSE               # 项目说明和 MIT 许可证
```

实际业务记录、向量、任务队列和检查点保存在 Docker 卷 `llm-wiki_postgres`；模型权重分别保存在 `llm-wiki_ocr-models`、`llm-wiki_asr-models`、`llm-wiki_embeddings`，不进入 Git。`models.py` 和迁移 SQL 定义数据库结构，需要随源码提交。

`data/` 会保存上传原件、衍生文件、日志、便携工具及测试截图。前后端工作目录下产生的运行数据也会被忽略；源码中的 `src/data` 不会因为目录名而被排除。个人代理配置和协作过程记录留在本机。

`deploy/Dockerfile.frontend` 与 `deploy/nginx.conf` 是早期保留的前端容器配置，当前默认启动链路未使用，其中的 `api` 容器地址也不适用于当前本地后端模式。当前前端按上文通过 Vite 启动。

Docker 的 PostgreSQL 卷保存业务数据与执行状态，本地 `paths.data_dir` 保存原件和衍生文件。备份时两者需要一起保存；模型缓存可重新下载。日常停止用 `start-services.ps1 stop`，保留数据卷。不要使用带 `-v` 的 Compose down 命令。

## 开源许可

本项目代码采用 [MIT License](LICENSE)，版权署名为 2026 2bigpig。第三方依赖、FFmpeg 和各模型权重遵循各自的许可证；本仓库提供接入与下载配置，不包含这些模型权重或便携工具二进制文件。

## 首版边界

尚未实现聊天问答、说话人分离、视觉模型画面理解、浏览器扩展、手机分享入口、收藏批量导入、多用户和永久删除。AI 正文通过来源与版本核对，人工补充在独立“个人笔记”中编辑；首版不提供任意改写 AI 正文后的自动合并。

这版提供可运行的核心流程与推理接入代码。平台读取成功率、真实转写与 OCR 效果、模型引用质量和 4060 性能，需要用个人真实素材进一步验收。
