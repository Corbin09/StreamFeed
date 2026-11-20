"""Crawler command-line interface.

python -m app.crawlers list                 # sources and their crawler plugins
python -m app.crawlers run <source>         # run one crawl now (id, name or unique type)
python -m app.crawlers reparse <run_id>     # re-parse a run's saved raw pages (no network)
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import or_, select

from app.config import get_settings
from app.crawlers.registry import UnknownSourceType, load_builtin_crawlers
from app.crawlers.runner import CrawlRunner
from app.db import SessionLocal
from app.models import CrawlRunStatus, CrawlRunTrigger, Source, SourceStatus


def _resolve_source(ref: str) -> Source:
    with SessionLocal() as session:
        conditions = [Source.name == ref, Source.type == ref]
        if ref.isdigit():
            conditions.append(Source.id == int(ref))
        matches = session.scalars(select(Source).where(or_(*conditions))).all()
    if not matches:
        sys.exit(f"error: no source matches {ref!r} (try: python -m app.crawlers list)")
    if len(matches) > 1:
        names = ", ".join(f"{s.id}:{s.name}" for s in matches)
        sys.exit(f"error: {ref!r} is ambiguous ({names}); use the id")
    return matches[0]


def cmd_list(_: argparse.Namespace) -> int:
    registry = load_builtin_crawlers()
    with SessionLocal() as session:
        sources = session.scalars(select(Source).order_by(Source.id)).all()
    print(f"{'ID':>3}  {'TYPE':<10} {'ENABLED':<8} {'STATUS':<12} {'PLUGIN':<8} NAME")
    for s in sources:
        plugin = "yes" if s.type in registry.types() else "missing"
        print(f"{s.id:>3}  {s.type:<10} {str(s.enabled):<8} {s.status:<12} {plugin:<8} {s.name}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    source = _resolve_source(args.source)
    if source.status == SourceStatus.NEEDS_LOGIN and not args.force:
        sys.exit(
            f"error: source {source.name!r} needs a manual login first "
            "(or pass --force to try anyway)"
        )
    try:
        result = CrawlRunner().run(source.id, trigger=CrawlRunTrigger.MANUAL)
    except UnknownSourceType as exc:
        sys.exit(f"error: {exc}")
    print(
        f"run {result.run_id}: {result.status} - pages={result.pages} posts={result.fetched} "
        f"inserted={result.inserted} updated={result.updated} parse_errors={result.parse_errors}"
    )
    if result.error:
        print(f"error: {result.error}")
    return 0 if result.status == CrawlRunStatus.SUCCESS else 1


def cmd_reparse(args: argparse.Namespace) -> int:
    try:
        result = CrawlRunner().reparse(args.run_id)
    except (LookupError, FileNotFoundError) as exc:
        sys.exit(f"error: {exc}")
    print(
        f"reparse of run {args.run_id}: {result.status} - pages={result.pages} "
        f"posts={result.fetched} inserted={result.inserted} updated={result.updated} "
        f"parse_errors={result.parse_errors}"
    )
    return 0 if result.status == CrawlRunStatus.SUCCESS else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.crawlers",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list sources and plugin availability").set_defaults(func=cmd_list)

    run = sub.add_parser("run", help="run one crawl now")
    run.add_argument("source", help="source id, name, or type when unique")
    run.add_argument("--force", action="store_true", help="run even if the source needs login")
    run.set_defaults(func=cmd_run)

    reparse = sub.add_parser("reparse", help="re-parse saved raw pages of a run")
    reparse.add_argument("run_id", type=int)
    reparse.set_defaults(func=cmd_reparse)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else get_settings().log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.verbose:
        # One line per HTTP request (and per post sent to the LLM) is noise.
        logging.getLogger("httpx").setLevel(logging.WARNING)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
