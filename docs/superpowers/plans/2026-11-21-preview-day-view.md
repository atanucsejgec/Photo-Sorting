# Preview Day View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** G in Preview shows a grid of all files of the shown file's day (modified date), all ticked; N/M move the ticked ones as one group; Ctrl+Z puts the group back; Enter opens one alone.

**Architecture:** the core (modes/preview.py) gains the day of a file and group moves/put-backs. The UI gains a DayGrid (new ui/preview_day.py, built on card_grid.CardGrid/GridCard) inside PreviewPage's stack beside the viewers; PreviewPage switches between "one" and "day" views and queues group moves on its existing queue.

**Tech Stack:** Python 3.12, PySide6, pytest-qt, uv (`--offline`).

**Spec:** docs/superpowers/specs/2026-11-21-preview-day-view-design.md

## Global Constraints
- Day = local date of the file's modified time (`os.stat().st_mtime`; the link itself if it's a broken link via `os.lstat`). Nothing read from inside files.
- Top line in the grid: `"{Fri 28 Feb 2025} · day {k} of {n} · {f} files · {t} ticked"` ("1 file" singular).
- "Nothing ticked" when N/M is pressed with no ticked file; "Finding the days…" while days are read.
- Every file starts ticked when a day opens; files left after a move stay as they were (unticked).
- Each file is moved and journalled one by one in the same run; a group is what one N/M moved; Ctrl+Z puts back the whole last group, everywhere.
- Keyboard-only use works at 1024×600, 11 pt; the strip lists the grid's keys exactly as the spec's key list.
- Thumbnails: one decode at a time per grid (CardGrid's mechanism); a day change drops late thumbnails.
- Never touch /run/media, the real app data/config, or the model files. Commits as `Atanu Biswas <154270931+atanucsejgec@users.noreply.github.com>`, no Co-Authored-By line. Tests through the flock + 6G wrapper.

## Review Focus
1. A day with hundreds of photos: the grid opens at once, thumbnails fill in, no freeze, memory stays modest.
2. Group move where one file fails (gone, permission): the others move, the failed one comes back into the grid **ticked**, in its place, with "Couldn't move <name>: …".
3. Ctrl+Z after a group move while some of that group's moves are still queued: it waits for them (it is queued after them) and puts back all.
4. Switching day/one view, or leaving, while thumbnails or moves are pending: no stale card, no crash, closing waits for the queue as now.
5. Esc in a file opened from the grid goes back to the grid, never to the Leave question; Esc in the plain one-at-a-time view still asks to leave.

---

### Task 1: Days and groups in the core

**Files:** Modify `src/photosort/modes/preview.py`; Test `tests/test_preview.py`.

**Interfaces (produces):**
- `file_day(path: Path) -> date | None` — the local date of the modified time; None if it can't be read.
- `day_text(d: date) -> str` — `"Fri 28 Feb 2025"` (English short weekday and month, no leading zero).
- `PreviewSession.move(rel, target_folder, group: int | None = None) -> str` — as now; `group` tags the move (None: a group of its own).
- `class PutBackError(Exception)` with `.back: list[str]` (put back before the failure) and `.error: BaseException`.
- `PreviewSession.put_back_group() -> list[str]` — puts back every live move of the last group, last first; returns where each is now, in put-back order; `[]` if nothing is left. On a failure: the ones already back stay back; the failed one and the rest stay live (a FileNotFoundError one is dropped as `put_back` does); raises `PutBackError`.
- `PreviewSession.put_back()` keeps working (one file).

- [ ] Tests: `test_file_day_is_the_modified_date` (os.utime to a set time → that local date; a missing path → None), `test_day_text`, `test_group_moves_put_back_together` (3 moves group 7, 1 move group 8: first put_back_group returns the group-8 one, the second returns all three in reverse order, the third `[]`; files back byte-identical; Undo of the run afterwards leaves the tree as at start), `test_untagged_moves_are_groups_of_one`, `test_put_back_group_partial_failure` (the middle file deleted from its target: PutBackError with `.back` == the last one; next call puts back the first; the deleted one is dropped).
- [ ] Run, see them fail; implement; run test_preview.py and test_undo.py; commit `feat: Preview core knows each file's day and moves in groups`.

### Task 2: The day grid

**Files:** Create `src/photosort/ui/preview_day.py`; Modify `src/photosort/ui/preview_page.py`; Test `tests/test_ui_preview_day.py` (new; reuse helpers from test_ui_preview.py by importing them, including its autouse `shown_in_folder` stub pattern so no test opens a real file manager).

**Interfaces:**
- Consumes Task 1.
- `class DayGrid(QWidget)` in preview_day.py: `show_day(root, rels: list[str], ticked: set[str] | None = None, focus: str | None = None)`, `ticked() -> list[str]` (in grid order), `focused() -> str | None`, `toggle_focused()`, `tick_all_or_none()`, `remove(rels)`, `insert(rel, ticked: bool)`, signal `open_file(str)`; built on CardGrid/GridCard; non-photos show kind (`file_kind`) and name.
- PreviewPage: `view() -> str` (`"one"` or `"day"`), `day_text() -> str` (the grid's top line), key G, buttons "◀ Day before (PgUp)" / "Day after (PgDn) ▶"; page moves use `group=` ids and Ctrl+Z uses `put_back_group` in both views.

- [ ] Tests (each by keyboard unless the name says click):
  - `test_g_opens_the_day_all_ticked` (files with utime on 2 days; G → only that day's files, ordered by mtime, all ticked, top line exact, strip exact)
  - `test_space_and_a_tick` (Space unticks focused; A ticks all; A again unticks all)
  - `test_n_moves_the_ticked_and_keeps_the_unticked` (one unticked → after N the 2 ticked are in Not needed, the unticked one stays in the grid unticked; journal has 2 moves)
  - `test_m_digit_moves_the_ticked_to_a_slot`
  - `test_nothing_ticked_says_so`
  - `test_empty_day_opens_the_next_day`, and `test_last_day_emptied_ends_the_preview`
  - `test_ctrl_z_puts_back_the_whole_group` (in grid, and again in the one view after a grid move)
  - `test_enter_opens_alone_and_esc_comes_back` (Enter → one view on that file, + zooms, Esc → grid of its day with the same ticks; Esc in plain one view still asks to leave)
  - `test_pgup_pgdn_change_day`
  - `test_a_group_move_with_one_failure` (one file removed under it: the rest move; message "Couldn't move <name>: …"; it returns to the grid ticked)
  - `test_day_grid_fits_1024x600`
  - `test_a_big_day_opens_at_once` (300 small jpegs on one day: G shows the grid within 1 s; thumbnails done later)
- [ ] Run, see them fail; implement; run test_ui_preview*.py 3×, then the full suite once through the wrapper; commit `feat: Preview's day view: a whole day at once, ticked, moved together`.
