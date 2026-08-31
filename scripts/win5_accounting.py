"""扉A: WIN5 キャリーオーバーの会計的 kill-test(モデル不要・codex 推奨の第一段)。

問い: 「当週の投票者**全体**がプラスになった回(実効払戻率 > 1.0)は過去に実在したか。
それは**事前に**識別できたか」。市場に勝つ必要のない唯一の JRA 内機構の実在確認。

会計(codex 検算済み)
--------------------
  払戻総額 = 払戻金(100円あたり) × 的中票数
  払戻総額 = R·P + C_in − C_out   (R = 払戻率 0.70、プレミアム日 0.80 がありうる)
  的中者ゼロの回: C_out = R·P + C_in(全額繰越)
  実効払戻率 rate = 払戻総額 / P
  C_in はチェーンで復元(2011 初回 = 0)。R は {0.70, 0.80} のうちチェーン整合する方。

判定規則(実行前に固定)
----------------------
  rate > 1.0 の回数と頻度を数える。**扉が開いている条件 = そのような回が実在し、かつ
  C_in(前週に確定・公知)と P の予測から事前に識別可能**(C_in/P > 0.3 が目安)。
  15 年で数回未満・または P の膨張が C/P を常に 0.3 未満に薄めるなら、扉は実質閉じ。

外部検証: JRA 公式の大口キャリーオーバー実額(2026-02-01=5億3990万5240円 等)と
チェーン計算が一致すること。

    python3 scripts/win5_accounting.py --cache ../out/win5_cache
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re


def yen(s: str) -> int:
    s = s.replace(",", "")
    val = 0
    m = re.match(r"(?:(\d+)億)?(?:(\d+)万)?(\d+)?円?$", s)
    if not m:
        raise ValueError(s)
    a, b, c = m.groups()
    val = int(a or 0) * 10**8 + int(b or 0) * 10**4 + int(c or 0)
    return val


def parse_event(path: pathlib.Path) -> dict | None:
    s = path.read_bytes().decode("utf-8", errors="replace")
    txt = re.sub(r"<script.*?</script>", "", s, flags=re.S)
    txt = re.sub(r"<[^>]+>", "\n", txt)
    lines = [x.strip() for x in txt.split("\n") if x.strip()]

    def after(key):
        for j, x in enumerate(lines):
            if x == key:
                return lines[j + 1]
        return None

    sales_s = after("発売金額")
    pay_s = after("払戻金")
    votes_s = after("的中票数")
    if not sales_s or not pay_s:
        return None
    d = {"date": path.stem.split("_")[1]}
    d["sales"] = yen(sales_s)
    # 的中なしの回は 払戻金 が「該当なし」等になる
    try:
        d["dividend"] = yen(pay_s)
    except ValueError:
        d["dividend"] = None
    try:
        d["winners"] = int(votes_s.replace("票", "").replace(",", "")) if votes_s else None
    except ValueError:
        d["winners"] = None
    # 対象 5 レース(会場+R)と的中馬番
    d["legs"] = re.findall(r"(中山|東京|京都|阪神|中京|小倉|福島|新潟|札幌|函館)(\d+)R", s)[:5]
    try:
        i = lines.index("的中馬番")
        d["combo"] = [int(x) for x in lines[i + 1:i + 6]]
    except (ValueError, IndexError):
        d["combo"] = None
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="../out/win5_cache")
    ap.add_argument("--json", dest="json_out", default="../out/win5_accounting.json")
    args = ap.parse_args()

    events = []
    for p in sorted(pathlib.Path(args.cache).glob("event_*.html")):
        e = parse_event(p)
        if e:
            events.append(e)
    print(f"parsed {len(events)} events "
          f"({events[0]['date']}..{events[-1]['date']})" if events else "no events")

    # --- チェーン計算 ---
    c_in = 0
    rows = []
    n_r80 = 0
    for e in events:
        P = e["sales"]
        payout = (e["dividend"] or 0) * (e["winners"] or 0)
        no_winner = (e["winners"] or 0) == 0
        # 年代別の名目払戻率。2014-06 の賭式別移行前は 73.8%(2011-09-04 の公式繰越
        # 852,808,741 円 = 0.738×P と円単位で一致する実測アンカー)。以後 70.0%、
        # 年末プレミアム等はチェーン整合(payout が 0.70 で説明できない)で 0.80 に切替。
        R = 0.738 if e["date"] < "20140601" else 0.70
        budget = R * P + c_in
        if e["date"] >= "20140601" and payout > budget + 1:
            R = 0.80
            budget = R * P + c_in
            n_r80 += 1
        # 繰越が発生するのは (a) 的中者ゼロ (b) 1票あたり 6 億円上限で頭打ち、の 2 つだけ。
        # 通常回の budget − payout は 100 円未満切り捨ての端数で、繰越ではなく切り捨て
        # (JRA FAQ)。当初これを繰越に入れて毎週幻の繰越を積み、外部検証が 3〜4 倍ずれた。
        if no_winner:
            c_out = budget
        elif (e["dividend"] or 0) >= 600_000_000:    # 100 円につき 6 億円の法定上限
            c_out = max(0.0, budget - payout)
        else:
            c_out = 0.0
        rate = payout / P if P else 0.0
        rows.append({"date": e["date"], "sales": P, "payout": int(payout),
                     "winners": e["winners"], "dividend": e["dividend"],
                     "carry_in": int(c_in), "carry_out": int(c_out), "R": R,
                     "rate": rate, "cin_over_p": c_in / P if P else 0.0,
                     "no_winner": no_winner})
        c_in = c_out

    # --- 外部検証(JRA 公式の大口実額) ---
    known = {"20260201": 539_905_240, "20251004": 257_063_660, "20250119": 449_023_820,
             "20200719": 464_091_040, "20190303": 464_985_570, "20181223": 599_802_210,
             "20141123": 411_631_640}
    print("\n=== 外部検証(チェーン計算の carry_out vs JRA 公式) ===")
    byd = {r["date"]: r for r in rows}
    ok = bad = 0
    for d, v in known.items():
        r = byd.get(d)
        if r is None:
            print(f"  {d}: (未取得)")
            continue
        match = abs(r["carry_out"] - v) < 1000
        ok += match
        bad += (not match)
        print(f"  {d}: chain={r['carry_out']:,} 公式={v:,} {'一致' if match else '**不一致**'}")

    profitable = [r for r in rows if r["rate"] > 1.0]
    big_cin = [r for r in rows if r["cin_over_p"] > 0.30]
    print("\n=== 判定(事前登録した規則) ===")
    print(f"  全 {len(rows)} 回 / 的中者ゼロ {sum(r['no_winner'] for r in rows)} 回 / "
          f"R=0.80 と推定 {n_r80} 回")
    print(f"  実効払戻率 > 1.0 の回: **{len(profitable)}**")
    for r in profitable[:15]:
        print(f"    {r['date']}  rate={r['rate']:.3f}  C_in/P={r['cin_over_p']:.3f}  "
              f"C_in={r['carry_in']/1e8:.2f}億  P={r['sales']/1e8:.2f}億")
    print(f"  事前識別条件 C_in/P > 0.30 の回: **{len(big_cin)}**")
    for r in big_cin[:15]:
        print(f"    {r['date']}  C_in/P={r['cin_over_p']:.3f}  rate={r['rate']:.3f}")
    # 繰越が P をどれだけ膨らませるか(事前識別の敵)
    import statistics
    base_p = statistics.median([r["sales"] for r in rows if r["carry_in"] == 0]) if rows else 0
    infl = [(r["carry_in"] / 1e8, r["sales"] / base_p) for r in rows if r["carry_in"] > 1e8]
    if infl:
        print(f"\n  繰越 1 億超の回の売上膨張(繰越ゼロ回の中央値 {base_p/1e8:.2f}億 比):")
        for c, x in sorted(infl, reverse=True)[:10]:
            print(f"    C_in={c:.2f}億 → 売上 {x:.2f}x")

    json.dump({"n_events": len(rows), "n_no_winner": sum(r["no_winner"] for r in rows),
               "external_check": {"ok": ok, "bad": bad},
               "n_rate_gt1": len(profitable), "n_cin_over_p_gt03": len(big_cin),
               "rows": rows}, open(args.json_out, "w"), indent=1)
    print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
