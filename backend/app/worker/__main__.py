"""Entry point: `python -m app.worker`. Stops after the current crawl on SIGTERM/SIGINT."""

from __future__ import annotations

import logging
import signal
import threading

from app.config import get_settings
from app.worker import Worker


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    stop = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        logging.getLogger(__name__).info(
            "Received %s; stopping after the current crawl", signal.Signals(signum).name
        )
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    Worker(settings=settings).run_forever(stop)


if __name__ == "__main__":
    main()
