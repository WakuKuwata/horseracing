# Quickstart: 予測実行時の荒れ度スナップショット捕捉 (086)

**Feature**: 086 | **Plan**: [plan.md](./plan.md)

実装完了後にこの順で回すと、SC-001 / 002 / 004 / 007 / 010 と FR-014 が実データで確認できる。
**SC-011 の正本は T018a / T038d の実測**(`docs/plan/086-capture-timing.md`)。
**SC-003 / 005 / 006 / 008 / 009 は自動テストが正本**(T030 / T028・T047a / T011 / T029 / T020・T036a・**T036c**)。

---

## 前提

```bash
docker start docker-postgres-1
```

```bash
export DATABASE_URL='postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing'
```

API(:8000)と ops worker を常駐させておく([[local-db-setup]])。
ops worker が動いていないと予測ジョブがキューに滞留したまま進まない。

---

## 1. migration を当てる

```bash
cd db && DATABASE_URL="$DATABASE_URL" uv run alembic upgrade head
```

head が `0013` になり、`chaos_snapshots` に **2 列 + 無条件 `UNIQUE(race_id)`**、
新表 `fetch_throttle_state` が増えている。
**手順は既存行数によらず同一**(nullable→backfill→NOT NULL→UNIQUE)。

**同一レースに 2 行以上ある環境では 0013 は中断する**(084 の追記経路が作りうる)。
その場合は退避してから再実行する:

```bash
cd db && DATABASE_URL="$DATABASE_URL" uv run python -m horseracing_db dedupe-chaos-snapshots
```

既定は dry-run。内容を確認してから `--apply` を付けて実行し、`alembic upgrade head` に戻る。
退避表 `chaos_snapshots_quarantine` はこの CLI が作る(migration は作らない —
中断とともに巻き戻って退避先が消えるため)。

---

> **DB の設定を 1 つ確認する**: `SHOW idle_in_transaction_session_timeout;`
> が `0`(無制限)か **30 秒より長い**こと。捕捉は排他を取ったまま外部取得を行うので、
> これより短いと日次経路(期限 30 秒)が静かに落ちる。

## 2. 新 artifact を発行して承認する

```bash
cd training && DATABASE_URL="$DATABASE_URL" uv run python -m horseracing_training chaos-bands \
  add-horizon --artifact e782c255adde487e200c5814a61962ffc8c87709811b4b9511c223f1c33b8d8f \
  --primary-horizon-min-seconds-to-post 600 --primary-horizon-max-seconds-to-post 86400 \
  --primary-horizon-basis schedule_jitter_floor_and_next_day_market_ceiling
```

新しい digest が `artifacts/chaos_bands/` に生成される。**v1 のファイルは書き換わらない**。

**承認 manifest は手で編集する**(コマンドは書き換えない) —
`config/chaos_bands_approved.json` に新 digest を `status="active"` で追加し、
現 e782c2… を `status="superseded"`(+ `superseded_by`)に変える。
自動承認にすると「承認済み digest しか受け付けない」という安全境界が自己承認で無効化される。

> 参考: 086 以前の `load_current_chaos_artifact` は `approved[-1]` を現行としていたため、
> **superseded の artifact を読んでいた**(manifest は active を先頭に置いている)。
> 086 で `status="active"` による解決に是正される。

**期待**: λ2 / λ3 / 五分位境界が v1 と完全に一致していること(出力に差分表示が出る)。

### 2b. api の参照先を新 digest に張り替える

api は `CHAOS_BANDS_ARTIFACT_PATH` で**特定のファイルパス**を読む。
張り替えないと、fail-closed ローダが窓を持たない v1 を拒否して**荒れ度が出なくなる**。

```bash
export CHAOS_BANDS_ARTIFACT_PATH="$PWD/artifacts/chaos_bands/<新しい digest>.json"
export CHAOS_BANDS_APPROVED_MANIFEST="$PWD/config/chaos_bands_approved.json"
```

API を再起動し、**レース詳細で荒れ度が出ること**を確認する(SC-010)。

---

## 3. 窓なし artifact が全経路で拒否されることを確認する(SC-006 の実データ確認・正本は T011)

```bash
cd training && DATABASE_URL="$DATABASE_URL" uv run python -m horseracing_training chaos-bands \
  prospective-report --artifact e782c255adde487e200c5814a61962ffc8c87709811b4b9511c223f1c33b8d8f
```

**期待**: 型付きエラーで**止まる**(`primary_horizon` 欠落)。
以前は `0..∞` に黙って落ちていた挙動が廃止されている。

---

## 4. 発走前レースで予測を実行する(SC-001)

admin か front のレース詳細で「予測生成」を押す。あるいは:

```bash
curl -s -XPOST localhost:8001/ops/v1/races/<race_id>/predict
```

```bash
curl -s 'localhost:8000/api/v1/jobs?job_type=predict&limit=1' | python3 -m json.tool
```

**期待**: `summary.capture.outcome == "captured"`、
`capture_strength == "confirmatory"`、`seconds_to_post` が窓内なら
`confirmation_eligible == true`。予測も通常どおり生成される。

---

## 5. 連続実行しても増えないことを確認する(SC-002)

同じレースでもう一度予測を実行する。

**期待**: `summary.capture.outcome == "skipped"` / `reason == "already_captured"`。
`chaos_snapshots` の行数が増えない。**外部取得も発生していない**
(ops worker のログに netkeiba への追加リクエストが出ない)。

出走取消が入ったレースでも同じである — 行は `void/field_changed` になるだけで、
**撮り直しは 1 回も起きない**。1 レースのオッズ入り行は生涯ちょうど 1 行。

---

## 6. 発走後レースでは取得が起きないことを確認する(SC-004)

発走時刻を過ぎたレースで予測を実行する。

**期待**: `reason == "post_time_elapsed"` かつ**外部取得 0 回**。
084 ではここで先にフェッチしてから拒否していた。

---

## 7. 取得元が抑制中でも予測が通ることを確認する(FR-014)

> **SC-003(捕捉が失敗しても予測は成功)の正本は自動テスト T030 である** —
> ここで起こすのは*見送り*であって失敗ではない。

`fetch_throttle_state` に手で抑制を書いてから予測を実行する。

```sql
INSERT INTO fetch_throttle_state (domain, next_allowed_at, blocked_until, block_reason, updated_at)
VALUES ('https://race.netkeiba.com', now(), now() + interval '30 minutes', 'manual_test', now())
ON CONFLICT (domain) DO UPDATE
  SET blocked_until = excluded.blocked_until, block_reason = 'manual_test', updated_at = now();
```

**期待**: `summary.capture.outcome == "skipped"` / `reason == "source_cooldown"`。
**ジョブの status は SUCCEEDED**、admin の行がエラー色にならない。

---

## 8. 契機別内訳を確認する(SC-007)

```bash
cd training && DATABASE_URL="$DATABASE_URL" uv run python -m horseracing_training chaos-bands \
  prospective-report --artifact <新しい digest>
```

**期待**: `by_capture_trigger` に **5 契機**が出る(0 件でも行が出る)。
`user_selected_share` が出る。`prospective_selection_bias` に
「日次の中立な捕捉が主・利用者選択は補助・除去できない」旨が出る。

**`predict_manual` と `predict_auto` が別行**であることを確認する。
データ更新の後に自動で積まれた予測は利用者が選んだものではないので中立であり、
合算すると選択バイアスを過大に見積もる。

---

## 8b. 新しい CLI 引数を通しで確認する

```bash
cd live && DATABASE_URL="$DATABASE_URL" uv run python -m horseracing_live \
  capture-chaos --race-id <race_id> --json
```

**期待**: 1 行 JSON が出る。契機は既定で `explicit_command`、期限は 30 秒。

```bash
cd live && DATABASE_URL="$DATABASE_URL" uv run python -m horseracing_live \
  capture-chaos --date <2 日以上先の開催日>
```

**期待**: **非 0 で終了して何もしない**(FR-001b1)。
`--allow-outside-horizon` を付けると実行される。

---

## 9. カバレッジで進捗を追う

```bash
cd training && DATABASE_URL="$DATABASE_URL" uv run python -m horseracing_training chaos-bands \
  coverage --from 2026-01-01 --to 2026-12-31
```

最小陽性 100(artifact の事前登録値)に対する現在地が出る。
本 feature 導入後は**予測を実行するだけで**この数字が積み上がる。

---

## 10. テスト一式

```bash
for p in db probability eval features training live ops api betting serving ingest; do (cd $p && uv run pytest -q) || echo "FAIL $p"; done
```

```bash
(cd scrape && uv run pytest -q)
```

scrape は `FetchRefused` の追加で挙動が変わる唯一の既存パッケージなので、
**全件緑**であることを必ず確認する(着手時点の実測は 97 件)。

```bash
(cd admin && pnpm test && pnpm build) && (cd front && pnpm test)
```

```bash
(cd front && pnpm gen:types && pnpm check:openapi)
```

```bash
(cd admin && pnpm gen:types && pnpm check:openapi)
```

型は front / admin の**両方**を再生成してコミットする(`check-openapi.sh` は committed の型と diff する)。

---

## 運用に戻る

日次の中立な捕捉は**引き続き主ソース**である(予測実行由来は補助)。

```bash
cd live && DATABASE_URL="$DATABASE_URL" uv run python -m horseracing_live capture-chaos --date YYYY-MM-DD
```

> **既定の挙動が変わった**: その日の最も遅い発走までの残り時間が 24 時間を超えると
> **このコマンドは非 0 で終了して何もしない**(FR-001b1)。
> 意図して先に流すときだけ `--allow-outside-horizon` を付ける。
> 1 レース生涯 1 観測なので、早く流すとその開催日ぶんが確認コホートから恒久脱落する。

**期待収量**: 採用した窓は 10 分〜24 時間なので、日次コマンド 1 回で
**当日の未発走レースをほぼ全部拾える**(FR-001a により窓外でも捕捉する)。

**正直な限界は「近接捕捉」についてのみ成立する**: 開催日の発走は 8.8 時間に分散するので、
**発走直前(T−30 分)に捕捉できるのは 1 回の実行あたり 1〜2 レースだけ**である。
全レースを発走直前に捕捉するにはスケジューラが要り、それは憲法が先送りしている。
オッズの成熟度が揃った観測を集めたいなら実行回数を増やすしかない。
