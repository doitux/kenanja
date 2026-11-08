"""Nebenstimmen für Sänger und Chorsatz (SATB), homorhythmisch zur Melodie –
wie in Renés Dateien (Holy Forever: untere Stimme meist eine Terz unter der Melodie,
obere Terz/Quarte darüber; SATB im gleichen Rhythmus wie die Melodie).
"""
from __future__ import annotations

import copy
from fractions import Fraction as F
from typing import Optional

from ..model import Note, Song
from ..theory import ChordSymbol, spell_pitch, tpc_pc
from .strings import LBar

RANGES = {'Sopran': (60, 79), 'Alt': (55, 74), 'Tenor': (48, 67), 'Bass': (40, 62),
          'Unterstimme': (53, 76), 'Oberstimme': (60, 81)}


def _scale(key_tpc):
    r = tpc_pc(key_tpc)
    return [(r + i) % 12 for i in (0, 2, 4, 5, 7, 9, 11)]


def _chord_at(lb: LBar, t: F, current: Optional[str]):
    sym = current
    for c in lb.bar.chords:
        if c.onset <= t:
            sym = c.symbol
    try:
        ch = ChordSymbol.parse(sym) if sym else None
        return None if ch is None or ch.is_nc else ch
    except ValueError:
        return None


def _diatonic(p: int, steps: int, scale: list[int]) -> int:
    q = p
    k = 0
    d = 1 if steps > 0 else -1
    while k < abs(steps):
        q += d
        if q % 12 in scale:
            k += 1
    return q


def _harmony_pitch(mel: int, ch: Optional[ChordSymbol], scale, below=True, prev=None, rng=(48, 84)) -> int:
    lo, hi = rng
    pcs = ch.pitch_classes() if ch else {}
    cands = []
    rngs = range(3, 8) if below else range(3, 6)
    for iv in rngs:
        q = mel - iv if below else mel + iv
        if lo <= q <= hi and q % 12 in pcs and pcs[q % 12] != 'B':
            pref = {3: 0, 4: 0, 5: 1.5, 6: 3, 7: 2}[iv]
            if prev is not None:
                pref += abs(q - prev) * 0.15
            cands.append((pref, q))
    if cands:
        return min(cands)[1]
    q = _diatonic(mel, -2 if below else 2, scale)   # Terz in der Tonleiter
    return q


def harmony_voice(song: Song, lbars: list[LBar], name: str = 'Unterstimme', below: bool = True,
                  only_kinds=None) -> None:
    """Schreibt eine zweite Stimme (unter/über der Melodie) in lbars[*].parts[name]."""
    key_tpc, _ = song.key_tpc()
    scale = _scale(key_tpc)
    rng = RANGES.get(name, (48, 84))
    current = None
    prev = None
    for lb in lbars:
        active = (only_kinds is None or lb.section.kind in only_kinds) and lb.item.choir != 'tacet'
        out = []
        if not active:
            lb.parts[name] = [Note(F(0), lb.bar.length)]
            continue
        for n in lb.bar.melody:
            if n.pitch is None or not active:
                out.append(Note(n.onset, n.dur))
                continue
            ch = _chord_at(lb, n.onset, current)
            q = _harmony_pitch(n.pitch, ch, scale, below, prev, rng)
            out.append(Note(n.onset, n.dur, q, spell_pitch(q, key_tpc), lyrics=dict(n.lyrics), tie=n.tie))
            prev = q
        if lb.bar.chords:
            current = lb.bar.chords[-1].symbol
        lb.parts[name] = out or [Note(F(0), lb.bar.length)]


def satb(song: Song, lbars: list[LBar], only_kinds=('chorus', 'choral'), names=('Sopran', 'Alt', 'Tenor', 'Bass')) -> None:
    """Vierstimmiger Chorsatz im Rhythmus der Melodie (in den gewählten Teilen)."""
    key_tpc, _ = song.key_tpc()
    scale = _scale(key_tpc)
    S, A, T, B = names
    current = None
    prev = {A: None, T: None, B: None}
    for lb in lbars:
        active = (only_kinds is None or lb.section.kind in only_kinds) and lb.item.choir == 'sing'
        parts = {S: [], A: [], T: [], B: []}
        if not active:
            for k in parts:
                lb.parts[k] = [Note(F(0), lb.bar.length)]
            if lb.bar.chords:
                current = lb.bar.chords[-1].symbol
            continue
        for n in lb.bar.melody:
            if n.pitch is None or not active:
                for k in parts:
                    parts[k].append(Note(n.onset, n.dur))
                continue
            ch = _chord_at(lb, n.onset, current)
            mel = n.pitch
            while mel > RANGES[S][1]:
                mel -= 12
            while mel < RANGES[S][0]:
                mel += 12
            a = _harmony_pitch(mel, ch, scale, True, prev[A], RANGES[A])
            pcs = ch.pitch_classes() if ch else {}
            used = {mel % 12, a % 12}
            # Tenor: fehlender Akkordton (bevorzugt), unter dem Alt
            want = [pc for pc, f in pcs.items() if pc not in used and f != 'B'] or list(pcs) or [a % 12]
            tl, th = RANGES[T]
            t_cands = [q for q in range(tl, min(th, a - 1) + 1) if q % 12 in want]
            if not t_cands:
                t_cands = [q for q in range(tl, min(th, a - 1) + 1) if q % 12 in pcs] or [a - 12]
            pt = prev[T] if prev[T] else (tl + th) // 2
            t = min(t_cands, key=lambda q: abs(q - pt))
            # Bass: Basston des Akkords
            bpc = ch.bass_pc() if ch else mel % 12
            bl, bh = RANGES[B]
            b_cands = [q for q in range(bl, min(bh, t - 1) + 1) if q % 12 == bpc] or [q for q in range(bl, bh + 1) if q % 12 == bpc]
            pb = prev[B] if prev[B] else 50
            b = min(b_cands, key=lambda q: abs(q - pb))
            for k, q in ((S, mel), (A, a), (T, t), (B, b)):
                parts[k].append(Note(n.onset, n.dur, q, spell_pitch(q, key_tpc), lyrics=dict(n.lyrics), tie=n.tie))
            prev = {A: a, T: t, B: b}
        if lb.bar.chords:
            current = lb.bar.chords[-1].symbol
        for k, v in parts.items():
            lb.parts[k] = v or [Note(F(0), lb.bar.length)]
