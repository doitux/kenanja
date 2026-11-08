"""MuseScore über die Kommandozeile nutzen: MusicXML -> .mscz / PDF / PNG / MP3, Einzelstimmen.

Gefunden wird MuseScore 4 (AppImage, Flatpak, Paket) oder MuseScore 3. Pfad lässt sich über die
Umgebungsvariable KENANJA_MUSESCORE oder die Einstellungen festlegen.
"""
from __future__ import annotations

import base64
import glob
import re
import tempfile
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

STYLE_DIR = Path(__file__).resolve().parent.parent / 'data'


def find_musescore() -> Optional[list[str]]:
    env = os.environ.get('KENANJA_MUSESCORE')
    if env:
        return env.split()
    for name in ('mscore4portable', 'mscore4', 'musescore4', 'mscore', 'musescore', 'MuseScore4', 'mscore3', 'musescore3'):
        p = shutil.which(name)
        if p:
            return [p]
    home = Path.home()
    pats = [str(home / 'Applications' / '*MuseScore*.AppImage'), str(home / 'Downloads' / '*MuseScore*.AppImage'),
            str(home / '*MuseScore*.AppImage'), str(home / 'bin' / '*MuseScore*.AppImage'),
            str(home / '.local' / 'bin' / '*MuseScore*.AppImage'), '/opt/*MuseScore*.AppImage',
            '/opt/*/*MuseScore*.AppImage', str(home / 'Programme' / '*MuseScore*.AppImage'),
            str(home / 'Programme' / '*' / '*MuseScore*.AppImage')]
    found = sorted({f for pat in pats for f in glob.glob(pat)}, key=lambda f: os.path.getmtime(f), reverse=True)
    if found:
        return [found[0]]
    if shutil.which('flatpak'):
        r = subprocess.run(['flatpak', 'info', 'org.musescore.MuseScore'], capture_output=True)
        if r.returncode == 0:
            return ['flatpak', 'run', 'org.musescore.MuseScore']
    return None


def version(ms=None) -> str:
    ms = ms or find_musescore()
    if not ms:
        return ''
    r = _run(ms + ['--long-version'], timeout=60)
    for line in ((r.stdout or '') + '\n' + (r.stderr or '')).splitlines():
        if re.search(r'MuseScore|\d+\.\d+\.\d+', line) and 'QStandardPaths' not in line:
            return line.strip()
    return ''


def _env():
    env = dict(os.environ)
    env.setdefault('QT_QPA_PLATFORM', 'offscreen')
    env.setdefault('APPIMAGE_EXTRACT_AND_RUN', '1')   # AppImage auch ohne FUSE
    if 'XDG_RUNTIME_DIR' not in env:
        env['XDG_RUNTIME_DIR'] = tempfile.mkdtemp(prefix='kenanja-rt-')
    return env


def _run(cmd, timeout=600):
    return subprocess.run(cmd, capture_output=True, text=True, env=_env(), timeout=timeout)


def convert(src, out, style: Optional[str] = None, ms=None) -> Optional[Path]:
    """Wandelt src (MusicXML/mscz) in out (.mscz, .pdf, .png, .mp3, .musicxml) um."""
    ms = ms or find_musescore()
    if not ms:
        return None
    cmd = list(ms)
    if style and Path(style).exists() and _is_ms4(ms):
        cmd += ['-S', str(style)]
    cmd += ['-o', str(out), str(src)]
    r = _run(cmd)
    out = Path(out)
    if out.suffix == '.png':
        # MuseScore hängt bei PNG die Seitennummer an
        cands = sorted(out.parent.glob(out.stem + '*.png'))
        return cands[0] if cands else None
    return out if out.exists() else None


_ms4_cache = {}


def _is_ms4(ms) -> bool:
    key = ' '.join(ms)
    if key not in _ms4_cache:
        v = version(ms)
        _ms4_cache[key] = (' 4.' in v or v.startswith('4') or 'MuseScore4' in v or 'Studio' in v)
    return _ms4_cache[key]


def export_parts_pdf(src, out_dir, base_name: str, style: Optional[str] = None, ms=None) -> list[Path]:
    """Partitur + Einzelstimmen als PDFs (über --score-parts-pdf)."""
    ms = ms or find_musescore()
    if not ms:
        return []
    out_dir = Path(out_dir)
    cmd = list(ms) + (['-S', str(style)] if style and _is_ms4(ms) else []) + ['--score-parts-pdf', str(src)]
    r = _run(cmd)
    txt = r.stdout.strip()
    start = txt.find('{')
    if start < 0:
        return []
    try:
        data = json.loads(txt[start:])
    except json.JSONDecodeError:
        return []
    paths = []
    if data.get('scoreBin'):
        p = out_dir / f'{base_name} - Partitur.pdf'
        p.write_bytes(base64.b64decode(data['scoreBin']))
        paths.append(p)
    for name, b64 in zip(data.get('parts', []), data.get('partsBin', [])):
        p = out_dir / f'{base_name} - {name}.pdf'
        p.write_bytes(base64.b64decode(b64))
        paths.append(p)
    return paths


def trim_png(path, pad: int = 12) -> Path:
    """Weißen Rand eines exportierten Notenbilds abschneiden."""
    from PIL import Image, ImageChops
    src = Image.open(path)
    if src.mode in ('RGBA', 'LA', 'P'):
        src = src.convert('RGBA')
        white = Image.new('RGBA', src.size, (255, 255, 255, 255))
        im = Image.alpha_composite(white, src).convert('RGB')
    else:
        im = src.convert('RGB')
    bg = Image.new('RGB', im.size, (255, 255, 255))
    bbox = ImageChops.difference(im, bg).getbbox()
    if bbox:
        l, t, r, b = bbox
        im = im.crop((max(0, l - pad), max(0, t - pad), min(im.width, r + pad), min(im.height, b + pad)))
    im.save(path)
    return Path(path)


def patch_harmonies(mscz_path, symbols: list[str], staff: int = 1) -> int:
    """Setzt die Akkordnamen in einer .mscz/.mscx exakt (MuseScore übernimmt beim MusicXML-Import
    nur Standardbezeichnungen). symbols: Akkorde in Reihenfolge des Vorkommens."""
    import re
    import zipfile
    import xml.etree.ElementTree as ET
    from ..theory import ChordSymbol
    p = Path(mscz_path)
    if p.suffix == '.mscz':
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            files = {n: z.read(n) for n in names}
        main = next(n for n in names if n.endswith('.mscx') and '/' not in n)
        xml = files[main].decode('utf-8')
    else:
        xml = p.read_text(encoding='utf-8')
    root = ET.fromstring(xml)
    sc = root.find('Score')
    st = [s for s in sc.findall('Staff')][staff - 1]
    hs = list(st.iter('Harmony'))
    n = 0
    for h, sym in zip(hs, symbols):
        try:
            ch = ChordSymbol.parse(sym)
        except ValueError:
            continue
        info = h.find('harmonyInfo')
        tgt = info if info is not None else h
        for tag in ('root', 'name', 'bass', 'base'):
            for el in tgt.findall(tag):
                tgt.remove(el)
        bass_tag = 'bass' if info is not None else 'base'
        new = []
        if ch.is_nc:
            new.append(('name', 'N.C.'))
        else:
            if ch.quality:
                new.append(('name', ch.quality))
            new.append(('root', str(ch.root)))
            if ch.bass is not None:
                new.append((bass_tag, str(ch.bass)))
        for i, (tag, val) in enumerate(new):
            el = ET.Element(tag)
            el.text = val
            tgt.insert(i, el)
        n += 1
    out = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding='unicode')
    if p.suffix == '.mscz':
        files[main] = out.encode('utf-8')
        with zipfile.ZipFile(p, 'w', zipfile.ZIP_DEFLATED) as z:
            for name in names:
                z.writestr(name, files[name])
    else:
        p.write_text(out, encoding='utf-8')
    return n
