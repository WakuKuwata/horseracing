import type { ReactNode } from "react";

import { roiBasisTag, type RoiBasis } from "../lib/attention";
import { formatPct, PLACEHOLDER } from "../lib/format";

/**
 * Feature 138: 注目条件のコンポーネントが回収率の数値を出す**唯一の経路**(FR-008・憲法 V)。
 *
 * どの基準で精算・換算した数値かを**同じ要素の中**に必ず添える(`ROI_BASIS_LABELS` の 5 つの
 * どれか)。公式の単勝払戻で精算した数値(集計方針 v2 の段階判定の基準・139)は近似ではなく
 * 〔公式払戻〕と添え、それ以外の 4 つ(確定オッズ・判断時オッズ・保存オッズ・判断時オッズ換算)は
 * 公式の払戻そのものではないので「近似」と書く。`data-kind="roi"` は不変テストの目印で、範囲内の
 * このノードはすべて計算基準のラベルを含む。数値(区間を含む)は `children` に入れる。
 */
export function RoiValue({ basis, children }: { basis: RoiBasis; children: ReactNode }) {
  return (
    <span className="attn-roi" data-kind="roi" data-roi-basis={basis}>
      {children}
      <span className="attn-roi-basis">{roiBasisTag(basis)}</span>
    </span>
  );
}

/** 回収率(比: 1.210735)→ 「121.1%」。欠落は「—」(0 で埋めない)。 */
export function formatRoi(ratio: number | null | undefined): string {
  return formatPct(ratio, 1);
}

/** 区間(比の組)→ 「区間 105.3〜137.1%」。区間が無い(開催日が 1 日しかない等)なら null。 */
export function formatCi(ci: readonly [number, number] | null | undefined): string | null {
  if (!ci) return null;
  const [lo, hi] = ci;
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
  return `区間 ${(lo * 100).toFixed(1)}〜${(hi * 100).toFixed(1)}%`;
}

/** 片側 p 値 → 「p=0.0006」「p=0.11」(有効数字 2 桁)。 */
export function formatP(p: number | null | undefined): string {
  if (p === null || p === undefined || !Number.isFinite(p)) return `p=${PLACEHOLDER}`;
  return `p=${Number(p.toPrecision(2))}`;
}

/** 件数 → 「5,235」。 */
export function formatCount(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return PLACEHOLDER;
  return n.toLocaleString("ja-JP");
}

/** 経過時間 → 「42 分」「3 時間 10 分」「2 時間」(分未満は切り捨て)。 */
export function formatDuration(ms: number): string {
  const totalMin = Math.max(0, Math.floor(ms / 60000));
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h === 0) return `${m} 分`;
  return m === 0 ? `${h} 時間` : `${h} 時間 ${m} 分`;
}
