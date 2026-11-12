# PyInstaller-Bauplan für Kenanja (onedir, wird danach als AppImage verpackt)
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent
datas = [(str(ROOT / 'kenanja' / 'data'), 'kenanja/data'),
         (str(ROOT / 'kenanja' / 'web' / 'static'), 'kenanja/web/static'),
         (str(ROOT / 'kenanja' / 'audio' / 'modelle'), 'kenanja/audio/modelle')]
# music21 ohne seinen großen Notenkorpus (wird nicht gebraucht)
datas += [d for d in collect_data_files('music21') if '/corpus/' not in d[0].replace('\\', '/')]
datas += collect_data_files('pyphen') + collect_data_files('librosa')
binaries = collect_dynamic_libs('onnxruntime')
hidden = ['PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore', 'PySide6.QtWebChannel', 'PySide6.QtNetwork',
          'PySide6.QtPrintSupport', 'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
          'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'multipart']
hidden += collect_submodules('kenanja')
try:
    import sherpa_onnx  # noqa: F401  (Spracherkennung; Modell wird bei Bedarf geladen)
    hidden += ['sherpa_onnx']
    binaries += collect_dynamic_libs('sherpa_onnx')
except ImportError:
    pass
try:
    import faster_whisper  # noqa: F401  (Spracherkennung, optional)
    hidden += ['faster_whisper', 'ctranslate2']
    datas += collect_data_files('faster_whisper')
    binaries += collect_dynamic_libs('ctranslate2')
except ImportError:
    pass
excl = ['tkinter', 'matplotlib', 'pandas', 'IPython', 'jupyter', 'notebook', 'pytest', 'torch',
        'music21.corpus._metadataCache', 'PySide6.Qt3DAnimation', 'PySide6.Qt3DCore', 'PySide6.Qt3DExtras',
        'PySide6.Qt3DInput', 'PySide6.Qt3DLogic', 'PySide6.Qt3DRender', 'PySide6.QtCharts',
        'PySide6.QtDataVisualization', 'PySide6.QtGraphs', 'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtQuick3D', 'PySide6.QtSpatialAudio', 'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtSensors',
        'PySide6.QtSerialPort', 'PySide6.QtSerialBus', 'PySide6.QtTextToSpeech', 'PySide6.QtPdf',
        'PySide6.QtPdfWidgets', 'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtSql', 'PySide6.QtTest',
        'PySide6.QtRemoteObjects', 'PySide6.QtScxml', 'PySide6.QtStateMachine', 'PySide6.QtLocation',
        'PySide6.QtPositioning', 'PySide6.QtHttpServer', 'PySide6.QtWebSockets', 'PySide6.QtUiTools',
        'PySide6.QtAxContainer', 'PySide6.QtConcurrent', 'PySide6.QtNetworkAuth', 'PySide6.QtSvgWidgets']
a = Analysis([str(ROOT / 'packaging' / 'start.py')], pathex=[str(ROOT)], binaries=binaries, datas=datas,
             hiddenimports=hidden, excludes=excl, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='kenanja', console=True, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='kenanja')
