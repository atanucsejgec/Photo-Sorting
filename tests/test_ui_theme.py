"""The dark theme, the bundled icons and literal labels."""

import re
from pathlib import Path

from PySide6.QtGui import QColor, QImage, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QComboBox, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QListWidget, QPushButton, QRadioButton, QSpinBox, QStyle,
    QStyleOptionSpinBox, QWidget,
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
    assert all(re.fullmatch(r"#[0-9a-f]{6}", v) for v in c.values())  # QColor-ready


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


# ----- controls under the theme ----------------------------------------------------

def _near(colour: QColor, token: str, tolerance: int = 40) -> bool:
    want = QColor(theme.COLORS[token])
    return (colour.alpha() > 200 and abs(colour.red() - want.red()) <= tolerance
            and abs(colour.green() - want.green()) <= tolerance
            and abs(colour.blue() - want.blue()) <= tolerance)


def _pixels(image: QImage, rect, token: str) -> int:
    """How many pixels in rect are about the colour of token."""
    return sum(_near(image.pixelColor(x, y), token)
               for x in range(max(rect.left(), 0), min(rect.right() + 1, image.width()))
               for y in range(max(rect.top(), 0), min(rect.bottom() + 1, image.height())))


def _show_focused(qtbot, widget: QWidget) -> QWidget:
    """widget in a shown window, focused, beside a button (so focus can leave)."""
    window = QWidget()
    layout = QHBoxLayout(window)
    layout.addWidget(widget)
    window.other = QPushButton("Other")
    layout.addWidget(window.other)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    window.activateWindow()
    widget.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is widget)
    return window


class _Spin(QSpinBox):
    def part(self, control: QStyle.SubControl):
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        return self.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option,
                                           control, self)


def test_spin_box_arrows_are_drawn(qtbot):
    spin = _Spin()
    spin.setRange(1, 99)
    spin.setValue(50)  # both arrows live
    window = _show_focused(qtbot, spin)  # noqa: F841 (kept alive)
    image = spin.grab().toImage()
    for control in (QStyle.SubControl.SC_SpinBoxUp, QStyle.SubControl.SC_SpinBoxDown):
        rect = spin.part(control)
        assert not rect.isEmpty(), control
        assert _pixels(image, rect, "text") >= 4, control  # an arrow, not an empty box


def test_focus_rings_are_two_pixels_all_round(qtbot):
    combo = QComboBox()
    combo.addItems(["Food", "Pets"])
    spin = QSpinBox()
    for widget in (combo, spin, QCheckBox("Include subfolders"),
                   QRadioButton("Date"), QListWidget()):
        window = _show_focused(qtbot, widget)  # noqa: F841 (kept alive)
        image = widget.grab().toImage()
        mid = image.height() // 2
        for x in (0, 1, image.width() - 2, image.width() - 1):  # left and right sides
            assert _near(image.pixelColor(x, mid), "focus"), (type(widget).__name__, x)


def test_focus_does_not_move_anything(qtbot):
    for widget in (QCheckBox("Include subfolders"), QRadioButton("Date"), QListWidget()):
        def layout_of(w=widget):
            inner = w.viewport().geometry() if isinstance(w, QListWidget) else None
            return w.sizeHint(), w.contentsRect(), inner

        window = _show_focused(qtbot, widget)
        focused = layout_of()
        window.other.setFocus()
        qtbot.waitUntil(lambda: QApplication.focusWidget() is window.other)
        assert layout_of() == focused, type(widget).__name__


def test_combo_box_arrow_is_drawn(qtbot):
    combo = QComboBox()
    combo.addItems(["Food", "Pets"])
    window = _show_focused(qtbot, combo)  # noqa: F841 (kept alive)
    image = combo.grab().toImage()
    right = combo.rect().adjusted(combo.width() - 24, 2, -2, -2)
    assert _pixels(image, right, "text") >= 4


def test_every_button_and_buddy_label_shows_its_ampersands(qtbot, tmp_path):
    from photosort.people import PeopleStore
    from photosort.settings import Settings
    from photosort.ui.category_editor import CategoryEditor
    from photosort.ui.main_window import MainWindow

    store = PeopleStore(tmp_path / "people")
    window = MainWindow(Settings(), tmp_path / "settings.json", people_store=store)
    window.history_page.load(tmp_path)
    # The window holds Home, History, People and Settings as pages.
    shown = [window, CategoryEditor(["Food & Drink", "Pets"], window)]
    for widget in shown:
        qtbot.addWidget(widget)
    texts = [button.text() for widget in shown for button in widget.findChildren(QAbstractButton)]
    texts += [label.text() for widget in shown for label in widget.findChildren(QLabel)
              if label.buddy() is not None]
    assert "Confirm && next group" in texts
    assert [t for t in texts if "&" in t.replace("&&", "")] == []


def test_use_min_font_raises_a_small_font_only(qapp):
    font = qapp.font()
    try:
        for size, expected in ((9, theme.MIN_POINT_SIZE), (14, 14)):
            small = qapp.font()
            small.setPointSizeF(size)
            qapp.setFont(small)
            theme.use_min_font(qapp)
            assert qapp.font().pointSizeF() == expected
    finally:
        qapp.setFont(font)
