# Data Model: モデル確率校正と edge haircut (017)

**スキーマ変更なし**(013 同様)。校正パラメータは recommendations.logic_version に格納。
016 の `stake_fraction`(migration 0006)を再利用。新規は value object とレポート(非永続)。

---

## 1. 永続スキーマ

変更なし。Kelly 推奨は既存 `recommendations` に保存(016 と同一)。校正適用時は **logic_version に校正情報を
追記**: `...;pcal=power(p^gamma);gamma=...;pwin=(date_from,date_to);psel=mle;haircut=rel:0.05;base_mv=...`。
これで `stake = stake_fraction × bankroll` に加え、どの校正器・haircut で生成したかを再現・監査できる(憲法 V)。

---

## 2. PCalibrator（value object、非永続 / logic_version にエンコード）

013 の `FLCalibrator` の p 版。fit は walk-forward(caller 保証)。

| フィールド | 意味 |
|---|---|
| `method` | `power`(=temperature, MVP) / `beta`(候補) / `isotonic`(gated) / `identity`(fallback) |
| `params` | power: `{"gamma": float}`(γ=1/T、γ<1 で過信緩和) |
| `train_window` | (date_from, date_to)（fit 窓） |
| `n_races` / `n_samples` | 窓内レース数 / informative(勝者 in-field・非 flat)数 |
| `prob_range` | 学習で見た p の (min, max)（外挿監査） |
| `select` | 方式/ハイパラ選択法（`mle` 等、窓内で実施） |
| `base_model_version` | 校正対象の予測モデル版 |
| `sufficient` | False → identity fallback（min_races/min_wins/per-band 未達） |
| `logic_version` | 上記を文字列化（再現キー） |

**apply**: `p'_i ∝ p_i^γ` をレース内正規化 + 009 engine-consistent clip（`_engine_normalize`）で **エンジンが
受け取るベクトルと一致**（FR-004、013 の `apply_g` と同型）。

---

## 3. Calibrated CanonicalField（016/011 の field に p' を適用）

`canonical_field`(011)で構築した field の `p_norm` を PCalibrator で `p'_norm` に変換し、009/Kelly はこの
p' を使う。`apply_p_calibrator(field, calibrator) -> CanonicalField`（p_norm を p'_norm に差し替え、
field_size/excluded/number_to_id は不変、レース内 Σ=1 維持）。**p≠q**: q(010)側は触らない。

---

## 4. PCalibrationReport（評価、非永続）— US1 採用ゲート

生 p vs 校正 p' の品質。realized 1 着教師、同着除外。

| フィールド | 意味 |
|---|---|
| `scope` | overall / 人気帯ラベル |
| `n_races` / `n_samples` / `n_dead_heat_excluded` | 件数・除外件数 |
| `nll_p` / `brier_p` / `ece_p` | 生 p |
| `nll_pp` / `brier_pp` / `ece_pp` | 校正 p'（race-normalized） |
| `reliability_p` / `reliability_pp` | 固定ビンの (mean_pred, emp_rate, n) |
| `reliability_slope_p/pp` | overconfidence 指標（傾き） |
| `over_under_top_p/pp` | 上位確率帯の over/under |
| `cal_in_large_p/pp` | calibration-in-the-large（平均予測 − 実現率） |
| `improved` | p' が NLL/Brier で改善（採用シグナル主） |

**joint reliability**（FR-005）: 009 後の券種別（exacta/trifecta 等）winner NLL/Brier を before/after で別途
レポートし、**joint 非悪化**を採用条件に。

---

## 5. KellyCalibrationCompareReport（diagnostic、非永続）— US2/US3

| フィールド | 意味 |
|---|---|
| `mode` | `raw` / `cal` / `cal+haircut` |
| 6 指標 | 終端 bankroll・対数成長率・最大DD・破産確率・分散・最大連敗（016 の BankrollSegment 再利用） |
| `risk_not_worse` | 生 Kelly 比で最大DD・破産確率が非悪化か（必須ガード） |
| `over_conservative` | 過小賭け（成長を過度に削る）検出フラグ |
| `verdict` | success = 校正改善 かつ Kelly リスク非悪化 |

**2×2(p×q)**: raw/cal p × raw/cal q の EV・edge 分布・Kelly リスク。順序 = q 校正(013)→O_est→p 校正
P_model' 結合。p 校正は market 側に戻さない。

---

## 6. KellyConfig 拡張（016 の dataclass に追加）

| 追加パラメータ | 既定 | 意味 |
|---|---|---|
| `haircut_type` | `none` | `none` / `relative` / `absolute` |
| `haircut` | 0.0 | h（relative: (1−h)·edge、absolute: edge−h） |

p_calibrator は config ではなく opt-in 引数で渡す(013 が calibrator を関数引数にしたのと同形)。

---

## 7. 不変条件 / リーク境界

- p'・haircut・調整後 edge・Kelly fraction は features/training に出現しない（leak-guard test、SC-002）。
- 校正器は対象レース結果を読まない（walk-forward、race_before 厳密前）。
- p'(本) と q'(013) は別系統（p≠q）。p 校正結果を market odds 推定に戻さない。
- p' は 009 入力ベクトルと一致（レース内 Σ=1、engine-consistent clip）。
- 決定論（golden-section MLE は乱数なし、固定ビン）。
