"""Leadstimme von der Gruppe trennen – mit einem BS-RoFormer-Karaoke-Modell (deutlich besser als MDX-KARA,
v. a. bei Gruppengesang mit Leadsänger in einer anderen Stimme).

Die Netzstruktur liegt als ONNX-Graph (ohne Gewichte) bei. Die Gewichte stammen aus dem öffentlich
bereitgestellten Modell (frazer & becruily) und werden beim ersten Mal heruntergeladen und für onnxruntime
abgelegt – ganz ohne PyTorch. Läuft auf dem Prozessor; etwa so lange wie der Song selbst bis ein paarmal so lange.
"""
from __future__ import annotations

import json
import os
import pickle
import shutil
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

from .btc import cache_dir

CKPT_URL = ('https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/'
            'bs_roformer_karaoke_frazer_becruily.ckpt')
HERE = Path(__file__).resolve().parent / 'modelle'
GRAPH = HERE / 'lead_roformer.onnx'
MAP = HERE / 'lead_roformer.json'
WEIGHTS = 'lead_roformer.weights'
N_FFT, HOP, SR = 2048, 441, 44100


def model_dir() -> Path:
    return cache_dir() / 'lead-roformer'


def available() -> bool:
    return GRAPH.exists() and MAP.exists()


def ready() -> bool:
    d = model_dir()
    return available() and (d / 'ok').exists() and (d / WEIGHTS).exists()


# --------------------------------------------------------------- Checkpoint ohne PyTorch lesen ----

class _Storage:
    def __init__(self, key, dtype):
        self.key, self.dtype = key, dtype


_DT = {'FloatStorage': np.float32, 'HalfStorage': np.float16, 'DoubleStorage': np.float64,
       'BFloat16Storage': 'bf16', 'LongStorage': np.int64, 'IntStorage': np.int32}


def load_checkpoint(path) -> dict:
    """PyTorch-Checkpoint (zip) als {Name: numpy-Array} lesen, ohne torch zu importieren."""
    z = zipfile.ZipFile(path)
    pkl = next(n for n in z.namelist() if n.endswith('/data.pkl') or n == 'data.pkl')
    root = pkl[:-len('data.pkl')]

    def rebuild(storage, offset, size, stride, *args):
        raw = z.read(f'{root}data/{storage.key}')
        if storage.dtype == 'bf16':
            a = (np.frombuffer(raw, np.uint16).astype(np.uint32) << 16).view(np.float32)
        else:
            a = np.frombuffer(raw, storage.dtype)
        size, stride = tuple(size), tuple(stride)
        if not size:
            return a[offset:offset + 1].reshape(()).copy()
        it = a.itemsize
        return np.lib.stride_tricks.as_strided(a[offset:], size, tuple(s * it for s in stride)).copy()

    class U(pickle.Unpickler):
        def find_class(self, mod, name):
            if mod == 'torch._utils' and name in ('_rebuild_tensor_v2', '_rebuild_tensor'):
                return rebuild
            if mod == 'torch' and name in _DT:
                return name
            if mod == 'collections' and name == 'OrderedDict':
                import collections
                return collections.OrderedDict
            if mod == 'torch._utils' and name == '_rebuild_parameter':
                return lambda data, *a: data
            raise pickle.UnpicklingError(f'nicht erlaubt: {mod}.{name}')

        def persistent_load(self, pid):
            # ('storage', storage_type, key, location, numel)
            _, stype, key, _loc, _n = pid
            return _Storage(key, _DT.get(stype if isinstance(stype, str) else getattr(stype, '__name__', ''), np.float32))

    sd = U(z.open(pkl)).load()
    return sd.get('state_dict', sd) if isinstance(sd, dict) else sd


# --------------------------------------------------------------- Einrichten ----

def ensure_model(progress=None) -> Path:
    """Gewichte laden (≈ 200 MB) und für den mitgelieferten Graphen ablegen."""
    d = model_dir()
    if ready():
        return d
    if not available():
        raise RuntimeError('Netzstruktur fehlt in dieser Installation')
    d.mkdir(parents=True, exist_ok=True)
    ck = d / 'modell.ckpt'
    if not ck.exists():
        tmp = ck.with_suffix('.part')
        with urllib.request.urlopen(CKPT_URL, timeout=60) as r, open(tmp, 'wb') as f:
            total = int(r.headers.get('Content-Length') or 0)
            done = 0
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                done += len(b)
                if progress and total:
                    progress(0.95 * done / total)
        tmp.rename(ck)
    build_weights(ck, d)
    ck.unlink(missing_ok=True)
    if progress:
        progress(1.0)
    return d


def build_weights(ckpt, d: Path) -> None:
    sd = load_checkpoint(ckpt)
    spec = json.loads(MAP.read_text())
    tmp = d / (WEIGHTS + '.part')
    with open(tmp, 'wb') as f:
        for e in spec['tensoren']:
            a = np.asarray(sd[e['quelle']], dtype=np.float32)
            if e['art'] == 'T':
                a = a.T
            a = np.ascontiguousarray(a.reshape(e['form']))
            if f.tell() != e['offset']:
                f.write(b'\0' * (e['offset'] - f.tell()))
            f.write(a.tobytes())
    tmp.rename(d / WEIGHTS)
    shutil.copyfile(GRAPH, d / GRAPH.name)
    (d / 'ok').write_text(spec.get('version', '1'))


# --------------------------------------------------------------- Trennen ----

class LeadRoformer:
    def __init__(self, threads=None):
        import onnxruntime as ort
        d = ensure_model()
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads or max(1, min(8, os.cpu_count() or 2))
        self.s = ort.InferenceSession(str(d / GRAPH.name), so, providers=['CPUExecutionProvider'])

    def chunk(self, y: np.ndarray) -> np.ndarray:
        """y: (2, n) → Leadstimme (2, n)."""
        import librosa
        S = np.stack([librosa.stft(c, n_fft=N_FFT, hop_length=HOP, win_length=N_FFT, window='hann', center=True,
                                   pad_mode='reflect') for c in y])          # (s, f, t)
        s_, f_, t_ = S.shape
        x = np.stack([S.real, S.imag], -1).transpose(1, 0, 2, 3).reshape(f_ * s_, t_, 2)[None].astype(np.float32)
        m = self.s.run(None, {'stft': x})[0][0, 0]                          # (f*s, t, 2)
        mc = (m[..., 0] + 1j * m[..., 1]).reshape(f_, s_, t_).transpose(1, 0, 2)
        out = S * mc
        return np.stack([librosa.istft(o, hop_length=HOP, win_length=N_FFT, n_fft=N_FFT, window='hann', center=True,
                                       length=y.shape[1]) for o in out]).astype(np.float32)

    def separate(self, y: np.ndarray, progress=None, chunk_s: float = 5.0, overlap_s: float = 0.5) -> np.ndarray:
        if y.ndim == 1:
            y = np.stack([y, y])
        y = y.astype(np.float32)
        n = y.shape[1]
        L, O = int(chunk_s * SR), int(overlap_s * SR)
        step = L - O
        out = np.zeros_like(y)
        w = np.zeros(n, np.float32)
        fade = np.ones(L, np.float32)
        fade[:O] = np.linspace(0, 1, O)
        fade[-O:] = np.linspace(1, 0, O)
        starts = []
        for a in range(0, n, step):
            starts.append(a)
            if a + L >= n:
                break
        for k, a in enumerate(starts):
            seg = y[:, a:a + L]
            m = seg.shape[1]
            if m < 4096:      # sehr kurze Aufnahme
                seg = np.pad(seg, ((0, 0), (0, 4096 - m)))
            r = self.chunk(seg)[:, :m]
            fw = fade[:m].copy()
            if a == 0:
                fw[:min(O, m)] = 1
            if k == len(starts) - 1:
                fw[max(0, m - O):] = 1
            out[:, a:a + m] += r * fw
            w[a:a + m] += fw
            if progress:
                progress((k + 1) / len(starts))
        return out / np.maximum(w, 1e-6)
