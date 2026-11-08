"""Kenanja auf der Kommandozeile.

  kenanja                     Kenanja im eigenen Fenster starten
  kenanja web                 Oberfläche stattdessen im Browser
  kenanja analyse SONG.mp3 [--text liedtext.txt] [--titel …] [--autoren …] [-o song.kenanja.json]
  kenanja import LEADSHEET.mscz [-o song.kenanja.json]
  kenanja tonart song.kenanja.json
  kenanja erzeugen song.kenanja.json [-o Ausgabeordner] [--transponieren N] [--bitrate 256] [--band S&L]
  kenanja pruefen             zeigt, ob MuseScore, LibreOffice, Rubber Band und die Modelle bereit sind
  kenanja deinstallieren      entfernt Kenanja restlos (Projekte bleiben; mit --alles auch die Projekte)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(prog='kenanja', description='Kenanja – aus einem MP3 Chord-Sheet, Leadsheet, '
                                 'Streicherstimmen, Chorblatt und Übe-Audio für deine Band machen.')
    from .util import version_text
    ap.add_argument('--version', action='version', version=f'Kenanja {version_text()}')
    sub = ap.add_subparsers(dest='cmd')
    fe = sub.add_parser('fenster', help='im eigenen Fenster starten (Standard)')
    fe.add_argument('--port', type=int, default=0)
    w = sub.add_parser('web', help='Oberfläche im normalen Browser starten')
    w.add_argument('--port', type=int, default=8765)
    w.add_argument('--kein-browser', action='store_true')
    a = sub.add_parser('analyse', help='Audio analysieren')
    a.add_argument('audio')
    a.add_argument('--text', help='Liedtext (Datei, in Sing-Reihenfolge)')
    a.add_argument('--titel', default='')
    a.add_argument('--autoren', default='')
    a.add_argument('-o', '--out')
    i = sub.add_parser('import', help='MuseScore-Datei übernehmen')
    i.add_argument('datei')
    i.add_argument('-o', '--out')
    t = sub.add_parser('tonart', help='Tonart-Vorschläge anhand der Stimmprofile')
    t.add_argument('song')
    e = sub.add_parser('erzeugen', help='alle Blätter, Noten und Übe-Audio erzeugen')
    e.add_argument('song')
    e.add_argument('-o', '--out', help='Zielordner (Standard: „Export“ neben der Song-Datei)')
    e.add_argument('--transponieren', type=int)
    e.add_argument('--bitrate', default='256')
    e.add_argument('--band', default='')
    e.add_argument('--nebenstimmen', action='store_true')
    e.add_argument('--satb', action='store_true')
    sub.add_parser('pruefen', help='Installation prüfen')
    de = sub.add_parser('deinstallieren', help='Kenanja restlos entfernen (Projekte bleiben, außer --alles)')
    de.add_argument('--alles', action='store_true', help='auch alle Kenanja-Projekte löschen')
    args = ap.parse_args(argv)

    if args.cmd in (None, 'fenster'):
        from .fenster import main as fenster
        sys.exit(fenster(port=getattr(args, 'port', 0)))
    if args.cmd == 'deinstallieren':
        from .util import uninstall
        uninstall(args.alles)
        return
    if args.cmd == 'web':
        from .web.app import main as web
        web(port=args.port, open_browser=not args.kein_browser)
        return
    if args.cmd == 'analyse':
        from .audio.analysis import build_song
        text = Path(args.text).read_text(encoding='utf-8') if args.text else None
        res = build_song(args.audio, text, title=args.titel, authors=args.autoren,
                         progress=lambda n, f: print(f'{f:5.0%}  {n}', flush=True))
        out = args.out or str(Path(args.audio).with_suffix('.kenanja.json'))
        res.song.save(out)
        print(json.dumps(res.report, ensure_ascii=False))
        print('gespeichert:', out)
    elif args.cmd == 'import':
        from .importers.musescore import import_musescore
        s = import_musescore(args.datei)
        out = args.out or str(Path(args.datei).with_suffix('.kenanja.json'))
        s.save(out)
        print('gespeichert:', out)
    elif args.cmd == 'tonart':
        from .model import Song
        from .voices import format_suggestions, load_profiles, suggest_keys, ambitus_report
        s = Song.load(args.song)
        print(json.dumps(ambitus_report(s, s.transpose), ensure_ascii=False, indent=1))
        print(format_suggestions(suggest_keys(s, load_profiles())))
    elif args.cmd == 'erzeugen':
        from .export import ExportOptions, export_all
        from .model import Song
        s = Song.load(args.song)
        if args.transponieren is not None:
            s.transpose = args.transponieren
        res = export_all(s, opts=ExportOptions(bitrate=args.bitrate, band=args.band,
                                               harmony_voices=args.nebenstimmen, satb=args.satb),
                         folder=args.out or Path(args.song).resolve().parent / 'Export')
        print(json.dumps(res, ensure_ascii=False, indent=1))
    elif args.cmd == 'pruefen':
        check()


def check():
    import shutil
    from .notation.musescore import find_musescore, version
    from .sheets.odt import find_soffice
    ok = True
    ms = find_musescore()
    print('MuseScore   :', (' '.join(ms) + '  ' + version(ms)) if ms else 'NICHT GEFUNDEN (Noten nur als MusicXML)')
    so = find_soffice()
    print('LibreOffice :', so or 'NICHT GEFUNDEN (keine PDFs der Textblätter)')
    rb = shutil.which('rubberband')
    print('Rubber Band :', rb or 'NICHT GEFUNDEN (sudo apt install rubberband-cli)')
    ff = shutil.which('ffmpeg')
    print('ffmpeg      :', ff or 'NICHT GEFUNDEN (sudo apt install ffmpeg)')
    try:
        from .audio.btc import ensure_weights
        print('Akkordmodell:', ensure_weights())
        from .audio.separate import ensure_model
        print('Trennmodell :', ensure_model())
    except Exception as e:
        ok = False
        print('Modelle     : Download fehlgeschlagen –', e)
    try:
        import faster_whisper  # noqa
        print('Whisper     : installiert (Text wird zusätzlich per Spracherkennung ausgerichtet)')
    except ImportError:
        print('Whisper     : nicht enthalten (optional) – der Text wird über die Silben der Melodie zugeordnet')
    return ok and bool(ff)


if __name__ == '__main__':
    main()
