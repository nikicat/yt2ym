"""
yt2ym — download YouTube audio (with chapter splitting) and upload to Yandex Music.

Run via:  uv run yt2ym.py <subcommand> ...

Token: run `login`, or pass --token, or set YANDEX_MUSIC_TOKEN, or place it in
~/.config/yt2ym/token.

Upload logic adapted from https://github.com/Kay0k1/yandex-music-uploader.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import urllib.parse
from pathlib import Path

import aiohttp
import asyncclick as click
from mutagen.id3 import TIT2, TRCK
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from yandex_music import ClientAsync

logger = logging.getLogger("yt2ym")

AUDIO_EXTS = {".mp3", ".m4a"}
CHAPTER_PAT = re.compile(r"^(\d+)\s*-\s*(.+)\.(?:mp3|m4a)$")


# --- pure helpers (audio / paths / yt-dlp) -----------------------------------


def probe_youtube(url: str) -> dict[str, str]:
    """Fetch video id + title from yt-dlp without downloading."""
    r = subprocess.run(
        ["yt-dlp", "--print", "%(id)s", "--print", "%(title)s",
         "--skip-download", url],
        check=True, capture_output=True, text=True,
    )
    lines = r.stdout.strip().splitlines()
    return {"id": lines[0], "title": lines[1]}


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


def extract_cover(path: Path) -> tuple[bytes, str] | None:
    """Return (image_bytes, mime) of the embedded cover, or None."""
    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            tags = MP3(path).tags
            if not tags:
                return None
            for k in tags:
                if k.startswith("APIC"):
                    apic = tags[k]
                    return apic.data, apic.mime
        elif suffix == ".m4a":
            covers = MP4(path).get("covr", [])
            if covers:
                c = covers[0]
                mime = "image/png" if c.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
                return bytes(c), mime
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
            if tit2 and tit2.text:
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
        audio.tags.add(TIT2(encoding=3, text=title))
        audio.tags.add(TRCK(encoding=3, text=str(track)))
        audio.save()
    elif suffix == ".m4a":
        audio = MP4(path)
        audio["\xa9nam"] = title
        audio["trkn"] = [(track, 0)]
        audio.save()


# --- Yandex Music ------------------------------------------------------------


class YandexMusic:
    """Yandex Music OAuth token + lazy client + upload operations."""

    TOKEN_FILE = Path.home() / ".config" / "yt2ym" / "token"

    # Public Smart TV credentials reused by every open-source Yandex Music client.
    CLIENT_ID = "23cabbbdc6cd418abb4b39c32c41195d"
    CLIENT_SECRET = "53bc75238f0c4d08a118e51fe9203300"

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
        """OAuth 2.0 device flow → save token → return ready instance."""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://oauth.yandex.ru/device/code",
                data={"client_id": cls.CLIENT_ID},
            ) as resp:
                d = await cls._ok_json(resp, "device/code")

            print(f"\n  Open: {d['verification_url']}")
            print(f"  Enter code: {d['user_code']}\n  Waiting for approval...\n")

            interval = d.get("interval", 5)
            timeout = d.get("expires_in", 300)
            elapsed = 0
            while elapsed < timeout:
                await asyncio.sleep(interval)
                elapsed += interval
                async with session.post(
                    "https://oauth.yandex.ru/token",
                    data={
                        "grant_type": "device_code",
                        "code": d["device_code"],
                        "client_id": cls.CLIENT_ID,
                        "client_secret": cls.CLIENT_SECRET,
                    },
                ) as r:
                    body = await r.json()
                    if r.status == 200 and "access_token" in body:
                        token = body["access_token"]
                        cls.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
                        cls.TOKEN_FILE.write_text(token)
                        cls.TOKEN_FILE.chmod(0o600)
                        print(f"✓ saved token to {cls.TOKEN_FILE}")
                        return cls(token)
                    err = body.get("error", "")
                    if err == "authorization_pending":
                        continue
                    if err == "slow_down":
                        interval += 1
                        continue
                    raise click.ClickException(f"oauth failed: {body}")
            raise click.ClickException("oauth timed out")

    def _session(self) -> aiohttp.ClientSession:
        """A new aiohttp session with this token's Authorization header baked in."""
        return aiohttp.ClientSession(headers={"Authorization": f"OAuth {self.token}"})

    @staticmethod
    async def _ok_json(r: aiohttp.ClientResponse, label: str) -> dict:
        """Raise if HTTP != 200, else return parsed JSON body."""
        if r.status != 200:
            raise RuntimeError(f"{label} failed: HTTP {r.status} {await r.text()}")
        return await r.json()

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

    async def list_playlists(self) -> list:
        c = await self._ensure_client()
        return await c.users_playlists_list()

    async def create_playlist(self, title: str, public: bool):
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

    async def set_playlist_cover(self, kind: str, image: bytes, mime: str) -> None:
        uid = await self.uid()
        async with self._session() as s:
            form = aiohttp.FormData()
            form.add_field("image", image, filename="cover", content_type=mime)
            async with s.post(
                f"https://api.music.yandex.net/users/{uid}/playlists/{kind}/cover/upload",
                data=form,
            ) as r:
                await self._ok_json(r, "playlist cover upload")

    async def clear_playlist(self, kind: str) -> int:
        """Remove every track from playlist `kind` via the /change endpoint.
        Returns the number of tracks removed."""
        uid = await self.uid()
        async with self._session() as s:
            async with s.get(f"https://api.music.yandex.net/users/{uid}/playlists/{kind}") as r:
                pl = (await self._ok_json(r, "playlist fetch"))["result"]
            track_count = pl.get("trackCount", 0)
            if not track_count:
                return 0
            form = aiohttp.FormData()
            form.add_field("kind", kind)
            form.add_field("revision", str(pl["revision"]))
            form.add_field("diff", json.dumps([{"op": "delete", "from": 0, "to": track_count}]))
            async with s.post(
                f"https://api.music.yandex.net/users/{uid}/playlists/{kind}/change",
                data=form,
            ) as r:
                await self._ok_json(r, "playlist clear")
        return track_count

    async def playlist_track_titles(self, kind: str) -> set[str]:
        """Fetch existing track titles in a playlist via raw API (the yandex_music
        library currently crashes deserializing user-uploaded tracks)."""
        uid = await self.uid()
        async with self._session() as s, s.get(
            f"https://api.music.yandex.net/users/{uid}/playlists/{kind}"
        ) as r:
            data = await self._ok_json(r, "playlist fetch")
        titles: set[str] = set()
        for t in data.get("result", {}).get("tracks", []):
            tr = t.get("track") or t
            title = tr.get("title")
            if title:
                titles.add(title)
        return titles

    async def upload_one(
        self,
        session: aiohttp.ClientSession,
        file_path: Path,
        playlist_kind: str,
    ) -> None:
        uid = await self.uid()
        file_name = file_path.name
        encoded = urllib.parse.quote(file_name, safe="_!() ").replace(" ", "+")
        url_req = (
            "https://api.music.yandex.net/loader/upload-url"
            f"?uid={uid}&playlist-id={uid}:{playlist_kind}"
            f"&visibility=private&path={encoded}"
        )

        async with session.post(url_req) as resp:
            data = await self._ok_json(resp, "upload-url")

        upload_url = data.get("post-target") or data.get("post_target")
        if not upload_url:
            raise RuntimeError(f"no upload URL in response: {data}")

        form = aiohttp.FormData()
        with open(file_path, "rb") as f:
            form.add_field("file", f, filename=file_name)
            async with session.post(
                upload_url, data=form, timeout=aiohttp.ClientTimeout(total=600)
            ) as up:
                body = await up.text()
                if up.status not in (200, 201):
                    raise RuntimeError(f"upload HTTP {up.status}: {body}")
                if "OK" not in body.upper() and "CREATED" not in body.upper():
                    raise RuntimeError(f"upload returned {up.status} but unexpected body: {body}")

    async def upload_many(self, files: list[Path], playlist_kind: str) -> None:
        # Yandex displays user-uploaded tracks newest-first, so upload in reverse
        # order to get caller-supplied order on display.
        async with self._session() as session:
            for f in reversed(files):
                logger.info("→ uploading %s (playlist kind=%s)", f.name, playlist_kind)
                await self.upload_one(session, f, playlist_kind)
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
        present (unless `force`). Uses YouTube's native m4a/aac (no transcoding)."""
        if not force:
            already = existing_audio(dest, split_chapters)
            if already:
                logger.info("already downloaded → %s (%d file(s); use --force to redo)",
                            dest, len(already))
                return already
        if not shutil.which("yt-dlp"):
            raise click.ClickException("yt-dlp not found in PATH")
        dest.mkdir(parents=True, exist_ok=True)
        cmd = [
            "yt-dlp",
            "-x",  # extract audio in native format (no transcoding)
            "--embed-metadata",
            "--no-embed-chapters",  # avoid chapter atoms — Yandex picks them as title
            "--embed-thumbnail",
            "-o", str(dest / "%(title)s.%(ext)s"),
        ]
        if split_chapters:
            cmd += [
                "--split-chapters",
                "-o", "chapter:" + str(dest / "%(section_number)02d - %(section_title)s.%(ext)s"),
            ]
        cmd.append(url)
        logger.info("Running: %s", " ".join(cmd))
        subprocess.run(cmd, check=True)

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
        await self.ym.set_playlist_cover(kind, *cover)
        logger.info("set playlist cover (%d bytes, %s) from %s",
                    len(cover[0]), cover[1], source.name)

    async def set_cover(self, playlist: str, source: Path) -> None:
        kind = await self.ym.resolve_or_create_playlist(playlist)
        if source.suffix.lower() in AUDIO_EXTS:
            await self._set_cover_from(kind, source)
            return
        mime = "image/jpeg" if source.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        await self.ym.set_playlist_cover(kind, source.read_bytes(), mime)
        logger.info("set playlist kind=%s cover from %s", kind, source.name)

    async def clear_playlist(self, playlist: str, yes: bool) -> None:
        kind = await self.ym.resolve_or_create_playlist(playlist)
        if not yes:
            existing = await self.ym.playlist_track_titles(kind)
            if not click.confirm(
                f"Remove {len(existing)} track(s) from playlist kind={kind}?",
                default=False,
            ):
                logger.info("aborted")
                return
        cleared = await self.ym.clear_playlist(kind)
        logger.info("cleared %d track(s) from playlist kind=%s", cleared, kind)

    def download(self, url: str, out: Path | None, split_chapters: bool, force: bool) -> list[Path]:
        dest = out or default_cache_dir(probe_youtube(url)["id"])
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
        probe = probe_youtube(url)
        dest = out or default_cache_dir(probe["id"])
        playlist_name = playlist or (probe["title"] if split_chapters else None)
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


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--token", envvar="YANDEX_MUSIC_TOKEN", default=None, help=TOKEN_HELP)
@click.pass_context
async def cli(ctx: click.Context, token: str | None) -> None:
    """yt2ym — download YouTube audio and upload to Yandex Music."""
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


@cli.command(help="download YouTube audio (splits by chapters if present)")
@click.argument("url")
@click.option("--single", is_flag=True, help="don't split chapters (download as one file)")
@click.option(
    "--out", type=click.Path(path_type=Path, file_okay=False), default=None, help=OUT_HELP,
)
@click.option("--force", is_flag=True, help=FORCE_HELP)
@click.pass_obj
async def download(c: Cli, url: str, single: bool, out: Path | None, force: bool) -> None:
    c.download(url, out, split_chapters=not single, force=force)


@cli.command(help="upload an audio file (mp3/m4a) or directory to Yandex Music")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--playlist", required=True, help=PLAYLIST_HELP)
@click.option("--force", is_flag=True, help=FORCE_HELP)
@click.pass_obj
async def upload(c: Cli, path: Path, playlist: str, force: bool) -> None:
    await c.upload(path, playlist, force)


@cli.command(help="download a YouTube video as a single track and upload it")
@click.argument("url")
@click.option("--playlist", required=True, help=PLAYLIST_HELP)
@click.option(
    "--out", type=click.Path(path_type=Path, file_okay=False), default=None, help=OUT_HELP,
)
@click.option("--force", is_flag=True, help=FORCE_HELP)
@click.pass_obj
async def sync(c: Cli, url: str, playlist: str, out: Path | None, force: bool) -> None:
    await c.sync(url, playlist, out, force, split_chapters=False)


@cli.command(
    "sync-album",
    help="download a chaptered YouTube video and upload each chapter as a track. "
         "Default playlist = video title (created if missing).",
)
@click.argument("url")
@click.option("--playlist", default=None, help=PLAYLIST_HELP + " Defaults to the video title.")
@click.option(
    "--out", type=click.Path(path_type=Path, file_okay=False), default=None, help=OUT_HELP,
)
@click.option("--force", is_flag=True, help=FORCE_HELP)
@click.pass_obj
async def sync_album(c: Cli, url: str, playlist: str | None, out: Path | None, force: bool) -> None:
    await c.sync(url, playlist, out, force, split_chapters=True)


if __name__ == "__main__":
    cli()
