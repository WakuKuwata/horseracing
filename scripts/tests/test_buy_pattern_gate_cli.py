"""Feature 109 T041: CLI fail-closed chain on a throw-away --spec-dir with --smoke.

Runs only with the real DB and the OOF bundle (skipped otherwise); never touches the frozen
files under specs/109-buy-pattern-gate. Execute from the training environment:

    cd training && uv run pytest ../scripts/tests -q
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
DRIVER = REPO / "scripts" / "buy_pattern_gate.py"
SPEC = REPO / "specs" / "109-buy-pattern-gate"
BUNDLE = (
    REPO
    / "artifacts/oof/8bdde26857f62c5571ef45b02954836dacae7e4a2ea9174c12eb0c9209fb691f/bundle.json"
)


def _db_up() -> bool:
    try:
        from sqlalchemy import create_engine, text

        url = os.environ.get(
            "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
        )
        with create_engine(url).connect() as c:
            c.execute(text("select 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not (BUNDLE.exists() and _db_up()),
    reason="needs the real DB at localhost:15432 and the 108 OOF bundle",
)


def run(spec: pathlib.Path, *args: str, expect: int = 0) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(DRIVER), "--spec-dir", str(spec), "--smoke", *args]
    cp = subprocess.run(cmd, cwd=REPO / "training", capture_output=True, text=True)
    assert cp.returncode == expect, (
        f"{' '.join(args)}\nrc={cp.returncode}\n{cp.stdout[-2000:]}\n{cp.stderr[-3000:]}"
    )
    return cp


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    spec = tmp_path_factory.mktemp("spec109")
    (spec / "evidence").mkdir()
    shutil.copy(SPEC / "gate-config.json", spec / "gate-config.json")
    # freeze must start from an unfrozen config even if the real one is already frozen
    cfg = json.loads((spec / "gate-config.json").read_text())
    cfg["patterns_hash"] = ""
    cfg["code_sha"] = ""
    (spec / "gate-config.json").write_text(json.dumps(cfg, ensure_ascii=False))
    out = run(spec, "freeze").stdout
    h = out.strip().split("gate_config_hash=")[-1].split()[0]
    run(spec, "--gate-config-hash", h, "selftest")
    run(spec, "--gate-config-hash", h, "screen")
    run(spec, "--gate-config-hash", h, "confirm")
    return spec, h


def test_smoke_chain_redacts_numbers_and_recomputes(chain):
    spec, h = chain
    for w in ("screening-discovery", "screening-qualification", "confirmatory"):
        cp = run(spec, "--gate-config-hash", h, "recompute", "--window", w)
        assert "bit-identical" in cp.stdout
    v = json.loads((spec / "verdict.json").read_text())
    assert v["smoke"] is True and len(v["limitations"]) == 5
    # smoke output never prints an ROI value
    cp = subprocess.run(
        [
            sys.executable,
            str(DRIVER),
            "--spec-dir",
            str(spec),
            "--smoke",
            "--gate-config-hash",
            h,
            "confirm",
        ],
        cwd=REPO / "training",
        capture_output=True,
        text=True,
    )
    assert cp.returncode != 0  # verdict already exists → refused
    assert "already exists" in (cp.stdout + cp.stderr)


def test_default_spec_dir_refuses_smoke():
    cp = subprocess.run(
        [sys.executable, str(DRIVER), "--smoke", "freeze"],
        cwd=REPO / "training",
        capture_output=True,
        text=True,
    )
    assert cp.returncode != 0 and "default spec dir" in (cp.stdout + cp.stderr)


def test_gate_config_hash_tamper_is_refused(chain):
    spec, h = chain
    run(spec, "--gate-config-hash", "0" * 64, "screen", expect=1)


def _copy_chain(chain, tmp_path) -> tuple[pathlib.Path, str]:
    spec, h = chain
    dst = tmp_path / "copy"
    shutil.copytree(spec, dst)
    return dst, h


def test_patterns_tamper_is_refused(chain, tmp_path):
    dst, h = _copy_chain(chain, tmp_path)
    p = json.loads((dst / "patterns.json").read_text())
    p["patterns"][0]["label_ja"] = "改変"
    (dst / "patterns.json").write_text(json.dumps(p, ensure_ascii=False))
    (dst / "verdict.json").unlink()
    run(dst, "--gate-config-hash", h, "confirm", expect=1)


def test_population_tamper_is_refused(chain, tmp_path):
    dst, h = _copy_chain(chain, tmp_path)
    pop = json.loads((dst / "population.json").read_text())
    pop["population_hash"] = "f" * 64
    (dst / "population.json").write_text(json.dumps(pop, ensure_ascii=False))
    (dst / "verdict.json").unlink()
    run(dst, "--gate-config-hash", h, "confirm", expect=1)


def test_survivors_tamper_is_refused(chain, tmp_path):
    dst, h = _copy_chain(chain, tmp_path)
    (dst / "verdict.json").unlink()
    run(dst, "--gate-config-hash", h, "--survivors-hash", "e" * 64, "confirm", expect=1)


def test_selftest_failure_blocks_screen(chain, tmp_path):
    dst, h = _copy_chain(chain, tmp_path)
    st = json.loads((dst / "evidence" / "selftest.json").read_text())
    st["passed"] = False
    st["smoke"] = False
    (dst / "evidence" / "selftest.json").write_text(json.dumps(st, ensure_ascii=False))
    (dst / "evidence" / "survivors.json").unlink()
    run(dst, "--gate-config-hash", h, "screen", expect=1)
