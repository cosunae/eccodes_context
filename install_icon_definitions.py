#!/usr/bin/env python3
"""Download and extract DWD's ICON eccodes definitions.

Fetches an `eccodes_definitions.edzw-*` archive from
https://opendata.dwd.de/weather/lib/grib/ and extracts it into a local
directory, ready to be used as one entry of ECCODES_DEFINITION_PATH
(see eccodes_context_switch.py / test_module.py).
"""

import argparse
import logging
import re
import tarfile
import urllib.request
from pathlib import Path

DWD_GRIB_LIB_URL = "https://opendata.dwd.de/weather/lib/grib/"
ARCHIVE_RE = re.compile(r'href="(eccodes_definitions\.edzw-[^"]+\.tar\.(?:bz2|gz))"')
VERSION_RE = re.compile(r"edzw-([\d.]+)(?:-(\d+))?\.tar")
DEFAULT_DEST = Path(__file__).resolve().parent / "icon_definitions"

logger = logging.getLogger("install_icon_definitions")


def list_available_archives() -> list[str]:
    with urllib.request.urlopen(DWD_GRIB_LIB_URL) as response:
        html = response.read().decode()
    return sorted(set(ARCHIVE_RE.findall(html)), key=_version_key)


def _version_key(archive_name: str):
    match = VERSION_RE.search(archive_name)
    version = tuple(int(part) for part in match.group(1).split("."))
    revision = int(match.group(2) or 0)
    return version + (revision,)


def download(archive_name: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive_path = dest_dir / archive_name
    url = DWD_GRIB_LIB_URL + archive_name
    logger.info("downloading %s", url)
    urllib.request.urlretrieve(url, archive_path)
    return archive_path


def extract(archive_path: Path, dest_dir: Path) -> Path:
    logger.info("extracting %s to %s", archive_path, dest_dir)
    with tarfile.open(archive_path) as tar:
        tar.extractall(dest_dir)
    archive_path.unlink()
    extracted = [p for p in dest_dir.iterdir() if p.is_dir() and p.name.startswith("definitions.edzw")]
    return extracted[0] if extracted else dest_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        default=str(DEFAULT_DEST),
        help="directory to install the ICON definitions into (default: %(default)s)",
    )
    parser.add_argument(
        "--archive",
        default=None,
        help="specific archive filename to install, e.g. eccodes_definitions.edzw-2.47.0-2.tar.bz2 "
        "(default: the latest one listed at " + DWD_GRIB_LIB_URL + ")",
    )
    parser.add_argument(
        "--list", action="store_true", help="list the archives available at %s and exit" % DWD_GRIB_LIB_URL
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    archives = list_available_archives()
    if args.list:
        for name in archives:
            print(name)
        return

    archive_name = args.archive or archives[-1]
    dest_dir = Path(args.dest).resolve()
    archive_path = download(archive_name, dest_dir)
    definitions_dir = extract(archive_path, dest_dir)
    logger.info("ICON definitions installed at: %s", definitions_dir)


if __name__ == "__main__":
    main()
