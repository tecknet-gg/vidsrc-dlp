import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class MediaType(Enum):
    MOVIE = "movie"
    TV = "tv"


@dataclass
class Media:
    id: int
    title: str
    media_type: MediaType = MediaType.MOVIE
    year: int | None = None
    release_date: str = ""
    overview: str = ""
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None
    imdb_id: str | None = None
    genres: list[str] = field(default_factory=list)
    vote_average: float | None = None
    poster_path: str | None = None


@dataclass
class StreamInfo:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    referer: str = ""
    stream_type: str = "hls"
    urls: list[str] = field(default_factory=list)
    trusted: bool = False


class StreamProvider(ABC):
    @abstractmethod
    def resolve(
        self,
        tmdb_id: int,
        media_type: str = "movie",
        season: int | None = None,
        episode: int | None = None,
    ) -> StreamInfo | None:
        ...


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger("vidsrc_dlp")
    root.setLevel(level)
    root.addHandler(handler)
