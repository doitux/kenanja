#!/bin/bash
# Baut Kenanja-<Version>-x86_64.AppImage (bzw. Kenanja-nightly-<Build>-<Commit>-x86_64.AppImage)
#   PY=…/python (mit allen Abhängigkeiten + pyinstaller)  TOOLS=Ordner mit appimagetool (+ optional runtime-x86_64)
#   In der CI ruft packaging/ci_build.sh das hier auf.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(dirname "$HERE")"
OUT="${OUT:-$ROOT/dist}"; WORK="${WORK:-$OUT/build}"
VERSION=$("$PY" -c "import sys; sys.path.insert(0,'$ROOT'); import kenanja; print(kenanja.__version__)")
BUILD="${KENANJA_BUILD:-}"
printf 'BUILD = "%s"\n' "$BUILD" > "$ROOT/kenanja/_build.py"
LABEL="${BUILD:-$VERSION}"
rm -rf "$WORK" "$OUT/kenanja" "$OUT/AppDir"; mkdir -p "$OUT"
"$PY" -m PyInstaller --noconfirm --clean --log-level WARN --distpath "$OUT" --workpath "$WORK" "$HERE/kenanja.spec"
"$PY" "$HERE/prune_qt.py" "$OUT/kenanja"
A="$OUT/AppDir"; mkdir -p "$A/usr/lib" "$A/usr/share/applications" "$A/usr/share/icons/hicolor/256x256/apps"
mv "$OUT/kenanja" "$A/usr/lib/kenanja"
cp "$HERE/kenanja.desktop" "$A/kenanja.desktop"; cp "$HERE/kenanja.desktop" "$A/usr/share/applications/"
cp "$ROOT/kenanja/web/static/icon.png" "$A/kenanja.png"; cp "$ROOT/kenanja/web/static/icon.png" "$A/usr/share/icons/hicolor/256x256/apps/kenanja.png"
ln -sf kenanja.png "$A/.DirIcon"
cat > "$A/AppRun" <<'RUN'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/kenanja/kenanja" "$@"
RUN
chmod +x "$A/AppRun"
RT=(); [ -f "$TOOLS/runtime-x86_64" ] && RT=(--runtime-file "$TOOLS/runtime-x86_64")
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOLS/appimagetool" "${RT[@]}" --comp zstd -n "$A" "$OUT/Kenanja-$LABEL-x86_64.AppImage"
rm -f "$ROOT/kenanja/_build.py"
ls -la "$OUT"/*.AppImage
