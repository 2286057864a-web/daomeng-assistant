"""同一窗口连续任务：任务锁、连接生命周期及旧页面恢复。"""
import contextlib
import io
import queue
import unittest
from unittest.mock import Mock, patch, call

import gui
import scan


class ImmediateThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


class ContinuousTasks(unittest.TestCase):
    def app(self):
        app = gui.App.__new__(gui.App)
        app.running = False
        app.device = object()
        app.root = Mock()
        app.lbl_dev = Mock()
        app.write = Mock()
        app._q = queue.Queue()
        return app

    def drain(self, app):
        while not app._q.empty():
            fn, args = app._q.get_nowait()
            fn(*args)

    def test_success_failure_and_next_task_reuse_one_app(self):
        app = self.app()
        events = []
        def failed():
            raise RuntimeError("模拟任务失败")
        with patch.object(gui.threading, "Thread", ImmediateThread), patch.object(gui.errcatch, "record"):
            for job in (lambda: events.append("first"), failed, lambda: events.append("next")):
                app.run(job)
                self.drain(app)
                self.assertFalse(app.running)
        self.assertEqual(events, ["first", "next"])

    def test_each_task_reconnects_without_erasing_pending_makeup_or_report(self):
        app = self.app()
        app._grade_work_path = "勾选待提交.json"
        app._work_report_path = "上一项工作.json"
        devices = [object(), object()]
        import uiautomator2
        with patch.object(gui.threading, "Thread", ImmediateThread), \
             patch.object(uiautomator2, "connect", side_effect=devices) as connect:
            seen = []
            for _ in range(2):
                app.run(lambda: seen.extend([app.connect(), app.connect()]))
                self.drain(app)
        self.assertEqual(seen, [devices[0], devices[0], devices[1], devices[1]])
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(app._grade_work_path, "勾选待提交.json")
        self.assertEqual(app._work_report_path, "上一项工作.json")

    def test_completion_unlocks_even_if_log_widget_fails(self):
        app = self.app()
        app.running = True
        app.write.side_effect = RuntimeError("模拟日志控件失败")
        with self.assertRaises(RuntimeError):
            app._done("")
        self.assertFalse(app.running)

    def test_start_failure_unlocks_for_retry(self):
        for stage in ("log", "thread"):
            with self.subTest(stage=stage):
                app = self.app()
                if stage == "log":
                    app.write.side_effect = RuntimeError("模拟日志失败")
                with patch.object(gui.threading, "Thread") as thread:
                    thread.return_value.start.side_effect = RuntimeError("模拟线程启动失败")
                    with self.assertRaises(RuntimeError):
                        app.run(Mock())
                self.assertFalse(app.running)

    def test_device_check_uses_task_mutex_and_does_not_replace_running_device(self):
        app = self.app()
        app.running = True
        device = app.device
        with patch.object(gui.threading, "Thread") as thread:
            app._check_device()
        thread.assert_not_called()
        self.assertIs(app.device, device)
        app.running = False
        app.run = Mock()
        app._check_device()
        app.run.assert_called_once()

    def test_ui_queue_yields_with_logs_still_pending(self):
        app = self.app()
        calls = []
        for i in range(300):
            app._post(calls.append, i)
        app._drain_ui()
        self.assertGreater(len(calls), 0)
        self.assertLess(len(calls), 300)
        app.root.after.assert_called_once()
        while not app._q.empty():
            app._drain_ui()
        self.assertEqual(calls, list(range(300)))

    def test_failed_ui_log_does_not_feed_back_into_redirected_stdout(self):
        app = self.app()
        app.running = True
        app._post(Mock(side_effect=RuntimeError("模拟界面回调失败")))
        app._post(app._done, "")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(gui.errcatch, "record") as record:
            app._drain_ui()
        self.assertEqual(output.getvalue(), "")
        record.assert_called_once()
        self.assertFalse(app.running)

    def test_double_click_never_starts_second_worker(self):
        app = self.app()
        with patch.object(gui.threading, "Thread") as thread:
            app.run(Mock())
            app.run(Mock())
        thread.assert_called_once()
        self.assertTrue(app.running)


LIST = '<hierarchy><node resource-id="app:id/text_searchcontent"/>' \
       '<node resource-id="app:id/radio_activity" checked="true"/></hierarchy>'
PEOPLE = '<hierarchy><node resource-id="app:id/back"/>' \
         '<node resource-id="app:id/recycleview"/></hierarchy>'
DETAIL = '<hierarchy><node resource-id="app:id/back"/>' \
         '<node resource-id="app:id/title_toolbar" text="上一项活动"/></hierarchy>'


class NavigationBetweenTasks(unittest.TestCase):
    def navigate(self, pages):
        device = Mock()
        state = {"index": 0}
        device.dump_hierarchy.side_effect = lambda: pages[state["index"]]
        device.return_value.click.side_effect = lambda: state.update(index=state["index"]+1)
        with patch.object(scan, "wake"), patch.object(scan.guard, "foreground", return_value=scan.PKG), \
             patch.object(scan.guard, "ensure_foreground"), patch.object(scan.time, "sleep"):
            scan.navigate(device)
        device.app_start.assert_not_called()
        device.app_stop.assert_not_called()
        device.swipe.assert_not_called()
        return device

    def test_next_task_returns_from_people_and_detail_pages(self):
        device = self.navigate([PEOPLE, DETAIL, LIST])
        self.assertEqual(device.call_args_list, [call(resourceId="app:id/back")]*2)

    def test_old_business_confirmation_is_cancelled_never_submitted(self):
        for message in ("确定录取?", "确定补签选中的 2 人？", "确定颁发学分？"):
            with self.subTest(message=message):
                dialog = '<hierarchy><node resource-id="android:id/message" text="' + message + '"/>' \
                    '<node resource-id="android:id/button2" text="取消"/>' \
                    '<node resource-id="android:id/button1" text="确定"/></hierarchy>'
                device = self.navigate([dialog, PEOPLE, DETAIL, LIST])
                self.assertEqual(device.call_args_list[0], call(resourceId="android:id/button2"))
                self.assertNotIn(call(resourceId="android:id/button1"), device.call_args_list)

    def test_management_page_uses_one_snapshot(self):
        device = self.navigate([LIST])
        device.dump_hierarchy.assert_called_once()

    def test_unknown_dialog_with_underlying_list_is_not_clicked_through(self):
        page = LIST.replace('</hierarchy>', '<node resource-id="android:id/message" text="未知操作"/>'
                            '<node resource-id="android:id/button1" text="确定"/></hierarchy>')
        device = Mock()
        device.dump_hierarchy.return_value = page
        with patch.object(scan, "wake"), patch.object(scan.guard, "foreground", return_value=scan.PKG), \
             patch.object(scan.guard, "ensure_foreground"), self.assertRaisesRegex(scan.guard.GuardError, "弹窗"):
            scan.navigate(device)
        device.return_value.click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
