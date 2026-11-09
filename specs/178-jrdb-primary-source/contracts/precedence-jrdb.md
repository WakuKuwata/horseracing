# Contract: 176 の書込み規則への JRDB 列の追加(差分だけ)

正本は `scrape/src/horseracing_scrape/precedence.py`。176 の [contracts/precedence.md](../../176-jra-official-source/contracts/precedence.md) の
§1〜§7 はそのまま。ここでは `incoming_source == "jrdb"` のときの判定と、`stored_source == "jrdb"` に対する他経路の判定だけを定める。

## §1 ロック順序と部分成功
同じ(`races` を `FOR UPDATE` → ロック下で再読込 → 純関数 → 同一 savepoint で値と来歴)。JRDB は 1 ファイル=複数レースなので
**レースごとに savepoint** を切り、1 レースの失敗が他レースに波及しない。結果は `jrdb_files.race_status[race_id]` に
applied/failed/skipped(理由)で残し、ファイルの `status='applied'` は全レース applied のときだけ。同じ sha を再投入すると
未 applied のレースだけ再処理する(applied 済みは製品値の変更 0)。

## §3a 書込み先プロファイル(必須)
`profile=current` では `first_3f`・`corner_orders`・`running_style`・`sire_line/damsire_line` を decode 段で落とし、upsert 段の列 allowlist でも
拒否する(二重ガード)。`profile=jrdb_canonical`(段 3b の別 DB)だけがこの 4 列を JRDB の定義で書く。通常取込・修正版・修復・再構築の
全経路が同じ関数を通る。

## §2 衝突検査
不変。JRDB が行を作るときは `existing=()` なので衝突は起きない。既存行があるレースでは netkeiba/公式と同じ検査を通す。
`nk:X` と `X` の別名(176 FR-011a)は `jra/alias.py` を**そのまま呼ぶ**(JRDB 用の複製を作らない)。

## §3 項目別遷移表(JRDB の列)

| 項目群 | incoming=jrdb | stored=jrdb に対する netkeiba | stored=jrdb に対する jra_official |
|---|---|---|---|
| 行・マスタの生成 | **write(作る)**。`horses.data_source='jrdb'`・`birth_year` は UKC 生年月日 | 作らない(既存どおり) | 作らない(緊急モードのみ作る) |
| entry_status | 一方向遷移(共通) | 共通 | 共通 |
| jockey / trainer | 写像済み ID だけ write。未解決は **keep**(NULL のままなら NULL)+`unresolved_person` | 時刻規則(共通) | 時刻規則(共通) |
| entry_static / race_static | stored が無ければ write。stored=jra_official なら keep+`static_mismatch`(値が違えば)。stored=netkeiba なら write+`static_mismatch` | **keep**+`static_mismatch` | write+`static_mismatch`(公式が正) |
| body_weight | TYB: 対で write(時刻規則)。SED 正式版: 結果確定後の確定値として write | 共通 | 共通 |
| post_time | BAC: stored が無ければ write。stored=jra_official なら keep | keep | write |
| win_odds / place_odds | **書かない**(TYB も基準オッズも) | — | — |
| 確定単勝オッズ・人気 | SED 正式版 → `apply_final_odds` と同じ規則(値が変わる行だけ・来歴 `win_odds_official_at=NULL`) | 既存どおり | 公式は数値を持たない |
| results | `race_atomic`(SED **正式版のみ**。速報版は入力にしない) | per_horse_legacy(既存) | race_atomic |
| laps | SRB → `race_laps` に source=jrdb。stored が netkeiba でも write(先頭馬の 1F は同じ定義=要 parity) | keep | — |
| win_payouts / exotic_payouts | HJC → 券種ごとの集合置換(共通)。stored=jra_official で同値なら値を触らず採用元は公式のまま | 共通 | 共通 |

`static_mismatch` は報告であって拒否ではない(176 と同じ)。報告 0 件が段 2 のゲート。

## §4 速報版と正式版
SED 速報版(当日 18:00)は `jrdb_files.kind='SED'`・`variant='daily'` で取り込むが、§3 の入力にするのは**正式版(木)だけ**。
速報版は `fetched` で止め、正式版が来たら `superseded`。理由: 速報版は確定オッズ・馬場・通過順・前後 3F・脚質が空で、
結果の先出しを JRDB に頼る必要が無い(公式・netkeiba が当日の結果を書く)。

## §5 修正版
同じ (kind, data_date, variant) で sha が違えば異内容。自動で新旧を決めてよいのは **速報版→正式版**(配信日程)と **日次→バックナンバー**
(訂正はバックナンバーに反映される)だけ。それ以外の異内容は `held`(自動適用せず、差分行を報告・運用者が承認したものだけ §3 を再適用。
更新できる列は §3 どおり・先着保持の項目は変えない)。`published_at` は記録するだけで判定に使わない。
