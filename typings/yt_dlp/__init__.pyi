# Minimal stubs for the parts of yt-dlp yt2ym uses: yt-dlp ships no type hints.

from types import TracebackType
from typing import Any, NamedTuple, Self

class ParsedOptions(NamedTuple):
    parser: Any
    options: Any
    urls: list[str]
    ydl_opts: dict[str, Any]

def parse_options(argv: list[str] | None = None) -> ParsedOptions: ...

class YoutubeDL:
    def __init__(self, params: dict[str, Any] | None = None) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    def extract_info(self, url: str, download: bool = True) -> dict[str, Any] | None: ...
    def download(self, url_list: list[str]) -> int: ...
