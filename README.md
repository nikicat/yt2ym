# yt2ym

[![CI](https://github.com/nikicat/yt2ym/actions/workflows/ci.yml/badge.svg)](https://github.com/nikicat/yt2ym/actions/workflows/ci.yml)
[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https://raw.githubusercontent.com/nikicat/yt2ym/main/pyproject.toml)](https://www.python.org/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Download audio from YouTube, X, or [any site yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md) (with chapter splitting) and upload it to Yandex Music as a playlist — with proper titles, track numbers, and album cover.

## Install / run

Requires [uv](https://github.com/astral-sh/uv) and ffmpeg on `$PATH`. yt2ym uses its own [yt-dlp](https://github.com/yt-dlp/yt-dlp), locked in `uv.lock`, and does not read yt-dlp config files. When a site breaks, update it with `uv lock --upgrade-package yt-dlp && uv sync`.

```bash
git clone https://github.com/nikicat/yt2ym
cd yt2ym
uv run yt2ym.py --help
```

## First-time setup

```bash
uv run yt2ym.py login
```

This runs the OAuth 2.0 **device flow** against Yandex: it prints a short user-code, you open the URL in a browser, approve, and the token gets saved to `~/.config/yt2ym/token` (0600). You can also pass `--token`, set `$YANDEX_MUSIC_TOKEN`, or place the token in that file manually.

## Subcommands

| | |
|---|---|
| `login` | OAuth device flow → save token. |
| `playlists` | List your playlists with their `kind` ids. |
| `create-playlist TITLE [--public]` | Create a new playlist (private by default). |
| `clear-playlist PLAYLIST [--yes]` | Remove every track from a playlist. |
| `set-cover PLAYLIST PATH` | Set a playlist's cover from an mp3/m4a (embedded cover) or image. |
| `download URL [--out DIR] [--single] [--redownload]` | Download audio to `$XDG_CACHE_HOME/yt2ym/<videoid>/` (`~/.cache/yt2ym/<videoid>/` by default), splitting by chapters if present. |
| `upload PATH --playlist X [--replace]` | Upload an mp3/m4a file or directory of them. |
| `sync URL --playlist X [--redownload] [--replace]` | Download a video as a single track and upload it. |
| `sync-album URL [--playlist X] [--redownload] [--replace]` | Download a chaptered video and upload each chapter as a track; the playlist defaults to the video title. |

`--playlist` accepts a numeric `kind` id (e.g. `3` = Liked) **or** a playlist name. Names are looked up; `upload`, `sync` and `sync-album` create a missing one as a private playlist, and the other commands error.

## Example

```bash
# All-in-one: download "Complete SUNDER OST", create a playlist of the same name,
# tag each chapter properly, upload them in correct order, set the album cover.
uv run yt2ym.py sync-album https://www.youtube.com/watch?v=AJK4eo2PwQY
```

Re-run the same command and it's a no-op: yt2ym checks both the local cache and the Yandex playlist before doing anything. Pass `--replace` to empty the playlist and upload everything again.

## How it works

- **Audio**: m4a/AAC as the site serves it, not re-encoded.
- **Tags**: each chapter is uploaded with its own title and track number.
- **Order**: Yandex displays user-uploaded tracks newest-first, so yt2ym uploads in reverse to make chapter 01 appear first.
- **Idempotency** is state-based:
  - Download skips if expected audio files already exist in the cache dir.
  - Upload fetches the target playlist's track titles and only uploads files whose title isn't already there.
  - `--redownload` downloads again even when the cache has the audio.
  - `--replace` removes every track from the playlist, then uploads all files.
- **Cache layout**: `$XDG_CACHE_HOME/yt2ym/<videoid>/` (default `~/.cache/yt2ym/<videoid>/`) per video. Override with `--out`.

## Token / credentials

yt2ym uses the publicly-known Yandex **Smart-TV OAuth client** (same one every open-source Yandex Music client uses) to get an access token via device flow. The token is yours; the client_id/secret are not secret.

## Credits

Upload logic adapted from [Kay0k1/yandex-music-uploader](https://github.com/Kay0k1/yandex-music-uploader) (MIT). Built on [MarshalX/yandex-music-api](https://github.com/MarshalX/yandex-music-api).

## License

[MIT](LICENSE).
