"""保存每次任务的提交意图和结果；不依赖手机或 GUI。"""
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

