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

### 表現規則の是正(R4・2026-11-09・**基準(閾値)は不変**)

18 年分の初回 parity(`artifacts/jrdb/parity/r4-first-run/`)で、不一致の大半が値の違いではなく**比較側の表現**の取り違えだったので、
「正規化後一致」の正規化を次のとおり直して取り直した(結果を見て閾値を動かしたのではなく、同じ意味の値を同じ表現に揃えた):

| 列 | 初回の不一致 | 是正 |
|---|---|---|
| grade | JRDB `特別`/`G2` vs DB `E`/`B`(JRA-VAN の文字コード) | DB の A/B/C/D/E/F/G/H/L を G1/G2/G3/重賞/特別/G1/G2/G3/L に写してから比較(`GRADE_FROM_LETTER`) |
| finish_time_diff | JRDB `0.2` vs DB `-0.2`(後着を負で持つ) | 大きさ(絶対値)で比較・許容差 0.1 秒は不変 |
| owner_name | JRDB `(有)サンデーレーシング` vs DB `サンデーレーシング` | 先頭/末尾の法人略号 `(有)(株)(同)(合)(資)(社)` を両側で除去 |
| owner_name / breeder_name | JRDB は全角 20 で切れる(`WhisperHillFarmLL`) | JRDB 値が欄いっぱいのとき DB 側を同じ長さの前方一致で比較(`prefix_if_truncated`) |
| race_name_short | JRDB 全角 4 の短縮名 vs DB「日高特別・2勝」 | 同上(幅 8 バイト) |
| sire / dam / damsire / owner / breeder | `ワッスルタッチ` vs `ワツスルタツチ`(JRA-VAN の歴史的大文字表記) | 小書き仮名を大書きに畳んで比較 |

是正しなかったもの(値そのものの違い): 血統名の **JRDB=カナ転写 vs DB=原綴り**(`ドバウィ` vs `Dubawi`。表記体系の違いで正規化不能・
現行 DB の血統名は特徴の集計キーなので JRDB で上書きしない)、`going` の開催内の馬場変更差、`prize_money` の 0.7 倍差(年 6 レース前後・頭数規則ではない・原因未特定)。

