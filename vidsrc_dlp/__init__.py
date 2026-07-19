"""
vidsrc-dlp — Search TMDB, resolve VidSrc streams, and download via yt-dlp.

Usage (CLI):
    vidsrc-dlp "Inception"
    vidsrc-dlp "Breaking Bad" --type tv --season 1 --episode 1

Usage (inline):
    >>> from vidsrc_dlp import search_movie, resolve, download
    >>> movies = search_movie("Inception")
    >>> stream = resolve(movies[0])
    >>> download(stream, movies[0])
"""

import logging

from vidsrc_dlp.config import load_config
from vidsrc_dlp.downloader import VideoDownloader
from vidsrc_dlp.resolver import MultiDomainResolver, VidSrcResolver
from vidsrc_dlp.tmdb import TMDBClient
from vidsrc_dlp.utils import Media, MediaType, StreamInfo

logger = logging.getLogger("vidsrc_dlp.api")

__version__ = "0.9.1"
__all__ = [
    "Media",
    "MediaType",
    "StreamInfo",
    "search_movie",
    "search_tv",
    "get_movie_details",
    "get_tv_details",
    "resolve",
    "download",
]


def search_movie(
    query: str, year: int | None = None, api_key: str | None = None
) -> list[Media]:
    """Search for movies by title.

    Parameters
    ----------
    query : str
        Movie title to search for.
    year : int, optional
        Filter by release year.
    api_key : str, optional
        TMDB API key. Falls back to .env if not provided.

    Returns
    -------
    list[Media]
        List of matching movies.

    Examples
    --------
    >>> from vidsrc_dlp import search_movie
    >>> movies = search_movie("Inception", year=2010)
    >>> movies[0].title
    'Inception'
    """
    config = load_config(api_key=api_key)
    return TMDBClient(config.tmdb_api_key).search_movie(query, year)


def search_tv(
    query: str, year: int | None = None, api_key: str | None = None
) -> list[Media]:
    """Search for TV shows by title.

    Parameters
    ----------
    query : str
        TV show title to search for.
    year : int, optional
        Filter by first air year.
    api_key : str, optional
        TMDB API key. Falls back to .env if not provided.

    Returns
    -------
    list[Media]
        List of matching TV shows.

    Examples
    --------
    >>> from vidsrc_dlp import search_tv
    >>> shows = search_tv("Breaking Bad")
    >>> shows[0].title
    'Breaking Bad'
    """
    config = load_config(api_key=api_key)
    return TMDBClient(config.tmdb_api_key).search_tv(query, year)


def get_movie_details(tmdb_id: int, api_key: str | None = None) -> Media | None:
    """Get full movie metadata including IMDb ID, genres, rating.

    Parameters
    ----------
    tmdb_id : int
        TMDB movie ID.
    api_key : str, optional
        TMDB API key. Falls back to .env if not provided.

    Returns
    -------
    Media | None

    Examples
    --------
    >>> from vidsrc_dlp import get_movie_details
    >>> m = get_movie_details(27205)
    >>> m.imdb_id
    'tt1375666'
    """
    config = load_config(api_key=api_key)
    return TMDBClient(config.tmdb_api_key).get_movie_details(tmdb_id)


def get_tv_details(tmdb_id: int, api_key: str | None = None) -> Media | None:
    """Get full TV show metadata including IMDb ID, genres, rating.

    Parameters
    ----------
    tmdb_id : int
        TMDB TV show ID.
    api_key : str, optional
        TMDB API key. Falls back to .env if not provided.

    Returns
    -------
    Media | None
    """
    config = load_config(api_key=api_key)
    return TMDBClient(config.tmdb_api_key).get_tv_details(tmdb_id)


def resolve(
    media: Media,
    season: int | None = None,
    episode: int | None = None,
) -> StreamInfo | None:
    """Resolve a playable HLS stream URL for a movie or TV episode.

    Parameters
    ----------
    media : Media
        The movie or TV show to resolve.
    season : int, optional
        Season number (required for TV shows).
    episode : int, optional
        Episode number (required for TV shows).

    Returns
    -------
    StreamInfo | None
        Stream URL and headers if resolution succeeds.

    Examples
    --------
    >>> from vidsrc_dlp import search_movie, resolve
    >>> movie = search_movie("Inception")[0]
    >>> stream = resolve(movie)
    >>> stream.url.startswith("http")
    True
    """
    resolver = MultiDomainResolver()
    media_type = "tv" if media.media_type == MediaType.TV else "movie"
    return resolver.resolve(
        media.id,
        media_type=media_type,
        season=season or media.season,
        episode=episode or media.episode,
    )


def download(
    stream: StreamInfo,
    media: Media,
    quality: str = "best",
    movies_dir: str | None = None,
    tv_dir: str | None = None,
) -> bool:
    """Download a resolved stream to disk.

    Before downloading, inspects the stream and selects the best
    matching quality. Logs available qualities for transparency.

    Parameters
    ----------
    stream : StreamInfo
        Stream URL and headers from resolve().
    media : Media
        The movie or TV show being downloaded.
    quality : str, optional
        Target quality (e.g. "1080p", "720p", "best").
        Defaults to the highest available.
    movies_dir : str, optional
        Override movies output directory.
    tv_dir : str, optional
        Override TV output directory.

    Returns
    -------
    bool
        True if download succeeded.

    Examples
    --------
    >>> from vidsrc_dlp import search_movie, resolve, download
    >>> movie = search_movie("Inception")[0]
    >>> stream = resolve(movie)
    >>> download(stream, movie)
    True
    """
    config = load_config(
        movies_dir=movies_dir,
        tv_dir=tv_dir,
        quality=quality,
    )
    downloader = VideoDownloader(config)
    summary = downloader.format_summary(stream)
    if summary:
        logger.info("Available qualities: %s", summary)
    return downloader.download(stream, media)
