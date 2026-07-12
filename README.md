# MovieFinder

Search TMDB, resolve a playable HLS stream via VidSrc's 4-hop chain, and download via yt-dlp.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add your TMDB_API_KEY
```

## Usage

```bash
# Movies
python main.py "Inception"
python main.py "The Matrix" --year 1999
python main.py "Interstellar" --quality 1080p --no-confirm --verbose

# TV Shows
python main.py "Breaking Bad" --type tv --season 1 --episode 1
python main.py "Severance" --type tv --season 2 --episode 5
```

## Output paths

Configure in `.env` or override via CLI:

```
MOVIES_DIR=./downloads/movies
TV_DIR=./downloads/tv
```

```bash
python main.py "Inception" --movies-dir "/Volumes/Media/Movies"
python main.py "Breaking Bad" --type tv --season 1 --episode 1 --tv-dir "/Volumes/Media/TV"
```

## Naming convention

```
Movies:  ./downloads/movies/Inception (2010)/Inception (2010).mp4
TV:      ./downloads/tv/Breaking Bad/Season 01/Breaking Bad - S01E01 - Pilot.mp4
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

Implement `StreamProvider` from `moviefinder.utils`.
