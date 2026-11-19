# Busy-day names, Pick faces, one place for undo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship three things on Photo Sorter's new UI, each behind the usual summary → Go → Undo:
- names for busy days in Date mode;
- a new **Pick faces** mode;
- all undo records and the scan cache kept in one central place, with a multi-select History.

**Architecture:** Three independent tracks, each on its own branch and worktree from `three-more` (4663400), merged back in the final task.
- **Track A** (`busy-days`): Date mode stops planning straight away. It returns a scan, the flow asks for busy-day names, then plans.
- **Track B** (`pick-faces`): a new mode kind `"pickfaces"`. It reuses Face mode's analyser and cache, groups faces with numpy, then shows a person grid, a folder list and a "Which folder?" page.
- **Track C** (`central-undo`): `journal`, `undo` and `cache` read and write under `appdata.data_dir()`. A new `migrate.bring_in` imports old `.photosort` folders. History is rewritten.

**Tech Stack:** Python 3.12, PySide6, pytest-qt (offscreen, 11 pt via conftest), numpy, uv. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-19-three-more-design.md`. Read it in full; its decisions bind.

## Global Constraints

- Nothing is ever deleted; only moved. Every run has a journal; Undo restores byte-for-byte.
- Every screen is fully keyboard-usable, and its keys are listed in the shortcut strip (`ui/shortcuts.py`). Use `tests/keys.py` helpers in keyboard tests. Every new shortcut uses `repeat=False`.
- Pages fit 1024×600 at 11 pt. Tests already run at 11 pt (conftest).
- No new dependencies and no downloads. Face models are only read.
- Tests use `tmp_path` only. **Never** read, write or move anything under `/run/media/atanu-linux/New Volume/`. Never touch `~/.local/share/photo-sorter` in tests: Track C's autouse fixture points `appdata.data_dir()` to a tmp folder.
- Heavy commands (the full suite, any real-model run) go through:
  `flock /tmp/claude-1000/-home-atanu-linux-Desktop-Photo-Sorting/423318be-1db7-4536-9e34-0ea101dd1b62/scratchpad/heavy.lock systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 timeout 900 <cmd>`
  One at a time. Single test files can run directly with `uv run pytest <file> -q`.
- Commits by `Atanu Biswas <154270931+atanucsejgec@users.noreply.github.com>`, with **no Co-Authored-By line**, ever.
- Run tests with `-W error`, as the suite does.

## Review Focus

1. **A run made before this update, on a folder whose `.photosort` was never imported, must still block or be blocked correctly.** Every sort and every undo calls `bring_in(root)` first (Task C4 test `test_undo_imports_first`).
2. **A drive mounted under a new path.** A relocated root keeps its runs' order and blocking (Task C5 test `test_relocate_then_undo_restores`).
3. **Busy-day name with a slash, dots or only spaces.** Cleaned, or treated as empty, never a nested folder (Task A1 test `test_busy_day_name_is_cleaned`).
4. **Pick faces on a folder with 0 faces, or 1 face.** "No faces found" summary, nothing to Go (Task B2 test `test_no_faces_found`).
5. **Folder name equal to an existing file name, or to "Duplicates".** Refused with a message (Task B2 test `test_folder_name_refusals`).

---

## Track C — one place for undo (branch `central-undo`)

### Task C1: Central data folder and journal

**Files:**
- Create: `src/photosort/appdata.py`
- Modify: `src/photosort/journal.py`, `src/photosort/people.py` (`default_folder` uses `appdata.data_dir()`), `tests/conftest.py`
- Test: `tests/test_journal.py`

**Interfaces:**
- Produces:
  - `appdata.data_dir() -> Path`: `Path(platformdirs.user_data_dir("photo-sorter"))`, not created.
  - `appdata.runs_dir() -> Path`: `data_dir()/"runs"`.
  - `journal.RUNS_DIR` stays as the **old** relative location `Path(".photosort")/"runs"`, for migration only.
  - `RunJournal.create(root, mode, include_subfolders, planned)`: same signature. It writes to `runs_dir()`, and meta gains `"root": os.path.abspath(root)`.
  - `RunInfo` gains `root: Path`.
  - `load_runs(root) -> list[RunInfo]`: only runs whose meta root equals `abspath(root)`. Newest first, as now.
  - `load_all_runs() -> list[RunInfo]`: every run, newest first.
  - `load_moves(root, run_id)`, `load_dirs(root, run_id)`, `mark_undone(root, run_id)`: same signatures, now reading the central files. Root is kept for the callers and not used for paths.
  - `forget_runs(run_ids: list[str]) -> None`: deletes both files of each run. Before deleting, it appends `{"run_id", "started"}` to `runs_dir()/forgotten.json` (written atomically).
  - `forgotten_keys() -> set[tuple[str, str]]`.
  - `relocate_root(old: Path, new: Path) -> int`: rewrites the root of every run whose root is `old` or inside it, swapping the prefix. Returns the number changed.
  - `import_run(old_runs_dir: Path, run_id: str, root: Path) -> str | None`: copies, verifies and returns the central run id (suffix `-imported-N` on a clash), or None if forgotten or already imported. The key is (run_id, started) against existing metas and `forgotten_keys()`.

- [ ] **Step 1: Write the failing tests** in `tests/test_journal.py`:
  - `test_records_live_in_the_central_folder`: after `RunJournal.create(tmp_path/"pics", "date", False, 1)`, nothing exists under `pics/.photosort`. `appdata.runs_dir()` holds `<id>.json` with `meta["root"] == str(tmp_path/"pics")`.
  - `test_load_runs_filters_by_root`: two roots, one run each. `load_runs(a)` returns only a's run. `load_all_runs()` returns both, newest first, with `.root` set.
  - `test_forget_runs_removes_and_remembers`: after forgetting, both files are gone and `(run_id, started)` is in `forgotten_keys()`.
  - `test_relocate_root_moves_prefix`: runs at `/x/a` and `/x/a/b` and `/x/ab`. `relocate_root(/x/a, /y)` changes the first two to `/y` and `/y/b`, leaves `/x/ab` alone, and returns 2.
  - `test_import_run_skips_forgotten_and_duplicates`, `test_import_run_suffixes_clashing_id`.
  - Update existing journal tests to the new location.
  - **Conftest:** an autouse function-scoped fixture `_app_data(tmp_path_factory, monkeypatch)` monkeypatches `photosort.appdata.data_dir` to a fresh tmp folder. Every test, UI ones included, is then isolated.
- [ ] **Step 2:** `uv run pytest tests/test_journal.py -q -W error`. The new tests fail.
- [ ] **Step 3:** Implement. Keep the fsync and atomic-write rules of the module docstring, and update the docstring.
- [ ] **Step 4:** The journal tests pass. The full suite goes through the heavy wrapper. Fix only callers whose behaviour the location change broke. Undo is Task C2.
- [ ] **Step 5:** Commit `feat: undo records live in one central folder`.

### Task C2: Undo over the central store

**Files:**
- Modify: `src/photosort/undo.py`
- Test: `tests/test_undo.py`

**Interfaces:**
- Consumes: C1.
- Produces:
  - `related_roots(root: Path) -> list[Path]`: replaces `related_folders`. It returns the distinct roots in `load_all_runs()` that are `root`, a folder around it, or a folder inside it, by path parts. No disk walk.
  - `blocking_runs(root, run_id, related=None)`: same meaning, using `related_roots`.
  - `dependents(root: Path, run_id: str) -> list[RunInfo]`: the **older**, not-undone runs (any related root) that this run blocks, i.e. `run_id in {b.run_id for b in blocking_runs(r.root, r.run_id)}`.
  - `undo_run(root, run_id, progress=None)`: unchanged contract.

- [ ] **Step 1: Failing tests:**
  - port every existing `related_folders` test to `related_roots`, built from journals and not from `.photosort` folders;
  - `test_dependents_lists_older_runs_this_one_blocks`: run A on `root`, run B moves A's file. `dependents(root, B) == [A]`, and `dependents(root, A) == []`;
  - `test_root_with_shared_prefix_is_not_related`: `/x/ab` is not related to `/x/a`.
- [ ] **Step 2:** Run; they fail.
- [ ] **Step 3:** Implement. Drop the disk walk.
- [ ] **Step 4:** `tests/test_undo.py` and `tests/test_journal.py` pass.
- [ ] **Step 5:** Commit `feat: undo finds related runs in the central store`.

### Task C3: Central scan cache

**Files:**
- Modify: `src/photosort/cache.py`, and every `AnalysisCache(root)` caller: `modes/duplicates.py:62`, `modes/repeated.py:43`, `modes/bad_photos.py:53`, `ui/flows/face.py:37`, `ui/flows/clip.py:43`, `ui/flows/no_people.py:35`
- Test: `tests/test_cache.py`

**Interfaces:**
- Produces:
  - `AnalysisCache(path: Path | None = None)`: the default is `appdata.data_dir()/"cache.db"`, and parent folders are created.
  - `merge_cache(old_db: Path) -> bool`: inserts all of the old db's entries into the central one (`INSERT OR IGNORE`). True on success. A corrupt or unreadable old db gives False and is left alone.
  - The `executor.PHOTOSORT_DIR` and `CACHE_NAME` handling of old empty `.photosort` folders stays.

- [ ] **Step 1: Failing tests:**
  - `test_cache_is_central`: put through one cache, then get through a new `AnalysisCache()`. Nothing is created under the photo folder.
  - `test_entry_survives_a_move_between_roots`: same name, size and mtime in another folder → a hit.
  - `test_merge_cache_imports_entries`.
  - `test_merge_cache_corrupt_returns_false`.
  - Keep the existing corruption, locking and read-only tests, pointed at the central path.
- [ ] **Step 2:** Run; they fail.
- [ ] **Step 3:** Implement and update the callers.
- [ ] **Step 4:** The cache tests and the touched mode tests pass.
- [ ] **Step 5:** Commit `feat: one central analysis cache`.

### Task C4: Bring old records in

**Files:**
- Create: `src/photosort/migrate.py`
- Modify: `src/photosort/ui/main_window.py`: Home's folder settles; `_start`; `_undo`. Also `ui/history_page.py`'s undo path, which C5 then reshapes.
- Test: `tests/test_migrate.py`, `tests/test_ui_flow.py`

**Interfaces:**
- Consumes: `journal.import_run`, `cache.merge_cache`, `journal.RUNS_DIR`.
- Produces:
  - `migrate.bring_in(root: Path, progress=None, cancel=None) -> int`: the number of runs imported.
  - It checks the `.photosort` folders of the folders around root, of root itself and of every folder inside root. The walk is the same as the old `related_folders`; move that code here.
  - For each `.photosort`:
    - import every `runs/*.json` and its `.jsonl` with `import_run(..., root=that folder)`;
    - delete an old pair only after its import has verified, or when it is already present or forgotten;
    - merge `cache.db` with `merge_cache` and delete it and its side files on success;
    - remove `runs/`, then `.photosort`, if empty.
  - Every OSError is logged and skipped.
  - It is idempotent and fast when there is nothing to do.
- Window:
  - when Home's folder text settles on an existing folder (the count already triggers there), run `bring_in` as a quiet `Task`, with no progress page and a log line only;
  - every sort (`_start`) and every undo (Done's Undo and History's Undo) runs `bring_in(root)` inside its worker task before planning or undoing.

- [ ] **Step 1: Failing tests:**
  - `test_bring_in_moves_old_runs_and_cleans_up`: build an old-layout `.photosort/runs` with a real executed run (create it by calling the pre-change journal format: write the json and jsonl by hand from a fixture in `tests/fstree.py`). After `bring_in`, `load_runs(root)` has it, `undo_run` restores the files byte-for-byte, and `root/.photosort` is gone.
  - `test_bring_in_read_only_keeps_old_files_and_does_not_reimport` (chmod; skip as root).
  - `test_bring_in_skips_forgotten`.
  - `test_bring_in_nested_and_parent_folders`.
  - `test_bring_in_keeps_photosort_with_unknown_files`.
  - `test_undo_imports_first` (UI): an old-layout run on the Home folder; History's Undo restores it.
- [ ] **Step 2:** Run; they fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Pass; the full suite passes (heavy wrapper).
- [ ] **Step 5:** Commit `feat: old undo records are brought into the central folder`.

### Task C5: History for every folder

**Files:**
- Modify: `src/photosort/ui/history_page.py`, `src/photosort/ui/main_window.py` (as needed)
- Test: `tests/test_ui_history.py` (new), plus the existing History tests (move them there)

**Interfaces:**
- Consumes: `load_all_runs`, `forget_runs`, `relocate_root`, `dependents`, `history_page.undo_with_message`, `bring_in`.
- Behaviour, with exact copy from the spec:
  - **Columns:** When, Mode, Folder, Files, Status. The Folder column is elided in the middle and shows the full path as a tooltip.
  - **Filter:** a "This folder only" switch, off by default, and its key F. It shows runs whose root is the Home folder or inside it.
  - **Selection:** `ExtendedSelection` by rows. Space toggles the focused row, and Ctrl+A selects all.
  - **Undo** (button or Enter): asks "Undo N runs?" (for one run: the existing question), with Cancel focused. Then it undoes newest first in one Task, stops at the first blocked or failed run, and reports which.
  - **Delete** (button or the Delete key):
    - if any selected run has `dependents` not in the selection, it shows "Forget the older runs that depend on it too, or undo it first." with buttons **Forget all N** and **Cancel**. Forget all adds them;
    - otherwise it asks "Forget N runs? Your files stay where they are, but these runs can no longer be undone." with **Keep** focused. If all are undone: "Forget N runs?";
    - then `forget_runs`.
  - **Folder not found:** a run whose root doesn't exist shows Status "Folder not found". Undo on it opens a folder picker titled "Where is this folder now?". The pick is accepted only if at least one done move's `dst` exists under it. Otherwise it shows "This doesn't look like the same folder" and changes nothing. If accepted: `relocate_root`, then undo.
  - **Strip:** Enter undo · Del forget · Space select · Ctrl+A all · F this folder.
- [ ] **Step 1: Failing tests:**
  - `test_lists_runs_from_all_folders_with_folder_column`;
  - `test_this_folder_only_filter`;
  - `test_multi_undo_newest_first_restores_all`;
  - `test_multi_undo_stops_at_blocked`;
  - `test_delete_asks_with_keep_focused_and_forgets`;
  - `test_delete_with_dependents_offers_forget_all`;
  - `test_folder_not_found_then_wrong_pick_refused`;
  - `test_relocate_then_undo_restores` (rename the tmp root);
  - `test_held_delete_asks_once`;
  - `test_keyboard_only_history` (keys helpers).
  - Every dialog is patched; no real modal.
- [ ] **Step 2:** Run; they fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Pass. Grab History at 1024×600 into the scratchpad and check that it fits.
- [ ] **Step 5:** Commit `feat: History lists every folder's runs; undo or forget many at once`.

---

## Track A — busy days (branch `busy-days`)

### Task A1: Date core: named day folders

**Files:**
- Modify: `src/photosort/modes/date_mode.py`, `src/photosort/modes/__init__.py` (Date's prepare returns a scan), `src/photosort/thresholds.py`
- Test: `tests/test_date_mode.py`, `tests/test_all_dates.py`

**Interfaces:**
- Produces:
  - `thresholds.BUSY_DAY_MIN_PHOTOS = 11`.
  - `date_mode.existing_day_folder(root: Path, d: date) -> str | None`: the existing folder, relative POSIX, in d's month folder (resolved case-insensitively via `resolve_existing_dir`), whose name casefolded is `DD-MM-YYYY` or starts with `DD-MM-YYYY `. The first in name order. None if there is none.
  - `day_folder_rule` uses `existing_day_folder` in place of the exact-name check. All callers, Date and Date (all files), now join a named folder. The target for such a day is that existing folder.
  - `@dataclass(frozen=True) class DateScan: root: Path; files: list[str]; dates: dict[str, date | None]`.
  - `prepare_date_scan(root, include_subfolders, progress=None, cancel=None) -> DateScan | None`: replaces `prepare_date_plan` as Date's `MODE_SPECS` prepare. Date's spec kind becomes `"date"`.
  - `busy_days(scan: DateScan) -> dict[date, list[str]]`: dates with ≥ `BUSY_DAY_MIN_PHOTOS` **photos** (`media.kind == "photo"`) that have no `existing_day_folder`, oldest first, holding all of that day's files (photos and videos).
  - `plan_date(root, files, dates, names: dict[date, str] | None = None) -> Plan`: a named day goes to `YYYY/MM-Month/DD-MM-YYYY <name>`. The name is cleaned with `naming.safe_name` then `fit_name(…, FOLDER_NAME_MAX, FOLDER_NAME_MAX_BYTES)` on the whole folder name. A name that cleans to "" means unnamed.
  - Summary line added when any are named: `f"{n} busy days named"` (`"1 busy day named"`).
- [ ] **Step 1: Failing tests:**
  - `test_named_busy_day_folder`: 12 photos on 20-02-2025, names `{d: "Puri trip"}`, all go to `2025/02-February/20-02-2025 Puri trip/`.
  - `test_busy_day_counts_photos_only`: 10 photos + 5 videos → not busy; 11 photos → busy.
  - `test_existing_named_day_folder_is_joined_and_not_asked`: `20-02-2025 Puri` exists. A single file of that day joins it (Date and Date (all files)), and `busy_days` excludes it.
  - `test_existing_prefix_without_space_is_not_a_day_folder`: `20-02-2025x` is not a match.
  - `test_busy_day_name_is_cleaned`: `"a/b"`, `" .. "` and `"   "`.
  - `test_unnamed_busy_day_unchanged`.
  - `test_date_undo_removes_named_folder`.
  - `test_prepare_date_scan_cancel`.
  - Existing Date tests still pass with `plan_date(..., names=None)`.
- [ ] **Step 2:** Run; they fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `tests/test_date_mode.py tests/test_all_dates.py tests/test_undo.py` pass.
- [ ] **Step 5:** Commit `feat: busy days can be named; named day folders are joined`.

### Task A2: The "Name the busy days" page and Date flow

**Files:**
- Create: `src/photosort/ui/busy_days_page.py`, `src/photosort/ui/flows/date.py`
- Modify: `src/photosort/ui/main_window.py` (build the page and register the flow for kind `"date"`), `src/photosort/ui/flows/__init__.py` docstring
- Test: `tests/test_ui_busy_days.py`

**Interfaces:**
- Consumes: A1.
- Flow:
  1. `start` runs `prepare_date_scan` in a Task (progress "Checking files…");
  2. if there are no busy days, it plans at once with `plan_date` in a Task and goes to `show_plan`;
  3. otherwise it shows the page; `named(names)` plans and goes to the summary with `back_to` set to the page.
- Page `BusyDaysPage(QWidget)`:
  - `load(root, days: dict[date, list[str]])`;
  - signals `named(dict)` and `left()`;
  - one row per day: the date label (`"Thu 20 Feb 2025"`) and `"34 photos, 3 videos"`; a collage of the first 12 thumbnails; a "Show all" button; a name `QLineEdit` with the placeholder `DD-MM-YYYY`;
  - Show all opens a scrollable thumbnail grid of every file of that day, closed by Esc;
  - thumbnails via `review_page.render_thumbnail` on pool threads, tagged with generation and row, as `events_page.py` does;
  - keys, as in the spec: ↑/↓ days, Enter/F2 name (in the box Enter done, Esc stop), A show all, Ctrl+Enter continue, Esc leave (asks). Strip: `↑↓ day · Enter name · A all photos · Ctrl+Enter continue · Esc leave`.
- [ ] **Step 1: Failing tests:**
  - `test_busy_days_page_shows_rows_oldest_first`;
  - `test_name_then_continue_plans_named_folder` (summary lists the move and "1 busy day named");
  - `test_no_busy_days_skips_page`;
  - `test_back_from_summary_returns_to_page_with_names_kept`;
  - `test_show_all_lists_every_file_and_esc_closes`;
  - `test_keyboard_only_date_with_busy_day` (keys helpers, through Go and Undo, sha256 restored);
  - `test_fits_1024x600`.
- [ ] **Step 2:** Run; they fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Pass; the full suite passes (heavy wrapper).
- [ ] **Step 5:** Commit `feat: name the busy days before a Date sort`.

---

## Track B — Pick faces (branch `pick-faces`)

### Task B1: Pick faces core

**Files:**
- Create: `src/photosort/modes/pick_faces.py`
- Modify: `src/photosort/thresholds.py`, `src/photosort/modes/__init__.py` (MODES entry `("pick_faces", "Pick faces", True)` right after `"face"`, kind `"pickfaces"`, no prepare beyond the scan)
- Test: `tests/test_pick_faces.py`

**Interfaces:**
- Consumes: `face_mode.faces_of(root, rel, analyse, cache)`, `face_mode.Analyser`, `scanner.scan`, `media.kind`, `naming`.
- Produces:
  - `thresholds.PICK_FACES_JOIN = 0.45`: tuned in B3's real run; the value may change, the name may not.
  - `@dataclass(frozen=True) class FoundFace: rel: str; index: int; face: Face`.
  - `@dataclass(frozen=True) class PersonGroup: id: int; faces: tuple[FoundFace, ...]`, with a property `photos -> frozenset[str]` and `best -> FoundFace` (highest score, then largest box area).
  - `scan_faces(root, include_subfolders, analyse, cache, progress=None, cancel=None) -> list[FoundFace] | None`: photos only. None if cancelled. Raises the same error as Face mode's scan when the engine fails on every photo.
  - `group_people(faces: list[FoundFace], join: float = PICK_FACES_JOIN) -> list[PersonGroup]`: sorted by `len(photos)` descending, then by best rel. Two faces of one photo never share a group. Memory is bounded: no matrix larger than `block × n` with block ≤ 1024.
  - `@dataclass(frozen=True) class PickFolder: name: str; faces: frozenset[tuple[str, int]]; remember: bool`.
  - `check_folder_name(root: Path, name: str, taken: list[str]) -> str | None`: the error text or None. It refuses:
    - empty after cleaning;
    - " & ";
    - hidden;
    - one of the app's folder names (`scanner.SKIP_DIRS`, casefolded);
    - a name equal to an existing **file** in root;
    - a duplicate of `taken`, casefolded.
    Use the exact refusal messages of `people.py` where they exist; otherwise `"A file named X is already there"` and `"There is already a folder called X in your list"`.
  - `photo_folders(folders: list[PickFolder]) -> dict[str, list[str]]`: rel → names of the folders whose faces appear in it, in folder order.
  - `plan_pick(root, folders, choices: dict[str, str | None]) -> Plan`:
    - a photo with one folder goes there;
    - a photo with several goes to `choices[rel]`; None or missing leaves it;
    - the target is `resolve_existing_dir(root, name)/<file name>` via `TargetAllocator`;
    - a photo already in its folder is not moved;
    - summary: `"57 photos → Friend"` per folder, then `"4 left where they are"` if any.
- [ ] **Step 1: Failing tests** (fake embeddings: unit vectors plus small noise, a fixed seed):
  - `test_group_people_joins_same_person_and_splits_different`;
  - `test_two_faces_in_one_photo_never_same_group`;
  - `test_groups_sorted_by_photo_count`;
  - `test_group_people_memory_bounded`: 30 000 random 512-d faces, `tracemalloc` peak < 400 MB, finishes < 60 s. Mark it `@pytest.mark.slow` only if the suite already has that marker; otherwise keep it, and it runs under the heavy wrapper.
  - `test_scan_faces_photos_only_and_cached`;
  - `test_folder_name_refusals`;
  - `test_plan_pick_single_and_conflict_choices`;
  - `test_plan_pick_joins_existing_folder_any_case`;
  - `test_pick_undo_restores` (sha256).
- [ ] **Step 2:** Run; they fail.
- [ ] **Step 3:** Implement. The grouping algorithm is the builder's choice within the rules above. Document it in the module docstring.
- [ ] **Step 4:** Pass.
- [ ] **Step 5:** Commit `feat: Pick faces core: find, group and plan`.

### Task B2: People grid page and flow

**Files:**
- Create: `src/photosort/ui/pick_faces_page.py`, `src/photosort/ui/flows/pick_faces.py`
- Modify: `src/photosort/ui/main_window.py` (build the page, register kind `"pickfaces"`), `src/photosort/ui/home_page.py` (tile hint `"Click faces, make folders"`), `src/photosort/ui/flows/__init__.py` docstring
- Test: `tests/test_ui_pick_faces.py`

**Interfaces:**
- Consumes: B1.
- Flow:
  - `MODELS = FACE_MODELS`;
  - `start` runs `ensure_models`, then a Task that makes the engine with `window.engine_factory(settings.use_gpu)`, opens the cache and calls `scan_faces` then `group_people`, as `flows/face.py` / `flows/no_people.py` do;
  - the error path matches Face's;
  - no faces → summary `"No faces found"` with Go off.
- Page `PickFacesPage(QWidget)`:
  - `load(root, groups)`;
  - signals `continued(list[PickFolder])` and `left()`.
  - **Grid:** person tiles using the face crop (`faces.face_crop` on pool threads, generation-tagged), the text `"in N photos"` and a selected tick. One-photo groups go under a collapsible "Seen once (N)" header at the end.
  - Click or Space toggles selection. Enter or double-click opens a tile dialog that lists every face crop ticked; unticking removes it from that group for this session. Esc closes the dialog.
  - **Name box and Add** (N focuses the box; Enter in it adds): validated with `check_folder_name`; an error is shown inline. Added tiles show `"→ Friend"` and can't be selected.
  - **Folder list:** `"Friend: 2 people, 57 photos"`, a "Remember on the People page" checkbox (off), and Remove (Delete key) which frees the tiles.
  - Ctrl+Enter continues; with no folders it shows `"Make a folder first"`. Esc leave asks.
  - Strip: `Arrows move · Space pick · Enter open · N name · Del remove folder · Ctrl+Enter continue · Esc leave`.
- [ ] **Step 1: Failing tests** (a fake engine giving fixed embeddings):
  - `test_grid_shows_people_most_photos_first_and_seen_once_section`;
  - `test_select_two_tiles_add_folder_badges_them`;
  - `test_folder_name_refusals` (UI shows the message);
  - `test_open_tile_untick_face_excludes_photo`;
  - `test_remove_folder_frees_tiles`;
  - `test_no_faces_found`;
  - `test_continue_without_folder_says_make_a_folder_first`;
  - `test_fits_1024x600`.
- [ ] **Step 2:** Run; they fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Pass.
- [ ] **Step 5:** Commit `feat: Pick faces: people grid and folders`.

### Task B3: Which folder?, summary, Remember, real-data tuning

**Files:**
- Create: `src/photosort/ui/which_folder_page.py`
- Modify: `src/photosort/ui/flows/pick_faces.py`, `src/photosort/ui/main_window.py` (`after_run` hook), `README.md`
- Test: `tests/test_ui_pick_faces.py`, `tests/test_ui_keyboard_e2e.py`

**Interfaces:**
- Consumes: B1, B2.
- Which folder:
  - after `continued`, photos with 2+ folders (`photo_folders`) go to `WhichFolderPage` (`load(root, conflicts: dict[str, list[str]], faces)`, signal `chosen(dict[str, str | None])`, `left()`); with none it is skipped;
  - one row per photo: the photo thumbnail with matching faces boxed, buttons per folder `"1 Friend"`, `"2 Family"`… and `"0 Leave it here"`;
  - top: `"All to Friend"`… and `"Leave all here"`;
  - keys: ↑/↓ rows, 1–9 and 0 on the focused row, Ctrl+Enter continue (unanswered rows are left where they are; the summary counts them), Esc back to the grid with its state kept.
- Then `plan_pick` runs in a Task, and the summary has `back_to` set to the previous page.
- **Remember:**
  - the window gains `after_run: Callable[[ExecResult], list[str]] | None`. `_go` keeps it (it is not cleared by `end_flow`); `_on_executed` calls it only when `result.moved > 0` and the run was not cancelled, appends its lines to Done, and then clears it. The error path clears it, and so does any new start;
  - Pick faces sets it when any folder has `remember=True`. For each such folder:
    - `PeopleStore.add(name, mean embedding normalised, crop of best face)`;
    - or, if the name is taken (`fold` match), `add_samples(that id, all embeddings)`;
    - line: `"Friend remembered on the People page"`.
  - A store error gives the line `"Couldn't remember Friend: <error>"` and never fails the run.
- [ ] **Step 1: Failing tests:**
  - `test_conflict_rows_and_all_to_buttons`;
  - `test_digit_keys_choose_and_zero_leaves`;
  - `test_no_conflicts_skips_page`;
  - `test_remember_after_success_adds_person`;
  - `test_remember_adds_samples_to_existing_name`;
  - `test_cancelled_run_remembers_nobody`;
  - `test_undo_keeps_remembered_person`;
  - `test_keyboard_only_pick_faces` (e2e: grid → add folder → which folder → Go → Undo restores sha256);
  - `test_which_folder_fits_1024x600`.
- [ ] **Step 2:** Run; they fail.
- [ ] **Step 3:** Implement. Add a README section for the three features.
- [ ] **Step 4:** Pass; the full suite passes (heavy wrapper).
- [ ] **Step 5: Real-data tuning** (heavy wrapper, real models read-only, copies only):
  - run `scan_faces` + `group_people` on `~/PhotoSortTest/Camera`;
  - write a contact sheet per group into the scratchpad;
  - report the group count, the top 10 sizes, obvious merges and splits at join 0.40 / 0.45 / 0.50;
  - set `PICK_FACES_JOIN` to the best value and say why in the report.
- [ ] **Step 6:** Commit `feat: Pick faces: which folder, remember, README`.

---

## Task F: Integrate and verify (on `three-more`)

**Files:** merge results; any caller fixes.

- [ ] **Step 1:** Merge `central-undo`, then `busy-days`, then `pick-faces` into `three-more`. Resolve:
  - `modes/__init__.py` (both kinds);
  - `main_window.py`;
  - `AnalysisCache(root)` in `flows/pick_faces.py`, which becomes `AnalysisCache()`;
  - any `load_runs` / `related_folders` use.
- [ ] **Step 2:** Add `test_no_photosort_folder_after_any_mode`: for each mode with a fake engine where needed, run it on a tmp tree and assert no `.photosort` is created. Run the full suite with the heavy wrapper, three times for the UI files.
- [ ] **Step 3:** Commit `merge: busy days, Pick faces, central undo`.
- [ ] **Step 4** (controller): real-screen keyboard runs on `~/PhotoSortTest` copies, with SHA checks:
  - Date with a busy day named;
  - Pick faces with two folders and a conflict;
  - History: multi-undo, forget, and a renamed test folder relocated.
  Then the final whole-branch review.
