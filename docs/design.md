# Photo Sorter: Design

*Written 2026-10-24. Status: reviewed, waiting for owner approval.*

Items marked **(my call)** were decided by the designer (Claude), not the owner. Everything else is what the owner asked for.

## 1. Why this exists

The owner has a big external SSD (`/run/media/atanu-linux/New Volume/`, NTFS, also used from Windows). It holds folders from a phone backup, for example `07.08.25/` with `Camera/` (759 files: 732 JPG and 27 MP4, 3.4 GB), `Media/` (1,963 files), `Screenshots/`, `Quick Share/`, `Vido/` and others. The photos are mixed up. The owner wants a desktop app on their Linux laptop that tidies any folder they point it at, with several sorting methods, and that never makes them afraid of losing a photo.

Findings from the sample folder:
- Every sampled Camera photo has an EXIF date.
- Only 1 of 49 sampled photos has GPS.
- File names come in these patterns: `IMG20250220181303.jpg`, `IMG..~2.jpg`, `IMG.._BURST..._COVER.jpg`, `VID2025....mp4`, `IMG-20250315-WA0036.jpg` (WhatsApp), and `1752070136514-<uuid>.jpg` (a Unix time in milliseconds followed by a UUID).

Hardware: Ryzen 7 5800H, 14 GB RAM, NVIDIA GPU (`nvidia-smi` is present).

Success means the owner can pick a folder, choose a mode, look over what will happen, press Go, and get a tidy folder. If they don't like the result, Undo puts everything back exactly as it was.

## 2. Goals, non-goals, anti-goals

**Goals**
- A desktop app with a window, not a command-line tool.
- 11 sorting modes (section 5). You run **one mode at a time**, and modes never combine in a single run.
- Original files are **moved**, not copied.
- **Undo** for every run.
- Whenever the app is unsure, it asks the owner before moving anything.
- All processing runs on the laptop. Photos never leave the machine.

**Non-goals (for now)**
- No Windows installer yet. The app must be *Windows-ready* (section 8), but only Linux is built and tested.
- Not a photo viewer, editor or gallery app beyond the review screens below.
- No cloud, backup, phone app, search, or multiple users.
- The app does not edit photos or their EXIF data.

**Anti-goals (a failure even if everything else works)**
- A photo or video gets lost, deleted or overwritten. The app **never deletes** anything. "Removed" files always go to a folder (`Duplicates/`, `Repeated Shots/`, `Bad Photos/`).
- Files are moved without the owner first seeing a summary and pressing Go.
- A run can't be fully undone, including a run that crashed or was cancelled halfway.
- A photo, or anything derived from one, gets sent over the internet. The only network use is the one-time download of the AI models and the GPU libraries (section 3).

## 3. How it's built (decided)

- **Language: Python.** UI: **Qt via PySide6**. (Proposed by the designer and agreed by the owner.)
- **Face recognition: InsightFace, ArcFace `buffalo_l` model.** The model license is non-commercial, which is fine for personal use, but the app must never be sold or distributed commercially with it.
- **Content understanding: OpenCLIP** (zero-shot, categories typed as words). The builder picks the model size, with ViT-B/32-class as a guideline.
- **AI runtime: ONNX Runtime.** The **GPU (CUDA) is used by default.** A **"Use GPU" on/off switch** goes in Settings, and the app falls back to the CPU automatically if CUDA isn't available.
- Models are **downloaded the first time a mode needs them**, with a progress bar. After that the app works fully offline.
- Expected size: about 1.1 GB without the GPU libraries, plus about 1.5–2.5 GB for the CUDA libraries.
- **How the CUDA libraries arrive (my call):** they are *not* bundled in the AppImage. On first launch with "Use GPU" on, the app asks once ("Download GPU support, ~2 GB?"), then downloads the NVIDIA CUDA/cuDNN runtime libraries (for example the `nvidia-*` pip wheels) into its app-data folder. If the download is declined or fails, or the GPU can't be used, the app runs on the CPU and says so in Settings. A CUDA install already on the system may be used if it's compatible.
- Packaging: **AppImage** for Linux **(my call)**.
- System Python is 3.14. If the AI libraries have no 3.14 wheels, the app ships or uses its own Python (for example 3.12 via `uv`). The builder's call.

## 4. How every mode works (shared rules)

1. **Pick a folder** (folder picker or drag-and-drop) and **pick a mode**.
2. **"Include subfolders" on/off option**, shown on the home screen next to the mode list.
   - **Off:** only files directly inside the picked folder are sorted. Subfolders, including ones made by earlier runs, are left alone. To sort inside an earlier result, for example faces in February, the owner picks `Camera/2025/02-February/` itself.
   - **On:** every photo or video in every subfolder is sorted, including files placed there by earlier runs. For example, running Face with this on, after Date, pulls the matched photos out of the date folders. The summary screen makes this visible before Go.
   - **Off by default** (owner confirmed). The setting is remembered between runs **(my call)**.
   - Even with it on, the app's hidden data folder and the "removed" folders (`Duplicates/`, `Repeated Shots/`, `Bad Photos/`) are always skipped, so set-aside files don't come back **(my call)**.
   - Subfolders left empty after a run are removed, and Undo recreates them **(my call)**.
3. The app analyses the files, with a progress bar and a **Cancel** button.
4. If the mode has "unsure" cases or choices, the **review screen** comes next (face, content, notes & pages, duplicates, repeated shots, bad photos, events).
5. A **summary** appears, for example "759 files → 14 month folders, 41 day folders, 6 No Date". It has a **Go** button and a way to see the full list of planned moves **(my call)**.
6. On Go, files are moved. All new folders are created **inside the folder that was picked**.
7. A **Done** screen shows a summary and an **Undo** button.

**Moving rules**
- Never overwrite. If the target name already exists, add ` (1)`, ` (2)` and so on **(my call)**.
- Moves within the same drive are renames. A move across drives must copy, check the copy, then remove the source.

**Undo**
- Every run writes an **undo record** in a hidden folder inside the picked folder (for example `.photosort/`). Paths are stored **relative to the picked folder**, so undo still works if the drive is mounted somewhere else or opened later on Windows.
- Each move is recorded **before** it happens, so a crash or cancel halfway can still be undone.
- An **Undo history** screen lists past runs (date, mode, number of files). Any run can be undone, **newest first**. You can't undo an older run while a newer run that touched the same files is still in place **(my call)**.
- Undo moves files back and removes folders the run created that are now empty.
- If a file was moved or deleted by hand after a run, undo skips it and reports it rather than failing.

**Files handled**
- Photos: jpg/jpeg, png, heic/heif, webp. Videos: mp4, mov, 3gp, mkv **(my call; the builder may add more)**.
- Videos are handled by these modes: date, type/source, duplicates (exact copies only), events, location, camera. All other modes skip videos.

**Cache:** fingerprints, face data and AI results may be cached (for example in `.photosort/cache.db`), keyed by path, size and modified time, so later runs are faster. The builder decides the details.

## 5. The 11 modes

### 5.1 Date
- The date comes from, in order:
  1. The date stored inside the file (EXIF `DateTimeOriginal` for photos, creation time from video metadata).
  2. The date in the file name. Patterns: `IMG20250220181303`, `IMG-20250315-WA0036`, `VID...`, `Screenshot_2025-...`, and 13-digit millisecond Unix times like `1752070136514-...` (only accepted if it falls in a sane range, 2000 to today).
  3. Otherwise the file goes into `No Date/`. The file's "modified" date is **never** used, because it is often the copy date.
- Folder layout: `2025/02-February/20-02-2025/`. Month names are in English.
- A day with **3 or more files** (photos and videos counted together) gets its own day folder.
- A day with 1 or 2 files is moved **loose into the month folder** `2025/02-February/`.

### 5.2 Face
- After choosing Face, the owner selects or drops **one photo** and types a name. If that photo has **several faces**, the app shows each face it found and asks for a name for each one. A face can be skipped.
- **Remembered people:** names and face data are saved in the app's own data folder (not the sorted folder), so they can be reused next time without giving the face again. The start screen lists remembered people with tick boxes, and the owner can add a new person **(my call on the tick-box UI)**. The owner can delete a remembered person. *Also my call:* the owner can add more photos of an existing person to improve recognition.
- Sorting starts automatically after naming.
- **Sure** matches are planned for moving. **Unsure** matches are collected and shown one at a time ("Is this Atanu? 63% sure", with Yes / No / Skip) **before anything moves**. Skip means the photo stays put.
- A photo with **one** named person goes to `<Name>/`.
- A photo with **two or more** named people goes to a **together folder**: `<Name1> & <Name2>/`, with names in alphabetical order **(my call on ordering)**.
- Faces the app doesn't recognise (strangers, background people) are ignored.
- Photos with none of the named people stay where they are.

### 5.3 Duplicates
- Catches **exact copies** (same file content) and **resized or recompressed copies** of the same photo (for example a WhatsApp copy of a camera photo). Near-identical repeated shots are **not** handled here; they have their own mode (5.4).
- Exact copies of **videos** are also caught.
- For each group, a review screen shows the files side by side with: file name, resolution, file size, date taken, sharpness, and folder. The **best one is pre-ticked** (highest resolution, then has an EXIF date, then larger file, then sharper) **(my call on the ranking)**. The owner confirms or changes the pick, can tick more than one to keep, or can choose "Not duplicates, keep all".
- Files not kept are moved to `Duplicates/`. Nothing is deleted.
- Reference mockup: the "which one to keep?" screen shown during design.

### 5.4 Repeated shots
- Groups photos taken **within a few seconds of each other** that **look almost the same**, like pressing the shutter again and again. The builder decides the exact time window and similarity measure.
- Review: the owner can open a group **full screen**, flip between the photos (arrow keys), see them side by side, and **zoom** to check faces and sharpness.
- The owner chooses: **keep all**, **keep one**, or **keep any number** they tick (for example 2 of 5). The sharpest photo is pre-ticked **(my call)**.
- Photos not kept move to `Repeated Shots/`. Nothing is deleted.

### 5.5 Notes & Pages
- Finds photos of paper: handwritten notes, book pages, printed documents, receipts.
- Subfolders: `Notes & Pages/Handwritten/`, `Notes & Pages/Book Pages/`, `Notes & Pages/Printed Documents/`, `Notes & Pages/Receipts/` **(my call)**.
- Unsure photos go to a review screen (Yes, with a category picker / No / Skip).
- Everything else stays put.

### 5.6 Type / source
- Folders: `Camera Photos/`, `Screenshots/`, `WhatsApp/`, `Videos/`, `Other/` **(my call)**.
- Detection uses file names, image size and EXIF (camera photos have a camera model; screenshots don't, and usually match the screen size). Paper photos are left to mode 5.5.

### 5.7 Content
- Uses OpenCLIP with an **editable category list** in Settings. Defaults **(my call)**: Food, Pets & Animals, Nature & Landscapes, Selfies & People, Buildings & City, Vehicles, Events & Celebrations, Text & Documents.
- Each photo goes to its best category folder. Unsure photos go to a review screen. Photos that fit no category stay put.

### 5.8 Bad photos
- Finds blurry, very dark, overexposed and accidental shots (pocket shots, a finger over the lens, the floor).
- Review screen like the duplicates one, with each photo's reason shown ("blurry", "too dark"). The owner ticks which ones to move.
- Ticked photos go to `Bad Photos/`. Nothing is deleted.

### 5.9 Events
- Groups photos and videos into events by gaps in time. Defaults **(my call, adjustable in Settings)**: a new event starts after a 6-hour gap, and an event needs at least 10 files.
- Review: each event shows its date range, a count, and a few thumbnails. The owner types a name, merges or splits events, or skips them. Unnamed events get `Event 20-02-2025`.
- Each event goes to its own folder. Files not in any event stay put.

### 5.10 Location
- Reads GPS from photos and videos, and looks up the place in an **offline** city database.
- Folders: `<Country>/<City>/`.
- Files without GPS **stay put**. The start screen warns how many files have no location before Go **(my call)**.

### 5.11 Camera
- One folder per device, from EXIF Make and Model (for example `OPPO CPH2581/`).
- Files with no device info stay put.

## 6. Screens

- **Home:** folder picker and drop zone, the mode list, the **Include subfolders** switch, and links to Undo history and Settings.
- **Mode setup** (where a mode needs it): the face naming screen, the content category list, and event settings.
- **Progress** with Cancel.
- **Review screens** as described per mode. Photos are shown large enough to judge, with full screen for repeated shots.
- **Summary / Go**, then **Done / Undo**.
- **Undo history.**
- **Settings:** Use GPU switch, content categories, event gap and minimum, remembered people.

Layout and look are left to the builder. Rough mockups were shown during design (the face flow and the duplicates screen) and are kept in `.superpowers/brainstorm/` for reference.

## 7. Build order (stages)

Each stage should be usable on its own:
1. **Core + Date.** Window, folder scanning, summary/Go, safe moving, undo record, Undo history, date mode.
2. **Duplicates + Repeated shots** (both use fingerprints and review screens).
3. **Face.**
4. **Type/source, Bad photos, Camera, Location, Events** (mostly no heavy AI).
5. **Content + Notes & Pages** (OpenCLIP). **(The order of stages 2–5 is my call.)**

## 8. Constraints

- **Windows-ready** (Linux only for now): use `pathlib` and platform-neutral app-data locations (for example `platformdirs`), keep undo paths relative, and never assume `/` paths in stored data.
- **Folder and file names must be valid on Windows and NTFS:** no `\ / : * ? " < > |`, no trailing dots or spaces, and no reserved names like `CON`. Person names typed by the owner are cleaned the same way.
- **No symlinks or shortcuts** in the output, since they don't work reliably on NTFS across operating systems.
- **Development and testing must use copies** of photos, never the owner's real folders, until the owner says otherwise.
- Must stay responsive on folders of a few thousand files. Heavy work runs off the UI thread.

## 9. Left to the builder

- Library choices beyond those named in section 3 (EXIF/video metadata readers, perceptual hash library, offline geocoder, blur and darkness measures).
- All thresholds: face sure/unsure, duplicate similarity, the repeated-shots time window, content confidence, and blur and darkness limits. Pick sensible defaults and keep them in one place so they can be tuned.
- Screen layout and visual design, cache format, and project structure.
- Test strategy.
