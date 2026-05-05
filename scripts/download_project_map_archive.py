"""
Download the YESAB project map file (all.zip) from the YESAB website.

This script checks the server's Last-Modified header to determine if the file has changed,
and only downloads the file if it has. It persists the last-modified timestamp in a state file
so that subsequent runs will only download if the file has changed.

(c)2026 Matt Wilkie, Yukon Government. MIT License.
"""

# /// script
# requires-python = ">=3.10"
# ///
import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request

# From https://yesab.ca/project-map
URL = "https://yesab.ca/wp-content/plugins/yesab-map-wp-plugin/geojson/all.zip"

BASE_DIR = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "yesab_all_zip.state.json"
OUTPUT_FILE = DATA_DIR / "yesab_all.zip"
TIMEOUT = 30  # seconds
CHUNK_SIZE = 8192


def load_state():
    """Return the persisted download state, or an empty state if none exists."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    """Persist the current download state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def build_headers(state: dict):
    """Build conditional request headers from the saved state."""
    headers = {}

    if OUTPUT_FILE.exists() and "last_modified" in state:
        headers["If-Modified-Since"] = state["last_modified"]

    return headers


def conditional_download(headers):
    """Download only if the server says the file changed."""
    req = urllib.request.Request(URL, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            response_headers = dict(resp.headers.items())
            OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
            with OUTPUT_FILE.open("wb") as f:
                while chunk := resp.read(CHUNK_SIZE):
                    f.write(chunk)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            print("Dataset unchanged (304 Not Modified)")
            return False, None
        raise

    return True, response_headers


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Check the YESAB download endpoint and refresh the local zip when needed."""
    parse_args(sys.argv[1:] if argv is None else argv)
    state = load_state()
    request_headers = build_headers(state)

    print("Checking YESAB all.zip...")
    if state and not OUTPUT_FILE.exists():
        print(f"Local dataset missing, forcing download: {OUTPUT_FILE}")

    try:
        changed, response_headers = conditional_download(request_headers)

        if not changed:
            return 0

        print("Download complete")

        new_state = {}

        if "Last-Modified" in response_headers:
            new_state["last_modified"] = response_headers["Last-Modified"]

        if "Content-Length" in response_headers:
            new_state["content_length"] = response_headers["Content-Length"]

        save_state(new_state)

        print("State updated:", new_state)

    except urllib.error.URLError as e:
        raise SystemExit(f"Request failed: {e}") from e

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
