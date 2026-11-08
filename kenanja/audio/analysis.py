"""Audioanalyse: aus einem MP3 einen Song-Entwurf machen.

Schritte (jeder einzeln nutzbar und zwischengespeichert):
  1. Laden (ffmpeg)                       5. Melodie aus der Gesangsspur (pYIN)
  2. Gesang/Begleitung trennen (MDX)      6. Text: eingefügter Liedtext -> Silben -> Noten
  3. Schläge, Tempo, Taktanfänge          7. Formteile (aus Textblöcken bzw. Wiederholungen)
  4. Akkorde (BTC) + Basston (Slash)      8. Song-Modell bauen (Takte, Akkorde, Melodie, Text)
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction as F
from pathlib import Path
from typing import Optional

import numpy as np

from ..model import Bar, ChordEvent, FormItem, Note, Section, Song
from ..theory import ChordSymbol, key_name, spell_pitch, tpc_pc, best_key_for_shift

SR = 44100


# ------------------------------------------------------------------ Laden ----

def load_audio(path, sr: int = SR, mono: bool = False) -> np.ndarray:
    ch = 1 if mono else 2
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'f32le', '-ac', str(ch), '-ar', str(sr), '-'],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, dtype=np.float32)
    return a.copy() if mono else a.reshape(-1, 2).T.copy()


def file_hash(path) -> str:
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()[:16]


def work_dir(path) -> Path:
    from .btc import cache_dir
    d = cache_dir().parent / 'analyse' / file_hash(path)
    d.mkdir(parents=True, exist_ok=True)
    return d


def stems(path, progress=None) -> tuple[np.ndarray, np.ndarray]:
    """Gesang/Begleitung (zwischengespeichert als FLAC)."""
    import soundfile as sf
    wd = work_dir(path)
    v, i = wd / 'gesang.flac', wd / 'begleitung.flac'
    if v.exists() and i.exists():
        return sf.read(v, dtype='float32')[0].T, sf.read(i, dtype='float32')[0].T
    from .separate import separate
    mix = load_audio(path)
    voc, inst = separate(mix, progress)
    sf.write(v, voc.T, SR)
    sf.write(i, inst.T, SR)
    return voc, inst


# ------------------------------------------------------------------ Schläge ----

def track_beats(y_mono: np.ndarray, sr: int = 22050, tempo_hint: Optional[float] = None) -> tuple[np.ndarray, float]:
    """Schlagzeiten (Sekunden) und Tempo. Gleichmäßiges Raster mit lokaler Feinanpassung."""
    import librosa
    oenv = librosa.onset.onset_strength(y=y_mono, sr=sr, hop_length=512, aggregate=np.median)
    tempo = tempo_hint or float(np.atleast_1d(librosa.feature.tempo(onset_envelope=oenv, sr=sr, hop_length=512,
                                                                    start_bpm=90, prior=None))[0])
    # Worship-Pop: 60–150 BPM bevorzugen
    while tempo < 58:
        tempo *= 2
    while tempo > 150:
        tempo /= 2
    _, beats = librosa.beat.beat_track(onset_envelope=oenv, sr=sr, hop_length=512, bpm=tempo, tightness=400,
                                       trim=False, units='frames')
    bt = librosa.frames_to_time(beats, sr=sr, hop_length=512)
    if len(bt) > 8:
        tempo = 60.0 / float(np.median(np.diff(bt)))
    # Lücken am Anfang/Ende mit dem Raster auffüllen
    period = 60.0 / tempo
    dur = len(y_mono) / sr
    while bt[0] - period > -0.05:
        bt = np.insert(bt, 0, bt[0] - period)
    while bt[-1] + period < dur:
        bt = np.append(bt, bt[-1] + period)
    return bt, tempo


# ------------------------------------------------------------------ Akkorde ----

BTC_QUAL = {'maj': '', 'min': 'm', 'sus2': '2', 'sus4': 'sus4', 'maj7': 'maj7', 'min7': 'm7', '7': '7',
            'maj6': '6', 'min6': 'm6', 'dim': 'dim', 'aug': 'aug', 'hdim7': 'm7b5', 'dim7': 'dim7',
            'minmaj7': 'mmaj7'}
ROOT_PC = {'C': 0, 'C#': 1, 'D': 2, 'D#': 3, 'E': 4, 'F': 5, 'F#': 6, 'G': 7, 'G#': 8, 'A': 9, 'A#': 10, 'B': 11}


def btc_label_to_parts(lbl: str):
    if lbl in ('N', 'X'):
        return None
    root, _, q = lbl.partition(':')
    return ROOT_PC[root], BTC_QUAL.get(q or 'maj', '')


def chord_probs(y22: np.ndarray) -> tuple[np.ndarray, float]:
    from .btc import BTC
    return BTC().predict(y22)


def beat_chords(probs: np.ndarray, fps: float, beats: np.ndarray, switch_penalty: float = 2.2) -> list[int]:
    """Ein Akkord pro Schlag, geglättet (Viterbi mit Wechselstrafe)."""
    n = len(beats)
    B = np.zeros((n, probs.shape[1]))
    for i in range(n):
        a = beats[i]
        b = beats[i + 1] if i + 1 < n else a + (beats[-1] - beats[-2] if n > 1 else 0.5)
        fa, fb = int(a / fps), max(int(a / fps) + 1, int(b / fps))
        seg = probs[fa:fb] if fa < len(probs) else probs[-1:]
        B[i] = np.log(np.clip(seg.mean(0), 1e-6, 1))
    K = B.shape[1]
    D = B[0].copy()
    back = np.zeros((n, K), dtype=np.int32)
    for i in range(1, n):
        best_prev = D.argmax()
        stay = D
        jump = D[best_prev] - switch_penalty
        choose_stay = stay >= jump
        back[i] = np.where(choose_stay, np.arange(K), best_prev)
        D = np.where(choose_stay, stay, jump) + B[i]
    path = [int(D.argmax())]
    for i in range(n - 1, 0, -1):
        path.append(int(back[i][path[-1]]))
    return path[::-1]


def bass_per_beat(y_inst22: np.ndarray, beats: np.ndarray, sr: int = 22050) -> list[Optional[int]]:
    """Tiefster deutlicher Ton (Tonklasse) pro Schlag – für Slash-Akkorde (G/F♯, D/F♯ …)."""
    import librosa
    C = np.abs(librosa.cqt(y_inst22, sr=sr, hop_length=512, fmin=librosa.note_to_hz('E1'), n_bins=36,
                           bins_per_octave=12))
    t = librosa.frames_to_time(np.arange(C.shape[1]), sr=sr, hop_length=512)
    res = []
    for i in range(len(beats)):
        a = beats[i]
        b = beats[i + 1] if i + 1 < len(beats) else a + 0.5
        m = (t >= a) & (t < b)
        if not m.any():
            res.append(None)
            continue
        e = C[:, m].mean(1)
        chroma = np.zeros(12)
        for k, v in enumerate(e):
            chroma[(k + 4) % 12] += v * (1.0 if k < 24 else 0.5)
        if chroma.max() <= 0 or chroma.max() < 1.6 * np.median(chroma):
            res.append(None)
        else:
            res.append(int(chroma.argmax()))
    return res


def estimate_key(beat_labels: list[str]) -> tuple[int, bool]:
    """Tonart aus der Akkordstatistik (Dur bevorzugt, wie in Lobpreisliedern üblich)."""
    major = [0, 2, 4, 5, 7, 9, 11]
    score = np.zeros(12)
    for lbl in beat_labels:
        p = btc_label_to_parts(lbl)
        if not p:
            continue
        r, q = p
        for k in range(12):
            deg = (r - k) % 12
            if deg in (0, 5, 7) and q in ('', '2', 'sus4', '6', 'maj7', '7'):
                score[k] += 2 if deg == 0 else 1.2
            elif deg in (2, 4, 9) and q in ('m', 'm7'):
                score[k] += 1.0
            elif deg in major:
                score[k] += 0.2
            else:
                score[k] -= 0.8
    k = int(score.argmax())
    tpc = best_key_for_shift(14, k)
    # Moll, wenn der erste/letzte Akkord die Moll-Parallele ist
    ends = [btc_label_to_parts(l) for l in (beat_labels[:8] + beat_labels[-8:]) if btc_label_to_parts(l)]
    rel_minor = (k + 9) % 12
    minor = sum(1 for r, q in ends if r == rel_minor and q.startswith('m')) > len(ends) * 0.5
    return tpc, minor


def detect_meter(beat_chords_idx: list[int], bass: list) -> tuple[int, int, dict]:
    """3/4 oder 4/4? Anteil der Akkordwechsel, die auf die Eins fallen (bei 4/4 auch die Drei)."""
    n = len(beat_chords_idx)
    changes = [i for i in range(1, n) if beat_chords_idx[i] != beat_chords_idx[i - 1]]
    res = {}
    for bpb in (3, 4):
        best = (0, -1.0)
        for ph in range(bpb):
            on1 = sum(1 for i in changes if (i - ph) % bpb == 0)
            on3 = sum(1 for i in changes if bpb == 4 and (i - ph) % bpb == 2)
            ratio = (on1 + 0.5 * on3) / max(1, len(changes))
            if ratio > best[1]:
                best = (ph, ratio)
        res[bpb] = best
    # Abstände zwischen Akkordwechseln: 3er-Takt -> Vielfache von 3 (3, 6, 12); 4er -> 2, 4, 8
    iv = np.diff(changes) if len(changes) > 2 else np.array([])
    s3 = float(np.sum(iv % 3 == 0))
    s4 = float(np.sum(iv % 4 == 0) + 0.5 * np.sum(iv == 2))
    res['abstand'] = (s3, s4)
    bpb = 3 if (s3 > 1.3 * s4 or res[3][1] > res[4][1] + 0.12) else 4
    phase = find_downbeat_phase(beat_chords_idx, bass, bpb)
    return bpb, phase, {k: (round(v[1], 2) if k != 'abstand' else v) for k, v in res.items()}


def find_downbeat_phase(beat_chords_idx: list[int], bass: list, beats_per_bar: int = 4) -> int:
    """Taktanfang finden: Akkordwechsel fallen bevorzugt auf die Eins (und die Drei)."""
    n = len(beat_chords_idx)
    best, best_s = 0, -1e9
    for ph in range(beats_per_bar):
        s = 0.0
        for i in range(1, n):
            pos = (i - ph) % beats_per_bar
            ch = beat_chords_idx[i] != beat_chords_idx[i - 1]
            if ch:
                s += {0: 3.0, 2: 1.0}.get(pos, -1.0) if beats_per_bar == 4 else (3.0 if pos == 0 else -0.5)
            if bass[i] is not None and i and bass[i] != bass[i - 1]:
                s += 0.5 if pos == 0 else 0.0
        if s > best_s:
            best, best_s = ph, s
    return best


# ------------------------------------------------------------------ Melodie ----

@dataclass
class VNote:
    start: float
    end: float
    pitch: int
    strength: float = 1.0
    fpitch: float = 0.0     # gemessene Tonhöhe (Bruchteil, stabiler Teil der Note)


def snap_to_key(notes: list['VNote'], key_tpc: int, minor: bool = False) -> None:
    """Gerundete Tonhöhe: bei knappen Fällen den leitereigenen Ton bevorzugen."""
    root = tpc_pc(key_tpc)
    scale = {(root + i) % 12 for i in (0, 2, 4, 5, 7, 9, 11)}
    for n in notes:
        fp = n.fpitch or float(n.pitch)
        lo, hi = int(np.floor(fp)), int(np.ceil(fp))
        r = int(round(fp))
        if r % 12 in scale:
            n.pitch = r
            continue
        other = hi if r == lo else lo
        if other % 12 in scale and abs(fp - other) < 0.8:
            n.pitch = other
        else:
            n.pitch = r


def vocal_notes(voc_mono22: np.ndarray, sr: int = 22050, min_dur: float = 0.09) -> list[VNote]:
    """Gesangsnoten aus der Gesangsspur: Tonhöhenverlauf (pYIN) + Einsätze -> Noten."""
    import librosa
    hop = 256
    f0, voiced, prob = librosa.pyin(voc_mono22, fmin=75, fmax=1000, sr=sr, frame_length=2048, hop_length=hop,
                                    fill_na=np.nan)
    rms = librosa.feature.rms(y=voc_mono22, frame_length=2048, hop_length=hop)[0]
    thr = max(0.012, np.percentile(rms, 30))
    t = librosa.frames_to_time(np.arange(len(f0)), sr=sr, hop_length=hop)
    midi = librosa.hz_to_midi(f0)
    onset_frames = set(librosa.onset.onset_detect(y=voc_mono22, sr=sr, hop_length=hop, backtrack=True,
                                                  units='frames', delta=0.08))
    notes: list[VNote] = []
    cur, cur_start, cur_vals = None, 0, []

    def close(i):
        nonlocal cur, cur_vals
        if cur is not None and cur_vals:
            st, en = t[cur_start], t[min(i, len(t) - 1)]
            if en - st >= min_dur:
                # Tonhöhe aus dem stabilen Teil (Anschleifen am Anfang ignorieren)
                k = len(cur_vals)
                stable = cur_vals[k // 3:] if k >= 6 else cur_vals
                fp = float(np.median(stable))
                notes.append(VNote(st, en, int(round(fp)), float(np.mean(rms[cur_start:i])), fp))
        cur, cur_vals = None, []

    for i in range(len(f0)):
        ok = voiced[i] and not np.isnan(midi[i]) and rms[i] >= thr * 0.6
        if not ok:
            close(i)
            continue
        m = midi[i]
        if cur is None:
            cur, cur_start, cur_vals = m, i, [m]
            continue
        med = float(np.median(cur_vals[-6:]))
        new_note = abs(m - med) > 0.8 and (len(cur_vals) >= 4)
        if i in onset_frames and len(cur_vals) >= 4:
            new_note = True
        if new_note:
            # nur trennen, wenn die neue Tonhöhe ein paar Frames hält
            ahead = midi[i:i + 4]
            if np.all(np.isfinite(ahead)) and (np.all(np.abs(ahead - m) < 0.6) or i in onset_frames):
                close(i)
                cur, cur_start, cur_vals = m, i, [m]
                continue
        cur_vals.append(m)
    close(len(f0))
    # Leise Phrasen sind meist Übersprechen anderer Instrumente (z. B. Geige) -> verwerfen
    if notes:
        med = float(np.median([n.strength for n in notes]))
        keep = []
        for a, b in phrases(notes):
            ph_str = float(np.median([n.strength for n in notes[a:b]]))
            if ph_str >= 0.35 * med:
                keep += notes[a:b]
        notes = keep
    # Oktavsprünge (pYIN-Fehler) glätten
    for k in range(1, len(notes) - 1):
        a, b, c = notes[k - 1].pitch, notes[k].pitch, notes[k + 1].pitch
        if abs(b - a) >= 11 and abs(b - c) >= 11 and abs(a - c) <= 5:
            notes[k].pitch += 12 if b < a else -12
    return notes


# ------------------------------------------------------------------ Raster ----

class Grid:
    """Umrechnung Sekunden <-> Takt/Schlag anhand der Schlagzeiten."""

    def __init__(self, beats: np.ndarray, first_downbeat: int, beats_per_bar: int = 4):
        self.beats = np.asarray(beats)
        self.db = first_downbeat
        self.bpb = beats_per_bar

    def beat_pos(self, t: float) -> float:
        b = self.beats
        if t <= b[0]:
            return (t - b[0]) / (b[1] - b[0])
        if t >= b[-1]:
            return len(b) - 1 + (t - b[-1]) / (b[-1] - b[-2])
        i = int(np.searchsorted(b, t) - 1)
        return i + (t - b[i]) / (b[i + 1] - b[i])

    def to_bar(self, t: float, q: int = 4) -> tuple[int, F]:
        """-> (Taktindex ab erstem Volltakt, Position in Vierteln, auf 1/q Schlag gerundet)"""
        bp = self.beat_pos(t) - self.db
        bp_q = round(bp * q) / q
        bar = int(np.floor(bp_q / self.bpb))
        pos = F(int(round((bp_q - bar * self.bpb) * q)), q)
        return bar, pos

    def bar_time(self, bar: int) -> float:
        i = self.db + bar * self.bpb
        if 0 <= i < len(self.beats):
            return float(self.beats[i])
        period = float(np.median(np.diff(self.beats)))
        if i < 0:
            return float(self.beats[0] + i * period)
        return float(self.beats[-1] + (i - len(self.beats) + 1) * period)


# ------------------------------------------------------------------ Text ----

def syllabify(word: str, lang: str = 'de') -> list[str]:
    """Silbentrennung (Pyphen-Wörterbücher, de/en)."""
    try:
        import pyphen
        dic = pyphen.Pyphen(lang='de_DE' if lang == 'de' else 'en_US')
        parts = dic.inserted(word, hyphen='­').split('­')
        return [p for p in parts if p] or [word]
    except Exception:
        return [word]


def detect_language(text: str) -> str:
    words = re.findall(r'[a-zäöüß]+', text.lower())
    de = sum(w in ('und', 'ich', 'du', 'der', 'die', 'das', 'mein', 'dein', 'ist', 'nicht', 'mich', 'dich', 'wir', 'herr')
             for w in words)
    en = sum(w in ('and', 'the', 'you', 'my', 'your', 'is', 'of', 'me', 'we', 'lord', 'to', 'i') for w in words)
    return 'de' if de >= en else 'en'


SECTION_HEAD = re.compile(r'^\s*[\[(]?\s*(intro|vers|verse|strophe|pre-?chorus|pre-?refrain|chorus|refrain|bridge|'
                          r'interlude|zwischenspiel|outro|ende|tag|coda|solo|instrumental|choral)\s*(\d*)\s*[\])]?\s*:?\s*$',
                          re.I)


@dataclass
class TextBlock:
    label: Optional[str]
    lines: list[str]


def parse_lyrics(text: str) -> list[TextBlock]:
    """Eingefügten Liedtext in Blöcke zerlegen (Leerzeilen bzw. Überschriften wie '[Chorus]', 'Refrain:')."""
    blocks, cur = [], TextBlock(None, [])
    for raw in text.splitlines():
        line = raw.strip()
        m = SECTION_HEAD.match(line)
        if m:
            if cur.lines or cur.label:
                blocks.append(cur)
            cur = TextBlock(line.strip('[]():').strip(), [])
            continue
        if not line:
            if cur.lines:
                blocks.append(cur)
                cur = TextBlock(None, [])
            continue
        cur.lines.append(line)
    if cur.lines:
        blocks.append(cur)
    # gleiche Blöcke ohne Überschrift: wiederholter Block = Refrain
    norm = [' '.join(b.lines).lower() for b in blocks]
    for i, b in enumerate(blocks):
        if not b.label:
            b.label = 'Chorus' if norm.count(norm[i]) > 1 else 'Verse'
    return blocks


# ------------------------------------------------------------------ Ausrichtung Text <-> Noten ----

@dataclass
class Syllable:
    text: str
    syllabic: str          # single | begin | middle | end
    line: int
    block: int


def text_syllables(blocks: list[TextBlock], lang: str) -> list[Syllable]:
    out = []
    li = 0
    for bi, b in enumerate(blocks):
        for line in b.lines:
            words = re.findall(r"[\w’'´`-]+[^\s\w]*|[^\s\w]+", line)
            words = [w for w in words if re.search(r'\w', w)]
            for w in words:
                core = re.match(r"^([\w’'´`-]+)(.*)$", w)
                body, punct = (core.group(1), core.group(2)) if core else (w, '')
                parts = syllabify(body, lang) if '-' not in body else body.split('-')
                parts = [p for p in parts if p]
                if not parts:
                    continue
                for k, ptxt in enumerate(parts):
                    if len(parts) == 1:
                        sy = 'single'
                    elif k == 0:
                        sy = 'begin'
                    elif k == len(parts) - 1:
                        sy = 'end'
                    else:
                        sy = 'middle'
                    out.append(Syllable(ptxt + (punct if k == len(parts) - 1 else ''), sy, li, bi))
            li += 1
    return out


def phrases(notes: list[VNote], gap: float = 0.38) -> list[tuple[int, int]]:
    out, a = [], 0
    for i in range(1, len(notes) + 1):
        if i == len(notes) or notes[i].start - notes[i - 1].end > gap:
            out.append((a, i))
            a = i
    return out


def align_lines(line_syl_counts: list[int], notes: list[VNote], allow_skip: bool = True) -> list[tuple[int, int]]:
    """Ordnet jeder Textzeile einen zusammenhängenden Notenbereich zu (dynamische Programmierung).
    Zeilengrenzen bevorzugt an Phrasengrenzen (Atempausen)."""
    N, L = len(notes), len(line_syl_counts)
    phr = phrases(notes)
    bounds = set(b for _, b in phr) | {0}
    # Überspringen ist billig für vereinzelte Töne (Übersprechen, Ad-libs), teuer mitten im Gesang
    skipc = [0.0] * N
    for a, b in phr:
        for j in range(a, b):
            skipc[j] = 0.3 if b - a <= 3 else (0.8 if b - a <= 7 else 1.2)
    gaps = [notes[i].start - notes[i - 1].end if 0 < i < N else 9.9 for i in range(N + 1)]
    INF = 1e18
    # erwartete Noten pro Silbe (Tonhöhenerkennung zerlegt Silben oft in mehrere Noten)
    dense = sum(b - a for a, b in phr if b - a > 3)
    ratio = min(2.5, max(1.0, dense / max(1, sum(line_syl_counts))))
    # D[l][i]: Kosten, wenn l Zeilen die ersten i Noten verbraucht haben
    D = [[INF] * (N + 1) for _ in range(L + 1)]
    P = [[None] * (N + 1) for _ in range(L + 1)]
    D[0][0] = 0
    for i in range(1, N + 1):  # Noten vor der ersten Zeile überspringen (Intro, Ad-libs)
        D[0][i] = D[0][i - 1] + skipc[i - 1]
        P[0][i] = ('skip', i - 1)
    for l in range(1, L + 1):
        s = line_syl_counts[l - 1]
        exp = s * ratio
        lo_n, hi_n = max(1, int(s * 0.5)), int(exp * 1.8) + 4
        for i in range(1, N + 1):
            best, arg = INF, None
            # Noten zwischen den Zeilen überspringen
            if allow_skip and D[l][i - 1] + skipc[i - 1] < best:
                best, arg = D[l][i - 1] + skipc[i - 1], ('skip', i - 1)
            maxgap = 0.0
            for k in range(1, min(hi_n, i) + 1):
                a = i - k
                if k >= 2:
                    maxgap = max(maxgap, gaps[a + 1])
                if maxgap > 2.6:      # eine Zeile überspannt keine lange Pause
                    break
                if k < lo_n or D[l - 1][a] >= INF:
                    continue
                c = D[l - 1][a] + abs(k - exp) * 0.6 + max(0.0, k - 1.4 * exp) * 1.5 + max(0, s - k) * 2.0
                c += 0 if a in bounds else (4.0 - min(3.0, gaps[a] * 6))
                c += 0 if i in bounds else (4.0 - min(3.0, gaps[i] * 6) if i < N else 0)
                if c < best:
                    best, arg = c, ('line', a)
            D[l][i], P[l][i] = best, arg
    # Ende: restliche Noten dürfen übrig bleiben
    tail = [0.0] * (N + 1)
    for j in range(N - 1, -1, -1):
        tail[j] = tail[j + 1] + skipc[j] * 0.35   # Wiederholungen am Schluss (Outro) sind häufig
    i = min(range(N + 1), key=lambda j: D[L][j] + tail[j])
    res = [None] * L
    l = L
    while l > 0 and i >= 0:
        kind, a = P[l][i]
        if kind == 'line':
            res[l - 1] = (a, i)
            l -= 1
        i = a
    return res


def assign_syllables(syls: list[Syllable], notes: list[VNote]) -> list[tuple[VNote, Optional[Syllable]]]:
    """Silben auf Noten verteilen: zu viele Noten -> Melisma (ohne Silbe), zu wenige -> Noten teilen."""
    notes = list(notes)
    while len(notes) < len(syls) and notes:
        k = max(range(len(notes)), key=lambda j: notes[j].end - notes[j].start)
        n = notes[k]
        mid = (n.start + n.end) / 2
        notes[k:k + 1] = [VNote(n.start, mid, n.pitch, n.strength, n.fpitch), VNote(mid, n.end, n.pitch, n.strength, n.fpitch)]
    extra = len(notes) - len(syls)
    starts = set(range(len(notes)))
    if extra > 0:
        # Kandidaten für Melisma: kurze Noten direkt nach der vorigen, Tonhöhenwechsel ohne Einsatz
        scored = []
        for j in range(1, len(notes)):
            gap = notes[j].start - notes[j - 1].end
            dur = notes[j].end - notes[j].start
            scored.append((gap * 4 + dur + (0.3 if notes[j].strength > notes[j - 1].strength * 1.3 else 0), j))
        for _, j in sorted(scored)[:extra]:
            starts.discard(j)
    out = []
    si = 0
    for j, n in enumerate(notes):
        if j in starts and si < len(syls):
            out.append((n, syls[si]))
            si += 1
        else:
            out.append((n, None))
    return out


# ------------------------------------------------------------------ Gesamtablauf ----

@dataclass
class AnalysisResult:
    song: Song
    report: dict = field(default_factory=dict)


def _norm_octave(notes: list[VNote]) -> int:
    if not notes:
        return 0
    med = float(np.median([n.pitch for n in notes]))
    shift = 0
    while med + shift < 61:
        shift += 12
    while med + shift > 73:
        shift -= 12
    return shift


def _label_counter(labels):
    from collections import Counter
    return Counter(labels)


def build_song(path, lyrics_text: Optional[str] = None, title: str = '', authors: str = '',
               progress=None, use_asr: bool = True) -> AnalysisResult:
    import librosa
    rep = {}

    def step(name, frac):
        if progress:
            progress(name, frac)

    step('Audio laden', 0.02)
    mix22 = load_audio(path, 22050, mono=True)
    step('Gesang und Begleitung trennen', 0.05)
    voc, inst = stems(path, (lambda f: progress('Gesang und Begleitung trennen', 0.05 + 0.45 * f)) if progress else None)
    voc22 = librosa.resample(voc.mean(0), orig_sr=SR, target_sr=22050)
    inst22 = librosa.resample(inst.mean(0), orig_sr=SR, target_sr=22050)
    step('Takt und Tempo', 0.52)
    beats, tempo = track_beats(mix22)
    step('Akkorde', 0.58)
    probs, fps = chord_probs(mix22)
    bass = bass_per_beat(inst22, beats)
    bc = beat_chords_with_bass(probs, fps, beats, bass)
    from .btc import label
    labels = [label(i) for i in bc]
    key_tpc, minor = estimate_key(labels)
    bpb, phase, meter_scores = detect_meter(beat_chords(probs, fps, beats), bass)
    if bpb == 3:
        phase = find_downbeat_phase(bc, bass, 3)
    grid = Grid(beats, phase, bpb)
    rep['takt'] = f'{bpb}/4'
    rep['taktart_werte'] = meter_scores
    step('Melodie', 0.65)
    vnotes = vocal_notes(voc22)
    snap_to_key(vnotes, key_tpc, minor)
    octv = _norm_octave(vnotes)
    for n in vnotes:
        n.pitch += octv
    rep.update(tempo=round(tempo, 1), key=key_name(key_tpc, minor), noten=len(vnotes))

    # Text
    step('Liedtext', 0.8)
    note_syl: dict[int, Syllable] = {}
    blocks = parse_lyrics(lyrics_text) if lyrics_text and lyrics_text.strip() else []
    line_ranges = []
    if blocks:
        lang = detect_language(lyrics_text)
        syls = text_syllables(blocks, lang)
        lines = [l for b in blocks for l in b.lines]
        counts = [sum(1 for s in syls if s.line == li) for li in range(len(lines))]
        ranges = align_lines(counts, vnotes)
        new_notes = []
        consumed = 0
        id_map = {}
        for li, rg in enumerate(ranges):
            if rg is None:
                continue
            a, b = rg
            pairs = assign_syllables([s for s in syls if s.line == li], vnotes[a:b])
            line_ranges.append((li, pairs[0][0].start if pairs else 0, pairs[-1][0].end if pairs else 0))
            for n, s in pairs:
                if s is not None:
                    note_syl[id(n)] = s
                new_notes.append(n)
            consumed = b
        # Noten außerhalb der Zeilen (Ad-libs, Hintergrund) behalten wir ohne Text
        keep = {id(n) for n in new_notes}
        rest = [n for n in vnotes if id(n) not in keep and not any(abs(n.start - m.start) < 0.02 for m in new_notes)]
        vnotes = sorted(new_notes + rest, key=lambda n: n.start)
        rep['zeilen'] = f'{sum(1 for r in ranges if r)} von {len(lines)} Zeilen zugeordnet'

    # Takte
    step('Noten setzen', 0.88)
    n_beats = len(beats)
    n_bars = (n_beats - phase) // bpb + (1 if (n_beats - phase) % bpb else 0)
    bars: list[Bar] = []
    # Akkordabschnitte (gleiche Akkordklasse) mit stabilem Basston -> Slash-Akkord
    seg_sym = [None] * n_beats
    i = 0
    root_scale = {(tpc_pc(key_tpc) + d) % 12 for d in (0, 2, 4, 5, 7, 9, 11)}
    while i < n_beats:
        j = i
        while j + 1 < n_beats and bc[j + 1] == bc[i]:
            j += 1
        # innerhalb eines langen Abschnitts kann der Bass wandern (G – G/F♯ – Em): in 2er-Gruppen prüfen
        k = i
        while k <= j:
            grp = list(range(k, min(j, k + 1) + 1)) if (j - i + 1) >= 4 else list(range(i, j + 1))
            bs = [bass[x] for x in grp if bass[x] is not None]
            maj = max(set(bs), key=bs.count) if bs else None
            share = bs.count(maj) / len(grp) if bs else 0
            bp = maj if (maj is not None and share >= 0.6 and maj in root_scale) else None
            sym = chord_symbol_from(bc[i], bp, key_tpc)
            for x in grp:
                seg_sym[x] = sym
            k = grp[-1] + 1
        i = j + 1
    for b in range(n_bars):
        bar = Bar(bpb, 4, audio_start=grid.bar_time(b))
        prev = None
        for k in range(bpb):
            i = phase + b * bpb + k
            if i >= n_beats:
                break
            sym = seg_sym[i]
            if sym and sym != prev:
                bar.chords.append(ChordEvent(F(k), sym))
            prev = sym
        bars.append(bar)
    # Akkorde: gleiche Akkorde über Taktgrenzen nicht wiederholen, aber am Taktanfang anzeigen
    last = None
    for bar in bars:
        if bar.chords and bar.chords[0].onset > 0 and last:
            bar.chords.insert(0, ChordEvent(F(0), last))
        if bar.chords:
            last = bar.chords[-1].symbol
    # Melodie quantisieren
    events = []
    for n in vnotes:
        b0, p0 = grid.to_bar(n.start, 4)
        b1, p1 = grid.to_bar(n.end, 4)
        if b0 < 0 or b0 >= len(bars):
            continue
        a = b0 * bpb + p0
        e = b1 * bpb + p1
        if e <= a:
            e = a + F(1, 4)
        events.append([a, e, n])
    events.sort(key=lambda x: x[0])
    # Überlappungen und Dubletten auflösen
    cleaned = []
    for ev in events:
        if cleaned and ev[0] == cleaned[-1][0]:
            if id(ev[2]) in note_syl and id(cleaned[-1][2]) not in note_syl:
                cleaned[-1] = ev
            continue
        if cleaned and cleaned[-1][1] > ev[0]:
            cleaned[-1][1] = ev[0]
        cleaned.append(ev)
    # kurze Lücken (< 1/4 Schlag) schließen
    for k in range(len(cleaned) - 1):
        if 0 < cleaned[k + 1][0] - cleaned[k][1] <= F(1, 4):
            cleaned[k][1] = cleaned[k + 1][0]
    for a, e, n in cleaned:
        tpc = spell_pitch(n.pitch, key_tpc)
        sy = note_syl.get(id(n))
        first = True
        t = a
        while t < e:
            bi = int(t // bpb)
            if bi >= len(bars):
                break
            pos = t - bi * bpb
            d = min(e - t, F(bpb) - pos)
            note = Note(onset=pos, dur=d, pitch=n.pitch, tpc=tpc, tie=(t + d < e))
            if first and sy is not None:
                note.lyrics = {0: (sy.text, sy.syllabic)}
            bars[bi].melody.append(note)
            first = False
            t += d
    for bar in bars:
        bar.melody.sort(key=lambda x: x.onset)
        _fill_rests(bar)

    # Formteile
    step('Formteile', 0.94)
    sections = _make_sections(bars, blocks, line_ranges, grid, note_syl, beats)
    song = Song(title=title or Path(path).stem, authors=authors, key=key_name(key_tpc, minor),
                tempo=round(tempo), sections=sections, audio=str(path))
    from ..lyrics import assign_pickups, auto_lines, apply_text
    assign_pickups(song.sections)
    for i, sec in enumerate(song.sections):
        txt = getattr(sec, '_text', None)
        if txt:
            apply_text(song.sections, i, txt)
            delattr(sec, '_text')
        else:
            auto_lines(song.sections, i)
    song.default_form()
    song.notes['analyse'] = rep
    step('Fertig', 1.0)
    return AnalysisResult(song, rep)


def _fill_rests(bar: Bar):
    out, t = [], F(0)
    for n in bar.melody:
        if n.onset > t:
            out.append(Note(t, n.onset - t))
        if n.onset < t:
            continue
        out.append(n)
        t = n.onset + n.dur
    if t < bar.length:
        out.append(Note(t, bar.length - t))
    bar.melody = out


def beat_chords_with_bass(probs, fps, beats, bass, switch_penalty: float = 2.2) -> list[int]:
    """Wie beat_chords, aber der erkannte Basston beeinflusst die Wahl (z. B. C2 statt G über C)."""
    from .btc import label
    prior = np.zeros(probs.shape[1])
    pcs_of = []
    for i in range(probs.shape[1]):
        p = btc_label_to_parts(label(i))
        pcs_of.append(_triad_pcs(p) if p else set())
    n = len(beats)
    B = np.zeros((n, probs.shape[1]))
    for i in range(n):
        a = beats[i]
        b = beats[i + 1] if i + 1 < n else a + 0.5
        fa, fb = int(a / fps), max(int(a / fps) + 1, int(b / fps))
        seg = probs[fa:fb] if fa < len(probs) else probs[-1:]
        B[i] = np.log(np.clip(seg.mean(0), 1e-6, 1))
        if bass[i] is not None:
            for c in range(probs.shape[1]):
                p = btc_label_to_parts(label(c))
                if not p:
                    continue
                if p[0] == bass[i]:
                    B[i, c] += 0.6
                elif bass[i] not in pcs_of[c]:
                    B[i, c] -= 0.4
    K = B.shape[1]
    D = B[0].copy()
    back = np.zeros((n, K), dtype=np.int32)
    for i in range(1, n):
        bp = int(D.argmax())
        jump = D[bp] - switch_penalty
        stay = D >= jump
        back[i] = np.where(stay, np.arange(K), bp)
        D = np.where(stay, D, jump) + B[i]
    path = [int(D.argmax())]
    for i in range(n - 1, 0, -1):
        path.append(int(back[i][path[-1]]))
    return path[::-1]


def _triad_pcs(parts) -> set:
    r, q = parts
    third = 3 if q.startswith('m') and not q.startswith('maj') else 4
    s = {r, (r + 7) % 12}
    if 'sus' in q or q == '2':
        s.add((r + (5 if 'sus4' in q else 2)) % 12)
    else:
        s.add((r + third) % 12)
    if '7' in q:
        s.add((r + (11 if 'maj7' in q else 10)) % 12)
    if q in ('6', 'm6'):
        s.add((r + 9) % 12)
    if q == '2':
        s.add((r + 4) % 12)
    return s


def chord_symbol_from(idx: int, bass_pc: Optional[int], key_tpc: int) -> Optional[str]:
    from .btc import label
    p = btc_label_to_parts(label(idx))
    if not p:
        return None
    r, q = p
    root_tpc = spell_pitch(r, key_tpc)
    sym = ChordSymbol(root_tpc, q)
    if bass_pc is not None and bass_pc != r:
        tones = _triad_pcs(p)
        if q == 'maj7' and bass_pc == (r + 11) % 12:
            sym = ChordSymbol(root_tpc, '', spell_pitch(bass_pc, key_tpc))
        elif bass_pc in tones and bass_pc != r:
            sym = ChordSymbol(root_tpc, q, spell_pitch(bass_pc, key_tpc))
        elif bass_pc == (r + 11) % 12 and q in ('', '2'):
            sym = ChordSymbol(root_tpc, q, spell_pitch(bass_pc, key_tpc))   # G/F♯ (Durchgangsbass)
    return sym.text()


def _make_sections(bars, blocks, line_ranges, grid, note_syl, beats) -> list[Section]:
    """Formteile: mit Text aus den Textblöcken, sonst aus Gesangspausen und Wiederholungen."""
    nb = len(bars)
    sung = [any(n.lyrics or (n.pitch is not None) for n in b.melody) for b in bars]
    spans = []  # (bar_start, bar_end, label, kind, text)
    if blocks and line_ranges:
        li_to_block = {}
        li = 0
        for bi, b in enumerate(blocks):
            for _ in b.lines:
                li_to_block[li] = bi
                li += 1
        occ = {}
        for li, t0, t1 in line_ranges:
            bi = li_to_block[li]
            occ.setdefault(bi, [t0, t1])
            occ[bi][0] = min(occ[bi][0], t0)
            occ[bi][1] = max(occ[bi][1], t1)
        for bi in sorted(occ):
            t0, t1 = occ[bi]
            b0, p0 = grid.to_bar(t0, 1)
            b1, p1 = grid.to_bar(t1, 1)
            if p0 >= grid.bpb - 1:  # Auftakt -> Teil beginnt im nächsten Takt
                b0 += 1
            spans.append([max(0, b0), min(nb, b1 + (1 if p1 > 0 else 0)), blocks[bi].label, '\n'.join(blocks[bi].lines)])
    else:
        # Gesungene Bereiche, getrennt durch >= 2 Takte ohne Gesang
        i = 0
        while i < nb:
            if not sung[i]:
                i += 1
                continue
            j = i
            silent = 0
            while j < nb and silent < 2:
                silent = silent + 1 if not sung[j] else 0
                j += 1
            end = j - silent
            # lange Bereiche in 8-Takt-Teile
            for a in range(i, end, 8):
                spans.append([a, min(end, a + 8), None, None])
            i = j
    # Lücken auffüllen (Intro/Interlude/Outro) und Grenzen bereinigen
    spans.sort()
    out = []
    pos = 0
    for k, (a, b, label, text) in enumerate(spans):
        a = max(a, pos)
        if b <= a:
            continue
        if a > pos:
            gap_label = 'Intro' if not out else 'Interlude'
            out.append([pos, a, gap_label, None])
        out.append([a, b, label, text])
        pos = b
    if pos < nb:
        out.append([pos, nb, 'Outro', None])
    # Beschriftung ohne Text: Wiederholungen der Akkordfolge -> Chorus
    def sig(a, b):
        return tuple(c.symbol for bar in bars[a:b] for c in bar.chords)
    if not blocks:
        sigs = [sig(a, b) for a, b, l, _ in out]
        from collections import Counter
        cnt = Counter(s for s, (_, _, l, _) in zip(sigs, out) if l is None)
        top = max(cnt.items(), key=lambda kv: kv[1])[0] if cnt else None
        for (o, s) in zip(out, sigs):
            if o[2] is None:
                o[2] = 'Chorus' if (s == top and cnt[top] > 1) else 'Verse'
    from ..importers.musescore import classify_label, SECTION_ABBR
    counts = {}
    total = {}
    for a, b, label, _ in out:
        kind, nice, num = classify_label(label or 'Verse')
        total[nice] = total.get(nice, 0) + 1
    secs = []
    for a, b, label, text in out:
        kind, nice, num = classify_label(label or 'Verse')
        counts[nice] = counts.get(nice, 0) + 1
        n = num or str(counts[nice])
        lab = f'{nice} {n}' if total[nice] > 1 or num else nice
        sid = SECTION_ABBR.get(nice, nice[:2].upper()) + n
        while any(s.id == sid for s in secs):
            sid += 'b'
        sec = Section(id=sid, label=lab, kind=kind, bars=bars[a:b],
                      audio_start=bars[a].audio_start, audio_end=(bars[b].audio_start if b < nb else None))
        if text:
            sec._text = text
        secs.append(sec)
    return secs
