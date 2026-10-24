#!/usr/bin/env bash
# Add Photo Sorter to the app menu: writes ~/.local/share/applications/photo-sorter.desktop.
set -euo pipefail

launcher="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/photo-sorter.sh"
case "$launcher" in
    *[\"\`\$\\%]*)
        echo "Can't install: the path $launcher contains \" \` \$ \\ or %." >&2
        exit 1
        ;;
esac

apps="$HOME/.local/share/applications"
mkdir -p "$apps"
# Exec is quoted because the path may contain spaces.
cat > "$apps/photo-sorter.desktop" <<ENTRY
[Desktop Entry]
Type=Application
Name=Photo Sorter
Comment=Sort photos and videos into folders
Exec="$launcher"
Icon=image-x-generic
Terminal=false
Categories=Graphics;
StartupWMClass=photo-sorter
ENTRY

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$apps" >/dev/null 2>&1 || true
fi
echo "Installed $apps/photo-sorter.desktop"
