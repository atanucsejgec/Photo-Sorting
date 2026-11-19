"""What a folder holds, for the tests that check a run and its Undo leave
everything as it was; and old-layout undo records, for the migration tests."""

import json
import os
from pathlib import Path


def tree(root) -> dict[str, bytes | str]:
    """Everything under root, links as links (never followed): path -> bytes,
    "dir" or "-> target". The app's own records (.photosort) are left out."""
    found: dict[str, bytes | str] = {}
    for folder, dirs, names in os.walk(root):
        for name in dirs + names:
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, root)
            if ".photosort" in rel.split(os.sep):
                continue
            if os.path.islink(path):
                found[rel] = "-> " + os.readlink(path)
            elif os.path.isdir(path):
                found[rel] = "dir"
            else:
                with open(path, "rb") as f:
                    found[rel] = f.read()
    return found


def old_layout_run(root, moves, run_id="20250101-120000-date",
                   started="2025-01-01T12:00:00+05:30", mode="date"):
    """Do moves (src, dst: POSIX paths relative to root) for real, and record
    them as the app did before undo records moved to one central folder:
    <root>/.photosort/runs/<run_id>.json and .jsonl, written by hand, with no
    "root" in the metadata. The folders the moves needed are made first and
    recorded as made. Return the old runs folder."""
    runs = os.path.join(root, ".photosort", "runs")
    os.makedirs(runs, exist_ok=True)
    lines = []
    for _, dst in moves:
        parts = dst.split("/")[:-1]
        for n in range(1, len(parts) + 1):
            rel = "/".join(parts[:n])
            if not os.path.isdir(os.path.join(root, rel)):
                os.mkdir(os.path.join(root, rel))
                lines.append({"op": "mkdir", "path": rel})
    for i, (src, dst) in enumerate(moves):
        lines.append({"op": "move", "i": i, "src": src, "dst": dst})
        os.rename(os.path.join(root, src), os.path.join(root, dst))
        lines.append({"op": "done", "i": i})
    with open(os.path.join(runs, f"{run_id}.jsonl"), "wb") as f:
        f.write(b"".join(json.dumps(e).encode("ascii") + b"\n" for e in lines))
    meta = {"run_id": run_id, "mode": mode, "started": started, "finished": started,
            "status": "done", "include_subfolders": False, "planned": len(moves),
            "moved": len(moves), "undone": False}
    with open(os.path.join(runs, f"{run_id}.json"), "wb") as f:
        f.write(json.dumps(meta, indent=1).encode("ascii"))
    return Path(runs)
