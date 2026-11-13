"""Erkennungs-Vorschau: Bei jedem erkannten Kill ein kleines Bild an die Oberfläche schicken.

Live und Scan rufen publish() auf, die App hängt sich mit listen() an. Bild = Szene klein + Killdot-Ausschnitt groß.
"""
import base64

import cv2
import numpy as np

_listeners = []


def listen(fn):
    _listeners.append(fn)


def publish(frame: np.ndarray, region, text: str):
    if not _listeners or frame is None:
        return
    try:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = region
        box = (int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h))
        cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2      # eng um die Mitte (dort sitzt das X)
        r = max(12, (box[2] - box[0]) // 5)
        crop = frame[max(0, cy - r):cy + r, max(0, cx - r):cx + r]
        zoom = cv2.resize(crop, (180, round(180 * crop.shape[0] / max(1, crop.shape[1]))), interpolation=cv2.INTER_NEAREST)
        scene = cv2.resize(frame, (320, round(320 * h / w)), interpolation=cv2.INTER_AREA)
        s = 320 / w
        cv2.rectangle(scene, (int(box[0] * s), int(box[1] * s)), (int(box[2] * s), int(box[3] * s)), (0, 0, 255), 1)
        zh = min(zoom.shape[0], scene.shape[0])
        canvas = np.full((scene.shape[0], 320 + 8 + 180, 3), 12, np.uint8)
        canvas[:, :320] = scene
        canvas[:zh, 328:] = zoom[:zh]
        ok, jpg = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not ok:
            return
        img = "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode()
    except Exception:
        return
    for fn in list(_listeners):
        try:
            fn(img, text)
        except Exception:
            pass


def kill_region(detector):
    """Bereich des Kill-Triggers (für den Rahmen im Vorschaubild)."""
    for st in detector.states:
        if st.trigger.event == "kill":
            return st.trigger.region
    return (0.4, 0.38, 0.6, 0.62)
