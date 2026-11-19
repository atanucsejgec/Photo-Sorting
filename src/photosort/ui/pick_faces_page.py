"""The Pick faces page, "People in this folder": a tile for each person found,
for the owner to pick and make folders of.

Each tile shows the person's clearest face (PersonGroup.best), cropped on a
pool thread (faces.face_crop) and tagged with the page generation and the
tile it was asked for; one for a page loaded since is ignored. Under the face
is "in N photos" and the tile's state: "✓ Picked", or "→ Friend" once it is
in the folder Friend (it can't then be picked for another). The people seen
in one photo only come last, under a "Seen once (N)" header on a row of its
own, closed until it is opened; their tiles are made when it first opens.

A tile opened (Enter, or a double-click) shows every face of the person,
each ticked, in a FacesDialog; unticking one leaves that face out of the
person for the rest of the page's life, so its photo is not moved for them.

Picked tiles become a folder with the name box and Add (N to type the name,
Enter to add). The name is checked with modes.pick_faces.check_folder_name,
and a refusal is shown under the box. The folders are listed beside the grid,
"Friend: 2 people, 57 photos", each with Remember on the People page (off)
and Remove, which frees its tiles.

Keys: the arrows move between the tiles and the header (by the rows shown),
Space picks a tile (or opens and closes the header), Enter opens it. In the
folder list ↑↓ move and Space ticks Remember. Anywhere, N types a name, Del
removes the folder of the focused row or tile, Ctrl+Enter continues
(continued(folders); with no folder, "Make a folder first") and Esc asks
whether to leave (left()); Tab and Shift+Tab go between the areas. In the
name box Enter adds and Esc goes back to the faces.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QFont, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from photosort.faces import Box, face_crop
from photosort.imageinfo import UnreadableImage
from photosort.modes.pick_faces import FoundFace, PersonGroup, PickFolder, check_folder_name
from photosort.people import clean_name
from photosort.ui import shortcuts
from photosort.ui.qimage import pil_to_qimage
from photosort.ui.review_page import LOADING
from photosort.ui.shortcuts import GridNavigator, ShortcutStrip
from photosort.ui.theme import page_spacing, styled
from photosort.ui.widgets import ButtonBar, Thumbnail
from photosort.ui.worker import Task

_log = logging.getLogger("photosort")

HEADING = "People in this folder"
SUBTITLE = ("Pick the faces of one person (or of several), type a folder name and Add. "
            "Their photos will move to that folder.")
CANT_SHOW = "Can't show"
PICKED = "✓ Picked"
NOTHING_PICKED = "Pick one or more faces first"
NO_FOLDER = "Make a folder first"
REMEMBER = "Remember on the People page"

TILE_CROP_PX = 112  # a tile's face crop
DIALOG_CROP_PX = 96  # a face crop in the FacesDialog
DIALOG_COLUMNS = 4
_TILE_PAD = 8
_TILE_WIDTH = TILE_CROP_PX + 2 * _TILE_PAD + 2  # the crop, the padding and the border
_SPACING = 10  # between the tiles
_MARGIN = 4  # round the grid: room for the focus glow
SIDE_WIDTH = 300  # the name box and the folder list


def _photos(n: int) -> str:
    return f"{n} photo{'' if n == 1 else 's'}"


def _render_crop(token: tuple[int, int], path: Path, box: Box, size: int,
                 progress=None, cancel=None):
    """Return (*token, QImage or CANT_SHOW), or (*token, None) if cancelled first.

    Runs on a pool thread; a QImage (unlike a QPixmap) may be made here.
    """
    if cancel is not None and cancel.is_set():
        return (*token, None)
    try:
        image = pil_to_qimage(face_crop(path, box, size))
    except UnreadableImage:
        return (*token, CANT_SHOW)
    except Exception:  # one face must never stop the page
        _log.exception("Face crop of %s failed", path)
        return (*token, CANT_SHOW)
    return (*token, image)


class PersonTile(QFrame):
    """One person: the clearest face, "in N photos" and the tile's state.
    The tile takes the focus."""

    clicked = Signal()
    # True when the double-click's first click (a press just before) picked
    # or unpicked the tile
    double_clicked = Signal(bool)

    def __init__(self, tile_id: int, group: PersonGroup, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tile_id = tile_id  # unique within a page generation
        self.group = group
        self.picked = False
        self.folder: str | None = None  # the folder the tile is in
        self._pressed = QElapsedTimer()  # since the last press
        styled(self, "card")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedWidth(_TILE_WIDTH)
        self.setToolTip("Click to pick · double-click to see every face")
        self.crop_label = Thumbnail(LOADING, TILE_CROP_PX)
        self.count_label = QLabel()
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setObjectName("muted")
        for label in (self.count_label, self.status_label):
            label.setFixedWidth(TILE_CROP_PX)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(_TILE_PAD, _TILE_PAD, _TILE_PAD, _TILE_PAD)
        layout.setSpacing(4)
        layout.addWidget(self.crop_label, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.count_label)
        layout.addWidget(self.status_label)

    def set_count(self, photos: int) -> None:
        self.count_label.setText(f"in {_photos(photos)}")

    def refresh_status(self) -> None:
        """Show the tile's state: picked, in a folder, or neither."""
        text = f"→ {self.folder}" if self.folder is not None else PICKED if self.picked else ""
        self.status_label.setToolTip(text)
        width = self.status_label.width()
        self.status_label.setText(self.status_label.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, width))
        # Picked stands out, in the accent's pill (see theme).
        name = "pickedTag" if self.picked and self.folder is None else "muted"
        if self.status_label.objectName() != name:
            self.status_label.setObjectName(name)
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)

    def set_crop(self, value: QImage | str) -> None:
        """Show the face crop, or a placeholder text. GUI thread only."""
        if isinstance(value, QImage):
            self.crop_label.set_image(value)
        else:
            self.crop_label.setText(value)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed.start()
            self.clicked.emit()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self._pressed.isValid() and self._pressed.elapsed()
                                     <= QApplication.doubleClickInterval())
            self._pressed.invalidate()
        event.accept()


class SeenOnceHeader(QFrame):
    """The header of the people seen once: "Seen once (N)", on a row of its
    own; it opens and closes their section. It takes the focus."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        styled(self, "card")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.text_label = QLabel()
        self.hint_label = QLabel()
        self.hint_label.setObjectName("muted")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.addWidget(self.text_label)
        layout.addWidget(self.hint_label)
        layout.addStretch()

    def set_state(self, count: int, is_open: bool) -> None:
        self.text_label.setText(f"Seen once ({count})")
        self.hint_label.setText("Enter to close" if is_open else "Enter to show them")

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class _FolderLabel(QLabel):
    """"<name><rest>", wrapping at spaces, with a name too long for one
    line cut with "…" in the middle to the label's width; the whole text as
    its tooltip. A long folder name never clips, nor pushes the row's
    buttons away (its width never asks for more than it is given)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name, self._rest = "", ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_parts(self, name: str, rest: str) -> None:
        self._name, self._rest = name, rest
        self.setToolTip(name + rest)
        self._elide()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt's name)
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        name = self.fontMetrics().elidedText(self._name, Qt.TextElideMode.ElideMiddle,
                                              max(self.width(), 1))
        if self.text() != name + self._rest:
            self.setText(name + self._rest)


class FolderRow(QFrame):
    """A folder made on the page: "Friend: 2 people, 57 photos", Remember
    and Remove. The row takes the focus; its keys tick and remove."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name = name
        styled(self, "card")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.label = _FolderLabel()  # a long name is cut with "…", never clipped
        self.remove_button = QPushButton("Remove")
        self.remember_check = QCheckBox(REMEMBER)
        self.remember_check.setToolTip("After the move, remember these faces as "
                                       f"{name} for Face mode")
        for widget in (self.remove_button, self.remember_check):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the row's keys do these
        top = QHBoxLayout()
        top.addWidget(self.label, 1)
        top.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignTop)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        layout.addLayout(top)
        layout.addWidget(self.remember_check)


class FacesDialog(QDialog):
    """Every face of a person, each ticked; unticking one leaves it out of
    the person (toggled(rel, index, ticked)). Esc closes it.

    The crops are made by the page (see PickFacesPage._request_crop)."""

    toggled = Signal(str, int, bool)

    def __init__(self, faces: tuple[FoundFace, ...], left_out: set[tuple[str, int]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Faces")
        self.faces = faces
        self.checks: list[QCheckBox] = []
        heading = QLabel(f"This person is in {_photos(len({f.rel for f in faces}))}. "
                         "Untick a face that isn't them.")
        heading.setWordWrap(True)
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        grid.setSpacing(_SPACING)
        for n, found in enumerate(faces):
            check = QCheckBox(Path(found.rel).name)
            check.setToolTip(found.rel)
            check.setIconSize(QSize(DIALOG_CROP_PX, DIALOG_CROP_PX))
            check.setChecked((found.rel, found.index) not in left_out)
            check.toggled.connect(
                lambda ticked, f=found: self.toggled.emit(f.rel, f.index, ticked))
            self.checks.append(check)
            grid.addWidget(check, n // DIALOG_COLUMNS, n % DIALOG_COLUMNS)
        grid.setRowStretch(-(-len(faces) // DIALOG_COLUMNS), 1)
        grid.setColumnStretch(DIALOG_COLUMNS, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll.setWidget(holder)
        self.navigator = GridNavigator(self.checks, lambda: DIALOG_COLUMNS, parent=self)
        self.close_button = QPushButton("Close")
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Esc does it
        self.close_button.clicked.connect(self.reject)
        self.strip = ShortcutStrip(self)
        for label, word in (("Arrows", "move"), ("Space", "tick"), ("Esc", "close")):
            shortcuts.describe(self, label, word)  # the cards' and QDialog's own
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(ButtonBar([], [self.close_button]))
        layout.addWidget(self.strip)
        self.strip.show_for(self)
        window = parent.window() if parent is not None else None
        width = min(DIALOG_COLUMNS * (DIALOG_CROP_PX + 110) + 60,
                    int(window.width() * 0.9) if window is not None else 10_000)
        height = min(480, int(window.height() * 0.9) if window is not None else 10_000)
        self.resize(width, height)

    def set_crop(self, n: int, value: QImage | str) -> None:
        if isinstance(value, QImage):
            self.checks[n].setIcon(QIcon(QPixmap.fromImage(value)))
        else:
            self.checks[n].setToolTip(f"{self.faces[n].rel} ({value})")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.navigator.focus()


class _SectionNavigator(GridNavigator):
    """A GridNavigator for cards whose (row, column) places are given by
    places() (the grid has a row the header has to itself): ↑ and ↓ go to
    the row above or below, to the card in the same column or else that
    row's last card."""

    def __init__(self, places, parent: QObject | None = None) -> None:
        self._places = places
        super().__init__([], lambda: 1, parent=parent)

    def _target(self, n: int, key: int) -> int | None:
        places = self._places()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            target = n + (1 if key == Qt.Key.Key_Right else -1)
            return target if 0 <= target < len(self.cards) else None
        row, column = places[n]
        row += -1 if key == Qt.Key.Key_Up else 1
        in_row = [i for i, (r, _) in enumerate(places) if r == row]
        if not in_row:
            return None
        return next((i for i in in_row if places[i][1] == column), in_row[-1])


@dataclass
class _Folder:
    name: str
    tiles: list[PersonTile]
    row: FolderRow


class PickFacesPage(QWidget):
    """The people found; emits continued(list[PickFolder]) or left()."""

    continued = Signal(object)  # list[PickFolder]
    left = Signal()
    keys_changed = Signal()  # a key was switched on or off: the strip redraws

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root = Path()
        self._generation = 0  # bumped whenever the tiles are replaced
        self._next_id = 0  # the next tile's id
        self._dialogs = 0  # FacesDialogs opened, ever
        self._crop_tasks: dict[tuple[int, int], Task] = {}  # kept alive until they report
        self._once_groups: list[PersonGroup] = []
        self._once_open = False
        self._columns = 0
        self._places: list[tuple[int, int]] = []
        self._left_out: set[tuple[str, int]] = set()  # faces unticked in a FacesDialog
        self._folders: list[_Folder] = []
        self._old_dialog: FacesDialog | None = None
        self.groups: list[PersonGroup] = []  # everyone found (as load had them)
        self.tiles: list[PersonTile] = []  # the people seen in more than one photo
        self.once_tiles: list[PersonTile] = []  # made when the section first opens
        self.faces_dialog: FacesDialog | None = None  # while one is open
        self.folder_rows: list[FolderRow] = []

        layout = QVBoxLayout(self)
        page_spacing(layout)
        self.heading_label = QLabel(HEADING)
        font = self.heading_label.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        font.setWeight(QFont.Weight.Bold)
        self.heading_label.setFont(font)
        self.subtitle_label = QLabel(SUBTITLE)
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.heading_label)
        layout.addWidget(self.subtitle_label)

        # The grid.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the tiles take it
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().installEventFilter(self)  # re-flows on resize
        self.once_header = SeenOnceHeader()
        self.once_header.clicked.connect(self._toggle_once)
        self.navigator = _SectionNavigator(lambda: self._places, parent=self)
        self._new_grid()

        # The name box and the folders.
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Folder name, e.g. Friend")
        self.name_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # N, or a click
        self.add_button = QPushButton("Add")
        self.add_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Enter in the name box
        self.add_button.clicked.connect(self._add)
        self.message_label = QLabel()
        self.message_label.setObjectName("error")
        self.message_label.setWordWrap(True)
        self.folder_box = QWidget()
        self._folder_list = QVBoxLayout(self.folder_box)
        self._folder_list.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        self._folder_list.addStretch()
        self.no_folders_label = QLabel("No folders yet")
        self.no_folders_label.setObjectName("muted")
        self.folder_scroll = QScrollArea()
        self.folder_scroll.setWidgetResizable(True)
        self.folder_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the rows take it
        self.folder_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.folder_scroll.setWidget(self.folder_box)
        self.folder_navigator = GridNavigator([], lambda: 1, vertical_only=True, parent=self)
        side = QWidget()
        side.setFixedWidth(SIDE_WIDTH)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        name_row = QHBoxLayout()
        name_row.addWidget(self.name_edit, 1)
        name_row.addWidget(self.add_button)
        side_layout.addLayout(name_row)
        side_layout.addWidget(self.message_label)
        side_layout.addWidget(self.no_folders_label)
        side_layout.addWidget(self.folder_scroll, 1)

        body = QHBoxLayout()
        body.addWidget(self.scroll, 1)
        body.addWidget(side)
        layout.addLayout(body, 1)

        self.back_button = QPushButton("Back")
        self.continue_button = QPushButton("Continue")
        self.continue_button.setDefault(True)
        self.back_button.clicked.connect(self._leave)
        self.continue_button.clicked.connect(self._continue)
        layout.addWidget(ButtonBar([self.back_button], [self.continue_button]))

        # Keys: the grid's (while a tile or the header has the focus), the
        # folder list's, then the page's. While a name is typed, N, Del and
        # Esc are off: they type, and Esc is the name box's.
        shortcuts.describe(self.scroll, "Arrows", "move")
        shortcuts.bind(self.scroll, "Space", "Space", "pick", self._space, repeat=False)
        shortcuts.bind(self.scroll, "Return", "Enter", "open", self._open, repeat=False)
        shortcuts.describe(self.folder_box, "↑↓", "move")
        shortcuts.bind(self.folder_box, "Space", "Space", "remember", self._toggle_remember,
                       repeat=False)
        shortcuts.bind(self.name_edit, "Return", "Enter", "add", self._add, repeat=False)
        shortcuts.bind(self.name_edit, "Escape", "Esc", "back to faces", self._back_to_grid,
                       repeat=False)
        name_key = shortcuts.bind(self, "N", "N", "name", self._focus_name, repeat=False)
        self._remove_key = shortcuts.bind(self, "Delete", "Del", "remove folder",
                                          self._remove_current, repeat=False)
        shortcuts.bind(self, "Ctrl+Return", "Ctrl+Enter", "continue", self._continue,
                       repeat=False)
        esc_key = shortcuts.bind(self, "Escape", "Esc", "leave", self._leave, repeat=False)
        shortcuts.pause_while_focused(self.name_edit, [name_key, self._remove_key, esc_key],
                                      resume=self._update_keys)
        shortcuts.describe_tab(self)
        self._update_keys()

    def load(self, root: Path, groups: list[PersonGroup]) -> None:
        """Show the people found in root (most photos first), nothing picked."""
        self.stop_thumbnails()
        self._close_dialog()
        self._generation += 1
        self._next_id = 0
        self._root = Path(root)
        self.groups = list(groups)
        self._left_out = set()
        for folder in self._folders:
            folder.row.deleteLater()
        self._folders = []
        self.folder_rows = []
        self.name_edit.clear()
        self.message_label.clear()
        self._once_open = False
        self.once_tiles = []
        self._once_groups = [g for g in groups if len(g.photos) == 1]
        self._new_grid()  # the tiles shown before are deleted
        self.tiles = [self._make_tile(g) for g in groups if len(g.photos) > 1]
        self.once_header.set_state(len(self._once_groups), False)
        self._lay_out(force=True)
        self.navigator.set_cards(self._cards(), 0)
        self._update_rows()
        if self.isVisible():
            self.navigator.focus()

    def clear(self) -> None:
        """Let go of the people (the page was left)."""
        self.load(Path(), [])

    def columns(self) -> int:
        return max(1, self._columns)

    def focus_first(self) -> None:
        self.navigator.focus()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.navigator.focus()  # the first tile, or the one the owner was on

    # ----- the grid --------------------------------------------------------

    def _new_grid(self) -> None:
        """An empty grid, in place of the one shown (which is deleted, its
        tiles too; the header is kept, hidden until laid out again)."""
        self.once_header.hide()  # before it is a window of its own for a while
        self.once_header.setParent(None)
        self._holder = QWidget()
        self._grid = QGridLayout(self._holder)
        self._grid.setSpacing(_SPACING)
        self._grid.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._columns = 0
        self._places = []
        self.scroll.setWidget(self._holder)

    def _make_tile(self, group: PersonGroup) -> PersonTile:
        tile = PersonTile(self._next_id, group, self._holder)
        self._next_id += 1
        tile.clicked.connect(lambda: self._pick(tile))
        tile.double_clicked.connect(lambda clicked: self._double_clicked(tile, clicked))
        tile.set_count(self._photo_count([tile]))
        tile.refresh_status()
        best = group.best
        self._request_crop((self._generation, tile.tile_id), self._root / best.rel,
                           best.face.box, TILE_CROP_PX)
        return tile

    def _cards(self) -> list[QWidget]:
        """The focusable cards, in order: the tiles, the header, the tiles seen once."""
        cards: list[QWidget] = list(self.tiles)
        if self._once_groups:
            cards.append(self.once_header)
            if self._once_open:
                cards += self.once_tiles
        return cards

    def _lay_out(self, force: bool = False) -> None:
        """Lay the cards out in as many columns as fit: the tiles, then the
        header on a row of its own, then (when open) the tiles seen once."""
        room = self.scroll.viewport().width() - 2 * _MARGIN
        columns = max(1, (room + _SPACING) // (_TILE_WIDTH + _SPACING))
        if columns == self._columns and not force:
            return
        self._columns = columns
        grid = self._grid
        for widget in [*self.tiles, *self.once_tiles, self.once_header]:
            grid.removeWidget(widget)
        places: list[tuple[int, int]] = []
        for n, tile in enumerate(self.tiles):
            places.append((n // columns, n % columns))
            grid.addWidget(tile, *places[-1])
        if self._once_groups:
            row = -(-len(self.tiles) // columns)
            places.append((row, 0))
            grid.addWidget(self.once_header, row, 0, 1, columns)
            self.once_header.show()
            for n, tile in enumerate(self.once_tiles):
                if self._once_open:
                    places.append((row + 1 + n // columns, n % columns))
                    grid.addWidget(tile, *places[-1])
                tile.setVisible(self._once_open)
        self._places = places

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (event.type() == QEvent.Type.Resize and isValid(self.scroll)
                and watched is self.scroll.viewport()):
            self._lay_out()
            self.navigator.reveal_soon()  # the focused tile, in the new view
        return super().eventFilter(watched, event)

    def _toggle_once(self) -> None:
        """Open or close the section of the people seen once."""
        self._once_open = not self._once_open
        if self._once_open and not self.once_tiles:
            for group in self._once_groups:
                self.once_tiles.append(self._make_tile(group))
        self.once_header.set_state(len(self._once_groups), self._once_open)
        self._lay_out(force=True)
        self.navigator.set_cards(self._cards(), len(self.tiles))  # on the header
        self.navigator.focus()

    def _current_tile(self) -> PersonTile | None:
        card = self.navigator.current()
        return card if isinstance(card, PersonTile) else None

    def _space(self) -> None:
        if self.navigator.current() is self.once_header:
            self._toggle_once()
        elif (tile := self._current_tile()) is not None:
            self._pick(tile)

    def _open(self) -> None:
        if self.navigator.current() is self.once_header:
            self._toggle_once()
        elif (tile := self._current_tile()) is not None:
            self._open_tile(tile)

    def _pick(self, tile: PersonTile) -> None:
        """Pick or unpick tile; not one in a folder."""
        if tile.folder is not None:
            return
        tile.picked = not tile.picked
        tile.refresh_status()
        self.message_label.clear()

    def _double_clicked(self, tile: PersonTile, clicked: bool) -> None:
        if clicked:
            self._pick(tile)  # the double-click's first click picked it: as it was
        self._open_tile(tile)

    # ----- a person's faces ------------------------------------------------

    def _open_tile(self, tile: PersonTile) -> None:
        self._close_dialog()
        dialog = FacesDialog(tile.group.faces, self._left_out, self)
        dialog.toggled.connect(self._face_toggled)
        dialog.finished.connect(self._dialog_closed)
        self.faces_dialog = dialog
        self._dialogs += 1
        for n, found in enumerate(tile.group.faces):
            self._request_crop((-self._dialogs, n), self._root / found.rel, found.face.box,
                               DIALOG_CROP_PX)
        dialog.open()

    def _face_toggled(self, rel: str, index: int, ticked: bool) -> None:
        if ticked:
            self._left_out.discard((rel, index))
        else:
            self._left_out.add((rel, index))
        self._update_counts()

    def _dialog_closed(self) -> None:
        self._close_dialog()
        self.navigator.focus()

    def _close_dialog(self) -> None:
        """Forget the dialog (closed, or about to be): its crops still to come
        are not decoded, and it is deleted once the next one opens."""
        if self._old_dialog is not None:
            self._old_dialog.deleteLater()
            self._old_dialog = None
        if self.faces_dialog is None:
            return
        for (generation, _), task in self._crop_tasks.items():
            if generation < 0:
                task.cancel_event.set()
        dialog, self.faces_dialog = self.faces_dialog, None
        if dialog.isVisible():
            dialog.finished.disconnect(self._dialog_closed)
            dialog.reject()
        self._old_dialog = dialog

    def _faces(self, tiles: list[PersonTile]) -> set[tuple[str, int]]:
        """The faces of tiles, those unticked left out."""
        return {(f.rel, f.index) for tile in tiles for f in tile.group.faces
                if (f.rel, f.index) not in self._left_out}

    def _photo_count(self, tiles: list[PersonTile]) -> int:
        return len({rel for rel, _ in self._faces(tiles)})

    def _update_counts(self) -> None:
        for tile in [*self.tiles, *self.once_tiles]:
            tile.set_count(self._photo_count([tile]))
        self._update_rows()

    # ----- crops -----------------------------------------------------------

    def _request_crop(self, token: tuple[int, int], path: Path, box: Box, size: int) -> None:
        """A face crop; token is (the generation, the tile's id) for a tile,
        (minus the dialog's number, the face's place) for a FacesDialog."""
        task = Task(_render_crop, token, path, box, size)
        task.signals.result.connect(self._crop_ready)
        self._crop_tasks[token] = task
        task.start()

    def _crop_ready(self, result: tuple[int, int, QImage | str | None]) -> None:
        generation, n, value = result
        self._crop_tasks.pop((generation, n), None)
        if value is None:
            return  # cancelled
        if generation == self._generation:
            tile = next((t for t in [*self.tiles, *self.once_tiles] if t.tile_id == n), None)
            if tile is not None:
                tile.set_crop(value)
        elif generation == -self._dialogs and self.faces_dialog is not None:
            if n < len(self.faces_dialog.checks):
                self.faces_dialog.set_crop(n, value)

    def stop_thumbnails(self) -> None:
        """Let the crops still waiting for a pool thread finish without decoding."""
        for task in self._crop_tasks.values():
            task.cancel_event.set()

    # ----- folders ---------------------------------------------------------

    def _focus_name(self) -> None:
        self.name_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.name_edit.selectAll()

    def _back_to_grid(self) -> None:
        self.navigator.focus()

    def _add(self) -> None:
        """A folder of the picked tiles, named as typed; or say why not."""
        picked = [t for t in [*self.tiles, *self.once_tiles] if t.picked]
        if not picked:
            self.message_label.setText(NOTHING_PICKED)
            return
        text = self.name_edit.text()
        problem = check_folder_name(self._root, text, [f.name for f in self._folders])
        if problem is not None:
            self.message_label.setText(problem)
            return
        name = clean_name(text)
        row = FolderRow(name)
        folder = _Folder(name, picked, row)
        row.remove_button.clicked.connect(lambda: self._remove(folder))
        for tile in picked:
            tile.picked = False
            tile.folder = name
            tile.refresh_status()
        self._folders.append(folder)
        self._folder_list.insertWidget(len(self._folders) - 1, row)
        self.name_edit.clear()
        self.message_label.clear()
        self._update_rows()
        self.navigator.focus()

    def _remove(self, folder: _Folder) -> None:
        """Remove folder; its tiles are free again, none picked."""
        if folder not in self._folders:
            return
        index = self._folders.index(folder)
        in_list = self.folder_box.isAncestorOf(QApplication.focusWidget())
        for tile in folder.tiles:
            tile.folder = None
            tile.picked = False
            tile.refresh_status()
        self._folders.remove(folder)
        self._folder_list.removeWidget(folder.row)
        folder.row.deleteLater()
        self._update_rows(min(index, len(self._folders) - 1))
        if in_list:
            if self._folders:
                self.folder_navigator.focus()
            else:
                self.navigator.focus()

    def _remove_current(self) -> None:
        """Del: the folder of the focused row, or of the focused tile."""
        focus = QApplication.focusWidget()
        if focus is not None and self.folder_box.isAncestorOf(focus):
            row = self.folder_navigator.current()
            folder = next((f for f in self._folders if f.row is row), None)
        else:
            tile = self._current_tile()
            name = tile.folder if tile is not None and tile.hasFocus() else None
            folder = next((f for f in self._folders if f.name == name), None)
        if folder is not None:
            self._remove(folder)

    def _toggle_remember(self) -> None:
        row = self.folder_navigator.current()
        if isinstance(row, FolderRow):
            row.remember_check.toggle()

    def _update_rows(self, current: int | None = None) -> None:
        """The folder rows' texts and keys, for the folders as they are."""
        for folder in self._folders:
            people = len(folder.tiles)
            folder.row.label.set_parts(
                folder.name, f": {people} {'person' if people == 1 else 'people'}, "
                             f"{_photos(self._photo_count(folder.tiles))}")
        self.folder_rows = [f.row for f in self._folders]
        self.folder_navigator.set_cards(self.folder_rows, current)
        self.no_folders_label.setVisible(not self._folders)
        self._update_keys()

    def _update_keys(self) -> None:
        """Del works while there is a folder to remove (not while a name is typed)."""
        on = bool(self._folders) and not self.name_edit.hasFocus()
        if self._remove_key.isEnabled() != on:
            self._remove_key.setEnabled(on)
            self.keys_changed.emit()

    # ----- leaving ---------------------------------------------------------

    def _continue(self) -> None:
        if not self._folders:
            self.message_label.setText(NO_FOLDER)
            return
        self.message_label.clear()
        self.continued.emit([
            PickFolder(f.name, frozenset(self._faces(f.tiles)), f.row.remember_check.isChecked())
            for f in self._folders])

    def _leave(self) -> None:
        if shortcuts.confirm_leave(self):
            self.stop_thumbnails()
            self.left.emit()
