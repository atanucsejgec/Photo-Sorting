"""Content and Notes & Pages: make sure the CLIP models are there, then
describe every photo with CLIP and judge it; the owner picks a folder for
each unsure photo, and the moves are planned."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, cast

from photosort.ai.models import CLIP_MODELS
from photosort.cache import AnalysisCache
from photosort.clip import ClipEngine
from photosort.modes import ModeSpec, content, notes
from photosort.modes._clip_scan import ClipScan, embed_photos
from photosort.modes.content import Verdict
from photosort.ui.flows import plan_failed
from photosort.ui.worker import Task

if TYPE_CHECKING:
    from photosort.ui.main_window import MainWindow

MODELS = CLIP_MODELS


@dataclass
class _ClipSort:
    """A Content or Notes & Pages sort, judged and waiting for its unsure answers."""

    choices: list[str]  # the categories (or kinds of paper) to pick from
    scan: ClipScan
    sure: dict[str, str]  # rel -> category
    unsure: list[Verdict]


def _sort_by_clip(root: Path, include_subfolders: bool, mode: str, choices: list[str],
                  engine_factory: Callable[[bool], ClipEngine], use_gpu: bool,
                  progress=None, cancel=None) -> _ClipSort | None:
    """Describe every photo with a CLIP engine made here, on the worker
    thread, through root's analysis cache; then judge them for mode
    ("content", choices being the categories, or "notes")."""
    engine = engine_factory(use_gpu)
    with AnalysisCache(root) as cache:
        scan = embed_photos(root, include_subfolders, engine.image_embedding,
                            progress=progress, cancel=cancel, cache=cache)
    if scan is None or (cancel is not None and cancel.is_set()):
        return None  # cancelled: the text model is never opened
    if not scan.embeddings:  # nothing to judge: the text model is never opened
        return _ClipSort(choices, scan, {}, [])
    text = engine.text_embeddings
    if mode == "content":
        sure, unsure = content.judge(scan, choices, content.category_embeddings(text, choices),
                                     text(content.background_prompts()))
    else:
        sure, unsure = notes.judge(scan, text(notes.PAPER_PROMPTS), text(notes.OTHER_PROMPTS))
    return _ClipSort(choices, scan, sure, unsure)


def start(window: "MainWindow", root: Path, include_subfolders: bool, spec: ModeSpec) -> None:
    choices = (list(window.settings.content_categories) if window.mode == "content"
               else list(notes.KINDS))
    window.begin_progress("Looking at photos…", joiner="of")
    window.run_task(
        Task(_sort_by_clip, root, include_subfolders, window.mode, choices,
             window.clip_engine_factory, window.settings.use_gpu),
        partial(_on_sorted, window), partial(_on_sort_error, window),
    )


def _on_sorted(window: "MainWindow", sort: _ClipSort | None) -> None:
    if sort is None or window.cancel_pressed():
        window.show_page(window.home)
        return
    window.flow_state = sort
    if sort.unsure:
        _ask(window, {})
    else:
        plan_moves(window, {})


def _ask(window: "MainWindow", answers: dict[str, str | None]) -> None:
    """Show the unsure photos, picked as in answers; the answers are then planned."""
    sort = cast(_ClipSort, window.flow_state)
    title = None  # "N photos to check"
    if window.mode == "notes":
        n = len(sort.unsure)
        title = f"{n} photo{'' if n == 1 else 's'} might be notes or pages"
    window.category_review_page.load(window.root, sort.unsure, sort.choices, answers, title)
    window.show_page(window.category_review_page)


def plan_moves(window: "MainWindow", answers: dict[str, str | None]) -> None:
    """Plan the moves, with the owner's answers for the unsure photos."""
    planner = content.plan_content if window.mode == "content" else notes.plan_notes
    sort = cast(_ClipSort, window.flow_state)
    try:
        plan = planner(window.root, sort.scan, sort.sure, answers)
    except OSError as e:  # e.g. the drive was unplugged during the review
        plan_failed(window, e)
        return
    if sort.unsure:  # back to the unsure photos, picked as last answered
        window.back_to = partial(_ask, window, dict(answers))
    window.show_plan(plan)


def _on_sort_error(window: "MainWindow", message: str) -> None:
    window.end_flow()
    window.show_page(window.home)
    window.show_error(f"Could not look at the photos: {message}")
