# Preview anywhere — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Preview moves files to any folder (including other drives), with Undo still order-safe, and the slot editor accepts a pasted path.
**Architecture:** Journal paths outside the root are absolute, and the run meta gains `also_roots`. Every journal reader resolves paths through one helper. Undo's blocking and dependents checks also scan the runs related to each also_root.
**Tech Stack:** Python 3.12, PySide6.
**Spec:** docs/superpowers/specs/2026-11-20-preview-anywhere-design.md (binding), which amends docs/superpowers/specs/2026-11-20-preview-design.md.

## Global Constraints
- One helper, `journal.abs_path(root: Path, p: str) -> Path`: an absolute p is returned as-is (a Path); otherwise `root / p`. Every reader of a move's src/dst or of mkdir/rmdir paths uses it. No `root / m.dst` may remain anywhere.
- An outside dst is stored as an absolute POSIX string. The run meta has `"also_roots": list[str]`, absolute and normalised with `norm_root`; it is missing on old runs and read as `[]`.
- Undo refuses rather than risk a wrong order or a partial undo:
  - a newer run touching any placed outside file blocks;
  - a missing also_root folder refuses with "<folder> isn't there — is the drive plugged in?" before anything moves.
- Existing runs, all of which are relative, behave exactly as before. The existing undo, journal, history and migrate tests stay green unchanged.
- The exact messages: "Folder not found", "The list is full", "Already in the list (slot N)", "<name> isn't there — is the drive plugged in?", "Moving <name> to <folder>…".

## Review Focus
1. A later sort in the outside target moves a Preview-placed file → Preview's undo is blocked; after the later sort is undone, Preview's undo restores byte for byte.
2. A cross-drive move (EXDEV) and its undo (EXDEV back) restore byte for byte, and the original is removed only after verification.
3. A target drive that's missing at undo → refused, nothing moved, not marked undone.
4. An old run (relative only) undoes as before. Mixed inside and outside moves in one run, with put-backs, undo correctly.
5. A name clash in the outside target → the allocator renames, never overwrites.

---

### Task 1: Core — absolute outside paths in journal, undo, history readers; PreviewSession anywhere
**Files:** `src/photosort/journal.py`, `src/photosort/undo.py`, `src/photosort/modes/preview.py`, `src/photosort/ui/history_page.py` (`folder_found`, `bring_in_for_undo` for also_roots, `looks_like` if it reads dst), and any other reader found by `grep -n "root / " src/photosort`. Tests: `tests/test_preview.py`, `tests/test_undo.py`, `tests/test_journal.py`.
**Interfaces produced:**
- `journal.abs_path(root, p) -> Path`
- `RunJournal.add_also_root(folder: Path)`
- `RunInfo.also_roots: tuple[Path, ...]`
- `PreviewSession.move(rel, target_folder)` now accepts any existing folder. It refuses only the file's own folder (no-op, as now), app folders, and a missing outside folder (FileNotFoundError with the spec's message). Inside the root, a missing folder is created as now.
- `preview.target_problem(root, folder) -> str | None` replaces `slot_problem`'s outside message.
- `undo.undo_run` checks also_roots exist first (raising `FolderMissing` with the spec's message) and blocks on newer runs in `related_roots` of every also_root.
- Strict `bring_in_for_undo(root)` also covers also_roots.
- [ ] Failing tests, one per Review Focus item (1–5), plus:
  - `test_abs_path_relative_and_absolute`;
  - `test_old_runs_unchanged` (an existing fixture run undoes the same);
  - `test_dependents_sees_outside_placed_files`;
  - `test_history_folder_found_with_outside_moves`.
- [ ] Run them, see them fail. Implement. Run `test_preview test_undo test_journal test_migrate test_ui_history*` → PASS. Run the full suite through the wrapper.
- [ ] Commit: `feat: Preview moves anywhere; undo stays order-safe across folders`

### Task 2: UI — path box in the slot editor, picker anywhere, slot states, moving status
**Files:** `src/photosort/ui/preview_page.py`, `tests/test_ui_preview.py`.
**Consumes:** Task 1's `target_problem`, `PreviewSession.move`.
- The path box, exactly as the spec describes. Paste works with Ctrl+V and a `file://` URL. Enter adds to the first empty slot.
- Slots are greyed only when the folder is missing. The outside-root refusal message is removed.
- Other folder…'s picker starts at the root and accepts anything that `target_problem` allows.
- "Moving <name> to <folder>…" shows when a queued move takes over 0.5 s, and clears when it's done.
- [ ] Failing tests:
  - `test_paste_path_adds_to_first_free_slot`
  - `test_paste_missing_path_says_folder_not_found`
  - `test_paste_duplicate_and_full`
  - `test_m_digit_outside_root_moves_and_undo_restores`
  - `test_cross_drive_move_shows_moving_status` (with an EXDEV monkeypatch and a slow copy)
  - `test_missing_outside_slot_says_plug_in`
  - `test_other_folder_outside_root_now_allowed`, which replaces the old refusal test
- [ ] Run `test_ui_preview.py` 3 times, then the full suite through the wrapper.
- [ ] Commit: `feat: Preview folder list takes pasted paths; any folder works`
