#!/usr/bin/env bash
# Start Photo Sorter from the repo this script lives in (also through a symlink).
set -euo pipefail

repo="$(dirname "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")")"

# Desktop launchers often start apps without ~/.local/bin on PATH.
uv="$(command -v uv || true)"
if [ -z "$uv" ]; then
    uv="$HOME/.local/bin/uv"
fi

exec "$uv" run --project "$repo" photo-sorter "$@"
