"""Windows-eigene Texterkennung (Windows.Media.Ocr), offline und leichtgewichtig."""
import asyncio
import threading

import cv2
import numpy as np
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

_local = threading.local()


def _engine():
    eng = getattr(_local, "engine", None)
    if eng is None:
        eng = OcrEngine.try_create_from_language(Language("de-DE"))
        if eng is None:
            eng = OcrEngine.try_create_from_user_profile_languages()
        if eng is None:
            raise RuntimeError("Windows-OCR nicht verfügbar (Sprachpaket Deutsch fehlt?)")
        _local.engine = eng
        _local.loop = asyncio.new_event_loop()
    return eng, _local.loop


async def _recognize(eng, bgr):
    bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    h, w = bgra.shape[:2]
    writer = DataWriter()
    writer.write_bytes(bgra.tobytes())
    bmp = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
    result = await eng.recognize_async(bmp)
    return [line.text for line in result.lines]


def read_lines(bgr: np.ndarray, frame_height: int) -> list[str]:
    """Liefert erkannte Textzeilen. Kleine HUD-Schrift wird auf ~720p×3 hochskaliert."""
    eng, loop = _engine()
    scale = max(1.0, 3.0 * 720 / frame_height)
    if scale > 1.0:
        bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return loop.run_until_complete(_recognize(eng, bgr))
