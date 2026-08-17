"""verdict artifact の台帳 — 記録された主張と artifact の数値を突き合わせる(読み取り専用)。

なぜ: 070 の再実行コミット `b66a139` は F03 を「+0.000675 で決定的に悪い」と書いたが、
**同じコミットが追加した artifact は −0.002343 CI[−0.00519, +0.00039]**だった。点推定は候補
有利で、CI 上限はゼロから 0.0004 しか離れていない。「決定的に悪い」で閉じた軸が、実際には
測れなかっただけだった。この種の取り違えが他にもあるかを機械的に見る。

**過去の verdict を再判定するものではない**(記録された verdict は不変)。やるのは
「artifact に何と書いてあるか」と「我々が何と書き残したか」の照合だけ。v3 の判定写像を
併記するのは読み方の補助であって、過去の判定の書き換えではない。

使い方:
    cd training && uv run python ../scripts/verdict_ledger.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: 記録側の主張。出典を必ず添える(二次資料であることを忘れないため)。
CLAIMS = {
    "070 F03": {
        "file": "out/f03.json",
        "claim": "+0.000675 で決定的に悪い / REJECT",
        "source": "commit b66a139 本文 + memory feature-070",
    },
    "070 F04": {
        "file": "out/f04.json",
        "claim": "NO_DECISION",
        "source": "commit b66a139 本文",
    },
    "070 F05support": {
        "file": "out/f05_support.json",
        "claim": "NO_DECISION / 点推定 -0.00072",
        "source": "commit b66a139 本文",
    },
    "088 着順分解": {
        "file": "artifacts/088_paired_report.json",
        "claim": "REJECT / diff -0.000551 / CI[-0.00221,+0.00117]",
        "source": "CLAUDE.md 088 要約",
    },
    "085 arm E": {
        "file": "out/085-armE-verdict.json",
        "claim": "ADOPT / -0.012838 / CI[-0.014835,-0.010920]",
        "source": "spec 085 §11.1",
    },
    "073 calib-split B": {
        "file": "out/073-calibsplit-B-verdict.json",
        "claim": "NO_DECISION / -0.003813",
        "source": "memory active-model-booster-stops-2020",
    },
    "073 calib-split C/D": {
        "file": "out/073-calibsplit-CD-verdict.json",
        "claim": "REJECT(calibration) / -0.011879",
        "source": "memory active-model-booster-stops-2020",
    },
    "069 F02": {
        "file": "specs/069-past-odds-features/f02_verdict.json",
        "claim": "ADOPT",
        "source": "CLAUDE.md 069 要約",
    },
}

#: v3 の recent guard は「自信を持って margin より悪い」ときだけ FAIL。旧 v2 はゼロ許容の
#: 符号テストで、帰無下でも 39% しか通らなかった。旧 artifact には窓ごとの CI が無いので、
#: 点推定と margin の比較で「v3 なら FAIL になり得たか」を保守的に見る。
RECENT_MARGIN = 0.005


def v3_reading(g: dict, ci: dict) -> str:
    """v3 の判定写像を当てるとどう読めるか(再判定ではなく読み方の補助)。"""
    rs = g.get("reasons") or {}
    windows = (rs.get("recent") or {}).get("windows") or {}
    # v2 の degraded は点推定 > 0。v3 は ci_low > margin なので、点推定が margin 未満なら
    # v3 では FAIL になり得ない。
    recent_v3_fail = any(
        isinstance(w, dict) and float(w.get("diff", 0.0)) > RECENT_MARGIN
        for w in windows.values()
    )
    if not g.get("primary"):
        return "REJECT(点推定で負け)"
    if recent_v3_fail:
        return "REJECT(直近窓が確信的に悪い)"
    if not g.get("top_noninferior") or not g.get("calibration"):
        return "REJECT(top2/3 か校正)"
    if not g.get("stat_guard"):
        return "NO_DECISION(検出力不足)"
    return "ADOPT 相当(部分集団は別途)"


def main() -> None:
    print(f"{'':<20}{'artifact 点推定':>16}{'CI':>26}{'記録された主張':<34}{'v3 の読み'}")
    print("-" * 130)
    mismatches = []
    for name, meta in CLAIMS.items():
        p = ROOT / meta["file"]
        if not p.exists():
            print(f"{name:<20}  (artifact なし: {meta['file']})")
            continue
        r = json.loads(p.read_text())
        ci = r.get("bootstrap_ci") or {}
        g = r.get("gate") or {}
        pt = ci.get("point")
        lo, hi = ci.get("ci_low"), ci.get("ci_high")
        cis = f"[{lo:+.5f}, {hi:+.5f}]" if lo is not None else "(なし)"
        print(f"{name:<20}{pt:>+16.6f}{cis:>26}  {meta['claim']:<32}{v3_reading(g, ci)}")
        # 記録の数値が artifact のどの欄にも見当たらないものを拾う
        fields = {pt, lo, hi}
        for v in (r.get("periods") or {}).values():
            if isinstance(v, dict) and "diff" in v:
                fields.add(round(v["diff"], 6))
        import re
        for m in re.findall(r"[-+]\d\.\d+", meta["claim"]):
            val = float(m)
            if not any(abs(val - f) < 5e-5 for f in fields if f is not None):
                mismatches.append((name, val, meta["source"]))

    if mismatches:
        print("\n【不一致】記録された数値が artifact のどの欄にも見当たらない:")
        for name, val, src in mismatches:
            print(f"  {name}: 記録は {val:+.6f} — 出典 {src}")
    else:
        print("\n記録された数値はすべて artifact のどこかに一致した。")

    print("\n注: 記録された verdict は不変。この表は照合であって再判定ではない。")


if __name__ == "__main__":
    main()
