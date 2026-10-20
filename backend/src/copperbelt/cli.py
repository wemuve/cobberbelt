"""Command-line entry points: ``copperbelt worker | drain | check-providers``."""

from __future__ import annotations

import argparse
import signal
import socket
import sys
import time
import uuid

import structlog

import copperbelt.services.documents  # noqa: F401  (registers job handlers)
from copperbelt.config import get_settings
from copperbelt.jobs.queue import drain, run_one

log = structlog.get_logger()


def worker() -> None:
    worker_id = f"{socket.gethostname()}:{uuid.uuid4().hex[:6]}"
    stopping = False

    def _stop(*_):  # noqa: ANN002, ANN202
        nonlocal stopping
        stopping = True
        log.info("worker.stopping", worker_id=worker_id)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker.started", worker_id=worker_id)
    poll = get_settings().worker_poll_seconds
    while not stopping:
        try:
            job = run_one(worker_id)
        except Exception:  # noqa: BLE001 - keep the worker alive; the job row records the failure
            log.exception("worker.loop_error")
            job = None
        if job is None:
            time.sleep(poll)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="copperbelt")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("worker", help="Run the background job worker")
    sub.add_parser("drain", help="Process queued jobs then exit")
    sub.add_parser("check-providers", help="Run live checks against every configured provider")
    args = parser.parse_args(argv)
    if args.cmd == "worker":
        worker()
    elif args.cmd == "drain":
        print(f"processed {drain(10_000)} job(s)")
    elif args.cmd == "check-providers":
        from copperbelt.providers.registry import PROVIDERS

        for p in PROVIDERS.values():
            r = p.check()
            print(f"{p.info.id:12} {r.status:22} {r.detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
