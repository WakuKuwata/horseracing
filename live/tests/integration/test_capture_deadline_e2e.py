"""Real-CLI deadline, advisory-lock, and process-group verification for Feature 086."""

from __future__ import annotations

import datetime
import json
import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import ChaosSnapshot, Horse, Race, RaceHorse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from horseracing_live.chaos_politeness import deadline_for

pytestmark = pytest.mark.integration

RACE_ID = "202607280101"
TRIGGER = "predict_manual"
OUTER_CUTOFF_S = 18.0
SLOW_RESPONSE_S = 6.0
LATE_WRITE_GRACE_S = 3.0
LIVE_DIR = Path(__file__).resolve().parents[2]


class _SlowHandler(BaseHTTPRequestHandler):
    server: "_SlowServer"

    def do_GET(self) -> None:  # noqa: N802
        self.server.requests.append(self.path)
        time.sleep(SLOW_RESPONSE_S)
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
        elif self.path.startswith("/odds"):
            body = json.dumps(
                {
                    "data": {
                        "odds": {
                            "1": {
                                f"{number:02d}": [
                                    str(2.0 + number),
                                    "0.0",
                                    str(number),
                                ]
                                for number in range(1, 5)
                            }
                        }
                    }
                }
            ).encode()
        else:
            self.send_error(404)
            return

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The deadline deliberately cancels the in-flight odds response.
            pass

    def log_message(self, _format: str, *_args) -> None:
        pass


class _SlowServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _SlowHandler)
        self.requests: list[str] = []


@contextmanager
def _slow_http_server():
    server = _SlowServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def _seed_future_race(session: Session) -> None:
    now = datetime.datetime.now(datetime.UTC)
    race = Race(
        race_id=RACE_ID,
        race_number=1,
        race_date=now.date(),
        post_time=now + datetime.timedelta(hours=1),
    )
    horses = [
        Horse(
            horse_id=f"deadline-e2e-H{number}",
            horse_name=f"Deadline E2E Horse {number}",
        )
        for number in range(1, 5)
    ]
    session.add_all([race, *horses])
    session.flush()

    session.add_all(
        [
            RaceHorse(
                race_id=RACE_ID,
                horse_id=horse.horse_id,
                horse_number=number,
                entry_status=EntryStatus.STARTED,
            )
            for number, horse in enumerate(horses, start=1)
        ]
    )
    session.flush()
    session.commit()


def _subprocess_env(tmp_path: Path, base_url: str) -> dict[str, str]:
    sitecustomize = tmp_path / "sitecustomize.py"
    sitecustomize.write_text(
        "import os\n"
        "import horseracing_scrape.urls as urls\n"
        "urls._ODDS_API = os.environ['CAPTURE_TEST_BASE_URL'] + '/odds'\n",
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(tmp_path)
        if not existing_pythonpath
        else f"{tmp_path}{os.pathsep}{existing_pythonpath}"
    )
    env["CAPTURE_TEST_BASE_URL"] = base_url
    return env


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _snapshot_count(engine) -> int:
    with Session(engine) as verify:
        return int(
            verify.scalar(
                select(func.count())
                .select_from(ChaosSnapshot)
                .where(ChaosSnapshot.race_id == RACE_ID)
            )
            or 0
        )


def test_real_cli_enforces_deadline_and_leaves_no_process_or_write(
    session: Session,
    engine,
    database_url: str,
    tmp_path: Path,
) -> None:
    _seed_future_race(session)
    budget_s = deadline_for(TRIGGER)

    with _slow_http_server() as (base_url, server):
        command = [
            "uv",
            "run",
            "python",
            "-m",
            "horseracing_live",
            "capture-chaos",
            "--race-id",
            RACE_ID,
            "--trigger",
            TRIGGER,
            "--json",
            "--database-url",
            database_url,
        ]
        wall_started = time.monotonic()
        proc = subprocess.Popen(  # noqa: S603 - fixed CLI argv and fixture race id
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=LIVE_DIR,
            env=_subprocess_env(tmp_path, base_url),
            start_new_session=True,
        )
        process_group_id = proc.pid
        try:
            stdout, stderr = proc.communicate(timeout=OUTER_CUTOFF_S)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            pytest.fail(f"capture CLI exceeded the {OUTER_CUTOFF_S:g}s outer cutoff")
        except BaseException:
            if proc.poll() is None:
                _kill_process_group(proc)
            raise
        wall_elapsed_s = time.monotonic() - wall_started

        assert proc.returncode == 0, stderr
        payload = json.loads(stdout)
        assert (payload["outcome"], payload["reason"]) == (
            "skipped",
            "deadline_exceeded",
        )
        # Two-sided on purpose. `elapsed_s` spans until the JSON is produced, so it
        # necessarily exceeds the budget by the cost of noticing the deadline and returning
        # (~20ms measured): `elapsed_s <= budget_s` would demand a zero-cost return path.
        # The lower bound is the part that matters and the one-sided form did NOT check it --
        # it proves the capture used its budget rather than bailing out early, and the upper
        # bound proves it stopped at the inner deadline instead of running to the outer cutoff.
        assert budget_s <= payload["elapsed_s"] <= budget_s + 1.0
        assert wall_elapsed_s < OUTER_CUTOFF_S
        assert len(server.requests) == 2
        assert server.requests[0] == "/robots.txt"
        assert server.requests[1].startswith("/odds?")

        with Session(engine) as lock_session:
            lock_acquired = lock_session.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                {"key": f"chaos-capture:{RACE_ID}"},
            )
            assert lock_acquired is True
            lock_session.rollback()

        assert not _process_group_exists(process_group_id)
        count_at_exit = _snapshot_count(engine)
        time.sleep(LATE_WRITE_GRACE_S)
        assert _snapshot_count(engine) == count_at_exit == 0
