"""The left column: the brand and the four pages, Sort, History, People and
Settings.

↑ and ↓ move between the items and Enter (or a click) picks one: chosen(key)
asks the window to show that page. The sidebar is one stop for Tab: only the
item last moved to takes Tab focus, so Tab leaves the sidebar. While a sort
runs the window locks it: the items are dimmed and can't be focused or
clicked.
"""

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPen, QPolygonF,
)
from PySide6.QtWidgets import QPushButton, QSizePolicy, QVBoxLayout, QWidget

from photosort.ui import icons, shortcuts
from photosort.ui.theme import COLORS, scaled_font, styled

# (key, label, icon), top to bottom.
ITEMS: tuple[tuple[str, str, str], ...] = (
    ("sort", "Sort", "layout-grid"),
    ("history", "History", "history"),
    ("people", "People", "users"),
    ("settings", "Settings", "settings"),
)
WIDTH_PX = 176
BRAND_SCALE = 1.15
_BAR_PX = 2  # the bar marking the page shown
_ICON_PX = 16


class Brand(QWidget):
    """"◆ Photo Sorter", with "Sorter" in the brand gradient. Painted, so the
    diamond needs no symbol font and the gradient fills the word."""

    WORDS = ("Photo ", "Sorter")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(scaled_font(self, BRAND_SCALE, QFont.Weight.ExtraBold))
        self.setAccessibleName("".join(self.WORDS))
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def _diamond_px(self) -> float:
        return QFontMetricsF(self.font()).capHeight() * 1.05

    def sizeHint(self) -> QSize:
        metrics = QFontMetricsF(self.font())
        width = self._diamond_px() * 1.6 + metrics.horizontalAdvance("".join(self.WORDS))
        return QSize(int(width) + 4, int(metrics.height()) + 4)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self.font())
        metrics = QFontMetricsF(self.font())
        baseline = (self.height() + metrics.capHeight()) / 2
        size = self._diamond_px()
        middle = baseline - metrics.capHeight() / 2
        half = size / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS["text"]))
        painter.drawPolygon(QPolygonF([QPointF(half, middle - half), QPointF(size, middle),
                                       QPointF(half, middle + half), QPointF(0, middle)]))
        x = size * 1.6
        first, second = self.WORDS
        painter.setPen(QColor(COLORS["text"]))
        painter.drawText(QPointF(x, baseline), first)
        x += metrics.horizontalAdvance(first)
        width = metrics.horizontalAdvance(second)
        gradient = QLinearGradient(x, 0, x + width, 0)
        gradient.setColorAt(0, QColor(COLORS["brand_from"]))
        gradient.setColorAt(1, QColor(COLORS["brand_to"]))
        painter.setPen(QPen(QBrush(gradient), 1))
        painter.drawText(QPointF(x, baseline), second)
        painter.end()


class SidebarItem(QPushButton):
    """A page's button; the page shown is checked, with a bar on its left."""

    def __init__(self, text: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._icon_name = icon_name
        self.setObjectName("sidebarItem")
        self.setCheckable(True)
        self.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.toggled.connect(self._recolour)
        self._recolour(False)

    def _recolour(self, checked: bool) -> None:
        self.setIcon(icons.icon(self._icon_name, "text" if checked else "muted"))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.isChecked():
            painter = QPainter(self)
            colour = COLORS["focus"] if self.isEnabled() else COLORS["border_hover"]
            painter.fillRect(QRectF(0, 4, _BAR_PX, self.height() - 8), QColor(colour))
            painter.end()


class Sidebar(QWidget):
    """The brand over the page items. chosen(key) when one is picked."""

    chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        styled(self, "sidebar")
        self.setFixedWidth(WIDTH_PX)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 18, 12, 18)
        layout.setSpacing(4)
        self.brand = Brand()
        layout.addWidget(self.brand)
        layout.addSpacing(14)
        self.items: dict[str, SidebarItem] = {}
        for key, label, icon_name in ITEMS:
            item = SidebarItem(label, icon_name)
            item.clicked.connect(lambda _=False, key=key: self._pick(key))
            item.installEventFilter(self)
            self.items[key] = item
            layout.addWidget(item)
        layout.addStretch()
        shortcuts.describe(self, "↑↓", "move")
        shortcuts.describe(self, "Enter", "open")
        shortcuts.describe(self, "Tab", "leave")
        self._locked = False
        self.select("sort")

    def select(self, key: str) -> None:
        """Mark key's page as the one shown (without emitting chosen); it
        becomes the item Tab comes back to."""
        for item_key, item in self.items.items():
            item.setChecked(item_key == key)
        self._make_tab_stop(self.items[key])

    def current(self) -> SidebarItem:
        return next((item for item in self.items.values() if item.isChecked()),
                    self.items["sort"])

    def focus_current(self) -> None:
        """Give the focus to the item of the page shown (unless locked)."""
        if not self._locked:
            self.current().setFocus(Qt.FocusReason.OtherFocusReason)

    def set_locked(self, locked: bool) -> None:
        """Dim the items, so they can't be focused or clicked; or bring them back."""
        self._locked = locked
        for item in self.items.values():
            item.setEnabled(not locked)

    def is_locked(self) -> bool:
        return self._locked

    def _pick(self, key: str) -> None:
        if not self._locked:
            self.chosen.emit(key)

    def _make_tab_stop(self, stop: QPushButton) -> None:
        for item in self.items.values():
            item.setFocusPolicy(Qt.FocusPolicy.StrongFocus if item is stop
                                else Qt.FocusPolicy.ClickFocus)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched in self.items.values():
            if event.type() == QEvent.Type.FocusIn:
                self._make_tab_stop(watched)
            elif event.type() == QEvent.Type.KeyPress:
                key = event.key()
                items = list(self.items.values())
                if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                    n = items.index(watched) + (1 if key == Qt.Key.Key_Down else -1)
                    if 0 <= n < len(items):
                        items[n].setFocus(Qt.FocusReason.OtherFocusReason)
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if not event.isAutoRepeat():
                        watched.click()
                    return True
        return super().eventFilter(watched, event)
