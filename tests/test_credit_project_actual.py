"""真实页面结构：带空格的底部按钮、只改变颜色的勾选图标。"""
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from PIL import Image

import credit

FIXTURES = Path(__file__).resolve().parent / 'fixtures/credit_projects'


class ActualProjectTests(unittest.TestCase):
    def device(self, selected=False):
        device = Mock()
        device.dump_hierarchy.return_value = (FIXTURES/'projects.xml').read_text(encoding='utf8')
        image = Image.new('RGB', (1440, 3200), 'white')
        for box, active in (((516,505,638,566), selected), ((1207,505,1329,566), False)):
            with Image.open(FIXTURES/('selected-marker.png' if active else 'unselected-marker.png')) as marker:
                image.paste(marker, box)
        device.screenshot.return_value = image
        return device

    def test_real_spaced_footer_and_gray_markers(self):
        items = credit.projects(self.device())
        self.assertEqual([p['btn'] for p in items], [(1081,3013)]*2)
        self.assertEqual([p['selected'] for p in items], [False,False])

    def test_real_purple_marker_is_selected_despite_xml_false(self):
        items = credit.projects(self.device(True))
        self.assertEqual([p['selected'] for p in items], [True,False])

    def test_already_selected_real_project_only_enters_people(self):
        device = self.device(True)
        with patch.object(credit.time,'sleep'):
            credit.open_project(device,credit.projects(device)[0])
        device.click.assert_called_once_with(1081,3013)

    def test_unknown_marker_color_or_failed_capture_stops_before_click(self):
        for failed in (False,True):
            device=self.device()
            if failed:
                device.screenshot.side_effect=RuntimeError('截图失败')
            else:
                device.screenshot.return_value=Image.new('RGB',(1440,3200),'white')
            with self.assertRaisesRegex(RuntimeError,'选中状态'):
                credit.open_project(device,credit.projects(device)[0])
            device.click.assert_not_called()

    def test_real_markers_drive_card_toggle_before_spaced_footer(self):
        device=self.device()
        def click(x,y):
            if (x,y)==(374,663):
                device.screenshot.return_value=self.device(True).screenshot.return_value
        device.click.side_effect=click
        with patch.object(credit.time,'sleep'):
            credit.open_project(device,credit.projects(device)[0])
        self.assertEqual([call.args for call in device.click.call_args_list],[(374,663),(1081,3013)])


if __name__=='__main__':
    unittest.main()
