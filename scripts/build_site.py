#!/usr/bin/env python3
"""Собирает готовый сайт в папку _site.

Берёт фото фона из images/hero.jpg и положение пары из images/hero.json и делает:
  - версии фона для телефона и компьютера (WebP 800/1200/1600 px);
  - картинку превью для Telegram/WhatsApp (nevara-og.jpg, 1200×630);
  - index.html с подставленными адресами картинок;
  - страницу для каждой открытой песни (по data/songs.json и шаблону song.html):
    адрес /<имя-песни>/, плеер, описание из YouTube, текст из lyrics/<имя-песни>.txt
    (служебные пометки в квадратных скобках убираются), размытая обложка на фоне;
  - sitemap.xml и robots.txt для поисковиков.
В имена файлов добавляется короткая метка содержимого: после замены фото
браузеры и мессенджеры сразу получают новую картинку, а не старую из кэша.

Если что-то не так (нет фото, ошибка в hero.json, осталась незаполненная метка),
скрипт завершается с ошибкой и _site не публикуется — на сайте остаётся прошлая версия.

Запуск: python scripts/build_site.py [папка проекта] [папка результата]
"""
import hashlib
import html
import io
from html.parser import HTMLParser
import json
import os
import re
import shutil
import sys

SITE_URL = "https://thisisnevara.com/"
WIDTHS = (800, 1200, 1600)
COPY = ("favicon.svg", "apple-touch-icon.png", "CNAME")   # apple-touch-icon — PNG 180×180 для iPhone: SVG iOS не берёт
UMAMI = "umami.txt"      # Website ID из Umami Cloud (вида 1a2b3c4d-…); нет файла — нет счётчика

# Нажатия, которые считаем в Umami: «Поделиться», переходы на YouTube и подписка
# («Слушать» отправляет сам плеер на странице, когда песня запущена).
# Ссылкам ставим data-umami-event (Umami сам дождётся отправки перед переходом),
# кнопкам «Слушать»/«Поделиться» — umami.track() при нажатии.
UMAMI_EVENTS = """<script>
(function(){
  function song(el){
    var r=el.closest(".new-release");if(r){var h3=r.querySelector("h3");if(h3)return h3.textContent.trim()}
    if(location.pathname!=="/"){var h1=document.querySelector("h1");if(h1)return h1.textContent.trim()}
    return "";
  }
  function mark(a,name){a.setAttribute("data-umami-event",name);var s=song(a);if(s)a.setAttribute("data-umami-event-song",s)}
  document.querySelectorAll('a[href*="youtube.com/"]').forEach(function(a){
    var h=a.getAttribute("href");
    if(h.indexOf("sub_confirmation")>-1)mark(a,"Подписка");
    else if(h.indexOf("/watch")>-1)mark(a,"YouTube: песня");
    else mark(a,"YouTube: канал");
  });
  document.addEventListener("click",function(e){
    var b=e.target.closest(".share");if(!b||!window.umami)return;
    var s=song(b);
    try{umami.track("Поделиться",s?{song:s}:undefined)}catch(err){}
  },true);
})();
</script>"""


def analytics_tag(root):
    """Счётчик посещений Umami Cloud: без cookie, статистику видит только владелец аккаунта.
    Считает только на настоящем домене — просмотры при локальной проверке не попадут в статистику."""
    p = os.path.join(root, UMAMI)
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8") as f:
        code = f.read().strip().lower()
    if not code:
        return ""
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", code):
        raise BuildError(f"{UMAMI}: нужен Website ID из Umami вида 1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f, а не {code!r}")
    return (f'<script defer src="https://cloud.umami.is/script.js" data-website-id="{code}" '
            f'data-domains="thisisnevara.com"></script>\n' + UMAMI_EVENTS)
COPY_DIRS = ("covers", "fonts")
PLACEHOLDER = re.compile(r"\{\{[A-Z_0-9]+\}\}")


class BuildError(Exception):
    pass


def esc(s):
    return html.escape(str(s), quote=True)


# ---------- тексты песен ----------
TAG_LINE = re.compile(r"^\s*[\[{][^\]}]*[\]}]\s*$")    # [Verse 1: Male …] — строки в (круглых) скобках остаются: это бэк-вокал
LABEL_LINE = re.compile(r"^\s*[\[{]?[A-Za-z][A-Za-z0-9 +&,:/'().-]*[\]}]\s*$")  # Female Solo}  /  Male Solo]
INLINE_TAG = re.compile(r"\s*\[[^\]]*\]\s*")


def _norm(s):
    return re.sub(r"[^0-9a-zа-яё]+", "", s.lower())


def clean_lyrics(text, name=""):
    """Оставляет только строки песни: без пометок в [скобках], без строки-заголовка с названием.
    Возвращает список куплетов (каждый — список строк)."""
    lines = []
    for raw in text.replace("\r", "").split("\n"):
        if TAG_LINE.match(raw) or LABEL_LINE.match(raw):
            continue
        line = INLINE_TAG.sub(" ", raw).strip()
        lines.append(line)
    # Убираем только явно обозначенный заголовок: название песни с двоеточием в первой строке («Пароль:»).
    # Строка с названием без двоеточия — это пропетая строка («Ven para mí…», «Бам-болео…»), её не трогаем.
    while lines and not lines[0]:
        lines.pop(0)
    if lines and name and lines[0].endswith(":") and _norm(lines[0].rstrip(":")) == _norm(name):
        lines.pop(0)
    stanzas, cur = [], []
    for line in lines + [""]:
        if line:
            cur.append(line)
        elif cur:
            stanzas.append(cur)
            cur = []
    return stanzas


def longest_word(name):
    """Длина самого длинного слова в названии (после дефиса можно перенести строку), не меньше 4."""
    return max(4, max((len(w) for w in re.split(r"\s+|(?<=-)", name) if w), default=4))


def nice_name(name):
    """«НОЯБРЬ НА ДВОИХ» -> «Ноябрь на двоих» (для заголовка вкладки и поиска)."""
    if not name.isupper():
        return name
    # заглавная буква — в начале и после « — » и « / »: «Мания — Испания», «Mi cometa / Моя комета»
    parts = re.split(r"( [—–/] )", name.lower())
    return "".join(p[:1].upper() + p[1:] if i % 2 == 0 else p for i, p in enumerate(parts))


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


def build(root, out, check=False, missing_lyrics=None):
    """check=True — проверить ссылки готового сайта (при публикации); при битых ссылках сайт не публикуется.
    missing_lyrics — список, куда добавляются песни без текста (для предупреждения в Actions)."""
    from PIL import Image

    src = os.path.join(root, "images", "hero.jpg")
    if not os.path.exists(src):
        raise BuildError("нет файла images/hero.jpg")
    try:
        with Image.open(src) as im:
            hero = im.convert("RGB")
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

    with open(src, "rb") as f:
        tag = hashlib.sha256(f.read() + json.dumps([left, right, og_top]).encode()).hexdigest()[:8]

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
        "{{ANALYTICS}}": analytics_tag(root),
        "{{HREFLANG}}": hreflang_links(),
    }
    for key, val in values.items():
        page = page.replace(key, val)
    left_over = sorted(set(PLACEHOLDER.findall(page)))
    if left_over:
        raise BuildError(f"в index.html остались незаполненные метки: {', '.join(left_over)}")
    page = mark_song_langs(page)
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)

    # Главные на других языках: /es/, /en/ — из той же страницы, с переводом из её словаря T
    home_pages = []
    if all(re.search(r"^\s*" + l + r":\{", page, re.M) for l in HOME_LANGS):
        for lang in HOME_LANGS:
            localized = localize_home(page, lang, ui_strings(page, lang, "index.html"))
            os.makedirs(os.path.join(tmp, lang))
            with open(os.path.join(tmp, lang, "index.html"), "w", encoding="utf-8") as f:
                f.write(localized)
            home_pages.append(f"{lang}/")
    else:
        print("Главные /es/ и /en/ пропущены: в index.html нет словаря переводов")

    pages = home_pages + build_songs(root, tmp, og_fallback=f"{SITE_URL}nevara-og.jpg?v={tag}",
                                     analytics=values["{{ANALYTICS}}"], missing_lyrics=missing_lyrics)

    # Своя страница «не найдено» (GitHub Pages сам отдаёт её с кодом 404)
    p404 = os.path.join(root, "404.html")
    if os.path.exists(p404):
        with open(p404, encoding="utf-8") as f:
            nf = f.read().replace("{{ANALYTICS}}", values["{{ANALYTICS}}"])
        if PLACEHOLDER.search(nf):
            raise BuildError("в 404.html остались незаполненные метки")
        with open(os.path.join(tmp, "404.html"), "w", encoding="utf-8") as f:
            f.write(nf)
    write_sitemap(tmp, pages)

    for name in COPY:
        p = os.path.join(root, name)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(tmp, name))
    for d in COPY_DIRS:
        p = os.path.join(root, d)
        if os.path.isdir(p):
            shutil.copytree(p, os.path.join(tmp, d))

    # Битые ссылки и картинки — сайт не публикуем, на нём остаётся прошлая рабочая версия
    if check:
        problems = check_links(tmp)
        if problems:
            shutil.rmtree(tmp, ignore_errors=True)
            more = f"\n  … и ещё {len(problems) - 20}" if len(problems) > 20 else ""
            raise BuildError("битые ссылки на сайте:\n  " + "\n  ".join(problems[:20]) + more)
        print("Ссылки проверены: битых нет")

    # Готово: подменяем результат целиком
    shutil.rmtree(out, ignore_errors=True)
    os.replace(tmp, out)
    print(f"Фон: {', '.join(n for _, n in variants)}; превью nevara-og.jpg?v={tag}; "
          f"телефон: --photo-w {photo_w:.1f}vw, --photo-c {photo_c:.4f}; страниц песен: {len(pages) - len(home_pages)}; "
          f"главные на других языках: {', '.join('/' + p for p in home_pages) or 'нет'}")
    return out


YEAR_TAIL = re.compile(r"\s+(?:19|20)\d{2}$")
VERSION_LABEL = {"ru": "Русская версия", "es": "Испанская версия"}

# Язык страницы песни. По умолчанию русский; песни из data/languages.json получают страницу
# целиком на этом языке: подписи, заголовок, описание для поиска, <html lang>.
LANGS_FILE = os.path.join("data", "languages.json")
OG_LOCALE = {"ru": "ru_RU", "es": "es_ES", "en": "en_US"}
HERO_ALT = {"ru": "Nevara — дуэт на вечерней террасе с видом на ночной город",
            "es": "Nevara — dúo en una terraza al anochecer con vistas a la ciudad",
            "en": "Nevara — a duo on an evening terrace overlooking the city at night"}
HOME_LANGS = ("es", "en")          # кроме русской главной (/) собираются /es/ и /en/


def load_page_langs(root, slugs):
    """{адрес песни: язык} из data/languages.json. Опечатка в адресе — предупреждение, а не ошибка:
    из-за неё не стоит оставлять сайт без обновления."""
    path = os.path.join(root, LANGS_FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        raise BuildError(f"ошибка в {LANGS_FILE}: {e}")
    langs = {}
    for lang, items in data.items():
        if lang.startswith("_"):                  # «_» — комментарий для людей
            continue
        if lang not in OG_LOCALE or lang == "ru" or not isinstance(items, list):
            raise BuildError(f"{LANGS_FILE}: неизвестный язык {lang!r} (можно: es, en)")
        for slug in items:
            if slug in slugs:
                langs[slug] = lang
            else:
                print(f"ВНИМАНИЕ: {LANGS_FILE}: песни с адресом {slug!r} нет — пропускаю")
    return langs


def ui_strings(template, lang, source="song.html"):
    """Подписи интерфейса на нужном языке — из словаря T в самом шаблоне (song.html, index.html),
    чтобы переводы хранились в одном месте и на странице, и при сборке."""
    m = re.search(r"^\s*" + re.escape(lang) + r":\{(.*)\}\s*,?\s*$", template, re.M)
    if not m:
        raise BuildError(f"в {source} нет переводов для языка {lang!r}")
    pairs = re.findall(r'(?:"([\w-]+)"|(\w+)):"((?:[^"\\]|\\.)*)"', m.group(1))
    return {a or b: json.loads(f'"{v}"') for a, b, v in pairs}


I18N_TEXT = re.compile(r'(data-i18n="([\w-]+)"[^>]*>)([^<]*)(?=<)')


def translate_ui(page, strings):
    """Подставляет переводы в элементы с data-i18n прямо в HTML: поисковик и посетитель
    без JavaScript сразу видят страницу на её языке."""
    def sub(m):
        v = strings.get(m.group(2))
        return m.group(1) + (esc(v) if v is not None else m.group(3))
    page = I18N_TEXT.sub(sub, page)
    if "share" in strings:
        page = page.replace('aria-label="Поделиться"', f'aria-label="{esc(strings["share"])}"')
    # подписи для экранного диктора: aria-label="…" data-i18n-label="ключ"
    page = re.sub(r'aria-label="[^"]*"(\s+data-i18n-label="([\w-]+)")',
                  lambda m: f'aria-label="{esc(strings[m.group(2)])}"{m.group(1)}' if m.group(2) in strings else m.group(0),
                  page)
    return page


# ---------- главные страницы на других языках ----------
def hreflang_links():
    """Взаимные ссылки трёх главных: поисковик понимает, что это один сайт на разных языках."""
    links = [f'<link rel="alternate" hreflang="ru" href="{SITE_URL}">']
    links += [f'<link rel="alternate" hreflang="{l}" href="{SITE_URL}{l}/">' for l in HOME_LANGS]
    links.append(f'<link rel="alternate" hreflang="x-default" href="{SITE_URL}">')
    return "\n".join(links)


URL_ATTR = re.compile(r'\b(src|href|srcset|imagesrcset)="([^"]*)"')


def one_level_down(page):
    """Страница лежит на уровень глубже (/es/): относительные адреса картинок и ссылок получают «../».
    Полные адреса (https://…), абсолютные (/…) и якоря (#…) не меняются."""
    def fix(u):
        if not u or re.match(r"(?:[a-z][a-z0-9+.-]*:|/|#|\.\./)", u, re.I):
            return u
        return "../" if u == "./" else "../" + u

    def sub(m):
        attr, val = m.group(1), m.group(2)
        if attr.endswith("srcset"):
            val = ", ".join(" ".join([fix(p.split()[0])] + p.split()[1:]) for p in val.split(",") if p.strip())
        else:
            val = fix(val)
        return f'{attr}="{val}"'
    return URL_ATTR.sub(sub, page)


def text_lang(text):
    """Язык строки песни: кириллица — русский, латиница — испанский (названия песен Nevara)."""
    if re.search("[а-яё]", text, re.I):
        return "ru"
    return "es" if re.search("[a-zñáéíóúü]", text, re.I) else ""


SONG_TEXT = re.compile(r'(<(?:span class="c-name"|span class="c-sub"|h3|p class="sub"|b))(>)([^<]*)')
AUTO_BLOCK = re.compile(r"(<!-- AUTO:RELEASES:START.*?<!-- AUTO:RELEASES:END -->)", re.S)


def mark_song_langs(page):
    """Названия и строки песен на главной помечаются своим языком (lang="ru"/"es"):
    экранный диктор читает их правильно и на испанской, и на английской главной."""
    def mark(m):
        lang = text_lang(m.group(3))
        return f'{m.group(1)} lang="{lang}"{m.group(2)}{m.group(3)}' if lang else m.group(0)
    return AUTO_BLOCK.sub(lambda b: SONG_TEXT.sub(mark, b.group(1)), page)


def localize_home(page, lang, strings):
    """Русская главная -> главная на другом языке: переведённый текст сразу в HTML,
    свой <title>, описание, canonical, og:locale и адреса на уровень глубже."""
    url = f"{SITE_URL}{lang}/"
    page = translate_ui(page, strings)
    page = page.replace('<html lang="ru">', f'<html lang="{lang}">', 1)
    for pattern, value in (
        (r'(<title>)[^<]*(</title>)', strings.get("doc-title")),
        (r'(<meta name="description" content=")[^"]*(")', strings.get("meta-desc")),
        (r'(<meta property="og:title" content=")[^"]*(")', strings.get("og-title")),
        (r'(<meta property="og:description" content=")[^"]*(")', strings.get("og-desc")),
        (r'(<meta property="og:url" content=")[^"]*(")', url),
        (r'(<link rel="canonical" href=")[^"]*(")', url),
        (r'(<meta property="og:image:alt" content=")[^"]*(")', HERO_ALT[lang]),
        (r'(<meta name="twitter:image:alt" content=")[^"]*(")', HERO_ALT[lang]),
    ):
        if not value:
            raise BuildError(f"index.html: нет перевода для {pattern!r} ({lang})")
        page, n = re.subn(pattern, lambda m: m.group(1) + esc(value) + m.group(2), page, count=1)
        if n != 1:
            raise BuildError(f"index.html: не найдено место для {pattern!r}")
    locales = [OG_LOCALE[lang]] + [OG_LOCALE[l] for l in OG_LOCALE if l != lang]
    block = "\n".join([f'<meta property="og:locale" content="{locales[0]}">'] +
                      [f'<meta property="og:locale:alternate" content="{l}">' for l in locales[1:]])
    page = re.sub(r'<meta property="og:locale" content="[^"]*">(?:\n<meta property="og:locale:alternate" content="[^"]*">)*',
                  lambda m: block, page, count=1)
    page = one_level_down(page)
    page = page.replace(' aria-current="page"', "")
    page = page.replace(f'data-lang="{lang}"', f'data-lang="{lang}" aria-current="page"', 1)
    return page


def version_key(slug):
    """Общий ключ для языковых версий: bam-boleo и bamboleo-espanol -> bamboleo."""
    return re.sub(r"-(?:espanol|spanish|russkiy|russian|ru|es)$", "", slug).replace("-", "")


def is_spanish(text):
    """Строка/куплет на латинице без кириллицы — считаем испанским (страница по умолчанию русская)."""
    return bool(re.search("[a-zñáéíóúü]", text, re.I)) and not re.search("[а-яё]", text, re.I)


def stanza_lang(lines):
    return ' lang="es"' if is_spanish(" ".join(lines)) else ""


def render_stanza(lines):
    """Куплет целиком на испанском -> <p lang="es">; в смешанном помечаем испанские строки по одной
    (например, припев «Ven para mí» внутри русского куплета)."""
    if stanza_lang(lines):
        return '<p lang="es">' + "<br>".join(esc(l) for l in lines) + "</p>"
    return "<p>" + "<br>".join(f'<span lang="es">{esc(l)}</span>' if is_spanish(l) else esc(l)
                               for l in lines) + "</p>"


def song_lang(name):
    return "ru" if re.search("[а-яё]", name, re.I) else "es"


def song_genre(s):
    sub = s.get("sub") or []
    return YEAR_TAIL.sub("", sub[1]).lower() if len(sub) > 1 else ""


def pick_others(s, songs, exclude=(), limit=6):
    """«Ещё песни» — у каждой страницы свой набор, но одинаковый при каждой сборке:
    соседние по дате релизы, затем песни того же жанра (по кругу после этой — чтобы ссылки
    распределялись между всеми песнями), затем самые популярные."""
    by_date = sorted(songs, key=lambda o: (o.get("published", ""), o["slug"]))
    picks = []

    def add(o):
        if len(picks) < limit and o is not s and o["slug"] not in exclude and o not in picks:
            picks.append(o)

    i = by_date.index(s)
    if i + 1 < len(by_date):
        add(by_date[i + 1])                                   # следующий релиз
    if i > 0:
        add(by_date[i - 1])                                   # предыдущий релиз
    genre = song_genre(s)
    same = [o for o in by_date if genre and song_genre(o) == genre]
    if s in same:
        j = same.index(s)
        for o in same[j + 1:] + same[:j]:
            if len(picks) >= 4:
                break
            add(o)
    for o in sorted(songs, key=lambda o: -int(o.get("views") or 0)):
        add(o)
    return picks


def build_songs(root, tmp, og_fallback=SITE_URL + "nevara-og.jpg", analytics="", missing_lyrics=None):
    """missing_lyrics — список, куда добавляются (название, ожидаемый файл) песен без текста."""
    """Страница для каждой песни из data/songs.json. Возвращает список (адрес, дата) для sitemap."""
    from PIL import Image, ImageFilter

    data_path = os.path.join(root, "data", "songs.json")
    tpl_path = os.path.join(root, "song.html")
    if not os.path.exists(data_path) or not os.path.exists(tpl_path):
        print("Страницы песен пропущены: нет data/songs.json или song.html")
        return []
    with open(data_path, encoding="utf-8") as f:
        songs = json.load(f)["songs"]
    with open(tpl_path, encoding="utf-8") as f:
        template = f.read()

    # Размер названия песни — один на всех страницах (см. {{NAME_W}} ниже)
    title_w = max((longest_word(s["name"]) for s in songs), default=4)

    # Языковые версии одной песни: «БАМ-БОЛЕО» и «BAMBOLEO (Español)» дают одинаковый ключ «bamboleo»
    groups = {}
    for s in songs:
        groups.setdefault(version_key(s["slug"]), []).append(s)
    page_langs = load_page_langs(root, {s["slug"] for s in songs})
    ui = {lang: ui_strings(template, lang) for lang in set(page_langs.values())}

    pages = []
    for s in songs:
        slug, name, vid = s["slug"], s["name"], s["id"]
        if not re.fullmatch(r"[a-z0-9-]+", slug) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            raise BuildError(f"неожиданные данные песни: {slug!r} / {vid!r}")
        lang = page_langs.get(slug, "ru")
        es = lang == "es"
        sub = s.get("sub", [])
        line = sub[0] if sub else ""
        genres = [YEAR_TAIL.sub("", g) or g for g in sub[1:]]    # «Latin Pop 2026» -> «Latin Pop», год есть в дате

        # Фон: обложка, уменьшенная и размытая заранее (файл ~2 КБ, без нагрузки на телефон)
        bg = ""             # без обложки — только фирменное свечение из CSS
        og_name = ""
        cover = s.get("cover") or ""
        if cover and not re.fullmatch(r"covers/[\w-]+\.webp", cover):
            raise BuildError(f"неожиданный путь обложки: {cover!r}")
        cover_path = os.path.join(root, cover)
        if cover and os.path.exists(cover_path):
            with Image.open(cover_path) as c:
                im = c.convert("RGB").resize((96, 54), Image.LANCZOS).filter(ImageFilter.GaussianBlur(3))
            data = io.BytesIO()
            im.save(data, "WEBP", quality=60)
            raw = data.getvalue()
            name_bg = f"img/bg-{vid}-{hashlib.sha256(raw).hexdigest()[:6]}.webp"
            with open(os.path.join(tmp, name_bg), "wb") as f:
                f.write(raw)
            bg = "../" + name_bg
            # Превью для мессенджеров и соцсетей: JPEG 1200×630 — WebP понимают не все (WhatsApp, VK, LinkedIn)
            with Image.open(cover_path) as c:
                og = c.convert("RGB")
            k = max(1200 / og.width, 630 / og.height)
            og = og.resize((round(og.width * k), round(og.height * k)), Image.LANCZOS)
            x, y = (og.width - 1200) // 2, (og.height - 630) // 2
            og = og.crop((x, y, x + 1200, y + 630))
            data = io.BytesIO()
            og.save(data, "JPEG", quality=82, optimize=True, progressive=True)
            raw = data.getvalue()
            og_name = f"img/og-{vid}-{hashlib.sha256(raw).hexdigest()[:6]}.jpg"
            with open(os.path.join(tmp, og_name), "wb") as f:
                f.write(raw)

        # Описание и текст
        about = ""
        if s.get("description"):
            paras = "".join(f"<p>{esc(p)}</p>" for p in s["description"])
            # lang у содержимого: если посетитель переключит подписи на другой язык, текст песни
            # и описание всё равно читаются экранным диктором на своём языке
            about = f'<section><h2 data-i18n="about">О песне</h2><div class="about" lang="{lang}">{paras}</div></section>'
        lyrics_html, has_lyrics = "", False
        lpath = os.path.join(root, "lyrics", f"{slug}.txt")
        if os.path.exists(lpath):
            with open(lpath, encoding="utf-8") as f:
                stanzas = clean_lyrics(f.read(), name)
            if stanzas:
                has_lyrics = True
                body = "".join(render_stanza(st) for st in stanzas)
                lyrics_html = (f'<section id="lyrics"><h2 data-i18n="lyrics">Текст песни</h2>'
                               f'<div class="lyrics" lang="{lang}">{body}</div></section>')
        if not has_lyrics and missing_lyrics is not None:
            missing_lyrics.append((name, f"lyrics/{slug}.txt"))   # файла нет или он пустой

        my_lang = song_lang(name)
        versions = "".join(
            f'<p class="version"><span data-i18n="ver-{song_lang(o["name"])}">'
            f'{VERSION_LABEL[song_lang(o["name"])]}</span> — <a href="../{esc(o["slug"])}/">{esc(o["name"])}</a></p>'
            for o in groups[version_key(slug)] if o is not s and song_lang(o["name"]) != my_lang)
        jump = ('<p class="jump"><a href="#lyrics" data-i18n="jump">Текст песни ↓</a></p>'
                if has_lyrics else "")

        pair = {o["slug"] for o in groups[version_key(slug)]}     # другая версия уже есть ссылкой наверху
        others = pick_others(s, songs, exclude=pair)
        others_html = "".join(
            f'<li><a href="../{esc(o["slug"])}/"><b>{esc(o["name"])}</b>'
            f'<span>{esc(o["sub"][0]) if o.get("sub") else ""}</span></a></li>' for o in others)

        nice = nice_name(name)
        in_genres = f" ({', '.join(genres)})" if genres else ""
        if es:
            page_title = f"{nice} — Nevara · letra de la canción" if has_lyrics else f"{nice} — Nevara · escuchar la canción"
            meta = (s["description"][0] if s.get("description") else
                    f"«{nice}» — canción de Nevara{in_genres}. "
                    + ("Escucha la canción y lee la letra." if has_lyrics else "Escucha la canción en YouTube."))
        else:
            page_title = f"{nice} — Nevara · текст песни" if has_lyrics else f"{nice} — Nevara · слушать песню"
            meta = (s["description"][0] if s.get("description") else
                    f"«{nice}» — песня Nevara{in_genres}. "
                    + ("Слушайте песню и читайте текст." if has_lyrics else "Слушайте песню на YouTube."))
        # Языковые версии песни (БАМ-БОЛЕО / BAMBOLEO) — разные записи с разными видео и текстами,
        # поэтому связаны только видимой ссылкой «Испанская версия», без hreflang: в поиске каждая
        # показывается по своим запросам и не подменяет другую
        if len(meta) > 160:
            meta = meta[:157].rsplit(" ", 1)[0] + "…"
        url = f"{SITE_URL}{slug}/"
        has_cover = bool(cover) and os.path.exists(cover_path)
        cover_url = SITE_URL + cover if has_cover else og_fallback
        date = s.get("published", "")
        ld = {
            "@context": "https://schema.org", "@type": "MusicRecording", "name": nice, "url": url, "inLanguage": lang,
            "image": cover_url, "datePublished": date, "genre": genres,
            "byArtist": {"@type": "MusicGroup", "name": "Nevara", "@id": f"{SITE_URL}#artist", "url": SITE_URL},
            "sameAs": [f"https://www.youtube.com/watch?v={vid}"],
        }
        # Видео на YouTube (картинка + музыка) — для Google это тоже видео: даём его данные,
        # чтобы страница могла показываться в поиске с превью и длительностью
        uploaded = s.get("uploaded") or date
        video = {
            "@type": "VideoObject", "@id": url + "#video", "name": f"{nice} — Nevara",
            "description": meta, "thumbnailUrl": [cover_url, f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"],
            "uploadDate": uploaded, "embedUrl": f"https://www.youtube.com/embed/{vid}",
            "url": f"https://www.youtube.com/watch?v={vid}",
        }
        if re.fullmatch(r"PT(?:\d+H)?(?:\d+M)?(?:\d+S)?", s.get("duration") or "") and s["duration"] != "PT":
            video["duration"] = s["duration"]
        ld["video"] = {"@id": url + "#video"}
        if has_lyrics:
            ld["recordingOf"] = {"@type": "MusicComposition", "name": nice,
                                 "lyrics": {"@type": "CreativeWork",
                                            "text": "\n\n".join("\n".join(st) for st in stanzas)}}
        values = {
            "{{PAGE_TITLE}}": esc(page_title), "{{META_DESC}}": esc(meta), "{{CANONICAL}}": url,
            "{{OG_IMAGE}}": SITE_URL + og_name if og_name else og_fallback,
            # подпись к картинке превью (для незрячих и когда картинка не загрузилась)
            "{{OG_ALT}}": esc(HERO_ALT[lang] if not og_name else
                              f"Portada de la canción «{nice}» — Nevara" if es else f"Обложка песни «{nice}» — Nevara"),
            "{{LANG}}": lang, "{{OG_LOCALE}}": OG_LOCALE[lang],
            "{{NAME_LANG}}": "es" if is_spanish(name) else "ru",
            "{{BACKDROP}}": f'<img src="{bg}" alt="" draggable="false">' if bg else "", "{{VIDEO_ID}}": vid, "{{COVER_IMG}}": (f'<img src="../{esc(cover)}" alt="Nevara — {esc(name)}" width="800" height="450" '
                              f'fetchpriority="high">' if has_cover else ""),
            "{{NAME}}": esc(name),
            # одна длина для всех страниц — по самому длинному слову среди всех песен:
            # названия везде одного размера, и самое длинное слово всё равно помещается в строку
            "{{NAME_W}}": str(title_w),
            "{{LINE}}": f'<p class="line" lang="{"es" if is_spanish(line) else "ru"}">{esc(line)}</p>' if line else "",
            "{{TAGS}}": "".join(f'<span class="tag">{esc(g)}</span>' for g in genres),
            "{{DATE}}": esc(date),
            "{{DATE_SHOWN}}": f"{date[8:10]}.{date[5:7]}.{date[:4]}" if len(date) == 10 else "",
            "{{VERSIONS}}": versions, "{{JUMP}}": jump, "{{ANALYTICS}}": analytics,
            "{{ABOUT}}": about, "{{LYRICS}}": lyrics_html, "{{OTHERS}}": others_html,
            "{{JSONLD}}": json.dumps({"@context": "https://schema.org", "@graph": [
                {k: v for k, v in ld.items() if k != "@context"}, video]},
                ensure_ascii=False).replace("</", "<\\/"),
        }
        page = template
        for key, val in values.items():
            page = page.replace(key, val)
        left_over = sorted(set(PLACEHOLDER.findall(page)))
        if left_over:
            raise BuildError(f"в song.html остались незаполненные метки: {', '.join(left_over)}")
        if lang in ui:
            page = translate_ui(page, ui[lang])
            # «Все песни» и логотип ведут на главную того же языка
            page = re.sub(r'data-home="([^"]*)" href="\.\./', lambda m: f'data-home="{m.group(1)}" href="../{lang}/', page)
        os.makedirs(os.path.join(tmp, slug))
        with open(os.path.join(tmp, slug, "index.html"), "w", encoding="utf-8") as f:
            f.write(page)
        pages.append(f"{slug}/")
    return pages


# ---------- проверка ссылок ----------
CSS_URL = re.compile(r"""url\(\s*['"]?([^'")]+)""")


class PageRefs(HTMLParser):
    """Адреса (src, href, srcset) и якоря (id) из тегов страницы — настоящим разбором HTML.
    Содержимое <script> и комментарии разбор пропускает сам при любом их написании."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs, self.ids = [], set()

    def handle_starttag(self, tag, attrs):
        for name, val in attrs:
            if val is None:
                continue
            if name == "id":
                self.ids.add(val)
            elif name in ("src", "href"):
                self.refs.append(val)
            elif name in ("srcset", "imagesrcset"):
                self.refs += [p.split()[0] for p in val.split(",") if p.strip()]

    handle_startendtag = handle_starttag


def parse_page(text):
    p = PageRefs()
    p.feed(text)
    p.close()
    return p


def check_links(site):
    """Каждая ссылка, картинка, шрифт и якорь (#…) внутри сайта ведёт на то, что существует.
    Внешние адреса (https://…) не проверяются. Возвращает список проблем вида «страница: адрес — что не так»."""
    site = os.path.abspath(site)
    ids, problems = {}, []

    def ids_of(path):
        if path not in ids:
            with open(path, encoding="utf-8") as f:
                ids[path] = parse_page(f.read()).ids
        return ids[path]

    for dirpath, _, files in os.walk(site):
        for fn in sorted(files):
            if not fn.endswith((".html", ".css")):
                continue
            src = os.path.join(dirpath, fn)
            with open(src, encoding="utf-8") as f:
                text = f.read()
            refs = []
            if fn.endswith(".css"):
                refs = CSS_URL.findall(text)
            else:
                # только адреса из разметки: ссылки внутри скриптов и комментариев не считаются
                refs = parse_page(text).refs
            for ref in refs:
                ref = ref.strip()
                if not ref or ref.startswith("//") or re.match(r"[a-z][a-z0-9+.-]*:", ref, re.I):
                    continue
                path, _, frag = ref.partition("#")
                path = path.split("?")[0]
                if not path:
                    target = src
                elif path.startswith("/"):
                    target = os.path.normpath(os.path.join(site, path.lstrip("/")))
                else:
                    target = os.path.normpath(os.path.join(dirpath, path))
                if path.endswith("/") or os.path.isdir(target):
                    target = os.path.join(target, "index.html")
                where = os.path.relpath(src, site).replace(os.sep, "/")
                if not (target == site or target.startswith(site + os.sep)):
                    problems.append(f"{where}: {ref} — ведёт за пределы сайта")
                elif not os.path.isfile(target):
                    problems.append(f"{where}: {ref} — файла нет")
                elif frag and target.endswith(".html") and frag not in ids_of(target):
                    problems.append(f"{where}: {ref} — на странице нет места #{frag}")
    return sorted(set(problems))


def write_sitemap(tmp, pages):
    urls = [f"  <url><loc>{SITE_URL}</loc></url>"]
    # Без <lastmod>: дата выхода видео — не дата изменения страницы (текст могли добавить позже),
    # а неточная дата хуже, чем никакой
    urls += [f"  <url><loc>{SITE_URL}{p}</loc></url>" for p in pages]
    with open(os.path.join(tmp, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(urls) + "\n</urlset>\n")
    with open(os.path.join(tmp, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}sitemap.xml\n")


def report_missing_lyrics(missing, env=None):
    """Песни без текста: жёлтое предупреждение в Actions на каждую (не ошибка — сайт публикуется)
    и полный список в итогах запуска (GitHub показывает не больше 10 предупреждений на шаг).
    Возвращает напечатанные строки. Вне GitHub Actions — обычные строки «ВНИМАНИЕ: …»."""
    env = os.environ if env is None else env
    in_actions = env.get("GITHUB_ACTIONS") == "true"
    lines = []
    for name, path in missing:
        msg = f"У песни «{name}» нет текста: {path}"
        if in_actions:
            msg = msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            lines.append(f"::warning title=Нет текста песни::{msg}")
        else:
            lines.append(f"ВНИМАНИЕ: {msg}")
    for line in lines:
        print(line)
    summary = env.get("GITHUB_STEP_SUMMARY")
    if missing and summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### ⚠️ Песни без текста ({len(missing)})\n\n")
            f.write("".join(f"- «{name}» — добавьте файл `{path}`\n" for name, path in missing))
            f.write("\nСайт опубликован. Предупреждение исчезнет при следующем запуске после добавления файла.\n")
    return lines


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(root, "_site")
    missing = []
    try:
        build(root, out, check=True, missing_lyrics=missing)
    except BuildError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)
    report_missing_lyrics(missing)
