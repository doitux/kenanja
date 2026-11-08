"""Kenanja im eigenen Fenster (Qt WebEngine) – ohne Qt: Bedienung im normalen Browser.

Der lokale Server läuft im Hintergrund, das Fenster zeigt seine Seite an. Links nach außen
(z. B. „Text suchen“) gehen in den normalen Browser, Dateien öffnen sich im passenden Programm.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP = 'Kenanja'
STUFEN = ('gpu', 'opengl', 'software')   # Ausweichstufen, falls das Fenster weiß bleibt (Grafiktreiber)
INSTANZ = 'kenanja-fenster'


def _cfg() -> Path:
    from .voices import config_dir
    return config_dir() / 'fenster.json'


def _lesen() -> dict:
    try:
        return json.loads(_cfg().read_text(encoding='utf-8'))
    except Exception:
        return {}


def _schreiben(**kw):
    d = _lesen()
    d.update(kw)
    try:
        _cfg().write_text(json.dumps(d), encoding='utf-8')
    except OSError:
        pass


def grafik_stufe() -> str:
    s = os.environ.get('KENANJA_GRAFIK') or _lesen().get('grafik')
    return s if s in STUFEN else 'gpu'


def _userns_gesperrt() -> bool:
    """Ubuntu/Kubuntu ab 24.04 sperren Benutzer-Namensräume für Programme ohne AppArmor-Profil –
    dann startet die Chromium-Sandbox von Qt WebEngine nicht."""
    try:
        return Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns').read_text().strip() == '1'
    except OSError:
        return False


def _qt_env():
    # Das Fenster zeigt nur die eigene lokale Seite; Links nach außen gehen in den normalen Browser.
    if os.geteuid() == 0 or os.environ.get('APPIMAGE') or _userns_gesperrt():
        os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX', '1')
    flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
    if '--autoplay-policy' not in flags:
        flags += ' --autoplay-policy=no-user-gesture-required'
    stufe = grafik_stufe()
    if stufe in ('opengl', 'software'):
        os.environ.setdefault('QSG_RHI_BACKEND', 'opengl')
        flags += ' --disable-features=Vulkan,DefaultANGLEVulkan,VulkanFromANGLE --disable-vulkan-surface'
    if stufe == 'software':
        flags += ' --disable-gpu --disable-gpu-compositing'
    os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = flags.strip()


def _neustart(stufe: str):
    _schreiben(grafik=stufe)
    print(f'Fenster bleibt leer – starte neu mit Grafik-Stufe „{stufe}“', flush=True)
    env = dict(os.environ, KENANJA_GRAFIK=stufe)
    for k in ('QTWEBENGINE_CHROMIUM_FLAGS', 'QSG_RHI_BACKEND'):
        env.pop(k, None)
    sys.stdout.flush()
    os.execve(sys.executable, [sys.executable, '-m', 'kenanja', 'fenster'], env)


def qt_verfuegbar() -> bool:
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
        return True
    except Exception:
        return False


def _schon_offen() -> bool:
    """Läuft Kenanja schon? Dann dort das Fenster nach vorne holen und hier aufhören."""
    from PySide6.QtNetwork import QLocalSocket
    s = QLocalSocket()
    s.connectToServer(INSTANZ)
    if s.waitForConnected(400):
        s.write(b'zeigen')
        s.waitForBytesWritten(400)
        s.disconnectFromServer()
        return True
    return False


def run(url: str) -> int:
    from PySide6.QtCore import QFile, QIODevice, QObject, QTimer, QUrl, Slot
    from PySide6.QtGui import QDesktopServices, QIcon
    from PySide6.QtNetwork import QLocalServer
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox

    from .web import app as web

    qapp = QApplication.instance() or QApplication(sys.argv[:1])
    qapp.setApplicationName(APP)
    qapp.setDesktopFileName('kenanja')
    icon = Path(web.STATIC) / 'icon.svg'
    if icon.exists():
        qapp.setWindowIcon(QIcon(str(icon)))

    f = QFile(':/qtwebchannel/qwebchannel.js')
    if f.open(QIODevice.ReadOnly):
        web.QWEBCHANNEL_JS = bytes(f.readAll())
        f.close()

    lokal = QUrl(url)

    def ist_lokal(u: QUrl) -> bool:
        return u.host() == lokal.host() and u.port() == lokal.port()

    class Bruecke(QObject):
        @Slot(str, str, result=str)
        def ordnerWaehlen(self, titel, start):
            d = QFileDialog.getExistingDirectory(win, titel or 'Ordner wählen', start or str(Path.home()))
            return d or ''

        @Slot(str)
        def extern(self, adresse):
            QDesktopServices.openUrl(QUrl(adresse))

    class Aussen(QWebEnginePage):
        """Fängt neue Fenster (target=_blank, window.open) ab und gibt sie an den normalen Browser."""
        def acceptNavigationRequest(self, u, typ, main):
            QDesktopServices.openUrl(u)
            self.deleteLater()
            return False

    class Seite(QWebEnginePage):
        def acceptNavigationRequest(self, u, typ, main):
            if main and not ist_lokal(u) and u.scheme() in ('http', 'https', 'mailto'):
                QDesktopServices.openUrl(u)
                return False
            return super().acceptNavigationRequest(u, typ, main)

        def createWindow(self, typ):
            return Aussen(self.profile(), self)

        def javaScriptConsoleMessage(self, level, msg, line, src):
            if os.environ.get('KENANJA_DEBUG'):
                print(f'[js] {msg} ({src}:{line})', flush=True)

    class Fenster(QMainWindow):
        def closeEvent(self, ev):
            laufend = web.busy_jobs()
            if laufend and not os.environ.get('KENANJA_SCREENSHOT'):
                r = QMessageBox.question(self, APP, 'Es läuft noch eine Analyse oder ein Export '
                                         f'({", ".join(sorted(set(laufend)))}).\nTrotzdem beenden?')
                if r != QMessageBox.Yes:
                    ev.ignore()
                    return
            geo = self.saveGeometry().toBase64().data().decode()
            _schreiben(geometrie=geo)
            ev.accept()

    win = Fenster()
    win.setWindowTitle(APP)
    win.resize(1360, 900)
    geo = _lesen().get('geometrie')
    if geo:
        from PySide6.QtCore import QByteArray
        win.restoreGeometry(QByteArray.fromBase64(geo.encode()))
    view = QWebEngineView(win)
    profil = QWebEngineProfile(win)   # ohne Namen = nichts auf der Platte (kein Browser-Cache unter ~/.cache)
    page = Seite(profil, view)
    view.setPage(page)
    st = page.settings()
    st.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
    st.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
    if hasattr(QWebEngineSettings, 'PdfViewerEnabled'):
        st.setAttribute(QWebEngineSettings.PdfViewerEnabled, True)

    def herunterladen(item):   # falls doch ein Download ausgelöst wird: in ~/Downloads ablegen
        ziel = Path.home() / 'Downloads'
        ziel.mkdir(exist_ok=True)
        try:
            item.setDownloadDirectory(str(ziel))
        except AttributeError:
            item.setPath(str(ziel / item.downloadFileName()))
        item.accept()
        item.isFinishedChanged.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ziel / item.downloadFileName()))))
    profil.downloadRequested.connect(herunterladen)

    kanal = QWebChannel(page)
    bruecke = Bruecke()
    kanal.registerObject('bridge', bruecke)
    page.setWebChannel(kanal)

    # zweiter Start: vorhandenes Fenster nach vorne holen
    QLocalServer.removeServer(INSTANZ)
    instanz = QLocalServer(win)
    instanz.listen(INSTANZ)

    def vorholen():
        c = instanz.nextPendingConnection()
        if c:
            c.close()
        win.showNormal()
        win.raise_()
        win.activateWindow()
    instanz.newConnection.connect(vorholen)

    def pruefen(ok):
        """Bleibt die dunkle Kopfzeile weiß, kommt der Grafiktreiber nicht klar → nächste Stufe."""
        if not ok or os.environ.get('KENANJA_SCREENSHOT'):
            return

        def check():
            img = view.grab().toImage()
            w = min(img.width(), 600)
            if w < 50 or img.height() < 40:
                return
            dunkel = gesamt = 0
            for x in range(10, w, 7):
                for y in (8, 20, 32):
                    c = img.pixelColor(x, y)
                    gesamt += 1
                    dunkel += (c.red() + c.green() + c.blue()) < 240
            if dunkel < gesamt * 0.3:
                n = STUFEN.index(grafik_stufe()) + 1
                if n < len(STUFEN):
                    _neustart(STUFEN[n])
                else:
                    print('Fenster bleibt leer – bitte mit „kenanja web“ im Browser starten', flush=True)
        QTimer.singleShot(2500, check)
    page.loadFinished.connect(pruefen)

    view.setUrl(lokal)
    win.setCentralWidget(view)
    win.show()
    if os.environ.get('KENANJA_SCREENSHOT'):   # für automatische Tests
        def shot():
            js = os.environ.get('KENANJA_JS')
            if js:
                page.runJavaScript(js)
                QTimer.singleShot(int(os.environ.get('KENANJA_JS_MS', '3000')),
                                  lambda: (win.grab().save(os.environ['KENANJA_SCREENSHOT']), qapp.quit()))
            else:
                win.grab().save(os.environ['KENANJA_SCREENSHOT'])
                qapp.quit()
        QTimer.singleShot(int(os.environ.get('KENANJA_SCREENSHOT_MS', '5000')), shot)
    return qapp.exec()


def main(browser: bool = False, port: int = 0):
    from .web import app as web
    if not browser and qt_verfuegbar():
        _qt_env()
        from PySide6.QtWidgets import QApplication
        qapp = QApplication(sys.argv[:1])
        if _schon_offen():
            print('Kenanja ist schon offen – Fenster nach vorne geholt.', flush=True)
            return 0
        server, url = web.start_server(port=port)
        code = run(url)
        server.should_exit = True
        return code
    if not browser:
        print('Qt (PySide6) nicht gefunden – öffne den Browser.', flush=True)
    web.main(port=port or 8765, open_browser=True)
    return 0
