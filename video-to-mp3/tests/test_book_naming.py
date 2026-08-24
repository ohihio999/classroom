# -*- coding: utf-8 -*-
"""
imgpdf 自動命名（辨識封面書名 → 博客來校正 → 主書名 - 作者）的測試。

Groq 呼叫一律 mock（測試不該依賴任何一把 key）；
ffmpeg、img2pdf、檔案搬移都是真的跑，博客來那組會實際連網，連不上就 skip。
"""

import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server


def make_png(path: Path, size: str = "800x1000") -> None:
    subprocess.run([server.FFMPEG, "-hide_banner", "-v", "error", "-f", "lavfi",
                    "-i", f"color=c=gray:s={size}", "-frames:v", "1", "-y", str(path)],
                   check=True)


def wait_for(job, limit: float = 60.0) -> None:
    started = time.time()
    while not job.done and time.time() - started < limit:
        time.sleep(0.05)


def fake_http(payload):
    """urlopen 的替身，回一個吐 JSON 的 context manager。"""
    response = mock.MagicMock()
    response.read.return_value = json.dumps(payload).encode()
    response.__enter__ = lambda self: self
    response.__exit__ = lambda *args: False
    return response


VISION_REPLY = {"choices": [{"message": {
    "content": '```json\n{"title":"原子習慣","author":"James Clear","confidence":"high"}\n```'}}]}


class TestNameCleanup(unittest.TestCase):
    def test_illegal_chars_become_fullwidth(self):
        self.assertEqual(server.sanitize_filename('原子習慣: 細微/改變'), '原子習慣： 細微／改變')

    def test_trailing_dots_and_spaces_removed(self):
        self.assertEqual(server.sanitize_filename('  書名 .. '), '書名')

    def test_length_capped(self):
        self.assertEqual(len(server.sanitize_filename('書' * 200)), server.MAX_NAME_LENGTH)

    def test_subtitle_dropped_author_kept(self):
        self.assertEqual(
            server.compose_folder_name('原子習慣：細微改變帶來巨大成就的實證法則', 'James Clear'),
            '原子習慣 - James Clear')

    def test_no_author_keeps_main_title_only(self):
        self.assertEqual(server.compose_folder_name('被討厭的勇氣:自我啟發之父的教導', ''),
                         '被討厭的勇氣')

    def test_title_without_colon_survives_whole(self):
        self.assertEqual(server.compose_folder_name('原子習慣WORKBOOK【實踐本】', ''),
                         '原子習慣WORKBOOK【實踐本】')


class TestTitleMatching(unittest.TestCase):
    CANDIDATES = ['原子習慣WORKBOOK【實踐本‧附練習別冊】',
                  '原子習慣：細微改變帶來巨大成就的實證法則',
                  '【博客來獨家套書】原子習慣+原子習慣WORKBOOK(官方版)']

    def test_picks_shortest_containing_match(self):
        # 含辨識書名的最短一筆＝本體，不是套書或 WORKBOOK
        self.assertEqual(server.pick_official_title('原子習慣', self.CANDIDATES),
                         self.CANDIDATES[1])

    def test_unrelated_candidates_rejected(self):
        self.assertEqual(server.pick_official_title('海賊王', self.CANDIDATES), '')

    def test_no_candidates(self):
        self.assertEqual(server.pick_official_title('原子習慣', []), '')


class TestVisionCall(unittest.TestCase):
    def setUp(self):
        self.shrink = mock.patch.object(server, 'shrink_for_vision', return_value=b'\xff\xd8fake')
        self.shrink.start()
        self.addCleanup(self.shrink.stop)

    def test_parses_json_wrapped_in_code_fence(self):
        with mock.patch('urllib.request.urlopen', return_value=fake_http(VISION_REPLY)):
            page = server.groq_read_page('key', 'x.png', ['model-a'])
        self.assertEqual(page['title'], '原子習慣')
        self.assertEqual(page['confidence'], 'high')

    def test_falls_back_to_next_model(self):
        tried = []

        def urlopen(request, timeout=None):
            model = json.loads(request.data)['model']
            tried.append(model)
            if model == 'model-a':
                raise urllib.error.HTTPError('u', 404, 'model_not_found', {},
                                             io.BytesIO(b'{"error":"no"}'))
            return fake_http(VISION_REPLY)

        with mock.patch('urllib.request.urlopen', side_effect=urlopen):
            page = server.groq_read_page('key', 'x.png', ['model-a', 'model-b'])
        self.assertEqual(tried, ['model-a', 'model-b'])
        self.assertEqual(page['model'], 'model-b')

    def test_all_models_unusable_raises(self):
        error = urllib.error.HTTPError('u', 404, 'x', {}, io.BytesIO(b'{}'))
        with mock.patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                server.groq_read_page('key', 'x.png', ['model-a'])
        self.assertIn('沒有可用的視覺模型', str(caught.exception))


class TestRateLimit(unittest.TestCase):
    """Groq 免費層 TPM 8000，一張圖約 4000 token，看兩張就會 429。"""

    def test_retry_after_header_wins(self):
        error = urllib.error.HTTPError('u', 429, 'slow down', {'retry-after': '3'},
                                       io.BytesIO(b'{}'))
        self.assertEqual(server.retry_after_seconds(error, ''), 3.0)

    def test_delay_parsed_from_message(self):
        error = urllib.error.HTTPError('u', 429, 'slow down', {}, io.BytesIO(b'{}'))
        self.assertAlmostEqual(server.retry_after_seconds(error, 'try again in 960ms'), 1.46)
        self.assertAlmostEqual(server.retry_after_seconds(error, 'try again in 12s'), 12.5)

    def test_delay_is_capped(self):
        error = urllib.error.HTTPError('u', 429, 'slow down', {'retry-after': '9999'},
                                       io.BytesIO(b'{}'))
        self.assertEqual(server.retry_after_seconds(error, ''), server.GROQ_RATE_LIMIT_MAX_WAIT)

    def test_429_retries_then_succeeds(self):
        attempts = []
        waited = []

        def urlopen(request, timeout=None):
            attempts.append(1)
            if len(attempts) == 1:
                raise urllib.error.HTTPError(
                    'u', 429, 'rate limit', {},
                    io.BytesIO(b'{"error":{"message":"try again in 960ms"}}'))
            return fake_http(VISION_REPLY)

        with mock.patch.object(server, 'shrink_for_vision', return_value=b'\xff\xd8fake'), \
             mock.patch.object(server.time, 'sleep', side_effect=waited.append), \
             mock.patch('urllib.request.urlopen', side_effect=urlopen):
            page = server.groq_read_page('key', 'x.png', ['m'], on_progress=lambda text: None)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(waited), 1)
        self.assertEqual(page['title'], '原子習慣')

    def test_429_gives_up_after_retries(self):
        def always_429(request, timeout=None):
            raise urllib.error.HTTPError('u', 429, 'rate limit', {}, io.BytesIO(b'{}'))

        with mock.patch.object(server, 'shrink_for_vision', return_value=b'\xff\xd8fake'), \
             mock.patch.object(server.time, 'sleep'), \
             mock.patch('urllib.request.urlopen', side_effect=always_429):
            with self.assertRaises(RuntimeError) as caught:
                server.groq_read_page('key', 'x.png', ['m'])
        self.assertIn('429', str(caught.exception))


class TestKeyResolution(unittest.TestCase):
    def test_prefers_the_key_from_the_web_page(self):
        with mock.patch.object(server, 'env_groq_key', return_value='env-key'), \
             mock.patch.object(server, 'groq_key_is_valid', return_value=True) as valid:
            self.assertEqual(server.resolve_groq_key('web-key'), 'web-key')
        valid.assert_called_once_with('web-key')

    def test_falls_back_to_env_when_web_key_expired(self):
        # 實際踩過：Firebase Secret 那把被 Groq 自動過期，本機環境變數才是新的
        with mock.patch.object(server, 'env_groq_key', return_value='env-key'), \
             mock.patch.object(server, 'groq_key_is_valid',
                               side_effect=lambda key: key == 'env-key'):
            self.assertEqual(server.resolve_groq_key('web-key'), 'env-key')

    def test_no_key_anywhere(self):
        with mock.patch.object(server, 'env_groq_key', return_value=''):
            with self.assertRaises(RuntimeError) as caught:
                server.resolve_groq_key('')
        self.assertIn('沒有拿到 Groq API Key', str(caught.exception))

    def test_all_keys_rejected_mentions_expiry(self):
        with mock.patch.object(server, 'env_groq_key', return_value='env-key'), \
             mock.patch.object(server, 'groq_key_is_valid', return_value=False):
            with self.assertRaises(RuntimeError) as caught:
                server.resolve_groq_key('web-key')
        self.assertIn('自動過期', str(caught.exception))

    def test_http_401_means_invalid(self):
        error = urllib.error.HTTPError('u', 401, 'unauthorized', {}, io.BytesIO(b'{}'))
        with mock.patch('urllib.request.urlopen', side_effect=error):
            self.assertFalse(server.groq_key_is_valid('whatever'))


class TestIdentifyFlow(unittest.TestCase):
    def setUp(self):
        # 這一組測辨識流程本身，key 檢查另外測，不要真的打 Groq
        patcher = mock.patch.object(server, 'resolve_groq_key', side_effect=lambda key: key or 'k')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_key_stops_early(self):
        with mock.patch.object(server, 'resolve_groq_key',
                               side_effect=RuntimeError('沒有拿到 Groq API Key，無法自動辨識書名。')):
            with self.assertRaises(RuntimeError) as caught:
                server.identify_book_name(['a.png'], '')
        self.assertIn('Groq API Key', str(caught.exception))

    def test_blank_pages_are_skipped(self):
        pages = {'01.png': {"title": "", "author": "", "confidence": "none", "model": "m"},
                 '02.png': {"title": "", "author": "", "confidence": "none", "model": "m"},
                 '03.png': {"title": "原子習慣", "author": "James Clear",
                            "confidence": "high", "model": "m"}}
        steps = []
        with mock.patch.object(server, 'groq_vision_models', return_value=['m']), \
             mock.patch.object(server, 'groq_read_page',
                               side_effect=lambda key, path, models, report=None: pages[Path(path).name]), \
             mock.patch.object(server, 'books_search_titles',
                               return_value=['原子習慣：細微改變帶來巨大成就的實證法則']):
            info = server.identify_book_name(list(pages), 'key', steps.append)
        self.assertEqual(info['page'], '03.png')
        self.assertEqual(info['name'], '原子習慣 - James Clear')
        self.assertEqual(info['source'], 'books.com.tw')
        self.assertTrue(any('辨識書名中' in step for step in steps))
        self.assertTrue(any('比對書名中' in step for step in steps))

    def test_nothing_recognised_raises(self):
        blank = {"title": "", "author": "", "confidence": "none", "model": "m"}
        with mock.patch.object(server, 'groq_vision_models', return_value=['m']), \
             mock.patch.object(server, 'groq_read_page', return_value=blank):
            with self.assertRaises(RuntimeError) as caught:
                server.identify_book_name(['1.png', '2.png'], 'key')
        self.assertIn('認不出書名', str(caught.exception))
        self.assertIn('沒有被動過', str(caught.exception))

    def test_falls_back_to_recognised_title(self):
        found = {"title": "某本冷門到搜不到的書", "author": "", "confidence": "high", "model": "m"}
        with mock.patch.object(server, 'groq_vision_models', return_value=['m']), \
             mock.patch.object(server, 'groq_read_page', return_value=found), \
             mock.patch.object(server, 'books_search_titles', return_value=[]):
            info = server.identify_book_name(['1.png'], 'key')
        self.assertEqual(info['source'], 'vision')
        self.assertEqual(info['name'], '某本冷門到搜不到的書')


class TestBooksSearchOnline(unittest.TestCase):
    """真的連博客來。搜尋頁改版時這裡會先炸，就知道要修解析。"""

    def test_search_returns_titles(self):
        titles = server.books_search_titles('原子習慣')
        if not titles:
            self.skipTest('連不上博客來，跳過')
        self.assertTrue(any('原子習慣' in title for title in titles))
        self.assertTrue(all(title == title.strip() for title in titles))


class TestImgPdfJob(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(server, 'resolve_groq_key', side_effect=lambda key: key or 'k')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / '掃描_20260824'
        self.folder.mkdir()

    def test_auto_naming_end_to_end(self):
        for number in (1, 2, 10):
            make_png(self.folder / f'{number}.png')
        images = server.scan_images(str(self.folder))
        self.assertEqual([Path(p).name for p in images], ['1.png', '2.png', '10.png'])

        found = {"title": "原子習慣", "author": "James Clear", "confidence": "high", "model": "m"}
        with mock.patch.object(server, 'groq_vision_models', return_value=['m']), \
             mock.patch.object(server, 'groq_read_page', return_value=found), \
             mock.patch.object(server, 'books_search_titles',
                               return_value=['原子習慣：細微改變帶來巨大成就的實證法則']):
            job = server.start_job('imgpdf', images, str(self.folder),
                                   target_name='', groq_key='key')
            wait_for(job)

        snapshot = job.snapshot()
        name = snapshot['targetName']
        self.assertEqual(name, '原子習慣 - James Clear')
        self.assertEqual([item['error'] for item in snapshot['items']], ['', '', ''])
        self.assertTrue((self.folder / name / f'{name}.pdf').exists())
        self.assertEqual(sorted(p.name for p in (self.folder / name).glob('*.png')),
                         [f'{name}_0001.png', f'{name}_0002.png', f'{name}_0003.png'])
        self.assertEqual([p.name for p in self.folder.iterdir()], [name])
        self.assertEqual(snapshot['identify']['fullTitle'],
                         '原子習慣：細微改變帶來巨大成就的實證法則')

        import pypdf
        pdf = pypdf.PdfReader(str(self.folder / name / f'{name}.pdf'))
        self.assertEqual(len(pdf.pages), 3)

    def test_manual_name_skips_identification(self):
        make_png(self.folder / '1.png', '400x600')
        with mock.patch.object(server, 'identify_book_name',
                               side_effect=AssertionError('填了名稱就不該辨識')):
            job = server.start_job('imgpdf', server.scan_images(str(self.folder)),
                                   str(self.folder), target_name='我自己取的名字', groq_key='')
            wait_for(job)
        self.assertTrue((self.folder / '我自己取的名字' / '我自己取的名字.pdf').exists())

    def test_failure_leaves_originals_untouched(self):
        for number in (1, 2):
            make_png(self.folder / f'{number}.png', '400x600')
        before = sorted(p.name for p in self.folder.iterdir())
        blank = {"title": "", "author": "", "confidence": "none", "model": "m"}
        with mock.patch.object(server, 'groq_vision_models', return_value=['m']), \
             mock.patch.object(server, 'groq_read_page', return_value=blank):
            job = server.start_job('imgpdf', server.scan_images(str(self.folder)),
                                   str(self.folder), target_name='', groq_key='key')
            wait_for(job)
        self.assertIn('認不出書名', job.message)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), before)


if __name__ == '__main__':
    unittest.main()
