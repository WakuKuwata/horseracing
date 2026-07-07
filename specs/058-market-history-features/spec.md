# Feature Specification: 過去走の市場評価(人気)as-of 特徴 — 精度最優先モデル(B1)

**Feature Branch**: `058-market-history-features`

**Created**: 2026-07-06

**Status**: 実装完了・**案C' デプロイ(features-015 を本番 main にマージ・精度最優先モデル lgbm-058-acc を非 active 登録・serving を per-model feature_hash 互換化して default lgbm-057=features-014 の serving を byte-parity で維持)**

**採否結果(2026-07-07)**: past_market 4 列(asof_mkt_rank_avg/norm_avg/best・asof_beat_mkt_avg)で features-014→**features-015**。**精度最優先モデル lgbm-058-acc**(pl_topk+OOF-TE jockey/trainer+isotonic・features-015)を学習し **非 active(candidate)登録**、default lgbm-057(features-014)は active 不変(SC-003/SC-005)。**production 19-fold OOS**: win LogLoss 0.21597→**0.21579**(−0.00018)・top2 0.33988→**0.33964**(−0.00024)・top3 0.43021→0.43027(+0.00007=僅微悪化で採用ゲート top3 no-regression のみ FAIL→自動 active 化されず candidate 保存=狙い通り)・win ECE 0.00063→0.00068。**059 の前例どおり binary→pl_topk でゲイン縮小**(spike win −0.00028 → production −0.00018)だが top2/top3 の狙い(2・3着圏)は改善維持。

**serving 互換(T013)の決定=案C'(per-model feature_hash 化で features-015 を本番 main に載せる)**: serving `load_serving_model` は元々 `feature_hash(model_input_features())`(グローバル現行特徴セット)を全 servable モデルに要求するため、**features-015 を本番 registry に bump すると現 active lgbm-057(features-014)が fail-closed** になる。当初は案D(本番 features-014 据え置き・persisted 表示のみ)を採用したが、**ユーザー判断で案C' に切替**(features-015 を本番 main にマージし、accuracy モデルの live 予測も可能にする)。

**案C' 実装(2026-07-08)**: (1) `registry.COMPATIBLE_PRIOR_FEATURE_VERSIONS = {"features-015": {"features-014": <pinned hash 37cd6eb…>}}` + `is_feature_version_servable(trained_fv, trained_hash)` — 古い版は **exact hash ピン留め**でのみ互換(版名を騙る部分集合 artifact を排除)。(2) `load_serving_model` のゲートを exact-path(hash 一致=挙動バイト不変)/ compat-path(互換版 + buildability[model.feature_cols ⊆ 現行] + 自己整合[hash=cols] + categorical/encoder ⊆ cols)に分離、いずれも破れば fail-closed。(3) 予測は既に `predictor.py` が `model.feature_cols` でモデル固有列をスライス。(4) compat 実行は logic_version に `reg=features-015` 付与で native と区別(監査)。**INV-S4 緩和を serving contract に明記**。**共有列バイト一致の担保**: past_market は additive left-merge(右キー一意 + 列名 disjoint)で既存列を数学的に perturb しない(`test_past_market_is_purely_additive`)+ **一度きりの実証: features-014 build == features-015 build の共有 121 列が check_exact + check_dtype 一致(73,633 行)**。**実 DB E2E: lgbm-057(features-014)が features-015 registry 下で compat-load でき、予測 win prob が persisted features-014 値とバイト完全一致(16 頭 mismatch 0)**=SC-005 死守。テスト: features 164 + serving 38 緑・ruff クリーン。codex 3 回レビュー反映済: (設計) hash ピン留め・(実装) categorical/encoder 検証・監査マーカー・contract 更新、(最終) **ブロッカー修正=`is_feature_version_servable` の同一版短絡を削除**(features-015 を名乗る hash 不一致 artifact=drop_features ablation/破損が pin なしで compat-load する穴を塞ぎ、旧 fail-closed を復元)+ TE 宣言ありなのに encoders 無し→fail、loader-level reject テスト 3 件追加。**value-parity の residual**: `test_past_market_is_purely_additive` は構造(右キー一意+列名 disjoint=left-merge は既存列を数学的に不変)を証明、実値 parity は一度きりの 73,633 行 check_exact 実証で担保(testcontainer は合成データのため product golden 不適合)。**マージ後の設計含意(重要)**: features-015 では `model_input_features()` に past_market が含まれるため、**将来 default(意思決定支援・p⊥q)モデルを再学習する際は past_market を明示 drop** して市場独立を維持する必要がある(accuracy モデルのみ past_market を含む)。現 default lgbm-057 は features-014 のまま compat serving=影響なし。

**実 DB E2E(2026-07-07)**: レース 202506010101 で default(無指定)=lgbm-057・logic feat=features-014、`?model_version=lgbm-058-acc`=lgbm-058-acc・logic feat=features-015、available_models の is_selected 切替・past_market 特徴(asof_mkt_rank_norm_avg)が explanation に寄与を確認。lgbm-058-acc を製品範囲(2024-01〜2026-07、lgbm-042 相当)に backfill。テスト: features unit 161 + past_market leak 6 緑・ruff クリーン。

**Input**: User description: 過去のレース(対象レースより厳密に前の出走)の確定人気(オッズ由来の市場ランク)を馬の履歴特徴として as-of 集約し、1・2・3着の予測率を上げる「精度最優先」モデル(B1)。既存の意思決定支援モデル(市場独立)を置き換えず、057 の切替基盤で共存。

## 背景と目的 *(non-normative)*

現行の予測モデル(意思決定支援)は公開情報のみを特徴にし、市場オッズ/人気を**一切特徴化しない**(モデル p と市場 q を独立に保つ製品価値)。一方で、馬の過去走における「市場の評価(人気)」は、その時点の厩舎意図・調教・私的情報を含む群衆の総合判断の蒸留であり、着順単独からは得られない履歴情報を持つ。これを対象レースより厳密に前の出走だけ as-of 集約すれば、リーク安全に予測精度を上げられる。

de-risk spike(3-fold OOS 2021–2023、**baseline=features-014=059 の相対能力を含む真の現行構成** vs +past_market)で **win/top2/top3 の全てが改善**(win 0.23190→0.23162=−0.00028・top2 0.36631→0.36586=−0.00045・top3 0.46149→0.46106=−0.00042、win 3/3 fold 全勝、AUC 全上昇、win ECE 改善 −0.00057)。**採番齟齬の是正**: 当初 spike は陳腐化した features-013 baseline(win −0.00035/top2・top3 −0.00069)で測ったが、別セッションが 059(相対能力、features-014、lgbm-057 active)を先に main に入れていたため、058 を現 main に載せ替え **features-014→features-015** に是正し、059 の上で再測定。**相対能力との一部重複でゲインは ~60-80% に縮小したが win/top2/top3 とも改善維持**。特にユーザー目的の top2/top3(2・3着圏)への効果は肯定。**注意**: 059 の前例(binary −0.00114 → pl_topk −0.00018 に縮小)から、production pl_topk では win ゲインはさらに縮む公算 → フルゲート + production 確認で最終判断。

本 feature は、この過去市場評価特徴を**別用途の「精度最優先」モデル**として実装する。既存の意思決定支援モデル(市場独立)は default(active)のまま維持し、精度最優先モデルは非 active・選択可能として 057 の切替基盤で共存させる。これにより「独立した第二意見」(default)と「最高精度の予測」(選択可)の両方を提供する。

**憲法 II の要請**: 市場オッズ由来を特徴化するため別 spec でリーク防止・利用可能タイミング・評価方法を定義してから実装する。default の意思決定支援モデルには past_market を含めない(p⊥q)。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 過去市場評価特徴のリーク安全な追加(Priority: P1)

過去走の人気(市場ランク)を、対象レースより厳密に前の出走だけ as-of 集約した特徴群として、リーク境界を機械的に保証した上でモデルに供給できる。

**Why this priority**: 特徴とそのリーク安全性が全ての土台。今走の市場評価が特徴に混入すれば予測は無意味になる(未来情報リーク)。

**Independent Test**: 対象レースの人気値を任意に変えても、その馬の past_market 特徴が一切変わらないこと(strictly-before)。同日他レース・未来レースの値を変えても不変。過去走の人気を変えたときだけ特徴が変わる。

**Acceptance Scenarios**:

1. **Given** ある馬に複数の過去走がある, **When** past_market 特徴を計算する, **Then** 対象レースより厳密に前(同日を除く)の出走だけが集約され、今走・同日・未来の値は一切反映されない。
2. **Given** 過去走の無い馬(デビュー馬), **When** 特徴を計算する, **Then** 全て欠損(Unknown/NaN)として渡され、0 で埋められない。
3. **Given** 今走の人気を書き換えたデータ, **When** 特徴を再計算する, **Then** その馬の past_market 特徴は完全に不変(リーク境界テスト)。
4. **Given** 市場評価特徴, **When** モデルの入力特徴一覧を検査する, **Then** 「今走の人気/オッズ」そのものは特徴に含まれない(過去 as-of 集約のみ)。

---

### User Story 2 - 事前登録ゲートによる採否判定(Priority: P1)

過去市場評価特徴を加えたモデルが、公開情報のみのモデル(baseline)に対して walk-forward OOS で予測精度を上げるかを、結果を見る前に固定した採用ゲートで判定する。1・2・3着すべての非悪化を必須とする。

**Why this priority**: 「効いたか」を偶然でなく厳密に確かめる。ユーザー目的は 1・2・3着の予測率なので top2/top3 の非悪化を必須ゲートにする。

**Independent Test**: フル walk-forward で baseline(過去市場評価を落とした構成)と candidate(加えた構成)を同一条件で比較 → win 採否 + top2/top3 非悪化を判定。閾値は結果を見る前に固定。

**Acceptance Scenarios**:

1. **Given** 事前登録した採用ゲート, **When** フル walk-forward feature-eval を実行, **Then** 平均 win LogLoss 改善かつ平均 win ECE 非悪化(tol 内)かつ fold 別ガード(過半勝ち・最悪 fold 非悪化 tol)を満たすときのみ「採用」と判定する。
2. **Given** 採否判定, **When** top2/top3 の OOS 指標を確認, **Then** top2/top3 の LogLoss が非悪化であることを必須条件(MUST)として併せて判定する。
3. **Given** 評価結果, **When** 閾値を検討, **Then** 結果を見てから閾値を動かさない(事前登録・憲法 III)。

---

### User Story 3 - 精度最優先モデルの共存運用(Priority: P2)

採用と判定された場合、過去市場評価を含む精度最優先モデルを学習・登録し、意思決定支援モデル(default)を変えずに、レース詳細で選択可能なモデルとして共存させる。

**Why this priority**: 特徴と採否(US1/US2)が土台。運用は 057 の既存切替基盤に載せるだけ。独立性を保つため default は意思決定支援のまま。

**Independent Test**: 精度最優先モデルを登録し用途ラベルを付与 → レース詳細で意思決定支援モデル(既定)と精度最優先モデルを切り替えて閲覧できる。既定(active)は意思決定支援のまま変わらない。

**Acceptance Scenarios**:

1. **Given** 採用された精度最優先モデル, **When** モデルを登録する, **Then** 非 active(候補)として登録され、既存の active(意思決定支援モデル)は変わらない(eval 合格 ≠ 自動昇格)。
2. **Given** 登録済みの精度最優先モデル, **When** 用途ラベルを付与, **Then** 「精度最優先(過去市場評価含む)」等の用途が人間に判別できる。
3. **Given** 両モデルの予測が存在するレース, **When** レース詳細を開く, **Then** 既定で意思決定支援モデルが表示され、精度最優先モデルに切り替えて予測を閲覧できる(057 の切替基盤)。

---

### Edge Cases

- デビュー馬・過去走ゼロ → 全 past_market 特徴 NaN(0 埋め禁止)。カバレッジで Unknown 量を明示。
- 過去走はあるが人気欠損 → その走は集約母集団から除外(残りの過去走で集約)。
- 過去走が全て未確定(結果無し) → 着順ベースの beat-market は NaN、人気ベースは有効。
- 採用ゲート不合格(win 悪化 or top2/top3 悪化) → 精度最優先モデルは登録・昇格しない(方向は spike で肯定でも、フル OOS で機械判定に従う)。
- 精度最優先モデルは default にしない(独立性維持のため意思決定支援が default 継続)。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは過去走の市場評価(人気ランク)を、対象レースより厳密に前(同日除外)の出走だけを集約した as-of 特徴群として算出できなければならない。今走・同日・未来の市場評価は特徴に一切流入してはならない。
- **FR-002**: システムは「今走の人気/オッズ」そのものをモデルの入力特徴にしてはならない(過去 as-of 集約のみ)。特徴名は既存のリーク検査(禁止トークン)に抵触してはならない。
- **FR-003**: 過去走の無い馬(デビュー馬)や人気欠損は、欠損(Unknown/NaN)として扱い 0 で埋めてはならない(Unknown と 0 の区別)。
- **FR-004**: システムは対象レースの市場評価値を変更しても、その馬の as-of 市場評価特徴が不変であることを機械的に保証しなければならない(リーク境界テスト:今走・同日・未来の不変)。
- **FR-005**: システムは過去市場評価特徴を加えたモデルを、公開情報のみの baseline に対し walk-forward OOS で比較し、**事前登録した**採用ゲート(平均 win LogLoss 改善・平均 win ECE 非悪化・fold 別ガード)で採否判定しなければならない。閾値は結果を見て変更してはならない。
- **FR-006**: 採否判定は **top2/top3(2・3着圏)の OOS 指標の非悪化を必須条件(MUST)**として含めなければならない。
- **FR-007**: 採用と判定された場合、システムは過去市場評価を含む精度最優先モデルを学習・登録できなければならない。登録は非 active(候補)とし、既存の active(意思決定支援モデル)を自動的に置き換えてはならない。
- **FR-008**: システムは精度最優先モデルに人間可読の用途を付与し、意思決定支援モデル(default)と切り替えて閲覧できなければならない(057 の切替基盤を利用)。
- **FR-009**: default の意思決定支援モデルは過去市場評価特徴を含めてはならない(モデル p と市場評価の独立性を維持)。過去市場評価特徴は精度最優先モデル専用とする。
- **FR-010**: システムは既存の採用済みモデルの予測を壊してはならない。特徴セット変更は特徴定義版として明示され、既存の材料化特徴のパリティ(再現一致)と staleness 検知(市場評価ソースの追加を含む)を保たなければならない。

### Key Entities *(include if feature involves data)*

- **過去市場評価特徴(past_market)**: 馬の過去走の人気ランクを as-of 集約した特徴群(直近平均・正規化平均・最良・市場超過=人気−着順の平均)。値は過去走由来のみ。モデル入力に供され、今走市場評価は含まない。
- **精度最優先モデル(accuracy-first)**: 過去市場評価特徴を含む学習済みモデル。用途は「精度最優先」。非 active・選択可能として意思決定支援モデルと共存。
- **意思決定支援モデル(default)**: 市場から独立した既存モデル。default(active)。過去市場評価特徴を含まない。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 過去市場評価特徴は、対象レースの市場評価を任意に変えても不変であること(リーク境界)が機械的に検証される。
- **SC-002**: フル walk-forward OOS で、過去市場評価を加えた構成の 1・2・3着すべての予測指標(LogLoss)が公開情報のみの baseline に対し非悪化であること(win は改善・top2/top3 は非悪化以上)が事前登録ゲートで確認される。
- **SC-003**: 採用時、精度最優先モデルが登録され、意思決定支援モデル(default/active)は変わらない。
- **SC-004**: オペレータはレース詳細で意思決定支援モデルと精度最優先モデルを切り替えて予測を閲覧でき、既定は意思決定支援モデルである。
- **SC-005**: default の意思決定支援モデルの予測は本 feature 導入前と不変(過去市場評価特徴を含まない=独立性維持)。

## Assumptions

- 過去走の人気(`race_horses.popularity`)は実 DB で ~99.6% 充足(市場ランク=オッズ由来)。オッズ「量」(magnitude)は本 feature では使わず人気ランクのみ(deferred)。
- 採用ゲートは 020/023/056 と同型の feature-eval(binary 設定)を PRIMARY とし、top2/top3 は同一 harness の Harville 導出指標で非悪化を確認する。production 上の限界寄与は binary spike より小さい可能性がある(020 教訓)ため、採用時は production 構成(pl_topk+TE+isotonic)での再学習・確認を行う。
- 精度最優先モデルの共存・切替・用途ラベルは 057(実装済み)の基盤をそのまま利用し、本 feature で新たな切替 UI/契約は作らない。
- 特徴の材料化・serving 経路は既存(025/055)を踏襲。FEATURE_VERSION を features-014→features-015 に上げ、市場評価ソース(popularity)を staleness fingerprint に含める。
- ECE のわずかな悪化は production の校正(isotonic/two_gamma、017/048)で相殺する設計(win-eval とは別経路)。
- **Deferred(本 feature 外)**: 精度最優先モデルを default(active)にすること・オッズ量特徴/past_market の TE 化/条件別・今走オッズの特徴化(serving で確定オッズ不在=B2、方針外)・betting/推奨側での past_market 利用。
- **Codex**: 環境未インストールで本セッション複数回起動失敗 → codex unavailable。single-opinion + セルフレビュー checklist で進める(features/eval/採用ゲート/リーク境界/FEATURE_VERSION に触るため本来 MUST-codex)。
