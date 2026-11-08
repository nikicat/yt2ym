# pyright: reportPrivateUsage=false
import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import aiohttp
import asyncclick as click
import pytest
from yandex_music import Playlist as YmPlaylist

from yt2ym.cli import Cli
from yt2ym.types import PlaylistKind, PlaylistTitle, Token, TrackTitle, Uid, Visibility
from yt2ym.yandex import Playlist, TrackSpan, YandexMusic, _Api

KIND = PlaylistKind(7)
TITLE = PlaylistTitle("Mix")
NEW_KIND = PlaylistKind(8)


class Remote:
    """A fake Yandex Music account with one playlist.

    It keeps the playlist's titles from the top down.
    """

    def __init__(self, titles: list[str] | None = None, *, failures: int = 0, lands: bool = False):
        self.titles = [TrackTitle(t) for t in titles or []]
        # The next `failures` uploads fail. With `lands`, a failed upload is stored anyway.
        # Yandex can answer HTTP 504 for an upload that it has already stored.
        self.failures = failures
        self.lands = lands
        self.attempts: list[str] = []
        self.created: list[tuple[PlaylistTitle, Visibility]] = []

    def upload(self, name: str) -> None:
        self.attempts.append(name)
        stored = not self.failures or self.lands
        if stored:
            # Yandex shows uploaded tracks newest first.
            self.titles.insert(0, TrackTitle(name))
        if self.failures:
            self.failures -= 1
            raise RuntimeError("upload HTTP 504: upstream request timeout")


class FakeApi(_Api):
    def __init__(self, remote: Remote) -> None:
        super().__init__(cast(aiohttp.ClientSession, None), Uid(1))
        self.remote = remote

    async def fetch_playlist(self, kind: PlaylistKind) -> Playlist:
        tracks = [{"title": t} for t in self.remote.titles]
        return Playlist.model_validate(
            {"revision": 1, "trackCount": len(tracks), "tracks": tracks}
        )

    async def delete_tracks(
        self, kind: PlaylistKind, playlist: Playlist, spans: list[TrackSpan]
    ) -> None:
        self.remote.titles = [
            t for i, t in enumerate(self.remote.titles)
            if not any(s.start <= i < s.stop for s in spans)
        ]

    async def upload_one(self, file_path: Path, playlist_kind: PlaylistKind) -> None:
        self.remote.upload(file_path.name)


class FakeYandexMusic(YandexMusic):
    def __init__(self, remote: Remote) -> None:
        super().__init__(Token("token"))
        self.remote = remote

    @asynccontextmanager
    async def _api(self) -> AsyncGenerator[_Api]:
        yield FakeApi(self.remote)

    async def list_playlists(self) -> list[YmPlaylist]:
        return [YmPlaylist(cover=None, kind=KIND, title=TITLE)]

    async def create_playlist(self, title: PlaylistTitle, visibility: Visibility) -> YmPlaylist:
        self.remote.created.append((title, visibility))
        return YmPlaylist(cover=None, kind=NEW_KIND, title=title)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replaces asyncio.sleep, and returns the delays that the retries asked for."""
    delays: list[float] = []

    async def sleep(delay: float, *_: Any) -> None:
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    return delays


def audio_files(tmp_path: Path, *names: str) -> list[Path]:
    # Files without tags, so that file_title gives each one its file name.
    files = [tmp_path / n for n in names]
    for f in files:
        f.write_bytes(b"not audio")
    return files


def upload(remote: Remote, files: list[Path], *, replace: bool = False) -> None:
    c = Cli(None)
    c.ym = FakeYandexMusic(remote)
    asyncio.run(c._upload_files(files, KIND, replace=replace))


def test_upload_skips_titles_in_playlist(tmp_path: Path):
    remote = Remote(["b.mp3"])
    upload(remote, audio_files(tmp_path, "a.mp3", "b.mp3", "c.mp3"))
    assert remote.attempts == ["c.mp3", "a.mp3"]
    assert remote.titles == ["a.mp3", "c.mp3", "b.mp3"]


def test_upload_replace_empties_playlist_and_uploads_in_order(tmp_path: Path):
    remote = Remote(["old", "b.mp3"])
    upload(remote, audio_files(tmp_path, "a.mp3", "b.mp3", "c.mp3"), replace=True)
    assert remote.titles == ["a.mp3", "b.mp3", "c.mp3"]


def test_upload_retries_failed_uploads(tmp_path: Path, sleeps: list[float]):
    remote = Remote(failures=2)
    upload(remote, audio_files(tmp_path, "a.mp3"))
    assert remote.attempts == ["a.mp3"] * 3
    assert remote.titles == ["a.mp3"]
    assert sleeps == [1, 2]


def test_upload_gives_up_after_attempts(tmp_path: Path, sleeps: list[float]):
    remote = Remote(failures=YandexMusic.UPLOAD_ATTEMPTS)
    with pytest.raises(RuntimeError):
        upload(remote, audio_files(tmp_path, "a.mp3"))
    assert len(remote.attempts) == YandexMusic.UPLOAD_ATTEMPTS


def test_upload_removes_repeats_of_retried_uploads_only(tmp_path: Path, sleeps: list[float]):
    # The first upload of b.mp3 fails after Yandex stored it, so the retry stores it again.
    remote = Remote(["z", "z"], failures=1, lands=True)
    upload(remote, audio_files(tmp_path, "a.mp3", "b.mp3"))
    assert remote.titles == ["a.mp3", "b.mp3", "z", "z"]


def test_resolve_playlist_errors_on_unknown_title():
    ym = FakeYandexMusic(Remote())
    assert asyncio.run(ym.resolve_playlist(TITLE)) == KIND
    with pytest.raises(click.ClickException):
        asyncio.run(ym.resolve_playlist(PlaylistTitle("Nope")))


def test_resolve_or_create_playlist_creates_a_private_one():
    remote = Remote()
    ym = FakeYandexMusic(remote)
    assert asyncio.run(ym.resolve_or_create_playlist(TITLE)) == KIND
    assert asyncio.run(ym.resolve_or_create_playlist(PlaylistTitle("New"))) == NEW_KIND
    assert remote.created == [("New", "private")]
