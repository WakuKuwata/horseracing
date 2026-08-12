# Research: 予測根拠の実効寄与化(レース内センタリング)

**Feature**: 089-explanation-race-centering | **Date**: 2026-08-09(codex レビュー反映済)

## D1. 問題の実測(spec 背景の再掲・数値の正本)

active モデル lgbm-064-f02acc の永続予測(explanation 保存済み)を race_class で層別:

| | 新馬(427頭) | 非新馬(3,818頭) |
|---|---|---|
| top-5 スロットに血統 | 5.9% | 1.2% |
| top-5 出現率: days_since_last | 99.3% | 9.3% |
| top-5 出現率: prev_finish | 29.0% | 88.7% |
| top-5 出現率: class_transition | 71.7% | 12.0% |

新馬戦の top-5 常連の中身(value / 寄与のレース内 std):

| 特徴 | value | 寄与レース内 std | レース内実効性 |
|---|---|---|---|
| prev_finish | 100% NaN | 0.024 | ほぼ定数=softmax 相殺 |
| days_since_last | 100% NaN | 0.013 | 同上 |
| class_transition | 100% NaN | 0.006 | 同上 |
| jockey_win_rate_vs_field | 実値 | 0.172 | 実効 1 位 |
| weight | 70.7% NaN | 0.084 | 実効 2 位タイ |
| sire_surface_win_rate | 実値 | 0.082 | 実効 2 位タイ |

**結論**: モデルの順位付けは正しい(race-softmax がレース内定数を相殺)。誤っているのは
「母集団比の margin 寄与の絶対値」で top-5 を選ぶ保存意味論。

**注意(codex #1)**: レース内 std が非ゼロ(0.006〜0.024)である事実は「センタリング
だけでは top-5 混入を 0 にできない」ことを意味する(平均を引いても分散は残る。全馬 NaN の
特徴でも TreeSHAP の相互作用配賦で寄与は馬ごとに微妙に揺れる)→ D5 の候補除外で構造保証。

### D1a. 実装後の実測(2026-08-09・実 DB・active lgbm-064-f02acc)

| | 新馬戦 202604020403(13頭) | 経験馬レース 202604020408(14頭) |
|---|---|---|
| method_version | 2 | 2 |
| レース内定数の特徴数 | **109 / 137(79.6%)** | 29 / 137(21.2%) |
| レース内定数の top-5 出現 | **0 件**(v1 実測 29〜99.3%) | 0 件 |
| FR-002 違反(全馬値で厳密検証) | 0 | 0 |
| v2 加法性(INV-E4b)違反行 | 0/13 | 0/14 |
| top-5 主役 | jockey 92.3% / weight 84.6% / trainer 69.2% / sire 61.5% | asof_pm_support_last 85.7% / **prev_finish 78.6%**(実値 7 種・centered +0.31 で 1 位) |

**新馬戦では特徴の 79.6% がレース内定数**であり、v1 の「母集団比の絶対値」選定はこの母集団
から根拠を選んでいた — 問題の規模が当初推定(3 特徴)より大きかったことが判明した。
同一レース・同一モデルでの旧 v1 run と新 v2 run の比較で**確率 mismatch 0/13**(SC-002)。

## D2. なぜ保存時にしか直せないか(表示側修正の不能性)

- 保存形式は top-5+`other_contribution` に切り詰め済み。特徴 f が馬 A では top-5、馬 B では
  other に畳まれるため、読み出し側ではレース内平均 mean_race(f) が計算できない
  (新馬戦の prev_finish 寄与は 124/427 頭にしか個別記録がない)。
- `compute_explanations` は切り詰め前の全寄与行列(n_horses × n_features+1)を保持して
  おり、かつ serving `predict_race` はレース単位のバッチで呼ぶ(predictor.py:99)。
  センタリングに必要な情報が揃うのはこの一点のみ。

**Decision**: 保存時(`compute_explanations` 内)にセンタリング。表示側の近似補正案は却下
(不完全データからの誤った補正になる)。

## D3. センタリングの数学的正当性と命名(codex #2 採用: 命名を限定)

race-softmax 系(cond_logit / pl_topk)では p_i = exp(z_i)/Σ_j exp(z_j)。margin z_i に
レース内定数 c を全馬に加えても p は不変(exp(c) が約分)。センタリング済み寄与
centered_{i,f} = contrib_{i,f} − mean_race(contrib_f) について次の恒等式が成立する:

    Σ_f centered_{i,f} = z_i − mean_race(z) = log p_i − mean_race(log p)

すなわち centered は**レース内相対 logit(= softmax 前の相対スコア)の正確な加法分解**で
あり、レース内順位・ペア間対数オッズ差に整合する。ただし **softmax 後の確率そのものへの
SHAP ではない** — 説明文言は「同一レース内の平均に対する、レース内正規化(softmax)前の
相対スコア寄与(最終確率の内訳ではない)」に限定する(codex 推奨文言を採用)。
平均は自馬を含むレース内平均であり「他馬平均」という表現は式と異なるため使わない。

softmax 後の確率空間 exact SHAP を採らない理由: (a) 他馬特徴の cross-attribution が入り
表示が複雑化 (b) 背景母集団の定義問題 (c) 計算コスト。LightGBM 内蔵 pred_contrib の
決定性・040 実証済み経路の維持を優先(codex も妥当と判定)。

**binary objective では成立しない**: 独立 sigmoid ではレース内定数寄与も各馬の確率に実際に
効くため、センタリングは誤った帰属になる。→ objective 分岐(D6)。

## D4. v2 保存形式(codex #6 採用: centered の score/other も保存)

**Decision**:
- `method_version: 2`。items は |centered| 降順(タイ特徴名昇順)で選定(D5 の候補除外
  つき・最大 k 件・**K 未満可**)。
- 各 item は `contribution`(生・母集団比)と `contribution_centered` の**両方**を保持。
- `base_value` / `score` / `other_contribution`(生)は v1 と同一定義を維持 →
  生の加法性検査(INV-E1)は v1/v2 共通式で成立(監査の連続性)。
- **新規フィールド**(codex #6 採用・当初の「centered other は保存しない」案を撤回):
  - `score_centered` = z_i − mean_race(z)(= Σ_f centered_{i,f})
  - `other_contribution_centered` = score_centered − Σ items.contribution_centered
  - `centering_population_size` = センタリング母集団の頭数
  これにより**保存 JSON 単体から v2 加法性(score_centered = Σtop centered + other
  centered)が検証可能**になる。per-feature レース内総和=0 は top-K 切り詰め後の保存形式
  からは検証不能なので生成時検査に置く(D5・quickstart の SQL 検証案は撤回)。

**Alternatives rejected**:
- centered のみ保存(生を捨てる): INV-E1 が検査不能になり 040 の監査性を退行させる。
- 全特徴の寄与を保存(切り詰め廃止): JSONB サイズ約 137/5 倍。表示は top-K で足り、
  再センタリングは --force 再生成で可能なため過剰。
- centered other 非保存(当初案): 各馬の top-K 外 centered 合計は一般に非ゼロで、保存
  しないと v2 の主値から相対スコアが再構成できない(codex 指摘採用)。

## D5. 候補除外・母集団・検査(codex #1/#3/#4 採用)

**候補除外(SC-001 の構造保証)**: top-K の候補から「**そのレースで value が全馬同値の
特徴**」を除外する(全馬 NaN も同値と扱う。NaN と非 NaN の混在は同値でない)。値が全馬で
同一の特徴はそのレースで馬を区別できず、centered の残差は TreeSHAP の相互作用配賦ノイズで
ある。除外により新馬戦の prev_finish/days_since_last/class_transition(全馬 NaN)の top-5
出現は**構造的に 0** になる(SC-001 を機械保証)。候補が k 未満なら items は k 未満で保存
(ゼロ埋め・水増し禁止)。**限界の明記(codex 要求)**: 相互作用経由の寄与配賦も同時に
隠れるが、値が同一の特徴を「この馬がこのレースで上位/下位の根拠」として表示しない方が
誠実である、を採用理由として固定。|centered|≤ε 型の除外は不採用(ε 調整が恣意的で、
実在する小さな実効差まで隠すため)。

**母集団(codex #3 採用)**: センタリング平均は **softmax 分母と同じ「予測バッチの全
started 行」**で取る。検査不能行を除いた平均は「レース内平均との差」ではなくなるため、
**レース単位 atomic** とする: 1 行でも検査不能(非有限値・整合性不備)なら当該レースの
v2 explanation は全行 None(予測は無傷)。当初の「通過行のみで平均」2 パス案は撤回。

**整合性検査(codex #4 採用: 現行 INV-E1 は自己参照)**: 現行実装は score を Σcontrib
から合成して再合算しており、実質 float 分割誤差の検査でしかない。是正:
- **raw margin 照合**: `compute_explanations` に任意引数 `expected_raw_scores` を追加し、
  base + Σ全 contrib を突き合わせる(rtol 1e-6)。serving は既に `raw = model.raw_predict(X)`
  を計算済みなので**追加の booster.predict は不要**(market-offset モデルは v2 対象外
  = D6 のため offset 補正も不要)。呼び出し元が渡さない場合は従来の内部整合検査のみ
  (v1 後方互換)。
- **有限性検査**: contrib 行列に NaN/Inf があればレース atomic に None。
- **per-feature レース内総和 ≈ 0**(atol): 生成時に検査、失敗はレース atomic None。
  保存形式からは検証不能なので runtime/単体テストの責務(quickstart 修正済)。
- **v2 加法性**: score_centered == Σ items.contribution_centered +
  other_contribution_centered(構成的に成立・保存 JSON から検証可能)。

**1 頭レース(codex 採用)**: 全特徴が「全馬同値」に該当し候補ゼロ → items は**空配列**で
保存(score_centered=0・other_centered=0・population_size=1)。任意のゼロ特徴を
「上位5要因」として表示するのは不誠実(codex)。表示は D8。

## D6. objective 分岐と market-offset(codex #5 採用: offset モデルは v2 対象外)

**Decision**: `compute_explanations(..., center_within_group: bool = False)` の opt-in。
- 既定 False = v1 バイト同一出力(INV-E5)。定数は `METHOD_VERSION_V1 = 1` /
  `METHOD_VERSION_V2 = 2` に分離(単一定数の 2 への書き換えは binary=v1 と衝突するため。
  codex 指摘採用)。
- serving `predict_race` が center を渡す条件:
  `model.objective in WinModel.SOFTMAX_OBJECTIVES` **かつ `model.market_offset is None`**。
- **market-offset モデル(lgbm-060-mkt 系)を v2 対象外にする理由(codex #5)**: softmax の
  実入力は `log-q offset + tree margin` だが pred_contrib は tree 部分しか説明しない。
  centered を「レース内相対スコアの分解」と主張すると offset 成分が欠けて虚偽になる。
  offset の centered 保存(`offset_centered`)は将来拡張(deferred)とし、089 では v1 維持
  (既存 040 と同じ限界のまま)で嘘をつかない。market-offset モデルの E2E を追加して
  v1 のままであることを固定する。
- 呼び出し元は serving predictor.py の 1 箇所のみ(全 tree grep で確認・training CLI 経路
  なし。codex も確認一致)。判定集合は training の `WinModel.SOFTMAX_OBJECTIVES` を正本と
  し文字列リテラルの二重管理を避ける。
- **防御範囲の拡張(codex 採用)**: 現行 try/except は pred_contrib 呼び出しのみ。
  センタリング・ソート・JSON 化を含む説明経路全体を関数内+呼び出し側の両方で防御し、
  例外は常に「explanation None・予測無傷」に落とす。

## D7. API 契約(additive 必須+version 厳密化 — codex #7 採用)

**発見(plan 時実査)**: API router は `Explanation.model_validate(exp)`
(predictions.py:151)で JSONB を typed model に落とす。pydantic v2 の既定は extra キーを
**黙って落とす**ため、schema に新フィールドを追加しない限り v2 の値は API 応答から静かに
消える(075 の splat-null 罠と同型。今回は plan 段階で検出)。

**Decision**:
- `ExplanationItem.contribution_centered: float | None = None`、`Explanation` に
  `score_centered: float | None = None` / `other_contribution_centered: float | None = None` /
  `centering_population_size: int | None = None` を追加(すべて additive・v1 行は None)。
- 「JSONB そのまま透過」ではなく「additive な型付き変換」と正しく表現する(codex)。
- openapi 再生成 → front/admin snapshot 更新+型再生成+drift-check 緑。既存フィールドの
  削除・改名ゼロ。admin に explanation UI は無い(生成型の同期のみ)。
- v2 の整合条件: v2 行は全 item で有限な contribution_centered と score_centered /
  other_contribution_centered を持つこと。生成側検査(D5)がこれを保証する。

## D8. front 表示(method_version 厳密分岐 — codex #7 採用)

**Decision**: `ExplanationPanel` の分岐は **`method_version === 2` の厳密一致**(`>= 2` は
将来 v3 を誤表示するため禁止。1 → v1 表示、それ以外の未知版 → 「未提供」扱い):
- **v2**: 主表示値(バー・符号付き数値)= `contribution_centered`。タイトル
  「レース内でのスコア寄与(上位k要因)」。注記(常時)=「同一レース内の平均に対する、
  レース内正規化前の相対スコア寄与です(最終確率の内訳ではありません)」+既存の因果注記。
  「その他の特徴(合算)」行は **`other_contribution_centered`** を表示(centered で意味論
  統一 — 当初の非表示案は centered other を保存する D4 改訂により撤回)。
  items 空(1頭レース等)は「このレースでは比較できる差がありません」を表示。
  v2 なのに `contribution_centered` が null/欠落の item がある場合は生値へフォールバック
  **せず**、explanation 全体を「未提供(形式不整合)」として扱う(誤意味論の値を主表示に
  出さない)。
- **v1**(既存保存行): 現行表示・現行注記を維持(退行なし)。

## D9. 旧行・backfill・冪等(codex 採用: バイト一致主張の限定)

**Decision**: v1 行は書き換えない(append-only 監査・憲法 V)。過去分の v2 化は既存
`serving predict-backfill --from --to --force`(044)の任意運用(新規 CLI なし)。--force は
新 run を append し、読み出し(select_prediction_run)は最新 run を選ぶため表示は自然に
v2 へ切り替わる。

**確率バイト一致の主張範囲(codex 採用)**: 「同一モデル・同一入力(X・オッズ状態・
calibrator・stage_discount)での explanation 有無/新旧比較」に限定する。--force 再生成と
過去 run の間の一致は、データ訂正・market-offset の current odds・runtime stage-discount
により運用上保証できない(SC-002/SC-006 の文言を spec 側で修正済)。logic_version への
追記は不要(説明は確率経路に非関与・method_version が explanation 自身に永続され監査十分)。

## D10. codex 設計レビュー採否(2026-08-09・`codex exec --sandbox read-only`)

CLAUDE.md 規約(serving 永続化経路=MUST トリガー)に基づき実施。codex 総合判定:
「センタリングの核は妥当・採用推奨。ただし条件付き」。**BLOCKER 1 + HIGH 6 + その他 8 を
全件триアージし、採用 12 / 部分採用 1 / 不採用 1**:

| # | 指摘 | 採否 | 反映先 |
|---|---|---|---|
| 1 | [BLOCKER] センタリングだけでは top-5 出現率 0% を保証できない(分散は残る・相互作用配賦・K 埋め) | **採用** | D5: 全馬同値(NaN 含む)特徴の候補除外+K 未満許容で 0% を構造保証。spec SC-001/FR-002 改訂 |
| 2 | [HIGH] 「確率への実効寄与」は命名過剰。正体は softmax 前のレース内相対 margin 寄与(Σcentered = log p_i − mean log p) | **採用** | D3: 命名限定・恒等式明記・「他馬平均」→「レース内平均(自身含む)」 |
| 3 | [HIGH] 「検査通過行だけの平均」は softmax 母集団とずれる。レース atomic にすべき | **採用** | D5: 全 started 行で平均・1 行でも不能なら全行 None(2 パス案撤回) |
| 4 | [HIGH] 現行 INV-E1 自己検査は自己参照(score を Σcontrib から合成) | **採用** | D5: `expected_raw_scores`(serving の既計算 raw)照合+NaN/Inf 明示検査。追加 predict 不要=性能見積もり維持 |
| 5 | [HIGH] market-offset モデルは pred_contrib が offset を説明せず v2 の主張が虚偽になる | **採用** | D6: offset モデルは v2 対象外(v1 維持)+E2E 固定。`offset_centered` は deferred |
| 6 | [HIGH] centered の「その他」と score は保存すべき(保存 JSON から相対スコア再構成不能・総和 0 検査も保存からは不能) | **採用** | D4: `score_centered`/`other_contribution_centered`/`centering_population_size` 追加(当初案撤回)。quickstart の SQL 検証を生成時テストへ変更 |
| 7 | [HIGH] version 分岐の厳密化(=== 2・v2 は finite 必須・fallback 禁止・「透過」でなく型付き変換) | **採用** | D7/D8 |
| 8 | METHOD_VERSION 単一定数化は binary=v1 と衝突 | **採用** | D6: V1/V2 定数分離 |
| 9 | try/except の防御範囲を説明経路全体へ | **採用** | D6 |
| 10 | 1 頭レースでゼロ特徴を「上位5要因」と表示するのは不誠実 → items 空+「比較対象なし」 | **採用** | D5/D8 |
| 11 | --force 再生成間の確率バイト一致は運用上保証できない(データ訂正・current odds・stage-discount) | **採用** | D9: SC-002/SC-006 の主張範囲を限定 |
| 12 | 040 の正本契約(prediction-explanation.md)が v1 固定のままで競合 | **採用** | 040 contract に「v1 契約・v2 は 089 参照」追記をタスク化 |
| 13 | ε ベースの候補除外(abs(centered)≤ε) | **部分採用** | 除外自体は採用するが基準は ε でなく「value 全馬同値」(決定的・調整パラメータなし)。D5 に理由記録 |
| 14 | v2 で centered other を UI 非表示にするのは可 | **不採用**(より強く) | centered other を保存する以上、v2 の「その他」行は centered 値で**表示**する(意味論統一で誤読源を消す)。D8 |

binary=v1 維持・呼び出し元 1 箇所・admin は型同期のみ・feature_snapshots 混入なし・
--force の append-only 整合は codex も妥当と確認(差分なし)。独立 Codex レビューとも
主要結論一致と codex 自身が報告。
