"""The project's public home: its repository, and how many stars it has.

The server asks GitHub at most once an hour and shares the answer, so
visitors' browsers never contact GitHub and the page works when GitHub
cannot be reached (the count is then simply left out).
"""

import logging
import threading
import time

import httpx

log = logging.getLogger(__name__)

FRESH_SECONDS = 3600  # how long a star count is kept
RETRY_SECONDS = 600  # after a failed lookup, wait this long before asking again

_lock = threading.Lock()
_cache: dict[str, tuple[float, int | None]] = {}  # repository -> (when to ask again, stars)


def repository_url(repository: str) -> str:
    return f"https://github.com/{repository}"


def stars(repository: str) -> int | None:
    """The repository's star count, or None when it is not known."""
    with _lock:
        due, known = _cache.get(repository, (0.0, None))
        if time.monotonic() < due:
            return known
        _cache[repository] = (time.monotonic() + RETRY_SECONDS, known)  # one lookup at a time
    count = _fetch(repository)
    with _lock:
        _cache[repository] = (time.monotonic() + (FRESH_SECONDS if count is not None else RETRY_SECONDS), count)
    return count


def _fetch(repository: str) -> int | None:
    try:
        response = httpx.get(
            f"https://api.github.com/repos/{repository}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "imagine-reader"},
            timeout=3,
        )
        if response.status_code != 200:
            log.info("GitHub answered %s for %s", response.status_code, repository)
            return None
        return int(response.json()["stargazers_count"])
    except (httpx.HTTPError, KeyError, ValueError) as error:
        log.info("could not read the star count of %s: %s", repository, type(error).__name__)
        return None
