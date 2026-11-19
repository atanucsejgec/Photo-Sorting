# Name faces in Pick faces — design

## Why
In Pick faces the owner sees a tile for each person found, but no names. The owner wants to put a name on a face there, so that the Face mode knows that person later. Names are for recognition; they do not move files.

## Goals
- Every tile has a name line under the face.
- A tile whose person is already on the People page shows that name by itself.
- The owner can name, or rename, a tile. The name is saved to the People page at once, with the tile's faces.
- Pick faces folders and moves work exactly as now.

## Non-goals
- Naming one face at a time. Naming is per tile, using the faces still ticked in it.
- Any change to grouping, folders, Which folder?, or moving.
- Undo of naming. A name is managed on the People page (rename or delete), as now.

## Anti-goals
- Teaching the People page a face the owner unticked.
- A freeze of the window while saving.
- A naming key that fires while the owner types in a box.

## Approach (owner picked "A": name the tile itself; names saved to the People page, known people shown)
Alternatives considered:
- B: each name is its own folder. Rejected by the owner.
- Naming only at Move ("Remember"). This already exists and does not show names.

## How it behaves
1. **Known names.** After grouping, each group's ticked faces' mean embedding (normalised) is scored with `people.person_scores` against the People page. If the best score is ≥ `thresholds.FACE_SURE`, the tile shows that person's name. Otherwise it shows a muted "No name".
2. **Naming.**
   - On a focused tile, **R** opens a small "Who is this?" dialog. A click on the tile's name line does the same.
   - The dialog shows the face crop and a name box, prefilled with the tile's name if it has one. Enter or Save saves, and Esc or Cancel cancels. Focus starts in the box.
   - The name is checked with `people.name_problem`, the same way the People page checks it, and a refusal is shown in the dialog.
   - R is off while any text box has focus.
3. **Saving.**
   - The faces saved are the tile's ticked faces.
   - If the name matches an existing person (compared as the People page compares names, casefolded): `add_samples(person.id, embeddings)`.
   - Otherwise: `add(name, normalised mean, the tile's best crop)` and then `add_samples(id, embeddings)`. These are the same rules as the Pick faces Remember.
   - The save runs off the GUI thread. While it runs, the tile's name line says "Saving…", and then it shows the name.
   - If the save fails, the tile keeps its old name and the error is shown in the page's message line. Nothing else changes.
   - Renaming a tile to another name saves the faces under the new name. The old person is not changed.
4. **Folder box help.** When N is pressed (or the box is clicked) with an empty box, and every picked tile has the same name, the box is prefilled with that name, selected. The owner can still type over it.
5. **Seen once.** These tiles work the same way once they are shown.
6. **Status.** The name line sits between the crop and "in N photos". It is elided with the full name as a tooltip, and stays readable at 1024×600 and 11 pt.

## Constraints
- Python 3.12, PySide6, the existing PeopleStore. No new dependencies, and nothing leaves the machine.
- Tests use a tmp PeopleStore only.
- Keyboard-only use must work, following the help strip and shortcuts conventions of the page.

## Left to the builder
- Where the scoring helper lives. A suggestion: `modes/pick_faces.known_names(people, store) -> dict[int, str]`.
- The dialog's widget details.
- How the save Task is run, using the same pool or worker pattern as the page.
