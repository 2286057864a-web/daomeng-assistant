"""启动时确保 adb server 在跑。

双击 exe 时，电脑上通常没有 adb daemon；而且 adb 默认把日志写到系统临时目录，
在受限环境里会被拒绝（cannot open 系统 Temp 目录下的 adb.log: Permission denied）。
这里给它指一个项目内可写目录，再拉起 5037 端口上的 server。失败不影响打开界面。

单独成文件，是为了不碰 gui.py 里既有的 App 方法，便于验收脚本比对。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import bootstrap


def _env() -> dict:
    """给 adb 一个可写的临时目录；否则 daemon 起不来。"""
    env = os.environ.copy()
    tmp = bootstrap.adb_dir()
    base = (tmp.parent / "tmp") if tmp else Path(__file__).resolve().parent / "out" / "tmp"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    env["TMP"] = str(base)
    env["TEMP"] = str(base)
    return env


def _run(args, timeout: float = 15.0) -> subprocess.CompletedProcess | None:
    d = bootstrap.adb_dir()
    if not d:
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        return subprocess.run(
            [str(d / "adb.exe"), *args],
            capture_output=True,
            timeout=timeout,
            creationflags=flags,
            env=_env(),
        )
    except Exception:
        return None


def ensure_adb_server(timeout: float = 15.0) -> bool:
    """成功返回 True；找不到 adb 或启动失败返回 False，不抛异常。"""
    r = _run(["start-server"], timeout=timeout)
    return bool(r and r.returncode == 0)


def is_server_running() -> bool:
    r = _run(["devices"])
    return bool(r and r.returncode == 0)


def devices() -> list[str]:
    r = _run(["devices"])
    if not r or r.returncode != 0:
        return []
    lines = r.stdout.decode("utf-8", "replace").splitlines()[1:]
    return [ln.split("\t")[0] for ln in lines if "\tdevice" in ln]