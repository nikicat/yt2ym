import pytest

from yt2ym import CHAPTER_PAT, collect_audio, existing_audio, file_title


@pytest.mark.parametrize("name,num,title", [
    ("01 - Intro.mp3", "01", "Intro"),
    ("02 - Chapter Two.m4a", "02", "Chapter Two"),
    ("03-Trimmed.mp3", "03", "Trimmed"),
])
def test_chapter_pat_matches(name, num, title):
    m = CHAPTER_PAT.match(name)
    assert m and m.group(1) == num and m.group(2) == title


@pytest.mark.parametrize("name", [
    "Intro.mp3",
    "01 - song.flac",
    "song.txt",
])
def test_chapter_pat_no_match(name):
    assert CHAPTER_PAT.match(name) is None


def test_collect_audio_file(tmp_path):
    f = tmp_path / "x.mp3"
    f.touch()
    assert collect_audio(f) == [f]


def test_collect_audio_dir_filters_extensions(tmp_path):
    (tmp_path / "a.mp3").touch()
    (tmp_path / "b.m4a").touch()
    (tmp_path / "c.txt").touch()
    out = collect_audio(tmp_path)
    assert sorted(p.name for p in out) == ["a.mp3", "b.m4a"]


def test_existing_audio_returns_chapters_when_split(tmp_path):
    for name in ("01 - A.mp3", "02 - B.mp3", "loose.mp3"):
        (tmp_path / name).touch()
    out = existing_audio(tmp_path, split_chapters=True)
    assert sorted(p.name for p in out) == ["01 - A.mp3", "02 - B.mp3"]


def test_existing_audio_returns_singles_when_not_split(tmp_path):
    (tmp_path / "01 - A.mp3").touch()
    (tmp_path / "loose.mp3").touch()
    out = existing_audio(tmp_path, split_chapters=False)
    assert [p.name for p in out] == ["loose.mp3"]


def test_existing_audio_falls_back_to_all_when_no_singles(tmp_path):
    (tmp_path / "01 - A.mp3").touch()
    (tmp_path / "02 - B.mp3").touch()
    out = existing_audio(tmp_path, split_chapters=False)
    assert sorted(p.name for p in out) == ["01 - A.mp3", "02 - B.mp3"]


def test_existing_audio_missing_dir(tmp_path):
    assert existing_audio(tmp_path / "nope", split_chapters=True) == []


def test_file_title_invalid_file_falls_back(tmp_path):
    f = tmp_path / "bogus.mp3"
    f.write_bytes(b"not a real mp3")
    assert file_title(f) == "bogus.mp3"


def test_file_title_non_audio_extension_falls_back(tmp_path):
    f = tmp_path / "weird.flac"
    f.touch()
    assert file_title(f) == "weird.flac"
