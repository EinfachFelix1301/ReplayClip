"""Erzeugt das ReplayClip-App-Icon (assets/replayclip.ico + replayclip.png).

Motiv: rotes Fadenkreuz (Kill) mit Play-Dreieck in der Mitte (Clip) auf dunklem Rund-Quadrat.
Aufruf: python tools/make_icon.py
"""
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 1024                      # Arbeitsgröße, wird für jede Icon-Größe herunterskaliert
RED = (255, 0, 0)
OUT = Path(__file__).resolve().parent.parent / "assets"


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size - 1, size - 1), radius, fill=255)
    return m


def background():
    # dunkler Verlauf oben -> unten, mittig rotes Glühen
    bg = Image.new("RGB", (S, S))
    top, bottom = (28, 28, 32), (9, 9, 11)
    px = ImageDraw.Draw(bg)
    for y in range(S):
        t = y / (S - 1)
        px.line((0, y, S, y), fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    glow = Image.new("L", (S, S), 0)
    ImageDraw.Draw(glow).ellipse((S * .18, S * .18, S * .82, S * .82), fill=120)
    glow = glow.filter(ImageFilter.GaussianBlur(S * .12))
    red = Image.new("RGB", (S, S), (150, 0, 0))
    return Image.composite(red, bg, glow)


def reticle():
    """Fadenkreuz-Ring mit vier Lücken + Striche nach innen, Play-Dreieck in der Mitte (Maske)."""
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    c, r, w = S / 2, S * .33, S * .062
    gap = 15                                     # Grad Lücke links/rechts jeder Achse
    for start in (0, 90, 180, 270):
        d.arc((c - r, c - r, c + r, c + r), start + gap, start + 90 - gap, fill=255, width=round(w))
    # Striche auf den Achsen, von außerhalb des Rings nach innen
    tick_out, tick_in, tw = r + S * .11, r - S * .08, S * .056
    for a in (0, 90, 180, 270):
        dx, dy = math.cos(math.radians(a)), math.sin(math.radians(a))
        d.line((c + dx * tick_in, c + dy * tick_in, c + dx * tick_out, c + dy * tick_out), fill=255, width=round(tw))
    return m


def play():
    m = Image.new("L", (S, S), 0)
    c, h = S / 2, S * .23
    # optisch zentriert: Schwerpunkt des Dreiecks auf die Mitte
    pts = [(c - h * .38, c - h / 2 * 1.08), (c - h * .38, c + h / 2 * 1.08), (c + h * .62, c)]
    ImageDraw.Draw(m).polygon(pts, fill=255)
    return m


def render():
    img = background()
    ring, tri = reticle(), play()
    # weiches rotes Leuchten hinter dem Fadenkreuz
    halo = ring.filter(ImageFilter.GaussianBlur(S * .025)).point(lambda v: v * .9)
    img = Image.composite(Image.new("RGB", (S, S), (255, 40, 40)), img, halo)
    img = Image.composite(Image.new("RGB", (S, S), RED), img, ring)
    shadow = tri.filter(ImageFilter.GaussianBlur(S * .02)).point(lambda v: v * .6)
    img = Image.composite(Image.new("RGB", (S, S), (0, 0, 0)), img, shadow)
    img = Image.composite(Image.new("RGB", (S, S), (255, 255, 255)), img, tri)
    # feiner heller Rand oben (Glanzkante)
    edge = ImageChops.subtract(rounded_mask(S, int(S * .22)),
                               rounded_mask(S, int(S * .22)).transform((S, S), Image.AFFINE, (1, 0, 0, 0, 1, -6)))
    img = Image.composite(Image.new("RGB", (S, S), (70, 70, 78)), img, edge.point(lambda v: v * .5))
    out = img.convert("RGBA")
    out.putalpha(rounded_mask(S, int(S * .22)))
    return out


def main():
    OUT.mkdir(exist_ok=True)
    big = render()
    big.resize((512, 512), Image.LANCZOS).save(OUT / "replayclip.png")
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    big.resize((256, 256), Image.LANCZOS).save(OUT / "replayclip.ico", sizes=[(s, s) for s in sizes])
    print("geschrieben:", OUT / "replayclip.ico", OUT / "replayclip.png")


if __name__ == "__main__":
    main()
