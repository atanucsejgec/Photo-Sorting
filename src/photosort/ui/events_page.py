"""The events screen: one row per event found, for the owner to name, merge,
split or skip.

Each row shows the event's dates, how many files it has and a few
thumbnails. A blank name means the default one, shown greyed in the field.
Thumbnails are made on pool threads (see review_page.render_thumbnail),
tagged with the page generation and the row they were asked for; one for a
row that is gone (merged, split, or a page loaded since) is ignored.

Keys: ↑ and ↓ move between the rows (a GridNavigator, one column); on a row,
Space skips or unskips it, F2 or Enter renames it (in the name box Enter is
done and Esc stops editing), M merges it with the next and S splits it.
Ctrl+Enter continues to the summary and Esc asks whether to leave (left()).
"""

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from photosort.media import kind
from photosort.modes.date_mode import MONTH_NAMES
from photosort.modes.events import Event, default_names, merge, split
from photosort.thresholds import EVENT_THUMB_PX, EVENT_THUMBS
from photosort.ui import shortcuts
from photosort.ui.review_page import LOADING, VIDEO, render_thumbnail
from photosort.ui.shortcuts import GridNavigator
from photosort.ui.theme import styled
from photosort.ui.widgets import ButtonBar
from photosort.ui.worker import Task

_THUMB_GAP = 6  # between an event's thumbnails


def _day_text(day) -> str:
    """"20 Feb 2025": English months, whatever the system locale."""
    return f"{day.day} {MONTH_NAMES[day.month - 1][:3]} {day.year}"


def range_text(event: Event) -> str:
    """The event's day, or its first and last day: "20 Feb 2025 – 22 Feb 2025"."""
    start, end = event.start.date(), event.end.date()
    return _day_text(start) if start == end else f"{_day_text(start)} – {_day_text(end)}"


def _can_split(event: Event) -> bool:
    try:
        split(event)
    except ValueError:
        return False
    return True


def _spread[T](items: tuple[T, ...], count: int) -> list[T]:
    """At most count of items, evenly spread from the first to the last."""
    if len(items) <= count:
        return list(items)
    step = (len(items) - 1) / (count - 1) if count > 1 else 0
    return [items[round(i * step)] for i in range(count)]


class _ThumbStrip(QWidget):
    """An event's thumbnails in a row. It wants room for EVENT_THUMBS of them
    whatever the event has, so the names of all rows line up; given less (a
    narrow window), it shows as many as fit, at least one."""

    _STEP = EVENT_THUMB_PX + _THUMB_GAP

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(EVENT_THUMBS * self._STEP - _THUMB_GAP, EVENT_THUMB_PX)

    def minimumSizeHint(self) -> QSize:
        return QSize(EVENT_THUMB_PX, EVENT_THUMB_PX)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        fits = max(1, (self.width() + _THUMB_GAP) // self._STEP)
        for n, label in enumerate(self.findChildren(QLabel)):
            label.move(n * self._STEP, 0)
            label.setVisible(n < fits)


class EventRow(QFrame):
    """One event: its thumbnails, name field, dates and count, and its
    buttons. The row takes the focus; the name box takes it to be typed in."""

    def __init__(self, row_id: int, event: Event, name: str, skipped: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row_id = row_id  # unique within a page generation
        self.found_event = event  # not .event: that is QWidget.event()
        styled(self, "card")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.thumb_files = _spread(event.files, EVENT_THUMBS)
        self.thumb_labels: list[QLabel] = []
        strip = _ThumbStrip()
        for rel in self.thumb_files:
            label = QLabel(VIDEO if kind(rel) == "video" else LOADING, strip)
            label.setFixedSize(EVENT_THUMB_PX, EVENT_THUMB_PX)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setObjectName("thumb")  # see theme
            label.setToolTip(rel)
            self.thumb_labels.append(label)

        self.name_edit = QLineEdit(name)
        self.name_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # F2 or Enter on the row
        shortcuts.bind(self.name_edit, "Return", "Enter", "done", self.stop_editing)
        shortcuts.bind(self.name_edit, "Escape", "Esc", "stop editing", self.stop_editing)
        self.range_label = QLabel(range_text(event))
        count = len(event.files)
        self.count_label = QLabel(f"{count} file{'' if count == 1 else 's'}")
        self.count_label.setObjectName("muted")
        self.merge_button = QPushButton("Merge with next")
        self.split_button = QPushButton("Split")
        self.split_button.setEnabled(_can_split(event))
        self.skip_check = QCheckBox("Skip")
        self.skip_check.setChecked(skipped)
        self.skip_check.setToolTip("Leave this event's files where they are")
        for widget in (self.merge_button, self.split_button, self.skip_check):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the row's keys do these

        details = QVBoxLayout()
        details.addWidget(self.name_edit)
        details.addWidget(self.range_label)
        details.addWidget(self.count_label)
        buttons = QHBoxLayout()
        for widget in (self.merge_button, self.split_button, self.skip_check):
            buttons.addWidget(widget)
        buttons.addStretch()
        details.addLayout(buttons)
        details.addStretch()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)
        layout.addWidget(strip, 0)
        layout.addLayout(details, 1)

    def rename(self) -> None:
        """Type a new name: the name box takes the focus, its text selected."""
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
            label.setPixmap(QPixmap.fromImage(value).scaled(
                EVENT_THUMB_PX, EVENT_THUMB_PX, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            label.setText(value)


class EventsPage(QWidget):
    """Shows the events found; emits finished(events, names typed, indices
    skipped), cancelled() (Back) or left() (Esc, then Leave)."""

    finished = Signal(object, object, object)  # list[Event], list[str], set[int]
    cancelled = Signal()
    left = Signal()
    keys_changed = Signal()  # a key was switched on or off: the strip redraws

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root = Path()
        self._generation = 0  # bumped whenever the rows are replaced
        self._next_id = 0
        self._thumb_tasks: dict[tuple[int, int, int], Task] = {}  # kept alive until they report
        self._left_out = 0
        self.rows: list[EventRow] = []
        self.navigator = GridNavigator([], lambda: 1, vertical_only=True, parent=self)
        self.navigator.current_changed.connect(self._update_row_keys)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 12)
        layout.setSpacing(16)
        self.heading_label = QLabel()
        font = self.heading_label.font()
        font.setPointSizeF(font.pointSizeF() * 1.6)
        font.setWeight(QFont.Weight.Bold)
        self.heading_label.setFont(font)
        self.subtitle_label = QLabel()
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.heading_label)
        layout.addWidget(self.subtitle_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the rows take it
        layout.addWidget(self.scroll, 1)
        self._list = self._new_list()

        self.back_button = QPushButton("Back")
        self.continue_button = QPushButton("Continue")
        self.continue_button.setDefault(True)
        layout.addWidget(ButtonBar([self.back_button], [self.continue_button]))

        self.back_button.clicked.connect(self._cancel)
        self.continue_button.clicked.connect(self._finish)

        # Keys: the rows' own (while one has focus), then the page's. While a
        # name is typed, the row keys and Esc are off: the keys type, and
        # Enter and Esc are the name box's.
        shortcuts.describe(self.scroll, "↑↓", "move")
        self._skip_key = shortcuts.bind(self.scroll, "Space", "Space", "skip",
                                        self._skip_current, repeat=False)
        self._rename_key = shortcuts.bind(self.scroll, ["F2", "Return"], "F2/Enter",
                                          "rename", self._rename_current, repeat=False)
        self._merge_key = shortcuts.bind(self.scroll, "M", "M", "merge",
                                         self._merge_current, repeat=False)
        self._split_key = shortcuts.bind(self.scroll, "S", "S", "split",
                                         self._split_current, repeat=False)
        shortcuts.bind(self, "Ctrl+Return", "Ctrl+Enter", "continue", self._finish,
                       repeat=False)
        self._esc_key = shortcuts.bind(self, "Escape", "Esc", "back", self._leave,
                                       repeat=False)
        shortcuts.describe_tab(self)

    def load(self, root: Path, events: list[Event], left_out: int) -> None:
        """Show the events found in root, none named or skipped; left_out
        files are in no event."""
        self.stop_thumbnails()
        self._generation += 1
        self._root = Path(root)
        self._left_out = left_out
        self._list = self._new_list()  # the rows shown before are deleted
        self.rows = []
        for event in events:
            self._insert_row(len(self.rows), event, "", False)
        self._update_rows()
        self._focus_row(0)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.navigator.focus()  # the first row, or the one the owner was on

    # ----- rows ------------------------------------------------------------

    def _new_list(self) -> QVBoxLayout:
        """An empty list for the rows, in place of the one shown (which is deleted)."""
        holder = QWidget()
        rows = QVBoxLayout(holder)
        rows.addStretch()
        self.scroll.setWidget(holder)
        return rows

    def _insert_row(self, index: int, event: Event, name: str, skipped: bool) -> None:
        row = EventRow(self._next_id, event, name, skipped)
        self._next_id += 1
        row.merge_button.clicked.connect(lambda: self._merge_with_next(row))
        row.split_button.clicked.connect(lambda: self._split(row))
        shortcuts.pause_while_focused(
            row.name_edit, [self._skip_key, self._rename_key, self._merge_key,
                            self._split_key, self._esc_key], resume=self._update_row_keys)
        self.rows.insert(index, row)
        self._list.insertWidget(index, row)
        row.show()  # now, not when the layout gets round to it: it may take the focus
        for n, rel in enumerate(row.thumb_files):
            if kind(rel) != "video":
                self._request_thumbnail(row.row_id, n, self._root / rel)

    def _remove_row(self, row: EventRow) -> None:
        for (_, row_id, _), task in self._thumb_tasks.items():
            if row_id == row.row_id:
                task.cancel_event.set()
        self.rows.remove(row)
        self._list.removeWidget(row)
        row.deleteLater()

    def _update_rows(self) -> None:
        """The heading, subtitle, default names and Merge buttons, for the rows as they are."""
        count = len(self.rows)
        self.heading_label.setText(f"{count} event{'' if count == 1 else 's'}")
        text = ("Type a name for each event, or leave it blank for the name shown. "
                "Merge or split events, or tick Skip to leave an event where it is.")
        if self._left_out:
            text += f" {self._left_out} files are in no event and stay where they are."
        self.subtitle_label.setText(text)
        names = default_names([row.found_event for row in self.rows])
        for n, (row, name) in enumerate(zip(self.rows, names)):
            row.name_edit.setPlaceholderText(name)
            row.merge_button.setEnabled(n + 1 < count)

    def _merge_with_next(self, row: EventRow) -> None:
        """One row for row's event and the next: row's name stays, and it is
        skipped if either was (better a file left put than one moved by mistake)."""
        index = self.rows.index(row)
        if index + 1 >= len(self.rows):
            return
        following = self.rows[index + 1]
        name = row.name_edit.text()
        skipped = row.skip_check.isChecked() or following.skip_check.isChecked()
        self._remove_row(row)
        self._remove_row(following)
        self._insert_row(index, merge(row.found_event, following.found_event), name, skipped)
        self._update_rows()
        self._focus_row(index)

    def _split(self, row: EventRow) -> None:
        """Two rows for row's event cut in two: the name stays on the first,
        the Skip tick on both."""
        try:
            first, second = split(row.found_event)
        except ValueError:
            return
        index = self.rows.index(row)
        name, skipped = row.name_edit.text(), row.skip_check.isChecked()
        self._remove_row(row)
        self._insert_row(index, first, name, skipped)
        self._insert_row(index + 1, second, "", skipped)
        self._update_rows()
        self._focus_row(index)

    def _focus_row(self, index: int) -> None:
        """Make row index the current one (after the rows changed), focused if shown."""
        self.navigator.set_cards(self.rows, index)
        if self.isVisible():
            self.navigator.focus()
        self._update_row_keys()

    def _update_row_keys(self) -> None:
        """M and S work only where the current row can be merged or split
        (and not while a name is typed: see _insert_row)."""
        row = self.navigator.current()
        editing = row is not None and row.name_edit.hasFocus()
        merge_ok = not editing and row is not None and row.merge_button.isEnabled()
        split_ok = not editing and row is not None and row.split_button.isEnabled()
        if (self._merge_key.isEnabled(), self._split_key.isEnabled()) != (merge_ok, split_ok):
            self._merge_key.setEnabled(merge_ok)
            self._split_key.setEnabled(split_ok)
            self.keys_changed.emit()

    def _skip_current(self) -> None:
        if (row := self.navigator.current()) is not None:
            row.skip_check.toggle()

    def _rename_current(self) -> None:
        if (row := self.navigator.current()) is not None:
            row.rename()

    def _merge_current(self) -> None:
        if (row := self.navigator.current()) is not None:
            self._merge_with_next(row)

    def _split_current(self) -> None:
        if (row := self.navigator.current()) is not None:
            self._split(row)

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

    def stop_thumbnails(self) -> None:
        """Let the thumbnails still waiting for a pool thread finish without decoding."""
        for task in self._thumb_tasks.values():
            task.cancel_event.set()

    # ----- answers ---------------------------------------------------------

    def _finish(self) -> None:
        self.finished.emit(
            [row.found_event for row in self.rows],
            [row.name_edit.text() for row in self.rows],
            {n for n, row in enumerate(self.rows) if row.skip_check.isChecked()},
        )

    def _cancel(self) -> None:
        self.stop_thumbnails()
        self.cancelled.emit()

    def _leave(self) -> None:
        if shortcuts.confirm_leave(self):
            self.stop_thumbnails()
            self.left.emit()
