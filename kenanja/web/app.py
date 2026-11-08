"""Lokale Weboberfläche von Kenanja (läuft nur auf deinem Rechner: http://127.0.0.1:8765)."""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import traceback
import uuid
from dataclasses import asdict
from fractions import Fraction as F
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import lyrics as LY
from ..model import ChordEvent, FormItem, Song
from ..voices import (CONGREGATION, VoiceProfile, ambitus_report, evaluate_shift, load_profiles, save_profiles,
                      suggest_keys, suggest_swaps)

STATIC = Path(__file__).resolve().parent / 'static'
HOME = Path(os.environ.get('KENANJA_HOME', str(Path.home() / 'Kenanja')))
from ..voices import config_dir as _config_dir  # noqa: E402
SETTINGS_FILE = _config_dir() / 'einstellungen.json'
OLD_SETTINGS_FILE = HOME / 'einstellungen.json'

DEFAULT_SETTINGS = {
    'projektordner': str(HOME / 'Projekte'),
    'ausgabe': str(HOME / 'Songs'),
    'band': 'S&L',
    'bitrate': '256',
    'spieler': [{'name': 'Violine', 'instrument': 'Violine', 'level': 'mittel'},
                {'name': 'Violoncello', 'instrument': 'Violoncello', 'level': 'mittel'}],
    'besetzung': ['Keys', 'Synth', 'Piano', 'AGit', 'LEGit', 'REGit', 'Bass', 'Drums', 'LVoc', 'BVoc', 'Strings'],
    'musescore': '',
}

app = FastAPI(title='Kenanja')
app.mount('/static', StaticFiles(directory=STATIC), name='static')
JOBS: dict[str, dict] = {}


def settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    src = SETTINGS_FILE if SETTINGS_FILE.exists() else (OLD_SETTINGS_FILE if OLD_SETTINGS_FILE.exists() else None)
    if src:
        s.update(json.loads(src.read_text(encoding='utf-8')))
    if s.get('musescore'):
        os.environ['KENANJA_MUSESCORE'] = s['musescore']
    return s


def projects_dir() -> Path:
    d = Path(settings()['projektordner']).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def pdir(pid: str) -> Path:
    if not re.fullmatch(r'[\w-]+', pid):
        raise HTTPException(400, 'Ungültige Projekt-ID')
    d = projects_dir() / pid
    if not d.exists():
        raise HTTPException(404, 'Projekt nicht gefunden')
    return d


def load_song(pid: str) -> Song:
    return Song.load(pdir(pid) / 'song.json')


def song_payload(song: Song) -> dict:
    d = json.loads(song.to_json())
    # Zusatzinfos für die Oberfläche
    for sec, sd in zip(song.sections, d['sections']):
        sd['instrumental'] = sec.is_instrumental
        sd['bar_chords'] = [' '.join(_chord_cells(b)) for b in sec.bars]
        sd['n_bars'] = len(sec.bars)
        sd['text'] = '\n'.join(sec.lines)
        sd.pop('bars', None)
    d['target_key'] = song.target_key()
    return d


def _chord_cells(bar) -> list[str]:
    """Akkorde eines Takts als Text: 'G G/F#' (Zählzeit 1 und 3) oder 'G . Em7 D' (je Schlag)."""
    beats = int(bar.length)
    if not bar.chords:
        return ['%']
    pos = {int(c.onset): c.symbol for c in bar.chords if c.onset == int(c.onset)}
    if set(pos) <= {0} and len(bar.chords) == 1:
        return [bar.chords[0].symbol]
    if beats == 4 and set(pos) <= {0, 2} and len(pos) == len(bar.chords):
        return [pos.get(0, '.'), pos.get(2, '.')] if 0 in pos else ['.', pos[2]]
    return [pos.get(i, '.') for i in range(beats)]


def _parse_cells(text: str, bar) -> list[ChordEvent]:
    cells = text.split()
    if not cells or cells == ['%']:
        return []
    beats = int(bar.length)
    if len(cells) == 1:
        pos = [0]
    elif len(cells) == 2 and beats == 4:
        pos = [0, 2]
    else:
        pos = list(range(len(cells)))
    out = []
    for p, c in zip(pos, cells):
        if c in ('.', '-', '/'):
            continue
        out.append(ChordEvent(F(p), c))
    return out


def save_song(pid: str, song: Song):
    song.save(pdir(pid) / 'song.json')


# ------------------------------------------------------------------ Seiten ----

@app.get('/', response_class=HTMLResponse)
def index():
    return (STATIC / 'index.html').read_text(encoding='utf-8')


@app.get('/api/einstellungen')
def get_settings():
    from ..notation.musescore import find_musescore
    from ..sheets.odt import find_soffice
    s = settings()
    ms = find_musescore()
    return {**s, 'gefunden': {'musescore': ' '.join(ms) if ms else '', 'libreoffice': find_soffice() or '',
                              'rubberband': shutil.which('rubberband') or ''}}


@app.put('/api/einstellungen')
def put_settings(data: dict):
    s = settings()
    old_proj = Path(s['projektordner']).expanduser()
    for k in ('projektordner', 'ausgabe'):
        if data.get(k):
            data[k] = str(Path(data[k]).expanduser())
    s.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    new_proj = Path(s['projektordner'])
    moved = 0
    if new_proj.resolve() != old_proj.resolve():
        try:
            new_proj.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(400, f'Projektordner kann nicht angelegt werden: {e}')
        # vorhandene Projekte mitnehmen (nur, was im neuen Ordner noch nicht existiert)
        if old_proj.exists() and not any(j.get('status') == 'analyse' for j in JOBS.values()):
            for d in old_proj.iterdir():
                if (d / 'projekt.json').exists() and not (new_proj / d.name).exists():
                    shutil.move(str(d), str(new_proj / d.name))
                    moved += 1
            try:
                old_proj.rmdir()
            except OSError:
                pass
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding='utf-8')
    return {**s, 'verschoben': moved}


# ------------------------------------------------------------------ Projekte ----

@app.get('/api/projekte')
def list_projects():
    out = []
    for d in sorted(projects_dir().iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        meta = d / 'projekt.json'
        if meta.exists():
            m = json.loads(meta.read_text(encoding='utf-8'))
            m['id'] = d.name
            m['status'] = JOBS.get(d.name, {}).get('status', m.get('status', 'fertig'))
            out.append(m)
    return out


def _write_meta(pid, **kw):
    d = projects_dir() / pid
    meta = d / 'projekt.json'
    m = json.loads(meta.read_text(encoding='utf-8')) if meta.exists() else {}
    m.update(kw)
    meta.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding='utf-8')


def _run_analysis(pid: str, audio: Path, text: str, title: str, authors: str):
    from ..audio.analysis import build_song
    job = JOBS[pid]
    try:
        def prog(name, frac):
            job.update(schritt=name, fortschritt=round(frac, 3))
        # Zwischenspeicher (getrennte Spuren) im Projekt – wird mit dem Projekt gelöscht
        res = build_song(audio, text or None, title=title, authors=authors, progress=prog,
                         cache=audio.parent / '.analyse')
        song = res.song
        song.audio = str(audio)
        old = projects_dir() / pid / 'song.json'
        if old.exists():  # bei erneuter Analyse Ablauf/Besetzung behalten, soweit möglich
            try:
                prev = Song.load(old)
                song.lineup = prev.lineup
                song.transpose = prev.transpose
            except Exception:
                pass
        save_song(pid, song)
        _write_meta(pid, status='fertig', bericht=res.report)
        job.update(status='fertig', fortschritt=1.0, schritt='Fertig')
    except Exception as e:
        traceback.print_exc()
        job.update(status='fehler', fehler=str(e))
        _write_meta(pid, status='fehler', fehler=str(e))


@app.post('/api/projekte')
async def new_project(audio: UploadFile = File(...), titel: str = Form(''), autoren: str = Form(''),
                      text: str = Form('')):
    pid = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
    d = projects_dir() / pid
    d.mkdir(parents=True)
    name = re.sub(r'[^\w.\- ]+', '_', audio.filename or 'audio.mp3')
    dest = d / name
    with dest.open('wb') as f:
        shutil.copyfileobj(audio.file, f)
    (d / 'liedtext.txt').write_text(text, encoding='utf-8')
    title = titel or Path(name).stem
    _write_meta(pid, titel=title, autoren=autoren, audio=name, status='analyse', erstellt=time.time())
    JOBS[pid] = {'status': 'analyse', 'fortschritt': 0.0, 'schritt': 'Start'}
    threading.Thread(target=_run_analysis, args=(pid, dest, text, title, autoren), daemon=True).start()
    return {'id': pid}


@app.post('/api/projekte/import')
async def import_project(datei: UploadFile = File(...)):
    from ..importers.musescore import import_musescore
    pid = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
    d = projects_dir() / pid
    d.mkdir(parents=True)
    dest = d / re.sub(r'[^\w.\- ]+', '_', datei.filename or 'partitur.mscz')
    with dest.open('wb') as f:
        shutil.copyfileobj(datei.file, f)
    if dest.suffix.lower() == '.json':
        song = Song.load(dest)
    else:
        song = import_musescore(dest)
    save_song(pid, song)
    _write_meta(pid, titel=song.title, autoren=song.authors, status='fertig', erstellt=time.time())
    return {'id': pid}


@app.post('/api/projekte/{pid}/neu-analysieren')
def reanalyse(pid: str, data: dict):
    d = pdir(pid)
    meta = json.loads((d / 'projekt.json').read_text(encoding='utf-8'))
    text = data.get('text', '')
    (d / 'liedtext.txt').write_text(text, encoding='utf-8')
    JOBS[pid] = {'status': 'analyse', 'fortschritt': 0.0, 'schritt': 'Start'}
    _write_meta(pid, status='analyse')
    threading.Thread(target=_run_analysis, args=(pid, d / meta['audio'], text, data.get('titel') or meta['titel'],
                                                 data.get('autoren') or meta.get('autoren', '')), daemon=True).start()
    return {'ok': True}


@app.get('/api/projekte/{pid}')
def get_project(pid: str):
    d = pdir(pid)
    meta = json.loads((d / 'projekt.json').read_text(encoding='utf-8'))
    job = JOBS.get(pid)
    out = {'id': pid, 'meta': meta, 'job': job}
    if (d / 'song.json').exists() and (not job or job.get('status') != 'analyse'):
        song = load_song(pid)
        out['song'] = song_payload(song)
    lt = d / 'liedtext.txt'
    out['liedtext'] = lt.read_text(encoding='utf-8') if lt.exists() else ''
    return out


@app.delete('/api/projekte/{pid}')
def delete_project(pid: str):
    shutil.rmtree(pdir(pid))
    return {'ok': True}


@app.get('/api/projekte/{pid}/audio')
def get_audio(pid: str):
    d = pdir(pid)
    meta = json.loads((d / 'projekt.json').read_text(encoding='utf-8'))
    if not meta.get('audio'):
        raise HTTPException(404, 'Kein Audio')
    return FileResponse(d / meta['audio'])


@app.put('/api/projekte/{pid}/song')
def put_song(pid: str, data: dict):
    """Speichert Kopfdaten, Ablauf, Besetzung und Tonart (die Takte bleiben serverseitig)."""
    song = load_song(pid)
    for k in ('title', 'subtitle', 'authors', 'copyright', 'version', 'transpose', 'tempo', 'lineup'):
        if k in data:
            setattr(song, k, data[k])
    if 'form' in data:
        ids = {s.id for s in song.sections}
        form = []
        for f in data['form']:
            if f.get('section') in ids:
                form.append(FormItem(**{k: v for k, v in f.items() if k in FormItem.__dataclass_fields__}))
        song.form = form
    if 'sections' in data:
        for sd in data['sections']:
            try:
                sec = song.section(sd['id'])
            except KeyError:
                continue
            if 'label' in sd:
                sec.label = sd['label']
            if 'kind' in sd:
                sec.kind = sd['kind']
            if 'bar_chords' in sd:
                for bar, txt in zip(sec.bars, sd['bar_chords']):
                    bar.chords = _parse_cells(txt, bar)
        for i, sd in enumerate(data['sections']):
            if 'text' in sd:
                idx = next((k for k, s in enumerate(song.sections) if s.id == sd['id']), None)
                if idx is not None and sd['text'].strip() != '\n'.join(song.sections[idx].lines).strip():
                    if sd['text'].strip():
                        LY.apply_text(song.sections, idx, sd['text'])
    save_song(pid, song)
    _write_meta(pid, titel=song.title, autoren=song.authors)
    return song_payload(song)


@app.post('/api/projekte/{pid}/abschnitt-kopieren/{sid}')
def copy_section(pid: str, sid: str):
    """Abschnitt duplizieren (z. B. für ein eigenes Zwischenspiel aus dem Chorus)."""
    import copy as cp
    song = load_song(pid)
    src = song.section(sid)
    new = cp.deepcopy(src)
    k = 2
    while any(s.id == f'{sid}x{k}' for s in song.sections):
        k += 1
    new.id = f'{sid}x{k}'
    new.label = src.label + ' (Kopie)'
    song.sections.insert(song.sections.index(src) + 1, new)
    save_song(pid, song)
    return song_payload(song)


# ------------------------------------------------------------------ Vorschau ----

@app.get('/api/projekte/{pid}/vorschau/{art}')
def preview(pid: str, art: str):
    from ..sheets.chordsheet import build_chord_sheet
    from ..sheets.choirsheet import build_choir_sheet
    from ..sheets.instrument_sheets import build_guitar_sheet
    song = load_song(pid)
    d = pdir(pid) / 'vorschau'
    d.mkdir(exist_ok=True)
    if art == 'chordsheet':
        _, pdf = build_chord_sheet(song, d / 'Chordsheet.odt')
    elif art == 'chortext':
        s = song.transposed() if song.transpose else song
        _, pdf = build_choir_sheet(s, d / 'Chortext.odt')
    elif art == 'gitarre':
        _, pdf = build_guitar_sheet(song, d / 'A-GIT.odt')
    else:
        raise HTTPException(404)
    if not pdf:
        raise HTTPException(500, 'PDF konnte nicht erzeugt werden – ist LibreOffice installiert?')
    return FileResponse(pdf, media_type='application/pdf', headers={'Cache-Control': 'no-store'})


# ------------------------------------------------------------------ Stimmen & Tonart ----

@app.get('/api/stimmprofile')
def get_profiles():
    return [asdict(p) for p in load_profiles().values()]


@app.put('/api/stimmprofile')
def put_profiles(data: list):
    profs = {CONGREGATION.id: CONGREGATION}
    for d in data:
        if d.get('id') == 'gemeinde':
            profs['gemeinde'] = VoiceProfile(**{**d, 'is_congregation': True})
            continue
        if not d.get('name'):
            continue
        d['id'] = d.get('id') or re.sub(r'\W+', '', d['name'].lower()) or uuid.uuid4().hex[:6]
        profs[d['id']] = VoiceProfile(**{k: v for k, v in d.items() if k in VoiceProfile.__dataclass_fields__})
    save_profiles(profs)
    return [asdict(p) for p in profs.values()]


def _profiles():
    return load_profiles()


@app.get('/api/projekte/{pid}/tonart')
def keys(pid: str):
    song = load_song(pid)
    profs = _profiles()
    res = suggest_keys(song, profs, top=13)
    cur = evaluate_shift(song, song.transpose, profs)

    def fmt(r):
        return {'shift': r['shift'], 'tonart': r['key'], 'tonart_de': r['key_de'], 'wert': r['score'],
                'gitarre': r['guitar'], 'hinweise': [f"{i.who}: {i.text} – {i.section}" for i in r['issues'][:6]]}
    return {'vorschlaege': [fmt(r) for r in res], 'aktuell': fmt(cur),
            'umfang': ambitus_report(song, song.transpose),
            'tauschen': suggest_swaps(song, song.transpose, profs)}


# ------------------------------------------------------------------ Erzeugen ----

def _run_export(pid: str, opts_d: dict):
    from ..arranger.strings import Player
    from ..export import ExportOptions, export_all
    job = JOBS[pid + ':export']
    try:
        st = settings()
        song = load_song(pid)
        players = [Player(p['name'], p.get('instrument', 'Violine'), p.get('level', 'mittel'))
                   for p in opts_d.get('spieler', st['spieler'])]
        opts = ExportOptions(
            chordsheet=opts_d.get('chordsheet', True), leadsheet=opts_d.get('leadsheet', True),
            partitur=opts_d.get('partitur', True) and bool(players), parts_pdf=True,
            choir_text=opts_d.get('chortext', True), guitar=opts_d.get('gitarre', True),
            piano=opts_d.get('klavier', True), harmony_voices=opts_d.get('nebenstimmen', False),
            satb=opts_d.get('satb', False), practice_audio=opts_d.get('ueben', True),
            part_audio=opts_d.get('uebespuren', False), bitrate=str(opts_d.get('bitrate', st['bitrate'])),
            band=opts_d.get('band', st['band']), players=players)
        log = []

        def lg(m):
            log.append(m)
            job['log'] = log[-12:]
        res = export_all(song, opts_d.get('ausgabe') or st['ausgabe'], opts, log=lg)
        job.update(status='fertig', ergebnis=res)
    except Exception as e:
        traceback.print_exc()
        job.update(status='fehler', fehler=str(e))


@app.post('/api/projekte/{pid}/erzeugen')
def export(pid: str, data: dict):
    pdir(pid)
    JOBS[pid + ':export'] = {'status': 'läuft', 'log': []}
    threading.Thread(target=_run_export, args=(pid, data), daemon=True).start()
    return {'ok': True}


@app.get('/api/projekte/{pid}/erzeugen')
def export_status(pid: str):
    return JOBS.get(pid + ':export', {'status': 'keiner'})


@app.get('/api/datei')
def get_file(pfad: str):
    p = Path(pfad).resolve()
    st = settings()
    allowed = [Path(st['ausgabe']).expanduser().resolve(), Path(st['projektordner']).expanduser().resolve()]
    if not any(str(p).startswith(str(a)) for a in allowed) or not p.exists():
        raise HTTPException(403, 'Nicht erlaubt')
    return FileResponse(p, filename=p.name)


def main(host='127.0.0.1', port=8765, open_browser=True):
    import uvicorn
    projects_dir()
    if open_browser:
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(f'http://{host}:{port}')).start()
    uvicorn.run(app, host=host, port=port, log_level='warning')
