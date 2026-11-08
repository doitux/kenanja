"""Entfernt aus dem PyInstaller-Ergebnis alle Qt-Teile, die Kenanja nicht braucht (Abhängigkeits-Hülle)."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]) / "_internal"
qt = root / "PySide6" / "Qt"
KEEP_PLUGINS = {"platforms": r"libq(xcb|wayland.*|offscreen|minimal)\.so", "xcbglintegrations": r".*",
                "wayland-decoration-client": r".*", "wayland-graphics-integration-client": r".*",
                "wayland-shell-integration": r".*", "platformthemes": r"libqxdgdesktopportal\.so", "imageformats": r".*",
                "iconengines": r".*", "tls": r".*", "platforminputcontexts": r"lib(compose|ibus)platforminputcontextplugin\.so",
                "networkinformation": r".*",
                "printsupport": r".*"}


def needed(f):
    try:
        out = subprocess.run(["readelf", "-d", str(f)], capture_output=True, text=True).stdout
    except FileNotFoundError:
        sys.exit("readelf fehlt (binutils)")
    return re.findall(r"\(NEEDED\)\s+Shared library: \[(.+?)\]", out)


for d in (qt / "plugins").iterdir():
    if d.name not in KEEP_PLUGINS:
        shutil.rmtree(d)
        continue
    for f in d.iterdir():
        if not re.fullmatch(KEEP_PLUGINS[d.name], f.name):
            f.unlink()
start = list((root / "PySide6").glob("*.abi3.so")) + list((root / "PySide6").glob("libpyside6*.so*")) + \
    list((root / "shiboken6").glob("*.so*")) + [qt / "libexec" / "QtWebEngineProcess"] + \
    [f for f in (qt / "plugins").rglob("*.so")]
libdirs = [qt / "lib", root, root / "PySide6", root / "shiboken6"]
index = {}
for d in libdirs:
    if d.exists():
        for f in d.iterdir():
            if f.is_file() or f.is_symlink():
                index.setdefault(f.name, f)
keep = set()
todo = list(start)
while todo:
    f = todo.pop()
    for n in needed(f):
        p = index.get(n)
        if p is not None and p.name not in keep:
            keep.add(p.name)
            todo.append(p.resolve())
removed = 0
# xkbcommon vom System nehmen: passt zu dessen Tastatur-/Compose-Dateien (sonst Fehlermeldungen wie „dead_hamza“)
for f in list(root.glob("libxkbcommon*.so*")) + list((qt / "lib").glob("libxkbcommon*.so*")):
    removed += f.stat().st_size
    f.unlink()
for f in (qt / "lib").iterdir():
    if f.name.startswith("libQt6") and f.name not in keep:
        removed += f.stat().st_size
        f.unlink()
for d in ("qml",):
    if (qt / d).exists():
        removed += sum(x.stat().st_size for x in (qt / d).rglob("*") if x.is_file())
        shutil.rmtree(qt / d)
tr = qt / "translations"
if tr.exists():
    for f in list(tr.rglob("*")):
        if f.is_file():
            keepit = (f.parent.name == "qtwebengine_locales" and f.name in ("de.pak", "en-US.pak")) or \
                     f.name in ("qtbase_de.qm", "qtwebengine_de.qm", "qt_de.qm")
            if not keepit:
                removed += f.stat().st_size
                f.unlink()
# nicht gebrauchte PySide6-Python-Module (ohne Qt-Bibliothek)
for f in (root / "PySide6").glob("Qt*.abi3.so"):
    lib = "libQt6" + f.name.split(".")[0][2:] + ".so.6"
    if lib not in keep and f.name not in ("QtCore.abi3.so",):
        removed += f.stat().st_size
        f.unlink()
print(f"entfernt: {removed / 1e6:.0f} MB, behalten: {sorted(x for x in keep if x.startswith('libQt6'))}")
