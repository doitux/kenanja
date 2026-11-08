"""Griffbilder für die Akustikgitarre: Tabelle offener Griffe plus Barré-Griffe (E-/A-Form)
und eine PNG-Zeichnung im Stil der A-GIT-Blätter.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..theory import ChordSymbol, tpc_pc

# Saiten E A D G H e; -1 = nicht anschlagen, 0 = leer; optional Fingersatz
OPEN = {
    'C': [-1, 3, 2, 0, 1, 0], 'C2': [-1, 3, 2, 0, 3, 0], 'Cadd9': [-1, 3, 2, 0, 3, 0], 'Cmaj7': [-1, 3, 2, 0, 0, 0],
    'C7': [-1, 3, 2, 3, 1, 0], 'C/E': [0, 3, 2, 0, 1, 0], 'C/G': [3, 3, 2, 0, 1, 0],
    'D': [-1, -1, 0, 2, 3, 2], 'D4': [-1, -1, 0, 2, 3, 3], 'Dsus4': [-1, -1, 0, 2, 3, 3], 'D2': [-1, -1, 0, 2, 3, 0],
    'Dsus2': [-1, -1, 0, 2, 3, 0], 'D7': [-1, -1, 0, 2, 1, 2], 'Dmaj7': [-1, -1, 0, 2, 2, 2], 'Dm': [-1, -1, 0, 2, 3, 1],
    'Dm7': [-1, -1, 0, 2, 1, 1], 'D/F#': [2, -1, 0, 2, 3, 2], 'D/A': [-1, 0, 0, 2, 3, 2],
    'E': [0, 2, 2, 1, 0, 0], 'E7': [0, 2, 0, 1, 0, 0], 'Em': [0, 2, 2, 0, 0, 0], 'Em7': [0, 2, 2, 0, 3, 0],
    'E4': [0, 2, 2, 2, 0, 0], 'Esus4': [0, 2, 2, 2, 0, 0],
    'F': [1, 3, 3, 2, 1, 1], 'Fmaj7': [-1, -1, 3, 2, 1, 0], 'F2': [-1, -1, 3, 2, 1, 3],
    'G': [3, 2, 0, 0, 0, 3], 'G2': [3, 0, 0, 2, 0, 3], 'G7': [3, 2, 0, 0, 0, 1], 'Gmaj7': [3, 2, 0, 0, 0, 2],
    'G4': [3, 3, 0, 0, 1, 3], 'Gsus4': [3, 3, 0, 0, 1, 3], 'G6': [3, 2, 0, 0, 0, 0],
    'A': [-1, 0, 2, 2, 2, 0], 'A2': [-1, 0, 2, 2, 0, 0], 'Asus2': [-1, 0, 2, 2, 0, 0], 'A4': [-1, 0, 2, 2, 3, 0],
    'Asus4': [-1, 0, 2, 2, 3, 0], 'A7': [-1, 0, 2, 0, 2, 0], 'Am': [-1, 0, 2, 2, 1, 0], 'Am7': [-1, 0, 2, 0, 1, 0],
    'Amaj7': [-1, 0, 2, 1, 2, 0], 'Am/G': [3, 0, 2, 2, 1, 0], 'Am7/G': [3, 0, 2, 0, 1, 0],
    'B7': [-1, 2, 1, 2, 0, 2], 'Bm7': [-1, 2, 0, 2, 0, 2], 'Bm': [-1, 2, 4, 4, 3, 2],
    'G/B': [-1, 2, 0, 0, 0, 3], 'G/F#': [2, 2, 0, 0, 0, 3], 'Em/D': [-1, -1, 0, 0, 0, 0],
}
BASIC = {'C', 'D', 'E', 'G', 'A', 'Am', 'Em', 'Dm', 'E7', 'A7', 'D7', 'G7', 'Em7', 'Am7'}

# Barré-Formen relativ zum Grundton auf der E- bzw. A-Saite
E_FORMS = {'': [0, 2, 2, 1, 0, 0], 'm': [0, 2, 2, 0, 0, 0], '7': [0, 2, 0, 1, 0, 0], 'm7': [0, 2, 0, 0, 0, 0],
           '4': [0, 2, 2, 2, 0, 0], 'maj7': [0, -1, 1, 1, 0, -1]}
A_FORMS = {'': [-1, 0, 2, 2, 2, 0], 'm': [-1, 0, 2, 2, 1, 0], '7': [-1, 0, 2, 0, 2, 0], 'm7': [-1, 0, 2, 0, 1, 0],
           '2': [-1, 0, 2, 2, 0, 0], '4': [-1, 0, 2, 2, 3, 0], 'maj7': [-1, 0, 2, 1, 2, 0]}


def shape_for(symbol: str) -> Optional[list[int]]:
    """Griff (6 Bundangaben) für ein (vereinfachtes) Akkordsymbol."""
    if symbol in OPEN:
        return OPEN[symbol]
    try:
        ch = ChordSymbol.parse(symbol)
    except ValueError:
        return None
    if ch.is_nc:
        return None
    q = ch.quality
    q = {'sus4': '4', 'sus2': '2', 'add9': '2', 'min': 'm', 'mi': 'm'}.get(q, q)
    root = tpc_pc(ch.root)
    for forms, open_pc in ((E_FORMS, 4), (A_FORMS, 9)):
        if q in forms:
            fret = (root - open_pc) % 12
            if fret == 0:
                fret = 12 if forms is E_FORMS and False else 0
            if fret > 9:
                continue
            return [(-1 if v < 0 else v + fret) for v in forms[q]]
    return None


def needs_diagram(symbol: str) -> bool:
    return symbol not in BASIC


def draw_diagram(symbol: str, shape: list[int], path, scale: int = 4) -> Path:
    """Zeichnet ein Griffbild als PNG (Stil der A-GIT-Blätter)."""
    from PIL import Image, ImageDraw, ImageFont
    s = scale
    W, H = 34 * s, 70 * s
    img = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(img)
    try:
        f_title = ImageFont.truetype('/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf', 9 * s)
        f_small = ImageFont.truetype('/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf', 6 * s)
    except OSError:
        f_title = f_small = ImageFont.load_default()
    fretted = [f for f in shape if f > 0]
    base = 1
    if fretted and max(fretted) > 4:
        base = min(fretted)
    x0, y0 = 5 * s, 18 * s
    gw, gh = 24 * s, 46 * s
    nfrets = 5
    sx, sy = gw / 5, gh / nfrets
    tw = d.textlength(symbol, font=f_title)
    d.text(((W - tw) / 2, 1 * s), symbol, fill='black', font=f_title)
    for i in range(6):
        d.line([(x0 + i * sx, y0), (x0 + i * sx, y0 + gh)], fill='black', width=max(1, s // 2))
    for j in range(nfrets + 1):
        w = 3 * s if (j == 0 and base == 1) else max(1, s // 2)
        d.line([(x0, y0 + j * sy), (x0 + gw, y0 + j * sy)], fill='black', width=w)
    if base > 1:
        d.text((x0 + gw + 1.5 * s, y0 + 0.2 * sy), str(base), fill='black', font=f_small)
    for i, f in enumerate(shape):
        x = x0 + i * sx
        if f < 0:
            d.text((x - 2.5 * s, y0 - 8 * s), '×', fill='#c00000', font=f_small)
        elif f == 0:
            r = 2 * s
            d.ellipse([x - r, y0 - 6 * s - r, x + r, y0 - 6 * s + r], outline='black', width=max(1, s // 2))
        else:
            y = y0 + (f - base + 0.5) * sy
            r = 2.6 * s
            d.ellipse([x - r, y - r, x + r, y + r], fill='black')
    img.save(path)
    return Path(path)
