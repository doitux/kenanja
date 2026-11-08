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
HOME = Path(os.environ.get('KENANJA_HOME', str(Path.home() / 'Kenanja')))   # alter Standard (Version 0.1)
from ..voices import config_dir as _config_dir  # noqa: E402
SETTINGS_FILE = _config_dir() / 'einstellungen.json'
OLD_SETTINGS_FILE = HOME / 'einstellungen.json'

# Aufbau eines Projekts:  <Projektordner>/<Projektname>/
#   projekt.json  song.kenanja.json  liedtext.txt
#   Original/  (hochgeladenes Audio bzw. MuseScore-Datei)
#   Analyse/   (Zwischenspeicher: getrennte Spuren)
#   Export/    (alle erzeugten Blätter, Noten, MP3s)
#   Vorschau/  (schnelle Vorschau-PDFs)
SONG_FILE = 'song.kenanja.json'
SUB = {'original': 'Original', 'analyse': 'Analyse', 'export': 'Export', 'vorschau': 'Vorschau'}

DEFAULT_SETTINGS = {
    'projektordner': str(Path.home() / 'Projekte' / 'Kenanja'),
    'bandordner': '',            # optional: fertigen Export zusätzlich dorthin kopieren
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
_LOCK = threading.Lock()


def settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    src = SETTINGS_FILE if SETTINGS_FILE.exists() else (OLD_SETTINGS_FILE if OLD_SETTINGS_FILE.exists() else None)
    if src:
        saved = json.loads(src.read_text(encoding='utf-8'))
        if 'ausgabe' in saved and 'bandordner' not in saved:   # Version 0.1: eigener Ausgabeordner → Band-Ordner
            out = str(Path(saved['ausgabe']).expanduser())
            saved['bandordner'] = '' if out == str(HOME / 'Songs') else out
        if src == OLD_SETTINGS_FILE or str(Path(saved.get('projektordner', '')).expanduser()) == str(HOME / 'Projekte'):
            saved.pop('projektordner', None)    # alter Standard → neuer Standard ~/Projekte/Kenanja
        s.update({k: v for k, v in saved.items() if k in DEFAULT_SETTINGS})
    if s.get('musescore'):
        os.environ['KENANJA_MUSESCORE'] = s['musescore']
    return s


def save_settings(s: dict):
    SETTINGS_FILE.write_text(json.dumps({k: s[k] for k in DEFAULT_SETTINGS if k in s}, ensure_ascii=False, indent=1),
                             encoding='utf-8')


def projects_dir() -> Path:
    d = Path(settings()['projektordner']).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(name: str) -> str:
    n = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', name or '').strip().strip('.')
    return re.sub(r'\s+', ' ', n)[:80] or 'Neuer Song'


def unique_dir(name: str, base: Optional[Path] = None, keep: Optional[Path] = None) -> Path:
    base = base or projects_dir()
    cand = base / safe_name(name)
    k = 2
    while cand.exists() and cand != keep:
        cand = base / f'{safe_name(name)} ({k})'
        k += 1
    return cand


def pdir(pid: str) -> Path:
    if not pid or '/' in pid or '\\' in pid or pid.startswith('.'):
        raise HTTPException(400, 'Ungültiger Projektname')
    d = projects_dir() / pid
    if not (d / 'projekt.json').exists():
        raise HTTPException(404, 'Projekt nicht gefunden')
    return d


def sub(pid_or_dir, key: str) -> Path:
    d = pid_or_dir if isinstance(pid_or_dir, Path) else pdir(pid_or_dir)
    p = d / SUB[key]
    p.mkdir(exist_ok=True)
    return p


def load_song(pid: str) -> Song:
    d = pdir(pid)
    song = Song.load(d / SONG_FILE)
    if song.audio and not Path(song.audio).is_absolute():
        song.audio = str(d / song.audio)
    return song


def save_song(pid: str, song: Song):
    d = pdir(pid)
    audio = song.audio
    if audio:
        try:
            song.audio = str(Path(audio).resolve().relative_to(d.resolve()))
        except ValueError:
            pass
    song.save(d / SONG_FILE)
    song.audio = audio


def migrate_projects():
    """Projekte aus Version 0.1 (Zeitstempel-Ordner, alter Standardordner) ins neue Schema bringen."""
    base = projects_dir()
    old_base = HOME / 'Projekte'
    sources = [base]
    if old_base.exists() and old_base.resolve() != base.resolve():
        sources.append(old_base)
    for src in sources:
        for d in list(src.iterdir()):
            meta_f = d / 'projekt.json'
            if not d.is_dir() or not meta_f.exists():
                continue
            if JOBS.get(d.name, {}).get('status') == 'analyse':
                continue
            meta = json.loads(meta_f.read_text(encoding='utf-8'))
            if (d / 'song.json').exists() and not (d / SONG_FILE).exists():
                (d / 'song.json').rename(d / SONG_FILE)
            for old, new in (('.analyse', 'Analyse'), ('vorschau', 'Vorschau')):
                if (d / old).exists() and not (d / new).exists():
                    (d / old).rename(d / new)
            for f in list(d.iterdir()):   # importierte Noten ebenfalls nach Original/
                if f.is_file() and f.suffix.lower() in ('.mscz', '.mscx') and f.name != meta.get('audio'):
                    (d / 'Original').mkdir(exist_ok=True)
                    f.rename(d / 'Original' / f.name)
            a = meta.get('audio')
            if a and (d / a).exists() and '/' not in a:
                (d / 'Original').mkdir(exist_ok=True)
                (d / a).rename(d / 'Original' / a)
                meta['audio'] = f'Original/{a}'
                if (d / SONG_FILE).exists():
                    sj = json.loads((d / SONG_FILE).read_text(encoding='utf-8'))
                    sj['audio'] = meta['audio']
                    (d / SONG_FILE).write_text(json.dumps(sj, ensure_ascii=False, indent=1), encoding='utf-8')
            meta_f.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
            if src != base or re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{4}', d.name):
                target = unique_dir(meta.get('titel') or d.name, base)
                shutil.move(str(d), str(target))
        if src != base:
            for leer in (src, HOME):   # leere Ordner der alten Version aufräumen
                try:
                    leer.rmdir()
                except OSError:
                    pass


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
    for k in ('projektordner', 'bandordner'):
        if data.get(k):
            data[k] = str(Path(data[k]).expanduser())
    s.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    new_proj = Path(s['projektordner'])
    moved = 0
    if new_proj.resolve() != old_proj.resolve():
        if any(j.get('status') in ('analyse', 'läuft') for j in JOBS.values()):
            raise HTTPException(409, 'Gerade läuft eine Analyse oder ein Export – bitte kurz warten.')
        try:
            new_proj.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(400, f'Projektordner kann nicht angelegt werden: {e}')
        if old_proj.exists():
            for d in old_proj.iterdir():
                if (d / 'projekt.json').exists():
                    shutil.move(str(d), str(unique_dir(d.name, new_proj)))
                    moved += 1
            try:
                old_proj.rmdir()
            except OSError:
                pass
    save_settings(s)
    return {**s, 'verschoben': moved}


# ------------------------------------------------------------------ Projekte ----

@app.get('/api/projekte')
def list_projects():
    with _LOCK:
        migrate_projects()
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
    d.mkdir(parents=True, exist_ok=True)
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
        d = projects_dir() / pid
        res = build_song(audio, text or None, title=title, authors=authors, progress=prog, cache=sub(d, 'analyse'))
        song = res.song
        song.audio = str(audio)
        old = d / SONG_FILE
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
    name = re.sub(r'[^\w.\- ]+', '_', audio.filename or 'audio.mp3')
    title = titel.strip() or Path(name).stem.replace('_', ' ')
    with _LOCK:
        d = unique_dir(title)
        d.mkdir(parents=True)
    pid = d.name
    dest = sub(d, 'original') / name
    with dest.open('wb') as f:
        shutil.copyfileobj(audio.file, f)
    (d / 'liedtext.txt').write_text(text, encoding='utf-8')
    _write_meta(pid, titel=title, autoren=autoren, audio=f"{SUB['original']}/{name}", status='analyse',
                erstellt=time.time())
    JOBS[pid] = {'status': 'analyse', 'fortschritt': 0.0, 'schritt': 'Start'}
    threading.Thread(target=_run_analysis, args=(pid, dest, text, title, autoren), daemon=True).start()
    return {'id': pid}


@app.post('/api/projekte/import')
async def import_project(datei: UploadFile = File(...)):
    from ..importers.musescore import import_musescore
    import tempfile
    fname = re.sub(r'[^\w.\- ]+', '_', datei.filename or 'partitur.mscz')
    tmp = Path(tempfile.mkdtemp(prefix='kenanja_imp_')) / fname
    with tmp.open('wb') as f:
        shutil.copyfileobj(datei.file, f)
    song = Song.load(tmp) if tmp.suffix.lower() == '.json' else import_musescore(tmp)
    with _LOCK:
        d = unique_dir(song.title or tmp.stem)
        d.mkdir(parents=True)
    pid = d.name
    shutil.move(str(tmp), str(sub(d, 'original') / fname))
    shutil.rmtree(tmp.parent, ignore_errors=True)
    song.notes['source'] = f"{SUB['original']}/{fname}"
    _write_meta(pid, titel=song.title, autoren=song.authors, status='fertig', erstellt=time.time())
    save_song(pid, song)
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
    out = {'id': pid, 'meta': meta, 'job': job, 'pfad': str(d)}
    if (d / SONG_FILE).exists() and (not job or job.get('status') != 'analyse'):
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
    new_id = _rename_project(pid, song.title)
    return {**song_payload(song), 'id': new_id}


def _rename_project(pid: str, title: str) -> str:
    """Projektordner heißt wie der Song – bei Titeländerung mit umbenennen (nicht während Analyse/Export)."""
    if not title or safe_name(title) == re.sub(r' \(\d+\)$', '', pid):
        return pid
    if any(k.split(':')[0] == pid and j.get('status') in ('analyse', 'läuft') for k, j in JOBS.items()):
        return pid
    with _LOCK:
        d = pdir(pid)
        target = unique_dir(title, keep=d)
        if target == d:
            return pid
        d.rename(target)
        for k in [k for k in JOBS if k.split(':')[0] == pid]:
            JOBS[target.name + k[len(pid):]] = JOBS.pop(k)
    return target.name


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
def preview(pid: str, art: str, oeffnen: int = 0):
    from ..sheets.chordsheet import build_chord_sheet
    from ..sheets.choirsheet import build_choir_sheet
    from ..sheets.instrument_sheets import build_guitar_sheet
    song = load_song(pid)
    d = sub(pid, 'vorschau')
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
    if oeffnen:   # im eigenen Fenster: im PDF-Betrachter (Okular …) öffnen
        return open_path({'pfad': str(pdf)})
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
        res = export_all(song, opts=opts, log=lg, folder=sub(pid, 'export'))
        band = (opts_d.get('bandordner') if 'bandordner' in opts_d else st.get('bandordner')) or ''
        if band:   # zusätzlich in den Band-Ordner (z. B. Nextcloud) kopieren
            from ..export import folder_name
            try:
                dst = Path(band).expanduser() / folder_name(song)
                shutil.copytree(res['ordner'], dst, dirs_exist_ok=True)
                res['kopie'] = str(dst)
                lg(f'Kopie in {dst}')
            except OSError as e:
                res.setdefault('fehler', []).append(f'Kopie in den Band-Ordner: {e}')
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


def _allowed(pfad: str) -> Path:
    p = Path(pfad).expanduser().resolve()
    st = settings()
    roots = [Path(st[k]).expanduser().resolve() for k in ('projektordner', 'bandordner') if st.get(k)]
    if not any(p == r or r in p.parents for r in roots) or not p.exists():
        raise HTTPException(403, 'Nicht erlaubt')
    return p


@app.get('/api/datei')
def get_file(pfad: str):
    p = _allowed(pfad)
    return FileResponse(p, filename=p.name)


@app.post('/api/oeffnen')
def open_path(data: dict):
    """Datei oder Ordner mit dem passenden Programm öffnen (LibreOffice, MuseScore, Dolphin …)."""
    import subprocess
    p = _allowed(data.get('pfad', ''))
    opener = shutil.which('xdg-open') or shutil.which('kde-open') or shutil.which('gio')
    if not opener:
        raise HTTPException(500, 'xdg-open nicht gefunden')
    args = [opener, 'open', str(p)] if opener.endswith('gio') else [opener, str(p)]
    env = {k: v for k, v in os.environ.items() if k not in ('QT_QPA_PLATFORM', 'LD_LIBRARY_PATH', 'QTWEBENGINE_CHROMIUM_FLAGS')}
    subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {'ok': True}


@app.get('/api/projekte/{pid}/ordner')
def project_folder(pid: str, art: str = ''):
    d = pdir(pid)
    return {'pfad': str(sub(d, art) if art in SUB else d)}


QWEBCHANNEL_JS: bytes = b''


@app.get('/qwebchannel.js')
def qwebchannel():
    from fastapi.responses import Response
    return Response(QWEBCHANNEL_JS or b'/* kein Kenanja-Fenster */', media_type='application/javascript')


def start_server(host='127.0.0.1', port=0):
    """Server im Hintergrund starten; gibt die URL zurück (Port 0 = einen freien wählen)."""
    import socket
    import uvicorn
    projects_dir()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError:
        sock.bind((host, 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='warning'))
    threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    return server, f'http://{host}:{port}'


def busy_jobs() -> list[str]:
    return [k.split(':')[0] for k, j in JOBS.items() if j.get('status') in ('analyse', 'läuft')]


def main(host='127.0.0.1', port=8765, open_browser=True):
    import webbrowser
    server, url = start_server(host, port)
    print(f'Kenanja läuft unter {url} – beenden mit Strg+C', flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.should_exit = True
