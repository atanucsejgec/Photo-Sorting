"""Turn a decoded PIL image into a QImage, on any thread."""

from PIL import Image
from PySide6.QtGui import QImage


def pil_to_qimage(img: Image.Image) -> QImage:
    """Return a QImage that owns a copy of img's pixels (converted to RGB).

    A QImage made from bytes only borrows them, so the bytes are kept in a
    local until .copy() has taken its own.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")
    data = img.tobytes()
    return QImage(data, img.width, img.height, 3 * img.width,
                  QImage.Format.Format_RGB888).copy()
