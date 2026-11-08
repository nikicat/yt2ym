from pathlib import Path
from typing import Any, Self

import asyncclick as click
import pytest

import yt2ym
from yt2ym import (
    Chapter,
    PlaylistResponse,
    UploadTarget,
    collect_audio,
    download_audio,
    existing_audio,
    file_title,
)


@pytest.mark.parametrize("name,num,title", [
    ("01 - Intro.mp3", 1, "Intro"),
    ("02 - Chapter Two.m4a", 2, "Chapter Two"),
    ("03-Trimmed.mp3", 3, "Trimmed"),
])
def test_chapter_parse(name: str, num: int, title: str):
    assert Chapter.parse(Path(name)) == Chapter(num, yt2ym.TrackTitle(title))


@pytest.mark.parametrize("name", [
    "Intro.mp3",
    "01 - song.flac",
    "song.txt",
])
def test_chapter_parse_rejects(name: str):
    assert Chapter.parse(Path(name)) is None


def test_chapter_outtmpl_round_trips():
    name = Chapter.OUTTMPL % {"section_number": 7, "section_title": "A - B", "ext": "m4a"}
    assert Chapter.parse(Path(name)) == Chapter(7, yt2ym.TrackTitle("A - B"))


def test_collect_audio_file(tmp_path: Path):
    f = tmp_path / "x.mp3"
    f.touch()
    assert collect_audio(f) == [f]


def test_collect_audio_dir_filters_extensions(tmp_path: Path):
    (tmp_path / "a.mp3").touch()
    (tmp_path / "b.m4a").touch()
    (tmp_path / "c.txt").touch()
    out = collect_audio(tmp_path)
    assert sorted(p.name for p in out) == ["a.mp3", "b.m4a"]


def make_cache(dest: Path, video: str, chapters: list[str]) -> None:
    (dest / Chapter.DIR).mkdir(parents=True)
    (dest / video).touch()
    for name in chapters:
        (dest / Chapter.DIR / name).touch()


def test_existing_audio_returns_chapters_when_split(tmp_path: Path):
    # The video's own title looks like a chapter, but it is not one.
    make_cache(tmp_path, "1989 - Live.mp3", ["01 - A.mp3", "02 - B.mp3"])
    out = existing_audio(tmp_path, split_chapters=True)
    assert sorted(p.name for p in out) == ["01 - A.mp3", "02 - B.mp3"]


def test_existing_audio_returns_the_video_when_not_split(tmp_path: Path):
    make_cache(tmp_path, "loose.mp3", ["01 - A.mp3"])
    out = existing_audio(tmp_path, split_chapters=False)
    assert [p.name for p in out] == ["loose.mp3"]


def test_existing_audio_returns_the_video_when_it_has_no_chapters(tmp_path: Path):
    make_cache(tmp_path, "loose.mp3", [])
    out = existing_audio(tmp_path, split_chapters=True)
    assert [p.name for p in out] == ["loose.mp3"]


def test_existing_audio_missing_dir(tmp_path: Path):
    assert existing_audio(tmp_path / "nope", split_chapters=True) == []


def test_file_title_invalid_file_falls_back(tmp_path: Path):
    f = tmp_path / "bogus.mp3"
    f.write_bytes(b"not a real mp3")
    assert file_title(f) == "bogus.mp3"


def test_file_title_non_audio_extension_falls_back(tmp_path: Path):
    f = tmp_path / "weird.flac"
    f.touch()
    assert file_title(f) == "weird.flac"


def test_playlist_response_titles_and_defaults():
    pl = PlaylistResponse.model_validate_json(
        '{"result": {"revision": 7, "trackCount": 3, "tracks": ['
        '{"track": {"title": "wrapped"}}, {"title": "bare"}, {"track": {}}]}}'
    ).result
    assert (pl.revision, pl.track_count) == (7, 3)
    assert [t.effective_title for t in pl.tracks] == ["wrapped", "bare", None]
    assert PlaylistResponse.model_validate_json('{"result": {"revision": 1}}').result.tracks == []


@pytest.mark.parametrize("key", ["post-target", "post_target"])
def test_upload_target_accepts_both_spellings(key: str):
    assert UploadTarget.model_validate_json(f'{{"{key}": "u"}}').post_target == "u"


URL = yt2ym.MediaUrl("https://example.com/v")


class FakeYoutubeDL:
    params: dict[str, Any] = {}
    retcode = 0

    def __init__(self, params: dict[str, Any]) -> None:
        FakeYoutubeDL.params = params

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def download(self, url_list: list[str]) -> int:
        return self.retcode


def test_download_audio_split_chapters_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(yt2ym, "YoutubeDL", FakeYoutubeDL)
    download_audio(URL, tmp_path, split_chapters=True)
    params = FakeYoutubeDL.params
    chapter = tmp_path / Chapter.OUTTMPL
    assert params["outtmpl"]["chapter"] == str(chapter)
    assert any(pp["key"] == "FFmpegSplitChapters" for pp in params["postprocessors"])


def test_download_audio_failure_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(yt2ym, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(FakeYoutubeDL, "retcode", 1)
    with pytest.raises(click.ClickException):
        download_audio(URL, tmp_path, split_chapters=False)


def test_probe_url_reports_yt_dlp_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingYoutubeDL(FakeYoutubeDL):
        def extract_info(self, url: str, download: bool = True) -> dict[str, Any] | None:
            raise yt2ym.DownloadError("ERROR: Unsupported URL")

    monkeypatch.setattr(yt2ym, "YoutubeDL", FailingYoutubeDL)
    with pytest.raises(click.ClickException, match="failed to probe"):
        yt2ym.probe_url(URL)


def test_parse_playlist() -> None:
    def parse(value: str) -> yt2ym.PlaylistRef | None:
        return yt2ym.parse_playlist(None, None, value)  # type: ignore[arg-type]

    assert parse("3") == yt2ym.PlaylistKind(3)
    assert isinstance(parse("3"), yt2ym.PlaylistKind)
    for title in ["Mix 3", "²"]:
        assert parse(title) == title
        assert isinstance(parse(title), yt2ym.PlaylistTitle)
