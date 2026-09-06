"""Feature 109 driver: freeze → selftest → screen → confirm → recompute (fail-closed).

Thin driver in the 107 style: everything that touches the DB, the OOF bundle, pandas, parquet
or git lives here; every statistic comes from ``horseracing_eval.buy_pattern_gate`` (numpy
only) so evidence and verdict can be recomputed bit-for-bit with the same function.

    cd training && uv run python ../scripts/buy_pattern_gate.py <sub> [--spec-dir DIR] [--smoke]

Order is enforced through the artefacts each step writes and the next step hashes:
  freeze    clean tree → patterns.json (393) → gate-config gets patterns_hash / code_sha /
            windows.confirmatory[1] → prints the gate-config hash
  selftest  gate hash → real rows (confirmatory window covariates, synthetic winners) →
            evidence/selftest.json (passed / run_code_sha / timing / per-pattern selection hashes)
  screen    clean tree → gate hash → selftest.passed → rows snapshot per window → population.json
            → discovery → qualification → survivors.json
  confirm   clean tree → 4 hashes → rows snapshot (no DB re-read) → survivors + controls →
            sensitivities → states → verdict.json (append-only)
  recompute re-derive point / CI / p-values from the saved evidence alone and assert parity

``--smoke`` shrinks the windows to three months of 2010 and the replicate counts, requires a
non-default ``--spec-dir`` (never touches the real frozen files), waives the clean-tree check,
and REDACTS every effect number from the printed output (only "fired / not fired" structure is
shown), so a smoke run can never leak a screening result.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

import numpy as np
import pandas as pd
from horseracing_eval import buy_pattern_gate as gate
from horseracing_eval import buy_patterns as bp
from horseracing_eval.decision import gate_config_hash
from horseracing_eval.hashing import stable_hash
from sqlalchemy import create_engine, text

REPO = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SPEC_DIR = REPO / "specs" / "109-buy-pattern-gate"
#: real runs write the 10^7-row parquets under the git-ignored artifacts/109; a non-default
#: --spec-dir (tests / smoke) gets its own <spec_dir>/artifacts so it can never overwrite them.
ARTIFACTS = REPO / "artifacts" / "109"
BUNDLE = (
    REPO
    / "artifacts/oof/8bdde26857f62c5571ef45b02954836dacae7e4a2ea9174c12eb0c9209fb691f/bundle.json"
)
BUNDLE_DIGEST = "8bdde26857f62c5571ef45b02954836dacae7e4a2ea9174c12eb0c9209fb691f"
DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
)
WINDOWS = ("discovery", "qualification", "confirmatory")
SMOKE_WINDOWS = {
    "discovery": ["2010-01-01", "2010-01-31"],
    "qualification": ["2010-02-01", "2010-02-28"],
    "confirmatory": ["2010-03-01", "2010-03-31"],
}

SQL = """
with hist as (
  select s.race_id, s.horse_id,
         lag(s.race_date)    over w as pv_date,
         lag(s.race_date, 2) over w as pv2_date,
         lag(s.race_id)      over w as pv_race_id
  from (select rh.race_id, rh.horse_id, r.race_date
        from race_horses rh join races r using(race_id)
        where rh.entry_status = 'started') s
  window w as (partition by s.horse_id order by s.race_date, s.race_id)
),
entered as (select race_id, count(*) as field_size from race_horses group by race_id),
win as (select race_id,
               count(*) filter (where result_status = 'finished' and finish_order = 1) as n_winners
        from race_results group by race_id)
select rh.race_id, rh.horse_id, rh.horse_number, rh.sex, rh.odds,
       r.race_date, r.track_type, r.distance, r.race_class,
       e.field_size,
       (r.race_date - h.pv_date) as interval_days,
       pr.finish_order as prev_finish, pr.result_status as prev_status,
       case when h.pv2_date is null then null
            when (h.pv_date - h.pv2_date) > 70 then 1 else 0 end as tataki_2,
       coalesce(rs.result_status = 'finished' and rs.finish_order = 1, false) as won,
       coalesce(w.n_winners, 0) as n_winners
from race_horses rh
join races r using(race_id)
left join entered e on e.race_id = rh.race_id
left join hist h on h.race_id = rh.race_id and h.horse_id = rh.horse_id
left join race_results pr on pr.race_id = h.pv_race_id and pr.horse_id = rh.horse_id
left join race_results rs on rs.race_id = rh.race_id and rs.horse_id = rh.horse_id
left join win w on w.race_id = rh.race_id
where rh.entry_status = 'started' and r.race_date >= :d_from and r.race_date <= :d_to
order by rh.race_id, rh.horse_number
"""


# ---------------------------------------------------------------------------------------------
# git / io helpers
# ---------------------------------------------------------------------------------------------


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()


def git_dirty() -> bool:
    out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return bool(out.strip())


def run_meta() -> dict:
    return {
        "run_code_sha": git_head(),
        "run_tree_dirty": git_dirty(),
        "run_at": dt.datetime.now(dt.UTC).isoformat(),
    }


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: pathlib.Path, obj, *, append_only: bool = False) -> None:
    if append_only and path.exists():
        raise SystemExit(f"refusing to overwrite {path} (append-only)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default) + "\n")


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    return str(o)


def _rel(path: pathlib.Path) -> str:
    path = pathlib.Path(path).resolve()
    return str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)


def evidence_ref(window: str, path: pathlib.Path) -> dict:
    return {"window": window, "path": _rel(path), "sha256": sha256_file(path)}


# ---------------------------------------------------------------------------------------------
# rows: DB + bundle → pandas → numpy dict
# ---------------------------------------------------------------------------------------------


def load_bundle(path: pathlib.Path = BUNDLE) -> dict:
    b = json.loads(path.read_text())
    if b.get("bundle_digest") != BUNDLE_DIGEST:
        raise SystemExit(f"bundle digest mismatch: {b.get('bundle_digest')}")
    return b["predictions"]


def load_bet_rows(
    d_from: str, d_to: str, *, db_url: str = DB_URL, preds: dict | None = None
) -> pd.DataFrame:
    with create_engine(db_url).connect() as c:
        df = pd.read_sql(text(SQL), c, params={"d_from": d_from, "d_to": d_to})
    preds = load_bundle() if preds is None else preds
    rid = df["race_id"].astype(str)
    hid = df["horse_id"].astype(str)
    df["in_bundle"] = rid.isin(preds.keys()).to_numpy()
    p = np.full(len(df), np.nan)
    for i, (r, h) in enumerate(zip(rid, hid, strict=True)):
        race = preds.get(r)
        if race is not None:
            v = race.get(h)
            if v is not None:
                p[i] = float(v["win"])
    df["p"] = p
    df["prev_finish"] = np.where(
        df["prev_status"].astype(object) == "finished", df["prev_finish"].astype(float), np.nan
    )
    return df


def to_arrays(df: pd.DataFrame) -> dict:
    dates = df["race_date"].astype(str).str.slice(0, 10)
    arr = {
        "race_id": df["race_id"].astype(str).to_numpy(dtype=object),
        "horse_number": df["horse_number"].astype(int).to_numpy(),
        "horse_id": df["horse_id"].astype(str).to_numpy(dtype=object),
        "race_date": dates.to_numpy(dtype=object),
        "year": dates.str.slice(0, 4).astype(int).to_numpy(),
        "track_type": df["track_type"].astype(object).to_numpy(dtype=object),
        "distance": pd.to_numeric(df["distance"], errors="coerce").astype(float).to_numpy(),
        "race_class": df["race_class"].astype(object).to_numpy(dtype=object),
        "race_class_canon": np.asarray([bp.canon_class(x) for x in df["race_class"]], dtype=object),
        "field_size": pd.to_numeric(df["field_size"], errors="coerce").astype(float).to_numpy(),
        "sex": df["sex"].astype(object).to_numpy(dtype=object),
        "sex_group": np.asarray(
            [("F" if s == "牝" else ("M" if s in ("牡", "セ") else None)) for s in df["sex"]],
            dtype=object,
        ),
        "is_summer": dates.str.slice(5, 7)
        .astype(int)
        .isin(list(bp.SUMMER_MONTHS))
        .to_numpy()
        .astype(float),
        "interval_days": pd.to_numeric(df["interval_days"], errors="coerce")
        .astype(float)
        .to_numpy(),
        "prev_finish": pd.to_numeric(df["prev_finish"], errors="coerce").astype(float).to_numpy(),
        "tataki_2": pd.to_numeric(df["tataki_2"], errors="coerce").astype(float).to_numpy(),
        "odds": pd.to_numeric(df["odds"], errors="coerce").astype(float).to_numpy(),
        "p": df["p"].astype(float).to_numpy(),
        "in_bundle": df["in_bundle"].astype(bool).to_numpy(),
        "won": df["won"].astype(bool).to_numpy(),
        "n_winners": df["n_winners"].astype(int).to_numpy(),
    }
    return arr


def build_window(cfg: dict, window: str, preds: dict) -> tuple[dict, dict]:
    d_from, d_to = cfg["windows"][window]
    df = load_bet_rows(d_from, d_to, preds=preds)
    arr = to_arrays(df)
    arr, report = gate.fix_population(arr)
    arr = gate.derive(arr, warmup_through=cfg.get("past_quantile_warmup_through", "2008-12-31"))
    report["window"] = window
    report["from"], report["to"] = d_from, d_to
    return arr, report


def rows_to_frame(arr: dict) -> pd.DataFrame:
    return pd.DataFrame({k: v for k, v in arr.items()})


def frame_to_rows(df: pd.DataFrame) -> dict:
    out = {}
    for k in df.columns:
        col = df[k]
        if col.dtype == object:
            out[k] = col.to_numpy(dtype=object)
        else:
            out[k] = col.to_numpy()
    return out


def rows_snapshot_path(window: str) -> pathlib.Path:
    return ARTIFACTS / f"rows-{window}.parquet"


def save_rows_snapshot(arr: dict, window: str) -> tuple[pathlib.Path, str]:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = rows_snapshot_path(window)
    rows_to_frame(arr).to_parquet(path, index=False)
    return path, sha256_file(path)


def load_rows_snapshot(window: str, expected_sha: str) -> dict:
    path = rows_snapshot_path(window)
    if not path.exists():
        raise SystemExit(f"rows snapshot missing: {path}")
    got = sha256_file(path)
    if got != expected_sha:
        raise SystemExit(f"rows snapshot {window} sha256 {got[:12]} != {expected_sha[:12]}")
    return frame_to_rows(pd.read_parquet(path))


# ---------------------------------------------------------------------------------------------
# frozen artefacts
# ---------------------------------------------------------------------------------------------


def load_family(spec_dir: pathlib.Path, cfg: dict) -> tuple[bp.Family, dict]:
    payload = json.loads((spec_dir / "patterns.json").read_text())
    gate.verify_frozen("patterns", payload, cfg["patterns_hash"])
    fam = bp.enumerate_family()
    if fam.hash() != cfg["patterns_hash"]:
        raise SystemExit("enumeration code no longer reproduces the frozen patterns.json")
    return fam, payload


def load_cfg(
    spec_dir: pathlib.Path, expected_hash: str | None, *, require_frozen: bool = True
) -> dict:
    return gate.load_gate_config(
        spec_dir / "gate-config.json", expected_hash, require_frozen=require_frozen
    )


def require_clean_tree(cfg: dict, what: str) -> None:
    if cfg.get("smoke"):
        return
    if git_dirty():
        raise SystemExit(
            f"{what}: working tree is dirty; commit first (git status --porcelain is non-empty)"
        )


def redact(cfg: dict, value):
    return "<redacted:smoke>" if cfg.get("smoke") else value


# ---------------------------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------------------------


def cmd_freeze(args) -> int:
    spec_dir = pathlib.Path(args.spec_dir).resolve()
    cfg_path = spec_dir / "gate-config.json"
    cfg = json.loads(cfg_path.read_text())
    if args.smoke:
        if spec_dir == DEFAULT_SPEC_DIR.resolve():
            raise SystemExit("--smoke refuses the default spec dir")
        cfg["smoke"] = True
        cfg["windows"] = dict(SMOKE_WINDOWS)
        cfg["bootstrap"] = dict(cfg["bootstrap"], b=200)
        cfg["demotion"] = dict(cfg["demotion"], min_days=3, min_hits=3)
        cfg["selftest"] = dict(
            cfg["selftest"],
            b_inner_size=200,
            b_inner_power=100,
            reps_size_per_config=3,
            reps_power=2,
            rho_grid=[1.0, 1.1],
        )
    else:
        if git_dirty():
            raise SystemExit("freeze: working tree is dirty; commit code and spec artefacts first")
    if (spec_dir / "patterns.json").exists():
        raise SystemExit("freeze: patterns.json already exists (frozen); refusing to overwrite")
    fam = bp.enumerate_family()
    payload = fam.to_dict()
    write_json(spec_dir / "patterns.json", payload)
    if not cfg["windows"]["confirmatory"][1]:
        with create_engine(DB_URL).connect() as c:
            last = c.execute(
                text(
                    "select max(r.race_date) from races r where exists "
                    "(select 1 from race_results rr "
                    "where rr.race_id = r.race_id and rr.result_status = 'finished')"
                )
            ).scalar()
        cfg["windows"]["confirmatory"][1] = str(last)
    cfg["patterns_hash"] = stable_hash(payload)
    cfg["code_sha"] = git_head()
    cfg["frozen_at"] = dt.datetime.now(dt.UTC).isoformat()
    write_json(cfg_path, cfg)
    h = gate_config_hash(cfg)
    write_json(
        spec_dir / "evidence" / "freeze.json",
        {
            "gate_config_hash": h,
            "patterns_hash": cfg["patterns_hash"],
            "n_patterns": len(fam.patterns),
            "code_sha": cfg["code_sha"],
            "confirmatory_end": cfg["windows"]["confirmatory"][1],
            "smoke": bool(args.smoke),
            **run_meta(),
        },
    )
    print(
        f"frozen: patterns={len(fam.patterns)} patterns_hash={cfg['patterns_hash'][:12]} "
        f"gate_config_hash={h}"
    )
    return 0


def _selection_hashes(fam: bp.Family, arrays: dict) -> dict[str, str]:
    out = {}
    for p in fam.patterns:
        m = bp.mask_for(p, arrays) & np.asarray(arrays["primary"], dtype=bool)
        out[p.pattern_id] = gate.selection_hash(arrays["race_id"][m], arrays["horse_number"][m])
    return out


def cmd_selftest(args) -> int:
    spec_dir = pathlib.Path(args.spec_dir).resolve()
    cfg = load_cfg(spec_dir, args.gate_config_hash)
    fam, _ = load_family(spec_dir, cfg)
    preds = load_bundle()
    t0 = time.time()
    arr_conf, rep_conf = build_window(cfg, "confirmatory", preds)
    arr_disc, _ = build_window(cfg, "discovery", preds)
    arr_qual, _ = build_window(cfg, "qualification", preds)
    days_conf = gate.day_universe(arr_conf)
    ids = [p.pattern_id for p in fam.patterns]
    prim = np.asarray(arr_conf["primary"], dtype=bool)
    masks = [bp.mask_for(p, arr_conf) & prim for p in fam.patterns]
    rng = np.random.default_rng(int(cfg["bootstrap"]["seed"]) + 1)
    # tau from the DISCOVERY window (screening data), reference mask = cap21_all
    rs_d, order_d = gate.race_structure(arr_disc, gate.day_universe(arr_disc))
    ref = gate.controls_masks(arr_disc)["cap21_all"] & np.asarray(arr_disc["primary"], dtype=bool)
    tau_info = gate.estimate_day_effect(
        rs_d,
        ref[order_d],
        np.asarray(arr_disc["won"], dtype=bool)[order_d],
        rng,
        sims=2 if cfg.get("smoke") else 5,
        it=3 if cfg.get("smoke") else 10,
    )
    def log(m: str) -> None:
        print(f"  [{time.time() - t0:7.0f}s] {m}", flush=True)
    report = gate.selftest(
        arr_conf,
        masks,
        ids,
        days_conf,
        cfg,
        tau=float(tau_info["tau"]),
        rng=rng,
        progress=log,
        checkpoint_dir=(spec_dir / "evidence"),
        extrapolate_only=bool(args.extrapolate),
    )
    report["tau_estimate"] = tau_info
    report["population_confirmatory"] = rep_conf
    report["selection_hashes"] = {
        "discovery": _selection_hashes(fam, arr_disc),
        "qualification": _selection_hashes(fam, arr_qual),
    }
    report["gate_config_hash"] = gate_config_hash(cfg)
    report["patterns_hash"] = cfg["patterns_hash"]
    report["smoke"] = bool(cfg.get("smoke"))
    report["extrapolate_only"] = bool(args.extrapolate)
    report["passed"] = (
        True if cfg.get("smoke") else bool(report["size_passed"]) and not args.extrapolate
    )
    report["elapsed_total_seconds"] = time.time() - t0
    report.update(run_meta())
    out = (
        spec_dir
        / "evidence"
        / ("selftest-extrapolate.json" if args.extrapolate else "selftest.json")
    )
    write_json(out, report)
    if cfg.get("smoke"):
        print(f"selftest(smoke): ran size/power configs; numbers redacted; wrote {out}")
    else:
        print(
            f"selftest: size_passed={report['size_passed']} passed={report['passed']} "
            f"tau={tau_info['tau']:.4f} elapsed={report['elapsed_total_seconds']:.0f}s wrote {out}"
        )
    if args.extrapolate:
        secs = [v.get("seconds_per_rep") for v in list(report["size"].values())]
        secs += [c.get("seconds_per_rep") for v in report["power"].values() for c in v["curve"]]
        secs = [s for s in secs if s]
        if secs:
            st = cfg["selftest"]
            n_size = 5 * 2 * int(st["reps_size_per_config"])
            n_power = 5 * 4 * len(st["rho_grid"]) * int(st["reps_power"])
            est = float(np.mean(secs)) * (n_size + n_power)
            print(
                f"extrapolated total ≈ {est / 60:.0f} min "
                f"(size {n_size} reps, power {n_power} reps)"
            )
        return 0
    return 0 if report["passed"] else 2


def _score_window(
    cfg: dict, arr: dict, fam: bp.Family, *, m: int = 1
) -> tuple[gate.DayMatrix, list, list]:
    days = gate.day_universe(arr)
    prim = np.asarray(arr["primary"], dtype=bool)
    ids = [p.pattern_id for p in fam.patterns] + [c.pattern_id for c in fam.controls]
    masks = [bp.mask_for(p, arr) & prim for p in fam.patterns] + [
        gate.controls_masks(arr)[c.pattern_id] & prim for c in fam.controls
    ]
    dm = gate.apply_masks(arr, masks, ids, days)
    scores, bs = gate.score_patterns(dm, cfg, block="race_day", m=m)
    return dm, scores, masks


def _write_bets(masks: list[np.ndarray], arr: dict, window: str) -> pathlib.Path:
    """Compact bet index (pattern_idx, row_idx) into the rows snapshot — joins back to the
    full columns bit-for-bit, and keeps 10^7-row windows out of git."""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    pidx = []
    ridx = []
    prim = np.asarray(arr["primary"], dtype=bool)
    for i, m in enumerate(masks):
        idx = np.flatnonzero(np.asarray(m, dtype=bool) & prim)
        pidx.append(np.full(len(idx), i, dtype=np.int16))
        ridx.append(idx.astype(np.int32))
    df = pd.DataFrame({"pattern_idx": np.concatenate(pidx), "row_idx": np.concatenate(ridx)})
    path = ARTIFACTS / f"{window}-bets.parquet"
    df.to_parquet(path, index=False)
    return path


def _summary(
    scores: list[gate.PatternScore], dm: gate.DayMatrix, cfg: dict, fam: bp.Family
) -> dict:
    return {
        "ids": list(dm.ids),
        "days": list(dm.days),
        "n_patterns": len(fam.patterns),
        "n_controls": len(fam.controls),
        "scores": [s.to_dict() for s in scores],
        "bootstrap": cfg["bootstrap"],
        "demotion": cfg["demotion"],
    }


def cmd_screen(args) -> int:
    spec_dir = pathlib.Path(args.spec_dir).resolve()
    cfg = load_cfg(spec_dir, args.gate_config_hash)
    require_clean_tree(cfg, "screen")
    st_path = spec_dir / "evidence" / "selftest.json"
    if not st_path.exists():
        raise SystemExit("screen: evidence/selftest.json missing — run selftest first")
    selftest = json.loads(st_path.read_text())
    if not (selftest.get("passed") or selftest.get("smoke")):
        raise SystemExit("screen: selftest did not pass (passed=false); fix the gate first")
    if selftest.get("gate_config_hash") != gate_config_hash(cfg):
        raise SystemExit("screen: selftest.json was produced under a different gate-config")
    if (spec_dir / "evidence" / "survivors.json").exists():
        raise SystemExit("screen: survivors.json already exists (append-only)")
    meta0 = run_meta()  # taken BEFORE this run writes anything (its own outputs dirty the tree)
    fam, _ = load_family(spec_dir, cfg)
    preds = load_bundle()
    t0 = time.time()
    population: dict = {
        "windows": {},
        "expected_races_per_year": cfg.get("expected_races_per_year", {}),
        "bundle_digest": BUNDLE_DIGEST,
        "gate_config_hash": gate_config_hash(cfg),
    }
    arrays: dict[str, dict] = {}
    for w in WINDOWS:
        arr, rep = build_window(cfg, w, preds)
        path, sha = save_rows_snapshot(arr, w)
        rep["rows_snapshot"] = _rel(path)
        rep["rows_hash"] = sha
        rep["day_universe"] = gate.day_universe(arr)
        exp = cfg.get("expected_races_per_year", {})
        rep["expected_delta_by_year"] = {
            y: (n - int(exp[y])) if (y in exp and exp[y] is not None) else None
            for y, n in rep["races_by_year"].items()
        }
        population["windows"][w] = rep
        arrays[w] = arr
        print(
            f"  [{time.time() - t0:6.0f}s] rows {w}: kept_races={rep['kept_races']} "
            f"rows={rep['kept_rows_primary']}"
        )
    population["population_hash"] = stable_hash(
        {w: population["windows"][w]["population_hash"] for w in WINDOWS}
    )
    # selection hashes must match what selftest recorded (no code drift after freeze)
    for w in ("discovery", "qualification"):
        got = _selection_hashes(fam, arrays[w])
        if got != selftest["selection_hashes"][w]:
            raise SystemExit(
                f"screen: {w} selection hashes differ from selftest.json (code or data drift)"
            )
    write_json(spec_dir / "population.json", population)

    results = {}
    for w in ("discovery", "qualification"):
        dm, scores, masks = _score_window(cfg, arrays[w], fam, m=1)
        bets_path = _write_bets(masks, arrays[w], w)
        summ = _summary(scores, dm, cfg, fam)
        summ["evidence_refs"] = [evidence_ref(w, bets_path), evidence_ref(w, rows_snapshot_path(w))]
        summ["window"] = w
        summ.update(meta0)
        write_json(spec_dir / "evidence" / f"screening-{w}-summary.json", summ)
        results[w] = {s.pattern_id: s for s in scores}
        print(f"  [{time.time() - t0:6.0f}s] scored {w}: {len(scores)} series")

    # survivor rule (research D3)
    n_pat = len(fam.patterns)
    reasons: dict[str, str] = {}
    cand = []
    for p in fam.patterns:
        d = results["discovery"][p.pattern_id]
        if d.demoted_reason == "not_fired":
            reasons[p.pattern_id] = "not_fired"
            continue
        if d.demoted_reason or not (d.roi >= float(cfg["survivor_rule"]["point_min"])):
            reasons[p.pattern_id] = "discovery"
            continue
        qv = results["qualification"][p.pattern_id]
        if qv.demoted_reason or not (qv.roi >= float(cfg["survivor_rule"]["point_min"])):
            reasons[p.pattern_id] = "qualification"
            continue
        lcb = min(
            d.ci_low if d.ci_low is not None else -np.inf,
            qv.ci_low if qv.ci_low is not None else -np.inf,
        )
        cand.append((p.pattern_id, float(lcb), d.selection_hash, qv.selection_hash))
    cand.sort(key=lambda t: (-t[1], t[0]))
    # merge identical bet sets (both windows); drop near-duplicates by Jaccard on
    # discovery ∪ qualification
    kept: list[dict] = []
    idx_disc = {p.pattern_id: i for i, p in enumerate(fam.patterns)}
    bet_sets: dict[str, set] = {}

    def bet_set(pid: str) -> set:
        if pid not in bet_sets:
            s = set()
            for w in ("discovery", "qualification"):
                a = arrays[w]
                m = bp.mask_for(fam.patterns[idx_disc[pid]], a) & np.asarray(
                    a["primary"], dtype=bool
                )
                s |= set(zip(a["race_id"][m].tolist(), a["horse_number"][m].tolist(), strict=True))
            bet_sets[pid] = s
        return bet_sets[pid]

    jmax = float(cfg["survivor_rule"]["jaccard_max"])
    kmax = int(cfg["survivor_rule"]["max_survivors"])
    for pid, lcb, hd, hq in cand:
        dup = None
        for k in kept:
            if (hd, hq) == (k["hash_discovery"], k["hash_qualification"]):
                dup = k
                break
            inter = len(bet_set(pid) & bet_set(k["pattern_id"]))
            union = len(bet_set(pid) | bet_set(k["pattern_id"]))
            if union and inter / union > jmax:
                dup = k
                break
        if dup is not None:
            dup["merged_from"].append(pid)
            reasons[pid] = "duplicate"
            continue
        if len(kept) >= kmax:
            reasons[pid] = "rank"
            continue
        kept.append(
            {
                "pattern_id": pid,
                "rank_key": lcb,
                "hash_discovery": hd,
                "hash_qualification": hq,
                "merged_from": [],
            }
        )
    counts = {"discovery": 0, "qualification": 0, "rank": 0, "duplicate": 0, "not_fired": 0}
    for r in reasons.values():
        counts[r] += 1
    surv_payload = {
        "survivors": [k["pattern_id"] for k in kept],
        "ranked": kept,
        "screened_out_counts": counts,
        "n_patterns": n_pat,
        "patterns_hash": cfg["patterns_hash"],
        "population_hash": population["population_hash"],
        "gate_config_hash": gate_config_hash(cfg),
    }
    surv_payload["survivors_hash"] = stable_hash(surv_payload["survivors"])
    surv_payload.update(meta0)
    write_json(spec_dir / "evidence" / "survivors.json", surv_payload, append_only=True)
    if cfg.get("smoke"):
        print(
            f"screen(smoke): fired={n_pat - counts['not_fired']} survivors=<redacted> "
            f"(structure only) elapsed={time.time() - t0:.0f}s"
        )
    else:
        print(
            f"screen: survivors={len(kept)} screened_out={counts} elapsed={time.time() - t0:.0f}s"
        )
    return 0


def _sensitivity_scores(
    cfg: dict, arr: dict, fam: bp.Family, ids: list[str], m: int
) -> dict[str, list]:
    days = gate.day_universe(arr)
    by_id = {p.pattern_id: p for p in fam.patterns}
    ctrl = gate.controls_masks(arr)
    out: dict[str, list] = {}
    for version in list(gate.ANALYSIS_VERSIONS) + list(gate.DEAD_HEAT_VERSIONS):
        payout, eligible = gate.payout_vector(arr, version)
        masks = [(bp.mask_for(by_id[i], arr) if i in by_id else ctrl[i]) for i in ids]
        dm = gate.apply_masks(arr, masks, ids, days, payout=payout, eligible=eligible)
        block = version if version in gate.ANALYSIS_VERSIONS else "race_day"
        scores, _ = gate.score_patterns(dm, cfg, block=block, m=m)
        out[version] = scores
    return out


def cmd_confirm(args) -> int:
    spec_dir = pathlib.Path(args.spec_dir).resolve()
    cfg = load_cfg(spec_dir, args.gate_config_hash)
    require_clean_tree(cfg, "confirm")
    verdict_path = spec_dir / "verdict.json"
    if verdict_path.exists():
        raise SystemExit("confirm: verdict.json already exists (append-only; no --force)")
    fam, _ = load_family(spec_dir, cfg)
    population = json.loads((spec_dir / "population.json").read_text())
    surv = json.loads((spec_dir / "evidence" / "survivors.json").read_text())
    if (
        surv["gate_config_hash"] != gate_config_hash(cfg)
        or surv["patterns_hash"] != cfg["patterns_hash"]
    ):
        raise SystemExit("confirm: survivors.json was produced under different frozen artefacts")
    if surv["population_hash"] != population["population_hash"]:
        raise SystemExit(
            "confirm: population hash mismatch between population.json and survivors.json"
        )
    gate.verify_frozen(
        "survivors", surv["survivors"], args.survivors_hash or surv["survivors_hash"]
    )
    selftest = json.loads((spec_dir / "evidence" / "selftest.json").read_text())
    meta0 = run_meta()  # BEFORE this run writes evidence (its own outputs dirty the tree)
    t0 = time.time()
    wrep = population["windows"]["confirmatory"]
    arr = load_rows_snapshot("confirmatory", wrep["rows_hash"])
    survivors = list(surv["survivors"])
    ctrl_ids = [c.pattern_id for c in fam.controls]
    ids = survivors + ctrl_ids
    m = max(len(survivors), 1)
    versions = _sensitivity_scores(cfg, arr, fam, ids, m)
    # controls are descriptive: strip them before the state machine
    surv_versions = {
        v: [s for s in scores if s.pattern_id in survivors] for v, scores in versions.items()
    }
    per_survivor: list[dict] = []
    states = {s: 0 for s in gate.STATES}
    if survivors:
        dec = gate.decide(surv_versions, alpha=float(cfg["test"]["holm_alpha_one_sided"]))
        prim = {s.pattern_id: s for s in surv_versions["race_day"]}
        # max-T (reference) from the primary bootstrap replicates
        days = gate.day_universe(arr)
        payout, eligible = gate.payout_vector(arr, "race_day")
        by_id = {p.pattern_id: p for p in fam.patterns}
        dm = gate.apply_masks(
            arr,
            [bp.mask_for(by_id[i], arr) for i in survivors],
            survivors,
            days,
            payout=payout,
            eligible=eligible,
            with_hashes=False,
        )
        _, bs = gate.score_patterns(dm, cfg, block="race_day", m=m)
        upper = gate.maxt_upper(
            bs.replicates, bs.point, alpha=float(cfg["test"]["holm_alpha_one_sided"])
        )
        for i, pid in enumerate(survivors):
            pat = by_id[pid]
            s = prim[pid]
            d = dec[pid]
            states[d["state"]] += 1
            per_survivor.append(
                {
                    "pattern_id": pid,
                    "label_ja": pat.label_ja,
                    "state": d["state"],
                    "reason": d["reason"],
                    "by_version": d["by_version"],
                    "available_at": pat.available_at,
                    "historical_close_signal": pat.available_at == "closing",
                    "p_profit_one_sided": s.p_profit_one_sided,
                    "p_futility_one_sided": s.p_futility_one_sided,
                    "roi": s.roi,
                    "ci": [s.ci_low, s.ci_high],
                    "n_bets": s.n_bets,
                    "n_hits": s.n_hits,
                    "n_days": s.n_days,
                    "max_single_hit_share": s.max_single_hit_share,
                    "leave_one_hit_out_roi": s.leave_one_hit_out_roi,
                    "mde_80": s.mde_80,
                    "simultaneous_upper_maxT": float(upper[i]),
                    "demoted_reason": s.demoted_reason,
                    "by_year": s.by_year,
                    "selection_hash": s.selection_hash,
                    "sensitivities": {
                        v: {
                            "roi": sc.roi,
                            "p_profit_one_sided": sc.p_profit_one_sided,
                            "p_futility_one_sided": sc.p_futility_one_sided,
                            "demoted_reason": sc.demoted_reason,
                        }
                        for v, scores in versions.items()
                        for sc in scores
                        if sc.pattern_id == pid
                    },
                }
            )
    n_pat = len(fam.patterns)
    states["SCREENED_OUT"] = n_pat - len(survivors)
    controls = {}
    for c in ctrl_ids:
        sc = next(s for s in versions["race_day"] if s.pattern_id == c)
        controls[c] = (
            {"accounting_sentinel": 1.0, "roi": None}
            if c == "no_bet"
            else {
                "roi": sc.roi,
                "ci": [sc.ci_low, sc.ci_high],
                "n_bets": sc.n_bets,
                "n_hits": sc.n_hits,
            }
        )
    controls["ev1_all"] = {"alias_of": "X.ev_ge_1"}
    # evidence
    summ = {
        "ids": ids,
        "days": gate.day_universe(arr),
        "window": "confirmatory",
        "versions": {v: [s.to_dict() for s in scores] for v, scores in versions.items()},
        "bootstrap": cfg["bootstrap"],
        "demotion": cfg["demotion"],
        "holm_m": m,
    }
    by_id_all = {p.pattern_id: p for p in fam.patterns}
    ctrl_masks = gate.controls_masks(arr)
    masks_all = [(bp.mask_for(by_id_all[i], arr) if i in by_id_all else ctrl_masks[i]) for i in ids]
    bets_path = _write_bets(masks_all, arr, "confirmatory")
    # keep a git-committed copy of the confirmatory bets (survivors + controls only)
    committed = spec_dir / "evidence" / "confirmatory-bets.parquet"
    pd.read_parquet(bets_path).to_parquet(committed, index=False)
    refs = [
        evidence_ref("confirmatory", bets_path),
        evidence_ref("confirmatory", committed),
        evidence_ref("confirmatory", rows_snapshot_path("confirmatory")),
    ]
    summ["evidence_refs"] = refs
    summ.update(meta0)
    write_json(spec_dir / "evidence" / "confirmatory-summary.json", summ)
    meta = meta0
    verdict = gate.build_verdict(
        cfg=cfg,
        gate_config_hash=gate_config_hash(cfg),
        patterns_hash=cfg["patterns_hash"],
        population_hash=population["population_hash"],
        survivors_hash=surv["survivors_hash"],
        bundle_digest=BUNDLE_DIGEST,
        run_code_sha=meta["run_code_sha"],
        run_tree_dirty=meta["run_tree_dirty"],
        windows=cfg["windows"],
        states=states,
        per_survivor=per_survivor,
        controls=controls,
        evidence_refs=refs + [evidence_ref("selftest", spec_dir / "evidence" / "selftest.json")],
        selftest_report=selftest,
        screened_out_reasons=surv["screened_out_counts"],
    )
    verdict["smoke"] = bool(cfg.get("smoke"))
    verdict["elapsed_seconds"] = time.time() - t0
    gate.validate_verdict(verdict)
    write_json(verdict_path, verdict, append_only=True)
    if cfg.get("smoke"):
        print(f"confirm(smoke): survivors=<redacted> states=<redacted> wrote {verdict_path}")
    else:
        print(
            f"confirm: states={verdict['states']} "
            f"controls={ {k: v.get('roi') for k, v in controls.items()} }"
        )
        print(verdict["conclusion_ja"])
    return 0


def cmd_recompute(args) -> int:
    spec_dir = pathlib.Path(args.spec_dir).resolve()
    cfg = load_cfg(spec_dir, args.gate_config_hash)
    fam, _ = load_family(spec_dir, cfg)
    population = json.loads((spec_dir / "population.json").read_text())
    w = args.window
    key = "confirmatory" if w == "confirmatory" else w.replace("screening-", "")
    wrep = population["windows"][key]
    arr = load_rows_snapshot(key, wrep["rows_hash"])
    if w == "confirmatory":
        summ = json.loads((spec_dir / "evidence" / "confirmatory-summary.json").read_text())
        ids = summ["ids"]
        m = int(summ["holm_m"])
        versions = _sensitivity_scores(cfg, arr, fam, ids, m)
        for v, scores in versions.items():
            got = json.dumps([s.to_dict() for s in scores], sort_keys=True, default=str)
            want = json.dumps(summ["versions"][v], sort_keys=True, default=str)
            if got != want:
                print(f"recompute: MISMATCH in version {v}")
                return 1
    else:
        summ = json.loads((spec_dir / "evidence" / f"screening-{key}-summary.json").read_text())
        dm, scores, _ = _score_window(cfg, arr, fam, m=1)
        got = json.dumps([s.to_dict() for s in scores], sort_keys=True, default=str)
        want = json.dumps(summ["scores"], sort_keys=True, default=str)
        if got != want:
            print("recompute: MISMATCH")
            return 1
    # bets index must reproduce the day universe & masks
    bets = pd.read_parquet(ARTIFACTS / f"{key}-bets.parquet")
    if bets["row_idx"].max() >= len(arr["odds"]):
        print("recompute: bets index out of range")
        return 1
    print(f"recompute {w}: bit-identical ({len(summ.get('ids', []))} series)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--spec-dir", default=str(DEFAULT_SPEC_DIR))
    ap.add_argument("--gate-config-hash", default=None)
    ap.add_argument("--survivors-hash", default=None)
    ap.add_argument("--smoke", action="store_true")
    sub = ap.add_subparsers(dest="sub", required=True)
    sub.add_parser("freeze")
    s = sub.add_parser("selftest")
    s.add_argument(
        "--extrapolate", action="store_true", help="20 reps per config, print the ETA and exit"
    )
    sub.add_parser("screen")
    sub.add_parser("confirm")
    r = sub.add_parser("recompute")
    r.add_argument(
        "--window",
        required=True,
        choices=["screening-discovery", "screening-qualification", "confirmatory"],
    )
    args = ap.parse_args(argv)
    spec_dir = pathlib.Path(args.spec_dir).resolve()
    if args.smoke and spec_dir == DEFAULT_SPEC_DIR.resolve():
        raise SystemExit("--smoke refuses the default spec dir (protects the frozen artefacts)")
    global ARTIFACTS
    if spec_dir != DEFAULT_SPEC_DIR.resolve():
        ARTIFACTS = spec_dir / "artifacts"
    if args.sub != "freeze" and args.gate_config_hash is None:
        raise SystemExit("--gate-config-hash is required for every subcommand except freeze")
    return {
        "freeze": cmd_freeze,
        "selftest": cmd_selftest,
        "screen": cmd_screen,
        "confirm": cmd_confirm,
        "recompute": cmd_recompute,
    }[args.sub](args)


if __name__ == "__main__":
    sys.exit(main())
