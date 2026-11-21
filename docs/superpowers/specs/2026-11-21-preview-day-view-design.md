# Preview: day view (design)

## Why
The owner looks through leftover files after the sorts. Files from one day
usually belong together (one trip, one event), so they want to see a whole
day at once, untick the few that don't belong, and move the rest in one go,
still able to open any one file big and zoom as before.

## What the owner decided
- The day view: option A, a key that switches into it from the normal Preview.
- **Day = the file's modified date** (local time), for every kind of file.
  The owner chose it over "created", which on Linux is the day the file was
  copied to this disk. Nothing is read from inside files.
- Every file of the day starts **ticked**; the owner unticks what doesn't belong.
- A file can be opened alone, big, with zoom etc. as in the normal Preview.

## Behaviour
- **G** in the one-at-a-time view opens the grid of the shown file's day.
  **G** or **Esc** in the grid goes back to one at a time, on the focused file.
- The grid holds that day's files that are still in the Preview list (other
  folders included when subfolders are on), ordered by modified time. Photos
  show a thumbnail; other files show their kind and name. Each card has a tick.
- Top line: "Fri 28 Feb 2025 · day 3 of 41 · 9 files · 7 ticked".
- Keys in the grid:
  - arrows: move between cards;
  - Space or a click on the tick: tick or untick;
  - A: tick all, or untick all when all are ticked;
  - Enter or a double-click: open that file alone;
  - PgUp/PgDn: the day before or after;
  - N: move all ticked files to Not needed;
  - M then 1…0 or Enter (Other folder…): move all ticked files there;
  - L: edit the folder list;
  - E: show the focused file in its folder;
  - Ctrl+Z: put back the whole last group;
  - G or Esc: back to one at a time.
  The arrows move between cards, so the days use PgUp/PgDn. Buttons
  "◀ Day before" and "Day after ▶" do the same.
- **A file opened alone from the grid** is the normal one-at-a-time view, with
  all its keys (zoom, ←/→ through the whole list, N, M…). Esc there goes back to
  the grid of that file's day (not the Leave question); G does too.
- **Moves:** the ticked files leave the grid at once. The unticked ones stay, **unticked**, so the next N or M doesn't take them by
  surprise. The owner ticks them again if wanted. When nothing is ticked, N or M
  says "Nothing ticked". When the grid is empty, the next day opens (else the
  day before; if no files are left, the Preview ends as after the last file).
- Each file is still moved and journalled one by one (same run, same Undo).
  The files moved by one N or M form a **group**. Ctrl+Z, in the grid or in the
  single view, puts back the whole last group. A single move is a group of one.
  If one file of a group can't be put back, the ones already back stay back,
  the message says which failed, and the next Ctrl+Z tries the rest again.
- The day of each file is read from its modified time on a pool thread, the
  first time G is pressed ("Finding the days…"), and kept. A put-back file
  is read again.
- Everything works by keyboard alone, and at 1024×600, 11 pt.

## Non-goals
Choosing another date source; a calendar; selecting across days; thumbnails
of videos or PDFs (they show their kind and name).

## Approaches
- **A (chosen): G switches** between one at a time and the day grid, any time.
- B: a separate "By day" Preview, chosen at start. Rejected by the owner:
  the view can't be switched mid-session.

## Left to the builder
Card size (reuse CardGrid/GridCard from Bad photos), the wording of the
kind label, and the thumbnail pool. Thumbnails must use the one-thread image
pool or CardGrid's own, never more than one decode at a time per grid.
