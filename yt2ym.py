"""
yt2ym — download audio (YouTube, X, any yt-dlp site) and upload to Yandex Music.

Run via:  uv run yt2ym.py <subcommand> ...

Run `yt2ym --help` for where the token comes from.

Upload logic adapted from https://github.com/Kay0k1/yandex-music-uploader.
"""
import asyncio
import json
import logging
import os
import re
import urllib.parse
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, ClassVar, Literal, NewType

import aiohttp
import asyncclick as click
from mutagen.id3 import APIC, TIT2, TRCK, Encoding
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from pydantic import AliasChoices, BaseModel, Field, ValidationError
from yandex_music import ClientAsync, DeviceCode
from yandex_music import Playlist as YmPlaylist
from yandex_music.exceptions import DeviceAuthError
from yt_dlp import YoutubeDL, parse_options
from yt_dlp.utils import DownloadError

logger = logging.getLogger("yt2ym")

AUDIO_EXTS = {".mp3", ".m4a"}


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


@dataclass(frozen=True, slots=True)
class Chapter:
    """A chapter of a split video, as its file name spells it: "NN - title.ext"."""

    number: TrackNumber
    title: TrackTitle

    # Chapter files get a directory of their own, because a video title can look like one too.
    DIR: ClassVar[str] = "chapters"
    OUTTMPL: ClassVar[str] = f"{DIR}/%(section_number)02d - %(section_title)s.%(ext)s"
    """The yt-dlp output template for chapter files. `parse` reads the names it writes."""

    _STEM: ClassVar[re.Pattern[str]] = re.compile(r"(?P<number>\d+)\s*-\s*(?P<title>.+)")

    @classmethod
    def files_in(cls, dest: Path) -> list[Path]:
        """Returns the chapter files that yt-dlp wrote for the download in `dest`."""
        return collect_audio(dest / cls.DIR) if (dest / cls.DIR).is_dir() else []

    @classmethod
    def parse(cls, path: Path) -> Chapter | None:
        """Returns the chapter that `path` holds, or None when it is not a chapter file."""
        if not is_audio(path) or not (m := cls._STEM.fullmatch(path.stem)):
            return None
        return cls(TrackNumber(int(m["number"])), TrackTitle(m["title"]))


class VideoMeta(BaseModel, frozen=True):
    id: VideoId
    title: str


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


# --- helpers (audio / paths / yt-dlp) ----------------------------------------


def probe_url(url: MediaUrl) -> VideoMeta:
    """Returns the id and title of the video at `url`, without downloading it.

    Errors with click.ClickException when yt-dlp fails or returns no id and title.
    """
    with YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        try:
            return VideoMeta.model_validate(ydl.extract_info(url, download=False))
        except (DownloadError, ValidationError) as e:
            raise click.ClickException(f"yt-dlp failed to probe {url}: {e}") from e


def download_audio(url: MediaUrl, dest: Path, split_chapters: bool, *, overwrite: bool) -> None:
    """Downloads the audio of `url` into `dest`.

    With `split_chapters`, it also writes the files that Chapter.files_in lists. With
    `overwrite`, it downloads again files that `dest` already holds.

    Errors with click.ClickException when yt-dlp fails.
    """
    # CLI flags rather than a params dict: parse_options builds the postprocessor chain
    # that -x, --embed-* and --split-chapters need.
    args = [
        # Prefer m4a/aac: opus can't carry the embedded PNG cover through
        # ffmpeg's stream-copy chapter split (Unsupported codec id in stream 1).
        "-f", "bestaudio[ext=m4a]/bestaudio",
        "-x",  # extract audio in native format (no transcoding)
        "--embed-metadata",
        "--no-embed-chapters",  # avoid chapter atoms — Yandex picks them as title
        "--embed-thumbnail",
        "-o", str(dest / "%(title)s.%(ext)s"),
    ]
    if overwrite:
        args.append("--force-overwrites")
    if split_chapters:
        args += [
            "--split-chapters",
            "-o", "chapter:" + str(dest / Chapter.OUTTMPL),
        ]
    dest.mkdir(parents=True, exist_ok=True)
    logger.info("yt-dlp %s %s", " ".join(args), url)
    # Passing argv makes yt-dlp ignore its config files.
    with YoutubeDL(parse_options(args).ydl_opts) as ydl:
        if ydl.download([url]):
            raise click.ClickException(f"yt-dlp failed to download {url}")


def default_cache_dir(video_id: VideoId) -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "yt2ym"
    return root / video_id


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTS


def collect_audio(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(filter(is_audio, path.iterdir()))


def existing_audio(dest: Path, split_chapters: bool) -> list[Path]:
    if not dest.exists():
        return []
    chapters = Chapter.files_in(dest)
    if split_chapters and chapters:
        return chapters
    return collect_audio(dest) or chapters


def extract_cover(path: Path) -> Cover | None:
    """Return the embedded cover image, or None."""
    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            tags = MP3(path).tags
            if not tags:
                return None
            for frame in tags.values():
                if isinstance(frame, APIC):
                    return Cover(frame.data)
        elif suffix == ".m4a":
            covers = MP4(path).get("covr", [])
            if covers:
                return Cover(bytes(covers[0]))
    except Exception:
        logger.debug("failed to extract cover from %s", path, exc_info=True)
    return None


def file_title(path: Path) -> TrackTitle:
    """Returns the title that Yandex Music shows for `path` once it is uploaded.

    That is the file's title tag, or the file name with its extension when there is no tag.
    """
    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            tags = MP3(path).tags
            tit2 = tags.get("TIT2") if tags else None
            if isinstance(tit2, TIT2) and tit2.text:
                return TrackTitle(tit2.text[0])
        elif suffix == ".m4a":
            nam = MP4(path).get("\xa9nam")
            if nam:
                return TrackTitle(nam[0])
    except Exception:
        logger.debug("failed to read title tag from %s", path, exc_info=True)
    return TrackTitle(path.name)


def write_title_track(path: Path, title: TrackTitle, track: TrackNumber) -> None:
    """Write title + track-number tags appropriate to the file format."""
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        audio = MP3(path)
        if audio.tags is None:
            audio.add_tags()
        assert audio.tags is not None
        audio.tags.add(TIT2(encoding=Encoding.UTF8, text=title))
        audio.tags.add(TRCK(encoding=Encoding.UTF8, text=str(track)))
        audio.save()
    elif suffix == ".m4a":
        audio = MP4(path)
        audio["\xa9nam"] = [title]
        audio["trkn"] = [(track, 0)]
        audio.save()


def download_to(
    url: MediaUrl, dest: Path, split_chapters: bool, *, redownload: bool
) -> list[Path]:
    """Returns the audio files of `url` in `dest`, and downloads them when `dest` holds none.

    With `split_chapters`, the files are the chapters, each tagged with its title and number.
    With `redownload`, it downloads again even when `dest` holds the files. Errors with
    click.ClickException when yt-dlp fails or produces no audio.
    """
    if not redownload:
        already = existing_audio(dest, split_chapters)
        if already:
            logger.info("already downloaded → %s (%d file(s); use --redownload to redo)",
                        dest, len(already))
            return already
    download_audio(url, dest, split_chapters, overwrite=redownload)

    # yt-dlp's --embed-metadata writes no per-chapter title or track number,
    # so each chapter file gets them from its file name.
    for f in Chapter.files_in(dest):
        if ch := Chapter.parse(f):
            write_title_track(f, ch.title, ch.number)

    files = existing_audio(dest, split_chapters)
    if not files:
        raise click.ClickException(f"no audio produced in {dest}")
    return files


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


# --- CLI orchestration -------------------------------------------------------


class Cli:
    """Runs the subcommands on the downloader and on YandexMusic.

    It reads the token only when a subcommand first needs Yandex Music.
    """

    def __init__(self, token_arg: str | None) -> None:
        self._token_arg = token_arg

    @cached_property
    def ym(self) -> YandexMusic:
        return YandexMusic.from_args(self._token_arg)

    # --- subcommands ---

    async def playlists(self) -> None:
        print(f"  {LIKED_KIND}\tМне нравится (Liked)")
        for p in await self.ym.list_playlists():
            print(f"  {p.kind}\t{p.title}")

    async def create_playlist(self, title: PlaylistTitle, visibility: Visibility) -> None:
        pl = await self.ym.create_playlist(title, visibility)
        print(f"created: kind={pl.kind} title={pl.title!r} visibility={visibility}")

    async def _upload_files(
        self, files: list[Path], kind: PlaylistKind, *, replace: bool
    ) -> None:
        """Uploads to playlist `kind` each of `files` whose title the playlist does not hold.

        With `replace`, it removes every track from the playlist and uploads all of `files`.
        """
        if replace:
            cleared = await self.ym.clear_playlist(kind)
            if cleared:
                logger.info("cleared %d track(s) from playlist kind=%s", len(cleared), kind)
            to_upload = files
        else:
            existing = await self.ym.playlist_track_titles(kind)
            to_upload = [f for f in files if file_title(f) not in existing]
            skipped = len(files) - len(to_upload)
            if skipped:
                logger.info("skipping %d file(s) already in playlist kind=%s "
                            "(use --replace to upload them again)", skipped, kind)
        if not to_upload:
            logger.info("nothing to upload to kind=%s", kind)
            return
        await self.ym.upload_many(to_upload, kind)
        # A retried upload can land twice, when the failed attempt had reached Yandex.
        if removed := await self.ym.remove_repeats(kind, {file_title(f) for f in to_upload}):
            logger.info("removed %d repeated track(s) from playlist kind=%s", len(removed), kind)

    async def _put_cover(self, kind: PlaylistKind, cover: Cover, source: Path) -> None:
        await self.ym.set_playlist_cover(kind, cover)
        logger.info("set playlist kind=%s cover (%d bytes, %s) from %s",
                    kind, len(cover.data), cover.mime, source.name)

    async def _set_cover_from(self, kind: PlaylistKind, source: Path) -> None:
        """Sets the cover of playlist `kind` from the cover embedded in `source`, if it has one."""
        if cover := extract_cover(source):
            await self._put_cover(kind, cover, source)
        else:
            logger.info("no embedded cover in %s; skipping playlist cover", source.name)

    async def set_cover(self, playlist: PlaylistRef, source: Path) -> None:
        """Sets the cover of `playlist` from an image, or from an audio file's embedded cover.

        Errors with click.ClickException when an audio file has no embedded cover.
        """
        kind = await self.ym.resolve_playlist(playlist)
        cover = extract_cover(source) if is_audio(source) else Cover(source.read_bytes())
        if cover is None:
            raise click.ClickException(f"no embedded cover in {source}")
        await self._put_cover(kind, cover, source)

    async def clear_playlist(self, playlist: PlaylistRef, yes: bool) -> None:
        kind = await self.ym.resolve_playlist(playlist)
        if not yes:
            pl = await self.ym.fetch_playlist(kind)
            if not click.confirm(
                f"Remove {pl.track_count} track(s) from playlist kind={kind}?",
                default=False,
            ):
                logger.info("aborted")
                return
        cleared = await self.ym.clear_playlist(kind)
        logger.info("cleared %d track(s) from playlist kind=%s", len(cleared), kind)

    def download(
        self, url: MediaUrl, out: Path | None, split_chapters: bool, *, redownload: bool
    ) -> None:
        dest = out or default_cache_dir(probe_url(url).id)
        download_to(url, dest, split_chapters, redownload=redownload)

    async def upload(self, path: Path, playlist: PlaylistRef, *, replace: bool) -> None:
        files = collect_audio(path)
        if not files:
            raise click.ClickException(f"no audio files at {path}")
        kind = await self.ym.resolve_or_create_playlist(playlist)
        await self._upload_files(files, kind, replace=replace)

    async def sync(
        self,
        url: MediaUrl,
        playlist: PlaylistRef | None,
        out: Path | None,
        *,
        split_chapters: bool,
        redownload: bool,
        replace: bool,
    ) -> None:
        """Uploads the audio of `url` to `playlist`, and sets the playlist's cover from it.

        A title that no playlist has gets a new private playlist. With `split_chapters`, each
        chapter is one track, and `playlist` defaults to the video title. Errors with
        click.ClickException when `playlist` is None without `split_chapters`.
        """
        probe = probe_url(url)
        dest = out or default_cache_dir(probe.id)
        if playlist is None:
            if not split_chapters:
                raise click.ClickException("--playlist is required for `sync`")
            playlist = PlaylistTitle(probe.title)
            logger.info("using video title as playlist name: %r", playlist)
        kind = await self.ym.resolve_or_create_playlist(playlist)
        files = download_to(url, dest, split_chapters, redownload=redownload)
        await self._upload_files(files, kind, replace=replace)
        await self._set_cover_from(kind, files[0])


def parse_playlist(
    _ctx: click.Context, _param: click.Parameter, value: str | None
) -> PlaylistRef | None:
    """Returns the playlist that a command-line argument names.

    An argument of decimal digits is a kind. Any other argument is a title.
    """
    if value is None:
        return None
    return PlaylistKind(value) if value.isdecimal() else PlaylistTitle(value)


PLAYLIST_HELP = (
    f'playlist by kind id (e.g. "{LIKED_KIND}" = Liked) or by name (creates if missing).'
)
TOKEN_HELP = (
    "Yandex Music OAuth token (else $YANDEX_MUSIC_TOKEN, "
    f"else ~/{YandexMusic.TOKEN_FILE.relative_to(Path.home())})"
)
OUT_HELP = "output directory (default: $XDG_CACHE_HOME/yt2ym/<videoid>/, or ~/.cache/yt2ym/…)"


def out_option[F: Callable[..., Any]](f: F) -> F:
    return click.option(
        "--out", type=click.Path(path_type=Path, file_okay=False),
        default=None, help=OUT_HELP,
    )(f)


def redownload_option[F: Callable[..., Any]](f: F) -> F:
    return click.option(
        "--redownload", is_flag=True, help="download again even when the cache has the audio"
    )(f)


def replace_option[F: Callable[..., Any]](f: F) -> F:
    return click.option(
        "--replace", is_flag=True,
        help="remove every track from the playlist, then upload all files",
    )(f)


def playlist_option[F: Callable[..., Any]](
    *, required: bool = True, help_suffix: str = ""
) -> Callable[[F], F]:
    return click.option(
        "--playlist", required=required, default=None, callback=parse_playlist,
        help=PLAYLIST_HELP + help_suffix,
    )


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--token", envvar="YANDEX_MUSIC_TOKEN", default=None, help=TOKEN_HELP)
@click.pass_context
async def cli(ctx: click.Context, token: str | None) -> None:
    """yt2ym — download YouTube/X/any yt-dlp-supported audio and upload to Yandex Music."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ctx.obj = Cli(token)


@cli.command(help="OAuth device flow → save token")
async def login() -> None:
    await YandexMusic.login()


@cli.command(help="list your Yandex Music playlists")
@click.pass_obj
async def playlists(c: Cli) -> None:
    await c.playlists()


@cli.command("create-playlist", help="create a new playlist")
@click.argument("title", type=PlaylistTitle)
@click.option(
    "--public", "visibility", flag_value="public", default="private",
    help="make playlist public (default: private)",
)
@click.pass_obj
async def create_playlist(c: Cli, title: PlaylistTitle, visibility: Visibility) -> None:
    await c.create_playlist(title, visibility)


@cli.command("clear-playlist", help="remove all tracks from a playlist (by kind id or name)")
@click.argument("playlist", callback=parse_playlist)
@click.option("--yes", is_flag=True, help="skip confirmation prompt")
@click.pass_obj
async def clear_playlist(c: Cli, playlist: PlaylistRef, yes: bool) -> None:
    await c.clear_playlist(playlist, yes)


@cli.command(
    "set-cover",
    help="set a playlist's cover from an audio file (embedded cover) or image file",
)
@click.argument("playlist", callback=parse_playlist)
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_obj
async def set_cover(c: Cli, playlist: PlaylistRef, source: Path) -> None:
    await c.set_cover(playlist, source)


@cli.command(help="download audio (splits by chapters if present)")
@click.argument("url", type=MediaUrl)
@click.option("--single", is_flag=True, help="don't split chapters (download as one file)")
@out_option
@redownload_option
@click.pass_obj
async def download(
    c: Cli, url: MediaUrl, single: bool, out: Path | None, redownload: bool
) -> None:
    c.download(url, out, split_chapters=not single, redownload=redownload)


@cli.command(help="upload an audio file (mp3/m4a) or directory to Yandex Music")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@playlist_option()
@replace_option
@click.pass_obj
async def upload(c: Cli, path: Path, playlist: PlaylistRef, replace: bool) -> None:
    await c.upload(path, playlist, replace=replace)


@cli.command(help="download a video as a single track and upload it")
@click.argument("url", type=MediaUrl)
@playlist_option()
@out_option
@redownload_option
@replace_option
@click.pass_obj
async def sync(
    c: Cli, url: MediaUrl, playlist: PlaylistRef, out: Path | None, redownload: bool,
    replace: bool,
) -> None:
    await c.sync(
        url, playlist, out, split_chapters=False, redownload=redownload, replace=replace
    )


@cli.command(
    "sync-album",
    help="download a chaptered video and upload each chapter as a track. "
         "Default playlist = video title (created if missing).",
)
@click.argument("url", type=MediaUrl)
@playlist_option(required=False, help_suffix=" Defaults to the video title.")
@out_option
@redownload_option
@replace_option
@click.pass_obj
async def sync_album(
    c: Cli, url: MediaUrl, playlist: PlaylistRef | None, out: Path | None, redownload: bool,
    replace: bool,
) -> None:
    await c.sync(
        url, playlist, out, split_chapters=True, redownload=redownload, replace=replace
    )


if __name__ == "__main__":
    cli()
