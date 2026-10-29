# 175 research — 設計判断 D1〜D13

2026-10-29。codex unavailable(400・141 以降継続・CLAUDE.md の規約により再試行せず復旧は別タスク)。各決定に「代替案と捨てた理由」を書き、D13 にセルフレビュー checklist を置く。

## D1 配置: eval 純関数+live CLI

- **決定**: 採点の純関数は `eval/decision_quote_scoring.py`、DB ロード・候補の再現・JSON 出力は `live/near_post_score.py`+`live near-post-score`。
- **根拠**: eval は db 以外に依存せず predictor-agnostic(068/100 の規律)。後段の読み取り API(api は eval を import 済み・B8 と同型)が同じ関数を呼べる。候補の再現は training artifact と `near_post.py` の部品(`build_day_features`/`rows_with_quote`/`ProductionPredictor`)が要り、live が唯一その両方を持つ。
- **代替**: (a) training に CLI → training は decision_quotes の読み方を持たず、近い部品は live にある。(b) api に read-time 集計だけ先に作る → 候補の再現(training artifact)が api に置けない(api は training 非依存)。純関数を eval に置けば後で api から呼べるので、順序は「CLI → API」で損がない。

## D2 入力の正規化(QuoteRecord)

- **決定**: live が 1 レース 1 件の plain dataclass `QuoteRecord` を組み立てる(保存行の列+現在の started 集合 {horse_id: 馬番}+結果の有無と勝馬 horse_id+公式払戻 {馬番: 円}+確定単勝 {馬番: odds}(来歴条件を満たす時のみ)+track_type)。eval は dict/dataclass しか読まない。
- **根拠**: B8 の `ProspectiveRace` と同じ型(router helper `races_from_queries` が ORM 行から組み立てる)。テストは合成 fixture で DB なしに回る。

## D3 分類の順序と B8 との関係

- **決定**: spec FR-002 の 10 段を eval に実装。B8 の `classify_race` とは入力型が違う(StoredMarketEv 行 vs decision_quotes 行)ので共有せず、**api のテスト**で「同じ合成レースを両方に食わせて、共有する段(jump_race/field_mismatch/invalid_values/pending_result/payout_race_missing/payout_inconsistent)の判定が一致する」parity テストを置く。
- **根拠**: eval は api を import できない。述語を eval に移して B8 を書き換えると B8 の挙動(front・OpenAPI は不変でも)に触れるので、本 spec では B8 を変えない。契約の二重実装は「同一 fixture の parity テスト」で固定する(049/106 と同じ扱い)。
- **代替**: 共通述語モジュールを eval に新設し B8 をそれに寄せる → B8 の変更が本 spec のスコープ外に波及。後段の API spec で統合するほうが筋。

## D4 精算と同期 bootstrap の差

- **決定**: `race_block_ratio_bootstrap_ci_v1(num=[inc, cand], den=[inc, cand], day_keys)` を **1 回**呼ぶ。`replicates` は (2, b) で同じ block draw を共有するので、差 `d_b = rep[1,b] − rep[0,b]` の percentile 2.5/97.5 が差の区間。どちらかが NaN(分母 0)の replicate は除外し件数を出す。点推定の差は `point[1] − point[0]`。
- **根拠**: 実装の docstring「Every row shares the same block draws (synchronised resampling)」。別々に呼んで区間を引き算すると相関を捨てて区間が広がる(=`keep` に偏る)か、別 seed なら再現性を失う。
- **day_keys**: 対(現行・候補とも適格)のレースがある開催日だけ(block universe=「規則の対がある開催日」)。現行単独の列集計は B8 と同じく「その列の目印がある開催日」。両方とも出力に `block_universe` を書く。
- **一側 p**: 現行単独は B8 と同じ `centered_one_sided_p_from_replicates`。差については p を出さない(規則は下限 > 0 だけを見る)。

## D5 三値と「候補なし」

- **決定**: `display_update_rule.decide` は `n_points` と `diff_ci_low` だけを取る。候補なしの実行は `verdict_overall: "no_candidate"` を CLI 層で返し、`decide` を呼ばない(呼ぶと 300 以上で `diff_ci_low` 必須の ValueError)。候補ありは `n_points` = **候補の目印本数**(登録日以降・再現できた対のレースのみ)で `decide` を呼ぶ。現行の目印本数・両腕の合計は参考として併記し、`n_points` には使わない。
- **根拠**: 規則文「300 点以上」は候補を精算した点数の床(候補の目印が 0 なら差は定義できない)。現行の目印本数は参考として併記。
- **セルフレビュー**: ここが唯一、規則文の解釈を要する箇所。plan に明記し、tasks で「n_points の定義=候補の目印本数(登録日以降・対のみ)」をテスト名に固定する。

## D6 候補の再現ゲート(ビット一致)

- **決定**: 朝の特徴行(parquet)→ `rows_with_quote(rows, quote)`(quote は保存 `field` から `ScrapedOdds` 相当を再構成: `horse_number`/`odds=win_odds`/`popularity`)→ `ProductionPredictor.predict` → `pred.p_ens` と全馬 `==`(float の等値)。一致したレースだけ候補を同じ行で予測。
- **根拠**: 保存行に候補の p̂ は無い。「同じ価格」は再現でしか成立せず、再現できた証明は現行の再現以外にない。174 result-review は保存 `market_ev_predictions` との差 ≤0.0027 を「入力の事後更新」と説明した=再構築は黙ってずれる。parquet があれば入力は同一で、不一致は predictor 側の非決定性だけ(num_threads=1 で決定論)。
- **parquet が無い日**(10/31 前の試運転が parquet なしで走った場合など): `build_day_features` で再構築して同じゲートにかける。`features_sha256` の一致は併記するが条件にしない(sha は全行の CSV で、1 列の更新で変わる)。
- **代替**: 許容誤差 1e-6 → 「同じ価格」の主張が弱くなる。研究用途なら許せるが、規則の `replace` に使う数値なので厳密側に倒す。

## D7 候補の種類と第 1 候補

- **postprocess(148 正規化)**: `p_norm_i = p_ens_i / Σ_started p_ens`、`EV_norm_i = p_norm_i × odds_i`、目印=`EV_norm > 1.2`。artifact は無く、`artifact_sha256` はこの関数を含む eval モジュールファイルの sha256(コード変更で sha が変わり照合に落ちる=再登録)。**既読期間での読み(10/25 報告 N1/N2)は「改善差は未確定」で、127% は ③ 再学習との組合せ**。本候補が効く保証はない。登録するのは「学習不要で今すぐ同じ quote で測れる」から。
- **ensemble_dir**: 別ディレクトリの `EnsembleMarketEvModel`(同じ 77 入力の design を要求=`design_matrix` が列不足で落ちれば候補エラー)。sha=当該年 manifest の sha(`load_members` の戻り値)。PL top-3 教師などはこの型で登録(学習は別 spec)。
- **混合列**: 候補なし(決定 3)。混合係数は D17 の規則外更新。

## D8 登録ファイル

- `specs/175-decision-quote-scoring/candidates.json`: 配列。各要素 `name`・`kind`・`artifact_path`(絶対・`ensemble_dir` のみ)・`artifact_sha256`・`mark_rule: {"threshold": 1.2, "column": "expected_return"}`・`registered_on`(JST 日付)・`train_through`(ISO 日付または null)・`note`。
- 三値の点は `captured_at` を JST 日付にして `>= registered_on`。登録前の点は `pre_registration` に同じ集計で別掲(読むためのもの・三値に使わない)。
- ファイルの sha256 を出力に記録。git 管理(変更履歴が登録履歴)。

## D9 診断

- 較正: 系統 ens15/single/mix × EV 帯(≤0.8 / 0.8–1.0 / 1.0–1.2 / >1.2・下限排他・上限包含)× オッズ帯(<5 / 5–10 / 10–20 / 20–40 / ≥40)。各セルで n・Σwon・Σp̂・比・同じ bootstrap の区間(num=won・den=Σp̂ を開催日ごとに集計)。
- 価格ずれ: `closing_available` のレースの全馬で `log(O_F/O_J)`。分位 p10/p50/p90・sd・平均を、全馬/目印(120% 列)/人気帯(1–3・4–8・9+)で。
- 保持率: 目印のうち `p_ens × O_F > 1.2` の割合(`approximation: "p_fixed"` を付ける)。再予測による保持率は `--retention-replay`(再現できたレースで `rows_with_quote` に O_F を入れて再予測)=任意。
- 全部 `diagnostic: true`。三値・終了コードに影響しない。

## D10 朝の特徴行 parquet(174 の変更)

- **決定**: `near-post-capture` の `build_day_features` 直後に `--features-dir`(既定 `artifacts/near_post`)配下 `<day>/features-<sha12>.parquet` を書く。既存なら書かない(append-only)。dry-run でも書く(朝の dry-run で先に作れる)。
- **検証(T0)**: 実 DB の 1 日分 frame で `frame_sha256(pd.read_parquet(path)) == frame_sha256(frame)` を確認。pandas の parquet 往復で object/datetime/カテゴリ dtype が崩れて sha が変わるなら `to_pickle` に切替(短期キャッシュなので版固定の不利は小さい)。切替えた場合は拡張子と contracts/cli.md を更新。
- **捕捉への影響**: 判定順・fetch・保存に触れない(ファイル書き込みが失敗しても捕捉は続ける=警告のみ)。10/31 前に入れる(決定 1)。

## D11 出力 JSON

contracts/report.md。先頭に `power_note`(300 点未満のあいだ三値は insufficient・年内見込み点数)を機械可読+文章で置く(SC-005)。

## D12 テスト

- eval: funnel の各段を 1 件ずつ落とす fixture で合計=件数・順序(2 つの欠陥を持つレースが先の段に落ちる)/ 精算(同着 2 頭=両方の額・払戻欠落=レース除外)/ 同期差(恒等候補=差 0・区間 [0,0])/ 決定論(2 回呼んで JSON 等値)/ 帯境界(0.8・1.0・1.2・5・10・20・40 がどちらに入るか)/ `decide` との結線(299 点=insufficient・300 点+下限 0.0=keep・下限 1e-9=replace)。
- live 単体: fake predictor で再現ゲート(一致/不一致・最大 |Δ|)・candidates.json の sha 不一致で終了 2・凍結 JSON sha 不一致で終了 2・`--out` 既存で終了 2・登録日前後の振り分け。
- live 統合: testcontainers に decision_quotes 2 行+結果+払戻を入れて CLI 集計=手計算。読み取り role での実行(169/170 の role を conftest で作る前例があれば流用)。
- api: B8 parity(D3)。features: leak-guard に `artifacts/near_post` 非参照。
- 174 回帰: near_post の既存テスト不変+parquet 書き込みの単体(存在時に書かない・失敗で捕捉継続)。

## D13 セルフレビュー checklist(codex 代替・plan 段階)

- [x] 二重実装: B8 とは入力型が違うため別実装、同一 fixture の parity テストで固定(D3)。
- [x] 事後選択: 帯・閾値・候補・登録日は結果の前に固定、三値は登録日以降のみ(D8)。bootstrap の seed は凍結 JSON から。
- [x] 分母: 目印本数×100 円。`n_points` は候補の目印本数(D5・解釈を明記)。
- [x] 同期: 差の区間は 1 回の bootstrap の replicate 行差(D4)。
- [x] 来歴: O_F は `observed_at IS NOT NULL AND official_at IS NULL`+結果あり。`official_at ≤ captured_at < post_time`。
- [x] 再現: ビット一致ゲート・parquet 往復は T0 で確認(D6/D10)。
- [x] 読み取り専用・append-only・決定論(plan D11/D12)。
- [x] 174 への波及: 判定順不変・書き込み失敗で捕捉継続(D10)。
- [x] 語彙: 出力に「採用/改善/利益」を使わない。
- [ ] 実装後: result-review に「parquet 往復の結果」「not_reproducible の実測割合」「10/31 の funnel」を記録。
