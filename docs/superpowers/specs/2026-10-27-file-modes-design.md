# Three new modes: No people, Extension, Date (all files)

Date: 2026-10-27. Owner's request, worked through in conversation the same day.

## Why

Photo Sorter sorts photos and videos well ("all work perfectly"). The owner also has folders full of every kind of file (APKs, EXEs, PDFs and more) and wants the same safe sorting for those. They also want a quick way to pull out photos that have no people in them.

Owner's words: "i want to 2 options is no face images sorting and extention sorting and date sorting for all type of files. exention sorting means if all apk or exe are sorted to one file. date sorting for all files means when it downloaded or created or modified."

## What the owner decided

1. **No people**
   - Photos with no person in them move into a folder called **No people**.
   - Photos with any face stay where they are.
   - It asks no names and no "who is this?" questions. ("yes that is what i mean")
2. **Extension**
   - Every file, of any type, goes into a folder named after its extension: all `.apk` into **APK**, all `.exe` into **EXE**, and so on. ("i have all type of files so sort then in their extention name folder")
3. **Date (all files)**
   - Every file, of any type, goes into date folders.
   - For non-photo files, the owner **chooses modified or created date each time** they sort. ("choose")
4. **Subfolders warning** (Extension and Date (all files) only)
   - When "Include subfolders" is on, a warning is shown before scanning. It says files inside folders may belong to programs or projects, and that moving them can break them.
   - If the owner says yes, the job runs as normal. ("if subfolder are chosen for extention and date sorting for all folder then give a warrarning if sub folder is clicked but do the job if he is ok")
5. **Built in parallel with the UI refresh** ("make then in prallal").
   - This work branches from the UI refresh branch (`ui-refresh` at ce78d8a, which has the new Home tiles), on its own branch `file-modes`, in its own worktree.
   - It merges after the UI refresh, so the new modes come out as tiles on the new Home, with keyboard keys and the shortcut strip.

## Goals

- **Three new Home tiles**, in the same style as the other 11:
  - **No people**: "Photos without faces"
  - **Extension**: "APK, EXE, PDF…"
  - **Date (all files)**: "Any file, year / month"
- **The same safety flow as every other mode:**
  - a summary before anything moves, then Go;
  - nothing is deleted;
  - every move is journalled, and Undo puts everything back;
  - name clashes never overwrite (the existing target allocator handles them).
- **Fully usable by keyboard**, with the shortcut strip, like the rest of the refreshed UI.
- **Dates for non-photo files:**
  - Date (all files) asks **Modified date** or **Created date** before scanning.
  - When a file has no created date (common on Linux, and after copying between drives), its modified date is used.
  - The summary says how many files that happened to.
  - Photos and videos keep using the date they were taken, through the existing `find_date`, the same as in Date mode.
- **Same folder layout as Date mode:** `YYYY/MM-Month`, with a day folder when 3 or more files share a day. Reuse `date_target`/`plan_date`.

## Non-goals

- No sorting by what is inside documents (no reading PDFs, etc.).
- Folders themselves are never moved, only files. As in every mode today, a source folder a run leaves empty is removed by the executor (journalled; Undo recreates it). No change to that.
- No change to the existing 11 modes. Date mode stays photos and videos only; the new tile is the any-file version.
- No "who is in it" for No people; that is the Face mode's job.

## Anti-goals (failure even if it "works")

- **Breaking installed programs or projects without warning:** pulling `.dll`/`.exe`/source files out of their folders while the subfolders switch is on. Hence the warning, with the safe answer focused.
- **Moving a file that is still being written:** an unfinished download.
- **A photo with a visible face ending up in No people.**
  - When unsure, the photo stays.
  - Any face the detector finds counts.
  - Known limit: "No people" means no face could be found. The owner asked for "no face images", so a person seen only from behind or very far away has no face to find, and that photo goes to No people.
- **Moving system clutter** that belongs where it is: hidden files, `desktop.ini`, `Thumbs.db`, `.DS_Store`.
- **Touching the app's own Undo records** (`.photosort`).

## How it works (my calls, for the owner to check)

- **Files every "all files" mode leaves alone:**
  - hidden files and folders (name starts with `.`);
  - symlinks;
  - unfinished downloads: `.part`, `.crdownload`, `.download`, `.partial`, `.!qb`;
  - `desktop.ini`, `Thumbs.db`, `.DS_Store`;
  - everything the existing scanner already skips (`SKIP_DIRS`, including `.photosort`).
  - On Windows, files marked hidden or system are skipped too.
- **Extension folder names:**
  - the last extension, upper-cased, without the dot: `photo.JPG` → `JPG`; `archive.tar.gz` → `GZ`;
  - a file with no extension goes to **No extension**;
  - an existing folder with the same name in another case (e.g. `apk`) is reused, the same way other modes reuse folders;
  - a file already in its right folder stays and is counted as "already in place".
- **Subfolders warning:**
  - a themed dialog, with the safe answer **Cancel** focused;
  - Continue runs the scan;
  - it shows every time subfolders is on for these two modes.
- **Date choice:**
  - a themed dialog with two choices, **Modified** and **Created**, with Esc to cancel;
  - the last choice is remembered and focused next time; it starts at Modified.
- **No people:**
  - photos only, as found by the existing scanner; videos and other files stay;
  - it uses the face model the app already has, with Face mode's detector settings and its face cache, so a folder already scanned by Face mode is fast, and both modes always agree on what a face is;
  - if the face models aren't downloaded yet, it asks the existing download question, as Face mode does;
  - a photo that can't be read stays where it is;
  - the summary counts photos moved, photos with people (stay) and photos that could not be read (stay).
- **The No people folder is a result folder, like a person's folder in Face mode.** It is not added to the scanner's skip list, so other modes behave exactly as today. With subfolders on, a later mode can sort those photos again, as it can any person folder.
- **Date (all files), photos and videos with no taken date:**
  - these fall back to the chosen Modified or Created date, so every file in this mode gets a date and there is no No Date folder;
  - the summary counts them;
  - "photos and videos" means the kinds the app already knows. Other image and video types (GIF, RAW, AVI…) are treated like any other file.
- **Tile order:** the three new tiles come after Type / source: No people, Extension, Date (all files).

## Constraints

- Python 3.12, PySide6, uv. Windows-ready: no Linux-only APIs without a fallback.
  - Created date: Linux exposes it only through `statx`. Use it where available and fall back to modified; on Windows use the creation time.
- No new runtime dependencies, and no network beyond the existing model download.
- **Existing tests keep passing.** Tests use tmp_path and fakes only.
- **Memory rule:** every heavy run goes under `systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0`. With the UI refresh running in parallel, every run also takes the shared `flock` lock, so only one capped run happens at a time.
- **Owner's standing rules:**
  - opus agents;
  - no Co-Authored-By lines;
  - personal noreply identity;
  - photos never leave the machine;
  - never touch `/run/media/atanu-linux/New Volume/`; test only on copies.

## Left to the builder

- **Module layout:** for example, a shared `scan_all_files` next to `scan`, and `modes/no_people.py`, `modes/extension_mode.py`, `modes/all_dates.py`.
- **How the subfolders warning and the date choice hook into the flow** (`flows/`). They must come before any scanning, and Cancel/Esc returns Home with nothing planned.
- **Icons**, from the vendored Lucide set or new Lucide SVGs with the same licence.
- **The face-detection minimum score** for "a face is present". Use the Face mode's detector settings; err toward "has a face".
- **Order of work:** the modes and planners come first. The keyboard end-to-end tests need the UI refresh's Task 5 (keyboard summary, Done and Undo), so the last task rebases this branch onto `ui-refresh` once Task 5 is in, then adds them.
- **Testing:**
  - unit tests per planner (names, case, no extension, skips, already in place, date fallback counts);
  - one keyboard-only end-to-end test per new mode, including the subfolders warning (Cancel and Continue) and the date choice;
  - Undo restoring every file.
