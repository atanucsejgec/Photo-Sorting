"""The app's line icons: SVG files in the icons folder next to this module.

They are Lucide icons (lucide-static 0.460.0, ISC licence; see icons/LICENSE),
drawn in "currentColor", which icon() replaces with a theme colour. Nothing
is fetched at runtime.
"""

from functools import cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from photosort.ui.theme import COLORS

FOLDER = Path(__file__).with_name("icons")

# Each MODES key's icon.
MODE_ICONS: dict[str, str] = {
    "date": "calendar",
    "face": "scan-face",
    "duplicates": "copy",
    "repeated": "film",
    "content": "shapes",
    "notes": "file-text",
    "bad": "triangle-alert",
    "events": "party-popper",
    "location": "map-pin",
    "camera": "camera",
    "type": "message-circle",
    "no_people": "user-x",
    "extension": "file-type",
    "all_dates": "calendar-clock",
}

_SIZES = (16, 20, 24, 32, 48, 64)  # drawn sharp at each; QIcon scales between


def _pixmap(svg: bytes, size: int) -> QPixmap:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    QSvgRenderer(QByteArray(svg)).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QPixmap.fromImage(image)


@cache
def icon(name: str, color: str = "text") -> QIcon:
    """The icon icons/<name>.svg, drawn in the theme colour color (a COLORS
    key), and in the "disabled" colour when its widget is disabled."""
    svg = (FOLDER / f"{name}.svg").read_text(encoding="utf-8")
    result = QIcon()
    for mode, token in ((QIcon.Mode.Normal, color), (QIcon.Mode.Disabled, "disabled")):
        data = svg.replace("currentColor", COLORS[token]).encode()
        for size in _SIZES:
            result.addPixmap(_pixmap(data, size), mode)
    return result
