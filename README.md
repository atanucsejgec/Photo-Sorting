# Photo Sorter

A desktop app that sorts the photos and videos in any folder you pick. It moves the
original files into tidy folders and keeps an Undo for every run.

Working now:
- **Date** mode: `2025/02-February/20-02-2025/`. A day with 3 or more files gets its
  own day folder; a day with 1–2 files goes loose into the month folder.

In progress: Duplicates and Repeated shots. Coming later: Face, Notes & Pages, Type,
Content, Bad photos, Events, Location, Camera.

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

- Undo records live in a hidden `.photosort` folder inside each folder you sort.
  Keep it if you want Undo to work later.
- Settings and the log file are kept per computer, in `~/.config/photo-sorter`
  and `~/.local/state/photo-sorter`. They are not part of this repository.
- Windows is planned. The code avoids Windows-invalid names already.
