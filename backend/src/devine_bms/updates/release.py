"""Checking an uploaded release is well formed (milestone 12b, decision 031: not who made it).
The same rules as the host agent's check_release (deploy/updater/bms_updater.py); a test keeps
the two in step."""

from __future__ import annotations

import re
import tarfile
from pathlib import Path

MAX_RELEASE = 200 * 1024 * 1024
NEEDED = ("VERSION", "backend/pyproject.toml", "web/dist/index.html", "deploy/containers/install-release.sh")


class ReleaseError(ValueError):
    pass


def check_release(path: Path) -> str:
    try:
        with tarfile.open(path, "r:gz") as tar:
            members = tar.getmembers()
            if not members:
                raise ReleaseError("the file is empty")
            top = members[0].name.split("/")[0]
            if not re.fullmatch(r"devine-bms-[0-9A-Za-z.+-]+", top):
                raise ReleaseError(f"not a Devine BMS release (top folder {top!r})")
            names = set()
            for m in members:
                parts = m.name.split("/")
                if m.name.startswith("/") or ".." in parts or parts[0] != top:
                    raise ReleaseError(f"unsafe path in the release: {m.name}")
                if not (m.isfile() or m.isdir()):
                    raise ReleaseError(f"only files and folders are allowed: {m.name}")
                names.add(m.name.rstrip("/"))
            for need in NEEDED:
                if f"{top}/{need}" not in names:
                    raise ReleaseError(f"the release has no {need}")
            version = tar.extractfile(f"{top}/VERSION").read().decode().strip()
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ReleaseError(f"not a .tar.gz release: {exc}") from exc
    if not re.fullmatch(r"[0-9A-Za-z.+-]{1,40}", version):
        raise ReleaseError("the release's VERSION is not valid")
    return version
