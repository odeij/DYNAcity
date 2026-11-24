"""Download helpers and the source ledger for open datasets kept under the private data root.

Files go to `<root>/raw/<source>/`; every download is recorded (URL, bytes, SHA256, retrieval
time, licence and attribution text) in `<root>/sources.json`. Nothing here is committed to Git.

Public object stores often cap one connection at a few tens of KB/s, so `download_ranges`
reads a file as several HTTP range requests in parallel. Requests carry a generic client name.
"""
from __future__ import annotations

import hashlib
import io
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "dynacity-open-data-fetch/1.0"
LEDGER_NAME = "sources.json"


def _request(url: str, start: int | None = None, end: int | None = None, method: str = "GET") -> urllib.request.Request:
    headers = {"User-Agent": USER_AGENT}
    if start is not None:
        headers["Range"] = f"bytes={start}-{end}"
    return urllib.request.Request(url, headers=headers, method=method)


def head(url: str) -> dict[str, str]:
    """Response headers of a HEAD request (lower-cased names)."""
    with urllib.request.urlopen(_request(url, method="HEAD"), timeout=60) as response:
        return {k.lower(): v for k, v in response.headers.items()}


def read_range(url: str, start: int, end: int, retries: int = 4) -> bytes:
    """Bytes `start..end` inclusive; retried on connection errors and checked for length."""
    last: Exception | None = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(_request(url, start, end), timeout=120) as response:
                data = response.read()
            if len(data) != end - start + 1:
                raise OSError(f"short range read: {len(data)} of {end - start + 1} bytes")
            return data
        except OSError as error:  # URLError, timeouts and short reads
            last = error
    raise OSError(f"range {start}-{end} of {url} failed after {retries} tries: {last}")


def download_ranges(url: str, destination: Path, *, max_bytes: int, workers: int = 16,
                    chunk_bytes: int = 4_000_000) -> dict:
    """Download `url` to `destination` with parallel range requests and return its ledger entry.

    Refuses files larger than `max_bytes` before reading any body. The file is written next to
    `destination` and renamed, so an interrupted run never leaves a partial file behind.
    """
    meta = head(url)
    size = int(meta["content-length"])
    if size > max_bytes:
        raise ValueError(f"{url} is {size} bytes, over the {max_bytes} byte cap")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    started = datetime.now(timezone.utc)
    spans = [(s, min(s + chunk_bytes, size) - 1) for s in range(0, size, chunk_bytes)]
    with open(partial, "wb") as out:
        out.truncate(size)

    def fetch(span: tuple[int, int]) -> None:
        data = read_range(url, *span)
        with open(partial, "r+b") as out:
            out.seek(span[0])
            out.write(data)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(fetch, spans))
    partial.replace(destination)
    return ledger_entry(url, destination, meta, started)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ledger_entry(url: str, path: Path, headers: dict[str, str] | None = None,
                 retrieved: datetime | None = None, **extra) -> dict:
    headers = headers or {}
    return {
        "url": url,
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "retrieved_at_utc": (retrieved or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "server_etag": headers.get("etag"),
        "server_last_modified": headers.get("last-modified"),
        **extra,
    }


def record_source(root: Path, source_id: str, *, files: list[dict], licence: str, attribution: str,
                  publisher: str, notes: str = "") -> Path:
    """Merge one source into `<root>/sources.json` (replacing an earlier record of the same id)."""
    ledger_path = root / LEDGER_NAME
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {"sources": {}}
    ledger["sources"][source_id] = {
        "publisher": publisher, "licence": licence, "attribution": attribution,
        "files": files, "notes": notes,
    }
    ledger_path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return ledger_path


class HttpRangeFile(io.RawIOBase):
    """A read-only seekable view of a remote file, fetched in range requests (for `zipfile`)."""

    def __init__(self, url: str, size: int | None = None, block_bytes: int = 4_000_000):
        self.url = url
        self.size = size if size is not None else int(head(url)["content-length"])
        self._pos = 0
        self._block = block_bytes
        self._cache: tuple[int, bytes] | None = None
        self.bytes_fetched = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self.size}[whence]
        self._pos = max(0, base + offset)
        return self._pos

    def read(self, n: int = -1) -> bytes:
        if self._pos >= self.size:
            return b""
        end = self.size if n is None or n < 0 else min(self.size, self._pos + n)
        out = bytearray()
        while self._pos < end:
            if self._cache and self._cache[0] <= self._pos < self._cache[0] + len(self._cache[1]):
                start, block = self._cache
                piece = block[self._pos - start: end - start]
                out += piece
                self._pos += len(piece)
                continue
            # Large reads (a whole archive member) fetch their own span in parallel; small ones read a block.
            span_end = min(self.size, max(end, self._pos + self._block))
            data = read_ranges_parallel(self.url, self._pos, span_end - 1)
            self.bytes_fetched += len(data)
            self._cache = (self._pos, data)
        return bytes(out)

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


def read_ranges_parallel(url: str, start: int, end: int, *, workers: int = 8, chunk_bytes: int = 1_000_000) -> bytes:
    """Bytes `start..end` inclusive, fetched as parallel range requests."""
    spans = [(s, min(s + chunk_bytes, end + 1) - 1) for s in range(start, end + 1, chunk_bytes)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return b"".join(pool.map(lambda span: read_range(url, *span), spans))
