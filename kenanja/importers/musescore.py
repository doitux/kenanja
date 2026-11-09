"""Import von MuseScore-Dateien (.mscz / .mscx, MuseScore 3 und 4) in das Kenanja-Song-Modell.

Damit lassen sich vorhandene Leadsheets/Partituren übernehmen – oder ein in MuseScore korrigiertes
Leadsheet wieder einlesen, um daraus Chord-Sheet, Streicher usw. neu zu erzeugen.
Wiederholungen und Volten werden aufgelöst (Strophe 2 bekommt die 2. Textzeile).
"""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from fractions import Fraction as F
from pathlib import Path

from ..model import Bar, ChordEvent, FormItem, Note, Section, Song
from ..theory import ChordSymbol, tpc_name, key_name, spell_pitch

DUR = {'whole': F(4), 'half': F(2), 'quarter': F(1), 'eighth': F(1, 2), '16th': F(1, 4),
       '32nd': F(1, 8), '64th': F(1, 16), 'breve': F(8)}

SECTION_WORDS = [
    (r'^(intro)\b', 'intro', 'Intro'),
    (r'^(choral|hymn)\b', 'choral', 'Choral'),
    (r'^(pre[- ]?chorus|pre[- ]?refrain)\s*(\d*)', 'prechorus', 'Pre-Chorus'),
    (r'^(pc|pre)\s*(\d*)$', 'prechorus', 'Pre-Chorus'),
    (r'^(verse|vers|strophe)\s*(\d*)', 'verse', 'Verse'),
    (r'^(v)\s*(\d*)$', 'verse', 'Verse'),
    (r'^(chorus|refrain)\s*(\d*)', 'chorus', 'Chorus'),
    (r'^(chr|ch|c)\s*(\d*)$', 'chorus', 'Chorus'),
    (r'^(bridge)\s*(\d*)', 'bridge', 'Bridge'),
    (r'^(br|b)\s*(\d*)$', 'bridge', 'Bridge'),
    (r'^(interlude|zwischenspiel|instrumental)\s*(\d*)', 'interlude', 'Interlude'),
    (r'^(int|i)\s*(\d*)$', 'interlude', 'Interlude'),
    (r'^(solo)\b', 'solo', 'Solo'),
    (r'^(outro|ende|ending|schluss)\b', 'outro', 'Outro'),
    (r'^(tag)\b', 'tag', 'Tag'),
    (r'^(coda)\b', 'outro', 'Coda'),
]
SECTION_ABBR = {'Intro': 'IN', 'Verse': 'V', 'Pre-Chorus': 'PC', 'Chorus': 'C', 'Bridge': 'B',
                'Interlude': 'I', 'Solo': 'S', 'Outro': 'O', 'Tag': 'T', 'Choral': 'CH', 'Coda': 'CO'}


def classify_label(text: str) -> tuple[str, str, str]:
    """'Chr1' -> ('chorus', 'Chorus', '1'); 'Interlude 2' -> ('interlude', 'Interlude', '2')."""
    t = text.strip().lower()
    for rx, kind, nice in SECTION_WORDS:
        m = re.match(rx, t)
        if m:
            num = ''
            if m.lastindex and m.lastindex >= 2:
                num = m.group(2) or ''
            return kind, nice, num
    return 'other', text.strip(), ''


def _text(el) -> str:
    if el is None:
        return ''
    return ''.join(el.itertext()).strip()


def read_mscx(path) -> ET.Element:
    path = Path(path)
    if path.suffix.lower() == '.mscz':
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith('.mscx') and '/' not in n.strip('/'))
            return ET.fromstring(z.read(name))
    return ET.parse(path).getroot()


class _Measure:
    def __init__(self):
        self.len = None
        self.start_repeat = False
        self.end_repeat = 0
        self.voltas = []         # [(endings, n_measures)]
        self.labels = []         # Abschnittsmarken
        self.per_staff = {}      # staff -> dict(notes=[], chords=[], texts=[], ts=(n,d))


def _parse_staff(staff_el, staff_idx, measures, state):
    ts = state.setdefault(('ts', staff_idx), (4, 4))
    for mi, m in enumerate(staff_el.findall('Measure')):
        while len(measures) <= mi:
            measures.append(_Measure())
        M = measures[mi]
        if m.get('len'):
            M.len = F(m.get('len')) * 4
        if m.find('startRepeat') is not None:
            M.start_repeat = True
        if m.find('endRepeat') is not None:
            M.end_repeat = int(m.findtext('endRepeat') or 2)
        data = {'notes': {}, 'chords': [], 'texts': [], 'labels': []}
        data_key = None
        for vi, v in enumerate(m.findall('voice')):
            pos = F(0)
            tup = F(1)
            for el in v:
                tg = el.tag
                if tg == 'TimeSig':
                    ts = (int(el.findtext('sigN')), int(el.findtext('sigD')))
                    state[('ts', staff_idx)] = ts
                elif tg == 'KeySig':
                    k = int(el.findtext('concertKey') or el.findtext('accidental') or '0')
                    if staff_idx == 0 and 'key' not in state:
                        state['key'] = k
                    data_key = k
                elif tg == 'Tempo' and 'tempo' not in state:
                    try:
                        state['tempo'] = float(el.findtext('tempo')) * 60
                    except (TypeError, ValueError):
                        pass
                elif tg == 'location':
                    fr = el.findtext('fractions')
                    if fr:
                        pos += F(fr) * 4
                elif tg == 'Tuplet':
                    tup = F(int(el.findtext('normalNotes')), int(el.findtext('actualNotes')))
                elif tg == 'endTuplet':
                    tup = F(1)
                elif tg == 'Harmony':
                    hi = el.find('harmonyInfo')
                    hi = hi if hi is not None else el
                    root = hi.findtext('root')
                    name = hi.findtext('name') or ''
                    bass = hi.findtext('bass') or hi.findtext('base')
                    if root is None:
                        sym = name or 'N.C.'
                    else:
                        sym = tpc_name(int(root)) + name + ('/' + tpc_name(int(bass)) if bass else '')
                    data['chords'].append((pos, sym))
                elif tg in ('RehearsalMark', 'StaffText', 'SystemText', 'PlayTechAnnotation', 'Expression'):
                    txt = _text(el.find('text'))
                    style = el.findtext('style') or ''
                    if tg == 'RehearsalMark' or style == 'rehearsal_mark' or (
                            tg in ('StaffText', 'SystemText') and classify_label(txt)[0] != 'other'
                            and len(txt) < 25):
                        data['labels'].append(txt)
                    elif txt:
                        data['texts'].append(txt)
                elif tg == 'Spanner' and el.get('type') == 'Volta':
                    vol = el.find('Volta')
                    if vol is not None:
                        endings = [int(x) for x in re.findall(r'\d+', vol.findtext('endings') or vol.findtext('beginText') or '1')]
                        n = el.find('next/location/measures')
                        M.voltas.append((endings, int(n.text) if n is not None else 1))
                elif tg in ('Chord', 'Rest'):
                    dt = el.findtext('durationType')
                    dots = int(el.findtext('dots') or 0)
                    if dt == 'measure':
                        dd = el.findtext('duration')
                        d = F(dd) * 4 if dd else (M.len or F(ts[0] * 4, ts[1]))
                    else:
                        d = DUR.get(dt, F(0))
                        add = d
                        for _ in range(dots):
                            add /= 2
                            d += add
                        d *= tup
                    if tg == 'Chord':
                        grace = any(el.find(g) is not None for g in
                                    ('acciaccatura', 'appoggiatura', 'grace4', 'grace8', 'grace16', 'grace32'))
                        if grace:
                            continue
                        ns = []
                        tie = False
                        for nel in el.findall('Note'):
                            p = int(nel.findtext('pitch'))
                            t = nel.findtext('tpc')
                            ns.append((p, int(t) if t is not None else None))
                            if nel.find("Spanner[@type='Tie']/Tie") is not None or nel.find('Tie') is not None:
                                tie = True
                        ns.sort(key=lambda x: -x[0])
                        lyr = {}
                        for ly in el.findall('Lyrics'):
                            no = int(ly.findtext('no') or 0)
                            txt = (ly.findtext('text') or '').strip()
                            txt = re.sub(r'<[^>]+>', '', txt)
                            if txt:
                                lyr[no] = (txt, ly.findtext('syllabic') or 'single')
                        note = Note(onset=pos, dur=d, pitch=ns[0][0], tpc=ns[0][1], lyrics=lyr, tie=tie,
                                    extra=ns[1:])
                        data['notes'].setdefault(vi, []).append(note)
                    else:
                        data['notes'].setdefault(vi, []).append(Note(onset=pos, dur=d))
                    pos += d
        data['ts'] = ts
        data['key'] = data_key
        M.per_staff[staff_idx] = data
        if data['labels'] and not M.labels:
            M.labels = data['labels']


def _unroll(measures) -> list[tuple[int, int]]:
    """Wiederholungen/Volten auflösen -> [(Taktindex, Durchgang)]"""
    order = []
    volta_of = {}
    for i, M in enumerate(measures):
        for endings, n in M.voltas:
            for k in range(i, min(len(measures), i + max(1, n))):
                volta_of[k] = (endings, i, i + max(1, n))
    i, rep_start, passno, jumped = 0, 0, 1, False
    guard = 0
    while i < len(measures) and guard < 10000:
        guard += 1
        M = measures[i]
        if M.start_repeat and not jumped:
            rep_start, passno = i, 1
        jumped = False
        if i in volta_of:
            endings, vs, ve = volta_of[i]
            if passno not in endings:
                i = ve
                continue
        order.append((i, passno))
        if M.end_repeat:
            if passno < M.end_repeat:
                passno += 1
                i = rep_start
                jumped = True
                continue
            passno = 1
        i += 1
    return order


def import_musescore(path, lead_staff: int | None = None) -> Song:
    root = read_mscx(path)
    score = root.find('Score')
    parts = []
    for p in score.findall('Part'):
        inst = p.find('Instrument')
        nm = p.findtext('trackName') or (inst.findtext('longName') if inst is not None else '') or '?'
        n_staves = len(p.findall('Staff')) or 1
        parts.extend([nm] * n_staves)
    staves = score.findall('Staff')
    measures, state = [], {}
    for si, st in enumerate(staves):
        _parse_staff(st, si, measures, state)

    # Leadstimme = Notenzeile mit dem meisten Text
    def lyr_count(si):
        return sum(len(n.lyrics) for M in measures for v in M.per_staff.get(si, {}).get('notes', {}).values() for n in v)
    if lead_staff is None:
        lead_staff = max(range(len(staves)), key=lyr_count)

    # Titel etc.
    meta = {}
    for vb in score.iter('VBox'):
        for t in vb.iter('Text'):
            meta.setdefault(t.findtext('style') or '', _text(t.find('text')))
    title = meta.get('title', '') or (score.findtext("metaTag[@name='workTitle']") or Path(path).stem)
    authors = ' / '.join(x for x in [meta.get('composer', ''), meta.get('poet', ''), meta.get('lyricist', '')] if x)

    key_sharps = state.get('key', 0)
    major_tpc = 14 + key_sharps

    order = _unroll(measures)
    if title.isupper():
        small = {'und', 'der', 'die', 'das', 'ein', 'eine', 'zu', 'im', 'in', 'wie', 'of', 'the', 'and', 'to', 'a', 'my'}
        title = ' '.join(w.lower() if (k and w.lower() in small) else w.capitalize() for k, w in enumerate(title.split()))
    song = Song(title=title, subtitle=meta.get('subtitle', ''),
                authors=authors, key=key_name(major_tpc), tempo=round(state.get('tempo', 80)))
    sections: list[Section] = []
    label_counts: dict = {}
    cur = None
    last_chord = None
    prev_mi = None
    for mi, passno in order:
        M = measures[mi]
        jumped_back = prev_mi is not None and mi <= prev_mi
        prev_mi = mi
        if M.labels or cur is None:
            raw = M.labels[0] if M.labels else 'Intro'
            kind, nice, num = classify_label(raw)
            if cur is not None and jumped_back and cur.notes_label == nice and cur.start_measure == mi:
                pass  # direkte Wiederholung (z. B. Bridge 2x) -> gleicher Abschnitt
            else:
                label_counts[nice] = label_counts.get(nice, 0) + 1
                n = num or str(label_counts[nice])
                sid = SECTION_ABBR.get(nice, re.sub(r'\W+', '', nice[:3]).upper()) + n
                while any(s.id == sid for s in sections):
                    sid += 'b'
                cur = Section(id=sid, label=f'{nice} {n}', kind=kind, verse=passno - 1)
                cur.notes_label, cur.start_measure, cur.explicit_num = nice, mi, bool(num)
                sections.append(cur)
        lead = M.per_staff.get(lead_staff, {})
        ts = lead.get('ts', (4, 4))
        bar = Bar(num=ts[0], den=ts[1])
        if M.len:
            fr = M.len / 4
            bar.num, bar.den = fr.numerator, fr.denominator
        # Akkorde aus allen Zeilen (meist im Leadsheet)
        seen = set()
        for si in sorted(M.per_staff):
            for pos, sym in M.per_staff[si]['chords']:
                if pos not in seen:
                    bar.chords.append(ChordEvent(pos, sym))
                    seen.add(pos)
        bar.chords.sort(key=lambda c: c.onset)
        notes = lead.get('notes', {})
        mel = notes.get(0, [])
        verse = passno - 1
        bar.melody = []
        for n in mel:
            nn = Note(n.onset, n.dur, n.pitch, n.tpc, {}, n.tie, list(n.extra))
            if n.lyrics:
                lv = n.lyrics.get(verse) or n.lyrics.get(0)
                if lv:
                    nn.lyrics = {0: lv}
            bar.melody.append(nn)
        bar.texts = list(lead.get('texts', []))
        if lead.get('key') is not None and mi > 0:
            bar.key = lead['key']
        for si, d in M.per_staff.items():
            if si == lead_staff:
                continue
            name = parts[si] if si < len(parts) else f'Stimme {si + 1}'
            if name in bar.parts:
                name = f'{name} {si + 1}'
            voices = d.get('notes', {})
            pnotes = [Note(n.onset, n.dur, n.pitch, n.tpc, {}, n.tie, list(n.extra)) for n in voices.get(0, [])]
            # zweite Stimme (divisi) als Zusatztöne mitnehmen
            for n2 in voices.get(1, []):
                if n2.pitch is None:
                    continue
                for n in pnotes:
                    if n.onset == n2.onset and n.pitch is not None:
                        n.extra.append((n2.pitch, n2.tpc))
                        break
            bar.parts[name] = pnotes
            if d.get('texts'):
                bar.texts += [f'{name}: {t}' for t in d['texts']]
        cur.bars.append(bar)
    # Nummern nur, wenn ein Abschnittstyp mehrfach vorkommt (wie auf üblichen Liedblättern)
    from collections import Counter
    cnt = Counter(s.notes_label for s in sections)
    for s in sections:
        if cnt[s.notes_label] == 1 and not s.explicit_num:
            s.label = s.notes_label
        for a in ('notes_label', 'start_measure', 'explicit_num'):
            delattr(s, a)
    song.sections = sections
    from ..lyrics import assign_pickups, auto_lines
    assign_pickups(sections)
    for i in range(len(sections)):
        auto_lines(sections, i)
    # Moll erkennen: letzter Akkord = Moll-Parallele?
    last = next((c.symbol for s in reversed(sections) for b in reversed(s.bars) for c in reversed(b.chords)), None)
    if last:
        try:
            ch = ChordSymbol.parse(last)
            if ch.minor and ch.root == major_tpc + 3:
                song.key = key_name(major_tpc, True)
        except ValueError:
            pass
    song.default_form()
    song.lineup = sorted({p for i, p in enumerate(parts) if i != lead_staff})
    song.notes['source'] = str(path)
    return song
