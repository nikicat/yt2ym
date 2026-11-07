"""
yt2ym — download audio (YouTube, X, any yt-dlp site) and upload to Yandex Music.

Run via:  uv run yt2ym.py <subcommand> ...

Token: run `login`, or pass --token, or set YANDEX_MUSIC_TOKEN, or place it in
~/.config/yt2ym/token.

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
from pathlib import Path
from typing import Any

import aiohttp
import asyncclick as click
from mutagen.id3 import APIC, TIT2, TRCK, Encoding
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from pydantic import AliasChoices, BaseModel, Field, ValidationError
from yandex_music import ClientAsync, DeviceCode
from yandex_music import Playlist as YmPlaylist
from yandex_music.exceptions import DeviceAuthError
from yt_dlp import YoutubeDL, parse_options

logger = logging.getLogger("yt2ym")

AUDIO_EXTS = {".mp3", ".m4a"}
CHAPTER_PAT = re.compile(r"^(\d+)\s*-\s*(.+)\.(?:mp3|m4a)$")


@dataclass(frozen=True, slots=True)
class Cover:
    data: bytes
    mime: str


class VideoMeta(BaseModel, frozen=True):
    id: str
    title: str


# --- Yandex API responses (only the fields we read) --------------------------


class TrackTitle(BaseModel):
    title: str | None = None


class PlaylistItem(TrackTitle):
    """A playlist entry: either wraps a `track`, or carries `title` itself."""
    track: TrackTitle | None = None

    @property
    def effective_title(self) -> str | None:
        return (self.track or self).title


class Playlist(BaseModel):
    revision: int
    track_count: int = Field(0, alias="trackCount")
    tracks: list[PlaylistItem] = []


class PlaylistResponse(BaseModel):
    result: Playlist


class UploadTarget(BaseModel):
    post_target: str = Field(validation_alias=AliasChoices("post-target", "post_target"))


# --- helpers (audio / paths / yt-dlp) ----------------------------------------


def probe_url(url: str) -> VideoMeta:
    """Returns the id and title of the video at `url`, without downloading it."""
    with YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        return VideoMeta.model_validate(ydl.extract_info(url, download=False))


def download_audio(url: str, dest: Path, split_chapters: bool) -> None:
    """Downloads the audio of `url` into `dest`.

    With `split_chapters`, it also writes one file per chapter next to it.

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
    if split_chapters:
        args += [
            "--split-chapters",
            "-o", "chapter:" + str(dest / "%(section_number)02d - %(section_title)s.%(ext)s"),
        ]
    dest.mkdir(parents=True, exist_ok=True)
    logger.info("yt-dlp %s %s", " ".join(args), url)
    # Passing argv makes yt-dlp ignore its config files.
    with YoutubeDL(parse_options(args).ydl_opts) as ydl:
        if ydl.download([url]):
            raise click.ClickException(f"yt-dlp failed to download {url}")


def default_cache_dir(video_id: str) -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "yt2ym"
    return root / video_id


def collect_audio(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.iterdir() if p.suffix.lower() in AUDIO_EXTS)


def existing_audio(dest: Path, split_chapters: bool) -> list[Path]:
    if not dest.exists():
        return []
    all_audio = sorted(p for p in dest.iterdir() if p.suffix.lower() in AUDIO_EXTS)
    chapters = [p for p in all_audio if CHAPTER_PAT.match(p.name)]
    if split_chapters and chapters:
        return chapters
    singles = [p for p in all_audio if not CHAPTER_PAT.match(p.name)]
    return singles or all_audio


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
                    return Cover(data=frame.data, mime=frame.mime)
        elif suffix == ".m4a":
            covers = MP4(path).get("covr", [])
            if covers:
                c = covers[0]
                mime = "image/png" if c.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
                return Cover(data=bytes(c), mime=mime)
    except Exception:
        logger.debug("failed to extract cover from %s", path, exc_info=True)
    return None


def file_title(path: Path) -> str:
    """Title used when comparing to Yandex playlist tracks. Prefers the
    format's title tag; falls back to the filename (with extension) — that's
    what Yandex displays when no tag is present."""
    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            tags = MP3(path).tags
            tit2 = tags.get("TIT2") if tags else None
            if isinstance(tit2, TIT2) and tit2.text:
                return str(tit2.text[0])
        elif suffix == ".m4a":
            nam = MP4(path).get("\xa9nam")
            if nam:
                return str(nam[0])
    except Exception:
        logger.debug("failed to read title tag from %s", path, exc_info=True)
    return path.name


def write_title_track(path: Path, title: str, track: int) -> None:
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
    uid: int

    def playlist_url(self, kind: str, suffix: str = "") -> str:
        return f"{self.URL}/users/{self.uid}/playlists/{kind}{suffix}"

    def upload_target_url(self, kind: str, file_name: str) -> str:
        encoded = urllib.parse.quote(file_name, safe="_!() ").replace(" ", "+")
        return (
            f"{self.URL}/loader/upload-url"
            f"?uid={self.uid}&playlist-id={self.uid}:{kind}"
            f"&visibility=private&path={encoded}"
        )

    # Raw API, because the yandex_music library currently crashes deserializing
    # user-uploaded tracks.
    async def fetch_playlist(self, kind: str) -> Playlist:
        async with self.session.get(self.playlist_url(kind)) as r:
            return (await _ok_json(r, "playlist fetch", PlaylistResponse)).result

    async def upload_cover(self, kind: str, cover: Cover) -> None:
        form = aiohttp.FormData()
        form.add_field("image", cover.data, filename="cover", content_type=cover.mime)
        async with self.session.post(self.playlist_url(kind, "/cover/upload"), data=form) as r:
            await _ok_text(r, "playlist cover upload")

    async def delete_tracks(self, kind: str, playlist: Playlist) -> None:
        """Removes every track from playlist `kind`.

        The given `playlist` is the playlist's current state. Its revision and track count
        go into the request.
        """
        form = aiohttp.FormData()
        form.add_field("kind", kind)
        form.add_field("revision", str(playlist.revision))
        form.add_field(
            "diff", json.dumps([{"op": "delete", "from": 0, "to": playlist.track_count}])
        )
        # The /change endpoint applies a diff to the playlist. This one deletes every index.
        async with self.session.post(self.playlist_url(kind, "/change"), data=form) as r:
            await _ok_text(r, "playlist clear")

    async def upload_one(self, file_path: Path, playlist_kind: str) -> None:
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


class YandexMusic:
    """Yandex Music OAuth token + lazy client + upload operations."""

    TOKEN_FILE = Path.home() / ".config" / "yt2ym" / "token"

    def __init__(self, token: str) -> None:
        self.token = token
        self._client: ClientAsync | None = None
        self._uid: int | None = None

    @classmethod
    def from_args(cls, cli_token: str | None) -> YandexMusic:
        if cli_token:
            return cls(cli_token.strip())
        env = os.getenv("YANDEX_MUSIC_TOKEN")
        if env:
            return cls(env.strip())
        if cls.TOKEN_FILE.exists():
            return cls(cls.TOKEN_FILE.read_text().strip())
        raise click.ClickException(
            "No Yandex Music token. Run `login`, pass --token, set $YANDEX_MUSIC_TOKEN, "
            f"or write it to {cls.TOKEN_FILE}."
        )

    @classmethod
    async def login(cls) -> YandexMusic:
        """OAuth 2.0 device flow (yandex-music's built-in Smart TV client) → save
        token → return ready instance."""

        def show(code: DeviceCode) -> None:
            print(f"\n  Open: {code.verification_url}")
            print(f"  Enter code: {code.user_code}\n  Waiting for approval...\n")

        try:
            token = (await ClientAsync().device_auth(on_code=show)).access_token
        except DeviceAuthError as e:
            raise click.ClickException(f"oauth failed: {e}") from e
        cls.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        cls.TOKEN_FILE.write_text(token)
        cls.TOKEN_FILE.chmod(0o600)
        print(f"✓ saved token to {cls.TOKEN_FILE}")
        return cls(token)

    async def _ensure_client(self) -> ClientAsync:
        if self._client is None:
            self._client = await ClientAsync(self.token).init()
            me = self._client.me
            if me is None or me.account is None or me.account.uid is None:
                raise click.ClickException("token invalid: failed to fetch account info")
            self._uid = me.account.uid
        return self._client

    async def uid(self) -> int:
        await self._ensure_client()
        assert self._uid is not None
        return self._uid

    @asynccontextmanager
    async def _api(self) -> AsyncGenerator[_Api]:
        uid = await self.uid()
        headers = {"Authorization": f"OAuth {self.token}"}
        async with aiohttp.ClientSession(headers=headers) as s:
            yield _Api(s, uid)

    async def list_playlists(self) -> list[YmPlaylist]:
        c = await self._ensure_client()
        return await c.users_playlists_list()

    async def create_playlist(self, title: str, public: bool) -> YmPlaylist:
        c = await self._ensure_client()
        pl = await c.users_playlists_create(
            title=title,
            visibility="public" if public else "private",
        )
        if pl is None:
            raise click.ClickException("failed to create playlist")
        return pl

    async def resolve_or_create_playlist(self, name_or_kind: str) -> str:
        """If `name_or_kind` is numeric → assume it's an existing kind id.
        Otherwise look up an existing playlist by exact title, create if missing.
        Returns the numeric kind as a string."""
        if name_or_kind.isdigit():
            return name_or_kind
        for p in await self.list_playlists():
            if p.title == name_or_kind:
                return str(p.kind)
        pl = await self.create_playlist(name_or_kind, public=False)
        logger.info("created playlist %r (kind=%s)", pl.title, pl.kind)
        return str(pl.kind)

    async def set_playlist_cover(self, kind: str, cover: Cover) -> None:
        async with self._api() as api:
            await api.upload_cover(kind, cover)

    async def fetch_playlist(self, kind: str) -> Playlist:
        """Returns playlist `kind` with its tracks."""
        async with self._api() as api:
            return await api.fetch_playlist(kind)

    async def clear_playlist(self, kind: str) -> int:
        """Removes every track from playlist `kind`. Returns the number of tracks removed."""
        async with self._api() as api:
            pl = await api.fetch_playlist(kind)
            if not pl.track_count:
                return 0
            await api.delete_tracks(kind, pl)
        return pl.track_count

    async def playlist_track_titles(self, kind: str) -> set[str]:
        """Returns the titles of the tracks in playlist `kind`."""
        pl = await self.fetch_playlist(kind)
        return {t for item in pl.tracks if (t := item.effective_title)}

    async def upload_many(
        self, files: list[Path], playlist_kind: str, *, attempts: int = 4
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
    """Thin orchestration layer: subcommand methods, owns a lazy YandexMusic."""

    def __init__(self, token_arg: str | None) -> None:
        self._token_arg = token_arg
        self._ym: YandexMusic | None = None

    @property
    def ym(self) -> YandexMusic:
        if self._ym is None:
            self._ym = YandexMusic.from_args(self._token_arg)
        return self._ym

    # --- subcommands ---

    async def login(self) -> None:
        await YandexMusic.login()

    async def playlists(self) -> None:
        print("  3\tМне нравится (Liked)")
        for p in await self.ym.list_playlists():
            print(f"  {p.kind}\t{p.title}")

    async def create_playlist(self, title: str, public: bool) -> None:
        pl = await self.ym.create_playlist(title, public)
        visibility = "public" if public else "private"
        print(f"created: kind={pl.kind} title={pl.title!r} visibility={visibility}")

    def _download_to(self, url: str, dest: Path, split_chapters: bool, force: bool) -> list[Path]:
        """Run yt-dlp into `dest`, tag chapter files. Skip if audio files already
        present (unless `force`). Uses the source's native m4a/aac (no transcoding)."""
        if not force:
            already = existing_audio(dest, split_chapters)
            if already:
                logger.info("already downloaded → %s (%d file(s); use --force to redo)",
                            dest, len(already))
                return already
        download_audio(url, dest, split_chapters)

        # yt-dlp's --embed-metadata writes artist/album but not per-chapter title.
        # Recover from the "NN - title" filename pattern.
        for f in dest.iterdir():
            m = CHAPTER_PAT.match(f.name)
            if not m:
                continue
            write_title_track(f, m.group(2), int(m.group(1)))

        files = existing_audio(dest, split_chapters)
        if not files:
            raise click.ClickException(f"no audio produced in {dest}")
        return files

    async def _upload_files(self, files: list[Path], kind: str, force: bool) -> None:
        """Upload `files` to playlist `kind`. Normally skips any whose title is
        already in the playlist. With `force`, first wipes the playlist clean,
        then uploads everything."""
        if force:
            cleared = await self.ym.clear_playlist(kind)
            if cleared:
                logger.info("cleared %d track(s) from playlist kind=%s", cleared, kind)
            to_upload = files
        else:
            existing = await self.ym.playlist_track_titles(kind)
            to_upload = [f for f in files if file_title(f) not in existing]
            skipped = len(files) - len(to_upload)
            if skipped:
                logger.info("skipping %d file(s) already in playlist kind=%s "
                            "(use --force to redo)", skipped, kind)
        if not to_upload:
            logger.info("nothing to upload to kind=%s", kind)
            return
        await self.ym.upload_many(to_upload, kind)

    async def _set_cover_from(self, kind: str, source: Path) -> None:
        cover = extract_cover(source)
        if not cover:
            logger.info("no APIC cover in %s; skipping playlist cover", source.name)
            return
        await self.ym.set_playlist_cover(kind, cover)
        logger.info("set playlist cover (%d bytes, %s) from %s",
                    len(cover.data), cover.mime, source.name)

    async def set_cover(self, playlist: str, source: Path) -> None:
        kind = await self.ym.resolve_or_create_playlist(playlist)
        if source.suffix.lower() in AUDIO_EXTS:
            await self._set_cover_from(kind, source)
            return
        mime = "image/jpeg" if source.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        await self.ym.set_playlist_cover(kind, Cover(data=source.read_bytes(), mime=mime))
        logger.info("set playlist kind=%s cover from %s", kind, source.name)

    async def clear_playlist(self, playlist: str, yes: bool) -> None:
        kind = await self.ym.resolve_or_create_playlist(playlist)
        if not yes:
            pl = await self.ym.fetch_playlist(kind)
            if not click.confirm(
                f"Remove {pl.track_count} track(s) from playlist kind={kind}?",
                default=False,
            ):
                logger.info("aborted")
                return
        cleared = await self.ym.clear_playlist(kind)
        logger.info("cleared %d track(s) from playlist kind=%s", cleared, kind)

    def download(self, url: str, out: Path | None, split_chapters: bool, force: bool) -> list[Path]:
        dest = out or default_cache_dir(probe_url(url).id)
        return self._download_to(url, dest, split_chapters, force)

    async def upload(self, path: Path, playlist: str, force: bool) -> None:
        files = collect_audio(path)
        if not files:
            raise click.ClickException(f"no audio files at {path}")
        kind = await self.ym.resolve_or_create_playlist(playlist)
        await self._upload_files(files, kind, force)

    async def sync(self, url: str, playlist: str | None, out: Path | None,
                   force: bool, *, split_chapters: bool) -> None:
        """Download (single track or chaptered) → resolve/create playlist →
        upload → set cover from the first track. With `split_chapters` and no
        explicit playlist, defaults the playlist name to the video title."""
        probe = probe_url(url)
        dest = out or default_cache_dir(probe.id)
        playlist_name = playlist or (probe.title if split_chapters else None)
        if playlist_name is None:
            raise click.ClickException("--playlist is required for `sync`")
        if playlist is None:
            logger.info("using video title as playlist name: %r", playlist_name)
        kind = await self.ym.resolve_or_create_playlist(playlist_name)
        files = self._download_to(url, dest, split_chapters, force)
        await self._upload_files(files, kind, force)
        await self._set_cover_from(kind, files[0])


PLAYLIST_HELP = 'playlist by kind id (e.g. "3" = Liked) or by name (creates if missing).'
TOKEN_HELP = "Yandex Music OAuth token (else $YANDEX_MUSIC_TOKEN / ~/.config/yt2ym/token)"
OUT_HELP = "output directory (default: ~/.cache/yt2ym/<videoid>/)"
FORCE_HELP = "bypass idempotency: re-download, and wipe the playlist before re-uploading"


def out_option[F: Callable[..., Any]](f: F) -> F:
    return click.option(
        "--out", type=click.Path(path_type=Path, file_okay=False),
        default=None, help=OUT_HELP,
    )(f)


def force_option[F: Callable[..., Any]](f: F) -> F:
    return click.option("--force", is_flag=True, help=FORCE_HELP)(f)


def playlist_option[F: Callable[..., Any]](
    *, required: bool = True, help_suffix: str = ""
) -> Callable[[F], F]:
    return click.option(
        "--playlist", required=required, default=None,
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
@click.pass_obj
async def login(c: Cli) -> None:
    await c.login()


@cli.command(help="list your Yandex Music playlists")
@click.pass_obj
async def playlists(c: Cli) -> None:
    await c.playlists()


@cli.command("create-playlist", help="create a new playlist")
@click.argument("title")
@click.option("--public", is_flag=True, help="make playlist public (default: private)")
@click.pass_obj
async def create_playlist(c: Cli, title: str, public: bool) -> None:
    await c.create_playlist(title, public)


@cli.command("clear-playlist", help="remove all tracks from a playlist (by kind id or name)")
@click.argument("playlist")
@click.option("--yes", is_flag=True, help="skip confirmation prompt")
@click.pass_obj
async def clear_playlist(c: Cli, playlist: str, yes: bool) -> None:
    await c.clear_playlist(playlist, yes)


@cli.command(
    "set-cover",
    help="set a playlist's cover from an audio file (embedded cover) or image file",
)
@click.argument("playlist")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_obj
async def set_cover(c: Cli, playlist: str, source: Path) -> None:
    await c.set_cover(playlist, source)


@cli.command(help="download audio (splits by chapters if present)")
@click.argument("url")
@click.option("--single", is_flag=True, help="don't split chapters (download as one file)")
@out_option
@force_option
@click.pass_obj
async def download(c: Cli, url: str, single: bool, out: Path | None, force: bool) -> None:
    c.download(url, out, split_chapters=not single, force=force)


@cli.command(help="upload an audio file (mp3/m4a) or directory to Yandex Music")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@playlist_option()
@force_option
@click.pass_obj
async def upload(c: Cli, path: Path, playlist: str, force: bool) -> None:
    await c.upload(path, playlist, force)


@cli.command(help="download a video as a single track and upload it")
@click.argument("url")
@playlist_option()
@out_option
@force_option
@click.pass_obj
async def sync(c: Cli, url: str, playlist: str, out: Path | None, force: bool) -> None:
    await c.sync(url, playlist, out, force, split_chapters=False)


@cli.command(
    "sync-album",
    help="download a chaptered video and upload each chapter as a track. "
         "Default playlist = video title (created if missing).",
)
@click.argument("url")
@playlist_option(required=False, help_suffix=" Defaults to the video title.")
@out_option
@force_option
@click.pass_obj
async def sync_album(c: Cli, url: str, playlist: str | None, out: Path | None, force: bool) -> None:
    await c.sync(url, playlist, out, force, split_chapters=True)


if __name__ == "__main__":
    cli()
