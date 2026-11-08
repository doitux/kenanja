"""Instrumenten-Blätter:
  * Akustikgitarre (Stil der A-GIT-Blätter): Teile, in denen die Gitarre nicht spielt, nur grau
    und kompakt; vereinfachte Akkorde in Courier; Griffbilder für seltenere Akkorde am Ende.
  * Klavier: Chord-Sheet mit eingefügten Noten (Intro/Zwischenspiele) als Bild.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from ..model import Song, lineup_states, plays
from ..theory import ChordSymbol
from . import chordsheet as CS
from .guitar_shapes import draw_diagram, needs_diagram, shape_for
from .odt import OdtDoc, to_pdf

GIT_BLUE = '#215e99'
GREY = '#7f7f7f'


def _authors_line(song: Song) -> str:
    parts = [a.strip().rstrip(',') for a in song.authors.replace(' / ', '\n').replace(';', '\n').splitlines() if a.strip()]
    return ' | '.join(parts)


def build_guitar_sheet(song: Song, out_odt, instrument: str = 'AGit', pdf: bool = True,
                       diagrams: bool = True, capo: int = 0):
    s = song.transposed() if song.transpose else song
    if capo:
        # Griffe relativ zum Kapodaster
        s = s.transposed(-capo)
    st = CS.SheetStyle(chord_color=GIT_BLUE, chord_kind='mono-bold', chord_font='Courier New', simplify=True,
                       show_cues=False)
    doc = OdtDoc(font='Arial', size=14, margins_cm=(1.5, 1.5, 1.5, 1.5))
    doc.paragraph(align='center').text(s.title, bold=True)
    if s.authors:
        doc.paragraph().text(_authors_line(s), italic=True, size=10)
    if capo:
        doc.paragraph().text(f'Kapo {capo}. Bund (Griffe in {s.target_key()})', bold=True, color='#c00000', size=11)
    doc.paragraph(size=8)
    states = lineup_states(s)
    used = []
    for k, (item, sec) in enumerate(s.ordered_form()):
        idx = s.sections.index(sec)
        playing = plays(states[k], instrument) if k < len(states) else True
        label = item.label or sec.label
        if not playing:
            groups = CS.L.line_groups(s.sections, idx)
            lines = [g[0] for g in groups]
            p = doc.paragraph(tabs=[(3.0, 'left')], keep_next=bool(lines[1:]), space_before=4)
            p.text(label, bold=True, color=GREY, size=12)
            if lines:
                p.tab().text(lines[0], color=GREY, size=11)
            for j, line in enumerate(lines[1:]):
                doc.paragraph(indent_cm=3.0, keep_next=j < len(lines) - 2).text(line, color=GREY, size=11)
            continue
        doc.paragraph(keep_next=True, space_before=6).text(label, bold=True)
        laid = CS.section_lines(s, idx, st)
        if laid:
            for j, ll in enumerate(laid):
                if ll.chords:
                    CS.add_positioned(doc, ll.chords, st, keep_next=True)
                    used += [c for _, c in ll.chords]
                doc.paragraph(keep_next=j < len(laid) - 1).text(ll.text or ' ')
        else:
            rows = CS.bar_lines(sec, st, doc.text_width, CS.L.chord_before(s.sections, idx))
            for j, row in enumerate(rows):
                CS.add_positioned(doc, row, st, keep_next=j < len(rows) - 1)
                used += [c for _, c in row if c != '|']
    if diagrams:
        seen = []
        for c in used:
            try:
                sym = ChordSymbol.parse(c).simplified_for_guitar().text()
            except ValueError:
                continue
            if sym not in seen and needs_diagram(sym) and shape_for(sym):
                seen.append(sym)
        if seen:
            tmp = Path(tempfile.mkdtemp(prefix='kenanja_griffe_'))
            doc.paragraph(size=10)
            p = doc.paragraph(keep_together=True)
            for i, sym in enumerate(seen):
                png = draw_diagram(_uni(sym), shape_for(sym), tmp / f'g{i}.png')
                p.image(png, 1.7, 3.5)
                p.text('  ')
    path = doc.save(out_odt)
    return path, (to_pdf(path) if pdf else None)


def _uni(s):
    from ..theory import _uni as u
    q = s
    return u(q)


def build_piano_sheet(song: Song, out_odt, snippet_renderer=None, pdf: bool = True,
                      snippet_kinds=('intro', 'interlude', 'solo', 'outro')):
    """Chord-Sheet für Klavier; für Intro/Zwischenspiele werden Notenbilder eingefügt,
    wenn ein snippet_renderer(song, section_index) -> PNG-Pfad übergeben wird."""
    st = CS.SheetStyle()
    s = song.transposed() if song.transpose else song
    doc = OdtDoc(font='Arial', size=14, margins_cm=st.margins)
    CS.add_title(doc, s, st)
    done = set()
    for item, sec in s.ordered_form():
        idx = s.sections.index(sec)
        if snippet_renderer and sec.kind in snippet_kinds and sec.id not in done:
            CS.add_section_header(doc, item, sec, st)
            png = None
            try:
                png = snippet_renderer(s, idx)
            except Exception as e:  # Notenbild optional
                png = None
            if png:
                from PIL import Image
                w, h = Image.open(png).size
                width = min(doc.text_width, 18.0)
                doc.paragraph(keep_together=True).image(png, width, width * h / w)
                done.add(sec.id)
                doc.paragraph(size=7)
                continue
            rows = CS.bar_lines(sec, st, doc.text_width, CS.L.chord_before(s.sections, idx))
            for j, row in enumerate(rows):
                CS.add_positioned(doc, row, st, keep_next=j < len(rows) - 1)
            doc.paragraph(size=7)
            continue
        CS.write_section(doc, s, idx, item, st)
    path = doc.save(out_odt)
    return path, (to_pdf(path) if pdf else None)
