# Data Model: 低履歴×血統適性 交互作用 (032)

スキーマ変更なし(DB migration head 0006 不変・新テーブルなし)。features の特徴量列を 5 追加。DB read は 026 と同一(新規読取列なし)。

## 入力（既存・再利用、新規読取なし）

`build_debut_pedigree_features(frames, *, history=None, pedigree=None)`:

| 入力 | 由来 | 用途 |
|---|---|---|
| frames（races/race_horses/race_results/horses） | loader | デビュー戦特定 + 026 `_other_offspring` 集計 |
| history（is_debut/is_low_history） | build_history_features（020/history group） | ゲーティングの左辺 |
| pedigree（sire_win_rate/sire_dist_band_win_rate） | build_pedigree_features（026） | ゲーティングの右辺 |

`sire_name`（026 集計キー、~100% populate）と `entry_status`（STARTED 判定）を使用。**生の今走 result/odds は読まない**。

## 出力（debut_pedigree group, 全て float64, missing=NULL）

per (race_id, horse_id):

| 列 | 定義 | NaN 条件 |
|---|---|---|
| `sire_debut_win_rate` | 同種牡馬の他産駒のデビュー戦(各馬初出走)の strictly-before 勝率(自馬除外・同日除外) | 種牡馬不明 / 他産駒デビュー戦の母数 < min_starts |
| `debut_x_sire_win_rate` | is_debut × sire_win_rate | is_debut or sire_win_rate が NaN |
| `debut_x_sire_dist_band_win_rate` | is_debut × sire_dist_band_win_rate | 同上 |
| `lowhist_x_sire_win_rate` | is_low_history × sire_win_rate | is_low_history or sire_win_rate が NaN |
| `lowhist_x_sire_dist_band_win_rate` | is_low_history × sire_dist_band_win_rate | 同上 |

- **デビュー戦**: 各 horse の race_date 最小の STARTED 出走 1 回。
- **sire_debut_win_rate の算術**: 026 `_other_offspring`(debut-runs サブセット, key=sire_name)で o_wins(デビュー戦勝利数)・o_cnt(デビュー戦数)を自馬除外で得る → `o_cnt>=min_starts ? o_wins/o_cnt : NaN`。min_starts は 026 と同既定(=10)を流用(plan/実装で確定)。
- **ゲーティング**: is_debut/is_low_history は history group で {0.0,1.0} 相当(ZERO_OK)。sire_* が NaN なら積も NaN(numpy 既定、0埋め禁止)。

## registry 登録

- 5 列を REGISTRY に source=`pedigree`(or derived)・timing=`PRE_ENTRY`・missing=`NULL` で追加。
- FEATURE_GROUPS: 5 列すべて group=`debut_pedigree`。
- FEATURE_VERSION: `features-009` → `features-010`。
- STATIC_COLUMNS には追加しない(as-of/derived ⇒ materialized_columns 自動収録)。
- ALL_COLUMNS は registry から自動導出。

## materialization（025 連携）

- `build_asof_features` に debut_pedigree ブロック追加(history/pedigree を渡し二重計算回避)。in-memory builder・serving fallback と同一関数。
- 新ソース列なし(sire_name は 026 で既にロード&fingerprint 包含)⇒ `source_fingerprint` 無改修。
- bit パリティ: materialize==in-memory `build_feature_matrix` が `assert_frame_equal(check_exact=True, check_dtype=True)`。
- serving 未来レース(parquet 非カバー): 単一レース fallback が build_debut_pedigree_features を当該レースだけ実行。

## リーク属性（憲法 II 必須記載）

- **source**: 同種牡馬他産駒の過去(strictly-before)デビュー戦由来の条件付き集約 + 既存 as-of 列の積(派生)。
- **利用可能タイミング**: PRE_ENTRY(血統・出走歴・種牡馬適性はいずれも予測時点既知)。
- **欠損処理**: NULL 伝播(0埋め禁止)。
- **非特徴**: 今走 result/finish_order/odds/人気。
