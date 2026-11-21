# Day view: ticking many files with the mouse (design)

## Why
The owner wants to tick or untick blocks of files in the Preview day grid
the way a file manager selects: with a drag box and modifier clicks. In this
grid, "selected" means "ticked".

## Approved behaviour
- **Drag a box** across cards, starting in a gap between cards or on a
  card's picture or name (not on its tick box): when the mouse is released,
  only the cards the box touches are ticked; every other card of the day is
  unticked. While dragging, the cards update live and a translucent box
  (the theme's accent colour, see-through fill, solid edge) shows.
- **Ctrl + drag**: the cards the box touches flip (ticked ↔ unticked),
  computed against the ticks as they were when the drag began, so moving
  the box back and forth never flips a card twice.
- **Ctrl + click** on a card: flips that card.
- **Shift + click** on a card: every card from the last clicked card
  (the anchor: the last plain, Ctrl or Shift click, or the focused card if
  there is none) to this one, in grid order, takes the anchor's tick state.
- **Plain click**: as now (the tick box flips; the picture focuses the card;
  a double-click opens it alone). A click without movement (under the
  platform's drag distance) is a click, never a box.
- Dragging past the top or bottom edge of the grid scrolls it, faster the
  further out the pointer is.
- The top line's ticked count updates live. The keyboard (Space, A) is
  unchanged. The focused card follows the last card clicked.
- Nothing is moved by any of this: only N or M move files.

## Non-goals
Keyboard range ticking (Shift+arrows); a drag that adds without Ctrl.

## Left to the builder
How the box is drawn (an overlay on the scroll area's viewport is fine),
the autoscroll speed curve, hit-testing by card geometry.
