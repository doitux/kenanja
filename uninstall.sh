#!/usr/bin/env bash
# Kenanja wieder entfernen. Deine Songs/Projekte bleiben, außer du rufst: bash uninstall.sh --alles
set -e
CFG="${XDG_CONFIG_HOME:-$HOME/.config}/kenanja/einstellungen.json"
PROJ=""
[ -f "$CFG" ] && PROJ=$(python3 -c "import json,os;print(os.path.expanduser(json.load(open('$CFG')).get('projektordner','')))" 2>/dev/null || true)
[ -z "$PROJ" ] && PROJ="$HOME/Projekte/Kenanja"
rm -rf "$HOME/.local/share/kenanja"                                  # Programm + Python-Umgebung (inkl. Qt)
rm -f  "$HOME/.local/bin/kenanja"                                    # Startbefehl
rm -f  "$HOME/.local/share/applications/kenanja.desktop"             # Startmenü-Eintrag
rm -f  "$HOME/.local/share/icons/hicolor/scalable/apps/kenanja.svg"  # Symbol
rm -rf "$HOME/.cache/kenanja" "$HOME/.cache/Kenanja"                 # Modelle, Grafik-Zwischenspeicher des Fensters
rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/kenanja"                   # Einstellungen, Stimmprofile, Fenstergröße
if [ "$1" = "--alles" ]; then
  # nur echte Kenanja-Projekte löschen (Ordner mit projekt.json), nie den ganzen Ordner blind
  if [ -d "$PROJ" ]; then
    for d in "$PROJ"/*/; do [ -f "$d/projekt.json" ] && rm -rf "$d"; done
    rmdir "$PROJ" 2>/dev/null || true
    rmdir "$(dirname "$PROJ")" 2>/dev/null || true                   # ~/Projekte, falls jetzt leer
  fi
  rm -rf "$HOME/Kenanja"                                             # Ordner der ersten Version
  echo "Kenanja inklusive aller Projekte entfernt (Kopien in deinem Band-Ordner bleiben)."
else
  echo "Kenanja entfernt. Deine Projekte liegen weiter in $PROJ."
fi
echo "Die Systempakete (ffmpeg, rubberband-cli, libreoffice-writer, libxcb-cursor0) bleiben, weil andere Programme sie nutzen können."
