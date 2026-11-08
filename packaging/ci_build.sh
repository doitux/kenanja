#!/bin/bash
# Baut die AppImage in einem Ubuntu-22.04-Container (glibc 2.35 → läuft auf 22.04 und allem Neueren).
# Wird von den GitHub-Workflows benutzt; lokal:  docker run --rm -v "$PWD:/src" -w /src ubuntu:22.04 packaging/ci_build.sh
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
apt-get update
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip python3-dev libpython3.10 binutils file curl ca-certificates git \
  ffmpeg rubberband-cli xvfb xauth fonts-dejavu-core libsndfile1 \
  libgl1 libegl1 libopengl0 libglib2.0-0 libfontconfig1 libfreetype6 libdbus-1-3 libnss3 libnspr4 libasound2 \
  libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
  libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libxcb-xfixes0 libxcb-sync1 libxcb-shm0 \
  libxcomposite1 libxdamage1 libxrandr2 libxtst6 libxkbfile1 libxshmfence1 libgbm1 libdrm2 libxss1 \
  libwayland-client0 libwayland-cursor0 libwayland-egl1 libcups2 libpulse0 libatomic1
python3 -m venv /opt/build-venv
/opt/build-venv/bin/pip install --upgrade pip
/opt/build-venv/bin/pip install -r "$ROOT/packaging/requirements-build.txt"
TOOLS=/opt/appimage-tools
mkdir -p "$TOOLS"
curl -fsSL -o "$TOOLS/appimagetool" \
  https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
curl -fsSL -o "$TOOLS/runtime-x86_64" \
  https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64
chmod +x "$TOOLS/appimagetool"
PY=/opt/build-venv/bin/python TOOLS="$TOOLS" OUT="$ROOT/dist" "$ROOT/packaging/build_appimage.sh"
cd "$ROOT/dist" && sha256sum Kenanja-*-x86_64.AppImage > SHA256SUMS && cat SHA256SUMS
