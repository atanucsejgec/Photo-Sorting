"""The People page: the people Photo Sorter remembers by face, where they can
be forgotten (Forget, or the Delete key; it asks first, with "Keep" focused).

The list is the Face setup page's, without ticks or drops (delete_only). It
is read from the store each time the page is shown (load).
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from photosort.people import PeopleStore
from photosort.ui import shortcuts
from photosort.ui.face_setup_page import FaceSetupPage
from photosort.ui.theme import heading, page_spacing


class PeoplePage(QWidget):
    keys_changed = Signal()  # a key was switched on or off (the strip redraws)

    def __init__(self, store: PeopleStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        layout = QVBoxLayout(self)
        page_spacing(layout)
        layout.addWidget(heading("People"))
        self.list_page = FaceSetupPage(delete_only=True)
        self.list_page.layout().setContentsMargins(0, 0, 0, 0)
        self.list_page.hint_label.setObjectName("muted")
        layout.addWidget(self.list_page, 1)
        self.people_list = self.list_page.people_list
        self.delete_button = self.list_page.delete_button

        shortcuts.describe(self.people_list, "↑↓", "choose")
        self._forget_key = shortcuts.bind(self, "Delete", "Delete", "forget",
                                          self.delete_button.click, repeat=False)
        self.people_list.model().rowsInserted.connect(self._update_keys)
        self.people_list.model().rowsRemoved.connect(self._update_keys)
        self.people_list.model().modelReset.connect(self._update_keys)
        self._update_keys()

    def load(self) -> None:
        """List the people in the store as it is now."""
        self.list_page.load(self.store)
        self._update_keys()

    def _update_keys(self, *_) -> None:
        enabled = self.people_list.count() > 0
        if self._forget_key.isEnabled() != enabled:
            self._forget_key.setEnabled(enabled)
            self.keys_changed.emit()
