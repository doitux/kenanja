#!/usr/bin/env bash
# Kenanja installieren (Kubuntu/Ubuntu/Debian). Aufruf im entpackten/geklonten Ordner: bash install.sh
set -e
cd "$(dirname "$0")"
PREFIX="$HOME/.local/share/kenanja"
BIN="$HOME/.local/bin"

echo "== Systempakete (sudo) =="
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip ffmpeg rubberband-cli libreoffice-writer fonts-liberation fonts-dejavu-core \
  libxcb-cursor0 >/dev/null   # libxcb-cursor0: für das eigene Kenanja-Fenster (Qt)

echo "== Python-Umgebung =="
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip wheel
# fenster = eigenes Programmfenster (Qt WebEngine, ca. 700 MB); whisper = Spracherkennung für die Textzuordnung
"$PREFIX/venv/bin/pip" install -q ".[whisper,fenster]" || "$PREFIX/venv/bin/pip" install -q ".[fenster]" \
  || "$PREFIX/venv/bin/pip" install -q .

mkdir -p "$BIN"
ln -sf "$PREFIX/venv/bin/kenanja" "$BIN/kenanja"

echo "== Startmenü-Eintrag =="
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$HOME/.local/share/applications" "$ICONS"
cp kenanja/web/static/icon.svg "$ICONS/kenanja.svg"
cat > "$HOME/.local/share/applications/kenanja.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Kenanja
Comment=Chord-Sheets, Leadsheets, Streicher, Chorblatt und Übe-Audio aus einem MP3
Exec=$BIN/kenanja
Icon=kenanja
Terminal=false
Categories=AudioVideo;Audio;Music;
StartupWMClass=kenanja
Actions=browser;

[Desktop Action browser]
Name=Im Browser öffnen
Exec=$BIN/kenanja web
EOF

echo "== Modelle laden und prüfen =="
"$BIN/kenanja" pruefen || true

case ":$PATH:" in *":$BIN:"*) ;; *) echo; echo "Hinweis: $BIN ist nicht im PATH – einmal neu anmelden oder 'export PATH=\$HOME/.local/bin:\$PATH'";; esac
echo
echo "Fertig. Start: im Startmenü 'Kenanja' oder im Terminal: kenanja   (im Browser: kenanja web)"
