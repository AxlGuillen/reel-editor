"""TikTok without downloading: a profile's latest videos with their views, a
video's metadata and the editors mentioned in captions ("ib @…", "inspo @…").

There is no public ranking: TikTok's search asks for a CAPTCHA (never solved
here) and yt-dlp's hashtag extractor is broken. The top of a niche comes from
a snowball over editors' profiles: seed profiles → their videos → the
profiles their captions mention → … (research of 07/11/2026: 56 profiles,
7 572 videos). Some profiles fail on and off ("secondary user ID"): retried."""
from __future__ import annotations

import re
import time

import yt_dlp

MENTION = re.compile(r"@([A-Za-z0-9_.]{2,24})")


def profile_videos(handle: str, n: int = 300, tries: int = 3) -> list[dict]:
    """[{url, views, title, dur, ts}] of the profile's latest `n` videos."""
    handle = handle.lstrip("@")
    err = None
    for k in range(tries):
        try:
            y = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True,
                                  "extract_flat": True, "playlistend": n})
            info = y.extract_info(f"https://www.tiktok.com/@{handle}", download=False)
            return [{"url": e.get("url"), "views": e.get("view_count"), "title": e.get("title"),
                     "dur": e.get("duration"), "ts": e.get("timestamp"), "handle": handle}
                    for e in info.get("entries") or []]
        except Exception as exc:  # noqa: BLE001 - TikTok blocks on and off
            err = str(exc)[:200]
            time.sleep(3 + 4 * k)
    raise RuntimeError(f"No se pudo leer @{handle}: {err}")


def video_meta(url: str) -> dict:
    """Views, likes, author, duration, size and the song of one video."""
    y = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True})
    i = y.extract_info(url, download=False)
    return {"url": url, "views": i.get("view_count"), "likes": i.get("like_count"), "uploader": i.get("uploader"),
            "dur": i.get("duration"), "title": i.get("title"), "date": i.get("upload_date"),
            "w": i.get("width"), "h": i.get("height"), "track": i.get("track"), "artist": i.get("artist")}


def mentions(videos: list[dict], known: set[str] = frozenset()) -> list[tuple[str, int]]:
    """Profiles mentioned in the captions, weighted by the views of the videos
    that mention them, minus the ones already read: the next seeds."""
    score: dict[str, int] = {}
    for v in videos:
        for m in MENTION.findall(v.get("title") or ""):
            m = m.rstrip(".").lower()
            if m not in known:
                score[m] = score.get(m, 0) + (v.get("views") or 0)
    return sorted(score.items(), key=lambda kv: -kv[1])


def top(videos: list[dict], n: int = 10, per_profile: int = 1) -> list[dict]:
    """The `n` most viewed, at most `per_profile` per profile."""
    out, seen = [], {}
    for v in sorted((v for v in videos if v.get("views")), key=lambda v: -v["views"]):
        if seen.get(v["handle"], 0) < per_profile:
            out.append(v)
            seen[v["handle"]] = seen.get(v["handle"], 0) + 1
        if len(out) == n:
            break
    return out
