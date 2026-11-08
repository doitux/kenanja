"""Liedtext-Werkzeuge: Silben eines Abschnitts (inkl. Auftakt), Zeilenumbrüche,
Zuordnung eines eingefügten Liedtexts zu den Melodiesilben und Akkord-Positionen.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from fractions import Fraction as F
from typing import Optional

from .model import Note, Section


@dataclass
class Syl:
    t: F             # Zeit in Vierteln ab Abschnittsbeginn (Auftakt: negativ)
    bar: int         # Taktindex im Abschnitt, -1 = Auftakt aus dem vorigen Abschnitt
    idx: int         # Index in bar.melody
    text: str
    syllabic: str    # single | begin | middle | end
    note: Note
    end: F = F(0)    # Ende inkl. Haltebögen

    @property
    def word_end(self) -> bool:
        return self.syllabic in ('single', 'end')


@dataclass
class ChordAt:
    t: F
    symbol: str


def _trailing_pickup(bar_notes: list[Note], verse: int = 0) -> int:
    """Anzahl Einträge am Taktende, die nach der letzten Pause gesungen werden."""
    k = 0
    for n in reversed(bar_notes):
        if n.pitch is None:
            break
        k += 1
    if k == 0 or k == len(bar_notes):
        return 0
    first = bar_notes[len(bar_notes) - k]
    if first.onset < 1 or not any(n.syllable(verse) for n in bar_notes[-k:]):
        return 0
    return k


def assign_pickups(sections: list[Section]) -> None:
    """Auftakte erkennen: Silben am Ende eines Abschnitts, die zum nächsten gehören."""
    for i in range(1, len(sections)):
        a, b = sections[i - 1], sections[i]
        b.pickup = 0
        b.spill = 0
        if not a.bars or not b.bars:
            continue
        # Überhang: Gesang des vorigen Abschnitts reicht in den ersten Takt hinein
        first_bar = b.bars[0].melody
        lead = 0
        for n in first_bar:
            if n.pitch is None:
                break
            lead += 1
        a_last = next((n for n in reversed(a.bars[-1].melody)), None)
        n_syl_b = sum(1 for bar in b.bars for n in bar.melody if n.pitch is not None and n.syllable(b.verse))
        if lead and first_bar[0].onset == 0 and lead < len(first_bar) and (
                (a_last is not None and a_last.pitch is not None and a_last.tie) or n_syl_b <= 3):
            b.spill = lead
            continue
        if b.is_instrumental or n_syl_b <= 3:
            continue
        last = a.bars[-1].melody
        k = _trailing_pickup(last, a.verse)
        if not k:
            continue
        # vor der Pause: endet dort ein Wort? (sonst ist es kein Auftakt)
        before = [n for n in last[:len(last) - k] if n.pitch is not None and n.syllable(a.verse)]
        if before and before[-1].syllable(a.verse)[1] in ('begin', 'middle'):
            continue
        # beginnt b zügig mit Gesang?
        first_sung = next((n for n in b.bars[0].melody if n.pitch is not None), None)
        if first_sung is None or first_sung.onset > 2:
            continue
        b.pickup = k


def syllables(sections: list[Section], i: int) -> list[Syl]:
    sec = sections[i]
    out: list[Syl] = []
    if sec.pickup and i > 0 and sections[i - 1].bars:
        prev = sections[i - 1]
        last = prev.bars[-1]
        notes = last.melody[len(last.melody) - sec.pickup:]
        base = len(last.melody) - sec.pickup
        for j, n in enumerate(notes):
            s = n.syllable(prev.verse)
            if n.pitch is not None and s:
                out.append(Syl(n.onset - last.length, -1, base + j, s[0], s[1], n))
    nxt = sections[i + 1] if i + 1 < len(sections) else None
    nxt_pickup = nxt.pickup if nxt else 0
    own_spill = getattr(sec, 'spill', 0)
    t0 = F(0)
    for bi, bar in enumerate(sec.bars):
        limit = len(bar.melody) - (nxt_pickup if bi == len(sec.bars) - 1 else 0)
        first = own_spill if bi == 0 else 0
        for ni, n in enumerate(bar.melody[:limit]):
            if ni < first:
                continue
            s = n.syllable(sec.verse)
            if n.pitch is not None and s:
                out.append(Syl(t0 + n.onset, bi, ni, s[0], s[1], n))
        t0 += bar.length
    # Überhang in den nächsten Abschnitt hinein
    if nxt is not None and getattr(nxt, 'spill', 0) and nxt.bars:
        for ni, n in enumerate(nxt.bars[0].melody[:nxt.spill]):
            s = n.syllable(sec.verse)
            if n.pitch is not None and s:
                out.append(Syl(t0 + n.onset, len(sec.bars), ni, s[0], s[1], n))
    _fill_ends(sections, i, out)
    return out


def _fill_ends(sections, i, syls):
    """Klingende Länge jeder Silbe inkl. Haltebögen bestimmen."""
    sec = sections[i]
    flat = []
    if i > 0 and sections[i - 1].bars:
        lb = sections[i - 1].bars[-1]
        flat += [(n.onset - lb.length, n) for n in lb.melody]
    t0 = F(0)
    for bar in sec.bars:
        flat += [(t0 + n.onset, n) for n in bar.melody]
        t0 += bar.length
    if i + 1 < len(sections) and sections[i + 1].bars:
        flat += [(t0 + n.onset, n) for n in sections[i + 1].bars[0].melody]
    pos = {id(n): k for k, (_, n) in enumerate(flat)}
    for sy in syls:
        k = pos.get(id(sy.note))
        end = sy.t + sy.note.dur
        if k is not None:
            while flat[k][1].tie and k + 1 < len(flat) and flat[k + 1][1].pitch is not None:
                k += 1
                end = flat[k][0] + flat[k][1].dur
        sy.end = end


def chords(sections: list[Section], i: int, dedupe: bool = True) -> list[ChordAt]:
    """Akkordwechsel eines Abschnitts in Abschnittszeit (Auftakt-Akkorde negativ)."""
    sec = sections[i]
    out: list[ChordAt] = []
    if sec.pickup and i > 0 and sections[i - 1].bars:
        last = sections[i - 1].bars[-1]
        start = last.melody[len(last.melody) - sec.pickup].onset
        for c in last.chords:
            if c.onset >= start - F(1, 2):
                out.append(ChordAt(c.onset - last.length, c.symbol))
    nxt = sections[i + 1] if i + 1 < len(sections) else None
    cut = None
    if nxt is not None and nxt.pickup and sec.bars:
        lb = sec.bars[-1]
        cut = lb.melody[len(lb.melody) - nxt.pickup].onset - F(1, 2)
    t0 = F(0)
    for bi, bar in enumerate(sec.bars):
        for c in bar.chords:
            if cut is not None and bi == len(sec.bars) - 1 and c.onset >= cut:
                continue
            out.append(ChordAt(t0 + c.onset, c.symbol))
        t0 += bar.length
    if dedupe:
        ded = []
        for c in out:
            if ded and ded[-1].symbol == c.symbol:
                continue
            ded.append(c)
        out = ded
    return out


def chord_before(sections: list[Section], i: int) -> Optional[str]:
    """Der zuletzt klingende Akkord vor Abschnitt i (für Abschnitte ohne Akkord am Anfang)."""
    for s in reversed(sections[:i]):
        for b in reversed(s.bars):
            if b.chords:
                return b.chords[-1].symbol
    return None


# ---------------------------------------------------------------- Zeilen ----

def _default_width(text: str) -> float:
    from .sheets.odt import text_width_cm
    return text_width_cm(text, 14)


def auto_lines(sections: list[Section], i: int, max_width: float = 17.2, width=None) -> None:
    """Zeilenumbrüche aus Melodie und Satzzeichen ableiten – so, dass die Zeilen auf ein
    A4-Chord-Sheet (Arial 14) passen und möglichst an Satz- bzw. Phrasenenden umbrechen."""
    width = width or _default_width
    sec = sections[i]
    syl = syllables(sections, i)
    if not syl:
        sec.lines, sec.line_starts = [], []
        return
    bar_len = sec.bars[0].length if sec.bars else F(4)
    grid = bar_len * 2

    def on_grid(t):
        r = t % grid
        return min(r, grid - r) <= F(5, 4)

    starts = [0]
    k = 1
    while k <= len(syl):
        cur_start = starts[-1]
        # Kandidaten: Wortenden ab cur_start
        best = None
        for e in range(cur_start + 1, len(syl) + 1):
            prev = syl[e - 1]
            if not prev.word_end and e < len(syl):
                continue
            w = width(join_syllables(syl[cur_start:e]))
            if w > max_width:
                break
            if e == len(syl):
                best = None
                break
            nxt = syl[e]
            gap = nxt.t - prev.end
            frac = w / max_width
            score = None
            punct = re.search(r'[.!?,;:–]["“”»]?$', prev.text)
            if punct and on_grid(nxt.t) and frac >= 0.5:
                best = (9, e)
                break
            sentence = re.search(r'[.!?]["“”»]?$', prev.text)
            if sentence and frac >= 0.55:
                score = 3 + frac
            elif sentence and frac >= 0.25 and gap >= F(1):
                score = 3.5 + frac
            elif re.search(r'[,;:–]$', prev.text) and frac >= 0.68:
                score = 2 + frac
            elif gap >= F(3, 2) and frac >= 0.5:
                score = 1.5 + frac
            elif gap >= F(1) and frac >= 0.8:
                score = 1 + frac
            if score is not None:
                best = (score, e) if best is None or score >= best[0] else best
                if score >= 3:
                    break
        else:
            best = None
            e = len(syl) + 1
        if best is None:
            # passt der Rest? sonst am letzten passenden Wortende umbrechen
            rest_w = width(join_syllables(syl[cur_start:]))
            if rest_w <= max_width:
                break
            last_fit = cur_start + 1
            for e2 in range(cur_start + 1, len(syl)):
                if syl[e2 - 1].word_end and width(join_syllables(syl[cur_start:e2])) <= max_width:
                    last_fit = e2
            starts.append(last_fit)
        else:
            starts.append(best[1])
        k = starts[-1] + 1
    # sehr kurze letzte Zeile an die vorige hängen, wenn es passt
    if len(starts) >= 2:
        last = join_syllables(syl[starts[-1]:])
        merged = join_syllables(syl[starts[-2]:])
        if len(last) <= 14 and width(merged) <= max_width:
            starts.pop()
    lines = []
    for j, st in enumerate(starts):
        en = starts[j + 1] if j + 1 < len(starts) else len(syl)
        lines.append(join_syllables(syl[st:en]))
    sec.lines, sec.line_starts = lines, starts


def join_syllables(syls: list[Syl]) -> str:
    out = ''
    for s in syls:
        t = s.text
        out += t + ('' if s.syllabic in ('begin', 'middle') else ' ')
    return re.sub(r'\s+', ' ', out).strip()


def line_groups(sections: list[Section], i: int) -> list[tuple[str, list[tuple[int, Syl]]]]:
    """Zeilen mit Zeichenposition jeder Silbe: [(Zeilentext, [(Zeichenindex, Silbe), ...]), ...]"""
    sec = sections[i]
    syl = syllables(sections, i)
    if not sec.lines or not sec.line_starts:
        return []
    groups = []
    for j, st in enumerate(sec.line_starts):
        en = sec.line_starts[j + 1] if j + 1 < len(sec.line_starts) else len(syl)
        line = sec.lines[j] if j < len(sec.lines) else join_syllables(syl[st:en])
        groups.append((line, _locate(line, syl[st:en])))
    return groups


def _norm(s: str) -> str:
    s = unicodedata.normalize('NFKD', s.lower())
    return ''.join(c for c in s if c.isalnum())


def _locate(line: str, syls: list[Syl]) -> list[tuple[int, Syl]]:
    """Findet jede Silbe in der (evtl. vom Nutzer eingefügten) Zeile – tolerant gegenüber
    Satzzeichen, Groß/Klein und abweichender Silbentrennung."""
    res = []
    pos = 0
    low = line.lower()
    for s in syls:
        key = re.sub(r'[^\w’\']', '', s.text.lower())
        found = -1
        if key:
            found = low.find(key, pos)
            if found - pos > 25:
                found = -1
        if found < 0:
            # nächsten Wortanfang nehmen
            m = re.compile(r'\w').search(line, pos)
            found = m.start() if m else len(line)
        res.append((found, s))
        pos = found + max(1, len(key))
    return res


def apply_text(sections: list[Section], i: int, text: str) -> None:
    """Eingefügten Liedtext (mit Zeilenumbrüchen) auf die Silben des Abschnitts legen.

    Bestimmt die Zeilenanfänge über Wortabgleich; Abweichungen werden toleriert."""
    sec = sections[i]
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    syl = syllables(sections, i)
    words = []  # (syl_index_of_word_start, normtext)
    cur = ''
    start = 0
    for k, s in enumerate(syl):
        if not cur:
            start = k
        cur += s.text
        if s.word_end:
            words.append((start, _norm(cur)))
            cur = ''
    if cur:
        words.append((start, _norm(cur)))
    starts = []
    wi = 0
    for line in lines:
        lw = [_norm(w) for w in re.findall(r"[\w’']+", line)]
        if not lw:
            continue
        # Anfangswort der Zeile in der Silbenfolge suchen (kleines Suchfenster)
        best = wi
        for cand in range(wi, min(len(words), wi + 6)):
            if words[cand][1] == lw[0] or words[cand][1].startswith(lw[0][:3]):
                best = cand
                break
        if best < len(words):
            starts.append(words[best][0])
        wi = best + len(lw)
    if not starts or starts[0] != 0:
        starts = [0] + [s for s in starts if s != 0]
    sec.lines = lines[:len(starts)]
    sec.line_starts = starts[:len(sec.lines)]
