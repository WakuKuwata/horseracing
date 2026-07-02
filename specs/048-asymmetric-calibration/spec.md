# Feature Specification: 非対称 p 校正 two_gamma (Asymmetric Calibration)

**Feature Branch**: `048-asymmetric-calibration` / **Created**: 2026-07-02 / **Status**: **ADOPTED**(事前登録ゲート機械通過、製品切替済み)
**Input**: 047 診断でモデル p の tail 圧縮が判明(本命側の乖離が穴側より大=非対称)。一様 power(017/046、p'∝p^γ)は対称なシャープ化しかできない → **p のみを入力とする**非対称校正 two_gamma を候補として、事前登録 A/B で一様 power に勝つ場合のみ製品(046 経路)へ採用する。

## 背景と目的
046 で製品経路は walk-forward power 校正(γ=1.34 実フィット)を既定適用済み。047 の所見: 本命帯(q≥0.30)でモデル p 0.185 vs 実現 0.413、穴帯で p 0.055 vs 実現 0.016 — 圧縮は両 tail だが**本命側の乖離が大きい非対称**。一様 γ は両側を同率でしか動かせない。
**重要な境界**: 047 の条件付けは q(市場)だが、**校正の入力に q を使うことは p×q blend であり不採用済み**(041 記録)+ p≠q 分離(憲法 II)に抵触。よって候補は **p のみの関数**である区分 power(two_gamma)に限定する。q 条件付き校正はスコープ外。

## 候補手法(事前登録・変更禁止)
**two_gamma**: engine 正規化済み p に対し、連続な区分 power
- w(p) = p^γlo (p ≤ pivot) / pivot^(γlo−γhi) · p^γhi (p > pivot) — pivot で連続・γ>0 で単調
- **pivot = 0.15 固定**(フィットしない。根拠: 047 の q_band 境界と一致・基礎勝率の約 2 倍。結果を見て動かさない)
- (γlo, γhi) ∈ [0.1, 5.0]² を train 窓内のみで winner-NLL 最小化(決定論: 粗グリッド→座標 golden 交互 2 巡)
- 適用は race-normalized ベクトルに対して(017 canonical)・engine `_norm` 整合
- 不足時(min_races=50/min_wins=30)は identity fallback(017 同一)

## 採用ゲート(事前登録 — 実行前に固定、数値を見て動かさない)
評価 = `evaluate_calibration_db` の walk-forward(train_frac=0.5、eval は train より厳密後、γ 選択は train 内のみ)。**同一窓・同一 split で baseline=power(γ MLE)と candidate=two_gamma を比較**:
- **PRIMARY**: eval winner-NLL(two_gamma) < NLL(power) かつ Brier(two_gamma) ≤ Brier(power) + 1e-4
- **MUST**: 009 後 joint(exacta・trifecta)の not_degraded=True(017 と同一 tol、raw p 比)
- **AUX(参考・ゲートでない)**: ECE・reliability slope・top-band over/under
- fit 不十分(identity fallback 発動)なら不採用
- **ADOPTED → 046 の `_fit_product_p_calibrator` を method="two_gamma" に切替**(lv に自動記録)。**不採用 → power 維持・負結果を記録**(ブランチ保全不要、eval 拡張自体は無害なのでマージ可)

## Requirements
- **FR-001**: two_gamma は p のみを入力とし、q/オッズ/結果を校正関数に使わない(p≠q・リーク境界)。フィットは walk-forward train 窓内のみ(選択リークなし)。
- **FR-002**: 変換は pivot で連続・単調(γ>0)であり、race-normalized ベクトルに適用・engine 正規化と整合。
- **FR-003**: 採用判定は本 spec の事前登録ゲートに完全一致。データを見た後の pivot/ゲート変更は禁止。
- **FR-004**: 不採用でも評価インフラ(method="two_gamma" の fit/apply/eval)はマージ可(害なし・opt-in)。製品切替は ADOPTED 時のみ。
- **FR-005**: スキーマ・API 変更なし。既存 power 経路はバイト不変(後方互換テスト)。

## Success Criteria
- **SC-001**: two_gamma の fit/apply が決定論・連続性(pivot 境界)・単調性・正規化(Σ=1)をテストで満たす。
- **SC-002**: γlo=γhi のとき一様 power と一致(退化整合)。
- **SC-003**: 事前登録 A/B が実 DB で機械実行され、ゲート判定どおりに採否が決まり記録される。
- **SC-004**: probability/betting スイート緑・既存 power/identity 挙動不変。

## Assumptions
- 評価データ = 永続化済み予測×結果(load_p_samples、046 時点で 517+ レース)。窓は全永続化期間・train_frac=0.5(evaluate_calibration_db 既定)。サンプルが薄い場合は不採用側に倒れる(identity fallback)。
- codex 見送り宣言済み(セッション内 2 回起動失敗)。プロトコルは 017 の確立済み A/B を流用。
## 結果(2026-07-02 実 DB A/B — 事前登録ゲート機械判定)

**第 1 回 A/B は無効(underpowered)**: 事前登録窓「全永続化期間(2024-11-02..2025-10-26)・train_frac=0.5」を機械実行したところ、予測は 2024-11(299)/2024-12(252)に密集し 2025 年側は孤立 3 レースのみ → **eval n_races=3** で検定力ゼロ。これはサンプル数(メタデータ)から判明した設計欠陥であり、結果を見た調整ではない。**窓をデータ密度のみに基づいて 2024-11-02..2024-12-28(密集 551 レース)へ改訂**し、ゲート・train_frac・両手法同一条件は不変のまま再実行。

**改訂窓 A/B(train 275 レース / eval 276 レース、dead-heat 0)**:

| 指標(eval) | raw p | power(γ=1.29854) | two_gamma(γlo=1.57364, γhi=0.60749) |
|---|---|---|---|
| winner-NLL(主) | 2.2479 | 2.2194 | **2.1954** |
| Brier(主) | 0.8543 | 0.8538 | **0.8476** |
| ECE(補助) | 0.0127 | 0.0076 | **0.0060** |
| rel.slope | 1.1489 | 0.8845 | 0.9727 |
| joint exacta NLL(raw 4.5841) | — | 4.5650 ✓ | **4.5293 ✓** |
| joint trifecta NLL(raw 6.9637) | — | **6.9835 ✗(悪化)** | **6.9510 ✓** |

- **PRIMARY**: NLL 2.1954 < 2.2194 ✅ / Brier 0.8476 ≤ 0.8538+1e-4 ✅
- **MUST**: joint exacta・trifecta とも not_degraded=True ✅(power は trifecta で悪化していたのを two_gamma が解消)
- identity fallback なし(sufficient=True) ✅
- → **ADOPTED**。γhi=0.607<1(本命側を押し上げ)・γlo=1.574>1(穴側シャープ化)は 047 の「tail 圧縮・本命側乖離大」所見と正確に整合。
- 製品切替: `_fit_product_p_calibrator` → method="two_gamma"。実 DB E2E(202505040401): 550 informative races で γlo=1.733/γhi=0.385 フィット、`pcal=two_gamma;gamma_lo=…;gamma_hi=…;pivot=0.15;…` が logic_version に記録、win 9+exotic 30 生成。
- 診断 CLI `kelly-calibration-compare` の明示 method="power" は据え置き(製品経路のみ切替)。

## Deferred
q 条件付き校正(=p×q blend、不採用済み方針)・3 区分以上/スプライン/isotonic・オンライン再フィット・pivot の再検討(データを見た後の変更は禁止のため次 feature 扱い)
