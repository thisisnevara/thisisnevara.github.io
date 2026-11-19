"""Тесты сборки сайта. Запуск: python -m unittest discover -s scripts -v"""
import contextlib
import io
import json
import os
import re
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
<body><img class="photo" src="{{HERO_SRC}}" srcset="{{HERO_SRCSET}}" sizes="{{HERO_SIZES}}">{{ANALYTICS}}</body></html>"""


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
        self.write("apple-touch-icon.png", "png")
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
        for name in ("favicon.svg", "apple-touch-icon.png", "CNAME", "covers/AAAAAAAAAAA-480-abcdef.webp"):
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
<link rel="canonical" href="{{CANONICAL}}"><meta property="og:image" content="{{OG_IMAGE}}"><meta property="og:image:alt" content="{{OG_ALT}}">
<script type="application/ld+json">{{JSONLD}}</script></head><body>{{BACKDROP}}
<div class="cover" data-video="{{VIDEO_ID}}">{{COVER_IMG}}</div><h1 style="--w:{{NAME_W}}">{{NAME}}</h1>{{LINE}}{{VERSIONS}}{{JUMP}}
<p>{{TAGS}}<time datetime="{{DATE}}">{{DATE_SHOWN}}</time></p>{{ABOUT}}{{LYRICS}}<ul>{{OTHERS}}</ul>{{ANALYTICS}}</body></html>"""

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
        self.assertIn("· слушать песню</title>", p)
        self.assertIn('href="#lyrics"', self.page("parol"))

    def test_songs_without_lyrics_are_listed(self):
        missing = []
        bs.build(self.root, self.out, missing_lyrics=missing)
        self.assertEqual(missing, [("НА МАКСИМУМ", "lyrics/na-maksimum.txt")])   # у «ПАРОЛЬ» текст есть
        self.write("lyrics/na-maksimum.txt", "Разгони мой пульс\n")
        missing = []
        bs.build(self.root, self.out, missing_lyrics=missing)
        self.assertEqual(missing, [])                                            # файл добавили — предупреждения нет

    def test_title_size_same_on_all_pages(self):
        """Названия всех песен одного размера — по самому длинному слову среди всех («МАКСИМУМ», 8 букв)."""
        bs.build(self.root, self.out)
        self.assertIn('<h1 style="--w:8">ПАРОЛЬ</h1>', self.page("parol"))
        self.assertIn('<h1 style="--w:8">НА МАКСИМУМ</h1>', self.page("na-maksimum"))
        self.assertEqual(bs.longest_word("ЛАТИНО-ПАНИКА"), 7)       # после дефиса строка переносится
        self.assertEqual(bs.longest_word("ДО УТРА"), 4)

    def test_share_preview_is_jpeg_1200x630(self):
        from PIL import Image
        bs.build(self.root, self.out)
        m = re.search(r'og:image" content="https://thisisnevara.com/(img/og-C6ynOt9dk1M-[0-9a-f]{6}\.jpg)"', self.page("parol"))
        self.assertTrue(m)
        with Image.open(os.path.join(self.out, m.group(1))) as im:
            self.assertEqual((im.format, im.size), ("JPEG", (1200, 630)))

    def test_share_preview_has_alt_text(self):
        self.songs[1]["cover"] = None
        self.write("data/songs.json", json.dumps({"songs": self.songs}, ensure_ascii=False))
        bs.build(self.root, self.out)
        self.assertIn('og:image:alt" content="Обложка песни «Пароль» — Nevara"', self.page("parol"))
        # без обложки превью — общая картинка сайта, и подпись тоже общая
        self.assertIn('og:image:alt" content="Nevara — дуэт на вечерней террасе', self.page("na-maksimum"))

    def test_analytics_only_with_code_file(self):
        self.write("404.html", "<html><head><meta name=robots content=noindex></head><body>404{{ANALYTICS}}</body></html>")
        bs.build(self.root, self.out)
        for name in ("index.html", "parol/index.html", "404.html"):
            with open(os.path.join(self.out, name), encoding="utf-8") as f:
                self.assertNotIn("umami", f.read(), name)
        wid = "1a2b3c4d-1a2b-4a2b-8a2b-1a2b3c4d5e6f"
        self.write("umami.txt", wid.upper() + "\n")
        bs.build(self.root, self.out)
        for name in ("index.html", "parol/index.html", "404.html"):
            with open(os.path.join(self.out, name), encoding="utf-8") as f:
                p = f.read()
            self.assertIn(f'data-website-id="{wid}"', p, name)
            self.assertIn('data-domains="thisisnevara.com"', p, name)
            self.assertIn('"Поделиться"', p, name)
        self.write("umami.txt", 'nevara"><script>')
        with self.assertRaises(bs.BuildError):
            bs.build(self.root, self.out)

    def test_video_data_for_google(self):
        self.songs[0]["uploaded"] = "2026-04-29T15:00:05Z"
        self.songs[0]["duration"] = "PT3M20S"
        self.write("data/songs.json", json.dumps({"songs": self.songs}, ensure_ascii=False))
        bs.build(self.root, self.out)
        p = self.page("parol")
        ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', p, re.S).group(1))
        rec, video = ld["@graph"]
        self.assertEqual(rec["@type"], "MusicRecording")
        self.assertEqual(rec["video"]["@id"], video["@id"])
        self.assertEqual(video["@type"], "VideoObject")
        self.assertEqual(video["uploadDate"], "2026-04-29T15:00:05Z")
        self.assertEqual(video["duration"], "PT3M20S")
        self.assertEqual(video["embedUrl"], "https://www.youtube.com/embed/C6ynOt9dk1M")
        self.assertTrue(video["thumbnailUrl"][0].startswith("https://thisisnevara.com/covers/"))

    def test_pages_for_every_song(self):
        bs.build(self.root, self.out)
        for s in self.songs:
            self.assertNotIn("{{", self.page(s["slug"]))
        p = self.page("parol")
        self.assertIn("<title>Пароль — Nevara · текст песни</title>", p)
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
        self.assertIn("<title>На максимум — Nevara · слушать песню</title>", p)
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
        self.assertIn("<url><loc>https://thisisnevara.com/parol/</loc></url>", sm)
        self.assertNotIn("<lastmod>", sm)
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



REAL_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "song.html")


class SpanishPageTests(unittest.TestCase):
    """Страница испанской песни целиком на испанском — по data/languages.json и настоящему шаблону song.html
    (так проверяется и то, что переводы из шаблона читаются при сборке)."""

    def setUp(self):
        from PIL import Image
        self.root = tempfile.mkdtemp()
        self.out = os.path.join(self.root, "_site")
        for d in ("images", "covers", "data", "lyrics"):
            os.makedirs(os.path.join(self.root, d))
        Image.new("RGB", (1600, 900), "#802040").save(os.path.join(self.root, "images", "hero.jpg"))
        Image.new("RGB", (800, 450), "#a03060").save(os.path.join(self.root, "covers", "qUbkpldW_Z0-800-aaaaaa.webp"))
        self.write("images/hero.json", json.dumps({"couple_left": 570, "couple_right": 1550}))
        self.write("index.html", INDEX)
        shutil.copy(REAL_TEMPLATE, os.path.join(self.root, "song.html"))
        songs = [
            {"id": "qUbkpldW_Z0", "slug": "bamboleo-espanol", "title": "Nevara — BAMBOLEO (Español) | Tú me llenas de energía | Latin Pop 2026",
             "name": "BAMBOLEO (Español)", "sub": ["Tú me llenas de energía", "Latin Pop 2026"], "published": "2026-10-20",
             "views": 20, "description": ["La versión en español de «БАМ-БОЛЕО»."], "cover": "covers/qUbkpldW_Z0-800-aaaaaa.webp"},
            {"id": "hVZ7I8PLAbE", "slug": "bam-boleo", "title": "Nevara — БАМ-БОЛЕО | Закрути меня смелее | Reggaeton 2026",
             "name": "БАМ-БОЛЕО", "sub": ["Закрути меня смелее", "Reggaeton 2026"], "published": "2026-05-05",
             "views": 10, "description": [], "cover": None},
        ]
        self.write("data/songs.json", json.dumps({"songs": songs}, ensure_ascii=False))
        self.write("data/languages.json", json.dumps({"_": "комментарий", "es": ["bamboleo-espanol", "no-such-song"]}))
        self.write("lyrics/bamboleo-espanol.txt", "Bamboleo, bamboleo\nMe llenas de energía\n")

    def tearDown(self):
        shutil.rmtree(self.root)

    def write(self, name, text):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as f:
            f.write(text)

    def page(self, slug):
        with open(os.path.join(self.out, slug, "index.html"), encoding="utf-8") as f:
            return f.read()

    def test_spanish_page_is_spanish(self):
        bs.build(self.root, self.out)
        p = self.page("bamboleo-espanol")
        self.assertNotRegex(p, r"\{\{[A-Z_0-9]+\}\}")       # метки заполнены (в комментарии шаблона «{{…}}» — не метка)
        self.assertIn('<html lang="es">', p)
        self.assertIn("<title>BAMBOLEO (Español) — Nevara · letra de la canción</title>", p)
        self.assertIn('og:locale" content="es_ES"', p)
        self.assertIn('og:image:alt" content="Portada de la canción «BAMBOLEO (Español)» — Nevara"', p)
        self.assertIn('"inLanguage": "es"', p)
        self.assertIn('data-i18n="back">← Todas las canciones<', p)
        self.assertIn('data-i18n="lyrics">Letra<', p)
        self.assertIn('data-i18n="ver-ru">Versión en ruso<', p)
        self.assertIn('aria-label="Compartir"', p)
        self.assertIn('<div class="lyrics" lang="es">', p)
        self.assertIn('data-home="#all-songs" href="../es/#all-songs"', p)      # «Все песни» — на испанскую главную
        self.assertIn('data-home="" href="../es/"', p)
        self.assertIn('data-home="#all-songs" href="../#all-songs"', self.page("bam-boleo"))
        visible = p.split("<body", 1)[1].split("<script>", 1)[0]      # видимая часть, без словаря переводов в скрипте
        for ru in ("Все песни", "Текст песни", "Подписаться", "Ещё песни", "продакшн", "Слушать", "О песне"):
            self.assertNotIn(ru, visible)

    def test_russian_page_stays_russian(self):
        bs.build(self.root, self.out)
        p = self.page("bam-boleo")
        self.assertIn('<html lang="ru">', p)
        self.assertIn('og:locale" content="ru_RU"', p)
        self.assertIn('data-i18n="back">← Все песни<', p)
        self.assertIn('data-i18n="ver-es">Испанская версия<', p)
        self.assertIn('<h1 lang="ru"', p)

    def test_language_versions_linked_without_hreflang(self):
        """Разные записи (своё видео, свой текст): видимая ссылка друг на друга есть, hreflang — нет,
        чтобы в поиске одна песня не подменяла другую."""
        bs.build(self.root, self.out)
        es, ru = self.page("bamboleo-espanol"), self.page("bam-boleo")
        for p in (es, ru):
            self.assertNotIn("hreflang", p)
        self.assertIn('<a href="../bam-boleo/">БАМ-БОЛЕО</a>', es)
        self.assertIn('<a href="../bamboleo-espanol/">BAMBOLEO (Español)</a>', ru)

    def test_without_languages_file_all_russian(self):
        os.remove(os.path.join(self.root, "data", "languages.json"))
        bs.build(self.root, self.out)
        self.assertIn('<html lang="ru">', self.page("bamboleo-espanol"))
        self.assertNotIn('hreflang', self.page("bam-boleo"))

    def test_broken_languages_file_stops_build(self):
        self.write("data/languages.json", "{oops")
        with self.assertRaises(bs.BuildError):
            bs.build(self.root, self.out)


REAL_INDEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "index.html")


class HomeLanguageTests(unittest.TestCase):
    """Главные /es/ и /en/ собираются из настоящего index.html: перевод сразу в HTML, свои адреса и метки."""

    def setUp(self):
        from PIL import Image
        self.root = tempfile.mkdtemp()
        self.out = os.path.join(self.root, "_site")
        os.makedirs(os.path.join(self.root, "images"))
        Image.new("RGB", (1600, 900), "#802040").save(os.path.join(self.root, "images", "hero.jpg"))
        with open(os.path.join(self.root, "images", "hero.json"), "w", encoding="utf-8") as f:
            json.dump({"couple_left": 570, "couple_right": 1550}, f)
        shutil.copy(REAL_INDEX, os.path.join(self.root, "index.html"))
        bs.build(self.root, self.out)

    def tearDown(self):
        shutil.rmtree(self.root)

    def built(self, name):
        with open(os.path.join(self.out, name), encoding="utf-8") as f:
            return f.read()

    def test_three_home_pages_with_own_addresses(self):
        for lang, url in (("ru", "https://thisisnevara.com/"), ("es", "https://thisisnevara.com/es/"),
                          ("en", "https://thisisnevara.com/en/")):
            with self.subTest(lang=lang):
                p = self.built("index.html" if lang == "ru" else f"{lang}/index.html")
                self.assertNotRegex(p, r"\{\{[A-Z_0-9]+\}\}")
                self.assertIn(f'<html lang="{lang}">', p)
                self.assertIn(f'<link rel="canonical" href="{url}">', p)
                self.assertIn(f'<meta property="og:url" content="{url}">', p)
                # взаимные hreflang на всех трёх
                for l, u in (("ru", ""), ("es", "es/"), ("en", "en/"), ("x-default", "")):
                    self.assertIn(f'<link rel="alternate" hreflang="{l}" href="https://thisisnevara.com/{u}">', p)
                self.assertIn(f'data-lang="{lang}" aria-current="page"', p)
                self.assertEqual(len(re.findall(r'<a [^>]*aria-current="page"', p)), 1)   # отмечен только текущий язык

    def test_spanish_home_is_translated_in_html(self):
        p = self.built("es/index.html")
        self.assertIn("<title>Nevara — Latin Pop y Reggaeton en ruso y español</title>", p)
        self.assertIn('og:locale" content="es_ES"', p)
        self.assertIn('data-i18n="tagline">Dos voces, un ritmo.', p)
        self.assertIn('aria-label="Género" data-i18n-label="genre"', p)
        self.assertIn('aria-label="Arriba" data-i18n-label="to-top"', p)
        visible = re.sub(r'<[^>]*\blang="ru"[^>]*>[^<]*', "", p.split("<body", 1)[1].split("<script>", 1)[0])
        for ru in ("Слушать", "Подписаться", "Смотреть", "Песни Nevara", "Все песни", "О проекте", "Популярные", "Наверх"):
            self.assertNotIn(ru, visible)

    def test_home_remembers_its_language_on_open(self):
        """Открыл /es/ (например, из Google) — язык запомнен, и кнопки на страницах песен будут испанскими."""
        p = self.built("es/index.html")
        self.assertIn('try{localStorage.setItem("nevara-lang",document.documentElement.lang)}catch(e){}', p)
        self.assertIn('<html lang="es">', p)

    def test_catalog_keeps_every_song_in_html(self):
        """Список «Все песни» показывает сначала последние песни, но в HTML остаются все — со ссылками
        (скрывает часть только скрипт), а надписи кнопки есть на всех трёх языках."""
        with open(os.path.join(os.path.dirname(REAL_INDEX), "data", "songs.json"), encoding="utf-8") as f:
            total = len(json.load(f)["songs"])
        for name, lang in (("index.html", "ru"), ("es/index.html", "es"), ("en/index.html", "en")):
            with self.subTest(lang=lang):
                p = self.built(name)
                catalog = p.split('<ol class="catalog-list">', 1)[1].split("</ol>", 1)[0]
                self.assertEqual(catalog.count("<li "), total)
                self.assertNotIn("c-extra", catalog)                  # без скрипта ничего не скрыто
                strings = bs.ui_strings(p, lang, "index.html")
                self.assertIn("{n}", strings["show-all"])
                self.assertIn("{n}", strings["show-less"])

    def test_english_home_title(self):
        p = self.built("en/index.html")
        self.assertIn("<title>Nevara — Latin Pop and Reggaeton in Russian and Spanish</title>", p)
        self.assertIn('data-i18n="h-about">About<', p)

    def test_relative_links_point_one_level_up(self):
        p = self.built("es/index.html")
        self.assertIn('href="../favicon.svg"', p)
        self.assertIn('src="../img/hero-', p)
        self.assertRegex(p, r'src="\.\./covers/[\w-]+\.webp"')
        self.assertRegex(p, r'<a class="release" href="\.\./[a-z0-9-]+/"')
        self.assertIn('href="#songs"', p)                                    # якорь на этой же странице
        self.assertIn('href="https://www.youtube.com/@thisisnevara"', p)     # полные адреса не тронуты
        self.assertNotRegex(p, r'\b(?:src|href)="(?!https?:|/|#|\.\./)')

    def test_song_names_marked_with_language(self):
        p = self.built("index.html")
        self.assertRegex(p, r'<span class="c-name" lang="ru">ПАРОЛЬ</span>')
        self.assertRegex(p, r'<span class="c-name" lang="es">VEN PARA MÍ</span>')

    def test_sitemap_lists_language_homes(self):
        sm = self.built("sitemap.xml")
        for u in ("https://thisisnevara.com/", "https://thisisnevara.com/es/", "https://thisisnevara.com/en/"):
            self.assertIn(f"<loc>{u}</loc>", sm)


class LinkCheckTests(unittest.TestCase):
    def setUp(self):
        self.site = tempfile.mkdtemp()
        for d in ("song", "fonts", "img"):
            os.makedirs(os.path.join(self.site, d))
        self.write("img/a.webp", "x")
        self.write("fonts/f.woff2", "x")
        self.write("fonts/fonts.css", "@font-face{src:url(f.woff2)} @font-face{src:url('missing.woff2')}")
        self.write("index.html", '<html><body><div id="all-songs"></div>'
                                 '<a href="song/">ok</a><a href="#all-songs">ok</a><a href="https://x.com/">ext</a>'
                                 '<img src="img/a.webp" srcset="img/a.webp 800w, img/b.webp 1600w">'
                                 '<a href="nope/">bad</a><a href="#nowhere">bad</a>'
                                 '<script>a.href="missing-in-script.html"</script></body></html>')
        self.write("song/index.html", '<a href="../#all-songs">ok</a><a href="/fonts/fonts.css">ok</a>'
                                      '<a href="../#gone">bad</a><a href="../../outside.html">bad</a>')

    def tearDown(self):
        shutil.rmtree(self.site)

    def write(self, name, text):
        with open(os.path.join(self.site, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_finds_every_kind_of_broken_link(self):
        problems = bs.check_links(self.site)
        self.assertEqual(problems, sorted([
            "fonts/fonts.css: missing.woff2 — файла нет",
            "index.html: img/b.webp — файла нет",
            "index.html: nope/ — файла нет",
            "index.html: #nowhere — на странице нет места #nowhere",
            "song/index.html: ../#gone — на странице нет места #gone",
            "song/index.html: ../../outside.html — ведёт за пределы сайта",
        ]))

    def test_real_site_has_no_broken_links(self):
        """Весь настоящий сайт из этого репозитория собирается без единой битой ссылки."""
        out = os.path.join(self.site, "_real")
        bs.build(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), out, check=True)
        self.assertTrue(os.path.isfile(os.path.join(out, "fonts", "fonts.css")))


class MissingLyricsReportTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.summary = os.path.join(self.dir, "summary.md")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_warnings_and_full_list_in_actions(self):
        missing = [(f"ПЕСНЯ {i}", f"lyrics/pesnya-{i}.txt") for i in range(1, 13)]   # больше 10 — список полный
        with contextlib.redirect_stdout(io.StringIO()):
            lines = bs.report_missing_lyrics(missing, {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": self.summary})
        self.assertEqual(lines[0], "::warning title=Нет текста песни::У песни «ПЕСНЯ 1» нет текста: lyrics/pesnya-1.txt")
        self.assertEqual(len(lines), 12)
        with open(self.summary, encoding="utf-8") as f:
            s = f.read()
        self.assertIn("Песни без текста (12)", s)
        self.assertIn("- «ПЕСНЯ 12» — добавьте файл `lyrics/pesnya-12.txt`", s)

    def test_nothing_missing_nothing_written(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bs.report_missing_lyrics([], {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": self.summary}), [])
        self.assertFalse(os.path.exists(self.summary))

    def test_plain_text_outside_actions(self):
        with contextlib.redirect_stdout(io.StringIO()):
            lines = bs.report_missing_lyrics([("НОВАЯ ПЕСНЯ", "lyrics/novaya-pesnya.txt")], {})
        self.assertEqual(lines, ["ВНИМАНИЕ: У песни «НОВАЯ ПЕСНЯ» нет текста: lyrics/novaya-pesnya.txt"])


class LocalizeHelpersTests(unittest.TestCase):
    def test_one_level_down(self):
        page = ('<a href="./">x</a><a href="es/">x</a><a href="#top">x</a><a href="/abs">x</a>'
                '<img src="img/a.webp" srcset="img/a-800.webp 800w, img/a-1600.webp 1600w">'
                '<a href="https://x.com/y">x</a><a href="../z/">x</a>')
        out = bs.one_level_down(page)
        self.assertIn('<a href="../">', out)
        self.assertIn('<a href="../es/">', out)
        self.assertIn('<a href="#top">', out)
        self.assertIn('<a href="/abs">', out)
        self.assertIn('srcset="../img/a-800.webp 800w, ../img/a-1600.webp 1600w"', out)
        self.assertIn('src="../img/a.webp"', out)
        self.assertIn('href="https://x.com/y"', out)
        self.assertIn('href="../z/"', out)


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



class OthersTests(unittest.TestCase):
    def setUp(self):
        import build_site as b
        self.b = b
        g = ["Latin Pop 2026", "Reggaeton 2026", "Latin Pop 2026", "Reggaeton 2026", "Latin Pop 2026",
             "Reggaeton 2026", "Latin Pop 2026", "Reggaeton 2026", "Latin Pop 2026", "Reggaeton 2026"]
        self.songs = [{"slug": f"s{n}", "published": f"2026-01-{n + 10:02d}", "views": n * 7 % 10,
                       "sub": ["строка", g[n]]} for n in range(10)]

    def test_neighbours_genre_popular_no_self_no_dupes(self):
        s = self.songs[5]
        picks = [o["slug"] for o in self.b.pick_others(s, self.songs)]
        self.assertEqual(len(picks), 6)
        self.assertEqual(len(set(picks)), 6)
        self.assertNotIn("s5", picks)
        self.assertEqual(picks[:2], ["s6", "s4"])                          # соседние релизы
        self.assertEqual(picks[2:4], ["s7", "s9"])                         # тот же жанр, по кругу после s5

    def test_sets_differ_between_pages_and_exclude_works(self):
        sets = {tuple(o["slug"] for o in self.b.pick_others(s, self.songs)) for s in self.songs}
        self.assertGreater(len(sets), 5)
        picks = [o["slug"] for o in self.b.pick_others(self.songs[5], self.songs, exclude={"s6"})]
        self.assertNotIn("s6", picks)
        self.assertEqual(len(picks), 6)



class StanzaLangTests(unittest.TestCase):
    def test_only_latin_stanzas_marked_spanish(self):
        import build_site as b
        self.assertEqual(b.stanza_lang(["Ven para mí", "esta noche"]), ' lang="es"')
        self.assertEqual(b.stanza_lang(["Этой ночью не уходи"]), "")
        self.assertEqual(b.stanza_lang(["Ven para mí", "Этой ночью не уходи"]), "")
        self.assertEqual(b.stanza_lang(["(oh-oh)"]), ' lang="es"')

    def test_mixed_stanza_marks_spanish_lines(self):
        import build_site as b
        self.assertEqual(b.render_stanza(["Сердце стучит", "Ven para mí"]),
                         '<p>Сердце стучит<br><span lang="es">Ven para mí</span></p>')
        self.assertEqual(b.render_stanza(["Ven, ven para mí", "esta noche"]),
                         '<p lang="es">Ven, ven para mí<br>esta noche</p>')



class TitleLineTests(unittest.TestCase):
    def test_sung_first_line_with_song_name_is_kept(self):
        import build_site as b
        st = b.clean_lyrics("Ven para mí…\nТы слышишь, как сердце\nТянется к тебе?\n", "VEN PARA MÍ")
        self.assertEqual(st[0][0], "Ven para mí…")

    def test_heading_line_is_removed(self):
        import build_site as b
        self.assertEqual(b.clean_lyrics("Два мира:\n\nПервая строка\n", "ДВА МИРА"), [["Первая строка"]])
        # без двоеточия — это строка песни, даже если после неё пустая строка
        self.assertEqual(b.clean_lyrics("Два мира\n\nПервая строка\n", "ДВА МИРА"), [["Два мира"], ["Первая строка"]])
        self.assertEqual(b.clean_lyrics("Пароль:\nПервая строка\n", "ПАРОЛЬ"), [["Первая строка"]])



class NiceNameTests(unittest.TestCase):
    def test_capital_after_separators(self):
        import build_site as b
        self.assertEqual(b.nice_name("НОЯБРЬ НА ДВОИХ"), "Ноябрь на двоих")
        self.assertEqual(b.nice_name("МАНИЯ — ИСПАНИЯ"), "Мания — Испания")
        self.assertEqual(b.nice_name("MI COMETA / МОЯ КОМЕТА"), "Mi cometa / Моя комета")
        self.assertEqual(b.nice_name("БАМ-БОЛЕО"), "Бам-болео")
        self.assertEqual(b.nice_name("BAMBOLEO (Español)"), "BAMBOLEO (Español)")


if __name__ == "__main__":
    unittest.main()
