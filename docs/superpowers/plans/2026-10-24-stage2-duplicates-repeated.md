# Stage 2: Duplicates + Repeated Shots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the two clean-up modes. **Duplicates** catches exact copies and resized or recompressed copies. **Repeated shots** catches near-identical photos taken seconds apart. Each has a "which ones to keep?" review screen, and repeated shots also get a full-screen viewer. Unkept files are moved to `Duplicates/` or `Repeated Shots/`, through the existing summary, Go and Undo flow.

**Architecture:** Each mode has a finder (`find_duplicate_groups`, `find_repeated_groups`) that returns `Group`s of `Candidate`s, each with a suggested keep set. A shared `plan_set_aside` turns the owner's keep choices into an ordinary stage-1 `Plan`, so the executor, journal and undo are reused unchanged. The UI gains a `ReviewPage` between Progress and Summary, plus a `FullscreenViewer` dialog. All tunable thresholds live in `photosort/thresholds.py`.

**Tech Stack:** Stage 1 stack, plus `numpy` and `imagehash` (both already in the local uv cache, so no big download is needed).

**Spec:** `docs/design.md`, sections 5.3 and 5.4, with sections 2, 4, 8 and 9. Stage 1 plan: `docs/superpowers/plans/2026-10-24-stage1-core-date.md`.

## Global Constraints

- Everything in stage 1's Global Constraints still holds: never delete, never overwrite, case-insensitive names, journal before move, relative POSIX paths, Windows-valid names, no symlinks, subfolders off by default, nothing moves before Go, copies only during development.
- **Nothing is deleted.** Unkept files go to `Duplicates/` (duplicates mode) or `Repeated Shots/` (repeated mode), placed **flat** (`Duplicates/<basename>`, clashes get ` (n)`) **(my call)**.
- **At least one file in every group is kept.** The UI won't confirm a group with nothing ticked, and `plan_set_aside` raises `ValueError` for an empty keep set.
- A group the owner **skips** moves nothing.
- The **Include subfolders** switch applies to these modes like any other. `Duplicates/`, `Repeated Shots/` and `Bad Photos/` are never scanned (the stage 1 scanner already does this).
- Duplicates: **exact copies** of photos *and videos*, plus **resized or recompressed copies** of photos. Near-identical shots taken at *different* moments are **not** duplicates; they belong to repeated shots.
- Duplicates pre-tick ranking: highest pixel count → has a date inside the file → larger file → sharper → first in path order.
- Repeated shots pre-tick: the **sharpest** photo.
- Review screens show for each file: thumbnail, file name, resolution, file size, date taken, sharpness label, and folder.
- Repeated-shots full-screen viewer: flip with the arrow keys, side-by-side view, zoom, and per-photo Keep. The choices are: **keep all**, **keep one**, or **keep any ticked number**.
- **All thresholds live in one module** (`photosort/thresholds.py`).
- Photos never leave the machine. No network use.

## Review Focus

1. **The owner unticks every photo in a group.** Confirm must be disabled and `plan_set_aside` must refuse, so a group's last copy can never be set aside. Tests in Task 2 and Task 4.
2. **Broken or unusual images in the folder** (zero-byte JPG, text renamed `.jpg`, PNG with alpha, HEIC). Finders must skip files they can't read and still group the rest, without crashing. Test in Task 2.
3. **Thousands of files.** Hash comparison must not be pure-Python O(n²): use numpy pairwise Hamming on uint64. Only files sharing a byte size get SHA-256 hashed. Test in Task 2: 3,000 random hashes pair in under 2 s.
4. **Rotated photos.** A camera photo with EXIF orientation 6 and its upright WhatsApp copy must still group. Resolution is reported after rotation. Tests in Task 1 and Task 2.
5. **Running the mode again after an earlier run.** Files already set aside in `Duplicates/` must not be found again. Test in Task 2.

---

## File Structure

```
src/photosort/thresholds.py            all tunables
src/photosort/imageinfo.py             open/size/phash/sharpness/thumbnail
src/photosort/dates.py                 + find_datetime, datetime_from_filename
src/photosort/groups.py                Candidate, Group, plan_set_aside, rank helpers, hamming pairs
src/photosort/modes/duplicates.py      find_duplicate_groups
src/photosort/modes/repeated.py        find_repeated_groups
src/photosort/modes/__init__.py        enable "duplicates", "repeated"
src/photosort/ui/review_page.py        ReviewPage, CandidateCard
src/photosort/ui/fullscreen_viewer.py  FullscreenViewer
src/photosort/ui/main_window.py        mode dispatch + review page wiring
tests/conftest.py                      + make_pattern_jpeg fixture
tests/test_imageinfo.py, test_groups.py, test_duplicates.py, test_repeated.py, test_ui_review.py, test_ui_viewer.py
```

---

### Task 1: Dependencies, thresholds, image info, precise dates

**Files:**
- Modify: `pyproject.toml`, `uv.lock`, `src/photosort/dates.py`, `tests/conftest.py`
- Create: `src/photosort/thresholds.py`, `src/photosort/imageinfo.py`, `tests/test_imageinfo.py`
- Test: `tests/test_dates.py` (additions)

**Interfaces:**
- Produces:
  - `thresholds`: `DUP_HASH_MAX = 6`, `DUP_ASPECT_TOL = 0.02`, `SAME_MOMENT_S = 2`, `REPEAT_WINDOW_S = 3`, `REPEAT_HASH_MAX = 12`, `SHARP_SLIGHTLY = 0.85`, `SHARP_BLURRY = 0.6`, `SHARPNESS_WIDTH = 512`, `THUMB_PX = 240`. These are all **(my call)** defaults.
  - `imageinfo.open_image(path: Path, max_px: int | None = None) -> PIL.Image.Image`: applies `ImageOps.exif_transpose`. When `max_px` is set, it uses JPEG `draft()` and then `thumbnail((max_px, max_px))`. Returns RGB.
  - `imageinfo.image_size(path) -> tuple[int, int]`: (width, height) after EXIF orientation, read without decoding pixels (orientations 5–8 swap).
  - `imageinfo.perceptual_hash(path) -> int`: 64-bit `imagehash.phash` of `open_image(path, 512)`, as an int.
  - `imageinfo.sharpness(path) -> float`: variance of a 4-neighbour Laplacian (numpy) on the grayscale image resized to `SHARPNESS_WIDTH` wide.
  - `imageinfo.thumbnail(path, max_px=THUMB_PX) -> PIL.Image.Image`, i.e. `open_image(path, max_px)`.
  - All imageinfo functions raise `imageinfo.UnreadableImage(Exception)` for any file Pillow can't read.
  - `dates.datetime_from_filename(name: str) -> datetime | None`: like `date_from_filename`, but only for names that also carry a time (`HHMMSS` right after the date, optional separator), or a 13-digit ms epoch. A date with no time gives `None`.
  - `dates.find_datetime(path: Path) -> datetime | None`: the metadata datetime (naive local; video datetimes are made naive local), else `datetime_from_filename`, else `None`.
  - `conftest.make_pattern_jpeg(path, seed: int, size=(800, 600), exif_dt: str | None = None, orientation: int | None = None, shift: int = 0, blur: float = 0.0, quality: int = 90) -> Path`: a seeded smooth random pattern (low-frequency noise upscaled, so resized copies hash alike). `shift` rolls the pattern by that many pixels, `blur` applies a GaussianBlur, and EXIF DateTimeOriginal and Orientation are written when given.

- [ ] **Step 1:** Run `uv add numpy imagehash` (try `--offline` first; the wheels are in the uv cache). Expected: `pyproject.toml` lists both, `uv sync` succeeds, and there is no large download.
- [ ] **Step 2: Write failing tests** in `tests/test_imageinfo.py`:
  - `test_image_size_respects_orientation`: an 800×600 pattern with orientation 6 gives `(600, 800)`.
  - `test_resized_copy_hashes_close`: seed 1 at 800×600 and the same seed saved at 400×300, quality 60, give a Hamming distance ≤ `DUP_HASH_MAX`.
  - `test_different_images_hash_far`: seeds 1 and 2 give a distance > `REPEAT_HASH_MAX`.
  - `test_sharpness_orders_blur`: `sharpness(blur=0) > sharpness(blur=3)`.
  - `test_unreadable_raises`: zero-byte `x.jpg` and a text file named `y.jpg` both raise `UnreadableImage`.
  - `test_thumbnail_bounded`: the result's max side is ≤ 240 and its mode is RGB, including for an RGBA PNG.
  - In `tests/test_dates.py`: `test_datetime_from_filename`. `IMG20250220181325.jpg` gives `datetime(2025,2,20,18,13,25)`. `IMG-20250315-WA0036.jpg` gives `None`. `PXL_20250220_181303123.jpg` gives `18:13:03`.
  - `test_find_datetime_prefers_metadata`.
- [ ] **Step 3:** Run `uv run pytest tests/test_imageinfo.py tests/test_dates.py -v`. Expected: the new tests FAIL.
- [ ] **Step 4: Implement** `thresholds.py`, `imageinfo.py`, the `dates.py` additions and the conftest fixture.
- [ ] **Step 5:** Run `uv run pytest -W error`. Expected: all pass, output pristine.
- [ ] **Step 6: Commit** `feat: image info, perceptual hash, sharpness, precise dates`.

### Task 2: Groups, set-aside planning, duplicates finder

**Files:**
- Create: `src/photosort/groups.py`, `src/photosort/modes/duplicates.py`, `tests/test_groups.py`, `tests/test_duplicates.py`

**Interfaces:**
- Consumes: `scanner.scan`, `media.kind`, `imageinfo.*`, `dates.find_datetime`, `thresholds`, `naming.TargetAllocator`, `plan.Move/Plan`.
- Produces:
  - `groups.Candidate` as `@dataclass(frozen=True)`: `rel: str, kind: Literal["photo","video"], width: int | None, height: int | None, size: int, taken: datetime | None, sharpness: float | None, note: str = ""`.
  - `groups.Group` as `@dataclass`: `kind: Literal["exact","resized","repeated"], members: list[Candidate], suggested_keep: set[str]` (rels).
  - `groups.sharpness_label(c: Candidate, group: Group) -> str`: `"best"` for the group's sharpest, `"slightly blurry"` when below `SHARP_SLIGHTLY`× the best, `"blurry"` when below `SHARP_BLURRY`× the best, otherwise `"sharp"`. `""` when sharpness is None.
  - `groups.best_copy(members) -> Candidate`: the duplicates ranking from Global Constraints.
  - `groups.sharpest(members) -> Candidate`.
  - `groups.close_pairs(hashes: Sequence[int], max_distance: int) -> list[tuple[int, int]]`: index pairs i<j with Hamming ≤ max. Numpy-vectorised (uint64 XOR + popcount by byte lookup), processed in row blocks so memory stays bounded.
  - `groups.plan_set_aside(root: Path, mode: str, folder: str, groups: list[Group], keep: dict[int, set[str]]) -> Plan`. Groups whose index isn't in `keep` move nothing. For each kept-index group, every member not in the keep set gets `Move(rel, allocate(f"{folder}/{basename}"))` from one `TargetAllocator(root)`. An empty keep set raises `ValueError`. Summary line 1 is exactly `f"{n} files → {folder}/ (from {g} groups)"`, and line 2 is exactly `f"Nothing is deleted. You can look through {folder}/ later."`.
  - `duplicates.find_duplicate_groups(root: Path, include_subfolders: bool, progress=None, cancel=None) -> list[Group] | None`:
    1. `scan`.
    2. Exact copies: group files by byte size, and SHA-256 only the size-collision files (photos and videos).
    3. For each readable photo, build a `Candidate` (size via `image_size`, `taken` via `find_datetime`, sharpness, phash).
    4. Resized links come from `close_pairs(hashes, DUP_HASH_MAX)`. A pair is kept only if the aspect ratios match within `DUP_ASPECT_TOL` and NOT (both have `taken` and they differ by more than `SAME_MOMENT_S` seconds).
    5. A union-find over exact and resized links gives the groups (size ≥ 2). `kind` is `"exact"` when all members share one SHA-256, otherwise `"resized"`. `suggested_keep = {best_copy(members).rel}`.
    6. `note` is `"Exact copy"` in exact groups. In resized groups it is `"Smaller copy (WhatsApp)"` for names matching `-WA\d+`, and `"Smaller copy"` for any non-best member with fewer pixels than the best.
    7. Progress is reported as `progress(i, total)` over files. `cancel` is checked per file and returns `None` when set. Unreadable photos are skipped for resized matching but still take part in exact matching. Videos get `width/height/sharpness=None`.
    8. Groups are sorted by their first member's rel.

- [ ] **Step 1: Write failing tests** in `test_groups.py`:
  - `test_close_pairs_small`: `[0b0, 0b1, 0b111111111]` with max 1 gives `[(0, 1)]`.
  - `test_close_pairs_fast`: 3,000 random uint64 values plus 5 planted near-twins. All planted pairs are found in under 2 s.
  - `test_best_copy_ranking`: a 4080×3060 file beats 1600×1200. On equal resolution, the one with a date wins. Then the larger file, then the sharper one.
  - `test_plan_set_aside_moves_unkept`: 2 groups, keep only for group 0. Only group 0's unkept files move, and they go to `Duplicates/<basename>`. Exact summary lines.
  - `test_plan_set_aside_clash`: two unkept `IMG_1.jpg` from different folders give `Duplicates/IMG_1.jpg` and `Duplicates/IMG_1 (1).jpg`.
  - `test_plan_set_aside_refuses_empty_keep` (Review Focus 1): `pytest.raises(ValueError)`.
- [ ] **Step 2: Write failing tests** in `test_duplicates.py` (real files via `make_pattern_jpeg`):
  - `test_exact_copies_grouped`: `a/x.jpg` and `b/x.jpg` with the same bytes, plus an unrelated photo, with subfolders on. One `"exact"` group of 2, note `"Exact copy"`.
  - `test_exact_video_copies_grouped`: two identical small mp4s via `make_mp4` give an exact group.
  - `test_resized_copy_grouped`: `IMG20250220181325.jpg` (800×600, EXIF 2025:02:20 18:13:25) and `IMG-20250220-WA0012.jpg` (400×300, q60, no EXIF). One `"resized"` group, suggested keep is the big one, and the WA note is set.
  - `test_burst_not_duplicate`: the same seed with shift=3 and EXIF times 2025:02:20 18:13:25 and 18:13:29 (4 s apart, same size) gives no duplicate group.
  - `test_rotated_camera_photo_and_upright_copy` (Review Focus 4): orientation 6 original (raw 800×600) and its copy saved upright at 300×400 give one group, and the reported resolution is `600×800`.
  - `test_broken_files_skipped` (Review Focus 2): a zero-byte jpg, text-as-jpg, an RGBA PNG and a valid pair. There is no exception, the pair is found, and the broken files are in no group.
  - `test_set_aside_files_not_found_again` (Review Focus 5): after executing the plan from `plan_set_aside`, running `find_duplicate_groups` again with subfolders on gives `[]`.
  - `test_cancel_returns_none`.
- [ ] **Step 3:** Run `uv run pytest tests/test_groups.py tests/test_duplicates.py -v`. Expected: FAIL.
- [ ] **Step 4: Implement** `groups.py` and `modes/duplicates.py`.
- [ ] **Step 5:** Run `uv run pytest -W error`. Expected: all pass.
- [ ] **Step 6: Commit** `feat: duplicates finder and set-aside planning`.

### Task 3: Repeated shots finder

**Files:**
- Create: `src/photosort/modes/repeated.py`, `tests/test_repeated.py`

**Interfaces:**
- Consumes: Task 1 and Task 2 interfaces.
- Produces:
  - `repeated.find_repeated_groups(root: Path, include_subfolders: bool, progress=None, cancel=None) -> list[Group] | None`. It considers readable photos that have a `taken` datetime.
    - Sort them by (taken, rel).
    - Walk in order. A photo joins the current chain when the gap to the previous photo is ≤ `REPEAT_WINDOW_S`, the phash distance to the previous photo is ≤ `REPEAT_HASH_MAX`, and the orientation class matches (landscape/portrait/square).
    - A pair with identical byte size and identical `taken` is treated as a copy, not a repeat (it is a duplicates-mode case), and does not link.
    - Chains of ≥ 2 become `Group("repeated", members, {sharpest(members).rel})`.
    - Progress and cancel work as in duplicates.

- [ ] **Step 1: Write failing tests** in `test_repeated.py`:
  - `test_burst_grouped`: 3 shots from seed 5 with shifts 0, 2 and 4, blur 0 / 2 / 0.5, and EXIF times 1 s apart. One group of 3, and suggested keep is the blur-0 one.
  - `test_far_in_time_not_grouped`: the same pattern 10 minutes apart gives `[]`.
  - `test_different_scene_breaks_chain`: shots 1 s apart but with different seeds give `[]`.
  - `test_no_date_ignored`: near-identical photos with no EXIF and undated names give `[]`.
  - `test_filename_time_used`: `IMG20250220181325.jpg` and `IMG20250220181326.jpg` with no EXIF and the same scene give one group.
  - `test_exact_copy_not_repeat`: identical bytes in two folders give `[]`.
- [ ] **Step 2:** Run `uv run pytest tests/test_repeated.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `modes/repeated.py`.
- [ ] **Step 4:** Run `uv run pytest -W error`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: repeated shots finder`.

### Task 4: Review page and main-window integration

**Files:**
- Create: `src/photosort/ui/review_page.py`, `tests/test_ui_review.py`
- Modify: `src/photosort/ui/main_window.py`, `src/photosort/modes/__init__.py` (set `available=True` for `"duplicates"` and `"repeated"`), `tests/test_ui_flow.py` (the "only Date available" test becomes "Date, Duplicates, Repeated shots available")

**Interfaces:**
- Consumes: `Group`, `Candidate`, `sharpness_label`, `plan_set_aside`, `find_duplicate_groups`, `find_repeated_groups`, `imageinfo.thumbnail`.
- Produces:
  - `review_page.CandidateCard(QFrame)` built as `CandidateCard(root: Path, candidate: Candidate, group: Group)`. It shows the thumbnail, or a grey box reading `"Video"` (or `"Can't show"` on `UnreadableImage`), and these detail lines:
    - basename
    - `"{w} × {h}"`
    - size in MB, one decimal (`"4.1 MB"`)
    - date `"20 Feb 2025, 18:13:25"`, or `"no date inside"`
    - `"Sharpness: {label}"`
    - note, if any
    - parent folder, with `"(top folder)"` for root
    
    It also has `keep_check: QCheckBox("Keep")`.
  - `review_page.ReviewPage(QWidget)` with `load(root: Path, mode: str, groups: list[Group])` and the signals `finished(object)` (emits `dict[int, set[str]]` of confirmed groups) and `cancelled()`.
    - Heading: `f"Group {i} of {n} · {KIND_LABEL[group.kind]}"`, where KIND_LABEL is `{"exact": "Exact copies", "resized": "Same photo, different size", "repeated": "Repeated shots"}`.
    - Subtitle: `"Tick the ones to keep. The others go to Duplicates/ (nothing is deleted)."`, or the same with `Repeated Shots/`.
    - Cards sit in a horizontal `QScrollArea`. Pre-ticks come from `suggested_keep`.
    - Buttons: `confirm_button` "Confirm & next group" (disabled while no card is ticked), `keep_all_button` ("Keep all (not duplicates)" in duplicates mode, "Keep all" in repeated mode), `keep_one_button` "Keep one (sharpest)" (repeated mode only), `skip_button` "Skip", `back_button` "Previous group" (disabled on group 1), `fullscreen_button` "Full screen…" (repeated mode; Task 5 wires it, so leave it hidden in this task), and `stop_button` "Stop reviewing" (emits `finished` with the groups confirmed so far).
    - Keep all records the group with every member kept, which moves nothing. Keep one ticks only the sharpest and confirms. Skip records nothing for that group and moves on. Previous goes back and keeps earlier answers editable.
    - After the last group it emits `finished(keep)`.
  - **Main window:**
    - `_start` dispatches on mode: `date` → as before. `duplicates` → `Task(find_duplicate_groups, root, include)`. `repeated` → `Task(find_repeated_groups, root, include)`.
    - Groups result `None` → Home. `[]` → Summary with `"No duplicates found."` / `"No repeated shots found."` and Go disabled. Otherwise → `review_page.load(...)`.
    - On `finished(keep)`: `plan_set_aside(root, mode, FOLDER[mode], groups, keep)` → the existing `_on_plan(plan)`.
    - On `cancelled` → Home.
    - `FOLDER = {"duplicates": "Duplicates", "repeated": "Repeated Shots"}`.
    - The journal mode is the plan's mode (`"duplicates"` or `"repeated"`).

- [ ] **Step 1: Write failing tests** in `test_ui_review.py` (pytest-qt, real images via `make_pattern_jpeg`, same fixture and teardown pattern as `test_ui_flow.py`):
  - `test_card_details`: a card for a 800×600 photo with EXIF shows `"800 × 600"`, `"20 Feb 2025, 18:13:25"` and `"Sharpness: best"`.
  - `test_confirm_disabled_when_nothing_ticked` (Review Focus 1).
  - `test_confirm_flow_emits_keep`: 2 groups. Confirm group 1 with the suggestion, skip group 2. `finished` gives `{0: {suggested}}`.
  - `test_keep_all_moves_nothing`: Keep all on a group gives keep = all its members.
  - `test_previous_group_keeps_answers`.
  - `test_keep_one_sharpest_repeated_mode`.
  - `test_video_card_placeholder`.
  - `test_duplicates_end_to_end` in `test_ui_flow.py`: a folder with an exact pair and a resized pair. Select Duplicates, Start, wait for the review page, confirm both groups, check the summary text, Go, Done. The unkept files are in `Duplicates/`. Undo restores them all and `Duplicates/` is gone.
  - `test_no_duplicates_found`: an unrelated photo folder gives a summary "No duplicates found." with Go disabled.
  - `test_three_modes_available`.
- [ ] **Step 2:** Run `uv run pytest tests/test_ui_review.py tests/test_ui_flow.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `uv run pytest -W error`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: review screen for duplicates and repeated shots`.

### Task 5: Full-screen viewer for repeated shots

**Files:**
- Create: `src/photosort/ui/fullscreen_viewer.py`, `tests/test_ui_viewer.py`
- Modify: `src/photosort/ui/review_page.py` (show and wire `fullscreen_button` in repeated mode)

**Interfaces:**
- Consumes: `Group`, `imageinfo.open_image`, `sharpness_label`.
- Produces:
  - `fullscreen_viewer.FullscreenViewer(QDialog)` built as `FullscreenViewer(root: Path, group: Group, keep: set[str], parent=None)`.
    - Images are shown in a `QGraphicsView`, loaded with `open_image(path, 2400)`.
    - `current_index`, `side_by_side: bool`, `zoom: float` (1.0 = fit to view).
    - Keys:
      - Left/Right: previous/next, wrapping.
      - `S`: toggle side-by-side (current and next shown together, scaled to fit).
      - `+` or `=`: zoom ×1.25. `-`: zoom ÷1.25. `0`: fit. The mouse wheel also zooms. Drag pans when zoomed.
      - Space: toggle Keep for the current photo.
      - Esc or Enter: close.
    - An overlay label reads `f"{i} of {n} · {basename} · Sharpness: {label} · {'KEEP' if kept else 'not kept'}"`.
    - A Keep checkbox is in the bottom bar.
    - `keep_result() -> set[str]`.
    - The dialog never lets the keep set become empty: un-keeping the last kept photo is refused, and the label flashes `"At least one photo must be kept."`.
  - Review page: `fullscreen_button` opens the viewer with the current ticks via `showFullScreen()` + `exec()`. After it closes, the card ticks are updated from `keep_result()`. Tests construct the viewer directly and send key events; they never call `exec()`.

- [ ] **Step 1: Write failing tests** in `test_ui_viewer.py`:
  - `test_arrows_flip_and_wrap`.
  - `test_space_toggles_keep`.
  - `test_cannot_unkeep_last`.
  - `test_zoom_keys_and_fit`: `+` then `0` brings zoom back to 1.0.
  - `test_side_by_side_toggle`.
  - `test_review_page_applies_viewer_result`: monkeypatch the viewer's `exec` to return immediately after setting keep. The cards reflect the change.
- [ ] **Step 2:** Run `uv run pytest tests/test_ui_viewer.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `uv run pytest -W error`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: full-screen viewer for repeated shots`.

### Task 6: Real-data check (controller)

- [ ] **Step 1:** On `~/PhotoSortTest/Camera` (a copy), run `find_duplicate_groups` and `find_repeated_groups` from a scratch script. Print the group counts and kinds, the time taken, and 5 sample groups with members.
- [ ] **Step 2:** Plan with the suggested keeps, execute, check that no group lost all members, undo, and confirm SHA-256 identical to `before.txt`.
- [ ] **Step 3:** Record the timings and results in the ledger. If either finder takes more than 60 s on 759 files, make a ruling on a cache or speed fix before calling stage 2 done.
