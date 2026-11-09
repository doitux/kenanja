"""Gemeinde-Modus: Melodie so notieren, wie sie auf Liedblättern für Gemeindegesang steht.

Grundsätze (wie bei handgesetzten Leadsheets für die Gemeinde):
* jede Silbe genau eine Note – keine Verzierungen, kein Anschleifen, keine Melismen
* jede Textzeile belegt einen Bereich ganzer Takte (meist zwei); Fehler wandern nicht in die nächste Zeile
* Einsätze im Sechzehntel-Raster, bevorzugt auf Zählzeiten und Achteln, Sechzehntel-Vorzieher erlaubt
* gleiche Zeilen (Refrain-Wiederholungen, gleich gebaute Strophenhälften) werden gleich notiert –
  die Mehrheit der Durchgänge entscheidet über Ton und Rhythmus
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

import numpy as np

SUB = 4                 # Zellen je Schlag (Sechzehntel)


@dataclass
class Track:
    t: np.ndarray        # Framezeiten (s)
    midi: np.ndarray     # Tonhöhe (nan = stimmlos)
    onset: np.ndarray    # Einsatzstärke je Frame (0…1, örtlich normiert)
    hop: float


def pitch_track(y: np.ndarray, sr: int = 22050, hop: int = 256) -> Track:
    import librosa
    from scipy.ndimage import maximum_filter1d
    f0, vflag, _ = librosa.pyin(y, fmin=90, fmax=1000, sr=sr, frame_length=2048, hop_length=hop, fill_na=np.nan)
    midi = librosa.hz_to_midi(f0)
    midi[~vflag] = np.nan
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, lag=2, max_size=3, fmin=150, fmax=8000)
    n = min(len(midi), len(env))
    midi, env = midi[:n], env[:n]
    loc = np.maximum(maximum_filter1d(env, int(3.0 * sr / hop)), 1e-9)
    t = librosa.frames_to_time(np.arange(n), sr=sr, hop_length=hop)
    return Track(t, midi, np.clip(env / loc, 0, 1), hop / sr)


class Cells:
    """Sechzehntel-Zellen entlang der Schlagzeiten, mit Messwerten je Zelle."""

    def __init__(self, tr: Track, beats: np.ndarray, phase: int, bpb: int):
        b = np.asarray(beats, dtype=float)
        per = float(np.median(np.diff(b)))
        b = np.concatenate([b, b[-1] + per * np.arange(1, 9)])
        self.times = np.array([b[i] + (b[i + 1] - b[i]) * k / SUB for i in range(len(b) - 1) for k in range(SUB)]
                              + [b[-1]])
        self.phase, self.bpb = phase, bpb
        N = len(self.times) - 1
        self.N = N
        fi = np.searchsorted(tr.t, self.times)
        self.V = np.zeros(N)
        self.P = np.full(N, np.nan)
        self.O = np.zeros(N)
        win = max(1, int(0.045 / tr.hop))
        for c in range(N):
            a, e = fi[c], max(fi[c] + 1, fi[c + 1])
            seg = tr.midi[a:e]
            if len(seg):
                ok = ~np.isnan(seg)
                self.V[c] = ok.mean()
                if ok.any():
                    self.P[c] = float(np.median(seg[ok]))
            lo, hi = max(0, fi[c] - win), min(len(tr.onset), fi[c] + win + 1)
            if hi > lo:
                self.O[c] = float(tr.onset[lo:hi].max())
        self.tr = tr

    def t(self, c) -> float:
        """Zeit einer Zelle (Index wird auf den gültigen Bereich begrenzt)."""
        return float(self.times[int(min(max(0, c), self.N))])

    def bar_cell(self, bar: int) -> int:
        return (self.phase + bar * self.bpb) * SUB

    def n_bars(self) -> int:
        return max(0, (self.N // SUB - self.phase) // self.bpb)

    def metric(self, c: int) -> float:
        pos = (c - self.phase * SUB) % (SUB * self.bpb)
        if pos % SUB == 0:
            return 0.25 if pos == 0 else 0.15
        if pos % 2 == 0:
            return 0.0
        return -0.45      # Sechzehntel (Vorzieher sind erlaubt, aber nicht der Normalfall)


# --------------------------------------------------------------------------- Zeilen auf Takte legen ----

def _line_cost(cl: Cells, b0: int, L: int, s: int) -> float:
    cs, ce = cl.bar_cell(b0), cl.bar_cell(b0 + L)
    lo = max(0, cs - SUB)
    if ce > cl.N:
        return 1e9
    V = cl.V[lo:ce]
    voiced = float(V.sum())
    need = s * 2.2 + 2
    cost = abs(np.log((voiced + 1) / need)) * 2.5
    # Einsätze zählen (lokale Maxima der Einsatzstärke in gesungenen Zellen)
    O = cl.O[lo:ce] * (V > 0.3)
    peaks = int(np.sum((O[1:-1] > 0.35) & (O[1:-1] >= O[:-2]) & (O[1:-1] >= O[2:])))
    cost += abs(peaks - s) / max(1, s) * 1.2
    # Atempause am Zeilenende (irgendwo im letzten Schlag … Anfang des nächsten Takts)
    tail = cl.V[max(lo, ce - 2 * SUB):min(cl.N, ce + 2)]
    cost += float(tail.min()) * 2.0 if len(tail) else 0
    # vor dem Auftakt sollte nicht gesungen werden (sonst gehört das zur vorigen Zeile)
    pre = cl.V[max(0, lo - 2):lo + 1]
    cost += float(pre.mean()) * 1.0 if len(pre) else 0
    cost += {1: 0.5, 2: 0.0, 3: 0.9, 4: 0.4}.get(L, 2.0)
    # eine Zeile beginnt im ersten Takt ihres Bereichs (oder mit Auftakt davor), nicht erst im zweiten
    sung = np.nonzero(cl.V[lo:ce] > 0.5)[0]
    if len(sung):
        first = lo + int(sung[0]) - cs
        if first >= cl.bpb * SUB:
            cost += 2.0
        elif first >= cl.bpb * SUB * 3 // 4:
            cost += 0.8
    else:
        cost += 3.0
    return cost


def place_lines(cl: Cells, counts: list[int], blocks: Optional[list[int]] = None) -> list[Optional[tuple[int, int]]]:
    """Jeder Textzeile einen Taktbereich zuordnen. Die Zeilen eines Textblocks (Strophe, Refrain) liegen
    lückenlos hintereinander und sind gleich lang (1, 2 oder 4 Takte) – so sind Gemeindelieder gebaut.
    Zwischen den Blöcken darf Musik ohne Gesang liegen. blocks: Zeilenzahl je Block."""
    NB = cl.n_bars()
    blocks = blocks or [len(counts)]
    starts = np.cumsum([0] + blocks[:-1]).tolist()
    bar_voice = [float(cl.V[cl.bar_cell(b):cl.bar_cell(b + 1)].sum()) for b in range(NB)]
    cache = {}

    def lc(b0, L, s):
        k = (b0, L, s)
        if k not in cache:
            cache[k] = _line_cost(cl, b0, L, s)
        return cache[k]

    INF = 1e18
    NBK = len(blocks)
    D = np.full((NBK + 1, NB + 1), INF)
    back = {}
    D[0, 0] = 0.0
    for b in range(1, NB + 1):          # vor dem ersten Block: Intro, kostet nichts
        D[0, b] = D[0, b - 1]
        back[0, b] = ('skip', b - 1, 0)
    for k in range(1, NBK + 1):
        n = blocks[k - 1]
        cnt = counts[starts[k - 1]:starts[k - 1] + n]
        for b in range(1, NB + 1):
            # Takt ohne Text zwischen Blöcken (Zwischenspiel); gesungene Takte zu überspringen kostet
            best, arg = D[k, b - 1] + bar_voice[b - 1] * 0.25, ('skip', b - 1, 0)
            for L in (1, 2, 4):
                a = b - n * L
                if a < 0 or D[k - 1, a] >= INF:
                    continue
                if n and all(c == 0 for c in cnt):
                    continue
                c = D[k - 1, a] + sum(lc(a + i * L, L, cnt[i]) for i in range(n) if cnt[i])
                c += {1: 0.3, 2: 0.0, 4: 0.3}[L] * n
                if c < best:
                    best, arg = c, ('block', a, L)
            D[k, b], back[k, b] = best, arg
    b = int(np.argmin(D[NBK]))
    out: list[Optional[tuple[int, int]]] = [None] * len(counts)
    k = NBK
    while k > 0 and b > 0:
        kind, a, L = back[k, b]
        if kind == 'block':
            for i in range(blocks[k - 1]):
                out[starts[k - 1] + i] = (a + i * L, a + (i + 1) * L)
            k -= 1
        b = a
    return out


def place_lines_from_anchors(counts: list[int], blocks: list[int], anchors: list[Optional[float]],
                             n_bars: int) -> list[Optional[tuple[int, int]]]:
    """Zeilen eines Blocks liegen in festem Abstand (1, 2 oder 4 Takte). Aus den gemessenen Zeilenanfängen
    (Taktposition, Kommazahl) wird je Block das Raster gewählt, zu dem die meisten Anfänge passen –
    einzelne falsch erkannte Zeilen werden so ans Raster gezogen."""
    out: list[Optional[tuple[int, int]]] = [None] * len(counts)
    pos = 0
    floor_bar = 0
    for n in blocks:
        idx = list(range(pos, pos + n))
        pos += n
        an = [(k, anchors[i]) for k, i in enumerate(idx) if anchors[i] is not None and counts[i] > 0]
        if not an:
            continue
        best = None
        for L in (2, 1, 4):
            for k0, a0 in an:
                st = int(np.floor(a0 + 0.3)) - k0 * L
                res = [abs(a - (st + k * L) - 0.3) for k, a in an]
                inl = sum(1 for r in res if r <= 0.75)
                key = (inl, -sum(min(r, 1.5) for r in res), {2: 0.2, 1: 0.1, 4: 0}[L])
                if best is None or key > best[0]:
                    best = (key, st, L)
        _, st, L = best
        st = max(st, floor_bar)
        if st + n * L > n_bars:
            st = max(floor_bar, n_bars - n * L)
        for k, i in enumerate(idx):
            if counts[i] > 0:
                out[i] = (st + k * L, st + (k + 1) * L)
        floor_bar = st + n * L
    return out


def _ransac_start(an: list[tuple[int, float]], n: int) -> tuple[int, int, int]:
    """(Starttakt, Zeilenlänge, Zahl passender Anfänge) aus gemessenen Zeilenanfängen eines Blocks."""
    best = None
    for L in (2, 1, 4):
        for k0, a0 in an:
            st = int(np.floor(a0 + 0.3)) - k0 * L
            res = [abs(a - (st + k * L) - 0.3) for k, a in an]
            inl = sum(1 for r in res if r <= 0.75)
            key = (inl, -sum(min(r, 1.5) for r in res), {2: 0.2, 1: 0.1, 4: 0}[L])
            if best is None or key > best[0]:
                best = (key, st, L)
    return best[1], best[2], best[0][0]


def melody_match(cl: Cells, a0: int, b0: int, nbars: int) -> float:
    """Wie gut stimmt die Melodie ab Takt b0 mit der ab Takt a0 überein (Tonklassen, oktavunabhängig)."""
    ca, cb = cl.bar_cell(a0), cl.bar_cell(b0)
    n = nbars * cl.bpb * SUB
    if cb + n > cl.N or ca + n > cl.N or cb < 0:
        return -1.0
    Va, Vb = cl.V[ca:ca + n], cl.V[cb:cb + n]
    Pa, Pb = cl.P[ca:ca + n], cl.P[cb:cb + n]
    both = (Va > 0.5) & (Vb > 0.5) & ~np.isnan(Pa) & ~np.isnan(Pb)
    sung = (Va > 0.5).sum()
    if sung < 8:
        return -1.0
    d = np.abs(((Pa[both] - Pb[both] + 6) % 12) - 6)
    pc = float((d < 0.75).sum()) / sung
    return pc - 0.4 * float(np.abs(Va - Vb).mean())


def locate_blocks(cl: Cells, labels: list[Optional[str]], sizes: list[int], counts: list[int],
                  anchors: list[Optional[float]]) -> list[Optional[tuple[int, int, int]]]:
    """Lage jedes Textblocks: (Starttakt, Endtakt, Zeilenlänge). Erst der erste Block über die gemessenen
    Zeilenanfänge, dann Wiederholungen (gleiche Überschrift) über den Melodievergleich mit dem ersten
    Vorkommen, dann die übrigen Blöcke in den Lücken dazwischen."""
    NB = cl.n_bars()
    nb = len(sizes)
    starts = np.cumsum([0] + sizes[:-1]).tolist()
    place: list[Optional[tuple[int, int, int]]] = [None] * nb
    filled: set = set()

    def block_anchors(k):
        return [(i, anchors[starts[k] + i]) for i in range(sizes[k]) if anchors[starts[k] + i] is not None]

    def bounds(k):
        lo = max([place[j][1] for j in range(k) if place[j]] or [0])
        hi = min([place[j][0] for j in range(k + 1, nb) if place[j]] or [NB])
        # Platz für noch nicht gesetzte Blöcke davor/danach freihalten (mindestens 1 Takt je Zeile)
        lo += sum(sizes[j] for j in range(k) if not place[j] and j > max([i for i in range(k) if place[i]] or [-1]))
        hi -= sum(sizes[j] for j in range(k + 1, nb) if not place[j]
                  and j < min([i for i in range(k + 1, nb) if place[i]] or [nb]))
        return lo, hi

    def fill(k):
        lo, hi = bounds(k)
        # nur Anfänge, die überhaupt in die Lücke passen (die grobe Zuordnung kann ganze Blöcke verfehlen)
        an = [(i, a) for i, a in block_anchors(k) if lo - 1 <= a <= hi + 1]
        best = None
        for L in (2, 1, 4):
            span = sizes[k] * L
            for x in range(lo, max(lo, hi - span) + 1):
                if x + span > NB:
                    break
                c = sum(_line_cost(cl, x + i * L, L, counts[starts[k] + i]) for i in range(sizes[k])
                        if counts[starts[k] + i])
                c += {2: 0.0, 1: 0.3, 4: 0.3}[L] * sizes[k]
                # Nähe zu den gemessenen Zeilenanfängen
                if an:
                    c += 0.4 * float(np.mean([min(3.0, abs(a - (x + i * L) - 0.3)) for i, a in an]))
                if best is None or c < best[0]:
                    best = (c, x, L)
        if best:
            _, x, L = best
            place[k] = (x, x + sizes[k] * L, L)
            filled.add(k)

    # 1. erster Block mit Text: gemessene Anfänge
    first = next((k for k in range(nb) if any(counts[starts[k] + i] for i in range(sizes[k]))), None)
    if first is None:
        return place
    an = block_anchors(first)
    if an:
        st, L, inl = _ransac_start(an, sizes[first])
        st = max(0, min(st, NB - sizes[first] * L))
        place[first] = (st, st + sizes[first] * L, L)
    else:
        fill(first)
    # 2./3. abwechselnd Wiederholungen suchen und Lücken füllen
    for _ in range(3 * nb):
        progress = False
        for k in range(nb):
            if place[k] or not labels[k]:
                continue
            tmpl = next((j for j in range(nb) if place[j] and labels[j] == labels[k]
                         and starts[j] < starts[k]), None)
            if tmpl is None:
                continue
            ts, te, L = place[tmpl]
            tl = min(te - ts, sizes[k] * L)
            lo, hi = bounds(k)
            span = sizes[k] * L
            best = None
            for x in range(lo, max(lo, hi - span) + 1):
                m = melody_match(cl, ts, x, tl)
                if best is None or m > best[0]:
                    best = (m, x)
            if best and best[0] > 0.25:
                place[k] = (best[1], best[1] + span, L)
                progress = True
        for k in range(nb):
            if place[k]:
                continue
            left = any(place[j] for j in range(k)) or k == 0
            right = any(place[j] for j in range(k + 1, nb)) or k == nb - 1
            if left and right:
                fill(k)
                progress = True
                break
        if all(place[k] or not any(counts[starts[k] + i] for i in range(sizes[k])) for k in range(nb)):
            break
        if not progress:
            for k in range(nb):
                if not place[k]:
                    fill(k)
                    break
    # Zuordnungsfenster: ein über die Lücke gesetzter Block bekommt die ganze Lücke bis zum nächsten Block
    windows = []
    for k in range(nb):
        if not place[k]:
            windows.append(None)
            continue
        st, en, L = place[k]
        if k in filled:
            st = max([place[j][1] for j in range(k) if place[j]] or [max(0, st - 1)])
            en = min([place[j][0] for j in range(k + 1, nb) if place[j]] or [min(NB, en + 2)])
        windows.append((st, en, L))
    return windows


# --------------------------------------------------------------------------- Silben in der Zeile ----

_GAP = {1: -0.7, 2: 0.0, 3: -0.15, 4: 0.0, 6: -0.1, 8: -0.05}


def place_syllables(cl: Cells, b0: int, b1: int, s: int) -> list[int]:
    """Silbeneinsätze (Zellen) in der Zeile [Takt b0, b1) – genau s Stück."""
    cs = max(0, cl.bar_cell(b0) - SUB)
    ce = min(cl.N, cl.bar_cell(b1))
    C = ce - cs
    if C < s:
        return [cs + int(k * C / max(1, s)) for k in range(s)]
    V, O, P = cl.V[cs:ce], cl.O[cs:ce], cl.P[cs:ce]
    Vn = np.array([V[c:c + 2].mean() for c in range(C)])
    dp = np.zeros(C)
    for c in range(1, C):
        if not np.isnan(P[c]) and not np.isnan(P[c - 1]) and abs(P[c] - P[c - 1]) >= 0.7:
            dp[c] = 1.0
    # Einsatz direkt nach einer Pause (Stimme setzt ein)
    rise = np.array([1.0 if Vn[c] > 0.5 and (c == 0 or V[c - 1] < 0.3) else 0.0 for c in range(C)])
    score = (1.6 * O + 0.6 * dp + 1.2 * rise + np.array([cl.metric(cs + c) for c in range(C)])
             - 3.0 * (Vn < 0.25))
    gap = np.array([_GAP.get(g, -0.25 - 0.04 * g) for g in range(C + 1)])
    gap[0] = -1e9
    cumV = np.r_[0, np.cumsum(V)]
    NEG = -1e18
    best = np.full((s, C), NEG)
    arg = np.zeros((s, C), dtype=int)
    best[0] = score - 0.6 * cumV[:C]          # gesungene Zellen vor der ersten Silbe: ausgelassener Einsatz
    for k in range(1, s):
        for c in range(k, C):
            prev = best[k - 1, :c] + gap[c - np.arange(c)]
            j = int(np.argmax(prev))
            best[k, c] = prev[j] + score[c]
            arg[k, c] = j
    # nach der letzten Silbe: gesungene Zellen bis zur nächsten Pause gehören zu ihr – gut
    c = int(np.argmax(best[s - 1]))
    pos = [c]
    for k in range(s - 1, 0, -1):
        c = int(arg[k, c])
        pos.append(c)
    return [cs + p for p in pos[::-1]]


def syllable_pitch(cl: Cells, c0: int, c1: int) -> Optional[float]:
    """Ton einer Silbe: Median des stabilen Teils (Anschliff am Anfang wird übersprungen)."""
    tr = cl.tr
    t0, t1 = cl.t(c0), cl.t(c1)
    a = t0 + 0.35 * (t1 - t0)
    i0, i1 = np.searchsorted(tr.t, [a, t1])
    seg = tr.midi[i0:i1]
    seg = seg[~np.isnan(seg)]
    if len(seg) < 3:
        i0, i1 = np.searchsorted(tr.t, [t0, t1])
        seg = tr.midi[i0:i1]
        seg = seg[~np.isnan(seg)]
    if len(seg) == 0:
        return None
    return float(np.median(seg))


def voice_end(cl: Cells, c: int, limit: int) -> int:
    """Erste Zelle ab c, in der die Stimme verstummt (höchstens bis limit)."""
    e = c + 1
    while e < limit and cl.V[e] > 0.35:
        e += 1
    return e


def assign_syllables_sung(syls: list, notes: list, split) -> list:
    """Silben auf Noten verteilen wie gesungen: Ein Anschliff (kurzer Ton knapp neben dem nächsten) beginnt die
    Silbe, der Zielton danach gehört dazu; ein gleich hoher Ton direkt danach ebenso."""
    notes = list(notes)
    while len(notes) < len(syls) and notes:
        k = max(range(len(notes)), key=lambda j: notes[j].end - notes[j].start)
        n = notes[k]
        notes[k:k + 1] = split(n)
    extra = len(notes) - len(syls)
    starts = set(range(len(notes)))
    if extra > 0:
        scored = []
        for j in range(1, len(notes)):
            p, n = notes[j - 1], notes[j]
            gap = n.start - p.end
            dur = n.end - n.start
            sc = gap * 4 + dur
            if gap < 0.06 and (p.end - p.start) < 0.22 and 1 <= abs(n.pitch - p.pitch) <= 4:
                sc -= 1.0          # Zielton nach einem Anschliff
            if gap < 0.06 and n.pitch == p.pitch:
                sc -= 0.5          # gleich hoch weitergesungen
            scored.append((sc, j))
        for _, j in sorted(scored)[:extra]:
            starts.discard(j)
    out, si = [], 0
    for j, n in enumerate(notes):
        if j in starts and si < len(syls):
            out.append((n, syls[si]))
            si += 1
        else:
            out.append((n, None))
    return out


def line_from_notes(cl: Cells, notes: list, syls: list, assign) -> Optional[list['LNote']]:
    """Silbennoten aus erkannten Noten einer Zeile: jede Silbe bekommt den Einsatz ihrer ersten Note und den Ton
    ihrer längsten (stabilsten) Note; Zusatznoten ohne Silbe (Verzierungen, Melismen) gehen darin auf."""
    if not notes or not syls:
        return None
    pairs = assign(syls, notes)
    groups: list[list] = []
    for n, sy in pairs:
        if sy is not None or not groups:
            groups.append([n])
        else:
            groups[-1].append(n)
    groups = groups[:len(syls)]
    cell_of = lambda t: int(np.argmin(np.abs(cl.times - t)))
    out = []
    for g in groups:
        main = max(g, key=lambda n: n.end - n.start)
        c0 = cell_of(g[0].start)
        c1 = max(c0 + 1, cell_of(g[-1].end))
        fp = main.fpitch or float(main.pitch)
        out.append(LNote(c0, c1, fp, int(main.pitch)))
    for k in range(len(out) - 1):
        if out[k + 1].cell <= out[k].cell:
            out[k + 1].cell = out[k].cell + 1
        out[k].end = min(max(out[k].end, out[k].cell + 1), out[k + 1].cell)
        if out[k + 1].cell - out[k].end <= 1:      # winzige Lücke: Note bis zur nächsten Silbe halten
            out[k].end = out[k + 1].cell
    while len(out) < len(syls):                     # zu wenige Noten: letzte Silben auf die Schlusszelle
        last = out[-1]
        out.append(LNote(last.end, last.end + 2, last.fpitch, last.pitch))
    return out


# --------------------------------------------------------------------------- gleiche Zeilen angleichen ----

@dataclass
class LNote:
    cell: int
    end: int
    fpitch: Optional[float]
    pitch: Optional[int] = None


def unify_repeats(lines: list[Optional[list[LNote]]], anchors: list[Optional[int]],
                  texts: Optional[list[str]] = None) -> int:
    """Zeilen mit gleicher Silbenzahl und ähnlichem Verlauf bilden eine Gruppe; je Silbe entscheidet die
    Mehrheit über Ton und Lage im Takt. Liefert die Zahl angeglichener Silben."""
    idx = [i for i, ln in enumerate(lines) if ln]
    parent = {i: i for i in idx}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def rel(i):
        return [n.cell - anchors[i] for n in lines[i]]

    norm = (lambda t: re.sub(r'[^\w]+', ' ', t.lower()).strip()) if texts else None
    for x in range(len(idx)):
        for y in range(x + 1, len(idx)):
            i, j = idx[x], idx[y]
            A, B = lines[i], lines[j]
            if len(A) != len(B) or len(A) < 3:
                continue
            if texts and norm(texts[i]) == norm(texts[j]):     # gleicher Text = gleiche Melodie
                parent[find(i)] = find(j)
                continue
            same_p = np.mean([a.pitch is not None and b.pitch is not None and a.pitch % 12 == b.pitch % 12
                              for a, b in zip(A, B)])
            same_r = np.mean([abs(p - q) <= 1 for p, q in zip(rel(i), rel(j))])
            if same_p >= 0.7 and same_r >= 0.6:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in idx:
        groups.setdefault(find(i), []).append(i)
    changed = 0
    for g in groups.values():
        if len(g) < 2:
            continue
        s = len(lines[g[0]])
        for k in range(s):
            ps = [lines[i][k].pitch for i in g if lines[i][k].pitch is not None]
            rs = [lines[i][k].cell - anchors[i] for i in g]
            es = [lines[i][k].end - lines[i][k].cell for i in g]
            if not ps:
                continue
            # nur bei echter Mehrheit angleichen (mehr als die Hälfte der Durchgänge einig) – sonst bleibt jeder,
            # wie er ist (bei zwei uneinigen Durchgängen weiß man nicht, wer recht hat)
            half = len(g) / 2
            cnt = Counter(p % 12 for p in ps).most_common()
            pc_best, pc_n = cnt[0]
            octs = Counter(p for p in ps if p % 12 == pc_best).most_common()
            p_best = octs[0][0] if pc_n > half else None
            rc = Counter(rs).most_common(1)[0]
            r_best = rc[0] if rc[1] > half else None
            ec = Counter(es).most_common(1)[0]
            e_best = ec[0] if ec[1] > half else None
            for i in g:
                n = lines[i][k]
                if p_best is not None and n.pitch != p_best:
                    n.pitch = p_best
                    changed += 1
                if r_best is not None and n.cell - anchors[i] != r_best:
                    n.cell = anchors[i] + r_best
                    changed += 1
                if e_best is not None:
                    n.end = n.cell + max(1, e_best)
                n.end = max(n.end, n.cell + 1)
        # Überlappungen nach dem Angleichen auflösen
        for i in g:
            ln = lines[i]
            for k in range(len(ln) - 1):
                if ln[k].end > ln[k + 1].cell:
                    ln[k].end = ln[k + 1].cell
                if ln[k + 1].cell <= ln[k].cell:
                    ln[k + 1].cell = ln[k].cell + 1
                    ln[k + 1].end = max(ln[k + 1].end, ln[k + 1].cell + 1)
    return changed


# --------------------------------------------------------------------------- Gesamtablauf ----

def transcribe(y22: np.ndarray, beats: np.ndarray, phase: int, bpb: int, counts: list[int],
               snap=None, tr: Optional[Track] = None, blocks: Optional[list[int]] = None,
               anchors: Optional[list[Optional[float]]] = None, vnotes: Optional[list] = None,
               line_syls: Optional[list] = None, assign=None, align=None, labels: Optional[list] = None,
               texts: Optional[list[str]] = None, old_ranges: Optional[list] = None):
    """-> (Liste je Zeile: [(start_s, end_s, midi, fpitch)] oder None, Track, Cells, Zahl angeglichener Silben)"""
    tr = tr or pitch_track(y22)
    cl = Cells(tr, beats, phase, bpb)
    block_place = None
    regular = True
    if anchors is not None and blocks:
        # Passt der Text erkennbar auf ein regelmäßiges Taktraster? (erster Block mit gemessenen Anfängen)
        pos = 0
        for n in blocks:
            an = [(i, anchors[pos + i]) for i in range(n) if anchors[pos + i] is not None and counts[pos + i]]
            if len(an) >= 3:
                _, _, inl = _ransac_start(an, n)
                regular = inl >= 0.7 * len(an)
                break
            pos += n
    if not regular and old_ranges is not None:
        spans = [None] * len(counts)
        line_notes_free = {}
        for i, rg in enumerate(old_ranges):
            if rg and vnotes is not None:
                ns = [v for v in vnotes if rg[0] - 0.03 <= v.start < rg[1] + 0.03]
                if ns:
                    line_notes_free[i] = ns
                    b = max(0, (int(np.argmin(np.abs(cl.times - ns[0].start))) + SUB - cl.phase * SUB) // (SUB * cl.bpb))
                    spans[i] = (b, b + 2)
    elif anchors is not None and labels is not None:
        block_place = locate_blocks(cl, labels, blocks or [len(counts)], counts, anchors)
        spans = [None] * len(counts)
        pos = 0
        for k, n in enumerate(blocks or [len(counts)]):
            if block_place[k]:
                st, en, L = block_place[k]
                for i in range(n):
                    spans[pos + i] = (st + i * L, st + (i + 1) * L)
            pos += n
    elif anchors is not None:
        spans = place_lines_from_anchors(counts, blocks or [len(counts)], anchors, cl.n_bars())
    else:
        spans = place_lines(cl, counts, blocks)
    # innerhalb eines Blocks: Zeilen über die erkannten Noten zuordnen (Zeilen dürfen unterschiedlich lang sein)
    line_notes: dict[int, list] = {}
    if not regular and old_ranges is not None:
        line_notes = line_notes_free
    elif block_place is not None and vnotes is not None and align is not None:
        pos = 0
        for k, n in enumerate(blocks):
            if block_place[k]:
                st, en, L = block_place[k]
                ta = cl.t(cl.bar_cell(st) - SUB) - 0.05
                tb = cl.t(cl.bar_cell(en) - 1)
                win = [v for v in vnotes if ta <= v.start < tb]
                rg = align(counts[pos:pos + n], win) if win else [None] * n
                for i, r in enumerate(rg):
                    if r and r[1] > r[0]:
                        line_notes[pos + i] = win[r[0]:r[1]]
            pos += n
    lines: list[Optional[list[LNote]]] = []
    anchors: list[Optional[int]] = []
    for li, sp in enumerate(spans):
        if sp is None or counts[li] == 0:
            lines.append(None)
            anchors.append(None)
            continue
        b0, b1 = sp
        if vnotes is not None and line_syls is not None:
            if li in line_notes:
                notes_li = line_notes[li]
                first = notes_li[0].start
                b0 = max(0, (int(np.argmin(np.abs(cl.times - first))) + SUB - cl.phase * SUB) // (SUB * cl.bpb))
            else:
                ta = cl.t(cl.bar_cell(b0) - SUB) - 0.05
                tb = cl.t(cl.bar_cell(b1) - 1)
                notes_li = [n for n in vnotes if ta <= n.start < tb]
            ln = line_from_notes(cl, notes_li, line_syls[li], assign)
            if ln:
                lines.append(ln)
                anchors.append(cl.bar_cell(b0))
                continue
        pos = place_syllables(cl, b0, b1, counts[li])
        limit = min(cl.N, cl.bar_cell(b1) + SUB)
        ln = []
        for k, c in enumerate(pos):
            nxt = pos[k + 1] if k + 1 < len(pos) else None
            if nxt is not None:
                e = voice_end(cl, c, nxt)
                e = nxt if nxt - e <= 1 else e        # kurze Lücke: bis zur nächsten Silbe halten
            else:
                e = voice_end(cl, c, limit)
                beat = SUB
                e = max(c + 1, int(round(e / (beat / 2))) * (beat // 2))   # Zeilenende auf Achtel
            fp = syllable_pitch(cl, c, nxt if nxt is not None else e)
            ln.append(LNote(c, max(c + 1, e), fp, int(round(fp)) if fp is not None else None))
        lines.append(ln)
        anchors.append(cl.bar_cell(b0))
    # Tonart-Rundung vor dem Angleichen
    if snap is not None:
        for ln in lines:
            if ln:
                snap(ln)
    # fehlende Töne aus dem Nachbarn füllen
    for ln in lines:
        if not ln:
            continue
        for k, n in enumerate(ln):
            if n.pitch is None:
                nb = [m.pitch for m in ln if m.pitch is not None]
                n.pitch = nb[0] if nb else 60
    changed = unify_repeats(lines, anchors, texts)
    out = []
    for ln in lines:
        if not ln:
            out.append(None)
            continue
        out.append([(cl.t(n.cell), cl.t(n.end), int(n.pitch),
                     float(n.fpitch) if n.fpitch is not None else float(n.pitch)) for n in ln])
    return out, tr, cl, changed, ('Taktraster' if regular else 'frei')
