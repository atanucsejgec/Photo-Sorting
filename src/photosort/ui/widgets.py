"""Small widgets the pages share."""

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QWidget

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
