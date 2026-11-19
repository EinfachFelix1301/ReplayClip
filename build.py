"""Baut ReplayClip als Windows-Ordner (PyInstaller) und packt ihn als ZIP.

Aufruf (im Projektordner, mit installiertem Python 3.12 und `pip install -r requirements.txt`):

    pip install pyinstaller
    python build.py

Ergebnis: dist\\ReplayClip-Windows.zip (wird nicht ins Repo committet, sondern als GitHub-Release hochgeladen).

Optional: FFmpeg mit ins Paket nehmen. Dafür FFMPEG_DIR auf einen Ordner setzen, der ffmpeg.exe, ffprobe.exe
und LICENSE enthält. Ohne diese Variable braucht der Spieler FFmpeg im PATH (siehe README).
"""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "_build"
DIST = ROOT / "dist"
APP_NAME = "ReplayClip"
FFMPEG_DIR = Path(os.environ["FFMPEG_DIR"]) if os.environ.get("FFMPEG_DIR") else None


def run(cmd, **kw):
    print("›", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kw)


def main():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)

    binaries = []
    if FFMPEG_DIR:
        for name in ("ffmpeg.exe", "ffprobe.exe", "LICENSE"):
            if not (FFMPEG_DIR / name).is_file():
                raise SystemExit(f"{name} fehlt in {FFMPEG_DIR}")
            binaries += ["--add-binary", f"{FFMPEG_DIR / name};bin"]

    run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", APP_NAME, "--distpath", str(WORK / "dist"), "--workpath", str(WORK / "build"),
        "--specpath", str(WORK),
        "--add-data", f"{ROOT / 'replayclip' / 'ui'};replayclip/ui",
        "--add-data", f"{ROOT / 'assets' / 'replayclip.ico'};assets",
        "--icon", str(ROOT / "assets" / "replayclip.ico"),
        *binaries,
        "--collect-submodules", "winrt", "--collect-submodules", "yt_dlp",
        "--hidden-import", "webview.platforms.winforms",
        str(ROOT / "ReplayClip.pyw"),
    ], cwd=ROOT)

    app = WORK / "dist" / APP_NAME
    (app / "presets").mkdir(exist_ok=True)
    for name in ("beispiel.json", "beispiel_killdot.png", "chakra-petch-600.ttf", "chakra-petch-700.ttf",
                 "OFL-ChakraPetch.txt"):
        shutil.copy2(ROOT / "presets" / name, app / "presets" / name)
    shutil.copy2(ROOT / "LICENSE", app / "LICENSE.txt")
    shutil.copy2(ROOT / "README.md", app / "README.md")

    # Eingebauter Selbsttest (Preset, Pfade, OCR); schreibt selftest.txt neben die EXE
    run([str(app / f"{APP_NAME}.exe"), "--self-test"], timeout=180)
    result = (app / "selftest.txt").read_text(encoding="utf-8")
    print(result)
    (app / "selftest.txt").unlink()
    (app / "config.json").unlink(missing_ok=True)
    if not result.startswith("OK"):
        raise SystemExit("Self-Test fehlgeschlagen")

    DIST.mkdir(exist_ok=True)
    archive = DIST / f"{APP_NAME}-Windows.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(app.rglob("*")):
            if f.is_file():
                z.write(f, Path(APP_NAME) / f.relative_to(app))
    print(f"fertig: {archive} ({archive.stat().st_size // 1_000_000} MB)")


if __name__ == "__main__":
    main()
