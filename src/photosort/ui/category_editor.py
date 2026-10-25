"""The editor of the Content mode's categories (Settings.content_categories).

The list always stays usable: a new or renamed name is refused, with the
reason shown under it, when settings.category_problem finds one (empty, no
letter or number, too long, one of the app's own folders, or another
category's folder); Add refuses a name past CONTENT_CATEGORIES_MAX, and
Remove the last one left.
"""

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton,
    QVBoxLayout, QWidget,
)

from photosort.settings import DEFAULT_CONTENT_CATEGORIES, category_problem
from photosort.thresholds import CONTENT_CATEGORIES_MAX


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

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Name:"))
        layout.addWidget(self.name_edit)
        layout.addWidget(self.message_label)
        layout.addWidget(buttons)
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
        self.list_widget.addItems(categories)
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.message_label.setObjectName("error")  # see theme

        self.add_button = QPushButton("Add…")
        self.rename_button = QPushButton("Rename…")
        self.remove_button = QPushButton("Remove")
        self.up_button = QPushButton("Move up")
        self.down_button = QPushButton("Move down")
        self.reset_button = QPushButton("Reset to defaults")
        side = QVBoxLayout()
        for button in (self.add_button, self.rename_button, self.remove_button,
                       self.up_button, self.down_button):
            side.addWidget(button)
        side.addStretch()
        side.addWidget(self.reset_button)
        row = QHBoxLayout()
        row.addWidget(self.list_widget, 1)
        row.addLayout(side)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Content mode sorts each photo into the folder of "
                                "the category it fits best."))
        layout.addLayout(row)
        layout.addWidget(self.message_label)
        layout.addWidget(buttons)

        self.add_button.clicked.connect(self._add)
        self.rename_button.clicked.connect(self._rename)
        self.remove_button.clicked.connect(self._remove)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.reset_button.clicked.connect(self._reset)
        self.list_widget.currentRowChanged.connect(self._update_buttons)
        self.list_widget.itemDoubleClicked.connect(lambda _: self._rename())
        self.list_widget.setCurrentRow(0)
        self._update_buttons()

    def categories(self) -> list[str]:
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]

    def _update_buttons(self, *_) -> None:
        row, count = self.list_widget.currentRow(), self.list_widget.count()
        self.rename_button.setEnabled(row >= 0)
        self.remove_button.setEnabled(row >= 0 and count > 1)
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < count - 1)

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
            self.message_label.setText(
                f"You can have up to {CONTENT_CATEGORIES_MAX} categories.")
            return
        self.message_label.setText("")
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
        self.message_label.setText("")
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
        self.message_label.setText("")
        self.list_widget.clear()
        self.list_widget.addItems(list(DEFAULT_CONTENT_CATEGORIES))
        self.list_widget.setCurrentRow(0)
        self._update_buttons()
