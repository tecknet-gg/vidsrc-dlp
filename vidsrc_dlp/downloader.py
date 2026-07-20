from __future__ import annotations

import logging
import os
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from vidsrc_dlp.config import Config
from vidsrc_dlp.utils import Media, MediaType, StreamInfo

logger = logging.getLogger("vidsrc_dlp.downloader")

MIN_MOVIE_BYTES = 100 * 1024 * 1024  # 100 MB
MIN_TV_BYTES = 10 * 1024 * 1024      # 10 MB

STALL_BACKOFF_SLEEP = 45
INITIAL_WORKERS = 8
PURGE_IDLE_THRESHOLD = 600  # 10 minutes of cumulative idle before purge
SCALE_UP_SPEED = 1_000_000  # 1 MB/s sustained triggers worker scale-up
SCALE_UP_WINDOW = 6          # number of speed samples (~90s) to check


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

    def _purge_partial_files(self, directory: Path, filename: str) -> None:
        """Delete partial download artifacts for a given filename."""
        for p in directory.iterdir():
            if p.name.startswith(filename):
                logger.warning("Purging partial file: %s", p.name)
                p.unlink(missing_ok=True)

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

        # Shared state between hook, monitor thread, and main loop
        class DownloadState:
            def __init__(self):
                self.speed_history: list[float] = []
                self.lock = threading.Lock()
                self.total_bytes: float = 0
                self.downloaded: float = 0

            def record(self, speed: float, total: float, downloaded: float) -> None:
                with self.lock:
                    self.total_bytes = total
                    self.downloaded = downloaded
                    if speed > 0:
                        self.speed_history.append(speed)
                        if len(self.speed_history) > 30:
                            self.speed_history.pop(0)

            def recent_avg(self, n: int = SCALE_UP_WINDOW) -> float:
                with self.lock:
                    samples = self.speed_history[-n:]
                    return sum(samples) / len(samples) if samples else 0.0

        state = DownloadState()

        def _make_hook(workers: int):
            last_tick = [time.time()]
            last_bytes = [0.0]

            def _hook(d: dict) -> None:
                if d["status"] == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                    downloaded = d.get("downloaded_bytes", 0)
                    speed = d.get("speed", 0) or 0

                    state.record(speed, total, downloaded)

                    if total > 0 and speed > 0:
                        pct = downloaded / total * 100
                        rate = speed / 1024 / 1024
                        logger.info(
                            "Progress: %.1f%% at %.1f MB/s (%d workers)",
                            pct, rate, workers,
                        )
                    elif total > 0:
                        pct = downloaded / total * 100
                        logger.info("Progress: %.1f%% at 0 B/s (%d workers)", pct, workers)

                    now = time.time()
                    delta = downloaded - last_bytes[0]
                    elapsed = now - last_tick[0]
                    if elapsed >= 10 and delta < 1024 * 1024:
                        logger.debug("Less than 1 MB downloaded in last 10s")
                    last_tick[0] = now
                    last_bytes[0] = downloaded

                    self._progress_hook(d)

            return _hook

        ydl_base_opts: dict = {
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
            "quiet": True,
            "no_warnings": True,
            "extractor_retries": 3,
            "fragment_retries": 10,
            "retries": 10,
            "throttledratelimit": 51200,
            "throttled_rerate": 45,
            "ignoreerrors": False,
            "skip_unavailable_fragments": False,
        }

        cumulative_idle = 0.0
        scale_up_flag = threading.Event()

        def _scale_up_monitor(workers: list[int]) -> None:
            while not scale_up_flag.is_set():
                time.sleep(15)
                if workers[0] >= INITIAL_WORKERS:
                    continue
                avg = state.recent_avg(SCALE_UP_WINDOW)
                if avg > SCALE_UP_SPEED:
                    logger.info(
                        "Throughput recovered (%.1f MB/s avg at %d workers) — scaling up",
                        avg / 1024 / 1024, workers[0],
                    )
                    scale_up_flag.set()
                    os.kill(os.getpid(), signal.SIGTERM)
                    return

        current_workers: list[int] = [INITIAL_WORKERS]

        while True:
            workers = current_workers[0]
            ydl_opts = {**ydl_base_opts, "concurrent_fragments": workers}
            ydl_opts["progress_hooks"] = [_make_hook(workers)]

            scale_up_flag.clear()
            monitor = threading.Thread(
                target=_scale_up_monitor, args=(current_workers,), daemon=True,
            )
            monitor.start()

            try:
                logger.info(
                    "Downloading %s with %d concurrent fragments "
                    "(idle=%.0fs, purge at %.0fs)",
                    filename, workers, cumulative_idle, PURGE_IDLE_THRESHOLD,
                )
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([target_url])
                logger.info("Download complete: %s", filename)
                return True
            except yt_dlp.utils.DownloadError as e:
                msg = str(e).lower()
                if scale_up_flag.is_set():
                    new_workers = min(INITIAL_WORKERS, workers * 2)
                    logger.info("Scaling up workers: %d → %d", workers, new_workers)
                    current_workers[0] = new_workers
                    continue

                if "throttl" in msg or "too slow" in msg:
                    cumulative_idle += STALL_BACKOFF_SLEEP
                    new_workers = max(1, workers // 2)
                    logger.warning(
                        "Throttled at %d workers (idle=%.0fs). "
                        "Sleeping %ds, reducing to %d workers",
                        workers, cumulative_idle, STALL_BACKOFF_SLEEP, new_workers,
                    )
                    current_workers[0] = new_workers
                    backoff = STALL_BACKOFF_SLEEP
                else:
                    cumulative_idle += 10
                    logger.warning(
                        "Download error at %d workers (idle=%.0fs): %s. "
                        "Retrying in 10s",
                        workers, cumulative_idle, e,
                    )
                    backoff = 10

                if cumulative_idle >= PURGE_IDLE_THRESHOLD:
                    logger.warning(
                        "Cumulative idle %.0fs exceeds %ds — "
                        "purging partial files and restarting fresh",
                        cumulative_idle, PURGE_IDLE_THRESHOLD,
                    )
                    self._purge_partial_files(output_dir, filename)
                    cumulative_idle = 0.0
                    current_workers[0] = INITIAL_WORKERS

                time.sleep(backoff)
                continue
            except Exception as e:
                logger.error("Unexpected error: %s", e)
                return False
            finally:
                scale_up_flag.set()

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
