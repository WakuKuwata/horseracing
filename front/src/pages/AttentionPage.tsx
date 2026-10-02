import { AttentionNote } from "../components/AttentionNote";
import { AttentionRulesPanel } from "../components/AttentionRulesPanel";

/**
 * Feature 138 (T036): 専用ページ `/attention` — 注目条件 S1〜S5 の定義・凍結した過去検証・
 * 価格ずれ試験・前向き検証の現況を、常設注記と一緒に開いた状態で出す。並びは順位の固定順だけ。
 */
export function AttentionPage() {
  return (
    <section data-testid="attention-page">
      <h2>注目条件</h2>
      <AttentionNote />
      <AttentionRulesPanel defaultOpen />
    </section>
  );
}
