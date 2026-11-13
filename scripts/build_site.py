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
import json
import os
import re
import shutil
import sys

SITE_URL = "https://thisisnevara.com/"
WIDTHS = (800, 1200, 1600)
COPY = ("favicon.svg", "CNAME")
UMAMI = "umami.txt"      # Website ID из Umami Cloud (вида 1a2b3c4d-…); нет файла — нет счётчика

# Нажатия, которые считаем в Umami: «Слушать», «Поделиться», переходы на YouTube и подписка.
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
    var b=e.target.closest(".play,.share");if(!b||!window.umami)return;
    var s=song(b);
    try{umami.track(b.classList.contains("play")?"Слушать":"Поделиться",s?{song:s}:undefined)}catch(err){}
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
COPY_DIRS = ("covers",)
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


def nice_name(name):
    """«НОЯБРЬ НА ДВОИХ» -> «Ноябрь на двоих» (для заголовка вкладки и поиска)."""
    return name[:1].upper() + name[1:].lower() if name.isupper() else name


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
    }
    for key, val in values.items():
        page = page.replace(key, val)
    left_over = sorted(set(PLACEHOLDER.findall(page)))
    if left_over:
        raise BuildError(f"в index.html остались незаполненные метки: {', '.join(left_over)}")
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)

    pages = build_songs(root, tmp, og_fallback=f"{SITE_URL}nevara-og.jpg?v={tag}",
                        analytics=values["{{ANALYTICS}}"])

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

    # Готово: подменяем результат целиком
    shutil.rmtree(out, ignore_errors=True)
    os.replace(tmp, out)
    print(f"Фон: {', '.join(n for _, n in variants)}; превью nevara-og.jpg?v={tag}; "
          f"телефон: --photo-w {photo_w:.1f}vw, --photo-c {photo_c:.4f}; страниц песен: {len(pages)}")
    return out


YEAR_TAIL = re.compile(r"\s+(?:19|20)\d{2}$")
VERSION_LABEL = {"ru": "Русская версия", "es": "Испанская версия"}


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


def build_songs(root, tmp, og_fallback=SITE_URL + "nevara-og.jpg", analytics=""):
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

    # Языковые версии одной песни: «БАМ-БОЛЕО» и «BAMBOLEO (Español)» дают одинаковый ключ «bamboleo»
    groups = {}
    for s in songs:
        groups.setdefault(version_key(s["slug"]), []).append(s)

    pages = []
    for i, s in enumerate(songs):
        slug, name, vid = s["slug"], s["name"], s["id"]
        if not re.fullmatch(r"[a-z0-9-]+", slug) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            raise BuildError(f"неожиданные данные песни: {slug!r} / {vid!r}")
        sub = s.get("sub", [])
        line = sub[0] if sub else ""
        genres = [YEAR_TAIL.sub("", g) or g for g in sub[1:]]    # «Latin Pop 2026» -> «Latin Pop», год есть в дате

        # Фон: обложка, уменьшенная и размытая заранее (файл ~2 КБ, без нагрузки на телефон)
        bg = ""             # без обложки — только фирменное свечение из CSS
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

        # Описание и текст
        about = ""
        if s.get("description"):
            paras = "".join(f"<p>{esc(p)}</p>" for p in s["description"])
            about = f'<section><h2 data-i18n="about">О песне</h2><div class="about">{paras}</div></section>'
        lyrics_html, has_lyrics = "", False
        lpath = os.path.join(root, "lyrics", f"{slug}.txt")
        if os.path.exists(lpath):
            with open(lpath, encoding="utf-8") as f:
                stanzas = clean_lyrics(f.read(), name)
            if stanzas:
                has_lyrics = True
                body = "".join(render_stanza(st) for st in stanzas)
                lyrics_html = (f'<section id="lyrics"><h2 data-i18n="lyrics">Текст песни</h2>'
                               f'<div class="lyrics">{body}</div></section>')

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
        page_title = f"{nice} — Nevara · текст песни" if has_lyrics else f"{nice} — Nevara · слушать песню"
        meta = (s["description"][0] if s.get("description") else
                f"«{nice}» — песня Nevara{(' (' + ', '.join(genres) + ')') if genres else ''}. "
                + ("Слушайте песню и читайте текст." if has_lyrics else "Слушайте песню на YouTube."))
        if len(meta) > 160:
            meta = meta[:157].rsplit(" ", 1)[0] + "…"
        url = f"{SITE_URL}{slug}/"
        has_cover = bool(cover) and os.path.exists(cover_path)
        cover_url = SITE_URL + cover if has_cover else og_fallback
        date = s.get("published", "")
        ld = {
            "@context": "https://schema.org", "@type": "MusicRecording", "name": nice, "url": url,
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
            "{{OG_IMAGE}}": cover_url, "{{BACKDROP}}": f'<img src="{bg}" alt="" draggable="false">' if bg else "", "{{VIDEO_ID}}": vid, "{{COVER_IMG}}": (f'<img src="../{esc(cover)}" alt="Nevara — {esc(name)}" width="800" height="450" '
                              f'fetchpriority="high">' if has_cover else ""),
            "{{NAME}}": esc(name),
            "{{LINE}}": f'<p class="line">{esc(line)}</p>' if line else "",
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
        os.makedirs(os.path.join(tmp, slug))
        with open(os.path.join(tmp, slug, "index.html"), "w", encoding="utf-8") as f:
            f.write(page)
        pages.append(f"{slug}/")
    return pages


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


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(root, "_site")
    try:
        build(root, out)
    except BuildError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)
