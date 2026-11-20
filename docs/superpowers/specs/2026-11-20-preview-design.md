# Preview — look at each remaining file and move it (design)

## Why
After all the sorts, some files are left over. The owner wants to go through them one at a time, see each one (image, video, audio, PDF, or anything else that can be shown), and move it with one key. N sends a file to a "Not needed" folder. M then a number 1–9 or 0 sends it to one of up to 10 listed folders. This is quicker than a file manager.

## Owner's decisions
- PySide6-Addons may be downloaded, so video and audio play inside the app. It is downloaded and committed (de8d360).
- The "Not needed" folder is **inside the folder being previewed**: `<folder>/Not needed`.
- Keys:
  - ←/→ go to the previous/next file.
  - Shift+←/→ jump 10 s back/forward in a video or audio.
  - Space plays and pauses.
  - +/− and Ctrl+wheel zoom images and PDFs.
  - There is a **speed button for 1×, 2× and 4×** in a video or audio. S cycles 1× → 2× → 4× → 1×, and three buttons show the speeds, with the current one marked.
- N moves to Not needed. M, then 1…9 or 0, moves to listed folder 1…10. Up to 10 folders are listed, and the owner can add, remove and re-order them, which renumbers them.
- If the folder wanted is not listed, the owner picks one.
- The owner said to build this while they're away, without further questions. Every call below marked **(my call)** is the controller's.

## Goals
- A new Home tile **Preview**, with the hint "Look at each file, move it". It is placed right after Pick faces.
- The usual folder pick, the usual "Include subfolders?" question and the usual `bring_in` of old records (non-strict, like a sort).
- One file at a time, big, with a top line: "12 of 340 · IMG_0001.jpg · 2.3 MB · 14 Feb 2025" and the file's folder relative to the root.
- Every move is real and immediate (no Go). Each one is journalled, so Undo works from Done and from History.
- It never deletes anything, and it never overwrites a file (the existing TargetAllocator naming).

## Non-goals
- Editing files.
- Renaming files.
- Previewing archives or office documents. Those get an info card plus "O open with the system app".
- Moving outside the previewed folder (my call; see Constraints).

## Viewers (by file type)
- **Image** (every type the app's `imageinfo.open_image` reads, HEIC included):
  - decoded on a pool thread, with EXIF rotation applied;
  - fitted to the window at first;
  - +/−/Ctrl+wheel zoom about the cursor or centre, from 10 % to 800 %;
  - F fits again;
  - when zoomed, drag pans and ↑/↓ scroll.
- **PDF: pypdfium2 (owner approved)**, since there's no QtPdf in the Linux wheel:
  - the document is opened once per file on a pool thread, which gives the page count; pages are rendered on pool threads at the zoom's dpi, at most 40 megapixels a page;
  - pdfium is not thread-safe, so every pdfium call in the app holds one lock, and the document is closed when the file is let go;
  - only the pages in view, plus one ahead, are rendered;
  - zoom changes the dpi (+/−/Ctrl+wheel, F fits the width);
  - ↑/↓/PgUp/PgDn scroll, and the top line shows "page 3 of 12".
  - If pypdfium2 can't be loaded, or the file is broken or locked with a password, or a page fails, the info card is shown with "Can't show this PDF".
- **Video** (`media.kind == "video"`, plus anything QMediaPlayer opens):
  - QMediaPlayer and QVideoWidget, with a position slider and time "0:12 / 3:40";
  - Space plays and pauses, Shift+←/→ jump ±10 s, and S or the speed buttons set 1×/2×/4× (the speed is kept for the next file);
  - the first frame shows paused;
  - it never autoplays (my call);
  - the sound is on.
- **Audio** (mp3, m4a, aac, wav, flac, ogg, opus, wma, amr): the same controls, with a large music icon and the file name instead of the picture.
- **Text** (txt, md, csv, tsv, json, log, xml, ini, yaml/yml, srt; at most 1 MB is read): a read-only, monospaced view. +/− change the font size.
- **Anything else, or a file that fails to open**: an info card with the name, type, size, modified date and full path, plus "O open with the system app" (QDesktopServices).
- Changing file stops the playback and lets go of the player's source before any move. A file that's playing is never moved while it's open.

## Moving
- **N** moves the file to `<root>/Not needed/` (created on the first use and journalled as a created folder), then the next file shows.
- **M** opens a small overlay listing the folder slots:
  - "1 Family · 2 Trips · … · 0 —", with each name shown as its path relative to the root;
  - pressing 1–9 or 0 moves to that slot;
  - an empty slot or a slot outside the root does nothing and shows "Slot 4 is empty" or "Trips isn't inside this folder";
  - **Enter, or the "Other folder…" button,** opens a folder picker starting at the root;
  - **Esc** closes the overlay without moving.
- **Picking an "Other folder…":**
  - The picked folder must be inside the root (my call). Otherwise: "Pick a folder inside <root name> — moves stay inside the folder you opened, so Undo can put them back."
  - The picker can make a new folder with its own New Folder button.
  - The file is moved there, and the folder is added to the first empty slot. If all 10 are full, it just moves, with the note "The list is full; edit it with L".
- **The slot list** is shown in a side panel the whole time, compact: number and name.
  - L, or the "Edit folders" button, opens an editor: a list of the 10 slots with Add… (picker, same inside-root rule), Remove (Del), and Move up/down (Alt+↑/↓). Moving re-numbers the slots.
  - The list is saved in Settings as `preview_folders`: 10 entries, each an absolute path or null (my call: one list for the app, not per folder). Slots outside the current root are shown greyed.
- **Ctrl+Z puts back** the last file moved in this session (last in, first out, all the way back). The file returns to where it was and is shown again. In the journal it is recorded as a reverse move in the same run, so undoing the run later is still correct, because undo runs in reverse order.
- **The run journal:**
  - One run per Preview session (mode "preview", label "Preview"), created at the first move.
  - The session ends when the owner leaves (Esc, then "Leave Preview?" with Leave focused, since nothing is lost), when the last file is handled, or when the window closes. It ends with `finish("done")`, and empty folders the session made are tidied as in `execute`.
  - The Done page then shows "Moved N files: X to Not needed, Y to folders", plus Undo, as usual.
  - Moves run one at a time on a worker. The next file shows at once, and a failed move shows the error and puts that file back in the list where it was.
- **Which files are listed:**
  - every file under the root (with subfolders if the owner says so), sorted by folder and then by natural name;
  - skipped are anything under `Not needed`, under any listed slot folder inside the root, and the folders the app always skips (`scanner.skipped_folder`).
  - Moving on past the last file shows "That was the last file", and Done.

## Constraints
- Moves stay inside the previewed root, because the journal stores paths relative to the root and Undo's order checks work per root.
- Never delete; never overwrite; never touch `/run/media/atanu-linux/New Volume/` in tests.
- Keyboard-only use works at 1024×600 and 11 pt, with a help strip in the app's style listing the keys that apply to the current file kind.
- The window never freezes: decoding, PDF rendering and moves run off the GUI thread.
- No downloads beyond PySide6-Addons and pypdfium2 (owner approved). Tests make their media themselves:
  - a WAV with the `wave` module;
  - a PDF with QPdfWriter;
  - a video with `/usr/bin/ffmpeg` when it's present, otherwise the test is skipped;
  - the PDF tests always run (pypdfium2 is a dependency).

## Left to the builder
Widget structure, the exact overlay look, and the name of the zoom-about-cursor helper.
