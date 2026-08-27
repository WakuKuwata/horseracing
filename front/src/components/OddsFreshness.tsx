const HOUR_MS = 60 * 60 * 1000;

type OddsFreshnessProps = {
  oddsAsOf: string | null | undefined;
  postTime: string | null | undefined;
  hasResults: boolean | null | undefined;
};

function formatOddsDateTime(value: string | null | undefined): string | null {
  if (!value) return null;

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;

  // 既存の formatDateTime は監査用の UTC 表示なので、人が読む正本はレース時刻と同じ JST に揃える。
  return new Intl.DateTimeFormat("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Tokyo",
  }).format(date);
}

function hoursBeforePost(
  oddsAsOf: string | null | undefined,
  postTime: string | null | undefined,
): number | null {
  if (!oddsAsOf || !postTime) return null;

  const oddsTime = new Date(oddsAsOf).getTime();
  const raceTime = new Date(postTime).getTime();
  if (Number.isNaN(oddsTime) || Number.isNaN(raceTime) || oddsTime > raceTime) return null;

  // 未測定の鮮度閾値や秒単位の精度を示さず、「約 N 時間」に必要な粒度だけへ丸める。
  return Math.round((raceTime - oddsTime) / HOUR_MS);
}

/**
 * 別クエリ由来の時刻と結果状態を受け取り、オッズを読む前提をレース単位で常時開示する。
 * 派生指標との依存を同じ注記に置き、時刻だけが tooltip に隠れて判断から抜けることを防ぐ。
 */
export function OddsFreshness({
  oddsAsOf,
  postTime,
  hasResults,
}: OddsFreshnessProps) {
  const formattedOddsAsOf = formatOddsDateTime(oddsAsOf);
  const relativeHours = hoursBeforePost(oddsAsOf, postTime);

  return (
    <p className="table-hint" data-testid="odds-freshness">
      {formattedOddsAsOf ? `${formattedOddsAsOf} 時点のオッズ` : "取得時点: 不明"}
      {relativeHours !== null && ` (発走約 ${relativeHours} 時間前)`}
      {/* オッズは単一の最新値で上書きされる(憲法 V)ので、確定済みレースでは取得時刻が
          発走より後になる = これは最終オッズである。黙って相対時刻を消すと、なぜ出ないのかが
          分からないままになる。 */}
      {relativeHours === null && postTime && oddsAsOf && (
        <span data-testid="odds-after-post"> (発走後に取得された最終オッズです)</span>
      )}
      {!postTime && (
        <>
          <br />
          発走時刻未登録のため残り時間は表示できません
        </>
      )}
      {hasResults !== true && (
        <>
          <br />
          最終オッズではなく、発走までに動く可能性があります
        </>
      )}
      <br />
      {/* 取得時刻が無い(オッズ自体が無い)なら、依存の説明は出さない — 存在しない時刻に
          依存するものは無く、出すと「市場評価がある」かのように読める。 */}
      {oddsAsOf && (
        <span data-testid="odds-dependency">
          市場評価はこの時点のオッズから計算しています。
          買い目の EV・疑似 ROI は<strong>買い目を作った時点で凍結したオッズ</strong>で
          計算されており、この時刻とは限りません。
        </span>
      )}
    </p>
  );
}
