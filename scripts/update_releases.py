#!/usr/bin/env python3
"""Обновляет блок «Новый релиз» и «Популярные треки» на сайте Nevara.

Берёт полноценные видео канала (без Shorts) через YouTube Data API v3:
  - самое новое видео  -> блок «Новый релиз»;
  - остальные           -> «Популярные треки», по убыванию просмотров (4 шт.).
Обложки скачиваются, обрезаются до 16:9 и вшиваются в index.html (WebP),
поэтому сайт показывает их даже без доступа к YouTube.

Если что-то пошло не так, index.html не меняется и скрипт завершается с ошибкой:
сайт продолжает показывать последние сохранённые песни.

Запуск: YOUTUBE_API_KEY=... python scripts/update_releases.py [путь к index.html]
"""
import base64
import html
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

CHANNEL_ID = "UCz_nzmjWeqU3sNkWOv_rP9A"   # @thisisnevara
POPULAR_COUNT = 4
NEW_FOR_DAYS = 60          # дольше этого срока бейдж «Новый релиз» меняется на «Последний релиз»
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
API = "https://www.googleapis.com/youtube/v3/"
START = re.compile(r"(<!-- AUTO:RELEASES:START[^>]*-->\n)(.*?)(\n\s*<!-- AUTO:RELEASES:END -->)", re.S)
GRADIENTS = ["#261642,#8b2861", "#173c4c,#5d2c83", "#592736,#a05a27", "#181e4b,#744d92",
             "#3a1640,#a0285c", "#1c3a3f,#3f2c83"]


class UpdateError(Exception):
    pass


# ---------- сеть (подменяется в тестах) ----------
def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "nevara-site-updater"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def api(endpoint, **params):
    params["key"] = os.environ["YOUTUBE_API_KEY"]
    url = API + endpoint + "?" + urllib.parse.urlencode(params)
    try:
        return json.loads(http_get(url))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise UpdateError(f"YouTube API {endpoint}: HTTP {e.code}: {body}") from e


# ---------- данные с YouTube ----------
def playlist_video_ids(playlist_id):
    ids, token = [], None
    while True:
        params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 50}
        if token:
            params["pageToken"] = token
        data = api("playlistItems", **params)
        ids += [it["contentDetails"]["videoId"] for it in data.get("items", [])]
        token = data.get("nextPageToken")
        if not token or len(ids) >= 500:
            return ids


def iso_seconds(d):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    dd, h, mi, s = (int(x or 0) for x in m.groups())
    return dd * 86400 + h * 3600 + mi * 60 + s


def fetch_videos():
    suffix = CHANNEL_ID[2:]
    only_long = True
    try:
        # UULF… — плейлист только полноценных видео канала (без Shorts и трансляций)
        ids = playlist_video_ids("UULF" + suffix)
    except UpdateError:
        ids = []
    if ids:
        print(f"Источник: плейлист полноценных видео (UULF), видео: {len(ids)}")
    else:
        only_long = False
        ids = playlist_video_ids("UU" + suffix)   # все загрузки, Shorts отфильтруем ниже
        print(f"ВНИМАНИЕ: плейлист UULF недоступен, использую все загрузки (UU) с фильтром Shorts "
              f"по длительности и #shorts — фильтр менее точный. Видео: {len(ids)}")
    if not ids:
        raise UpdateError("на канале не найдено ни одного видео")

    videos = []
    for i in range(0, len(ids), 50):
        data = api("videos", part="snippet,statistics,contentDetails,status", id=",".join(ids[i:i + 50]))
        for v in data.get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            if v.get("status", {}).get("privacyStatus") != "public":
                continue
            if sn.get("liveBroadcastContent", "none") != "none":     # анонсы премьер и эфиры
                continue
            secs = iso_seconds(v.get("contentDetails", {}).get("duration"))
            text = (sn.get("title", "") + " " + sn.get("description", "")).lower()
            if not only_long and (secs <= 60 or "#shorts" in text):
                continue
            if not VIDEO_ID.match(v.get("id", "")):
                raise UpdateError(f"неожиданный формат id видео: {v.get('id')!r}")
            videos.append({
                "id": v["id"],
                "title": sn.get("title", ""),
                "published": sn.get("publishedAt", ""),
                "views": int(st.get("viewCount", 0) or 0),
                "thumbs": sn.get("thumbnails", {}),
            })
    if len(videos) < 2:
        raise UpdateError(f"слишком мало подходящих видео ({len(videos)}), сайт не меняю")
    return videos


# ---------- названия ----------
SEP = r"\s*[—–\-:|]\s*"
NOISE = re.compile(r"\s*[\(\[](official|оф[иі]ц|lyric|audio|video|клип|премьера)[^\)\]]*[\)\]]", re.I)


def split_title(raw):
    """«Nevara — BAMBOLEO | Versión en Español | Latin Pop 2026» -> ("BAMBOLEO", "Versión en Español · Latin Pop 2026")."""
    t = re.sub(r"^\s*nevara" + SEP, "", raw, flags=re.I)
    t = re.sub(SEP + r"nevara\s*$", "", t, flags=re.I)
    parts = [p.strip() for p in t.split("|") if p.strip()]
    if not parts:
        return raw.strip(), ""
    name = NOISE.sub("", parts[0]).strip() or parts[0]
    extra = [p for p in parts[1:] if not re.match(r"(official|оф[иі]ц)", p, re.I) and p.lower() != "nevara"]
    return name, " · ".join(extra)


# ---------- обложки ----------
def thumb_data_uri(thumbs, width):
    from PIL import Image

    for key in ("maxres", "standard", "high", "medium"):
        url = thumbs.get(key, {}).get("url")
        if not url:
            continue
        try:
            im = Image.open(io.BytesIO(http_get(url))).convert("RGB")
        except Exception:
            continue
        w, h = im.size
        target_h = round(w * 9 / 16)
        if h > target_h:                          # 4:3 с чёрными полосами -> центр 16:9
            top = (h - target_h) // 2
            im = im.crop((0, top, w, top + target_h))
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=72, method=6)
        return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()
    # Не публикуем карточку без обложки: лучше оставить прошлую версию блока
    raise UpdateError("не удалось скачать обложку ни в одном размере; сайт не меняю")


# ---------- HTML ----------
def esc(s):
    return html.escape(s, quote=True)


def is_recent(published, now=None):
    from datetime import datetime, timezone, timedelta
    now = now or datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        return True
    return now - dt <= timedelta(days=NEW_FOR_DAYS)


def build_block(new, popular, indent="      ", now=None, catalog=()):
    title, sub = split_title(new["title"])
    img = thumb_data_uri(new["thumbs"], 800)
    cover = f'<img src="{img}" alt="{esc("Nevara — " + title)}">'
    badge = ('<span class="badge" data-i18n="new">НОВЫЙ РЕЛИЗ</span>' if is_recent(new["published"], now)
             else '<span class="badge" data-i18n="latest">ПОСЛЕДНИЙ РЕЛИЗ</span>')
    vid = esc(new["id"])
    play = ('<button type="button" class="play" data-title="' + esc("Nevara — " + title) + '">'
            '<span class="pill"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>'
            '<span data-i18n="listen-here">Слушать</span></span></button>')
    out = [
        '<div class="new-release">',
        f'  <div class="cover" data-video="{vid}">{cover}{play}</div>',
        '  <div class="text">',
        f'    {badge}',
        f'    <h3>{esc(title)}</h3>',
    ]
    if sub:
        # Первая часть — подзаголовок строкой, остальные («Latin Pop 2026» и т. п.) — неразрывные плашки
        parts = sub.split(" · ")
        out.append(f'    <p class="sub">{esc(parts[0])}</p>')
        if len(parts) > 1:
            tags = "".join(f'<span class="tag">{esc(p)}</span>' for p in parts[1:])
            out.append(f'    <p class="tags">{tags}</p>')
    out += [
        f'    <a class="btn" href="https://www.youtube.com/watch?v={vid}" data-i18n="watch-new">Смотреть на YouTube</a>',
        '  </div>',
        '</div>',
        '<p class="subhead" data-i18n="popular">Популярные треки</p>',
        '<div class="release-grid">',
    ]
    for n, v in enumerate(popular, 1):
        name, _ = split_title(v["title"])
        img = thumb_data_uri(v["thumbs"], 480)
        cover = f'<img src="{img}" alt="" loading="lazy">'
        grad = GRADIENTS[(n - 1) % len(GRADIENTS)]
        out.append(
            f'  <a class="release" href="https://www.youtube.com/watch?v={esc(v["id"])}">'
            f'<div class="cover" style="background:linear-gradient(135deg,{grad})">{cover}</div>'
            f'<div class="meta"><small>NEVARA / {n:02d}</small><b>{esc(name)}</b>'
            f'<span data-i18n="watch">Смотреть на YouTube ↗</span></div></a>'
        )
    out.append('</div>')
    out += build_catalog(catalog)
    return "\n".join(indent + line for line in out)


def build_catalog(videos):
    """Раскрывающийся список всех песен: новые сверху, без обложек.
    Полное исходное название видео — в подсказке (title), на случай ошибки сокращения."""
    if not videos:
        return []
    chevron = ('<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" '
               'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>')
    out = [
        '<details class="catalog">',
        f'  <summary><span data-i18n="catalog">Все песни Nevara</span><span class="count">{len(videos)}</span>{chevron}</summary>',
        '  <ol class="catalog-list">',
    ]
    for v in videos:
        name, sub = split_title(v["title"])
        day = v["published"][:10]
        shown = f'{day[5:7]}.{day[:4]}' if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else ""
        # части подписи неразрывные: «Latin Pop 2026» не разорвётся посередине
        sub_html = (f'<span class="c-sub">' + " · ".join(f"<span>{esc(p)}</span>" for p in sub.split(" · ")) + "</span>"
                    if sub else "")
        when = f'<time datetime="{esc(day)}">{shown}</time>' if shown else ""
        out.append(
            f'    <li><a href="https://www.youtube.com/watch?v={esc(v["id"])}" title="{esc(v["title"])}">'
            f'<span class="c-text"><span class="c-name">{esc(name)}</span>{sub_html}</span>'
            f'{when}<span class="c-arrow" aria-hidden="true">↗</span></a></li>'
        )
    out += ['  </ol>', '</details>']
    return out


def main(path):
    if not os.environ.get("YOUTUBE_API_KEY"):
        raise UpdateError("не задан YOUTUBE_API_KEY (секрет в настройках GitHub)")
    with open(path, encoding="utf-8") as f:
        page = f.read()
    if not START.search(page):
        raise UpdateError("в index.html не найдены метки AUTO:RELEASES")

    videos = fetch_videos()
    videos.sort(key=lambda v: v["published"], reverse=True)
    new = videos[0]
    popular = sorted(videos[1:], key=lambda v: v["views"], reverse=True)[:POPULAR_COUNT]

    block = build_block(new, popular, catalog=videos)
    updated = START.sub(lambda m: m.group(1) + block + m.group(3), page, count=1)
    if updated == page:
        print("Изменений нет.")
        return
    # Атомарная запись: сначала во временный файл рядом, потом мгновенная замена.
    # Если процесс прервётся посередине, index.html останется целым.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(updated)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    print(f"Новый релиз: {split_title(new['title'])[0]} ({new['id']})")
    for n, v in enumerate(popular, 1):
        print(f"  {n}. {split_title(v['title'])[0]} — {v['views']} просмотров")


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) > 1 else "index.html")
    except UpdateError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)
