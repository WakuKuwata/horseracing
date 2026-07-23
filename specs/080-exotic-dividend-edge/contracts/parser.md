# Contract: parse_exotic_odds (real netkeiba result-page markup)

**Module**: `scrape/src/horseracing_scrape/parse/exotic_odds.py`
**Signature (unchanged)**: `parse_exotic_odds(html: str) -> ScrapedExoticOdds`

## Input

- `html`: 実 netkeiba result ページ HTML(`race/result.html?race_id=...`)。日次 `scrape_results` が既に fetch・cache 済のもの(相乗り時は同一文字列を再利用、追加 fetch 0)。

## Output

- `ScrapedExoticOdds(key, rows)`:
  - `key`: `race_id_from_html(html)` で実 markup から抽出(fixture 専用 `race_key_from(div)` は廃止)。
  - `rows`: 対応 6 券種の `ScrapedExoticRow(bet_type, numbers=馬番tuple, odds=倍率)` 列。

## 券種マップ(日本語ラベル → canonical)

| netkeiba ラベル | canonical bet_type | selection 正準(upsert 側) |
|---|---|---|
| 複勝 | place | single(各当選馬 1 行) |
| 馬連 | quinella | sorted pair |
| ワイド | wide | sorted pair(複数当選=複数行) |
| 馬単 | exacta | ordered pair |
| 3連複 | trio | sorted triple |
| 3連単 | trifecta | ordered triple |
| 枠連 / 単勝 | (skip) | — |

## 変換規則

- **odds 倍率** = 払戻金(円)/ 100(netkeiba は 100 円あたり払戻)。
- **numbers** = 組合せセルの 馬番を順序保持で抽出(exacta/trifecta は着順=左→右、quinella/wide/trio は upsert が sort)。
- **同着/複数払戻**(複勝 2-3 頭・ワイド複数・同着分割)= 各当選 selection を**別 row** として全出力(取りこぼし禁止)。

## エッジ挙動(MUST)

| ケース | 挙動 |
|---|---|
| 未対応券種(枠連)・欠損テーブル | その券種スキップ、他は継続(partial) |
| 結果未確定(払戻テーブル無) | rows 空 → 呼び出し側で skip(例外を上げるか空返しかは実装で統一、相乗り側で吸収) |
| 馬番 parse 不能な行 | その行スキップ、他行継続 |
| odds None/<=0 | row は出しても可(upsert 側で skip) |
| **markup 変更で全 0 行** | **fail-loud**: 期待券種数の下限(例: 確定レースなら最低 quinella+trio+trifecta が存在)を満たさない場合は異常として検知可能にする(silent-empty を通さない) |

## 検証済み実 markup(T0 spike 2026-07-23・fixture `results_202602011206.html`)

live result ページに **`Payout_Detail_Table` × 2** が含まれる(左表=単勝/複勝/枠連/馬連・右表=ワイド/馬単/3連複/3連単)= **日次 result 相乗りで追加リクエスト 0 を実物で確認**。行構造:

```
<tr> <th>券種ラベル</th>
     <td class="Result"> …組合せ… </td>
     <td class="Payout"><span>払戻金<br />払戻金…</span></td>
     <td class="Ninki">…人気…</td> </tr>
```

- **Payout セル**: `<span>` 内を `<br />` で分割 → 各払戻金(例 `2,000円`・`10,940円`)。カンマ除去・`円` 除去 → `/100` = 倍率。
- **Result セル(2 形式)**:
  - **combo 系**(馬連/ワイド/馬単/3連複/3連単/枠連): 選択ごとに `<ul><li><span>N</span></li>…</ul>`。`<ul>` の繰り返し = 複数選択(ワイドは 3 `<ul>`)。`<li>` 内 `<span>` の馬番を順序保持(exacta/trifecta は着順)。空 `<li>` は無視。
  - **複勝**: `<div><span>N</span></div>` の非空 `<span>` が各単勝選択(例 `1`,`9`,`10`)。空 `<div><span></span></div>` はパディング → 無視。
- **1:1 zip**: 券種行内で「Result の選択列」と「Payout の `<br>` 分割列」を同数で対応付け(不一致は fail-loud)。
- **券種**: 複勝/馬連/ワイド/馬単/3連複/3連単 を採用、**単勝・枠連はスキップ**(単勝=既存 win 経路、枠連=canonical 外)。
- **Ninki セル**: 無視。

**実装上の最難所**: 複勝の `<div><span>` パディング構造(空 span を除外して非空のみを payout と zip)。combo 系の `<ul>` 単位分割。→ 実 fixture テストで固定。
**同着**: 本 fixture に同着行は無いが、複数払戻の markup(複勝/ワイドの複数 `<br>`/`<ul>`)と同一機構=同着追加選択も同じ形。同着専用 fixture は後日捕獲 or 既知構造で合成。

## テスト(network-free, 実 fixture)

- 実 netkeiba result HTML fixture(`scrape/tests/fixtures/real/result_*.html`)に対し:
  1. 対応全券種の (bet_type, selection, 倍率) が期待値一致
  2. 同着複勝 4 頭・ワイド複数払戻の全行抽出
  3. 未対応券種スキップで他券種継続
  4. 結果未確定 fixture で rows 空
  5. 期待券種欠落時の fail-loud(silent-empty 検知)
