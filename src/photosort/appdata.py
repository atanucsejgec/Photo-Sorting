"""Where the app keeps its own data: one folder per user, never in the owner's
photo folders. People, undo records ("runs") and, later, the scan cache all
live here. Nothing is created until something is written.
"""

from pathlib import Path

import platformdirs


def data_dir() -> Path:
    """The app's data folder (not created)."""
    return Path(platformdirs.user_data_dir("photo-sorter"))


def runs_dir() -> Path:
    """The folder holding every run's undo record (not created)."""
    return data_dir() / "runs"
