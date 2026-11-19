from .job_sort import sort_clip
"""Live-Modus: HUD beobachten, nach dem Kill das Replay (NVIDIA oder OBS) speichern und zuschneiden.

FPS-schonend: nur `live_fps` Bilder pro Sekunde, OCR nur bei Änderung im Notify-Bereich,
niedrige Prozess-Priorität, Schnitt per Stream-Copy (kein Encoder).
"""
import ctypes
import threading
import time
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path

import dxcam
import psutil

from . import clips
from .config import Config, key_name, nvidia_hotkey
from .detector import Detector, Preset, Segment, Segmenter
from .obs import ObsClient, ObsError, launch_obs, obs_running
from . import preview

GAME_PROCESS_HINT = "gtaprocess"  # FiveM_b3407_GTAProcess.exe o. ä.

# --- Tastendruck an NVIDIA (SendInput) ---------------------------------------------------
_ULONG_PTR = ctypes.c_size_t


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_user32 = ctypes.windll.user32
_KEYUP = 0x0002


def _key(vk: int, up: bool):
    scan = _user32.MapVirtualKeyW(vk, 0)
    inp = _INPUT(type=1, u=_INPUTUNION(ki=_KEYBDINPUT(vk, scan, _KEYUP if up else 0, 0, 0)))
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


# Tasten, die zusammen mit unserem künstlichen Alt das Spiel verlassen würden:
# Tab (Alt+Tab, z. B. Waffenrad/Inventar), Esc (Alt+Esc), Leertaste (Alt+Leertaste = Fenstermenü),
# Enter (Alt+Enter = Vollbild umschalten), F4 (Alt+F4 = Spiel schließen)
_DANGER_KEYS = (0x09, 0x1B, 0x20, 0x0D, 0x73)


def _extra_modifiers_down(vks):
    families = ((0x10, 0xA0, 0xA1), (0x11, 0xA2, 0xA3),
                (0x12, 0xA4, 0xA5), (0x5B, 0x5C))
    wanted = set(vks)
    if any(_user32.GetAsyncKeyState(key) & 0x8000
           for family in families if not wanted.intersection(family)
           for key in family):
        return True
    alt_in_hotkey = wanted.intersection((0x12, 0xA4, 0xA5))
    return bool(alt_in_hotkey) and any(_user32.GetAsyncKeyState(k) & 0x8000 for k in _DANGER_KEYS if k not in wanted)


def press_hotkey(vks: list[int]):
    # In particular, Shift + Alt+F10 would DISABLE Instant Replay.
    # Alt so kurz wie möglich halten: je kürzer, desto kleiner das Fenster für Alt+Tab & Co.
    if _extra_modifiers_down(vks):
        return False
    pressed = []
    try:
        for vk in vks:
            if _extra_modifiers_down(vks):
                return False
            _key(vk, False)
            pressed.append(vk)
            time.sleep(0.02)
        time.sleep(0.04)
        return True
    finally:
        for vk in reversed(pressed):
            _key(vk, True)
            time.sleep(0.01)


# --- Fokus: nach dem Hotkey sicher im Spiel bleiben ----------------------------------------
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
_user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
_user32.IsIconic.argtypes = (wintypes.HWND,)
_user32.IsWindow.argtypes = (wintypes.HWND,)
_user32.BringWindowToTop.argtypes = (wintypes.HWND,)
_user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
_kernel32 = ctypes.windll.kernel32


def _window_exe(hwnd) -> str:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name()
    except (psutil.Error, ValueError):
        return "?"


def game_window():
    """Vordergrundfenster, falls es FiveM ist, sonst None."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    name = _window_exe(hwnd).lower()
    return hwnd if GAME_PROCESS_HINT in name and "fivem" in name else None


def _focus(hwnd) -> bool:
    if not _user32.IsWindow(hwnd):
        return False
    fg = _user32.GetForegroundWindow()
    me = _kernel32.GetCurrentThreadId()
    other = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(other) and other != me and _user32.AttachThreadInput(me, other, True)
    try:
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, 9)        # SW_RESTORE
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            _user32.AttachThreadInput(me, other, False)
    return _user32.GetForegroundWindow() == hwnd


def keep_game_focused(game, log, seconds=2.5):
    """Nach dem Hotkey: verliert FiveM den Fokus, sofort zurückholen und melden, wer ihn hatte."""
    if not game:
        return
    deadline = time.monotonic() + seconds
    reported = False
    while time.monotonic() < deadline:
        fg = _user32.GetForegroundWindow()
        if fg and fg != game:
            if not reported:
                log(f"⚠ Spiel hat beim Clippen den Fokus verloren (an {_window_exe(fg)}) – hole es zurück.")
                reported = True
            if not any(_user32.GetAsyncKeyState(k) & 0x8000 for k in (0x12, 0x09)):  # Alt+Tab-Menü noch offen? abwarten
                _focus(game)
        time.sleep(0.05)


# --- Replay-Datei abwarten ------------------------------------------------------------------
def _videos(folder: Path) -> dict[Path, float]:
    return {p: p.stat().st_mtime for p in folder.rglob("*.mp4")}


def wait_for_replay(folder: Path, before: dict, timeout=20.0) -> Path | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.0)
        # eigene Clips (Kills-Ordner) nicht für ein Replay halten
        new = [p for p in folder.rglob("*.mp4") if p not in before and p.parent.name != "Kills"]
        if new:
            path = max(new, key=lambda p: p.stat().st_mtime)
            # warten, bis NVIDIA fertig geschrieben hat
            last = -1
            while time.time() < deadline + 30:
                size = path.stat().st_size
                if size == last and size > 0:
                    return path
                last = size
                time.sleep(1.0)
            return path
    return None


def _is_black(clip: Path, at: float) -> bool:
    import cv2
    cap = cv2.VideoCapture(str(clip))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, at) * 1000)
        ok, frame = cap.read()
        return bool(ok) and float(frame.mean()) < 3.0
    finally:
        cap.release()


def game_running() -> bool:
    for proc in psutil.process_iter(["name"]):
        name = (proc.info["name"] or "").lower()
        if GAME_PROCESS_HINT in name and "fivem" in name:
            return True
    return False


class LiveClipper(threading.Thread):
    def __init__(self, cfg: Config, log, status, on_clip=lambda _p, _s: None):
        """status(text, on, hot): on = Erkennung läuft, hot = Kill erkannt, Clip steht aus."""
        super().__init__(daemon=True)
        self.cfg, self.log, self.status, self.on_clip = cfg, log, status, on_clip
        self.stop_event = threading.Event()
        self._save_lock = threading.Lock()
        self._last_obs_error = None

    def stop(self):
        self.stop_event.set()

    def run(self):
        try:
            self._run()
        except Exception as exc:  # Fehler im Fenster zeigen statt still sterben
            self.log(f"Live-Fehler: {exc}")
            self.status("Live: Fehler", False, False)

    def _run(self):
        cfg = self.cfg
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        if cfg.clip_source != "obs":
            self._warn_buffer()

        preset = Preset.load(cfg.preset)
        events = {"kill", "death"} if cfg.clip_deaths else {"kill"}
        interval = 1.0 / cfg.live_fps
        cam = None

        while not self.stop_event.is_set():
            if not game_running():
                if cam:
                    cam.release()
                    cam = None
                self.status("Live: wartet auf FiveM …", False, False)
                self.stop_event.wait(5)
                continue

            if cam is None:
                if cfg.clip_source == "obs" and not self._prepare_obs():
                    self.stop_event.wait(10)
                    continue
                cam = dxcam.create(output_idx=cfg.monitor, output_color="BGR")
                detector = Detector(preset, events, cfg.detect_mode)
                segmenter = Segmenter(cfg.pre_s, cfg.post_s, join_overlap=False)
                interval = 1.0 / max(cfg.live_fps, detector.min_fps)
                frame = None
                last_game_check = time.monotonic()
                self.status("Live: aktiv – wartet auf Kills", True, False)

            tick = time.monotonic()
            grabbed = cam.grab()
            if grabbed is not None:
                frame = grabbed
            if frame is not None:
                for event in detector.update(tick, frame):
                    self.log(f"{event.upper()} erkannt ({datetime.now():%H:%M:%S})")
                    preview.publish(frame, preview.kill_region(detector),
                                    f"Live · {'Kill' if event == 'kill' else 'Tod'} um {datetime.now():%H:%M:%S}")
                    self.status(f"{event.upper()} erkannt – Clip in {int(cfg.post_s)} s", True, True)
                    done = segmenter.add(tick, event)
                    if done:
                        self._save(done, tick)

            seg = segmenter.current
            if seg:
                # Clip würde länger als der Replay-Puffer -> jetzt sichern
                if tick - seg.start >= cfg.replay_buffer_s - 3:
                    seg.end = tick
                done = segmenter.pop_if_done(tick)
                if done:
                    self._save(done, tick)

            if tick - last_game_check > 5:
                last_game_check = tick
                if not game_running():
                    continue
            self.stop_event.wait(max(0.0, interval - (time.monotonic() - tick)))

        if cam:
            cam.release()
        self.status("Live: aus", False, False)

    def _warn_buffer(self):
        cfg = self.cfg
        need = cfg.pre_s + cfg.post_s + 5
        if cfg.replay_buffer_s < need:
            where = "OBS (Einstellungen → Ausgabe → Replay-Puffer)" if cfg.clip_source == "obs" else "der NVIDIA App"
            self.log(f"⚠ Replay-Länge ist {cfg.replay_buffer_s} s – in {where} auf mindestens {int(need)} s "
                     "stellen (besser 2–3 min), sonst fehlt der Vorlauf.")

    def _obs(self) -> ObsClient:
        return ObsClient(port=self.cfg.obs_port, password=self.cfg.obs_password)

    def _prepare_obs(self) -> bool:
        """OBS verbinden und Replay-Puffer starten (schaltet ihn bei Bedarf ein)."""
        if self.cfg.obs_autostart and not obs_running() and launch_obs():
            self.log("Starte OBS (minimiert) …")
            deadline = time.monotonic() + 40          # OBS braucht beim Start ein paar Sekunden
            while time.monotonic() < deadline and not self.stop_event.is_set():
                self.stop_event.wait(2)
                try:
                    with self._obs() as obs:
                        obs.request("GetCurrentProgramScene")   # erst „bereit“, wenn OBS fertig geladen hat
                        break
                except (ObsError, OSError):
                    pass
        try:
            with self._obs() as obs:
                if obs.ensure_source():
                    self.log("OBS: Szene war leer – Bildschirmaufnahme angelegt")
                for name in obs.fix_fivem_sources():
                    self.log(f"OBS: Quelle „{name}“ auf neue FiveM-Version umgestellt")
                self.cfg.replay_buffer_s = obs.ensure_replay_buffer()
        except (ObsError, OSError) as exc:
            if str(exc) != self._last_obs_error:   # nicht alle 10 s dieselbe Meldung
                self._last_obs_error = str(exc)
                self.log(f"✖ {exc}")
            self.status("Live: wartet auf OBS …", False, False)
            return False
        self._last_obs_error = None
        self.log(f"OBS verbunden – Replay-Puffer läuft ({self.cfg.replay_buffer_s} s)")
        self._warn_buffer()
        return True

    def _save(self, seg: Segment, now: float):
        # Wait for sprint/crouch keys in a worker, never in the detection loop.
        threading.Thread(target=self._save_worker, args=(seg, now), daemon=True).start()

    def _save_worker(self, seg: Segment, now: float):
        with self._save_lock:
            if self.cfg.clip_source == "obs":
                # Kein Tastendruck nötig – OBS speichert per WebSocket und nennt die Datei
                pressed, wall = time.monotonic(), datetime.now()
                try:
                    with self._obs() as obs:
                        obs.ensure_replay_buffer()   # falls OBS zwischendurch neu gestartet wurde
                        replay = obs.save_replay()
                except (ObsError, OSError) as exc:
                    self.log(f"✖ Replay nicht gespeichert: {exc}")
                    self.status("Live: aktiv – wartet auf Kills", True, False)
                    return
                self._finish(seg, now, pressed, wall, replay=replay)
                return
            try:
                folder = Path(self.cfg.replay_dir)
                # Hotkey frisch aus der NVIDIA App – Änderung (z. B. auf Einfg) gilt ohne Neustart
                hotkey = nvidia_hotkey() or self.cfg.replay_hotkey
                if hotkey != self.cfg.replay_hotkey:
                    self.log(f"NVIDIA-Hotkey geändert – ReplayClip drückt jetzt {'+'.join(map(key_name, hotkey))}")
                    self.cfg.replay_hotkey = hotkey
                deadline = time.monotonic() + 30.0
                waiting = False
                while not self.stop_event.is_set():
                    if _extra_modifiers_down(self.cfg.replay_hotkey):
                        if not waiting:
                            self.log("Kill vorgemerkt: Shift/Strg/Tab/Esc/Leertaste kurz loslassen.")
                            waiting = True
                    else:
                        before = _videos(folder)
                        game = game_window()
                        if press_hotkey(self.cfg.replay_hotkey):
                            pressed = time.monotonic()
                            wall = datetime.now()
                            threading.Thread(target=keep_game_focused, args=(game, self.log), daemon=True).start()
                            self._finish(seg, now, pressed, wall, folder, before)
                            return
                    if time.monotonic() >= deadline:
                        self.log("Replay nicht gespeichert: Zusatztaste blieb gedrueckt. Live-Erkennung laeuft weiter.")
                        self.status("Live: aktiv - wartet auf Kills", True, False)
                        return
                    self.stop_event.wait(0.05)
            except Exception as exc:
                self.log(f"Replay-Speichern fehlgeschlagen: {exc}")
                self.status("Live: aktiv - wartet auf Kills", True, False)

    def _finish(self, seg: Segment, now, pressed, wall, folder=None, before=None, replay=None):
        cfg = self.cfg
        replay = replay or wait_for_replay(folder, before)
        if not replay:
            self.log("✖ Kein Replay gespeichert – ist NVIDIA Instant Replay an? (Alt+Shift+F10)")
            self.status("Live: aktiv – wartet auf Kills", True, False)
            return
        try:
            length = clips.duration(replay)
            # Replay endet beim Hotkey-Druck
            start = length - (pressed - seg.start)
            end = length - max(0.0, pressed - seg.end)
            kill_at = length - (pressed - seg.first)
            if kill_at < 0:
                # Replay beginnt erst nach dem Kill (NVIDIA-Puffer nach dem letzten Speichern zu kurz) -> nur Leerlauf
                self.log(f"Übersprungen: Replay nur {length:.0f} s lang, der Kill liegt davor – kein Clip.")
                if cfg.delete_replay_after_cut:
                    replay.unlink(missing_ok=True)
                self.status("Live: aktiv – wartet auf Kills", True, False)
                return
            if start < -1:
                self.log(f"⚠ Replay nur {length:.0f} s lang – Vorlauf um {-start:.0f} s kürzer "
                         "(NVIDIA-Puffer war gerade neu gestartet)")
            when = wall - timedelta(seconds=pressed - seg.first)
            seg_dir = clips.target_dir(cfg.ensure_output(), seg.label)
            with clips.clip_lock:
                dup = clips.find_duplicate(
                    [seg_dir, *(clips.target_dir(f, seg.label) for f in cfg.kill_folders)], when, seg.label)
                if not dup:
                    seg_dir.mkdir(parents=True, exist_ok=True)
                    dst = clips.clip_name(seg_dir, seg.label, when)
                    clips.cut(replay, start, end, dst)
            if dup:
                self.log(f"Schon vorhanden, übersprungen: {dup.name}")
                if cfg.delete_replay_after_cut:
                    replay.unlink(missing_ok=True)
                self.status("Live: aktiv – wartet auf Kills", True, False)
                return
            if cfg.clip_source == "obs" and _is_black(dst, seg.first - seg.start):
                self.log("⚠ Clip ist schwarz – in OBS zeigt die Spiel-/Bildschirmquelle kein Bild. "
                         "OBS öffnen und die Quelle prüfen (oder „Bildschirmaufnahme“ hinzufügen).")
            dst = sort_clip(dst, seg_dir, self.log, cfg.preset)
            self.log(f"✔ {dst.name} ({end - max(0.0, start):.0f} s)")
            self.on_clip(dst, seg)
            if cfg.delete_replay_after_cut:
                replay.unlink(missing_ok=True)
        except Exception as exc:
            self.log(f"✖ Schneiden fehlgeschlagen ({replay.name}): {exc}")
        self.status("Live: aktiv – wartet auf Kills", True, False)
