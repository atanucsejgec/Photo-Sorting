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
