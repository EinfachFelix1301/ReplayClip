"""Einstellungen laden/speichern + NVIDIA-Instant-Replay-Werte aus der Registry."""
import json
import os
import sys
import winreg
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Als EXE: Einstellungen + Presets neben ReplayClip.exe (portabel), sonst im Projektordner
ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
PRESET_DIR = ROOT / "presets"

NV_KEY = r"Software\NVIDIA Corporation\Global\ShadowPlay\NVSPCAPS"


def _nv_value(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, NV_KEY) as k:
            value, _ = winreg.QueryValueEx(k, name)
            return value
    except OSError:
        return None


def _nv_dword(name):
    v = _nv_value(name)
    if isinstance(v, (bytes, bytearray)) and len(v) >= 4:
        return int.from_bytes(v[:4], "little")
    return v if isinstance(v, int) else None


KEY_NAMES = {0x10: "Shift", 0x11: "Strg", 0x12: "Alt", 0x13: "Pause", 0x14: "Feststell", 0x20: "Leertaste",
             0x21: "Bild↑", 0x22: "Bild↓", 0x23: "Ende", 0x24: "Pos1", 0x2C: "Druck", 0x2D: "Einfg", 0x2E: "Entf",
             0x91: "Rollen", **{0x60 + i: f"Num{i}" for i in range(10)}, **{0x70 + i: f"F{i + 1}" for i in range(24)}}


def key_name(vk: int) -> str:
    return KEY_NAMES.get(vk, chr(vk) if 0x30 <= vk <= 0x5A else hex(vk))


def nvidia_hotkey() -> list:
    """Aktueller „Replay speichern“-Hotkey aus der NVIDIA App (VK-Codes), [] wenn nicht lesbar."""
    count = _nv_dword("DVRHKeyCount") or 0
    return [k for k in (_nv_dword(f"DVRHKey{i}") for i in range(count)) if k]


def nvidia_defaults():
    """Speicherordner, Replay-Länge (s) und Hotkey (VK-Codes) aus den NVIDIA-Einstellungen."""
    path = _nv_value("DefaultPathW")
    if isinstance(path, (bytes, bytearray)):
        path = path.decode("utf-16-le").rstrip("\0")
    return {
        "replay_dir": path or str(Path.home() / "Videos"),
        "replay_buffer_s": _nv_dword("DVRBufferLen") or 60,
        "replay_hotkey": nvidia_hotkey() or [0x12, 0x79],  # Alt+F10
    }


@dataclass
class Config:
    preset: str = "beispiel"
    detect_mode: str = "beide"      # "beide" (X oder Meldung reicht), "killdot" (rotes X) oder "notify" (Meldung)
    pre_s: float = 20.0
    post_s: float = 20.0
    clip_deaths: bool = False
    live_enabled: bool = True
    live_fps: float = 2.0
    monitor: int = 0
    output_dir: str = str(Path.home() / "Videos" / "ReplayClips")
    delete_replay_after_cut: bool = True
    replay_dir: str = ""
    replay_buffer_s: int = 0
    replay_hotkey: list = field(default_factory=list)
    batch_fps: float = 2.0
    clip_source: str = "nvidia"     # "nvidia" (Instant Replay per Hotkey) oder "obs" (Replay-Puffer per WebSocket)
    obs_port: int = 0               # 0 = aus der OBS-Installation lesen (Standard 4455)
    obs_password: str = ""          # leer = aus der OBS-Installation lesen
    obs_autostart: bool = True      # OBS selbst (minimiert) starten, wenn FiveM läuft
    kill_folders: list = field(default_factory=list)  # Kills-Ordner aus Scans (für die Galerie)

    @classmethod
    def load(cls):
        data = {}
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        cfg = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        nv = nvidia_defaults()
        # NVIDIA-Werte immer frisch lesen, falls in der NVIDIA App geändert
        cfg.replay_dir = cfg.replay_dir or nv["replay_dir"]
        cfg.replay_buffer_s = nv["replay_buffer_s"]
        # Hotkey: was in der NVIDIA App eingestellt ist, gewinnt (z. B. Einfg statt Alt+F10)
        cfg.replay_hotkey = nvidia_hotkey() or cfg.replay_hotkey or nv["replay_hotkey"]
        return cfg

    def save(self):
        data = asdict(self)
        data.pop("replay_buffer_s")
        CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def ensure_output(self):
        os.makedirs(self.output_dir, exist_ok=True)
        return Path(self.output_dir)
