"""show_folder: the file manager is asked to show a file selected, else its folder opens."""

import os
from pathlib import Path

import pytest
from PySide6.QtGui import QDesktopServices

from photosort.ui import show_folder


@pytest.fixture
def opened(monkeypatch):
    urls: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        lambda url: urls.append(url.toLocalFile()) or True)
    return urls


def test_the_command_asks_the_file_manager_to_show_the_item(tmp_path):
    path = tmp_path / "it's a \"photo\" [1].jpg"
    command = show_folder.show_item_command(path)
    assert command[:2] == ["gdbus", "call"]
    assert "org.freedesktop.FileManager1.ShowItems" in command
    item = command[-2]
    # A GVariant string array: the URI is fully %-encoded, so no quote or bracket breaks it.
    assert item.startswith("['file://") and item.endswith("']")
    uri = item[2:-2]
    assert set(uri[len("file://"):]) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~/%")
    assert command[-1] == ""
    assert command[command.index("--timeout") + 1] == "5"


def test_a_name_that_is_not_utf8_keeps_its_bytes(tmp_path):
    path = Path(os.fsdecode(bytes(tmp_path) + b"/caf\xe9.jpg"))
    uri = show_folder.show_item_command(path)[-2][2:-2]
    assert uri.endswith("/caf%E9.jpg")


def test_the_folder_opens_when_the_file_manager_cannot(qtbot, tmp_path, monkeypatch, opened):
    (tmp_path / "a.jpg").write_bytes(b"x")
    monkeypatch.setattr(show_folder, "show_item_command",
                        lambda path: ["photosort-no-such-program"])
    show_folder.show_in_folder(tmp_path / "a.jpg")
    qtbot.waitUntil(lambda: opened == [str(tmp_path)], timeout=5000)


def test_the_folder_opens_when_the_call_fails(qtbot, tmp_path, monkeypatch, opened):
    (tmp_path / "a.jpg").write_bytes(b"x")
    monkeypatch.setattr(show_folder, "show_item_command", lambda path: ["false"])
    show_folder.show_in_folder(tmp_path / "a.jpg")
    qtbot.waitUntil(lambda: opened == [str(tmp_path)], timeout=5000)


def test_nothing_else_opens_when_the_call_works(qtbot, tmp_path, monkeypatch, opened):
    (tmp_path / "a.jpg").write_bytes(b"x")
    monkeypatch.setattr(show_folder, "show_item_command", lambda path: ["true"])
    show_folder.show_in_folder(tmp_path / "a.jpg")
    qtbot.waitUntil(lambda: not show_folder._running, timeout=5000)
    assert opened == []


def test_the_folder_that_opens_keeps_a_name_that_is_not_utf8(qtbot, tmp_path, monkeypatch):
    folder = Path(os.fsdecode(bytes(tmp_path) + b"/caf\xe9"))
    try:
        folder.mkdir()
    except OSError:
        pytest.skip("this filesystem refuses names that are not UTF-8")
    urls = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: urls.append(url) or True)
    monkeypatch.setattr(show_folder, "show_item_command", lambda path: ["false"])
    show_folder.show_in_folder(folder / "a.jpg")
    qtbot.waitUntil(lambda: len(urls) == 1, timeout=5000)
    assert bytes(urls[0].toEncoded()).endswith(b"/caf%E9")
