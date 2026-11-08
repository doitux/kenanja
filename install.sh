#!/usr/bin/env bash
# Kenanja installieren (Kubuntu/Ubuntu/Debian). Aufruf im entpackten/geklonten Ordner: bash install.sh
set -e
cd "$(dirname "$0")"
PREFIX="$HOME/.local/share/kenanja"
BIN="$HOME/.local/bin"

echo "== Systempakete (sudo) =="
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip ffmpeg rubberband-cli libreoffice-writer fonts-liberation fonts-dejavu-core >/dev/null

echo "== Python-Umgebung =="
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip wheel
"$PREFIX/venv/bin/pip" install -q ".[whisper]" || "$PREFIX/venv/bin/pip" install -q .

mkdir -p "$BIN"
ln -sf "$PREFIX/venv/bin/kenanja" "$BIN/kenanja"

echo "== Startmenü-Eintrag =="
mkdir -p "$HOME/.local/share/applications" "$PREFIX"
cp kenanja/web/static/icon.svg "$PREFIX/icon.svg" 2>/dev/null || true
cat > "$HOME/.local/share/applications/kenanja.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Kenanja
Comment=Chord-Sheets, Leadsheets, Streicher, Chorblatt und Übe-Audio aus einem MP3
Exec=$BIN/kenanja
Icon=$PREFIX/icon.svg
Terminal=false
Categories=AudioVideo;Audio;Music;
EOF

echo "== Modelle laden und prüfen =="
"$BIN/kenanja" pruefen || true

case ":$PATH:" in *":$BIN:"*) ;; *) echo; echo "Hinweis: $BIN ist nicht im PATH – einmal neu anmelden oder 'export PATH=\$HOME/.local/bin:\$PATH'";; esac
echo
echo "Fertig. Start: im Startmenü 'Kenanja' oder im Terminal: kenanja"
