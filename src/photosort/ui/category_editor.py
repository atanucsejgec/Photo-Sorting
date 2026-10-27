"""The editor of the Content mode's categories (Settings.content_categories).

The list always stays usable: a new or renamed name is refused, with the
reason shown under it, when settings.category_problem finds one (empty, no
letter or number, too long, one of the app's own folders, or another
category's folder); Add refuses a name past CONTENT_CATEGORIES_MAX, and
Remove the last one left.

Keys, in the editor: ↑↓ choose, Insert or A add, F2 rename, Delete remove,
Alt+↑ and Alt+↓ move the chosen one, R resets to the defaults, Enter is OK
and Esc cancels (Enter presses a button Tab has reached instead, once:
widgets.ButtonKeys). In the name dialog: Enter OK, Esc cancel. A key whose
button is disabled does nothing, and is not listed.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton,
    QVBoxLayout, QWidget,
)

from photosort.settings import DEFAULT_CONTENT_CATEGORIES, category_problem
from photosort.thresholds import CONTENT_CATEGORIES_MAX
from photosort.ui import shortcuts
from photosort.ui.shortcuts import ShortcutStrip
from photosort.ui.theme import heading
from photosort.ui.widgets import ButtonBar, ButtonKeys

EDITOR_MIN_WIDTH = 600


def _frame(dialog: QDialog) -> QVBoxLayout:
    """dialog's layout, with the margins of the app's dialogs."""
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 20, 24, 12)
    layout.setSpacing(12)
    return layout


def _add_strip(dialog: QDialog, layout: QVBoxLayout) -> ShortcutStrip:
    strip = ShortcutStrip(dialog)
    layout.addWidget(strip)
    strip.show_for(dialog)
    return strip


class CategoryNameDialog(QDialog):
    """Asks for a category name; OK is only possible for a name that is fine
    beside others (the rest of the list), and the problem is shown otherwise."""

    def __init__(self, title: str, name: str, others: list[str],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._others = list(others)
        self.setWindowTitle(title)
        self.name_edit = QLineEdit(name)
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.message_label.setObjectName("error")  # see theme
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        self.setMinimumWidth(360)
        layout = _frame(self)
        layout.addWidget(heading(title))
        name_label = QLabel("Name:")
        name_label.setBuddy(self.name_edit)
        layout.addWidget(name_label)
        layout.addWidget(self.name_edit)
        layout.addWidget(self.message_label)
        layout.addWidget(buttons)
        shortcuts.describe(self, "Enter", "OK")
        shortcuts.describe(self, "Esc", "cancel")
        self.strip = _add_strip(self, layout)
        self.name_edit.textChanged.connect(self._check)
        self._check()

    def name(self) -> str:
        """The name typed, without spaces around it."""
        return self.name_edit.text().strip()

    def _problem(self) -> str | None:
        return category_problem(self.name(), self._others)

    def _check(self, *_) -> None:
        problem = self._problem()
        self.message_label.setText(problem or "")
        self.ok_button.setEnabled(problem is None)

    def accept(self) -> None:
        if self._problem() is None:  # Enter in the field still reaches here
            super().accept()


class CategoryEditor(QDialog):
    """Edits a copy of categories; categories() is the list as it now stands."""

    def __init__(self, categories: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Content categories")
        self.list_widget = QListWidget()
        self.list_widget.setMinimumWidth(220)
        self.list_widget.addItems(categories)
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.message_label.setObjectName("error")  # see theme
        self.message_label.hide()  # shown with a problem to tell (see _message)

        self.add_button = QPushButton("Add…")
        self.rename_button = QPushButton("Rename…")
        self.remove_button = QPushButton("Remove")
        self.up_button = QPushButton("Move up")
        self.down_button = QPushButton("Move down")
        self.reset_button = QPushButton("Reset to defaults")
        side = QVBoxLayout()
        side.setSpacing(6)
        for button in (self.add_button, self.rename_button, self.remove_button,
                       self.up_button, self.down_button):
            side.addWidget(button)
        side.addStretch()
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self.list_widget, 1)
        row.addLayout(side)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        layout = _frame(self)
        layout.addWidget(heading("Content categories"))
        hint = QLabel("Content mode sorts each photo into the folder of "
                      "the category it fits best.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addLayout(row, 1)
        layout.addWidget(self.message_label)
        # Reset beside OK and Cancel: in a short window the side column fits.
        layout.addWidget(ButtonBar([self.reset_button], [buttons]))
        self.setMinimumWidth(EDITOR_MIN_WIDTH)  # the strip on two rows, not four

        self._keys: list = []  # (shortcut, the button it presses), switched on with it
        self.add_button.clicked.connect(self._add)
        self.rename_button.clicked.connect(self._rename)
        self.remove_button.clicked.connect(self._remove)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.reset_button.clicked.connect(self._reset)
        self.list_widget.currentRowChanged.connect(self._update_buttons)
        self.list_widget.itemDoubleClicked.connect(lambda _: self._rename())
        # Keys (see the module doc): each one presses its button, so it does
        # what the button does and is off while the button is.
        shortcuts.describe(self.list_widget, "↑↓", "choose")
        shortcuts.bind(self.list_widget, "Return", "Enter", "OK", self.accept, repeat=False)
        words = ((self.add_button, "add"), (self.rename_button, "rename"),
                 (self.remove_button, "remove"), (self.up_button, "move up"),
                 (self.down_button, "move down"), (self.reset_button, "reset"),
                 (self.ok_button, "OK"), (self.cancel_button, "cancel"))
        ButtonKeys([button for button, _ in words])
        for button, word in words:
            shortcuts.describe(button, "Enter", word)
        self._keys = [
            (shortcuts.bind(self, keys, label, word, button.click, repeat=repeat), button)
            for keys, label, word, button, repeat in (
                (["Insert", "A"], "Insert/A", "add", self.add_button, False),
                ("F2", "F2", "rename", self.rename_button, False),
                ("Delete", "Delete", "remove", self.remove_button, False),
                ("Alt+Up", "Alt+↑", "move up", self.up_button, True),
                ("Alt+Down", "Alt+↓", "move down", self.down_button, True),
                ("R", "R", "reset", self.reset_button, False))]
        shortcuts.describe(self, "Esc", "cancel")
        shortcuts.describe_tab(self)
        self.strip = _add_strip(self, layout)
        self.list_widget.setCurrentRow(0)
        self._update_buttons()
        self.list_widget.setFocus()

    def categories(self) -> list[str]:
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]

    def _update_buttons(self, *_) -> None:
        row, count = self.list_widget.currentRow(), self.list_widget.count()
        self.rename_button.setEnabled(row >= 0)
        self.remove_button.setEnabled(row >= 0 and count > 1)
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < count - 1)
        if shortcuts.follow(self._keys):
            self.strip.refresh()

    def _message(self, text: str) -> None:
        """A problem to tell, under the list; hidden while there is none."""
        self.message_label.setText(text)
        self.message_label.setVisible(bool(text))

    def _ask_name(self, title: str, name: str, others: list[str]) -> str | None:
        """The name the owner gave, or None if they cancelled."""
        dialog = CategoryNameDialog(title, name, others, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        name = dialog.name()
        dialog.deleteLater()
        return name if accepted else None

    def _add(self) -> None:
        names = self.categories()
        if len(names) >= CONTENT_CATEGORIES_MAX:
            self._message(f"You can have up to {CONTENT_CATEGORIES_MAX} categories.")
            return
        self._message("")
        name = self._ask_name("Add a category", "", names)
        if name is not None:
            self.list_widget.addItem(name)
            self.list_widget.setCurrentRow(self.list_widget.count() - 1)

    def _rename(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0:
            return
        names = self.categories()
        others = names[:row] + names[row + 1:]
        name = self._ask_name("Rename the category", names[row], others)
        if name is not None:
            self.list_widget.item(row).setText(name)

    def _remove(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or self.list_widget.count() <= 1:
            return
        self._message("")
        self.list_widget.takeItem(row)
        self._update_buttons()

    def _move(self, delta: int) -> None:
        row = self.list_widget.currentRow()
        to = row + delta
        if row < 0 or not 0 <= to < self.list_widget.count():
            return
        item = self.list_widget.takeItem(row)
        self.list_widget.insertItem(to, item)
        self.list_widget.setCurrentRow(to)

    def _reset(self) -> None:
        self._message("")
        self.list_widget.clear()
        self.list_widget.addItems(list(DEFAULT_CONTENT_CATEGORIES))
        self.list_widget.setCurrentRow(0)
        self._update_buttons()
