"""Тесты скрипта обновления. Запуск: python -m unittest discover -s scripts -v

Работают без интернета и без ключа: ответы YouTube подменяются тестовыми данными.
"""
import io
import json
import re
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
# + старая малопопулярная песня: она есть только в общем списке (не новая и не в популярных)
EXTRA = VIDEOS + [("XL9t_Nde2v8", "Nevara — КОД ДОСТУПА | На твоих губах | Reggaeton 2026",
                   "2026-06-16T10:00:00Z", 10, "PT3M", "none")]


def jpeg(letterbox):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (480, 360) if letterbox else (1280, 720), "black")
    ImageDraw.Draw(im).rectangle((0, 45, 480, 315) if letterbox else (0, 0, 1280, 720), fill="#c0306a")
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


class FakeYouTube:
    def __init__(self, videos=VIDEOS, uulf=True, api_error=None, broken_thumbs=False, privacy=None, broken_n=()):
        self.videos, self.uulf, self.api_error, self.broken_thumbs = videos, uulf, api_error, broken_thumbs
        self.privacy = privacy or {}     # id -> "private" | "unlisted"
        self.broken_n = set(broken_n)    # номера видео, чьи обложки YouTube «не отдаёт»

    def __call__(self, url):
        if url.startswith("thumb:"):
            if self.broken_thumbs or int(url.split(":")[1]) in self.broken_n:
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
            # как у настоящего YouTube: закрытые видео по общему ключу не видны
            items = [v for v in items if self.privacy.get(v[0]) != "private"]
            return json.dumps({"items": [{"contentDetails": {"videoId": v[0]}} for v in items]}).encode()
        ids = q["id"][0].split(",")
        out = []
        for n, v in enumerate(self.videos):
            if v[0] in ids and self.privacy.get(v[0]) != "private":
                thumbs = {"high": {"url": f"thumb:{n}:high"}}
                if n % 2 == 0:
                    thumbs["maxres"] = {"url": f"thumb:{n}:maxres"}
                out.append({"id": v[0],
                            "snippet": {"title": v[1], "description": "", "publishedAt": v[2],
                                        "liveBroadcastContent": v[5], "thumbnails": thumbs},
                            "statistics": {"viewCount": str(v[3])},
                            "contentDetails": {"duration": v[4]},
                            "status": {"privacyStatus": self.privacy.get(v[0], "public")}})
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
        grid = page.split('class="release-grid"')[1].split('<details class="catalog">')[0]
        order = [vid for vid in ("ZVjUQYGsI4c", "hVZ7I8PLAbE", "C6ynOt9dk1M", "GuD_Mh0cCrA")]
        positions = [grid.index(v) for v in order]
        self.assertEqual(positions, sorted(positions))                   # по убыванию просмотров
        self.assertNotIn("qUbkpldW_Z0", grid)                            # новый релиз не повторяется

    def test_player_uses_new_release_id(self):
        page = self.run_with(FakeYouTube())
        new_block = page.split('<p class="subhead"')[0]
        self.assertEqual(page.count('data-video="'), 1)                  # плеер только у нового релиза
        self.assertIn('data-video="qUbkpldW_Z0"', new_block)
        self.assertIn('class="play"', new_block)
        self.assertIn('href="https://www.youtube.com/watch?v=qUbkpldW_Z0"', new_block)  # запасная ссылка
        self.assertNotIn("<iframe", page)                                 # до нажатия YouTube не грузится

    def test_subtitle_and_tags(self):
        page = self.run_with(FakeYouTube())
        new_block = page.split('<p class="subhead"')[0]
        self.assertIn('<p class="sub">Versión en\u00a0Español</p>', new_block)
        self.assertIn('<span class="tag">Latin Pop\u00a02026</span>', new_block)

    def test_catalog_lists_all_songs_newest_first(self):
        page = self.run_with(FakeYouTube())
        self.assertEqual(page.count('<details class="catalog">'), 1)
        catalog = page.split('<details class="catalog">')[1].split('</details>')[0]
        self.assertIn('<span class="count">5</span>', catalog)
        order = ["qUbkpldW_Z0", "hVZ7I8PLAbE", "C6ynOt9dk1M", "GuD_Mh0cCrA", "ZVjUQYGsI4c"]   # по дате, новые сверху
        positions = [catalog.index(v) for v in order]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("SHORTS00001", catalog)
        self.assertNotIn("PREMIERE001", catalog)
        self.assertIn('<time datetime="2026-11-08">11.2026</time>', catalog)

    def test_catalog_keeps_versions_apart_and_full_title(self):
        page = self.run_with(FakeYouTube())
        catalog = page.split('<details class="catalog">')[1]
        self.assertIn('<span class="c-name">BAMBOLEO</span><span class="c-sub">Versión en\u00a0Español<span class="c-sep"> · </span><span class="c-genre">Latin Pop\u00a02026</span></span>', catalog)
        self.assertIn('title="Nevara — BAMBOLEO | Versión en Español | Latin Pop 2026"', catalog)

    def test_catalog_escapes_titles(self):
        videos = [("AAAAAAAAAAA", 'Nevara — "Песня" <b>', "2026-11-08T10:00:00Z", 1, "PT3M", "none"),
                  ("BBBBBBBBBBB", "Nevara — Другая", "2026-10-08T10:00:00Z", 1, "PT3M", "none")]
        page = self.run_with(FakeYouTube(videos=videos))
        catalog = page.split('<details class="catalog">')[1]
        self.assertNotIn("<b>", catalog)
        self.assertIn('title="Nevara — &quot;Песня&quot; &lt;b&gt;"', catalog)

    def test_only_public_videos_shown(self):
        # новый релиз (BAMBOLEO) скрыт, одна песня «по ссылке» — обеих не должно быть нигде на сайте
        page = self.run_with(FakeYouTube(privacy={"qUbkpldW_Z0": "private", "C6ynOt9dk1M": "unlisted"}))
        self.assertNotIn("qUbkpldW_Z0", page)
        self.assertNotIn("C6ynOt9dk1M", page)
        new_block = page.split('<p class="subhead"')[0]
        self.assertIn('data-video="hVZ7I8PLAbE"', new_block)              # новым стал следующий открытый
        self.assertIn('<span class="count">3</span>', page)
        covers = os.listdir(os.path.join(self.dir, "covers"))
        self.assertFalse([c for c in covers if c.startswith(("qUbkpldW_Z0", "C6ynOt9dk1M"))])

    def test_hidden_song_disappears_on_next_run(self):
        self.run_with(FakeYouTube())
        self.assertIn("C6ynOt9dk1M", self.page())
        page = self.run_with(FakeYouTube(privacy={"C6ynOt9dk1M": "private"}))
        self.assertNotIn("C6ynOt9dk1M", page)
        self.assertFalse([c for c in os.listdir(os.path.join(self.dir, "covers")) if c.startswith("C6ynOt9dk1M")])

    def test_slugify(self):
        cases = {"НОЯБРЬ НА ДВОИХ": "noyabr-na-dvoikh", "BAMBOLEO (Español)": "bamboleo-espanol",
                 "МАНИЯ — ИСПАНИЯ": "maniya-ispaniya", "MI COMETA / МОЯ КОМЕТА": "mi-cometa-moya-kometa",
                 "ЛАТИНО-ПАНИКА": "latino-panika", "VEN PARA MÍ": "ven-para-mi", "ОЛЕ ОЛЕ": "ole-ole",
                 "ЭТО ГРАВИТАЦИЯ": "eto-gravitatsiya", "БАЙЛАМЕ": "baylame"}
        for name, slug in cases.items():
            with self.subTest(name=name):
                self.assertEqual(upd.slugify(name), slug)

    def test_clean_description(self):
        text = ("🔔 Подписаться на Nevara: https://www.youtube.com/@thisisnevara?sub_confirmation=1\n"
                "🌐 Официальный сайт: https://thisisnevara.com\n\n"
                "❄️🔥 «НОЯБРЬ НА ДВОИХ» — новая Latin Pop премьера 2026.\n\n"
                "Второй абзац о песне.\n\n"
                "Ноябрь на двоих — горячая ночь,\nТанцуй со мной, прогоним холод прочь.\n\n"
                "Ещё абзац после текста\n\n#Nevara #LatinPop")
        self.assertEqual(upd.clean_description(text),
                         ["❄️🔥 «НОЯБРЬ НА ДВОИХ» — новая Latin Pop премьера 2026.", "Второй абзац о песне."])
        self.assertEqual(upd.clean_description(""), [])
        self.assertEqual(upd.clean_description("#Nevara #LatinPop"), [])

    def test_song_data_and_links_to_song_pages(self):
        page = self.run_with(FakeYouTube())
        with open(os.path.join(self.dir, "data", "songs.json"), encoding="utf-8") as f:
            songs = json.load(f)["songs"]
        self.assertEqual([s["id"] for s in songs][0], "qUbkpldW_Z0")            # новые сверху
        bam = songs[0]
        self.assertEqual(bam["slug"], "bamboleo")
        self.assertEqual(bam["sub"], ["Versión en Español", "Latin Pop 2026"])
        self.assertTrue(os.path.exists(os.path.join(self.dir, bam["cover"])))
        catalog = page.split('<details class="catalog">')[1]
        self.assertIn('href="bamboleo/"', catalog)
        self.assertNotIn("youtube.com/watch", catalog)                           # список ведёт на страницы песен
        self.assertIn('class="text-link" href="bamboleo/"', page)

    def songs(self):
        with open(os.path.join(self.dir, "data", "songs.json"), encoding="utf-8") as f:
            return json.load(f)["songs"]

    def test_every_song_page_gets_a_cover(self):
        self.run_with(FakeYouTube())
        songs = self.songs()
        self.assertTrue(songs)
        for s in songs:
            self.assertTrue(s["cover"] and os.path.exists(os.path.join(self.dir, s["cover"])), s["id"])

    def test_page_cover_falls_back_to_previous_then_to_none(self):
        self.run_with(FakeYouTube(videos=EXTRA))
        before = {s["id"]: s["cover"] for s in self.songs()}
        last = len(EXTRA) - 1                       # старая песня, не новая и не в популярных
        last_id = EXTRA[last][0]
        self.run_with(FakeYouTube(videos=EXTRA, broken_n={last}))  # YouTube не отдал её обложку — берём прошлую
        after = {s["id"]: s["cover"] for s in self.songs()}
        self.assertEqual(after[last_id], before[last_id])
        self.assertTrue(os.path.exists(os.path.join(self.dir, after[last_id])))
        shutil.rmtree(os.path.join(self.dir, "data"))  # прошлой нет — страница без обложки, но обновление идёт
        self.run_with(FakeYouTube(videos=EXTRA, broken_n={last}))
        self.assertIsNone({s["id"]: s["cover"] for s in self.songs()}[last_id])

    def test_slug_stays_when_title_changes(self):
        self.run_with(FakeYouTube(videos=EXTRA))
        first = {s["id"]: s["slug"] for s in self.songs()}
        renamed = [(v[0], v[1].replace("BAMBOLEO", "BAMBOLEO NUEVO"),) + tuple(v[2:]) for v in EXTRA]
        self.run_with(FakeYouTube(videos=renamed))
        self.assertEqual({s["id"]: s["slug"] for s in self.songs()}, first)
        with open(os.path.join(self.dir, "data", "slugs.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), first)

    def test_hidden_song_keeps_its_slug_reserved(self):
        self.run_with(FakeYouTube(videos=EXTRA))
        hidden = EXTRA[-1][0]
        slug = {s["id"]: s["slug"] for s in self.songs()}[hidden]
        self.run_with(FakeYouTube(videos=EXTRA, privacy={hidden: "private"}))
        self.assertNotIn(hidden, [s["id"] for s in self.songs()])
        self.run_with(FakeYouTube(videos=EXTRA))
        self.assertEqual({s["id"]: s["slug"] for s in self.songs()}[hidden], slug)

    def test_same_names_get_unique_slugs(self):
        videos = [("AAAAAAAAAAA", "Nevara — ДО УТРА | Версия 1", "2026-11-08T10:00:00Z", 1, "PT3M", "none"),
                  ("BBBBBBBBBBB", "Nevara — ДО УТРА | Версия 2", "2026-10-08T10:00:00Z", 1, "PT3M", "none")]
        self.run_with(FakeYouTube(videos=videos))
        with open(os.path.join(self.dir, "data", "songs.json"), encoding="utf-8") as f:
            slugs = {s["id"]: s["slug"] for s in json.load(f)["songs"]}
        self.assertEqual(slugs, {"BBBBBBBBBBB": "do-utra", "AAAAAAAAAAA": "do-utra-2"})   # старшая — без номера

    def test_shorts_and_premieres_excluded(self):
        for uulf in (True, False):
            with self.subTest(uulf=uulf):
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write(PAGE)
                page = self.run_with(FakeYouTube(uulf=uulf))
                self.assertNotIn("SHORTS00001", page)
                self.assertNotIn("PREMIERE001", page)

    def test_covers_saved_as_files_and_cropped(self):
        from PIL import Image
        page = self.run_with(FakeYouTube())
        self.assertNotIn("data:image", page)
        self.assertNotIn("i.ytimg.com", page)
        names = re.findall(r'src="(covers/[^"]+\.webp)"', page)
        self.assertEqual(len(names), 5)
        for n in names:
            path = os.path.join(self.dir, n)
            self.assertTrue(os.path.exists(path), n)
            with Image.open(path) as im:
                w, h = im.size
            self.assertAlmostEqual(w / h, 16 / 9, delta=0.02)            # чёрные полосы 4:3 обрезаны
        self.assertIn('loading="lazy"', page)

    def test_stale_covers_removed_only_after_success(self):
        os.makedirs(os.path.join(self.dir, "covers"))
        old = os.path.join(self.dir, "covers", "OLDVIDEO001-480-abcdef.webp")
        with open(old, "wb") as f:
            f.write(b"old")
        upd.http_get = FakeYouTube(broken_thumbs=True)               # сбой: старое не трогаем
        with self.assertRaises(upd.UpdateError):
            upd.main(self.path)
        self.assertTrue(os.path.exists(old))
        self.assertEqual(self.page(), PAGE)
        self.run_with(FakeYouTube())                                     # успех: старое убрано
        self.assertFalse(os.path.exists(old))
        # 5 песен × обложка 800 px для страниц + 4 популярные × 480 px
        self.assertEqual(len(os.listdir(os.path.join(self.dir, "covers"))), 9)


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
        self.assertEqual(sorted(os.listdir(self.dir)), ["covers", "data", "index.html"])
        self.assertEqual(sorted(os.listdir(os.path.join(self.dir, "data"))), ["slugs.json", "songs.json"])
        self.assertFalse([f for f in os.listdir(os.path.join(self.dir, "covers")) if not f.endswith(".webp")])

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
