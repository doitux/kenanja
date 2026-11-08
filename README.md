# Kenanja

> „Kenanja aber, der Oberste der Leviten, leitete den Gesang; er unterwies sie darin, denn er verstand sich darauf.“
> – nach 1. Chronik 15,22

**Kenanja macht aus einer Aufnahme (MP3/WAV) die Unterlagen, die eine Lobpreisband für einen neuen Song braucht:**

| Ergebnis | Format |
|---|---|
| Chord-Sheet mit Besetzungsangaben (+grün / −rot), Akkorde exakt über der Silbe | LibreOffice (.odt) + PDF |
| Leadsheet: Melodie, Akkorde, Text, Formteile | MuseScore (.mscz) + PDF |
| Partitur mit Streicherstimmen (Geige, Bratsche, Cello – nach Spielerniveau) + Einzelstimmen | MuseScore + PDFs |
| Chor-Textblatt (eine Seite) mit Ausdrucksanweisungen im Farbcode | .odt + PDF |
| Blatt für Akustikgitarre (vereinfachte Akkorde, Griffbilder) und Klavier (mit Intro-Noten) | .odt + PDF |
| Nebenstimmen für Sänger, Chorsatz SATB | MuseScore + PDF |
| Übe-Audio: Original in eure Tonart verschoben und auf euren Ablauf geschnitten | MP3, Bitrate wählbar |

Dazu: **Stimmprofile** (wer singt bequem bis wohin, wo wird es dünn, was schafft die Gemeinde) und ein
**Tonart-Vorschlag**, der zeigt, wie oft und wie lange Spitzen- und Tieftöne vorkommen.

Alles läuft **lokal auf dem eigenen Rechner** (Linux, ohne Grafikkarte). Die Bedienung erfolgt im Browser.

## Ablauf

1. **Neuer Song:** Audio wählen, Titel/Autoren eintragen, Liedtext einfügen (in Sing-Reihenfolge, gern mit
   Überschriften wie `[Verse 1]`, `[Chorus]`; Hinweise wie `(Ladies)` / `(Men)` werden übernommen).
   „Text suchen“ öffnet eine Websuche – kopiert wird von Hand.
2. **Analyse** (einige Minuten): Gesang/Begleitung trennen, Tempo, Taktart (3/4 oder 4/4), Taktanfänge,
   Tonart, Akkorde (inkl. Slash-Akkorde), Melodie, Text auf die Melodie legen, Formteile.
3. **Song & Text prüfen:** Akkorde pro Takt und Textzeilen korrigieren, jeden Teil im Original anhören.
4. **Ablauf:** Teile verschieben, doppeln, wiederholen, entfernen; festlegen, welches Original-Audio ein Teil
   im Übe-Audio bekommt (z. B. Schluss-Chorus mit dem ruhigen Audio von Chorus 1).
5. **Besetzung:** Raster „Teil × Instrument“ → daraus entstehen die +/− Angaben. Chor-Anweisungen
   (rot = leiser, grün = lauter, lila = Solo/nicht mitsingen), Streicherrollen (oder automatisch).
6. **Stimmen & Tonart:** Wer singt welchen Teil, singt die Gemeinde mit? Rangliste aller Tonarten mit Begründung.
7. **Erzeugen:** alle Dateien in einen Ordner `Titel - Autor/` (Namensschema wie im Bandordner).

In MuseScore korrigierte Leadsheets lassen sich wieder **importieren** – danach Chord-Sheet, Streicher usw. neu erzeugen.

## Installation (Kubuntu/Ubuntu)

```bash
git clone https://github.com/doitux/kenanja.git
cd kenanja
bash install.sh
```

`install.sh` installiert die Systempakete (ffmpeg, Rubber Band, LibreOffice Writer, Liberation-Schriften),
richtet Kenanja in einer eigenen Python-Umgebung ein (`~/.local/share/kenanja`), legt den Befehl `kenanja`
und einen Eintrag im Startmenü an und lädt die Modelle. **MuseScore 4** (AppImage, Flatpak oder Paket) wird
automatisch gefunden; sonst den Pfad in den Einstellungen eintragen.

Start: im Startmenü **Kenanja** oder im Terminal `kenanja` → der Browser öffnet `http://127.0.0.1:8765`.

**Wo liegt was?** Programm: `~/.local/share/kenanja` · Befehl: `~/.local/bin/kenanja` · Modelle und
zwischengespeicherte Analysen: `~/.cache/kenanja` · Stimmprofile: `~/.config/kenanja` · Projekte: `~/Kenanja`.
**Entfernen:** `bash uninstall.sh` (Projekte bleiben) bzw. `bash uninstall.sh --alles`.

### Kommandozeile

```bash
kenanja analyse song.mp3 --text liedtext.txt --titel "Draw Me Close" --autoren "Kelly Carpenter"
kenanja import leadsheet.mscz
kenanja tonart song.kenanja.json
kenanja erzeugen song.kenanja.json -o ~/Band/Songs --transponieren -3 --bitrate 320 --band "S&L"
kenanja pruefen
```

## Wie gut ist das?

Kenanja liefert einen **Entwurf**, den man in wenigen Minuten nachbessert – keinen fertigen Satz.
Gemessen an echten Songs der Band:

* Tempo, Taktart und Tonart: zuverlässig.
* Akkorde: Grundgerüst stimmt meist; Feinheiten (sus, add9, Slash) brauchen einen prüfenden Blick.
* Melodie: Einsätze werden fast alle gefunden, die Tonhöhe stimmt bei rund drei Vierteln der Noten –
  das Leadsheet daher in MuseScore durchsehen.
* Text: wird zeilenweise den Gesangsphrasen zugeordnet. Mit installiertem Whisper (Spracherkennung)
  wird der Text zusätzlich an den gesungenen Wörtern verankert – das hilft bei Wiederholungen.
  Passt der eingefügte Text nicht zur Aufnahme (z. B. andere Version), warnt Kenanja.

## Verwendete freie Software und Modelle

* Akkorderkennung: **BTC** – Bi-directional Transformer for Chord recognition (Park et al., ISMIR 2019), MIT-Lizenz,
  https://github.com/jayg996/BTC-ISMIR19 – hier ohne PyTorch in numpy nachgerechnet.
* Gesangstrennung: **UVR-MDX-NET Voc_FT** aus dem Ultimate-Vocal-Remover-Projekt (ONNX), https://github.com/TRvlvr/model_repo
* Tonhöhe: pYIN (librosa) · Silbentrennung: Pyphen · Noten: music21, MuseScore · Text: LibreOffice
* Transponieren: **Rubber Band** (R3-Engine, Formanterhalt) · optional Spracherkennung: faster-whisper

Lizenz: GPL-3.0-or-later. Songtexte und Aufnahmen gehören ihren Rechteinhabern – Kenanja ist für die
Probenvorbereitung im Rahmen der Lizenzen eurer Gemeinde (z. B. CCLI) gedacht.

*Soli Deo Gloria.*
