import unittest
from unittest.mock import Mock, patch

import roster_device as rd


class AdmitSubmitTests(unittest.TestCase):
    def setUp(self):
        self.phone = Mock()
        self.backend = rd.DeviceBackend(self.phone)
        self.backend.task = "admit"
        self.backend.count = Mock(return_value=1)
        self.backend._footer = Mock(return_value={"text": "录取 1 人", "cx": 40, "cy": 80})
        self.backend._confirm_dialog = Mock()
        for target in ("roster_device.guard.begin_submit", "roster_device.guard.end_submit", "roster_device.time.sleep"):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_submit_stays_on_signup_page(self):
        receipt = self.backend.submit(students=[{"name": "张甲"}])
        self.phone.click.assert_called_once_with(40, 80)
        self.backend._confirm_dialog.assert_called_once_with()
        self.phone.assert_not_called()  # No tab lookup after confirmation.
        self.assertTrue(receipt.submitted)
        self.assertEqual(receipt.identities, ())

    def test_missing_confirmation_propagates_without_retry(self):
        self.backend._confirm_dialog.side_effect = RuntimeError("没有出现确认弹窗")
        with self.assertRaisesRegex(RuntimeError, "没有出现确认弹窗"):
            self.backend.submit(students=[{"name": "张甲"}])
        self.phone.click.assert_called_once_with(40, 80)
        self.phone.assert_not_called()
