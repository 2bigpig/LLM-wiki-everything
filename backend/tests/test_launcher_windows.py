"""Windows 真进程验收：隐藏控制台发送真实 Ctrl+C，数据库和资料目录均隔离。"""

import ctypes
import os
import re
import socket
import subprocess
import sys
import time
from ctypes import wintypes
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from wiki_app.launcher import ROOT

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows 控制台与进程树专用验收")


def send_ctrl_c(process):
    """辅助进程只附着测试控制台，避免把 Ctrl+C 广播到 pytest 或用户的终端。"""
    code = f"""
import ctypes, time
from ctypes import wintypes
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.AttachConsole.argtypes = [wintypes.DWORD]
k.FreeConsole()
if not k.AttachConsole({process.pid}):
    raise ctypes.WinError(ctypes.get_last_error())
k.SetConsoleCtrlHandler(None, True)
if not k.GenerateConsoleCtrlEvent(0, 0):
    raise ctypes.WinError(ctypes.get_last_error())
time.sleep(0.2)
k.FreeConsole()
"""
    subprocess.run([sys.executable, "-c", code], creationflags=subprocess.CREATE_NO_WINDOW,
                   check=True, timeout=5)


def start_script(arguments, environment, logfile):
    """与用户同一 PowerShell 命令；测试控制台隐藏，不弹出交互窗口。"""
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts/start-backend.ps1"), *arguments],
        cwd=ROOT, env=environment, stdout=logfile, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
        startupinfo=startup,
    )


def wait_until(predicate, process, log_path, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(log_path.read_text(encoding="utf-8", errors="replace"))
        if predicate():
            return
        time.sleep(0.15)
    pytest.fail("等待启动/任务完成超时：\n" + log_path.read_text(encoding="utf-8", errors="replace"))


def wait_for_api(process, log_path, api_url):
    def ready():
        try:
            with httpx.Client(trust_env=False, timeout=1) as client:
                return client.get(api_url + "/healthz").status_code == 200
        except httpx.HTTPError:
            return False

    wait_until(ready, process, log_path)


def stop_script(process):
    if process.poll() is None:
        send_ctrl_c(process)
        process.wait(timeout=30)


def assert_exited(pid):
    """查询刚刚记录的测试 PID，不对它发送结束请求。"""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 3000) == 0, f"测试进程 {pid} 未退出"
        finally:
            kernel.CloseHandle(handle)


@pytest.fixture
def launcher_database(tmp_path, configure):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 TEST_DATABASE_URL 后执行真实 Windows 启动验收")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    # 独立数据库避免 search_path 回退到 public 的检查点或队列表，绝不消费业务库待办。
    admin_dsn = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    name = "wiki_launcher_test_" + uuid4().hex
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    parsed = make_url(url).set(database=name)
    path = configure(services={"api_port": port},
                     database={"db_host": parsed.host, "db_port": parsed.port or 5432,
                               "db_user": parsed.username, "db_name": parsed.database},
                     paths={"data_dir": str(tmp_path / "files"), "log_dir": str(tmp_path / "logs")},
                     models={"embeddings_enabled": False}, llm={"llm_model": ""})
    class PrivateEnvironment(dict):
        # pytest 在失败报告中展示 fixture repr，不能把继承的系统凭据一起输出。
        def __repr__(self):
            return "<isolated launcher environment; values hidden>"

    environment = PrivateEnvironment({**os.environ, "WIKI_CONFIG_FILE": str(path),
                   "POSTGRES_PASSWORD": parsed.password or "", "API_TOKEN": "", "LLM_API_KEY": "",
                   "TEST_DATABASE_URL": parsed.render_as_string(hide_password=False), "PYTHONUTF8": "1",
                   "WIKI_TEST_API_URL": f"http://127.0.0.1:{port}"})
    try:
        migration = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(ROOT / "scripts/start-backend.ps1"), "-Service", "migrate"],
            cwd=ROOT, env=environment, capture_output=True, encoding="utf-8", errors="replace", timeout=30,
        )
        assert migration.returncode == 0, migration.stderr
        yield environment
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.mark.parametrize("reload", [False, True])
def test_unified_start_queue_and_ctrl_c(launcher_database, tmp_path, reload):
    api_url = launcher_database["WIKI_TEST_API_URL"]
    log_path = tmp_path / "launcher.log"
    with log_path.open("wb") as logfile:
        process = start_script(["-Reload"] if reload else [], launcher_database, logfile)
        try:
            wait_for_api(process, log_path, api_url)
            with httpx.Client(trust_env=False, timeout=5) as client:
                record = client.post(api_url + "/api/records", json={
                    "content": "Launcher integration: this record must be read by the real queue worker."
                })
                assert record.status_code == 201
                url = api_url + "/api/records/" + record.json()["id"]
                wait_until(lambda: client.get(url).json()["status"] == "waiting_configuration",
                           process, log_path)
                assert client.get(url).json()["sources"]
            stop_script(process)
            assert process.returncode == 0
            output = log_path.read_text(encoding="utf-8", errors="replace")
            assert "[API]" in output and "[Worker]" in output
            assert "Worker 意外退出" not in output
            assert "Worker 已停止" in output
            # 正常停止应注销消费者，而不只是被 Job 强制回收。
            dsn = make_url(launcher_database["TEST_DATABASE_URL"]).set(drivername="postgresql").render_as_string(hide_password=False)
            with psycopg.connect(dsn) as connection:
                assert connection.execute("SELECT count(*) FROM procrastinate_workers").fetchone()[0] == 0
            if reload:
                assert "Started reloader process" in output
            for pid in re.findall(r"pid=(\d+)|Started (?:server|reloader) process \[(\d+)\]", output):
                assert_exited(int(pid[0] or pid[1]))
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", int(api_url.rsplit(":", 1)[1])))
        finally:
            # 失败清理仅针对本测试创建的 PowerShell 进程树。
            if process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                process.wait(timeout=5)


def test_legacy_api_worker_commands(launcher_database, tmp_path):
    for role in ("api", "worker"):
        log_path = tmp_path / f"{role}.log"
        with log_path.open("wb") as logfile:
            process = start_script(["-Service", role], launcher_database, logfile)
            try:
                if role == "api":
                    wait_for_api(process, log_path, launcher_database["WIKI_TEST_API_URL"])
                else:
                    wait_until(lambda: "Worker 启动" in log_path.read_text(encoding="utf-8", errors="replace"),
                               process, log_path)
                stop_script(process)
            finally:
                if process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                    process.wait(timeout=5)
