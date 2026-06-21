# Data Model: Core DB スキーマと基盤テーブル契約

PostgreSQL 16 / SQLAlchemy 2.0 / Alembic。全テーブル共通で `created_at` (INSERT 時 `now()`)、
`updated_at` (`BEFORE UPDATE` トリガで `now()` 自動更新) を持つ。型は Postgres 型で記す。

論理ラベル名は `1着率` / `2着以内率` / `3着以内率`。物理列名は英語 (`win_prob` / `top2_prob` /
`top3_prob`) を用いる (憲法 I: 英語は実装識別子としてのみ)。

## 状態コード体系 (enums.py で定数化、CHECK で強制)

| 区分 | 列 | 許容値 | 意味 |
|---|---|---|---|
| 出走状態 | `race_horses.entry_status` | `started` / `cancelled` / `excluded` | 出走 / 出走取消 / 競走除外 |
| 完走状態 | `race_results.result_status` | `finished` / `stopped` / `disqualified` | 完走 / 競走中止 / 失格 |
| ID 対応状態 | `id_mappings.mapping_status` | `unmapped` / `mapped` / `conflict` / `rejected` | 未対応 / 対応済 / 衝突 / 却下 |
| エンティティ種別 | `id_mappings.entity_type` | `horse` / `jockey` / `trainer` | 対応対象の種別 |
| データソース | `source` 系 | `jra_van` / `netkeiba` | 取込元 |
| ジョブ状態 | `ingestion_jobs.status` | `queued` / `running` / `succeeded` / `failed` / `partial` | 取込ジョブ状態 |
| モデル採用状態 | `model_versions.adoption_status` | `candidate` / `active` / `retired` | 候補 / 採用中 / 退役 |
| 券種 | `recommendations.bet_type` | `win` / `place` / `quinella` / `exacta` / `wide` / `trio` / `trifecta` | 単勝/複勝/馬連/馬単/ワイド/3連複/3連単 |

## コアテーブル (US1 / US2)

### races
レース単位の基本情報。時系列分割の基準。

| 列 | 型 | 制約 | 備考 |
|---|---|---|---|
| `race_id` | text | PK, CHECK `~ '^[0-9]{12}$'` | 12桁 `YYYYVVKKDDRR` |
| `race_name` / `race_name_short` | text | | |
| `venue_code` | text | | 場所コード |
| `distance` | integer | | |
| `track_type` | text | | 芝/ダート等 |
| `race_status` | text | | 確定/未確定など (取込が設定) |
| `race_date` | date | INDEX (`race_date`, `post_time`) | walk-forward 分割の基準 |
| `race_number` | integer | CHECK `>= 1 AND <= 12` | |
| `grade` / `race_class` | text | | |
| `weather` / `going` | text | | |
| `post_time` | timestamptz | | 発走時刻 |

- **制約名**: `ck_races_race_id_format`, `ck_races_race_number_range`, `ix_races_race_date_post_time`
  (aiuma 0002 踏襲)。
- 2007 境界は **ここに CHECK を置かない** (R8)。

### horses
馬の基本情報と血統参照。血統参照は欠損可 (海外馬・未知血統対応)。

| 列 | 型 | 制約 | 備考 |
|---|---|---|---|
| `horse_id` | text | PK | |
| `horse_name` | text | | |
| `sex` | text | | |
| `birth_year` | integer | | |
| `sire_id` / `dam_id` / `damsire_id` | text | nullable | 血統参照 (欠損可) |
| `sire_name` / `dam_name` / `damsire_name` | text | nullable | |
| `data_source` | text | | 由来ソース |

### jockeys / trainers
| 列 | 型 | 制約 |
|---|---|---|
| `jockey_id` / `trainer_id` | text | PK |
| `jockey_name` / `trainer_name` | text | |

### race_horses
レース出走馬。発走前情報を保持。`(race_id, horse_id)` 複合 PK。

| 列 | 型 | 制約 | 備考 |
|---|---|---|---|
| `race_id` | text | PK, FK→races | |
| `horse_id` | text | PK, FK→horses | |
| `sex` | text | | |
| `age` | integer | | |
| `frame` | integer | | 枠番 |
| `horse_number` | integer | | 馬番 |
| `jockey_id` | text | FK→jockeys, nullable | |
| `trainer_id` | text | FK→trainers, nullable | |
| `weight` | integer | nullable | 馬体重 (未発表時 null) |
| `weight_diff` | integer | nullable | 増減 (未発表時 null) |
| `odds` | numeric | nullable | **最新単勝オッズのみ。履歴保存しない** (上書き) |
| `popularity` | integer | nullable | |
| `running_style` | text | nullable | 脚質 |
| `jockey_weight` | numeric | nullable | 斤量 |
| `entry_status` | text | NOT NULL, default `started`, CHECK | `started`/`cancelled`/`excluded` |

- **制約名**: `ck_race_horses_entry_status`。
- 欠損 (Unknown) は `null`。`0` を代入しない (FR-010)。

### race_results
レース結果。学習ラベルの正。`(race_id, horse_id)` 複合 PK。

| 列 | 型 | 制約 | 備考 |
|---|---|---|---|
| `race_id` | text | PK, FK→races | |
| `horse_id` | text | PK, FK→horses | |
| `finish_order` | integer | nullable | 完走時のみ。同着は同値を共有 |
| `finish_time` | interval | nullable | |
| `finish_time_diff` | interval | nullable | |
| `corner_orders` | text[] | nullable | 通過順 |
| `last_3f` | numeric | nullable | 上がり3F |
| `result_status` | text | NOT NULL, default `finished`, CHECK | `finished`/`stopped`/`disqualified` |

- **制約名**: `ck_race_results_result_status`, `ck_race_results_finish_order_when_finished`。

## 不変条件 (テストで検証、可能なものは DB 制約)

- **INV-1**: `entry_status ∈ {cancelled, excluded}` の `race_horses` 行は `race_results` 行を持たない
  (非出走)。→ アプリ + 検証クエリで担保 (クロステーブル制約は DB で表現しづらいため)。
- **INV-2**: `result_status = finished` の行は `finish_order` が NOT NULL。`stopped`/`disqualified` は
  `finish_order` を NULL 可。→ DB CHECK `ck_race_results_finish_order_when_finished`:
  `result_status <> 'finished' OR finish_order IS NOT NULL`。
- **INV-3**: ラベル導出は `result_status = 'finished'` のみを完走前提集計に含める。`cancelled`/
  `excluded` (非出走) と `stopped`/`disqualified` (非完走) を除外。疑似着順に変換しない。
- **INV-4**: `odds` の更新で履歴行は増えない (同一 PK の上書き)。
- **INV-5**: 学習ラベル (標準ケース): あるレースの `finished` 馬について
  `win = (finish_order == 1)`, `top2 = (finish_order <= 2)`, `top3 = (finish_order <= 3)`。
  同着・少頭数の許容誤差は評価 feature で定義。

## ID 対応・取込監査 (US3)

### id_mappings
異種ソース ID ↔ 正規 ID。未対応・衝突を表現。

| 列 | 型 | 制約 | 備考 |
|---|---|---|---|
| `id_mapping_id` | uuid | PK | |
| `entity_type` | text | NOT NULL, CHECK `∈ {horse,jockey,trainer}` | |
| `source` | text | NOT NULL, CHECK `∈ {jra_van,netkeiba}` | |
| `source_id` | text | NOT NULL | ソース側 ID |
| `canonical_id` | text | nullable | 正規 ID (未対応時 null) |
| `mapping_status` | text | NOT NULL, default `unmapped`, CHECK | `unmapped`/`mapped`/`conflict`/`rejected` |
| `conflict_group_id` | uuid | nullable | 衝突グループの束ね |
| `resolved_at` | timestamptz | nullable | 手動解決時刻 |
| `resolution_note` | text | nullable | |

- **一意制約**: `uq_id_mappings_entity_source_sourceid` = UNIQUE(`entity_type`,`source`,`source_id`)。
- 衝突 (同一 source_id が複数 canonical を指す候補) は `mapping_status='conflict'` +
  `conflict_group_id` で表現し、黙って上書きしない (FR-015)。
- **制約名**: `ck_id_mappings_entity_type`, `ck_id_mappings_source`, `ck_id_mappings_status`。

### ingestion_jobs
取込ジョブ状態 (aiuma 0001 踏襲 + 拡張)。

| 列 | 型 | 制約 | 備考 |
|---|---|---|---|
| `ingestion_job_id` | uuid | PK | |
| `source` | text | CHECK `∈ {jra_van,netkeiba}` | |
| `job_type` | text | | 種別 (race_list/shutuba/result/odds など) |
| `scope` / `scope_value` | text | | 対象範囲 (date/race_id 等) |
| `status` | text | NOT NULL, default `queued`, CHECK | `queued`/`running`/`succeeded`/`failed`/`partial` |
| `trace_id` | text | | |
| `retry_count` / `max_retry` | integer | default 0 / 5 | |
| `checkpoint` | text | | 再開ポイント |
| `started_at` / `completed_at` | timestamptz | | |
| `error_message` | text | | 失敗理由 |

- **制約名**: `ck_ingestion_jobs_status`, `ck_ingestion_jobs_source`。

## 予測・推奨の最小契約 (US4)

### model_versions
学習済みモデル + 校正器のバージョン管理。

| 列 | 型 | 制約 | 備考 |
|---|---|---|---|
| `model_version` | text | PK | 例: `lightgbm-2026-06-21-001` |
| `model_family` | text | | 例: `lightgbm` |
| `feature_version` | text | | 特徴量定義版 |
| `label_schema` | text | default `win_top2_top3` | ラベル体系 |
| `adoption_status` | text | NOT NULL, default `candidate`, CHECK | `candidate`/`active`/`retired` |
| `metrics_summary` | jsonb | nullable | 評価サマリ (LogLoss/Brier/AUC/ECE 等) |
| `weights_uri` / `calibrator_uri` | text | nullable | 成果物参照 |
| `registered_at` | timestamptz | default now() | |

- **制約名**: `ck_model_versions_adoption_status`。
- 採用判定ロジック (active は 1 つ等) は学習/採用 feature の責務。本 feature は契約のみ。

### prediction_runs
予測実行単位。

| 列 | 型 | 制約 | 備考 |
|---|---|---|---|
| `prediction_run_id` | uuid | PK | |
| `race_id` | text | FK→races, NOT NULL | |
| `model_version` | text | FK→model_versions, NOT NULL | |
| `logic_version` | text | NOT NULL | 予測ロジック版 |
| `computed_at` | timestamptz | NOT NULL, default now() | 計算時刻 |

- **索引**: `ix_prediction_runs_race_id`。

### race_predictions
予測実行ごとの馬別確率。

| 列 | 型 | 制約 | 備考 |
|---|---|---|---|
| `prediction_run_id` | uuid | PK, FK→prediction_runs | |
| `horse_id` | text | PK, FK→horses | |
| `win_prob` | numeric | (下記の行 CHECK で制約) | 1着率 |
| `top2_prob` | numeric | (下記の行 CHECK で制約) | 2着以内率 |
| `top3_prob` | numeric | (下記の行 CHECK で制約) | 3着以内率 |

- **行 CHECK** (単一) `ck_race_predictions_prob_monotonic`:
  `0 <= win_prob AND win_prob <= top2_prob AND top2_prob <= top3_prob AND top3_prob <= 1` (憲法 IV)。
  個別列の CHECK は設けず、この 1 つの行 CHECK のみで範囲と単調性を担保する。
- レース内合計 (Σ≈1/2/3) は行制約にできず、検証クエリ / 下流の正規化責務 (quickstart 参照)。

### feature_snapshots
予測時点の特徴量固定 (再現性)。

| 列 | 型 | 制約 | 備考 |
|---|---|---|---|
| `prediction_run_id` | uuid | PK, FK→prediction_runs | |
| `horse_id` | text | PK | |
| `feature_version` | text | NOT NULL | |
| `features` | jsonb | NOT NULL | 予測時の特徴量値 (Unknown は明示) |

### recommendations
買い目候補と疑似ROI。提示時点の監査情報を保持 (憲法 V)。

| 列 | 型 | 制約 | 備考 |
|---|---|---|---|
| `recommendation_id` | uuid | PK | |
| `prediction_run_id` | uuid | FK→prediction_runs, NOT NULL | |
| `race_id` | text | FK→races, NOT NULL | |
| `bet_type` | text | NOT NULL, CHECK | 券種 (7 値: win/place/quinella/exacta/wide/trio/trifecta) |
| `selection` | jsonb | NOT NULL | 買い目 (馬番組合せ等) |
| `market_odds_used` | numeric | nullable | 計算時の実市場オッズ |
| `estimated_market_odds_used` | numeric | nullable | 計算時の推定市場オッズ |
| `is_estimated_odds` | boolean | NOT NULL, default false | 疑似評価フラグ (実 vs 推定の区別, FR-022) |
| `pseudo_odds` | numeric | nullable | 疑似オッズ |
| `pseudo_roi` | numeric | nullable | 疑似ROI |
| `logic_version` | text | NOT NULL | 買い目ロジック版 |
| `computed_at` | timestamptz | NOT NULL, default now() | |

- **制約名**: `ck_recommendations_bet_type`。
- 券種別結合確率の詳細列・推定オッズ変換規則は **P0 未決のため本 feature 範囲外**。上記は非破壊
  拡張可能な最小契約 (FR-023)。`selection` を jsonb にして券種ごとの構造差を吸収する。

## 再利用バリデータ (validation.py)

DB 制約に置かないが下流が共有するロジック。

- `is_valid_race_id(race_id: str) -> bool`: `^[0-9]{12}$`。
- `is_in_ingest_scope(race_date: date) -> bool`: `race_date >= date(2007, 1, 1)` (2007 境界, R8/FR-024)。

## マイグレーション (ストーリー単位で3リビジョン)

ストーリー独立性のため 3 リビジョンに分割する (aiuma の 0001/0002/0003 と同様):

- `0001_core_schema` (US1/US2): races/horses/jockeys/trainers → race_horses/race_results。
  CHECK・`race_date` 索引・`updated_at` トリガ関数 (`set_updated_at()`) と各テーブルへの
  `BEFORE UPDATE` トリガ。
- `0002_ingestion_id_schema` (US3): id_mappings, ingestion_jobs。
- `0003_prediction_contract` (US4): model_versions → prediction_runs →
  race_predictions/feature_snapshots/recommendations。

各リビジョン内のテーブル順は FK 依存順。`downgrade()` は逆順に drop (トリガ・関数含む)。
全リビジョン通しで冪等な apply/rollback を検証 (SC-005)。
