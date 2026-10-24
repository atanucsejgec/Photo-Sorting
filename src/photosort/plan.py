"""A plan: the moves a sort would make, shown to the owner before Go."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Move:
    """Move one file. Both paths are POSIX strings relative to the root."""

    src: str
    dst: str


@dataclass
class Plan:
    mode: str
    moves: list[Move]
    summary: list[str]
    unchanged: int = 0
