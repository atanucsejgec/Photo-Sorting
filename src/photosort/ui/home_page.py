"""Home ("Sort" in the sidebar): the folder, the subfolders switch and the
mode tiles.

There is no Start button: Enter on a tile, or a click, asks for its mode
(start_requested). The arrows move between the tiles as they are laid out:
up to MAX_COLUMNS columns, fewer when the window is narrow (one per
TILE_MIN_WIDTH). The tiles are one stop for Tab, the tile last moved to.
In a window too short for them all, the tiles scroll (the focused one in
view) rather than squeeze.

F opens the folder picker and S switches subfolders, but only while the
focus is outside the folder field, where every letter types. In the field,
Enter uses the folder typed and goes to the tiles, and Esc goes to the tiles.
A pasted path is trimmed of the spaces and line breaks around it.

The count of photos and videos in the folder runs on a worker Task, one at a
time, a short while after the folder or the switch last changed; a count
for a folder or switch that has changed since is dropped. Counting again
the folder and switch last counted (on coming back Home) keeps showing the
last count until the new one arrives. A path that is not a folder says so.
"""

import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractButton, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QStyle, QStyleOption, QVBoxLayout, QWidget,
)

from photosort.modes import MODES
from photosort.scanner import walk_media
from photosort.ui import icons, shortcuts
from photosort.ui.labels import literal
from photosort.ui.theme import SMALL_SCALE, heading, page_spacing, scaled_font, styled
from photosort.ui.widgets import Switch
from photosort.ui.worker import Task

HEADING = "Sort a folder"
# The tiles, in the order shown (left to right, then down), with their hints.
TILES: tuple[tuple[str, str], ...] = (
    ("date", "Year / month"),
    ("face", "Per person"),
    ("duplicates", "Exact copies"),
    ("repeated", "Bursts, keep best"),
    ("content", "Food, pets, nature…"),
    ("notes", "Receipts, documents"),
    ("bad", "Blurry, dark"),
    ("events", "Trips and days"),
    ("location", "Country / city"),
    ("camera", "Per phone"),
    ("type", "WhatsApp, screenshots"),
    ("no_people", "Photos without faces"),
    ("extension", "APK, EXE, PDF…"),
    ("all_dates", "Any file, year / month"),
)
MAX_COLUMNS = 4
TILE_MIN_WIDTH = 180  # px a tile needs; fewer columns below MAX_COLUMNS of them
TILE_ICON_PX = 22
# Kept tight so all the tiles fit 1024 x 600 at the app's 11 pt font, unscrolled.
TILE_PAD_Y = 9  # px above the icon and below the hint
TILE_ICON_GAP = 1  # px between the icon and the name, on top of the spacing
TILE_SPACING = 8  # px between the tiles
NO_FOLDER = "No such folder"
_ARROWS = (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down)
_ENTER = (Qt.Key.Key_Return, Qt.Key.Key_Enter)


def count_media(root: Path, include_subfolders: bool, progress=None,
                cancel: threading.Event | None = None) -> int | None:
    """The photos and videos a sort of root would see; None if cancelled."""
    n = 0
    for _ in walk_media(root, include_subfolders, cancel):
        n += 1
    if cancel is not None and cancel.is_set():
        return None
    return n


def _media(n: int) -> str:
    """The count, saying what it counts: a mode for any file sorts more."""
    return "1 photo or video" if n == 1 else f"{n} photos and videos"


def _inserted(old: str, new: str) -> str:
    """The text an edit put in, turning old into new."""
    start = 0
    while start < min(len(old), len(new)) and old[start] == new[start]:
        start += 1
    end = 0
    while (end < min(len(old), len(new)) - start
           and old[len(old) - 1 - end] == new[len(new) - 1 - end]):
        end += 1
    return new[start:len(new) - end]


class ModeTile(QAbstractButton):
    """A mode's tile: its icon, name and a one-line hint. A click, or Enter
    while it has focus, clicks it; a held Enter's repeats do nothing (a tile
    needs a fresh press). Space does nothing: it ticks, and a tile has no tick."""

    def __init__(self, key: str, name: str, hint: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key, self.name, self.hint = key, name, hint
        styled(self, "modeTile")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setText(literal(name))  # for accessibility; the labels below show it
        self.setAccessibleDescription(hint)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, TILE_PAD_Y, 14, TILE_PAD_Y)
        layout.setSpacing(2)
        self.icon_label = QLabel()
        self.icon_label.setPixmap(
            icons.icon(icons.MODE_ICONS[key], "focus").pixmap(TILE_ICON_PX, TILE_ICON_PX))
        self.name_label = QLabel(name)
        self.name_label.setFont(scaled_font(self, 1.0, QFont.Weight.DemiBold))
        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("muted")
        self.hint_label.setFont(scaled_font(self, SMALL_SCALE))
        layout.addWidget(self.icon_label)
        layout.addSpacing(TILE_ICON_GAP)
        for label in (self.icon_label, self.name_label, self.hint_label):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.name_label)
        layout.addWidget(self.hint_label)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.sizeHint().height())  # the grid reflows instead

    def paintEvent(self, event) -> None:
        option = QStyleOption()
        option.initFrom(self)
        if self.isDown():
            option.state |= QStyle.StateFlag.State_Sunken  # :pressed
        painter = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, option, painter, self)
        painter.end()

    def keyPressEvent(self, event) -> None:
        if event.key() in _ENTER:
            if not event.isAutoRepeat():
                self.click()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space:
            event.accept()  # Space ticks; there is nothing to tick on a tile
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            event.accept()
            return
        super().keyReleaseEvent(event)


class HomePage(QWidget):
    """The folder, the switch and the tiles; start_requested(mode key)."""

    start_requested = Signal(str)
    COUNT_DELAY_MS = 300  # after the last change, before counting

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        page_spacing(layout)
        layout.addWidget(heading(HEADING))

        # The folder field, with the count inside it on the right.
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Type, paste or drop the folder with your photos")
        self.folder_edit.setAcceptDrops(False)  # a dropped folder goes to the window
        pick = self.folder_edit.addAction(icons.icon("folder", "muted"),
                                          QLineEdit.ActionPosition.LeadingPosition)
        pick.setToolTip("Choose a folder")
        pick.triggered.connect(self.browse)
        self.count_label = QLabel(self.folder_edit)
        self.count_label.setObjectName("muted")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.count_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self.browse)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.folder_edit, 1)
        row.addWidget(self.browse_button)
        layout.addLayout(row)

        self.subfolders_switch = Switch("Include subfolders")
        self.subfolders_check = self.subfolders_switch  # the name the tests know
        layout.addWidget(self.subfolders_switch, 0, Qt.AlignmentFlag.AlignLeft)

        # The tiles.
        labels = {key: label for key, label, _ in MODES}
        available = {key: ok for key, _, ok in MODES}
        self.tile_grid = QWidget()
        self.tile_layout = QGridLayout(self.tile_grid)
        self.tile_layout.setContentsMargins(2, 4, 2, 4)  # room for the focus glow
        self.tile_layout.setSpacing(TILE_SPACING)
        self.mode_buttons: dict[str, ModeTile] = {}
        for key, hint in TILES:
            tile = ModeTile(key, labels[key], hint)
            tile.setEnabled(available[key])
            tile.clicked.connect(lambda _=False, key=key: self.start_requested.emit(key))
            self.mode_buttons[key] = tile
        self._columns = 0
        self._current = self.mode_buttons["date"]
        self._reflow(MAX_COLUMNS)
        self.tile_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        # In a short window (1024 x 600 at 125 %) the tiles scroll rather
        # than squeeze; a tile given focus is scrolled into view.
        self.tile_scroll = QScrollArea()
        self.tile_scroll.setWidgetResizable(True)
        self.tile_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tile_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tile_scroll.setWidget(self.tile_grid)
        layout.addWidget(self.tile_scroll, 1)

        # Keys (see the module doc). F and S work anywhere on the page but in
        # the field (bound on the page, switched off while the field has
        # focus); the tiles list them too, so that their strip reads in order:
        # the arrows, Enter, F, S, then Tab (which only the tiles send to the
        # sidebar).
        self._letter_keys = [
            shortcuts.bind(self, "F", "F", "folder", self.browse),
            shortcuts.bind(self, "S", "S", "subfolders", self.subfolders_switch.toggle),
        ]
        for label, word in (("←↑↓→", "choose"), ("Enter", "start"), ("F", "folder"),
                            ("S", "subfolders"), ("Tab", "sidebar")):
            shortcuts.describe(self.tile_grid, label, word)
        shortcuts.bind(self.folder_edit, "Return", "Enter", "use this folder",
                       self._use_typed_folder)
        shortcuts.bind(self.folder_edit, "Escape", "Esc", "leave", self.focus_tiles)
        shortcuts.describe(self.subfolders_switch, "Space", "on/off")

        # The count.
        self._generation = 0  # bumped by each change of folder or switch
        self._count_task: Task | None = None
        self._counting = False  # a count Task is running
        self._count_generation = 0  # the generation the running count is for
        self._count_again = False  # count once the running one is done
        self._count_what: tuple[Path, bool] | None = None  # what the running count is of
        self._last_count: tuple[tuple[Path, bool], str] | None = None  # (what, text)
        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.setInterval(self.COUNT_DELAY_MS)
        self._count_timer.timeout.connect(self._start_count)
        self._typed = ""  # the field's text before the latest edit
        self.folder_edit.textEdited.connect(self._edited)
        self.folder_edit.textChanged.connect(self._folder_changed)
        self.subfolders_switch.toggled.connect(lambda _: self.refresh_count())
        # Last: the filter uses all the above.
        for watched in (self.folder_edit, self.tile_grid, *self.mode_buttons.values()):
            watched.installEventFilter(self)

    # ----- the folder ------------------------------------------------------

    def folder(self) -> Path | None:
        """The folder in the field, resolved (no symlinks), if it is an existing directory."""
        text = self.folder_edit.text().strip()
        if not text:
            return None
        try:
            path = Path(text).expanduser().resolve()
        except (OSError, RuntimeError):  # e.g. a symlink loop
            return None
        return path if path.is_dir() else None

    def browse(self) -> None:
        """The system's folder picker; the folder picked goes in the field."""
        start = self.folder()
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder",
                                                  str(start) if start else "")
        if folder:
            self.folder_edit.setText(folder)

    def _edited(self, text: str) -> None:
        """Trim a pasted path (an edit putting in more than one character, or a
        line break): spaces typed one at a time stay, as a folder name may end
        in one until the next letter."""
        put_in = _inserted(self._typed, text)
        if (len(put_in) > 1 or any(c in put_in for c in "\r\n\t")) and text != text.strip():
            self.folder_edit.setText(text.strip())

    def _folder_changed(self, text: str) -> None:
        self._typed = text
        self.refresh_count()

    def _use_typed_folder(self) -> None:
        text = self.folder_edit.text()
        if text != text.strip():
            self.folder_edit.setText(text.strip())
        self._count_now()
        self.focus_tiles()

    # ----- the tiles -------------------------------------------------------

    def columns(self) -> int:
        return self._columns

    def set_current(self, key: str | None) -> None:
        """Make key's tile the one focus_tiles() and Tab go to (Date for None
        or an unknown key)."""
        self._make_current(self.mode_buttons.get(key or "", self.mode_buttons["date"]))

    def focus_tiles(self) -> None:
        self._current.setFocus(Qt.FocusReason.OtherFocusReason)

    def _make_current(self, tile: ModeTile) -> None:
        self._current = tile
        for other in self.mode_buttons.values():
            other.setFocusPolicy(Qt.FocusPolicy.StrongFocus if other is tile
                                 else Qt.FocusPolicy.ClickFocus)

    def _reflow(self, columns: int) -> None:
        if columns == self._columns:
            return
        self._columns = columns
        for tile in self.mode_buttons.values():
            self.tile_layout.removeWidget(tile)
        for n, tile in enumerate(self.mode_buttons.values()):
            self.tile_layout.addWidget(tile, n // columns, n % columns)
        for column in range(MAX_COLUMNS):
            self.tile_layout.setColumnStretch(column, 1 if column < columns else 0)

    def _move(self, tile: ModeTile, key: int) -> None:
        tiles = list(self.mode_buttons.values())
        n, columns, last = tiles.index(tile), self._columns, len(tiles) - 1
        if key == Qt.Key.Key_Left:
            target = n - 1
        elif key == Qt.Key.Key_Right:
            target = n + 1
        elif key == Qt.Key.Key_Up:
            target = n - columns
        else:
            target = n + columns
            if target > last and n // columns < last // columns:
                target = last  # the row below is short: its last tile
        if 0 <= target <= last and tiles[target].isEnabled():
            tiles[target].setFocus(Qt.FocusReason.OtherFocusReason)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if watched is self.tile_grid and kind == QEvent.Type.Resize:
            self._reflow(max(1, min(MAX_COLUMNS, watched.width() // TILE_MIN_WIDTH)))
        elif watched is self.folder_edit:
            if kind in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                # Every letter types in the field; the strip then leaves F and S out.
                for shortcut in self._letter_keys:
                    shortcut.setEnabled(kind == QEvent.Type.FocusOut)
            elif kind == QEvent.Type.Resize:
                self._place_count()
        elif isinstance(watched, ModeTile):
            if kind == QEvent.Type.FocusIn:
                self._make_current(watched)
                self.tile_scroll.ensureWidgetVisible(watched, 4, 4)
            elif kind == QEvent.Type.KeyPress and event.key() in _ARROWS:
                if event.modifiers() == Qt.KeyboardModifier.NoModifier or (
                        event.modifiers() == Qt.KeyboardModifier.KeypadModifier):
                    self._move(watched, event.key())
                    return True
        return super().eventFilter(watched, event)

    # ----- the count -------------------------------------------------------

    def _counted_what(self) -> tuple[Path, bool] | None:
        """The folder and switch a count is of; None without a folder."""
        root = self.folder()
        return None if root is None else (root, self.subfolders_switch.isChecked())

    def _no_folder_text(self) -> str:
        return NO_FOLDER if self.folder_edit.text().strip() else ""

    def refresh_count(self) -> None:
        """Count again soon: the folder, the switch or the files have changed."""
        self._generation += 1
        if self._counting and self._count_task is not None:
            self._count_task.cancel_event.set()  # stale now
        what = self._counted_what()
        if what is None:
            self._show_count(self._no_folder_text())
        elif self._last_count is not None and self._last_count[0] == what:
            self._show_count(self._last_count[1])  # no flicker; the new count follows
        else:
            self._show_count("Counting…")
        self._count_timer.start()

    def _count_now(self) -> None:
        if self._count_timer.isActive():
            self._count_timer.stop()
            self._start_count()

    def _start_count(self) -> None:
        if self._counting:
            self._count_again = True  # once the running one is done
            return
        self._count_again = False
        what = self._counted_what()
        if what is None:
            self._show_count(self._no_folder_text())
            return
        self._counting = True
        self._count_generation = self._generation
        self._count_what = what
        task = Task(count_media, *what)
        task.signals.result.connect(self._counted)
        task.signals.error.connect(self._count_failed)
        self._count_task = task  # kept alive while it runs
        task.start()

    def _counted(self, n: int | None) -> None:
        self._count_done(None if n is None else _media(n))

    def _count_failed(self, message: str) -> None:
        self._count_done("Can't read this folder")

    def _count_done(self, text: str | None) -> None:
        """text: what to show, or None if the count was cancelled."""
        self._counting = False
        if self._count_generation == self._generation:
            self._show_count(text or "")
            self._last_count = None if text is None else (self._count_what, text)
        if self._count_again:
            self._start_count()

    def stop_count(self) -> None:
        """Stop a running count (the window is closing)."""
        self._count_timer.stop()
        self._count_again = False
        if self._count_task is not None:
            self._count_task.cancel_event.set()

    def _show_count(self, text: str) -> None:
        self.count_label.setText(text)
        self._place_count()

    def _place_count(self) -> None:
        """The count sits inside the field, on the right; the text stops short of it."""
        label = self.count_label
        width = label.sizeHint().width() if label.text() else 0
        field = self.folder_edit
        label.setGeometry(field.width() - width - 12, 0, width, field.height())
        field.setTextMargins(0, 0, width + 8 if width else 0, 0)
