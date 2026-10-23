"""The ``copperbelt`` command line: argument handling, drain, provider checks and the worker loop's resilience."""

from __future__ import annotations

import signal
from types import SimpleNamespace

import pytest

from copperbelt import cli
from copperbelt.providers import registry


@pytest.fixture
def restore_signal_handlers():
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    yield
    for s, h in saved.items():
        signal.signal(s, h)


def test_a_subcommand_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
    assert "worker" in capsys.readouterr().err


def test_unknown_subcommand_is_rejected() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["migrate"])
    assert exc.value.code == 2


def test_drain_reports_how_many_jobs_ran(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[int] = []
    monkeypatch.setattr(cli, "drain", lambda n: calls.append(n) or 3)
    assert cli.main(["drain"]) == 0
    assert calls == [10_000]
    assert capsys.readouterr().out.strip() == "processed 3 job(s)"


def test_drain_on_an_empty_queue_processes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["drain"]) == 0
    assert capsys.readouterr().out.strip() == "processed 0 job(s)"


def test_check_providers_prints_one_line_per_provider(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fake(pid: str, status: str, detail: str) -> SimpleNamespace:
        return SimpleNamespace(info=SimpleNamespace(id=pid), check=lambda: SimpleNamespace(status=status, detail=detail))

    monkeypatch.setattr(
        registry,
        "PROVIDERS",
        {"planet": fake("planet", "unconfigured", "CBI_PLANET_API_KEY not set"), "pvgis": fake("pvgis", "live_verified", "HTTP 200")},
    )
    assert cli.main(["check-providers"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert lines[0].split()[:2] == ["planet", "unconfigured"]
    assert lines[0].endswith("CBI_PLANET_API_KEY not set")
    assert lines[1].split() == ["pvgis", "live_verified", "HTTP", "200"]


def test_worker_survives_a_failing_job_and_stops_on_sigterm(monkeypatch: pytest.MonkeyPatch, restore_signal_handlers) -> None:
    outcomes = iter([RuntimeError("handler crashed"), SimpleNamespace(id="job-1"), None])
    ran: list[str] = []

    def run_one(worker_id: str):
        ran.append(worker_id)
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(ran) >= 3:
            signal.raise_signal(signal.SIGTERM)  # the queue is empty: ask the worker to stop, as an orchestrator would

    monkeypatch.setattr(cli, "run_one", run_one)
    monkeypatch.setattr(cli.time, "sleep", sleep)
    cli.worker()
    # 1st call raised and 3rd found the queue empty: both poll; the 2nd ran a job and loops straight on.
    assert len(ran) == 3
    assert len(set(ran)) == 1 and ":" in ran[0]
    assert len(slept) == 2
    assert slept[0] == cli.get_settings().worker_poll_seconds
