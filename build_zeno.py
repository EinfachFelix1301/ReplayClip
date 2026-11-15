"""Baut die Weitergabe-Version für ZenoCity-Spieler: E:\EinfachFelix\KillClip-Zeno\KillClip + .zip.

Ohne persönliche config.json (entsteht beim ersten Start beim Spieler). FFmpeg kommt aus FFMPEG_DIR.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = Path(r"E:\EinfachFelix\KillClip-Zeno")
FFMPEG_DIR = TARGET / "KillClip" / "_internal" / "bin"   # ffmpeg.exe, ffprobe.exe, LICENSE aus dem letzten Paket
SEVEN_ZIP = Path(r"C:\Program Files\7-Zip\7z.exe")
WORK = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_build"


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    ff = WORK / "ffmpeg"
    if FFMPEG_DIR.is_dir():
        shutil.copytree(FFMPEG_DIR, ff, dirs_exist_ok=True)
    for name in ("ffmpeg.exe", "ffprobe.exe", "LICENSE"):
        if not (ff / name).is_file():
            raise SystemExit(f"{name} fehlt in {ff}")

    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", "KillClip", "--distpath", str(WORK / "dist"), "--workpath", str(WORK / "build"),
        "--specpath", str(WORK),
        "--add-data", f"{ROOT / 'killclip' / 'ui'};killclip/ui",
        "--add-data", f"{ROOT / 'assets' / 'killclip.ico'};assets",
        "--icon", str(ROOT / "assets" / "killclip.ico"),
        *[a for n in ("ffmpeg.exe", "ffprobe.exe", "LICENSE") for a in ("--add-binary", f"{ff / n};bin")],
        "--collect-submodules", "winrt", "--hidden-import", "webview.platforms.winforms",
        str(ROOT / "KillClip.pyw"),
    ], check=True, cwd=ROOT)

    app = WORK / "dist" / "KillClip"
    (app / "presets").mkdir(exist_ok=True)
    for name in ("zenocity.json", "zenocity_killdot.png", "zenocity_logo.png", "chakra-petch-600.ttf", "chakra-petch-700.ttf"):
        shutil.copy2(ROOT / "presets" / name, app / "presets" / name)
    shutil.copy2(ROOT / "LIESMICH-ZENO.txt", app / "LIESMICH.txt")
    shutil.copy2(ROOT / "ANLEITUNG-ZENO.html", app / "ANLEITUNG.html")

    subprocess.run([str(app / "KillClip.exe"), "--self-test"], check=True, timeout=120)
    result = (app / "selftest.txt").read_text(encoding="utf-8")
    print(result)
    (app / "selftest.txt").unlink()
    (app / "config.json").unlink(missing_ok=True)
    if not result.startswith("OK"):
        raise SystemExit("Self-Test fehlgeschlagen")

    out = TARGET / "KillClip"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(app, out)
    (TARGET / "KillClip-Zeno.7z").unlink(missing_ok=True)   # alte 7z-Version; ZIP öffnet jedes Windows
    archive = TARGET / "KillClip-Zeno.zip"
    archive.unlink(missing_ok=True)
    subprocess.run([str(SEVEN_ZIP), "a", "-tzip", "-mx=7", str(archive), "KillClip"], check=True, cwd=TARGET,
                   stdout=subprocess.DEVNULL)
    print(f"Fertig: {archive} ({archive.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
