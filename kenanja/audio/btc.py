"""Akkorderkennung mit dem BTC-Modell (Bi-directional Transformer for Chord recognition,
Park et al., ISMIR 2019, MIT-Lizenz – https://github.com/jayg996/BTC-ISMIR19).

Läuft ohne PyTorch: die Gewichte werden einmal aus der Original-Datei gelesen, als .npz
zwischengespeichert, und das Netz wird mit numpy gerechnet. 170 Akkordklassen
(Dur, Moll, sus2, sus4, 6, 7, maj7, m7, dim, aug, …).
"""
from __future__ import annotations

import io
import math
import pickle
import struct
import urllib.request
from collections import OrderedDict
from pathlib import Path

import numpy as np

MODEL_URL = 'https://raw.githubusercontent.com/jayg996/BTC-ISMIR19/master/test/btc_model_large_voca.pt'
ROOTS = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
QUALS = ['min', 'maj', 'dim', 'aug', 'min6', 'maj6', 'min7', 'minmaj7', 'maj7', '7', 'dim7', 'hdim7', 'sus2', 'sus4']
SR, HOP, NBINS, BPO, TIMESTEP = 22050, 2048, 144, 24, 108


def cache_dir() -> Path:
    import os
    base = os.environ.get('XDG_CACHE_HOME') or str(Path.home() / '.cache')
    p = Path(base) / 'kenanja' / 'models'
    p.mkdir(parents=True, exist_ok=True)
    return p


def label(i: int) -> str:
    if i == 169:
        return 'N'
    if i == 168:
        return 'X'
    r, q = divmod(i, 14)
    return ROOTS[r] + ('' if q == 1 else ':' + QUALS[q])


# ------------------------------------------------------------ Laden ohne torch ----

_DT = {'FloatStorage': np.float32, 'DoubleStorage': np.float64, 'LongStorage': np.int64,
       'IntStorage': np.int32, 'HalfStorage': np.float16, 'ByteStorage': np.uint8, 'BoolStorage': np.bool_}


class _Storage:
    def __init__(self, key, dtype):
        self.key, self.dtype, self.data = key, dtype, None


class _Tensor:
    def __init__(self, storage, offset, size, stride):
        self.storage, self.offset, self.size, self.stride = storage, offset, tuple(size), tuple(stride)

    def numpy(self):
        a = self.storage.data
        isz = a.itemsize
        return np.lib.stride_tricks.as_strided(a[self.offset:], shape=self.size,
                                               strides=[s * isz for s in self.stride]).copy()


def _rebuild_tensor_v2(storage, offset, size, stride, *args):
    return _Tensor(storage, offset, size, stride)


def _rebuild_parameter(t, *args):
    return t


class _Unpickler(pickle.Unpickler):
    def __init__(self, f, storages):
        super().__init__(f, encoding='latin1')
        self.storages = storages

    def find_class(self, module, name):
        if module == 'torch._utils' and name == '_rebuild_tensor_v2':
            return _rebuild_tensor_v2
        if module == 'torch._utils' and name == '_rebuild_parameter':
            return _rebuild_parameter
        if module == 'torch' and name in _DT:
            return name
        if module == 'collections' and name == 'OrderedDict':
            return OrderedDict
        if module.startswith('numpy'):
            return super().find_class(module, name)
        if module.startswith('torch'):
            return lambda *a, **k: None
        return super().find_class(module, name)

    def persistent_load(self, pid):
        typename, stype, key, location, size = pid[:5]
        if key not in self.storages:
            self.storages[key] = _Storage(key, _DT.get(stype if isinstance(stype, str) else 'FloatStorage', np.float32))
        return self.storages[key]


def load_legacy_torch(path) -> dict:
    """Liest eine Datei im alten torch.save-Format (vor 1.6) ohne torch."""
    with open(path, 'rb') as f:
        pickle.load(f, encoding='latin1')  # magic
        pickle.load(f, encoding='latin1')  # protocol
        pickle.load(f, encoding='latin1')  # sys info
        storages = {}
        obj = _Unpickler(f, storages).load()
        keys = pickle.load(f, encoding='latin1')
        for k in keys:
            n = struct.unpack('<q', f.read(8))[0]
            st = storages[k]
            st.data = np.frombuffer(f.read(n * np.dtype(st.dtype).itemsize), dtype=st.dtype)
    return obj


def ensure_weights() -> Path:
    npz = cache_dir() / 'btc_large_voca.npz'
    if npz.exists():
        return npz
    pt = cache_dir() / 'btc_model_large_voca.pt'
    if not pt.exists():
        urllib.request.urlretrieve(MODEL_URL, pt)
    ck = load_legacy_torch(pt)
    arrays = {k: v.numpy() for k, v in ck['model'].items()}
    arrays['__mean'] = np.asarray(ck['mean'], dtype=np.float64)
    arrays['__std'] = np.asarray(ck['std'], dtype=np.float64)
    np.savez_compressed(npz, **arrays)
    return npz


# ------------------------------------------------------------ Netz (numpy) ----

def _timing_signal(length, channels, min_ts=1.0, max_ts=1.0e4):
    pos = np.arange(length)
    n = channels // 2
    inc = math.log(max_ts / min_ts) / (n - 1)
    inv = min_ts * np.exp(np.arange(n) * -inc)
    st = pos[:, None] * inv[None, :]
    sig = np.concatenate([np.sin(st), np.cos(st)], axis=1)
    return sig.astype(np.float32)


def _ln(x, g, b, eps=1e-6):
    m = x.mean(-1, keepdims=True)
    s = x.std(-1, keepdims=True, ddof=1)
    return g * (x - m) / (s + eps) + b


def _conv1d(x, w, b):
    # x [T, C_in], w [C_out, C_in, 3], Padding (1, 1)
    xp = np.pad(x, ((1, 1), (0, 0)))
    T = x.shape[0]
    out = b[None, :].copy() + sum(xp[k:k + T] @ w[:, :, k].T for k in range(3))
    return out


def _softmax(z):
    z = z - z.max(-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(-1, keepdims=True)


class BTC:
    def __init__(self, npz=None):
        W = np.load(npz or ensure_weights())
        self.W = {k: W[k].astype(np.float32) for k in W.files}
        self.mean = W['__mean']
        self.std = W['__std']
        self.timing = _timing_signal(TIMESTEP, 128)
        T = TIMESTEP
        fwd = np.triu(np.full((T, T), -np.inf, dtype=np.float32), 1)
        self.masks = (fwd, fwd.T.copy())

    def _block(self, x, pre, mask):
        W = self.W
        xn = _ln(x, W[pre + 'layer_norm_mha.gamma'], W[pre + 'layer_norm_mha.beta'])
        q = xn @ W[pre + 'multi_head_attention.query_linear.weight'].T
        k = xn @ W[pre + 'multi_head_attention.key_linear.weight'].T
        v = xn @ W[pre + 'multi_head_attention.value_linear.weight'].T
        H = 4
        T, D = q.shape
        q = q.reshape(T, H, D // H).transpose(1, 0, 2) * (D // H) ** -0.5
        k = k.reshape(T, H, D // H).transpose(1, 0, 2)
        v = v.reshape(T, H, D // H).transpose(1, 0, 2)
        logits = q @ k.transpose(0, 2, 1) + mask[None, :T, :T]
        ctx = _softmax(logits) @ v
        ctx = ctx.transpose(1, 0, 2).reshape(T, D)
        y = ctx @ W[pre + 'multi_head_attention.output_linear.weight'].T
        x = x + y
        xn = _ln(x, W[pre + 'layer_norm_ffn.gamma'], W[pre + 'layer_norm_ffn.beta'])
        h = np.maximum(0, _conv1d(xn, W[pre + 'positionwise_convolution.layers.0.conv.weight'],
                                  W[pre + 'positionwise_convolution.layers.0.conv.bias']))
        h = np.maximum(0, _conv1d(h, W[pre + 'positionwise_convolution.layers.1.conv.weight'],
                                  W[pre + 'positionwise_convolution.layers.1.conv.bias']))
        return x + h

    def forward(self, feat):
        """feat [T<=108, 144] (normalisiert) -> Wahrscheinlichkeiten [T, 170]"""
        W = self.W
        T = feat.shape[0]
        x = feat @ W['self_attn_layers.embedding_proj.weight'].T
        x = x + self.timing[:T]
        for l in range(8):
            pre = f'self_attn_layers.self_attn_layers.{l}.'
            a = self._block(x, pre + 'attn_block.', self.masks[0])
            b = self._block(x, pre + 'backward_attn_block.', self.masks[1])
            x = np.concatenate([a, b], axis=1) @ W[pre + 'linear.weight'].T + W[pre + 'linear.bias']
        x = _ln(x, W['self_attn_layers.layer_norm.gamma'], W['self_attn_layers.layer_norm.beta'])
        logits = x @ W['output_layer.output_projection.weight'].T + W['output_layer.output_projection.bias']
        return _softmax(logits)

    def features(self, y: np.ndarray) -> np.ndarray:
        import librosa
        chunk = SR * 10
        feats = []
        for s in range(0, len(y), chunk):
            seg = y[s:s + chunk]
            if len(seg) < 2048:
                break
            feats.append(np.abs(librosa.cqt(seg, sr=SR, n_bins=NBINS, bins_per_octave=BPO, hop_length=HOP)))
        f = np.log(np.concatenate(feats, axis=1) + 1e-6).T
        return ((f - self.mean) / self.std).astype(np.float32)

    def predict(self, y: np.ndarray) -> tuple[np.ndarray, float]:
        """y: Mono-Audio mit 22050 Hz. Liefert (Wahrscheinlichkeiten [N,170], Sekunden pro Frame)."""
        f = self.features(y)
        n = f.shape[0]
        pad = (-n) % TIMESTEP
        f = np.pad(f, ((0, pad), (0, 0)))
        probs = np.concatenate([self.forward(f[i:i + TIMESTEP]) for i in range(0, f.shape[0], TIMESTEP)])[:n]
        return probs, 10 / TIMESTEP
