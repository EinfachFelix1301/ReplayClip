# ReplayClip

Clippt Kills in Spielen automatisch – 20 Sekunden davor und danach. Entwickelt für FiveM, mit eigenen Presets
auch für andere Spiele und Server nutzbar.

- **Live:** erkennt die Kill-Meldung am Bildschirm, drückt 20 s später den Replay-Hotkey (NVIDIA Instant Replay)
  oder lässt OBS den Replay-Puffer speichern, und schneidet das Replay auf den Kill zu (Stream-Copy, kein Neu-Encoden).
- **Ordner scannen:** Ordner wählen → alle Videos darin (inkl. Unterordner) werden durchsucht, Clips landen in
  `<Ordner>\Kills`. Schon gescannte Videos merkt sich `Kills\.replayclip.json` und überspringt sie.
- **Einzelne Videos:** „Dateien …“ wählen oder Videos auf die Verknüpfung ziehen.
- **Galerie:** alle Clips mit Vorschaubild. Klick spielt ab, Ordner-Symbol zeigt die Datei im Explorer.
- **Tode suchen:** wie Kills, aber mit eigenem Ordner `Tode`.
- **Montage:** Clips auswählen, Reihenfolge festlegen, Musik darunter (Musik und Spielsound getrennt regelbar).
  Ergebnis landet in `<Ausgabeordner>\Montagen`.
- **Erkennung:** Kill-Meldung (Text per OCR) oder Killdot (Bild). Mehrere Kills kurz hintereinander landen
  in einem Clip (`kill3_…mp4`).

Alles läuft lokal auf deinem PC. Es wird nichts hochgeladen.

---

## Installation (Windows 10/11)

### Schritt 1: FFmpeg installieren

ReplayClip braucht FFmpeg zum Schneiden. Am einfachsten in einer PowerShell:

```powershell
winget install Gyan.FFmpeg
```

Danach PowerShell neu öffnen und prüfen:

```powershell
ffmpeg -version
```

Erscheint eine Versionsnummer, passt es. Wenn `winget` nicht gefunden wird, FFmpeg von
<https://www.gyan.dev/ffmpeg/builds/> laden und den `bin`-Ordner in die Umgebungsvariable `PATH` eintragen.

### Schritt 2: ReplayClip holen

**Variante A – fertige Version (empfohlen):**

1. Unter [Releases](https://github.com/EinfachFelix1301/ReplayClip/releases) die Datei `ReplayClip-Windows.zip` herunterladen.
2. Rechtsklick → **Alle extrahieren …**. Den ganzen Ordner `ReplayClip` entpacken, nicht nur die EXE kopieren.
3. `ReplayClip.exe` im Ordner doppelklicken.

Windows meldet bei unbekannten Programmen oft „Der Computer wurde durch Windows geschützt“. Dann auf
**Weitere Informationen** → **Trotzdem ausführen** klicken. Das Programm ist nicht signiert, der Quellcode liegt offen im Repo.

**Variante B – aus dem Quellcode:**

1. Python 3.12 installieren (<https://www.python.org/downloads/>, Haken bei „Add python.exe to PATH“).
2. Repo holen: `git clone https://github.com/EinfachFelix1301/ReplayClip.git` (oder ZIP herunterladen und entpacken).
3. Im Ordner:
   ```powershell
   pip install -r requirements.txt
   python ReplayClip.pyw
   ```

Die Einstellungen (`config.json`) entstehen beim ersten Start neben der EXE bzw. im Projektordner.

---

## Einrichtung: Aufnahme

ReplayClip braucht einen Replay-Puffer, also eine Aufnahme, die immer die letzten Minuten behält.
Dafür gibt es zwei Wege.

### Weg 1: NVIDIA Instant Replay (Standard bei NVIDIA-Grafikkarten)

1. In der **NVIDIA App** → Einstellungen → Aufnahmen: **Instant Replay** einschalten.
2. Länge auf **2–3 Minuten** stellen.
3. Den Hotkey zum Speichern so lassen, Standard ist **Alt + F10**. ReplayClip liest ihn aus der NVIDIA App und drückt ihn selbst.
4. Falls Spiele nicht aufgenommen werden: unter Windows **Einstellungen → Datenschutz → Desktop-Aufnahme** auf **Ein** stellen.
5. In ReplayClip unter **Einstellungen** → **Clippen mit** → **NVIDIA** lassen.

### Weg 2: OBS (auch für AMD und Intel)

1. OBS Studio installieren (<https://obsproject.com>).
2. In OBS: **Werkzeuge → WebSocket-Server-Einstellungen → „WebSocket-Server aktivieren“** anhaken.
3. Unten bei **Quellen** auf **+** → **Bildschirmaufnahme** (oder **Spielaufnahme**) → OK.
4. In ReplayClip unter **Einstellungen** → **Clippen mit** → **OBS** stellen. ReplayClip testet die Verbindung.
   Unten muss stehen: *„OBS … verbunden – Replay-Puffer läuft (180 s)“*.

ReplayClip startet OBS dann selbst (minimiert im Tray), schaltet den Replay-Puffer ein und clippt automatisch.
Das WebSocket-Passwort liest ReplayClip automatisch aus OBS. Nur wenn OBS auf einem anderen PC läuft, muss
das Passwort ins Feld unter „Clippen mit“ eingetragen werden.

---

## Benutzung

1. ReplayClip starten und offen lassen (minimieren geht).
2. Spiel starten. Oben steht *„Live: aktiv – wartet auf Kills“*, sobald die Erkennung bereit ist.
3. Spielen. Nach jedem Kill wartet ReplayClip den Nachlauf ab, speichert und schneidet. Unten kommt kurz
   *„Kill geclippt“*.

Fertige Clips liegen unter `Videos\ReplayClips\Kills` (Ausgabeordner ist in den Einstellungen änderbar),
mit Uhrzeit im Namen.

Auf einen Clip klicken zeigt ihn mit roter Markierung, wo der Kill ist. Links unter **Letzte Erkennung** sieht man
das Bild, bei dem der Kill erkannt wurde. Das hilft beim Einstellen des Presets.

### Bestehende Aufnahmen durchsuchen

**Ordner scannen** wählen und den Ordner mit alten Videos angeben. ReplayClip sucht alle Kills darin, auch in
Unterordnern. Bereits gescannte Videos werden beim nächsten Mal übersprungen.

### Montage

Bei Clips auf **＋** klicken oder einen Zeitraum übernehmen. In der Vorschau pro Clip **Start hier** / **Ende hier**
setzen, die Reihenfolge mit ↑ / ↓ ändern. Musik entweder als eigene MP3 wählen oder über **Musik suchen**
(NoCopyrightSounds, siehe unten) bzw. **Musik per Link** (yt-dlp) laden. Dann **Montage erstellen**.

---

## Eigenes Preset für einen Server

Die Erkennung hängt vom Spiel und vom Server ab (Schriftfarbe, Position der Meldung). Dafür gibt es Presets im
Ordner `presets`. Das Beispiel `beispiel.json` kopieren, umbenennen und anpassen:

| Feld | Bedeutung |
|---|---|
| `name` | Anzeigename im Programm |
| `region` | Bildausschnitt für die Kill-Meldung, relativ zum Bild: `[x1, y1, x2, y2]` von 0 bis 1 |
| `keywords` | Wörter, die in einer Textzeile vorkommen müssen (OCR-Fehler werden toleriert) |
| `any_of` | Mindestens eines davon muss zusätzlich vorkommen |
| `exclude` | Zeile ignorieren, wenn eines davon vorkommt |
| `rearm_s` | So viele Sekunden muss die Meldung weg sein, bevor sie wieder zählt |
| `jobs` | Optional: Zuordnung von Schlüsselwörtern im HUD zu Unterordnern |

Am einfachsten geht es über die Oberfläche: unter **Erkennung** den Bereich **„Im Bild markieren“** verwenden.
Der markierte Killdot wird als Bild in den Preset-Ordner geschrieben (`<name>_killdot.png`) und ins Preset eingetragen.

---

## Fehlerbehebung

| Meldung / Problem | Lösung |
|---|---|
| „ffmpeg nicht gefunden“ | FFmpeg installieren (Schritt 1), PowerShell neu öffnen, ReplayClip neu starten |
| Dauerhaft „Live: wartet auf FiveM …“ | Spiel läuft nicht oder lädt noch. ReplayClip merkt es nach ein paar Sekunden von selbst |
| „OBS nicht erreichbar“ | In OBS den WebSocket-Server aktivieren (Weg 2, Schritt 2), dann in ReplayClip **OBS testen** |
| „OBS-Passwort falsch“ | In OBS unter **WebSocket-Server-Einstellungen → Verbindungsinformationen anzeigen** das Passwort kopieren und unter „Clippen mit“ eintragen |
| Clips bleiben leer oder schwarz | Desktop-Aufnahme in Windows erlauben (Weg 1, Schritt 4) |
| Kills werden nicht erkannt | Preset und Bildausschnitt prüfen, „Letzte Erkennung“ zeigt das gesehene Bild |
| Antivirus meldet die EXE | Fehlalarm bei unsignierten Programmen. Quellcode prüfen oder aus dem Quellcode starten (Variante B) |

Die Einstellungen liegen in `config.json`. Das Protokoll ist unten im Fenster aufklappbar (**Protokoll**).

---

## Entwickeln und selbst bauen

```powershell
pip install -r requirements.txt pyinstaller
python ReplayClip.pyw            # Entwicklungsstart
python build.py                  # baut dist\ReplayClip-Windows.zip
```

Mit `$env:FFMPEG_DIR = "C:\pfad\zu\ffmpeg\bin"` vor `python build.py` wird FFmpeg direkt ins Paket übernommen.
Dann gelten die Lizenzbedingungen von FFmpeg (siehe unten).

Vor einem Release lohnt der eingebaute Selbsttest: `ReplayClip.exe --self-test` schreibt `selftest.txt`.
`build.py` führt ihn automatisch aus.

---

## Was ReplayClip macht – und was nicht

ReplayClip ist ein reines **Aufnahme-Werkzeug**. Es liest nur das Bild vom Bildschirm (wie eine Aufnahme-Software),
erkennt darin die Kill-Meldung und sagt NVIDIA Instant Replay bzw. OBS, dass gespeichert werden soll.
Es greift **nicht** ins Spiel ein: kein Einlesen von Spielspeicher, keine Injection, keine Mods, kein Vorteil im Spiel.

## Leistung

2 Bilder pro Sekunde Capture (DXGI). OCR läuft nur, wenn sich der Bereich der Kill-Meldung ändert.
Der Prozess läuft mit niedriger Priorität. Gemessen: ca. 1 % CPU, keine GPU-Last.

## Lizenzen und Drittanbieter

- Code: [MIT](LICENSE)
- Schrift Chakra Petch: SIL Open Font License 1.1, siehe [presets/OFL-ChakraPetch.txt](presets/OFL-ChakraPetch.txt)
- FFmpeg ist **nicht** im Repo enthalten. Beim Bauen mit `FFMPEG_DIR` gelten die Lizenzen von FFmpeg (LGPL/GPL, ffmpeg.org).
- Musik-Suche nutzt [NoCopyrightSounds](https://ncs.io). Die Tracks sind frei für Videos, **Quellenangabe ist Pflicht**
  (ReplayClip legt sie als `.txt` neben die Musik). Es werden keine Musikdateien mitgeliefert.
- Musik per Link (SoundCloud u. a., über [yt-dlp](https://github.com/yt-dlp/yt-dlp)): nur Titel laden, die du nutzen
  darfst. Geschützte Songs werden auf TikTok/YouTube stummgeschaltet oder gesperrt.
- GTA V und FiveM sind Marken ihrer jeweiligen Inhaber. ReplayClip ist ein inoffizielles Fan-Tool und steht in keiner
  Verbindung zu Rockstar Games, Take-Two oder Cfx.re.

## Mitmachen

Fehler und Wünsche bitte als [Issue](https://github.com/EinfachFelix1301/ReplayClip/issues) melden. Für neue
Server-Presets gern einen Pull Request mit der JSON-Datei und einem Screenshot der Erkennung.
