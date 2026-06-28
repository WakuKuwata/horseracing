# Parser Contract: 実 netkeiba パーサ (022)

本 feature が公開/維持する内部インターフェース契約。**シグネチャと dataclass 形は不変**（実装のみ置換）。取得層・upsert は呼び出し側で、ここは「入力（実 netkeiba コンテンツ）→ 出力（既存 dataclass）」の純関数契約。

## 1. parse 関数シグネチャ（維持）

```python
# scrape/parse/entries.py
def parse_entries(html: str) -> ScrapedEntry        # 実 netkeiba 出走表 HTML を解析
# scrape/parse/results.py
def parse_results(html: str) -> ScrapedResult        # 実 netkeiba 結果 HTML を解析
# scrape/parse/odds.py
def parse_odds(payload: str) -> ScrapedOdds          # 実 netkeiba 単勝オッズ JSON を解析
```

- 入力: entries/results は HTML 文字列、odds は JSON 文字列（取得方式ハイブリッド）。
- 出力: 既存 `models.py` の dataclass（[data-model.md](../data-model.md) 参照）。
- 失敗時: 必須要素を欠く場合 `ParseError` を送出（fail-close、誤データを作らない）。
- 純関数・ネットワーク非依存（テストは保存フィクスチャで実行）。

> 注: 既存 `parse_odds(html)` は HTML 前提だった。本 feature で **入力を odds JSON へ変更**する（呼び出し側 pipeline の odds 取得 URL も JSON エンドポイントへ）。これは置換の一部。

## 2. URL 構築契約（新規ヘルパ）

```python
# scrape/venues.py または新規 urls.py
def netkeiba_entries_url(race_id: str) -> str   # race/shutuba.html?race_id=...
def netkeiba_result_url(race_id: str) -> str    # race/result.html?race_id=...
def netkeiba_odds_url(race_id: str) -> str      # 単勝 odds JSON エンドポイント
```

- 入力 race_id は JRA-VAN 12桁（= netkeiba race_id、恒等）。
- エンティティ ID の guess-join ではない（憲法 I 非抵触、research R6）。
- odds JSON エンドポイントの正確な URL/パラメータは実装最初のタスクで実サンプル確定し、`parser_version` に反映。

## 3. 実 netkeiba マークアップ → フィールド対応（実装で実サンプル確定）

### entries (出走表 HTML)
| 出力フィールド | 抽出元（想定・要実サンプル確定） |
|---|---|
| race key (year/track/kai/nichime/race_no) | URL の race_id 分解 + ページ内テキスト交差検証 |
| race_date / distance / track_type / going / weather / race_class | レース見出し・コース表記ブロック |
| frame / horse_number | `Shutuba_Table` 各行のセル |
| netkeiba_horse_id | 行内 `/horse/{id}` リンク |
| horse_name | `HorseName` |
| netkeiba_jockey_id / jockey_name | `/jockey/{id}` リンク・テキスト |
| netkeiba_trainer_id / trainer_name | `/trainer/{id}` リンク・テキスト |
| sex / age | 性齢テキスト（例「牡3」）正規化 |
| weight | 斤量セル |
| entry_status | 取消・除外表記 → started/cancelled 等 |

### results (結果 HTML)
| 出力 | 抽出元 |
|---|---|
| finish_order | 着順テーブル |
| result_status | 除外/中止/失格表記 → finished/stopped/disqualified |
| finish_time | タイム文字列（例「1:34.5」）正規化 |
| netkeiba_horse_id | `/horse/{id}` |

### odds (単勝 JSON)
| 出力 | 抽出元 |
|---|---|
| netkeiba_horse_id or 馬番 | JSON の馬キー |
| odds | 単勝オッズ値 |
| popularity | 人気順 |

⚠️ **突合キー（I1）**: `update_odds` は horse_id 一致で更新する。odds JSON が **馬番のみ**なら `race_horses.(race_id, horse_number) → horse_id` で解決する経路を取り込み側に用意する（horse_id を含むならそのまま resolve_entity）。実エンドポイントのキー形は T018/T019 で確定。

## 4. robustness / バージョニング

- 必須要素セレクタが取れない → `ParseError` → pipeline が fail-close し `ingestion_jobs.errors` に記録。
- 必須フィールド（馬番/horse_id/finish_order）は **strict parse**（`to_int/to_float` で None に潰さず ParseError）。
- **URL の race_id と HTML 本文の race_id を照合**、不一致なら ParseError（誤レース投入防止）。
- **odds JSON は no-cache** で取得（`odds_adapter`）。JSON 必須キー欠損は fail-close。古い odds を書かない（憲法 V）。
- `ParserProfile(version, required_selectors, invariants)` を parse 層に持つ。`parser_version` を監査に記録、netkeiba 構造変化時に上げる。
- セレクタは限定的に（過度に脆い深いパスを避ける）。実フィクスチャ回帰テストで構造変化を検知。
- **永続化補修（upsert 小改修・スキーマ変更なし）**: `update_odds` に `popularity`、`backfill_results` に `finish_time`(str→Interval) を追加（既存カラムだが現状書かれていない）。

## 5. 不変条件テスト（契約テスト）

- entries/results/odds 各 parser を**実 HTML/JSON フィクスチャ**で検証（正常）。
- 必須要素を欠いた改変フィクスチャで `ParseError`（fail-close）。
- 出力 dataclass の形が upsert の期待と一致（型・必須フィールド）。
- leak-guard: odds/結果由来の値がモデル特徴量に出現しない（既存テスト維持）。
