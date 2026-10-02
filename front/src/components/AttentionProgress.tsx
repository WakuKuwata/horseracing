import { type Level, PROGRESS_LABEL, progressDots, progressScaleText } from "../lib/attention";

/**
 * Feature 138: 「検証の進み具合 ●○○」(展開パネルと日付の一覧だけに置く・チップには置かない)。
 *
 * ● は支援技術では「黒丸 白丸 白丸」と読まれ段階が伝わらないので、● を `aria-hidden` にし、
 * 「(3 段階中 1)」を視覚的に隠して並べる。画面上の表示(ラベル + ●○○)は変えない。
 */
export function AttentionProgress({ level, testId }: { level: Level; testId: string }) {
  return (
    <span className="attn-level" data-testid={testId}>
      {PROGRESS_LABEL}
      <span className="attn-level__dots" aria-hidden="true">
        {progressDots(level)}
      </span>
      <span className="visually-hidden">({progressScaleText(level)})</span>
    </span>
  );
}
