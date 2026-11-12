"""全局异常记录 + 一键诊断包。

目标：出错时不用用户复述——异常自己落盘，用户点一下就能把现场打包发出去。

- 未捕获异常（主线程 / 子线程）写进 out/错误记录.log，附设备、页面 XML、手机截图、软件截图
- 卡住（超过设定秒数）也会留一次现场
- export_package() 把日志、现场、提交记录、最近诊断目录打成一个 zip

注意：本文件拼接多行文本一律用 NL = chr(10)，不写反斜杠转义。
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
import zipfile
from contextlib import contextmanager
from pathlib import Path

NL = chr(10)

_WINDOW = None


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = _app_root()
OUT = ROOT / "out"
ERRLOG = OUT / "错误记录.log"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def register_window(root) -> None:
    """登记主窗口，出错时连软件界面一起截下来。"""
    global _WINDOW
    _WINDOW = root


def _device_line() -> str:
    try:
        import adb_boot

        devs = adb_boot.devices()
        return "设备: " + (", ".join(devs) if devs else "未连接")
    except Exception as exc:  # noqa: BLE001
        return "设备: 读取失败（" + type(exc).__name__ + "）"


def _page_xml(device=None) -> str:
    try:
        import uiautomator2 as u2

        return (device or u2.connect()).dump_hierarchy() or ""
    except Exception:
        return ""


def _app_png(dest: Path) -> bool:
    """截主窗口那块屏幕区域；窗口最小化或被遮住时可能不准。"""
    if _WINDOW is None or threading.current_thread() is not threading.main_thread():
        return False
    try:
        from PIL import ImageGrab

        root = _WINDOW
        x, y = root.winfo_rootx(), root.winfo_rooty()
        w, h = root.winfo_width(), root.winfo_height()
        if w < 50 or h < 50:
            return False
        ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(dest)
        return dest.exists()
    except Exception:
        return False


def _phone_png(dest: Path, device=None) -> bool:
    """截手机当前画面：先用 uiautomator2，失败再直接调 adb。"""
    try:
        if device is None:
            import uiautomator2 as u2

            device = u2.connect()
        device.screenshot(str(dest))
        if dest.exists() and dest.stat().st_size > 1000:
            return True
    except Exception:
        pass
    try:
        import subprocess

        import adb_boot

        d = adb_boot.bootstrap.adb_dir()
        if not d:
            return False
        out = subprocess.run(
            [str(d / "adb.exe"), "exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=20,
        )
        if out.returncode == 0 and len(out.stdout) > 1000:
            dest.write_bytes(out.stdout)
            return True
    except Exception:
        pass
    return False


def capture(tag: str, device=None) -> dict:
    """错误现场：页面 XML + 手机截图 + 软件截图。返回写出的文件路径（可能不全）。"""
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    got = {"xml": "", "phone": "", "app": ""}
    xml = _page_xml(device)
    if xml:
        path = OUT / ("错误现场-" + stamp + "-" + tag + ".xml")
        try:
            path.write_text(xml, encoding="utf-8")
            got["xml"] = str(path)
        except Exception:
            pass
    phone = OUT / ("错误现场-" + stamp + "-" + tag + "-手机.png")
    if _phone_png(phone, device):
        got["phone"] = str(phone)
    app = OUT / ("错误现场-" + stamp + "-" + tag + "-软件.png")
    if _app_png(app):
        got["app"] = str(app)
    return got


def record(exc_type, exc, tb, *, where: str = "主线程", snapshot: bool = True) -> Path:
    """把一次异常写进日志；返回日志路径。本身绝不抛异常。"""
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        lines = [
            NL + "===== " + _now() + " · " + where + " · "
            + getattr(exc_type, "__name__", str(exc_type)) + ": " + str(exc) + " =====",
            _device_line(),
        ]
        if snapshot:
            got = capture("异常")
            for key, label in (("xml", "页面"), ("phone", "手机截图"), ("app", "软件截图")):
                if got.get(key):
                    lines.append("现场" + label + ": " + got[key])
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        lines.append(text.rstrip())
        with ERRLOG.open("a", encoding="utf-8") as fh:
            fh.write(NL.join(lines) + NL)
    except Exception:
        pass
    return ERRLOG


@contextmanager
def stall(seconds: float, tag: str, device=None):
    """超过 seconds 还没走完，就自动留一份现场（只记录，不抛异常）。"""
    done = {"v": False}

    def _fire():
        if done["v"]:
            return
        try:
            OUT.mkdir(parents=True, exist_ok=True)
            got = capture(tag + "-卡住", device)
            lines = [
                NL + "===== " + _now() + " · 疑似卡住（超过 " + str(int(seconds))
                + " 秒）· " + tag + " =====",
                _device_line(),
            ]
            for key, label in (("xml", "页面"), ("phone", "手机截图"), ("app", "软件截图")):
                if got.get(key):
                    lines.append("现场" + label + ": " + got[key])
            with ERRLOG.open("a", encoding="utf-8") as fh:
                fh.write(NL.join(lines) + NL)
        except Exception:
            pass

    timer = threading.Timer(seconds, _fire)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        done["v"] = True
        timer.cancel()


def install() -> None:
    """挂上主线程与子线程的异常钩子。"""

    def _main_hook(exc_type, exc, tb):
        record(exc_type, exc, tb, where="主线程")

    def _thread_hook(args):
        name = args.thread.name if getattr(args, "thread", None) else "?"
        record(args.exc_type, args.exc_value, args.exc_traceback,
               where="线程 " + str(name), snapshot=False)

    sys.excepthook = _main_hook
    threading.excepthook = _thread_hook


def _env_info() -> str:
    lines = [
        "时间: " + _now(),
        "程序目录: " + str(ROOT),
        "打包运行: " + str(bool(getattr(sys, "frozen", False))),
        "系统: " + sys.platform,
        "Python: " + sys.version.split()[0],
        _device_line(),
    ]
    for mod in ("uiautomator2", "openpyxl", "yaml", "lxml"):
        try:
            m = __import__(mod)
            lines.append(mod + ": " + str(getattr(m, "__version__", "已导入")))
        except Exception as exc:  # noqa: BLE001
            lines.append(mod + ": 导入失败（" + type(exc).__name__ + "）")
    return NL.join(lines) + NL


def export_package(dest: Path | None = None) -> Path:
    """把日志、现场、提交记录、最近一次诊断目录打成一个 zip。"""
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    target = Path(dest) if dest else ROOT / ("诊断包-" + stamp + ".zip")

    picks: list[Path] = []
    for pat in ("*.log", "错误现场-*.xml", "错误现场-*.png", "pending.json"):
        picks += sorted(OUT.glob(pat))

    records = OUT / "提交记录"
    if records.exists():
        picks += sorted(records.glob("*.json"))

    diags = sorted(OUT.glob("diag-*"))
    if diags:
        picks += [p for p in diags[-1].rglob("*") if p.is_file()]

    env = OUT / "环境信息.txt"
    env.write_text(_env_info(), encoding="utf-8")
    picks.append(env)

    gui_log = OUT / "gui.log"
    if gui_log.exists():
        tail = gui_log.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
        recent = OUT / "gui-最近200行.log"
        recent.write_text(NL.join(tail) + NL, encoding="utf-8")
        picks.append(recent)

    picks = list(dict.fromkeys(picks))
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in picks:
            try:
                zf.write(p, p.relative_to(ROOT))
            except Exception:
                pass
    return target