#!/usr/bin/env bash
# Legt die neueste Testversion in die Vorabversion „nightly“ (wie bei Pulsecut): Tag „nightly“ zeigt auf den
# gebauten Stand, die AppImage trägt Build-Nummer und Commit im Namen, ältere Nightly-AppImages werden entfernt.
set -euo pipefail
repo="$GITHUB_REPOSITORY"; tag="nightly"; app="$1"; sums="$2"; name="$(basename "$app")"
if ! gh release view "$tag" --repo "$repo" > /dev/null 2>&1; then
    gh release create "$tag" --repo "$repo" --prerelease --target "$GITHUB_SHA" \
        --title "Testversion" --notes "Testversion des neuesten Stands." || true
fi
gh api -X PATCH "repos/$repo/git/refs/tags/$tag" -f sha="$GITHUB_SHA" -F force=true > /dev/null
for f in "$app" "$sums"; do
    for attempt in 1 2 3; do
        if gh release upload "$tag" "$f" --repo "$repo" --clobber; then break; fi
        if [ "$attempt" -eq 3 ]; then exit 1; fi
        sleep 10
    done
done
gh release view "$tag" --repo "$repo" --json assets -q '.assets[].name' | while read -r asset; do
    if [[ "$asset" == Kenanja-nightly-*-x86_64.AppImage && "$asset" != "$name" ]]; then
        gh release delete-asset "$tag" "$asset" --repo "$repo" -y && echo "entfernt: $asset"
    fi
done
build="${name#Kenanja-}"; build="${build%-x86_64.AppImage}"
gh release edit "$tag" --repo "$repo" --prerelease --title "Testversion ${build#nightly-}" --notes "$(cat <<NOTES
Automatisch gebaute Testversion vom neuesten Stand auf \`main\` (Commit ${GITHUB_SHA::7}, $(date -u '+%Y-%m-%d %H:%M UTC')), nachdem alle Tests durchgelaufen sind.

**Linux-AppImage** (x86-64, Ubuntu/Kubuntu 22.04 und neuer):

\`\`\`bash
sudo apt install ffmpeg rubberband-cli libreoffice-writer fonts-liberation
chmod +x $name && ./$name
\`\`\`

Beim ersten Start legt Kenanja einen Startmenü-Eintrag an. Entfernen: \`./$name deinstallieren\`.

\`\`\`
$(cat "$sums")
\`\`\`
NOTES
)"
echo "veröffentlicht: $name"
