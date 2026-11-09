"""Spracherkennung mit Wortzeiten (optional): verankert den eingefügten Liedtext in der Aufnahme.

Nutzt sherpa-onnx mit einem mehrsprachigen Parakeet-Modell (u. a. Deutsch und Englisch). Das Modell wird bei
Bedarf einmalig heruntergeladen (rund 650 MB). Fehlt sherpa-onnx oder das Modell, liefert ``words`` eine leere
Liste und die Analyse läuft ohne Spracherkennung weiter.
"""
from __future__ import annotations

import json
import re
import tarfile
import unicodedata
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import numpy as np

URL = ('https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/'
       'sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2')
FILES = ('encoder.int8.onnx', 'decoder.int8.onnx', 'joiner.int8.onnx', 'tokens.txt')
SR = 16000


def model_dir() -> Path:
    from .btc import cache_dir
    return cache_dir() / 'sprache' / 'parakeet-v3'


def available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception:
        return False


def model_ready() -> bool:
    d = model_dir()
    return all((d / f).exists() for f in FILES)


def ensure_model(progress=None) -> Path:
    """Modell herunterladen und entpacken (nur die benötigten Dateien)."""
    d = model_dir()
    if model_ready():
        return d
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / 'download.part'

    def hook(blocks, bs, total):
        if progress and total > 0:
            progress(min(1.0, blocks * bs / total))
    urllib.request.urlretrieve(URL, tmp, hook)
    with tarfile.open(tmp, 'r:bz2') as tf:
        for m in tf.getmembers():
            name = Path(m.name).name
            if m.isfile() and name in FILES:
                src = tf.extractfile(m)
                with open(d / (name + '.part'), 'wb') as f:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                (d / (name + '.part')).rename(d / name)
    tmp.unlink(missing_ok=True)
    if not model_ready():
        raise RuntimeError('Sprachmodell unvollständig')
    return d


_REC = None


def _recognizer():
    global _REC
    if _REC is None:
        import os
        import sherpa_onnx as so
        d = model_dir()
        _REC = so.OfflineRecognizer.from_transducer(
            encoder=str(d / FILES[0]), decoder=str(d / FILES[1]), joiner=str(d / FILES[2]), tokens=str(d / FILES[3]),
            num_threads=max(1, min(8, (os.cpu_count() or 2))), model_type='nemo_transducer')
    return _REC


def _chunks(y: np.ndarray, lo: float = 6.0, hi: float = 11.0) -> list[tuple[int, int]]:
    """Abschnitte von lo..hi Sekunden, geschnitten an der leisesten Stelle (möglichst zwischen Wörtern)."""
    hop = SR // 50
    rms = np.sqrt(np.convolve(y ** 2, np.ones(hop) / hop, mode='same')[::hop] + 1e-12)
    rms = np.convolve(rms, np.ones(5) / 5, mode='same')
    out, a = [], 0
    n = len(y)
    while a < n:
        if n - a <= hi * SR:
            out.append((a, n))
            break
        f0, f1 = (a + int(lo * SR)) // hop, (a + int(hi * SR)) // hop
        b = (f0 + int(np.argmin(rms[f0:f1]))) * hop
        out.append((a, b))
        a = b
    return out


def words(y16: np.ndarray, cache: Optional[Path] = None, progress=None) -> list[tuple[float, float, str]]:
    """[(start, ende, wort)] für die ganze Spur (16 kHz mono)."""
    if cache is not None and cache.exists():
        try:
            return [tuple(w) for w in json.loads(cache.read_text())]
        except Exception:
            pass
    if not available() or not model_ready():
        return []
    rec = _recognizer()
    y = y16.astype(np.float32)
    peak = float(np.abs(y).max()) or 1.0
    y = y / peak * 0.5
    spans = _chunks(y)
    out: list[list] = []
    for k, (a, b) in enumerate(spans):
        if progress:
            progress(k / max(1, len(spans)))
        seg = y[a:b]
        if np.sqrt(np.mean(seg ** 2)) < 0.004:
            continue
        s = rec.create_stream()
        s.accept_waveform(SR, seg)
        rec.decode_stream(s)
        r = s.result
        t0 = a / SR
        cur = None
        for tok, ts in zip(r.tokens, r.timestamps):
            new_word = tok.startswith((' ', '▁')) or cur is None or not re.search(r'\w', cur[2] + tok)
            if new_word:
                cur = [t0 + float(ts), t0 + float(ts) + 0.2, tok.strip(' ▁')]
                out.append(cur)
            else:
                cur[2] += tok
                cur[1] = t0 + float(ts) + 0.2
    res = [(float(a), float(b), w) for a, b, w in out if re.search(r'\w', w)]
    for k in range(len(res) - 1):     # Wortende höchstens bis zum nächsten Wort
        if res[k][1] > res[k + 1][0]:
            res[k] = (res[k][0], res[k + 1][0], res[k][2])
    if cache is not None:
        try:
            cache.write_text(json.dumps(res))
        except Exception:
            pass
    return res


def merge(*lists: list) -> list[tuple[float, float, str]]:
    """Wortlisten mehrerer Spuren zusammenführen. Doppelte bleiben drin: die Zuordnung wählt die passendere
    Fassung; dass ein Wort nicht zweimal benutzt wird, stellt syllable_times sicher."""
    return sorted(w for l in lists for w in l)


def norm(w: str) -> str:
    w = unicodedata.normalize('NFKD', w.lower())
    return ''.join(c for c in w if c.isalnum())


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return -1.0
    if a == b:
        return 2.0
    r = SequenceMatcher(None, a, b).ratio()
    if r >= 0.75:
        return 1.2
    if len(a) >= 3 and len(b) >= 3 and (a[:3] == b[:3] or a[-3:] == b[-3:]) and r >= 0.5:
        return 0.6
    return -0.8 if r < 0.4 else -0.3


def match(text_words: list[str], asr: list[tuple[float, float, str]]) -> list[Optional[int]]:
    """Liedtext-Wörter (gesungene Reihenfolge) monoton auf erkannte Wörter abbilden; je Wort Index oder None.
    Zusätzliche Wörter in der Aufnahme (Ad-libs, Wiederholungen) und Lücken im Text sind billig."""
    A = [norm(w) for _, _, w in asr]
    T = [norm(w) for w in text_words]
    n, m = len(T), len(A)
    if not n or not m:
        return [None] * n
    GT, GA = -0.35, -0.15
    S = np.zeros((n + 1, m + 1), dtype=np.float32)
    S[1:, 0] = np.arange(1, n + 1) * GT
    S[0, 1:] = 0.0                       # freier Anfang in der Aufnahme
    bt = np.zeros((n + 1, m + 1), dtype=np.int8)
    sim = np.array([[_sim(t, a) for a in A] for t in T], dtype=np.float32)
    for i in range(1, n + 1):
        d = S[i - 1, :-1] + sim[i - 1]
        u = S[i - 1, 1:] + GT
        row = np.empty(m + 1, dtype=np.float32)
        row[0] = S[i, 0]
        b = np.zeros(m + 1, dtype=np.int8)
        best_du = np.maximum(d, u)
        b_du = np.where(d >= u, 0, 1).astype(np.int8)
        for j in range(1, m + 1):
            left = row[j - 1] + GA
            if best_du[j - 1] >= left:
                row[j] = best_du[j - 1]
                b[j] = b_du[j - 1]
            else:
                row[j] = left
                b[j] = 2
        S[i] = row
        bt[i] = b
    i, j = n, int(np.argmax(S[n]))       # freies Ende in der Aufnahme
    res: list[Optional[int]] = [None] * n
    while i > 0 and j > 0:
        k = bt[i, j]
        if k == 0:
            if sim[i - 1, j - 1] > 0:
                res[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif k == 1:
            i -= 1
        else:
            j -= 1
    # einzelne Treffer weit weg von ihren Nachbarn sind unsicher
    idx = [k for k, r in enumerate(res) if r is not None]
    orig = list(res)
    for q, k in enumerate(idx):
        prv = idx[q - 1] if q else None
        nxt = idx[q + 1] if q + 1 < len(idx) else None
        ok = False
        for o in (prv, nxt):
            if o is not None and abs(o - k) <= 3 and abs(asr[orig[o]][0] - asr[orig[k]][0]) < 8.0:
                ok = True
        if not ok and sim[k, orig[k]] < 2.0:
            res[k] = None
    return res


def syllable_times(syls: list, asr: list[tuple[float, float, str]]) -> list[Optional[float]]:
    """Je Silbe die Startzeit ihres Worts, falls das Wort sicher erkannt wurde (nur für Wortanfänge)."""
    words_, first = [], []
    cur = ''
    for k, s in enumerate(syls):
        if s.syllabic in ('single', 'begin') or not cur:
            if cur:
                words_.append(cur)
            cur = ''
            first.append(k)
        cur += s.text
    if cur:
        words_.append(cur)
    m = match(words_, asr)
    last = None
    for wi, aj in enumerate(m):          # dasselbe Wort aus zwei Spuren nicht zweimal verwenden
        if aj is None:
            continue
        if last is not None and asr[aj][0] - asr[last][0] < 0.2:
            m[wi] = None
            continue
        last = aj
    out: list[Optional[float]] = [None] * len(syls)
    for wi, aj in enumerate(m):
        if aj is not None and wi < len(first):
            out[first[wi]] = asr[aj][0]
    # je Zeile: Treffer, die zeitlich nicht zum Rest der Zeile passen, verwerfen (falsche Wiederholung o. ä.)
    by_line: dict = {}
    for k, s in enumerate(syls):
        by_line.setdefault(s.line, []).append(k)
    for ks in by_line.values():
        an = [(q, out[k]) for q, k in enumerate(ks) if out[k] is not None]
        if len(an) < 2:
            continue
        off = np.array([t - 0.4 * q for q, t in an])
        med = float(np.median(off))
        tol = 2.5 if len(an) >= 3 else 1e9
        bad = [i for i, o in enumerate(off) if abs(o - med) > tol]
        if len(an) == 2:
            (q0, t0), (q1, t1) = an
            if t1 - t0 > 1.0 * (q1 - q0) + 2.5:
                bad = [1]
        for i in bad:
            out[ks[an[i][0]]] = None
    return out
