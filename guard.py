"""guard.py —— 危险动作前的护栏（借鉴两个参考软件的安全机制）。

单独成文件，不依赖 runner，scan.py / runner.py / 以后的分支流程都能用。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"

PKG = "com.jingcai.apps.qualitydev"

# 这台机器（Android 17 / HyperOS）上 uiautomator2 的 app_current() 会返回过期的包名，
# 所以前台判断走 dumpsys window，读不到才回退 app_current()。
FOCUS_RE = re.compile(r"mCurrentFocus=Window\{[^}]*?\s([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)\}")

# 只允许点这些（文字或 id 片段）；命中黑名单立刻拒绝
ALLOW = {
    "我的", "我的活动", "管理", "管理活动", "人员管理", "签到管理",
    "签到列表", "未签到", "已签到", "学分管理", "颁发", "补签", "录取",
    "全选", "筛选", "报名", "请假", "完结活动", "确认", "确定", "返回",
}
DENY = {"发布活动", "btn_createactivity", "退出", "删除", "解散"}


class GuardError(RuntimeError):
    pass


def foreground_info(d):
    """当前前台 {package, activity}；dumpsys 优先，读不到再回退 app_current()。"""
    try:
        out = d.shell("dumpsys window | grep -m1 mCurrentFocus")
        if isinstance(out, (tuple, list)):
            out = out[0]
        match = FOCUS_RE.search(str(out or ""))
        if match:
            return {"package": match.group(1), "activity": match.group(2)}
    except Exception:
        pass
    try:
        info = d.app_current() or {}
        return {"package": info.get("package", ""), "activity": info.get("activity", "")}
    except Exception:
        return {"package": "", "activity": ""}


def foreground(d, timeout=3.0):
    """当前前台包名；读不到返回空串。"""
    return foreground_info(d)["package"]


def ensure_foreground(d, pkg=PKG, tries=8):
    """连续读到前台包名不对就抛错，交给上层停流程。"""
    for _ in range(tries):
        if foreground(d) == pkg:
            return True
        time.sleep(0.25)
    raise GuardError(f"前台不是 {pkg}（当前 {foreground(d) or '读不到'}），已停止")


def check_target(text=None, id_=None):
    """点击目标白名单/黑名单检查。"""
    label = text or id_ or ""
    for bad in DENY:
        if bad in label:
            raise GuardError(f"拒绝点击黑名单目标：{label}")
    if text is not None and text not in ALLOW:
        raise GuardError(f"不在白名单里的文字：{text}")
    return True


def diagnostics(d, tag, extra=None):
    """失败现场：截图 + 页面 XML + 操作记录。返回目录。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = OUT / f"diag-{tag}-{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        (folder / "hierarchy.xml").write_text(d.dump_hierarchy(), encoding="utf-8")
    except Exception:
        pass
    try:
        d.screenshot(str(folder / "screen.png"))
    except Exception:
        pass
    (folder / "meta.json").write_text(
        json.dumps({"tag": tag, "time": stamp, "extra": extra or {}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  [诊断] {folder}")
    return folder


def load_pending():
    path = OUT / "pending.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def begin_submit(task, info=None):
    """提交前落一条“结果待确认”记录。"""
    OUT.mkdir(exist_ok=True)
    (OUT / "pending.json").write_text(
        json.dumps({"task": task, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "info": info or {}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def end_submit():
    path = OUT / "pending.json"
    if path.exists():
        path.unlink()


def warn_pending():
    """启动时检查上次是否有未确认的提交。"""
    info = load_pending()
    if not info:
        return None
    print(f"[WARN] 上次「{info.get('task')}」在 {info.get('time')} 提交，结果未确认。")
    print("       已禁止自动重试：先手动确认手机上的结果，再删除 out/pending.json。")
    return info