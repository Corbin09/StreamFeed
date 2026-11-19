"""Create a login session for a browser-based source, by hand, on the host.

Usage (from backend/, with the dev stack running so the database is reachable):

    .venv/bin/python -m app.crawlers.login facebook [--browser-path /usr/bin/brave-browser]

A visible browser window opens on the site's login page. Log in yourself (use the secondary
account, pass 2FA), then come back to the terminal and press Enter. The session is saved to
`data/sessions/<type>.json` with mode 0600; the worker loads it in every run. The source's
`needs_login` state and failure count are reset so it is scheduled again.

The session file grants access to the account: it is gitignored, never logged, and must not
be shared.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import update

from app.config import get_settings
from app.crawlers.fetchers import save_storage_state
from app.db import SessionLocal
from app.models import Source, SourceStatus

SITES: dict[str, dict[str, str]] = {
    "facebook": {
        "login_url": "https://www.facebook.com/login",
        "cookie": "c_user",  # present only when logged in
        "domain": "facebook.com",
    },
}


def session_file(source_type: str) -> Path:
    return Path(get_settings().data_dir) / "sessions" / f"{source_type}.json"


def reset_sources(source_type: str) -> int:
    with SessionLocal() as session:
        count = session.execute(
            update(Source)
            .where(Source.type == source_type)
            .values(status=SourceStatus.IDLE, consecutive_failures=0, last_error=None)
        ).rowcount
        session.commit()
        return count or 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source_type", choices=sorted(SITES))
    parser.add_argument(
        "--browser-path",
        help="Chromium-based browser to open (e.g. /usr/bin/brave-browser); "
        "default: BROWSER_EXECUTABLE_PATH, BROWSER_CHANNEL, or Playwright's Chromium",
    )
    args = parser.parse_args(argv)
    site = SITES[args.source_type]
    settings = get_settings()

    from playwright.sync_api import sync_playwright

    target = session_file(args.source_type)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            executable_path=args.browser_path or settings.browser_executable_path or None,
            channel=None if args.browser_path else settings.browser_channel or None,
        )
        context = browser.new_context(locale="vi-VN", timezone_id=settings.timezone)
        page = context.new_page()
        page.goto(site["login_url"])
        print(
            f"\nA browser window is open at {site['login_url']}.\n"
            "Log in with the secondary account (finish 2FA and any checks),\n"
            "wait until the home feed shows, then press Enter here. Ctrl+C cancels.\n"
        )
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            browser.close()
            print("Cancelled; nothing saved.")
            return 1

        cookies = {c["name"] for c in context.cookies() if site["domain"] in c["domain"]}
        if site["cookie"] not in cookies:
            browser.close()
            print("Not logged in yet (no session cookie found); nothing saved.", file=sys.stderr)
            return 1
        save_storage_state(context, target)
        browser.close()

    reset = reset_sources(args.source_type)
    print(f"Saved the session to {target} (mode 0600); reset {reset} {args.source_type} source(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
