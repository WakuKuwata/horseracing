# Contract: 段 1 parity 報告(READ ONLY)

`scrape jrdb-parity --year YYYY --files-dir ABS [--db-url …]` が `artifacts/jrdb/parity/<year>.json` を書く。製品表には書かない。

## 入力
その年の BAC/KYI(または KYG/KYH)/UKC/SED(または SEC)/SRB/HJC/KZA/CZA の展開済みテキスト。DB は読み取り専用。

## 出力(JSON)
```
{ "year": 2008, "files": {kind: {"count": n, "sha256": [...]}},
  "counts": { "races": {"jrdb": n, "db": n, "both": n, "jrdb_only": [...race_id], "db_only": [...]},
              "entries": {...}, "results": {...}, "payout_rows": {...} },
  "ids": { "horse": {"joined": n, "unjoined": [...]}, "jockey_codes": {"mapped": n, "unmapped": n, "conflict": n}, "trainer_codes": {...} },
  "columns": { "<db_column>": { "meaning": "same|normalized|different|jrdb_only", "note": "...",
                                 "denominator": {"both_present": n, "jrdb_only": n, "db_only": n, "unjoined": n},
                                 "by_segment": { "<距離帯>/<芝ダ障>": {"n": n, "agree": n, "rate": 0.99, "tolerance": 0.1} },
                                 "examples_disagree": [ {race_id, horse_id, jrdb, db} ... up to 10 ] } } }
```
`meaning` は人が書く(D1 の表の転記。コードが推定しない)。`rate` の分母は both_present だけ。

加えて **`uncovered_reference_cells`**(codex D15 #6): 対象期間・対象列(JRA-VAN 由来 48 列から別定義 4 列を除いた 44 列)を固定し、
DB 側の各**非欠損セル**について、(i) 正規 ID で一意に対応する JRDB 行がある、(ii) JRDB の値が非欠損(欠損コードを 0 等に置換していない)、
(iii) 定義が同じか検証済み正規化で同義、を全件検査する。未充足セルを `{row_missing, id_unresolved, value_missing, definition_mismatch}` に
分けて列ごとに数え、合計 0 が「その期間・その 44 列で不足なし」の必要条件。別定義 4 列は `definition_mismatch` として別掲し、0 を要求しない
(JRDB 定義で置き換える列)。騎手・調教師の `id_unresolved` もここに数える。

## 判定(列ごと・事前固定)
| 列 | 許容差 | 基準 | 既知 |
|---|---|---|---|
| finish_order / finish_time / horse_number / frame / horse_name / sex | 完全一致 | 99.9% | サンプル 100% |
| odds(確定)・popularity | 完全一致 | 99.9% | サンプル 100% |
| last_3f | ≤0.1 秒 | 99% | 95.7%(DB 側異常値) |
| first_3f | ≤0.1 秒 | 各区分 99% | 1200m のみ 100%・他 <10% → **不合格(別定義)** |
| weight / weight_diff | 完全一致(空=0 を同値) | 99.9% | 100% |
| jockey_weight | ≤0.1kg | 99.9% | 見習い 5 行差 → 要確認 |
| race_class / grade / going / weather / track_type / distance | 正規化後一致 | 99.5% | 対応表どおり |
| owner_name / breeder_name / sire,dam,damsire name | 正規化(NFKC・空白除去・末尾「氏」除去)後一致 | 99.5% | — |
| prize_money(1 着) / post_time | 完全一致 / DB NULL は分母外 | 99.9% | — |
| corner_orders / running_style / sire_line | — | **比較だけ・基準なし(別定義)** | — |
| win payouts(HJC) | 完全一致 | 99.9% | 24/24 |

基準を動かすときは、結果を見る前に contracts を更新して commit してから回す。
