"""Beat-Erkennung für die Montage: Tempo, Beats, Takte (Downbeats) und Drops – nur numpy + FFmpeg.

Ablauf: Musik -> Mono 22 kHz -> Spektral-Flux (Onsets) -> Tempo per Autokorrelation -> Beat-Raster per
dynamischer Programmierung (Ellis 2007) -> Taktanfang über Bass-Onsets -> Drops = Takte, an denen die
Energie (v. a. Bass) nach einem ruhigeren Teil sprunghaft steigt.
"""
import subprocess
from dataclasses import dataclass, field

import numpy as np

from . import clips

SR = 22050
HOP = 512
NFFT = 2048
FPS = SR / HOP          # Onset-Frames pro Sekunde (~43)


@dataclass
class Analysis:
    bpm: float
    beats: list                       # Sekunden
    downbeats: list                   # Sekunden (Taktanfänge, 4/4)
    drops: list = field(default_factory=list)   # [(Sekunde, Stärke)] stärkster zuerst
    duration: float = 0.0
    main_drop: float | None = None    # erster Drop mit (fast) voller Energie = der „richtige“ Drop
    gain: float = 1.0                 # Faktor, der den Song auf gleiche Lautheit bringt (laute Songs < 1)

    @property
    def bar(self) -> float:
        return 240.0 / self.bpm       # 4 Beats

    def as_dict(self) -> dict:
        return {"bpm": round(self.bpm, 1), "drop": self.main_drop, "gain": round(self.gain, 3),
                "beats": len(self.beats), "duration": self.duration}


def _decode(path) -> np.ndarray:
    raw = subprocess.run([clips.tool("ffmpeg"), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True, check=True,
                         creationflags=clips.NO_WINDOW | clips.BELOW_NORMAL).stdout
    return np.frombuffer(raw, np.float32)


def _spectrogram(y: np.ndarray) -> np.ndarray:
    n = 1 + max(0, (len(y) - NFFT) // HOP)
    frames = np.lib.stride_tricks.as_strided(y, shape=(n, NFFT), strides=(y.strides[0] * HOP, y.strides[0]))
    return np.abs(np.fft.rfft(frames * np.hanning(NFFT).astype(np.float32), axis=1))


def _onsets(spec: np.ndarray, lo: int = 0, hi: int | None = None) -> np.ndarray:
    s = np.log1p(spec[:, lo:hi] * 10)
    flux = np.maximum(0.0, np.diff(s, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")    # lokalen Pegel abziehen
    flux = np.maximum(flux, 0)
    return flux / (flux.max() or 1.0)


def _tempo(env: np.ndarray) -> float:
    env = env - env.mean()
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lags = np.arange(len(ac))
    bpm = np.where(lags > 0, 60.0 * FPS / np.maximum(lags, 1), 0)
    valid = (bpm >= 70) & (bpm <= 190)
    # leichte Vorliebe für 100–160 BPM (typische Montage-Musik), gegen Halb-/Doppeltempo-Fehler
    weight = np.exp(-0.5 * (np.log2(np.maximum(bpm, 1) / 130.0) / 0.9) ** 2)
    score = np.where(valid, ac * weight, -np.inf)
    lag = int(np.argmax(score))
    # Feinjustage per Parabel um das Maximum
    if 1 <= lag < len(ac) - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        denom = a - 2 * b + c
        lag = lag + (0.5 * (a - c) / denom if denom else 0)
    return 60.0 * FPS / lag


def _track(env: np.ndarray, bpm: float, tightness: float = 100.0) -> np.ndarray:
    period = 60.0 * FPS / bpm
    n = len(env)
    score = env.astype(np.float64).copy()
    back = np.full(n, -1)
    lo, hi = int(round(period / 2)), int(round(period * 2))
    offsets = np.arange(lo, hi + 1)
    penalty = -tightness * np.log(offsets / period) ** 2
    for t in range(hi, n):
        prev = score[t - offsets] + penalty
        k = int(np.argmax(prev))
        score[t] = env[t] + prev[k]
        back[t] = t - offsets[k]
    # bestes Ende in der letzten Periode, dann zurückverfolgen
    t = int(n - hi + np.argmax(score[n - hi:]))
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    return np.array(beats[::-1])


def analyze(path) -> Analysis:
    y = _decode(path)
    if len(y) < SR * 5:
        raise ValueError("Musik ist kürzer als 5 s")
    spec = _spectrogram(y)
    env = _onsets(spec)
    bass_bin = int(150 / (SR / NFFT))                              # bis ~150 Hz
    bass_env = _onsets(spec, 0, bass_bin)
    bpm = _tempo(env)
    beat_frames = _track(env, bpm)
    beats = beat_frames / FPS

    # Taktanfang: die Phase (0..3), auf der die Bass-Onsets am stärksten sind
    strength = [bass_env[beat_frames[p::4]].mean() if len(beat_frames[p::4]) else 0 for p in range(4)]
    phase = int(np.argmax(strength))
    downbeats = beats[phase::4]

    # Energie je Takt (Bass doppelt gewichtet)
    rms = np.sqrt((spec[:, :bass_bin] ** 2).mean(axis=1)) * 2 + np.sqrt((spec ** 2).mean(axis=1))
    bar_energy = []
    db_frames = (downbeats * FPS).astype(int)
    for i, f in enumerate(db_frames):
        end = db_frames[i + 1] if i + 1 < len(db_frames) else min(len(rms), f + int(4 * 60 * FPS / bpm))
        bar_energy.append(float(rms[f:end].mean()) if end > f else 0.0)
    e = np.array(bar_energy)
    e = e / (e.max() or 1.0)

    # Drop: nach 2 ruhigeren Takten deutlich mehr Energie in den nächsten 2 Takten
    drops = []
    for i in range(2, len(e) - 1):
        before, after = e[max(0, i - 2):i].mean(), e[i:i + 2].mean()
        jump = after - before
        if jump > 0.12 and after > 0.55:
            drops.append((float(downbeats[i]), float(jump * after), float(after)))
    # benachbarte Kandidaten (innerhalb 4 Takten) zusammenfassen, stärkster bleibt
    bar = 240.0 / bpm
    drops.sort(key=lambda d: -d[1])
    picked = []
    for t, s, after in drops:
        if all(abs(t - p[0]) > 4 * bar for p in picked):
            picked.append((round(t, 3), round(s, 3), after))
    # Haupt-Drop: frühester Kandidat, nach dem der Song (fast) seine volle Energie erreicht –
    # nicht schon der Beat-Einsatz nach dem Intro
    main = None
    early = [p for p in picked if 5.0 <= p[0] <= 0.6 * len(y) / SR]   # danach muss noch genug Song kommen
    if early:
        peak = max(p[2] for p in early)
        main = min(p[0] for p in early if p[2] >= 0.8 * peak)
    # Lautheit: RMS der lauten Hälfte (Refrain/Drop) auf -18 dBFS bringen – Montage-Musik soll unter dem Spiel liegen
    frame_rms = np.sqrt(np.mean(y[: len(y) // SR * SR].reshape(-1, SR) ** 2, axis=1) + 1e-12)
    loud = np.sort(frame_rms)[len(frame_rms) // 2:]
    level_db = 20 * np.log10(float(np.mean(loud)) + 1e-9)
    gain = float(np.clip(10 ** ((-18.0 - level_db) / 20), 0.05, 2.0))
    return Analysis(bpm=float(bpm), beats=[round(float(b), 3) for b in beats],
                    downbeats=[round(float(b), 3) for b in downbeats], drops=[(t, s) for t, s, _ in picked],
                    duration=len(y) / SR, main_drop=main, gain=gain)
