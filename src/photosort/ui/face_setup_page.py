"""The Face setup page: the remembered people, ticked for this sort.

Everyone comes up unticked: the owner ticks the people to sort by, one for
all their photos, two or more for only the photos with two or more of them.
Only the people load() is asked to tick (say, ones just named) come up ticked. A photo
dropped on the page is named like one picked with "Add person…". The same
list, without ticks, drops and with only Forget, is the People page.

Keys (not on the People page, which has its own): ↑↓ move in the list,
Space ticks, Enter starts sorting (once someone is ticked), A adds a person,
Delete forgets one (after asking) and Esc goes back Home. Enter presses a
button Tab has reached, once (widgets.ButtonKeys).

The face pictures are small JPEGs in the app's own data folder, so they are
read on the GUI thread.
"""

import logging
from collections.abc import Iterable

from PIL import Image
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from photosort.people import PeopleError, PeopleStore, Person
from photosort.ui import dialogs, shortcuts
from photosort.ui.face_naming_dialog import dropped_photo
from photosort.ui.labels import literal
from photosort.ui.qimage import pil_to_qimage
from photosort.ui.theme import heading
from photosort.ui.widgets import ButtonBar, ButtonKeys

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
    keys_changed = Signal()  # a key was switched on or off (the strip redraws)

    def __init__(self, parent: QWidget | None = None, *, delete_only: bool = False) -> None:
        super().__init__(parent)
        self._delete_only = delete_only
        self._store: PeopleStore | None = None
        self._people: dict[str, Person] = {}
        self._loading = False
        self.setAcceptDrops(not delete_only)

        layout = QVBoxLayout(self)
        if not delete_only:
            layout.setContentsMargins(32, 24, 32, 12)
            layout.setSpacing(14)
            layout.addWidget(heading(HEADING))
        self.hint_label = QLabel()
        self.hint_label.setObjectName("muted")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.people_list = QListWidget()
        self.people_list.setIconSize(QSize(ICON_PX, ICON_PX))
        layout.addWidget(self.people_list, 1)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.hide()  # shown with a problem to tell (see _status)
        layout.addWidget(self.status_label)

        self.add_button = QPushButton("Add person…")
        self.more_button = QPushButton()
        self.delete_button = QPushButton()
        self.back_button = QPushButton("Back")
        self.start_button = QPushButton("Start sorting")
        self.start_button.setObjectName("primaryButton")  # see theme
        for button in (self.back_button, self.start_button):
            button.setMinimumWidth(120)
        layout.addWidget(ButtonBar([self.add_button, self.more_button, self.delete_button],
                                   [self.back_button, self.start_button]))
        for button in (self.add_button, self.more_button, self.start_button,
                       self.back_button):
            button.setVisible(not delete_only)
        buttons = [self.add_button, self.more_button, self.delete_button, self.back_button,
                   self.start_button]
        ButtonKeys(buttons)

        self.people_list.itemChanged.connect(self._ticks_changed)
        self.people_list.currentItemChanged.connect(self._update_buttons)
        self.add_button.clicked.connect(self.add_requested)
        self.more_button.clicked.connect(self._more)
        self.delete_button.clicked.connect(self._forget)
        self.start_button.clicked.connect(lambda: self.start.emit(self.ticked_ids()))

        self._keys: list = []  # (shortcut, the button it presses), switched on with it
        if not delete_only:
            shortcuts.describe(self.people_list, "↑↓", "choose")
            shortcuts.describe(self.people_list, "Space", "tick")
            for owner, keys, label, word, button in (
                    (self.people_list, "Return", "Enter", "start", self.start_button),
                    (self, "A", "A", "add person", self.add_button),
                    (self, "Delete", "Delete", "forget", self.delete_button),
                    (self, "Escape", "Esc", "back", self.back_button)):
                key = shortcuts.bind(owner, keys, label, word, button.click, repeat=False)
                self._keys.append((key, button))
            shortcuts.describe_tab(self)
        self._update_buttons()

    # ----- loading ---------------------------------------------------------

    def load(self, store: PeopleStore, ticked: Iterable[str] = ()) -> None:
        """List the people remembered in store, unticked but for the ids in ticked."""
        self._store = store
        self._status("")
        try:
            people = store.people()
        except PeopleError as e:
            people = []
            self._status(str(e))
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

    def _status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

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
            "Add photos…" if person is None else literal(f"Add photos to {person.name}…"))
        self.delete_button.setText(
            "Forget…" if person is None else literal(f"Forget {person.name}"))
        self.more_button.setEnabled(person is not None)
        self.delete_button.setEnabled(person is not None)
        self.start_button.setEnabled(bool(self.ticked_ids()))
        changed = False
        for key, button in self._keys:
            if key.isEnabled() != button.isEnabled():
                key.setEnabled(button.isEnabled())
                changed = True
        if changed:
            self.keys_changed.emit()

    def focus_first(self) -> None:
        """The list, on its current person; Add person… when nobody is remembered."""
        target = self.people_list if self.people_list.count() else self.add_button
        target.setFocus(Qt.FocusReason.OtherFocusReason)

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
        return dialogs.ask(
            self, FORGET_TITLE,
            f"Forget {name}? Photo Sorter will no longer recognise them. "
            "Photos already sorted stay where they are.",
            yes="Forget", no="Keep", default="no")

    def _forget(self) -> None:
        person = self._current()
        if person is None or self._store is None or not self.confirm_forget(person.name):
            return
        try:
            self._store.delete(person.id)
        except PeopleError as e:
            self._status(str(e))
            return
        self.load(self._store, self.ticked_ids())  # the others' ticks kept
