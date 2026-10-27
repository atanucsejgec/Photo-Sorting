import os

import pytest

from photosort import naming
from photosort.naming import (
    TargetAllocator, fit_name, fold, real_dir, resolve_existing_dir, safe_name,
)
from photosort.thresholds import FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES


def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def test_safe_name():
    assert safe_name('a:b?.jpg') == 'a_b_.jpg'
    assert safe_name('Rahul. ') == 'Rahul'
    assert safe_name('con') == '_con'
    assert safe_name('CON.txt') == '_CON.txt'
    assert safe_name('  ') == '_'
    assert safe_name('Atanu & Rahul') == 'Atanu & Rahul'


def test_safe_name_all_bad_chars_and_control_chars():
    assert safe_name('\\/:*?"<>|') == '_________'
    assert safe_name('a\x00b\x1fc') == 'a_b_c'


def test_safe_name_reserved_names():
    for name in ("PRN", "aux", "Nul.jpg", "com1", "LPT9.tar.gz"):
        assert safe_name(name) == "_" + name
    assert safe_name("CON .txt") == "_CON .txt"
    assert safe_name("console") == "console"
    assert safe_name("com10") == "com10"


def test_safe_name_leading_spaces_and_only_dots():
    assert safe_name('  Rahul') == 'Rahul'
    assert safe_name('...') == '_'
    assert safe_name('') == '_'


def test_allocate_free_name(tmp_path):
    assert TargetAllocator(tmp_path).allocate("2025/x.jpg") == "2025/x.jpg"


def test_allocate_clash_on_disk_case_insensitive(tmp_path):
    _touch(tmp_path / "2025" / "IMG.jpg")
    assert TargetAllocator(tmp_path).allocate("2025/img.JPG") == "2025/img (1).JPG"


def test_allocate_clash_with_earlier_allocation(tmp_path):
    alloc = TargetAllocator(tmp_path)
    assert alloc.allocate("d/a.jpg") == "d/a.jpg"
    assert alloc.allocate("d/a.jpg") == "d/a (1).jpg"
    assert alloc.allocate("d/a.jpg") == "d/a (2).jpg"


def test_allocate_skips_numbers_taken_on_disk(tmp_path):
    _touch(tmp_path / "d" / "a.jpg")
    _touch(tmp_path / "d" / "A (1).JPG")
    assert TargetAllocator(tmp_path).allocate("d/a.jpg") == "d/a (2).jpg"


def test_allocate_splits_at_last_dot_and_handles_no_dot(tmp_path):
    _touch(tmp_path / "a.b.jpg")
    _touch(tmp_path / "README")
    alloc = TargetAllocator(tmp_path)
    assert alloc.allocate("a.b.jpg") == "a.b (1).jpg"
    assert alloc.allocate("README") == "README (1)"


def test_allocate_top_level_and_separate_dirs(tmp_path):
    alloc = TargetAllocator(tmp_path)
    assert alloc.allocate("x.jpg") == "x.jpg"
    assert alloc.allocate("d/x.jpg") == "d/x.jpg"
    assert alloc.allocate("e/x.jpg") == "e/x.jpg"


def test_allocate_dir_names_compared_case_insensitively(tmp_path):
    alloc = TargetAllocator(tmp_path)
    assert alloc.allocate("Rahul/a.jpg") == "Rahul/a.jpg"
    # One spelling per folder, so a run never makes "Rahul" and "rahul".
    assert alloc.allocate("rahul/A.jpg") == "Rahul/A (1).jpg"


def test_allocate_clashes_with_a_directory_on_disk(tmp_path):
    (tmp_path / "d" / "a.jpg").mkdir(parents=True)
    assert TargetAllocator(tmp_path).allocate("d/a.jpg") == "d/a (1).jpg"


def test_allocate_caches_listing_and_creates_nothing(tmp_path):
    alloc = TargetAllocator(tmp_path)
    assert alloc.allocate("d/a.jpg") == "d/a.jpg"
    assert not (tmp_path / "d").exists()
    _touch(tmp_path / "d" / "b.jpg")  # appears after the listing was read
    assert alloc.allocate("d/b.jpg") == "d/b.jpg"


def test_resolve_existing_dir_keeps_spelling_of_missing_parts(tmp_path):
    assert resolve_existing_dir(tmp_path, "") == ""
    assert resolve_existing_dir(tmp_path, "No Date") == "No Date"
    (tmp_path / "2025").mkdir()
    assert resolve_existing_dir(tmp_path, "2025/02-February") == "2025/02-February"


@pytest.mark.usefixtures("case_sensitive_fs")
def test_resolve_existing_dir_uses_on_disk_spelling(tmp_path):
    (tmp_path / "no date").mkdir()
    (tmp_path / "2025" / "02-february").mkdir(parents=True)
    _touch(tmp_path / "FILE")  # a file is not a folder to reuse
    assert resolve_existing_dir(tmp_path, "No Date") == "no date"
    assert (resolve_existing_dir(tmp_path, "2025/02-February/20-02-2025")
            == "2025/02-february/20-02-2025")
    assert resolve_existing_dir(tmp_path, "file") == "file"


@pytest.mark.usefixtures("case_sensitive_fs")
def test_allocate_uses_on_disk_spelling_of_folder(tmp_path):
    _touch(tmp_path / "no date" / "x.jpg")
    alloc = TargetAllocator(tmp_path)
    assert alloc.allocate("No Date/x.jpg") == "no date/x (1).jpg"
    assert alloc.allocate("No Date/y.jpg") == "no date/y.jpg"
    assert os.listdir(tmp_path) == ["no date"]


def test_fold_compares_case_and_unicode_forms():
    assert fold("ATANU") == fold("atanu")
    assert fold("\u099c\u09df") == fold("\u099c\u09af\u09bc")  # জয় typed two ways
    assert fold("Zoe") != fold("Zoé")


def test_resolve_existing_dir_matches_another_unicode_form(tmp_path):
    (tmp_path / "\u099c\u09df").mkdir()  # জয়, as another program spelt it
    assert resolve_existing_dir(tmp_path, "\u099c\u09af\u09bc/x") == "\u099c\u09df/x"
    alloc = TargetAllocator(tmp_path)
    assert alloc.allocate("\u099c\u09af\u09bc/x.jpg") == "\u099c\u09df/x.jpg"


def _fit(name):
    return fit_name(name, FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES)


def _usable_folder(tmp_path, folder):
    assert len(folder) <= FOLDER_NAME_MAX
    assert len(folder.encode()) <= FOLDER_NAME_MAX_BYTES
    assert safe_name(folder) == folder
    (tmp_path / folder).mkdir()  # a Linux file system takes it


def test_fit_name_keeps_a_name_that_fits():
    assert _fit("OPPO CPH2581") == "OPPO CPH2581"
    assert _fit("x" * FOLDER_NAME_MAX) == "x" * FOLDER_NAME_MAX


def test_fit_name_cuts_by_characters():
    assert _fit("A" * 98 + ". " + "b" * 50) == "A" * 98  # no trailing ". " left
    assert fit_name("abcdef", 3, 200) == "abc"


@pytest.mark.parametrize("max_chars, max_bytes", [(0, 200), (100, 0), (-1, -1)])
def test_fit_name_refuses_impossible_limits(max_chars, max_bytes):
    with pytest.raises(ValueError):
        fit_name("abc", max_chars, max_bytes)


def test_fit_name_cuts_by_bytes(tmp_path):
    folder = _fit("অ" * 100)
    assert folder == "অ" * (FOLDER_NAME_MAX_BYTES // 3)
    _usable_folder(tmp_path, folder)


def test_fit_name_never_leaves_a_dangling_vowel_sign(tmp_path):
    # "কা" is KA + the vowel sign AA (a combining mark): cut between them, the
    # vowel sign goes too, rather than ending the name on it.
    name = "ক" * 65 + "া" + "ক" * 10  # 198 bytes fit: the cut falls just after the sign
    folder = _fit(name)
    assert folder == "ক" * 65
    _usable_folder(tmp_path, folder)


@pytest.mark.parametrize("name", [
    "কলকাতা",  # Kolkata: ends on the vowel sign AA
    "সীমা",
    "জয়",  # ends on the nukta (a combining mark)
    "Cafe\u0301",  # Café, its accent typed as a combining mark
])
def test_fit_name_keeps_a_final_mark_when_nothing_is_cut(tmp_path, name):
    assert _fit(name) == name
    _usable_folder(tmp_path, name)


@pytest.mark.parametrize("name, cut", [
    ("Duplicates" + " " * 95 + "x" * 20, "_Duplicates"),  # would be skipped by scans
    ("CON" + " " * 100 + "y", "_CON"),  # reserved on Windows
    ("Bad Photos", "_Bad Photos"),
    (".hidden", "_.hidden"),
    ("a:b", "a_b"),
])
def test_fit_name_gives_a_valid_scanned_folder(tmp_path, name, cut):
    folder = _fit(name)
    assert folder == cut
    _usable_folder(tmp_path, folder)


def test_real_dir(tmp_path):
    (tmp_path / "folder").mkdir()
    (tmp_path / "file").write_bytes(b"f")
    assert real_dir(tmp_path / "folder") is True
    assert real_dir(tmp_path / "file") is False
    assert real_dir(tmp_path / "missing") is False
    try:
        os.symlink(tmp_path / "folder", tmp_path / "link", target_is_directory=True)
        os.symlink(tmp_path / "missing", tmp_path / "broken")
    except OSError:
        pytest.skip("symlinks are not allowed here")
    assert real_dir(tmp_path / "link") is False  # a link to a folder is not one
    assert real_dir(tmp_path / "broken") is False


def test_a_junction_is_not_a_real_dir(tmp_path, monkeypatch):
    (tmp_path / "junction").mkdir()  # Linux has no junctions: pretend it is one
    monkeypatch.setattr(naming.os.path, "isjunction",
                        lambda p: os.fspath(p) == os.fspath(tmp_path / "junction"))
    assert real_dir(tmp_path / "junction") is False
