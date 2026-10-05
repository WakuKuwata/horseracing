/**
 * Feature 138: 注目条件(S1〜S5)の表示規則 — 純関数と凍結した表示語の唯一の置き場所。
 *
 * どの条件のチップを出すか(`chip_rule`・`chip_stage`・`chip_s2`)と、チップの条件を現在値で
 * 満たすか(`chip_now`)は API が eval の同じ判定関数で決める。**front は該当を導き直さない。**
 * front が決めるのは 2 つだけ:
 *
 * 1. 価格鮮度(4 本目の軸)— **最新の計算**(表示版 `mev-ens15-v1` の最新行)のオッズ取得時刻 ×
 *    発走時刻 × 描画時刻。pick の凍結時刻(最初の計算)では決めない(それで決めると強調はほぼ
 *    常に最弱になる)。描画時に 1 回評価し、自動では更新しない(再取得で更新される)。
 * 2. 強調レベル = 4 軸(過去検証・前向き検証・価格ずれ試験・価格鮮度)の**最小値**。チップの
 *    条件を現在値で満たさない(「判断時点のみ該当」)・現在値が無い(「現在値なし」)なら 1 に固定。
 *    手動での上げ下げはしない。
 *
 * 表示語は spec FR-006 の表の凍結値。段階は API では ASCII、日本語はこのファイルだけに置く
 * (FR-014)。10 分/60 分は**経過時間の区切りの約束**であって、その間にオッズがどれだけ動くかは
 * 測っていない — 「通常」という語は使わない(`UNMEASURED_ODDS_DRIFT` との衝突回避)。
 * 「おすすめ」「推奨」「印」もここの語に入れない(`ATTENTION_SCOPE`)。
 */
import type {
  AttentionBuyTimeSource,
  AttentionExclusionCounts,
  AttentionLevels,
  AxisLevel,
  CheckpointDecision,
  ChipNow,
  RuleId,
  RuleSummary,
  Stage,
  StageDetail,
} from "../api/types";

export type Level = AxisLevel;

// --- 回収率の計算基準ラベル(FR-008・憲法 V) ------------------------------------------------

/**
 * 注目条件のコンポーネントに出る**回収率の数値には必ずどれか 1 つ**を同じ要素内に添える
 * (`data-kind="roi"` の不変テスト)。キーは API の `valuation_basis`(`buy_time_conversion` だけは
 * front 側の名前 — 下記)。
 *
 * - `official_win_payout`: 前向き検証の段階判定の基準(集計方針 v2・139)。公式の単勝払戻
 *   (100 円あたり)で精算した値で、勝ち馬に実際に払われた額なので**「近似」を付けない**
 * - `closing_odds_approx`: 過去検証・選ばれた馬の実際の回収率・価格ずれ試験(確定オッズ × 100 円)
 * - `frozen_pick_odds`: 前向き検証の参考(v1 の精算=判断時の `odds_used` × 100 円)。v1 の判定記録も
 *   これで精算した
 * - `stored_odds_mutable`: 前向き検証の参考(現在の保存オッズ × 100 円・再取込で変わりうる)
 * - `buy_time_conversion`: 判断時点の見込み(registry の `BUY_TIME_EXPECTATION`)。過去データで、
 *   判断時のオッズで条件を満たした馬を買ったと仮定した換算値で、精算した実績ではない。API の
 *   `buy_time_expectation` は `valuation_basis` を持たない(基準は registry の出典で固定)ので、
 *   この名前は front が付ける
 *
 * 公式払戻以外は、公式の払戻そのものではないので「近似」と書く。
 */
export const ROI_BASIS_LABELS = {
  official_win_payout: "公式払戻",
  closing_odds_approx: "確定オッズ近似",
  frozen_pick_odds: "判断時オッズ・近似",
  stored_odds_mutable: "保存オッズ(参考)・近似",
  buy_time_conversion: "判断時オッズ換算・近似",
} as const;

export type RoiBasis = keyof typeof ROI_BASIS_LABELS;

/** 不変テストで「どれか 1 つを含む」を確かめるための一覧。 */
export const ROI_BASIS_LABEL_VALUES: readonly string[] = Object.values(ROI_BASIS_LABELS);

/** 画面に添える形(例: 〔確定オッズ近似〕)。 */
export function roiBasisTag(basis: RoiBasis): string {
  return `〔${ROI_BASIS_LABELS[basis]}〕`;
}

// --- 価格鮮度(描画時に計算) -----------------------------------------------------------------

/** レベル 3 の上限(取得から 10 分以内・境界を含む)。 */
export const FRESHNESS_LEVEL3_MS = 10 * 60 * 1000;
/** レベル 2 の上限(取得から 60 分以内・境界を含む)。 */
export const FRESHNESS_LEVEL2_MS = 60 * 60 * 1000;

export const FRESHNESS_LABELS = {
  within10m: "10 分以内",
  within60m: "60 分以内",
  over60m: "60 分超",
  afterPost: "発走後",
  postUnknown: "発走時刻不明",
  noCurrent: "最新の計算なし",
} as const;

export type FreshnessReason = keyof typeof FRESHNESS_LABELS;

export type Freshness = {
  level: Level;
  reason: FreshnessReason;
  label: (typeof FRESHNESS_LABELS)[FreshnessReason];
};

function makeFreshness(level: Level, reason: FreshnessReason): Freshness {
  return { level, reason, label: FRESHNESS_LABELS[reason] };
}

function parseTime(value: string | null | undefined): number | null {
  if (!value) return null;
  const t = new Date(value).getTime();
  return Number.isNaN(t) ? null : t;
}

/**
 * 価格鮮度(spec「強調レベルの決定規則」の 4 本目の軸)。
 *
 * - 3「10 分以内」: 最新の計算に使ったオッズの取得から 10 分以内(ちょうど 10 分を含む)かつ発走前
 * - 2「60 分以内」: 60 分以内(ちょうど 60 分を含む)かつ発走前
 * - 1: 「60 分超」/「発走後」(結果あり、または描画時刻 ≥ 発走時刻)/「発走時刻不明」/
 *   「最新の計算なし」(現在値の取得時刻が無い=未計算・再計算待ち・オッズ欠落)
 *
 * レベル 1 の理由が重なるときの語の優先順は 発走後 → 最新の計算なし → 発走時刻不明 → 60 分超
 * (終わったレースでは鮮度そのものが意味を失うので「発走後」を先に言う。レベルはどれでも 1)。
 * 取得時刻が描画時刻より後(時計のずれ)は経過 0 として扱う。
 *
 * @param currentOddsObservedAt 現在値(表示版の最新行)の `odds_observed_at`。pick の値を渡さない。
 * @param now 描画時刻。呼び出し側が 1 回だけ取る(自動更新しない)。
 */
export function freshnessLevel(
  currentOddsObservedAt: string | null | undefined,
  postTime: string | null | undefined,
  hasResults: boolean | null | undefined,
  now: Date | number,
): Freshness {
  const nowMs = typeof now === "number" ? now : now.getTime();
  const postMs = parseTime(postTime);
  if (hasResults === true || (postMs !== null && nowMs >= postMs)) {
    return makeFreshness(1, "afterPost");
  }
  const observedMs = parseTime(currentOddsObservedAt);
  if (observedMs === null) return makeFreshness(1, "noCurrent");
  if (postMs === null) return makeFreshness(1, "postUnknown");
  const age = Math.max(0, nowMs - observedMs);
  if (age <= FRESHNESS_LEVEL3_MS) return makeFreshness(3, "within10m");
  if (age <= FRESHNESS_LEVEL2_MS) return makeFreshness(2, "within60m");
  return makeFreshness(1, "over60m");
}

// --- 強調レベル ----------------------------------------------------------------------------------

/** 前向き検証が閉じた段階(不通過・判定保留)。チップは消さず主ラベルを段階名にし、強調は 1 固定。 */
export function isClosedStage(stage: Stage): boolean {
  return stage === "failed" || stage === "undecided";
}

/**
 * 強調レベル = 4 軸(過去検証・前向き検証・価格ずれ試験・価格鮮度)の最小値(SC-003)。
 *
 * 1 に固定する場合: チップの条件を現在値で満たさない(`no_longer`=「判断時点のみ該当」)・
 * 現在値が無い(`unknown`=「現在値なし」)・`chip_now` が無い・軸レベルが無い・チップの段階が
 * 不通過/判定保留(API の前向きの軸も 1 を返すが、ここでも重ねて固定する)。
 *
 * レベル 3 は「出走表の白枠・太い左帯・塗りつぶしチップ」の発火条件。凍結値では価格ずれ試験の
 * 軸が全条件 2 以下なので、現状は到達しない(規則は残す)。
 */
export function emphasisLevel(
  levels: AttentionLevels | null | undefined,
  freshness: Level,
  chipNow: ChipNow | null | undefined,
  chipStage?: StageDetail | null,
): Level {
  if (!levels) return 1;
  if (chipNow !== "matches") return 1;
  if (chipStage && isClosedStage(chipStage.stage)) return 1;
  return Math.min(levels.backtest, levels.prospective, levels.price_noise, freshness) as Level;
}

// --- 表示語表(FR-006・FR-014) ----------------------------------------------------------------

/**
 * 前向き検証の段階(ASCII)→ 日本語。
 *
 * researching=研究中 / observing=観察中(判定記録が無いままチェックポイントに達していれば
 * 「観察中・判定待ち」)/ passed=「{checkpoint} 点通過」/ failed=「{checkpoint} 点不通過」/
 * undecided=判定保留。「判定待ち」は観察中の補足表示でレベルを変えない。
 */
export function stageLabel(detail: StageDetail): string {
  switch (detail.stage) {
    case "researching":
      return "研究中";
    case "observing":
      return detail.checkpoint_pending ? "観察中・判定待ち" : "観察中";
    case "passed":
      return detail.checkpoint == null ? "通過" : `${detail.checkpoint} 点通過`;
    case "failed":
      return detail.checkpoint == null ? "不通過" : `${detail.checkpoint} 点不通過`;
    case "undecided":
      return "判定保留";
  }
}

/** チップの主ラベル(例: 「注目条件 S1・研究中」「注目条件 S4・300 点不通過」)。
 *  aria-label にも同じ文字列を使う。閾値の数値は入れない(期待値シグナルとして読まれる)。 */
export function chipLabel(ruleId: RuleId, detail: StageDetail): string {
  return `注目条件 ${ruleId}・${stageLabel(detail)}`;
}

/** 過去検証の軸の表示語(基準と同じ内容の語・CS-02)。 */
export const BACKTEST_LEVEL_LABELS: Record<Level, string> = {
  3: "通算の区間下限 100% 超・確認窓 110% 以上",
  2: "通算・確認窓とも 100% 超",
  1: "通算か確認窓が 100% 以下",
};

export function backtestLevelLabel(level: Level): string {
  return BACKTEST_LEVEL_LABELS[level];
}

/** 「検証の進み具合」のテキストラベル。●表現はチップに置かず、展開パネルと一覧にだけ置く。 */
export const PROGRESS_LABEL = "検証の進み具合";

const PROGRESS_DOTS: Record<Level, string> = {
  1: "●○○",
  2: "●●○",
  3: "●●●",
};

/** 強調レベル → ●○○ / ●●○ / ●●●(必ず `PROGRESS_LABEL` と並べて出す)。 */
export function progressDots(level: Level): string {
  return PROGRESS_DOTS[level];
}

/** ●○○ の読み上げ用テキスト(支援技術向け・●は「黒丸 白丸…」と読まれて段階が伝わらない)。
 *  画面の ● は aria-hidden にし、この語を視覚的に隠して並べる。 */
export function progressScaleText(level: Level): string {
  return `3 段階中 ${level}`;
}

/** チェックポイント判定の表示語(判定記録の `decision`・ASCII → 日本語)。 */
export const DECISION_LABELS: Record<CheckpointDecision["decision"], string> = {
  passed: "通過",
  failed: "不通過",
  continue: "継続",
  undecided: "判定保留",
};

/**
 * 前向き検証の集計外の件数(排他的な 11 分類)の表示語。並びは eval `classify_pick` の優先順
 * (`EXCLUSION_ORDER`)。139 で `pending_result` の直後に 2 分類を足した:
 *
 * - `payout_race_missing`「公式払戻なし」: 結果はあるが、そのレースの公式単勝払戻がまだ 1 件も無い
 *   (レース単位で集計外 — 勝ち馬だけ除くと回収率が下に偏る)
 * - `payout_inconsistent`「払戻の不整合」: 勝ち馬なのに払戻が無い/勝ち馬でない馬に払戻がある
 *   (データの不整合。待っても解消しないので件数を別に目立たせる)
 */
export const EXCLUSION_LABELS: Record<keyof AttentionExclusionCounts, string> = {
  voided_scratched: "取消 void",
  before_start: "集計開始前",
  post_time_unknown: "発走時刻不明",
  computed_after_post: "発走後の計算",
  result_known_at_compute: "計算時に結果確定済み",
  observed_after_post: "発走後のオッズ",
  pending_result: "結果未確定",
  payout_race_missing: "公式払戻なし",
  payout_inconsistent: "払戻の不整合",
  unsettled_horse: "自馬の結果なし",
  dead_heat: "同着",
};

/** `EXCLUSION_LABELS` の表示順(`classify_pick` の優先順)。 */
export const EXCLUSION_ORDER = Object.keys(EXCLUSION_LABELS) as (keyof AttentionExclusionCounts)[];

// --- 前向き検証の精算基準(集計方針 v2・139) -------------------------------------------------------

/**
 * 前向き検証の回収率の見出し。段階の判定は公式の単勝払戻で精算した値(`official`)で行い、同じ
 * 集計対象を判断時オッズで精算した値(v1 の精算)と保存オッズでの値は参考として並べる(139 D3・D12)。
 */
export const PROSPECTIVE_BASIS_LABELS = {
  official: "回収率(段階判定の基準)",
  frozen: "参考(v1・判断時オッズ)",
  stored: "参考(保存オッズ)",
} as const;

/** 判定記録の回収率の基準(記録の精算 `valuation_basis`)。未知・欠落は null — 数値を出さない
 *  (基準のわからない回収率に別の基準のラベルを付けない)。 */
export function decisionRoiBasis(
  valuationBasis: CheckpointDecision["valuation_basis"] | undefined,
): RoiBasis | null {
  if (valuationBasis === "official_win_payout" || valuationBasis === "frozen_pick_odds") {
    return valuationBasis;
  }
  return null;
}

// --- 判断時点の見込み(過去データからの換算・139 D13) ----------------------------------------------

/** 一覧と展開パネルの見出し(spec FR-007・FR-008)。 */
export const BUY_TIME_LABEL = "判断時点の見込み(過去データからの換算)";
/** 数値の前に置く説明(D13: 「見込み」だけだと保証や購入の勧めに読まれるので、何の値かを言う)。 */
export const BUY_TIME_LEAD =
  "過去データで、判断時のオッズで条件を満たした馬を買ったと仮定した換算回収率";
/** 数値の後に置く但し書き。`ATTENTION_SCOPE` が禁じる語(推奨・買い目など)を使わない。 */
export const BUY_TIME_CAVEAT = "参考値・購入を勧めるものではありません";
/**
 * API の `buy_time_expectation` が null のとき(registry の値が独立検証を通る前・139 D6)に
 * 数値の代わりに出す文。検証前の換算値は画面に出さない(0 や「—」の数値でも埋めない)。
 */
export const BUY_TIME_PENDING =
  "独立検証を通った値だけを表示します(現在は検証待ちのため、換算値は表示していません)";

/**
 * 換算値の幅(registry の `range_low`〜`range_high`・二つの推定量を 5% 単位に丸めた範囲)→
 * 「約 85〜90%」、両端が同じなら「約 85%」。整数の % で出す(小数を付けると 3 桁の確定値に読まれる・
 * 独立検証 r02_verification.md)。
 */
export function formatBuyTimeRange(low: number, high: number): string {
  const lo = Math.round(low * 100);
  const hi = Math.round(high * 100);
  return lo === hi ? `約 ${lo}%` : `約 ${lo}〜${hi}%`;
}

/** 換算値の区間(二つの推定量の 95% 区間を外側に丸めた包絡)→ 「(区間 73〜109%)」。 */
export function formatBuyTimeInterval(low: number, high: number): string {
  return `(区間 ${Math.round(low * 100)}〜${Math.round(high * 100)}%)`;
}

/** 区間が 100% を含むとき(S1)に、換算値の後に添える文。 */
export const BUY_TIME_INTERVAL_INCLUDES_100 = "100% を下回る見込みですが、区間は 100% を含みます。";
/** 区間の上限も 100% 未満のとき(S3〜S5)に、換算値の後に添える文。 */
export const BUY_TIME_INTERVAL_BELOW_100 = "100% を下回る推定です(区間の上限も 100% 未満)。";

/** API の `interval_includes_100` → 添える文。値が無い(`included_in` の条件)なら null。 */
export function buyTimeIntervalSentence(includes100: boolean | null | undefined): string | null {
  if (includes100 === true) return BUY_TIME_INTERVAL_INCLUDES_100;
  if (includes100 === false) return BUY_TIME_INTERVAL_BELOW_100;
  return null;
}

/**
 * 単独の区間が無効な条件(S2: 分母になる確定時の選定が 2 頭しかない)は自分の値を出さず、
 * 馬がすべて含まれる条件(`included_in`・S2 ⊂ S1)を示す。数値・% は出さない。
 */
export function buyTimeIncludedInText(includedIn: RuleId): string {
  return (
    `単独の値は出しません(この条件の馬はすべて ${includedIn} に含まれます。` +
    `${includedIn} の見込みを参照してください)`
  );
}

/**
 * 換算値が何の値かの説明(一覧の行の補足に 1 回だけ置く)。期間・開催日数は出典の転記。
 * 「2026-08-02..2026-10-04」のように 1 年に収まる期間は「2026 年」と書く。時間帯別の値は出さない
 * (事前登録した対比が null だった・r02_verification.md)。
 */
export function buyTimeFramingText(source: AttentionBuyTimeSource): string {
  const [from, to] = source.period.split("..");
  const sameYear = from !== undefined && to !== undefined && from.slice(0, 4) === to.slice(0, 4);
  const when = sameYear ? `${from.slice(0, 4)} 年の` : `${source.period.replace("..", "〜")} の`;
  return (
    `この見込みは、締切オッズでの過去成績を ${when} ${source.race_days.toLocaleString("ja-JP")} ` +
    "開催日の発走前オッズで換算した値で、実績ではありません。"
  );
}

/**
 * 出典 → 「算出: 2026-08-02〜2026-10-04・564 組・444 レース・17 開催日・算出日 2026-10-04・
 * 版 buy-time-v2・独立検証済み」。値は registry の転記で、front は書式だけを決める。`version` は
 * 数値の組の名前(値が変われば版が上がる・139 D13)。`status` が `verified` で始まれば
 * 「独立検証済み」、`provisional` で始まれば「暫定値(独立検証前)」、それ以外は状態の文字列を
 * そのまま添える(知らない状態を黙って消さない)。
 */
export function buyTimeSourceText(source: AttentionBuyTimeSource): string {
  const period = source.period.replace("..", "〜");
  const status = source.status.startsWith("verified")
    ? "独立検証済み"
    : source.status.startsWith("provisional")
      ? "暫定値(独立検証前)"
      : `状態 ${source.status}`;
  const n = (v: number) => v.toLocaleString("ja-JP");
  return (
    `算出: ${period}・${n(source.pairs)} 組・${n(source.races)} レース・` +
    `${n(source.race_days)} 開催日・算出日 ${source.computed_on}・版 ${source.version}・${status}`
  );
}

// --- レジストリの属性(探索後固定・対照) ---------------------------------------------------------

/**
 * どの条件が「探索後固定」(`posthoc`)・「対照」(`control`)かは eval レジストリの属性で、
 * `GET /attention-rules` の `RuleSummary.posthoc` / `control` が運ぶ。front は条件 id を
 * 書き写さず、この応答から引く(手書きの定数はレジストリと黙ってずれる)。
 */
export type RuleFlags = {
  posthoc: ReadonlySet<RuleId>;
  control: ReadonlySet<RuleId>;
};

/** 一覧の応答 → 属性の集合。一覧が未取得(読み込み中・取得失敗)なら null(属性は「不明」)。 */
export function ruleFlags(rules: readonly RuleSummary[] | null | undefined): RuleFlags | null {
  if (!rules) return null;
  return {
    posthoc: new Set(rules.filter((r) => r.posthoc).map((r) => r.id)),
    control: new Set(rules.filter((r) => r.control).map((r) => r.id)),
  };
}

/** 条件 id の表示(対照なら「対照 S5」)。`control` が不明(null)なら id だけ。 */
export function ruleIdText(id: RuleId, control: ReadonlySet<RuleId> | null | undefined): string {
  return control?.has(id) ? `対照 ${id}` : id;
}

export const CHIP_NOW_LABELS = {
  no_longer: "判断時点のみ該当",
  unknown: "現在値なし",
} as const;

/** チップに添える語。現在値でも条件を満たす(`matches`)・チップが無い(null)なら何も添えない。 */
export function chipNowLabel(chipNow: ChipNow | null | undefined): string | null {
  if (chipNow === "no_longer" || chipNow === "unknown") return CHIP_NOW_LABELS[chipNow];
  return null;
}
