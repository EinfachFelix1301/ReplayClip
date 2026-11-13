"""Musik suchen: NoCopyrightSounds (ncs.io) – frei nutzbar in Videos, mit Quellenangabe."""
import html
import re
import urllib.parse
import urllib.request
from pathlib import Path

GENRES = {"Phonk": 16, "Brazilian Phonk": 26, "Trap": 14, "Drum & Bass": 3, "Dubstep": 5,
          "Bass": 1, "Hardstyle": 9, "House": 10, "Alle": ""}
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KillClip"}
_ATTR = re.compile(r'data-url="(https[^"]+\.mp3)"')


def _get(url: str, timeout=15) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=timeout) as r:
        return r.read()


def search(query: str = "", genre: str = "Phonk", page: int = 1) -> list[dict]:
    params = {"q": query.strip(), "genre": GENRES.get(genre, ""), "page": max(1, int(page))}
    page_html = _get("https://ncs.io/music-search?" + urllib.parse.urlencode(params)).decode("utf-8", "replace")
    out, seen = [], set()
    for m in _ATTR.finditer(page_html):
        if m.group(1) in seen:
            continue
        seen.add(m.group(1))
        block = page_html[m.start():m.start() + 3000]
        attr = lambda k: html.unescape((re.search(k + r'="([^"]*)"', block) or [None, ""])[1])
        out.append({"url": m.group(1), "artist": attr("data-artistraw"), "track": attr("data-track"),
                    "genre": attr("data-genre"), "cover": attr("data-cover")})
    return out


def credit(artist: str, track: str) -> str:
    return (f"Song: {artist} - {track} [NCS Release]\nMusic provided by NoCopyrightSounds\n"
            "Free Download/Stream: https://ncs.io\n")


def download(url: str, artist: str, track: str, music_dir: Path) -> Path:
    if not url.startswith("https://ncsmusic.s3.") and "ncs.io" not in url:
        raise ValueError("Nur NCS-Links")
    music_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[\\/:*?"<>|]+', "", f"{artist.split(',')[0].strip()} - {track}").strip()[:90] or "ncs-track"
    dst = music_dir / f"{name}.mp3"
    if not dst.is_file():
        tmp = dst.with_suffix(".part")
        tmp.write_bytes(_get(url, timeout=60))
        tmp.replace(dst)
    dst.with_suffix(".txt").write_text(credit(artist, track), encoding="utf-8")
    return dst
