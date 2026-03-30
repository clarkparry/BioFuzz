#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import urllib.request

DEFAULT_URL = (
    "https://zinc20.docking.org/substances/subsets/anodyne.smi"
)
DEFAULT_USER_AGENT = "BioFuzz/1.0 (+https://github.com/clarkparry/biofuzz)"
DEFAULT_RETRIES = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download a SMILES seed corpus")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", default="seeds/zinc_druglike_10k.smi")
    parser.add_argument("--max-lines", type=int, default=10000)
    return parser.parse_args()


def _url_with_params(url: str, **params: int | str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in params.items():
        query[key] = str(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    page = 1
    page_size = max(1, min(args.max_lines, 500))
    last_request_url = _url_with_params(args.url, count=page_size, page=page)

    with output.open("w", encoding="utf-8") as fh:
        while written < args.max_lines:
            request_url = _url_with_params(args.url, count=page_size, page=page)
            last_request_url = request_url
            request = urllib.request.Request(
                request_url,
                headers={
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Accept": "text/plain, text/*;q=0.9, */*;q=0.1",
                },
            )

            page_written = 0
            for attempt in range(1, DEFAULT_RETRIES + 1):
                try:
                    with urllib.request.urlopen(request, timeout=60) as resp:
                        for raw_line in resp:
                            line = raw_line.decode("utf-8", errors="replace").strip()
                            if not line:
                                continue
                            fh.write(f"{line}\n")
                            written += 1
                            page_written += 1
                            if written >= args.max_lines:
                                break
                    break
                except (HTTPError, URLError, TimeoutError) as exc:
                    if attempt == DEFAULT_RETRIES:
                        raise
                    print(
                        f"Retrying page {page} after {type(exc).__name__}: {exc}",
                        flush=True,
                    )
                    time.sleep(attempt)

            if page_written == 0:
                break
            print(
                f"Fetched page {page}: {page_written} lines (total {written}/{args.max_lines})",
                flush=True,
            )
            page += 1

    print(f"Wrote {written} SMILES to {output} from {last_request_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
