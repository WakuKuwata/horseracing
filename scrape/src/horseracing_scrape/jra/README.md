# `horseracing_scrape.jra` — JRA 公式サイト経路(Feature 176)

| モジュール | 役割 |
|---|---|
| `fetch.py` | `JraFetcher`: 共有スロット・robots・捕捉時間帯 guard・cp932 strict・応答の分類・kind 許可制の archive |
| `policy.py` | 起動時検証(policy・env・共有リミッタ・DB)。1 件も送る前に落ちる |
| `navigate.py` | 索引 → 開催日 → ページの cname 遷移。パラメータエラーは索引から 1 回だけ辿り直す |
| `parse/` | 出馬表・変更情報・単複オッズ・結果・払戻・馬場の解析(値オブジェクトを返す・DB を知らない) |
| `upsert_jra.py` | 公式経路の薄い書込み層(期間ガード・未知 ID の隔離・取消馬の馬番復元)。規則は共通 writer |
| `cli.py` / `results_cli.py` / `baba_cli.py` / `audit.py` | `scrape jra-*` サブコマンド |

契約: `specs/176-jra-official-source/contracts/`(`fetch-and-parse.md` / `precedence.md` / `cli.md`)。
運用: `specs/176-jra-official-source/manual.md`。

## 禁止事項

1. **`HttpFetcher` を触らない**。netkeiba 側の取得は不変で、共有するのはスロットと robots キャッシュだけ。
2. **両経路の規則の正本は `horseracing_scrape/precedence.py`**。衝突検査・状態遷移・オッズの採否・払戻の集合
   置換をこのパッケージで再実装しない(`upsert_jra.py` は共通 writer を呼ぶだけ)。
3. **fixture は raw のまま、cname を平文で書かない**。manifest とテストには `cname_sha256` だけを残す。
   動的 cname をコードやテストに直書きしない(`test_jra_fixture_manifest.py` が検査する)。
