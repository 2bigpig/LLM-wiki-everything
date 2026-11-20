"""本地 API/Worker 统一启动器；只管理本次创建的两个进程，不启动 Docker 或前端。"""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from wiki_app.config import get_settings

ROOT = Path(__file__).resolve().parents[3]
MIGRATE_COMMAND = "powershell -ExecutionPolicy Bypass -File .\\scripts\\start-backend.ps1 -Service migrate"


def announce(message: str):
    """启动器本身输出到当前终端；业务日志继续由 API/Worker 分别落盘。"""
    print(f"[启动器] {message}", flush=True)


def check_environment():
    """只做连接与表结构检查，不迁移、不改数据库，也不占用已有服务的端口。"""
    virtual_python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not virtual_python.is_file():
        raise RuntimeError("缺少 .venv，请先运行 scripts/setup-local.ps1。")
    try:
        settings = get_settings()
    except ValueError as exc:
        raise RuntimeError(str(exc)) from None
    try:
        family = socket.AF_INET6 if ":" in settings.api_host else socket.AF_INET
        with socket.socket(family) as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((settings.api_host, settings.api_port))
    except OSError as exc:
        raise RuntimeError(f"端口 {settings.api_port} 已占用或不可用，请先检查已有 API；不会自动停止它。") from exc
    try:
        import psycopg

    except (ImportError, ValueError) as exc:
        raise RuntimeError("依赖或 .env 配置不完整，请运行 scripts/setup-local.ps1 并检查 .env。") from exc
    try:
        with psycopg.connect(settings.postgres_dsn,
                             connect_timeout=settings.db_connect_timeout_seconds) as connection:
            names = ("records", "attachments", "sources", "analyses", "relations", "wiki_pages",
                     "wiki_revisions", "model_calls", "processing_runs", "procrastinate_jobs",
                     "checkpoints", "checkpoint_blobs", "checkpoint_writes")
            names += ("resources", "capture_inputs", "topics", "record_topics", "capture_insights",
                      "review_batches", "review_cards", "review_preferences", "resource_review_states")
            names += ("model_connections", "managed_models", "model_revisions", "model_preferences",
                      "model_probes", "embedding_spaces", "source_vectors")
            with connection.cursor() as cursor:
                missing = []
                for name in names:
                    cursor.execute("SELECT to_regclass(%s)", (name,))
                    if cursor.fetchone()[0] is None:
                        missing.append(name)
            if missing:
                raise RuntimeError(f"数据库缺少表 {', '.join(missing)}；请执行：{MIGRATE_COMMAND}")
            with connection.cursor() as cursor:
                for table, required in {"model_preferences": {"llm_schedule"},
                        "processing_runs": {"llm_admitted_at", "scheduled_for"},
                        "capture_insights": {"llm_admitted_at", "scheduled_for"}}.items():
                    cursor.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema=current_schema() AND table_name=%s", (table,))
                    if required - {row[0] for row in cursor.fetchall()}:
                        raise RuntimeError("数据库缺少调度字段，请执行 scripts/prepare-model-settings.ps1 完成开发环境准备。")
    except psycopg.Error as exc:
        # 不输出包含 DSN 的底层异常，避免密码进入终端或日志。
        raise RuntimeError(
            "数据库连接失败，请启动 Docker 服务，检查 config.yaml 的 database 和 .env 的密码。"
        ) from exc


def api_main(reload: bool = False):
    """Windows 定向停止使用 Ctrl+Break；重载监视器需要将它转成 SIGINT。"""
    import uvicorn

    if os.name == "nt":
        signal.signal(signal.SIGBREAK, lambda *_: signal.raise_signal(signal.SIGINT))
    try:
        settings = get_settings()
        uvicorn.run("wiki_app.main:app", host=settings.api_host, port=settings.api_port, reload=reload,
                    reload_dirs=[str(ROOT / "backend/src")] if reload else None)
    except KeyboardInterrupt:
        pass


def forward_output(role: str, process):
    """持续排空输出管道，标注来源；防止日志过多导致子进程阻塞。"""
    with process.stdout:
        for line in process.stdout:
            print(f"[{role}] {line.rstrip()}", flush=True)


def start_process(role: str, command: list[str], job):
    """参数数组直接启动同一 Python 环境，避免 shell 转义和多余的命令解释进程。"""
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x4 if os.name == "nt" else 0
    process = subprocess.Popen(
        command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace", bufsize=1,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"},
        creationflags=flags, start_new_session=os.name != "nt",
    )
    try:
        if job:
            job.attach_and_resume(process)
    except Exception:
        process.kill()
        process.wait()
        process.stdout.close()
        raise
    reader = threading.Thread(target=forward_output, args=(role, process), daemon=True)
    reader.start()
    announce(f"{role} 已启动 pid={process.pid}")
    return process, reader


def stop_processes(processes: list, job, timeout: float):
    """先定向请求正常退出；共享停止预算后回收本次进程树，包含重载后代。"""
    for process in processes:
        if process.poll() is None:
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                pass  # 无控制台或进程已退出时，后续 Job 清理仍能保证没有残留。
    deadline = time.monotonic() + timeout
    while any(process.poll() is None for process in processes) and time.monotonic() < deadline:
        time.sleep(get_settings().health_poll_seconds)
    if any(process.poll() is None for process in processes):
        announce("停止等待超时，正在结束本次启动的进程树。")
    if job:
        job.close()
    else:
        for process in processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    for process in processes:
        process.wait()


def supervise(api_command: list[str], worker_command: list[str],
              startup_timeout: float | None = None, stop_timeout: float | None = None) -> int:
    """API 健康后才启动消费者；异常退出采用整组停止，不悄悄重启或留下半套后端。"""
    settings = get_settings()
    startup_timeout = settings.startup_timeout_seconds if startup_timeout is None else startup_timeout
    stop_timeout = settings.stop_timeout_seconds if stop_timeout is None else stop_timeout
    stopping = threading.Event()
    signals = (signal.SIGINT, signal.SIGTERM) + ((signal.SIGBREAK,) if os.name == "nt" else ())
    previous = {sig: signal.signal(sig, lambda *_: stopping.set()) for sig in signals}
    processes, readers = [], []
    job = None
    try:
        if os.name == "nt":
            from wiki_app.windows_job import WindowsJob

            job = WindowsJob()
        api, reader = start_process("API", api_command, job)
        processes.append(api)
        readers.append(reader)
        # 绕过系统代理；本机健康检查不应转发到外部代理服务器。
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + startup_timeout
        while not stopping.is_set():
            if api.poll() is not None:
                raise RuntimeError(f"API 意外退出，退出码 {api.returncode}。")
            try:
                with opener.open(settings.api_url + "/healthz",
                                 timeout=settings.health_timeout_seconds) as response:
                    ready = response.status == 200 and json.load(response).get("status") == "ok"
                if ready:
                    break
            except (OSError, URLError, ValueError, AttributeError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError(f"API 在 {startup_timeout:g} 秒内未就绪，请查看 API 日志。")
            stopping.wait(settings.health_poll_seconds)
        if stopping.is_set():
            return 0
        worker, reader = start_process("Worker", worker_command, job)
        processes.append(worker)
        readers.append(reader)
        announce("API 已就绪，Worker 已启动。按 Ctrl+C 统一停止；业务日志位于 LOG_DIR。")
        while not stopping.wait(settings.process_poll_seconds):
            for role, process in zip(("API", "Worker"), processes, strict=True):
                if process.poll() is not None:
                    raise RuntimeError(f"{role} 意外退出，退出码 {process.returncode}。")
        return 0
    except (OSError, RuntimeError) as exc:
        announce(f"启动或运行失败：{exc}")
        return 1
    finally:
        announce("正在停止本次启动的 API/Worker…")
        try:
            stop_processes(processes, job, stop_timeout)
            for reader in readers:
                reader.join(timeout=settings.output_join_seconds)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def main() -> int:
    """供 PowerShell both 入口调用；独立 API/Worker 启动方式保持兼容。"""
    parser = argparse.ArgumentParser(description="统一启动本地 API 和 Worker")
    parser.add_argument("--reload", action="store_true", help="仅为 API 启用源码热重载")
    args = parser.parse_args()
    os.chdir(ROOT)
    try:
        check_environment()
    except RuntimeError as exc:
        announce(str(exc))
        return 1
    return supervise(
        [sys.executable, "-u", "-c", "from wiki_app.launcher import api_main; "
         f"api_main(reload={args.reload!r})"],
        [sys.executable, "-u", "-c", "from wiki_app.worker import main; main()"],
    )


if __name__ == "__main__":
    raise SystemExit(main())
