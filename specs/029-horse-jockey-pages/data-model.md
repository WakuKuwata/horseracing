# Phase 1 Data Model: 馬・騎手プロフィールページ

**スキーマ変更なし。** 既存テーブルを read 集約する表示用の導出（永続化しない）。本書は集約の定義と表示エンティティの形を定める。

## 入力（既存テーブル・read のみ）

- **horses**: `horse_id`(PK), `horse_name`, `sex`, `birth_year`, `sire_name`/`sire_id`, `dam_name`/`dam_id`, `damsire_name`/`damsire_id`, `data_source`。
- **jockeys**: `jockey_id`(PK), `jockey_name`。
- **race_horses**: PK `(race_id, horse_id)`, `jockey_id`, `trainer_id`, `horse_number`, `popularity`, `odds`, `entry_status`, `running_style`。
- **race_results**: PK `(race_id, horse_id)`, `finish_order`, `finish_time`, `finish_time_diff`, `last_3f`, `result_status`。
- **races**: `race_id`, `race_date`, `venue_code`, `race_number`, `race_name`, `race_class`, `distance`, `track_type`。

## 導出エンティティ: 馬プロフィール（HorseProfile）

| フィールド | 由来 |
|------------|------|
| horse_id / horse_name / sex / birth_year / data_source | horses |
| sire_name / dam_name / damsire_name | horses（**名前**。ID は ~0% のため表示は名前） |
| starts（出走数） | count(race_horses where entry_status='started') |
| wins / seconds_in（連対=2着以内） / shows_in（複勝=3着以内） | count(race_results finished & finish_order ∈ {1} / {1,2} / {1,2,3}) |
| win_rate / quinella_rate / show_rate | wins/starts, seconds_in/starts, shows_in/starts（starts=0 は null＝Unknown と 0 を区別） |
| avg_finish（平均着順） | avg(finish_order) over finished のみ（null は除外） |

## 導出エンティティ: 馬レース履歴行（HorseHistoryRow、paged）

| フィールド | 由来 |
|------------|------|
| race_id / race_date / venue_code / race_number / race_name / race_class / distance / track_type | races |
| horse_number / popularity / odds / entry_status | race_horses |
| finish_order / finish_time_sec（finish_time の Interval→秒換算） / last_3f / result_status | race_results |

- 並び: `race_date DESC`（安定順、tie-break `race_id DESC`）。ページング `Page[HorseHistoryRow]`。

## 導出エンティティ: 騎手プロフィール（JockeyProfile）

| フィールド | 由来 |
|------------|------|
| jockey_id / jockey_name | jockeys |
| mounts（騎乗数） | count(race_horses where jockey_id=? and entry_status='started') |
| wins / seconds_in / shows_in | count(対応する race_results finished & finish_order ∈ {1}/{1,2}/{1,2,3}) |
| win_rate / quinella_rate / show_rate | wins/mounts 等（mounts=0 は null） |
| avg_finish | avg(finish_order) over finished |

## 導出エンティティ: 騎手騎乗履歴行（JockeyHistoryRow、paged）

| フィールド | 由来 |
|------------|------|
| race_id / race_date / venue_code / race_number / race_name | races |
| horse_id / horse_name | race_horses + horses（騎乗馬） |
| finish_order / result_status | race_results |

- 並び: `race_date DESC`。ページング `Page[JockeyHistoryRow]`。

## 集計規則（母数・区分、研究 D2）

| 指標 | 母数 | 対象/除外 |
|------|------|-----------|
| 出走数/騎乗数 | — | entry_status='started' のみ。取消・除外は含めない |
| 勝率/連対率/複勝率 | 出走数(started) | finished かつ finish_order 非 null を分子。中止・失格は分子に入らないが母数には含む |
| 平均着順 | finished のみ | finish_order 非 null の平均（取消・中止は除外） |

- starts/mounts=0 のとき率は **null（Unknown）**として返し、0 と区別（FR-014）。

## 契約の additive 変更（既存 HorseEntry）

- `HorseEntry` に `jockey_id: str | None` と `trainer_id: str | None` を追加（列は race_horses に既存）。front の出走表リンク用。

## バリデーション/エラー（要件→実装）

| 規則 | 由来 | 実装 |
|------|------|------|
| 未存在 horse/jockey | FR-016 | session.get → None で typed 404（馬/騎手 ID は固定フォーマットなし→形式 422 は設けない） |
| surrogate/欠損はリンク不可 | FR-009 | front: `nk:` プレフィックス/ null は非リンク |
| 実績ゼロ | FR-014 | starts=0 → 率 null・履歴空（200 empty） |
| 表示値を特徴量にしない | FR-013 | api は features/training を import しない（leak-guard）、read 専用 |
