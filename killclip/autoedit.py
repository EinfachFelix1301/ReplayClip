"""Auto-Schnitt: Kills auf den Beat legen – erster Kill genau auf den Drop, jeder weitere auf einen Takt.

1. Kill-Zeitpunkte je Clip finden (rotes Killdot-X, sonst Vorlauf als Schätzung)
2. Schnittplan auf dem Beat-Raster der Musik bauen (Musik startet kurz vor dem Drop = Intro)
3. Jede Einstellung einzeln rendern (1080p60, mit kurzem Flash beim Kill)
4. Alles mit Übergängen (xfade/acrossfade) zusammensetzen, Musik drunter, Intro-Titel, Ab-/Aufblende
"""
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import beats as beatlib
from . import clips
from . import titlecard
from .batch import iter_frames
from .detector import Detector, Preset

TRANSITIONS = {"clean": ("fade", 0.3), "flash": ("fadewhite", 0.25), "hart": (None, 0.0)}
FONT = "C\\:/Windows/Fonts/bahnschrift.ttf"
FLASH_S = 0.07          # Kill-Flash
MIN_AFTER_BEATS = 2     # so viele Beats bleibt der Kill nach dem Treffer mindestens stehen


@dataclass
class Shot:
    path: str
    src_start: float      # inklusive Übergangs-Vorlauf
    src_end: float        # inklusive Übergangs-Nachlauf
    out_start: float      # Schnitt in der fertigen Montage
    out_end: float
    kills: list = field(default_factory=list)   # Kill-Zeitpunkte relativ zum Shot-Anfang (Flash)
    head: float = 0.0     # Vorlauf für den Übergang IN diesen Shot
    t_kind: str = ""      # Übergang in diesen Shot (xfade-Name, leer = erster Shot)
    t_dur: float = 0.0
    game: float = 1.0     # Spielsound-Faktor (Teaser leiser)


_kill_cache: dict = {}


def kill_times(path: str, preset: str, fallback: float, cancel: threading.Event) -> list[float]:
    """Zeitpunkte (s im Clip), an denen das rote Killdot-X auftaucht (gecacht je Datei-Stand)."""
    try:
        key = (str(Path(path).resolve()), Path(path).stat().st_mtime, preset)
    except OSError:
        return [fallback]
    if key in _kill_cache:
        return _kill_cache[key]
    found = _detect(path, preset, cancel)
    if not cancel.is_set() and found:
        _kill_cache[key] = found
    return found or [fallback]


def _detect(path: str, preset: str, cancel: threading.Event) -> list[float]:
    try:
        det = Detector(Preset.load(preset), {"kill"}, "killdot")
    except Exception:
        return []
    # grob mit 10 fps suchen, dann rund um jeden Treffer mit 30 fps nachschärfen (Kill auf ±0,03 s)
    coarse = [t for t, frame in iter_frames(Path(path), 10.0, cancel) if det.update(t, frame)]
    trig = next(st.trigger for st in det.states if st.trigger.event == "kill")
    exact = []
    for t in coarse:
        first = t
        for ft, frame in _window(path, max(0.0, t - 0.2), 0.24):
            if trig.sees_template(frame):
                first = min(first, ft)
                break
        exact.append(round(first, 3))
    return exact


def _window(path: str, start: float, length: float):
    """Einzelbilder (720p, volle Bildrate) aus einem kurzen Abschnitt."""
    raw = subprocess.run([clips.tool("ffmpeg"), "-v", "error", "-ss", f"{start:.3f}", "-i", str(path),
                          "-t", f"{length:.3f}", "-vf", "scale=1280:720", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                         capture_output=True, creationflags=clips.NO_WINDOW | clips.BELOW_NORMAL).stdout
    n = len(raw) // (1280 * 720 * 3)
    if not n:
        return
    rate = subprocess.run([clips.tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=avg_frame_rate", "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                          creationflags=clips.NO_WINDOW).stdout.strip()
    try:
        num, den = rate.split("/")
        fps = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    frames = np.frombuffer(raw[:n * 1280 * 720 * 3], np.uint8).reshape(n, 720, 1280, 3)
    for i, fr in enumerate(frames):
        yield start + i / fps, fr


def prepare(items: list[dict], preset: str, pre_s: float, cancel: threading.Event, progress=None) -> list[dict]:
    """Kill-Zeiten an die Montage-Clips hängen (nur innerhalb Start/Ende)."""
    done = [0]
    lock = threading.Lock()

    def one(it):
        found = kill_times(it["path"], preset, min(pre_s, it["end"]), cancel)
        with lock:
            done[0] += 1
            if progress:
                progress(done[0], len(items), Path(it["path"]).name)
        kills = [k for k in found if it["start"] <= k <= it["end"]] or [min(max(it["start"], pre_s), it["end"])]
        return {**it, "kills": kills}

    with ThreadPoolExecutor(max_workers=4) as pool:     # 4 Clips gleichzeitig
        return list(pool.map(one, items))


def _next(grid: list, t: float) -> float | None:
    return next((g for g in grid if g >= t - 1e-3), None)


def _grid(times: list, step: float, until: float) -> list:
    """Beat-Raster über das Songende hinaus verlängern (Musik läuft dann in Schleife weiter)."""
    out = list(times)
    while out and out[-1] < until:
        out.append(round(out[-1] + step, 3))
    return out


HARD = ("fade", 0.04)          # „harter“ Schnitt auf den Beat (2 Frames, damit xfade/acrossfade sauber bleiben)
INTROS = ("teaser", "titel", "kein")


def _drop(ana: beatlib.Analysis, bars_g: list) -> float:
    return ana.main_drop or next((d for d, _ in sorted(ana.drops) if d >= 5.0), None) or _next(bars_g, 8.0)


def plan(items: list[dict], ana: beatlib.Analysis, style: str = "clean", intro: str = "teaser"):
    """items: [{path, start, end, kills}] (kills in Clip-Sekunden).

    Gibt (Shots, Musik-Startpunkt, Titel-Fenster) zurück. Erster Kill des ersten Clips landet genau auf dem Drop.
    Teaser-Intro: 2 Takte schnelle Kill-Schnitte auf den Beat, dann 1 Takt Anlauf mit Titel, dann Drop.
    """
    beat, bar = ana.bar / 4, ana.bar
    main_kind, main_d = TRANSITIONS.get(style, TRANSITIONS["clean"])
    main_kind = main_kind or HARD[0]
    main_d = main_d or HARD[1]
    horizon = ana.duration + sum(i["end"] - i["start"] for i in items) + 60
    beats_g, bars_g = _grid(ana.beats, beat, horizon), _grid(ana.downbeats, bar, horizon)
    drop = _drop(ana, bars_g)
    items = [{**it, "kills": sorted(k for k in it["kills"] if it["start"] <= k <= it["end"]) or [it["start"] + 0.5]}
             for it in items]
    first = items[0]
    k0 = first["kills"][0]

    # --- Intro festlegen: wie lange vor dem Drop und was dort läuft
    teaser_src = []
    if intro == "teaser" and len(items) > 1:
        approach = min(bar, max(0.6, k0 - first["start"] - 0.05))
        snip = 2 * beat if bar < 2.4 else beat           # bei langsamen Songs schneller schneiden
        n_snip = max(2, int(round(2 * bar / snip)))
        pool = sorted(items[1:], key=lambda it: -len(it["kills"]))   # Multi-Kills zuerst
        for i in range(n_snip):
            it = pool[i % len(pool)]
            k = it["kills"][(i // len(pool)) % len(it["kills"])]
            teaser_src.append((it, k))
        lead_total = approach + n_snip * snip
        if drop - lead_total < 0:                        # Drop zu früh im Song -> Teaser kürzen
            keep = max(0, int((drop - approach) // snip))
            teaser_src, lead_total = teaser_src[:keep], approach + keep * snip
    elif intro == "titel":
        approach = min(max(5.0, 4 * bar), 10.0, k0 - first["start"] - 0.05, drop)
        lead_total, snip = approach, 0
    else:                                                # kein Intro: ein Takt Anlauf, direkt rein
        approach = min(bar, max(0.6, k0 - first["start"] - 0.05), drop)
        lead_total, snip = approach, 0
    offset = drop - lead_total
    shots, cursor = [], offset                           # cursor = Schnittstelle in Musikzeit

    def add(path, src_start, src_end, kills, out_s, out_e, kind, d, game=1.0):
        head = d / 2 if shots else 0.0
        if shots:                                        # vorigem Shot Nachlauf für diesen Übergang geben
            shots[-1].src_end += d / 2
        shots.append(Shot(path, max(0.0, src_start - head), src_end, out_s - offset, out_e - offset,
                          [round(k - (src_start - head), 3) for k in kills], head,
                          kind if shots else "", d if shots else 0.0, game))

    # --- Teaser: harte Schnitte auf den Beat, Kill sitzt auf ~60 % des Schnipsels
    for it, k in teaser_src:
        a = max(it["start"], k - 0.6 * snip)
        b = min(it["end"], a + snip)
        add(it["path"], a, b, [k] if a <= k <= b else [], cursor, cursor + snip, *HARD, game=0.55)
        cursor += snip
    title = (cursor - offset, drop - offset - 0.12) if intro in ("teaser", "titel") else None
    if intro == "titel":
        title = (0.5, drop - offset - 0.4)

    # --- Hauptteil
    for n, it in enumerate(items):
        kills = it["kills"]
        k_first, k_last = kills[0], kills[-1]
        if n == 0 and teaser_src:                        # Teaser -> Anlauf: weißer Flash
            into_kind, into_d = "fadewhite", 0.2
        else:
            into_kind, into_d = main_kind, main_d
        pre_avail = k_first - it["start"] - (into_d / 2 if shots else 0)
        post_avail = it["end"] - k_last - max(main_d, 0.04)
        if n == 0:
            hit = drop
        else:
            lead = min(bar, max(0.8, pre_avail))
            hit = _next(bars_g, cursor + lead)
            if hit - cursor > pre_avail:
                hit = _next(beats_g, cursor + min(lead, pre_avail))
                if hit - cursor > pre_avail:
                    hit = cursor + pre_avail
        last_hit = hit + (k_last - k_first)
        want = last_hit + MIN_AFTER_BEATS * beat
        cut = _next(bars_g, want)
        if cut - last_hit > post_avail:
            cut = _next(beats_g, want)
            if cut is None or cut - last_hit > post_avail:
                cut = last_hit + max(0.4, post_avail)
        if n == len(items) - 1:
            cut = min(last_hit + max(bar, MIN_AFTER_BEATS * beat), last_hit + max(0.4, post_avail))
        src_start = k_first - (hit - cursor)
        src_end = k_last + (cut - last_hit)
        add(it["path"], src_start, src_end, kills, cursor, cut, into_kind, into_d)
        cursor = cut
    return shots, offset, title


def preview_plan(items: list[dict], music: str, style: str, preset: str, pre_s: float,
                 cancel: threading.Event, progress=None, intro: str = "teaser") -> dict:
    """Schnittplan für die Vorschau in der App – dasselbe, was „Montage erstellen“ rendert."""
    ana = beatlib.analyze(music)
    items = prepare(items, preset, pre_s, cancel, progress)
    shots, offset, title = plan(items, ana, style, intro)
    total = shots[-1].out_end
    beat = ana.bar / 4
    grid = [b - offset for b in _grid(ana.beats, beat, offset + total + 1) if offset <= b <= offset + total]
    bars = [b - offset for b in _grid(ana.downbeats, ana.bar, offset + total + 1) if offset <= b <= offset + total]
    drop = _drop(ana, _grid(ana.downbeats, ana.bar, offset + total + 1)) - offset
    return {"shots": [{"path": s.path, "src_start": s.src_start, "src_end": s.src_end, "out_start": s.out_start,
                       "out_end": s.out_end, "kills": s.kills, "head": s.head, "kind": s.t_kind, "dur": s.t_dur,
                       "game": s.game} for s in shots],
            "offset": offset, "total": total, "bpm": ana.bpm, "beats": grid, "bars": bars, "drop": drop,
            "title": title, "gain": ana.gain, "transition": style}


def _run(args):
    from .montage import _run as run
    return run(args)


def _render_shot(shot: Shot, dst: Path, enc: list):
    from .montage import has_audio
    audio = has_audio(Path(shot.path))
    length = shot.src_end - shot.src_start
    flashes = "".join(f",eq=brightness=0.32:saturation=1.3:enable='between(t,{k:.3f},{k + FLASH_S:.3f})'"
                      for k in shot.kills)
    args = [clips.tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{shot.src_start:.3f}", "-i", shot.path]
    if not audio:
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    args += ["-t", f"{length:.3f}", "-map", "0:v:0", "-map", "0:a:0" if audio else "1:a:0",
             "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
                    f"setsar=1,fps=60{flashes}",
             "-af", f"volume={shot.game:.2f}",
             *enc, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(dst)]
    _run(args)


def _title(text: str, window: tuple) -> str:
    safe = text.replace("\\", "").replace("'", "").replace(":", "\\:").replace("%", "")[:40]
    a, b = window
    fade = min(0.25, max(0.05, (b - a) / 4))
    alpha = (f"if(lt(t,{a:.3f}),0,if(lt(t,{a + fade:.3f}),(t-{a:.3f})/{fade:.3f},"
             f"if(lt(t,{b - fade:.3f}),1,if(lt(t,{b:.3f}),({b:.3f}-t)/{fade:.3f},0))))")
    return (f",drawtext=fontfile='{FONT}':text='{safe}':fontsize=132:fontcolor=white:borderw=7:bordercolor=black@0.6"
            f":x=(w-tw)/2:y=(h-th)/2:alpha='{alpha}'")


def subtitle_for(items: list[dict]) -> str:
    """Untertitel der Titelkarte: „KILL MONTAGE · 20 KILLS · 09.11.2026“ (Datum aus den Clip-Namen)."""
    import re
    n = sum(len(it.get("kills") or [1]) for it in items)
    dates = sorted({m.group(1) for it in items if (m := re.search(r"(\d{4}-\d{2}-\d{2})", Path(it["path"]).name))})
    date = ""
    if dates:
        y, mo, d = dates[-1].split("-")
        date = f" · {d}.{mo}.{y}"
    return f"KILL MONTAGE · {n} KILL{'S' if n != 1 else ''}{date}"


def render(items: list[dict], music: str, music_vol: float, game_vol: float, style: str, intro_text: str,
           preset: str, pre_s: float, tmp: Path, target: Path, log, progress, cancel: threading.Event,
           intro: str = "teaser") -> Path | None:
    from .montage import encoder, X264
    ana = beatlib.analyze(music)
    log(f"Musik: {ana.bpm:.0f} BPM, Drop bei {ana.main_drop:.1f} s" if ana.main_drop else
        f"Musik: {ana.bpm:.0f} BPM (kein klarer Drop – nehme einen Takt)")
    steps = len(items) * 2 + 1
    items = prepare(items, preset, pre_s, cancel,    # 1) Kill-Zeitpunkte (aus dem Cache, wenn schon gesucht)
                    lambda i, n, name: progress({"pct": i / steps * 100, "file": f"Kills suchen: {name}",
                                                 "index": i + 1, "total": n, "eta": None}))
    if cancel.is_set():
        return None

    shots, offset, title_win = plan(items, ana, style, intro)
    total = shots[-1].out_end
    log(f"Schnittplan: {len(shots)} Schnitte, {total:.0f} s, Intro „{intro}“, Musik-Angleichung {ana.gain:.2f}×")

    parts = []
    for i, shot in enumerate(shots):       # 2) Einstellungen rendern
        if cancel.is_set():
            return None
        progress({"pct": (len(items) + i) / steps * 100, "file": f"Schneiden: {Path(shot.path).name}",
                  "index": i + 1, "total": len(shots), "eta": None})
        part = tmp / f"s{i:04d}.mp4"
        try:
            _render_shot(shot, part, encoder())
        except RuntimeError:
            _render_shot(shot, part, X264)
        parts.append(part)

    progress({"pct": (steps - 1) / steps * 100, "file": "Übergänge, Musik, Intro …", "index": len(shots),
              "total": len(shots), "eta": None})
    args = [clips.tool("ffmpeg"), "-v", "error", "-y"]
    for p in parts:
        args += ["-i", str(p)]
    args += ["-ss", f"{offset:.3f}", "-stream_loop", "-1", "-i", music]
    m = len(parts)
    card = None
    if intro_text.strip() and title_win and title_win[1] > title_win[0] + 0.3:
        progress({"pct": (steps - 1) / steps * 100, "file": "Titelkarte …", "index": len(shots),
                  "total": len(shots), "eta": None})
        card = titlecard.render(intro_text, subtitle_for(items), title_win[1] - title_win[0], tmp / "title.mov", preset)
        args += ["-itsoffset", f"{title_win[0]:.3f}", "-i", str(card)]
    graph, v, a = [], "[0:v]", "[0:a]"
    for i in range(1, m):
        sh = shots[i]
        off = sh.out_start - sh.t_dur / 2
        graph.append(f"{v}[{i}:v]xfade=transition={sh.t_kind}:duration={sh.t_dur:.3f}:offset={off:.3f}[v{i}]")
        graph.append(f"{a}[{i}:a]acrossfade=d={sh.t_dur:.3f}:c1=tri:c2=tri[a{i}]")
        v, a = f"[v{i}]", f"[a{i}]"
    if card:                               # Titelkarte (mit Alpha) übers Intro legen
        graph.append(f"{v}[{m + 1}:v]overlay=x=0:y=0:eof_action=pass:format=auto[vt]")
        v = "[vt]"
    fade_out = max(0.0, total - 1.5)
    graph.append(f"{v}fade=t=in:st=0:d=0.3,fade=t=out:st={fade_out:.3f}:d=1.5,format=yuv420p[vout]")
    # Lautstärke angleichen: Musik auf Ziel-Lautheit (Regler = Feinanteil)
    # Spielsound: Spitzen (Schüsse, Voice) komprimieren, dann fest UNTER die Musik legen (-26 LUFS)
    graph.append(f"{a}acompressor=threshold=-30dB:ratio=6:attack=3:release=150,loudnorm=I=-26:TP=-4:LRA=7,aresample=48000,volume={game_vol:.3f}[game]")
    graph.append(f"[{m}:a]aresample=48000,volume={music_vol * ana.gain:.3f},afade=t=in:st=0:d=0.3,"
                 f"atrim=0:{total:.3f},afade=t=out:st={fade_out:.3f}:d=1.5[mus]")
    # fertiger Mix auf YouTube-Lautheit (-14 LUFS) – Verhältnis Musik/Spiel bleibt, nichts übersteuert
    graph.append("[game][mus]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=9,"
                 "aresample=48000,alimiter=limit=0.95[aout]")
    out = tmp / "final.mp4"
    tail = ["-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
    try:
        _run(args + encoder() + tail)
    except RuntimeError:
        _run(args + X264 + tail)
    out.replace(target)
    return target
