import { usePauseState } from "../api/opsStatus";

export const PAUSE_SHORT = "データ取得を停止中のため受け付けていません";
export const PAUSE_BANNER =
  "データ取得停止中: 更新・予測・買い目生成・購入記録の受付を止めています(設定 collection_paused)。表示は保存済みデータのままです。";

/**
 * B5: one line next to a disabled write button. Neutral tone (muted, no alert role): the pause is
 * an operating state the operator chose, not a failure of the page.
 */
export function OpsPauseNotice({ className = "refresh__status" }: { className?: string }) {
  const { paused } = usePauseState();
  if (!paused) return null;
  return (
    <span className={`${className} ${className}--muted`} role="status" data-testid="ops-paused-notice">
      {PAUSE_SHORT}
    </span>
  );
}

/** B5: header banner shown only while ops reports the pause (so the state is visible once, early). */
export function OpsPauseBanner() {
  const { paused } = usePauseState();
  if (!paused) return null;
  return (
    <p className="ops-pause-banner" role="status" data-testid="ops-pause-banner">
      {PAUSE_BANNER}
    </p>
  );
}
