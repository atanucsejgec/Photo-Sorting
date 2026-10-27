# Photo Sorter

A desktop app that sorts the photos, videos and other files in any folder you pick. It
moves the original files into tidy folders and keeps an Undo for every run.

It has 14 ways of sorting:
- **Date** mode: `2025/02-February/20-02-2025/`. A day with 3 or more files gets its
  own day folder; a day with 1–2 files goes loose into the month folder. Files with no
  date go to `No Date/`.
- **Face** mode: give it one photo and type the names of the people in it. It remembers
  them, finds them in every photo, and moves each photo into a folder named after the
  person (`Atanu/`). Tick two or more people and only the photos with two or more of
  them are sorted, into `Atanu & Zoe/`. When it isn't sure, it asks ("Is this Atanu?
  72% sure"). Strangers are ignored. Face data stays on this computer.
- **Duplicates** mode: finds exact copies and re-saved or resized copies (for example
  `IMG…~2.jpg` edits, WhatsApp copies), shows them side by side, and moves the ones you
  don't keep into `Duplicates/`.
- **Repeated shots** mode: finds burst shots taken seconds apart, with a full-screen
  compare (arrow keys, side by side, zoom), and moves the ones you don't keep into
  `Repeated Shots/`.
- **Notes & Pages** mode: finds photos of paper and moves them into
  `Notes & Pages/Handwritten/`, `Book Pages/`, `Printed Documents/` or `Receipts/`.
  When it isn't sure, it asks first.
- **Type / source** mode: `Camera Photos/`, `Screenshots/`, `WhatsApp/`, `Videos/` and
  `Other/`, from the file names and the camera info.
- **Content** mode: moves each photo into the folder of what it shows: `Food/`,
  `Pets & Animals/`, `Nature & Landscapes/` and so on. You can change the list in
  Settings → **Content categories…**. When it isn't sure, it asks first; photos that fit
  none of them stay where they are.
- **Bad photos** mode: finds blurry, very dark, burnt-out and accidental shots (pocket
  shots, a finger over the lens) and shows them with the reason. The ones you tick go
  to `Bad Photos/`. Only camera photos are judged: screenshots, stickers and other
  graphics are left alone.
- **Events** mode: groups photos and videos taken close together into events (a new
  event after a 6-hour gap, at least 10 files; both can be changed in Settings). You
  can name, merge, split or skip each event; each one goes to its own folder, by
  default `Event 20-02-2025/`.
- **Location** mode: `India/Kolkata/`, from the GPS position in each photo and video.
  The place is looked up offline, in a city list that comes with the app (GeoNames data,
  through the `reverse_geocode` library); nothing is sent anywhere.
- **Camera** mode: one folder per phone or camera, such as `OPPO CPH2581/`.
- **No people** mode: photos with no face in them go to `No people/`; the rest stay.
- **Extension** mode: every file, of any type, into a folder named after its extension
  (`APK/`, `EXE/`, `PDF/`).
- **Date (all files)** mode: every file, of any type, into the same folders as Date mode,
  day folders too, by the date it was modified or created (you choose each time).

In the other modes, files the mode can't place stay where they are. You always see what
will move before anything does. Nothing is ever deleted: the files you set aside go to a
folder (`Duplicates/`, `Repeated Shots/`, `Bad Photos/`), and Undo puts everything back.

## Keyboard

The whole app works without the mouse. Every screen lists its keys at the bottom.
The common ones:

- **Arrow keys** move between tiles, cards, buttons and list rows.
- **Enter** does the focused thing: starts the mode on a tile, opens a photo big on a
  review screen, and on the summary it is **Go** (Go has the focus there).
- **Space** ticks or unticks.
- **Esc** goes back. On a review screen it first asks before throwing your answers away.
- **Ctrl+Enter** on a review screen goes on to the summary with your answers so far.
- **Tab** and **Shift+Tab** move between the parts of a screen, and to the sidebar.
- On Home, **F** opens the folder picker and **S** switches subfolders on or off.
- On Done, **Home** has the focus, so a second Enter never undoes the sort. **U** undoes
  it, after asking.

Nothing moves before the summary screen, and no key skips it.

## Set up on a new computer (Linux)

1. Install the tools (once):

   ```bash
   sudo apt install git ffmpeg          # ffmpeg is only needed for the tests
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Close and reopen the terminal so `uv` is on the PATH.

2. Download the app:

   ```bash
   git clone git@github.com:atanucsejgec/Photo-Sorting.git
   cd Photo-Sorting
   uv sync                              # downloads Python 3.12 and the libraries
   ```

   `uv sync` also downloads about 2 GB of NVIDIA CUDA libraries, so Face mode can use
   the graphics card. On a computer without an NVIDIA card, use
   `uv sync --no-group gpu` instead; Face mode then runs on the CPU (about half as
   fast). The app menu launcher picks the right one by itself.

3. Add it to the app menu:

   ```bash
   bash scripts/install-desktop-entry.sh
   ```

   Then open **Photo Sorter** from the app menu. Or run `uv run photo-sorter`
   from the folder.

4. Optional: check that everything works with `uv run pytest`.

## Get the newest version

```bash
cd Photo-Sorting
git pull
uv sync
```

## Good to know

- The first time you use Face mode or No people it downloads the face models (about
  190 MB) into `~/.local/share/photo-sorter/models`, after asking. The first time you
  use Content or Notes & Pages it downloads the picture model they share (OpenCLIP,
  about 609 MB) into the same folder, once, after you say Yes. The people you name are
  kept in `~/.local/share/photo-sorter/people`. None of this is part of this repository.
- Settings → **Use GPU** switches Face mode between the graphics card and the CPU.
- Undo records live in a hidden `.photosort` folder inside each folder you sort.
  Keep it if you want Undo to work later.
- Settings and the log file are kept per computer, in `~/.config/photo-sorter`
  and `~/.local/state/photo-sorter`. They are not part of this repository.
- Windows is planned. The code avoids Windows-invalid names already.
