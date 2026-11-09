"""Notenausgabe als MusicXML (Leadsheet, Partitur mit Streichern, Chorsatz, Einzelstimmen).

Die Noten werden mit music21 geschrieben (saubere Notenwerte, Haltebögen, Balken); die
Akkordsymbole werden danach exakt so eingefügt, wie sie im Song stehen (Gsus24, C2, Am7/G …).
MuseScore liest die Datei ein und speichert sie als .mscz bzw. erzeugt PDFs (siehe musescore.py).
"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from fractions import Fraction as F
from pathlib import Path
from typing import Optional

from ..arranger.strings import INSTRUMENTS, LBar, instrument_of, linearize
from ..model import Note, Song
from ..theory import ChordSymbol, tpc_name, tpc_step_alter

PART_DEFS = {
    'Gesang': ('treble', 'Voice', 'voice.vocals'),
    'Leadsheet': ('treble', 'Voice', 'voice.vocals'),
    'Violine': ('treble', 'Violin', 'strings.violin'),
    'Bratsche': ('alto', 'Viola', 'strings.viola'),
    'Violoncello': ('bass', 'Violoncello', 'strings.cello'),
    'Sopran': ('treble', 'Soprano', 'voice.soprano'),
    'Alt': ('treble', 'Alto', 'voice.alto'),
    'Tenor': ('treble8vb', 'Tenor', 'voice.tenor'),
    'Bass': ('bass', 'Bass', 'voice.bass'),
    'Klavier': ('treble', 'Piano', 'keyboard.piano'),
}


VOCAL_PARTS = {'Sopran', 'Alt', 'Tenor', 'Bass', 'Unterstimme', 'Oberstimme', 'Melodie'}


def _clef_for(name: str):
    from music21 import clef
    if name in PART_DEFS:
        c = PART_DEFS[name][0]
    else:
        inst = instrument_of(name)
        c = INSTRUMENTS[inst]['clef'] if inst in INSTRUMENTS else 'treble'
        if 'tenor' in name.lower():
            c = 'treble8vb'
        if 'bass' in name.lower() and 'violin' not in name.lower():
            c = 'bass'
    return {'treble': clef.TrebleClef(), 'alto': clef.AltoClef(), 'bass': clef.BassClef(),
            'treble8vb': clef.Treble8vbClef()}[c]


def _instrument_for(name: str):
    from music21 import instrument
    n = name.lower()
    if 'violon' in n or 'cello' in n:
        i = instrument.Violoncello()
    elif 'bratsche' in n or 'viola' in n:
        i = instrument.Viola()
    elif 'violin' in n or 'geige' in n:
        i = instrument.Violin()
    elif 'sopran' in n:
        i = instrument.Soprano()
    elif n.startswith('alt'):
        i = instrument.Alto()
    elif 'tenor' in n:
        i = instrument.Tenor()
    elif n.startswith('bass'):
        i = instrument.Bass()
    elif 'klavier' in n or 'piano' in n:
        i = instrument.Piano()
    else:
        i = instrument.Vocalist()
    i.partName = name
    i.partAbbreviation = {'Violine': 'Vl.', 'Violoncello': 'Vc.', 'Bratsche': 'Va.', 'Gesang': 'Ges.',
                          'Leadsheet': ''}.get(name, name[:3] + '.')
    return i


def _pitch(n_pitch: int, tpc: Optional[int]):
    from music21 import pitch
    if tpc is None:
        from ..theory import spell_pitch
        tpc = spell_pitch(n_pitch)
    step, alter = tpc_step_alter(tpc)
    from ..theory import STEP_PC
    octave = (n_pitch - alter - STEP_PC[step]) // 12 - 1
    p = pitch.Pitch(step + {0: '', 1: '#', 2: '##', -1: '-', -2: '--'}[alter] + str(octave))
    return p


def _m21_note(n: Note, lyric_verse: int = 0, with_lyrics=True):
    from music21 import chord, duration, note, tie
    d = duration.Duration(float(n.dur))
    if n.pitch is None:
        r = note.Rest()
        r.duration = d
        return r
    if n.extra:
        ps = [_pitch(n.pitch, n.tpc)] + [_pitch(p, t) for p, t in n.extra]
        el = chord.Chord(ps)
    else:
        el = note.Note(_pitch(n.pitch, n.tpc))
    el.duration = d
    if with_lyrics and n.lyrics:
        syl = n.lyrics.get(lyric_verse) or n.lyrics.get(0)
        if syl:
            from music21 import note as m21note
            ly = m21note.Lyric(text=syl[0], syllabic=syl[1] if syl[1] in ('single', 'begin', 'middle', 'end') else 'single')
            el.lyrics = [ly]
    return el


def _ts_str(bar) -> str:
    return f'{bar.num}/{bar.den}'


def build_score(song: Song, lbars: list[LBar], parts: list[str], lead_name: str = 'Gesang',
                title_suffix: str = '', include_chords_in: tuple = ('lead',)) -> 'object':
    """Erzeugt eine music21-Partitur. parts: Liste der Stimmen; 'lead' = Melodie mit Text & Akkorden."""
    from music21 import bar as m21bar, dynamics, expressions, key, layout, metadata, meter, note, stream, tempo, tie
    sc = stream.Score()
    md = metadata.Metadata()
    md.title = song.title
    if song.subtitle:
        md.movementName = song.subtitle
    if song.authors:
        md.composer = song.authors.replace('\n', ' ')
    sc.metadata = md
    key_tpc, minor = song.key_tpc()
    for pname in parts:
        is_lead = pname == 'lead'
        name = lead_name if is_lead else pname
        part = stream.Part()
        part.id = re.sub(r'\W', '', name) or 'P'
        part.partName = name
        part.insert(0, _instrument_for(name))
        cur_ts = None
        cur_key = None
        pending_tie_stop = False
        for i, lb in enumerate(lbars):
            m = stream.Measure(number=i + 1)
            if i == 0:
                m.insert(0, _clef_for(name if not is_lead else 'Gesang'))
            ts = _ts_str(lb.bar)
            if ts != cur_ts:
                m.timeSignature = meter.TimeSignature(ts)
                cur_ts = ts
            ks = getattr(lb.bar, 'key', None)
            sharps = ks if ks is not None else (cur_key if cur_key is not None else key_tpc - 14)
            if sharps != cur_key:
                m.keySignature = key.KeySignature(int(sharps))
                cur_key = sharps
            if i == 0 and is_lead:
                mm = tempo.MetronomeMark(number=round(song.tempo), referent=note.Note(type='quarter'))
                m.insert(0, mm)
            if lb.first:
                label = lb.item.label or lb.section.label
                if pname == parts[0]:
                    rm = expressions.RehearsalMark(label)
                    m.insert(0, rm)
            # Inhalte
            if is_lead:
                notes = lb.bar.melody
            else:
                notes = lb.parts.get(pname, [])
            for onset, kind, text in lb.marks.get(pname if not is_lead else '__lead__', []):
                if kind == 'dyn':
                    m.insert(float(onset), dynamics.Dynamic(text))
                else:
                    te = expressions.TextExpression(text)
                    m.insert(float(onset), te)
            if not is_lead and lb.first and lb.item.note and pname in (lb.item.strings or {}) and False:
                pass
            filled = F(0)
            L = lb.bar.length
            seq = sorted([n for n in notes], key=lambda n: n.onset)
            for n in seq:
                if n.onset > filled:
                    r = note.Rest()
                    r.duration.quarterLength = float(n.onset - filled)
                    m.insert(float(filled), r)
                    filled = n.onset
                if n.onset < filled:
                    continue
                dur = min(n.dur, L - n.onset)
                if dur <= 0:
                    continue
                nn = copy.copy(n)
                nn.dur = dur
                el = _m21_note(nn, 0, with_lyrics=is_lead or pname in VOCAL_PARTS)
                if n.pitch is not None:
                    if pending_tie_stop and n.onset == 0:
                        el.tie = tie.Tie('stop' if not n.tie else 'continue')
                    elif n.tie:
                        el.tie = tie.Tie('start')
                    pending_tie_stop = bool(n.tie)
                else:
                    pending_tie_stop = False
                m.insert(float(n.onset), el)
                filled = n.onset + dur
            if filled < L:
                r = note.Rest()
                r.duration.quarterLength = float(L - filled)
                if filled == 0:
                    r.fullMeasure = True
                m.insert(float(filled), r)
            if lb.last and i + 1 < len(lbars):
                m.rightBarline = m21bar.Barline('double')
            if i == len(lbars) - 1:
                m.rightBarline = m21bar.Barline('final')
            part.append(m)
        sc.insert(0, part)
    return sc


def _harmony_el(symbol: str) -> Optional[ET.Element]:
    try:
        ch = ChordSymbol.parse(symbol)
    except ValueError:
        return None
    h = ET.Element('harmony', {'print-frame': 'no'})
    if ch.is_nc:
        r = ET.SubElement(h, 'root')
        ET.SubElement(r, 'root-step').text = 'C'
        k = ET.SubElement(h, 'kind', {'text': 'N.C.'})
        k.text = 'none'
        return h
    step, alter = tpc_step_alter(ch.root)
    r = ET.SubElement(h, 'root')
    ET.SubElement(r, 'root-step').text = step
    if alter:
        ET.SubElement(r, 'root-alter').text = str(alter)
    k = ET.SubElement(h, 'kind', {'text': ch.quality})
    k.text = _xml_kind(ch.quality)
    if ch.bass is not None:
        bstep, balter = tpc_step_alter(ch.bass)
        b = ET.SubElement(h, 'bass')
        ET.SubElement(b, 'bass-step').text = bstep
        if balter:
            ET.SubElement(b, 'bass-alter').text = str(balter)
    return h


def _xml_kind(q: str) -> str:
    table = {'': 'major', 'm': 'minor', '7': 'dominant', 'm7': 'minor-seventh', 'maj7': 'major-seventh',
             'sus4': 'suspended-fourth', 'sus': 'suspended-fourth', 'sus2': 'suspended-second', '6': 'major-sixth',
             'm6': 'minor-sixth', 'dim': 'diminished', 'aug': 'augmented', '9': 'dominant-ninth',
             'add9': 'major', '2': 'major', 'm9': 'minor-ninth', 'maj9': 'major-ninth', '7sus4': 'dominant',
             'm7b5': 'half-diminished', 'dim7': 'diminished-seventh'}
    return table.get(q, 'major' if not q.startswith('m') or q.startswith('maj') else 'minor')


def insert_harmonies(xml_path, lbars: list[LBar], part_index: int = 0) -> None:
    """Fügt die Akkordsymbole in die erste Stimme (Leadsheet) ein – exakt in der Schreibweise des Chord-Sheets."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    parts = root.findall('part')
    if not parts:
        return
    part = parts[part_index]
    divisions = 1
    for mi, meas in enumerate(part.findall('measure')):
        attr = meas.find('attributes')
        if attr is not None and attr.find('divisions') is not None:
            divisions = int(attr.find('divisions').text)
        if mi >= len(lbars):
            break
        chords = lbars[mi].bar.chords
        if not chords:
            continue
        # Positionen der Noten (Stimme 1) bestimmen
        pos = F(0)
        slots = []  # (onset, index im measure)
        children = list(meas)
        for ci, el in enumerate(children):
            if el.tag == 'note':
                if el.find('chord') is not None:
                    continue
                v = el.findtext('voice') or '1'
                if v != '1':
                    continue
                d = int(el.findtext('duration') or 0)
                slots.append((pos, ci))
                pos += F(d, divisions)
            elif el.tag == 'backup':
                pos -= F(int(el.findtext('duration') or 0), divisions)
            elif el.tag == 'forward':
                pos += F(int(el.findtext('duration') or 0), divisions)
        inserts = []
        for c in chords:
            h = _harmony_el(c.symbol)
            if h is None:
                continue
            target = None
            for on, ci in slots:
                if on <= c.onset:
                    target = (on, ci)
            if target is None:
                target = slots[0] if slots else (F(0), len(children))
            on, ci = target
            off = c.onset - on
            if off:
                ET.SubElement(h, 'offset').text = str(int(off * divisions))
            inserts.append((ci, h))
        for ci, h in sorted(inserts, key=lambda x: -x[0]):
            meas.insert(ci, h)
    ET.indent(tree)
    tree.write(xml_path, encoding='UTF-8', xml_declaration=True)


def write_musicxml(song: Song, lbars: list[LBar], parts: list[str], path, lead_name: str = 'Gesang') -> Path:
    path = Path(path)
    sc = build_score(song, lbars, parts, lead_name=lead_name)
    sc.write('musicxml', fp=str(path), makeNotation=True)
    if 'lead' in parts:
        insert_harmonies(path, lbars, parts.index('lead'))
    _fix_credits(path, song)
    return path


def _fix_credits(path, song: Song):
    """Titel/Untertitel/Autoren sauber setzen (music21 schreibt teils Platzhalter)."""
    tree = ET.parse(path)
    root = tree.getroot()
    for c in root.findall('credit'):
        root.remove(c)
    ident = root.find('identification')
    if ident is not None:
        for cr in ident.findall('creator'):
            if (cr.text or '').strip() in ('Music21', 'Music21 Fragment'):
                cr.text = ''
    work = root.find('work')
    if work is not None and work.find('work-title') is not None:
        work.find('work-title').text = song.title
    mt = root.find('movement-title')
    if mt is not None:
        if song.subtitle:
            mt.text = song.subtitle
        else:
            root.remove(mt)
    tree.write(path, encoding='UTF-8', xml_declaration=True)
