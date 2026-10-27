"""Small widgets the pages share."""

from collections.abc import Sequence

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QCheckBox, QLabel, QLayout, QLayoutItem, QWidget

from photosort.ui.theme import COLORS


class Switch(QCheckBox):
    """A checkbox drawn as an on/off switch: a track with a knob, then the text."""

    TRACK_W, TRACK_H = 30, 16
    _PAD = 4  # room for the focus ring
    _GAP = 10

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        width = self._PAD * 2 + self.TRACK_W + self._GAP + metrics.horizontalAdvance(self.text())
        return QSize(width, max(self.TRACK_H, metrics.height()) + self._PAD * 2)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        on, enabled = self.isChecked(), self.isEnabled()
        track = QRectF(self._PAD, (self.height() - self.TRACK_H) / 2, self.TRACK_W,
                       self.TRACK_H)
        radius = self.TRACK_H / 2
        if on and enabled:
            fill = QLinearGradient(track.topLeft(), track.topRight())
            fill.setColorAt(0, QColor(COLORS["accent_from"]))
            fill.setColorAt(1, QColor(COLORS["accent_to"]))
            painter.setBrush(fill)
            painter.setPen(Qt.PenStyle.NoPen)
        else:
            painter.setBrush(QColor(COLORS["raised"]))
            painter.setPen(QPen(QColor(COLORS["border"]), 1))
        painter.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        knob = self.TRACK_H - 6
        x = track.right() - 3 - knob if on else track.left() + 3
        painter.setPen(Qt.PenStyle.NoPen)
        knob_colour = "on_accent" if on and enabled else ("muted" if enabled else "disabled")
        painter.setBrush(QColor(COLORS[knob_colour]))
        painter.drawEllipse(QRectF(x, track.top() + 3, knob, knob))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(COLORS["focus"]), 2))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
        painter.setPen(QColor(COLORS["text" if on else "muted"] if enabled
                              else COLORS["disabled"]))
        text_rect = QRectF(track.right() + self._GAP, 0,
                           self.width() - track.right() - self._GAP, self.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, self.text())
        painter.end()


class ButtonBar(QWidget):
    """A page's row of buttons: left ones on the left, right ones on the
    right, as one row when they fit. When they do not (a narrow window),
    the left ones come first and the right ones below them, right-aligned,
    each group wrapping onto more rows as it must; no button is ever
    squeezed below its size."""

    SPACING = 10

    def __init__(self, left: Sequence[QWidget], right: Sequence[QWidget],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = _BarLayout(self, self.SPACING)
        for widget in left:
            self._layout.add(widget, right=False)
        for widget in right:
            self._layout.add(widget, right=True)

    def rows(self) -> int:
        """How many rows the buttons take now."""
        return self._layout.rows(self.width())


class _BarLayout(QLayout):
    """ButtonBar's layout (see there); its height follows its width."""

    _GROUP_GAP = 24  # at least this between the groups on one row

    def __init__(self, parent: QWidget, spacing: int) -> None:
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self._spacing = spacing
        self._items: list[tuple[QLayoutItem, bool]] = []  # (item, on the right)
        self._right = False

    def add(self, widget: QWidget, right: bool) -> None:
        self._right = right
        self.addWidget(widget)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append((item, self._right))

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index][0] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        return self._items.pop(index)[0] if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), move=False)[0]

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, move=True)

    def sizeHint(self) -> QSize:
        shown = [item.sizeHint() for item, _ in self._items if not item.isEmpty()]
        width = sum(s.width() for s in shown) + self._spacing * max(0, len(shown) - 1)
        if any(right for item, right in self._items if not item.isEmpty()):
            width += self._GROUP_GAP
        return QSize(width, max((s.height() for s in shown), default=0))

    def minimumSize(self) -> QSize:
        shown = [item.sizeHint() for item, _ in self._items if not item.isEmpty()]
        return QSize(max((s.width() for s in shown), default=0),
                     max((s.height() for s in shown), default=0))

    def rows(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), move=False)[1]

    def _arrange(self, rect: QRect, move: bool) -> tuple[int, int]:
        """Place the items in rect (if move); return (the height they take,
        the rows they take)."""
        left = [item for item, right in self._items if not right and not item.isEmpty()]
        right = [item for item, on_right in self._items if on_right and not item.isEmpty()]
        if not left and not right:
            return 0, 0
        gap = self._spacing

        def span(items: list[QLayoutItem]) -> int:
            return sum(i.sizeHint().width() for i in items) + gap * max(0, len(items) - 1)

        if span(left) + span(right) + (self._GROUP_GAP if left and right else 0) <= rect.width():
            rows = [(left, right)]  # one row: left at the left, right at the right
        else:
            rows = [(row, []) for row in self._wrap(left, rect.width())]
            rows += [([], row) for row in self._wrap(right, rect.width())]
        y = rect.y()
        for n, (lefts, rights) in enumerate(rows):
            height = max(i.sizeHint().height() for i in lefts + rights)
            if move:
                x = rect.x()
                for item in lefts:
                    item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
                    x += item.sizeHint().width() + gap
                x = rect.right() + 1 - span(rights)
                for item in rights:
                    item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
                    x += item.sizeHint().width() + gap
            y += height + (gap if n < len(rows) - 1 else 0)
        return y - rect.y(), len(rows)

    def _wrap(self, items: list[QLayoutItem], width: int) -> list[list[QLayoutItem]]:
        rows: list[list[QLayoutItem]] = []
        used = 0
        for item in items:
            w = item.sizeHint().width()
            if rows and used + self._spacing + w <= width:
                rows[-1].append(item)
                used += self._spacing + w
            else:
                rows.append([item])
                used = w
        return rows


SHORTEST_THUMB_PX = 96  # a card's thumbnail shrinks to fit a short window, not below this


class Thumbnail(QLabel):
    """A square thumbnail, side px, showing text until set_image(); the
    photo is fitted inside it at any side (never scaled up). It says when it
    is double-clicked."""

    double_clicked = Signal()

    def __init__(self, text: str, side: int, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._source: QPixmap | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("thumb")  # see theme
        self.set_side(side)

    def set_side(self, side: int) -> None:
        if self.minimumWidth() == side == self.maximumHeight():
            return
        self.setFixedSize(side, side)
        self._fit()

    def set_image(self, image: QImage) -> None:
        self.setText("")
        self._source = QPixmap.fromImage(image)
        self._fit()

    def _fit(self) -> None:
        if self._source is None:
            return
        side = self.minimumWidth()
        if max(self._source.width(), self._source.height()) <= side:
            self.setPixmap(self._source)
        else:
            self.setPixmap(self._source.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()
        event.accept()


def needed_height(widget: QWidget) -> int:
    """How tall widget wants to be at the width it has (fixed) or wants,
    its wrapped labels counted."""
    width = max(widget.minimumWidth(), widget.sizeHint().width())
    height = widget.heightForWidth(width)
    return height if height >= 0 else widget.sizeHint().height()


def fitting_thumb_side(widest: int, rest: int, room: int) -> int:
    """The side of a card's thumbnail so that the card, rest px tall without
    it, fits room px: at most widest, at least SHORTEST_THUMB_PX."""
    return max(SHORTEST_THUMB_PX, min(widest, room - rest))
