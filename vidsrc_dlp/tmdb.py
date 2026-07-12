from __future__ import annotations

import logging

import requests

from vidsrc_dlp.utils import Media, MediaType

logger = logging.getLogger("vidsrc_dlp.tmdb")


class TMDBClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.themoviedb.org/3"

    def search_movie(self, query: str, year: int | None = None) -> list[Media]:
        params = {"api_key": self.api_key, "query": query.strip(), "language": "en-US"}
        if year:
            params["primary_release_year"] = year
        return self._search("search/movie", params, MediaType.MOVIE)

    def search_tv(self, query: str, year: int | None = None) -> list[Media]:
        params = {"api_key": self.api_key, "query": query.strip(), "language": "en-US"}
        if year:
            params["first_air_date_year"] = year
        return self._search("search/tv", params, MediaType.TV)

    def _search(
        self, endpoint: str, params: dict, media_type: MediaType
    ) -> list[Media]:
        try:
            r = requests.get(
                f"{self.base_url}/{endpoint}", params=params, timeout=10
            )
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            logger.error("TMDB search request failed: %s", e)
            return []

        results = data.get("results", [])
        if not results:
            logger.info("No TMDB results for %s", params.get("query"))
            return []

        items = []
        for m in results:
            date_field = "release_date" if media_type == MediaType.MOVIE else "first_air_date"
            raw_date = m.get(date_field, "") or ""
            year_val = _parse_year(raw_date)
            items.append(
                Media(
                    id=m["id"],
                    title=m.get("title" if media_type == MediaType.MOVIE else "name", "Unknown"),
                    media_type=media_type,
                    year=year_val,
                    release_date=raw_date,
                    overview=m.get("overview", "") or "",
                    vote_average=m.get("vote_average"),
                    poster_path=m.get("poster_path"),
                )
            )

        logger.info(
            "TMDB %s search for '%s': %d results",
            media_type.value,
            params.get("query"),
            len(items),
        )
        return items

    def get_movie_details(self, tmdb_id: int) -> Media | None:
        return self._get_details(f"movie/{tmdb_id}", MediaType.MOVIE)

    def get_tv_details(self, tmdb_id: int) -> Media | None:
        return self._get_details(f"tv/{tmdb_id}", MediaType.TV)

    def _get_details(self, endpoint: str, media_type: MediaType) -> Media | None:
        params = {"api_key": self.api_key, "language": "en-US"}
        try:
            r = requests.get(
                f"{self.base_url}/{endpoint}", params=params, timeout=10
            )
            r.raise_for_status()
            d = r.json()
        except requests.RequestException as e:
            logger.error("TMDB detail request failed: %s", e)
            return None

        date_field = "release_date" if media_type == MediaType.MOVIE else "first_air_date"
        raw_date = d.get(date_field, "") or ""
        year_val = _parse_year(raw_date)
        title_key = "title" if media_type == MediaType.MOVIE else "name"

        genre_names = [g["name"] for g in d.get("genres", []) if "name" in g]

        media = Media(
            id=d["id"],
            title=d.get(title_key, "Unknown"),
            media_type=media_type,
            year=year_val,
            release_date=raw_date,
            overview=d.get("overview", "") or "",
            genres=genre_names,
            vote_average=d.get("vote_average"),
            poster_path=d.get("poster_path"),
        )

        # Fetch external IDs for IMDb
        try:
            ext = requests.get(
                f"{self.base_url}/{endpoint}/external_ids",
                params={"api_key": self.api_key},
                timeout=10,
            ).json()
            media.imdb_id = ext.get("imdb_id")
        except requests.RequestException:
            pass

        return media

    def get_episode_title(self, tmdb_id: int, season: int, episode: int) -> str | None:
        try:
            r = requests.get(
                f"{self.base_url}/tv/{tmdb_id}/season/{season}/episode/{episode}",
                params={"api_key": self.api_key, "language": "en-US"},
                timeout=10,
            )
            r.raise_for_status()
            return r.json().get("name")
        except requests.RequestException as e:
            logger.debug("Could not fetch episode title: %s", e)
            return None


def _parse_year(date_str: str) -> int | None:
    if date_str and len(date_str) >= 4:
        try:
            return int(date_str[:4])
        except ValueError:
            return None
    return None
