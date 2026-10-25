import json

import pytest

from photosort.settings import (
    DEFAULT_CONTENT_CATEGORIES,
    Settings,
    default_path,
    load_settings,
    save_settings,
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
])
def test_bad_content_categories_fall_back(tmp_path, bad):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"content_categories": bad, "use_gpu": False}))
    loaded = load_settings(path)
    assert loaded.content_categories == DEFAULT_CATEGORIES
    assert loaded.use_gpu is False
