"""Tonhöhenverlauf des Gesangs.

Optional RMVPE (Wei u. a. 2023, „Robust Model for Vocal Pitch Estimation in Polyphonic Music“, aus dem
RVC-Projekt, MIT-Lizenz) als ONNX-Modell – deutlich robuster gegen Begleitung, Chor und Oktavfehler als pYIN.
Fehlt das Modell, wird pYIN (librosa) benutzt. Ergebnis immer im Raster von librosa (hop Samples, center=True).
"""
from __future__ import annotations

import hashlib
import os
import urllib.request
from pathlib import Path
from typing import Optional

import numpy as np

URL = 'https://github.com/thewh1teagle/rmvpe-onnx/releases/download/model-files-v1.0/rmvpe.onnx'
SIZE_MB = 345

_SESSION = None
_CACHE: dict = {}


def model_path() -> Path:
    from .btc import cache_dir
    return cache_dir() / 'rmvpe.onnx'


def ready() -> bool:
    # In den Tests mit 10 Referenz-Songs war RMVPE insgesamt nicht besser als pYIN (bei einzelnen Songs deutlich
    # besser, bei anderen schlechter) – daher nur auf ausdrücklichen Wunsch.
    return os.environ.get('KENANJA_F0') == 'rmvpe' and model_path().exists()


def ensure_model(progress=None) -> Path:
    p = model_path()
    if p.exists():
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.part')

    def hook(blocks, bs, total):
        if progress and total > 0:
            progress(min(1.0, blocks * bs / total))
    urllib.request.urlretrieve(URL, tmp, hook)
    tmp.rename(p)
    return p


def _session():
    global _SESSION
    if _SESSION is None:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, min(8, os.cpu_count() or 2))
        _SESSION = ort.InferenceSession(str(model_path()), so, providers=['CPUExecutionProvider'])
        meta = _SESSION.get_modelmeta().custom_metadata_map
        import librosa
        _SESSION._cfg = {k: meta[k] for k in meta}
        _SESSION._mel = librosa.filters.mel(sr=int(meta['sample_rate']), n_fft=int(meta['window_length']),
                                            n_mels=int(meta['n_mels']), fmin=float(meta['mel_fmin']),
                                            fmax=float(meta['mel_fmax']), htk=True).astype(np.float32)
    return _SESSION


def _rmvpe(y16: np.ndarray, threshold: float = float(os.environ.get('KENANJA_TEST_RMVPE_THR', '0.03'))) -> tuple[np.ndarray, np.ndarray]:
    """-> (f0 in Hz, 0 = stimmlos; Sicherheit 0..1) im 10-ms-Raster."""
    import librosa
    s = _session()
    cfg = s._cfg
    win, hop = int(cfg['window_length']), int(cfg['hop_length'])
    n_class, cc = int(cfg['n_class']), float(cfg['cents_const'])
    cents_map = np.arange(n_class, dtype=np.float32) * 20.0 + cc
    out_f, out_c = [], []
    CH = 30 * 16000
    for a in range(0, len(y16), CH):
        seg = y16[a:a + CH].astype(np.float32)
        if len(seg) <= win:
            n = max(1, len(seg) // hop + 1)
            out_f.append(np.zeros(n, np.float32))
            out_c.append(np.zeros(n, np.float32))
            continue
        mag = np.abs(librosa.stft(seg, n_fft=win, hop_length=hop, win_length=win, window='hann', center=True,
                                  pad_mode='reflect'))
        mel = np.log(np.clip(s._mel @ mag, 1e-5, None)).astype(np.float32)
        nf = mel.shape[-1]
        pad = (32 - nf % 32) % 32
        if pad:
            mel = np.pad(mel, ((0, 0), (0, pad)), mode='reflect' if nf > 1 else 'edge')
        hid = s.run(None, {s.get_inputs()[0].name: mel[None]})[0][0, :nf]
        c = hid.argmax(1)
        conf = hid.max(1)
        f = np.zeros(nf, np.float32)
        for i in range(nf):
            if conf[i] < threshold:
                continue
            lo, hi = max(0, c[i] - 4), min(n_class, c[i] + 5)
            w = hid[i, lo:hi]
            ws = float(w.sum())
            if ws > 0:
                f[i] = 10.0 * 2.0 ** (float((w * cents_map[lo:hi]).sum() / ws) / 1200.0)
        out_f.append(f)
        out_c.append(conf.astype(np.float32))
    return np.concatenate(out_f), np.concatenate(out_c)


def track(y: np.ndarray, sr: int = 22050, hop: int = 256, fmin: float = 90, fmax: float = 1000
          ) -> tuple[np.ndarray, np.ndarray]:
    """-> (f0 in Hz mit NaN für stimmlos, stimmhaft-Flag) im Raster hop/sr (Frames wie librosa center=True)."""
    import librosa
    key = (hashlib.md5(np.ascontiguousarray(y[::97]).tobytes()).hexdigest(), len(y), sr, hop, ready())
    if key in _CACHE:
        f, v = _CACHE[key]
        return f.copy(), v.copy()
    n = 1 + len(y) // hop
    if ready():
        y16 = librosa.resample(np.asarray(y, dtype=np.float32), orig_sr=sr, target_sr=16000)
        f10, conf = _rmvpe(y16)
        t10 = np.arange(len(f10)) * 0.01
        t = np.arange(n) * hop / sr
        idx = np.clip(np.round(t / 0.01).astype(int), 0, len(f10) - 1)
        f = f10[idx].astype(float)
        f[(f < fmin) | (f > fmax)] = 0.0
        v = f > 0
        f = np.where(v, f, np.nan)
    else:
        f, v, _ = librosa.pyin(y, fmin=fmin, fmax=fmax, sr=sr, frame_length=2048, hop_length=hop, fill_na=np.nan)
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[key] = (f, v)
    return f.copy(), v.copy()
