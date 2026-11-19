"""OBS-Anbindung über obs-websocket (ab OBS 28 eingebaut): Replay-Puffer starten, Länge lesen, speichern.

Pro Aktion eine kurze Verbindung (Handshake ~50 ms) – kein Dauer-Socket, der still abreißen kann.
"""
import base64
import hashlib
import json
import os
import subprocess
import time
import uuid
import winreg
from pathlib import Path

import psutil
import websocket

OBS_WS_CONFIG = Path(os.environ.get("APPDATA", "")) / "obs-studio" / "plugin_config" / "obs-websocket" / "config.json"
EVENTS_OUTPUTS = 1 << 6         # Abo für ReplayBufferSaved/ReplayBufferStateChanged
AUTH_FAILED = 4009


class ObsError(RuntimeError):
    pass


def local_settings() -> dict:
    """Port/Passwort aus der OBS-Installation auf diesem PC (spart das Abtippen)."""
    try:
        data = json.loads(OBS_WS_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {"enabled": bool(data.get("server_enabled")), "port": int(data.get("server_port") or 4455),
            "password": (data.get("server_password") or "") if data.get("auth_required") else ""}


def obs_exe() -> Path | None:
    """obs64.exe aus der Registry (Installer) oder dem Standardordner."""
    roots = []
    for key in (r"SOFTWARE\OBS Studio", r"SOFTWARE\WOW6432Node\OBS Studio"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                roots.append(Path(winreg.QueryValueEx(k, "")[0]))
        except OSError:
            pass
    roots.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "obs-studio")
    for root in roots:
        exe = root / "bin" / "64bit" / "obs64.exe"
        if exe.is_file():
            return exe
    return None


def obs_running() -> bool:
    return any((p.info["name"] or "").lower() == "obs64.exe" for p in psutil.process_iter(["name"]))


def launch_obs() -> bool:
    """OBS minimiert (im Tray) starten; WebSocket-Server vorher einschalten, falls er aus ist."""
    exe = obs_exe()
    if not exe or obs_running():
        return False
    try:
        data = json.loads(OBS_WS_CONFIG.read_text(encoding="utf-8"))
        if not data.get("server_enabled"):
            data["server_enabled"] = True
            OBS_WS_CONFIG.write_text(json.dumps(data, indent=4), encoding="utf-8")
    except (OSError, ValueError):
        pass  # Noch nie gestartet: obs-websocket legt die Datei an (Server ist dann standardmäßig aus)
    # Reste eines Absturzes -> OBS würde beim Start auf den Dialog „Absturz erkannt“ warten
    for stale in (OBS_WS_CONFIG.parents[2] / ".sentinel").glob("run_*"):
        try:
            stale.unlink()
        except OSError:
            pass
    subprocess.Popen([str(exe), "--minimize-to-tray", "--disable-shutdown-check"], cwd=str(exe.parent),
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                     close_fds=True)
    return True


def _b64sha(text: str) -> str:
    return base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode()


class ObsClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, password: str = "", timeout: float = 5.0):
        local = local_settings()
        self.host, self.timeout = host, timeout
        self.port = port or local.get("port", 4455)
        self.password = password or local.get("password", "")
        self._local_disabled = local.get("enabled") is False
        self._ws = None

    # --- Verbindung -----------------------------------------------------------------------
    def __enter__(self):
        try:
            self._ws = websocket.create_connection(f"ws://{self.host}:{self.port}", timeout=self.timeout)
        except OSError as exc:
            hint = ("In OBS: Werkzeuge → WebSocket-Server-Einstellungen → „WebSocket-Server aktivieren“."
                    if self._local_disabled else "Läuft OBS? (Ab OBS 28, Werkzeuge → WebSocket-Server-Einstellungen)")
            raise ObsError(f"OBS nicht erreichbar – {hint}") from exc
        hello = self._recv_op(0)
        identify = {"rpcVersion": 1, "eventSubscriptions": EVENTS_OUTPUTS}
        auth = hello.get("authentication")
        if auth:
            if not self.password:
                raise ObsError("OBS verlangt ein Passwort – in ReplayClip unter Einstellungen eintragen")
            secret = _b64sha(self.password + auth["salt"])
            identify["authentication"] = _b64sha(secret + auth["challenge"])
        self._send(1, identify)
        try:
            self._recv_op(2)
        except websocket.WebSocketConnectionClosedException as exc:
            if getattr(self._ws, "status", None) == AUTH_FAILED or auth:
                raise ObsError("OBS-Passwort falsch – in OBS unter Werkzeuge → WebSocket-Server-Einstellungen "
                               "nachsehen") from exc
            raise ObsError("OBS hat die Verbindung abgelehnt") from exc
        return self

    def __exit__(self, *_exc):
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def _send(self, op: int, data: dict):
        self._ws.send(json.dumps({"op": op, "d": data}))

    def _recv(self, deadline: float) -> dict:
        self._ws.settimeout(max(0.1, deadline - time.monotonic()))
        try:
            return json.loads(self._ws.recv())
        except websocket.WebSocketTimeoutException as exc:
            raise ObsError("OBS antwortet nicht") from exc

    def _recv_op(self, op: int, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            msg = self._recv(deadline)
            if msg.get("op") == op:
                return msg.get("d", {})

    # --- Befehle ----------------------------------------------------------------------------
    def request(self, kind: str, data: dict | None = None, timeout: float = 5.0) -> dict:
        rid = uuid.uuid4().hex
        self._send(6, {"requestType": kind, "requestId": rid, "requestData": data or {}})
        deadline = time.monotonic() + timeout
        while True:
            msg = self._recv(deadline)
            d = msg.get("d", {})
            if msg.get("op") == 7 and d.get("requestId") == rid:
                status = d.get("requestStatus", {})
                if not status.get("result"):
                    raise ObsError(f"OBS: {kind} fehlgeschlagen ({status.get('comment') or status.get('code')})")
                return d.get("responseData") or {}

    def replay_seconds(self) -> int:
        mode = self.request("GetProfileParameter", {"parameterCategory": "Output", "parameterName": "Mode"})
        cat = "AdvOut" if (mode.get("parameterValue") or "").lower() == "advanced" else "SimpleOutput"
        val = self.request("GetProfileParameter", {"parameterCategory": cat, "parameterName": "RecRBTime"})
        try:
            return int(val.get("parameterValue") or val.get("defaultParameterValue") or 20)
        except ValueError:
            return 20

    def _enable_replay_buffer(self, seconds: int):
        """Replay-Puffer im OBS-Profil anhaken. OBS übernimmt das erst beim Neuladen des Profils."""
        for cat in ("SimpleOutput", "AdvOut"):
            for name, value in (("RecRB", "true"), ("RecRBTime", str(seconds))):
                self.request("SetProfileParameter", {"parameterCategory": cat, "parameterName": name,
                                                     "parameterValue": value})
        current = self.request("GetProfileList")["currentProfileName"]
        helper = "ReplayClip-Neuladen"
        try:
            self.request("CreateProfile", {"profileName": helper})     # wechselt automatisch dorthin
            time.sleep(1.0)
        finally:
            self.request("SetCurrentProfile", {"profileName": current})
            time.sleep(1.5)
            try:
                self.request("RemoveProfile", {"profileName": helper})
            except ObsError:
                pass

    def fix_fivem_sources(self) -> list[str]:
        """FiveM-Updates benennen den Spielprozess um (FiveM_b3407_… -> FiveM_b3889_…). OBS-Quellen, die
        noch den alten Namen suchen, bleiben dann schwarz/stumm -> auf den laufenden Prozess umstellen."""
        running = next((p.info["name"] for p in psutil.process_iter(["name"])
                        if (p.info["name"] or "").lower().endswith("_gtaprocess.exe")), None)
        if not running:
            return []
        fixed = []
        for inp in self.request("GetInputList")["inputs"]:
            if inp.get("inputKind") not in ("game_capture", "window_capture", "wasapi_process_output_capture"):
                continue
            settings = self.request("GetInputSettings", {"inputName": inp["inputName"]})["inputSettings"]
            window = settings.get("window") or ""
            parts = window.split(":")
            if len(parts) >= 3 and parts[-1].lower().endswith("_gtaprocess.exe") and parts[-1] != running:
                parts[-1] = running
                self.request("SetInputSettings", {"inputName": inp["inputName"],
                                                  "inputSettings": {"window": ":".join(parts)}, "overlay": True})
                fixed.append(inp["inputName"])
        return fixed

    def ensure_source(self):
        """Leere Szene = schwarze Clips. Dann eine Bildschirmaufnahme anlegen."""
        scene = self.request("GetCurrentProgramScene")["currentProgramSceneName"]
        if not self.request("GetSceneItemList", {"sceneName": scene}).get("sceneItems"):
            self.request("CreateInput", {"sceneName": scene, "inputName": "ReplayClip Bildschirm",
                                         "inputKind": "monitor_capture", "inputSettings": {}})
            return True
        return False

    def ensure_replay_buffer(self, seconds: int = 180) -> int:
        """Startet den Replay-Puffer (schaltet ihn bei Bedarf in OBS ein). Gibt seine Länge in Sekunden zurück."""
        try:
            self.request("GetReplayBufferStatus")
        except ObsError:
            try:
                self._enable_replay_buffer(seconds)
                self.request("GetReplayBufferStatus")
            except ObsError as exc:
                raise ObsError("OBS-Replay-Puffer ist aus – in OBS: Einstellungen → Ausgabe → "
                               "„Replay-Puffer aktivieren“ anhaken") from exc
        if not self.request("GetReplayBufferStatus").get("outputActive"):
            try:
                self.request("StartReplayBuffer")
            except ObsError as exc:
                raise ObsError("OBS-Replay-Puffer lässt sich nicht starten – in OBS: Einstellungen → Ausgabe → "
                               "„Replay-Puffer aktivieren“ anhaken") from exc
            deadline = time.monotonic() + 5
            while not self.request("GetReplayBufferStatus").get("outputActive"):
                if time.monotonic() > deadline:
                    raise ObsError("OBS-Replay-Puffer startet nicht")
                time.sleep(0.3)
        return self.replay_seconds()

    def save_replay(self, timeout: float = 30.0) -> Path:
        """Speichert den Replay-Puffer und wartet, bis OBS die Datei fertig geschrieben hat."""
        self.request("SaveReplayBuffer")
        deadline = time.monotonic() + timeout
        while True:
            msg = self._recv(deadline)
            d = msg.get("d", {})
            if msg.get("op") == 5 and d.get("eventType") == "ReplayBufferSaved":
                return Path(d["eventData"]["savedReplayPath"])
