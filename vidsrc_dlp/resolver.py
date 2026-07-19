from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import requests

from vidsrc_dlp.utils import StreamInfo, StreamProvider

logger = logging.getLogger("vidsrc_dlp.resolver")


def _run_async(coro):
    """Run an async coroutine, handling both no-loop and running-loop contexts."""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class VidSrcResolver(StreamProvider):
    base_domain: str = "vidsrc.to"
    request_delay: float = 0.3
    timeout: int = 30

    def resolve(
        self,
        tmdb_id: int,
        media_type: str = "movie",
        season: int | None = None,
        episode: int | None = None,
    ) -> StreamInfo | None:
        logger.info(
            "Resolving TMDB ID %d (%s) via %s", tmdb_id, media_type, self.base_domain
        )
        try:
            return self._resolve_domain(self.base_domain, tmdb_id, media_type, season, episode)
        except Exception as e:
            logger.error("Resolution failed: %s", e)
            return None

    def _resolve_domain(
        self,
        domain: str,
        tmdb_id: int,
        media_type: str,
        season: int | None,
        episode: int | None,
    ) -> StreamInfo | None:
        session = requests.Session()
        session.headers.update(HEADERS)

        if media_type == "tv" and season is not None and episode is not None:
            embed_url = f"https://{domain}/embed/tv/{tmdb_id}/{season}/{episode}"
        else:
            embed_url = f"https://{domain}/embed/movie/{tmdb_id}"

        r1 = session.get(embed_url, timeout=15)
        vsembed_url = self._extract_vsembed(r1.text, embed_url)
        if not vsembed_url:
            logger.error("No vsembed iframe found on %s", embed_url)
            return None

        r2 = session.get(vsembed_url, headers={"Referer": embed_url}, timeout=15)
        hashes = re.findall(r'data-hash=["\']([A-Za-z0-9+/=_-]+)["\']', r2.text)
        if not hashes:
            logger.error("No data-hash found on vsembed")
            return None
        logger.info("Step 2: %d source hash(es) found", len(hashes))

        for i, h in enumerate(hashes):
            result = self._resolve_source(session, h, vsembed_url)
            if result:
                return result

        logger.error("All %d sources failed", len(hashes))
        return None

    def _resolve_source(
        self, session: requests.Session, rcp_hash: str, referer: str
    ) -> StreamInfo | None:
        rcp_url = f"https://cloudorchestranova.com/rcp/{rcp_hash}"
        r3 = session.get(rcp_url, headers={"Referer": referer}, timeout=self.timeout)
        if r3.status_code != 200:
            logger.warning("RCP request failed: %d", r3.status_code)
            return None

        prorcp = re.search(r"prorcp/([A-Za-z0-9+/=_-]+)", r3.text)
        if not prorcp:
            logger.warning("No prorcp hash in RCP response")
            return None

        prorcp_url = f"https://cloudorchestranova.com/prorcp/{prorcp.group(1)}"
        r4 = session.get(
            prorcp_url, headers={"Referer": rcp_url}, timeout=self.timeout
        )
        if r4.status_code != 200:
            logger.warning("prorcp request failed: %d", r4.status_code)
            return None

        m3u8_urls = re.findall(r"https?://[^\"' ]+\.m3u8[^\"' ]*", r4.text)
        if not m3u8_urls:
            logger.warning("No m3u8 URLs in prorcp response")
            return None

        token_main = self._fetch_token(
            session, "https://peregrinepalaver.space/generate.php", rcp_url
        )
        token_pg = self._fetch_token(
            session, "https://app2.putgate.com/generate.php", rcp_url
        )

        valid_urls: list[str] = []
        for raw_url in m3u8_urls:
            resolved = raw_url
            if token_main:
                resolved = resolved.replace("__TOKEN__", token_main)
            if token_pg:
                resolved = resolved.replace("__TOKENPG__", token_pg)
            if "{v" not in resolved:
                valid_urls.append(resolved)

        if not valid_urls:
            logger.warning("All m3u8 URLs had unresolved placeholders")
            return None

        return StreamInfo(
            url=valid_urls[0],
            urls=valid_urls,
            headers={"User-Agent": HEADERS["User-Agent"]},
            referer="https://cloudorchestranova.com/",
            stream_type="hls",
        )

    @staticmethod
    def _extract_vsembed(html: str, page_url: str) -> str | None:
        src = re.search(r'src=["\']([^"\']*vsembed[^"\']*)["\']', html)
        if not src:
            src = re.search(r'src=["\']([^"\']*embed[^"\']*)["\']', html)
        if not src:
            return None
        url = src.group(1)
        if url.startswith("//"):
            url = "https:" + url
        return url

    @staticmethod
    def _fetch_token(
        session: requests.Session, url: str, referer: str
    ) -> str | None:
        try:
            r = session.get(url, headers={"Referer": referer}, timeout=10)
            if r.status_code == 200 and r.text.strip():
                return r.text.strip()
        except requests.RequestException:
            pass
        return None


@dataclass
class MappleResolver(StreamProvider):
    api_key: str = "mptv_sk_a8f29c4e7b3d1f"
    api_base: str = "https://mapple.rip/api"
    timeout: int = 15

    def resolve(
        self,
        tmdb_id: int,
        media_type: str = "movie",
        season: int | None = None,
        episode: int | None = None,
    ) -> StreamInfo | None:
        logger.info("Resolving TMDB ID %d (%s) via Mapple (4KHD)", tmdb_id, media_type)
        try:
            params = {
                "mediaId": tmdb_id,
                "mediaType": media_type,
                "source": "mapple",
                "apikey": self.api_key,
            }
            if media_type == "tv":
                params["season"] = season or 1
                params["episode"] = episode or 1

            r = requests.get(
                f"{self.api_base}/stream",
                params=params,
                headers={
                    "User-Agent": HEADERS["User-Agent"],
                    "Referer": f"https://mapple.uk/watch/{media_type}/{tmdb_id}",
                    "Origin": "https://mapple.uk",
                },
                timeout=self.timeout,
            )
            if r.status_code != 200:
                logger.warning("Mapple API returned %d", r.status_code)
                return None

            data = r.json()
            if not data.get("success"):
                logger.warning("Mapple API returned unsuccessful: %s", data)
                return None

            stream_url = data["data"]["stream_url"]
            logger.info("Mapple resolved stream (1080p available)")

            return StreamInfo(
                url=stream_url,
                headers={"User-Agent": HEADERS["User-Agent"]},
                referer="https://mapple.uk/",
                stream_type="hls",
            )
        except Exception as e:
            logger.error("Mapple resolution failed: %s", e)
            return None


@dataclass
class CinebyResolver(StreamProvider):
    base_url: str = "https://www.cineby.at/movie"
    timeout: int = 60
    stream_domains: tuple = ("moon.ironwallnet.net", "checknews02.site", "randomseg01.site")

    def resolve(
        self,
        tmdb_id: int,
        media_type: str = "movie",
        season: int | None = None,
        episode: int | None = None,
    ) -> StreamInfo | None:
        logger.info("Resolving TMDB ID %d (%s) via Cineby (4K)", tmdb_id, media_type)
        if media_type != "movie":
            logger.info("Cineby only supports movies, skipping")
            return None
        try:
            return self._resolve_with_playwright(tmdb_id)
        except ImportError:
            logger.warning("Playwright not installed. Install with: pip install playwright && playwright install chromium")
            return None
        except Exception as e:
            logger.error("Cineby resolution failed: %s", e)
            return None

    def _resolve_with_playwright(self, tmdb_id: int) -> StreamInfo | None:
        from playwright.async_api import async_playwright
        import asyncio

        url = f"{self.base_url}/{tmdb_id}?play=true"

        async def _capture(retries: int = 2) -> list[str]:
            from playwright.async_api import async_playwright as _async_pw

            stealth = None
            try:
                from playwright_stealth import stealth_async
                stealth = stealth_async
            except ImportError:
                pass

            for attempt in range(retries):
                async with _async_pw() as pw:
                    browser = await pw.chromium.launch(
                        headless=True,
                        args=[
                            "--no-sandbox",
                            "--disable-setuid-sandbox",
                            "--disable-dev-shm-usage",
                            "--disable-gpu",
                        ],
                    )
                    context = await browser.new_context(
                        viewport={"width": 1920, "height": 1080},
                        locale="en-US",
                        user_agent=HEADERS["User-Agent"],
                    )
                    page = await context.new_page()

                    if stealth:
                        await stealth(page)

                    m3u8_urls: list[str] = []

                    async def on_response(response):
                        ct = response.headers.get("content-type", "")
                        if "application/vnd.apple.mpegurl" in ct:
                            m3u8_urls.append(response.url)

                    page.on("response", on_response)

                    try:
                        await page.goto(url, wait_until="networkidle", timeout=self.timeout * 1000)
                    except Exception:
                        await asyncio.sleep(15)

                    await asyncio.sleep(10)

                    # Check for Cloudflare challenge page
                    try:
                        cf = await page.query_selector("#cf-wrapper")
                        if cf:
                            logger.warning(
                                "Cineby attempt %d/%d: Cloudflare challenge detected",
                                attempt + 1, retries,
                            )
                            await browser.close()
                            continue
                    except Exception:
                        pass

                    await browser.close()

                    if m3u8_urls:
                        return m3u8_urls
                    logger.debug(
                        "Cineby attempt %d/%d: no m3u8s, retrying",
                        attempt + 1, retries,
                    )
            return []

        m3u8_urls = _run_async(_capture())

        if not m3u8_urls:
            logger.warning("No m3u8 streams captured from Cineby")
            return None

        logger.info("Cineby resolved 4K stream")

        return StreamInfo(
            url=m3u8_urls[0],
            headers={"User-Agent": HEADERS["User-Agent"]},
            referer="https://www.cineby.at/",
            stream_type="hls",
        )


@dataclass
class MultiDomainResolver(StreamProvider):
    domains: list[str] = field(default_factory=lambda: [
        "vidsrc.su",
        "vidsrc.to",
        "vidsrcme.ru",
    ])
    request_delay: float = 0.3
    timeout: int = 30

    def _accept(self, stream: StreamInfo | None) -> bool:
        if stream is None:
            return False
        try:
            import yt_dlp
            opts = {
                "quiet": True,
                "no_warnings": True,
                "http_headers": {
                    "User-Agent": stream.headers.get("User-Agent", HEADERS["User-Agent"]),
                    "Referer": stream.referer or stream.url,
                },
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(stream.url, download=False)
            total = info.get("filesize_approx") or info.get("filesize") or 0
            duration = info.get("duration") or 0
            heights = sorted(set(
                f.get("height") for f in info.get("formats") or []
                if f.get("height")
            ))
            max_height = heights[-1] if heights else 0
            frag_count = len(info.get("fragments") or info.get("requested_formats") or [])
            if duration > 0 and duration < 900 and max_height < 720:
                logger.warning(
                    "Rejecting stream: only %ds duration (expected >15min) at max %dp",
                    duration, max_height,
                )
                return False
            if total > 0 and total < 50 * 1024 * 1024 and not heights:
                logger.warning(
                    "Rejecting stream: only %.0f MB estimated with no resolvable heights",
                    total / 1024 / 1024,
                )
                return False
            if max_height < 240 and total > 0 and total < 50 * 1024 * 1024:
                logger.warning(
                    "Rejecting stream: max height %dp with only %.0f MB",
                    max_height, total / 1024 / 1024,
                )
                return False
            logger.debug(
                "Stream check: max %dp, %d fragments, %.0f MB, %ds duration",
                max_height, frag_count, total / 1024 / 1024, duration,
            )
        except Exception as e:
            logger.debug("Quality check skipped: %s", e)
        return True

    def resolve(
        self,
        tmdb_id: int,
        media_type: str = "movie",
        season: int | None = None,
        episode: int | None = None,
    ) -> StreamInfo | None:
        logger.info(
            "Resolving TMDB ID %d (%s) with auto provider", tmdb_id, media_type,
        )

        cineby = CinebyResolver()
        result = cineby.resolve(tmdb_id, media_type, season, episode)
        if self._accept(result):
            return result
        logger.info("Cineby unavailable, falling back to vidsrc domains")

        logger.info("Trying %d vidsrc domains: %s", len(self.domains), self.domains)
        base = VidSrcResolver(request_delay=self.request_delay, timeout=self.timeout)
        for domain in self.domains:
            logger.info("Trying domain: %s", domain)
            try:
                result = base._resolve_domain(domain, tmdb_id, media_type, season, episode)
                if self._accept(result):
                    logger.info("Resolved stream from %s", domain)
                    return result
            except Exception as e:
                logger.debug("Domain %s failed: %s", domain, e)
        logger.error("All resolution attempts failed")
        return None
