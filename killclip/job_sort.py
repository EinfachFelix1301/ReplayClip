"""Clips nach Job/Fraktion sortieren: Name aus dem HUD lesen, Ordner anlegen, Job in den Dateinamen.

1. Bekannte Jobs (Preset-Liste + schon vorhandene Ordner) im HUD-Text suchen – Reihenfolge = Vorrang
   (Fraktionen vor Arbeitsjobs, falls das HUD beides zeigt).
2. Sonst: Zeile direkt über der Straße = Jobname. Nur übernehmen, wenn er auf ≥3 von 6 Bildern gleich ist.
Unsichere Clips bleiben, wo sie sind.
"""
import json
import re
import threading
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np

from .config import PRESET_DIR
from .ocr import read_lines

_move_lock = threading.Lock()
SPECIAL = {"montagen", "musik"}            # dazu alles mit "_" vorne (_Original, _Duplikate, …)
_SKIP = re.compile(r"LEITSTELLE|DIENST|OFFEN|HUNGER|DURST|ANGENOMMEN", re.I)
# Straßenzeile (GTA-Straßennamen + OCR-Varianten) – zählt nie als Job (sonst wird „Grove St“ zu „Grove Street“)
_STREET = re.compile(r"\b(ST|STR|SI|RD|RO|AVE|AV|BLVD|BLVO|DR|OR|WAY|LN|PL|PKWY|HWY|FWY|FVVY|APPROACH|AP)\b[ .\-]*"
                     r"(N|S|O|W|NO|SO|NW|SW|NE|SE)?\s*$", re.I)
FRAMES = (.1, .25, .4, .55, .7, .85)


def is_job_dir(d: Path, root: Path) -> bool:
    return d.is_dir() and d.parent == root and not d.name.startswith("_") and d.name.lower() not in SPECIAL


def job_dirs(root) -> list[Path]:
    root = Path(root)
    return sorted(d for d in root.iterdir() if is_job_dir(d, root)) if root.is_dir() else []


def _key(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def known_jobs(preset: str, root) -> list[tuple[str, str]]:
    """[(Suchtext, Ordnername)] – Preset-Liste zuerst (Vorrang), dann schon vorhandene Ordner."""
    out = []
    try:
        data = json.loads((PRESET_DIR / f"{preset}.json").read_text(encoding="utf-8"))
        out = [(_key(j["match"]), j.get("name") or j["match"]) for j in data.get("jobs", [])]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    have = {k for k, _ in out}
    out += [(_key(d.name), d.name) for d in job_dirs(root) if _key(d.name) not in have]
    return [(k, n) for k, n in out if k]


def _white_text(crop: np.ndarray) -> np.ndarray:
    """Nur weiße HUD-Schrift behalten, farbige Rang-Kästen ausblenden, 4x vergrößern -> schwarz auf weiß."""
    big = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    _, s, v = cv2.split(cv2.cvtColor(big, cv2.COLOR_BGR2HSV))
    badge = cv2.dilate(((s > 110) & (v > 70)).astype(np.uint8) * 255, np.ones((9, 25), np.uint8))
    text = ((v > 150) & (s < 70)).astype(np.uint8) * 255
    text[badge > 0] = 0
    return cv2.cvtColor(255 - text, cv2.COLOR_GRAY2BGR)


def _clean(line: str) -> str:
    t = re.sub(r"[^A-Z0-9' ]", "", line.upper()).strip()
    compact = t.replace(" ", "")
    if re.fullmatch(r"[A-Z]{3}O", compact):          # OCR liest D gern als O: LSPO -> LSPD
        t = compact[:3] + "D"
    return t


def _contains(text_key: str, words: list[str], key: str) -> bool:
    if len(key) <= 4:                                 # kurze Kürzel (LSPD, FIB, DOJ) nur exakt als Wort
        return key in words or key in {w[:len(key)] for w in words if len(w) <= len(key) + 1}
    return key in text_key or any(SequenceMatcher(None, key, w).ratio() >= 0.8 for w in words if len(w) >= 4)


def detect_job(path, preset: str = "zenocity", root=None) -> str | None:
    known = known_jobs(preset, root) if root else []
    cap = cv2.VideoCapture(str(path))
    known_votes, free_votes = Counter(), Counter()
    try:
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        for fraction in FRAMES:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(frames * fraction)))
            ok, frame = cap.read()
            if not ok:
                continue
            h, w = frame.shape[:2]
            crop = frame[int(h * .79):int(h * .97), int(w * .13):int(w * .40)]
            white = read_lines(_white_text(crop), 2160)
            narrow = frame[int(h * .79):int(h * .97), int(w * .15):int(w * .38)]
            lines = [l for l in white + read_lines(crop, 360) + read_lines(narrow, 360) if not _STREET.search(l.strip())]
            words = [_key(x) for line in lines for x in _clean(line).split()]
            text_key = _key(" ".join(lines))
            hits = [n for k, n in known if _contains(text_key, words, k)]
            for n in dict.fromkeys(hits):
                known_votes[n] += 1
            cands = [_clean(l) for l in white if not _SKIP.search(l)]
            cands = [c for c in cands if len(re.sub(r"[^A-Z]", "", c)) >= 3]
            if len(cands) >= 2:                      # Zeile über der Straße
                free_votes[cands[-2]] += 1
    finally:
        cap.release()
    if known_votes:
        order = {n: i for i, (_, n) in enumerate(known)}
        best = max(known_votes.items(), key=lambda kv: (kv[1], -order.get(kv[0], 99)))
        if best[1] >= 2:
            return best[0]
    if free_votes:
        name, votes = free_votes.most_common(1)[0]
        if votes >= 3:
            return name if len(name) <= 4 else name.title()
    return None


def safe_dir_name(job: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "", job).strip(" .")[:40]


def sort_clip(path, output_dir, log=print, preset: str = "zenocity"):
    path = Path(path).resolve()
    root = Path(output_dir).resolve()
    # Nie ein Original-Replay oder fremdes Video verschieben – nur eigene Clips im Ausgabeordner
    if not (path.parent == root or is_job_dir(path.parent, root)):
        return path
    try:
        job = detect_job(path, preset, root)
        if not job:
            log(f"Job nicht eindeutig erkannt: {path.name} bleibt im bisherigen Ordner.")
            return path
        folder_name = safe_dir_name(job)
        if not folder_name:
            return path
        tag = folder_name.replace(" ", "-")
        with _move_lock:
            folder = root / folder_name
            folder.mkdir(parents=True, exist_ok=True)
            if folder.resolve().parent != root or folder.is_symlink():
                raise RuntimeError("Unerwarteter Zielordner")
            stem = path.stem if path.stem.endswith(f"_{tag}") else f"{path.stem}_{tag}"   # Job im Dateinamen
            if path.parent == folder and stem == path.stem:
                return path
            dest = folder / f"{stem}{path.suffix}"
            number = 2
            while dest.exists():
                dest = folder / f"{stem}_{number}{path.suffix}"
                number += 1
            path.rename(dest)  # Windows rename überschreibt nie
        log(f"Job {folder_name}: {dest.name}")
        return dest
    except Exception as error:
        log(f"Job-Sortierung fehlgeschlagen; Clip bleibt gespeichert: {error}")
        return path
