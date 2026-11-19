# 统一输入、图谱与站内回顾

## 使用

点击“记录内容”，直接输入文字、粘贴分享文案和多个链接、粘贴截图或拖入文件。文件可以连续添加和移除；标题、用途和收藏原因在“更多选项”。识别预览只解析输入，不访问网页或调用模型，取消勾选的链接仍保留在原文。保存时后端重新校验数量、大小和来源。

相同视频或附件可以再次主动收藏，生成新的时间点和备注；详情展示历史入口。双击、网络重试或后台重试不增加收藏次数。资源身份依据规范化 URL、YouTube/Bilibili 稳定视频 ID、文件 SHA-256；短链接后台经公网安全校验后解析，不按标题合并。每次主动收藏正常重新读取，首版不跨收藏复用 OCR/ASR 缓存。

“输入来源”逐项显示成功、取消或错误。部分读取失败仍整理成功部分；点击“重试此来源”只重新读取该失败项，成功片段复制到新版本，历史引用继续可访问。全部失败且没有有效正文时不分析、不生成 Wiki；全部取消时仅保存。无密钥或预算不足不阻止保存、事实图谱和基础回顾。

“知识图谱”展示收藏事件、内容资源、主题和 Wiki，可搜索、按时间/类型筛选、含归档历史、选节点展开邻居。默认一跳，最多三跳，单次上限 200 节点/500 边；超限请缩小范围。选择资源查看收藏时间线；选择关系核对原因、来源版本、证据并认可/忽略。系统事实、AI 建议与用户认可分别标识，失效证据不参与新解释，历史反馈保留。主题不自动合并近义词，也不把混合记录的主题分给每个附件。

再次收藏后的历史提示即时呈现；整理完成后的详情最多显示两条有依据的旧内容联系。有两次明确个人表达时，可以异步生成“可能相关”的深层解释，复用已有模型与预算；模型不可用只影响该解释。只有再次收藏事实时不推断个人情感或偏好。

“发现与回顾”默认每天上海时间 09:00 后由 Worker 生成至多三条卡片。优先近期新资料相关的旧内容，其次近期再次收藏，再其次长期未回顾；旧内容至少保存 14 天，按资源去重。只有真正展示或反馈后才进入七天冷却；不足时少展示。关闭服务期间不通知、不积压历史批次，重启只补当天。卡片复用已有证据，不额外调用 LLM。归档资源不推荐。

支持“有帮助”“暂不需要”“不准确”“不再推荐”；可以停用、暂停七天、恢复或取消屏蔽。反馈与偏好存数据库，不覆盖个人笔记。公共默认值见 `config.yaml` 的 `capture`、`graph`、`review`，修改后重启 API/Worker。

## 从旧版本升级

先在原终端 Ctrl+C 停止本地 API/Worker，并备份 PostgreSQL 与 `paths.data_dir`；不要删除 Docker 卷。以下命令安装新增直接依赖、升级前端锁文件与数据库，最后正常启动：

```powershell
.\.venv\Scripts\python.exe -m pip install -c backend/constraints.txt -e './backend[dev]'
npm --prefix frontend ci
powershell -ExecutionPolicy Bypass -File .\scripts\start-services.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1 -Service migrate
powershell -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1
# 另开终端
npm --prefix frontend run dev
```

0002 保留旧记录 ID、收藏时间、旧来源 ID 和 Wiki 引用，回填资源、当前来源关联及主题，移除跨记录 URL 唯一约束。旧短文中的引用链接不会因迁移变成自动读取任务。平台下载的衍生媒体不会再次变成上传输入。没有更改数据库卷或模型缓存卷。已有重复收藏时不能安全降回 0001，降级命令会拒绝；需要回退应用时恢复事先完整备份。

## 接口与排错

新接口沿用 API 鉴权，具体字段见本地 `/docs`：

- `POST /api/captures/recognize`：纯解析预览；`POST /api/captures`：multipart 统一保存，可带 `Idempotency-Key`。旧 records/上传接口保留。
- `POST /api/records/{id}/inputs/{input_id}/retry`：单来源重试，旧来源版本不变。
- `GET /api/graph`：搜索/筛选，以 `focus=类型:ID` 查询邻居；`GET /api/graph/nodes/{kind}/{id}`：详情与时间线；`PATCH /api/graph/topics/{id}`：主题反馈。已有联系反馈接口继续可用。
- `GET /api/reviews`、`PATCH /api/reviews/preferences`、`POST /api/reviews/{id}/feedback`：每日卡片、偏好和反馈；`GET /api/reviews/blocked` 与 `POST /api/reviews/resources/{id}/restore`：屏蔽管理。
- `PATCH /api/insights/{id}`、`POST /api/insights/{id}/retry`：可选解释反馈与恢复。

“缺少必要表”说明尚未升级数据库；“没有今日回顾”先核对时区、09:00、Worker 是否运行、暂停/归档/冷却和十四天门槛，没有候选是正常状态。图谱没有主题可能是模型未配置或尚未完成分析，事实来源仍可查看。“解释依据已过期”需要核对新版本，旧引文仍能从历史来源打开。链接读取失败可重试对应来源或添加本地文件；共享文件/模型服务问题沿用 README 排错步骤。

## 验收边界

后端使用离线来源与模型替身检验引用、故障和预算语义，原生 PostgreSQL 在随机 schema 验证旧数据迁移、JSON 引用查询、资源/请求并发和每日唯一批次。Windows 验收使用独立数据库和临时端口，不接管已有服务。浏览器覆盖桌面和手机统一输入、截图、时间线、图谱与回顾。真实平台权限、模型整理质量和个人长视频性能仍需真实素材验收。

2026-11-19 验收：101 项后端通过（含原生 PostgreSQL、真实队列和 Windows 进程验收），8 项桌面/手机浏览器通过（含混合输入、截图、拖放、部分失败及来源重试），Node 配置测试、前端构建、Ruff、依赖、Compose 静默解析与 PowerShell 语法检查通过。沿用现有卷的真实 OCR/ASR 各返回有效片段、BGE-M3 返回 1024 维向量。验收没有对用户正在运行的实例执行 0002 迁移，也没有提交或推送。
