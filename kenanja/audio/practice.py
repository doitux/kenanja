"""Übe-Audio: das Original in eure Tonart verschieben und auf euren Ablauf schneiden.

Qualität geht vor:
  * Tonhöhenverschiebung mit Rubber Band (R3-Engine „fein“, Formanterhalt für natürliche Stimmen),
    Verarbeitung durchgehend in 32-Bit-Gleitkomma.
  * Schnitte auf Taktanfängen mit kurzer, leistungserhaltender Überblendung (kein Knacksen).
  * Nur EINE MP3-Kodierung am Ende (LAME), Bitrate wählbar (128–320 kbit/s oder VBR V0).
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from ..model import Song


@dataclass
class Segment:
    start: float          # Sekunden im Original
    end: float
    label: str = ''


def _decode(path, sr=None) -> tuple[np.ndarray, int]:
    if sr is None:
        info = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries',
                               'stream=sample_rate', '-of', 'csv=p=0', str(path)], capture_output=True, text=True)
        sr = int(info.stdout.strip() or 44100)
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'f32le', '-ac', '2', '-ar', str(sr), '-'],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy(), sr


def _pickup_seconds(song: Song, idx: int) -> float:
    """Länge des gesungenen Auftakts vor Abschnitt idx (in Sekunden)."""
    sec = song.sections[idx]
    if not sec.pickup or idx == 0 or not song.sections[idx - 1].bars:
        return 0.0
    last = song.sections[idx - 1].bars[-1]
    onset = last.melody[len(last.melody) - sec.pickup].onset
    beats = float(last.length - onset)
    return beats * 60.0 / (song.tempo or 80)


def segments_from_form(song: Song) -> list[Segment]:
    """Ablauf -> Audioabschnitte aus dem Original (zusammenhängende Teile werden verbunden).
    Bei Sprüngen wird ein gesungener Auftakt mitgenommen (Schnitt entsprechend früher)."""
    segs: list[Segment] = []
    secs = song.sections
    for item, sec in song.ordered_form():
        src = song.section(item.audio_from) if item.audio_from else sec
        if src.audio_start is None:
            continue
        idx = secs.index(src)
        end = src.audio_end
        if end is None:
            end = secs[idx + 1].audio_start if idx + 1 < len(secs) and secs[idx + 1].audio_start else None
        if end is None:
            end = 1e9
        if segs and abs(segs[-1].end - src.audio_start) < 0.05:
            segs[-1].end = end
            segs[-1].label += ' + ' + (item.label or sec.label)
        else:
            pk = _pickup_seconds(song, idx) if segs else 0.0
            if segs and pk:
                segs[-1].end -= pk
            segs.append(Segment(src.audio_start - pk, end, item.label or sec.label))
    return segs


def assemble(audio: np.ndarray, sr: int, segs: list[Segment], fade_ms: float = 40.0) -> np.ndarray:
    """Abschnitte mit leistungserhaltender Überblendung aneinanderfügen."""
    n_fade = max(1, int(sr * fade_ms / 1000))
    t = np.linspace(0, np.pi / 2, n_fade, dtype=np.float32)
    fin, fout = np.sin(t)[:, None], np.cos(t)[:, None]
    out = None
    for s in segs:
        a = max(0, int(round(s.start * sr)) - n_fade // 2)
        b = min(len(audio), int(round(min(s.end, len(audio) / sr) * sr)) + n_fade // 2)
        piece = audio[a:b].copy()
        if out is None:
            out = piece
            continue
        if len(piece) < n_fade or len(out) < n_fade:
            out = np.concatenate([out, piece])
            continue
        cross = out[-n_fade:] * fout + piece[:n_fade] * fin
        out = np.concatenate([out[:-n_fade], cross, piece[n_fade:]])
    if out is None:
        return audio
    # sanft ein- und ausblenden
    k = min(len(out) // 4, int(sr * 0.01))
    out[:k] *= np.linspace(0, 1, k, dtype=np.float32)[:, None]
    k2 = min(len(out) // 4, int(sr * 1.5))
    if segs and segs[-1].end < len(audio) / sr - 0.5:
        out[-k2:] *= np.linspace(1, 0, k2, dtype=np.float32)[:, None] ** 2
    return out


def pitch_shift(audio: np.ndarray, sr: int, semitones: float, formant: bool = True,
                tempo: float = 1.0) -> np.ndarray:
    """Rubber Band (CLI bevorzugt, sonst pylibrb). tempo < 1 = langsamer (zum Üben)."""
    if semitones == 0 and tempo == 1.0:
        return audio
    import soundfile as sf
    rb = shutil.which('rubberband') or shutil.which('rubberband-r3')
    if rb:
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / 'in.wav', Path(d) / 'out.wav'
            sf.write(src, audio, sr, subtype='FLOAT')
            cmd = [rb, '-3', '-q', '--pitch', str(semitones)]
            if formant:
                cmd.append('--formant')
            if tempo != 1.0:
                cmd += ['--tempo', str(tempo)]
            cmd += [str(src), str(dst)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0 and dst.exists():
                return sf.read(dst, dtype='float32')[0]
    try:
        import pylibrb
        opts = (pylibrb.Option.ENGINE_FINER | pylibrb.Option.PROCESS_OFFLINE |
                (pylibrb.Option.FORMANT_PRESERVED if formant else 0))
        st = pylibrb.RubberBandStretcher(sample_rate=sr, channels=2, options=opts,
                                         initial_time_ratio=1 / tempo, initial_pitch_scale=2 ** (semitones / 12))
        x = np.ascontiguousarray(audio.T)
        st.set_max_process_size(x.shape[1])
        st.set_expected_input_duration(x.shape[1])
        st.study(x, final=True)
        st.process(x, final=True)
        return st.retrieve_available().T.copy()
    except Exception as e:
        raise RuntimeError('Für das Transponieren wird Rubber Band benötigt (sudo apt install rubberband-cli).') from e


def _limit(audio: np.ndarray, ceiling: float = 0.97) -> np.ndarray:
    peak = float(np.max(np.abs(audio))) if audio.size else 0
    if peak > ceiling:
        audio = audio * (ceiling / peak)
    return audio


def encode_mp3(audio: np.ndarray, sr: int, out, bitrate: str = '256', title: str = '', artist: str = '',
               album: str = '') -> Path:
    """bitrate: '128', '192', '256', '320' (kbit/s, konstant) oder 'V0' (variabel, höchste Stufe)."""
    import soundfile as sf
    out = Path(out)
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / 'x.wav'
        sf.write(src, audio, sr, subtype='FLOAT')
        cmd = ['ffmpeg', '-y', '-v', 'error', '-i', str(src), '-c:a', 'libmp3lame']
        if str(bitrate).upper().startswith('V'):
            cmd += ['-q:a', str(bitrate)[1:] or '0']
        else:
            cmd += ['-b:a', f'{int(bitrate)}k']
        cmd += ['-id3v2_version', '3']
        for k, v in (('title', title), ('artist', artist), ('album', album)):
            if v:
                cmd += ['-metadata', f'{k}={v}']
        cmd.append(str(out))
        subprocess.run(cmd, check=True)
    return out


def render_practice(song: Song, out_mp3, bitrate: str = '256', transpose: Optional[int] = None,
                    use_form: bool = True, formant: bool = True, tempo: float = 1.0,
                    fade_ms: float = 40.0, band: str = '') -> Path:
    """Übe-Audio erzeugen: Ablauf schneiden, transponieren, als MP3 speichern."""
    if not song.audio:
        raise ValueError('Für diesen Song ist kein Original-Audio hinterlegt.')
    audio, sr = _decode(song.audio)
    if use_form:
        segs = segments_from_form(song)
        if segs:
            audio = assemble(audio, sr, segs, fade_ms)
    st = song.transpose if transpose is None else transpose
    audio = pitch_shift(audio, sr, st, formant=formant, tempo=tempo)
    audio = _limit(audio)
    title = song.title + (f' – {band}-Ablauf' if band else ' – Ablauf') + (f' ({song.target_key()})' if st else '')
    return encode_mp3(audio, sr, out_mp3, bitrate, title=title, artist=song.authors.replace('\n', ' '))
