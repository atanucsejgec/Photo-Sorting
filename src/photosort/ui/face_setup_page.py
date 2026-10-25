"""The Face setup page: the remembered people, ticked for this sort.

Everyone comes up unticked: the owner ticks the people to sort by, one for
all their photos, two or more for only the photos with two or more of them.
Only the people load() is asked to tick (say, ones just named) come up ticked. A photo
dropped on the page is named like one picked with "Add person…". The same
list, without ticks, drops and with only Forget, is the Settings
"Remembered people…" dialog.

The face pictures are small JPEGs in the app's own data folder, so they are
read on the GUI thread.
"""

import logging
from collections.abc import Iterable

from PIL import Image
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from photosort.people import PeopleError, PeopleStore, Person
from photosort.ui.face_naming_dialog import dropped_photo
from photosort.ui.qimage import pil_to_qimage

_log = logging.getLogger("photosort")

ICON_PX = 64
HEADING = "Sort by face"
HINT = ("Tick one person to sort their photos into a folder named after them. With two "
        "or more ticked, only photos with two or more of them are sorted, into a folder "
        "named after the people in each photo.")
NOBODY = "No one is remembered yet. Add a person: pick a photo of them and type their name."
PEOPLE_HINT = "The people Photo Sorter knows by face, for sorting by face."
NOBODY_LEFT = "No one is remembered."
FORGET_TITLE = "Forget person"
_ID_ROLE = Qt.ItemDataRole.UserRole


def _icon(person: Person) -> QIcon:
    try:
        with Image.open(person.thumb) as img:
            img.load()
            return QIcon(QPixmap.fromImage(pil_to_qimage(img)))
    except Exception:  # a missing or damaged picture must never stop the page
        _log.warning("Face picture of %s can't be shown", person.thumb, exc_info=True)
        return QIcon()


class FaceSetupPage(QWidget):
    """Lists the remembered people; emits start(ids) with the ticked ones.

    add_requested, photo_dropped(path) and more_requested(person id) ask
    the owner of the page (the main window) to name faces in a photo.
    With delete_only, there are no ticks and Forget is the only button.
    """

    start = Signal(list)  # list[str]: the ticked person ids, in list order
    add_requested = Signal()
    photo_dropped = Signal(object)  # Path: a photo to add a person from
    more_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, delete_only: bool = False) -> None:
        super().__init__(parent)
        self._delete_only = delete_only
        self._store: PeopleStore | None = None
        self._people: dict[str, Person] = {}
        self._loading = False
        self.setAcceptDrops(not delete_only)

        layout = QVBoxLayout(self)
        if not delete_only:
            layout.setContentsMargins(32, 24, 32, 24)
            layout.setSpacing(16)
            heading = QLabel(HEADING)
            font = heading.font()
            font.setPointSizeF(font.pointSizeF() * 1.6)
            font.setWeight(QFont.Weight.Bold)
            heading.setFont(font)
            layout.addWidget(heading)
        self.hint_label = QLabel()
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.people_list = QListWidget()
        self.people_list.setIconSize(QSize(ICON_PX, ICON_PX))
        layout.addWidget(self.people_list, 1)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.add_button = QPushButton("Add person…")
        self.more_button = QPushButton()
        self.delete_button = QPushButton()
        row = QHBoxLayout()
        for button in (self.add_button, self.more_button, self.delete_button):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)

        self.back_button = QPushButton("Back")
        self.start_button = QPushButton("Start sorting")
        self.start_button.setDefault(True)
        row = QHBoxLayout()
        row.addStretch()
        for button in (self.back_button, self.start_button):
            button.setMinimumWidth(120)
            row.addWidget(button)
        layout.addLayout(row)
        for button in (self.add_button, self.more_button, self.start_button,
                       self.back_button):
            button.setVisible(not delete_only)

        self.people_list.itemChanged.connect(self._ticks_changed)
        self.people_list.currentItemChanged.connect(self._update_buttons)
        self.add_button.clicked.connect(self.add_requested)
        self.more_button.clicked.connect(self._more)
        self.delete_button.clicked.connect(self._forget)
        self.start_button.clicked.connect(lambda: self.start.emit(self.ticked_ids()))
        self._update_buttons()

    # ----- loading ---------------------------------------------------------

    def load(self, store: PeopleStore, ticked: Iterable[str] = ()) -> None:
        """List the people remembered in store, unticked but for the ids in ticked."""
        self._store = store
        self.status_label.setText("")
        try:
            people = store.people()
        except PeopleError as e:
            people = []
            self.status_label.setText(str(e))
        self._people = {p.id: p for p in people}
        ticked = set(ticked)

        self._loading = True
        self.people_list.clear()
        for person in people:
            item = QListWidgetItem(_icon(person), person.name)
            item.setData(_ID_ROLE, person.id)
            if self._delete_only:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            else:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                              | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if person.id in ticked
                                   else Qt.CheckState.Unchecked)
            self.people_list.addItem(item)
        self._loading = False
        if people:
            self.people_list.setCurrentRow(0)
        if self._delete_only:
            self.hint_label.setText(PEOPLE_HINT if people else NOBODY_LEFT)
        else:
            self.hint_label.setText(HINT if people else NOBODY)
        self._update_buttons()

    def has_people(self) -> bool:
        return bool(self._people)

    def ticked_ids(self) -> list[str]:
        if self._delete_only:
            return []
        return [item.data(_ID_ROLE) for item in self._items()
                if item.checkState() == Qt.CheckState.Checked]

    def _items(self) -> list[QListWidgetItem]:
        return [self.people_list.item(n) for n in range(self.people_list.count())]

    def _current(self) -> Person | None:
        item = self.people_list.currentItem()
        return None if item is None else self._people.get(item.data(_ID_ROLE))

    # ----- ticks and buttons -----------------------------------------------

    def _ticks_changed(self, _item: QListWidgetItem) -> None:
        if not self._loading:
            self._update_buttons()

    def _update_buttons(self, *_) -> None:
        person = self._current()
        self.more_button.setText(
            "Add photos…" if person is None else f"Add photos to {person.name}…")
        self.delete_button.setText("Forget…" if person is None else f"Forget {person.name}")
        self.more_button.setEnabled(person is not None)
        self.delete_button.setEnabled(person is not None)
        self.start_button.setEnabled(bool(self.ticked_ids()))

    def _more(self) -> None:
        person = self._current()
        if person is not None:
            self.more_requested.emit(person.id)

    # ----- dropping a photo -------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if dropped_photo(event):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if dropped_photo(event):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        path = dropped_photo(event)
        if path is not None:
            event.acceptProposedAction()
            self.photo_dropped.emit(path)

    # ----- forgetting ------------------------------------------------------

    def confirm_forget(self, name: str) -> bool:
        answer = QMessageBox.question(
            self, FORGET_TITLE,
            f"Forget {name}? Photo Sorter will no longer recognise them. "
            "Photos already sorted stay where they are.")
        return answer == QMessageBox.StandardButton.Yes

    def _forget(self) -> None:
        person = self._current()
        if person is None or self._store is None or not self.confirm_forget(person.name):
            return
        try:
            self._store.delete(person.id)
        except PeopleError as e:
            self.status_label.setText(str(e))
            return
        self.load(self._store, self.ticked_ids())  # the others' ticks kept


class RememberedPeopleDialog(QDialog):
    """Settings' list of the remembered people, where they can be forgotten."""

    def __init__(self, store: PeopleStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Remembered people")
        layout = QVBoxLayout(self)
        self.page = FaceSetupPage(delete_only=True)
        layout.addWidget(self.page, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.page.load(store)
