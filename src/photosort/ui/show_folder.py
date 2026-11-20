"""Show a file in the file manager, selected in its folder (Preview's E).

The desktop's file manager is asked over D-Bus (the freedesktop
FileManager1 ShowItems call, which Nautilus, Dolphin, Nemo, Thunar and
others answer) through `gdbus`, in a QProcess: the page never waits for it.
If that can't be done (no gdbus, no file manager answering, an error), the
file's folder is opened with the desktop's default app instead. Nothing is
ever moved or changed.
"""

import logging
import os
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices

_log = logging.getLogger("photosort")

_running: set[QProcess] = set()  # kept alive until each one ends


def show_item_command(path: Path) -> list[str]:
    """The gdbus command asking the file manager to show path, selected.

    The URI is fully %-encoded from the path's bytes, so a quote, bracket or
    a name that is not UTF-8 can neither break the GVariant text nor be lost.
    """
    uri = "file://" + urllib.parse.quote(os.fsencode(os.path.abspath(path)), safe="/")
    return ["gdbus", "call", "--session",
            "--dest", "org.freedesktop.FileManager1",
            "--object-path", "/org/freedesktop/FileManager1",
            "--method", "org.freedesktop.FileManager1.ShowItems",
            f"['{uri}']", ""]


def _open_folder(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))


def show_in_folder(path: Path) -> None:
    """Show path selected in the file manager; else open its folder."""
    command = show_item_command(path)
    process = QProcess()
    process.setProgram(command[0])
    process.setArguments(command[1:])
    process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
    _running.add(process)
    done = False

    def finish(failed: bool, why: str = "") -> None:
        nonlocal done
        if done:
            return
        done = True
        _running.discard(process)
        process.deleteLater()
        if failed:
            _log.info("Show in folder: the file manager didn't answer (%s); "
                      "opening the folder", why)
            _open_folder(path)

    process.errorOccurred.connect(
        lambda error: finish(True, error.name)
        if error == QProcess.ProcessError.FailedToStart else None)
    process.finished.connect(
        lambda code, status: finish(code != 0 or status != QProcess.ExitStatus.NormalExit,
                                    f"exit {code}"))
    process.start()
