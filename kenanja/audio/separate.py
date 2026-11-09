"""Gesang vom Rest trennen – mit dem frei verfügbaren UVR-MDX-NET-Voc_FT-Modell (ONNX).

Läuft mit onnxruntime auf dem Prozessor (keine Grafikkarte nötig, ca. 1–2 Minuten pro Song).
Das Modell (≈ 65 MB) wird beim ersten Mal von GitHub geladen und zwischengespeichert.
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

import numpy as np

from .btc import cache_dir

BASE = 'https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/'
# name: (Datei, n_fft, dim_f, dim_t, Ausgleich)
MODELS = {
    'gesang': ('UVR-MDX-NET-Voc_FT.onnx', 7680, 3072, 256, 1.021),   # alle Stimmen vs. Begleitung
    'lead': ('UVR_MDXNET_KARA_2.onnx', 5120, 2048, 256, 1.065),      # Chor/Backing aus der Gesangsspur (Karaoke)
}
MODEL_URL = BASE + MODELS['gesang'][0]
HOP = 1024
SR = 44100


def ensure_model(name: str = 'gesang') -> Path:
    fname = MODELS[name][0]
    p = cache_dir() / fname
    if not p.exists() or p.stat().st_size < 1_000_000:
        tmp = p.with_suffix('.part')
        urllib.request.urlretrieve(BASE + fname, tmp)
        tmp.rename(p)
    return p


def _stft(x, n_fft, dim_f):
    import librosa
    # x: [2, n] -> [4, dim_f, T]
    specs = [librosa.stft(c, n_fft=n_fft, hop_length=HOP, window='hann', center=True, pad_mode='reflect') for c in x]
    out = []
    for s in specs:
        s = s[:dim_f]
        out += [s.real, s.imag]
    return np.stack(out).astype(np.float32)


def _istft(spec, length, n_fft, dim_f):
    import librosa
    chans = []
    for c in range(2):
        re, im = spec[2 * c], spec[2 * c + 1]
        full = np.zeros((n_fft // 2 + 1, re.shape[1]), dtype=np.complex64)
        full[:dim_f] = re + 1j * im
        chans.append(librosa.istft(full, n_fft=n_fft, hop_length=HOP, window='hann', center=True, length=length))
    return np.stack(chans)


def separate(mix: np.ndarray, progress=None, denoise: bool = False, model: str = 'gesang') -> tuple[np.ndarray, np.ndarray]:
    """mix: [2, n] Stereo mit 44100 Hz. Liefert (Stimme, Rest) gleicher Form.
    model='gesang': alle Stimmen vs. Begleitung; model='lead': aus der Gesangsspur die Leadstimme vs. Chor."""
    import onnxruntime as ort
    if mix.ndim == 1:
        mix = np.stack([mix, mix])
    _, N_FFT, DIM_F, DIM_T, COMPENSATE = MODELS[model]
    sess = ort.InferenceSession(str(ensure_model(model)), providers=['CPUExecutionProvider'])
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
        spec = _stft(part, N_FFT, DIM_F)[None]
        pred = sess.run(None, {'input': spec})[0]
        if denoise:
            pred = (pred - sess.run(None, {'input': -spec})[0]) * 0.5
        wav = _istft(pred[0], chunk, N_FFT, DIM_F)
        outs.append(wav[:, trim:trim + gen])
        if progress:
            progress((k + 1) / len(steps))
    pred = np.concatenate(outs, axis=1)[:, :n] * COMPENSATE
    if model == 'lead':   # das Karaoke-Modell liefert den Hintergrundgesang – die Leadstimme ist der Rest
        return mix - pred, pred
    return pred, mix - pred
