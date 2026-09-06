# Contract: patterns.json(パターン族)

- 生成: `buy_patterns.enumerate()` が決定論的に 393 本を返し(構造的同一の 9 本を除去済み)、driver `freeze` が `patterns.json` に書く。hash は書かれた内容に対して取る。
- 形は [data-model.md §2](../data-model.md)。条件 `op ∈ {eq, in_band(lo≤x<hi), is_true, is_null}`。
- `horse_rule`: レース単位条件のみのパターンは `fav | cap11 | cap21` のいずれか必須。馬単位条件を含むパターンは null(自己選択)か、C×H / F×H では `cap11 | cap21 | fav` と AND(fav と AND = 「条件を満たしかつ 1 番人気」)。
- `available_at`: 条件ごとに必須。`pre_entry`(sex・クラス・距離・芝ダ・間隔・前走着順・叩き 2 走目)/ `post_draw`(頭数)/ `closing`(odds・q・fav_q・entropy・p_rank・p/q・p 分位・EV。p 自体は結果を読まないが順位と比は closing と束の組み合わせなので closing に倒す)。
- 禁止: 条件の `field` に `won`・`finish_order`・`payout` を含む定義は列挙時に例外。
- 結果並べ替え不変: テストで `won` 列を並べ替えても各パターンの `selection_hash` が不変。
- 対照は `controls` キーに別置(`no_bet` / `favorite` / `cap11_all` / `cap21_all`)。`ev1_all` は `X.ev_ge_1` の別名として表示のみ。族の hash には含めるが順位づけ対象にしない。
- 帯は全て `[lo, hi)`(research D4 に列挙)。fav と p 順位の同率は `horse_number` 昇順で一意化。厳密過去分位の境界値は上側の帯へ。
- 厳密過去分位(p / エントロピー)はそのレース日より前のデータだけから作り、2008 年は欠損=買わない。
