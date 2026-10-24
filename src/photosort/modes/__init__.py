"""The sorting modes, as (key, label, available)."""

MODES: list[tuple[str, str, bool]] = [
    ("date", "Date", True),
    ("face", "Face", False),
    ("duplicates", "Duplicates", False),
    ("repeated", "Repeated shots", False),
    ("notes", "Notes & Pages", False),
    ("type", "Type / source", False),
    ("content", "Content", False),
    ("bad", "Bad photos", False),
    ("events", "Events", False),
    ("location", "Location", False),
    ("camera", "Camera", False),
]
