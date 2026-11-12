"""打包/运行时引导：让 exe 找到自带的 adb 与依赖。

开发时：adb 在 ../tools/platform-tools；打包后：在 _internal/platform-tools。
"""

import os
import sys
from pathlib import Path


def adb_dir() -> Path | None:
    here = Path(__file__).resolve().parent
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", here))
        for cand in (base / "platform-tools", base.parent / "platform-tools"):
            if (cand / "adb.exe").exists():
                return cand
    for cand in (here.parent / "tools" / "platform-tools", here / "platform-tools"):
        if (cand / "adb.exe").exists():
            return cand
    return None


def setup() -> None:
    d = adb_dir()
    if d:
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    # 开发态下依赖装在 tools/libs
    libs = Path(__file__).resolve().parent.parent / "tools" / "libs"
    if not getattr(sys, "frozen", False) and libs.exists():
        sys.path.insert(0, str(libs))
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")