"""Chord-Sheet: Titel mittig, Autoren rechts kursiv, Abschnitte fett mit
Besetzungswechseln (+grün / −rot), Akkorde blau über der Silbe (Erweiterungen hochgestellt),
Instrumentalteile als Taktzeilen. Akkorde werden exakt per Tabstopp auf die Silbe gesetzt;
wo es eng wird, werden Leerzeichen (bzw. Silbentrennstriche) in den Text eingefügt.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction as F
from typing import Optional

from .. import lyrics as L
from ..model import FormItem, Section, Song
from ..theory import ChordSymbol
from .odt import OdtDoc, text_width_cm, to_pdf

BLUE = '#0a5394'
GREEN = '#006400'
RED = '#ff0000'
GREY = '#7f7f7f'


@dataclass
class SheetStyle:
    size: float = 14                 # Schriftgröße Text
    chord_size: float = 14           # Schriftgröße Akkorde
    chord_bold: bool = True
    chord_color: str = BLUE
    chord_kind: str = 'sans-bold'    # Metrik für Akkordbreite
    chord_font: Optional[str] = None  # None = Dokumentschrift (Arial)
    gap_cm: float = 0.25             # Mindestabstand zwischen zwei Akkorden
    bars_per_line: int = 4
    simplify: bool = False           # Gitarren-Vereinfachung
    show_cues: bool = True
    margins: tuple = (1.5, 1.5, 1.5, 1.5)


def chord_parts(symbol: str, simplify=False) -> list[tuple[str, bool]]:
    try:
        ch = ChordSymbol.parse(symbol)
        if simplify:
            ch = ch.simplified_for_guitar()
        return ch.parts_for_sheet()
    except ValueError:
        return [(symbol, False)]


def chord_width(symbol: str, st: SheetStyle) -> float:
    w = 0.0
    for t, sup in chord_parts(symbol, st.simplify):
        w += text_width_cm(t, st.chord_size * (0.58 if sup else 1), st.chord_kind)
    return w


# ------------------------------------------------------------------ Zeilen-Layout ----

@dataclass
class LaidLine:
    text: str
    chords: list[tuple[float, str]] = field(default_factory=list)   # (x in cm, Symbol)


def _sp(st):
    return text_width_cm(' ', st.size)


def layout_line(text: str, syl_pos: list[tuple[int, L.Syl]], chords: list[L.ChordAt],
                st: SheetStyle) -> LaidLine:
    """Setzt Akkorde über die Zeile. syl_pos: (Zeichenindex, Silbe)."""
    anchors = []  # (char_pos, kind, symbol)  kind: 'at' | 'gap'
    lead_chords = []
    for c in chords:
        k = None
        for j, (_, s) in enumerate(syl_pos):
            if s.t <= c.t + F(1, 8):
                k = j
        if k is None:
            lead_chords.append(c)
            continue
        cpos, s = syl_pos[k]
        if abs(c.t - s.t) <= F(1, 4):
            anchors.append((cpos, 'at', c.symbol))
            continue
        nxt = syl_pos[k + 1] if k + 1 < len(syl_pos) else None
        if nxt is not None and nxt[1].t - c.t <= F(1, 2):
            anchors.append((nxt[0], 'at', c.symbol))
            continue
        # Akkordwechsel während einer gehaltenen Silbe / in einer Pause: Lücke nach der Silbe
        end = cpos + len(s.text.rstrip('-'))
        # bis zum Wortende gehen, wenn die Silbe ein Wortende ist
        anchors.append((min(end, len(text)), 'gap', c.symbol))

    S = text
    offset = 0
    placed: list[tuple[float, str]] = []
    # Akkorde vor der ersten Silbe: am Zeilenanfang, Text einrücken
    x = 0.0
    for c in lead_chords:
        placed.append((x, c.symbol))
        x += chord_width(c.symbol, st) + st.gap_cm
    if lead_chords:
        first_char = syl_pos[0][0] if syl_pos else 0
        need = x - text_width_cm(S[:first_char], st.size)
        if need > 0:
            n = math.ceil(need / _sp(st))
            S = ' ' * n + S
            offset += n

    anchors.sort(key=lambda a: (a[0], 0 if a[1] == 'at' else 1))
    for pos, kind, sym in anchors:
        p = pos + offset
        if kind == 'gap':
            if p >= len(S):
                S = S + ' '
                p = len(S)
            elif S[p:p + 1] == ' ':
                p += 1
            else:
                # mitten im Wort: Trennstrich einfügen
                S = S[:p] + ' - ' + S[p:]
                offset += 3
                p += 3
            if p > len(S):
                S += ' ' * (p - len(S))
        xa = text_width_cm(S[:p], st.size)
        x_min = (placed[-1][0] + chord_width(placed[-1][1], st) + st.gap_cm) if placed else 0.0
        if xa < x_min - 1e-6:
            need = x_min - xa
            n = math.ceil(need / _sp(st))
            before = S[p - 1:p]
            if before.isalpha() and kind == 'at':
                ins = ' ' * max(1, n - 2) + '- '
                n = len(ins)
            else:
                ins = ' ' * n
            S = S[:p] + ins + S[p:]
            offset += len(ins)
            p += len(ins)
            xa = text_width_cm(S[:p], st.size)
        if kind == 'gap' and p >= len(S.rstrip()) and p > len(S):
            S += ' '
        placed.append((xa, sym))
    return LaidLine(S.rstrip(), placed)


def section_lines(song: Song, sec_index: int, st: SheetStyle) -> list[LaidLine]:
    secs = song.sections
    groups = [g for g in L.line_groups(secs, sec_index) if g[1]]
    if not groups:
        return []
    chs = L.chords(secs, sec_index)
    first_t = groups[0][1][0][1].t
    # Abschnitt ohne Akkord am Anfang: klingenden Akkord übernehmen
    if not chs or chs[0].t > first_t + F(1):
        prev = L.chord_before(secs, sec_index)
        if prev:
            chs = [L.ChordAt(min(F(0), first_t), prev)] + chs
    # Grenzen zwischen den Zeilen
    bounds = []
    for j in range(1, len(groups)):
        prev_last = groups[j - 1][1][-1][1]
        t_b = groups[j][1][0][1].t
        bounds.append(max(t_b - F(1), prev_last.t + F(5, 16)))
    out = []
    for j, (text, sp) in enumerate(groups):
        lo = bounds[j - 1] if j > 0 else F(-1000)
        hi = bounds[j] if j < len(bounds) else F(100000)
        mine = [c for c in chs if lo <= c.t < hi]
        out.append(layout_line(text, sp, mine, st))
    return out


def bar_lines(sec: Section, st: SheetStyle, text_width: float, start_chord: Optional[str] = None) -> list[list[tuple[float, str]]]:
    """Instrumentalteil als Taktzeilen: [[(x, text), ...], ...] – Text '|' oder Akkord."""
    rows = []
    bars = sec.bars
    n = len(bars)
    per = st.bars_per_line
    sizes = []
    rest = n
    while rest > 0:
        if rest == per + 1:
            sizes.append(per + 1)
            break
        sizes.append(min(per, rest))
        rest -= sizes[-1]
    i = 0
    carried = start_chord
    for size in sizes:
        W = text_width / max(per, size)
        row = []
        chunk = bars[i:i + size]
        i += size
        for bi, bar in enumerate(chunk):
            x0 = bi * W
            row.append((x0, '|'))
            x_min = x0 + text_width_cm('| ', st.chord_size, st.chord_kind)
            chords = bar.chords
            if not chords and carried:
                from ..model import ChordEvent
                chords = [ChordEvent(F(0), carried)]
            if not chords:
                continue
            carried = chords[-1].symbol
            for c in chords:
                x = x0 + 0.45 + float(c.onset / bar.length) * (W - 0.6)
                x = max(x, x_min)
                row.append((x, c.symbol))
                x_min = x + chord_width(c.symbol, st) + st.gap_cm
        row.append((min(len(chunk) * W, text_width - 0.35), '|'))
        rows.append(row)
    return rows


# ------------------------------------------------------------------ Dokument ----

def _chord_spans(p, symbol, st: SheetStyle):
    if symbol == '|':
        p.text('|', bold=st.chord_bold, color=st.chord_color, size=st.chord_size, font=st.chord_font)
        return
    for t, sup in chord_parts(symbol, st.simplify):
        p.text(t, bold=st.chord_bold, color=st.chord_color, sup=sup, size=st.chord_size, font=st.chord_font)


def add_positioned(doc: OdtDoc, items: list[tuple[float, str]], st: SheetStyle, **pprops):
    tabs = [(x, 'left') for x, _ in items if x > 0.001]
    p = doc.paragraph(tabs=tabs, **pprops)
    for x, sym in items:
        if x > 0.001:
            p.tab()
        _chord_spans(p, sym, st)
    return p


def add_title(doc: OdtDoc, song: Song, st: SheetStyle, version: bool = False):
    authors = [a.strip() for a in song.authors.replace(' / ', '\n').replace(';', '\n').splitlines() if a.strip()]
    tw = doc.text_width
    p = doc.paragraph(tabs=[(tw / 2, 'center'), (tw, 'right')], keep_next=True)
    p.tab().text(song.title, bold=True)
    if authors:
        p.tab().text(authors[0], italic=True, size=10)
    if version or song.subtitle:
        p2 = doc.paragraph(tabs=[(tw / 2, 'center'), (tw, 'right')], keep_next=True)
        p2.tab().text(f'v.{song.version}' if version else song.subtitle, size=10)
        if len(authors) > 1:
            p2.tab().text(authors[1], italic=True, size=10)
        rest = authors[2:]
    else:
        rest = authors[1:]
    for a in rest:
        doc.paragraph(align='right', keep_next=True).text(a, italic=True, size=10)
    doc.paragraph(size=8)


def add_section_header(doc: OdtDoc, item: FormItem, sec: Section, st: SheetStyle, extra: str = ''):
    p = doc.paragraph(keep_next=True, space_before=2)
    p.text((item.label or sec.label) + ':', bold=True)
    if st.show_cues:
        for a in item.add:
            p.text(' +' + a, bold=True, color=GREEN)
        for r in item.remove:
            p.text(' -' + r, bold=True, color=RED)
        if item.note:
            p.text(' ' + item.note, bold=True, color=GREEN)
    if extra:
        p.text(' ' + extra, bold=True, color=GREEN)
    return p


def write_section(doc: OdtDoc, song: Song, sec_index: int, item: FormItem, st: SheetStyle):
    sec = song.sections[sec_index]
    add_section_header(doc, item, sec, st)
    laid = section_lines(song, sec_index, st)
    if laid:
        for k, ll in enumerate(laid):
            last = k == len(laid) - 1
            if ll.chords:
                add_positioned(doc, ll.chords, st, keep_next=True)
            doc.paragraph(keep_next=not last).text(ll.text or ' ')
    else:
        rows = bar_lines(sec, st, doc.text_width, L.chord_before(song.sections, sec_index))
        for k, row in enumerate(rows):
            add_positioned(doc, row, st, keep_next=k < len(rows) - 1)
    doc.paragraph(size=7)


def build_chord_sheet(song: Song, out_odt, st: SheetStyle | None = None, pdf: bool = True):
    """Erzeugt das Chord-Sheet (ODT, optional PDF) für den Song in seiner Zieltonart."""
    st = st or SheetStyle()
    s = song.transposed() if song.transpose else song
    doc = OdtDoc(font='Arial', size=st.size, margins_cm=st.margins)
    add_title(doc, s, st)
    for item, sec in s.ordered_form():
        write_section(doc, s, s.sections.index(sec), item, st)
    path = doc.save(out_odt)
    return (path, to_pdf(path)) if pdf else (path, None)
