#!/usr/bin/env python3
"""Собирает готовый сайт в папку _site.

Берёт фото фона из images/hero.jpg и положение пары из images/hero.json и делает:
  - версии фона для телефона и компьютера (WebP 800/1200/1600 px);
  - картинку превью для Telegram/WhatsApp (nevara-og.jpg, 1200×630);
  - index.html с подставленными адресами картинок.
В имена файлов добавляется короткая метка содержимого: после замены фото
браузеры и мессенджеры сразу получают новую картинку, а не старую из кэша.

Если что-то не так (нет фото, ошибка в hero.json, осталась незаполненная метка),
скрипт завершается с ошибкой и _site не публикуется — на сайте остаётся прошлая версия.

Запуск: python scripts/build_site.py [папка проекта] [папка результата]
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys

SITE_URL = "https://thisisnevara.com/"
WIDTHS = (800, 1200, 1600)
COPY = ("favicon.svg", "CNAME")
COPY_DIRS = ("covers",)
PLACEHOLDER = re.compile(r"\{\{[A-Z_0-9]+\}\}")


class BuildError(Exception):
    pass


def load_settings(root):
    path = os.path.join(root, "images", "hero.json")
    try:
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
        left, right = float(s["couple_left"]), float(s["couple_right"])
    except FileNotFoundError:
        raise BuildError("нет файла images/hero.json")
    except (ValueError, KeyError, TypeError) as e:
        raise BuildError(f"ошибка в images/hero.json: {e}")
    if not (0 <= left < right <= 1600):
        raise BuildError("в images/hero.json должно быть 0 ≤ couple_left < couple_right ≤ 1600")
    og_top = float(s.get("og_top", 0.0))
    return left, right, og_top


def build(root, out):
    from PIL import Image

    src = os.path.join(root, "images", "hero.jpg")
    if not os.path.exists(src):
        raise BuildError("нет файла images/hero.jpg")
    try:
        hero = Image.open(src).convert("RGB")
    except Exception as e:
        raise BuildError(f"не удалось открыть images/hero.jpg: {e}")
    w, h = hero.size
    if abs(w / h - 16 / 9) > 0.02:
        raise BuildError(f"фото должно быть 16:9 (сейчас {w}×{h})")
    left, right, og_top = load_settings(root)

    with open(os.path.join(root, "index.html"), encoding="utf-8") as f:
        page = f.read()

    tmp = out + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.join(tmp, "img"))

    tag = hashlib.sha256(open(src, "rb").read() + json.dumps([left, right, og_top]).encode()).hexdigest()[:8]

    # Фон: версии под разную ширину экрана (не больше исходника)
    variants = []
    for width in WIDTHS:
        if width > w:
            continue
        im = hero if width == w else hero.resize((width, round(width * 9 / 16)), Image.LANCZOS)
        name = f"img/hero-{tag}-{width}.webp"
        im.save(os.path.join(tmp, name), "WEBP", quality=74, method=6)
        variants.append((width, name))
    if not variants:
        raise BuildError(f"фото слишком маленькое ({w}×{h}), нужно хотя бы 800 px по ширине")
    srcset = ", ".join(f"{name} {width}w" for width, name in variants)
    largest = variants[-1][1]

    # Превью для мессенджеров: 1200×630 из полосы фото (og_top — сдвиг полосы вниз, 0…1)
    base = hero.resize((1600, 900), Image.LANCZOS)
    top = round(max(0.0, min(1.0, og_top)) * (900 - 840))
    og = base.crop((0, top, 1600, top + 840)).resize((1200, 630), Image.LANCZOS)
    og.save(os.path.join(tmp, "nevara-og.jpg"), "JPEG", quality=84, optimize=True, progressive=True)

    # Телефон: пара по ширине экрана и по центру
    photo_w = 1600 / (right - left) * 100
    photo_c = (left + right) / 2 / 1600
    sizes = f"(max-width: 480px) {photo_w:.0f}vw, 100vw"   # подсказка браузеру, какую версию брать

    values = {
        "{{HERO_SRC}}": largest,
        "{{HERO_SRCSET}}": srcset,
        "{{HERO_SIZES}}": sizes,
        "{{HERO_PRELOAD}}": (f'<link rel="preload" as="image" href="{largest}" imagesrcset="{srcset}" '
                             f'imagesizes="{sizes}" fetchpriority="high">'),
        "{{OG_IMAGE}}": f"{SITE_URL}nevara-og.jpg?v={tag}",
        "{{PHOTO_W}}": f"{photo_w:.1f}vw",
        "{{PHOTO_C}}": f"{photo_c:.4f}",
    }
    for key, val in values.items():
        page = page.replace(key, val)
    left_over = sorted(set(PLACEHOLDER.findall(page)))
    if left_over:
        raise BuildError(f"в index.html остались незаполненные метки: {', '.join(left_over)}")
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)

    for name in COPY:
        p = os.path.join(root, name)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(tmp, name))
    for d in COPY_DIRS:
        p = os.path.join(root, d)
        if os.path.isdir(p):
            shutil.copytree(p, os.path.join(tmp, d))

    # Готово: подменяем результат целиком
    shutil.rmtree(out, ignore_errors=True)
    os.replace(tmp, out)
    print(f"Фон: {', '.join(n for _, n in variants)}; превью nevara-og.jpg?v={tag}; "
          f"телефон: --photo-w {photo_w:.1f}vw, --photo-c {photo_c:.4f}")
    return out


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(root, "_site")
    try:
        build(root, out)
    except BuildError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)
