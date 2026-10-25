"""The dark theme, the bundled icons and literal labels."""

import re
from pathlib import Path

from PySide6.QtGui import QColor, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect, QHBoxLayout, QPushButton, QRadioButton, QWidget,
)

from photosort.modes import MODES
from photosort.ui import icons, theme
from photosort.ui.labels import literal

UI = Path(theme.__file__).parent


def test_apply_sets_the_dark_palette_and_the_stylesheet(qapp):
    theme.apply(qapp)
    palette = qapp.palette()
    assert palette.color(QPalette.ColorRole.Window) == QColor(theme.COLORS["bg"])
    assert palette.color(QPalette.ColorRole.WindowText) == QColor(theme.COLORS["text"])
    assert qapp.styleSheet() == theme.stylesheet()


def test_the_tokens_are_the_spec_palette():
    c = theme.COLORS
    assert {k: c[k] for k in ("bg", "panel", "border", "text", "muted", "accent_from",
                              "accent_to", "focus")} == {
        "bg": "#0d1117", "panel": "#161b22", "border": "#30363d", "text": "#e6edf3",
        "muted": "#8b949e", "accent_from": "#06b6d4", "accent_to": "#8b5cf6",
        "focus": "#22d3ee"}
    assert "danger" in c


def test_the_stylesheet_targets_the_object_names():
    qss = theme.stylesheet()
    for name in ("primaryButton", "modeTile", "sidebar", "shortcutStrip", "card", "muted"):
        assert f"#{name}" in qss, name


def test_no_colour_literals_outside_the_theme():
    literal_colour = re.compile(r"""["'][^"'\n]*(#[0-9a-fA-F]{3,8}\b|rgba?\()""")
    found = [f"{path.name}:{n}"
             for path in UI.rglob("*.py") if path.name != "theme.py"
             for n, line in enumerate(path.read_text().splitlines(), 1)
             if literal_colour.search(line)]
    assert found == []


def test_focus_glows_and_the_glow_leaves_with_it(qtbot, qapp):
    theme.apply(qapp)
    window = QWidget()
    layout = QHBoxLayout(window)
    first, second = QPushButton("One"), QPushButton("Two")
    layout.addWidget(first)
    layout.addWidget(second)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    window.activateWindow()
    first.setFocus()
    qtbot.waitUntil(lambda: isinstance(first.graphicsEffect(), QGraphicsDropShadowEffect))
    second.setFocus()
    qtbot.waitUntil(lambda: isinstance(second.graphicsEffect(), QGraphicsDropShadowEffect))
    assert first.graphicsEffect() is None


def test_every_mode_has_an_icon(qapp):
    assert set(icons.MODE_ICONS) == {key for key, _, _ in MODES}
    for key, name in icons.MODE_ICONS.items():
        icon = icons.icon(name)
        assert not icon.isNull(), key
        image = icon.pixmap(24, 24).toImage()
        assert any(image.pixelColor(x, y).alpha() > 0
                   for x in range(24) for y in range(24)), key  # something is drawn


def test_icons_are_drawn_in_the_text_colour(qapp):
    image = icons.icon("layout-grid").pixmap(48, 48).toImage()
    drawn = {image.pixelColor(x, y).name() for x in range(48) for y in range(48)
             if image.pixelColor(x, y).alpha() == 255}
    assert drawn == {theme.COLORS["text"]}


def test_the_icons_ship_with_their_licence():
    folder = UI / "icons"
    assert "ISC License" in (folder / "LICENSE").read_text()
    for name in icons.MODE_ICONS.values():
        assert (folder / f"{name}.svg").is_file()


def test_literal_shows_an_ampersand_on_buttons(qtbot):
    assert not QKeySequence.mnemonic("Notes & Pages").isEmpty()  # the bug, unescaped
    for button in (QRadioButton(literal("Notes & Pages")), QPushButton(literal("Notes & Pages"))):
        qtbot.addWidget(button)
        assert button.text().replace("&&", "&") == "Notes & Pages"
        assert QKeySequence.mnemonic(button.text()).isEmpty()


def test_no_message_box_left_in_the_ui():
    found = [path.name for path in UI.rglob("*.py") if "QMessageBox" in path.read_text()]
    assert found == []
