# KillClip

Clippt FiveM-Kills automatisch: 20 s davor, 20 s danach.

- **Live:** erkennt die Kill-Notify am Bildschirm, drückt 20 s später den NVIDIA-Instant-Replay-Hotkey
  und schneidet das Replay auf den Kill zu (Stream-Copy, kein Neu-Encoden).
- **Ordner scannen:** Ordner wählen → alle Videos darin (inkl. Unterordner) werden durchsucht,
  Clips landen in `<Ordner>\Kills`. Schon gescannte Videos merkt sich `Kills\.killclip.json` und überspringt sie.
- **Einzelne Videos:** „Dateien …“ oder Videos auf die Desktop-Verknüpfung ziehen.
- **Galerie:** alle Clips mit Vorschaubild, Klick spielt ab, Ordner-Symbol zeigt die Datei im Explorer.
- Kills kurz hintereinander landen in einem Clip (`kill3_…mp4`).
- **Montage:** bei Clips auf ＋ klicken oder in „Montage“ einen Zeitraum (Von/Bis) übernehmen. Vorschau im Player,
  pro Clip „Start hier“/„Ende hier“, Reihenfolge mit ↑/↓. Musik darunter (Musik/Spielsound getrennt regelbar,
  Ausblenden am Ende). Ergebnis landet in `<Ausgabeordner>\Montagen` (NVENC, wenn verfügbar).
- **Erkennung:** Kill-Notify (Text) oder Killdot (Bild). „Im Bild markieren“: Rahmen um den Kill-Notify-Bereich
  bzw. eng um den Killdot ziehen – wird ins Preset geschrieben (`presets/<name>_killdot.png`).

## Voraussetzungen

- NVIDIA Instant Replay **an** (`Alt+Shift+F10`), Länge in der NVIDIA App auf **2–3 min**.
- Python 3.12, FFmpeg im PATH, `pip install -r requirements.txt`.
- Windows-OCR mit Deutsch (ist bei deutschem Windows dabei).

## FPS

2 Bilder/s Capture (DXGI), OCR nur wenn sich der Notify-Bereich ändert, Prozess auf „niedriger als normal“.
Gemessen: ~1 % CPU, keine GPU-Last.

## Neuer Server

`presets/<name>.json` kopieren und anpassen:

- `region`: x1, y1, x2, y2 relativ zum Bild (0..1)
- `keywords`: müssen in einer Textzeile vorkommen (OCR-Fehler werden toleriert)
- `any_of`: mindestens eins davon zusätzlich
- `exclude`: Zeile ignorieren, wenn das vorkommt
- `rearm_s`: so lange muss die Meldung weg sein, bevor sie neu zählt

Einstellungen landen in `config.json` (Ausgabeordner, Vor-/Nachlauf, Replay löschen nach Schnitt usw.).

## Was KillClip macht – und was nicht

KillClip ist ein reines **Aufnahme-Werkzeug**. Es liest nur das Bild vom Bildschirm (wie eine Aufnahme-Software),
erkennt darin die Kill-Meldung und sagt NVIDIA Instant Replay bzw. OBS, dass gespeichert werden soll.
Es greift **nicht** ins Spiel ein: kein Einlesen von Spielspeicher, keine Injection, keine Mods, kein Vorteil im Spiel.

## Lizenzen und Drittanbieter

- Code: [MIT](LICENSE)
- Schrift Chakra Petch: SIL Open Font License 1.1 – siehe [presets/OFL-ChakraPetch.txt](presets/OFL-ChakraPetch.txt)
- FFmpeg ist **nicht** enthalten (beim Build aus einer eigenen FFmpeg-Installation, LGPL/GPL, ffmpeg.org).
- Musik-Suche nutzt [NoCopyrightSounds](https://ncs.io) – Tracks sind frei für Videos, **Quellenangabe Pflicht**
  (KillClip legt sie als `.txt` neben die Musik). Es werden keine Musikdateien mitgeliefert.
- Musik per Link (SoundCloud u. a., über [yt-dlp](https://github.com/yt-dlp/yt-dlp)): nur Titel laden, die du
  nutzen darfst. Geschützte Songs werden auf TikTok/YouTube stummgeschaltet oder gesperrt.
- ZenoCity-Logo: gehört ZenoCity (zenocity.cc). GTA V / FiveM sind Marken ihrer jeweiligen Inhaber;
  KillClip ist ein inoffizielles Fan-Tool und steht in keiner Verbindung zu Rockstar Games, Take-Two oder Cfx.re.
