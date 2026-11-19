"""启动器边界测试：使用本测试创建的子进程，不占用真实业务队列。"""

import os
import signal
import socket
import subprocess
import sys
from unittest.mock import MagicMock

import psycopg
import pytest

from wiki_app import launcher


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture
def checked_environment(monkeypatch, tmp_path):
    """环境检查单测不依赖开发机 .venv；CI 使用全局 Python 时也能验证后续分支。"""
    executable = tmp_path / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(launcher, "ROOT", tmp_path)


def test_missing_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="setup-local"):
        launcher.check_environment()


def test_port_in_use_does_not_stop_existing_service(monkeypatch, checked_environment):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        monkeypatch.setattr(launcher, "PORT", listener.getsockname()[1])
        with pytest.raises(RuntimeError, match="端口"):
            launcher.check_environment()
        assert listener.fileno() >= 0


def test_database_failure_does_not_leak_password(environment, monkeypatch, checked_environment):
    monkeypatch.setattr(launcher, "PORT", 0)
    monkeypatch.setattr(psycopg, "connect", MagicMock(side_effect=psycopg.OperationalError("secret-password")))
    with pytest.raises(RuntimeError, match="数据库连接失败") as error:
        launcher.check_environment()
    assert "secret-password" not in str(error.value)


def test_missing_tables_only_suggests_migration(environment, monkeypatch, checked_environment):
    monkeypatch.setattr(launcher, "PORT", 0)
    connection = MagicMock()
    cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (None,)
    monkeypatch.setattr(psycopg, "connect", MagicMock(return_value=connection))
    with pytest.raises(RuntimeError, match="-Service migrate"):
        launcher.check_environment()
    assert all(call.args[0] == "SELECT to_regclass(%s)" for call in cursor.execute.call_args_list)


def test_api_failure_never_starts_worker(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(launcher, "PORT", free_port())
    marker = tmp_path / "worker-started"
    worker_code = f"from pathlib import Path; Path({str(marker)!r}).touch()"
    code = launcher.supervise(
        [sys.executable, "-c", "raise SystemExit(7)"],
        [sys.executable, "-c", worker_code], startup_timeout=3, stop_timeout=0.1,
    )
    assert code == 1 and not marker.exists()
    assert "API 意外退出" in capsys.readouterr().out


def test_health_timeout_cleans_child(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "PORT", free_port())
    command = [sys.executable, "-c", "import time; time.sleep(120)"]
    assert launcher.supervise(command, command, startup_timeout=0.2, stop_timeout=0.1) == 1
    assert "未就绪" in capsys.readouterr().out


@pytest.mark.parametrize("failing_role", ["API", "Worker"])
def test_failure_cleans_tree_but_keeps_unrelated_process(monkeypatch, capsys, failing_role):
    port = free_port()
    monkeypatch.setattr(launcher, "PORT", port)
    # 孙进程忽略正常停止信号，验证 Job/进程组的兜底清理，而非恰好收到广播退出。
    child_code = (
        "import signal,time; "
        "signal.signal(getattr(signal, 'SIGBREAK', signal.SIGTERM), signal.SIG_IGN); "
        "time.sleep(120)"
    )
    api_code = f"""
import json, os, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
p = subprocess.Popen([sys.executable, '-c', {child_code!r}])
print('GRANDCHILD_PID=' + str(p.pid), flush=True)
if {failing_role == 'API'!r}:
    threading.Timer(1, lambda: os._exit(8)).start()
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({{'status': 'ok'}}).encode())
HTTPServer(('127.0.0.1', {port}), Handler).serve_forever()
"""
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        assert launcher.supervise(
            [sys.executable, "-u", "-c", api_code],
            [sys.executable, "-c", "import time; time.sleep(1); raise SystemExit(9)"
             if failing_role == "Worker" else "import time; time.sleep(120)"],
            startup_timeout=5, stop_timeout=0.1,
        ) == 1
        output = capsys.readouterr().out
        assert f"{failing_role} 意外退出" in output
        assert unrelated.poll() is None
        with socket.socket() as listener:
            if os.name != "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            line = next(line for line in output.splitlines() if "GRANDCHILD_PID=" in line)
            pid = int(line.split("GRANDCHILD_PID=")[1])
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x100000, False, pid)
            if handle:
                try:
                    assert kernel.WaitForSingleObject(handle, 3000) == 0
                finally:
                    kernel.CloseHandle(handle)
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_signal_handlers_are_restored(monkeypatch):
    monkeypatch.setattr(launcher, "PORT", free_port())
    previous = signal.getsignal(signal.SIGINT)
    command = [sys.executable, "-c", "raise SystemExit(1)"]
    assert launcher.supervise(command, command, startup_timeout=3, stop_timeout=0.1) == 1
    assert signal.getsignal(signal.SIGINT) == previous
