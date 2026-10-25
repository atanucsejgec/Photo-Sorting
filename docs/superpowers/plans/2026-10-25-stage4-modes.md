# Stage 4: Camera, Type/source, Location, Bad photos, Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five modes that need no heavy AI:
- **Camera:** one folder per device.
- **Type / source:** Camera Photos, Screenshots, WhatsApp, Videos, Other.
- **Location:** `<Country>/<City>/`, looked up offline.
- **Bad photos:** blurry, dark, overexposed and accidental shots, reviewed and then set aside in `Bad Photos/`.
- **Events:** groups split by time gaps, reviewed, named, then each moved into its own folder.

All five go through the existing summary, Go, Done and Undo flow.

**Architecture:**
- **Metadata readers.** A small metadata layer (`metadata.py`, with `mp4meta.py` for videos) reads the camera make/model and GPS from photos (EXIF) and from MP4/MOV videos (ISO-BMFF atoms).
- **Plan modes.** Camera, Type/source and Location are kind `"plan"` modes. Each scans, classifies every file and returns a `Plan`, like Date mode.
- **Bad photos.** It measures every photo once (cached in `.photosort/cache.db`) and lists those that fail a quality rule, each with its reason. A new review page lets the owner untick photos to keep, then `plan_bad` sets the ticked ones aside, flat, in `Bad Photos/`.
- **Events.** It dates every file, cuts the timeline at gaps of `event_gap_hours`, and keeps runs of at least `event_min_files`. A new review page lets the owner name, merge, split or skip events, then `plan_events` moves each event into its own folder.

**Tech Stack:** The stage 3 stack, plus `reverse_geocode==1.6.6` (LGPL, a 3.5 MB wheel holding the GeoNames city list), which pulls in `scipy`. The lookup is offline, and nothing is sent anywhere.

**Spec:** `docs/design.md` §5.6 (Type/source), §5.8 (Bad photos), §5.9 (Events), §5.10 (Location) and §5.11 (Camera). These rest on §2, §4, §6, §8 and §9. The earlier plans are `2026-10-24-stage1-core-date.md`, `2026-10-24-stage2-duplicates-repeated.md` and `2026-10-24-stage3-face.md`.

**Measured by the controller before planning** (test copy `~/PhotoSortTest/Camera`, OPPO phone):
- The MP4s carry no make/model and no GPS. They have only `moov/meta` keys `com.android.version` and `com.android.capture.fps`, and `moov/udta` holds `titl`.
- Make/model and GPS for these videos are therefore found only on other phones. Apple and Samsung write `com.apple.quicktime.*` keys, `©mak`/`©mod`/`©xyz` in `udta`, or `com.android.manufacturer`/`com.android.model`.

## Global Constraints

- **Earlier rules still hold.** Everything in the stage 1–3 Global Constraints still applies:
  - never delete, never overwrite, journal before every move;
  - relative POSIX paths, case-insensitive and NFC folder matching via `naming.fold` / `resolve_existing_dir` / `TargetAllocator`;
  - Windows-valid names via `naming.safe_name`, no symlinks;
  - subfolders off by default, nothing moves before Go;
  - copies only during development; all tunables live in `photosort/thresholds.py`;
  - every new folder is created inside the picked folder.
- **Which modes handle videos (§4):**
  - Type/source, Events, Location and Camera handle photos and videos.
  - Bad photos skips videos.
- **No network** in any stage 4 mode. Location uses the bundled `reverse_geocode` data only.
- **Folder names made from data** (camera make/model, country, city, typed event names):
  - cleaned with `naming.safe_name`, then cut by `naming.fit_name(name, FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES)` (100 characters / 200 UTF-8 bytes; the same rule face folders use);
  - a name the scanner skips (`scanner.skipped_folder`) gets a `_` prefix;
  - an empty result is never used: such a file stays put.
- **Already in place:** a file whose parent folder equals its target folder (compared with `naming.fold`, the target resolved with `resolve_existing_dir`) counts as unchanged and is never moved. That avoids `x (1).jpg` churn on a rerun with subfolders on.
- **Plan summaries:** each line is fixed text, as given in the task. A count line is left out when its count is 0, unless the task says otherwise.
- **The set-aside folder** for Bad photos is exactly `Bad Photos`. It is already in `scanner.SKIP_DIRS`.
- **Event settings** (`Settings`):
  - `event_gap_hours: int = 6`, valid 1–168;
  - `event_min_files: int = 10`, valid 2–1000;
  - an out-of-range or wrong-typed value in an old settings file gives the default.

## Review Focus

1. **Rerun with subfolders on after the same mode.**
   - Camera, Type, Location and Events: a file already in its folder (for example `OPPO CPH2581/x.jpg`, `Screenshots/s.png`, `India/Kolkata/x.jpg`, `Goa trip/x.jpg`) is counted unchanged, never moved to a ` (1)` name.
   - Bad photos: files already in `Bad Photos/` are never scanned.
   - Tests: Tasks 2, 3, 4 and 5.
2. **Junk metadata never crashes a run, and the file stays put.** Cases:
   - EXIF Make with trailing NULs or spaces, an empty Model, or a Make/Model of bytes;
   - GPS `(0, 0)`, latitude > 90, or a zero-denominator rational;
   - a truncated MP4, an atom size of 0 or 1, or an atom size past the end of the file;
   - an unreadable file.

   Tests: Task 1.
3. **A name from data that isn't a valid folder.** Cases: `"CON"`, `"Bad Photos"`, `"a/b"`, `".hidden"`, a 300-character model string, a Bengali city name.
   - It is cleaned, never hidden, never a skipped folder, and fits 200 bytes.
   - Tests: Task 1 (`camera_folder`, `fit_name`) and Task 3 (place folder).
4. **Event edge cases.** Cases:
   - many files with the same timestamp;
   - undated files (they stay put);
   - Split leaving events below the minimum (allowed);
   - Merge across everything;
   - two events defaulting to the same `Event dd-mm-yyyy` name (each gets its own folder: `Event 20-02-2025`, `Event 20-02-2025 (2)`).

   Tests: Task 5.
5. **Extreme images in Bad photos.** Cases:
   - a uniform black, white or grey image (std 0: no NaN, no divide-by-zero);
   - a 1×1 image, a PNG with alpha, a 16-bit or palette PNG, a 10000×500 panorama.

   Each gets a reason or none, and never an exception. Tests: Task 4.

---

## File Structure

```
pyproject.toml / uv.lock              + reverse_geocode==1.6.6 (pulls scipy)
src/photosort/thresholds.py           + FOLDER_NAME_MAX(_BYTES), BAD_* limits
src/photosort/naming.py               + fit_name (moved from face_mode's _cut/_clean_cut)
src/photosort/mp4meta.py              NEW  ISO-BMFF tag reader for MP4/MOV/3GP
src/photosort/metadata.py             NEW  camera_of, gps_of (photos + videos), camera_folder
src/photosort/geo.py                  NEW  place_of(lat, lon) -> (country, city), lazy reverse_geocode
src/photosort/quality.py              NEW  measure_quality, bad_reason
src/photosort/settings.py             + event_gap_hours, event_min_files
src/photosort/modes/__init__.py       MODES flags; MODE_SPECS for camera/type/location/bad/events; kinds "bad", "events"
src/photosort/modes/camera_mode.py    NEW
src/photosort/modes/type_mode.py      NEW
src/photosort/modes/location_mode.py  NEW
src/photosort/modes/bad_photos.py     NEW  find_bad_photos, plan_bad
src/photosort/modes/events.py         NEW  Event, find_events, merge/split, default names, plan_events
src/photosort/ui/bad_photos_page.py   NEW  grid of bad photos with reasons, Move ticks
src/photosort/ui/photo_preview.py     NEW  one photo large (double-click on a card)
src/photosort/ui/events_page.py       NEW  events list: name, range, count, thumbnails, merge/split/skip
src/photosort/ui/settings_dialog.py   + event gap / minimum spin boxes
src/photosort/ui/main_window.py       wiring for kinds "bad" and "events"
README.md                             modes list (controller, at merge)
```

---

### Task 1: Metadata readers and folder-name fitting

**Files:**
- Create: `src/photosort/mp4meta.py`, `src/photosort/metadata.py`, `tests/test_mp4meta.py`, `tests/test_metadata.py`
- Modify: `src/photosort/naming.py`, `src/photosort/modes/face_mode.py` (use `fit_name`), `src/photosort/thresholds.py`, `tests/test_naming.py`

**Interfaces:**
- Produces:
  - `mp4meta.mp4_tags(path: Path) -> dict[str, str]`.
    - Reads ISO-BMFF boxes from the file by seeking: headers only, plus the payloads of the boxes below.
    - Covers `moov/udta` children `©xyz`, `©mak`, `©mod`, and `moov/meta` (`keys` + `ilst`) UTF-8 string values.
    - Keys: `"©xyz"`, `"©mak"`, `"©mod"`, or the reverse-DNS key name (e.g. `"com.apple.quicktime.make"`, `"com.android.model"`).
    - Returns `{}` for anything malformed or unreadable, and never raises.
    - It never reads more than `MP4_META_MAX = 4 MiB` of payload in total.
  - `metadata.camera_of(path: Path) -> tuple[str, str] | None`: `(make, model)`, each stripped of whitespace and NULs.
    - Photos: EXIF 271/272.
    - Videos: `com.apple.quicktime.make`/`model`, else `com.android.manufacturer`/`model`, else `©mak`/`©mod`.
    - Returns None when both are empty or unreadable.
  - `metadata.gps_of(path: Path) -> tuple[float, float] | None`: `(lat, lon)` in degrees.
    - Photos: the EXIF GPS IFD (0x8825), with refs N/S/E/W and rationals.
    - Videos: ISO 6709 from `©xyz` or `com.apple.quicktime.location.ISO6709`.
    - Returns None when unreadable, out of range (|lat| > 90, |lon| > 180), or exactly (0, 0).
  - `metadata.camera_folder(make: str, model: str) -> str` follows these rules:
    - If fold(model) starts with fold(make), use the model alone. Otherwise use `f"{make} {model}"`, or whichever part is non-empty.
    - Then clean the result with `safe_name`, then `fit_name`, then add the `_` prefix if it is a skipped folder.
    - Example: `("OPPO", "CPH2581")` gives `"OPPO CPH2581"`, and `("Apple", "Apple iPhone 12")` gives `"Apple iPhone 12"`.
  - `naming.fit_name(name: str, max_chars: int, max_bytes: int) -> str`:
    - It is `face_mode`'s cut logic, moved here: cut at code points and strip a trailing mark, dot or space.
    - It then runs `safe_name`, and adds the `_` prefix when `scanner.skipped_folder`.
    - `face_mode.together_folder` uses it, with no behaviour change, so the existing face tests stay green.
  - `thresholds.FOLDER_NAME_MAX = 100` and `FOLDER_NAME_MAX_BYTES = 200`. `FACE_FOLDER_MAX`/`_BYTES` stay as aliases.

- [ ] **Step 1: Write failing tests:**
  - `mp4meta`, on bytes written in tmp_path:
    - a minimal `ftyp`+`moov/udta/©xyz` gives `{"©xyz": "+22.5726+088.3639/"}`;
    - `moov/meta` with `hdlr`+`keys`+`ilst` holding `com.apple.quicktime.make = "Apple"` reads that key;
    - the real OPPO layout (keys `com.android.version`, `com.android.capture.fps`, `udta/titl`) gives no make/model;
    - a size-0 atom, a size-1 atom with a 64-bit size, an atom running past EOF, and a truncated file all give `{}` or partial data, never an exception.
  - `camera_of`:
    - a JPEG with EXIF Make `"OPPO\x00"` and Model `" CPH2581 "` gives `("OPPO", "CPH2581")`;
    - a JPEG without EXIF gives None;
    - an MP4 with the Apple keys gives `("Apple", "iPhone 12")`.
  - `gps_of`:
    - a JPEG with GPS 22°34'21.4"N 88°21'50.0"E gives approximately (22.5726, 88.3639);
    - an S/W ref gives negative values;
    - (0, 0) gives None, a zero denominator gives None, and lat 95 gives None;
    - an MP4 `©xyz "-33.8688+151.2093/"` gives (-33.8688, 151.2093).
  - `camera_folder`:
    - the two examples above;
    - `("", "Pixel 7")` gives `"Pixel 7"`;
    - `("CON", "")` gives a name that is Windows-valid (`safe_name(r) == r`, and `fold(r) != "con"`);
    - `("Bad", "Photos")` gives `"_Bad Photos"`;
    - a 300-character model fits within 200 bytes.
  - `fit_name`: the Bengali, `"অ"*100` and `"Duplicates"+" "*95+"x"*20` cases moved from `test_face_mode.py`.
- [ ] **Step 2:** Run `uv run pytest tests/test_mp4meta.py tests/test_metadata.py tests/test_naming.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `uv run pytest -W error`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: read camera and GPS from photos and videos`.

---

### Task 2: Camera and Type/source modes

**Files:**
- Create: `src/photosort/modes/camera_mode.py`, `src/photosort/modes/type_mode.py`, `tests/test_camera_mode.py`, `tests/test_type_mode.py`
- Modify: `src/photosort/modes/__init__.py`, `tests/test_ui_flow.py`, `tests/test_date_mode.py` (`test_modes_list`)

**Interfaces:**
- Consumes: Task 1's `camera_of` and `camera_folder`; `naming.TargetAllocator`, `resolve_existing_dir` and `fold`; `scanner.scan`; `media.kind`; `imageinfo.image_size`.
- Produces:
  - `camera_mode.prepare_camera_plan(root, include_subfolders, progress=None, cancel=None) -> Plan | None`, with mode `"camera"`.
    - Summary lines: `f"{n} files → {d} camera folders"`, `f"{s} files have no camera info and stay where they are"`, `f"{k} already in place"`.
  - `type_mode.source_of(path: Path) -> str`, one of `CAMERA_PHOTOS = "Camera Photos"`, `SCREENSHOTS = "Screenshots"`, `WHATSAPP = "WhatsApp"`, `VIDEOS = "Videos"`, `OTHER = "Other"`. Rules, first match wins **(my call)**:
    1. The basename matches `-WA\d+` (case-insensitive): WhatsApp, for photos and videos.
    2. A photo whose basename, casefolded with spaces/`_`/`-` removed, contains `"screenshot"`; or a `.png` photo with no camera make/model: Screenshots.
    3. Any other video: Videos.
    4. A photo with a camera make or model (`camera_of`): Camera Photos.
    5. Anything else, including unreadable photos: Other.
  - `type_mode.prepare_type_plan(...) -> Plan | None`, with mode `"type"`.
    - Summary: `f"{n} files → " + ", ".join(f"{folder} {count}" for folders with count > 0, in the order Camera Photos, Screenshots, WhatsApp, Videos, Other)`, plus `f"{k} already in place"`.
  - `MODE_SPECS["camera"]` and `MODE_SPECS["type"]` are kind `"plan"`, and `MODES` marks both available.

- [ ] **Step 1: Write failing tests** (files built in tmp_path with Pillow EXIF and Task 1's MP4 helper):
  - Camera:
    - two OPPO photos and one Pixel photo go to `OPPO CPH2581/` ×2 and `Pixel 7/` ×1;
    - a photo with no EXIF stays, with summary line `"1 files have no camera info and stay where they are"`;
    - an MP4 with Apple keys goes to `Apple iPhone 12/`;
    - rerun with subfolders on: `OPPO CPH2581/a.jpg` counts as unchanged;
    - an existing folder `oppo cph2581/` (different case) is reused.
  - Type:
    - `IMG-20250315-WA0036.jpg` goes to WhatsApp;
    - `VID-20250315-WA0001.mp4` goes to WhatsApp;
    - `Screenshot_2025-03-01-10-00-00.png` goes to Screenshots;
    - `Screen Shot 2025.jpg` (no EXIF) goes to Screenshots;
    - `x.png` with no EXIF goes to Screenshots;
    - `VID20250627.mp4` goes to Videos;
    - a JPEG with Make goes to Camera Photos;
    - a JPEG with no EXIF goes to Other;
    - an unreadable `.jpg` goes to Other;
    - the summary text is exact;
    - a rerun leaves all of them unchanged.
  - UI flow: Camera → Start gives the summary, Go moves, Undo restores. Same for Type.
  - `test_modes_list`: date, face, duplicates, repeated, type and camera are available.
- [ ] **Step 2:** Run `uv run pytest tests/test_camera_mode.py tests/test_type_mode.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Model both modes on `date_mode.prepare_date_plan`: progress after each file, cancel before each.
- [ ] **Step 4:** Run `uv run pytest -W error`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: camera and type/source modes`.

---

### Task 3: Location mode

**Files:**
- Create: `src/photosort/geo.py`, `src/photosort/modes/location_mode.py`, `tests/test_geo.py`, `tests/test_location_mode.py`
- Modify: `pyproject.toml`, `uv.lock`, `src/photosort/modes/__init__.py`, `tests/test_ui_flow.py`, `tests/test_date_mode.py`

**Interfaces:**
- Consumes: Task 1's `gps_of` and `fit_name`.
- Produces:
  - `geo.place_of(lat: float, lon: float) -> tuple[str, str] | None` gives `(country, city)`.
    - It uses `reverse_geocode`'s nearest city.
    - `reverse_geocode` is imported inside the function on first use, so app start and the other modes never load it.
    - Returns None if the lookup fails or gives an empty country.
  - `location_mode.place_folder(country: str, city: str) -> str` gives `f"{c}/{t}"`. Each part goes through `fit_name`. An empty city gives the country alone.
  - `location_mode.prepare_location_plan(...) -> Plan | None`, with mode `"location"`. Summary:
    - `f"{n} files → {c} countries, {t} cities"`;
    - `f"{m} files have no location and stay where they are"`, shown **whenever m > 0** (the §5.10 warning before Go);
    - `f"{k} already in place"`.
  - `MODE_SPECS["location"]` is kind `"plan"`, and it is available.

- [ ] **Step 1:** Add `reverse_geocode==1.6.6` with `uv add`. Tell the controller the download size in the report. Check that `uv run python -c "import reverse_geocode; print(reverse_geocode.get((22.5726, 88.3639)))"` gives Kolkata, India. Record the exact returned keys in the report.
- [ ] **Step 2: Write failing tests:**
  - `place_of(22.5726, 88.3639)` gives `("India", "Kolkata")`.
  - `place_of(48.8566, 2.3522)` gives `("France", "Paris")`.
  - Location mode, with JPEGs carrying GPS in tmp_path:
    - two Kolkata photos and one Paris photo go to `India/Kolkata/` ×2 and `France/Paris/`;
    - a photo without GPS stays, with the warning line exact;
    - a rerun with subfolders on leaves `India/Kolkata/a.jpg` unchanged;
    - a monkeypatched `place_of` returning `("CON", "a/b")` gives a valid folder;
    - a monkeypatched `place_of` returning a Bengali city name works.
  - `test_location_loads_geocoder_lazily`: a subprocess running `import photosort.ui.main_window` does not have `reverse_geocode` in `sys.modules`.
  - UI flow: Location → summary → Go → Undo.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `uv run pytest -W error`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: location mode with an offline city lookup`.

---

### Task 4: Bad photos

**Files:**
- Create: `src/photosort/quality.py`, `src/photosort/modes/bad_photos.py`, `src/photosort/ui/bad_photos_page.py`, `src/photosort/ui/photo_preview.py`, `tests/test_quality.py`, `tests/test_bad_photos.py`, `tests/test_ui_bad.py`
- Modify: `src/photosort/thresholds.py`, `src/photosort/modes/__init__.py`, `src/photosort/ui/main_window.py`, `tests/test_ui_flow.py`, `tests/test_date_mode.py`

**Interfaces:**
- Consumes: `imageinfo._decode`, made public as `imageinfo.decode(path, min_px)`; `AnalysisCache`; `groups.Candidate`; `dates.find_datetime`; `naming.TargetAllocator`.
- Produces:
  - `quality.Quality`, a frozen dataclass with `sharpness`, `mean`, `p95`, `std` and `bright_frac`, all floats. Measured on the greyscale photo decoded at `SHARPNESS_WIDTH` (512) wide:
    - `sharpness` is the same Laplacian variance as `imageinfo`;
    - `mean`, `p95` and `std` are of the 0–255 grey values;
    - `bright_frac` is the fraction of pixels ≥ 250.
  - `quality.measure_quality(path, cache=None) -> Quality | None`.
    - Cache kind `"quality-v1"`, packed with `struct "<5d"`.
    - Returns None (and caches nothing) for an unreadable photo.
  - `quality.bad_reason(q: Quality) -> str | None`. The first rule that matches wins **(my call; tuned in Task 6)**:
    1. `q.std < BAD_FLAT_STD` (12) gives `"accidental shot"`;
    2. `q.mean < BAD_DARK_MEAN` (35) and `q.p95 < BAD_DARK_P95` (100) gives `"too dark"`;
    3. `q.bright_frac > BAD_BRIGHT_FRAC` (0.5) or `q.mean > BAD_BRIGHT_MEAN` (235) gives `"too bright"`;
    4. `q.sharpness < BAD_BLUR` (25.0) gives `"blurry"`;
    5. otherwise None.
  - `bad_photos.find_bad_photos(root, include_subfolders, progress=None, cancel=None) -> list[Candidate] | None`:
    - photos only;
    - each Candidate has `note=reason`, `sharpness=q.sharpness`, `width`/`height` from `image_size`, the file size, and `taken`;
    - the list is sorted by rel.
  - `bad_photos.plan_bad(root, bad: list[Candidate], move: set[str]) -> Plan`:
    - mode `"bad"`; each rel in `move` goes to `Bad Photos/<basename>`, given a free name by `TargetAllocator`;
    - the folder is resolved with `resolve_existing_dir`;
    - summary: `f"{n} photos → Bad Photos"` and `f"{s} photos stay where they are"`, where `s = len(bad) - n`.
  - `MODE_SPECS["bad"] = ModeSpec("bad", _find_bad, "Bad Photos", "No bad photos found.")`. The kind Literal gains `"bad"`, and the mode is available.
  - `ui.bad_photos_page.BadPhotosPage(QWidget)`:
    - `load(root, bad: list[Candidate], move: set[str] | None = None)`.
    - A scrollable grid of cards. Each card has a thumbnail (`THUMB_PX`, made off-thread with a generation token, as in `review_page`), the file name, the reason, the folder, and a `"Move to Bad Photos"` tick, ticked by default.
    - Buttons: `tick_all_button` "Tick all", `untick_all_button` "Untick all", `back_button` "Back", and `continue_button` "Continue".
    - Signals: `finished(set)` with the ticked rels, and `cancelled()`.
    - Double-clicking a thumbnail opens `PhotoPreview`.
  - `ui.photo_preview.PhotoPreview(QDialog)`: `PhotoPreview(path, parent)` shows the photo up to 1600 px, fitted to the window and loaded off-thread. Esc closes it.
  - Main window flow:
    - kind `"bad"`: progress "Checking photos… i of n";
    - an empty list gives the summary with `none_found` and Go disabled;
    - otherwise the page; `finished` → `plan_bad` → summary;
    - Back on the summary returns to the page with the ticks kept;
    - `OSError` while planning goes Home and calls `show_error`.

- [ ] **Step 1: Write failing tests:**
  - `bad_reason`:
    - all-black gives "accidental shot";
    - mean 20 / p95 80 / std 15 gives "too dark";
    - bright_frac 0.6 gives "too bright";
    - sharpness 10 with normal light gives "blurry";
    - a normal photo gives None.
  - `measure_quality` on generated images:
    - a uniform grey 64×64 gives std 0 and no NaN;
    - a 1×1 image gives a Quality, with no exception;
    - a 10000×500 panorama works;
    - an RGBA PNG and a palette PNG are measured;
    - an unreadable file gives None and is not cached;
    - a second call with the cache reads the cache (`decode` is monkeypatched to fail).
  - `find_bad_photos`:
    - a black JPEG, a Gaussian-blurred noise JPEG, a sharp noise JPEG and an MP4 give the black and blurred photos only, with their reasons;
    - files already in `Bad Photos/` are never listed, even with subfolders on.
  - `plan_bad`: a name clash in `Bad Photos/` gets ` (1)`, and an existing `bad photos/` (different case) is reused.
  - UI:
    - the page shows one card per photo, with every tick on;
    - "Untick all" and then Continue emits an empty set, and the summary shows 0 with Go disabled;
    - double-click opens the preview;
    - `test_bad_photos_end_to_end`: Bad → Start → page → Continue → summary → Go → the files are in `Bad Photos/`; Undo restores them and the folder is gone;
    - Back on the summary returns to the page with the ticks kept.
- [ ] **Step 2:** Run `uv run pytest tests/test_quality.py tests/test_bad_photos.py tests/test_ui_bad.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `uv run pytest -W error`, and run the UI test files 3×. Expected: all pass.
- [ ] **Step 5: Commit** `feat: bad photos mode with a review grid`.

---

### Task 5: Events

**Files:**
- Create: `src/photosort/modes/events.py`, `src/photosort/ui/events_page.py`, `tests/test_events.py`, `tests/test_ui_events.py`
- Modify: `src/photosort/settings.py`, `src/photosort/ui/settings_dialog.py`, `src/photosort/modes/__init__.py`, `src/photosort/ui/main_window.py`, `tests/test_settings.py`, `tests/test_ui_flow.py`, `tests/test_ui_dialogs.py`, `tests/test_date_mode.py`

**Interfaces:**
- Consumes: `dates.find_datetime`; `naming.fit_name`, `TargetAllocator`, `resolve_existing_dir` and `fold`; `date_mode.MONTH_NAMES`; `imageinfo.thumbnail`.
- Produces:
  - `events.Event`, a frozen dataclass:
    - `files: tuple[str, ...]`, sorted by taken time, then rel;
    - `times: tuple[datetime, ...]`, aligned with `files`;
    - `start` and `end` properties.
  - `events.find_events(root, include_subfolders, gap_hours: int, min_files: int, progress=None, cancel=None) -> tuple[list[Event], int] | None`:
    - returns the events, then the number of files left in no event (undated, or in runs below `min_files`);
    - photos and videos are included, and a file with no datetime is never in an event;
    - a new event starts when the gap to the previous file is **strictly more than** `gap_hours` hours.
  - `events.merge(a: Event, b: Event) -> Event`.
  - `events.split(e: Event) -> tuple[Event, Event]`:
    - cuts at the largest gap between neighbouring files, choosing the earliest such gap on a tie;
    - raises `ValueError` for an event with fewer than 2 files or with all times equal.
  - `events.default_names(events: list[Event]) -> list[str]`:
    - each name is `f"Event {start:%d-%m-%Y}"`;
    - later events whose names repeat get ` (2)`, ` (3)` in order.
  - `events.plan_events(root, events: list[Event], names: list[str], skipped: set[int], left_out: int) -> Plan`:
    - mode `"events"`; event i's files go to `fit_name(names[i])/<basename>` unless i is in `skipped`;
    - a blank typed name uses the default name;
    - two events given the same folder name share that folder;
    - summary: `f"{n} files → {e} event folders"`, `f"{s} files stay where they are"` (left_out plus the skipped events' files) and `f"{k} already in place"`.
  - `MODE_SPECS["events"] = ModeSpec("events", _find_events, None, "No events found.")`:
    - `prepare` takes `gap_hours` and `min_files` keyword arguments, which the main window fills from `Settings`;
    - the mode is available.
  - `ui.events_page.EventsPage(QWidget)`, `load(root, events, left_out)`, one row per event:
    - a name `QLineEdit` with the default name as placeholder;
    - a date range: `"20 Feb 2025"` for one day, `"20 Feb 2025 – 22 Feb 2025"` for several, with English months;
    - `f"{n} files"`;
    - up to 4 thumbnails, made off-thread with a generation token;
    - a `"Merge with next"` button (disabled on the last row), a `"Split"` button (disabled when `split` would raise), and a `"Skip"` tick.
    - Merging and splitting keep typed names: a merged row keeps the first row's name, and the split halves keep the name on the first half only.
    - Buttons: `back_button` "Back" and `continue_button` "Continue".
    - Signals: `finished(list, list, set)` (events, names, skipped) and `cancelled()`.
  - Settings dialog:
    - `"New event after a gap of"` [spin 1–168] `"hours"`;
    - `"An event needs at least"` [spin 2–1000] `"files"`;
    - both are saved like the other settings.
  - Main window flow:
    - kind `"events"`: progress "Dating files… i of n";
    - no events gives the summary with `none_found` and Go disabled;
    - otherwise the page; `finished` → `plan_events` → summary;
    - Back on the summary returns to the page with the edits kept.

- [ ] **Step 1: Write failing tests:**
  - `find_events` (monkeypatched `find_datetime`):
    - 12 files within 2 h, a 7 h gap, then 3 files, then a 7 h gap, then 10 files: events of 12 and 10, with 3 left out;
    - a gap of exactly 6 h is not a new event;
    - undated files are counted left out;
    - 20 files with the same timestamp make one event.
  - `split`:
    - times 0, 1, 10 and 11 h split into [0, 1] and [10, 11];
    - equal times raise.
  - `merge`: keeps the time order.
  - `default_names`: two events starting 20-02-2025 give `"Event 20-02-2025"` and `"Event 20-02-2025 (2)"`.
  - `plan_events`:
    - a typed name `"Goa / trip."` gives a cleaned folder;
    - skipped events stay;
    - a rerun with subfolders on leaves files in `Goa trip/` unchanged;
    - the summary text is exact.
  - Settings:
    - defaults are 6 and 10;
    - an old file without these fields loads the defaults;
    - `event_gap_hours: 0` and `"6"` both give 6.
  - UI:
    - the rows show range, count and placeholder;
    - Merge with next joins the rows and the count;
    - Split splits the row;
    - the Skip tick works;
    - `test_events_end_to_end` (monkeypatched dates, real moves in tmp_path): Events → Start → page → name one event → Continue → summary → Go → the files are in `Goa trip/` and `Event 20-02-2025/`; Undo restores them and both folders are gone;
    - Back on the summary returns to the page with the names kept;
    - the settings dialog spin boxes save.
- [ ] **Step 2:** Run `uv run pytest tests/test_events.py tests/test_ui_events.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `uv run pytest -W error`, and run the UI test files 3×. Expected: all pass.
- [ ] **Step 5: Commit** `feat: events mode with naming, merging and splitting`.

---

### Task 6: Real-data check (controller)

- [ ] **Step 1:** On `~/PhotoSortTest/Camera` (a copy), from a scratch script, run each new mode's finder or planner. Record the time, the counts and the summary lines.
  - **Bad photos:** look at every flagged photo (contact sheet), and at 20 random unflagged ones.
  - **Tuning rule:** if more than 25 % of the flagged photos are fine photos, or an obviously bad one is missed, re-tune `BAD_*` in `thresholds.py`. Record the ruling, then rerun.
- [ ] **Step 2:** For every mode with moves, plan, execute and undo. Check that the SHA-256 of every file matches a snapshot taken just before. Do not undo the owner's earlier runs on the copy.
- [ ] **Step 3:** Record everything in the ledger. Deploy to `~/PhotoSorter` (move the worktree to the new commit; never a new path).
