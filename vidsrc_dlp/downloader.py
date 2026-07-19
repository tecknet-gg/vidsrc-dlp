from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from vidsrc_dlp.config import Config
from vidsrc_dlp.utils import Media, MediaType, StreamInfo

logger = logging.getLogger("vidsrc_dlp.downloader")

MIN_MOVIE_BYTES = 100 * 1024 * 1024  # 100 MB
MIN_TV_BYTES = 10 * 1024 * 1024      # 10 MB


@dataclass
class VideoDownloader:
    config: Config

    def list_formats(self, stream: StreamInfo) -> list[dict]:
        return self._list_formats_for_url(stream.url, stream)

    def format_summary(self, stream: StreamInfo | None) -> str | None:
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

    def _estimate_total_bytes(self, stream: StreamInfo) -> int | None:
        target_url = self._pick_best_url(stream)
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
            with yt_dlp.YoutubeDL({**opts, "format": self._build_format_spec()}) as ydl:
                info = ydl.extract_info(target_url, download=False)
            return info.get("filesize_approx") or info.get("filesize")
        except Exception:
            return None

    def _check_quality_gate(
        self, stream: StreamInfo, media: Media
    ) -> bool:
        if not self.config.quality_gate:
            return True
        threshold = MIN_MOVIE_BYTES if media.media_type == MediaType.MOVIE else MIN_TV_BYTES
        estimated = self._estimate_total_bytes(stream)
        if estimated is None:
            logger.info("Quality gate: unable to estimate size, proceeding anyway")
            return True
        if estimated < threshold:
            logger.warning(
                "Quality gate: estimated size %.1f MB is below %.0f MB threshold — rejecting stream",
                estimated / 1024 / 1024, threshold / 1024 / 1024,
            )
            return False
        logger.info(
            "Quality gate: estimated size %.1f MB passes threshold", estimated / 1024 / 1024,
        )
        return True

    def _pick_best_url(self, stream: StreamInfo) -> str:
        urls = stream.urls or [stream.url]
        if len(urls) <= 1:
            return stream.url

        best_url = stream.url
        best_height = 0
        for url in urls:
            formats = self._list_formats_for_url(url, stream)
            max_h = max(
                (f.get("height") or 0 for f in formats if f.get("height")),
                default=0,
            )
            if max_h > best_height:
                best_height = max_h
                best_url = url
        if best_url != stream.url:
            logger.info(
                "Selected best quality URL (%dp) over default", best_height
            )
        return best_url

    def _list_formats_for_url(
        self, url: str, stream: StreamInfo
    ) -> list[dict]:
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
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            formats = info.get("formats", []) if info else []
            return [
                f
                for f in formats
                if f.get("vcodec") and f.get("vcodec") != "none"
            ]
        except Exception as e:
            logger.debug("Format detection failed for %s: %s", url[:80], e)
            return []

    def download(self, stream: StreamInfo, media: Media) -> bool:
        if media.media_type == MediaType.TV:
            output_dir, filename = self._tv_path(media)
        else:
            output_dir, filename = self._movie_path(media)

        output_dir.mkdir(parents=True, exist_ok=True)
        output_template = str(output_dir / f"{filename}.%(ext)s")

        if not self._check_quality_gate(stream, media):
            return False

        target_url = self._pick_best_url(stream)
        logger.info("Download URL: %s...", target_url[:100])

        fmt = self._build_format_spec()
        logger.info("Format: %s", fmt)

        ydl_opts: dict = {
            "format": fmt,
            "outtmpl": output_template,
            "merge_output_format": "mp4",
            "concurrent_fragments": self.config.concurrent_fragments,
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
                ydl.download([target_url])
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
