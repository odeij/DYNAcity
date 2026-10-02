"""Same-origin proxy for Google Photorealistic 3D Tiles, keeping the Maps key on the server.

The photorealistic basemap (`dynacity serve --basemap google`) streams Google's
3D Tiles into the viewer. Called directly from the browser, those requests
would carry the Maps API key in every URL and header, where anyone with the
page can copy it. Instead the page asks this server for
`/v1/3dtiles/root.json`; the server adds the key and forwards the request to
`tile.googleapis.com`. Google's tileset links are absolute paths
(`/v1/3dtiles/datasets/…?session=…`), so the loader resolves every child
request back to this same origin without any rewriting.

The proxy forwards only `/v1/3dtiles/…` paths to one fixed host, so it cannot
be pointed anywhere else. Google bills per root-tileset request (each starts a
multi-hour session), so root requests count against a `RequestBudget`.

Use is subject to Google's Map Tiles API terms: the page shows Google's logo
text and the data credits carried in the tiles, and nothing is cached here.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .limits import RequestBudget

TILES_HOST = "https://tile.googleapis.com"
ROOT_PATH = "v1/3dtiles/root.json"
KEY_VARIABLE = "GOOGLE_MAPS_API_KEY"
_SAFE_PATH = re.compile(r"v1/3dtiles/[A-Za-z0-9_\-./]+")


class TilesError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class TileResponse:
    body: bytes
    content_type: str


class GoogleTilesProxy:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        sessions: RequestBudget | None = None,
        opener: Callable[..., Any] = urlopen,
        timeout: float = 30.0,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get(KEY_VARIABLE)
        self.sessions = sessions or RequestBudget(total=100, per_minute=10, what="photorealistic map sessions")
        self._open = opener
        self.timeout = timeout

    def fetch(self, path: str, query: Mapping[str, str]) -> TileResponse:
        if not _SAFE_PATH.fullmatch(path) or ".." in path:
            raise TilesError(404, "not a Google 3D Tiles path")
        if not self.api_key:
            raise TilesError(503, f"photorealistic tiles need {KEY_VARIABLE} set where `dynacity serve` runs")
        if path == ROOT_PATH:
            refused = self.sessions.take()
            if refused:
                raise TilesError(429, refused)
        # The key travels only in this server-to-Google header; a `key` sent by
        # the browser is dropped rather than forwarded.
        params = {k: v for k, v in query.items() if k.lower() != "key"}
        url = f"{TILES_HOST}/{path}" + (f"?{urlencode(params)}" if params else "")
        request = Request(url, headers={"X-Goog-Api-Key": self.api_key, "User-Agent": "dynacity-viewer"})
        try:
            with self._open(request, timeout=self.timeout) as response:
                return TileResponse(
                    body=response.read(),
                    content_type=response.headers.get("Content-Type", "application/octet-stream"),
                )
        except HTTPError as exc:
            # Google's 4xx bodies explain key problems (API not enabled, bad
            # referrer restriction); they never echo the key back.
            detail = exc.read()[:500].decode("utf-8", "replace") or exc.reason
            raise TilesError(exc.code, f"Google Map Tiles API: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            raise TilesError(502, f"could not reach {TILES_HOST}: {getattr(exc, 'reason', exc)}") from exc
