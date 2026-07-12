from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Config:
    tmdb_api_key: str
    movies_dir: Path
    tv_dir: Path
    quality: str = "best"
    no_confirm: bool = False


def load_config(
    movies_dir: str | None = None,
    tv_dir: str | None = None,
    quality: str | None = None,
    no_confirm: bool = False,
) -> Config:
    load_dotenv()

    api_key = os.getenv("TMDB_API_KEY")
    if not api_key:
        raise ValueError(
            "TMDB_API_KEY not found in .env file. "
            "Create a .env file with TMDB_API_KEY=your_key_here"
        )

    default_downloads = Path.cwd() / "downloads"

    resolved_movies = (
        Path(movies_dir)
        if movies_dir
        else Path(os.getenv("MOVIES_DIR", str(default_downloads / "movies")))
    )
    resolved_tv = (
        Path(tv_dir)
        if tv_dir
        else Path(os.getenv("TV_DIR", str(default_downloads / "tv")))
    )

    return Config(
        tmdb_api_key=api_key,
        movies_dir=resolved_movies,
        tv_dir=resolved_tv,
        quality=quality or "best",
        no_confirm=no_confirm,
    )
