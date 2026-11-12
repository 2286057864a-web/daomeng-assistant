"""提交前持久化、保守拦截重复任务；不依赖手机或 GUI。"""
import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def check_previous(out_dir, task, title, score, identities):
    """同活动同任务同分值，有重叠身份则停止；旧年级任务缺身份时按活动拦截。"""
    def key(identity):
        name, sid = identity
        return ("sid", sid) if sid else ("name", name)

    wanted = set(map(key, identities)) if identities is not None else None
    for path in sorted((Path(out_dir) / "提交记录").glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if entry["version"] != 1:
                raise ValueError("不支持的记录版本")
            if (entry["task"], entry["title"], entry["score"]) != (task, title, score):
                continue
            previous = entry["identities"]
            overlap = wanted is None or previous is None or wanted.intersection(map(key, previous))
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"提交记录损坏，不能确认是否重复提交：{path}") from exc
        if overlap:
            raise RuntimeError(f"发现同活动人员的历史提交记录，已阻止再次提交：{path}。请先人工核对手机；未确认的记录不要删除。")


class SubmissionLog:
    def __init__(self, out_dir, task, title, score, identities):
        self.path = Path(out_dir).resolve() / "提交记录" / (uuid4().hex + ".json")
        self.data = {"version": 1, "task": task, "title": title, "score": score,
                     "identities": identities, "state": "提交结果待核对",
                     "note": "已保存提交意图；可能即将或已经点击，禁止自动重试。",
                     "time": datetime.now().isoformat(timespec="seconds")}

    def save(self, state=None, note=None):
        if state is not None:
            self.data["state"] = state
        if note is not None:
            self.data["note"] = note
        atomic_json(self.path, self.data)

