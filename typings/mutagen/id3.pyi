# Minimal stubs for the parts of mutagen yt2ym uses: mutagen ships py.typed
# but leaves attributes like `tags` unannotated, which strict pyright rejects.

from collections.abc import MutableMapping
from typing import ClassVar

class Encoding(int):
    LATIN1: ClassVar[Encoding]
    UTF16: ClassVar[Encoding]
    UTF16BE: ClassVar[Encoding]
    UTF8: ClassVar[Encoding]

class Frame: ...

class TextFrame(Frame):
    text: list[str]
    def __init__(self, *, encoding: Encoding, text: str) -> None: ...

class TIT2(TextFrame): ...
class TRCK(TextFrame): ...

class APIC(Frame):
    data: bytes
    mime: str

class ID3(MutableMapping[str, Frame]):
    def add(self, frame: Frame) -> None: ...
