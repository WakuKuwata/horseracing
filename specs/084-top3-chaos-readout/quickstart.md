# Quickstart: 上位3着ベースの荒れ度読み出しの検証 (rev2)

**Feature**: 084-top3-chaos-readout | **Revised**: 2026-07-26

実装が end-to-end で動くことを示す実行可能シナリオ。実装コード本体は tasks / implement の領分。

---

## 前提

- ローカル Postgres 起動(`docker-postgres-1`, port 15432)
- `DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`
- 実 DB に 2007-2026 のレース(67,636)

```bash
uv run --project db alembic upgrade head && uv run --project db alembic current
```

期待: `0012_chaos_readout` が head。**既存テーブルに変更が無い**ことを
`alembic downgrade -1` → `upgrade head` の往復で確認。

---

## 1. 導出コアの不変条件(DB 不要)

```bash
uv run --project probability pytest tests/unit/test_chaos_distribution.py -q
```

期待: [contracts/chaos-distribution.md](./contracts/chaos-distribution.md) の全テストが緑。特に:

- `test_events_need_order` — **`(1,9,10)` と `(10,1,9)`**(同じ S=20・違う着順)で
  `himo_are` / `total_collapse` が**異なる値**になる
- `test_total_collapse_lambda_invariant` — 生と補正で `total_collapse` が一致
- `test_triple_mass_is_one_operational_lambda` — 運用 λ で |Σ−1| ≤ 1e-9
- `test_adversarial_lambda_raises` — λ=1.5 × 極端 q で `ChaosInvariantError`
- `test_structural_zero_is_zero_not_none` — n=7 で `s_ge_20 == 0.0` + 理由、**n=8 では正の値**
- `test_uniform_eight_expected_s` — 一様 8 頭で `E[S] == 13.5`

---

## 2. λ とバンド境界の fit(オフライン 1 回)

```bash
uv run --project training training chaos-bands fit \
  --fit-from 2020-01-01 --fit-to 2023-12-31 --valid-from 2024-01-01 \
  --out-dir artifacts/chaos_bands
```

期待:

- `lambda2 ≈ 0.8304` / `lambda3 ≈ 0.7111`
- `quintile_edges ≈ [0.01957, 0.06593, 0.11181, 0.17031]`(**軸は `p_s_ge_20`**)
- `n_races_fit ≈ 13788`、除外理由別件数が出力される
- `numeric_stability_report` が緑
- `{artifact_digest}.json` として publish され、承認 manifest への追記が案内される

**再実行 / 上書き**: 同一 digest が既に存在すれば typed error(`O_EXCL`)。
**`--valid-from` を `fit_to` 以前にすると typed error**。
**注**: `valid_from` は捕捉対象日**以前**でなければ表示されない(統一ゲート
`target_date > fit_through` かつ `target_date >= valid_from`)。本 quickstart は
`--valid-from 2024-01-01` とすることで §3 の 2026-07-26 捕捉と §4 の表示が両立する。
`valid_from` より前のレースは **readout を書かず typed skip**(band/artifact_digest は non-null 必須のため)。

---

## 3. 凍結の捕捉と不変性

```bash
uv run --project live live capture-chaos --date 2026-07-26
```

期待: `chaos_snapshots` + `chaos_readouts` に**同一トランザクション**で行が入り、
`capture_strength` 別の内訳が出力される。

**捕捉規律の検証**(BLOCKER 修正点):

| ケース | 期待 |
|---|---|
| 結果が既に確定しているレース | 捕捉されない(rejected.result_settled) |
| 取得中に結果が確定した | 捕捉されない(取得後の再確認で弾く) |
| `post_time` 不明 | 捕捉されるが `capture_strength='weak'`(実測 2024 年は post_time 0% なので全て weak) |
| DB 既存値の読み取りのみ | `capture_strength='unknown'`(確認コホートから除外) |
| popularity に欠番/重複 | 捕捉されない(rejected.invalid_popularity_ranks) |
| 一部の馬にオッズ無し | 捕捉されない(rejected.partial_market_odds) |

**不変性(SC-003)**:

```bash
# 1) 凍結行から荒れ度を算出して控える
# 2) 当該レースの race_horses.odds / popularity を書き換える(検証用 DB でのみ)
# 3) 再度算出する
```

期待: **全数値がバイト一致**。一致しなければ実装が現在の DB を読んでいる(契約違反)。

**意味論のゴールデンケース(SC-008)**: 1着=1番人気・2着=17番人気・3着=18番人気のレースで、
現在 DB の popularity を書き換えた後に凍結行から算出して
`S=36` / `himo_are=true` / `total_collapse=false` になること。

---

## 4. API の純追加

```bash
curl -s localhost:8000/api/v1/races/<race_id>/predictions | jq '{
  chaos: .race_chaos, dispersion: .race_dispersion }'
```

期待:

- `race_chaos.status == "available"`、`band_axis == "p_s_ge_20"`
- `events` に `s_ge_20` / `himo_are` / `total_collapse`。各々 `adjusted_mass`(**非 null**)・
  `raw_mass`・`is_structural_zero`・`lambda_sensitive`
- `total_collapse.lambda_sensitive == false`
- `calibration_status == "provisional"`、`is_pseudo == true`
- `snapshot.seconds_to_post` が**数値**、`capture_strength` が入る
- **`race_dispersion` が本 feature 導入前とバイト一致**(SC-004)

**必ず確認する分岐**:

| ケース | 期待 |
|---|---|
| **予測 run が無いレース** | **`race_chaos` が返る**(API-6)。front も描画する(SC-009) |
| N ≤ 9 のレース | `himo_are.adjusted_mass == 0.0` かつ `is_structural_zero == true` |
| N = 3 のレース | `unavailable_reason="field_too_small"`(fit は N≥4 なので表示も N≥4・FR-029a) |
| 取消で popularity が飛ぶレース(max>N) | **捕捉・表示される**(1..N の順列は要求しない・実測 26.1%) |
| N = 8 のレース | `s_ge_20` は **0.0 ではなく正の値**(境界の実装ミス検出) |
| snapshot 無し | HTTP **200** で `status="unavailable"` / `unavailable_reason="no_snapshot"` |
| `target_date <= fit_through` | `unavailable_reason="out_of_validity_window"` |
| **2026 年のレース** | **表示される**(rev1 の逆転ゲートなら全滅していた。回帰テストで固定) |

---

## 5. 契約ドリフトと境界

```bash
cd front && pnpm test && ./scripts/check-openapi.sh
cd admin && pnpm test
uv run --project api pytest -q
```

期待:

- OpenAPI drift-check 緑、front / admin snapshot byte 一致
- **`app.openapi()` と両 committed snapshot の比較テストが緑**(API-9)
- API の AST / import-graph 境界テスト緑(**`live` 非 import**・全 path GET)
- `extra="forbid"` と明示 keyword マップの回帰テスト緑(075 の splat-null 再発防止)
- pseudo バッジ不変テスト緑

---

## 6. リーク境界

```bash
uv run --project features pytest tests/unit/test_feature084_leak_guard.py -q
```

期待: 表示軸名(`chaos_band` / `p_s_ge_20` / `himo_are` / `total_collapse` /
`expected_top3_popularity_sum`)が feature registry / `materialized_columns()` / model recipe に
現れない。`FEATURE_VERSION` 不変。

---

## 7. OOS 診断(SECONDARY)

```bash
uv run --project training training chaos-bands diagnose \
  --from 2024-01-01 --to 2026-12-31 --artifact <digest>
```

期待(discovery 数値の再現):

| band | S≥20 実現 | ヒモ荒れ 実現 | 総崩れ 実現 |
|---|---|---|---|
| ①`t3_calm` 揃う | ≈0.007 | ≈0.011 | ≈0.003 |
| ②`t3_mild` やや揃う | ≈0.036 | ≈0.076 | ≈0.016 |
| ③`t3_mid` 標準 | ≈0.070 | ≈0.122 | ≈0.029 |
| ④`t3_rough` やや崩れる | ≈0.132 | ≈0.154 | ≈0.065 |
| ⑤`t3_wild` 崩れやすい | ≈0.244 | ≈0.175 | ≈0.092 |

(すべて `p_s_ge_20` 軸の五分位・2024+ n=8,818 の実測値)

加えて:

- **N のみ**と **`g(H,N)`** のベースラインが併記される(実測 (H,N) = 0.7585)
- **頭数バケット内の AUC / proper score** が出る(N 9-11 で E[S] 0.768 vs H 0.710 等)
- reliability / Brier / log score が主表示、AUC は補助
- バンドの頭数平均が 9.8→16.1 と偏ることが**隠されず**表示される
- `s_ge_30` は **NO_DECISION**
- 先頭に「SECONDARY — 採否ゲートではない」「2024+ は discovery データ」の但し書き

---

## 8. 運用カバレッジ(US6 の go/no-go)

```bash
uv run --project training training chaos-bands coverage --from <d> --to <d>
```

期待: 捕捉カバレッジ率・`capture_strength` 別内訳・`seconds_to_post` 分布・**post_time 充足率**。事前登録閾値を満たさなければ
凍結・確認機構を後回しにする判断材料になる。

**前提の確認**: 2026-07-26(未実施)の 36 レースにオッズと人気が既に入り結果は 0 件
= 発走前オッズは日次で取り込めている。捕捉は新規スクレイピングでなく「上書き前の凍結」。
**post_time 充足率の実測**: 2023 年 0% / 2024 年 0% / 2025 年 22.9% / **2026 年 100%**
→ `confirmatory` コホートは 2026 年以降のみで成立する(CAP-10)。

---

## 9. 前向き検証(空で始まる)

```bash
uv run --project training training chaos-bands prospective-report --artifact <digest>
```

期待: 登録直後は **NO_DECISION**(`valid_from` 以降の `confirmatory` 行がまだ無い)。
これは正常。事前登録した最小陽性 100 に達するのは **S≥20 でおよそ 0.5-0.8 年**
(2025 実績 109 開催日 / 3,455 レース、クラスタ design effect 1.5-2.5 を見込む)。
`s_ge_30` は 6.9-11.5 年かかるため**昇格を阻害しない診断専用**であることが出力に明記される。
