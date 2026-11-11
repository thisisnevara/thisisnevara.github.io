"""Тесты скрипта обновления. Запуск: python -m unittest discover -s scripts -v

Работают без интернета и без ключа: ответы YouTube подменяются тестовыми данными.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import update_releases as upd  # noqa: E402

PAGE = """<html><body>
      <!-- AUTO:RELEASES:START (тест) -->
      СТАРЫЙ БЛОК
      <!-- AUTO:RELEASES:END -->
</body></html>"""

# id, название, дата, просмотры, длительность, статус трансляции
VIDEOS = [
    ("qUbkpldW_Z0", "Nevara — BAMBOLEO | Versión en Español | Latin Pop 2026", "2026-11-08T10:00:00Z", 1200, "PT3M12S", "none"),
    ("C6ynOt9dk1M", "Nevara — ПАРОЛЬ (Official Audio)", "2026-09-01T10:00:00Z", 5400, "PT2M58S", "none"),
    ("hVZ7I8PLAbE", "Nevara - БАМ-БОЛЕО | Latin Pop", "2026-10-01T10:00:00Z", 8100, "PT3M05S", "none"),
    ("GuD_Mh0cCrA", "ЛАТИНО-ПАНИКА | Nevara", "2026-08-01T10:00:00Z", 3000, "PT3M30S", "none"),
    ("ZVjUQYGsI4c", "Nevara — Ven Para Mí | Lyric Video", "2026-07-01T10:00:00Z", 9900, "PT2M45S", "none"),
    ("SHORTS00001", "Nevara teaser #shorts", "2026-11-09T10:00:00Z", 50000, "PT0M30S", "none"),
    ("PREMIERE001", "Nevara — Скоро премьера", "2026-11-12T10:00:00Z", 0, "PT0S", "upcoming"),
]


def jpeg(letterbox):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (480, 360) if letterbox else (1280, 720), "black")
    ImageDraw.Draw(im).rectangle((0, 45, 480, 315) if letterbox else (0, 0, 1280, 720), fill="#c0306a")
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


class FakeYouTube:
    def __init__(self, videos=VIDEOS, uulf=True, api_error=None, broken_thumbs=False):
        self.videos, self.uulf, self.api_error, self.broken_thumbs = videos, uulf, api_error, broken_thumbs

    def __call__(self, url):
        if url.startswith("thumb:"):
            if self.broken_thumbs:
                raise OSError("network down")
            return jpeg(letterbox=url.endswith("high"))
        if self.api_error:
            raise upd.UpdateError(self.api_error)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        endpoint = url.split("/v3/")[1].split("?")[0]
        if endpoint == "playlistItems":
            pl = q["playlistId"][0]
            if pl.startswith("UULF"):
                if not self.uulf:
                    raise upd.UpdateError("404 playlistNotFound")
                items = [v for v in self.videos if v[4] != "PT0M30S"]
            else:
                items = self.videos
            return json.dumps({"items": [{"contentDetails": {"videoId": v[0]}} for v in items]}).encode()
        ids = q["id"][0].split(",")
        out = []
        for n, v in enumerate(self.videos):
            if v[0] in ids:
                thumbs = {"high": {"url": f"thumb:{n}:high"}}
                if n % 2 == 0:
                    thumbs["maxres"] = {"url": f"thumb:{n}:maxres"}
                out.append({"id": v[0],
                            "snippet": {"title": v[1], "description": "", "publishedAt": v[2],
                                        "liveBroadcastContent": v[5], "thumbnails": thumbs},
                            "statistics": {"viewCount": str(v[3])},
                            "contentDetails": {"duration": v[4]},
                            "status": {"privacyStatus": "public"}})
        return json.dumps({"items": out}).encode()


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "index.html")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(PAGE)
        self._orig_get = upd.http_get
        os.environ["YOUTUBE_API_KEY"] = "test-key"

    def tearDown(self):
        upd.http_get = self._orig_get
        shutil.rmtree(self.dir)

    def page(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def run_with(self, fake):
        upd.http_get = fake
        upd.main(self.path)
        return self.page()

    # --- названия ---
    def test_split_title(self):
        cases = {
            "Nevara — BAMBOLEO | Versión en Español | Latin Pop 2026": ("BAMBOLEO", "Versión en Español · Latin Pop 2026"),
            "Nevara — ПАРОЛЬ (Official Audio)": ("ПАРОЛЬ", ""),
            "Nevara - БАМ-БОЛЕО | Latin Pop": ("БАМ-БОЛЕО", "Latin Pop"),
            "ЛАТИНО-ПАНИКА | Nevara": ("ЛАТИНО-ПАНИКА", ""),
            "Nevara — Ven Para Mí | Lyric Video": ("Ven Para Mí", "Lyric Video"),
            "Просто песня": ("Просто песня", ""),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(upd.split_title(raw), expected)

    # --- выбор песен ---
    def test_new_release_and_popular_order(self):
        page = self.run_with(FakeYouTube())
        self.assertNotIn("СТАРЫЙ БЛОК", page)
        new_block = page.split('<p class="subhead"')[0]
        self.assertIn("v=qUbkpldW_Z0", new_block)                      # самое новое — BAMBOLEO
        grid = page.split('class="release-grid"')[1]
        order = [vid for vid in ("ZVjUQYGsI4c", "hVZ7I8PLAbE", "C6ynOt9dk1M", "GuD_Mh0cCrA")]
        positions = [grid.index(v) for v in order]
        self.assertEqual(positions, sorted(positions))                   # по убыванию просмотров
        self.assertNotIn("qUbkpldW_Z0", grid)                            # новый релиз не повторяется

    def test_shorts_and_premieres_excluded(self):
        for uulf in (True, False):
            with self.subTest(uulf=uulf):
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write(PAGE)
                page = self.run_with(FakeYouTube(uulf=uulf))
                self.assertNotIn("SHORTS00001", page)
                self.assertNotIn("PREMIERE001", page)

    def test_covers_embedded_and_cropped(self):
        page = self.run_with(FakeYouTube())
        self.assertEqual(page.count("data:image/webp;base64,"), 5)
        self.assertNotIn("i.ytimg.com", page)

    def test_titles_are_escaped(self):
        videos = [("AAAAAAAAAAA", 'Nevara — <script>alert(1)</script>', "2026-11-08T10:00:00Z", 1, "PT3M", "none"),
                  ("BBBBBBBBBBB", "Nevara — Песня & друзья", "2026-10-08T10:00:00Z", 1, "PT3M", "none")]
        page = self.run_with(FakeYouTube(videos=videos))
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("Песня &amp; друзья", page)

    # --- бейдж ---
    def test_badge_switches_after_60_days(self):
        v = {"title": "Nevara — X", "id": "AAAAAAAAAAA", "published": "2026-01-01T00:00:00Z", "thumbs": {}}
        upd.http_get = FakeYouTube()
        v["thumbs"] = {"high": {"url": "thumb:0:high"}}
        old = upd.build_block(v, [], now=datetime(2026, 11, 1, tzinfo=timezone.utc))
        fresh = upd.build_block(v, [], now=datetime(2026, 1, 15, tzinfo=timezone.utc))
        self.assertIn('data-i18n="latest"', old)
        self.assertIn('data-i18n="new"', fresh)

    # --- ошибки: файл не должен меняться ---
    def assert_unchanged_after_error(self, fake):
        upd.http_get = fake
        with self.assertRaises(upd.UpdateError):
            upd.main(self.path)
        self.assertEqual(self.page(), PAGE)

    def test_api_error_keeps_page(self):
        self.assert_unchanged_after_error(FakeYouTube(api_error="HTTP 403 quotaExceeded"))

    def test_missing_cover_keeps_page(self):
        self.assert_unchanged_after_error(FakeYouTube(broken_thumbs=True))

    def test_too_few_videos_keeps_page(self):
        self.assert_unchanged_after_error(FakeYouTube(videos=VIDEOS[:1]))

    def test_bad_video_id_keeps_page(self):
        bad = [("bad id\"><x", "Nevara — X", "2026-11-08T10:00:00Z", 1, "PT3M", "none")] + VIDEOS[1:3]
        self.assert_unchanged_after_error(FakeYouTube(videos=bad))

    def test_missing_key(self):
        del os.environ["YOUTUBE_API_KEY"]
        with self.assertRaises(upd.UpdateError):
            upd.main(self.path)
        self.assertEqual(self.page(), PAGE)

    def test_missing_markers(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("<html>без меток</html>")
        upd.http_get = FakeYouTube()
        with self.assertRaises(upd.UpdateError):
            upd.main(self.path)

    def test_atomic_write_leaves_no_temp_file(self):
        self.run_with(FakeYouTube())
        self.assertEqual(sorted(os.listdir(self.dir)), ["index.html"])

    def test_interrupted_write_keeps_page(self):
        real_replace = os.replace

        def crash(*args):
            raise KeyboardInterrupt("процесс прерван")
        upd.os.replace = crash
        try:
            upd.http_get = FakeYouTube()
            with self.assertRaises(KeyboardInterrupt):
                upd.main(self.path)
        finally:
            upd.os.replace = real_replace
        self.assertEqual(self.page(), PAGE)

    def test_second_run_is_stable(self):
        first = self.run_with(FakeYouTube())
        second = self.run_with(FakeYouTube())
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
