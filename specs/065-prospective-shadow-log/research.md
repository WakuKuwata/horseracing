# Phase 0 Research: prospective shadow-betting log

Decision / Rationale / Alternatives 形式。codex 反映は §R8。

## R1. prospective flag を独立引数にする

**Decision**: `generate_recommendations(..., prospective: bool = False, odds_asof=None)` の**独立引数**。既定 False = 現行バイト同等。

**Rationale**: マーカーは「この推奨は発走前・凍結オッズで前向きに出した」という生成コンテキストの記録で、選定/確率とは直交。独立引数なら backfill(默 False)と prospective(True)を1経路で分岐でき、off で logic_version バイト同等(064 の win_odds_cap と同型)。

**Alternatives**: 別関数に複製(却下: 重複)、環境変数(却下: 監査不能)。

## R2. logic_version マーカー文法と off バイト同等

**Decision**: `prospective=True` のときだけ `default_logic_version` に `;prospective=1;odds_asof=<iso>` を追記。False では一切追記しない → 既存 lv とバイト同等。回帰テスト `test_prospective_off_is_byte_identical`。

**Rationale**: 064 の `;oddscap=` 条件付き追記の前例。marker off で現行永続推奨・serving を壊さない。`prospective=1` は特異トークンで backfill と誤マッチしない(backfill は付与しない)。

## R3. odds_asof に何を入れるか(=「約定可能だった時点」)【§R8 で是正済み】

**Decision**: odds_asof = **prospective 収集フローで発走前オッズを fresh 取得した scrape/capture 時刻**。凍結オッズ値そのものは既存 `market_odds_used`(recommend 時の odds)。決定時刻は `Recommendation.computed_at` を併記。

**Rationale(codex 是正)**: 当初案の `_odds_as_of`=`max(RaceHorse.updated_at)` は**却下** — updated_at は trigger 由来の汎用行鮮度で、entry/scratch/脚質更新でも動き、オッズ観測時刻を表さない(気配値時刻を過大表示)。「オッズをいつ観測したか」は取得イベントの時刻でしか正直に表せない。`race_horses.odds` が後で closing に上書きされても、odds_asof(capture 時刻)と market_odds_used は生成時に凍結済みで不変。

**Alternatives**: `max(RaceHorse.updated_at)`(**却下**: 汎用行鮮度・オッズ観測時刻でない=codex)、`Recommendation.computed_at`(補助として併記=決定時刻であり気配値鮮度ではない)。

## R4. 「発走前=result-pending 時に生成」の機械保証

**Decision**: prospective 収集経路は生成前に `guards.is_result_pending(session, race_id)`(race_results 0 行、019)を **必要条件**の fail-closed ガードにする。**ただしこれは発走前の十分条件ではない**(§R8 で是正): 「結果行の不在」は「レース後・結果未 ingest」を含みうるため、**capture 規律**(同一フローで fresh scrape + capture 時刻記録 + post_time 前要求・未知は弱保証ラベル + scrape 直後の再確認)を併せて満たして初めて「発走前」を担保する。marker は「生成時に結果が無く、かつ capture 時刻 <asof> のオッズで出した」を意味する(完全な発走前保証ではないと正直に開示)。

**Rationale**: wall-clock でなく「結果行の不在」で判定するのは 019 の設計だが、それ単独では closing-oracle の裏口を塞げない(§R8-1)。生成後に結果が入っても marker は残る(正しい)。精算は既存 `win_realized` が**凍結 market_odds_used**で回収=後から入った結果で凍結オッズは変わらない(closing-oracle 排除)。leak-guard: 「結果を入れても marker/凍結オッズ/評価が不変」テスト。

## R5. read-time 集計の非混同担保(SC-002)

**Decision**: `api/backtest.py::shadow_log_summary` は recommendations のうち **bet_type=win かつ logic_version に `prospective=1` を含む かつ settled** のみを対象に、既存 `win_realized`(凍結 market_odds_used)で集計。backfill(marker 無し)・未確定(settled=False)・exotic・疑似は構造的に除外。

**Rationale**: フィルタが3条件 AND=backfill/未確定/exotic を1件も混ぜない(SC-002)。049 win_realized をそのまま使い betting 非 import 境界維持。テスト: backfill 混在・未確定混在・exotic 混在で prospective のみ集計。

## R6. closing-oracle 排除の証明

**Decision**: prospective 推奨の評価は生成時に凍結した `market_odds_used` のみを使い、現在の `race_horses.odds`(closing)を一切読まない。SC-001 テスト: 記録後にそのレースの race_horses.odds を closing 値へ更新 → shadow_log_summary の realized がバイト不変。

**Rationale**: これが計器の存在意義。049 win_realized は既に market_odds_used で評価する(現在オッズを読まない)ので、性質を継承するだけ。

## R7. 冪等キー(policy-aware + prospective)

**Decision**: prospective win 群の冪等キーは (run, bet_type=win, **prospective marker**)。064 の `_has_win_group`(logic_version の oddscap で識別)を prospective marker も見るよう拡張。同一 prospective policy 再実行は skip、backfill 群とは別群。

**Rationale**: 064 の group 細分化前例。二重記録防止(SC-004)。backfill と prospective が同一 run に併存しても別群。

## R8. codex second opinion(plan 段並走・反映済み)

総評: 中核(WIN 行は既に market_odds_used 凍結・win_realized は凍結オッズで評価)は妥当。ただし**現状のまま実装するな** — 3つの重大な穴を指摘。全て採用。

**採用(重大・計器の信頼性に直結)**:
1. **【最大の自己欺瞞】result-pending だけでは「発走前」を保証しない**(R4 を是正)。`result-pending`=race_results 0 行は「未走」でなく「結果未 ingest」を含む。かつ `scrape.update_odds` は result-pending レースのオッズを上書きするため、**レース後・結果 ingest 前に scrape すると closing オッズを "prospective" として凍結しうる**=排除したはずの closing-oracle が裏口から復活。→ 対策: prospective 収集は**同一フローで発走前オッズを fresh scrape し、その捕捉時刻を記録**、可能なら **post_time 前**を強制。post_time が null のレースは「発走前保証が弱い」と**明示ラベル**し混ぜない。marker の意味を「生成時に結果が無く、かつ捕捉時刻 <asof> のオッズで出した」に厳密化(=完全な発走前保証ではないと正直に開示)。
2. **odds_asof に RaceHorse.updated_at(max)は誤り**(R3 を是正)。updated_at は trigger 由来の汎用行鮮度で、entry/scratch/脚質更新でも動く=オッズ観測時刻でない。→ **odds_asof = 収集フローの scrape/capture 時刻**。決定時刻は `Recommendation.computed_at` を併記。odds_asof を「約定可能な気配値時刻」と偽って提示しない。
3. **冪等が run-scoped で壊れている**(R7 を是正)。既存 `_has_win_group` は prediction_run 単位だが、live `run_serving` は append-only で再収集ごとに**新しい run** を作る→run 跨ぎで prospective 行が重複。→ 冪等キーを **(race, model, policy=prospective marker) で run 跨ぎ**に。advisory-lock で check-then-insert 競合を防ぐ。

**採用(設計是正)**:
4. **WIN 生成経路を pin**: live_serve は `generate_kelly_recommendations`(exotic)。prospective 収集は明示的に **WIN 行**(`generate_recommendations`)を生成する経路にする。
5. **skip-rate は recommendations だけからは算出不能**(FR-004 是正)。買い目ゼロのレースは行が残らない→見送り分母が作れない。attempt log 永続化は schema 変更で不可。→ **shadow-log から skip-rate を落とす**(算出できない指標を偽装しない)。的中率/回収率/確定数/未確定数/void 数に限定。
6. **集計は recommendations を run 跨ぎで直接クエリ**(active-run scoped の表示クエリを使わない)。**favorite_realized や race_horses.odds の現在値読みを ROI に一切入れない**(favorite_realized は現在オッズを読む=禁止)。
7. **marker は厳密トークン解析**(`;prospective=1;odds_asof=` or `;` split、loose contains 禁止)。**custom logic_version でも marker が消えないよう、custom/default 解決後に付与**(または prospective 時は custom logic を拒否)。ROI 分母は `win_realized.hit is not None`(void は別計上)。

**保留/確認事項**: serving/pipeline.py が market-offset モデル(win オッズ読み=lgbm-060-mkt)を active にしていないか要確認(p≠q 前提と衝突しないか)。shadow log 自体は active モデルの p を使うだけだが、active が市場読みモデルなら計器の解釈が変わる。

**残リスク(実装しても残る)**: ①post_time null レースでは発走前保証が弱い ②operator が締切直前 or 結果 ingest 前に回すと late-market/closing 的になる=**運用規律に依存**(one-shot per race/policy を強制) ③string marker は unindexed で少量前提。

**結論**: これは「表示ラベル」でなく「捕捉時刻規律 + 発走前保証の明示的弱さ + run 跨ぎ冪等」を伴う計器。codex 指摘で spec に**発走前タイミングの正直な限界**と**capture 規律**を追加、FR-004 から skip-rate を除去、契約を run 跨ぎクエリ・厳密 marker・現在オッズ非参照に是正する。
