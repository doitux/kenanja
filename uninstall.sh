#!/usr/bin/env bash
# Kenanja wieder entfernen. Deine Songs/Projekte (~/Kenanja) bleiben, außer du rufst: bash uninstall.sh --alles
set -e
CFG="${XDG_CONFIG_HOME:-$HOME/.config}/kenanja/einstellungen.json"
PROJ=""
[ -f "$CFG" ] && PROJ=$(python3 -c "import json;print(json.load(open('$CFG')).get('projektordner',''))" 2>/dev/null || true)
rm -rf "$HOME/.local/share/kenanja"                       # Programm + Python-Umgebung
rm -f  "$HOME/.local/bin/kenanja"                         # Startbefehl
rm -f  "$HOME/.local/share/applications/kenanja.desktop"  # Startmenü-Eintrag
rm -rf "$HOME/.cache/kenanja"                             # Modelle + zwischengespeicherte Analysen
rm -rf "$HOME/.config/kenanja"                            # Stimmprofile
if [ "$1" = "--alles" ]; then
  rm -rf "$HOME/Kenanja"                                  # Standardordner für Projekte und Ausgaben
  [ -n "$PROJ" ] && rm -rf "$PROJ"                        # selbst gewählter Projektordner
  echo "Kenanja inklusive aller Projekte entfernt (fertige Blätter in deinem Ausgabeordner bleiben)."
else
  echo "Kenanja entfernt. Deine Projekte liegen weiter in ${PROJ:-~/Kenanja/Projekte}."
fi
echo "Die Systempakete (ffmpeg, rubberband-cli, libreoffice-writer) bleiben, weil andere Programme sie nutzen können."
