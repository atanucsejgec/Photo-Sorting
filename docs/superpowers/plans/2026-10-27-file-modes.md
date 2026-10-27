# No people, Extension and Date (all files) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three modes to Photo Sorter, each behind the usual summary → Go → Undo:
- **Extension:** every file into a folder named after its extension;
- **Date (all files):** every file into year/month folders, by the date the owner chooses;
- **No people:** photos with no face found into a "No people" folder.

**Architecture:**
- **New scanner.** A second scanner, `scan_all_files`, finds every file, not only media, and skips the clutter the spec lists.
- **Planners.**
  - Extension and Date (all files) are plain planners of a new flow kind `"files"`. It asks the subfolders warning and, for Date (all files), the date choice before any scan.
  - No people is its own flow kind, `"nopeople"`. It reuses Face mode's analyser and face cache.
- **Branch.** This work runs on branch `file-modes`, from `ui-refresh` at ce78d8a, in parallel with the UI refresh. The last task merges `ui-refresh` in once its Task 5 has landed, then adds the keyboard end-to-end tests.

**Tech Stack:** Python 3.12, PySide6, pytest-qt (offscreen), uv. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-27-file-modes-design.md`. Read it in full; its decisions are binding.

## Global Constraints

- **Safety, no change from today:**
  - a summary comes before anything moves;
  - nothing is ever deleted;
  - every move is journalled and Undo restores it;
  - name clashes never overwrite (`TargetAllocator`).
  - The existing 11 modes behave exactly as today. In particular, `scan()` and `SKIP_DIRS` don't change.
- **Tiles** come after Type / source, in this order, with these labels and hints:
  - `("no_people", "No people")`, hint "Photos without faces";
  - `("extension", "Extension")`, hint "APK, EXE, PDF…";
  - `("all_dates", "Date (all files)")`, hint "Any file, year / month".
- **Folder names:**
  - `No people` (the No people mode);
  - each extension upper-cased without the dot (Extension);
  - `No extension` (Extension);
  - Date (all files) uses the same layout as Date mode (`date_target`, with day folders at 3 or more files a day).
- **Subfolders warning** (Extension and Date (all files), only when subfolders is on; shown before any scan):
  - title "Include subfolders?";
  - text "Files inside folders may belong to programs or projects. Moving them can break them. Continue?";
  - buttons "Continue" / "Cancel", default "Cancel".
- **Date choice** (Date (all files) only; shown after the warning and before any scan):
  - title "Which date?";
  - text "Sort files by the date they were last modified, or the date they were created? Photos and videos use the date they were taken.";
  - choices "Modified" / "Created";
  - focus starts on the last choice, and Modified the first time;
  - Esc cancels → Home, nothing planned.
- **All-files scan skips:**
  - hidden files and folders (leading `.`);
  - symlinks;
  - `SKIP_DIRS` folders;
  - the suffixes `.part`, `.crdownload`, `.download`, `.partial`, `.!qb` (case-insensitive);
  - the names `desktop.ini`, `thumbs.db`, `.ds_store` (casefold);
  - on Windows, entries with the hidden or system attribute.
- **No people:**
  - photos only (`media.kind == "photo"`);
  - Face mode's analyser, detector settings and `FACES_KIND` cache;
  - an unreadable photo stays;
  - the face engine failing on the first photos is an error, never "no faces".
- **Windows-ready:** the created date uses `st_birthtime` where `os.stat` has it (Windows), Linux `statx` where available, and otherwise the modified date. No new dependencies; no network.
- **Process:**
  - opus agents; no Co-Authored-By line; the owner's noreply identity; tests use tmp_path and fakes only.
  - Every heavy run: `flock /tmp/claude-1000/-home-atanu-linux-Desktop-Photo-Sorting/423318be-1db7-4536-9e34-0ea101dd1b62/scratchpad/heavy.lock systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 timeout 900 <cmd>`, one at a time. The UI refresh agents share this lock.
  - Work only in the worktree `/home/atanu-linux/Desktop/Photo Sorting/.worktrees/file-modes`, and never touch the main checkout.

## Review Focus

1. **Odd file names in Extension.** Only a suffix of 1–10 characters from `[A-Za-z0-9_+-]` counts as an extension.
   - `report.final version` → `No extension`; `file.` → `No extension`; `archive.tar.gz` → `GZ`;
   - `PHOTO.JPG` and `photo.jpg` → the same folder;
   - `café.ñ` → `No extension`.
   - Test in Task 1.
2. **An existing folder in another case** (`apk/x.apk` with subfolders on) counts as already in place: no move, and no second `APK` folder. Test in Task 1.
3. **Created date is unavailable or fails.** If `statx` raises, or the kernel or filesystem gives no birth time, the file falls back to its modified date and is counted. File times are converted to dates in local time. Test in Task 2.
4. **The face engine is broken, versus a photo that is truly faceless.** If every photo fails to analyse, the run ends in the error path with nothing planned. An empty face list moves the photo, and an unreadable one stays. Test in Task 3.
5. **Cancel and Esc at each question.** At the warning, the date choice or the progress page, the run returns Home with nothing planned and the sidebar unlocked. A second run then works normally. Test in Tasks 1, 2 and 4.

---

## File Structure

```
src/photosort/scanner.py              + scan_all_files, PARTIAL_SUFFIXES, CLUTTER_NAMES (Task 1)
src/photosort/modes/extension_mode.py NEW (Task 1)
src/photosort/modes/all_dates.py      NEW (Task 2)
src/photosort/modes/no_people.py      NEW (Task 3)
src/photosort/modes/__init__.py       MODES + MODE_SPECS + ModeSpec fields (Tasks 1–3)
src/photosort/ui/flows/files.py       NEW kind "files" (Tasks 1–2)
src/photosort/ui/flows/no_people.py   NEW kind "nopeople" (Task 3)
src/photosort/ui/dialogs.py           + choose() (Task 2)
src/photosort/settings.py             + file_date (Task 2)
src/photosort/ui/home_page.py         TILES (Tasks 1–3); ui/icons.py MODE_ICONS + SVGs (Tasks 1–3)
src/photosort/ui/main_window.py       _FLOWS entries (Tasks 1, 3)
```

---

### Task 1: All-files scanner, Extension mode, the "files" flow with the subfolders warning

**Files:**
- Modify: `src/photosort/scanner.py`, `src/photosort/modes/__init__.py`, `src/photosort/ui/home_page.py` (TILES), `src/photosort/ui/icons.py` (+ a vendored Lucide SVG for the Extension tile, e.g. `file-type.svg`), `src/photosort/ui/main_window.py` (`_FLOWS`)
- Create: `src/photosort/modes/extension_mode.py`, `src/photosort/ui/flows/files.py`, `tests/test_scan_all.py`, `tests/test_extension_mode.py`, `tests/test_ui_files.py`

**Interfaces:**
- Produces:
  - `scanner.scan_all_files(root: Path, include_subfolders: bool) -> list[str]`: sorted POSIX paths relative to root, with the Global Constraints skips. Same contract as `scan()` otherwise: an unreadable subfolder is skipped and an unreadable root raises.
  - `scanner.PARTIAL_SUFFIXES: frozenset[str]`, `scanner.CLUTTER_NAMES: frozenset[str]`.
  - `extension_mode.NO_EXTENSION = "No extension"`; `extension_mode.extension_folder(name: str) -> str`; `extension_mode.plan_extension(root: Path, files: list[str]) -> Plan` with `mode="extension"`; `extension_mode.prepare_extension_plan(root, include_subfolders, progress=None, cancel=None) -> Plan | None`.
  - `ModeSpec.kind` gains `"files"`; new field `ModeSpec.warn_subfolders: bool = False`. For `MODE_SPECS["extension"]`: kind "files", warn_subfolders True.
  - `flows/files.py`: `MODELS = ()`; `start(window, root, include_subfolders, spec)`.
    - If `spec.warn_subfolders and include_subfolders`, it calls `dialogs.ask(window, "Include subfolders?", <text>, yes="Continue", no="Cancel", default="no")`; on False it does `window.show_page(window.home)` and returns.
    - Then it does the same as `plain.start`.
  - The summary lines are exactly `f"{n} files → {k} folders"`, plus `f"{u} already in place"` when u > 0.
- [ ] **Step 1: Write failing tests.**
  - **`test_scan_all.py`:**
    - every kind of file is found (`.apk`, `.exe`, `.pdf`, `.jpg`, no extension);
    - each skip in the Global Constraints is skipped (one assertion per item; the Windows attribute is tested through a monkeypatched `scanner._hidden_on_windows(entry) -> bool`);
    - subfolders on and off; `.photosort` is never entered;
    - an unreadable root raises.
  - **`test_extension_mode.py`:**
    - `extension_folder`: `"a.apk"→"APK"`, `"B.Exe"→"EXE"`, `"x.tar.gz"→"GZ"`, `"README"→"No extension"`, `"file."→"No extension"`, `"report.final version"→"No extension"`, `"café.ñ"→"No extension"`, `"a.verylongextension"→"No extension"`.
    - **Review Focus 1:** a plan for `PHOTO.JPG` and `photo.jpg` puts both under one folder, renamed so nothing collides.
    - **Review Focus 2:** `apk/x.apk` with subfolders on is in place (not moved), and the summary says "1 already in place".
    - a folder named `APK` that already holds a different `x.apk` → the new file is allocated another name.
  - **`test_ui_files.py`** (dialogs patched):
    - with subfolders off, no warning is shown, and the summary shows "3 files → 2 folders";
    - with subfolders on, the warning is shown with the exact texts; "Cancel" → Home with nothing planned and the sidebar unlocked; "Continue" → summary;
    - Go then Undo restores every file (sha256 of each file before and after);
    - **Review Focus 5:** cancel on the progress page → Home; then a second run works.
  - The tile shows "Extension" with its hint, after Type / source.
- [ ] **Step 2:** Run the new test files (lock + cap). Expected: FAIL.
- [ ] **Step 3:** Implement. Read `_hidden_on_windows` from `entry.stat(follow_symlinks=False).st_file_attributes`, guarded with `getattr`, so it is always False on Linux.
- [ ] **Step 4:** Run the full suite (lock + cap). Expected: all pass.
- [ ] **Step 5: Commit** `feat: Extension mode, an all-files scan and the subfolders warning`.

---

### Task 2: Date (all files), with the date choice

**Files:**
- Create: `src/photosort/modes/all_dates.py`, `tests/test_all_dates.py`
- Modify: `src/photosort/modes/__init__.py`, `src/photosort/settings.py`, `src/photosort/ui/dialogs.py`, `src/photosort/ui/flows/files.py`, `src/photosort/ui/home_page.py`, `src/photosort/ui/icons.py` (+ a Lucide SVG such as `calendar-clock.svg`), `tests/test_ui_files.py`, `tests/test_settings.py`, `tests/test_ui_theme.py` (or wherever the dialogs tests live)

**Interfaces:**
- Consumes: Task 1's `scan_all_files`, `flows/files.start`, `ModeSpec.warn_subfolders`.
- Produces:
  - `all_dates.DateSource = Literal["modified", "created"]`.
  - `all_dates.created_time(path: Path) -> float | None`: `os.stat(path).st_birthtime` if present; else Linux `statx` through ctypes (`STATX_BTIME`; None if `stx_mask` lacks it, or on any `OSError`/`AttributeError`); else None.
  - `all_dates.file_date(path: Path, source: DateSource) -> tuple[date, bool]`: the date in local time and whether it fell back to modified.
  - `all_dates.prepare_all_dates_plan(root, include_subfolders, *, date_source: DateSource, progress=None, cancel=None) -> Plan | None` with `mode="all_dates"`.
    - Media files (`media.kind` not None) use `find_date(path).date`; when that is None, they use `file_date(path, date_source)`.
    - Folders come from `date_mode.date_target` with the same day-folder rule. Reuse `plan_date`'s logic. `plan_date` stays unchanged for Date mode: either factor out the shared part, or call it and rebuild the summary.
  - Summary lines, exactly:
    - `f"{n} files → {m} month folders, {d} day folders"`;
    - then, only when the count is > 0:
      - `f"{u} already in place"`;
      - `f"{c} files had no created date: modified date used"`;
      - `f"{p} photos and videos had no taken date: file date used"`.
  - `Settings.file_date: str = "modified"`, validated to `{"modified", "created"}`. A bad value loads as "modified".
  - `dialogs.choose(parent, title: str, text: str, options: Sequence[str], default: int = 0) -> int | None`: buttons in order, focus on `default`, ←→ move, Enter picks, Esc → None. Strip: "←→ choose · Enter pick · Esc cancel".
  - `ModeSpec.asks_date_source: bool = False`. For `MODE_SPECS["all_dates"]`: kind "files", warn_subfolders True, asks_date_source True.
  - `flows/files.start`: after the warning, if `spec.asks_date_source`, it calls `choose(..., ["Modified", "Created"], default=0 if settings.file_date == "modified" else 1)`. None → Home. Otherwise it saves `settings.file_date` and runs the Task with `date_source=` bound.
- [ ] **Step 1: Write failing tests.**
  - `file_date` with `os.utime`-set times returns the modified date in local time.
  - "created" with `created_time` monkeypatched to a known time returns that date.
  - **Review Focus 3:** `created_time` returning None, or the statx call raising `OSError`, falls back to modified with `True`, and the summary counts it.
  - `created_time` on a real tmp file returns None or a float, never raises.
  - A photo with EXIF/filename date uses it; a photo with no date uses the file date and is counted.
  - With 3 files on one day, a day folder is used.
  - The summary strings are exact.
  - `choose`: Enter on the default; → then Enter picks index 1; Esc → None.
  - Settings round-trips `file_date`, and a bad value loads as "modified".
  - UI:
    - **Review Focus 5:** Esc at the date choice → Home.
    - Subfolders on gives the warning, then the choice.
    - The choice is remembered (the second run focuses "Created").
    - Go then Undo restores every file.
- [ ] **Step 2:** Run (lock + cap). Expected: FAIL.
- [ ] **Step 3:** Implement. Pass the date source with `functools.partial(spec.prepare, date_source=...)` into `Task`.
- [ ] **Step 4:** Run the full suite (lock + cap). Expected: all pass.
- [ ] **Step 5: Commit** `feat: Date (all files), by modified or created date`.

---

### Task 3: No people

**Files:**
- Create: `src/photosort/modes/no_people.py`, `src/photosort/ui/flows/no_people.py`, `tests/test_no_people.py`, `tests/test_ui_no_people.py`
- Modify: `src/photosort/modes/__init__.py`, `src/photosort/modes/face_mode.py` (make the analyser and `_faces_of` importable without leading underscores, keeping old names as aliases if tests use them), `src/photosort/ui/main_window.py` (`_FLOWS`), `src/photosort/ui/home_page.py`, `src/photosort/ui/icons.py` (+ a Lucide SVG such as `user-x.svg`)

**Interfaces:**
- Consumes: `face_mode` analyser + faces-of + `FACES_KIND` cache; `cache.AnalysisCache`; `flows.check_failed`; `window.engine_factory`, `window.settings.use_gpu`.
- Produces:
  - `no_people.NO_PEOPLE = "No people"`.
  - `no_people.find_no_people(root, include_subfolders, analyse, progress=None, cancel=None, cache=None) -> Plan | None` with `mode="no_people"`.
    - Photos with an empty face list → `No people/<name>`, allocated.
    - Photos with faces stay; unreadable photos stay.
    - Photos already directly in a `No people` folder (casefold) are in place.
  - Summary lines, exactly: `f"{n} photos without faces → No people"`, `f"{f} photos with people stay"`, and only when > 0, `f"{u} photos could not be read (they stay)"` and `f"{a} already in place"`.
  - `ModeSpec.kind` gains `"nopeople"`.
  - `flows/no_people.py`:
    - `MODELS = ("face_detection", "face_recognition")`.
    - `start` begins progress with "Looking for faces…" and runs a Task that makes the engine on the worker thread and opens `AnalysisCache(root)`, the same as `flows/face._scan_faces`.
    - Result → `window.show_plan`; error → `check_failed`.
- [ ] **Step 1: Write failing tests** (fake `analyse`; real tiny JPEGs in tmp_path):
  - no faces → moved;
  - one face → stays;
  - an `UnreadableImage` → stays and is counted;
  - videos and PDFs are untouched;
  - a cached faces entry is used without calling `analyse` (use the face_mode tests' cache fixture style);
  - **Review Focus 4:** `analyse` raising `RuntimeError` for every photo → the error is raised (the flow shows "Could not check the files: …", with nothing planned), never a plan moving everything;
  - the summary strings are exact;
  - UI:
    - the models are missing → the existing download question → refuse → Home;
    - with models (fake engine), the summary, then Go, then Undo restores every file;
    - the tile shows "No people" with its hint.
- [ ] **Step 2:** Run (lock + cap). Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the full suite (lock + cap). Expected: all pass.
- [ ] **Step 5: Commit** `feat: No people mode`.

---

### Task 4: Merge the UI refresh and keyboard-only end-to-end tests

**Wait for the UI refresh:** start only when the UI refresh ledger (`/home/atanu-linux/Desktop/Photo Sorting/.superpowers/sdd/2026-10-25-ui-refresh/progress.md`) has `Task 5: complete`. The controller checks this before dispatching.

**Files:**
- Modify: whatever the merge touches. Expect conflicts in `home_page.py` TILES, `icons.py`, `dialogs.py`, `main_window.py` `_FLOWS`, `tests/conftest.py`, and the README.
- Create: `tests/test_ui_file_modes_keyboard.py`

**Interfaces:**
- Consumes: the UI refresh's Task 5 keyboard summary, Done and e2e helpers; `shortcuts.bind`/`describe` (every dialog key must show in its strip).
- [ ] **Step 1:** `git merge ui-refresh` (no rebase, no force) and resolve the conflicts so both sides' changes stay. Then run the full suite (lock + cap). Expected: all pass.
- [ ] **Step 2: Write failing keyboard-only tests,** using only `qtbot.keyClick(s)` after show, with no `.click()`:
  - **Extension:** type the folder, Enter → arrows to the Extension tile → Enter → (subfolders on: warning → → then Enter on Continue) → summary → Enter (Go) → Done → U → Yes → every file restored.
  - **Date (all files):** the same path, plus the date choice → Created; and Esc at the choice → Home.
  - **No people:** a fake engine → summary → Go → Undo.
  - The `choose` dialog's and the warning's strips equal their live keys.
- [ ] **Step 3:** Run (lock + cap). Expected: FAIL first, then implement any missing key wiring, then PASS.
- [ ] **Step 4:** Add the three modes to the README's mode list, with one line each. Then run the full suite and the UI files 3× in sequence (lock + cap). Expected: all pass.
- [ ] **Step 5: Commit** `test: keyboard runs of No people, Extension and Date (all files)`.

---

### Task 5: Real-data check (controller)

- [ ] **Step 1:** Make a test folder `~/PhotoSortTest/Files`. Fill it with copies of photos from `~/PhotoSortTest/Camera` plus generated dummy `.apk`, `.exe`, `.pdf`, `.zip`, `.txt` and extensionless files, a `.crdownload`, a hidden file, and a subfolder with files. Take a sha256 snapshot.
- [ ] **Step 2:** On the real display, run each new mode by keyboard, once with subfolders off and once on (Continue). Undo each run and check the snapshot.
- [ ] **Step 3:** Run No people on `~/PhotoSortTest/Camera` and look at a sample of the No people photos for faces it missed. Undo.
- [ ] **Step 4:** Final whole-branch review → fixes → merge to main after the UI refresh merge → deploy `~/PhotoSorter` → tell the owner.
