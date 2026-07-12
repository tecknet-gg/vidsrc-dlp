# vidsrc-dlp

Search TMDB, resolve a playable HLS stream via VidSrc's 4-hop chain, and download via yt-dlp.

## Install

```bash
pip install vidsrc-dlp
cp .env.example .env  # add your TMDB_API_KEY
```

Or for local development:

```bash
git clone https://github.com/jeevan/vidsrc-dlp
cd vidsrc-dlp
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env  # add your TMDB_API_KEY
```

## Usage

```bash
# Movies
vidsrc-dlp "Inception"
vidsrc-dlp "The Matrix" --year 1999
vidsrc-dlp "Interstellar" --quality 1080p --no-confirm --verbose

# TV Shows
vidsrc-dlp "Breaking Bad" --type tv --season 1 --episode 1
vidsrc-dlp "Severance" --type tv --season 2 --episode 5

# Test with main.py (no install needed)
python main.py "Inception"
```

## Output paths

Configure in `.env` or override via CLI:

```
MOVIES_DIR=./downloads/movies
TV_DIR=./downloads/tv
```

```bash
vidsrc-dlp "Inception" --movies-dir "/Volumes/Media/Movies"
vidsrc-dlp "Breaking Bad" --type tv --season 1 --episode 1 --tv-dir "/Volumes/Media/TV"
```

## Naming convention

```
Movies:  {MOVIES_DIR}/Inception (2010)/Inception (2010).mp4
TV:      {TV_DIR}/Breaking Bad/Season 01/Breaking Bad - S01E01 - Pilot.mp4
```

## Architecture

1. **TMDB** — search API for movie/TV metadata
2. **VidSrc Resolver** — `requests`-only 4-hop chain to extract an HLS URL
3. **Downloader** — `yt-dlp` + ffmpeg to download and transcode to MP4

## How the resolver works

```
vidsrc.to/embed/{movie|tv}/{tmdb_id}[/{season}/{episode}]
  → vsembed.ru iframe (parse src attr)
    → cloudorchestranova.com/rcp/{hash} (parse prorcp hash)
      → cloudorchestranova.com/prorcp/{hash} (m3u8 URLs with __TOKEN__ placeholders)
        → generate.php endpoints (JWT tokens)
          → final m3u8 URL
```

## Adding a new stream provider

Implement `StreamProvider` from `vidsrc_dlp.utils`.

## Acknowledgements

- **[MaheshSharan/vidsrc](https://github.com/MaheshSharan/vidsrc)** — The request-based 4-hop resolution chain was reverse-engineered from this open-source PHP scraper. Critical reference for the VidSrc token flow.
- **[TMDB](https://www.themoviedb.org/)** — Movie and TV metadata API. All search, detail, episode info sourced from their free tier.
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** — The download engine. Handles HLS fragment fetching, decryption, and ffmpeg transcoding.
- **[requests](https://github.com/psf/requests)** — HTTP library for the resolver chain.
- **[python-dotenv](https://github.com/theskumar/python-dotenv)** — Environment variable loading from `.env`.
