# UI Refresh (dark tech look, sidebar, full keyboard use) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle every Photo Sorter screen in the owner's chosen dark tech look, with a left sidebar. Make the whole app usable by keyboard alone, with a small strip on each screen listing exactly the shortcuts that work there.

**Architecture:**
- One stylesheet and token module (`ui/theme.py`) styles the whole app.
- Bundled SVG icons (`ui/icons.py`).
- A shortcut module (`ui/shortcuts.py`) both binds each key and records it. The strip is therefore built from the same list the keys are bound from, so it can never disagree with them.
- `main_window.py` (958 lines) is split first, with no behaviour change, so the restyle lands on small files.

**Tech Stack:** Python 3.12, PySide6, pytest-qt (offscreen), uv. No new runtime dependencies. The SVG icons are vendored files.

**Spec:** `docs/superpowers/specs/2026-10-25-ui-refresh-design.md`. Read it in full; its decisions are binding.

## Global Constraints

- **No behaviour change to sorting:**
  - same plans, same summary → Go → Done → Undo flow;
  - nothing moves without the summary screen;
  - no key skips the summary.
- **Palette** (spec): near-black background `#0d1117`, panels and tiles `#161b22`, borders `#30363d`/`#262c36`, text `#e6edf3`, muted text `#8b949e`.
  - The accent gradient is `#06b6d4` → `#8b5cf6`; brand text uses `#22d3ee` → `#a78bfa`.
  - The focus ring is cyan `#22d3ee`, a 2 px ring plus a soft glow.
  - All of these are tokens in `ui/theme.py`; no colour literals anywhere else.
- **Sidebar items, in order:** "Sort", "History", "People", "Settings". The sidebar is dimmed and can't be focused or clicked from Start until the Done screen, and comes back on Done.
- **Home:**
  - folder field (typing and pasting still work), file count, Browse;
  - "Include subfolders" switch;
  - 11 mode tiles, 4 columns, in this order: Date, Face, Duplicates, Repeated shots, Content, Notes & Pages, Bad photos, Events, Location, Camera, Type / source;
  - each tile's hint is the spec's text exactly;
  - no Start button: Enter or a click on a tile starts that mode;
  - focus starts on the last-used mode's tile, else Date.
- **Keys everywhere:**
  - arrows move;
  - Enter does the focused thing;
  - Space ticks or unticks;
  - Esc goes back;
  - Tab / Shift+Tab move between regions.
  - On Home: F opens the folder picker and S switches subfolders, only while focus is outside the folder field.
  - In the folder field: Enter confirms the path and moves focus to the tiles; Esc leaves the field.
- **Review screens** (Duplicates/Repeated, Face questions, Bad photos, Events, Content/Notes review):
  - Esc steps back one.
  - On the first item, or on a grid screen, Esc asks "Leave this review? Your answers so far will be lost." with **Stay** focused; Leave goes Home.
  - Ctrl+Enter goes to the summary with the answers so far.
  - On grids, Enter opens the photo big.
- **Summary:** Go has focus. **Done:** the default button is "Home", never Undo.
- **Shortcut strip:**
  - at the bottom of every screen and every in-app dialog;
  - small: about 85 % of the body font, key chips plus one word each;
  - it lists the screen's keys plus the focused control's keys, and nothing else;
  - it is reference text, not clickable.
- **Dialogs:** every `QMessageBox` becomes a themed dialog with a strip.
- **Native pickers stay native:** the folder picker and the Face naming photo picker.
- **Labels show `&` literally.** "Notes & Pages" must never render as "Notes _Pages".
- **Windows-ready:** no Linux-only fonts or APIs; system UI font; no runtime network.
- **Mouse use still works everywhere.**
- **Process:**
  - opus agents; commits with no Co-Authored-By line; the owner's noreply identity;
  - every test run under `systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 …`, one at a time.
  - Tests use tmp_path and fakes only. Never touch `/run/media/`, `~/PhotoSortTest` or `~/.local/share/photo-sorter`.

## Review Focus

1. **Focus survives async updates.** When thumbnails arrive or the grid reflows on resize, focus stays on the same card and the arrows follow the new layout. Test in Task 4.
2. **Esc auto-repeat or a double Esc on the first item.** The confirmation opens once, with Stay focused. Holding Esc or pressing Enter by accident keeps you in the review. Test in Task 4.
3. **Enter held on the summary.** Go fires exactly once. Done's default is Home, so the repeat lands harmlessly on Home's tiles and starts nothing: a tile needs a fresh key press after the screen changes. Test in Task 5.
4. **Folder field typing.** Typing "F", "S" or a path with spaces in the field never triggers a shortcut. A pasted path with a trailing newline or spaces is trimmed. Test in Task 3.
5. **A small window or 125 % scaling** (1024×600). Tiles reflow to fewer columns, the strip wraps instead of being clipped, and the arrows follow the visual grid. Test in Task 3.

---

## File Structure

```
src/photosort/ui/main_window.py        shell only after Task 1 (stack, sidebar, routing); ≤ ~450 lines
src/photosort/ui/flows/__init__.py     NEW (Task 1)
src/photosort/ui/flows/*.py            NEW per-flow handlers + worker functions: plain, groups, bad, events, face, clip (Task 1)
src/photosort/ui/theme.py              NEW tokens + QSS + apply(app) (Task 2)
src/photosort/ui/icons.py, ui/icons/   NEW SVG loader + vendored line icons (Task 2)
src/photosort/ui/shortcuts.py          NEW bind/describe/keys_for + ShortcutStrip (Task 2)
src/photosort/ui/dialogs.py            NEW themed ask/warn/info (Task 2)
src/photosort/ui/sidebar.py            NEW (Task 3)
src/photosort/ui/home_page.py          NEW folder + switch + ModeTile grid (Task 3)
src/photosort/ui/history_page.py       NEW from history_dialog (Task 3)
src/photosort/ui/people_page.py        NEW from RememberedPeopleDialog (Task 3)
src/photosort/ui/settings_page.py      NEW from settings_dialog, saves on change (Task 3)
src/photosort/ui/*_page.py, dialogs    restyled + keys (Tasks 4–5)
```

---

### Task 1: Split main_window.py (no behaviour change)

**Files:**
- Create: `src/photosort/ui/flows/__init__.py`, `src/photosort/ui/flows/{plain,groups,bad,events,face,clip}.py`
- Modify: `src/photosort/ui/main_window.py`

**Interfaces:**
- Produces:
  - `MainWindow.back_to: Callable[[], None] | None` replaces the five review fields (`_groups`, `_bad`, `_events`, `_clip`, face state) and the precedence chain in `_back_from_summary`.
    - Each flow sets `back_to` when it reaches the summary.
    - `_start`, `_go` and every error path set it to None.
    - Back on the summary calls it, else goes Home.
  - Each flows module exposes `start(window, root, include_subfolders, spec)` plus its handlers. Module-level worker functions move with their flow; tests that import them get updated import paths.
  - Every public attribute that tests use on `MainWindow` stays: `folder_edit`, `mode_buttons`, `start_button`, the page attributes, `engine_factory`/`clip_engine_factory`, `show_error`.
- [ ] **Step 1:** Run the full suite (capped) and record the count as the baseline.
- [ ] **Step 2:** Move each flow into its module, one flow per commit if that helps. The only test changes allowed are import paths.
- [ ] **Step 3:** Add `test_back_to_cleared_on_start_and_go` to `tests/test_ui_flow.py`. Each flow sets `back_to` at the summary; Start, Go and the error paths clear it.
- [ ] **Step 4:** Run the full suite (capped) and the UI files 3× in sequence. Expected: baseline + 1, no other change.
- [ ] **Step 5: Commit** `refactor: one flow per module; one back-to for the summary`.

---

### Task 2: Theme, icons, shortcut strip, themed dialogs

**Files:**
- Create: `src/photosort/ui/theme.py`, `src/photosort/ui/icons.py`, `src/photosort/ui/icons/*.svg`, `src/photosort/ui/shortcuts.py`, `src/photosort/ui/dialogs.py`, `tests/test_ui_theme.py`, `tests/test_ui_shortcuts.py`
- Modify: `src/photosort/app.py` (call `theme.apply(app)`), and every `QMessageBox` use in `src/photosort/ui/` (12 today) → `dialogs.*`

**Interfaces:**
- Produces:
  - `theme.COLORS: dict[str, str]` (the token names: `bg`, `panel`, `border`, `text`, `muted`, `accent_from`, `accent_to`, `focus`, `danger`), `theme.stylesheet() -> str` and `theme.apply(app: QApplication) -> None`. `apply` sets the palette and the stylesheet.
  - Object names that QSS targets: `primaryButton`, `modeTile`, `sidebar`, `shortcutStrip`, `card`, `muted`.
  - `icons.icon(name: str) -> QIcon`, `icons.MODE_ICONS: dict[str, str]`, which maps every MODES key to an SVG name. The SVGs are line icons from a permissively licensed set (e.g. Lucide, ISC licence). Ship the licence file next to them, and add `package-data` so the SVGs are installed.
  - `shortcuts.bind(owner: QWidget, keys: str, label: str, word: str, slot: Callable[[], None], context=Qt.WidgetWithChildrenShortcut) -> QShortcut`. It creates the QShortcut and records `(label, word)` on the owner.
  - `shortcuts.describe(owner: QWidget, label: str, word: str) -> None` records keys the owner handles itself, such as arrows in `keyPressEvent`.
  - `shortcuts.keys_for(focus: QWidget | None, screen: QWidget) -> list[tuple[str, str]]`: the focused control's records (the focus widget or its nearest recorded ancestor below `screen`), then the screen's own records, de-duplicated by label.
  - `ShortcutStrip(QWidget)`, objectName `shortcutStrip`: `show_for(screen: QWidget) -> None`. It follows `QApplication.focusChanged` and redraws from `keys_for`. It wraps, so it is never clipped.
  - `dialogs.ask(parent, title, text, yes: str, no: str, default: Literal["yes", "no"] = "no") -> bool`, `dialogs.warn(parent, title, text) -> None`, `dialogs.info(parent, title, text) -> None`. All are themed, with a strip: "←→ choose · Enter pick · Esc no/close".
  - `ui.labels.literal(text: str) -> str` doubles `&` for any `QAbstractButton`/`QLabel`-with-mnemonic text. Use it on all mode names.
- [ ] **Step 1: Write failing tests:**
  - `apply` sets a dark window colour: the palette `Window` equals `COLORS["bg"]`;
  - every MODES key has an icon whose `QIcon` is not null;
  - `bind` makes a working shortcut (qtbot.keyClick triggers the slot) and its label shows in `keys_for`;
  - a `describe`d key shows too;
  - focus moving into a control with its own records puts those first, and focus leaving removes them;
  - the strip text updates on focus change;
  - `dialogs.ask` with `default="no"`: Enter → False, ←→ then Enter on Yes → True, Esc → False;
  - `literal("Notes & Pages")` renders as "Notes & Pages" on a QRadioButton/QPushButton (check `text()` shown via `QAbstractButton.text().replace("&&", "&")`, and that no mnemonic is registered);
  - no `QMessageBox` remains in `src/photosort/ui` (grep test).
- [ ] **Step 2:** Run `systemd-run … uv run pytest tests/test_ui_theme.py tests/test_ui_shortcuts.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. Replace the QMessageBox uses, keeping their texts. Existing tests that patch `QMessageBox` are switched to patching `dialogs.ask`/`warn`/`info`.
- [ ] **Step 4:** Run the full suite (capped). Expected: all pass.
- [ ] **Step 5: Commit** `feat: dark theme, icons, shortcut strip and themed dialogs`.

---

### Task 3: Sidebar, Home tiles, and the History / People / Settings pages

**Files:**
- Create: `src/photosort/ui/sidebar.py`, `src/photosort/ui/home_page.py`, `src/photosort/ui/history_page.py`, `src/photosort/ui/people_page.py`, `src/photosort/ui/settings_page.py`, `tests/test_ui_home.py`, `tests/test_ui_pages.py`
- Modify: `src/photosort/ui/main_window.py`, `src/photosort/settings.py` (`last_mode: str | None`, validated against the MODES keys), the tests that use `start_button` (84 references across 5 files) → `window.mode_buttons[key].click()`
- Delete or keep as thin wrappers: `history_dialog.py`, `settings_dialog.py`, `RememberedPeopleDialog`. Keep `undo_summary`, and keep any function other code imports.

**Interfaces:**
- Consumes: Task 2's `theme`, `icons`, `shortcuts.bind/describe`, `ShortcutStrip`, `dialogs`.
- Produces:
  - `Sidebar(QWidget)`, objectName `sidebar`: `items: dict[str, QPushButton]` (keys "sort", "history", "people", "settings"), `set_locked(locked: bool)`, and the signal `chosen(str)`. Up/Down move between items, Enter picks, Tab leaves.
  - `ModeTile(QAbstractButton)`, objectName `modeTile`: icon, name and hint. Clicking it or pressing Enter starts the mode.
  - `HomePage`:
    - `folder_edit: QLineEdit`, `browse_button`, `subfolders_switch` (a checkable styled QCheckBox; keep the name `subfolders_check` as an alias for tests), `count_label`;
    - `mode_buttons: dict[str, ModeTile]`;
    - signal `start_requested(str)`.
    - Arrow keys move between tiles by visual position after reflow (columns = max(1, width // tile_min_width)).
    - The file count runs on a worker Task with a generation token. It counts photos and videos, honouring the subfolders switch.
  - `MainWindow.mode_buttons` and `MainWindow.folder_edit` proxy to the HomePage. `MainWindow.start_button` is removed.
  - The pages:
    - `HistoryPage` has an Undo action on the selected run, behind `dialogs.ask` (default "no").
    - `PeoplePage` has Forget (the Delete key) behind `dialogs.ask`.
    - `SettingsPage` saves each change at once; the "Content categories…" button opens the category editor.
  - Whenever a sort flow is active, the sidebar is locked by `MainWindow`.
- [ ] **Step 1: Write failing tests** (`tests/test_ui_home.py`, `tests/test_ui_pages.py`):
  - the tile order and hints are exactly the Global Constraints values;
  - focus starts on the Date tile, or on `last_mode`'s tile when it is set;
  - →→↓ from Date lands on "Bad photos" with 4 columns;
  - after resizing to a 2-column width, ↓ from Date lands on "Duplicates" (**Review Focus 5**);
  - Enter on a tile with a valid folder starts that mode, and `last_mode` is saved; with no folder, a themed warning appears;
  - **Review Focus 4:**
    - typing "fs" in the folder field changes its text and toggles nothing;
    - F outside the field opens the picker (patched);
    - S toggles the switch;
    - a pasted `"/tmp/x \n"` is trimmed to `/tmp/x`;
  - the count label shows the number of photos and videos, and changes when subfolders is toggled;
  - the sidebar:
    - Up/Down/Enter switch pages;
    - it is locked during a flow (a fake slow worker) and unlocked on Done;
  - History page: Undo asks first (No keeps the run, Yes undoes it); the "Choose a folder first" text shows when there is no folder;
  - People page: Delete → ask → Forget;
  - Settings page: changing the gap spin box writes settings at once;
  - the Home strip lists exactly: "←↑↓→ choose", "Enter start", "F folder", "S subfolders", "Tab sidebar". While the folder field has focus, it lists "Enter use this folder · Esc leave".
- [ ] **Step 2:** Run the new test files (capped). Expected: FAIL.
- [ ] **Step 3:** Implement, and update the 5 UI test files' `start_button` uses.
- [ ] **Step 4:** Run the full suite (capped) and the UI files 3× in sequence. Expected: all pass.
- [ ] **Step 5: Commit** `feat: sidebar, mode tiles and pages for history, people and settings`.

---

### Task 4: Review screens by keyboard

**Files:**
- Modify: `src/photosort/ui/review_page.py`, `face_question_page.py`, `bad_photos_page.py`, `category_review_page.py`, `events_page.py`, the relevant `flows/*.py`
- Create: `tests/test_ui_review_keys.py`

**Interfaces:**
- Consumes: Task 2's `shortcuts`, `dialogs.ask`; Task 3's locked sidebar.
- Produces:
  - a shared `shortcuts.confirm_leave(parent) -> bool`, which calls `dialogs.ask(parent, "Leave this review?", "Your answers so far will be lost.", yes="Leave", no="Stay", default="no")`;
  - a grid focus helper in `shortcuts.py`, `GridNavigator(cards: list[QWidget], columns: Callable[[], int])`, used by the bad, category and duplicates card grids. It keeps the focused card's identity across reloads and reflows.
- Keys per screen (each one bound with `shortcuts.bind`/`describe`, so the strip shows it):
  - **Duplicates/Repeated:**
    - ←→ move between files; Space toggles keep/set aside;
    - Enter opens full screen;
    - K keep all; 1 keep one (sharpest); N skip;
    - Ctrl+→ confirm & next group;
    - Esc: previous group, or on the first group `confirm_leave`;
    - Ctrl+Enter stop reviewing (summary).
  - **Face questions:**
    - Y yes, N no, Space skip;
    - Esc: previous, or on the first `confirm_leave`;
    - Ctrl+Enter stop asking (summary).
  - **Bad photos:**
    - ←↑↓→ move; Space tick; Enter view big;
    - A tick all; U untick all;
    - Ctrl+Enter continue;
    - Esc `confirm_leave`.
  - **Content/Notes review:**
    - ←↑↓→ move;
    - Enter opens the card's category picker (in the picker: ↑↓ choose, Enter set, Esc close);
    - L leave this one where it is; Shift+L leave all;
    - Space view big;
    - Ctrl+Enter continue;
    - Esc `confirm_leave`.
  - **Events:**
    - ↑↓ move rows; Space skip/unskip;
    - F2 or Enter rename (in the name box: Enter done, Esc stop editing);
    - M merge with next; S split;
    - Ctrl+Enter continue;
    - Esc `confirm_leave`.
- [ ] **Step 1: Write failing tests:** one per screen, keyboard only (no `.click()`): drive a small fake flow to the summary, and assert the plan matches the keys pressed. Also:
  - **Review Focus 2:**
    - Esc on the first item opens one dialog with Stay focused;
    - a second Esc closes it and stays in the review;
    - Enter keeps the review;
    - Leave goes Home with nothing planned.
  - **Review Focus 1:** focus card 5, deliver thumbnails and resize to fewer columns: card 5 still has focus, and ↓ moves by the new column count.
  - Each screen's strip equals its bound plus described keys.
- [ ] **Step 2:** Run `tests/test_ui_review_keys.py` (capped). Expected: FAIL.
- [ ] **Step 3:** Implement. Restyle the cards with the `card` object name and a focus ring. Existing button attributes stay, so the mouse paths and the old tests still work.
- [ ] **Step 4:** Run the full suite (capped) and the UI files 3× in sequence. Expected: all pass.
- [ ] **Step 5: Commit** `feat: review screens by keyboard, with a leave check`.

---

### Task 5: Progress, summary, done, the remaining dialogs, and keyboard-only end-to-end tests

**Files:**
- Modify: `main_window.py`/`flows` (progress, summary, done), `face_setup_page.py`, `face_naming_dialog.py`, `category_editor.py`, `photo_preview.py`, `fullscreen_viewer.py`, `model_download.py`, the README (keyboard section)
- Create: `tests/test_ui_keyboard_e2e.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Keys:
  - **Progress:** Esc cancels; the "Stopping…" behaviour is unchanged.
  - **Summary:** Go focused; Enter Go; Esc back (calls `back_to`); ←→ switch buttons; "Show all moves" is reachable by Tab, Enter opens it.
  - **Done:** Home focused (Enter); U undo this run (asks first, default No); Tab to the others.
  - **Face setup:** ↑↓ people; Space tick; Enter start sorting; A add person; Delete forget.
  - **Face naming dialog:** Tab between faces and the name box; Enter save; Esc cancel.
  - **Category editor:** ↑↓; Insert or A add; F2 rename; Delete remove; Alt+↑/Alt+↓ move; R reset; Enter OK; Esc cancel.
  - **Photo preview / full-screen viewer:** ←→ previous/next where it has them; Space tick where it has one; Esc close.
  - **Model download question:** a themed `dialogs.ask` with default "no" (a download needs a deliberate Yes).
- [ ] **Step 1: Write failing tests** (`tests/test_ui_keyboard_e2e.py`). These are keyboard-only runs, using only `qtbot.keyClick(s)` after the window is shown and with no `.click()`, over tmp photos with fake engines. One test per flow family:
  - Date (plain): Home → type the folder, Enter → arrows to Date, Enter → summary → Enter (Go) → Done → U → Yes → files restored.
  - Duplicates (groups review).
  - Face (setup → questions → summary → Go).
  - Events (rename via F2, merge via M, Ctrl+Enter, Go).
  - Content (picker via Enter, Ctrl+Enter, Go).
  - History page Undo.
  - Settings page change.
  - **Review Focus 3:** on the summary, hold Enter (`keyClick` with `autorep=True` ×3). `execute` is called exactly once, Done shows with Home focused, and no new flow starts.
  - Every stacked page and every in-app dialog shows a strip equal to its bound plus described keys (iterate over pages and the dialogs above).
- [ ] **Step 2:** Run the file (capped). Expected: FAIL.
- [ ] **Step 3:** Implement. Add a "Keyboard" section to the README: the common keys plus "every screen lists its keys at the bottom".
- [ ] **Step 4:** Run the full suite (capped) and the UI files 3× in sequence. Expected: all pass.
- [ ] **Step 5: Commit** `feat: keyboard through summary, done and dialogs; keyboard-only tests`.

---

### Task 6: Real-screen check and deploy (controller)

- [ ] **Step 1:** Take offscreen grabs of every page and dialog at 1280×800 and 1024×600 into the scratchpad. Look at each one: contrast, clipping, the focus ring, the strip.
- [ ] **Step 2:** On the real display, with the test copy (`~/PhotoSortTest/Camera`), run one sort per flow family by keyboard only, Undo each one, and check the SHA-256 of every file against a snapshot.
- [ ] **Step 3:** Final whole-branch review → fixes → merge to main → deploy `~/PhotoSorter` (`git checkout --detach`, `uv sync --offline`) → tell the owner.
