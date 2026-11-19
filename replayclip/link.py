"""Musik per Link laden (SoundCloud und alles andere, was yt-dlp kennt) – landet als MP3 im Musik-Ordner."""
import re
from pathlib import Path

from . import clips

MAX_MINUTES = 20


class _Quiet:
    """yt-dlp soll nichts auf die Konsole schreiben – Fehler kommen als Exception."""
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def download(url: str, music_dir: Path) -> Path:
    url = (url or "").strip()
    if not re.match(r"https?://\S+$", url, re.I):
        raise ValueError("Das ist kein Link (muss mit https:// anfangen)")
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("yt-dlp fehlt – einmal „pip install yt-dlp“ ausführen") from None

    music_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(music_dir / "%(uploader,creator|Link).40s - %(title).70s.%(ext)s"),
        "windowsfilenames": True,
        "noplaylist": True,
        "playlist_items": "1",                      # Set/Playlist-Link: nur der erste Titel
        "match_filter": yt_dlp.utils.match_filter_func(f"duration <? {MAX_MINUTES * 60}"),
        "ffmpeg_location": str(Path(clips.tool("ffmpeg")).parent),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        "socket_timeout": 20,
        "retries": 2,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _Quiet(),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))
        msg = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)?(\S+:\s)?", "", msg)
        if "404" in msg:
            msg = "Titel nicht gefunden (Link falsch, gelöscht oder privat)"
        raise RuntimeError(msg[:200]) from None
    if info and info.get("entries"):
        info = next((e for e in info["entries"] if e), None)
    files = (info or {}).get("requested_downloads") or []
    if not files:
        raise RuntimeError(f"Unter dem Link ist kein Titel ladbar (oder er ist länger als {MAX_MINUTES} Minuten)")
    dst = Path(files[0]["filepath"])
    if not dst.is_file():
        raise RuntimeError("Download kam nicht an")
    artist = info.get("uploader") or info.get("creator") or ""
    dst.with_suffix(".txt").write_text(
        f"Song: {artist + ' - ' if artist else ''}{info.get('title') or dst.stem}\n"
        f"Quelle: {info.get('webpage_url') or url}\n", encoding="utf-8")
    return dst
