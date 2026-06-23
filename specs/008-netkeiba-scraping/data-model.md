# Data Model: netkeiba スクレイピング取り込み

新テーブルは作らない。既存 core / id_mappings / ingestion_jobs に書く。

## パース結果(中間 dataclass、非永続)

- **ScrapedRace**: netkeiba race キー(年/開催場/回/日/レース番号)+ 日付/距離/馬場/天候/クラス等。
- **ScrapedEntry**: race + 出走馬(netkeiba horse_id/名・枠・馬番・netkeiba jockey/trainer id・斤量・性齢・entry_status)。
- **ScrapedOdds**: race + 馬(netkeiba horse_id・単勝オッズ・人気)。
- **ScrapedResult**: race + 馬(netkeiba horse_id・着順・結果状態・タイム等)。

## ID 解決(idmap)

```
resolve(session, entity_type, netkeiba_id) -> canonical_id | surrogate
  row = id_mappings WHERE entity_type, source='netkeiba', source_id=netkeiba_id
  if row and row.mapping_status='mapped' and row.canonical_id: return row.canonical_id
  else:
    upsert id_mappings(entity_type, 'netkeiba', netkeiba_id, canonical_id=NULL, status='unmapped')  # キュー
    return f"nk:{netkeiba_id}"   # 名前空間付き代替 ID(一意・衝突なし)
```

`horses/jockeys/trainers` には canonical_id か `nk:{id}` を PK として upsert。代替 ID 行は `horses.data_source='netkeiba'`
を記録(jockeys/trainers は data_source 列が無いため記録しない)。代替 ID は JRA-VAN 数値 ID/12 桁形式と一致しない。
推測結合しない。マッピングは別運用で `mapping_status='mapped'`+canonical_id を付与(本フィーチャーはキューまで)。

## race_id 構成(venues)

```
build_race_id(scraped_race) -> str | None
  vv = NETKEIBA_TO_JRAVAN_VENUE[track_code]     # 不明なら None
  race_id = f"{year:04d}{vv}{kai:02d}{nichime:02d}{race_no:02d}"
  return race_id if is_valid_race_id(race_id) else None
```

None のとき races/race_horses に行を作らない(FR-005、偽 ID 禁止)。2007 未満も対象外。

## 取り込み不変条件

- **INV-N1**: netkeiba ID は id_mappings 経由でのみ canonical へ。未対応は `nk:{id}` 代替 + UNMAPPED キュー。推測結合禁止。
- **INV-N2**: 代替 ID は netkeiba ID ごとに一意(同一 Unknown 使い回し禁止)。未マッピング馬は history で debut/Unknown。
- **INV-N3**: race_id は `is_valid_race_id` を通る 12 桁のみ。構成不能なら行を作らない。
- **INV-N4**: 結果は **insert-only**(`ON CONFLICT (race_id,horse_id) DO NOTHING`)。既存 JRA-VAN 行を更新しない。非出走に
  race_results を作らない。同着は finish_order 共有。
- **INV-N5**: 前売りオッズは **race_results 不在(結果未確定)レースのみ** race_horses.odds を最新値で上書き
  (updated_at のみ、スナップショット履歴なし)。欠損/不正は更新しない。
- **INV-N6**: 取り込みは idempotent(再実行で重複・破壊なし)。各実行は ingestion_jobs に監査。
- **INV-N7**: 必須要素欠損は fail-close(誤データを作らない)+ errors 記録。
- **INV-N8**: オッズ/人気はモデル特徴に使わない(betting のみ、005/006/007 で担保)。

## 書き込み規則(既存スキーマ)

| 取り込み | 先 | 規則 |
|---|---|---|
| entries | races / race_horses / horses / jockeys / trainers | PK upsert(べき等)。entry_status=started(取消・除外は反映) |
| entries | id_mappings | 未マッピング netkeiba ID を UNMAPPED で upsert |
| odds | race_horses.odds | 結果未確定レースのみ最新値上書き + updated_at |
| results | race_results | insert-only(DO NOTHING)。非出走に行を作らない |
| 全取り込み | ingestion_jobs | source='netkeiba'、job_type、scope、counts、status、parser_version、時刻 |

## enum 対応

- entry_status: started / cancelled(取消)/ excluded(競走除外)。
- result_status: finished / stopped(競走中止)/ disqualified(失格)。netkeiba 表記をこの 3 値に対応付ける(新値なし)。
- 同着: finish_order を共有(明示フラグなし)。

## スコープ外(将来)

- id_mappings の自動解決(名寄せ)。本フィーチャーは未マッピングを安全に積むまで。
- 複勝/馬連/三連複オッズ、推定オッズ変換、地方/海外、JS 動的オッズの高度取得。
