"""Alles für einen Song erzeugen – mit einheitlichen Ordner- und Dateinamen:

  <Titel> - <Autor>/
      <Titel> - Chordsheet.odt / .pdf
      <Titel> - Leadsheet.mscz / .pdf
      <Titel> - Partitur.mscz / .pdf  (+ Einzelstimmen: <Titel> - Violine.pdf …)
      <Titel> - Chortext v1.0.odt / .pdf
      <Titel> - A-GIT.odt / .pdf, <Titel> - Klavier.odt / .pdf
      <Titel> - Nebenstimmen.mscz / .pdf, <Titel> - Chorsatz SATB.mscz / .pdf
      <Titel> - Ablauf.mp3 (transponiert + geschnitten), Übe-Spuren aus MuseScore
      <Titel>.kenanja.json  (Projektdatei zum Weiterbearbeiten)
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .arranger.strings import Player, arrange_strings, linearize, plan_roles
from .arranger.vocal import harmony_voice, satb
from .model import Song
from .notation import musescore as MS
from .notation.musicxml import write_musicxml
from .sheets.chordsheet import build_chord_sheet
from .sheets.choirsheet import build_choir_sheet
from .sheets.instrument_sheets import build_guitar_sheet, build_piano_sheet

STYLE = Path(__file__).resolve().parent / 'data' / 'stil.mss'


@dataclass
class ExportOptions:
    chordsheet: bool = True
    leadsheet: bool = True
    partitur: bool = True
    parts_pdf: bool = True
    choir_text: bool = True
    guitar: bool = True
    piano: bool = True
    harmony_voices: bool = False
    satb: bool = False
    satb_kinds: tuple = ('chorus', 'choral')
    practice_audio: bool = True
    part_audio: bool = False          # Übe-Spuren aus MuseScore (Melodie/Geige/Cello)
    bitrate: str = '256'
    band: str = ''                    # z. B. 'Band' -> '… - Band-Ablauf.mp3'
    players: list = field(default_factory=lambda: [Player('Violine', 'Violine', 'mittel'),
                                                    Player('Violoncello', 'Violoncello', 'mittel')])
    style: Optional[str] = None


def safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', '-', s).strip() or 'Song'


def short_author(authors: str) -> str:
    """'Anna & Max Muster, …' -> 'AM.Muster'; 'Eva Beispiel' -> 'E.Beispiel'."""
    first = re.split(r'[,/\n;]', authors or '')[0].strip()
    if not first:
        return ''
    words = re.findall(r"[\w'’.-]+", first.replace('&', ' ').replace(' und ', ' '))
    if len(words) == 1 or words[0][:1].islower():
        return first
    last = words[-1]
    initials = ''.join(w[0].upper() for w in words[:-1] if w[0].isalpha())
    return f'{initials}.{last}'


def folder_name(song: Song) -> str:
    a = short_author(song.authors)
    return safe(f'{song.title} - {a}' if a else song.title)


def _score_files(song: Song, lbars, parts, base: Path, style, lead_name='Gesang', log=print) -> dict:
    """MusicXML -> .mscz (mit Kenanja-Stil, exakten Akkordnamen) -> PDF."""
    out = {}
    xml = base.with_suffix('.musicxml')
    write_musicxml(song, lbars, parts, xml, lead_name=lead_name)
    out['musicxml'] = xml
    ms = MS.find_musescore()
    if not ms:
        log('MuseScore nicht gefunden – nur MusicXML erzeugt (lässt sich in MuseScore öffnen).')
        return out
    mscz = MS.convert(xml, base.with_suffix('.mscz'), style=style, ms=ms)
    if mscz:
        if 'lead' in parts:
            syms = [c.symbol for lb in lbars for c in lb.bar.chords]
            MS.patch_harmonies(mscz, syms, staff=parts.index('lead') + 1)
        out['mscz'] = mscz
        pdf = MS.convert(mscz, base.with_suffix('.pdf'), ms=ms)
        if pdf:
            out['pdf'] = pdf
        xml.unlink(missing_ok=True)
        out.pop('musicxml', None)
    return out


def snippet_renderer_factory(tmp: Path, style, log=print):
    def render(song: Song, idx: int):
        ms = MS.find_musescore()
        if not ms:
            return None
        sub = Song(title='', key=song.key, tempo=song.tempo, sections=song.sections,
                   form=[f for f in song.form if f.section == song.sections[idx].id][:1])
        if not sub.form:
            return None
        lb = linearize(sub)
        x = tmp / f'snip{idx}.musicxml'
        write_musicxml(sub, lb, ['lead'], x, lead_name='')
        png = MS.convert(x, tmp / f'snip{idx}.png', style=style, ms=ms)
        if png:
            return MS.trim_png(png)
        return None
    return render


def export_all(song: Song, out_root=None, opts: ExportOptions | None = None,
               log: Callable[[str], None] = print, folder=None) -> dict:
    """Alles erzeugen. Ziel: `folder` direkt (z. B. <Projekt>/Export) oder `out_root`/<Titel (Autoren)>."""
    opts = opts or ExportOptions()
    style = opts.style or (str(STYLE) if STYLE.exists() else None)
    s = song.transposed() if song.transpose else song
    folder = Path(folder) if folder else Path(out_root) / folder_name(song)
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / safe(song.title)
    files: dict[str, list] = {}
    errors = []

    def run(name, fn):
        try:
            log(f'… {name}')
            res = fn()
            if res:
                files[name] = [str(p) for p in (res if isinstance(res, (list, tuple)) else [res]) if p]
        except Exception as e:  # ein Fehler soll nicht alles stoppen
            errors.append(f'{name}: {e}')
            log(f'Fehler bei {name}: {e}')
            traceback.print_exc()

    song.save(folder / f'{safe(song.title)}.kenanja.json')
    tag = f'{base}'
    if opts.chordsheet:
        run('Chord-Sheet', lambda: list(build_chord_sheet(song, f'{tag} - Chordsheet.odt')))
    if opts.choir_text:
        run('Chor-Textblatt', lambda: list(build_choir_sheet(s, f'{tag} - Chortext v{song.version}.odt')))
    if opts.guitar:
        run('Gitarrenblatt', lambda: list(build_guitar_sheet(song, f'{tag} - A-GIT.odt')))
    tmp = Path(tempfile.mkdtemp(prefix='kenanja_'))
    if opts.piano:
        run('Klavierblatt', lambda: list(build_piano_sheet(song, f'{tag} - Klavier.odt',
                                                           snippet_renderer_factory(tmp, style, log))))
    lbars = None
    if opts.leadsheet:
        run('Leadsheet', lambda: list(_score_files(s, linearize(s), ['lead'], Path(f'{tag} - Leadsheet'), style,
                                                   lead_name='Leadsheet', log=log).values()))
    if opts.partitur and opts.players:
        def part():
            lb = arrange_strings(s, opts.players)
            names = [p.name for p in opts.players]
            res = _score_files(s, lb, ['lead'] + names, Path(f'{tag} - Partitur'), style, log=log)
            out = list(res.values())
            if opts.parts_pdf and res.get('mscz'):
                pdfs = MS.export_parts_pdf(res['mscz'], folder, safe(song.title), style=None)
                out += [p for p in pdfs if 'Partitur' not in p.name]
            return out
        run('Partitur und Streicherstimmen', part)
    if opts.harmony_voices:
        def nv():
            lb = linearize(s)
            harmony_voice(s, lb, 'Unterstimme', below=True)
            harmony_voice(s, lb, 'Oberstimme', below=False)
            return list(_score_files(s, lb, ['Oberstimme', 'lead', 'Unterstimme'], Path(f'{tag} - Nebenstimmen'),
                                     style, lead_name='Melodie', log=log).values())
        run('Nebenstimmen', nv)
    if opts.satb:
        def ch():
            lb = linearize(s)
            satb(s, lb, only_kinds=opts.satb_kinds)
            return list(_score_files(s, lb, ['Sopran', 'Alt', 'Tenor', 'Bass'], Path(f'{tag} - Chorsatz SATB'),
                                     style, log=log).values())
        run('Chorsatz', ch)
    if opts.practice_audio and song.audio and Path(song.audio).exists():
        from .audio.practice import render_practice
        name = f'{tag} - {opts.band}-Ablauf.mp3' if opts.band else f'{tag} - Ablauf.mp3'
        run('Übe-Audio', lambda: render_practice(song, name, opts.bitrate, band=opts.band))
    if opts.part_audio and files.get('Partitur und Streicherstimmen'):
        def pa():
            mscz = next((Path(p) for p in files['Partitur und Streicherstimmen'] if p.endswith('.mscz')), None)
            if not mscz:
                return []
            mp3 = MS.convert(mscz, Path(f'{tag} - Partitur.mp3'))
            return [mp3] if mp3 else []
        run('Übe-Spuren', pa)
    shutil.rmtree(tmp, ignore_errors=True)
    summary = {'ordner': str(folder), 'dateien': files, 'fehler': errors}
    (folder / 'kenanja-bericht.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    return summary
