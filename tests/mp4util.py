"""Build small MP4 (ISO-BMFF) files byte by byte, for the metadata tests.

Only the boxes the readers look at are written; there is no playable video.
Box types are given as str and written as latin-1, so "©xyz" is b"\\xa9xyz".
"""

import struct
from pathlib import Path


def box(kind: str, payload: bytes = b"") -> bytes:
    """One box: 32-bit size, 4-character type, payload."""
    raw = kind.encode("latin-1")
    assert len(raw) == 4, kind
    return struct.pack(">I", 8 + len(payload)) + raw + payload


def ftyp() -> bytes:
    return box("ftyp", b"isom" + struct.pack(">I", 0) + b"isommp42")


def udta_text(kind: str, text: str, lang: int = 0x15C7) -> bytes:
    """A QuickTime user-data text box (©xyz, ©mak, ...): length, language, text."""
    raw = text.encode("utf-8")
    return box(kind, struct.pack(">HH", len(raw), lang) + raw)


def data(value: bytes, type_indicator: int = 1, locale: int = 0) -> bytes:
    """An ilst item's data box: type indicator (1 = UTF-8), locale, value."""
    return box("data", struct.pack(">II", type_indicator, locale) + value)


def _value(value: str | float | bytes) -> bytes:
    if isinstance(value, str):
        return data(value.encode("utf-8"), 1)
    if isinstance(value, float):
        return data(struct.pack(">f", value), 23)  # well-known type 23: float32
    return value  # a ready-made data box (or anything else to put in the item)


def item(index: int, *children: bytes) -> bytes:
    """An ilst item for the index-th key (1-based): its type is the index."""
    payload = b"".join(children)
    return struct.pack(">II", 8 + len(payload), index) + payload


def keys(*names: str) -> bytes:
    """A keys box listing names in the mdta namespace."""
    entries = b"".join(struct.pack(">I", 8 + len(k.encode())) + b"mdta" + k.encode()
                       for k in names)
    return box("keys", bytes(4) + struct.pack(">I", len(names)) + entries)


def hdlr() -> bytes:
    return box("hdlr", bytes(8) + b"mdta" + bytes(12) + b"\x00")


def mdta_meta(items: dict[str, str | float | bytes], iso: bool = False) -> bytes:
    """A meta box with hdlr (mdta), keys and ilst holding items in order.

    str values are UTF-8 data, floats are float32 data, bytes go in as they
    are. iso=True writes the 4-byte version/flags an ISO-style meta has.
    """
    ilst = box("ilst", b"".join(
        item(n, _value(v)) for n, v in enumerate(items.values(), start=1)
    ))
    return box("meta", (bytes(4) if iso else b"") + hdlr() + keys(*items) + ilst)


def mp4(*moov_children: bytes, after: bytes = b"") -> bytes:
    """ftyp, then moov holding moov_children, then after (free, mdat, ...)."""
    return ftyp() + box("moov", b"".join(moov_children)) + after


def write(path: Path, content: bytes) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path
