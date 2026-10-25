import struct

import pytest
from mp4util import box, data, ftyp, hdlr, item, keys, mdta_meta, mp4, udta_text, write

from photosort import mp4meta
from photosort.mp4meta import mp4_tags

XYZ = "+22.5726+088.3639/"


def test_udta_location(tmp_path):
    udta = box("udta", udta_text("©xyz", XYZ))
    path = write(tmp_path / "v.mp4", mp4(box("mvhd", bytes(100)), udta))
    assert mp4_tags(path) == {"©xyz": XYZ}


def test_udta_make_and_model(tmp_path):
    udta = box("udta", udta_text("©mak", "samsung") + udta_text("©mod", "SM-A525F"))
    path = write(tmp_path / "v.mp4", mp4(udta))
    assert mp4_tags(path) == {"©mak": "samsung", "©mod": "SM-A525F"}


def test_moov_meta_keys(tmp_path):
    meta = mdta_meta({"com.apple.quicktime.make": "Apple",
                      "com.apple.quicktime.model": "iPhone 12"})
    path = write(tmp_path / "v.mov", mp4(box("mvhd", bytes(100)), meta))
    assert mp4_tags(path) == {"com.apple.quicktime.make": "Apple",
                              "com.apple.quicktime.model": "iPhone 12"}


def test_iso_style_meta_inside_udta(tmp_path):
    meta = mdta_meta({"com.android.manufacturer": "Google", "com.android.model": "Pixel 7"},
                     iso=True)
    path = write(tmp_path / "v.mp4", mp4(box("udta", meta)))
    assert mp4_tags(path) == {"com.android.manufacturer": "Google",
                              "com.android.model": "Pixel 7"}


def test_oppo_layout_has_no_make_or_model(tmp_path):
    # As measured on the owner's OPPO videos.
    meta = mdta_meta({"com.android.version": "13", "com.android.capture.fps": 30.0})
    trak = box("trak", box("tkhd", bytes(84)) + box("mdia", bytes(200)))
    moov_children = (box("mvhd", bytes(100)), meta, trak, trak,
                     box("udta", box("titl", b"\x00\x07\x15\xc7Oplus_0")))
    path = write(tmp_path / "v.mp4", mp4(*moov_children,
                                         after=box("free", bytes(8)) + box("mdat", bytes(5000))))
    assert mp4_tags(path) == {"com.android.version": "13"}


def test_only_utf8_values_are_strings(tmp_path):
    meta = mdta_meta({"a.float": 1.5,
                      "a.utf16": data("x".encode("utf-16-be"), 2),
                      "a.bad": data(b"\xff\xfe", 1),
                      "a.good": "yes"})
    path = write(tmp_path / "v.mp4", mp4(meta))
    assert mp4_tags(path) == {"a.good": "yes"}


def test_ilst_index_out_of_range_ignored(tmp_path):
    ilst = box("ilst", item(1, data(b"1")) + item(7, data(b"x")) + item(0, data(b"y")))
    path = write(tmp_path / "v.mp4", mp4(box("meta", hdlr() + keys("a.one") + ilst)))
    assert mp4_tags(path) == {"a.one": "1"}


def test_keys_after_ilst(tmp_path):
    ilst = box("ilst", item(1, data(b"Apple")))
    meta = box("meta", hdlr() + ilst + keys("com.apple.quicktime.make"))
    path = write(tmp_path / "v.mp4", mp4(meta))
    assert mp4_tags(path) == {"com.apple.quicktime.make": "Apple"}


def test_size_zero_box_runs_to_end_of_file(tmp_path):
    good = mp4(box("udta", udta_text("©xyz", XYZ)))
    path = write(tmp_path / "v.mp4", good + struct.pack(">I", 0) + b"mdat" + bytes(64))
    assert mp4_tags(path) == {"©xyz": XYZ}
    # A size-0 moov is read to the end of the file.
    moov = box("udta", udta_text("©xyz", XYZ))
    path = write(tmp_path / "w.mp4", ftyp() + struct.pack(">I", 0) + b"moov" + moov)
    assert mp4_tags(path) == {"©xyz": XYZ}


def test_size_one_box_has_64_bit_size(tmp_path):
    free = struct.pack(">I", 1) + b"free" + struct.pack(">Q", 16 + 40) + bytes(40)
    payload = box("udta", udta_text("©xyz", XYZ))
    moov = struct.pack(">I", 1) + b"moov" + struct.pack(">Q", 16 + len(payload)) + payload
    path = write(tmp_path / "v.mp4", ftyp() + free + moov)
    assert mp4_tags(path) == {"©xyz": XYZ}


def test_huge_64_bit_box_before_moov(tmp_path):
    mdat = struct.pack(">I", 1) + b"mdat" + struct.pack(">Q", 2**62)
    moov = box("moov", box("udta", udta_text("©xyz", XYZ)))
    path = write(tmp_path / "v.mp4", ftyp() + mdat + moov)
    assert mp4_tags(path) == {}


@pytest.mark.parametrize("bad", [
    struct.pack(">I", 3) + b"free",  # smaller than its own header
    struct.pack(">I", 1) + b"free" + struct.pack(">Q", 8),  # 64-bit size below 16
])
def test_impossible_box_size(tmp_path, bad):
    path = write(tmp_path / "v.mp4", ftyp() + bad + mp4(box("udta", udta_text("©xyz", XYZ))))
    assert mp4_tags(path) == {}


def test_box_running_past_end_of_file(tmp_path):
    udta = box("udta", udta_text("©xyz", XYZ))
    moov = struct.pack(">I", 8 + len(udta) + 1000) + b"moov" + udta
    path = write(tmp_path / "v.mp4", ftyp() + moov)
    assert mp4_tags(path) in ({}, {"©xyz": XYZ})
    # A text box claiming more text than it holds gives nothing.
    text = box("©xyz", struct.pack(">HH", 200, 0x15C7) + XYZ.encode())
    path = write(tmp_path / "w.mp4", mp4(box("udta", text)))
    assert mp4_tags(path) == {}


def test_truncated_files_never_raise(tmp_path):
    meta = mdta_meta({"com.apple.quicktime.make": "Apple",
                      "com.apple.quicktime.model": "iPhone 12"})
    whole = mp4(box("mvhd", bytes(100)), meta, box("udta", udta_text("©xyz", XYZ)))
    full = mp4_tags(write(tmp_path / "whole.mp4", whole))
    assert len(full) == 3
    path = tmp_path / "cut.mp4"
    for end in range(len(whole)):
        path.write_bytes(whole[:end])
        tags = mp4_tags(path)
        assert tags.items() <= full.items()


def test_garbage_and_unreadable(tmp_path):
    assert mp4_tags(write(tmp_path / "g.mp4", b"\xff" * 300)) == {}
    assert mp4_tags(write(tmp_path / "e.mp4", b"")) == {}
    assert mp4_tags(tmp_path / "missing.mp4") == {}
    assert mp4_tags(tmp_path) == {}  # a folder


def test_payload_reads_capped(tmp_path, monkeypatch):
    path = write(tmp_path / "v.mp4", mp4(box("udta", udta_text("©mak", "Apple")
                                               + udta_text("©xyz", XYZ))))
    monkeypatch.setattr(mp4meta, "MP4_META_MAX", 4 + len("Apple") + 4 + len(XYZ) - 1)
    assert mp4_tags(path) == {"©mak": "Apple"}  # the second payload would pass the cap


def test_cap_is_four_mib():
    assert mp4meta.MP4_META_MAX == 4 * 1024 * 1024


def test_moov_after_large_media_data(tmp_path):
    # mdat first, as some phones write it: skipped by seeking, never read.
    path = tmp_path / "v.mp4"
    size = 50 * 1024 * 1024
    with open(path, "wb") as f:
        f.write(ftyp() + struct.pack(">I", size) + b"mdat")
        f.truncate(len(ftyp()) + size)  # sparse: no 50 MB written
        f.seek(0, 2)
        f.write(box("moov", box("udta", udta_text("©xyz", XYZ))))
    assert mp4_tags(path) == {"©xyz": XYZ}
