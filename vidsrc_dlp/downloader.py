from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from vidsrc_dlp.config import Config
from vidsrc_dlp.utils import Media, MediaType, StreamInfo

logger = logging.getLogger("vidsrc_dlp.downloader")


@dataclass
class VideoDownloader:
    config: Config

    def list_formats(self, stream: StreamInfo) -> list[dict]:
        """Inspect the stream and return available video formats.

        Each format dict contains keys like:
            format_id, height, width, vcodec, acodec, tbr, filesize,
            format_note (e.g. "1080p"), ext
        """
        quiet = not logger.isEnabledFor(logging.DEBUG)
        opts = {
            "quiet": True,
            "no_warnings": True,
            "http_headers": {
                "User-Agent": stream.headers.get(
                    "User-Agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                ),
                "Referer": stream.referer or stream.url,
            },
        }
        try:
            with yt_dlp.YoutubeDL({**opts, "quiet": quiet}) as ydl:
                info = ydl.extract_info(stream.url, download=False)
            formats = info.get("formats", []) if info else []
            videos = sorted(
                (f for f in formats if f.get("vcodec") and f.get("vcodec") != "none"),
                key=lambda f: f.get("height") or 0,
                reverse=True,
            )
            return videos
        except Exception as e:
            logger.debug("Format detection failed: %s", e)
            return []

    def format_summary(self, stream: StreamInfo | None) -> str | None:
        """Describe available qualities as a human-readable string."""
        if not stream:
            return None
        formats = self.list_formats(stream)
        if not formats:
            return None
        seen: set[int] = set()
        parts = []
        for f in formats:
            h = f.get("height")
            if h and h not in seen:
                seen.add(h)
                note = f.get("format_note") or f"{h}p"
                parts.append(note)
        return ", ".join(parts) if parts else None

    def download(self, stream: StreamInfo, media: Media) -> bool:
        if media.media_type == MediaType.TV:
            output_dir, filename = self._tv_path(media)
        else:
            output_dir, filename = self._movie_path(media)

        output_dir.mkdir(parents=True, exist_ok=True)
        output_template = str(output_dir / f"{filename}.%(ext)s")

        fmt = self._build_format_spec()
        logger.info("Format: %s", fmt)

        ydl_opts: dict = {
            "format": fmt,
            "outtmpl": output_template,
            "merge_output_format": "mp4",
            "http_headers": {
                "User-Agent": stream.headers.get(
                    "User-Agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                ),
                "Referer": stream.referer or stream.url,
            },
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                }
            ],
            "progress_hooks": [self._progress_hook],
            "quiet": True,
            "no_warnings": True,
            "extractor_retries": 3,
            "fragment_retries": 5,
            "retries": 5,
            "ignoreerrors": False,
        }

        try:
            logger.info("Downloading %s", filename)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([stream.url])
            logger.info("Download complete: %s", filename)
            return True
        except yt_dlp.utils.DownloadError as e:
            logger.error("Download failed: %s", e)
            return False
        except Exception as e:
            logger.error("Unexpected error: %s", e)
            return False

    def _movie_path(self, media: Media) -> tuple[Path, str]:
        label = self._media_label(media)
        folder = self.config.movies_dir / label
        return folder, label

    def _tv_path(self, media: Media) -> tuple[Path, str]:
        show_dir = self.config.tv_dir / self._safe_filename(media.title)
        season_dir = show_dir / f"Season {media.season or 1}"
        ep = media.episode or 1
        parts = [
            self._safe_filename(media.title),
            f"S{media.season or 1:02d}E{ep:02d}",
        ]
        if media.episode_title:
            parts.append(self._safe_filename(media.episode_title))
        filename = " - ".join(parts)
        return season_dir, filename

    def _media_label(self, media: Media) -> str:
        title = self._safe_filename(media.title)
        if media.year:
            return f"{title} ({media.year})"
        return title

    def _build_format_spec(self) -> str:
        quality = self.config.quality
        if quality == "best":
            return "bestvideo+bestaudio/best"
        try:
            height = quality.replace("p", "").strip()
            int(height)
            return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
        except ValueError:
            return "bestvideo+bestaudio/best"

    @staticmethod
    def _progress_hook(d: dict) -> None:
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            if total > 0:
                logger.info("Progress: %.1f%%", downloaded / total * 100)
        elif d["status"] == "finished":
            logger.info("Post-processing...")

    @staticmethod
    def _safe_filename(title: str) -> str:
        return "".join(c if c.isalnum() or c in " ._-" else "_" for c in title).strip()
