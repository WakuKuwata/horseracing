"""結果 JSON を表に要約する(レポート作成用)。

    cd training && uv run python ../scripts/roi_explore/summarize.py [--run a1]
"""

from __future__ import annotations

import argparse
import glob
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import evaluate as ev  # noqa: E402

ART = ev.ART


def fmt_win(st: dict, boot: dict | None = None) -> str:
    if not st or not st.get("n_bets"):
        return "-"
    s = f"{st['roi']:.3f} (n={st['n_bets']:,} hit={st['n_hits']} yrs≥1 {st['years_ge1']}/{st['years_n']})"
    if boot:
        s += f" CI[{boot['ci_lo']:.3f},{boot['ci_hi']:.3f}]"
    return s


def arm_a(run: str) -> None:
    d = ART / "results" / run
    if not (d / "summary.json").exists():
        print(f"[Arm A] {run}: not finished"); return
    sm = json.loads((d / "summary.json").read_text())
    print(f"=== Arm A/D run={run}: patterns={sm['n_patterns']} rejected={sm['n_rejected']} elapsed={sm['elapsed_s']}s")
    print("  by_kind:", sm["by_kind"], " by_source:", sm["by_source"])
    print("  stage_counts:", sm["stage_counts"], " reasons:", sm["stage_reasons"])
    print(f"  reached_C={sm.get('n_reached_C')} confirmed_holm={sm.get('n_confirmed_holm')} "
          f"ci_lo>1={sm['n_survivors_ci']} point>=1(weak)={sm['n_weak_point']}")
    sv = json.loads((d / "survivors.json").read_text())
    for key in ("ci_gt1", "point_ge1"):
        for rec in sv[key]:
            print(f"  [{key}] {rec['pattern_id']} ({rec['label_ja']}) kind={rec['kind']} bet={rec['bet_type']}")
            for w in ("D", "Q", "C"):
                if w in rec:
                    print(f"      {w}: {fmt_win(rec[w], rec.get('C_boot') if w == 'C' else None)}")
            if "C_test" in rec:
                print(f"      C one-sided p={rec['C_test']['p_one_sided']:.4f} holm={rec.get('confirm_holm')}")
    near = json.loads((d / "near_misses.json").read_text())
    print(f"  --- near misses (top {min(15, len(near))} by min(D,Q) roi) ---")
    for rec in near[:15]:
        line = f"  {rec['pattern_id'][:48].ljust(48)} {rec['bet_type']:8s} D {fmt_win(rec.get('D'))} | Q {fmt_win(rec.get('Q'))} | C {fmt_win(rec.get('C'), rec.get('C_boot'))}"
        print(line, "::", rec["label_ja"][:40], f"[{rec['stage']}:{rec['stage_reason']}]")
    ctl = json.loads((d / "controls.json").read_text())
    print("  --- controls ---")
    for rec in ctl:
        parts = [rec["pattern_id"].ljust(18)]
        for w in ("D", "Q", "C"):
            if w in rec and rec[w].get("n_bets"):
                parts.append(f"{w} {fmt_win(rec[w], rec.get(f'{w}_boot'))}")
        print("  " + " | ".join(parts))
    if (d / "null.json").exists():
        nl = json.loads((d / "null.json").read_text())
        print("  --- q-null diagnostic ---", {k: v for k, v in nl.items() if k in ("reps", "n_patterns", "mean_pass_DQ", "p_any_pass_DQ", "pass_hist", "max_roi_D_quantiles")})


def arm_b() -> None:
    for f in sorted(glob.glob(str(ART / "results" / "armB_*" / "result.json"))):
        r = json.loads(open(f).read())
        nl = r["null"].get("summary", {})
        print(f"=== Arm B {r['kind']}: atoms={r['n_atoms']} observed pass D∧Q={r['observed_n_pass_DQ']} | null mean_pass={nl.get('mean_pass_DQ')} p_any={nl.get('p_any_pass_DQ')} best_D p50={nl.get('best_D_roi_p50'):.3f} p95={nl.get('best_D_roi_p95'):.3f}")
        for o in r["observed"]:
            if o["pass_DQ"]:
                cb = o.get("C_boot", {})
                print(f"   PASS_DQ D {o['D']['roi']:.3f} Q {o['Q']['roi']:.3f} C {o['C']['roi']:.3f} CI[{cb.get('ci_lo', float('nan')):.3f},{cb.get('ci_hi', float('nan')):.3f}] n_C={o['C']['n_bets']} :: {' & '.join(o['conditions'])}")


def arm_c() -> None:
    print("=== Arm C variants (policy pred>20&odds<21 and pred>10&odds<21; ROI D / Q / C) ===")
    for f in sorted(glob.glob(str(ART / "results" / "armC_*" / "result.json"))):
        r = json.loads(open(f).read())
        tag = r["tag"]
        out = [tag.ljust(44), f"corr={r['corr_pred_ret']:.4f}", f"top_dec={r['policies']['top_decile']['ALL']['roi']:.3f}"]
        for pol in ("pred>20&odds<21", "pred>10&odds<21", "pred>0&odds<21"):
            rec = r["policies"].get(pol, {})
            cells = []
            for w in ("D", "Q", "C"):
                st = rec.get(w, {})
                cells.append(f"{st['roi']:.3f}({st['n_bets']})" if st.get("n_bets") else "-")
            cb = rec.get("C_boot", {})
            ci = f"CI[{cb['ci_lo']:.2f},{cb['ci_hi']:.2f}]" if cb else ""
            out.append(f"{pol}: " + "/".join(cells) + ci)
        print("  " + " | ".join(out))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="a1")
    args = ap.parse_args(argv)
    arm_a(args.run)
    arm_b()
    arm_c()
    return 0


if __name__ == "__main__":
    sys.exit(main())
