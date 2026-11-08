"""The domain types that the downloader, Yandex Music and the CLI share."""

from dataclasses import dataclass
from typing import Literal, NewType

Token = NewType("Token", str)
"""A Yandex Music OAuth token."""

Uid = NewType("Uid", int)
"""The id of a Yandex Music account."""

MediaUrl = NewType("MediaUrl", str)
"""The page that yt-dlp downloads from. It can also be any other input that yt-dlp accepts."""

TrackTitle = NewType("TrackTitle", str)
"""The title of a track, as Yandex Music shows it in a playlist."""

TrackNumber = NewType("TrackNumber", int)
"""The number of a track within its album, counted from 1."""

TrackIndex = NewType("TrackIndex", int)
"""The position of a track in a playlist, counted from 0 at the top."""

VideoId = NewType("VideoId", str)
"""The id that the media's site gives the video, such as a YouTube video id."""


# Classes rather than NewType, because a PlaylistRef is told apart at run time.
class PlaylistKind(int):
    """The id of a playlist within its owner's account."""


class PlaylistTitle(str):
    """The title of a playlist. Commands select a playlist by its exact title."""


type PlaylistRef = PlaylistKind | PlaylistTitle
"""A playlist given by its kind or by its title."""

LIKED_KIND = PlaylistKind(3)
"""The kind of every account's Liked playlist (Мне нравится)."""


type Visibility = Literal["public", "private"]


@dataclass(frozen=True, slots=True)
class Cover:
    data: bytes

    @property
    def mime(self) -> Literal["image/png", "image/jpeg"]:
        return "image/png" if self.data.startswith(b"\x89PNG") else "image/jpeg"
