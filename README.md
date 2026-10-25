# Photo Sorter

A desktop app that sorts the photos and videos in any folder you pick. It moves the
original files into tidy folders and keeps an Undo for every run.

Working now:
- **Date** mode: `2025/02-February/20-02-2025/`. A day with 3 or more files gets its
  own day folder; a day with 1–2 files goes loose into the month folder.

- **Duplicates** mode: finds exact copies and re-saved or resized copies (for example
  `IMG…~2.jpg` edits, WhatsApp copies), shows them side by side, and moves the ones you
  don't keep into `Duplicates/`.
- **Repeated shots** mode: finds burst shots taken seconds apart, with a full-screen
  compare (arrow keys, side by side, zoom), and moves the ones you don't keep into
  `Repeated Shots/`.
- **Face** mode: give it one photo and type the names of the people in it. It remembers
  them; tick the people to sort by. With one ticked, it moves every photo of them into
  a folder named after them (`Atanu/`); with two or more, only the photos of them
  together, into `Atanu & Zoe/` (photos of just one of them stay put). When it isn't
  sure, it asks ("Is this Atanu? 72% sure"). Strangers are ignored. Face data stays on
  this computer.

Nothing is ever deleted. Coming later: Notes & Pages, Type, Content, Bad photos,
Events, Location, Camera.

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

- The first time you use Face mode it downloads its face models (about 190 MB) into
  `~/.local/share/photo-sorter/models`, after asking. The people you name are kept in
  `~/.local/share/photo-sorter/people`. Neither is part of this repository.
- Settings → **Use GPU** switches Face mode between the graphics card and the CPU.
- Undo records live in a hidden `.photosort` folder inside each folder you sort.
  Keep it if you want Undo to work later.
- Settings and the log file are kept per computer, in `~/.config/photo-sorter`
  and `~/.local/state/photo-sorter`. They are not part of this repository.
- Windows is planned. The code avoids Windows-invalid names already.
