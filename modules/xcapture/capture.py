"""Tweet -> card PNG. Data from the public syndication endpoint (works without login, one tweet at a time),
drawing by headless Edge/Chrome (emoji, fonts and images come right), crop by the alpha channel with Pillow.
"""
from __future__ import annotations

import html
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone

import requests
from PIL import Image

BROWSERS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)
SYNDICATION = "https://cdn.syndication.twimg.com/tweet-result"
CARD_W = 600
SCALE = 2
TZ_MX = timezone(timedelta(hours=-6))

CSS = """
html,body{margin:0;background:transparent}
body{width:%dpx;font-family:'Segoe UI','Segoe UI Emoji',Arial,sans-serif;-webkit-font-smoothing:antialiased}
.card{box-sizing:border-box;width:%dpx;background:#000;color:#e7e9ea;border:1px solid #2f3336;border-radius:16px;padding:14px 16px 12px}
.head{display:flex;align-items:center;gap:10px}
.av{width:44px;height:44px;border-radius:50%%;background:#333}
.nm{font-weight:700;font-size:15px;line-height:20px;display:flex;align-items:center;gap:4px}
.hd{color:#71767b;font-size:15px;line-height:20px}
.txt{margin-top:10px;font-size:17px;line-height:24px;white-space:pre-wrap;word-wrap:break-word}
.txt a,.lnk{color:#1d9bf0}
.media{margin-top:12px;border:1px solid #2f3336;border-radius:16px;overflow:hidden;display:grid;gap:2px}
.media img{width:100%%;display:block;object-fit:cover}
.m1 img{max-height:520px}
.m2{grid-template-columns:1fr 1fr}.m2 img{height:300px}
.m3{grid-template-columns:1fr 1fr}.m3 img{height:150px}.m3 img:first-child{grid-row:span 2;height:302px}
.m4{grid-template-columns:1fr 1fr}.m4 img{height:150px}
.qt{margin-top:12px;border:1px solid #2f3336;border-radius:16px;padding:12px}
.qt .nm,.qt .hd{font-size:15px}.qt .txt{font-size:15px;line-height:20px;margin-top:4px}
.when{margin-top:12px;color:#71767b;font-size:15px}
.stats{margin-top:12px;padding-top:12px;border-top:1px solid #2f3336;display:flex;gap:20px;color:#71767b;font-size:14px}
.stats b{color:#e7e9ea}
"""

VERIFIED = ('<svg viewBox="0 0 22 22" width="18" height="18"><path fill="#1d9bf0" d="M20.396 11c-.018-.646-.215-1.275'
            '-.57-1.816-.354-.54-.852-.972-1.438-1.246.223-.607.27-1.264.14-1.897-.131-.634-.437-1.218-.882-1.687'
            '-.47-.445-1.053-.75-1.687-.882-.633-.13-1.29-.083-1.897.14-.273-.587-.704-1.086-1.245-1.44S11.647 1.62 '
            '11 1.604c-.646.017-1.273.213-1.813.568s-.969.854-1.24 1.44c-.608-.223-1.267-.272-1.902-.14-.635.13-1.22.436'
            '-1.69.882-.445.47-.749 1.055-.878 1.688-.13.633-.08 1.29.144 1.896-.587.274-1.087.705-1.443 1.245-.356.54'
            '-.555 1.17-.574 1.817.02.647.218 1.276.574 1.817.356.54.856.972 1.443 1.245-.224.606-.274 1.263-.144 1.896'
            '.13.634.433 1.218.877 1.688.47.443 1.054.747 1.687.878.633.132 1.29.084 1.897-.136.274.586.705 1.084 1.246'
            ' 1.439.54.354 1.17.551 1.816.569.647-.016 1.276-.213 1.817-.567s.972-.854 1.245-1.44c.604.239 1.266.296 '
            '1.903.164.636-.132 1.22-.447 1.68-.907.46-.46.776-1.044.908-1.681s.075-1.299-.165-1.903c.586-.274 1.084'
            '-.705 1.439-1.246.354-.54.551-1.17.569-1.816zM9.662 14.85l-3.429-3.428 1.293-1.302 2.072 2.072 4.4-4.794'
            ' 1.347 1.246z"/></svg>')
ICON_REPLY = '<svg viewBox="0 0 24 24" width="18" height="18" style="vertical-align:-4px"><path fill="#71767b" d="M1.751 10c0-4.42 3.584-8 8.005-8h4.366c4.49 0 8.129 3.64 8.129 8.13 0 2.96-1.607 5.68-4.196 7.11l-8.054 4.46v-3.69h-.067c-4.49.1-8.183-3.51-8.183-8.01z"/></svg>'
ICON_HEART = '<svg viewBox="0 0 24 24" width="18" height="18" style="vertical-align:-4px"><path fill="#f91880" d="M20.884 13.19c-1.351 2.48-4.001 5.12-8.379 7.67l-.503.3-.504-.3c-4.379-2.55-7.029-5.19-8.382-7.67-1.36-2.5-1.41-4.86-.514-6.67.887-1.79 2.647-2.91 4.601-3.01 1.651-.09 3.368.56 4.798 2.01 1.429-1.45 3.146-2.1 4.796-2.01 1.954.1 3.714 1.22 4.601 3.01.896 1.81.846 4.17-.514 6.67z"/></svg>'


def browser() -> str:
    for p in BROWSERS:
        if os.path.exists(p):
            return p
    raise RuntimeError("No encontré Edge ni Chrome para dibujar la tarjeta")


def parse_id(ref: str) -> str:
    m = re.search(r"status(?:es)?/(\d+)", ref) or re.fullmatch(r"\s*(\d+)\s*", ref)
    if not m:
        raise ValueError(f"No es un tuit: {ref!r}")
    return m.group(1)


def fetch(tweet_id: str) -> dict:
    # the token is derived from the id (the same formula the official embed uses)
    token = re.sub(r"(0+|\.)", "", _base36((int(tweet_id) / 1e15) * math.pi))
    r = requests.get(SYNDICATION, params={"id": tweet_id, "token": token or "a"}, timeout=20,
                     headers={"User-Agent": "Mozilla/5.0"})
    if r.status_code != 200 or not r.text.strip().startswith("{"):
        raise RuntimeError(f"X no devolvió el tuit {tweet_id} (HTTP {r.status_code}); puede estar borrado o ser de una cuenta protegida")
    d = r.json()
    if d.get("__typename") == "TweetTombstone" or "text" not in d:
        raise RuntimeError(f"El tuit {tweet_id} no está disponible")
    return d


def _base36(x: float) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    n, frac = int(x), x - int(x)
    out = ""
    while n:
        out = digits[n % 36] + out
        n //= 36
    out = out or "0"
    out += "."
    for _ in range(12):
        frac *= 36
        out += digits[int(frac)]
        frac -= int(frac)
    return out


def _text_html(d: dict) -> str:
    """Tweet text with the t.co links resolved/removed and @, # and links in blue."""
    text = d.get("text", "")
    lo, hi = d.get("display_text_range") or (0, len(text))
    ents = d.get("entities") or {}
    reps = []
    for u in ents.get("urls", []):
        reps.append((u["indices"], u.get("display_url", u["url"])))
    for m in ents.get("media", []) or []:
        reps.append((m["indices"], ""))
    chars = list(text)
    for (a, b), repl in sorted(reps, key=lambda r: -r[0][0]):
        chars[a:b] = [f"\x00{repl}\x01"] if repl else [""]
    flat = "".join(chars)
    flat = html.escape(flat.replace("\x00", "\x02").replace("\x01", "\x03"))
    flat = flat.replace("\x02", '<span class="lnk">').replace("\x03", "</span>")
    flat = re.sub(r"(?<![\w&])([@#]\w+)", r'<span class="lnk">\1</span>', flat)
    return flat.strip()


def _hd(user: dict, quoted: bool = False) -> str:
    badge = VERIFIED if user.get("is_blue_verified") or user.get("verified") else ""
    av = user.get("profile_image_url_https", "").replace("_normal", "_bigger")
    img = f'<img class="av" src="{av}" style="width:{28 if quoted else 44}px;height:{28 if quoted else 44}px">'
    return (f'<div class="head">{img}<div><div class="nm">{html.escape(user.get("name", ""))}{badge}</div>'
            f'<div class="hd">@{user.get("screen_name", "")}</div></div></div>')


def _photos(d: dict) -> list[str]:
    out = [p["url"] for p in d.get("photos", []) or []]
    if not out:  # a video/gif: its poster
        out = [m.get("media_url_https") for m in d.get("mediaDetails", []) or [] if m.get("media_url_https")]
    return out[:4]


def _media(d: dict) -> str:
    urls = _photos(d)
    if not urls:
        return ""
    return f'<div class="media m{len(urls)}">' + "".join(f'<img src="{u}">' for u in urls) + "</div>"


def card_html(d: dict) -> str:
    when = ""
    try:
        t = datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")).astimezone(TZ_MX)
        when = t.strftime("%I:%M %p · %d %b %Y").lstrip("0")
    except Exception:
        pass
    qt = ""
    q = d.get("quoted_tweet")
    if q:
        qt = (f'<div class="qt">{_hd(q.get("user", {}), True)}<div class="txt">{_text_html(q)}</div>{_media(q)}</div>')
    stats = (f'<span>{ICON_REPLY} <b>{d.get("conversation_count", 0):,}</b></span>'
             f'<span>{ICON_HEART} <b>{d.get("favorite_count", 0):,}</b></span>')
    body = (f'<div class="card">{_hd(d.get("user", {}))}<div class="txt">{_text_html(d)}</div>{_media(d)}{qt}'
            f'<div class="when">{when}</div><div class="stats">{stats}</div></div>')
    return f"<!doctype html><meta charset=utf-8><style>{CSS % (CARD_W, CARD_W)}</style><body>{body}</body>"


def _wait_file(path: str, timeout: float = 30.0) -> None:
    """Edge hands control back before the screenshot is written: wait until the file exists and stops growing."""
    end, last = time.time() + timeout, -1
    while time.time() < end:
        size = os.path.getsize(path) if os.path.exists(path) else -1
        if size > 0 and size == last:
            return
        last = size
        time.sleep(0.4)
    raise RuntimeError("Edge no generó la captura a tiempo")


def render(tweet_ref: str, out_dir: str) -> dict:
    """One tweet -> {id, png, user, text, likes}. Raises RuntimeError with a readable message."""
    tid = parse_id(tweet_ref)
    d = fetch(tid)
    os.makedirs(out_dir, exist_ok=True)
    user = d.get("user", {}).get("screen_name", "x")
    png = os.path.join(out_dir, f"{user}_{tid}.png")
    tmp = tempfile.mkdtemp(prefix="xcap_")
    try:
        page = os.path.join(tmp, "card.html")
        with open(page, "w", encoding="utf-8") as f:
            f.write(card_html(d))
        raw = os.path.join(tmp, "raw.png")
        subprocess.run([browser(), "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                        f"--user-data-dir={os.path.join(tmp, 'prof')}", "--default-background-color=00000000",
                        f"--force-device-scale-factor={SCALE}", f"--window-size={CARD_W},2600",
                        "--virtual-time-budget=8000", f"--screenshot={raw}", "file:///" + page.replace("\\", "/")],
                       check=True, capture_output=True, timeout=60)
        _wait_file(raw)
        im = Image.open(raw).convert("RGBA")
        box = im.getchannel("A").getbbox()
        if not box:
            raise RuntimeError("La tarjeta salió vacía")
        im.crop(box).save(png)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"id": tid, "png": png, "user": user, "text": d.get("text", ""), "likes": d.get("favorite_count", 0),
            "replies": d.get("conversation_count", 0)}
