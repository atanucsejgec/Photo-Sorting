import json

import pytest

from photosort.settings import (
    DEFAULT_CONTENT_CATEGORIES,
    Settings,
    category_problem,
    default_path,
    load_settings,
    save_settings,
    valid_category_list,
)


def test_defaults(tmp_path):
    assert load_settings(tmp_path / "missing.json") == Settings(False, True, None)


def test_roundtrip(tmp_path):
    path = tmp_path / "deep" / "dir" / "settings.json"
    s = Settings(include_subfolders=True, use_gpu=False, last_folder="/photos/2024")
    save_settings(s, path)
    assert load_settings(path) == s


def test_corrupt_file_gives_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert load_settings(path) == Settings()


def test_non_object_json_gives_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[true, false]")
    assert load_settings(path) == Settings()


def test_bad_fields_fall_back_and_unknown_keys_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "include_subfolders": "yes",
        "use_gpu": False,
        "last_folder": 42,
        "colour": "blue",
    }))
    assert load_settings(path) == Settings(False, False, None)


def test_last_folder_null_accepted(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"include_subfolders": True, "last_folder": None}))
    assert load_settings(path) == Settings(True, True, None)


def test_save_overwrites_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(Settings(include_subfolders=True), path)
    save_settings(Settings(last_folder="/x"), path)
    assert load_settings(path) == Settings(last_folder="/x")
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_default_path():
    assert default_path().as_posix().endswith("photo-sorter/settings.json")


def test_face_people_default_and_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"include_subfolders": True, "use_gpu": False,
                                "last_folder": "/x"}))  # written before face_people existed
    assert load_settings(path).face_people == []
    save_settings(Settings(face_people=["ab12", "cd34"]), path)
    assert load_settings(path).face_people == ["ab12", "cd34"]


def test_bad_face_people_falls_back(tmp_path):
    path = tmp_path / "settings.json"
    for bad in ("ab12", ["ab12", 3], {"a": 1}, None):
        path.write_text(json.dumps({"face_people": bad}))
        assert load_settings(path).face_people == []


def test_event_defaults_and_old_file(tmp_path):
    assert (Settings().event_gap_hours, Settings().event_min_files) == (6, 10)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"include_subfolders": True, "face_people": []}))  # before events
    s = load_settings(path)
    assert (s.event_gap_hours, s.event_min_files) == (6, 10)
    save_settings(Settings(event_gap_hours=168, event_min_files=2), path)
    s = load_settings(path)
    assert (s.event_gap_hours, s.event_min_files) == (168, 2)


def test_bad_event_settings_fall_back(tmp_path):
    path = tmp_path / "settings.json"
    for gap, minimum in ((0, 1), ("6", "10"), (169, 1001), (True, True), (6.0, 10.5),
                         (None, [10])):
        path.write_text(json.dumps({"event_gap_hours": gap, "event_min_files": minimum}))
        s = load_settings(path)
        assert (s.event_gap_hours, s.event_min_files) == (6, 10)
    path.write_text(json.dumps({"event_gap_hours": 1, "event_min_files": 1000}))
    s = load_settings(path)
    assert (s.event_gap_hours, s.event_min_files) == (1, 1000)


DEFAULT_CATEGORIES = ["Food", "Pets & Animals", "Nature & Landscapes", "Selfies & People",
                      "Buildings & City", "Vehicles", "Events & Celebrations",
                      "Text & Documents"]


def test_content_categories_default_in_order():
    assert Settings().content_categories == DEFAULT_CATEGORIES
    assert list(DEFAULT_CONTENT_CATEGORIES) == DEFAULT_CATEGORIES


def test_content_categories_default_is_not_shared():
    first = Settings()
    first.content_categories.append("Mine")
    assert Settings().content_categories == DEFAULT_CATEGORIES


def test_content_categories_missing_from_an_old_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"include_subfolders": True, "face_people": ["ab12"]}))
    assert load_settings(path).content_categories == DEFAULT_CATEGORIES


def test_content_categories_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    mine = ["Meals", "জয়", "Documents"] + [f"c{i}" for i in range(27)]  # 30 names
    save_settings(Settings(content_categories=mine), path)
    assert load_settings(path).content_categories == mine
    save_settings(Settings(content_categories=["Food"]), path)
    assert load_settings(path).content_categories == ["Food"]


@pytest.mark.parametrize("bad", [
    [],
    ["", "x"],
    ["Food", "food"],  # the same folder on NTFS
    ["জয়", "জয়"],  # one name in two Unicode forms
    [f"c{i}" for i in range(31)],
    ["  ", "x"],
    ["...", "x"],
    "Food",
    ["Food", 3],
    None,
    ["Food", "Food."],  # the same folder once made Windows-valid
    ["a/b", "a:b"],  # both "a_b"
    ["Bad Photos", "_Bad Photos"],  # both "_Bad Photos"
    ["x" * 120 + "a", "x" * 120 + "b"],  # both cut to the same 100 characters
])
def test_bad_content_categories_fall_back(tmp_path, bad):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"content_categories": bad, "use_gpu": False}))
    loaded = load_settings(path)
    assert loaded.content_categories == DEFAULT_CATEGORIES
    assert loaded.use_gpu is False


def test_valid_category_list():
    assert valid_category_list(list(DEFAULT_CONTENT_CATEGORIES))
    assert valid_category_list(["Bad Photos"])  # its folder is "_Bad Photos"
    assert valid_category_list(["x" * 200])  # its folder is cut short
    assert not valid_category_list(["Food", "FOOD"])
    assert not valid_category_list(("Food",))  # JSON gives lists only


@pytest.mark.parametrize("name, others, problem", [
    ("Meals", ["Food", "Pets & Animals"], None),
    ("x" * 50, [], None),
    ("", [], "Type a name."),
    ("   ", ["Food"], "Type a name."),
    ("\t", [], "Type a name."),
    ("..", [], "Use at least one letter or number."),
    ("_", [], "Use at least one letter or number."),
    ("?", [], "Use at least one letter or number."),
    ("|", ["Food"], "Use at least one letter or number."),
    ("- & -", [], "Use at least one letter or number."),
    ("কলকাতা", [], None),
    ("7", [], None),
    ("food", ["Pets", "Food"], "There's already a category called Food."),
    ("Food.", ["Food"], "There's already a category called Food."),
    ("a:b", ["a/b"], "There's already a category called a/b."),
    ("x" * 51, [], "That name is too long."),
    ("Bad Photos", [], "Photo Sorter uses that name for its own folder. Pick another name."),
    ("duplicates ", [], "Photo Sorter uses that name for its own folder. Pick another name."),
    (".hidden", [], "Photo Sorter uses that name for its own folder. Pick another name."),
])
def test_category_problem(name, others, problem):
    assert category_problem(name, others) == problem


def test_file_date_roundtrips(tmp_path):
    path = tmp_path / "settings.json"
    assert Settings().file_date == "modified"
    save_settings(Settings(file_date="created"), path)
    assert load_settings(path).file_date == "created"


@pytest.mark.parametrize("bad", ["Created", "taken", "", 1, None, ["created"], {"a": 1}])
def test_a_bad_file_date_loads_as_modified(tmp_path, bad):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"file_date": bad, "use_gpu": False}))
    assert load_settings(path) == Settings(use_gpu=False, file_date="modified")
