"""用 Windows 自带 OCR 在当前键盘区域内定位搜索键；不切换输入法。"""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from PIL import ImageOps


_OCR_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and
    $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await($operation, $resultType) {
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.GetAwaiter().GetResult()
}
$language = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages |
    Where-Object { $_.LanguageTag -like 'zh-Hans*' } | Select-Object -First 1
if (!$language) { throw 'Chinese OCR language is unavailable' }
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
$file = [System.IO.File]::OpenRead($env:DMK_OCR_IMAGE)
$stream = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($file)
try {
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    try {
        $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $lines = @($result.Lines | ForEach-Object {
            $words = @($_.Words | ForEach-Object {
                $r = $_.BoundingRect
                @{text=$_.Text; x=$r.X; y=$r.Y; width=$r.Width; height=$r.Height}
            })
            @{text=$_.Text; words=$words}
        })
        ConvertTo-Json -InputObject $lines -Depth 5 -Compress
    } finally { if ($bitmap) { $bitmap.Dispose() } }
} finally { if ($stream) { $stream.Dispose() }; $file.Dispose() }
'''


def keyboard_region(xml, package):
    """只取系统标出的键盘输入区，排除应用内容和候选词栏。"""
    boxes = set()
    for node in ET.fromstring(xml).iter('node'):
        if (node.get('package') != package or node.get('resource-id') != 'android:id/inputArea'
                or node.get('visible-to-user', 'true') != 'true'):
            continue
        b = tuple(int(x) for x in re.findall(r'-?\d+', node.get('bounds', '')))
        if len(b) == 4 and 0 <= b[0] < b[2] and 0 <= b[1] < b[3]:
            boxes.add(b)
    return next(iter(boxes)) if len(boxes) == 1 else None


def _ocr_lines(image):
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    if not powershell.is_file():
        raise RuntimeError('无法使用 Windows 文字识别定位搜索键；输入法保持不变。')
    with tempfile.TemporaryDirectory(prefix='dmk-keyboard-') as folder:
        path = Path(folder)/'keyboard.png'
        image.save(path)
        env = dict(os.environ, DMK_OCR_IMAGE=str(path))
        try:
            result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-EncodedCommand',
                base64.b64encode(_OCR_SCRIPT.encode('utf-16le')).decode('ascii')],
                capture_output=True, timeout=15, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('识别键盘搜索键超时，未点击、未切换输入法。') from exc
        if result.returncode:
            raise RuntimeError('Windows 文字识别不可用，请检查简体中文 OCR 组件；未切换输入法。')
        try:
            return json.loads(result.stdout.decode('utf-8-sig'))
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError('键盘文字识别返回异常，未点击、未切换输入法。') from exc


def find_search_key(image, region):
    """返回截图中“搜索”文字中心的屏幕坐标；不匹配发送、确认或其它按键。"""
    left, top, right, bottom = region
    if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
        raise RuntimeError('键盘边界与截图不一致，未点击搜索。')
    crop = image.crop(region).convert('RGB')
    scale = min(1.0, 2000/max(crop.size))
    if scale < 1:
        crop = crop.resize((round(crop.width*scale), round(crop.height*scale)))
    hits = set()
    for high_contrast in (False, True):
        # 彩色键帽上的白字可能被 OCR 漏掉；只在原图无命中时增强对比度再识别。
        ocr_image = (ImageOps.grayscale(crop).point(lambda v: 255 if v > 220 else 0).convert('RGB')
                     if high_contrast else crop)
        for line in _ocr_lines(ocr_image):
            # OCR 可能把整排按键合成一行，因此同时检查单词与整行。
            words = line['words']
            groups = [[word] for word in words] + [words[i:i+2] for i in range(len(words)-1)] + [words]
            for group in groups:
                label = ''.join(word['text'] for word in group).replace(' ', '').casefold()
                if label not in ('搜索', 'search') or not group:
                    continue
                x1 = min(w['x'] for w in group)
                y1 = min(w['y'] for w in group)
                x2 = max(w['x']+w['width'] for w in group)
                y2 = max(w['y']+w['height'] for w in group)
                if 0 <= x1 < x2 <= crop.width and 0 <= y1 < y2 <= crop.height:
                    hits.add((round(left+(x1+x2)/2/scale), round(top+(y1+y2)/2/scale)))
        if hits:
            break
    if len(hits) != 1:
        raise RuntimeError('未能在当前键盘中唯一识别“搜索”键，未点击、未切换输入法。')
    return next(iter(hits))
