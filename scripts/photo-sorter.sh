#!/usr/bin/env bash
# Start Photo Sorter from the repo this script lives in (also through a symlink).
set -euo pipefail

repo="$(dirname "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")")"

# Desktop launchers often start apps without ~/.local/bin on PATH.
uv="$(command -v uv || true)"
if [ -z "$uv" ]; then
    uv="$HOME/.local/bin/uv"
fi

args=(run --project "$repo")
# Without an NVIDIA GPU the ~2 GB of CUDA libraries are no use: skip them.
if ! command -v nvidia-smi >/dev/null 2>&1; then
    args+=(--no-group gpu)
fi

exec "$uv" "${args[@]}" photo-sorter "$@"
