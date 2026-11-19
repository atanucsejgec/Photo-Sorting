"""The "Name the busy days" screen of a Date sort: one row per busy day, for
the owner to name or leave blank.

Each row shows the day ("Thu 20 Feb 2025"), how many photos and videos it
has, a collage of its first BUSY_DAY_COLLAGE photos, Show all, and a name
box. A blank name keeps the day's folder "DD-MM-YYYY", shown greyed in the
box; a name makes it "DD-MM-YYYY <name>". Show all opens a scrollable grid
of every file of the day in place of the rows; Esc (or Close) closes it.
Thumbnails are made on pool threads (see review_page.render_thumbnail),
tagged with the page generation and the row (the grid's, with its own
generation); one for rows or a grid that is gone is ignored.

Keys: ↑ and ↓ move between the days (a GridNavigator, one column); on a
day, Enter or F2 types its name (in the name box Enter is done and Esc
stops editing) and A shows all its files. Ctrl+Enter continues (named())
and Esc asks whether to leave (left()).
"""

from datetime import date
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from photosort.media import kind
from photosort.modes.date_mode import MONTH_NAMES
from photosort.thresholds import (
    BUSY_DAY_ALL_THUMB_PX, BUSY_DAY_COLLAGE, BUSY_DAY_COLLAGE_ROWS, BUSY_DAY_THUMB_PX,
)
from photosort.ui import shortcuts
from photosort.ui.review_page import LOADING, VIDEO, render_thumbnail
from photosort.ui.shortcuts import GridNavigator
from photosort.ui.theme import page_spacing, scaled_font, styled
from photosort.ui.widgets import ButtonBar
from photosort.ui.worker import Task

HEADING = "Name the busy days"
SUBTITLE = ("These days have many photos. Type a name to keep a day in a folder such "
            "as \"20-02-2025 Puri trip\", or leave it blank for the date alone.")
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_GAP = 4  # between the collage's thumbnails


def day_text(d: date) -> str:
    """"Thu 20 Feb 2025": English names, whatever the system locale."""
    return f"{_WEEKDAYS[d.weekday()]} {d.day} {MONTH_NAMES[d.month - 1][:3]} {d.year}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def count_text(files: list[str]) -> str:
    """"34 photos, 3 videos" ("34 photos" with none)."""
    videos = sum(kind(rel) == "video" for rel in files)
    text = _plural(len(files) - videos, "photo")
    return f"{text}, {_plural(videos, 'video')}" if videos else text


class _Collage(QWidget):
    """A day's thumbnails, BUSY_DAY_COLLAGE_ROWS rows of them. It wants room
    for BUSY_DAY_COLLAGE whatever the day has, so the rows line up; given
    less (a narrow window), it shows as many columns as fit, at least two."""

    _STEP = BUSY_DAY_THUMB_PX + _GAP
    _COLUMNS = -(-BUSY_DAY_COLLAGE // BUSY_DAY_COLLAGE_ROWS)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(self._COLUMNS * self._STEP - _GAP,
                     BUSY_DAY_COLLAGE_ROWS * self._STEP - _GAP)

    def minimumSizeHint(self) -> QSize:
        return QSize(2 * self._STEP - _GAP, self.sizeHint().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        columns = max(2, min(self._COLUMNS, (self.width() + _GAP) // self._STEP))
        for n, label in enumerate(self.findChildren(QLabel)):
            row, column = divmod(n, columns)
            label.move(column * self._STEP, row * self._STEP)
            label.setVisible(row < BUSY_DAY_COLLAGE_ROWS)


def _thumb_pixmap(image: QImage, px: int) -> QPixmap:
    return QPixmap.fromImage(image).scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)


class DayRow(QFrame):
    """One busy day: its date and counts, name box and Show all, and its
    collage. The row takes the focus; the name box takes it to be typed in."""

    def __init__(self, row_id: int, day: date, files: list[str],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row_id = row_id  # unique within a page generation
        self.day = day
        self.files = list(files)
        styled(self, "card")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.collage_files = [rel for rel in files if kind(rel) != "video"][:BUSY_DAY_COLLAGE]
        self.thumb_labels: list[QLabel] = []
        collage = _Collage()
        for rel in self.collage_files:
            label = QLabel(LOADING, collage)
            label.setFixedSize(BUSY_DAY_THUMB_PX, BUSY_DAY_THUMB_PX)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setObjectName("thumb")  # see theme
            label.setToolTip(rel)
            self.thumb_labels.append(label)

        self.date_label = QLabel(day_text(day))
        self.date_label.setFont(scaled_font(self.date_label, 1.15))
        self.count_label = QLabel(count_text(files))
        self.count_label.setObjectName("muted")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(f"{day.day:02d}-{day.month:02d}-{day.year:04d}")
        self.name_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # F2 or Enter on the row
        shortcuts.bind(self.name_edit, "Return", "Enter", "done", self.stop_editing)
        shortcuts.bind(self.name_edit, "Escape", "Esc", "stop editing", self.stop_editing)
        self.show_all_button = QPushButton("Show all")
        self.show_all_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the row's A does it
        self.show_all_button.setToolTip(f"See all {len(files)} files of this day")

        details = QVBoxLayout()
        details.addWidget(self.date_label)
        details.addWidget(self.count_label)
        details.addWidget(self.name_edit)
        buttons = QHBoxLayout()
        buttons.addWidget(self.show_all_button)
        buttons.addStretch()
        details.addLayout(buttons)
        details.addStretch()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)
        layout.addLayout(details, 1)
        layout.addWidget(collage, 0)

    def rename(self) -> None:
        """Type a name: the name box takes the focus, its text selected."""
        self.name_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self.name_edit.selectAll()

    def stop_editing(self) -> None:
        """Back to the row; the name typed stays."""
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_thumbnail(self, n: int, value: QImage | str) -> None:
        """Show thumbnail n, or a placeholder text such as "Can't show". GUI thread only."""
        label = self.thumb_labels[n]
        if isinstance(value, QImage):
            label.setText("")
            label.setPixmap(_thumb_pixmap(value, BUSY_DAY_THUMB_PX))
        else:
            label.setText(value)


class AllFilesView(QWidget):
    """Every file of one day, as a scrollable grid of thumbnails; Esc or
    Close closes it (closed())."""

    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.heading_label = QLabel()
        self.heading_label.setFont(scaled_font(self.heading_label, 1.3))
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setIconSize(QSize(BUSY_DAY_ALL_THUMB_PX, BUSY_DAY_ALL_THUMB_PX))
        self.grid.setGridSize(QSize(BUSY_DAY_ALL_THUMB_PX + 16, BUSY_DAY_ALL_THUMB_PX + 36))
        self.grid.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.grid.setWordWrap(False)
        self.close_button = QPushButton("Close")
        layout.addWidget(self.heading_label)
        layout.addWidget(self.grid, 1)
        layout.addWidget(ButtonBar([], [self.close_button]))
        self.close_button.clicked.connect(self.closed.emit)
        shortcuts.describe(self, "↑↓←→", "move")  # the grid's own; Esc works on Close too
        self._esc_key = shortcuts.bind(self, "Escape", "Esc", "close", self.closed.emit,
                                       repeat=False)

    def load(self, day: date, files: list[str]) -> None:
        self.heading_label.setText(f"{day_text(day)}: all {_plural(len(files), 'file')}")
        self.grid.clear()
        for rel in files:
            item = QListWidgetItem(rel.rpartition("/")[2])
            item.setToolTip(rel)
            if kind(rel) == "video":
                item.setText(f"{VIDEO}: {item.text()}")
            self.grid.addItem(item)
        if files:
            self.grid.setCurrentRow(0)

    def set_thumbnail(self, n: int, value: QImage | str) -> None:
        item = self.grid.item(n)
        if item is None:
            return
        if isinstance(value, QImage):
            item.setIcon(QIcon(_thumb_pixmap(value, BUSY_DAY_ALL_THUMB_PX)))
        else:
            item.setText(f"{value}: {item.toolTip().rpartition('/')[2]}")


class BusyDaysPage(QWidget):
    """Shows the busy days; emits named({day: name typed}, blank ones left
    out) on Continue, or left() (Esc or Back, then Leave)."""

    named = Signal(object)  # dict[date, str]
    left = Signal()
    keys_changed = Signal()  # a key was switched on or off: the strip redraws

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root = Path()
        self._generation = 0  # bumped whenever the rows are replaced
        self._all_generation = 0  # bumped whenever Show all opens or closes
        self._thumb_tasks: dict[tuple[int, int, int], Task] = {}  # kept alive until they report
        self._all_tasks: dict[tuple[int, int], Task] = {}
        self._all_row: DayRow | None = None  # whose files Show all shows
        self.rows: list[DayRow] = []
        self.navigator = GridNavigator([], lambda: 1, vertical_only=True, parent=self)

        layout = QVBoxLayout(self)
        page_spacing(layout)
        self.views = QStackedWidget()
        layout.addWidget(self.views, 1)

        rows_view = QWidget()
        rows_layout = QVBoxLayout(rows_view)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(12)
        self.heading_label = QLabel(HEADING)
        self.heading_label.setFont(scaled_font(self.heading_label, 1.6, QFont.Weight.Bold))
        self.subtitle_label = QLabel(SUBTITLE)
        self.subtitle_label.setWordWrap(True)
        rows_layout.addWidget(self.heading_label)
        rows_layout.addWidget(self.subtitle_label)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the rows take it
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rows_layout.addWidget(self.scroll, 1)
        self.back_button = QPushButton("Back")
        self.continue_button = QPushButton("Continue")
        self.continue_button.setDefault(True)
        rows_layout.addWidget(ButtonBar([self.back_button], [self.continue_button]))
        self.views.addWidget(rows_view)
        self._rows_view = rows_view
        self._list = self._new_list()

        self.all_view = AllFilesView()
        self.all_view.closed.connect(self.close_all)
        self.views.addWidget(self.all_view)

        self.back_button.clicked.connect(self._leave)
        self.continue_button.clicked.connect(self._finish)

        # Keys: the rows' own (while one has focus), then the page's. While a
        # name is typed, the row keys and Esc are off: the keys type, and
        # Enter and Esc are the name box's. While Show all is open, its Esc
        # is the one, and Ctrl+Enter is off.
        shortcuts.describe(self.scroll, "↑↓", "day")
        self._name_key = shortcuts.bind(self.scroll, ["Return", "F2"], "Enter", "name",
                                        self._name_current, repeat=False)
        self._all_key = shortcuts.bind(self.scroll, "A", "A", "all photos",
                                       self._show_all_current, repeat=False)
        self._continue_key = shortcuts.bind(self, "Ctrl+Return", "Ctrl+Enter", "continue",
                                            self._finish, repeat=False)
        self._esc_key = shortcuts.bind(self, "Escape", "Esc", "leave", self._leave,
                                       repeat=False)
        shortcuts.describe_tab(self)

    def load(self, root: Path, days: dict[date, list[str]]) -> None:
        """Show days (oldest first, each with all its files, relative to
        root), none named."""
        self.stop_thumbnails()
        self.close_all()
        self._generation += 1
        self._root = Path(root)
        self._list = self._new_list()  # the rows shown before are deleted
        self.rows = []
        for row_id, (day, files) in enumerate(days.items()):
            row = DayRow(row_id, day, files)
            row.show_all_button.clicked.connect(lambda _=False, r=row: self.show_all(r))
            shortcuts.pause_while_focused(
                row.name_edit, [self._name_key, self._all_key, self._esc_key])
            self.rows.append(row)
            self._list.insertWidget(len(self.rows) - 1, row)
            for n, rel in enumerate(row.collage_files):
                self._request_thumbnail(row.row_id, n, self._root / rel)
        self.navigator.set_cards(self.rows, 0)
        if self.isVisible():
            self.navigator.focus()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.views.currentWidget() is self.all_view:
            self.all_view.grid.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.navigator.focus()  # the first row, or the one the owner was on

    def _new_list(self) -> QVBoxLayout:
        """An empty list for the rows, in place of the one shown (which is deleted)."""
        holder = QWidget()
        rows = QVBoxLayout(holder)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.addStretch()
        self.scroll.setWidget(holder)
        return rows

    def _name_current(self) -> None:
        if (row := self.navigator.current()) is not None:
            row.rename()

    def _show_all_current(self) -> None:
        if (row := self.navigator.current()) is not None:
            self.show_all(row)

    # ----- Show all ----------------------------------------------------------

    def show_all(self, row: DayRow) -> None:
        """Every file of row's day, in place of the rows."""
        self._stop_all()
        self._all_generation += 1
        self._all_row = row
        self.all_view.load(row.day, row.files)
        for n, rel in enumerate(row.files):
            if kind(rel) != "video":
                self._request_all_thumbnail(n, self._root / rel)
        self._continue_key.setEnabled(False)
        self._esc_key.setEnabled(False)
        self.views.setCurrentWidget(self.all_view)
        self.all_view.grid.setFocus(Qt.FocusReason.OtherFocusReason)
        self.keys_changed.emit()

    def close_all(self) -> None:
        """Back to the rows, on the day that was shown."""
        if self.views.currentWidget() is not self.all_view:
            return
        self._stop_all()
        self._all_generation += 1
        self.all_view.grid.clear()
        row, self._all_row = self._all_row, None
        self._continue_key.setEnabled(True)
        self._esc_key.setEnabled(True)
        self.views.setCurrentWidget(self._rows_view)
        if row in self.rows and self.isVisible():
            self.navigator.focus(self.rows.index(row))
        self.keys_changed.emit()

    def _stop_all(self) -> None:
        for task in self._all_tasks.values():
            task.cancel_event.set()

    # ----- thumbnails ------------------------------------------------------

    def _request_thumbnail(self, row_id: int, n: int, path: Path) -> None:
        token = (self._generation, row_id, n)
        task = Task(render_thumbnail, token, path)
        task.signals.result.connect(self._thumbnail_ready)
        self._thumb_tasks[token] = task
        task.start()

    def _thumbnail_ready(self, result: tuple[int, int, int, QImage | str | None]) -> None:
        generation, row_id, n, value = result
        self._thumb_tasks.pop((generation, row_id, n), None)
        if generation != self._generation or value is None:
            return  # asked for rows that are gone
        row = next((r for r in self.rows if r.row_id == row_id), None)
        if row is not None:
            row.set_thumbnail(n, value)

    def _request_all_thumbnail(self, n: int, path: Path) -> None:
        token = (self._all_generation, n)
        task = Task(render_thumbnail, token, path)
        task.signals.result.connect(self._all_thumbnail_ready)
        self._all_tasks[token] = task
        task.start()

    def _all_thumbnail_ready(self, result: tuple[int, int, QImage | str | None]) -> None:
        generation, n, value = result
        self._all_tasks.pop((generation, n), None)
        if generation != self._all_generation or value is None:
            return  # asked for a grid that is gone
        self.all_view.set_thumbnail(n, value)

    def stop_thumbnails(self) -> None:
        """Let the thumbnails still waiting for a pool thread finish without decoding."""
        for task in self._thumb_tasks.values():
            task.cancel_event.set()
        self._stop_all()

    # ----- answers ---------------------------------------------------------

    def _finish(self) -> None:
        self.named.emit({row.day: row.name_edit.text() for row in self.rows
                         if row.name_edit.text().strip()})

    def _leave(self) -> None:
        if shortcuts.confirm_leave(self):
            self.stop_thumbnails()
            self.left.emit()
