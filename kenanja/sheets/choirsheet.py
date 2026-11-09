"""Chor-Textblatt: eine Seite, nur Text, mit Ausdrucksanweisungen in einem festen Farbcode:
  rot   = leiser / zurückhaltend      grün = lauter / offener
  lila  = nicht mitsingen (Solo)      grau = Text, den der Chor nicht singt
"""
from __future__ import annotations

from pathlib import Path

from .. import lyrics as L
from ..model import Song
from .odt import OdtDoc, to_pdf

RED, GREEN, PURPLE, GREY = '#ff0000', '#006400', '#8b008b', '#808080'
CUE_FONT = 'DejaVu Serif'

SOFT_WORDS = ('leise', 'zurück', 'intim', 'zögerlich', 'erzählend', 'wie am anfang', 'ruhig', 'sanft', 'staunend')
LOUD_WORDS = ('laut', 'kräftig', 'voll', 'offen', 'freudig', 'begeistert', 'feiernd', 'überzeugt', 'dankbar',
              'motivierend', 'herzen', 'jubelnd')


def cue_color(item) -> str:
    if item.choir_dyn == 'soft':
        return RED
    if item.choir_dyn == 'loud':
        return GREEN
    t = (item.choir_cue or '').lower()
    if any(w in t for w in SOFT_WORDS) and not any(w in t for w in ('lauter', 'kräftig')):
        return RED
    return GREEN


def _section_text_lines(song: Song, idx: int) -> list[str]:
    sec = song.sections[idx]
    if sec.lines:
        return sec.lines
    return [g[0] for g in L.line_groups(song.sections, idx)]


def build_choir_sheet(song: Song, out_odt, pdf: bool = True, size: float = 14, one_page: bool = True):
    """Erzeugt das Chor-Textblatt. Mit one_page wird die Schrift so lange verkleinert, bis alles auf
    eine Seite passt."""
    sizes = [size, 13, 12, 11.5, 11, 10.5, 10] if one_page else [size]
    result = (None, None)
    for sz in sizes:
        doc = _make(song, sz)
        path = doc.save(out_odt)
        if not pdf:
            return path, None
        p = to_pdf(path)
        result = (path, p)
        if not one_page or p is None or _pages(p) <= 1:
            break
    return result


def _pages(pdf) -> int:
    try:
        import pymupdf
        return len(pymupdf.open(pdf))
    except Exception:
        import subprocess
        r = subprocess.run(['pdfinfo', str(pdf)], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            if line.startswith('Pages:'):
                return int(line.split()[1])
    return 1


def _make(song: Song, size: float) -> OdtDoc:
    doc = OdtDoc(font='Arial', size=size, margins_cm=(1.5, 1.5, 1.2, 1.5))
    tw = doc.text_width
    authors = [a.strip() for a in song.authors.replace(' / ', '\n').replace(';', '\n').splitlines() if a.strip()]
    p = doc.paragraph(tabs=[(tw / 2, 'center'), (tw, 'right')])
    p.tab().text(song.title, bold=True, size=size)
    if authors:
        p.tab().text(authors[0], italic=True, size=10)
    p2 = doc.paragraph(tabs=[(tw / 2, 'center'), (tw, 'right')])
    p2.tab().text(f'v.{song.version}', size=10)
    if len(authors) > 1:
        p2.tab().text(authors[1], italic=True, size=10)
    for a in authors[2:]:
        doc.paragraph(align='right').text(a, italic=True, size=10)
    doc.paragraph(size=6)
    for item, sec in song.ordered_form():
        idx = song.sections.index(sec)
        lines = _section_text_lines(song, idx)
        hp = doc.paragraph(keep_next=bool(lines), space_before=3)
        hp.text((item.label or sec.label) + (':' if lines else ''), bold=True)
        if item.choir == 'solo':
            hp.text(' → nicht mitsingen (Solo)', bold=True, italic=True, color=PURPLE, font=CUE_FONT, size=size - 1)
        if item.choir_cue:
            hp.text(' → ' + item.choir_cue, bold=True, italic=True, color=cue_color(item), font=CUE_FONT, size=size - 1)
        grey = item.choir in ('solo', 'tacet')
        for k, line in enumerate(lines):
            doc.paragraph(keep_next=k < len(lines) - 1).text(line, color=GREY if grey else None)
    return doc
