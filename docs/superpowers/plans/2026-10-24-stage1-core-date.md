# Stage 1: Core + Date Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working desktop app that sorts a picked folder by date, with a summary-before-Go, safe moves that never overwrite, and full Undo history. This is the base every later mode builds on.

**Architecture:** The core is pure Python with no Qt dependency: scan → analyse → plan (a list of `Move`s) → execute (journalled moves) → undo (replay the journal backwards). All stored paths are POSIX strings relative to the picked folder. The Qt UI (PySide6) is a thin layer that runs core functions on a worker thread and shows Home → Progress → Summary → Done pages. Later stages add new `plan_*` functions and review pages, and reuse scanner, executor, journal and undo unchanged.

**Tech Stack:** Python 3.12 (already installed via uv), uv, PySide6-Essentials, Pillow, pillow-heif, hachoir (pure-Python video metadata), platformdirs, pytest, and pytest-qt.

**Spec:** `docs/design.md`. Read sections 2, 4, 5.1, 6, 7 and 8 before starting.

**Scope:** Stage 1 of 5 (design §7). Stages 2–5 get their own plans. AppImage packaging and the CUDA download (design §3) belong to a final packaging plan, written after stage 5. In stage 1 the app runs through a launcher script.

## Global Constraints

- The app **never deletes** a photo or video, and **never overwrites**. If the target exists, add ` (1)`, ` (2)` and so on. Name comparison is case-insensitive, because the owner's drive is NTFS.
- Nothing moves until the owner has seen the summary and pressed **Go**.
- Every move is recorded in the journal **before** it happens. A crashed or cancelled run must be fully undoable.
- Undo records live in `<picked folder>/.photosort/`. Paths are stored **relative to the picked folder** as POSIX strings.
- Folder and file names must be valid on Windows/NTFS: no `\ / : * ? " < > |`, no trailing dots or spaces, no reserved names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`).
- No symlinks or shortcuts in the output.
- **Include subfolders** is **off by default**. The last value is remembered. `.photosort`, `Duplicates`, `Repeated Shots` and `Bad Photos` are always skipped.
- Subfolders left empty after a run are removed. Undo recreates them.
- Use `pathlib` and `platformdirs`. Never hard-code `/` in stored data.
- **Development and testing must use copies**, never the owner's real folders.
- Heavy work runs off the UI thread.
- Photos: `.jpg .jpeg .png .heic .heif .webp`. Videos: `.mp4 .mov .3gp .mkv`. Extensions are case-insensitive.
- **Large downloads:** tell the owner before any big download, and tell them when fast internet is no longer needed (see Task 1, Step 1).

## Review Focus

1. **Two files with the same name going to the same folder**, including names that differ only by case (`IMG_1.jpg` and `img_1.JPG` from different subfolders). Expect the second one to become `img_1 (1).JPG`. Test in Task 4.
2. **The app crashes or is cancelled mid-run.** The journal has an intent with no "done" mark, and the file may or may not have moved. Undo should put back everything that moved and not complain about the rest. Test in Task 6.
3. **The owner changes things by hand between a run and Undo.** A moved file got deleted, or a new file now sits at the original path. Undo should skip and report the missing file, and restore the other one as `name (1).ext` without overwriting anything. Test in Task 6.
4. **Broken files**: zero-byte files, or text files renamed to `.jpg`. The date read must not raise. It should fall back to the filename, then to `No Date`, and the run should carry on. Test in Task 2.
5. **Junk dates inside files**: EXIF `0000:00:00 00:00:00`, future dates, and video creation time `1904-01-01` (the MP4 zero epoch). Expect them to be rejected, with the next date source used instead. Test in Task 2.

---

## File Structure

```
pyproject.toml
scripts/photo-sorter.sh                 launcher
scripts/install-desktop-entry.sh        adds an app-menu entry
src/photosort/__init__.py
src/photosort/app.py                    main()
src/photosort/media.py                  file kinds
src/photosort/scanner.py                folder scanning
src/photosort/dates.py                  date from metadata / filename
src/photosort/naming.py                 Windows-safe names, TargetAllocator
src/photosort/plan.py                   Move, Plan
src/photosort/journal.py                undo records on disk
src/photosort/executor.py               safe moves
src/photosort/undo.py                   undo + blocking rule
src/photosort/settings.py               Settings load/save
src/photosort/modes/__init__.py         MODES registry
src/photosort/modes/date_mode.py        date planning
src/photosort/ui/worker.py              background task runner
src/photosort/ui/main_window.py         pages: Home/Progress/Summary/Done
src/photosort/ui/history_dialog.py
src/photosort/ui/settings_dialog.py
tests/conftest.py                       fixtures: make_jpeg, make_mp4, offscreen Qt
tests/test_*.py                         one per module
```

---

### Task 1: Project skeleton, media kinds, scanner

**Files:**
- Create: `pyproject.toml`, `src/photosort/__init__.py`, `src/photosort/media.py`, `src/photosort/scanner.py`, `tests/conftest.py`, `tests/test_scanner.py`

**Interfaces:**
- Produces:
  - `media.PHOTO_EXTS: frozenset[str]`, `media.VIDEO_EXTS: frozenset[str]` (lowercase, with the dot)
  - `media.kind(path: str | Path) -> Literal["photo", "video"] | None`
  - `scanner.SKIP_DIRS: frozenset[str] = {".photosort", "duplicates", "repeated shots", "bad photos"}` (compared with `casefold()`)
  - `scanner.scan(root: Path, include_subfolders: bool) -> list[str]`: sorted, POSIX, relative to root, media files only. Skips: hidden files and dirs (names starting with `.`, for example Android `.trashed-*`), symlinks, and any dir whose casefolded name is in `SKIP_DIRS`, at any depth.
  - `tests/conftest.py` fixtures: `make_jpeg(path: Path, exif_dt: str | None = None, size=(16, 16)) -> Path` writes a real JPEG with EXIF DateTimeOriginal (tag 36867 in IFD 0x8769) when given. `make_mp4(path: Path, creation_time: str | None) -> Path` uses `ffmpeg -f lavfi -i color=c=black:s=16x16:d=0.1 [-metadata creation_time=...]`, and the fixture calls `pytest.skip` when ffmpeg is missing. Also sets `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")`.

- [ ] **Step 1: Download checkpoint.** Tell the owner: "Next step downloads about 150 MB of Python packages (mostly Qt). Go to fast internet and tell me when you're there." Wait for their reply.
- [ ] **Step 2: Create `pyproject.toml`**: name `photo-sorter`, version `0.1.0`, `requires-python = ">=3.12,<3.13"`. Dependencies: `PySide6-Essentials>=6.8`, `Pillow>=11`, `pillow-heif>=0.20`, `hachoir>=3.3`, `platformdirs>=4`. Dev group: `pytest>=8`, `pytest-qt>=4.4`. Script `photo-sorter = "photosort.app:main"`. Use the hatchling build with a src layout. Add `[tool.pytest.ini_options] testpaths = ["tests"]` and `qt_api = "pyside6"`. Run `uv sync --python 3.12`. Expected: it finishes with no errors and creates `.venv/`. Then tell the owner the downloads for stage 1 are done and fast internet isn't needed.
- [ ] **Step 3: Write failing tests** in `tests/test_scanner.py`:
  - `test_scan_top_level_only`: `root/a.jpg`, `root/sub/b.JPG`, `root/notes.txt`. `scan(root, False) == ["a.jpg"]`.
  - `test_scan_with_subfolders`: same tree. `scan(root, True) == ["a.jpg", "sub/b.JPG"]`.
  - `test_scan_skips_app_folders_any_depth`: files in `Duplicates/`, `sub/bad photos/`, `.photosort/` and `Repeated Shots/` are all excluded with `include_subfolders=True`.
  - `test_scan_skips_hidden_and_symlinks`: `.trashed-1.jpg` and a symlink `link.jpg` are excluded.
  - `test_kind`: `kind("x.HEIC") == "photo"`, `kind("v.3gp") == "video"`, `kind("a.txt") is None`.
- [ ] **Step 4:** Run `uv run pytest tests/test_scanner.py -v`. Expected: FAIL (import errors).
- [ ] **Step 5: Implement** `media.py` and `scanner.py`. Use `os.scandir` recursion, not `rglob`, so skipped dirs are never entered.
- [ ] **Step 6:** Run `uv run pytest tests/test_scanner.py -v`. Expected: 5 passed.
- [ ] **Step 7: Commit** `feat: project skeleton and folder scanner`.

### Task 2: Reading dates

**Files:**
- Create: `src/photosort/dates.py`, `tests/test_dates.py`

**Interfaces:**
- Consumes: `media.kind`
- Produces:
  - `dates.date_from_filename(name: str, today: date | None = None) -> date | None`
  - `dates.date_from_metadata(path: Path) -> datetime | None`
  - `dates.find_date(path: Path) -> DateResult` where `@dataclass(frozen=True) class DateResult: date: date | None; source: Literal["metadata", "filename", "none"]`

**Rules:**
- **Metadata for photos:** Pillow `getexif()`. Try `DateTimeOriginal` 36867, then `DateTimeDigitized` 36868 (both in IFD 0x8769), then `DateTime` 306 (IFD0). Format is `YYYY:MM:DD HH:MM:SS`. Call `pillow_heif.register_heif_opener()` once at import.
- **Metadata for videos:** hachoir `createParser` + `extractMetadata`, key `creation_date`. Treat it as UTC and convert to local time with `.astimezone()`.
- **Valid metadata range:** from 1990-01-01 to today + 1 day. Anything outside, or unparseable, means `None`. **Any exception means `None`.**
- **Filename:** a name starting with exactly 13 digits is milliseconds since the Unix epoch, converted to local time. Otherwise use the first `YYYY[-_.]?MM[-_.]?DD` not preceded by a digit that is a real calendar date. Valid range for filename dates: 2000-01-01 to today.
- **`find_date` order:** metadata → filename → `DateResult(None, "none")`. The file's modified date is **never** used.

- [ ] **Step 1: Write failing tests**, using `today=date(2026, 10, 24)` where relevant:
  - `test_filename_patterns` (parametrised): `IMG20250220181303.jpg`→2025-02-20; `IMG20250220181303~2.jpg`→2025-02-20; `IMG20250226120057_BURST001_COVER.jpg`→2025-02-26; `IMG-20250315-WA0036.jpg`→2025-03-15; `VID20250301101010.mp4`→2025-03-01; `Screenshot_2025-02-20-18-13-03-123_com.whatsapp.jpg`→2025-02-20; `PXL_20250220_181303123.jpg`→2025-02-20.
  - `test_filename_ms_epoch`: `date_from_filename("1752070136514-9e348cb0-dad6-4a6d-bce0-81f92d2a193f.jpg") == datetime.fromtimestamp(1752070136.514).date()`.
  - `test_filename_rejects` (parametrised, expect `None`): `holiday.jpg`, `IMG_20251399_000000.jpg`, `20991231_120000.jpg`, `abcd-20250230.jpg`, `9999999999999-x.jpg`.
  - `test_metadata_photo`: `make_jpeg(p, "2025:02:20 18:13:03")`. `find_date(p) == DateResult(date(2025, 2, 20), "metadata")`.
  - `test_metadata_wins_over_filename`: file `IMG20240101000000.jpg` with EXIF 2025-02-20 gives 2025-02-20 from metadata.
  - `test_metadata_video`: `make_mp4(p, "2025-03-01T10:10:10Z")` gives `datetime(2025,3,1,10,10,10,tzinfo=timezone.utc).astimezone().date()` from metadata.
  - `test_junk_metadata_falls_back` (Review Focus 5): EXIF `0000:00:00 00:00:00` on `IMG-20250315-WA0036.jpg` gives 2025-03-15 from filename. EXIF `2099:01:01 00:00:00` on `x.jpg` gives `(None, "none")`. An mp4 with `creation_time=1904-01-01T00:00:00Z` named `v.mp4` gives `(None, "none")`.
  - `test_broken_files_dont_raise` (Review Focus 4): a zero-byte `IMG20250220181303.jpg` and a text file saved as `note.jpg` give `filename` and `none` respectively, with no exception.
- [ ] **Step 2:** Run `uv run pytest tests/test_dates.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `dates.py`.
- [ ] **Step 4:** Run `uv run pytest tests/test_dates.py -v`. Expected: all pass. The video tests are skipped only if ffmpeg is missing (it exists on this machine).
- [ ] **Step 5: Commit** `feat: read dates from metadata and filenames`.

### Task 3: Safe names and unique targets

**Files:**
- Create: `src/photosort/naming.py`, `src/photosort/plan.py`, `tests/test_naming.py`

**Interfaces:**
- Produces:
  - `naming.safe_name(name: str) -> str`: replaces `\ / : * ? " < > |` and control chars with `_`, strips leading spaces and trailing dots/spaces, and prefixes `_` when the stem (the part before the first dot) casefolds to a reserved name. An empty result becomes `_`.
  - `class naming.TargetAllocator(root: Path)` with `allocate(dst: str) -> str`. Returns `dst`, or `stem (n).ext` with the smallest free n ≥ 1, so the result clashes (case-insensitively) with neither a file on disk under `root` nor an earlier `allocate` result. It reads each target directory's listing once and caches it.
  - `plan.Move` as `@dataclass(frozen=True) class Move: src: str; dst: str` (relative POSIX).
  - `plan.Plan` as `@dataclass class Plan: mode: str; moves: list[Move]; summary: list[str]; unchanged: int = 0`.

- [ ] **Step 1: Write failing tests:**
  - `test_safe_name`: `safe_name('a:b?.jpg') == 'a_b_.jpg'`, `safe_name('Rahul. ') == 'Rahul'`, `safe_name('con') == '_con'`, `safe_name('CON.txt') == '_CON.txt'`, `safe_name('  ') == '_'`, `safe_name('Atanu & Rahul') == 'Atanu & Rahul'`.
  - `test_allocate_free_name`: an empty root gives `allocate("2025/x.jpg") == "2025/x.jpg"`.
  - `test_allocate_clash_on_disk_case_insensitive`: with `root/2025/IMG.jpg` on disk, `allocate("2025/img.JPG") == "2025/img (1).JPG"`.
  - `test_allocate_clash_with_earlier_allocation`: three calls for `d/a.jpg` return `d/a.jpg`, `d/a (1).jpg`, `d/a (2).jpg`.
- [ ] **Step 2:** Run `uv run pytest tests/test_naming.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `naming.py` and `plan.py`.
- [ ] **Step 4:** Run `uv run pytest tests/test_naming.py -v`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: windows-safe names and clash-free targets`.

### Task 4: Date mode planner

**Files:**
- Create: `src/photosort/modes/__init__.py`, `src/photosort/modes/date_mode.py`, `tests/test_date_mode.py`

**Interfaces:**
- Consumes: `scanner.scan`, `dates.find_date`, `naming.TargetAllocator`, `plan.Move`, `plan.Plan`
- Produces:
  - `modes.MODES: list[tuple[str, str, bool]]` as (key, label, available). The 11 modes, in this order: `("date","Date",True)`, `("face","Face",False)`, `("duplicates","Duplicates",False)`, `("repeated","Repeated shots",False)`, `("notes","Notes & Pages",False)`, `("type","Type / source",False)`, `("content","Content",False)`, `("bad","Bad photos",False)`, `("events","Events",False)`, `("location","Location",False)`, `("camera","Camera",False)`.
  - `date_mode.MONTH_NAMES`: the English month names, January to December.
  - `date_mode.date_target(d: date | None, day_count: int) -> str` returns the target folder: `"No Date"` when `d` is None; `"2025/02-February/20-02-2025"` when `day_count >= 3`; otherwise `"2025/02-February"`.
  - `date_mode.plan_date(root: Path, files: list[str], dates: dict[str, date | None]) -> Plan` (mode `"date"`). Day counts include photos and videos together. Each file's dst is `allocate(target + "/" + basename)`. A file whose current parent already equals its target (casefolded) is not moved and is counted in `unchanged`. Summary line 1 is exactly `f"{n} files → {m} month folders, {k} day folders, {x} No Date"`, where n is the number of moves and m and k count the distinct month and day folders that receive moves. If `unchanged`, line 2 is `f"{unchanged} already in place"`.
  - `date_mode.prepare_date_plan(root: Path, include_subfolders: bool, progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None) -> Plan | None` runs scan → `find_date` per file (calling `progress(i, total)`) → `plan_date`. Returns `None` if cancelled.

- [ ] **Step 1: Write failing tests:**
  - `test_date_target`: `(None,0)`→`"No Date"`; `(date(2025,2,20),3)`→`"2025/02-February/20-02-2025"`; `(date(2025,2,20),2)`→`"2025/02-February"`.
  - `test_plan_day_folder_and_loose`: three files dated 2025-02-20 plus one dated 2025-02-21 plus one undated. The first three go to `2025/02-February/20-02-2025/`, the 21st goes loose into `2025/02-February/`, and the undated one goes to `No Date/`. Summary line is `"5 files → 1 month folders, 1 day folders, 1 No Date"`.
  - `test_photos_and_videos_counted_together`: 2 jpg plus 1 mp4 on the same day all go into the day folder.
  - `test_same_name_from_different_subfolders` (Review Focus 1): `a/IMG_1.jpg` and `b/img_1.JPG`, same date, files `{a, b}` plus a third file on that date. The dsts are distinct, and one ends with `img_1 (1).JPG` (or `IMG_1 (1).jpg`, depending on order; assert that the casefolded dsts are unique and one contains ` (1)`).
  - `test_already_in_place_not_moved`: `2025/02-February/x.jpg` dated 2025-02-15 (alone that day) gives no move, `unchanged == 1`.
  - `test_prepare_date_plan_end_to_end`: build real JPEGs in tmp. `prepare_date_plan(root, False)` returns a `Plan` with the expected dsts. A pre-set `cancel` event returns `None`.
- [ ] **Step 2:** Run `uv run pytest tests/test_date_mode.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `uv run pytest tests/test_date_mode.py -v`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: date mode planner`.

### Task 5: Journal and safe executor

**Files:**
- Create: `src/photosort/journal.py`, `src/photosort/executor.py`, `tests/test_journal.py`, `tests/test_executor.py`

**Interfaces:**
- Consumes: `plan.Plan`, `plan.Move`, `naming.TargetAllocator`
- Produces:
  - `journal.RunJournal.create(root: Path, mode: str, include_subfolders: bool, planned: int) -> RunJournal`. `run_id` is `f"{now:%Y%m%d-%H%M%S}-{mode}"`, with `-2`, `-3` and so on appended if it already exists.
  - Methods: `record_intent(src: str, dst: str) -> int`, `record_done(i: int)`, `record_dir_created(rel: str)`, `record_dir_removed(rel: str)`, `finish(status: Literal["done", "cancelled", "failed"])`, and the property `run_id`.
  - Storage: `<root>/.photosort/runs/<run_id>.jsonl` is append-only, one JSON object per line, with `flush()` + `os.fsync()` after every line. Line shapes: `{"op":"move","i":0,"src":..,"dst":..}`, `{"op":"done","i":0}`, `{"op":"mkdir","path":..}` and `{"op":"rmdir","path":..}`. `<root>/.photosort/runs/<run_id>.json` holds the metadata `{run_id, mode, started, finished, status, include_subfolders, planned, moved, undone: false}`, written atomically (temp file + `os.replace`). Status is `"running"` until `finish`.
  - `journal.RunInfo` as `@dataclass(frozen=True)`: `run_id: str, mode: str, started: datetime, status: str, moved: int, undone: bool`.
  - `journal.load_runs(root: Path) -> list[RunInfo]`, newest first.
  - `journal.JournalMove` as `@dataclass(frozen=True)`: `src: str, dst: str, done: bool`.
  - `journal.load_moves(root, run_id) -> list[JournalMove]`.
  - `journal.load_dirs(root, run_id) -> tuple[list[str], list[str]]` as (created, removed).
  - `journal.mark_undone(root, run_id) -> None`.
  - `executor.move_file(src: Path, dst: Path) -> None`: `os.rename`. On `OSError` with `errno.EXDEV`, it uses `shutil.copy2`, compares size and SHA-256, and then unlinks src. On mismatch it removes the copy and raises `IOError`. **It raises `FileExistsError` if `dst` exists**, because `os.rename` on Linux would silently overwrite.
  - `executor.ExecResult` as `@dataclass`: `moved: int, skipped: list[str], cancelled: bool, run_id: str`.
  - `executor.execute(root: Path, plan: Plan, journal: RunJournal, progress: Callable[[int, int], None] | None = None, cancel: threading.Event | None = None) -> ExecResult`. Per move: if the source is missing, it adds to `skipped`. If the dst now exists, it re-allocates with a new `TargetAllocator(root)`. It creates missing parent dirs (each one recorded with `record_dir_created`), then `record_intent` → `move_file` → `record_done`. It checks `cancel` between moves. Afterwards it removes source dirs left empty, walking upwards and never removing `root` or anything under `.photosort` (each one recorded with `record_dir_removed`). It finishes with `"done"` or `"cancelled"`. On an unexpected exception it finishes with `"failed"` and re-raises.

- [ ] **Step 1: Write failing tests:**
  - `test_journal_roundtrip`: create, intent ×2, done for 0 only, finish("done"). `load_moves` gives `[JournalMove(a,b,True), JournalMove(c,d,False)]`. `load_runs` gives one run with status `"done"`.
  - `test_runs_newest_first`: two journals created in sequence give a newest-first order.
  - `test_execute_moves_and_journals`: two moves in tmp. Files are at the dsts, `moved == 2`, and the journal has both moves done.
  - `test_execute_never_overwrites`: the plan dst `d/a.jpg` already exists on disk (with different content). After execute, both files exist, the original content at `d/a.jpg` is unchanged, and the moved file is at `d/a (1).jpg`.
  - `test_move_file_refuses_existing_dst`: `pytest.raises(FileExistsError)`.
  - `test_move_file_cross_device`: monkeypatch `os.rename` to raise `OSError(errno.EXDEV, "x")`. The file is copied with the same bytes and the source is gone.
  - `test_execute_removes_empty_source_dirs`: `sub/a.jpg` → `x/a.jpg` removes `sub/`. Root and `.photosort/` remain, and the journal records `rmdir sub`.
  - `test_execute_cancel`: a cancel event set by the `progress` callback after the first move gives `cancelled=True`, `moved == 1`, and status `"cancelled"`.
- [ ] **Step 2:** Run `uv run pytest tests/test_journal.py tests/test_executor.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run the same command. Expected: all pass.
- [ ] **Step 5: Commit** `feat: journalled safe executor`.

### Task 6: Undo

**Files:**
- Create: `src/photosort/undo.py`, `tests/test_undo.py`

**Interfaces:**
- Consumes: everything in `journal`, `executor.move_file`, `naming.TargetAllocator`
- Produces:
  - `undo.UndoBlocked(Exception)` with the attribute `blocking: list[str]` (run_ids).
  - `undo.UndoResult` as `@dataclass`: `restored: int, missing: list[str], renamed: list[tuple[str, str]]`.
  - `undo.blocking_runs(root: Path, run_id: str) -> list[str]`: newer runs that are not undone and have any move `src` (casefolded) equal to one of this run's `dst`s.
  - `undo.undo_run(root: Path, run_id: str, progress: Callable[[int, int], None] | None = None) -> UndoResult`. It raises `UndoBlocked` if `blocking_runs` is non-empty, and `ValueError` if the run is already undone. It works through moves in reverse:
    - if the dst file exists, it moves it back to src, recreating parents; if src is now occupied, it uses `TargetAllocator` → `name (1).ext` and records the pair in `renamed`.
    - else if src exists, it does nothing (the move never happened).
    - otherwise it appends to `missing`.
    - It then removes the run's `mkdir` dirs that are now empty, deepest first, and calls `mark_undone`. Undo writes no journal of its own.

- [ ] **Step 1: Write failing tests:**
  - `test_undo_full_run`: execute a date plan on 4 real JPEGs from `sub/`. After undo, every file is back at its original path with identical bytes, `sub/` is recreated, the `2025/` tree is gone, and `load_runs` shows `undone=True`.
  - `test_undo_after_crash` (Review Focus 2): hand-write a journal with three intents. #0 is done and its file is at dst. #1 has no done mark but its file is at dst (crashed after the move). #2 has no done mark and its file is still at src. After undo, all three are at src, `restored == 2`, and `missing == []`.
  - `test_undo_after_manual_changes` (Review Focus 3): after a run, delete one moved file and create a new file at another file's original path. Undo reports the deleted one in `missing`, restores the other as `name (1).jpg` (in `renamed`), and the new hand-made file is untouched.
  - `test_undo_blocked_by_newer_run`: run 1 moves `a.jpg`→`d/a.jpg`, and run 2 moves `d/a.jpg`→`e/a.jpg`. Undoing run 1 raises `UndoBlocked(blocking=[run2])`. After undoing run 2, undoing run 1 succeeds.
  - `test_undo_twice_raises`: `pytest.raises(ValueError)`.
- [ ] **Step 2:** Run `uv run pytest tests/test_undo.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `uv run pytest tests/test_undo.py -v`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: undo with crash recovery and blocking rule`.

### Task 7: Settings

**Files:**
- Create: `src/photosort/settings.py`, `tests/test_settings.py`

**Interfaces:**
- Produces:
  - `settings.Settings` as `@dataclass`: `include_subfolders: bool = False`, `use_gpu: bool = True`, `last_folder: str | None = None`.
  - `settings.default_path() -> Path` is `Path(platformdirs.user_config_dir("photo-sorter")) / "settings.json"`.
  - `settings.load_settings(path: Path | None = None) -> Settings`. A missing or corrupt file, or one with unknown keys, gives defaults for the bad or missing fields.
  - `settings.save_settings(s: Settings, path: Path | None = None) -> None` is atomic and creates parent dirs.

- [ ] **Step 1: Write failing tests:**
  - `test_defaults`: a missing file gives `Settings(False, True, None)`.
  - `test_roundtrip`.
  - `test_corrupt_file_gives_defaults`: the file contains `{not json`.
- [ ] **Step 2:** Run `uv run pytest tests/test_settings.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run `uv run pytest tests/test_settings.py -v`. Expected: 3 passed.
- [ ] **Step 5: Commit** `feat: settings with subfolders off by default`.

### Task 8: Main window and the date flow

**Files:**
- Create: `src/photosort/app.py`, `src/photosort/ui/__init__.py`, `src/photosort/ui/worker.py`, `src/photosort/ui/main_window.py`, `tests/test_ui_flow.py`

**Interfaces:**
- Consumes: `modes.MODES`, `date_mode.prepare_date_plan`, `journal.RunJournal`, `executor.execute`, `undo.undo_run`, `settings.*`
- Produces:
  - `worker.Task(QRunnable)` built as `Task(fn: Callable[..., object], *args, cancellable: bool = True)`. It calls `fn(*args, progress=<emits signals.progress>, cancel=self.cancel_event)`, leaving out `cancel` when `cancellable=False` (used for `undo_run`). Signals live on `worker.TaskSignals(QObject)`: `progress(int, int)`, `result(object)` and `error(str)`, plus a `cancel_event: threading.Event` attribute. It runs on `QThreadPool.globalInstance()`.
  - `main_window.MainWindow(settings: Settings, settings_path: Path | None = None)` is a `QMainWindow` with a `QStackedWidget`. The pages are attributes `home`, `progress_page`, `summary_page` and `done_page`, and the method `current_page() -> QWidget`.
    - **Home:** folder `QLineEdit` (`folder_edit`), Browse button, drag-and-drop of a folder onto the window, mode radio buttons from `MODES` (unavailable ones disabled and labelled `"<label> (coming soon)"`), `subfolders_check` (`QCheckBox "Include subfolders"`, from settings and saved on change), `start_button` (enabled only when the folder exists, is a directory, and a mode is selected), and **Undo history…** and **Settings…** buttons.
    - **Progress:** a bar with the text `"Checking files… i / total"`, and a Cancel button that sets `cancel_event` and returns Home when the result is `None`.
    - **Summary:** the plan's summary lines, a **Show all moves** toggle revealing a two-column table (From, To), `go_button`, and Back. If `plan.moves` is empty it shows `"Nothing to move."` and `go_button` is disabled.
    - **Moving:** reuses the Progress page with the text `"Moving… i / total"`.
    - **Done:** text `f"Moved {moved} files."` plus skipped count if any, `undo_button`, and Home. Undo runs `undo_run` in a `Task` and then shows `f"Undo complete: {restored} files put back."` plus missing/renamed counts.
    - Errors from tasks are shown in a `QMessageBox.warning`. `UndoBlocked` text: `"Undo the newer run(s) first: <ids>"`. A folder that isn't writable shows `"Can't write to this folder."` before scanning.
  - `app.main() -> int` loads settings, shows `MainWindow` (title `"Photo Sorter"`, minimum size 900×600) and runs the app. It sets `QApplication.setApplicationName("Photo Sorter")`.

- [ ] **Step 1: Write failing tests** (pytest-qt, with a tmp settings path):
  - `test_start_disabled_without_folder`: with an empty folder field, `start_button` is disabled. Setting a valid tmp dir enables it.
  - `test_only_date_mode_available`: exactly one enabled mode radio button, labelled "Date". The other 10 are disabled and contain "coming soon".
  - `test_subfolders_default_off_and_saved`: the box is unchecked initially. Checking it and reloading settings from the tmp path gives `include_subfolders is True`.
  - `test_full_date_flow_and_undo`: a tmp folder with 3 JPEGs (EXIF 2025-02-20). Set the folder, click Start, `qtbot.waitUntil(lambda: w.current_page() is w.summary_page)`, then check the summary contains `"3 files → 1 month folders, 1 day folders, 0 No Date"`. Click Go and wait for `done_page`. The files are in `2025/02-February/20-02-2025/`. Click Undo and wait for the "Undo complete" text. The files are back at the top level.
  - `test_nothing_to_move`: an empty folder gives a summary with "Nothing to move." and `go_button` disabled.
- [ ] **Step 2:** Run `uv run pytest tests/test_ui_flow.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `worker.py`, `main_window.py` and `app.py`.
- [ ] **Step 4:** Run `uv run pytest tests/test_ui_flow.py -v`. Expected: 5 passed.
- [ ] **Step 5: Commit** `feat: main window with date sorting flow`.

### Task 9: Undo history, Settings dialog, launcher, real-data check

**Files:**
- Create: `src/photosort/ui/history_dialog.py`, `src/photosort/ui/settings_dialog.py`, `scripts/photo-sorter.sh`, `scripts/install-desktop-entry.sh`, `tests/test_ui_dialogs.py`
- Modify: `src/photosort/ui/main_window.py` (wire up the two buttons)

**Interfaces:**
- Consumes: `journal.load_runs`, `undo.undo_run`, `undo.blocking_runs`, `settings.*`
- Produces:
  - `history_dialog.HistoryDialog(root: Path, parent=None)` is a `QDialog`. The table `runs_table` has the columns Date, Mode, Files, Status (`"Done"`, `"Cancelled"`, `"Failed"`, `"Undone"`), newest first, from `load_runs(root)`, with an `undo_button` for the selected run. The button is disabled for undone runs. A run with `blocking_runs` non-empty shows the tooltip `"Undo newer runs first"` and the button is disabled. Undo runs in a `worker.Task` and refreshes the table. If no folder is picked, the Home button shows the text `"Pick a folder first."`.
  - `settings_dialog.SettingsDialog(settings: Settings, parent=None)` has the `gpu_check` box (`"Use GPU"`) and the note `"GPU is used by the face and content modes (coming in later stages)."`. OK saves the settings.
  - `scripts/photo-sorter.sh` runs `exec uv run --project "<repo dir resolved from script location>" photo-sorter "$@"`.
  - `scripts/install-desktop-entry.sh` writes `~/.local/share/applications/photo-sorter.desktop` (`Name=Photo Sorter`, `Exec=<abs path>/scripts/photo-sorter.sh`, `Terminal=false`, `Categories=Graphics;`).

- [ ] **Step 1: Write failing tests:**
  - `test_history_lists_runs_and_undoes`: two executed runs in tmp (on different files). The table has 2 rows, newest first. Undoing the newest updates its status to "Undone".
  - `test_history_blocks_older_dependent_run`: the setup from `test_undo_blocked_by_newer_run`. Selecting the older run disables `undo_button`.
  - `test_settings_dialog_saves_gpu`: unchecking `gpu_check` and accepting gives saved settings with `use_gpu is False`.
- [ ] **Step 2:** Run `uv run pytest tests/test_ui_dialogs.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the dialogs and wiring, and the two scripts (`chmod +x`).
- [ ] **Step 4:** Run `uv run pytest -v`. Expected: the whole suite passes.
- [ ] **Step 5: Real-data check on a copy.** Run `cp -a "/run/media/atanu-linux/New Volume/07.08.25/Camera" ~/PhotoSortTest/Camera` (3.4 GB local copy; ~71 GB free). Record the checksums: `(cd ~/PhotoSortTest/Camera && find . -type f -exec sha256sum {} + | sort -k2) > /tmp/claude-1000/-home-atanu-linux-Desktop-Photo-Sorting/423318be-1db7-4536-9e34-0ea101dd1b62/scratchpad/before.txt`. Run a date sort on the copy through the core (`prepare_date_plan` → `execute`) in a short script, and print the summary. Check that every file appears under `2025/...`, `2024/...` or `No Date/`, and that the file count is unchanged. Undo, then recompute the checksums. Expected: identical to `before.txt`, and no `20xx/` folders left.
- [ ] **Step 6: Owner try-out.** Run `scripts/install-desktop-entry.sh`. Ask the owner to open **Photo Sorter** from the app menu, sort `~/PhotoSortTest/Camera` by Date, look at the result, and press Undo. Wait for their feedback before calling stage 1 done.
- [ ] **Step 7: Commit** `feat: undo history, settings dialog, launcher`.
