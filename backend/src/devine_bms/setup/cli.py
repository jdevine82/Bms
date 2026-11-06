"""Export and import the site setup, and reset a password, on the command line (milestones 5A, 6A).

    uv run python -m devine_bms.setup.cli export > site.json
    uv run python -m devine_bms.setup.cli import site.json [--check]
    uv run python -m devine_bms.setup.cli changes [--limit 20]
    uv run python -m devine_bms.setup.cli password <username>    (asks for the new password)

`password` is the way back in when the only admin has forgotten their password: it needs
a shell on the server, sets the password, unlocks the account and records it in the audit log.

A running backend picks up an import the next time it restarts; changes made through
the web app or API go live at once.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from pathlib import Path

from devine_bms.db.session import make_engine, make_sessionmaker
from devine_bms.settings import get_settings
from devine_bms.setup.service import SetupError, SetupService
from devine_bms.setup.startup import StartupError, prepare_site


async def _run(args) -> int:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    try:
        if args.command == "import":
            if not await _has_site(sessions):
                # An empty database: the file becomes the site, as at first start.
                settings.config_path = args.file
                site = await prepare_site(sessions, settings)
                print(f"Imported {args.file} as a new site ({site}).")
                return 0
            site = await prepare_site(sessions, settings)
            data = json.loads(Path(args.file).read_text(encoding="utf-8"))
            try:
                result = await SetupService(sessions, site).import_config(
                    data, client=f"cli:{getpass.getuser()}", check_only=args.check)
            except SetupError as exc:
                print(json.dumps(exc.as_dict(), indent=2), file=sys.stderr)
                return 1
            print(json.dumps(result, indent=2))
            return 0
        site = await prepare_site(sessions, settings)
        if args.command == "password":
            return await _password(sessions, site, args.username)
        service = SetupService(sessions, site)
        if args.command == "export":
            print(json.dumps(await service.export(), indent=2, ensure_ascii=False))
        else:
            for change in await service.changes(args.limit):
                print(f"{change['ts']}  {change['client']:20} {change['summary']}")
        return 0
    except StartupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()


async def _password(sessions, site: str, username: str) -> int:
    from devine_bms.auth.service import Actor, AuthError, AuthService

    password = getpass.getpass(f"New password for {username}: ")
    if password != getpass.getpass("Again: "):
        print("ERROR: the passwords do not match", file=sys.stderr)
        return 1
    actor = Actor(client=f"cli:{getpass.getuser()}", role="admin")
    try:
        await AuthService(sessions, site).set_user_password(actor, username.lower(), password)
    except AuthError as exc:
        print(f"ERROR: {exc.message}", file=sys.stderr)
        return 1
    print(f"Set a new password for {username}; the account is unlocked and its sessions ended.")
    return 0


async def _has_site(sessions) -> bool:
    from sqlalchemy import select

    from devine_bms.db import models as db

    async with sessions() as session:
        return await session.scalar(select(db.Site.id).limit(1)) is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Devine BMS site setup")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("export", help="print the site setup as JSON")
    imp = sub.add_parser("import", help="replace the site setup with a file")
    imp.add_argument("file")
    imp.add_argument("--check", action="store_true", help="only check the file")
    ch = sub.add_parser("changes", help="show the change log")
    ch.add_argument("--limit", type=int, default=20)
    pw = sub.add_parser("password", help="set a new password for an account (asks for it)")
    pw.add_argument("username")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
