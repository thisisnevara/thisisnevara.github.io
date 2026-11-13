"""Тесты сборки сайта. Запуск: python -m unittest discover -s scripts -v"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import build_site as bs  # noqa: E402

INDEX = """<html><head>
<meta property="og:image" content="{{OG_IMAGE}}">
{{HERO_PRELOAD}}
<style>.hero{--photo-w:{{PHOTO_W}};--photo-c:{{PHOTO_C}}}</style></head>
<body><img class="photo" src="{{HERO_SRC}}" srcset="{{HERO_SRCSET}}" sizes="{{HERO_SIZES}}"></body></html>"""


class BuildTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.root = tempfile.mkdtemp()
        self.out = os.path.join(self.root, "_site")
        os.makedirs(os.path.join(self.root, "images"))
        os.makedirs(os.path.join(self.root, "covers"))
        Image.new("RGB", (1600, 900), "#802040").save(os.path.join(self.root, "images", "hero.jpg"))
        self.settings({"couple_left": 570, "couple_right": 1550})
        self.write("index.html", INDEX)
        self.write("favicon.svg", "<svg/>")
        self.write("CNAME", "thisisnevara.com\n")
        self.write("covers/AAAAAAAAAAA-480-abcdef.webp", "x")

    def tearDown(self):
        shutil.rmtree(self.root)

    def write(self, name, text):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as f:
            f.write(text)

    def settings(self, s):
        self.write("images/hero.json", json.dumps(s))

    def built(self, name="index.html"):
        with open(os.path.join(self.out, name), encoding="utf-8") as f:
            return f.read()

    def test_builds_all_files(self):
        from PIL import Image
        bs.build(self.root, self.out)
        page = self.built()
        self.assertNotIn("{{", page)
        imgs = sorted(os.listdir(os.path.join(self.out, "img")))
        self.assertEqual(len(imgs), 3)
        self.assertTrue(all(f.startswith("hero-") and f.endswith(".webp") for f in imgs))
        for f in imgs:
            self.assertIn(f"img/{f}", page)
        with Image.open(os.path.join(self.out, "nevara-og.jpg")) as og:
            self.assertEqual(og.size, (1200, 630))
        for name in ("favicon.svg", "CNAME", "covers/AAAAAAAAAAA-480-abcdef.webp"):
            self.assertTrue(os.path.exists(os.path.join(self.out, name)), name)

    def test_phone_numbers_from_settings(self):
        bs.build(self.root, self.out)
        page = self.built()
        self.assertIn("--photo-w:163.3vw", page)      # 1600 / (1550 − 570) × 100
        self.assertIn("--photo-c:0.6625", page)       # (570 + 1550) / 2 / 1600

    def test_new_photo_gets_new_names(self):
        from PIL import Image
        bs.build(self.root, self.out)
        first = self.built()
        Image.new("RGB", (1600, 900), "#204080").save(os.path.join(self.root, "images", "hero.jpg"))
        bs.build(self.root, self.out)
        second = self.built()
        self.assertNotEqual(first, second)            # другие имена и ?v= — кэш не мешает
        self.assertEqual(len(os.listdir(os.path.join(self.out, "img"))), 3)   # без старых файлов

    def test_small_photo_skips_bigger_versions(self):
        from PIL import Image
        Image.new("RGB", (1200, 675), "#802040").save(os.path.join(self.root, "images", "hero.jpg"))
        bs.build(self.root, self.out)
        self.assertEqual(len(os.listdir(os.path.join(self.out, "img"))), 2)

    def assert_fails_and_keeps_previous(self, change):
        bs.build(self.root, self.out)
        before = self.built()
        change()
        with self.assertRaises(bs.BuildError):
            bs.build(self.root, self.out)
        self.assertEqual(self.built(), before)        # прошлая сборка не тронута

    def test_missing_photo(self):
        self.assert_fails_and_keeps_previous(lambda: os.remove(os.path.join(self.root, "images", "hero.jpg")))

    def test_wrong_ratio(self):
        from PIL import Image
        self.assert_fails_and_keeps_previous(
            lambda: Image.new("RGB", (1000, 1000)).save(os.path.join(self.root, "images", "hero.jpg")))

    def test_bad_settings(self):
        self.assert_fails_and_keeps_previous(lambda: self.write("images/hero.json", "{oops"))
        self.settings({"couple_left": 570, "couple_right": 1550})
        self.assert_fails_and_keeps_previous(lambda: self.settings({"couple_left": 1500, "couple_right": 600}))

    def test_unknown_placeholder(self):
        self.assert_fails_and_keeps_previous(lambda: self.write("index.html", INDEX + "{{SOMETHING_NEW}}"))


SONG_TPL = """<html><head><title>{{PAGE_TITLE}}</title><meta name="description" content="{{META_DESC}}">
<link rel="canonical" href="{{CANONICAL}}"><meta property="og:image" content="{{OG_IMAGE}}">
<script type="application/ld+json">{{JSONLD}}</script></head><body>{{BACKDROP}}
<div class="cover" data-video="{{VIDEO_ID}}">{{COVER_IMG}}</div><h1>{{NAME}}</h1>{{LINE}}{{VERSIONS}}{{JUMP}}
<p>{{TAGS}}<time datetime="{{DATE}}">{{DATE_SHOWN}}</time></p>{{ABOUT}}{{LYRICS}}<ul>{{OTHERS}}</ul></body></html>"""

LYRICS = """Пароль:
[Verse 1]
Ночь кипит, как яркий карнавал,
Ты вошла — и воздух тесным стал.

[Pre-Chorus: Male Solo]
Тише слова
Female Solo}
— громче походка,
(ай-ай-ай)
"""


class SongPageTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.root = tempfile.mkdtemp()
        self.out = os.path.join(self.root, "_site")
        for d in ("images", "covers", "data", "lyrics"):
            os.makedirs(os.path.join(self.root, d))
        Image.new("RGB", (1600, 900), "#802040").save(os.path.join(self.root, "images", "hero.jpg"))
        Image.new("RGB", (800, 450), "#a03060").save(os.path.join(self.root, "covers", "C6ynOt9dk1M-800-aaaaaa.webp"))
        self.write("images/hero.json", json.dumps({"couple_left": 570, "couple_right": 1550}))
        self.write("index.html", INDEX)
        self.write("song.html", SONG_TPL)
        self.songs = [
            {"id": "C6ynOt9dk1M", "slug": "parol", "title": "Nevara — ПАРОЛЬ | Двигайся, пока горят огни | Reggaeton 2026",
             "name": "ПАРОЛЬ", "sub": ["Двигайся, пока горят огни", "Reggaeton 2026"], "published": "2026-04-29",
             "views": 10, "description": ["Песня о ночном танце."], "cover": "covers/C6ynOt9dk1M-800-aaaaaa.webp"},
            {"id": "HTOOCYLaWmc", "slug": "na-maksimum", "title": "Nevara — НА МАКСИМУМ", "name": "НА МАКСИМУМ",
             "sub": [], "published": "2026-05-12", "views": 5, "description": [], "cover": "covers/missing.webp"},
        ]
        self.write("data/songs.json", json.dumps({"songs": self.songs}, ensure_ascii=False))
        self.write("lyrics/parol.txt", LYRICS)

    def tearDown(self):
        shutil.rmtree(self.root)

    def write(self, name, text):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as f:
            f.write(text)

    def page(self, slug):
        with open(os.path.join(self.out, slug, "index.html"), encoding="utf-8") as f:
            return f.read()

    def test_page_without_text_cover_and_description(self):
        self.songs[1]["cover"] = None
        self.write("data/songs.json", json.dumps({"songs": self.songs}, ensure_ascii=False))
        bs.build(self.root, self.out)
        p = self.page("na-maksimum")
        self.assertNotIn("<section", p)                  # нет пустых «О песне» и «Текст песни»
        self.assertNotIn('href="#lyrics"', p)            # и ссылки на текст тоже
        self.assertNotIn("<img src=\"../covers", p)      # нейтральный фон вместо обложки
        self.assertIn("nevara-og.jpg?v=", p)             # превью для соцсетей — общая картинка сайта
        self.assertIn("· клип</title>", p)
        self.assertIn('href="#lyrics"', self.page("parol"))

    def test_pages_for_every_song(self):
        bs.build(self.root, self.out)
        for s in self.songs:
            self.assertNotIn("{{", self.page(s["slug"]))
        p = self.page("parol")
        self.assertIn("<title>Пароль — Nevara · текст песни и клип</title>", p)
        self.assertIn('data-video="C6ynOt9dk1M"', p)
        self.assertIn("Песня о ночном танце.", p)
        self.assertIn('<span class="tag">Reggaeton</span>', p)
        self.assertIn('"@type": "MusicRecording"', p)

    def test_lyrics_cleaned(self):
        bs.build(self.root, self.out)
        p = self.page("parol")
        self.assertIn("Ночь кипит, как яркий карнавал,<br>Ты вошла — и воздух тесным стал.", p)
        self.assertIn("Тише слова<br>— громче походка,<br>(ай-ай-ай)", p)       # бэк-вокал в () остаётся
        lyrics = p.split('class="lyrics"')[1]
        for junk in ("[Verse", "Pre-Chorus", "Female Solo", "Пароль:"):
            self.assertNotIn(junk, lyrics)

    def test_song_without_lyrics_or_cover(self):
        bs.build(self.root, self.out)
        p = self.page("na-maksimum")
        self.assertNotIn('class="lyrics"', p)
        self.assertNotIn('data-i18n="about"', p)
        self.assertIn("<title>На максимум — Nevara · клип</title>", p)
        self.assertNotIn('<img src="../img/', p)                                 # без обложки — только свечение из CSS
        self.assertNotIn("читайте текст", p)                                     # не обещаем текст, которого нет

    def test_blurred_background_made_from_cover(self):
        bs.build(self.root, self.out)
        bgs = [f for f in os.listdir(os.path.join(self.out, "img")) if f.startswith("bg-")]
        self.assertEqual(len(bgs), 1)
        self.assertIn(f'src="../img/{bgs[0]}"', self.page("parol"))

    def test_sitemap_and_robots(self):
        bs.build(self.root, self.out)
        with open(os.path.join(self.out, "sitemap.xml"), encoding="utf-8") as f:
            sm = f.read()
        self.assertIn("<loc>https://thisisnevara.com/parol/</loc><lastmod>2026-04-29</lastmod>", sm)
        self.assertIn("https://thisisnevara.com/na-maksimum/", sm)
        with open(os.path.join(self.out, "robots.txt"), encoding="utf-8") as f:
            self.assertIn("Sitemap: https://thisisnevara.com/sitemap.xml", f.read())

    def test_hidden_song_page_disappears(self):
        bs.build(self.root, self.out)
        self.assertTrue(os.path.exists(os.path.join(self.out, "na-maksimum")))
        self.write("data/songs.json", json.dumps({"songs": self.songs[:1]}, ensure_ascii=False))
        bs.build(self.root, self.out)
        self.assertFalse(os.path.exists(os.path.join(self.out, "na-maksimum")))

    def test_bad_slug_rejected(self):
        self.songs[0]["slug"] = "../evil"
        self.write("data/songs.json", json.dumps({"songs": self.songs}, ensure_ascii=False))
        with self.assertRaises(bs.BuildError):
            bs.build(self.root, self.out)

    def test_works_without_song_data(self):
        os.remove(os.path.join(self.root, "data", "songs.json"))
        bs.build(self.root, self.out)
        self.assertTrue(os.path.exists(os.path.join(self.out, "index.html")))



class VersionTests(unittest.TestCase):
    def test_version_key_pairs_languages(self):
        import build_site as b
        self.assertEqual(b.version_key("bam-boleo"), b.version_key("bamboleo-espanol"))
        self.assertNotEqual(b.version_key("ola-ola"), b.version_key("ole-ole"))
        self.assertEqual(b.song_lang("БАМ-БОЛЕО"), "ru")
        self.assertEqual(b.song_lang("BAMBOLEO (Español)"), "es")

    def test_year_removed_from_genre(self):
        import build_site as b
        self.assertEqual(b.YEAR_TAIL.sub("", "Latin Pop 2026"), "Latin Pop")


if __name__ == "__main__":
    unittest.main()
