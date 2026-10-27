"""The ways a sort runs, one module per kind of mode (see photosort.modes.MODE_SPECS):

- plain: the moves are planned straight away (Date, Type, Camera, Location);
- files: every file, not only media, planned straight away after a warning
  when subfolders are included, and for Date (all files) the date to sort
  by (Extension, Date (all files));
- groups: groups of duplicates or repeated shots, reviewed first;
- bad: bad photos, ticked first;
- events: events, named, merged, split or skipped first;
- face: the people to look for, then the unsure matches;
- clip: Content and Notes & Pages, judged with CLIP, then the unsure photos.

Each module has MODELS, the AI models it needs before it starts, and
start(window, root, include_subfolders, spec), which the MainWindow calls once
the folder and the mode are checked and the settings saved. The rest of the
module is the handlers for its Tasks and its pages, and its worker functions.

A flow works through the window: run_task, begin_progress, show_page,
show_plan, show_summary, show_error and save_settings. It keeps its working data in
window.flow_state (of its own type; no other flow reads it). When it reaches
the summary with something to go back to, it sets window.back_to, which Back
on the summary calls (else Back goes Home). Start, Go (once its run has
begun) and the error paths clear both.
"""


def check_failed(window, message: str) -> None:
    """The files could not be checked: home, and say so."""
    window.end_flow()
    window.show_page(window.home)
    window.show_error(f"Could not check the files: {message}")


def plan_failed(window, error: OSError) -> None:
    """The moves could not be planned (e.g. the drive was unplugged): home, and say so."""
    window.end_flow()
    window.show_page(window.home)
    window.show_error(f"Could not plan the moves: {error}")
