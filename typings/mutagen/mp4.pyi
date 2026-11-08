from collections.abc import Sequence
from pathlib import Path
from typing import Literal, overload

class MP4Cover(bytes):
    FORMAT_JPEG: int
    FORMAT_PNG: int
    imageformat: int

class MP4:
    def __init__(self, filename: Path) -> None: ...
    @overload
    def get(self, key: Literal["covr"], default: list[MP4Cover]) -> list[MP4Cover]: ...
    @overload
    def get(self, key: Literal["\xa9nam"]) -> list[str] | None: ...
    @overload
    def __setitem__(self, key: Literal["\xa9nam"], value: list[str]) -> None: ...
    @overload
    def __setitem__(self, key: Literal["trkn"], value: Sequence[tuple[int, int]]) -> None: ...
    def save(self) -> None: ...
