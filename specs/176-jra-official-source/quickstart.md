# Quickstart: 176 の検証手順

目的は spec の SC-001〜012 を再現可能な手順で確かめること。実装の本体は tasks.md とコードに置く。コマンドは cwd=リポジトリ root。

## 0. 前提

- Phase 1 以降の検証は**開催日の最終レース後**に行う(本番 DB への 0023 適用・共通 writer の切替は開催中に行わない。研究 D20)。
- 一時 PostgreSQL は各パッケージの testcontainers が立てる。実 DB を使う手順は「実 DB」と明記した箇所だけ。
- fixture は `scrape/tests/fixtures/jra/`(raw Shift_JIS・manifest に raw_sha256)。`manifest.json` の sha が一致することを `scrape/tests/unit/test_jra_fixture_manifest.py` が確認する。

## 1. 単体・契約テスト(ネットワークなし)

```bash
cd scrape && uv run pytest -q tests/unit/test_jra_*.py
```

期待: 解析器(出馬表・変更情報・単複オッズ・結果・払戻・馬場断片・accessJ)が fixture から data-model §4 の値を返す。構造変更 fixture(列欠落・見出し変更・別日・パラメータエラー画面)は `ParseError` / `JraParameterError` で**部分値を返さない**(SC-007)。cp932 拡張文字と壊れたバイトを区別する。`precedence.py` の遷移表・オッズ判定・衝突検査が contracts/precedence.md の表どおり。

```bash
cd scrape && uv run pytest -q tests/unit/test_politeness_contract.py tests/unit/test_jra_politeness.py
```

期待: `source_interval_s("jra.go.jp")` は env 未設定・NaN・inf・<10 で例外、netkeiba とその他ホストは不変。`throttle_key` が `www.jra.go.jp` を `jra.go.jp` に、`notjra.go.jp` を別鍵に(SC-006)。

## 2. 統合テスト(一時 PostgreSQL)

```bash
cd scrape && uv run pytest -q -m integration tests/integration/test_jra_*.py tests/integration/test_precedence_*.py
```

期待(SC-010/011/012 の契約テスト):
- **順序行列**: netkeiba→公式→netkeiba / 公式→netkeiba→公式 / 同一入力 / 古い入力 / 同時刻異値 / 時刻不明 を、出走状態・静的項目・騎手・体重(対)・単勝・複勝・結果・券種別払戻のそれぞれに適用し、業務値が規則どおり・二重出走行 0・競合が summary に記録される。
- **衝突の両方向**: JRA 先行後に netkeiba が `nk:` を返す / 同馬番別 ID / 同 ID 別馬番 / 既存重複 / 取消馬番不明 / 入力重複 で、二重行 0・隔離レースの既存行が 1 行も変わらない・`merge_candidates` が出る(SC-011)。
- **来歴の部分更新**: A 馬だけ体重変更・B 馬は旧値・騎手だけ変更・同値の公式採用・旧値拒否・NULL 保持で、各値の `source` / `observed_at` / `ingestion_job_id` が真実を表す。`win_odds.official_at == races.win_odds_official_at`。
- **結果**: `race_atomic` は既存行があれば挿入 0+`results_mismatch`、1 行不正で挿入 0。`per_horse_legacy` は現行どおり(`corner_orders` / `running_style` の NULL 補完だけ)。
- **払戻集合**: 同着・勝ち馬集合の訂正・同額の公式採用(値不変・source と来歴だけ移る)・組番重複は parse_failed・元返し・返還・未発表と発売なしの区別・枠連は保存しない・円/100→倍率。
- **並行実行**(同期点つき): 結果確定とオッズ更新 / 公式払戻と netkeiba 再投入 / 新規レース行の作成競合 / 逆順の複数レース。値更新後・来歴更新前の障害で両方戻る。
- **送信直前の制御**(SC-012・HTTP 単位): robots・POST・リダイレクト先・再試行の全要求が共有スロットを通り、別プロセス競合でも間隔 ≥ 設定値、DB 障害で 0 件、スロット待ち中に捕捉時間帯が開くと次の送信で止まる、`HORSERACING_SHARED_LIMITER=0` と policy 欠損で 0 件。
- **拒否と辿り直し**: robots 403 / 429 / Retry-After 秒と HTTP-date / 503 / 400 / 200 パラメータエラー / error013 / リダイレクトループ を分類し、同じ不正 POST を再送しない、辿り直しは 1 回。
- **archive**: 単複オッズページが archive に残らない、出馬表・結果は残り `url.txt` に cname 平文が無い。

## 3. parity(SC-001 / SC-003)— 固定 fixture と一時 PostgreSQL

```bash
# 実 DB(読み取り専用)から netkeiba 側の対象行を取得段階・取得時刻・ID 解決状態つきで書き出す(一度だけ・repo に保存)
uv run --project scrape python scripts/export_parity_fixture.py --date 2026-11-01 --venue 05 --stage pre_result \
  --out scrape/tests/fixtures/jra/parity/netkeiba_20261101_tokyo.json
uv run --project scrape python scripts/export_parity_fixture.py --date 2026-10-31 --venue 05 --stage post_result \
  --out scrape/tests/fixtures/jra/parity/netkeiba_20261031_tokyo.json
cd scrape && uv run pytest -q -m integration tests/integration/test_jra_parity.py
```

期待: 一時 DB に netkeiba fixture を投入 → 公式 fixture を `jra-entries` 相当で投入 → 出走馬集合・馬番・騎手・調教師・斤量が**比較可能なレースで 100% 一致**(取消馬は状態まで)、`nk:` 衝突で隔離されたレースは「比較不能」として分母から分けて数え、件数が出る(SC-001)。結果・払戻は着順・タイム・馬体重・単勝払戻額 100% 一致・同着レースは勝ち馬の数だけ行(SC-003)。新規 `nk:` 行 0(SC-002)。

## 4. 実 DB での補助確認(Phase 2 以降・開催終了後)

```bash
scripts/jra_ops.sh fetch 2026-11-01            # 収集(業務表不変)
scripts/jra_ops.sh audit 2026-11-01            # 照合(DB 読み取り専用)
scripts/jra_ops.sh entries 2026-11-01 --dry-run
```

期待: `requests_sent` の合計が出馬表+変更情報+結果+払戻+馬場で ≤20、単複オッズ全レース込みで ≤50(SC-005)。audit の一致率と `stored_source=null` の件数が出る。dry-run は業務表・job・来歴を書かない(`SELECT count(*)` 前後一致)。

## 5. リーク境界と予測不変(SC-008)

```bash
cd features && uv run pytest -q tests/unit/test_jra_leak_guard.py tests/integration/test_forbidden_tables_do_not_move_features.py
```

期待: features ソースに `race_field_provenance` / `race_horse_field_provenance` / `jra_track_conditions` / `JraTrackCondition` / `cushion` / `moisture` が無く、列名にも含まれない(静的)。一時 DB に標準の seed を入れて `features materialize` → 禁止 3 表だけに行を入れて再 materialize → parquet がバイト一致、active 相当モデルの予測もバイト一致(動的)。SC-008 は「同じ入力データでコード変更前後」に限る(正規 ID・取消・騎手が正しく直った後の予測まで不変は要求しない)。

## 6. OpenAPI と境界(SC-009)

```bash
cd api && uv run pytest -q tests/test_openapi_drift.py
cd front && pnpm test -- openapi ; cd ../admin && pnpm test -- openapi
cd ops && uv run pytest -q tests/integration/test_boundary.py tests/integration/test_capture_window.py
```

期待: snapshot 不変。ops の `capture_window` 再 export で既存テスト緑、ops の禁止 import 不変。

## 7. 既存経路の回帰(Phase 1 の切替前後)

```bash
cd scrape && uv run pytest -q
cd ops && uv run pytest -q
cd live && uv run pytest -q
cd db && uv run pytest -q
```

期待: 契約変更として更新したテスト(取消の一方向・オッズの全馬一体・複勝の時刻規則・衝突隔離)以外は無変更で緑。`decision_quotes` のテスト不変。174 の T−3 捕捉の fetch 経路は変更なし(`live/near_post.py` 差分ゼロ)。

## 8. migration

```bash
cd db && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head   # 一時 DB
```

期待: 0023 で 5 CHECK 置換+来歴 2 表、0024 で馬場表。`jra_official` 行がある状態の downgrade は中断する(テストで固定)。本番適用は最終レース後に `lock_timeout` を短く設定し、適用前後に `alembic_current` と行数を記録する(`specs/176-jra-official-source/result-review.md` に転記)。
