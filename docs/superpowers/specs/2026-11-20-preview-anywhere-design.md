# Preview: move anywhere, paste folder paths — design

The owner approved this on 2026-11-20. It amends docs/superpowers/specs/2026-11-20-preview-design.md and replaces its "moves stay inside the root" rule.

## Owner's request
1. Paste or type a folder path into the folder list.
2. Move Preview files to any folder, including one outside the opened folder and one on another drive.

## Behaviour
**Paste a path.**
- The slot editor (L) gets a path box at the top: "Paste or type a folder path".
- Enter adds the path to the first empty slot.
- `~` is expanded, and quotes and spaces around the path are stripped. A `file://` URL is turned into a path.
- If the path is not an existing directory: "Folder not found", nothing is added.
- If all slots are full: "The list is full".
- If the folder is already listed: "Already in the list (slot N)".
- Add… (the picker) still works. The picker starts at the opened folder and can go anywhere.

**Moving anywhere.**
- Slots and Other folder… may be any existing folder.
- Refused targets:
  - the file's own folder (nothing to do);
  - a folder inside an app folder (`.photosort` or the app data dir);
  - a folder that no longer exists **outside** the root: "<name> isn't there — is the drive plugged in?". Missing slot folders **inside** the root are still re-created and journalled, as before.
- Not needed stays `<root>/Not needed`.
- Slots are never greyed just for being outside the root. They are greyed when the folder is missing.

**Cross-drive.**
- `executor.move_file` already copies, verifies and then removes the original on EXDEV; use it unchanged.
- The move queue runs these in order on its worker. The next file shows at once, and the status line says "Moving <name> to <drive or folder>…" while a move takes over 0.5 s.
- Leaving or closing the window waits for the queue (with the status line).

**Journal and Undo (the safety core).**
- A Preview run keeps its root (the opened folder).
- A move whose target is outside the root records `dst` as an **absolute** POSIX path. Moves inside the root stay root-relative. Created folders outside the root are recorded absolute too.
- The run's meta gains `"also_roots": [<absolute target folders outside root>]`. The list is updated as targets are first used.
- Every reader of moves treats an absolute `dst`/mkdir path as itself, and a relative one as `root / path`. That covers undo_run, files_in, root_present, folder_found, blocking_runs, dependents and relocation.
- **Order safety.**
  - A newer, not-undone run (of any mode) that moved a file this run placed outside the root blocks this run's undo, exactly as it does inside the root. Undo refuses with the usual "undo these first" message.
  - In practice: for each also_root T, the runs of `related_roots(T)` are checked. Their moves' absolute src (`folder / src`) is compared case-insensitively with this run's absolute placed paths.
  - `dependents` is the inverse: for a run R, an older Preview run whose absolute placed path R moved is a dependent.
- **Old records.** The undo paths' strict `bring_in` also runs for each also_root.
- **Unplugged drive.** If any file of the run is outside the root and its also_root folder is missing, Undo refuses with "<folder> isn't there — is the drive plugged in?". Nothing moves and nothing is marked undone. (This is per run, all or nothing, so a partial undo never happens because a drive is missing.)
- **Relocation** ("Folder not found → where is it now?") re-points only the root, as now. Absolute targets are not re-pointed.

## Non-goals
- Re-pointing moved target folders.
- Moving into "Not needed" anywhere other than the root.

## Constraints
- Never overwrite: use the allocator in the target folder.
- Never delete except as `move_file`'s verified cross-device move.
- Tests use tmp dirs. "Another drive" is simulated by monkeypatching `os.rename` to raise EXDEV for paths under a chosen tmp folder.
- Commits use the owner's identity, with no Co-Authored-By line.
- Heavy runs go through the memory-cap wrapper.
