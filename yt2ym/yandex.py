"""Talks to Yandex Music for one account: playlists, uploads and covers."""

import asyncio
import json
import logging
import urllib.parse
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import asyncclick as click
from pydantic import AliasChoices, BaseModel, Field, ValidationError
from yandex_music import ClientAsync, DeviceCode
from yandex_music import Playlist as YmPlaylist
from yandex_music.exceptions import DeviceAuthError

from yt2ym.types import (
    Cover,
    PlaylistKind,
    PlaylistRef,
    PlaylistTitle,
    Token,
    TrackIndex,
    TrackTitle,
    Uid,
    Visibility,
)

logger = logging.getLogger(__name__)


# --- Yandex API responses (only the fields we read) --------------------------


class Track(BaseModel):
    title: TrackTitle | None = None


class PlaylistItem(Track):
    """A playlist entry: either wraps a `track`, or carries `title` itself."""
    track: Track | None = None

    @property
    def effective_title(self) -> TrackTitle | None:
        return (self.track or self).title


class Playlist(BaseModel):
    revision: int
    track_count: int = Field(0, alias="trackCount")
    tracks: list[PlaylistItem] = []

    def repeats(self, titles: set[TrackTitle]) -> list[TrackIndex]:
        """Returns the positions of the tracks that repeat an earlier track of one of `titles`."""
        seen: set[TrackTitle] = set()
        out: list[TrackIndex] = []
        for i, item in enumerate(self.tracks):
            if (t := item.effective_title) is not None and t in titles:
                if t in seen:
                    out.append(TrackIndex(i))
                seen.add(t)
        return out


@dataclass(frozen=True, slots=True)
class TrackSpan:
    """The tracks of a playlist from position `start` up to, but not including, `stop`."""

    start: TrackIndex
    stop: TrackIndex

    @classmethod
    def at(cls, i: TrackIndex) -> TrackSpan:
        return cls(i, TrackIndex(i + 1))

    @classmethod
    def all_of(cls, playlist: Playlist) -> TrackSpan:
        return cls(TrackIndex(0), TrackIndex(playlist.track_count))


class PlaylistResponse(BaseModel):
    result: Playlist


class UploadTarget(BaseModel):
    post_target: str = Field(validation_alias=AliasChoices("post-target", "post_target"))


# --- Yandex Music ------------------------------------------------------------


async def _ok_text(r: aiohttp.ClientResponse, label: str) -> str:
    """Returns the response body. Errors with RuntimeError when the status is not 200."""
    body = await r.text()
    if r.status != 200:
        raise RuntimeError(f"{label} failed: HTTP {r.status} {body}")
    return body


async def _ok_json[M: BaseModel](r: aiohttp.ClientResponse, label: str, model: type[M]) -> M:
    """Returns the response body parsed into `model`.

    Errors as `_ok_text` does, and with ValidationError when the body does not fit `model`.
    """
    return model.model_validate_json(await _ok_text(r, label))


@dataclass(frozen=True, slots=True)
class _Api:
    """Raw Yandex Music API calls for one account, over one authorized session."""

    URL = "https://api.music.yandex.net"

    session: aiohttp.ClientSession
    uid: Uid

    def playlist_url(self, kind: PlaylistKind, suffix: str = "") -> str:
        return f"{self.URL}/users/{self.uid}/playlists/{kind}{suffix}"

    def upload_target_url(self, kind: PlaylistKind, file_name: str) -> str:
        encoded = urllib.parse.quote(file_name, safe="_!() ").replace(" ", "+")
        return (
            f"{self.URL}/loader/upload-url"
            f"?uid={self.uid}&playlist-id={self.uid}:{kind}"
            f"&visibility=private&path={encoded}"
        )

    # Raw API, because the yandex_music library currently crashes deserializing
    # user-uploaded tracks.
    async def fetch_playlist(self, kind: PlaylistKind) -> Playlist:
        async with self.session.get(self.playlist_url(kind)) as r:
            return (await _ok_json(r, "playlist fetch", PlaylistResponse)).result

    async def upload_cover(self, kind: PlaylistKind, cover: Cover) -> None:
        form = aiohttp.FormData()
        form.add_field("image", cover.data, filename="cover", content_type=cover.mime)
        async with self.session.post(self.playlist_url(kind, "/cover/upload"), data=form) as r:
            await _ok_text(r, "playlist cover upload")

    async def delete_tracks(
        self, kind: PlaylistKind, playlist: Playlist, spans: list[TrackSpan]
    ) -> None:
        """Removes the tracks in `spans` from playlist `kind`.

        The given `playlist` is the playlist's current state, and its revision goes into
        the request.
        """
        # The /change endpoint applies a diff to the playlist. Deleting from the end keeps
        # the earlier positions valid, however the ops are applied.
        ops = [
            {"op": "delete", "from": s.start, "to": s.stop}
            for s in sorted(spans, key=lambda s: s.start, reverse=True)
        ]
        form = aiohttp.FormData()
        form.add_field("kind", str(kind))
        form.add_field("revision", str(playlist.revision))
        form.add_field("diff", json.dumps(ops))
        async with self.session.post(self.playlist_url(kind, "/change"), data=form) as r:
            await _ok_text(r, "playlist change")

    async def upload_one(self, file_path: Path, playlist_kind: PlaylistKind) -> None:
        file_name = file_path.name

        async with self.session.post(self.upload_target_url(playlist_kind, file_name)) as resp:
            try:
                post_target = (await _ok_json(resp, "upload-url", UploadTarget)).post_target
            except ValidationError as e:
                raise RuntimeError(f"no upload URL in response: {e}") from e

        form = aiohttp.FormData()
        with open(file_path, "rb") as f:
            form.add_field("file", f, filename=file_name)
            async with self.session.post(
                post_target, data=form, timeout=aiohttp.ClientTimeout(total=600)
            ) as up:
                body = await up.text()
                if up.status not in (200, 201):
                    raise RuntimeError(f"upload HTTP {up.status}: {body}")
                if "OK" not in body.upper() and "CREATED" not in body.upper():
                    raise RuntimeError(f"upload returned {up.status} but unexpected body: {body!r}")


@dataclass(frozen=True, slots=True)
class _Account:
    client: ClientAsync
    uid: Uid


class YandexMusic:
    """The only way the tool talks to Yandex Music, as the account that owns the token."""

    TOKEN_FILE = Path.home() / ".config" / "yt2ym" / "token"

    def __init__(self, token: Token) -> None:
        self.token = token
        self._account_cache: _Account | None = None

    @classmethod
    def from_args(cls, cli_token: str | None) -> YandexMusic:
        if cli_token:
            return cls(Token(cli_token.strip()))
        if cls.TOKEN_FILE.exists():
            return cls(Token(cls.TOKEN_FILE.read_text().strip()))
        raise click.ClickException(
            "No Yandex Music token. Run `login`, pass --token, set $YANDEX_MUSIC_TOKEN, "
            f"or write it to {cls.TOKEN_FILE}."
        )

    @classmethod
    async def login(cls) -> YandexMusic:
        """Logs in through the browser, and returns a YandexMusic with the new token.

        It saves the token to TOKEN_FILE. Errors with click.ClickException when the login fails.
        """

        def show(code: DeviceCode) -> None:
            print(f"\n  Open: {code.verification_url}")
            print(f"  Enter code: {code.user_code}\n  Waiting for approval...\n")

        try:
            # The OAuth device flow, with yandex-music's built-in Smart TV client.
            token = Token((await ClientAsync().device_auth(on_code=show)).access_token)
        except DeviceAuthError as e:
            raise click.ClickException(f"oauth failed: {e}") from e
        cls.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        cls.TOKEN_FILE.write_text(token)
        cls.TOKEN_FILE.chmod(0o600)
        print(f"✓ saved token to {cls.TOKEN_FILE}")
        return cls(token)

    async def _account(self) -> _Account:
        """Returns the client logged in with the token, and its account's uid.

        Errors with click.ClickException when the token is invalid.
        """
        if self._account_cache is None:
            client = await ClientAsync(self.token).init()
            me = client.me
            if me is None or me.account is None or me.account.uid is None:
                raise click.ClickException("token invalid: failed to fetch account info")
            self._account_cache = _Account(client, Uid(me.account.uid))
        return self._account_cache

    @asynccontextmanager
    async def _api(self) -> AsyncGenerator[_Api]:
        uid = (await self._account()).uid
        headers = {"Authorization": f"OAuth {self.token}"}
        async with aiohttp.ClientSession(headers=headers) as s:
            yield _Api(s, uid)

    async def list_playlists(self) -> list[YmPlaylist]:
        return await (await self._account()).client.users_playlists_list()

    async def create_playlist(self, title: PlaylistTitle, visibility: Visibility) -> YmPlaylist:
        c = (await self._account()).client
        pl = await c.users_playlists_create(title=title, visibility=visibility)
        if pl is None or pl.kind is None:
            raise click.ClickException("failed to create playlist")
        return pl

    async def _find_playlist(self, title: PlaylistTitle) -> PlaylistKind | None:
        """Returns the kind of the playlist titled exactly `title`, or None when there is none."""
        for p in await self.list_playlists():
            if p.title == title and p.kind is not None:
                return PlaylistKind(p.kind)
        return None

    async def resolve_playlist(self, playlist: PlaylistRef) -> PlaylistKind:
        """Returns the kind of `playlist`. A name selects the playlist with that exact title.

        Errors with click.ClickException when no playlist has that title.
        """
        if isinstance(playlist, PlaylistKind):
            return playlist
        if (kind := await self._find_playlist(playlist)) is None:
            raise click.ClickException(f"no playlist titled {playlist!r}")
        return kind

    async def resolve_or_create_playlist(self, playlist: PlaylistRef) -> PlaylistKind:
        """Returns the kind of `playlist`, as resolve_playlist does.

        Creates a private playlist when no playlist has that title.
        """
        if isinstance(playlist, PlaylistKind):
            return playlist
        if (kind := await self._find_playlist(playlist)) is not None:
            return kind
        pl = await self.create_playlist(playlist, "private")
        logger.info("created playlist %r (kind=%s)", pl.title, pl.kind)
        assert pl.kind is not None
        return PlaylistKind(pl.kind)

    async def set_playlist_cover(self, kind: PlaylistKind, cover: Cover) -> None:
        async with self._api() as api:
            await api.upload_cover(kind, cover)

    async def fetch_playlist(self, kind: PlaylistKind) -> Playlist:
        """Returns playlist `kind` with its tracks."""
        async with self._api() as api:
            return await api.fetch_playlist(kind)

    async def clear_playlist(self, kind: PlaylistKind) -> list[PlaylistItem]:
        """Removes every track from playlist `kind`, and returns the tracks it removed."""
        async with self._api() as api:
            pl = await api.fetch_playlist(kind)
            if pl.track_count:
                await api.delete_tracks(kind, pl, [TrackSpan.all_of(pl)])
        return pl.tracks

    async def remove_repeats(
        self, kind: PlaylistKind, titles: set[TrackTitle]
    ) -> list[PlaylistItem]:
        """Removes from playlist `kind` every track that repeats an earlier one of `titles`.

        Returns the tracks it removed.
        """
        async with self._api() as api:
            pl = await api.fetch_playlist(kind)
            if repeats := pl.repeats(titles):
                await api.delete_tracks(kind, pl, [TrackSpan.at(i) for i in repeats])
        return [pl.tracks[i] for i in repeats]

    async def playlist_track_titles(self, kind: PlaylistKind) -> set[TrackTitle]:
        """Returns the titles of the tracks in playlist `kind`."""
        pl = await self.fetch_playlist(kind)
        return {t for item in pl.tracks if (t := item.effective_title)}

    async def upload_many(
        self, files: list[Path], playlist_kind: PlaylistKind, *, attempts: int = 4
    ) -> None:
        # Yandex displays user-uploaded tracks newest-first, so upload in reverse
        # order to get caller-supplied order on display.
        # Storage backend intermittently returns HTTP 200 with an empty body —
        # retry transparently before giving up.
        async with self._api() as api:
            for f in reversed(files):
                for attempt in range(1, attempts + 1):
                    logger.info("→ uploading %s (playlist kind=%s)%s",
                                f.name, playlist_kind,
                                "" if attempt == 1 else f" [retry {attempt}/{attempts}]")
                    try:
                        await api.upload_one(f, playlist_kind)
                        break
                    except RuntimeError as e:
                        if attempt == attempts:
                            raise
                        delay = 2 ** (attempt - 1)
                        logger.warning("upload failed (%s); retrying in %ds", e, delay)
                        await asyncio.sleep(delay)
        logger.info("done: %d file(s) uploaded", len(files))
