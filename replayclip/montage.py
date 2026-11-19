"""Montage: mehrere Clips (mit Start/Ende je Clip) zu einem Video, optional mit Musik darunter."""
import json
import os
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path

from . import clips

MONTAGE_DIR = "Montagen"
FADE_OUT = 2.0
_encoder = None


def _run(args):
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            creationflags=clips.NO_WINDOW | clips.BELOW_NORMAL)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-600:] or "FFmpeg-Fehler")
    return result.stdout


X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def encoder() -> list[str]:
    """NVENC, wenn die NVIDIA-Karte es kann (schnell, kaum CPU) – sonst x264."""
    global _encoder
    if _encoder is None:
        try:
            _run([clips.tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.2",
                  "-c:v", "h264_nvenc", "-f", "null", "-"])
            _encoder = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"]
        except Exception:
            _encoder = X264
    return _encoder


def has_audio(path: Path) -> bool:
    out = _run([clips.tool("ffprobe"), "-v", "error", "-select_streams", "a", "-show_entries",
                "stream=index", "-of", "json", str(path)])
    return bool(json.loads(out or "{}").get("streams"))


def _segment(src: Path, start: float, length: float, dst: Path):
    audio = has_audio(src)
    args = [clips.tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src)]
    if not audio:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    args += ["-t", f"{length:.3f}", "-map", "0:v:0", "-map", "0:a:0" if audio else "1:a:0",
             "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=60"]
    tail = ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(dst)]
    global _encoder
    try:
        _run(args + encoder() + tail)
    except RuntimeError:
        if encoder() is X264:
            raise
        # NVENC belegt (z. B. NVIDIA-Aufnahme/Instant Replay läuft) -> auf CPU-Encoder ausweichen
        _encoder = X264
        _run(args + X264 + tail)


def _add_music(video: Path, music: Path, music_vol: float, game_vol: float, dst: Path):
    length = clips.duration(video)
    fade = min(FADE_OUT, length / 2)
    graph = (f"[0:a]acompressor=threshold=-30dB:ratio=6:attack=3:release=150,loudnorm=I=-26:TP=-4:LRA=7,aresample=48000,volume={game_vol:.3f}[game];"
             f"[1:a]aresample=48000,volume={music_vol:.3f},atrim=0:{length:.3f},"
             f"afade=t=out:st={length - fade:.3f}:d={fade:.3f}[music];"
             "[game][music]amix=inputs=2:duration=first:normalize=0[mix]")
    _run([clips.tool("ffmpeg"), "-v", "error", "-y", "-i", str(video), "-stream_loop", "-1", "-i", str(music),
          "-filter_complex", graph, "-map", "0:v:0", "-map", "[mix]", "-c:v", "copy",
          "-c:a", "aac", "-b:a", "192k", "-t", f"{length:.3f}", "-movflags", "+faststart", str(dst)])


SPED_DIR = Path(os.environ.get("LOCALAPPDATA", ".")) / "ReplayClip" / "sped"


def sped_up(music: str, speed: float) -> str:
    """„Sped Up“ wie auf TikTok/YouTube: schneller UND höher (Nightcore-Art). Ergebnis wird gecacht."""
    if not music or abs(float(speed) - 1.0) < 0.01:
        return music
    src = Path(music)
    import hashlib
    key = hashlib.sha1(f"{src.resolve()}|{src.stat().st_mtime}|{float(speed):.3f}".encode()).hexdigest()[:16]
    dst = SPED_DIR / f"{key}.m4a"
    if not dst.is_file():
        SPED_DIR.mkdir(parents=True, exist_ok=True)
        rate = _run([clips.tool("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_entries",
                     "stream=sample_rate", "-of", "csv=p=0", str(src)]).strip() or "44100"
        tmp = dst.with_suffix(".part.m4a")
        _run([clips.tool("ffmpeg"), "-v", "error", "-y", "-i", str(src), "-vn",
              "-af", f"asetrate={int(rate)}*{float(speed):.4f},aresample=48000", "-c:a", "aac", "-b:a", "256k", str(tmp)])
        os.replace(tmp, dst)
    return str(dst)


def _copy_credit(music: str, video: Path):
    """NCS-Quellenangabe (Musik.txt) neben die fertige Montage legen – für die Videobeschreibung."""
    src = Path(music).with_suffix(".txt")
    if src.is_file():
        text = "Für die Videobeschreibung:\n\n" + src.read_text(encoding="utf-8")
        video.with_suffix(".txt").write_text(text, encoding="utf-8")


def render(items: list[dict], out_dir: Path, music: str | None, music_vol: float, game_vol: float,
           log, progress, cancel: threading.Event, auto: dict | None = None) -> Path | None:
    """items: [{path, start, end}] in Reihenfolge. Gibt die fertige Datei zurück (None = abgebrochen).

    auto = {style, intro, preset, pre_s}: mit Musik Kills auf den Beat legen (autoedit), sonst Clips am Stück.
    """
    if not items:
        raise ValueError("Keine Clips in der Montage")
    if music and not Path(music).is_file():
        raise ValueError("Musikdatei nicht gefunden")
    target_dir = Path(out_dir) / MONTAGE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = clips.clip_name(target_dir, "montage", datetime.now())
    tmp = target_dir / f".tmp-{uuid.uuid4().hex}"
    tmp.mkdir()
    steps = len(items) + 1 + bool(music)
    credit_src = music
    if music and auto and auto.get("speed"):
        music = sped_up(music, float(auto["speed"]))
    try:
        if auto and music and not auto.get("plain"):
            from . import autoedit
            items = [it for it in items if Path(it["path"]).is_file() or log(f"Clip fehlt – übersprungen: {Path(it['path']).name}")]
            items = [{**it, "start": float(it["start"]), "end": float(it["end"] or 0)} for it in items]
            items = [it for it in items if it["end"] - it["start"] >= 1.0]
            if not items:
                raise ValueError("Keine Clips übrig")
            done = autoedit.render(items, music, music_vol, game_vol, auto.get("style", "clean"),
                                   auto.get("intro", ""), auto.get("preset", "beispiel"), float(auto.get("pre_s", 10)),
                                   tmp, target, log, progress, cancel, auto.get("intro_mode", "teaser"))
            if done:
                _copy_credit(credit_src, done)
                progress({"pct": 100, "file": "", "index": len(items), "total": len(items), "eta": 0})
                log(f"Montage fertig: {target.name} ({clips.duration(target):.0f} s, {len(items)} Clips, auf den Beat)")
            return done
        parts = []
        for i, item in enumerate(items):
            if cancel.is_set():
                return None
            src = Path(item["path"])
            if not src.is_file():
                log(f"Clip fehlt (gelöscht/verschoben) – übersprungen: {src.name}")
                continue
            start, end = float(item["start"]), float(item["end"] or 0)
            if end - start < 0.2:
                continue
            progress({"pct": i / steps * 100, "file": src.name, "index": i + 1, "total": len(items), "eta": None})
            part = tmp / f"{i:04d}.mp4"
            _segment(src, start, end - start, part)
            parts.append(part)
        if not parts:
            raise ValueError("Keine Clips übrig – fehlen die Dateien oder sind alle kürzer als 0,2 s?")
        progress({"pct": len(items) / steps * 100, "file": "Zusammenfügen …", "index": len(items),
                  "total": len(items), "eta": None})
        (tmp / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        joined = tmp / "joined.mp4"
        _run([clips.tool("ffmpeg"), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
              "-c", "copy", "-movflags", "+faststart", str(joined)])
        if music:
            if cancel.is_set():
                return None
            progress({"pct": (steps - 1) / steps * 100, "file": "Musik darunterlegen …", "index": len(items),
                      "total": len(items), "eta": None})
            mixed = tmp / "mixed.mp4"
            _add_music(joined, Path(music), music_vol, game_vol, mixed)
            joined = mixed
        os.replace(joined, target)
        if music:
            _copy_credit(credit_src, target)
        progress({"pct": 100, "file": "", "index": len(items), "total": len(items), "eta": 0})
        log(f"Montage fertig: {target.name} ({clips.duration(target):.0f} s, {len(parts)} Clips)")
        return target
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
