from photosort.naming import TargetAllocator, safe_name


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
    assert alloc.allocate("rahul/A.jpg") == "rahul/A (1).jpg"


def test_allocate_clashes_with_a_directory_on_disk(tmp_path):
    (tmp_path / "d" / "a.jpg").mkdir(parents=True)
    assert TargetAllocator(tmp_path).allocate("d/a.jpg") == "d/a (1).jpg"


def test_allocate_caches_listing_and_creates_nothing(tmp_path):
    alloc = TargetAllocator(tmp_path)
    assert alloc.allocate("d/a.jpg") == "d/a.jpg"
    assert not (tmp_path / "d").exists()
    _touch(tmp_path / "d" / "b.jpg")  # appears after the listing was read
    assert alloc.allocate("d/b.jpg") == "d/b.jpg"
