"""用户报告的键盘截图回归，仅含按键区，不含人员信息。"""
import os
from pathlib import Path
import unittest

from PIL import Image
from keyboard_search import find_search_key


@unittest.skipUnless(os.name == 'nt', '需要 Windows 自带简体中文 OCR')
class KeyboardPhotoTests(unittest.TestCase):
    def test_white_search_label_on_blue_key_at_phone_resolutions(self):
        with Image.open(Path(__file__).parent/'fixtures/keyboard_search_blue.png') as photo:
            for width in (720, 864, 1080, 1440, 2160):
                with self.subTest(width=width):
                    scale = width/photo.width
                    image = photo.resize((width, round(photo.height*scale)))
                    x, y = find_search_key(image, (0, 0, *image.size))
                    # 照片中的搜索键边界；坐标随分辨率等比换算。
                    self.assertTrue(733*scale < x < 853*scale)
                    self.assertTrue(393*scale < y < 503*scale)


if __name__ == '__main__':
    unittest.main()
