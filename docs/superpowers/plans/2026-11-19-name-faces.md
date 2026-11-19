# Name faces in Pick faces — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pick faces tiles show a name: a known person's automatically, or one the owner types with R. A typed name is saved straight to the People page with the tile's ticked faces.

**Architecture:**
- A pure scoring helper and a pure save helper live in core code (`modes/pick_faces.py`, plus the flow's existing `_remember_data`).
- The page gains a name line per tile, a "Who is this?" dialog, and a `name_requested` signal.
- The flow fills the known names on load, and runs saves on a pool thread without leaving the page.

**Tech Stack:** Python 3.12, PySide6, numpy, the existing PeopleStore.

**Spec:** docs/superpowers/specs/2026-11-19-name-faces-design.md

## Global Constraints
- A known name is shown when the best `people.person_scores` score of the group's normalised mean of ticked-face embeddings is ≥ `thresholds.FACE_SURE` (0.45). Otherwise the tile shows a muted "No name".
- The faces saved are the tile's ticked faces only (not those in the page's left-out set).
- Name check: `people.name_problem`. An existing person matches by `fold(name)`, the same comparison `flows/pick_faces._remember` uses. If one exists: `add_samples(id, embeddings)`. If not: `add(name, normalised mean, picture)` then `add_samples(id, embeddings)`.
- Key **R** on a focused tile, or a click on the name line, opens the dialog titled "Who is this?". Enter saves and Esc cancels; focus starts in the box, which is prefilled with the current name. R is paused while any text box has focus.
- While saving, the name line says "Saving…". If a save fails, the old name stays and the page's message line shows "Couldn't save <name>: <error>".
- The folder box prefill: on N (or a click) into an empty box, when every picked tile has the same non-empty name, fill in that name, selected.
- Tests use a tmp PeopleStore only (make_window's). Never use the default PeopleStore().
- Commit author: Atanu Biswas <154270931+atanucsejgec@users.noreply.github.com>. No Co-Authored-By line.
- Heavy runs go through: `flock <scratchpad>/heavy.lock systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0 timeout 900 <cmd>`, one at a time.

## Review Focus
1. **The owner unticks a wrong face, then names the tile.** The unticked face's embedding must not be saved. This is pinned in Task 2's test.
2. **Naming the same tile twice with the same name.** The samples are added again, and there is no duplicate person. Pinned in Task 1.
3. **Pressing R while typing a folder name.** It types "r" and opens no dialog. Pinned in Task 2.
4. **The People page is empty or unreadable.** All tiles show "No name", and the page still loads. Pinned in Task 1, where an unreadable store gives no names.
5. **A long name at 1024×600.** The name line elides and keeps the tile's width, and the tooltip holds the full name. Pinned in Task 2.

---

### Task 1: Core — known names and saving a name

**Files:**
- Modify: `src/photosort/modes/pick_faces.py` (add `known_names`)
- Modify: `src/photosort/ui/flows/pick_faces.py` (add `save_name`, reusing `_remember_data` and the rules in `_remember`; `_remember` must call the shared helper so the rules aren't duplicated)
- Test: `tests/test_pick_faces.py`, `tests/test_ui_pick_faces.py` (the flow-level save test)

**Interfaces:**
- Produces: `known_names(groups: list[PersonGroup], people: list[Person], left_out: set[tuple[str, int]] = frozenset()) -> dict[int, str]`. It maps a group index (its position in `groups`) to a name, and leaves out a group whose faces are all left out.
- Produces: `save_name(store: PeopleStore, root: Path, name: str, faces: list[tuple[str, Face]]) -> str`. It returns the saved, cleaned name and raises on a refused name (ValueError carrying `name_problem`'s message) or a store or disk error. It runs on a worker thread (it reads a photo for the picture).

- [ ] **Step 1: Failing tests**, using synthetic unit embeddings:
  - `test_known_names_shows_a_remembered_person`: a group whose mean has score ≥ 0.45 to the person "Rahul" → `{0: "Rahul"}`.
  - `test_known_names_below_sure_is_no_name`: best score 0.40 → `{}`.
  - `test_known_names_ignores_left_out_faces`: with the matching faces left out, the result is `{}`.
  - `test_known_names_no_people`: `[]` → `{}`.
  - `test_save_name_new_person_saves_every_face` (tmp store, a real small jpg): a new person, and the samples count equals the number of faces plus 1 if `add` stores the mean.
  - `test_save_name_existing_name_adds_samples_no_duplicate`: "rahul" when "Rahul" exists → still 1 person, with more samples.
  - `test_save_name_refuses_bad_name`: `""` and a `"/"`-containing name raise ValueError, and the store is unchanged.
  - The existing Remember tests stay green.
- [ ] **Step 2:** Run them and see them fail.
- [ ] **Step 3:** Implement. The mean is normalised with `np.linalg.norm`. `_remember` is refactored to use the same add-or-add_samples helper.
- [ ] **Step 4:** Run `uv run pytest tests/test_pick_faces.py tests/test_ui_pick_faces.py -q` and expect PASS.
- [ ] **Step 5:** Commit: `feat: Pick faces knows remembered people and can save a name`.

### Task 2: UI — name line, "Who is this?" dialog, flow wiring

**Files:**
- Modify: `src/photosort/ui/pick_faces_page.py`:
  - `PersonTile` gets a name line between the crop and the count, with `set_name(name: str | None)` and `set_saving()`;
  - add a `NameDialog` (or similar);
  - add the `name_requested = Signal(int, str)` signal (tile_id, name), and the methods `set_tile_name(tile_id, name)`, `name_failed(tile_id, message)`, `set_known_names(by_group_index: dict[int, str])` and `ticked_faces(tile_id) -> list[tuple[str, int]]`;
  - add the R key in the help strip ("R name"), and update the module docstring.
- Modify: `src/photosort/ui/flows/pick_faces.py`:
  - `_on_found` sets the known names after `load`, wrapping the `people_store.people()` call so an error means no names;
  - connect `name_requested` to run `save_name` on a pool thread, without the progress page and without leaving the page;
  - on success, `set_tile_name`; on error, `name_failed`.
- Test: `tests/test_ui_pick_faces.py`

**Interfaces:** Consumes `known_names` and `save_name` from Task 1.

- [ ] **Step 1: Failing tests** (real window from make_window, with a tmp PeopleStore):
  - `test_tile_shows_known_name_and_no_name`
  - `test_r_names_a_tile_and_saves_to_people`: R, type "Asha", Enter → wait until the tile shows "Asha", the store has Asha, and the samples equal the ticked faces.
  - `test_unticked_face_is_not_saved`: untick one face in the faces dialog, name the tile → its embedding is not in Asha's samples.
  - `test_r_while_typing_folder_name_types_r`
  - `test_esc_cancels_naming_nothing_saved`
  - `test_bad_name_is_refused_in_dialog`
  - `test_save_failure_keeps_old_name_and_says_so`: the store's `add` raises → the message line is "Couldn't save Asha: …" and the tile shows its old text.
  - `test_long_name_elides_at_1024x600`: a 100-character name → the tile width is unchanged, and the tooltip is the full name.
  - `test_folder_box_prefills_common_name`: two picked tiles both "Asha", then N → the box is "Asha", all selected; with mixed names, the box stays empty.
  - `test_seen_once_tile_can_be_named`
- [ ] **Step 2:** Run them and see them fail.
- [ ] **Step 3:** Implement, following the page's shortcuts and `pause_while_focused` conventions and the crop pool pattern for the save. A save's result for a page loaded since is ignored, with the same generation tag as the crops.
- [ ] **Step 4:** Run `uv run pytest tests/test_ui_pick_faces.py -q` 3 times and expect PASS each time. Then run the full suite through the heavy wrapper and expect all to pass.
- [ ] **Step 5:** Commit: `feat: name faces in Pick faces; names saved to the People page`.
