"""Spracherkennung mit Wortzeiten (optional): verankert den eingefügten Liedtext in der Aufnahme.

Nutzt sherpa-onnx mit einem mehrsprachigen Parakeet-Modell (u. a. Deutsch und Englisch). Das Modell wird bei
Bedarf einmalig heruntergeladen (zusammen rund 1 GB). Fehlt sherpa-onnx oder das Modell, liefert ``words`` eine leere
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


def all_ready() -> bool:
    return model_ready() and ctc_ready()


def ensure_model(progress=None) -> Path:
    """Beide Modelle (Worterkennung ~650 MB, Textausrichtung ~350 MB) herunterladen und entpacken."""
    d = model_dir()
    if model_ready():
        if not ctc_ready():
            _download(CTC_URL, ctc_dir(), CTC_FILES, (lambda f: progress(0.65 + 0.35 * f)) if progress else None)
        return d
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / 'download.part'

    def hook(blocks, bs, total):
        if progress and total > 0:
            progress(0.65 * min(1.0, blocks * bs / total))
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
    if not ctc_ready():
        _download(CTC_URL, ctc_dir(), CTC_FILES, (lambda f: progress(0.65 + 0.35 * f)) if progress else None)
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


# ---------------------------------------------------------------- Zwangsausrichtung (CTC) ----
# Zwischen sicher erkannten Wörtern werden die übrigen Wörter des bekannten Liedtexts per CTC-Ausrichtung
# (mehrsprachiges Buchstabenmodell) zeitlich festgelegt.
CTC_URL = ('https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/'
           'sherpa-onnx-omnilingual-asr-1600-languages-300M-ctc-int8-2025-11-12.tar.bz2')
CTC_FILES = ('model.int8.onnx', 'tokens.txt')
CTC_FR = 0.02


def ctc_dir() -> Path:
    from .btc import cache_dir
    return cache_dir() / 'sprache' / 'omni-ctc'


def ctc_ready() -> bool:
    return all((ctc_dir() / f).exists() for f in CTC_FILES)


def _download(url: str, d: Path, files: tuple, progress=None):
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / 'download.part'

    def hook(blocks, bs, total):
        if progress and total > 0:
            progress(min(1.0, blocks * bs / total))
    urllib.request.urlretrieve(url, tmp, hook)
    with tarfile.open(tmp, 'r:bz2') as tf:
        for m in tf.getmembers():
            name = Path(m.name).name
            if m.isfile() and name in files:
                src = tf.extractfile(m)
                with open(d / (name + '.part'), 'wb') as f:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                (d / (name + '.part')).rename(d / name)
    tmp.unlink(missing_ok=True)


_CTC = None


def _ctc():
    global _CTC
    if _CTC is None:
        import os
        import onnxruntime as ort
        so_ = ort.SessionOptions()
        so_.intra_op_num_threads = max(1, min(8, os.cpu_count() or 2))
        sess = ort.InferenceSession(str(ctc_dir() / CTC_FILES[0]), so_, providers=['CPUExecutionProvider'])
        tok = {}
        for i, l in enumerate(open(ctc_dir() / CTC_FILES[1], encoding='utf-8')):
            t = l.rstrip('\n').rsplit(' ', 1)[0]
            tok.setdefault(t if t else ' ', i)
        _CTC = (sess, tok)
    return _CTC


def ctc_logprobs(y16: np.ndarray, chars: str, cache: Optional[Path] = None) -> tuple[np.ndarray, str]:
    """Log-Wahrscheinlichkeiten je 20-ms-Frame für [blank] + chars (nur die im Text vorkommenden Buchstaben)."""
    sess, tok = _ctc()
    chars = ''.join(c for c in dict.fromkeys(chars) if c in tok)
    if cache is not None and cache.exists():
        try:
            z = np.load(cache, allow_pickle=False)
            if str(z['chars']) == chars:
                return z['lp'], chars
        except Exception:
            pass
    cols = [0] + [tok[c] for c in chars]
    y = y16.astype(np.float32)
    y = y / (float(np.abs(y).max()) or 1.0) * 0.5
    W, O = 30 * SR, 2 * SR
    n = int(np.ceil(len(y) / 320))
    out = np.full((n, len(cols)), -30.0, np.float32)
    a = 0
    while a < len(y):
        seg = y[max(0, a - O):a + W + O]
        lg = sess.run(None, {'x': seg[None]})[0][0]
        lg = lg - lg.max(1, keepdims=True)
        lp = (lg - np.log(np.exp(lg).sum(1, keepdims=True)))[:, cols]
        off = (a - max(0, a - O)) // 320
        f0 = a // 320
        k = min(W // 320, n - f0, len(lp) - off)
        if k > 0:
            out[f0:f0 + k] = lp[off:off + k]
        a += W
    if cache is not None:
        try:
            with open(cache, 'wb') as f:
                np.savez(f, lp=out, chars=np.array(chars))
        except Exception:
            pass
    return out, chars


def _viterbi(X: np.ndarray, seq: list[int]):
    """CTC-Viterbi: X (T, V) mit Spalte 0 = blank; seq = Spaltenindizes. Liefert Zustandsfolge (T,) oder None."""
    S = len(seq)
    T = len(X)
    if S == 0 or T < S:
        return None
    L = 2 * S + 1
    lab = np.zeros(L, int)
    lab[1::2] = seq
    skip = np.zeros(L, bool)
    if S > 1:
        skip[3::2] = np.array(seq[1:]) != np.array(seq[:-1])
    NEG = -1e9
    dp = np.full(L, NEG)
    dp[0] = X[0, 0]
    dp[1] = X[0, lab[1]]
    bp = np.zeros((T, L), np.int8)
    for t in range(1, T):
        a1 = np.concatenate([[NEG], dp[:-1]])
        a2 = np.where(skip, np.concatenate([[NEG, NEG], dp[:-2]]), NEG)
        st = np.stack([dp, a1, a2])
        b = st.argmax(0)
        dp = st.max(0) + X[t, lab]
        bp[t] = b
    s = L - 1 if dp[L - 1] >= dp[L - 2] else L - 2
    if dp[s] < NEG / 2:
        return None
    path = np.zeros(T, int)
    for t in range(T - 1, -1, -1):
        path[t] = s
        s -= int(bp[t, s])
    return path


def align_words(lp: np.ndarray, chars: str, words: list[str], t0: float, t1: float, char_times=None):
    """Wörter (in Reihenfolge) im Zeitfenster [t0, t1) ausrichten. Liefert je Wort (start, güte) oder None."""
    f0, f1 = max(0, int(t0 / CTC_FR)), min(len(lp), int(t1 / CTC_FR))
    X = lp[f0:f1]
    if len(X) < 2:
        return [None] * len(words)
    ref = X.max(1)
    seq, wid = [], []
    for k, w in enumerate(words):
        for c in w:
            if c in chars:
                seq.append(chars.index(c) + 1)
                wid.append(k)
    path = _viterbi(X, seq)
    if path is None:
        return [None] * len(words)
    if char_times is not None:          # Zeit jedes Buchstabens (erstes Frame seines Zustands)
        ct = [None] * len(seq)
        for t, st in enumerate(path):
            if st % 2 == 1 and ct[st // 2] is None:
                ct[st // 2] = (f0 + t) * CTC_FR
        char_times.extend(ct)
    first = [None] * len(words)
    score = [[] for _ in words]
    for t, st in enumerate(path):
        if st % 2 == 1:
            k = wid[st // 2]
            if first[k] is None:
                first[k] = (f0 + t) * CTC_FR
            score[k].append(float(X[t, seq[st // 2]] - ref[t]))
    return [(first[k], float(np.mean(score[k]))) if first[k] is not None else None for k in range(len(words))]


def clean_word(w: str) -> str:
    return re.sub(r"[^\w']", '', w.lower().replace('’', "'")).replace('_', '')


def fill_syllable_times(syls: list, st: list, lp: np.ndarray, chars: str, t_end: float) -> list:
    """Silbenzeiten für alle Silben: erkannte Wörter (st, nur Wortanfänge) bleiben fest, alle Silben dazwischen
    werden per CTC-Ausrichtung des bekannten Texts gesetzt."""
    first, wsyl = [], []                    # je Wort: erste Silbe, Silbenindizes
    for k, s_ in enumerate(syls):
        if s_.syllabic in ('single', 'begin') or not wsyl:
            first.append(k)
            wsyl.append([k])
        else:
            wsyl[-1].append(k)
    nw = len(first)
    anc = [st[k] for k in first]
    out = list(st)
    idx = [i for i, a in enumerate(anc) if a is not None]
    if not idx:
        return out
    segs = []
    i0 = idx[0]
    if i0 > 0:
        segs.append((0, i0 + 1, max(0.0, anc[i0] - (1.2 * i0 + 4)), anc[i0] + 1.5, i0))
    for q in range(len(idx) - 1):
        i, j = idx[q], idx[q + 1]
        if j - i < 1:
            continue
        ta, tb = anc[i], anc[j]
        if tb - ta > max(30.0, 2.5 * (j - i) + 8):
            continue                         # große Lücke: vermutlich nicht gesungener Text
        segs.append((i, j, ta - 0.3, tb - 0.02, None))
    il = idx[-1]
    if il < nw - 1:
        segs.append((il, nw, anc[il] - 0.3, min(t_end, anc[il] + 1.2 * (nw - il) + 4), None))
    for (i, j, ta, tb, last_fixed) in segs:
        words = []
        spans = []                           # je Silbe: Buchstabenbereich in der Folge
        pos = 0
        for w in range(i, j):
            wtxt = ''
            for k in wsyl[w]:
                c = ''.join(ch for ch in clean_word(syls[k].text) if ch in chars)
                spans.append((k, pos, pos + len(c)))
                pos += len(c)
                wtxt += c
            words.append(wtxt)
        ct: list = []
        r = align_words(lp, chars, words, ta, tb, ct)
        if not ct:
            continue
        for k, a, b in spans:
            if a < b and a < len(ct) and ct[a] is not None and out[k] is None:
                out[k] = ct[a]
    # Zeiten müssen ansteigen: Ausreißer verwerfen (feste Anker haben Vorrang)
    last = -1.0
    for k in range(len(out)):
        if out[k] is None:
            continue
        if out[k] <= last + 0.04 and st[k] is None:
            out[k] = None
            continue
        last = out[k]
    return out
