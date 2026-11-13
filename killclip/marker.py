"""„Im Bild markieren“: Kill-Notify-Bereich oder Killdot per Rahmen festlegen und ins Preset schreiben."""
import base64
import json

import cv2
import numpy as np

from .config import PRESET_DIR
from .detector import color_mask

KILLDOT_MARGIN = 1.0  # Suchbereich = markierter Killdot + 1× seine Größe rundherum


def read_frame(path, seconds: float):
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        length = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, seconds) * 1000)
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError("Bild konnte nicht gelesen werden")
        return frame, length
    finally:
        cap.release()


def frame_preview(path, seconds: float, width=1280) -> dict:
    frame, length = read_frame(path, seconds)
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
    jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
    return {"img": "data:image/jpeg;base64," + base64.b64encode(jpg).decode(), "duration": length,
            "t": seconds, "w": w, "h": h}


def _load(preset: str) -> tuple[dict, object]:
    path = PRESET_DIR / f"{preset}.json"
    return json.loads(path.read_text(encoding="utf-8")), path


def info(preset: str) -> dict:
    data, _ = _load(preset)
    notify = next((t for t in data["triggers"] if t["event"] == "kill" and t.get("kind", "ocr") == "ocr"), None)
    dot = next((t for t in data["triggers"] if t["event"] == "kill" and t.get("kind") == "template"), None)
    return {"notify": notify["region"] if notify else None,
            "killdot": bool(dot and (PRESET_DIR / dot["template"]).is_file())}


def save(preset: str, kind: str, path, seconds: float, box: list[float]) -> str:
    """box = x, y, w, h relativ zum Bild (0..1)."""
    x, y, w, h = (min(1.0, max(0.0, float(v))) for v in box)
    if w < 0.004 or h < 0.004:
        raise ValueError("Rahmen ist zu klein")
    data, preset_path = _load(preset)
    triggers = data["triggers"]
    if kind == "notify":
        trig = next((t for t in triggers if t["event"] == "kill" and t.get("kind", "ocr") == "ocr"), None)
        if not trig:
            raise ValueError("Im Preset gibt es keine Kill-Notify-Erkennung")
        trig["region"] = [round(x, 4), round(y, 4), round(min(1.0, x + w), 4), round(min(1.0, y + h), 4)]
        msg = "Kill-Notify-Bereich gespeichert"
    else:
        frame, _ = read_frame(path, seconds)
        fh, fw = frame.shape[:2]
        px, py, pw, ph = int(x * fw), int(y * fh), max(4, int(w * fw)), max(4, int(h * fh))
        name = f"{preset}_killdot.png"
        crop = frame[py:py + ph, px:px + pw]
        red = cv2.countNonZero(color_mask(crop)) >= 4
        ok, png = cv2.imencode(".png", crop)
        if not ok:
            raise RuntimeError("Killdot-Bild konnte nicht gespeichert werden")
        png.tofile(str(PRESET_DIR / name))
        m = KILLDOT_MARGIN
        region = [max(0.0, x - w * m), max(0.0, y - h * m), min(1.0, x + w * (1 + m)), min(1.0, y + h * (1 + m))]
        triggers[:] = [t for t in triggers if not (t["event"] == "kill" and t.get("kind") == "template")]
        triggers.append({"event": "kill", "kind": "template", "template": name, "ref_height": fh,
                         "match": 0.55 if red else 0.75, "color": "red" if red else "",
                         "region": [round(v, 4) for v in region], "keywords": [], "rearm_s": 1.5})
        msg = "Killdot gespeichert" if red else ("Killdot gespeichert – aber kein rotes X im Rahmen. "
                                                 "Weißes X = Assist; für Kills ein rotes X markieren")
    preset_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return msg
