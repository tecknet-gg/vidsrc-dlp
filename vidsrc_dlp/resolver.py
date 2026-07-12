from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import requests

from vidsrc_dlp.utils import StreamInfo, StreamProvider

logger = logging.getLogger("vidsrc_dlp.resolver")

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
            return self._resolve(tmdb_id, media_type, season, episode)
        except Exception as e:
            logger.error("Resolution failed: %s", e)
            return None

    def _resolve(
        self,
        tmdb_id: int,
        media_type: str,
        season: int | None,
        episode: int | None,
    ) -> StreamInfo | None:
        session = requests.Session()
        session.headers.update(HEADERS)

        if media_type == "tv" and season is not None and episode is not None:
            embed_url = (
                f"https://{self.base_domain}/embed/tv/{tmdb_id}/{season}/{episode}"
            )
        else:
            embed_url = f"https://{self.base_domain}/embed/movie/{tmdb_id}"

        r1 = session.get(embed_url, timeout=15)
        vsembed_url = self._extract_vsembed(r1.text, embed_url)
        if not vsembed_url:
            logger.error("No vsembed iframe found on %s", embed_url)
            return None
        logger.info("Step 1: %s", vsembed_url)
        time.sleep(self.request_delay)

        r2 = session.get(vsembed_url, headers={"Referer": embed_url}, timeout=15)
        hashes = re.findall(r'data-hash=["\']([A-Za-z0-9+/=_-]+)["\']', r2.text)
        if not hashes:
            logger.error("No data-hash found on vsembed")
            return None
        logger.info("Step 2: %d source hash(es) found", len(hashes))
        time.sleep(self.request_delay)

        for i, h in enumerate(hashes):
            logger.debug("Trying source %d/%d", i + 1, len(hashes))
            result = self._resolve_source(session, h, vsembed_url)
            if result:
                logger.info("Stream resolved from source %d", i + 1)
                return result
            time.sleep(self.request_delay)

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
        logger.debug("Step 3: prorcp hash found")
        time.sleep(self.request_delay)

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
        logger.debug("Step 4: %d raw m3u8 URL(s) found", len(m3u8_urls))
        time.sleep(self.request_delay)

        token_main = self._fetch_token(
            session, "https://peregrinepalaver.space/generate.php", rcp_url
        )
        token_pg = self._fetch_token(
            session, "https://app2.putgate.com/generate.php", rcp_url
        )
        logger.debug("Step 5: tokens resolved")

        for raw_url in m3u8_urls:
            resolved = raw_url
            if token_main:
                resolved = resolved.replace("__TOKEN__", token_main)
            if token_pg:
                resolved = resolved.replace("__TOKENPG__", token_pg)
            if "{v" not in resolved:
                return StreamInfo(
                    url=resolved,
                    headers={"User-Agent": HEADERS["User-Agent"]},
                    referer="https://cloudorchestranova.com/",
                    stream_type="hls",
                )

        logger.warning("All m3u8 URLs had unresolved placeholders")
        return None

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
