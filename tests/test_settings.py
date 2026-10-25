import json

from photosort.settings import Settings, default_path, load_settings, save_settings


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
