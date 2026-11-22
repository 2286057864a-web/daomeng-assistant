import unittest
from unittest.mock import patch

from PIL import Image
import keyboard_search as ks


def line(text, x=100, y=50):
    return {'text': text, 'words': [dict(text=text, x=x, y=y, width=80, height=30)]}


class KeyboardOcrTests(unittest.TestCase):
    def test_search_position_is_relative_to_keyboard_region(self):
        image = Image.new('RGB', (800, 1200))
        with patch.object(ks, '_ocr_lines', return_value=[line('搜索')]) as ocr:
            self.assertEqual(ks.find_search_key(image, (200,800,800,1200)), (340,865))
        self.assertEqual(ocr.call_args.args[0].size, (600,400))

    def test_split_chinese_words_in_same_key_are_joined(self):
        words = [dict(text=text, x=x, y=50, width=30, height=30)
                 for text,x in [('中',10),('搜',100),('索',140)]]
        with patch.object(ks, '_ocr_lines', return_value=[{'text':'中 搜 索', 'words':words}]):
            self.assertEqual(ks.find_search_key(Image.new('RGB',(800,1200)), (0,800,800,1200)), (135,865))

    def test_large_image_coordinates_are_scaled_back(self):
        with patch.object(ks, '_ocr_lines', return_value=[line('Search')]):
            self.assertEqual(ks.find_search_key(Image.new('RGB',(4000,3000)), (0,1000,4000,3000)), (280,1130))

    def test_send_confirm_and_duplicate_search_are_rejected(self):
        for words in ([line('发送')], [line('确认')], [line('搜索'),line('搜索',300,200)], []):
            with self.subTest(words=words), patch.object(ks,'_ocr_lines',return_value=words), \
                 self.assertRaisesRegex(RuntimeError,'唯一识别'):
                ks.find_search_key(Image.new('RGB',(800,1200)), (0,800,800,1200))

    def test_out_of_bounds_ocr_result_is_not_clicked(self):
        with patch.object(ks,'_ocr_lines',return_value=[line('搜索',900,100)]), \
             self.assertRaisesRegex(RuntimeError,'唯一识别'):
            ks.find_search_key(Image.new('RGB',(800,1200)), (0,800,800,1200))

    def test_app_search_and_keyboard_candidates_are_excluded(self):
        xml = '<hierarchy><node package="app" resource-id="android:id/inputArea" bounds="[0,0][800,800]"/>' \
              '<node package="keyboard" resource-id="android:id/candidatesArea" bounds="[0,800][800,850]"/>' \
              '<node package="keyboard" resource-id="android:id/inputArea" bounds="[0,850][800,1200]"/></hierarchy>'
        self.assertEqual(ks.keyboard_region(xml,'keyboard'), (0,850,800,1200))

    def test_bad_region_is_rejected_before_ocr(self):
        with patch.object(ks,'_ocr_lines') as ocr, self.assertRaisesRegex(RuntimeError,'边界'):
            ks.find_search_key(Image.new('RGB',(800,1200)), (0,800,900,1200))
        ocr.assert_not_called()

    def test_contrast_retry_keeps_screen_coordinates(self):
        with patch.object(ks, '_ocr_lines', side_effect=[[], [line('搜索')]]):
            self.assertEqual(ks.find_search_key(Image.new('RGB', (800,1200)),
                                                (200,800,800,1200)), (340,865))

    def test_duplicate_search_is_not_resolved_by_another_pass(self):
        with patch.object(ks, '_ocr_lines', side_effect=[
                [line('搜索'), line('搜索',300,200)], [line('搜索')]]) as ocr, \
                self.assertRaisesRegex(RuntimeError, '唯一识别'):
            ks.find_search_key(Image.new('RGB',(800,1200)), (0,800,800,1200))
        self.assertEqual(ocr.call_count, 1)

    def test_duplicate_search_in_contrast_pass_is_also_rejected(self):
        with patch.object(ks, '_ocr_lines', side_effect=[
                [], [line('搜索'), line('搜索',300,200)]]), \
                self.assertRaisesRegex(RuntimeError, '唯一识别'):
            ks.find_search_key(Image.new('RGB',(800,1200)), (0,800,800,1200))


if __name__ == '__main__':
    unittest.main()
