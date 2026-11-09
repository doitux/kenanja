"""Streicher-Arrangeur im Stil typischer Lobpreis-Arrangements:

* Aufbau über den Song: Intro und erste Strophe schweigen die Streicher („1. x tacet“),
  sie kommen nach und nach dazu und sind im zurückgenommenen Schluss-Chorus wieder raus.
* Cello: Fundament – Grundton/Basston in halben und ganzen Noten; in ruhigen Teilen als
  Liegeton in Tenorlage (Terz/Quinte); in Steigerungen Achtel-Puls; im Choral die Melodie
  eine Oktave tiefer.
* Geige/Bratsche: im Chorus Liegetöne (Terz/Quinte, möglichst Liegeton-Verbindungen),
  später Gegenstimme mit Terzen/Sexten zur Melodie und Füllfiguren dort, wo die Melodie
  pausiert; in der Bridge Läufe/Arpeggien zur Steigerung; im Finale hoch und geteilt.
* Zwischenspiele: Hook bzw. Melodie als Solo.
* Spielniveau pro Spieler: leicht / mittel / schwer (Tonumfang, Rhythmik, Doppelgriffe).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from fractions import Fraction as F
from typing import Optional

from ..model import Bar, FormItem, Note, Section, Song, lineup_states
from ..theory import ChordSymbol, best_key_for_shift, spell_pitch, tpc_pc

INSTRUMENTS = {
    'Violine': {'clef': 'treble', 'levels': {'leicht': (55, 83), 'mittel': (55, 88), 'schwer': (55, 93)},
                'sweet': (62, 79), 'pad': (60, 74)},
    'Bratsche': {'clef': 'alto', 'levels': {'leicht': (48, 76), 'mittel': (48, 81), 'schwer': (48, 86)},
                 'sweet': (55, 72), 'pad': (53, 67)},
    'Violoncello': {'clef': 'bass', 'levels': {'leicht': (36, 62), 'mittel': (36, 67), 'schwer': (36, 74)},
                    'sweet': (40, 60), 'pad': (45, 62)},
}
ALIASES = {'violin': 'Violine', 'geige': 'Violine', 'violine': 'Violine', 'viola': 'Bratsche',
           'bratsche': 'Bratsche', 'cello': 'Violoncello', 'violoncello': 'Violoncello', 'vc': 'Violoncello'}


def instrument_of(name: str) -> str:
    n = name.lower()
    for k, v in ALIASES.items():
        if k in n:
            return v
    return 'Violine'


@dataclass
class Player:
    name: str                    # Stimmenname in der Partitur, z. B. 'Violine', 'Violine (Titus)'
    instrument: str = 'Violine'  # Violine | Bratsche | Violoncello
    level: str = 'mittel'        # leicht | mittel | schwer

    @property
    def range(self) -> tuple[int, int]:
        return INSTRUMENTS[self.instrument]['levels'].get(self.level, INSTRUMENTS[self.instrument]['levels']['mittel'])

    @property
    def low_voice(self) -> bool:
        return self.instrument == 'Violoncello'


# ------------------------------------------------------------------ Linearisierung ----

@dataclass
class LBar:
    """Ein Takt im Ablauf (so, wie er gespielt wird)."""
    form_index: int
    item: FormItem
    section: Section
    bar: Bar
    first: bool = False           # erster Takt des Ablauf-Teils
    last: bool = False
    parts: dict = field(default_factory=dict)     # Stimme -> [Note]
    marks: dict = field(default_factory=dict)     # Stimme -> [(onset, 'dyn'|'text', str)]


def linearize(song: Song) -> list[LBar]:
    out = []
    k = 0
    for fi, item in enumerate(song.form):
        sec = song.section(item.section)
        for r in range(max(1, item.repeat)):
            for bi, bar in enumerate(sec.bars):
                b = copy.deepcopy(bar)
                b.parts = {}
                out.append(LBar(fi, item, sec, b, first=(bi == 0 and r == 0), last=(bi == len(sec.bars) - 1)))
    return out


# ------------------------------------------------------------------ Rollenplanung ----

ROLES_UPPER = ('tacet', 'pad', 'counter', 'build', 'hook', 'melody', 'high')
ROLES_LOW = ('tacet', 'roots', 'tenor', 'pulse', 'melody', 'hook')


def plan_roles(song: Song, players: list[Player]) -> dict[int, dict[str, str]]:
    """Bestimmt pro Ablauf-Eintrag die Rolle jeder Streicherstimme (falls nicht vorgegeben)."""
    states = lineup_states(song)
    seen = {}
    plan = {}
    n_items = len(song.form)
    # Intensität: Größe der Band in diesem Teil
    sizes = [len(st) for st in states] or [0]
    max_size = max(sizes) or 1
    last_loud = max((i for i, it in enumerate(song.form)
                     if song.section(it.section).kind in ('chorus', 'choral', 'bridge')), default=-1)
    for fi, item in enumerate(song.form):
        sec = song.section(item.section)
        kind = sec.kind
        seen[kind] = seen.get(kind, 0) + 1
        nth = seen[kind]
        st = states[fi] if fi < len(states) else set()
        removed_rhythm = {'drums', 'bass'} <= {r.lower() for r in item.remove}
        quiet = removed_rhythm or (len(st) <= max_size * 0.4 and fi > n_items // 2)
        solo_note = (item.note or '').lower()
        strings_solo = any(w in solo_note for w in ('string', 'violin', 'geige', 'streicher', 'cello'))
        roles = {}
        for pl in players:
            if pl.name in item.strings and item.strings[pl.name] != 'auto':
                roles[pl.name] = item.strings[pl.name]
                continue
            low = pl.low_voice
            if kind == 'intro':
                r = 'hook' if (strings_solo and not low) else 'tacet'
            elif kind == 'verse':
                r = 'tacet' if nth == 1 else (('roots' if low else 'pad') if nth == 2 else ('tenor' if low else 'counter'))
            elif kind == 'prechorus':
                r = 'tenor' if low else 'pad'
            elif kind == 'chorus':
                if quiet:
                    r = 'tacet'
                elif nth == 1:
                    r = 'tacet' if low else 'pad'
                elif fi == last_loud or nth >= 3:
                    r = 'roots' if low else 'high'
                else:
                    r = 'tenor' if low else 'counter'
            elif kind == 'bridge':
                r = 'pulse' if low else 'build'
            elif kind in ('interlude', 'solo'):
                if strings_solo or ('solo' not in solo_note and not item.note):
                    r = 'roots' if low else 'hook'
                else:
                    r = 'tacet' if 'solo' in solo_note else ('roots' if low else 'pad')
            elif kind == 'choral':
                r = 'melody' if low else 'high'
            elif kind in ('outro', 'tag', 'ending'):
                r = 'tenor' if low else 'pad'
            else:
                r = 'tenor' if low else 'pad'
            roles[pl.name] = r
        plan[fi] = roles
    return plan


DYN_FOR_ROLE = {'pad': 'mp', 'counter': 'mf', 'build': 'mf', 'hook': 'mf', 'melody': 'f', 'high': 'f',
                'roots': 'mp', 'tenor': 'mp', 'pulse': 'mf'}


# ------------------------------------------------------------------ Stimmführung ----

def _segments(lb: LBar, current: Optional[str]) -> list[tuple[F, F, Optional[ChordSymbol]]]:
    """Akkordabschnitte im Takt: (Beginn, Dauer, Akkord)."""
    L = lb.bar.length
    pts = [(c.onset, c.symbol) for c in lb.bar.chords if c.onset < L]
    if not pts or pts[0][0] > 0:
        pts = [(F(0), current)] + pts
    segs = []
    for k, (on, sym) in enumerate(pts):
        end = pts[k + 1][0] if k + 1 < len(pts) else L
        if end <= on:
            continue
        try:
            ch = ChordSymbol.parse(sym) if sym else None
            if ch is not None and ch.is_nc:
                ch = None
        except ValueError:
            ch = None
        segs.append((on, end - on, ch))
    return segs


def _melody_at(lb: LBar, t: F) -> Optional[int]:
    for n in lb.bar.melody:
        if n.pitch is not None and n.onset <= t < n.onset + n.dur:
            return n.pitch
    return None


def _melody_rests(lb: LBar) -> list[tuple[F, F]]:
    """Zeiträume ohne neue Melodienoten (Pausen und lange Haltetöne ab der 2. Zählzeit)."""
    L = lb.bar.length
    busy = []
    for n in lb.bar.melody:
        if n.pitch is not None:
            busy.append((n.onset, min(L, n.onset + max(n.dur, F(1)))))
    busy.sort()
    gaps, t = [], F(0)
    for a, b in busy:
        if a > t:
            gaps.append((t, a - t))
        t = max(t, b)
    if t < L:
        gaps.append((t, L - t))
    return [g for g in gaps if g[1] >= 1]


def _candidates(pcs: dict[int, str], lo: int, hi: int, prefer=('3', '5', '9', '7', 'R', '6', '4', 'B')):
    out = []
    for p in range(lo, hi + 1):
        f = pcs.get(p % 12)
        if f is not None:
            out.append((p, prefer.index(f) if f in prefer else len(prefer)))
    return out


def _pick(cands, prev, melody=None, below=True, avoid_pc=None):
    best, best_cost = None, 1e9
    for p, rank in cands:
        cost = rank * 1.2
        if prev is not None:
            d = abs(p - prev)
            cost += d * 0.7 if d <= 4 else 3 + d
        if melody is not None:
            iv = melody - p
            if below:
                if iv < 3:
                    cost += 8
                elif iv > 12:
                    cost += (iv - 12) * 0.6
            else:
                if -iv < 3:
                    cost += 8
        if avoid_pc is not None and p % 12 == avoid_pc:
            cost += 3
        if cost < best_cost:
            best, best_cost = p, cost
    return best


def _key_spell(song: Song):
    tpc, _ = song.key_tpc()
    return tpc


def _note(on, dur, p, key_tpc, extra=()):
    return Note(onset=on, dur=dur, pitch=p, tpc=spell_pitch(p, key_tpc),
                extra=[(e, spell_pitch(e, key_tpc)) for e in extra])


def arrange_strings(song: Song, players: list[Player], plan: Optional[dict] = None) -> list[LBar]:
    """Erzeugt Streicherstimmen für den Ablauf. Gibt die linearisierten Takte mit .parts zurück."""
    s = song
    lbars = linearize(s)
    plan = plan or plan_roles(s, players)
    key_tpc = _key_spell(s)
    for pl in players:
        lo, hi = pl.range
        inst = INSTRUMENTS[pl.instrument]
        prev = None
        current_chord = None
        last_role = None
        for k, lb in enumerate(lbars):
            role = plan.get(lb.form_index, {}).get(pl.name, 'tacet')
            notes: list[Note] = []
            segs = _segments(lb, current_chord)
            if lb.bar.chords:
                current_chord = lb.bar.chords[-1].symbol
            if lb.first and role != last_role:
                if role == 'tacet':
                    pass
                else:
                    lb.marks.setdefault(pl.name, []).append((F(0), 'dyn', DYN_FOR_ROLE.get(role, 'mf')))
            last_role = role if lb.first else last_role
            if role == 'tacet':
                lb.parts[pl.name] = [Note(F(0), lb.bar.length)]
                prev = None if lb.last else prev
                continue
            if role in ('melody', 'hook'):
                mel = [n for n in lb.bar.melody]
                if role == 'hook' and not any(n.pitch is not None for n in mel):
                    role = 'pad'
                else:
                    shift = _fit_octave([n.pitch for n in lb.section.bars[0].melody + mel if n.pitch is not None],
                                        inst['sweet'] if not pl.low_voice else (lo, lo + 24), lo, hi)
                    for n in mel:
                        if n.pitch is None:
                            notes.append(Note(n.onset, n.dur))
                        else:
                            p = n.pitch + shift
                            while p > hi:
                                p -= 12
                            while p < lo:
                                p += 12
                            notes.append(Note(n.onset, n.dur, p, (n.tpc if shift % 12 == 0 and n.tpc is not None
                                                                 else spell_pitch(p, key_tpc)), tie=n.tie))
                    if not notes:
                        notes = [Note(F(0), lb.bar.length)]
                    lb.parts[pl.name] = notes
                    continue
            if pl.low_voice:
                notes, prev = _low_voice(role, lb, segs, prev, pl, key_tpc)
            else:
                notes, prev = _upper_voice(role, lb, segs, prev, pl, key_tpc, k, lbars)
            lb.parts[pl.name] = notes or [Note(F(0), lb.bar.length)]
    return lbars


def _fit_octave(pitches, sweet, lo, hi) -> int:
    if not pitches:
        return 0
    mid = (min(pitches) + max(pitches)) / 2
    target = (sweet[0] + sweet[1]) / 2
    best = min(range(-36, 37, 12), key=lambda o: (max(0, lo - (min(pitches) + o)) + max(0, max(pitches) + o - hi)) * 10
               + abs(mid + o - target))
    return best


def _low_voice(role, lb, segs, prev, pl, key_tpc):
    lo, hi = pl.range
    notes = []
    for on, dur, ch in segs:
        if ch is None:
            notes.append(Note(on, dur))
            continue
        pcs = ch.pitch_classes()
        if role in ('roots', 'pulse'):
            bpc = ch.bass_pc()
            cands = [p for p in range(lo, min(hi, lo + 22) + 1) if p % 12 == bpc]
            p = min(cands, key=lambda x: (abs(x - prev) if prev else abs(x - (lo + 9)))) if cands else lo
        else:  # tenor: Liegeton in Tenorlage
            tl, th = INSTRUMENTS[pl.instrument]['pad']
            cands = _candidates(pcs, max(lo, tl), min(hi, th), prefer=('5', '3', 'R', '9', '7', 'B', '6', '4'))
            p = _pick(cands, prev if prev else (tl + th) // 2) if cands else None
            if p is None:
                notes.append(Note(on, dur))
                continue
        if role == 'pulse' and dur >= 2 and pl.level != 'leicht':
            # Achtel-Puls: 1/2 – 1 – 1 – 1 – 1/2 (pro 4 Schläge)
            t = on
            pattern = [F(1, 2), F(1), F(1), F(1), F(1, 2)]
            for d in pattern:
                if t >= on + dur:
                    break
                d = min(d, on + dur - t)
                notes.append(_note(t, d, p, key_tpc))
                t += d
        else:
            notes.append(_note(on, dur, p, key_tpc))
        prev = p
    return notes, prev


def _upper_voice(role, lb, segs, prev, pl, key_tpc, k, lbars):
    lo, hi = pl.range
    inst = INSTRUMENTS[pl.instrument]
    pad_lo, pad_hi = inst['pad']
    if role == 'high':
        pad_lo, pad_hi = min(hi - 7, pad_lo + 7), min(hi, pad_hi + 9)
    elif role in ('counter', 'build'):
        pad_lo, pad_hi = pad_lo + 2, min(hi, pad_hi + 5)
    notes = []
    rests = _melody_rests(lb) if role in ('counter', 'build', 'high') else []
    for si, (on, dur, ch) in enumerate(segs):
        if ch is None:
            notes.append(Note(on, dur))
            continue
        pcs = ch.pitch_classes()
        mel = _melody_at(lb, on)
        below = not (role in ('counter', 'high') and mel is not None and mel < 67)
        cands = _candidates(pcs, max(lo, pad_lo), min(hi, pad_hi))
        p = _pick(cands, prev if prev else (pad_lo + pad_hi) // 2, mel, below=below,
                  avoid_pc=(mel % 12) if mel is not None else None)
        if p is None:
            notes.append(Note(on, dur))
            continue
        # Füllfigur in einer Melodie-Pause innerhalb dieses Akkordabschnitts?
        fill = None
        for (ra, rd) in rests:
            a = max(ra, on + F(1))
            b = min(ra + rd, on + dur)
            if b - a >= 1:
                fill = (a, b)
                break
        extra = []
        if role == 'high' and pl.level == 'schwer':
            # geteilt: Terz/Sexte darunter
            below_c = [c for c, _ in _candidates(pcs, p - 9, p - 3)]
            if below_c:
                extra = [max(below_c)]
        if fill and pl.level != 'leicht':
            a, b = fill
            notes.append(_note(on, a - on, p, key_tpc, extra))
            # Ziel: Akkordton des nächsten Abschnitts (oder dieses), stufenweise hin
            nxt = segs[si + 1][2] if si + 1 < len(segs) else _next_chord(lbars, k)
            target_cands = _candidates((nxt or ch).pitch_classes(), max(lo, pad_lo), min(hi, pad_hi + 4))
            target = _pick(target_cands, p) if target_cands else p
            run = _scale_run(p, target, int((b - a) * 2), key_tpc, role == 'build')
            step = F(1, 2)
            t = a
            for q in run:
                if t >= b:
                    break
                d = min(step, b - t)
                notes.append(_note(t, d, q, key_tpc))
                t += d
            if t < b:
                notes.append(_note(t, b - t, run[-1] if run else p, key_tpc))
            if b < on + dur:
                notes.append(_note(b, on + dur - b, target, key_tpc))
                p = target
        elif role == 'build' and dur >= 2 and pl.level != 'leicht':
            # aufsteigendes Arpeggio in Achteln
            arp = sorted(c for c, _ in _candidates(pcs, p, min(hi, p + 14)))
            t = on
            j = 0
            while t < on + dur and arp:
                d = min(F(1, 2), on + dur - t)
                notes.append(_note(t, d, arp[j % len(arp)], key_tpc))
                j += 1
                t += d
            p = arp[min(len(arp) - 1, j - 1)] if arp else p
        else:
            notes.append(_note(on, dur, p, key_tpc, extra))
        prev = p
    return _merge_ties(notes), prev


def _next_chord(lbars, k) -> Optional[ChordSymbol]:
    for lb in lbars[k + 1:k + 3]:
        if lb.bar.chords:
            try:
                ch = ChordSymbol.parse(lb.bar.chords[0].symbol)
                return None if ch.is_nc else ch
            except ValueError:
                return None
    return None


def _scale_run(a: int, b: int, n: int, key_tpc: int, arpeggio=False) -> list[int]:
    """Tonleiterlauf von a nach b in n Schritten (diatonisch zur Tonart)."""
    if n <= 0:
        return []
    root = tpc_pc(key_tpc)
    scale = {(root + i) % 12 for i in (0, 2, 4, 5, 7, 9, 11)}
    seq = []
    cur = a
    direction = 1 if b >= a else -1
    if b == a:
        # Wechselnote
        up = cur + 1
        while up % 12 not in scale:
            up += 1
        return ([up, a] * n)[:n]
    while len(seq) < n:
        nxt = cur + direction
        while nxt % 12 not in scale:
            nxt += direction
        if (direction > 0 and nxt > b) or (direction < 0 and nxt < b):
            nxt = b
        seq.append(nxt)
        cur = nxt
        if cur == b:
            break
    # Zeit auffüllen: Zielton halten
    while len(seq) < n:
        seq.append(b)
    return seq


def _merge_ties(notes: list[Note]) -> list[Note]:
    out = []
    for n in notes:
        if n.dur <= 0:
            continue
        out.append(n)
    return out


def strings_report(lbars: list[LBar], players: list[Player]) -> dict:
    """Kurze Statistik (für Tests/Vergleiche)."""
    rep = {}
    for pl in players:
        ps = [n.pitch for lb in lbars for n in lb.parts.get(pl.name, []) if n.pitch is not None]
        per = {}
        for lb in lbars:
            lab = lb.item.label or lb.section.label
            cnt = sum(1 for n in lb.parts.get(pl.name, []) if n.pitch is not None)
            per[lab] = per.get(lab, 0) + cnt
        rep[pl.name] = {'noten': len(ps), 'umfang': (min(ps), max(ps)) if ps else None, 'pro_teil': per}
    return rep
