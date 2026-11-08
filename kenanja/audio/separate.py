"""Gesang vom Rest trennen – mit dem frei verfügbaren UVR-MDX-NET-Voc_FT-Modell (ONNX).

Läuft mit onnxruntime auf dem Prozessor (keine Grafikkarte nötig, ca. 1–2 Minuten pro Song).
Das Modell (≈ 65 MB) wird beim ersten Mal von GitHub geladen und zwischengespeichert.
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

import numpy as np

from .btc import cache_dir

MODEL_URL = 'https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/UVR-MDX-NET-Voc_FT.onnx'
N_FFT, HOP, DIM_F, DIM_T, COMPENSATE = 7680, 1024, 3072, 256, 1.021
SR = 44100


def ensure_model() -> Path:
    p = cache_dir() / 'UVR-MDX-NET-Voc_FT.onnx'
    if not p.exists() or p.stat().st_size < 1_000_000:
        tmp = p.with_suffix('.part')
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.rename(p)
    return p


def _stft(x):
    import librosa
    # x: [2, n] -> [4, DIM_F, T]
    specs = [librosa.stft(c, n_fft=N_FFT, hop_length=HOP, window='hann', center=True, pad_mode='reflect') for c in x]
    out = []
    for s in specs:
        s = s[:DIM_F]
        out += [s.real, s.imag]
    return np.stack(out).astype(np.float32)


def _istft(spec, length):
    import librosa
    chans = []
    for c in range(2):
        re, im = spec[2 * c], spec[2 * c + 1]
        full = np.zeros((N_FFT // 2 + 1, re.shape[1]), dtype=np.complex64)
        full[:DIM_F] = re + 1j * im
        chans.append(librosa.istft(full, n_fft=N_FFT, hop_length=HOP, window='hann', center=True, length=length))
    return np.stack(chans)


def separate(mix: np.ndarray, progress=None, denoise: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """mix: [2, n] Stereo mit 44100 Hz. Liefert (Gesang, Begleitung) gleicher Form."""
    import onnxruntime as ort
    if mix.ndim == 1:
        mix = np.stack([mix, mix])
    sess = ort.InferenceSession(str(ensure_model()), providers=['CPUExecutionProvider'])
    chunk = HOP * (DIM_T - 1)
    trim = N_FFT // 2
    gen = chunk - 2 * trim
    n = mix.shape[1]
    pad = gen - (n % gen)
    mp = np.concatenate([np.zeros((2, trim)), mix, np.zeros((2, pad)), np.zeros((2, trim))], axis=1).astype(np.float32)
    outs = []
    steps = list(range(0, mp.shape[1] - 2 * trim, gen))
    for k, i in enumerate(steps):
        part = mp[:, i:i + chunk]
        if part.shape[1] < chunk:
            part = np.pad(part, ((0, 0), (0, chunk - part.shape[1])))
        spec = _stft(part)[None]
        pred = sess.run(None, {'input': spec})[0]
        if denoise:
            pred = (pred - sess.run(None, {'input': -spec})[0]) * 0.5
        wav = _istft(pred[0], chunk)
        outs.append(wav[:, trim:trim + gen])
        if progress:
            progress((k + 1) / len(steps))
    vocals = np.concatenate(outs, axis=1)[:, :n] * COMPENSATE
    return vocals, mix - vocals
