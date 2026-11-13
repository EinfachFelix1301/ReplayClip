"""Kill-/Tod-Erkennung aus HUD-Text und Zusammenfassen zu Clip-Abschnitten."""
import json
import re
from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import cv2
import numpy as np

from . import ocr
from .config import PRESET_DIR

_UMLAUTS = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "s"})


def normalize(text: str) -> str:
    text = text.lower().translate(_UMLAUTS)
    return re.sub(r"[^a-z0-9 ]+", " ", text)


def _fuzzy_in(keyword: str, tokens: list[str], threshold: float) -> bool:
    return any(SequenceMatcher(None, keyword, t).ratio() >= threshold for t in tokens)


SURE_MATCH = 0.75       # ab hier zählt ein roter Killdot ohne weitere Prüfung
KILLDOT_FPS = 5.0       # Killdot ist nur ~0,5 s sichtbar -> mindestens so oft prüfen


def color_mask(bgr: np.ndarray) -> np.ndarray:
    """Kräftig rote Pixel (Kill-X) = 255, alles andere 0."""
    h, s, v = cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV))
    return ((((h < 10) | (h > 170)) & (s > 100) & (v > 120)) * 255).astype(np.uint8)


@dataclass
class Trigger:
    event: str                      # "kill" oder "death"
    region: tuple                   # x1, y1, x2, y2 relativ (0..1)
    keywords: list                  # alle müssen (unscharf) in einer Zeile vorkommen
    any_of: list = field(default_factory=list)    # zusätzlich mindestens eins davon (lockerer)
    exclude: list = field(default_factory=list)   # Zeile ignorieren, wenn eins davon vorkommt
    threshold: float = 0.84         # Ähnlichkeit für keywords (OCR-Lesefehler tolerieren)
    rearm_s: float = 4.0            # so lange muss der Treffer weg sein, bevor er neu zählt
    kind: str = "ocr"               # "ocr" = Kill-Notify-Text, "template" = Bild (z. B. Killdot)
    template: str = ""              # Bilddatei in presets/ (nur kind="template")
    ref_height: int = 1080          # Bildhöhe, bei der das Template markiert wurde
    match: float = 0.75             # Mindest-Übereinstimmung fürs Template
    color: str = ""                 # "red" = nur rote Pixel vergleichen (Kill-X; weißes X = Assist)

    def __post_init__(self):
        self.keywords = [normalize(k).strip() for k in self.keywords]
        self.any_of = [normalize(k).strip() for k in self.any_of]
        self.exclude = [normalize(k).strip() for k in self.exclude]
        self._template = None
        self._scaled = {}           # Bildhöhe -> skaliertes Template

    def _prepare(self, bgr: np.ndarray) -> np.ndarray:
        return color_mask(bgr) if self.color == "red" else cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    def template_for(self, height: int):
        if self._template is None:
            path = PRESET_DIR / self.template
            img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR) if path.is_file() else None
            if img is None:
                raise RuntimeError(f"Killdot-Bild fehlt ({self.template}) – in den Einstellungen neu markieren")
            self._template = img
        if height not in self._scaled:
            f = height / self.ref_height
            img = self._template if abs(f - 1) < 0.01 else cv2.resize(
                self._template, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)
            self._scaled[height] = self._prepare(img)
        return self._scaled[height]

    def sees_template(self, frame: np.ndarray) -> bool:
        crop = self.crop(frame)
        height = frame.shape[0]
        if self.color == "red" and height > self.ref_height:
            # Bildschirm (z. B. 1440p) auf die Markier-Auflösung bringen – wie beim Scan der Aufnahmen
            f = self.ref_height / height
            crop, height = cv2.resize(crop, None, fx=f, fy=f, interpolation=cv2.INTER_AREA), self.ref_height
        tmpl = self.template_for(height)
        img = self._prepare(crop)
        th, tw = tmpl.shape[:2]
        if img.shape[0] < th or img.shape[1] < tw:
            return False
        res = np.nan_to_num(cv2.matchTemplate(img, tmpl, cv2.TM_CCOEFF_NORMED), nan=0.0, posinf=0.0, neginf=0.0)
        if self.color == "red":
            # Das Kill-X sitzt immer am Fadenkreuz = Mitte des markierten Bereichs; Rot am Rand zählt nicht
            ey, ex = (img.shape[0] - th) // 2, (img.shape[1] - tw) // 2
            tol = max(3, max(th, tw) // 2)
            y0, x0 = max(0, ey - tol), max(0, ex - tol)
            res = res[y0:ey + tol + 1, x0:ex + tol + 1]
            _, score, _, (x, y) = cv2.minMaxLoc(res)
            x, y = x + x0, y + y0
        else:
            _, score, _, (x, y) = cv2.minMaxLoc(res)
        if self.color != "red" or score >= SURE_MATCH:
            return score >= self.match
        # Knapper Treffer: nur zählen, wenn das X frei steht (kein rotes Auto, Blut, Rücklicht drumherum)
        inner = cv2.countNonZero(img[y:y + th, x:x + tw])
        outer = cv2.countNonZero(img[max(0, y - th):y + 2 * th, max(0, x - tw):x + 2 * tw]) - inner
        return score >= self.match and outer <= inner // 2

    def _excluded(self, tokens: list[str]) -> bool:
        # OCR klebt Wörter zusammen („tkjwurdestvon“) -> lange Ausschlusswörter auch als Teilwort prüfen
        return any(_fuzzy_in(x, tokens, 0.8) or (len(x) >= 5 and any(x in t for t in tokens))
                   for x in self.exclude)

    def matching_lines(self, lines: list[str]) -> list[str]:
        out = []
        for line in lines:
            tokens = normalize(line).split()
            if not tokens or self._excluded(tokens):
                continue
            if self.any_of and not any(_fuzzy_in(k, tokens, 0.7) for k in self.any_of):
                continue
            if all(_fuzzy_in(k, tokens, self.threshold) for k in self.keywords):
                out.append(" ".join(tokens))
        return out

    def count_matches(self, lines: list[str]) -> int:
        return len(self.matching_lines(lines))

    def crop(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = self.region
        return frame[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)]


@dataclass
class Preset:
    name: str
    triggers: list

    @classmethod
    def load(cls, key: str):
        data = json.loads((PRESET_DIR / f"{key}.json").read_text(encoding="utf-8"))
        return cls(data["name"], [Trigger(**t) for t in data["triggers"]])


def list_presets() -> list[str]:
    return sorted(p.stem for p in PRESET_DIR.glob("*.json"))


NOTIFY_MEMORY_S = 15.0  # so lange (ab letzter Sichtung) gilt eine Kill-Meldung als „schon gezählt“
_ID_RE = re.compile(r"\bid\s*(\d+)")


def same_notify(a: str, b: str) -> bool:
    """Gleiche Kill-Meldung, auch wenn die OCR sie je Bild etwas anders liest."""
    ia, ib = _ID_RE.search(a), _ID_RE.search(b)
    if ia and ib:
        return ia.group(1) == ib.group(1)
    return SequenceMatcher(None, a, b).ratio() >= 0.6


class _TriggerState:
    """Feuert, wenn mehr Treffer sichtbar sind als im Rearm-Fenster davor (neue Notify im Stapel).

    Text-Meldungen zählen zusätzlich nur, wenn die Meldung neu ist: Schiebt eine andere Meldung
    (z. B. „Du wurdest … getötet“) eine alte Kill-Meldung im Stapel weiter, wird sie nicht nochmal gezählt.
    """

    def __init__(self, trigger: Trigger):
        self.trigger = trigger
        self.history = deque()
        self.prev_gray = None
        self.prev_count = 0
        self.prev_new = False
        self.seen: list[list] = []          # [zuletzt gesehen, Text] bereits gezählter Kill-Meldungen

    def update(self, t: float, frame: np.ndarray) -> bool:
        if self.trigger.kind == "template":
            self.prev_count = int(self.trigger.sees_template(frame))
            return self._record(t, self.prev_count)
        crop = self.trigger.crop(frame)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        # OCR nur, wenn sich der Bereich sichtbar geändert hat
        if self.prev_gray is None or gray.shape != self.prev_gray.shape or cv2.absdiff(gray, self.prev_gray).mean() > 3.0:
            self.prev_gray = gray
            lines = self.trigger.matching_lines(ocr.read_lines(crop, frame.shape[0]))
            self.prev_count = len(lines)
            self.prev_new = self._remember(t, lines)
        fired = self._record(t, self.prev_count)
        new, self.prev_new = self.prev_new, False
        return fired and new

    def _remember(self, t: float, lines: list[str]) -> bool:
        """Merkt sich die sichtbaren Kill-Meldungen; True, wenn eine noch nicht gezählte dabei ist."""
        self.seen = [s for s in self.seen if t - s[0] <= NOTIFY_MEMORY_S]
        new = False
        for line in lines:
            old = next((s for s in self.seen if same_notify(line, s[1])), None)
            if old:
                old[0] = t
            else:
                self.seen.append([t, line])
                new = True
        return new

    def _record(self, t: float, count: int) -> bool:
        while self.history and t - self.history[0][0] > self.trigger.rearm_s:
            self.history.popleft()
        recent_max = max((c for _, c in self.history), default=0)
        self.history.append((t, count))
        return count > recent_max


DETECT_KINDS = {"notify": {"ocr"}, "killdot": {"template"}, "beide": {"ocr", "template"}}
SAME_KILL_S = 6.0       # Killdot und Kill-Meldung innerhalb dieser Zeit = derselbe Kill (Meldung kommt bei Zeno bis ~5 s später)


class Detector:
    """mode: "notify" (Kill-Meldung), "killdot" (rotes X) oder "beide" (eins von beiden reicht).

    Bei "beide" laufen beide Erkennungen parallel; sehen beide denselben Kill, zählt er einmal.
    Fehlt eine davon (Killdot nicht markiert, keine deutsche Texterkennung), läuft die andere allein weiter.
    Tode immer per Text.
    """

    def __init__(self, preset: Preset, events: set[str], mode: str = "beide"):
        kinds = DETECT_KINDS.get(mode, {"ocr"})
        both = len(kinds) > 1

        def usable(t):
            if t.event != "kill":
                return True
            if t.kind not in kinds:
                return False
            # bei "beide" einen nicht markierten Killdot einfach weglassen
            return not (both and t.kind == "template" and not (PRESET_DIR / t.template).is_file())

        self.states = [_TriggerState(t) for t in preset.triggers if t.event in events and usable(t)]
        if "kill" in events and not any(s.trigger.event == "kill" for s in self.states):
            raise RuntimeError("Killdot ist noch nicht markiert – Einstellungen → „Im Bild markieren“"
                               if mode == "killdot" else "Im Preset fehlt die Kill-Notify-Erkennung")
        self.both = both
        self.min_fps = KILLDOT_FPS if any(s.trigger.kind == "template" for s in self.states) else 0.0
        self._open = {"ocr": [], "template": []}    # gezählte Kills, deren Gegenstück noch aussteht

    def update(self, t: float, frame: np.ndarray) -> list[str]:
        out = []
        for s in list(self.states):
            try:
                fired = s.update(t, frame)
            except Exception:
                if self.both and s.trigger.kind == "ocr" and len(self.states) > 1:
                    self.states.remove(s)          # keine Texterkennung verfügbar -> nur Killdot
                    continue
                raise
            if not fired:
                continue
            ev = s.trigger.event
            if ev == "kill" and self.both:
                # Jede Meldung der einen Erkennung „verbraucht“ höchstens eine der anderen (= derselbe Kill).
                # So bleiben zwei schnelle Kills zwei Kills, ein Kill mit X + Meldung aber einer.
                kind = s.trigger.kind
                other = "ocr" if kind == "template" else "template"
                self._open[other] = [x for x in self._open[other] if t - x < SAME_KILL_S]
                if self._open[other]:
                    self._open[other].pop(0)
                    continue
                self._open[kind].append(t)
            out.append(ev)
        return out


@dataclass
class Segment:
    start: float
    end: float
    first: float                    # Zeitpunkt des ersten Ereignisses
    kills: int = 0
    deaths: int = 0

    @property
    def label(self) -> str:
        if self.kills:
            return f"kill{self.kills if self.kills > 1 else ''}" + ("_death" if self.deaths else "")
        return "death"


class Segmenter:
    """Ereignis bei t -> Abschnitt [t-pre, t+post]; Ereignisse im Nachlauf verlängern ihn.

    join_overlap=True fasst auch Abschnitte zusammen, die sich nur überlappen würden (Batch).
    """

    def __init__(self, pre: float, post: float, join_overlap: bool):
        self.pre, self.post, self.join_overlap = pre, post, join_overlap
        self.current: Segment | None = None

    def add(self, t: float, event: str) -> Segment | None:
        """Gibt einen abgeschlossenen Abschnitt zurück, falls das Ereignis einen neuen beginnt."""
        cur = self.current
        reach = t - self.pre if self.join_overlap else t
        finished = None
        if cur and reach <= cur.end:
            cur.end = max(cur.end, t + self.post)
        else:
            finished = cur
            cur = self.current = Segment(max(0.0, t - self.pre), t + self.post, t)
        if event == "kill":
            cur.kills += 1
        else:
            cur.deaths += 1
        return finished

    def pop_if_done(self, now: float) -> Segment | None:
        if self.current and now >= self.current.end:
            seg, self.current = self.current, None
            return seg
        return None

    def flush(self) -> Segment | None:
        seg, self.current = self.current, None
        return seg
