"""The review screen: one group at a time, the owner ticks the files to keep.

Thumbnails are made on pool threads (a full decode of a HEIC or PNG takes
long enough to freeze the window) and arrive at ReviewPage._thumbnail_ready
tagged with the page generation they were asked for; one asked for an
earlier group, whose cards are gone, is ignored.

Keys: ← and → move between the files (a GridNavigator, one row), Space
keeps or sets aside the focused one, and Enter shows it big (repeated
shots: the full-screen viewer, from that file). K keeps all, 1 keeps the
sharpest only (repeated shots), N skips, Ctrl+→ confirms and goes on to the
next group, Ctrl+Enter stops reviewing (to the summary). Esc goes back a
group, or on the first asks whether to leave (left()).
"""

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from photosort.groups import Candidate, Group, sharpest, sharpness_label
from photosort.imageinfo import UnreadableImage, thumbnail
from photosort.modes import MODE_SPECS
from photosort.modes.date_mode import MONTH_NAMES
from photosort.naming import resolve_existing_dir
from photosort.thresholds import THUMB_PX
from photosort.ui import shortcuts
from photosort.ui.fullscreen_viewer import FullscreenViewer
from photosort.ui.labels import literal
from photosort.ui.photo_preview import PhotoPreview
from photosort.ui.qimage import pil_to_qimage
from photosort.ui.shortcuts import GridNavigator
from photosort.ui.theme import styled
from photosort.ui.worker import Task

_log = logging.getLogger("photosort")

KIND_LABEL = {
    "exact": "Exact copies",
    "resized": "Same photo, resized or re-saved",
    "repeated": "Repeated shots",
}
LOADING = "Loading…"
CARD_SPACING = 12  # between the cards of a group
VIDEO = "Video"
CANT_SHOW = "Can't show"


def _taken_text(taken: datetime | None) -> str:
    if taken is None:
        return "no date inside"
    month = MONTH_NAMES[taken.month - 1][:3]  # English, whatever the system locale
    return f"{taken.day} {month} {taken:%Y, %H:%M:%S}"  # no leading zero on the day


def render_thumbnail(token: tuple[int, int], path: Path, progress=None, cancel=None):
    """Return (*token, QImage or CANT_SHOW), or (*token, None) if cancelled first.

    Runs on a pool thread; a QImage (unlike a QPixmap) may be made here.
    """
    if cancel is not None and cancel.is_set():
        return (*token, None)
    try:
        image = pil_to_qimage(thumbnail(path))
    except UnreadableImage:
        return (*token, CANT_SHOW)
    except Exception:  # a thumbnail must never stop the review
        _log.exception("Thumbnail of %s failed", path)
        return (*token, CANT_SHOW)
    return (*token, image)


class CandidateCard(QFrame):
    """One file of a group: its thumbnail, what is known about it, and Keep.
    The card takes the focus; the Keep tick is Space's."""

    def __init__(self, root: Path, candidate: Candidate, group: Group,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root = Path(root)
        self.candidate = candidate
        styled(self, "card")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.thumb_label = QLabel(VIDEO if candidate.kind == "video" else LOADING)
        self.thumb_label.setFixedSize(THUMB_PX, THUMB_PX)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setObjectName("thumb")  # see theme

        c = candidate
        folder, _, name = c.rel.rpartition("/")
        lines = [name]
        if c.width is not None and c.height is not None:
            lines.append(f"{c.width} × {c.height}")
        lines.append(f"{c.size / 1_000_000:.1f} MB")
        lines.append(_taken_text(c.taken))
        label = sharpness_label(c, group)
        if label:
            lines.append(f"Sharpness: {label}")
        if c.note:
            lines.append(c.note)
        lines.append(folder or "(top folder)")
        self.details_label = QLabel("\n".join(lines))
        self.details_label.setFixedWidth(THUMB_PX)
        self.details_label.setWordWrap(True)
        self.details_label.setToolTip(c.rel)

        self.keep_check = QCheckBox("Keep")
        self.keep_check.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Space on the card ticks

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        layout.addWidget(self.thumb_label)
        layout.addWidget(self.details_label)
        layout.addWidget(self.keep_check)
        layout.addStretch()

    def path(self) -> Path:
        return self.root / self.candidate.rel

    def set_thumbnail(self, value: QImage | str) -> None:
        """Show the thumbnail, or a placeholder text such as CANT_SHOW. GUI thread only."""
        if isinstance(value, QImage):
            self.thumb_label.setText("")
            self.thumb_label.setPixmap(QPixmap.fromImage(value))
        else:
            self.thumb_label.setText(value)


class ReviewPage(QWidget):
    """Shows the groups one by one; emits finished(keep), cancelled()
    (Cancel) or left() (Esc on the first group, then Leave).

    keep maps a group's index to the rels to keep, for each group the owner
    confirmed (or kept whole); skipped groups have no entry.
    """

    finished = Signal(object)  # dict[int, set[str]]
    cancelled = Signal()
    left = Signal()
    keys_changed = Signal()  # a key was switched on or off: the strip redraws

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root = Path()
        self._mode = ""
        self._groups: list[Group] = []
        self._keep: dict[int, set[str]] = {}
        self._index = 0
        self._generation = 0  # bumped whenever the cards are replaced
        self._thumb_tasks: dict[tuple[int, int], Task] = {}  # kept alive until they report
        self.cards: list[CandidateCard] = []
        self.preview: PhotoPreview | None = None  # while one is open
        self.navigator = GridNavigator([], lambda: len(self.cards), parent=self)

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
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the cards take it
        layout.addWidget(self.scroll, 1)

        self.back_button = QPushButton("Previous group")
        self.stop_button = QPushButton("Stop reviewing")
        self.cancel_button = QPushButton("Cancel")
        self.fullscreen_button = QPushButton("Full screen…")
        self.keep_all_button = QPushButton("Keep all")
        self.keep_one_button = QPushButton("Keep one (sharpest)")
        self.skip_button = QPushButton("Skip")
        self.confirm_button = QPushButton(literal("Confirm & next group"))
        self.confirm_button.setDefault(True)
        row = QHBoxLayout()
        for button in (self.back_button, self.stop_button, self.cancel_button):
            row.addWidget(button)
        row.addStretch()
        for button in (self.fullscreen_button, self.keep_all_button, self.keep_one_button,
                       self.skip_button, self.confirm_button):
            row.addWidget(button)
        layout.addLayout(row)

        self.back_button.clicked.connect(self._previous)
        self.stop_button.clicked.connect(self._finish)
        self.cancel_button.clicked.connect(self._cancel)
        self.fullscreen_button.clicked.connect(lambda: self._open_fullscreen())
        self.keep_all_button.clicked.connect(self._keep_all)
        self.keep_one_button.clicked.connect(self._keep_one)
        self.skip_button.clicked.connect(self._skip)
        self.confirm_button.clicked.connect(self._confirm)

        # Keys: the cards' own (while one has focus), then the page's.
        shortcuts.describe(self.scroll, "←→", "move")
        shortcuts.bind(self.scroll, "Space", "Space", "keep", self._toggle_current,
                       repeat=False)
        shortcuts.bind(self.scroll, "Return", "Enter", "view big", self._view_current,
                       repeat=False)
        shortcuts.bind(self, "K", "K", "keep all", self._keep_all, repeat=False)
        self._keep_one_key = shortcuts.bind(self, "1", "1", "keep sharpest",
                                            self._keep_one, repeat=False)
        shortcuts.bind(self, "N", "N", "skip", self._skip, repeat=False)
        self._confirm_key = shortcuts.bind(self, "Ctrl+Right", "Ctrl+→", "confirm",
                                           self._confirm, repeat=False)
        shortcuts.bind(self, "Escape", "Esc", "back", self._back, repeat=False)
        shortcuts.bind(self, "Ctrl+Return", "Ctrl+Enter", "stop", self._finish,
                       repeat=False)

    # ----- loading ---------------------------------------------------------

    def load(self, root: Path, mode: str, groups: list[Group]) -> None:
        """Start reviewing groups (at least one) found in root by mode."""
        self._root = Path(root)
        self._mode = mode
        self._groups = list(groups)
        self._keep = {}
        folder = resolve_existing_dir(self._root, MODE_SPECS[mode].set_aside_folder)
        self.subtitle_label.setText(
            f"Tick the ones to keep. The others go to {folder}/ (nothing is deleted)."
        )
        repeated = mode == "repeated"
        self.keep_all_button.setText("Keep all" if repeated else "Keep all (not duplicates)")
        self.keep_one_button.setVisible(repeated)
        self.fullscreen_button.setVisible(repeated)
        self._keep_one_key.setEnabled(repeated)
        self._show_group(0)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.navigator.focus()

    def resume(self) -> None:
        """Show again the group the review ended on, with the answers given
        so far, the focus on the file it was on."""
        self._show_group(self._index, self.navigator.current_index())

    def _show_group(self, index: int, focused: int = 0) -> None:
        self.stop_thumbnails()
        self._generation += 1
        self._index = index
        group = self._groups[index]
        self.heading_label.setText(
            f"Group {index + 1} of {len(self._groups)} · {KIND_LABEL[group.kind]}"
        )
        ticked = self._keep.get(index, group.suggested_keep)
        strip = QWidget()
        row = QHBoxLayout(strip)
        row.setContentsMargins(4, 4, 4, 4)  # room for the focus glow
        row.setSpacing(CARD_SPACING)
        self.cards = []
        for n, candidate in enumerate(group.members):
            card = CandidateCard(self._root, candidate, group)
            card.keep_check.setChecked(candidate.rel in ticked)
            card.keep_check.toggled.connect(self._update_buttons)
            row.addWidget(card, 0, Qt.AlignmentFlag.AlignTop)
            self.cards.append(card)
            if candidate.kind == "photo":
                self._request_thumbnail(n, card.path())
        row.addStretch()
        self.scroll.setWidget(strip)  # deletes the previous group's cards
        self.navigator.set_cards(self.cards, focused)
        self.back_button.setEnabled(index > 0)
        self._update_buttons()
        if self.isVisible():
            self.navigator.focus()

    def _request_thumbnail(self, n: int, path: Path) -> None:
        token = (self._generation, n)
        task = Task(render_thumbnail, token, path)
        task.signals.result.connect(self._thumbnail_ready)
        self._thumb_tasks[token] = task
        task.start()

    def _thumbnail_ready(self, result: tuple[int, int, QImage | str | None]) -> None:
        generation, n, value = result
        self._thumb_tasks.pop((generation, n), None)
        if generation != self._generation or value is None:
            return  # asked for cards that are gone
        self.cards[n].set_thumbnail(value)

    def stop_thumbnails(self) -> None:
        """Let the thumbnails still waiting for a pool thread finish without decoding."""
        for task in self._thumb_tasks.values():
            task.cancel_event.set()

    # ----- answers ---------------------------------------------------------

    def _ticked(self) -> set[str]:
        return {card.candidate.rel for card in self.cards if card.keep_check.isChecked()}

    def _update_buttons(self, *_) -> None:
        can_confirm = bool(self._ticked())
        self.confirm_button.setEnabled(can_confirm)
        if self._confirm_key.isEnabled() != can_confirm:
            self._confirm_key.setEnabled(can_confirm)
            self.keys_changed.emit()

    def _answer(self, keep: set[str] | None) -> None:
        """Record keep (None: skipped) for the current group and move on."""
        if keep is None:
            self._keep.pop(self._index, None)
        else:
            self._keep[self._index] = keep
        if self._index + 1 < len(self._groups):
            self._show_group(self._index + 1)
        else:
            self._finish()

    def _finish(self) -> None:
        self.stop_thumbnails()
        self.finished.emit(dict(self._keep))

    def _cancel(self) -> None:
        self.stop_thumbnails()
        self.cancelled.emit()

    def _back(self) -> None:
        """Esc: the previous group, or from the first, leave if the owner says so."""
        if self._index > 0:
            self._show_group(self._index - 1)
        elif shortcuts.confirm_leave(self):
            self.stop_thumbnails()
            self.left.emit()

    def _toggle_current(self) -> None:
        card = self.navigator.current()
        if card is not None:
            card.keep_check.toggle()

    def _view_current(self) -> None:
        """The focused file big: repeated shots full screen, a duplicate in the preview."""
        card = self.navigator.current()
        if card is None:
            return
        if self._mode == "repeated":
            self._open_fullscreen(self.navigator.current_index())
        else:
            self.preview = PhotoPreview(card.path(), self)
            self.preview.finished.connect(self._preview_closed)
            self.preview.open()

    def _preview_closed(self) -> None:
        self.preview = None  # it deletes itself

    def _confirm(self) -> None:
        ticked = self._ticked()
        if ticked:  # a group always keeps at least one file
            self._answer(ticked)

    def _keep_all(self) -> None:
        self._answer({c.rel for c in self._groups[self._index].members})

    def _keep_one(self) -> None:
        best = sharpest(self._groups[self._index].members).rel
        for card in self.cards:
            card.keep_check.setChecked(card.candidate.rel == best)
        self._confirm()

    def _skip(self) -> None:
        self._answer(None)

    def _open_fullscreen(self, start: int = 0) -> None:
        """Show the group full screen from file start, then tick the cards
        the owner kept there."""
        viewer = FullscreenViewer(self._root, self._groups[self._index], self._ticked(), self,
                                  start=start)
        viewer.showFullScreen()
        viewer.exec()
        keep = viewer.keep_result()
        viewer.dispose()
        for card in self.cards:
            card.keep_check.setChecked(card.candidate.rel in keep)

    def _previous(self) -> None:
        if self._index > 0:
            self._show_group(self._index - 1)
