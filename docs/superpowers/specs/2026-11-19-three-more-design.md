# Busy-day names, Pick faces, one place for undo — design

Date: 2026-11-19. Owner's words are quoted; everything else marked *(my call)* was proposed by Claude and accepted or left to Claude.

Builds on main 3402dc7 (the dark UI with sidebar, keyboard everywhere, shortcut strip, 14 mode tiles). Every global rule of the earlier specs still holds:
- nothing is ever deleted, only moved;
- every run has a journal and Undo, and a summary with Go comes before anything moves;
- every screen is fully usable by keyboard and shows its keys in the small strip;
- 11 pt font, 1024×600 minimum window;
- photos and face data never leave the machine; no new downloads.

Three independent parts. They can be built in parallel, but Part C touches Undo and gets the most testing.

---

## Part A — Naming busy days (Date mode)

**Owner's request:** "if any date has more then 10 photos then after date sorting show all the photos collage and ask user if he want to name the date if he give name then the date folder name wil be … 'date the given name'".

**Why:** a day with many photos is usually an event (a trip, a wedding). A name makes it findable later without opening the folder.

### Behaviour
- Applies to **Date** mode only, not Date (all files) *(my call, accepted)*.
- A **busy day** is a date with **more than 10 photos** in this run's plan, so 11 or more. Videos don't count *(my call, accepted)*. Make it a named constant, `BUSY_DAY_MIN_PHOTOS = 11`.
- After the scan and before the summary, if there is at least one busy day, a new **Name the busy days** page appears. With none, the flow is unchanged.
- One row or card per busy day, oldest first, showing:
  - the date, for example "Thu 20 Feb 2025", with "34 photos, 3 videos";
  - a collage of that day's photos: up to 12 thumbnails, plus "Show all" which opens a scrollable grid of every photo that day. Owner: "show all the photos collage" *(the 12 + Show all split is my call)*;
  - a name box, empty by default.
- Name given → that day's folder is `YYYY/MM-Month/DD-MM-YYYY <name>`, e.g. `2025/02-February/20-02-2025 Puri trip`. Every file of that day goes there, photos and videos alike.
- Name empty → unchanged: `DD-MM-YYYY`.
- Names are cleaned the way Events names are (`naming.fit_name` / safe-name rules, `FOLDER_NAME_MAX`). A name that cleans to nothing counts as empty.
- **A day folder that already exists** for that date from an earlier run, named or not (`20-02-2025` or `20-02-2025 Something`): that day is **not asked**, and its files join the existing folder as Date mode does today *(my call: one day never gets split across two folders)*. The match is on the folder starting with `DD-MM-YYYY`, case-insensitive. If two exist, the first by name wins.
- That "existing day folder" rule changes for **every day, not only busy ones**, and in the shared `day_folder_rule`, so Date (all files) gets it too *(my call)*. A day with 1 file whose named folder `20-02-2025 Puri trip` exists joins it. Before, only the exact `20-02-2025` was recognised. Busy-day naming itself stays Date-only.
- Keys, in the strip:
  - ↑/↓ moves between days;
  - Enter or F2 types a name (in the box, Enter is done and Esc stops editing);
  - A opens "Show all";
  - Ctrl+Enter continues to the summary;
  - Esc asks whether to leave.
  - Reuse the Events page's patterns (`GridNavigator`, thumbnail threads tagged by generation).
- The summary gains a line such as "3 busy days named".
- Undo puts everything back, including removing the named folders it created, exactly as for any Date run.

---

## Part B — Pick faces (new mode)

**Owner's request:** "scan all the faces of the folder and show all the faces, after that i will click the faces and save the faces photo a specific folder, for example i select 2 faces of my friend and create a friend folder and click move then the two faces photos will be move to the folder."

**Owner's decisions:** faces shown **grouped by person** (option A). A photo that belongs to two of the new folders → **ask the owner** (option A).

**Why:** Face mode needs people named beforehand on the People page. This mode needs no setup: see who is in the folder, pick, move.

### Name and place
- A new tile, **Pick faces**, hint "Click faces, make folders", after Face in MODES. Key `pick_faces`, new ModeSpec kind `"pickfaces"`.
- Photos only; videos are skipped. Subfolders follow the usual Home setting.
- The face models are already on disk; use `ensure_models(FACE_MODELS)` as Face mode does.

### Scan and grouping
- Every photo is analysed with the existing `FaceEngine.analyse` through the existing `AnalysisCache` (`faces-v1`), so a folder Face mode already scanned groups in seconds. Same progress page, Stop and error handling as Face mode's scan.
- Faces are grouped into **people** by their embeddings (cosine similarity), using numpy only, with no new library *(my call, accepted)*.
  - The method is left to the builder: e.g. average-linkage agglomerative clustering, or greedy centroid assignment followed by a merge pass.
  - The join threshold is a named constant in `thresholds.py`, tuned on the test copies. Start near `FACE_SURE` (0.45).
  - Within one photo, two faces are never put in the same person.
  - **Memory:** must stay bounded for ~30 000 faces. No full n×n float matrix held at once: work in blocks. The 6 GB cap must never be approached.
- Each person gets an id and a list of (photo, face) pairs.

### The page: "People in this folder"
- A grid of **person tiles**, most photos first. Each tile shows:
  - the clearest face crop (highest detection score, largest box);
  - "in 42 photos";
  - a tick mark when selected.
- People seen in only **one** photo are collapsed into a section at the end, "Seen once (N)", which opens on Enter or click *(my call)*.
- Click or Space selects or unselects a tile. **Several tiles can be selected**: picking two tiles of the same friend is how a split person is put back together.
- Enter (or double-click) on a tile **opens** it: all of its faces as crops, each ticked. Unticking removes that face, so that photo is not moved because of this person. Esc closes it.
- A **name box + Add** (keys: N to focus the name box, Enter adds):
  - creates a new folder entry from the selected tiles, e.g. **Friend**;
  - those tiles get a "→ Friend" badge and can't be selected for another folder;
  - the folder list beside or below the grid shows "Friend: 2 people, 57 photos" with Remove (Delete key).
  - Repeat for more folders, e.g. "Family".
- Folder names: the same cleaning and refusals as person names (`naming.safe_name`, no " & ", not hidden, not one of the app's own folder names). An existing folder of that name in the root is joined (`resolve_existing_dir`), like other modes.
- **Remember as a person** *(my call, accepted in spirit)*: each folder row has a checkbox, off by default, "Remember on the People page". After a successful run, the folder's chosen face embeddings are saved to `PeopleStore` as one person named after the folder. If the name is taken by an existing person, the samples are added to that person. Nothing is saved if the run is cancelled or fails. Undoing the run later does **not** remove the remembered person: the owner removes it on the People page (Forget) if wanted *(my call: keeps People separate from Undo)*. The Done page says "Friend remembered on the People page" so it's visible.
- Ctrl+Enter continues; with no folders, it says "Make a folder first". Esc asks whether to leave.

### Photos in two folders (owner: ask)
- A photo goes to a folder when any of its faces belongs to one of that folder's chosen faces.
- A photo matching **two or more** folders goes to a **Which folder?** page, after the grid and before the summary:
  - one row per photo: the photo, its matching faces boxed, and one button per folder plus **Leave it here**;
  - at the top, "**All to Friend**", "**All to Family**"… and "**Leave all here**", for doing them all at once (owner: "you choose for each one, or for all at once");
  - keys: ↑/↓ rows, 1–9 picks that folder for the row, 0 leaves it, Ctrl+Enter continues.
  - With no such photos the page is skipped.
- Summary: "57 photos → Friend, 120 → Family, 4 left where they are". Then Go, Done, Undo as every mode.
- Folders go directly in the root: `<root>/Friend/`. The files keep their names; clashes are resolved by `TargetAllocator`.

---

## Part C — One place for all undo records (and the scan cache)

**Owner's request:** "you make a json folder in the sorting folder for undo but every folder has its json, that's very problematic, so keep all the undo data in one place so that I can undo or delete them by one select."

**Owner's decision:** a folder is recognised by its **full path**. If it is not found, History says so and the owner points to where it is now *(my pick, owner agreed)*.

**Why:** hidden `.photosort` folders in every sorted folder are clutter, and the owner wants one list to undo or clear runs from.

### Where things live
- **Undo records:** `platformdirs.user_data_dir("photo-sorter")/runs/`, next to `people/`.
  - Same two files per run as today (`<run_id>.jsonl` append-only and fsynced, `<run_id>.json` atomic).
  - The metadata gains `"root"`: the absolute path of the sorted folder (`os.path.abspath`, no symlink resolving). Move paths stay relative to root, as now.
  - Run ids must be unique across all folders: keep the current scheme, since exclusive create already retries.
- **Scan cache:** one central `cache.db` in `user_data_dir("photo-sorter")/`. The key is unchanged (kind, casefolded name, size, mtime), so moving files keeps entries, as now. Same corruption and failure rules.
- **After this change the app writes nothing into the owner's folders** except the moved files and the folders it creates for them.

### Bringing old records in (migration)
- Old `.photosort` folders can be anywhere, so they are brought in **when a folder is picked on Home**. The folder, the folders around it (up to the filesystem root) and every folder inside it are checked, the same walk `undo.related_folders` does today. The check runs in a worker with progress shown when it takes long; the page must not freeze.
- For each `.photosort/runs` found:
  1. copy each run's two files into the central `runs/`, adding `"root"`;
  2. read back and verify;
  3. only then delete the old copies.
- An old run id that clashes with a central one gets a suffix.
- The old `cache.db` entries are merged into the central cache, then the old cache files are removed. Remove `.photosort` itself only when it is then empty.
- Anything that fails stays where it was and is retried next time. The owner is never shown an error for it, only a log line. **Nothing is ever lost**: no old file is deleted before its copy is verified.
- A read-only folder or drive: its old records are copied (so History shows them), its old files are left, and they are not copied twice.

### History
- History lists **every run from every folder**, newest first, with columns: When, Mode, **Folder** (the path, shortened in the middle), Files, Status.
- A filter "This folder only" shows only runs of the folder picked on Home (and folders inside it) *(my call)*. Default: all.
- **Multi-select:** Ctrl/Shift click; Space toggles the focused row; Ctrl+A selects all.
- **Undo** (button or Enter):
  - undoes the selected runs **newest first**, asking once ("Undo 3 runs?" with Cancel focused);
  - the existing blocking rule still applies: a newer run that moved files these placed must be undone first. The check now uses the central store (runs whose root is the same folder, a folder around it, or a folder inside it).
  - If one run is blocked or fails, the ones before it stay undone, the rest are not tried, and a message says which.
- **Delete** (button or the Delete key):
  - forgets the selected records;
  - asks first, with **Keep** focused: "Forget 3 runs? Your files stay where they are, but these runs can no longer be undone." Runs already undone get a shorter message;
  - deleting never moves a file;
  - **a run that an older, not-undone run depends on** (it moved files the older one placed, as `blocking_runs` works it out) can't be forgotten alone. The message is "Forget the older runs that depend on it too, or undo it first." Its button **Forget all N** selects them together *(my call: so no undo is ever left half-possible)*;
  - forgotten runs are written to a lasting **imported list** in the central store, keyed by run_id plus its `started` time, so an old copy left on a read-only drive is never brought back in *(my call)*. Migration skips anything in that list, and anything already imported.
- **Folder not found:** a run whose root does not exist shows "Folder not found" in Status. Undo on it asks "Where is this folder now?" with a folder picker.
  - The picked folder is accepted only if at least one of the run's moved files is found there at its recorded place. Otherwise: "This doesn't look like the same folder", and nothing changes.
  - When accepted, every run whose root is that old path, or inside it, is re-pointed to the new path. Then the undo goes ahead as normal.
- The Done page's Undo and the U key keep working exactly as now, through the central store.

---

## Non-goals

- Part A: no naming for Date (all files), and no renaming of existing day folders.
- Part B: no cloud or online face service, ever. No new grouping library or download. A photo is never copied into two folders.
- Part C: no drive-UUID tracking (rejected by the owner). No sync across computers.

## Anti-goals (a failure even if it "works")

- Any file moved to the wrong place, or not restored byte-for-byte by Undo.
- An old undo record lost during migration.
- The machine running out of memory while grouping faces.
- A screen reachable only by mouse.
- Writing into `/run/media/atanu-linux/New Volume/` during development or testing.

## Testing

- **TDD**, with tmp_path only; fake face engines in unit tests. Use the shared `tests/keys.py` helpers for keyboard tests.
- Part C needs a test user_data_dir per test (monkeypatched), never the real one.
- Real-screen keyboard runs on `~/PhotoSortTest` copies, with SHA checks after every Undo, for each part. As in the last round, run Pick faces with the real face models (read-only).
- Heavy runs go through `flock … systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0`, one at a time.

## Left to the builder

- The exact grouping algorithm and threshold value, within the memory rule.
- Page layouts within the existing theme, provided they fit 1024×600 at 11 pt.
- The module split, for example:
  - `modes/pick_faces.py` for scanning, grouping and planning;
  - `ui/flows/pick_faces.py`, `ui/pick_faces_page.py` and `ui/which_folder_page.py`;
  - `ui/busy_days_page.py`;
  - `journal.py` / `undo.py` / `cache.py` for the move to the central store, plus a `migrate.py`.

## Approaches considered

- **Grouping faces:** reuse the existing embeddings and cache, grouped with numpy (**chosen**: offline, nothing new to download, fast on scanned folders) vs. a dedicated clustering library (slightly better on look-alikes, but a new dependency and download; opening a tile and unticking covers that case).
- **Recognising a folder in the central store:** full path plus "point to it" when it's missing (**chosen**: simple and explainable; undo can never act on a wrong guess) vs. drive-UUID tracking (automatic, but not available on every drive, and a wrong guess is hard to explain).
- **Busy-day names before Go vs. renaming after the move:** before Go (**chosen**: one plan, one Undo, nothing moves twice).
