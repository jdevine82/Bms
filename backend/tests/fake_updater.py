"""A stand-in for the host's updater agent, for the browser test (web/tests/updates.mjs).
The real agent (deploy/updater/bms_updater.py) with pct and the site's health replaced; a release
whose version is 0.9.9 fails its install, so the rollback can be seen.
  uv run python tests/fake_updater.py PORT BACKEND_URL RELEASE_DIR STATE_DIR TOKEN
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_updater import U, FakeSite  # noqa: E402


def main() -> None:
    port, backend, release_dir, state_dir, token = sys.argv[1:6]
    site = FakeSite()

    def run(cmd, stdin=None, timeout=0):
        if cmd[:2] == ["pct", "push"]:
            shutil.copy(cmd[3], Path(release_dir) / Path(cmd[4]).name)
            version = Path(cmd[4]).name.removeprefix("devine-bms-").removesuffix(".tar.gz")
            site.before = site.version  # a rollback restores what was running
            site.after, site.install_rc = version, (1 if version == "0.9.9" else 0)
        return site.run(cmd, stdin, timeout)

    def http(method, url, body=None, tok=None, timeout=5):
        if url.endswith("/api/updates/report"):
            return U.http_json(method, f"{backend}/api/updates/report", body, tok, timeout)
        return site.http(method, url, body, tok, timeout)

    import time

    updater = U.Updater(Path(state_dir), token=token, runner=run, http=http,
                        sleep=lambda s: time.sleep(min(s, 0.3)), answer_s=3, stable_s=1, poll_s=0.5)
    U.make_server(updater, ("127.0.0.1", int(port)), token).serve_forever()


if __name__ == "__main__":
    main()
