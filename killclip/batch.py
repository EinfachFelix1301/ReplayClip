from .job_sort import job_dirs, sort_clip
"""Bestehende Aufnahmen nach Kills/Toden durchsuchen und Clips schneiden."""
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from . import clips
from .config import Config
from .detector import Detector, Preset, Segmenter
from . import preview

FRAME_H = 720
# NVIDIA-Dateiname: "Desktop 2026.11.06 - 18.58.20.03.mp4" -> Aufnahmestart
_NV_NAME = re.compile(r"(\d{4})\.(\d{2})\.(\d{2}) - (\d{2})\.(\d{2})\.(\d{2})")


def recording_start(path: Path, length: float, pre_s: float = 0.0) -> datetime:
    clip_t = clips.clip_time(path)
    if clip_t:
        # eigener KillClip-Clip: heißt nach dem ersten Kill, beginnt Vorlauf-Sekunden davor
        return clip_t - timedelta(seconds=pre_s)
    m = _NV_NAME.search(path.name)
    if m:
        named = datetime(*map(int, m.groups()))
        # NVIDIA: Aufnahme heißt nach ihrem Start, Instant Replay (".DVR") nach dem Speichern = Ende
        return named - timedelta(seconds=length) if ".DVR" in path.name.upper() else named
    return datetime.fromtimestamp(path.stat().st_mtime) - timedelta(seconds=length)


def _video_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        [clips.tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, text=True, check=True, creationflags=clips.NO_WINDOW,
    ).stdout.strip()
    w, h = map(int, out.split("x")[:2])
    return w, h


def iter_frames(path: Path, fps: float, cancel: threading.Event):
    w, h = _video_size(path)
    out_w = round(w * FRAME_H / h / 2) * 2
    size = out_w * FRAME_H * 3
    cmd = [clips.tool("ffmpeg"), "-v", "error", "-hwaccel", "auto", "-threads", "2", "-i", str(path),
           "-an", "-sn", "-vf", f"fps={fps},scale={out_w}:{FRAME_H}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=clips.NO_WINDOW | clips.BELOW_NORMAL)
    try:
        n = 0
        while not cancel.is_set():
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield n / fps, np.frombuffer(buf, np.uint8).reshape(FRAME_H, out_w, 3)
            n += 1
    finally:
        proc.kill()
        proc.wait()


VIDEO_EXT = {".mp4", ".mkv", ".mov"}
KILLS_DIR = "Kills"
MANIFEST = ".killclip.json"


def scan_file(path: Path, cfg: Config, out_dir: Path, log, progress, cancel: threading.Event,
              on_clip=lambda _p, _s: None, deaths_only=False) -> list[Path]:
    """Scannt ein Video; progress(0..1) für diese Datei, on_clip(pfad, segment) je geschnittenem Clip.

    deaths_only: nur eigene Tode suchen, jeder Tod ein eigener Clip (Modus „Tode suchen“).
    """
    preset = Preset.load(cfg.preset)
    if deaths_only:
        events = {"death"}
    else:
        events = {"kill", "death"} if cfg.clip_deaths else {"kill"}
    detector = Detector(preset, events, cfg.detect_mode)
    if not detector.states:
        raise RuntimeError("Im Preset fehlt die Tod-Erkennung")
    segmenter = Segmenter(cfg.pre_s, cfg.post_s, join_overlap=not deaths_only)
    length = clips.duration(path)
    started = recording_start(path, length, cfg.pre_s)
    out_dir.mkdir(parents=True, exist_ok=True)
    segments = []

    log(f"Scanne {path.name} ({length / 60:.0f} min)")
    for t, frame in iter_frames(path, max(cfg.batch_fps, detector.min_fps), cancel):
        for event in detector.update(t, frame):
            log(f"{event.upper()} bei {timedelta(seconds=int(t))}")
            preview.publish(frame, preview.kill_region(detector),
                            f"Scan · {path.name[:40]} · {'Kill' if event == 'kill' else 'Tod'} bei "
                            f"{(started + timedelta(seconds=t)):%H:%M:%S}")
            done = segmenter.add(t, event)
            if done:
                segments.append(done)
        progress(min(t / length, 1.0))
    if cancel.is_set():
        return []
    if segmenter.current:
        segments.append(segmenter.flush())

    written = []
    roots = [cfg.output_dir, out_dir, *cfg.kill_folders]
    for seg in segments:
        end = min(seg.end, length)
        when = started + timedelta(seconds=seg.first)
        with clips.clip_lock:
            dup = clips.find_duplicate(roots, when)
            if dup:
                log(f"Schon vorhanden, übersprungen: {dup.name}")
                continue
            dst = clips.clip_name(out_dir, seg.label, when)
            clips.cut(path, seg.start, end, dst)
        dst = sort_clip(dst, cfg.output_dir, log, cfg.preset)
        written.append(dst)
        on_clip(dst, seg)
    progress(1.0)
    return written


def find_videos(folder: Path) -> list[Path]:
    """Alle Videos im Ordner (rekursiv), ohne den eigenen Kills-Ordner."""
    out = folder / KILLS_DIR
    return sorted(p for p in folder.rglob("*")
                  if p.suffix.lower() in VIDEO_EXT and out not in p.parents)


def _load_manifest(out_dir: Path) -> dict:
    try:
        return json.loads((out_dir / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def scan_many(files: list[Path], out_dir: Path, cfg: Config, log, progress, cancel: threading.Event,
              on_clip=lambda _p, _s: None, skip_done=True, deaths_only=False) -> int:
    """Scannt mehrere Videos nach out_dir. progress(dict) mit Gesamtstand und Restzeit.

    Bereits gescannte Dateien (gleiche Größe) stehen in out_dir/.killclip.json und werden übersprungen.
    Die Tode-Suche trägt nichts ins Manifest ein (sonst überspringt der Kill-Scan die Datei später).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(out_dir)
    todo = [f for f in files if not (skip_done and manifest.get(str(f)) == f.stat().st_size)]
    if len(todo) < len(files):
        log(f"{len(files) - len(todo)} Datei(en) schon gescannt – übersprungen")
    if not todo:
        progress({"pct": 100, "file": "", "index": 0, "total": 0, "eta": 0})
        return 0

    lengths = {}
    for f in todo:
        try:
            lengths[f] = clips.duration(f)
        except Exception:
            lengths[f] = 0.0
    total_len = sum(lengths.values()) or 1.0
    done_len, total_clips, t0 = 0.0, 0, time.time()

    for i, path in enumerate(todo, 1):
        if cancel.is_set():
            break

        def file_progress(frac, path=path, i=i):
            processed = done_len + frac * lengths[path]
            elapsed = time.time() - t0
            eta = elapsed / processed * (total_len - processed) if processed > 1 else None
            progress({"pct": processed / total_len * 100, "file": path.name, "index": i,
                      "total": len(todo), "eta": eta})

        try:
            written = scan_file(path, cfg, out_dir, log, file_progress, cancel, on_clip, deaths_only)
        except Exception as exc:
            log(f"✖ {path.name}: {exc}")
            written = None
        if cancel.is_set():
            break
        done_len += lengths[path]
        if written is not None:
            total_clips += len(written)
            log(f"{path.name}: {len(written)} {'Tod-' if deaths_only else ''}Clip(s)")
            if deaths_only:
                continue
            manifest[str(path)] = path.stat().st_size
            (out_dir / MANIFEST).write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    return total_clips


BACKUP_DIR = "_Original"
_CLIP_NAME = re.compile(r"^(kill\d*(_death)?|death)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}")


def find_clips(folder: Path) -> list[Path]:
    folders = [folder, *job_dirs(folder)]
    return sorted(p for directory in folders if directory.is_dir()
                  for p in directory.glob("*.mp4") if _CLIP_NAME.match(p.stem))


def retrim_clip(path: Path, cfg: Config, cancel: threading.Event) -> str:
    """Erkennt die Kills im Clip neu und kürzt auf [erster Kill - pre, letzter Kill + post].

    Original wird nach <Ordner>/_Original verschoben. Gibt eine kurze Statusmeldung zurück.
    """
    length = clips.duration(path)
    events = {"kill", "death"} if "death" in path.stem else {"kill"}
    detector = Detector(Preset.load(cfg.preset), events, cfg.detect_mode)
    times = [t for t, frame in iter_frames(path, 4.0, cancel) if detector.update(t, frame)]
    if cancel.is_set():
        return "abgebrochen"
    if not times:
        return "kein Kill erkannt – unverändert"

    start = max(0.0, times[0] - cfg.pre_s)
    end = min(length, times[-1] + cfg.post_s)
    if start < 1.0 and length - end < 1.0:
        return "passt schon"

    backup = path.parent / BACKUP_DIR / path.name
    backup.parent.mkdir(exist_ok=True)
    if backup.exists():
        backup = backup.with_stem(f"{path.stem}_{int(time.time())}")
    os.replace(path, backup)
    try:
        clips.cut(backup, start, end, path)
    except Exception:
        os.replace(backup, path)  # Original zurück, falls der Schnitt scheitert
        raise
    return f"{length:.0f} s → {end - start:.0f} s"


def retrim_many(files: list[Path], cfg: Config, log, progress, cancel: threading.Event, on_clip=lambda _p: None) -> int:
    changed = 0
    for i, path in enumerate(files, 1):
        if cancel.is_set():
            break
        progress({"pct": (i - 1) / len(files) * 100, "file": path.name, "index": i, "total": len(files), "eta": None})
        try:
            msg = retrim_clip(path, cfg, cancel)
        except Exception as exc:
            msg = f"Fehler: {exc}"
        log(f"{path.name}: {msg}")
        if "→" in msg:
            changed += 1
            on_clip(path)
    progress({"pct": 100, "file": "", "index": len(files), "total": len(files), "eta": 0})
    return changed
