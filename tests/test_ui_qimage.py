from PIL import Image
from PySide6.QtGui import QColor, QImage

from photosort.ui.qimage import pil_to_qimage


def test_pil_to_qimage_copies_the_pixels():
    img = Image.new("RGB", (5, 3), (10, 20, 30))  # 15 bytes a row: not 4-aligned
    img.putpixel((4, 2), (200, 100, 50))
    image = pil_to_qimage(img)
    del img
    assert isinstance(image, QImage)
    assert (image.width(), image.height()) == (5, 3)
    assert image.pixelColor(0, 0) == QColor(10, 20, 30)
    assert image.pixelColor(4, 2) == QColor(200, 100, 50)


def test_pil_to_qimage_converts_other_modes():
    image = pil_to_qimage(Image.new("L", (2, 2), 128))
    assert image.pixelColor(1, 1) == QColor(128, 128, 128)
