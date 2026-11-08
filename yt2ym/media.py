"""Downloads audio with yt-dlp into the cache, and reads and writes its tags."""

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import asyncclick as click
from mutagen.id3 import APIC, TIT2, TRCK, Encoding
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from pydantic import BaseModel, ValidationError
from yt_dlp import YoutubeDL, parse_options
from yt_dlp.utils import DownloadError

from yt2ym.types import Cover, MediaUrl, TrackNumber, TrackTitle, VideoId

logger = logging.getLogger(__name__)


AUDIO_EXTS = {".mp3", ".m4a"}


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
