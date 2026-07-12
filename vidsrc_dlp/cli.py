from __future__ import annotations

import argparse
import logging

from vidsrc_dlp.config import load_config
from vidsrc_dlp.downloader import VideoDownloader
from vidsrc_dlp.resolver import VidSrcResolver
from vidsrc_dlp.tmdb import TMDBClient
from vidsrc_dlp.utils import MediaType, setup_logging

logger = logging.getLogger("vidsrc_dlp.cli")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="vidsrc-dlp",
        description="Search TMDB, resolve stream, and download movies & TV shows.",
    )
    parser.add_argument("query", nargs="?", help="Movie or TV show title to search for")
    parser.add_argument("--type", choices=["movie", "tv"], default="movie", help="Media type")
    parser.add_argument("--season", type=int, help="Season number (for TV)")
    parser.add_argument("--episode", type=int, help="Episode number (for TV)")
    parser.add_argument("--year", type=int, help="Filter by release year")
    parser.add_argument("--quality", default="best", help="Quality (e.g. 1080p, 720p, best)")
    parser.add_argument("--movies-dir", help="Override movies output directory")
    parser.add_argument("--tv-dir", help="Override TV output directory")
    parser.add_argument("--no-confirm", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--verbose-ytdlp", action="store_true", help="Show yt-dlp output")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if args.query is None:
        print("usage: vidsrc-dlp <title> [options]")
        print()
        print("Movies:")
        print('  vidsrc-dlp "Inception"')
        print('  vidsrc-dlp "The Matrix" --year 1999')
        print()
        print("TV:")
        print('  vidsrc-dlp "Breaking Bad" --type tv --season 1 --episode 1')
        raise SystemExit(1)

    setup_logging(args.verbose)

    if args.type == "tv":
        if args.season is None or args.episode is None:
            logger.error("--season and --episode are required for TV shows")
            raise SystemExit(1)

    config = load_config(
        movies_dir=args.movies_dir,
        tv_dir=args.tv_dir,
        quality=args.quality,
        no_confirm=args.no_confirm,
    )

    tmdb = TMDBClient(api_key=config.tmdb_api_key)
    media_type = MediaType.TV if args.type == "tv" else MediaType.MOVIE
    search_fn = tmdb.search_tv if args.type == "tv" else tmdb.search_movie
    detail_fn = tmdb.get_tv_details if args.type == "tv" else tmdb.get_movie_details

    results = search_fn(args.query, year=args.year)
    if not results:
        logger.error("No %s found for: %s", args.type, args.query)
        raise SystemExit(1)

    best = results[0]
    logger.info("Found: %s", _format_media_info(best))

    # Enrich with full details
    detailed = detail_fn(best.id)
    if detailed:
        best = detailed

    # Fetch episode title for TV
    if args.type == "tv":
        ep_title = tmdb.get_episode_title(best.id, args.season, args.episode)
        best.season = args.season
        best.episode = args.episode
        best.episode_title = ep_title if ep_title else None
        best.media_type = MediaType.TV

    logger.info(
        "Metadata: %s",
        _format_metadata(best),
    )

    if not args.no_confirm:
        label = (
            f"{best.title} S{best.season:02d}E{best.episode:02d}"
            if best.media_type == MediaType.TV
            else best.title
        )
        answer = input(f"Download '{label}'? [Y/n] ").strip().lower()
        if answer and answer not in ("y", "yes", ""):
            logger.info("Skipped.")
            return

    resolver = VidSrcResolver()
    stream = resolver.resolve(
        best.id,
        media_type=args.type,
        season=best.season,
        episode=best.episode,
    )
    if stream is None:
        logger.error("No stream available for %s", best.title)
        raise SystemExit(1)

    logger.info("Stream URL: %s", stream.url[:100])

    downloader = VideoDownloader(config)

    summary = downloader.format_summary(stream)
    if summary:
        logger.info("Available qualities: %s", summary)
    else:
        logger.info("Available qualities: detecting...")

    success = downloader.download(stream, best)
    raise SystemExit(0 if success else 1)


def _format_media_info(media) -> str:
    parts = [media.title]
    if media.year:
        parts.append(f"({media.year})")
    return " ".join(parts)


def _format_metadata(media) -> str:
    parts = []
    if media.imdb_id:
        parts.append(f"IMDb: {media.imdb_id}")
    if media.vote_average:
        parts.append(f"Rating: {media.vote_average:.1f}/10")
    if media.year:
        parts.append(f"Year: {media.year}")
    if media.genres:
        parts.append(f"Genres: {', '.join(media.genres[:3])}")
    if media.media_type == MediaType.TV and media.episode_title:
        parts.append(f"Episode: {media.episode_title}")
    return " | ".join(parts)
