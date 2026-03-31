"""
Base Azure DevOps REST API client.
Handles authentication, pagination, and rate-limit retries.
"""
import base64
import time
import logging
from typing import Any, Generator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

logger = logging.getLogger(__name__)

API_VERSION = "7.1"


def _build_session(pat: str) -> requests.Session:
    """Return a requests Session authenticated with a PAT token."""
    token = base64.b64encode(f":{pat}".encode()).decode()
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session


_session: requests.Session | None = None


def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = _build_session(config.ADO_PAT)
    return _session


def get(endpoint: str, params: dict | None = None) -> Any:
    """
    GET a single ADO REST endpoint.
    `endpoint` is either a full URL or a path relative to ADO_BASE_URL.
    """
    url = endpoint if endpoint.startswith("http") else f"{config.ADO_BASE_URL}/{endpoint}"
    p = {"api-version": API_VERSION}
    if params:
        p.update(params)

    response = get_session().get(url, params=p)
    if response.status_code == 429:
        retry_after = int(response.headers.get("Retry-After", 10))
        logger.warning("Rate limited — sleeping %d s", retry_after)
        time.sleep(retry_after)
        response = get_session().get(url, params=p)

    response.raise_for_status()
    return response.json()


def get_paged(endpoint: str, params: dict | None = None, top: int = 200) -> Generator[dict, None, None]:
    """
    Yield all items from a paged ADO list endpoint (continuationToken style).
    """
    url = endpoint if endpoint.startswith("http") else f"{config.ADO_BASE_URL}/{endpoint}"
    p = {"api-version": API_VERSION, "$top": top}
    if params:
        p.update(params)

    while True:
        response = get_session().get(url, params=p)
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 10))
            logger.warning("Rate limited — sleeping %d s", retry_after)
            time.sleep(retry_after)
            continue

        response.raise_for_status()
        data = response.json()
        items = data.get("value", [])
        yield from items

        token = response.headers.get("x-ms-continuationtoken")
        if not token or not items:
            break
        p["continuationToken"] = token
