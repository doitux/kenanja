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

Alles läuft **lokal auf dem eigenen Rechner** (Linux, ohne Grafikkarte) – in einem eigenen Programmfenster.

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

## Installation (Kubuntu/Ubuntu 22.04 und neuer)

Kenanja gibt es als fertiges Programm (**AppImage**, eine einzige Datei) unter
[Releases → Testversion](https://github.com/doitux/kenanja/releases/tag/nightly). Einmal die Hilfsprogramme
installieren, AppImage herunterladen, ausführbar machen, starten:

```bash
sudo apt install ffmpeg rubberband-cli libreoffice-writer fonts-liberation
mkdir -p ~/Programme && mv Kenanja-*-x86_64.AppImage ~/Programme/Kenanja.AppImage
chmod +x ~/Programme/Kenanja.AppImage && ~/Programme/Kenanja.AppImage
```

Beim ersten Start legt Kenanja einen Eintrag im Startmenü an; die Modelle (ca. 90 MB) lädt es bei der ersten
Analyse. **MuseScore 4** (AppImage, Flatpak oder Paket) wird automatisch gefunden; sonst den Pfad in den
Einstellungen eintragen. Restlos entfernen: `./Kenanja-…AppImage deinstallieren` (Projekte bleiben,
mit `--alles` auch die Projekte).

Aus dem Quellcode (Entwicklung): `git clone https://github.com/doitux/kenanja.git && cd kenanja && bash install.sh`
richtet Kenanja in einer Python-Umgebung unter `~/.local/share/kenanja` ein. Die AppImage selbst wird bei jedem
Stand auf `main` automatisch gebaut und getestet (`packaging/`, `.github/workflows/ci.yml`).

Start: im Startmenü **Kenanja** oder im Terminal `kenanja` – es öffnet sich ein eigenes Fenster (Qt WebEngine). Ein zweiter Start holt das offene Fenster nach vorne. Lieber im normalen Browser: `kenanja web`
(oder Rechtsklick auf den Startmenü-Eintrag → „Im Browser öffnen“). Bleibt das Fenster weiß, schaltet Kenanja
selbst auf eine einfachere Grafikausgabe um.

**Projekte** liegen standardmäßig in `~/Projekte/Kenanja` (in den Einstellungen änderbar, vorhandene Projekte
werden dann mit verschoben). Jeder Song bekommt einen Ordner mit seinem Namen:

```
~/Projekte/Kenanja/Mein Song/
├── projekt.json, song.kenanja.json, liedtext.txt   Bearbeitungsstand
├── Original/    dein MP3/WAV bzw. die importierte MuseScore-Datei
├── Analyse/     Zwischenspeicher (getrennter Gesang) – darf gelöscht werden
├── Export/      Chord-Sheet, Leadsheet, Partitur, Stimmen, Chorblatt, Übe-MP3 …
└── Vorschau/    schnelle Vorschau-PDFs
```

Ändert sich der Titel, wird der Ordner mit umbenannt. Optional kopiert Kenanja jeden Export zusätzlich in einen
**Band-Ordner** (z. B. den geteilten Cloud-Ordner).

**Wo liegt sonst noch was?** Programm: `~/.local/share/kenanja` · Befehl: `~/.local/bin/kenanja` · Modelle:
`~/.cache/kenanja` · Einstellungen und Stimmprofile: `~/.config/kenanja`.
**Entfernen:** `kenanja deinstallieren` (Projekte bleiben) bzw. `kenanja deinstallieren --alles`.

### Kommandozeile

```bash
kenanja analyse song.mp3 --text liedtext.txt --titel "Mein Song" --autoren "Vorname Nachname"
kenanja import leadsheet.mscz
kenanja tonart song.kenanja.json
kenanja erzeugen song.kenanja.json -o ~/Band/Songs/Mein\ Song --transponieren -3 --bitrate 320 --band "Band"
kenanja pruefen
```

## Wie gut ist das?

Kenanja liefert einen **Entwurf**, den man in wenigen Minuten nachbessert – keinen fertigen Satz.
Erfahrungswerte aus Tests mit echten Aufnahmen:

* Tempo, Taktart und Tonart: zuverlässig.
* Akkorde: Grundgerüst stimmt meist; Feinheiten (sus, add9, Slash) brauchen einen prüfenden Blick.
* Melodie: Kenanja trennt zuerst die Leadstimme vom Chor/Backgroundgesang und notiert wie ein
  Gemeinde-Leadsheet: eine Note je Silbe, gleiche Zeilen gleich, im Zweifel die einfachere Variante,
  immer in Frauenlage. Das Leadsheet trotzdem in MuseScore durchsehen.
* Tonart-Wahl: Die höchsten und tiefsten Töne entscheiden über die Zieltonart. Unter „Stimmen & Tonart“
  listet Kenanja genau diese Stellen; jede lässt sich anhören (getrennte Leadstimme und erkannter Ton)
  und mit einem Klick korrigieren oder als Verzierung von der Wertung ausnehmen.
* Text: Mit der Spracherkennung (Einstellungen → „Spracherkennung herunterladen“, einmalig ca. 650 MB,
  danach offline; oder `kenanja sprachmodell`) legt Kenanja jedes erkannte Wort dort an, wo es gesungen
  wird – das verbessert Text und Melodie deutlich. Ohne sie wird der Text über die Gesangsphrasen zugeordnet.
  Passt der eingefügte Text nicht zur Aufnahme (z. B. andere Version), warnt Kenanja.

## Verwendete freie Software und Modelle

* Akkorderkennung: **BTC** – Bi-directional Transformer for Chord recognition (Park et al., ISMIR 2019), MIT-Lizenz,
  https://github.com/jayg996/BTC-ISMIR19 – hier ohne PyTorch in numpy nachgerechnet.
* Gesangstrennung: **UVR-MDX-NET Voc_FT**, Leadstimme vom Chor: **UVR_MDXNET_KARA_2** – beide aus dem Ultimate-Vocal-Remover-Projekt (ONNX), https://github.com/TRvlvr/model_repo
* Tonhöhe: pYIN (librosa) · Silbentrennung: Pyphen · Noten: music21, MuseScore · Text: LibreOffice
* Transponieren: **Rubber Band** (R3-Engine, Formanterhalt)
* Spracherkennung (optional): **sherpa-onnx** mit **NVIDIA Parakeet TDT 0.6B v3** (CC-BY-4.0), https://github.com/k2-fsa/sherpa-onnx

Lizenz: GPL-3.0-or-later. Songtexte und Aufnahmen gehören ihren Rechteinhabern – Kenanja ist für die
Probenvorbereitung im Rahmen der Lizenzen eurer Gemeinde (z. B. CCLI) gedacht.

*Soli Deo Gloria.*
