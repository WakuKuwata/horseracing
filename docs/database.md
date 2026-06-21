# DB 仕様

## 参考元

初期 DB 設計は `/Users/kuwatawaku/workspace/aiuma` の既存 migration を参考にする。

参考ファイル:

- `/Users/kuwatawaku/workspace/aiuma/infra/db/migrations/versions/0001_core_schema.py`
- `/Users/kuwatawaku/workspace/aiuma/infra/db/migrations/versions/0002_race_identity_constraints.py`

元メモには `horces`、`race_horces` という表記があるが、`aiuma` の実テーブル名は `horses`、`race_horses` である。新規仕様では `horses`、`race_horses` を正式名とする。

## 初期テーブル

初期は以下のテーブルを中核として扱う。

- `races`
- `horses`
- `jockeys`
- `trainers`
- `race_horses`
- `race_results`

その他のテーブルは後続設計で決める。

## `races`

レース単位の基本情報を保持する。

主な項目:

- `race_id`: 12 桁のレース ID。主キー。
- `race_name`
- `race_name_short`
- `venue_code`
- `distance`
- `track_type`
- `race_status`
- `race_date`
- `race_number`
- `grade`
- `race_class`
- `weather`
- `going`
- `post_time`
- `created_at`
- `updated_at`

制約:

- `race_id` は `^[0-9]{12}$` を満たす。
- `race_number` は 1 から 12。

## `horses`

馬の基本情報と血統に関する参照情報を保持する。

主な項目:

- `horse_id`: 主キー。
- `horse_name`
- `sex`
- `birth_year`
- `sire_id`
- `dam_id`
- `damsire_id`
- `sire_name`
- `dam_name`
- `damsire_name`
- `data_source`
- `created_at`
- `updated_at`

## `jockeys`

騎手情報を保持する。

主な項目:

- `jockey_id`: 主キー。
- `jockey_name`
- `created_at`
- `updated_at`

## `trainers`

調教師情報を保持する。

主な項目:

- `trainer_id`: 主キー。
- `trainer_name`
- `created_at`
- `updated_at`

## `race_horses`

レース出走馬の情報を保持する。`race_id` と `horse_id` の組み合わせを主キーとする。

主な項目:

- `race_id`
- `horse_id`
- `sex`
- `age`
- `frame`
- `horse_number`
- `jockey_id`
- `trainer_id`
- `weight`
- `weight_diff`
- `odds`
- `popularity`
- `running_style`
- `jockey_weight`
- `cancelled`
- `created_at`
- `updated_at`

方針:

- `odds` は最新の単勝オッズとして扱う。
- オッズ更新時は最新値で上書きする。
- 取消・除外は `cancelled` で表現する。

## `race_results`

レース結果を保持する。`race_id` と `horse_id` の組み合わせを主キーとする。

主な項目:

- `race_id`
- `horse_id`
- `finish_order`
- `finish_time`
- `finish_time_diff`
- `corner_orders`
- `last_3f`
- `disqualified`
- `created_at`
- `updated_at`

方針:

- 学習ラベルは `race_results` を正として作成する。
- `1着率`、`2着以内率`、`3着以内率` の教師ラベルは `finish_order` から作る。
- 失格、取消、除外、同着の扱いは後続設計で明文化する。

## 後続で検討するテーブル候補

以下は必要性が高いが、まだ正式テーブルとして確定しない。

- `feature_snapshots`: 予測時点の特徴量を固定するテーブル。
- `model_versions`: 学習済みモデルと校正器のバージョン管理。
- `prediction_runs`: 予測実行単位。
- `race_predictions`: 予測実行ごとの馬別確率。
- `recommendations`: 買い目候補と疑似ROI。
- `scraping_jobs` または `ingestion_jobs`: 取込ジョブの状態管理。
- `id_mappings`: JRA-VAN と netkeiba の ID 対応。

ただし、オッズスナップショット保存用テーブルは初期方針では作らない。

予測・推奨結果を保存する場合は、最新オッズとは別に、計算時に使用した市場オッズ、推定市場オッズ、疑似オッズ、疑似ROI、計算時刻、ロジックバージョンを結果側に保持する。これはオッズ履歴を保存するためではなく、提示済みの判断結果を後から確認するための最小監査情報とする。
