# Research: 特徴量生成

Phase 0 調査。リーク安全の機構と特徴量定義を確定する。最重要は as-of 機構 (R1)。

## R1. as-of 機構 (`race_date < R`、同日除外) — リーク安全の核

- **Decision**: 過去成績特徴量は、対象レース R (`race_date = D`) より**厳密に前の日**の履歴のみで計算
  する。pandas でのベクトル化手順:
  1. 馬の「出走履歴」フレームを構築: 1 行 = (horse_id, race_id, race_date, entry_status, result_status,
     finish_order, last_3f)。
  2. `(horse_id, race_date)` 単位で当日寄与を集約 (同日複数出走を 1 日に畳む)。
  3. `(horse_id, race_date)` で安定ソート → 馬ごとに cumulative (cumcount/cumsum) → **distinct date で
     1 つシフト**し、date D の行が「D より厳密に前」の累積のみを参照するようにする。
  4. ターゲット race-horse に `(horse_id, race_date)` で結合。
- **Rationale**: 日単位集約 + distinct-date シフトにより、同日レース (同日前半→後半) の結果混入を構造的
  に防ぐ。fold 非依存で安全 (R は自分の date より前しか見ない)。codex も `race_date < R` を妥当と評価。
- **Alternatives considered**: 行単位 shift → 同日複数出走で漏れる。per-race の date<D サブクエリ →
  ~100 万回クエリで非現実的。`merge_asof(allow_exact_matches=False)` は prev_* に有効だが累積には日集約が
  必要。
- **検証**: SC-001 のリーク検査 (合成データで R の特徴が `race_date >= R` を 1 件も使わない) で担保。

## R2. MVP 特徴量セット (定義)

完走前提系は `result_status='finished'` のみ、start 系は `entry_status='started'` を母集団とする。

| 特徴量 | 定義 (as-of D 未満) | 欠損時 |
|---|---|---|
| `career_starts` | 過去の started 回数 | 0 (新馬は 0、これは件数なので 0 でよい) |
| `days_since_last` | D − 直近 started の race_date (日数) | null (出走歴なし) |
| `prev_finish` | 直近 finished の finish_order | null |
| `prev_last3f` | 直近 finished の last_3f | null |
| `avg_finish` | finished の finish_order 平均 | null |
| `win_rate` | finished 中 finish_order==1 の割合 | null |

履歴件数 (非完走系、別系統・0 埋め可: 件数の 0 は意味を持つ):

| 特徴量 | 定義 |
|---|---|
| `cancel_count` / `exclude_count` / `stop_count` | 過去の取消/除外/中止回数 |
| `prev_was_cancel` / `prev_was_exclude` / `prev_was_stop` | 前走 (entry/result 上) が取消/除外/中止か |

- **Rationale**: codex 推奨の少数セット。完走前提 (avg_finish 等) と非完走 (件数) を分離し、後者は件数の
  0 が意味を持つため 0 可。前者は出走歴ゼロで null (Unknown)。
- **Alternatives considered**: 大量特徴量 → MVP では検証コスト増。距離別・馬場別実績は P2/後続。

## R3. 完走/非出走/非完走の扱い

- **Decision**: start = `entry_status='started'`。完走 = `result_status='finished'`。
  - 取消・除外 (DNS): start に含めない (career_starts に入れない)。`cancel_count`/`exclude_count` で別保持。
  - 競走中止・失格 (DNF): start に含める (career_starts に入れる) が完走前提系 (avg_finish/last3f/win_rate)
    から除外。`stop_count` で別保持。
  - `days_since_last` は started 基準 (DNF も「出走した」ので含む。取消明けは含めない)。
- **Rationale**: docs/modeling.md と憲法 II・codex に一致。0 埋めで意味を壊さない。

## R4. 欠損・固定スキーマ・フラグ

- **Decision**: 固定列。過去成績系 (prev_*, avg_*, win_rate, days_since_last) は履歴ゼロで `null`
  (Unknown)。フラグ: `has_past_race`=(career_starts>0)、`is_debut`=(career_starts==0)、
  `past_race_count`=career_starts、`is_low_history`=(1<=career_starts<=低履歴上限, 既定 2)。
- **Rationale**: 憲法 IV (Unknown≠0)。LightGBM が欠損を扱える前提でも missing_policy を明記。
- **Alternatives considered**: 0 埋め → 重大な意味歪み (codex 最重要リスク③)。

## R5. FeatureRegistry とメタデータ強制

- **Decision**: 各特徴量に `(source, availability_timing, missing_policy)` を宣言する registry。
  availability_timing の正規値: `pre_entry`(出馬表前)/`post_frame`(枠順後)/`post_weight`(馬体重後)/
  `post_odds`(オッズ後)/`pre_race`(直前)/`post_result`(結果後)。
  - `build_feature_matrix` は全列が registry に登録済みかを検証し、未登録列があれば fail-fast。
  - `model_input_features()` は `post_result` を機械的に除外する。
  - 結果確定 `odds`/`popularity` は **registry に「モデル特徴量」として登録しない**。feature matrix に
    混入したら未登録列として検出される。
- **Rationale**: 憲法 II の「全特徴量に source/timing/missing を必須記載」を機構で強制 (codex)。
- **Alternatives considered**: 規約のみ (強制なし) → 宣言倒れ。

## R6. pandas リーク検査パターン

- **Decision**: cutoff は機構 (日集約 + distinct-date シフト) で固定。pandas の sort は安定ソート
  (`kind='stable'`)、groupby は `sort=False` で順序保持しつつキーで結合。リーク検査テストで「R の特徴が
  未来/同日を使わない」を合成データで明示確認。
- **Rationale**: pandas の shift/groupby はオフバイワンで漏れやすい (codex)。機構 + テストで二重化。

## R7. 保存 (on-the-fly, スキーマ変更なし)

- **Decision**: MVP は `build_feature_matrix(session, ...)` が DataFrame (FeatureMatrix) を返す
  on-the-fly。スキーマ変更なし。`feature_snapshots` は予測時点の監査用であり feature store の代替では
  ない (混同しない)。materialize (US4) は P2 で非破壊拡張。
- **Rationale**: 正しさに materialize は不要。最適化は後 (codex)。

## R8. target encoding (US3, P2)

- **Decision**: 騎手/調教師/開催場などの target encoding (勝率等) は train 境界 (日付) より前のみで fit。
  未知カテゴリは全体平均等の既定値 (0 埋め/エラーにしない)。out-of-fold も選択肢。
- **Rationale**: 目的変数集約は fold 漏れの最大経路 (codex リスク②)。train-only を契約化。
- **Alternatives considered**: 全期間で encoding → 重大リーク。

## R9. 決定論

- **Decision**: 乱数なし。安定ソート、(race_id, horse_id) で確定順序。同一入力・同一 as-of で完全一致。
- **Rationale**: 再現性は学習・採用判定の前提 (憲法 V, SC-005)。
