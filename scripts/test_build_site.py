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
        self.assertEqual(Image.open(os.path.join(self.out, "nevara-og.jpg")).size, (1200, 630))
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


if __name__ == "__main__":
    unittest.main()
