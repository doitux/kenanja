"""Kleinigkeiten, die überall gebraucht werden – v. a. für das gepackte Programm (AppImage)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, 'frozen', False))


def clean_env(env: dict | None = None) -> dict:
    """Umgebung für fremde Programme (ffmpeg, LibreOffice, MuseScore, xdg-open …): ohne die Bibliothekspfade
    des gepackten Programms – sonst laden sie unsere Bibliotheken statt ihrer eigenen."""
    env = dict(os.environ if env is None else env)
    if not FROZEN:
        return env
    for k in ('LD_LIBRARY_PATH', 'PYTHONHOME', 'PYTHONPATH', 'QT_PLUGIN_PATH', 'QML2_IMPORT_PATH',
              'QTWEBENGINEPROCESS_PATH', 'QTWEBENGINE_RESOURCES_PATH', 'QTWEBENGINE_LOCALES_PATH',
              'QT_QPA_PLATFORM_PLUGIN_PATH', 'SSL_CERT_FILE', 'GDK_PIXBUF_MODULE_FILE', 'QTWEBENGINE_CHROMIUM_FLAGS',
              'QTWEBENGINE_DISABLE_SANDBOX', 'QSG_RHI_BACKEND'):
        orig = env.pop(k + '_ORIG', None)
        env.pop(k, None)
        if orig:
            env[k] = orig
    inside = [p for p in (getattr(sys, '_MEIPASS', None), os.environ.get('APPDIR'),
                          str(Path(sys.executable).resolve().parent)) if p]
    for k in ('LD_LIBRARY_PATH', 'QT_PLUGIN_PATH', 'PYTHONPATH', 'PATH'):
        if k in env:
            keep = [p for p in env[k].split(':') if p and not any(p.startswith(i) for i in inside)]
            if keep:
                env[k] = ':'.join(keep)
            else:
                env.pop(k)
    return env


def patch_subprocess():
    """Im gepackten Programm: jeder Aufruf eines fremden Programms bekommt automatisch die saubere Umgebung."""
    if not FROZEN:
        return
    import subprocess
    orig = subprocess.Popen.__init__

    def init(self, *args, **kw):
        kw['env'] = clean_env(kw.get('env'))
        orig(self, *args, **kw)
    subprocess.Popen.__init__ = init


def version_text() -> str:
    from . import __version__
    try:
        from ._build import BUILD
    except ImportError:
        BUILD = ''
    return f'{__version__} ({BUILD})' if BUILD else __version__


def desktop_integration():
    """Als AppImage: Startmenü-Eintrag und Symbol anlegen bzw. auf den aktuellen Ort der AppImage zeigen lassen."""
    app = os.environ.get('APPIMAGE')
    if not app:
        return
    share = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    icon_src = Path(__file__).resolve().parent / 'web' / 'static' / 'icon.svg'
    icon = share / 'icons' / 'hicolor' / 'scalable' / 'apps' / 'kenanja.svg'
    desk = share / 'applications' / 'kenanja.desktop'
    q = app.replace('"', '\\"')
    text = f'''[Desktop Entry]
Type=Application
Name=Kenanja
GenericName=Noten für die Lobpreisband
Comment=Chord-Sheets, Leadsheets, Streicher, Chorblatt und Übe-Audio aus einem MP3
Exec="{q}" %F
Icon=kenanja
Terminal=false
Categories=AudioVideo;Audio;Music;
Keywords=Noten;Chords;Leadsheet;Band;Lobpreis;MuseScore;
StartupWMClass=kenanja
Actions=browser;

[Desktop Action browser]
Name=Im Browser öffnen
Exec="{q}" web
'''
    try:
        if not desk.exists() or desk.read_text(encoding='utf-8') != text:
            desk.parent.mkdir(parents=True, exist_ok=True)
            desk.write_text(text, encoding='utf-8')
        if icon_src.exists() and not icon.exists():
            icon.parent.mkdir(parents=True, exist_ok=True)
            icon.write_bytes(icon_src.read_bytes())
    except OSError:
        pass


def uninstall(alles: bool = False):
    """Alles entfernen, was Kenanja außerhalb der Projekte angelegt hat – und die AppImage selbst."""
    import json
    import shutil
    home = Path.home()
    share = Path(os.environ.get('XDG_DATA_HOME') or home / '.local' / 'share')
    cache = Path(os.environ.get('XDG_CACHE_HOME') or home / '.cache')
    cfg = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config') / 'kenanja'
    proj = home / 'Projekte' / 'Kenanja'
    try:
        p = json.loads((cfg / 'einstellungen.json').read_text(encoding='utf-8')).get('projektordner')
        if p:
            proj = Path(p).expanduser()
    except Exception:
        pass
    weg = [share / 'kenanja', home / '.local' / 'bin' / 'kenanja', share / 'applications' / 'kenanja.desktop',
           share / 'icons' / 'hicolor' / 'scalable' / 'apps' / 'kenanja.svg', cache / 'kenanja', cache / 'Kenanja',
           share / 'Kenanja', cfg]
    for p in weg:
        if p.is_symlink() or p.is_file():
            p.unlink()
            print('entfernt:', p)
        elif p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            print('entfernt:', p)
    if alles:
        if proj.is_dir():
            for d in proj.iterdir():   # nur echte Kenanja-Projekte, nie den Ordner blind
                if (d / 'projekt.json').exists():
                    shutil.rmtree(d, ignore_errors=True)
                    print('Projekt gelöscht:', d.name)
            for leer in (proj, proj.parent):
                try:
                    leer.rmdir()
                except OSError:
                    pass
        shutil.rmtree(home / 'Kenanja', ignore_errors=True)
    app = os.environ.get('APPIMAGE')
    if app and Path(app).exists():
        Path(app).unlink()
        print('entfernt:', app)
    print('Kenanja ist entfernt.' + ('' if alles else f' Deine Projekte liegen weiter in {proj}.'))
