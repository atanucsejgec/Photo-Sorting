# Photo Sorter UI refresh: modern dark look and full keyboard use

Date: 2026-10-25. Owner-approved direction from browser mockups (`.superpowers/brainstorm/…/home-a-sidebar.html`).

## Why

All 11 modes work ("all work perfectly"). The owner finds the UI plain and wants it to look like a modern tech application, and to feel easy. They also want to drive the whole app from the keyboard (arrows and Enter doing most of the work), so the mouse is rarely needed.

## What the owner decided

- **Look:** mockup A's "dark tech" style, with mockup C's left sidebar.
  - Near-black background (`#0d1117`-like), with slightly lighter panels and tiles.
  - One accent: a cyan-to-violet gradient (`#06b6d4`/`#22d3ee` → `#8b5cf6`/`#a78bfa`), used for the primary button and the brand word.
  - A cyan glow ring marks the item with keyboard focus.
- **Home:**
  - Sidebar items: **Sort, History, People, Settings**.
  - Main area:
    - the folder, with a file count once chosen, and a Browse button;
    - an "Include subfolders" switch;
    - the 11 modes as tiles in a 4-column grid, each with an icon, its name and a one-line hint.
  - Hint texts (mockup): Date "Year / month"; Face "Per person"; Duplicates "Exact copies"; Repeated "Bursts, keep best"; Content "Food, pets, nature…"; Notes & Pages "Receipts, documents"; Bad photos "Blurry, dark"; Events "Trips and days"; Location "Country / city"; Camera "Per phone"; Type / source "WhatsApp, screenshots".
- **Keyboard:**
  - Arrows move the focus glow; Enter does the focused thing.
  - **Space** ticks or unticks.
  - **Esc** goes back.
  - On review screens, **Ctrl+Enter** continues (Enter there opens the photo big).
  - On the summary, **Go** already has focus, so Enter moves the files.
  - Home letter keys: **F** opens the folder picker, **S** switches subfolders; **Tab** reaches the sidebar.
- **Shortcut strip on every screen.** It is a small strip along the bottom. It lists **every** shortcut that works on that screen, and only those, as key chips plus a word ("←↑↓→ move · Space tick · Enter view big · Ctrl+Enter continue · Esc back").
  - Dialogs get the same strip.
  - The strip changes with the screen. It is reference text, not something you click.

## Goals

1. Every screen and dialog in the dark tech style, consistent with the others.
2. **The whole app works by keyboard alone**, from choosing a folder through Go, Done and Undo, in every mode, including:
   - Face naming;
   - Events renaming, merging and splitting;
   - the category picker;
   - the photo preview and full-screen viewer;
   - Settings and the category editor;
   - History and Undo;
   - the model download question.
3. The focused item is always visible (glow), and focus always starts somewhere sensible when a screen opens:
   - the first tile on Home (the last-used mode, if remembered);
   - the first card on review screens;
   - **Go** on the summary;
   - the main button on Done.
4. Small fix: "Notes & Pages" shows as "Notes _Pages" on Home today. The `&` is being read as a keyboard mnemonic. Every label must show `&` literally.

## Non-goals

- No change to what any mode does, what it moves, or the summary → Go → Undo safety flow.
- No light theme or theme switcher, for now.
- No new features beyond the sidebar pages below.

## Anti-goals (a failure even if it "works")

- **A key that moves or sets aside files without the summary screen.** Enter on the summary is the deliberate Go. No shortcut skips the summary.
- **Focus getting lost:** a screen where arrow keys do nothing, or where focus lands on something invisible.
- **Slower than now.** No heavy animation or effects that lag on this laptop or on a plain Windows PC.
- **Breaking the mouse.** Everything still works by clicking, as today.
- **Text that is hard to read.** Contrast must stay comfortable, including the muted hint text and the shortcut strip.

## Sidebar pages (my calls; the owner approved the sidebar but not these details)

- **Sort:** the Home described above.
- **History:**
  - today's History dialog content, shown as a page in the main area;
  - the past runs for the chosen folder, with Undo on the selected run (Enter, after a confirmation);
  - with no folder chosen, it says to choose one first.
- **People:** the remembered people list, with Forget, as it is today. It moves out of Settings into this page.
- **Settings:** today's Settings content as a page: GPU, events gap and size, content categories (opens the category editor).
- **During a sort**, from Start until the Done screen, the sidebar is shown dimmed and can't be focused or clicked. Esc and Back are the way out, as today. This keeps a half-finished review from being abandoned by accident.

## Constraints

- PySide6, as now. Windows-ready, so no Linux-only fonts or APIs. The system UI font is fine. No runtime network.
- **Icons:** simple line icons, bundled as SVG files in the package with a licence that allows it (e.g. Lucide, ISC licence). Emoji render inconsistently across Linux and Windows, so don't use them.
- **Styling:** one place for colours, sizes and the stylesheet, so later tweaks are easy.
- **Existing tests keep passing.** Widget names and attributes that tests use may be renamed only if the tests change with them.
- **Memory rule** (owner's laptop): every test run goes under `systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0`, one at a time.
- Owner's standing rules: opus agents, no Co-Authored-By lines, personal noreply identity, photos never leave the machine.

## Esc on review screens (owner decided: "ok go with your suggestion")

This covers the Duplicates/Repeated review, Face questions, Bad photos, Events and Content/Notes review.
- **Esc goes back one step:** the previous group or question, or, on grid screens, the previous screen in the flow.
- **On the first item** (or on a grid screen), Esc asks "Leave this review? Your answers so far will be lost." with **Stay** focused. Enter therefore keeps you in; choosing Leave goes Home.
- **Ctrl+Enter means "I'm done":** it goes to the summary with the answers so far, the same as today's Stop reviewing / Continue.
- No key skips the summary.

## Calls made after the builder check (mine, for the owner to check)

- **System file pickers stay native.** These are the folder picker (F / Browse) and the photo picker in Face naming. They can't be themed, and on Windows they keep Quick Access and recent folders. They are the only screens without the dark look or the shortcut strip. Every in-app message, warning and yes/no question becomes a themed dialog with a strip.
- **The folder stays a field you can type or paste a path into.**
  - Letter shortcuts (F, S) work only when focus is outside the field.
  - Enter in the field confirms the path and moves focus to the tiles.
  - Esc in the field leaves it.
- **The strip follows focus.** It lists the screen's own keys, plus the focused control's keys while one is focused: a name box, a picker or a number box. For example, on Events while a name is being typed: "Enter done · Esc stop editing". It lists the app's keys and Tab/Shift+Tab; ordinary typing keys are not listed.
- **No Start button.** Enter or a click on a mode tile starts the sort, as in the mockup. Tile order follows the mockup.
- **Settings as a page saves each change as it's made.**
- **The main button on Done is "Home", never Undo**, so a second Enter can't undo a sort. Undo stays one Tab away, and in History.
- **The sidebar comes back on Done.**
- **The file count** is photos and videos in the folder (and subfolders when the switch is on), counted off the GUI thread.

## Left to the builder

- **Exact colours and spacing,** within the palette above.
- **Tile and card sizes, and how many columns at small window sizes.**
- **Icon choice per mode.**
- **How a focused tile looks when pressed.**
- **How each screen's shortcut list is defined in code,** as long as the strip always shows exactly the shortcuts that are live on that screen.
- **Remaining keys,** in the same spirit, e.g. Delete for Forget on People, or F2 for rename on Events.
- **Testing:**
  - a keyboard-only end-to-end test per flow family: plain modes, review grids, Face, Events, Content/Notes, History/Undo, Settings;
  - a test that each screen's strip matches its live shortcuts.
