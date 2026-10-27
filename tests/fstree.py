"""What a folder holds, for the tests that check a run and its Undo leave
everything as it was."""

import os


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
