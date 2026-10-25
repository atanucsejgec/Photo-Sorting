"""Read the text tags of MP4 and MOV videos: camera make and model, and place.

A video is a tree of ISO-BMFF boxes (atoms). Only box headers are read, by
seeking, plus the payloads of the few boxes that hold tags, so a large video
costs a few small reads; the video data itself is never touched. The tags
come from:

- moov/udta: the QuickTime user-data text boxes ©xyz (ISO 6709 location),
  ©mak and ©mod (make and model);
- moov/meta and moov/udta/meta: a keys box naming the tags (reverse-DNS, such
  as "com.apple.quicktime.make") and an ilst box with their values. An ilst
  item's type is the 1-based number of its key; without a keys box (iTunes
  style) the item's own four-character type is its name.

Only UTF-8 values become tags. Anything malformed ends the reading.
"""

import os
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from photosort.thresholds import MP4_META_MAX

_UDTA_TEXT = frozenset({"©xyz", "©mak", "©mod"})
_META_CHILDREN = frozenset({b"hdlr", b"keys", b"ilst"})
_UTF8 = 1  # well-known data type: UTF-8 text


class _Malformed(Exception):
    """The box tree does not hold together; stop reading."""


class _Reader:
    """Box headers and payloads from an open file, payloads within a budget."""

    def __init__(self, file: BinaryIO, budget: int) -> None:
        self._file = file
        self._left = budget

    def boxes(self, at: int, end: int) -> Iterator[tuple[str, int, int]]:
        """(type, payload start, box end) for each box from at up to end.

        Fewer than 8 bytes left over (a QuickTime 32-bit zero terminator,
        say) end the list. A box that cannot fit raises _Malformed.
        """
        while end - at >= 8:
            self._file.seek(at)
            size, kind = struct.unpack(">I4s", self._read(8))
            start = at + 8
            if size == 1:  # a 64-bit size follows the type
                (size,) = struct.unpack(">Q", self._read(8))
                start = at + 16
                if size < 16:
                    raise _Malformed
            elif size == 0:  # runs to the end of its parent
                size = end - at
            elif size < 8:
                raise _Malformed
            if at + size > end:
                raise _Malformed
            yield kind.decode("latin-1"), start, at + size
            at += size

    def peek(self, at: int, n: int) -> bytes:
        self._file.seek(at)
        return self._read(n)

    def payload(self, start: int, end: int) -> bytes | None:
        """The bytes from start to end, or None when that would pass the budget."""
        if end - start > self._left:
            return None
        self._left -= end - start
        self._file.seek(start)
        return self._read(end - start)

    def _read(self, n: int) -> bytes:
        raw = self._file.read(n)
        if len(raw) < n:
            raise _Malformed
        return raw


def mp4_tags(path: Path) -> dict[str, str]:
    """The video's text tags, by name ("©xyz", "com.apple.quicktime.make", ...).

    Reads at most MP4_META_MAX bytes of payload. A malformed file gives the
    tags read before the fault (often none); an unreadable one gives {}.
    Never raises.
    """
    tags: dict[str, str] = {}
    try:
        with open(path, "rb") as file:
            reader = _Reader(file, MP4_META_MAX)
            for kind, start, end in reader.boxes(0, os.fstat(file.fileno()).st_size):
                if kind == "moov":
                    _read_moov(reader, start, end, tags)
                    break
    except Exception:  # malformed or unreadable: whatever was read so far
        pass
    return tags


def _read_moov(reader: _Reader, start: int, end: int, tags: dict[str, str]) -> None:
    for kind, child_start, child_end in reader.boxes(start, end):
        if kind == "udta":
            _read_udta(reader, child_start, child_end, tags)
        elif kind == "meta":
            _read_meta(reader, child_start, child_end, tags)


def _read_udta(reader: _Reader, start: int, end: int, tags: dict[str, str]) -> None:
    for kind, child_start, child_end in reader.boxes(start, end):
        if kind in _UDTA_TEXT:
            raw = reader.payload(child_start, child_end)
            value = _udta_text(raw) if raw is not None else None
            if value is not None:
                tags.setdefault(kind, value)
        elif kind == "meta":
            _read_meta(reader, child_start, child_end, tags)


def _udta_text(raw: bytes) -> str | None:
    """A user-data text box's text: 16-bit length, 16-bit language, text."""
    if len(raw) < 4:
        return None
    (length,) = struct.unpack(">H", raw[:2])
    text = raw[4:4 + length]
    return _utf8(text) if len(text) == length else None


def _read_meta(reader: _Reader, start: int, end: int, tags: dict[str, str]) -> None:
    """A meta box: QuickTime style (children straight away) or ISO style
    (4 bytes of version and flags first), told apart by its first child."""
    if end - start >= 8 and reader.peek(start, 8)[4:] not in _META_CHILDREN:
        start += 4
    names: list[str | None] | None = None
    ilst = None
    for kind, child_start, child_end in reader.boxes(start, end):
        if kind == "keys":
            raw = reader.payload(child_start, child_end)
            names = _key_names(raw) if raw is not None else []
        elif kind == "ilst":
            ilst = reader.payload(child_start, child_end)
    if ilst is not None:
        _read_ilst(ilst, names, tags)


def _key_names(raw: bytes) -> list[str | None]:
    """A keys box's names, in order (None for one that is not UTF-8)."""
    names: list[str | None] = []
    if len(raw) < 8:
        return names
    (count,) = struct.unpack(">I", raw[4:8])
    pos = 8
    while len(names) < count and pos + 8 <= len(raw):
        (size,) = struct.unpack(">I", raw[pos:pos + 4])
        if size < 8 or pos + size > len(raw):
            break
        names.append(_utf8(raw[pos + 8:pos + size]))  # after size and namespace
        pos += size
    return names


def _read_ilst(raw: bytes, names: list[str | None] | None, tags: dict[str, str]) -> None:
    """Each item's first UTF-8 value, named by its key (or by its own type)."""
    for kind, value in _inner_boxes(raw):
        if names is None:
            name = kind.decode("latin-1")
        else:
            (index,) = struct.unpack(">I", kind)
            name = names[index - 1] if 1 <= index <= len(names) else None
        text = _first_utf8(value)
        if name is not None and text is not None:
            tags.setdefault(name, text)


def _first_utf8(item: bytes) -> str | None:
    """An ilst item's first data box holding UTF-8: type indicator, locale, value."""
    for kind, value in _inner_boxes(item):
        if kind == b"data" and len(value) >= 8 and struct.unpack(">I", value[:4])[0] == _UTF8:
            text = _utf8(value[8:])
            if text is not None:
                return text
    return None


def _inner_boxes(raw: bytes) -> Iterator[tuple[bytes, bytes]]:
    """(type, payload) of the boxes laid end to end in raw; stops at a bad size."""
    pos = 0
    while pos + 8 <= len(raw):
        size, kind = struct.unpack(">I4s", raw[pos:pos + 8])
        if size < 8 or pos + size > len(raw):
            return
        yield kind, raw[pos + 8:pos + size]
        pos += size


def _utf8(raw: bytes) -> str | None:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
