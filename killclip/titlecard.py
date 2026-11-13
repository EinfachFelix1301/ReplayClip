"""Intro-Titelkarte: animierte Grafik (Logo + Titel + Untertitel) als transparentes Video zum Drüberlegen.

Ablauf in der Zeit des Titel-Fensters:
  Slam (0–0.14 s)  Titel knallt von 1.9× auf 1×, Bewegungsunschärfe + RGB-Split
  Aufprall         weißer Flash, Lichtstrahlen, Wackeln, das schräge Band zieht auf
  Halten           Glanz-Streifen läuft über den Text, zwei kurze Glitches
  Raus (0.12 s)    Zoom 1→1.12, RGB-Split, ausblenden
Farben/Schrift/Logo kommen aus dem Preset ("intro"), Standard = Zeno (Neon-Violett, Chakra Petch).
"""
import base64
import io
import json
import math
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import clips
from .config import PRESET_DIR

W, H, FPS = 1920, 1080, 60
SLAM, OUT = 0.14, 0.12
DEFAULT = {"logo": "", "font": "", "font_sub": "", "color": "#ff0000", "deep": "#5a0000", "sub_color": "#ff6b6b"}


def _style(preset: str) -> dict:
    try:
        data = json.loads((PRESET_DIR / f"{preset}.json").read_text(encoding="utf-8"))
        return {**DEFAULT, **data.get("intro", {})}
    except (OSError, ValueError):
        return dict(DEFAULT)


def _rgb(hexcol: str) -> tuple:
    h = hexcol.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _font(name: str, size: int, fallback: str) -> ImageFont.FreeTypeFont:
    for cand in (PRESET_DIR / name if name else None, Path(r"C:\Windows\Fonts") / fallback):
        if cand and cand.is_file():
            return ImageFont.truetype(str(cand), size)
    return ImageFont.load_default()


def _spaced(draw, text, font, spacing):
    """Breite eines Texts mit Buchstabenabstand."""
    return sum(draw.textlength(ch, font=font) for ch in text) + spacing * max(0, len(text) - 1)


def _draw_spaced(draw, xy, text, font, spacing, fill):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + spacing


def _keyed_logo(path: Path, size: int) -> Image.Image | None:
    """Logo ohne dunklen Hintergrund: Helligkeit -> Transparenz (Blitze/Glow bleiben, Schwarz verschwindet)."""
    if not path.is_file():
        return None
    im = Image.open(path).convert("RGBA")
    im.thumbnail((size, size), Image.LANCZOS)
    a = np.asarray(im).astype(np.float32)
    lum = a[..., :3].max(axis=2)
    alpha = np.clip((lum - 28) / 90, 0, 1) * (a[..., 3] / 255)
    a[..., 3] = alpha * 255
    return Image.fromarray(a.astype(np.uint8), "RGBA")


class TitleCard:
    """Baut die statischen Ebenen einmal; frame(t) setzt sie animiert zusammen."""

    def __init__(self, text: str, subtitle: str, duration: float, preset: str = "zenocity"):
        st = _style(preset)
        self.duration = max(0.6, duration)
        self.col, self.deep, self.subcol = _rgb(st["color"]), _rgb(st["deep"]), _rgb(st["sub_color"])
        text = (text or "").upper()[:24]
        subtitle = (subtitle or "").upper()[:60]
        big = _font(st["font"], 172 if len(text) <= 10 else 132, "bahnschrift.ttf")
        small = _font(st["font_sub"], 40, "bahnschrift.ttf")
        tmp = ImageDraw.Draw(Image.new("L", (10, 10)))
        tw = _spaced(tmp, text, big, 10)
        sw = _spaced(tmp, subtitle, small, 7)
        logo = _keyed_logo(PRESET_DIR / st["logo"], 300) if st["logo"] else None
        lw = logo.width + 30 if logo else 0
        block_w = lw + max(tw, sw)
        x0 = (W - block_w) / 2
        tx = x0 + lw
        ty = H / 2 - 120

        # Text-Ebene (weiß) + Untertitel
        self.text = Image.new("RGBA", (W, H))
        d = ImageDraw.Draw(self.text)
        _draw_spaced(d, (tx, ty), text, big, 10, (255, 255, 255, 255))
        _draw_spaced(d, (tx + 4, ty + 182 if len(text) <= 10 else ty + 146), subtitle, small, 7, (*self.subcol, 255))
        if logo:
            self.text.alpha_composite(logo, (int(x0), int(H / 2 - logo.height / 2 - 18)))
        # Neon-Glow (Farbe) hinter dem Text
        a = self.text.getchannel("A")
        glow = Image.new("RGBA", (W, H), (*self.col, 0))
        glow.putalpha(a.filter(ImageFilter.GaussianBlur(22)).point(lambda v: min(255, int(v * 1.6))))
        self.glow = glow
        self.mask = a                                           # für den Glanz-Streifen

        # Schräges Band (wie das Z) mit Neon-Kanten
        self.band = Image.new("RGBA", (W, H))
        bd = ImageDraw.Draw(self.band)
        top, bot, slant = H / 2 - 200, H / 2 + 175, 90
        bd.polygon([(0 + slant, top), (W, top), (W - slant, bot), (0, bot)], fill=(8, 5, 12, 200))
        grad = np.linspace(0, 1, W)
        fade = np.clip(np.minimum(grad, 1 - grad) * 5, 0, 1)    # an den Rändern auslaufen
        ba = np.asarray(self.band).copy()
        ba[..., 3] = (ba[..., 3] * fade[None, :]).astype(np.uint8)
        self.band = Image.fromarray(ba, "RGBA")
        edge = Image.new("RGBA", (W, H))
        ed = ImageDraw.Draw(edge)
        ed.line([(slant, top), (W, top)], fill=(*self.col, 255), width=4)
        ed.line([(0, bot), (W - slant, bot)], fill=(*self.col, 255), width=4)
        ea = np.asarray(edge).copy()
        ea[..., 3] = (ea[..., 3] * fade[None, :]).astype(np.uint8)
        edge = Image.fromarray(ea, "RGBA")
        self.band.alpha_composite(edge)
        eg = edge.filter(ImageFilter.GaussianBlur(10))
        self.band.alpha_composite(eg)

        # Lichtstrahlen beim Aufprall (vom Textzentrum)
        self.rays = Image.new("L", (W, H))
        rd = ImageDraw.Draw(self.rays)
        cx, cy = tx + tw / 2, ty + 90
        rng = np.random.default_rng(7)
        for ang in np.linspace(0, 2 * math.pi, 28, endpoint=False) + rng.uniform(0, 0.2, 28):
            r = rng.uniform(700, 1400)
            rd.polygon([(cx, cy), (cx + math.cos(ang - .02) * r, cy + math.sin(ang - .02) * r),
                        (cx + math.cos(ang + .02) * r, cy + math.sin(ang + .02) * r)], fill=int(rng.uniform(90, 200)))
        self.rays = self.rays.filter(ImageFilter.GaussianBlur(6))
        self.center = (cx, cy)
        self.glitch_t = [self.duration * 0.42, self.duration * 0.71] if self.duration > 1.0 else [self.duration * 0.5]

    # --- Animation -------------------------------------------------------------------------
    @staticmethod
    def _scaled(img: Image.Image, s: float, center) -> Image.Image:
        if abs(s - 1) < 1e-3:
            return img
        cx, cy = center
        return img.transform(img.size, Image.AFFINE, (1 / s, 0, cx - cx / s, 0, 1 / s, cy - cy / s), Image.BILINEAR)

    @staticmethod
    def _rgb_split(img: Image.Image, dx: int) -> Image.Image:
        if dx == 0:
            return img
        r, g, b, a = img.split()
        r = ImageChops.offset(r, dx, 0)
        b = ImageChops.offset(b, -dx, 0)
        a = ImageChops.lighter(a, ImageChops.lighter(ImageChops.offset(a, dx, 0), ImageChops.offset(a, -dx, 0)))
        return Image.merge("RGBA", (r, g, b, a))

    def frame(self, t: float) -> Image.Image:
        D = self.duration
        out = Image.new("RGBA", (W, H))
        if t < 0 or t > D:
            return out
        # Kinobalken
        bars = min(1.0, t / 0.15) * min(1.0, (D - t) / 0.15)
        if bars > 0:
            bh = int(118 * (1 - (1 - bars) ** 3))
            ImageDraw.Draw(out).rectangle([0, 0, W, bh], fill=(0, 0, 0, 255))
            ImageDraw.Draw(out).rectangle([0, H - bh, W, H], fill=(0, 0, 0, 255))
        # Band zieht nach dem Aufprall auf
        if t >= SLAM * 0.6:
            p = min(1.0, (t - SLAM * 0.6) / 0.18)
            band = self.band.copy()
            band.putalpha(band.getchannel("A").point(lambda v: int(v * p * min(1.0, (D - t) / OUT))))
            out.alpha_composite(band)

        # Titel: Slam, Halten, Raus
        if t < SLAM:
            p = t / SLAM
            scales = [1 + 0.9 * (1 - p) ** 2 * k for k in (1.15, 1.0, 0.85)]   # Bewegungsunschärfe
            alpha_k, split = min(1.0, p * 2.2), int(22 * (1 - p))
        elif t > D - OUT:
            p = (t - (D - OUT)) / OUT
            scales, alpha_k, split = [1 + 0.12 * p], 1 - p, int(26 * p)
        else:
            scales, alpha_k, split = [1.0], 1.0, 0
        # Wackeln nach dem Aufprall
        shake = 0.0 if t < SLAM else max(0.0, 1 - (t - SLAM) / 0.32)
        sx = int(math.sin(t * 91) * 16 * shake)
        sy = int(math.cos(t * 77) * 10 * shake)
        layer = Image.new("RGBA", (W, H))
        for s in scales:
            g = self._scaled(self.glow, s, self.center)
            tx = self._scaled(self.text, s, self.center)
            part = Image.alpha_composite(g, tx)
            if len(scales) > 1:
                part.putalpha(part.getchannel("A").point(lambda v: v // len(scales) + (v // 3 if s == 1 else 0)))
            layer.alpha_composite(part)
        # Glanz-Streifen über den Text (nur im Halten)
        if SLAM + 0.15 < t < D - OUT and D > 0.9:
            p = (t - SLAM - 0.15) / max(0.2, D - OUT - SLAM - 0.15)
            band_x = int(-300 + p * (W + 600))
            shine = Image.new("L", (W, H))
            ImageDraw.Draw(shine).polygon([(band_x, 0), (band_x + 90, 0), (band_x - 230, H), (band_x - 320, H)], fill=150)
            shine = ImageChops.multiply(shine.filter(ImageFilter.GaussianBlur(14)), self.mask)
            white = Image.new("RGBA", (W, H), (255, 255, 255, 0))
            white.putalpha(shine)
            layer.alpha_composite(white)
        # Glitch: horizontale Streifen versetzt + RGB-Split
        for gt in self.glitch_t:
            if gt <= t < gt + 0.06:
                arr = np.asarray(layer).copy()
                rng = np.random.default_rng(int(gt * 1000))
                for _ in range(7):
                    y0 = int(rng.uniform(H * .3, H * .7)); hgt = int(rng.uniform(8, 46))
                    arr[y0:y0 + hgt] = np.roll(arr[y0:y0 + hgt], int(rng.uniform(-60, 60)), axis=1)
                layer = Image.fromarray(arr, "RGBA")
                split = max(split, 12)
        layer = self._rgb_split(layer, split)
        if alpha_k < 1:
            layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha_k)))
        out.alpha_composite(layer, (sx, sy))

        # Aufprall: Strahlen + weißer Flash
        if SLAM <= t < SLAM + 0.35:
            k = 1 - (t - SLAM) / 0.35
            rays = Image.new("RGBA", (W, H), (*self.col, 0))
            rays.putalpha(self.rays.point(lambda v: int(v * k * 0.55)))
            out.alpha_composite(rays)
        if SLAM <= t < SLAM + 0.12:
            k = 1 - (t - SLAM) / 0.12
            out.alpha_composite(Image.new("RGBA", (W, H), (255, 255, 255, int(170 * k))))
        return out


def render(text: str, subtitle: str, duration: float, dst: Path, preset: str = "zenocity") -> Path:
    """Titelkarte als transparentes Video (QuickTime-RLE mit Alpha) für den overlay-Filter."""
    card = TitleCard(text, subtitle, duration, preset)
    n = max(1, int(round(card.duration * FPS)))
    proc = subprocess.Popen([clips.tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
                             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "qtrle", str(dst)],
                            stdin=subprocess.PIPE, creationflags=clips.NO_WINDOW | clips.BELOW_NORMAL)
    try:
        for i in range(n):
            proc.stdin.write(card.frame(i / FPS).tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
    if proc.returncode:
        raise RuntimeError("Titelkarte konnte nicht erstellt werden")
    return dst


def preview_png(text: str, subtitle: str, preset: str = "zenocity") -> str:
    """Standbild (Halte-Phase) für die Vorschau in der App – als data:-URL."""
    card = TitleCard(text, subtitle, 1.4, preset)
    img = card.frame(0.6)
    img.thumbnail((960, 540))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
