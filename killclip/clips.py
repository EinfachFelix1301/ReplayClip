"""FFmpeg-Hilfen: Dauer lesen und Clips verlustfrei (Stream-Copy) ausschneiden."""
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

NO_WINDOW = subprocess.CREATE_NO_WINDOW
BELOW_NORMAL = subprocess.BELOW_NORMAL_PRIORITY_CLASS


def tool(name: str) -> str:
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "bin" / f"{name}.exe"
    if getattr(sys, "frozen", False) and bundled.is_file():
        return str(bundled)
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} nicht gefunden – FFmpeg installieren (winget install Gyan.FFmpeg)")
    return path


def duration(path: Path) -> float:
    out = subprocess.run(
        [tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True, creationflags=NO_WINDOW,
    ).stdout.strip()
    return float(out)


CLIP_TS = re.compile(r"^(?:kill\d*(?:_death)?|death)_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})")
DUP_WINDOW_S = 3.0          # gleicher Kill, wenn die Zeitstempel so nah beieinander liegen
clip_lock = threading.Lock()  # Prüfen + Schneiden am Stück (Live und Scan gleichzeitig)


def clip_time(path: Path) -> datetime | None:
    m = CLIP_TS.match(path.stem)
    return datetime.strptime(m[1], "%Y-%m-%d_%H-%M-%S") if m else None


def find_duplicate(roots, when: datetime) -> Path | None:
    """Gibt einen vorhandenen Clip zurück, der denselben Kill zeigt (Ordner + direkte Unterordner)."""
    for root in dict.fromkeys(Path(r) for r in roots):
        if not root.is_dir():
            continue
        for clip in (*root.glob("*.mp4"), *root.glob("*/*.mp4")):
            if clip.parent != root and (clip.parent.name.startswith("_") or clip.parent.name == "Montagen"):
                continue  # _Duplikate, _Fehlerkennung, _Original zählen nicht als vorhanden
            t = clip_time(clip)
            if t and abs((t - when).total_seconds()) <= DUP_WINDOW_S:
                return clip
    return None


def clip_name(out_dir: Path, label: str, when: datetime) -> Path:
    base = out_dir / f"{label}_{when:%Y-%m-%d_%H-%M-%S}"
    path, i = base.with_suffix(".mp4"), 2
    while path.exists():
        path, i = Path(f"{base}_{i}.mp4"), i + 1
    return path


def cut(src: Path, start: float, end: float, dst: Path) -> Path:
    """Schneidet [start, end] ohne Neu-Encodieren – keine GPU-/CPU-Last, Start auf Keyframe genau."""
    subprocess.run(
        [tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", str(src),
         "-t", f"{end - max(0.0, start):.3f}", "-map", "0", "-c", "copy",
         "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(dst)],
        check=True, capture_output=True, creationflags=NO_WINDOW | BELOW_NORMAL,
    )
    return dst


def thumbnail(clip: Path, at: float, width=360) -> bytes:
    """JPEG-Vorschaubild aus dem Clip (für die Galerie)."""
    return subprocess.run(
        [tool("ffmpeg"), "-v", "error", "-ss", f"{at:.2f}", "-i", str(clip), "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "6", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
        capture_output=True, check=True, creationflags=NO_WINDOW | BELOW_NORMAL,
    ).stdout
