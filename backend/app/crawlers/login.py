"""Create a login session for a browser-based source, by hand, on the host.

Usage (from backend/, with the dev stack running so the database is reachable):

    .venv/bin/python -m app.crawlers.login facebook [--browser-path /usr/bin/brave-browser]

A visible browser window opens on the site's login page. Log in yourself (use the secondary
account, pass 2FA). The CLI notices when the login is complete (session cookie set and no
longer on a login, checkpoint or 2FA page), waits a few seconds, and saves the session to
`data/sessions/<type>.json` with mode 0600; the worker loads it in every run. The source's
`needs_login` state and failure count are reset so it is scheduled again.

The session file grants access to the account: it is gitignored, never logged, and must not
be shared.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

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
        # While the page is on one of these, the login is not finished yet.
        "pending_paths": "/login,/checkpoint,/two_step_verification,/two_factor,/recover",
    },
}

POLL_SECONDS = 2.0
SETTLE_SECONDS = 5.0


def login_complete(site: dict[str, str], cookie_names: set[str], url: str) -> bool:
    if site["cookie"] not in cookie_names:
        return False
    path = urlparse(url).path
    return not any(path.startswith(p) for p in site["pending_paths"].split(","))


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
        "--timeout", type=int, default=600, help="seconds to wait for the login (default 600)"
    )
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
            "Log in with the secondary account (finish 2FA and any checks).\n"
            "The session is saved automatically once the login is complete. Ctrl+C cancels.\n",
            flush=True,
        )
        deadline = time.monotonic() + args.timeout
        try:
            while True:
                # Follow the whole browser, not one tab: the site or the browser may open,
                # close or replace tabs during the login.
                if not browser.is_connected():
                    print(
                        "The browser was closed before the login finished; nothing saved.",
                        file=sys.stderr,
                    )
                    return 1
                names = {c["name"] for c in context.cookies() if site["domain"] in c["domain"]}
                urls = [p.url for p in context.pages if not p.is_closed()]
                if any(login_complete(site, names, url) for url in urls) or (
                    not urls and site["cookie"] in names
                ):
                    break
                if time.monotonic() > deadline:
                    browser.close()
                    print(f"No login within {args.timeout} s; nothing saved.", file=sys.stderr)
                    return 1
                time.sleep(POLL_SECONDS)
            print("Login detected; saving the session in a few seconds...", flush=True)
            time.sleep(SETTLE_SECONDS)  # let the site finish setting its cookies
        except KeyboardInterrupt:
            browser.close()
            print("Cancelled; nothing saved.")
            return 1

        save_storage_state(context, target)
        browser.close()

    reset = reset_sources(args.source_type)
    print(f"Saved the session to {target} (mode 0600); reset {reset} {args.source_type} source(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
