"""提交失败时如实保存结果；只使用假设备，不连接或提交手机。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import device_checks


class SubmissionHistoryTests(unittest.TestCase):
    def submit_with_dialog(self, tmp, message, fail_confirmation=False):
        footer = '<hierarchy><node resource-id="app:id/tv_next" text="补签2人" bounds="[0,400][200,450]"/></hierarchy>'
        dialog = ('<hierarchy><node resource-id="android:id/message" text="' + message + '"/>'
                  '<node resource-id="android:id/button1" text="确定" bounds="[100,200][200,250]"/></hierarchy>')
        device = Mock()
        device.dump_hierarchy.side_effect = [footer, footer, footer] + [dialog] * 4
        if fail_confirmation:
            device.click.side_effect = [None, RuntimeError("连接中断")]
        with patch("guard.ensure_foreground"), patch.object(device_checks.time, "sleep"):
            with self.assertRaises(RuntimeError):
                device_checks.submit_selected_makeup(device, "目标活动", tmp, timeout=1, interval=0)
        entries = list((Path(tmp) / "提交记录").glob("*.json"))
        self.assertEqual(len(entries), 1)
        return device, json.loads(entries[0].read_text(encoding="utf8"))

    def test_count_mismatch_before_confirmation_is_recorded_unsent(self):
        with tempfile.TemporaryDirectory() as tmp:
            device, entry = self.submit_with_dialog(tmp, "确定为选中3人补签？")
            self.assertEqual(device.click.call_count, 1)
            self.assertEqual(entry["state"], "未提交")
            self.assertIs(entry["submission_attempted"], False)

    def test_confirmation_click_failure_remains_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            device, entry = self.submit_with_dialog(tmp, "确定为选中2人补签？", fail_confirmation=True)
            self.assertEqual(device.click.call_count, 2)
            self.assertEqual(entry["state"], "提交结果待核对")
            self.assertIs(entry["submission_attempted"], True)

if __name__ == "__main__":
    unittest.main()
