#!/usr/bin/env bash
# Kenanja wieder entfernen. Deine Songs/Projekte (~/Kenanja) bleiben, außer du rufst: bash uninstall.sh --alles
set -e
rm -rf "$HOME/.local/share/kenanja"                       # Programm + Python-Umgebung
rm -f  "$HOME/.local/bin/kenanja"                         # Startbefehl
rm -f  "$HOME/.local/share/applications/kenanja.desktop"  # Startmenü-Eintrag
rm -rf "$HOME/.cache/kenanja"                             # Modelle + zwischengespeicherte Analysen
rm -rf "$HOME/.config/kenanja"                            # Stimmprofile
if [ "$1" = "--alles" ]; then
  rm -rf "$HOME/Kenanja"                                  # Projekte und erzeugte Dateien
  echo "Kenanja inklusive aller Projekte entfernt."
else
  echo "Kenanja entfernt. Deine Projekte liegen weiter in ~/Kenanja."
fi
echo "Die Systempakete (ffmpeg, rubberband-cli, libreoffice-writer) bleiben, weil andere Programme sie nutzen können."
