"""The app's one look: the colour tokens, the stylesheet and apply(app).

Every colour the UI uses is a token in COLORS; no other module writes a
colour literal (a test checks). Widgets opt in to the special looks by
object name:

- primaryButton: the accent-gradient button (Go, the default in a dialog);
- modeTile, card: a raised panel (give the widget Qt.WA_StyledBackground,
  as styled() does, so a plain QWidget paints it);
- sidebar: the left column;
- shortcutStrip: the key strip along the bottom (see shortcuts.ShortcutStrip);
- muted: quieter text; error: a problem, in the danger colour;
- thumb: the grey box a thumbnail or photo shows in while it loads;
- viewer, viewerOverlay: the full-screen viewer and its caption.

The control with keyboard focus gets a 2 px focus ring from the stylesheet
and, when it is a solid control (a button, a field, a tile, a card), a soft
glow (a drop shadow in the focus colour) that follows focus around.
"""

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QComboBox, QGraphicsDropShadowEffect, QLineEdit,
    QPushButton, QWidget,
)
from shiboken6 import isValid

COLORS: dict[str, str] = {
    "bg": "#0d1117",  # the window
    "panel": "#161b22",  # tiles, cards, fields, lists
    "deep": "#0a0e14",  # the sidebar and the full-screen viewer, darker than bg
    "raised": "#21262d",  # buttons, key chips, progress tracks
    "border": "#30363d",
    "border_soft": "#262c36",  # around tiles and cards
    "border_hover": "#484f58",
    "line": "#1f2630",  # separators: the strip's top, the sidebar's edge
    "text": "#e6edf3",
    "chip_text": "#c9d1d9",  # the key names in the strip
    "muted": "#8b949e",
    "disabled": "#6e7681",
    "accent_from": "#06b6d4",  # the accent gradient (primary button, progress)
    "accent_to": "#8b5cf6",
    "brand_from": "#22d3ee",  # the brand word's gradient
    "brand_to": "#a78bfa",
    "on_accent": "#ffffff",  # text on the accent gradient
    "focus": "#22d3ee",  # the focus ring and glow
    "selection": "#12384a",  # a selected row: a dark cyan
    "danger": "#f85149",
    "thumb_bg": "#1b2230",  # a thumbnail's box while it loads
    "overlay": "rgba(10, 14, 20, 190)",  # the viewer's caption box (stylesheet only)
    "face_box": "#ffd400",  # the box drawn round a face on a photo
}

GLOW_BLUR_PX = 18
GLOW_ALPHA = 150  # of 255: soft, as in the mockup


def stylesheet() -> str:
    """The app-wide stylesheet, built from COLORS."""
    c = COLORS
    gradient = (f"qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                f"stop:0 {c['accent_from']}, stop:1 {c['accent_to']})")
    return f"""
QMainWindow, QDialog {{ background: {c['bg']}; }}
QToolTip {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']};
    padding: 4px 6px; }}
QLabel#muted {{ color: {c['muted']}; }}
QLabel#error {{ color: {c['danger']}; }}
QLabel#thumb {{ background: {c['thumb_bg']}; color: {c['muted']}; }}

QPushButton {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['border']};
    border-radius: 8px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {c['border_hover']}; }}
QPushButton:pressed, QPushButton:checked {{ background: {c['border']}; }}
QPushButton:focus {{ border: 2px solid {c['focus']}; padding: 6px 13px; }}
QPushButton:disabled {{ background: {c['panel']}; color: {c['disabled']};
    border-color: {c['border_soft']}; }}
QPushButton#primaryButton {{ background: {gradient}; color: {c['on_accent']};
    font-weight: bold; border: 1px solid transparent; }}
QPushButton#primaryButton:focus {{ border: 2px solid {c['focus']}; }}
QPushButton#primaryButton:disabled {{ background: {c['raised']}; color: {c['disabled']};
    border-color: {c['border_soft']}; }}

QLineEdit, QAbstractSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']};
    border-radius: 8px; padding: 6px 8px;
    selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus,
QTextEdit:focus {{ border: 2px solid {c['focus']}; padding: 5px 7px; }}
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {{
    color: {c['disabled']}; border-color: {c['border_soft']}; }}
QComboBox QAbstractItemView {{ background: {c['panel']}; color: {c['text']};
    border: 1px solid {c['border']}; selection-background-color: {c['selection']}; }}

QCheckBox, QRadioButton {{ color: {c['text']}; spacing: 8px; padding: 2px;
    border: 1px solid transparent; border-radius: 6px; }}
QCheckBox:focus, QRadioButton:focus {{ border-color: {c['focus']}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {c['disabled']}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px;
    background: {c['panel']}; border: 1px solid {c['border']}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {c['focus']}; border-color: {c['focus']}; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {c['border_hover']}; }}

QListView, QTreeView, QTableView {{ background: {c['panel']}; color: {c['text']};
    alternate-background-color: {c['bg']}; gridline-color: {c['border_soft']};
    border: 1px solid {c['border_soft']}; border-radius: 8px;
    selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
QListView:focus, QTreeView:focus, QTableView:focus {{ border: 1px solid {c['focus']}; }}
QHeaderView::section {{ background: {c['panel']}; color: {c['muted']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 6px; }}
QTableCornerButton::section {{ background: {c['panel']}; border: none; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {c['border']}; border-radius: 5px; min-height: 24px;
    min-width: 24px; }}
QScrollBar::handle:hover {{ background: {c['border_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QProgressBar {{ background: {c['raised']}; border: none; border-radius: 4px;
    max-height: 8px; color: transparent; }}
QProgressBar::chunk {{ background: {gradient}; border-radius: 4px; }}

QWidget#modeTile, QWidget#card {{ background: {c['panel']};
    border: 1px solid {c['border_soft']}; border-radius: 9px; }}
QWidget#modeTile:hover, QWidget#card:hover {{ border-color: {c['border']}; }}
QWidget#modeTile:focus, QWidget#card:focus {{ border: 2px solid {c['focus']}; }}
QWidget#sidebar {{ background: {c['deep']}; border-right: 1px solid {c['line']}; }}
QWidget#shortcutStrip {{ border-top: 1px solid {c['line']}; }}
QLabel#keyChip {{ background: {c['raised']}; color: {c['chip_text']};
    border: 1px solid {c['border']}; border-radius: 4px; padding: 0 5px; }}
QLabel#keyWord {{ color: {c['muted']}; }}

QDialog#viewer {{ background: {c['deep']}; }}
QDialog#viewer QLabel, QDialog#viewer QCheckBox {{ color: {c['text']}; }}
QLabel#viewerOverlay {{ background: {c['overlay']}; color: {c['text']};
    padding: 6px 10px; }}
"""


def palette() -> QPalette:
    """The dark palette, for what the stylesheet does not reach."""
    c = {k: QColor(v) for k, v in COLORS.items() if v.startswith("#")}
    p = QPalette()
    roles = QPalette.ColorRole
    for role, token in ((roles.Window, "bg"), (roles.WindowText, "text"),
                        (roles.Base, "panel"), (roles.AlternateBase, "bg"),
                        (roles.Text, "text"), (roles.Button, "raised"),
                        (roles.ButtonText, "text"), (roles.BrightText, "on_accent"),
                        (roles.Highlight, "selection"), (roles.HighlightedText, "text"),
                        (roles.ToolTipBase, "panel"), (roles.ToolTipText, "text"),
                        (roles.PlaceholderText, "muted"), (roles.Link, "focus"),
                        (roles.LinkVisited, "brand_to"), (roles.Light, "border_hover"),
                        (roles.Midlight, "border"), (roles.Mid, "border_soft"),
                        (roles.Dark, "deep"), (roles.Shadow, "deep")):
        p.setColor(role, c[token])
    disabled = QPalette.ColorGroup.Disabled
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        p.setColor(disabled, role, c["disabled"])
    return p


def styled(widget: QWidget, name: str) -> QWidget:
    """Give widget the look named name (e.g. "card"); return it.

    WA_StyledBackground lets a plain QWidget paint the stylesheet's background.
    """
    widget.setObjectName(name)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    return widget


class _FocusGlow(QObject):
    """Puts a soft glow round the focused control, and takes it off again."""

    _GLOWS = (QPushButton, QLineEdit, QAbstractSpinBox, QComboBox)
    _GLOWING_NAMES = ("modeTile", "card")

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._glowing: QWidget | None = None
        self._effect: QGraphicsDropShadowEffect | None = None
        app.focusChanged.connect(self._moved)

    def _wants_glow(self, widget: QWidget) -> bool:
        if isinstance(widget, QLineEdit):
            return widget.hasFrame()  # a frameless one is part of something else
        if isinstance(widget, self._GLOWS):
            return True
        return widget.objectName() in self._GLOWING_NAMES

    def _moved(self, old: QWidget | None, new: QWidget | None) -> None:
        glowing, effect = self._glowing, self._effect
        self._glowing = self._effect = None
        if (glowing is not None and isValid(glowing) and isValid(effect)
                and glowing.graphicsEffect() is effect):
            glowing.setGraphicsEffect(None)  # deletes it
        if new is None or new.graphicsEffect() is not None or not self._wants_glow(new):
            return  # never replace an effect a widget has of its own
        glow = QGraphicsDropShadowEffect(new)
        colour = QColor(COLORS["focus"])
        colour.setAlpha(GLOW_ALPHA)
        glow.setColor(colour)
        glow.setBlurRadius(GLOW_BLUR_PX)
        glow.setOffset(0, 0)
        new.setGraphicsEffect(glow)
        self._glowing, self._effect = new, glow


def apply(app: QApplication) -> None:
    """Give the whole app the dark look: the Fusion style (the same on every
    platform), the palette, the stylesheet and the focus glow."""
    app.setStyle("Fusion")
    app.setPalette(palette())
    app.setStyleSheet(stylesheet())
    if app.findChild(_FocusGlow) is None:
        _FocusGlow(app)
