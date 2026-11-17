"""Download the pinned tursodb release for this host into .bin/tursodb."""

import argparse
import hashlib
import io
import pathlib
import platform
import stat
import sys
import tarfile
import urllib.request

# The release matching the locked pyturso engine; it ships --sync-server.
VERSION = "0.8.3"

# Archive sha256 per (system, machine), from the release's published digests.
ARCHIVES = {
    ("Darwin", "arm64"): (
        "aarch64-apple-darwin",
        "9990ea61d065a9f229ad81bda0156ee92081229b2ccf986c258c03e9ea705a75",
    ),
    ("Darwin", "x86_64"): (
        "x86_64-apple-darwin",
        "7db0a635e5f59771f78cbb298bf8c28c2f523ad9d814c3c332b251667034f66e",
    ),
    ("Linux", "aarch64"): (
        "aarch64-unknown-linux-gnu",
        "4a832c5125ea9137b020843c61731644666db90c450b027f5d5cffe455c4c3fb",
    ),
    ("Linux", "x86_64"): (
        "x86_64-unknown-linux-gnu",
        "4475c4df29f744fb3b2fdeef622d9297bbb80698039eec57d1b3ba7e048b5e31",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=".bin", type=pathlib.Path)
    arguments = parser.parse_args()
    host = (platform.system(), platform.machine())
    if host not in ARCHIVES:
        sys.stderr.write(f"no tursodb {VERSION} release for {host[0]} {host[1]}\n")
        return 1
    triple, sha256 = ARCHIVES[host]
    name = f"turso_cli-{triple}"
    asset = f"v{VERSION}/{name}.tar.xz"
    with urllib.request.urlopen(
        f"https://github.com/tursodatabase/turso/releases/download/{asset}"
    ) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != sha256:
        sys.stderr.write(f"{name}.tar.xz does not match its pinned sha256\n")
        return 1
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:xz") as tar:
        member = tar.extractfile(f"{name}/tursodb")
        if member is None:
            sys.stderr.write(f"{name}.tar.xz holds no tursodb file\n")
            return 1
        binary = member.read()
    arguments.directory.mkdir(exist_ok=True)
    path = arguments.directory / "tursodb"
    path.write_bytes(binary)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    sys.stdout.write(f"{path} {VERSION}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
