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
import os
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


def work_dir(path, base=None) -> Path:
    """Zwischenspeicher für die Analyse eines Audios (Standard: ~/.cache/kenanja/analyse/<hash>)."""
    if base is None:
        from .btc import cache_dir
        base = cache_dir().parent / 'analyse'
    d = Path(base) / file_hash(path)
    d.mkdir(parents=True, exist_ok=True)
    return d


def stems(path, progress=None, cache=None) -> tuple[np.ndarray, np.ndarray]:
    """Gesang/Begleitung (zwischengespeichert als FLAC)."""
    import soundfile as sf
    wd = work_dir(path, cache)
    v, i = wd / 'gesang.flac', wd / 'begleitung.flac'
    if v.exists() and i.exists():
        return sf.read(v, dtype='float32')[0].T, sf.read(i, dtype='float32')[0].T
    from .separate import separate
    mix = load_audio(path)
    voc, inst = separate(mix, progress)
    sf.write(v, voc.T, SR)
    sf.write(i, inst.T, SR)
    return voc, inst


def lead_or_group(voc22: np.ndarray, lead22: np.ndarray, sr: int = 22050) -> np.ndarray:
    """Leadstimme, aber wo das Karaoke-Modell alles als „Chor“ wegnimmt (Gruppen- oder Unisono-Gesang,
    z. B. „nur Frauen“), die ganze Gesangsspur – sonst fehlen dort Melodie und Text."""
    from scipy.ndimage import uniform_filter1d
    n = min(len(voc22), len(lead22))
    v, l = voc22[:n], lead22[:n]
    hop = 512
    def db(x):
        e = np.sqrt(uniform_filter1d(x.astype(np.float64) ** 2, hop)[::hop] + 1e-12)
        return 20 * np.log10(e)
    dv, dl = db(v), db(l)
    k = max(1, int(0.6 * sr / hop))
    dv, dl = uniform_filter1d(dv, k), uniform_filter1d(dl, k)
    # Anteil Lead: 1, wenn die Leadspur höchstens 6 dB leiser als die Gesangsspur ist; 0 ab 15 dB leiser
    w = np.clip((dl - dv + 15) / 9, 0, 1)
    w = np.interp(np.arange(n), np.arange(len(w)) * hop, w)
    return (w * l + (1 - w) * v).astype(np.float32)


def lead_stem(path, voc: np.ndarray, progress=None, cache=None, model: str = 'mdx') -> np.ndarray:
    """Nur die Leadstimme (Chor/Backgroundgesang entfernt) – zwischengespeichert als FLAC.
    model='mdx': UVR-MDX-KARA (schnell); model='roformer': BS-RoFormer-Karaoke (genauer, langsamer)."""
    import soundfile as sf
    wd = work_dir(path, cache)
    if model == 'roformer':
        from . import roformer as R
        f = wd / 'lead_rof.flac'
        if f.exists():
            return sf.read(f, dtype='float32')[0].T
        lead = R.LeadRoformer().separate(voc, progress)
        sf.write(f, lead.T, SR)
        return lead
    f = wd / 'lead.flac'
    if f.exists():
        return sf.read(f, dtype='float32')[0].T
    from .separate import separate
    lead, _ = separate(voc, progress, model='lead')
    sf.write(f, lead.T, SR)
    return lead


# ------------------------------------------------------------------ Schläge ----

def scale_beats(beats: np.ndarray, tempo: float, faktor: float, mix22: Optional[np.ndarray] = None,
                sr: int = 22050) -> tuple[np.ndarray, float]:
    """Notation in doppeltem (faktor 2) oder halbem (0.5) Tempo: Schläge verdoppeln bzw. jeden zweiten nehmen."""
    beats = np.asarray(beats, dtype=float)
    if faktor == 2 and len(beats) > 1:
        mids = (beats[:-1] + beats[1:]) / 2
        out = np.empty(len(beats) + len(mids))
        out[0::2], out[1::2] = beats, mids
        return out, tempo * 2
    if faktor == 0.5 and len(beats) > 3:
        off = 0
        if mix22 is not None:
            import librosa
            oenv = librosa.onset.onset_strength(y=mix22, sr=sr, hop_length=512)
            fr = np.clip(librosa.time_to_frames(beats, sr=sr, hop_length=512), 0, len(oenv) - 1)
            off = 0 if oenv[fr[0::2]].mean() >= oenv[fr[1::2]].mean() else 1
        return beats[off::2], tempo / 2
    return beats, tempo


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


def vocal_notes(voc_mono22: np.ndarray, sr: int = 22050, min_dur: float = 0.09, jump: float = 0.6,
                onset_delta: float = 0.08, weak: float = 0.35) -> list[VNote]:
    """Gesangsnoten aus der (Lead-)Gesangsspur: Tonhöhenverlauf (pYIN) + Einsätze -> Noten."""
    import librosa
    hop = 256
    rms = librosa.feature.rms(y=voc_mono22, frame_length=2048, hop_length=hop)[0]
    t = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    from .f0 import track as f0_track
    f0, voiced = f0_track(voc_mono22, sr, hop)
    n = min(len(f0), len(rms))
    f0, voiced, rms, t = f0[:n], voiced[:n], rms[:n], t[:n]
    midi = librosa.hz_to_midi(f0)
    thr = max(0.012, np.percentile(rms, 30))
    f0 = midi
    onset_frames = set(librosa.onset.onset_detect(y=voc_mono22, sr=sr, hop_length=hop, backtrack=True,
                                                  units='frames', delta=onset_delta))
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
        new_note = abs(m - med) > jump and (len(cur_vals) >= 4)
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
            if ph_str >= weak * med:
                keep += notes[a:b]
        notes = keep
    # Oktavsprünge (pYIN-Fehler) glätten
    for k in range(1, len(notes) - 1):
        a, b, c = notes[k - 1].pitch, notes[k].pitch, notes[k + 1].pitch
        if abs(b - a) >= 11 and abs(b - c) >= 11 and abs(a - c) <= 5:
            notes[k].pitch += 12 if b < a else -12
    return notes


def merge_scoops(notes: list[VNote], max_dur: float = 0.27, max_iv: int = 3, gap: float = 0.07) -> list[VNote]:
    """Sänger schleifen Töne oft von unten an oder lassen sie am Ende abfallen. Die Tonhöhenerkennung macht
    daraus eine kurze Extranote mit falschem Ton. Solche Ansätze werden der Zielnote zugeschlagen."""
    out: list[VNote] = []
    notes = sorted(notes, key=lambda n: n.start)
    i = 0
    while i < len(notes):
        n = notes[i]
        d = n.end - n.start
        if i + 1 < len(notes):
            m = notes[i + 1]
            if (d <= max_dur and m.start - n.end <= gap and 1 <= abs(m.pitch - n.pitch) <= max_iv
                    and (m.end - m.start) >= 0.8 * d):
                m.start = n.start
                i += 1
                continue
        if out:
            p = out[-1]
            nxt_gap = (notes[i + 1].start - n.end) if i + 1 < len(notes) else 9.0
            if (d <= 0.16 and n.start - p.end <= gap and 1 <= abs(n.pitch - p.pitch) <= 4 and nxt_gap > 0.08
                    and (p.end - p.start) > 2 * d):
                p.end = n.end
                i += 1
                continue
        out.append(n)
        i += 1
    return out


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


SECTION_HEAD = re.compile(r'^\s*[\[(]?\s*(intro|vers|verse|strophe|pre-?chorus|pre-?refrain|post-?chorus|post-?refrain|'
                          r'chorus|refrain|bridge|interlude|zwischenspiel|zwischenteil|outro|ende|ending|schluss|tag|coda|'
                          r'solo|instrumental|choral|vamp)\s*(\d*)\s*[\])]?\s*:?\s*$', re.I)
ARROW = re.compile(r'\s*(→|->|=>).*$')           # Regie-Hinweise wie „→ alle“ / „→ Frauen“
SYL_DASH = re.compile(r'(?<=\w)\s+[-–]\s+(?=\w)')  # „Je – sus“, „ho - ly“: Silben eines Wortes
COLUMNS = re.compile(r'\t+|\s{3,}')


@dataclass
class TextBlock:
    label: Optional[str]
    lines: list[str]


def split_by_repetition(lines: list[str], min_len: int = 4) -> list[TextBlock]:
    """Zerlegt einen durchgehenden Liedtext anhand wiederkehrender Zeilengruppen in Blöcke."""
    norm = [re.sub(r'\W+', ' ', l.lower()).strip() for l in lines]
    n = len(norm)
    # Anfang einer wiederholten Passage: Zeile kommt mehrfach vor, aber mit unterschiedlicher Vorgängerzeile
    heads = set()
    for v in set(norm):
        pos = [i for i, x in enumerate(norm) if x == v]
        if len(pos) < 2:
            continue
        prev = {norm[p - 1] if p else None for p in pos}
        if len(prev) > 1:
            heads.add(v)
    cuts = [0]
    for i in range(1, n):
        if norm[i] in heads and norm[i] != norm[i - 1] and i - cuts[-1] >= min_len:
            cuts.append(i)
    blocks = []
    for k, a in enumerate(cuts):
        b = cuts[k + 1] if k + 1 < len(cuts) else n
        blocks.append(TextBlock(None, lines[a:b]))
    # Block, der als Ganzes am häufigsten vorkommt, aber nicht der erste ist -> Refrain
    return blocks


JUNK = re.compile(r'(https?://|\]\(|^you might also like|^\d+\s*contributors|embed$|^translations|^see .* live|'
                  r'^get tickets|lyrics$|^\[?songtext|^\*+$)', re.I)
CUE = re.compile(r'^\(\s*([^()]{2,40})\s*\)$')


def clean_lyrics_text(text: str) -> str:
    """Kopierten Liedtext säubern (Links, Werbung, „You might also like“ …)."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and JUNK.search(line):
            continue
        out.append(line)
    return '\n'.join(out)


def parse_lyrics(text: str) -> list[TextBlock]:
    """Eingefügten Liedtext in Blöcke zerlegen (Leerzeilen bzw. Überschriften wie '[Chorus]', 'Refrain:').
    Zeilen wie '(Ladies)' / '(Men)' werden als Sänger-Hinweis gemerkt, '(x2)' wiederholt die Zeile."""
    blocks, cur = [], TextBlock(None, [])
    cur.cues = []
    raw_lines = [ARROW.sub('', l).rstrip() for l in clean_lyrics_text(text).splitlines()]
    # Kopfzeilen (Titel, Autoren, Version) vor der ersten Überschrift weglassen, wenn diese gleich folgt
    nonempty = [i for i, l in enumerate(raw_lines) if l.strip()]
    first_head = next((i for i in nonempty[:6] if SECTION_HEAD.match(raw_lines[i].strip())), None)
    if first_head is not None and 0 < sum(1 for i in nonempty if i < first_head) <= 3:
        raw_lines = raw_lines[first_head:]
    expanded = []
    for l in raw_lines:
        # mehrspaltige Zeilen (Tabulatoren, große Abstände) nacheinander singen
        parts = [SYL_DASH.sub('', p.strip()) for p in COLUMNS.split(l.strip()) if p.strip()] if l.strip() else []
        merged_parts: list[str] = []
        for p_ in parts:   # Einwürfe wie „ho - ly“ gehören zur Zeile davor
            if merged_parts and len(p_.split()) <= 2:
                merged_parts[-1] += ' ' + p_
            else:
                merged_parts.append(p_)
        for p_ in merged_parts:   # sehr lange Zeilen an Satzenden teilen
            if len(p_.split()) > 12:
                for q in [q.strip() for q in re.split(r'(?<=[.!?;])\s+', p_) if q.strip()]:
                    w = q.split()
                    commas = [k for k, x in enumerate(w[:-1]) if x.endswith(',')]
                    if len(w) > 12 and commas:
                        k = min(commas, key=lambda k: abs(k + 1 - len(w) / 2))
                        expanded += [' '.join(w[:k + 1]), ' '.join(w[k + 1:])]
                    else:
                        expanded.append(q)
            else:
                expanded.append(p_)
        if not merged_parts:
            expanded.append('')
    for raw in expanded:
        line = raw.strip()
        m = SECTION_HEAD.match(line)
        if m:
            if cur.lines or cur.label:
                blocks.append(cur)
            cur = TextBlock(line.strip('[]():').strip(), [])
            cur.cues = []
            continue
        if not line:
            if cur.lines:
                blocks.append(cur)
                cur = TextBlock(None, [])
                cur.cues = []
            continue
        c = CUE.match(line)
        if c:
            cur.cues.append((len(cur.lines), c.group(1)))
            continue
        rep = re.search(r'\(?\s*[x×]\s*(\d)\s*\)?$|\(\s*(\d)\s*[x×]\s*\)$', line, re.I)
        times = 1
        if rep:
            times = int(rep.group(1) or rep.group(2))
            line = line[:rep.start()].strip()
        line = re.sub(r'\s*\([^)]*\)\s*$', '', line).strip() or line   # Echo/Backing in Klammern weg
        cur.lines += [line] * times
    if cur.lines or cur.label:
        blocks.append(cur)
    # Überschrift ohne eigenen Text (z. B. zweites [Chorus]): Text des gleichnamigen Blocks übernehmen
    for k, b in enumerate(blocks):
        if not b.lines and b.label:
            src = next((x for x in blocks[:k] if x.label and x.label.lower() == b.label.lower() and x.lines), None)
            if src is None:   # „Chorus“ nach „Chorus 1“: letzter Block gleicher Art
                base = re.sub(r'[\s\d]+$', '', b.label.lower())
                src = next((x for x in reversed(blocks[:k]) if x.label and x.lines
                            and re.sub(r'[\s\d]+$', '', x.label.lower()) == base), None)
            if src:
                b.lines = list(src.lines)
    blocks = [b for b in blocks if b.lines]
    # Text ohne Leerzeilen/Überschriften: an Wiederholungen in Strophe/Refrain zerlegen
    if len(blocks) == 1 and not blocks[0].label and len(blocks[0].lines) > 12:
        blocks = split_by_repetition(blocks[0].lines)
    # Blöcke ohne Überschrift: nach Ähnlichkeit gruppieren – erste Gruppe Verse, zweite Chorus, Rest Bridge/Tag
    def key(b):
        return [re.sub(r'\W+', ' ', l.lower()).strip() for l in b.lines]
    groups = []
    for b in blocks:
        if b.label:
            continue
        kb = key(b)
        for g in groups:
            kg = key(g[0])
            n = min(len(kb), len(kg), 4)
            if kb[:n] == kg[:n]:
                g.append(b)
                break
        else:
            groups.append([b])
    names = ['Verse', 'Chorus', 'Bridge', 'Tag']
    for gi, g in enumerate(groups):
        for b in g:
            b.label = names[min(gi, len(names) - 1)]
            b.geraten = True        # Überschrift nur geraten – nicht für „gleiche Melodie wie …“ verwenden
    for b in blocks:
        if not hasattr(b, 'cues'):
            b.cues = []
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
    res = [None] * L
    # Passen nicht alle Zeilen auf die Noten (z. B. Text länger als die Aufnahme, wenig erkannte Noten),
    # werden so viele Zeilen wie möglich zugeordnet; die übrigen bleiben ohne Noten.
    l = L
    while l > 0 and min(D[l]) >= INF:
        l -= 1
    if l == 0:
        return res
    i = min(range(N + 1), key=lambda j: D[l][j] + tail[j])
    while l > 0 and i >= 0:
        if P[l][i] is None:
            break
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


# ------------------------------------------------------------------ Spracherkennung (optional) ----

def transcribe_words(voc16: np.ndarray, lang: str, model_size: str = 'small') -> list[tuple[float, float, str]]:
    """Wörter mit Zeitstempeln aus der Gesangsspur (faster-whisper). Leere Liste, wenn nicht verfügbar."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return []
    from .btc import cache_dir
    import os
    os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
    model = WhisperModel(model_size, device='cpu', compute_type='int8', download_root=str(cache_dir() / 'whisper'))
    segs, _ = model.transcribe(voc16, language=lang, word_timestamps=True, vad_filter=True, beam_size=5,
                               condition_on_previous_text=False)
    return [(w.start, w.end, w.word) for s in segs for w in (s.words or [])]


def _wnorm(w: str) -> str:
    import unicodedata
    w = unicodedata.normalize('NFKD', w.lower())
    return ''.join(c for c in w if c.isalnum())


def _wsim(a: str, b: str) -> float:
    if not a or not b:
        return -1
    if a == b:
        return 2.0
    from difflib import SequenceMatcher
    r = SequenceMatcher(None, a, b).ratio()
    return 1.0 if r >= 0.75 else (-0.5 if r >= 0.5 else -1.0)


def align_lines_asr(lines: list[str], asr: list[tuple[float, float, str]], notes: list['VNote']):
    """Zeilen über erkannte Wörter verankern (robust bei Wiederholungen und Ad-libs).
    Liefert Notenbereiche je Zeile oder None, wenn zu wenig erkannt wurde."""
    words, wline = [], []
    for li, line in enumerate(lines):
        for w in re.findall(r"[\w’']+", line):
            words.append(_wnorm(w))
            wline.append(li)
    A = [_wnorm(w) for _, _, w in asr]
    n, m = len(words), len(A)
    if n == 0 or m == 0:
        return None
    G = -0.6
    S = np.zeros((n + 1, m + 1), dtype=np.float32)
    S[1:, 0] = np.arange(1, n + 1) * G
    S[0, 1:] = np.arange(1, m + 1) * -0.2          # Ad-libs/Fremdwörter im Audio sind billig
    bt = np.zeros((n + 1, m + 1), dtype=np.int8)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d = S[i - 1, j - 1] + _wsim(words[i - 1], A[j - 1])
            u = S[i - 1, j] + G
            l = S[i, j - 1] - 0.2
            k = int(np.argmax([d, u, l]))
            S[i, j] = (d, u, l)[k]
            bt[i, j] = k
    i, j = n, m
    match = [None] * n
    while i > 0 and j > 0:
        k = bt[i, j]
        if k == 0:
            if _wsim(words[i - 1], A[j - 1]) > 0:
                match[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif k == 1:
            i -= 1
        else:
            j -= 1
    if sum(1 for x in match if x is not None) < 0.4 * n:
        return None
    starts, ends = [None] * len(lines), [None] * len(lines)
    for wi, aj in enumerate(match):
        if aj is None:
            continue
        li = wline[wi]
        t0, t1 = asr[aj][0], asr[aj][1]
        starts[li] = t0 if starts[li] is None else min(starts[li], t0)
        ends[li] = t1 if ends[li] is None else max(ends[li], t1)
    res = []
    for li in range(len(lines)):
        if starts[li] is None:
            res.append(None)
            continue
        nxt = next((starts[k] for k in range(li + 1, len(lines)) if starts[k] is not None), 1e9)
        a = next((k for k, nn in enumerate(notes) if nn.start >= starts[li] - 0.3), len(notes))
        b = next((k for k, nn in enumerate(notes) if nn.start >= min(nxt - 0.15, ends[li] + 0.6)), len(notes))
        res.append((a, b) if b > a else None)
    # Lücken (nicht erkannte Zeilen) per Silbenzählung dazwischen verteilen
    return res


# ------------------------------------------------------------------ Gesamtablauf ----

@dataclass
class AnalysisResult:
    song: Song
    report: dict = field(default_factory=dict)


def _fix_octaves(notes: list[VNote]) -> None:
    """Melodie in eine Lage bringen: Phrasen von Männer- und Frauenstimmen auf dieselbe Oktave,
    einzelne Ausreißer (Oktavfehler der Tonhöhenerkennung) korrigieren."""
    if len(notes) < 8:
        return
    target = float(np.median([n.pitch for n in notes]))
    for a, b in phrases(notes):
        pm = float(np.median([n.pitch for n in notes[a:b]]))
        # nur verschieben, wenn die Phrase klar in einer anderen Lage liegt (Männer- statt Frauenstimme);
        # höhere/tiefere Teile der Melodie selbst (z. B. Bridge eine Sexte höher) bleiben, wie sie sind
        dev = target - pm
        sh = 12 * int(np.sign(dev)) * int((abs(dev) + 2.5) // 12) if abs(dev) > 9.5 else 0
        if sh:
            for n in notes[a:b]:
                n.pitch += sh
    for k in range(len(notes)):
        nb = [notes[j].pitch for j in range(max(0, k - 3), min(len(notes), k + 4)) if j != k]
        if not nb:
            continue
        med = float(np.median(nb))
        while notes[k].pitch - med > 9:
            notes[k].pitch -= 12
        while med - notes[k].pitch > 9:
            notes[k].pitch += 12


DEBUG: dict = {}


def _smooth_octaves(notes: list, shift_cost: float = 1.5, gap: float = 3.0) -> None:
    """Je Ton Oktave −12/0/+12 so wählen, dass Sprünge über eine Quinte teuer sind (Zeilen über längere
    Pausen hinweg zählen nicht als Nachbarn). Ändert die Tonhöhen an Ort und Stelle."""
    if len(notes) < 3:
        return
    S = (-12, 0, 12)

    def jump(d):
        d = abs(d)
        return 0.0 if d <= 5 else (d - 5) * 0.5 if d <= 7 else 1.0 + (d - 7) * 3.0
    cost = [abs(s_) / 12 * shift_cost for s_ in S]
    back = []
    for k in range(1, len(notes)):
        cont = notes[k].start - notes[k - 1].end < gap
        nc, bk = [], []
        for j, s_ in enumerate(S):
            best, arg = None, 0
            for i, s0 in enumerate(S):
                c = cost[i] + (jump(notes[k].pitch + s_ - notes[k - 1].pitch - s0) if cont else 0.0)
                if best is None or c < best:
                    best, arg = c, i
            nc.append(best + abs(s_) / 12 * shift_cost)
            bk.append(arg)
        cost = nc
        back.append(bk)
    j = int(np.argmin(cost))
    choice = [j]
    for bk in reversed(back):
        j = bk[j]
        choice.append(j)
    choice.reverse()
    for n, j in zip(notes, choice):
        n.pitch += S[j]


def _consistent_lines(vnotes, note_syl, width: float = 6.0) -> None:
    """Alle Zeilen in eine gemeinsame Lage bringen: Es wird die Mittellage gesucht, in die (oktavverschoben)
    möglichst viele Zeilen passen – bei Gleichstand mit möglichst wenigen Verschiebungen. Zeilen, die nur
    eine Oktave daneben liegen (Männerstimme, Gruppe), werden dorthin geholt."""
    by_line: dict[int, list] = {}
    for vn in vnotes:
        by_line.setdefault(note_syl[id(vn)].line, []).append(vn)
    if len(by_line) < 2:
        return
    meds = {li: float(np.median([n.pitch for n in ns])) for li, ns in by_line.items()}
    best = None
    for c in range(40, 90):
        fit = moved = 0
        sh = {}
        for li, m in meds.items():
            s_ = min((-12, 0, 12), key=lambda x: (abs(m + x - c), abs(x)))
            if abs(m + s_ - c) <= width:
                fit += len(by_line[li])
                if s_:
                    moved += len(by_line[li])
                    sh[li] = s_
        key = (fit, -moved, -abs(c - 64))
        if best is None or key > best[0]:
            best = (key, sh)
    for li, s_ in best[1].items():
        for n in by_line[li]:
            n.pitch += s_


def _octave_fix_lines(vnotes, note_syl) -> None:
    """(ältere Variante) ganze Zeilen und einzelne Ausreißer um eine Oktave zurückholen."""
    by_line: dict[int, list] = {}
    for vn in vnotes:
        by_line.setdefault(note_syl[id(vn)].line, []).append(vn)
    song_med = float(np.median([vn.pitch for vn in vnotes])) if vnotes else 66.0
    for ln_notes in by_line.values():
        med = float(np.median([vn.pitch for vn in ln_notes]))
        dev = song_med - med
        sh = 12 * int(np.sign(dev)) if abs(dev) >= 8.5 else 0
        for vn in ln_notes:
            vn.pitch += sh


def _norm_octave(notes: list[VNote], lo: int = 57, hi: int = 74) -> int:
    """Leadsheet-Lage: die Oktave, in der die Melodie (nach Dauer gewichtet) am besten in den üblichen
    Notationsbereich a … d'' (Gemeinde-Lage) passt. Robuster als „Median in einer festen Oktave“,
    der bei Melodien um g'/a' herum zwischen zwei Oktaven kippt."""
    if not notes:
        return 0
    p = np.array([n.pitch for n in notes], dtype=float)
    w = np.array([max(0.05, n.end - n.start) for n in notes])
    best, arg = None, 0
    for sh in (-36, -24, -12, 0, 12, 24, 36):
        q = p + sh
        out = np.clip(lo - q, 0, None) + np.clip(q - hi, 0, None)
        cost = float((out * w).sum())
        if best is None or cost < best - 1e-9:
            best, arg = cost, sh
    return arg


def _label_counter(labels):
    from collections import Counter
    return Counter(labels)


def build_song(path, lyrics_text: Optional[str] = None, title: str = '', authors: str = '',
               progress=None, use_asr: bool = True, cache=None, gemeinde: bool = True,
               tempo_faktor: float = 1.0, melodie: str = 'erste') -> AnalysisResult:
    import librosa
    rep = {}

    def step(name, frac):
        if progress:
            progress(name, frac)

    step('Audio laden', 0.02)
    mix22 = load_audio(path, 22050, mono=True)
    step('Gesang und Begleitung trennen', 0.05)
    voc, inst = stems(path, (lambda f: progress('Gesang und Begleitung trennen', 0.05 + 0.45 * f)) if progress else None,
                      cache)
    voc22 = librosa.resample(voc.mean(0), orig_sr=SR, target_sr=22050)
    inst22 = librosa.resample(inst.mean(0), orig_sr=SR, target_sr=22050)
    step('Takt und Tempo', 0.52)
    beats, tempo = track_beats(mix22)
    beats, tempo = scale_beats(beats, tempo, tempo_faktor, mix22)
    step('Akkorde', 0.58)
    probs, fps = chord_probs(mix22)
    bass = bass_per_beat(inst22, beats)
    bc = beat_chords_with_bass(probs, fps, beats, bass, switch_penalty=3.0)
    from .btc import label
    labels = [label(i) for i in bc]
    key_tpc, minor = estimate_key(labels)
    bpb, phase, meter_scores = detect_meter(beat_chords(probs, fps, beats), bass)
    if bpb == 3:
        phase = find_downbeat_phase(bc, bass, 3)
    grid = Grid(beats, phase, bpb)
    rep['takt'] = f'{bpb}/4'
    rep['taktart_werte'] = meter_scores
    step('Leadstimme vom Chor trennen', 0.62)
    # Leadstimme: zwei Trennmodelle – je Song gewinnt später das, bei dem die Wiederholungen einheitlicher klingen
    from . import roformer as _R
    want = os.environ.get('KENANJA_TEST_LEAD', '')
    models = [m for m in ('mdx', 'roformer') if (m == 'mdx' or _R.ready()) and (not want or want == m)]
    lead_cands = []      # (Name, roh, mit Gruppen-Ausgleich)
    for mi, m in enumerate(models):
        try:
            lo, hi = 0.62 + 0.1 * mi / len(models), 0.62 + 0.1 * (mi + 1) / len(models)
            lead = lead_stem(path, voc, (lambda f, lo=lo, hi=hi: progress('Leadstimme vom Chor trennen', lo + (hi - lo) * f))
                             if progress else None, cache, model=m)
            raw = librosa.resample(lead.mean(0), orig_sr=SR, target_sr=22050)
            lead_cands.append(('RoFormer' if m == 'roformer' else 'MDX', raw, lead_or_group(voc22, raw)))
        except Exception as e:   # Modell nicht ladbar o. ä.
            rep['leadstimme'] = f'{m} nicht getrennt ({type(e).__name__})'
    if lead_cands:
        rep['stimmtrennung'], lead22_raw, lead22 = lead_cands[0]
    else:                       # mit der ganzen Gesangsspur weiter
        lead22_raw = lead22 = voc22
    if os.environ.get('KENANJA_TEST_STEM') == 'gesang':
        lead22 = voc22
    step('Melodie', 0.74)
    note_syl: dict[int, Syllable] = {}
    blocks = parse_lyrics(lyrics_text) if lyrics_text and lyrics_text.strip() else []
    if blocks:
        rep['textbloecke'] = [b.label for b in blocks]
    line_ranges = []
    if blocks and gemeinde:
        # Gemeinde-Modus: eine Note je Silbe, Zeilen auf ganzen Takten, gleiche Zeilen gleich notiert
        from . import gemeinde as G
        lang = detect_language(lyrics_text)
        syls = text_syllables(blocks, lang)
        lines = [l for b in blocks for l in b.lines]
        counts = [sum(1 for s_ in syls if s_.line == li) for li in range(len(lines))]

        def _snap(ln):
            tmp = [VNote(0, 0, n.pitch, fpitch=n.fpitch) for n in ln if n.fpitch is not None]
            snap_to_key(tmp, key_tpc, minor)
            it = iter(tmp)
            for n in ln:
                if n.fpitch is not None:
                    n.pitch = next(it).pitch
        step('Liedtext und Melodie', 0.8)
        # 1. Noten wie bisher erkennen und die Zeilen grob zuordnen (liefert Zeilenanfänge als Anker)
        # (ohne Anschleif-Zusammenfassung: kurze Töne mit eigener Silbe sind hier echte Noten; Anschliffe
        #  ohne eigene Silbe gehen später in der Silbennote auf)
        vn0 = vocal_notes(lead22)
        snap_to_key(vn0, key_tpc, minor)
        rg0 = align_lines(counts, merge_scoops([VNote(n.start, n.end, n.pitch, n.strength, n.fpitch) for n in vn0]))
        vm = merge_scoops([VNote(n.start, n.end, n.pitch, n.strength, n.fpitch) for n in vn0])
        anchors = [((grid.beat_pos(vm[r[0]].start) - phase) / bpb if r else None) for r in rg0]
        line_syls = [[s_ for s_ in syls if s_.line == li] for li in range(len(lines))]
        labels = [re.sub(r'[\d\s.:]+$', '', (b_.label or '')).strip().lower() or None for b_ in blocks]
        mel_labels = [None if getattr(b_, 'geraten', False) else lb_ for b_, lb_ in zip(blocks, labels)]
        # 1b. Spracherkennung (optional): Wortzeiten verankern Zeilen und Silben dort, wo sie gesungen wurden
        syl_times = None
        if use_asr:
            from . import asr as A
            try:
                if A.available() and A.model_ready():
                    step('Spracherkennung', 0.78)
                    # ganze Gesangsspur und Leadstimme: zusammen werden deutlich mehr Wörter sicher erkannt
                    wd = work_dir(path, cache)
                    per_stem = []
                    for k, (src, sr_, fn) in enumerate(((voc.mean(0), SR, 'woerter.json'),
                                                        (lead22, 22050, 'woerter_lead.json'))):
                        y16 = librosa.resample(np.asarray(src, dtype=np.float32), orig_sr=sr_, target_sr=16000)
                        per_stem.append(A.words(y16, wd / fn, (lambda f, k=k: progress('Spracherkennung',
                                                                                       0.78 + 0.01 * (k + f)))
                                                if progress else None))
                    found = A.merge(*per_stem)
                    if found:
                        st = A.syllable_times(syls, found)
                        if A.ctc_ready():
                            # alle übrigen Silben per Zwangsausrichtung des bekannten Texts zwischen den Ankern
                            step('Text ausrichten', 0.8)
                            allc = ''.join(sorted(set(''.join(A.clean_word(s_.text) for s_ in syls))))
                            y16v = librosa.resample(voc.mean(0), orig_sr=SR, target_sr=16000)
                            lp, chars = A.ctc_logprobs(y16v, allc, wd / 'ausrichtung.npz')
                            st = A.fill_syllable_times(syls, st, lp, chars, len(y16v) / 16000)
                        syl_times = [[st[k] for k, s_ in enumerate(syls) if s_.line == li] for li in range(len(lines))]
                        nw = sum(1 for s_ in syls if s_.syllabic in ('single', 'begin'))
                        hit = sum(1 for s_, t in zip(syls, st) if t is not None and s_.syllabic in ('single', 'begin'))
                        rep['spracherkennung'] = f'{hit} von {nw} Wörtern verankert' + (
                            '' if A.ctc_ready() else ' (Feinausrichtung fehlt – Spracherkennung neu herunterladen)')
                        for li, ts in enumerate(syl_times):
                            if ts and ts[0] is not None:
                                anchors[li] = (grid.beat_pos(ts[0]) - phase) / bpb
                else:
                    rep['spracherkennung'] = 'nicht eingerichtet'
            except Exception as e:  # Spracherkennung ist optional
                rep['spracherkennung'] = f'nicht verfügbar ({type(e).__name__})'
                syl_times = None
        # 2. Blöcke verorten (Wiederholungen über die Melodie), je Silbe eine Note, gleiche Zeilen angleichen
        old_ranges = [((vm[r[0]].start, vm[r[1] - 1].end) if r and r[1] > r[0] else None) for r in rg0]
        def _transcribe(stem22, vn_, mel=melodie):
            return G.transcribe(
                stem22, beats, phase, bpb, counts, snap=_snap, blocks=[len(b_.lines) for b_ in blocks],
                anchors=anchors, vnotes=vn_, line_syls=line_syls, align=align_lines,
                assign=lambda sy, ns: G.assign_syllables_sung(
                    sy, ns, lambda n: [VNote(n.start, (n.start + n.end) / 2, n.pitch, n.strength, n.fpitch),
                                       VNote((n.start + n.end) / 2, n.end, n.pitch, n.strength, n.fpitch)]),
                labels=labels, texts=lines, old_ranges=old_ranges, syl_times=syl_times, melodie=mel,
                mel_labels=mel_labels,
                scale={(tpc_pc(key_tpc) + d) % 12 for d in ((0, 2, 3, 5, 7, 8, 10) if minor else (0, 2, 4, 5, 7, 9, 11))})
        res_lines, g_tr, _, changed, gmode = _transcribe(lead22, vn0)
        dis_lead = G.STATS.get('ton_angeglichen', 0) / max(1, G.STATS.get('silben_in_gruppen', 0))
        rep['stimme'] = 'Leadstimme'
        stats_best = dict(G.STATS)
        for name_, raw_, l22_ in lead_cands[1:]:
            # weiteres Trennmodell: das mit den einheitlicheren Wiederholungen gewinnt
            step('Melodie gegenprüfen', 0.82)
            vn_c = vocal_notes(l22_)
            snap_to_key(vn_c, key_tpc, minor)
            alt_c = _transcribe(l22_, vn_c)
            dis_c = G.STATS.get('ton_angeglichen', 0) / max(1, G.STATS.get('silben_in_gruppen', 0))
            DEBUG.setdefault('modelle', []).append((rep['stimmtrennung'], dis_lead, name_, dis_c))
            if dis_c < dis_lead:
                res_lines, g_tr, _, changed, gmode = alt_c
                dis_lead, lead22, lead22_raw, rep['stimmtrennung'] = dis_c, l22_, raw_, name_
                stats_best = dict(G.STATS)
        G.STATS.clear(); G.STATS.update(stats_best)
        if syl_times is not None and lead22 is not voc22 and os.environ.get('KENANJA_TEST_STEM') != 'lead':
            # Gegenprobe mit der ganzen Gesangsspur: Manchmal trennt das Lead-Modell die Melodie falsch ab
            # (Frauenstimme als „Chor“ o. ä.). Welche Spur passt, zeigt sich daran, wie einheitlich die
            # wiederholten Zeilen klingen – die stimmigere Fassung gewinnt.
            step('Melodie gegenprüfen', 0.84)
            vn_g = vocal_notes(voc22)
            snap_to_key(vn_g, key_tpc, minor)
            alt = _transcribe(voc22, vn_g)
            dis_voc = G.STATS.get('ton_angeglichen', 0) / max(1, G.STATS.get('silben_in_gruppen', 0))
            DEBUG['uneinig'] = (dis_lead, dis_voc, changed, alt[3])
            if dis_voc < dis_lead - 0.04:
                res_lines, g_tr, _, changed, gmode = alt
                rep['stimme'] = 'ganze Gesangsspur'
            elif os.environ.get('KENANJA_TEST_ZEILENWAHL'):
                # je Zeile: singt die Gruppe laut mit (Chor-/Gruppengesang), trägt sie oft die Melodie, während
                # der Leadsänger eine zweite Stimme singt – dann die Zeile aus der ganzen Gesangsspur nehmen
                theta = float(os.environ['KENANJA_TEST_ZEILENWAHL'])
                n_ = min(len(voc22), len(lead22_raw))
                back22 = voc22[:n_] - lead22_raw[:n_]
                swapped = 0
                for li, (la, lv) in enumerate(zip(res_lines, alt[0])):
                    if not la or not lv:
                        continue
                    a_, b_ = int(la[0][0] * 22050), int(la[-1][1] * 22050)
                    if b_ - a_ < 2205:
                        continue
                    rl = float(np.sqrt(np.mean(lead22_raw[a_:b_] ** 2)) + 1e-9)
                    rb = float(np.sqrt(np.mean(back22[a_:b_] ** 2)) + 1e-9)
                    if rb / rl > theta:
                        res_lines[li] = lv
                        swapped += 1
                rep['stimme'] = f'Leadstimme, {swapped} Zeilen aus der Gruppe'
        if melodie == 'mehrstimmig' and lead22 is not voc22:
            # Mehrstimmig: die Gruppe/Backgroundstimmen (Gesang ohne Lead) Silbe für Silbe mitschreiben und als
            # zweite Stimme dort notieren, wo sie eigenständig neben der Melodie verläuft
            step('Zweite Stimme', 0.86)
            try:
                n_ = min(len(voc22), len(lead22_raw))
                back22 = (voc22[:n_] - lead22_raw[:n_]).astype(np.float32)
                vn_b = vocal_notes(back22)
                snap_to_key(vn_b, key_tpc, minor)
                stats_keep = dict(G.STATS)
                back_lines = _transcribe(back22, vn_b, 'roh')[0]
                G.STATS.clear(); G.STATS.update(stats_keep)
                n2 = 0
                for li, ln in enumerate(res_lines):
                    bl = back_lines[li] if li < len(back_lines) else None
                    if not ln or not bl or len(bl) != len(ln):
                        continue
                    sv = G.second_voice([x[2] for x in ln], [x[2] for x in bl])
                    res_lines[li] = [(x[0], x[1], x[2], x[3], a) for x, a in zip(ln, sv)]
                    n2 += sum(a is not None for a in sv)
                rep['zweite_stimme'] = f'{n2} Töne'
            except Exception as e:
                rep['zweite_stimme'] = f'nicht möglich ({type(e).__name__})'
        rep['zuordnung'] = gmode
        if G.STATS.get('vorlage_uebertragen'):
            rep['melodie'] = f"Melodie aus dem ersten Durchgang übernommen ({G.STATS['vorlage_uebertragen']} Töne angepasst)"
        DEBUG['track'] = (np.asarray(g_tr.t), np.asarray(g_tr.midi))
        rep['_rohnoten'] = len(vm)
        vnotes = []
        for li, ln in enumerate(res_lines):
            if not ln:
                continue
            ls = [s_ for s_ in syls if s_.line == li]
            for (t0, t1, p, fp, *alt), sy in zip(ln, ls):
                vn = VNote(t0, t1, p, fpitch=fp)
                if alt and alt[0] is not None:
                    vn.alt_delta = alt[0] - p
                vnotes.append(vn)
                note_syl[id(vn)] = sy
            line_ranges.append((li, ln[0][0], ln[-1][1]))
        # Oktavlage glätten: die Tonhöhenerkennung springt bei Gruppen-/Männerstimmen gern eine Oktave.
        # Eine Melodie bewegt sich aber fast nur in kleinen Schritten – je Ton wird die Oktave so gewählt,
        # dass der Verlauf möglichst glatt bleibt (dynamische Programmierung über die gesungene Reihenfolge).
        if os.environ.get('KENANJA_TEST_OKT') == 'alt':
            _octave_fix_lines(vnotes, note_syl)
        else:
            _consistent_lines(vnotes, note_syl)
            _smooth_octaves(sorted(vnotes, key=lambda n: n.start),
                            gap=float(os.environ.get('KENANJA_TEST_OKTGAP', '8.0')),
                            shift_cost=float(os.environ.get('KENANJA_TEST_OKTSC', '1.5')))
        # erst jetzt (ohne Ausreißer-Zeilen und -Töne) die Oktave der ganzen Melodie festlegen
        octv = _norm_octave(vnotes)
        for n in vnotes:
            n.pitch += octv
        # Diagnose (nur für Tests): je Silbe Zeit, Ton und ob ihr Wort per Spracherkennung verankert war
        idx_in_line = {id(s_): k for li in range(len(lines)) for k, s_ in enumerate([x for x in syls if x.line == li])}
        DEBUG['silben'] = []
        for vn in vnotes:
            sy = note_syl[id(vn)]
            k = idx_in_line[id(sy)]
            ts_ = syl_times[sy.line] if syl_times else None
            kk = k
            while ts_ and kk > 0 and ts_[kk] is None and [x for x in syls if x.line == sy.line][kk].syllabic in ('middle', 'end'):
                kk -= 1
            DEBUG['silben'].append((sy.line, k, sy.text, vn.start, vn.pitch, bool(ts_ and ts_[kk] is not None),
                                    ts_[k] if ts_ else None))
        DEBUG['beats'] = (np.asarray(beats), phase, bpb)
        rep['zeilen'] = f'{sum(1 for r in res_lines if r)} von {len(lines)} Zeilen zugeordnet'
        rep['angeglichen'] = changed
        rep.update(tempo=round(tempo, 1), key=key_name(key_tpc, minor), noten=len(vnotes))
    else:
        vnotes = merge_scoops(vocal_notes(lead22))
        snap_to_key(vnotes, key_tpc, minor)
        octv = _norm_octave(vnotes)
        for n in vnotes:
            n.pitch += octv
        _fix_octaves(vnotes)
        rep.update(tempo=round(tempo, 1), key=key_name(key_tpc, minor), noten=len(vnotes))

    # Text
    step('Liedtext', 0.8)
    if blocks and not gemeinde:
        lang = detect_language(lyrics_text)
        syls = text_syllables(blocks, lang)
        lines = [l for b in blocks for l in b.lines]
        counts = [sum(1 for s in syls if s.line == li) for li in range(len(lines))]
        ranges = None
        if use_asr:
            try:
                step('Spracherkennung (Text verankern)', 0.72)
                voc16 = librosa.resample(voc.mean(0), orig_sr=SR, target_sr=16000)
                asr = transcribe_words(voc16, lang)
                if asr:
                    ranges = align_lines_asr(lines, asr, vnotes)
                    if ranges:
                        # nicht verankerte Zeilen zwischen ihren Nachbarn per Silbenzählung einpassen
                        k = 0
                        while k < len(ranges):
                            if ranges[k] is not None:
                                k += 1
                                continue
                            e = k
                            while e < len(ranges) and ranges[e] is None:
                                e += 1
                            lo = ranges[k - 1][1] if k > 0 and ranges[k - 1] else 0
                            hi = ranges[e][0] if e < len(ranges) and ranges[e] else len(vnotes)
                            sub = align_lines(counts[k:e], vnotes[lo:hi]) if hi > lo else [None] * (e - k)
                            for q, r in enumerate(sub):
                                ranges[k + q] = (r[0] + lo, r[1] + lo) if r else None
                            k = e
                        rep['spracherkennung'] = f'{len(asr)} Wörter erkannt'
            except Exception as e:  # Spracherkennung ist optional
                rep['spracherkennung'] = f'nicht verfügbar ({type(e).__name__})'
                ranges = None
        if ranges is None:
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
        # Lage nur nach den gesungenen Textnoten nachjustieren (Ad-libs, Instrumente im Outro o. ä. zählen nicht)
        sung = [n for n in vnotes if id(n) in note_syl]
        if len(sung) >= 8:
            d_oct = _norm_octave(sung)
            if d_oct:
                for n in vnotes:
                    n.pitch += d_oct

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
    prev = None
    for b in range(n_bars):
        bar = Bar(bpb, 4, audio_start=grid.bar_time(b))
        for k in range(bpb):
            i = phase + b * bpb + k
            if i >= n_beats:
                break
            sym = seg_sym[i]
            if sym and sym != prev:
                bar.chords.append(ChordEvent(F(k), sym))
            prev = sym
        bars.append(bar)
    # Lesbarkeit: im selben Takt gleicher Akkord mit anderem Basston -> nur der erste; Ein-Schlag-Slashakkorde weg
    for bar in bars:
        merged = []
        for c in bar.chords:
            try:
                ch = ChordSymbol.parse(c.symbol)
            except ValueError:
                merged.append(c)
                continue
            if merged:
                try:
                    pc = ChordSymbol.parse(merged[-1].symbol)
                    if pc.root == ch.root and pc.quality == ch.quality:
                        continue
                except ValueError:
                    pass
            merged.append(c)
        out_c = []
        for k, c in enumerate(merged):
            end = merged[k + 1].onset if k + 1 < len(merged) else bar.length
            if '/' in c.symbol and end - c.onset <= 1 and len(merged) > 2:
                continue
            out_c.append(c)
        bar.chords = out_c
    # Akkorde nur bei Wechsel (der Rest wird beim Satz ergänzt)
    # Melodie quantisieren
    events = []
    qres = 2 if tempo >= 100 else 4          # schnelle Lieder: Achtelraster, sonst Sechzehntel
    for n in vnotes:
        b0, p0 = grid.to_bar(n.start, qres)
        b1, p1 = grid.to_bar(n.end, qres)
        if b0 < 0 or b0 >= len(bars):
            continue
        a = b0 * bpb + p0
        e = b1 * bpb + p1
        if e <= a:
            e = a + F(1, qres)
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
        if 0 < cleaned[k + 1][0] - cleaned[k][1] <= F(1, 2):
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
            delta = getattr(n, 'alt_delta', None)
            if delta:                     # mehrstimmig: zusätzlich gesungene zweite Stimme
                note.extra = [(n.pitch + delta, spell_pitch(n.pitch + delta, key_tpc))]
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
    rep['unsicher'] = mark_outliers(sections) + mark_rare_extremes(sections)
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
    for item in song.form:
        cue = getattr(song.section(item.section), '_cue', '')
        if cue:
            item.note = f'({cue})'
    for sec in song.sections:
        if hasattr(sec, '_cue'):
            delattr(sec, '_cue')
    if note_syl:
        # Plausibilität: erkannte Gesangsnoten je Textsilbe (im Gemeinde-Modus die Rohnoten vor der Vereinfachung)
        raw = rep.pop('_rohnoten', None)
        sung = len(note_syl) if raw is not None else sum(1 for n in vnotes if id(n) in note_syl)
        rate = (raw if raw is not None else len(vnotes)) / max(1, sung)
        rep['noten_pro_silbe'] = round(rate, 2)
        if rate < 1.05 and len(note_syl) > 40:
            rep['warnung'] = ('Der Text scheint länger zu sein als das, was in der Aufnahme gesungen wird – '
                              'vielleicht stammt er von einer anderen Version. Bitte Ablauf prüfen.')
    song.notes['analyse'] = rep
    step('Fertig', 1.0)
    return AnalysisResult(song, rep)


def mark_outliers(sections: list[Section], max_dur=F(1, 2), jump: int = 3) -> int:
    """Kurze Töne, die deutlich über bzw. unter beiden Nachbarn liegen (typisch: Anschleifen von weit unten,
    Kickser, Oktavfehler der Tonhöhenerkennung), als unsicher markieren. Sie bleiben im Leadsheet stehen,
    zählen aber nicht für Spitzentöne und Tonart-Vorschlag."""
    seq = []   # (Note, Abschnitt, Takt, absolute Position in Vierteln)
    pos = F(0)
    for sec in sections:
        for bi, b in enumerate(sec.bars):
            for n in b.melody:
                if n.pitch is not None:
                    seq.append((n, pos + n.onset))
            pos += b.length
    marked = 0
    for i, (n, at) in enumerate(seq):
        if n.dur > max_dur:
            continue
        nb = []
        if i > 0 and at - (seq[i - 1][1] + seq[i - 1][0].dur) <= 1:
            nb.append(seq[i - 1][0].pitch)
        if i + 1 < len(seq) and seq[i + 1][1] - (at + n.dur) <= 1:
            nb.append(seq[i + 1][0].pitch)
        if not nb:
            continue
        if n.pitch >= max(nb) + jump or n.pitch <= min(nb) - jump:
            n.unsicher = True
            marked += 1
    return marked


def mark_rare_extremes(sections: list[Section], max_count: int = 2, max_beats=F(1), gap: int = 3) -> int:
    """Höchster/tiefster Ton, der nur ein-, zweimal kurz vorkommt und deutlich (≥ gap Halbtöne) über bzw. unter
    allen anderen liegt, ist meist ein Erkennungsfehler oder eine Verzierung: unsicher markieren (zählt dann nicht
    für Spitzentöne und Tonart-Vorschlag, bleibt aber im Leadsheet und unter „Spitzentöne prüfen“ sichtbar)."""
    notes = [n for sec in sections for b in sec.bars for n in b.melody if n.pitch is not None and not n.unsicher]
    marked = 0
    for side in (1, -1):
        for _ in range(1):
            ps = sorted({n.pitch for n in notes if not n.unsicher}, reverse=side > 0)
            if len(ps) < 4:
                break
            ext, nxt = ps[0], ps[1]
            ns = [n for n in notes if n.pitch == ext and not n.unsicher]
            if abs(ext - nxt) >= gap and len(ns) <= max_count and sum((n.dur for n in ns), F(0)) <= max_beats:
                for n in ns:
                    n.unsicher = True
                    marked += 1
            else:
                break
    return marked


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
            spans.append([max(0, b0), min(nb, b1 + (1 if p1 > 0 else 0)), blocks[bi].label, '\n'.join(blocks[bi].lines),
                          ' / '.join(c for _, c in getattr(blocks[bi], 'cues', []))])
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
                spans.append([a, min(end, a + 8), None, None, ''])
            i = j
    # Lücken auffüllen (Intro/Interlude/Outro) und Grenzen bereinigen
    spans.sort()
    out = []
    pos = 0
    for k, (a, b, label, text, cue) in enumerate(spans):
        a = max(a, pos)
        if b <= a:
            continue
        if a > pos:
            if out and a - pos <= 2:
                out[-1][1] = a           # kurze Lücke = Atempause, gehört zum vorigen Teil
            else:
                gap_label = 'Intro' if not out else 'Interlude'
                out.append([pos, a, gap_label, None, ''])
        out.append([a, b, label, text, cue])
        pos = b
    if pos < nb:
        if out and (str(out[-1][2]).lower().startswith('outro') or nb - pos <= 2):
            out[-1][1] = nb
        else:
            out.append([pos, nb, 'Outro', None, ''])
    # Beschriftung ohne Text: ähnliche Teile gruppieren (Grundtöne pro Takt), häufigster später Teil = Chorus
    if not blocks:
        def roots(a, b):
            out = []
            for bar in bars[a:b]:
                r = None
                if bar.chords:
                    try:
                        r = ChordSymbol.parse(bar.chords[0].symbol).root_pc()
                    except ValueError:
                        r = None
                out.append(r)
            return out

        def sim(x, y):
            n = min(len(x), len(y))
            if n == 0:
                return 0.0
            return sum(1 for i in range(n) if x[i] is not None and x[i] == y[i]) / max(len(x), len(y))
        vocal = [k for k, o in enumerate(out) if o[2] is None]
        groups: list[list[int]] = []
        for k in vocal:
            rk = roots(out[k][0], out[k][1])
            for g in groups:
                if sim(rk, roots(out[g[0]][0], out[g[0]][1])) >= 0.6:
                    g.append(k)
                    break
            else:
                groups.append([k])
        if groups:
            first = groups[0]
            rest = sorted(groups[1:], key=lambda g: -len(g))
            for k in first:
                out[k][2] = 'Verse'
            if rest:
                for k in rest[0]:
                    out[k][2] = 'Chorus'
                for g in rest[1:]:
                    for k in g:
                        out[k][2] = 'Bridge' if len(g) <= 2 else 'Pre-Chorus'
            # Wenn alles eine Gruppe ist: abwechselnd Verse/Chorus ist geraten -> neutral lassen
    from ..importers.musescore import classify_label, SECTION_ABBR
    counts = {}
    total = {}
    for a, b, label, _, _ in out:
        kind, nice, num = classify_label(label or 'Verse')
        total[nice] = total.get(nice, 0) + 1
    secs = []
    for a, b, label, text, cue in out:
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
        if cue:
            sec._cue = cue
        secs.append(sec)
    return secs
