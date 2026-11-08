"""The yt2ym command line."""

import logging
from collections.abc import Callable
from functools import cached_property
from pathlib import Path
from typing import Any

import asyncclick as click

from yt2ym.media import (
    collect_audio,
    default_cache_dir,
    download_to,
    extract_cover,
    file_title,
    is_audio,
    probe_url,
)
from yt2ym.types import (
    LIKED_KIND,
    Cover,
    MediaUrl,
    PlaylistKind,
    PlaylistRef,
    PlaylistTitle,
    Visibility,
)
from yt2ym.yandex import YandexMusic

logger = logging.getLogger(__name__)


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
