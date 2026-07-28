# Contract: CLI (rev2)

新規サブコマンドのみ。既存コマンドの引数・既定値・出力を変更しない。

---

## 1. `live capture-chaos` — 凍結の唯一の書き手

```bash
uv run --project live live capture-chaos --race-id 202602011206
uv run --project live live capture-chaos --date 2026-07-26          # その日の未確定レース
```

| 引数 | 内容 |
|---|---|
| `--race-id` / `--date` | 排他・どちらか必須 |
| `--min-seconds-to-post` | 既定 0。これ未満なら捕捉しない |

**規約**: [storage-and-artifact.md](./storage-and-artifact.md) §2 の CAP-1..10 に従う。

- キャッシュを使わない新規取得・取得前後の result-pending 確認・`captured_at < post_time`
- `chaos_snapshots` と `chaos_readouts` を**同一トランザクション**で書く(CAP-8)
- `--source` の自称を信用せず、取得アダプタが返した出所を記録する(CAP-6)
- popularity の重複・欠損 / 部分オッズ / n<4 / 結果確定済み は **typed skip**(理由別件数を出力)。
  例外で落とさず他レースの捕捉を止めない
- 出力: `captured` / `skipped` / 理由別 `rejected` / `capture_strength` 別内訳

**operator 手順(CAP-9)**: 憲法「初期は全て手動実行」に従い自動スケジューラは導入しない。
**1 日 1 コマンド**を運用手順として定める:

- 推奨実行時点: 開催日の各レース **T−30 分**を目安(`--min-seconds-to-post 600` 等で下限を設ける)
- 最大 staleness: 同一レースの再実行は旧行を void にして置換(1 レース 1 有効行・SNAP-4)
- 網羅率目標: US6 のカバレッジ閾値。未達なら表示を縮小版に落とす(FR-035)
- **`confirmatory` は `post_time` 既知のレースのみ**(CAP-10。実測 2026 年 100% / 2025 年 22.9%)

手動実行のみに依存すると証跡が集まらず表示も常に `no_snapshot` になるため、
カバレッジ報告(US6)を運用のゲートにする。

---

## 2. `training chaos-bands fit`

```bash
uv run --project training training chaos-bands fit \
  --fit-from 2020-01-01 --fit-to 2023-12-31 --valid-from 2024-01-01 \
  --out-dir artifacts/chaos_bands
```

**規約**:

- λ は `eval/stage_discount.py` の条件付き NLL + golden-section を**市場 q** に適用して fit
  (049 の数学を再利用・049 の artifact は読まない)
- バンド境界は同じ窓の **`P(S≥20)` の五分位**(E[S] ではない)。包含規則 `p <= edge` → 下側バンド
- `eligibility_predicate` / `operational_lambda_envelope` / `field_size_reference_quantiles` を
  payload に含める(FR-029a / FR-029b / FR-018a)
- **`valid_from > fit_through` を必須**とし、満たさなければ typed error
- `artifact_digest` を payload 全体から計算し**その名前で publish**(`O_EXCL`)。
  既存があれば typed error(上書き禁止)
- **数値安定性ゲート**(代表 + 敵対フィールドで Σ=1)を通し `numeric_stability_report` に記録
- `calibration_status` は `provisional` 固定
- fit に使ったレース集合の除外理由別件数を出力
- 出力の最後に「承認 manifest に digest を追記してください」と案内する

**配置**: 数学は `eval`(λ の条件付き NLL は q だけで完結)、**オーケストレーションは `training`**
(E[S] / P(S≥20) の算出に `probability` が要り、`probability → eval` の一方向依存により
`eval` からは呼べないため)。

---

## 3. `training chaos-bands diagnose`

OOS 診断(**SECONDARY** — 採否ゲートに使わない)。

```bash
uv run --project training training chaos-bands diagnose \
  --from 2024-01-01 --to 2026-12-31 --artifact <digest> [--persist]
```

**規約**:

- `diagnose_from > artifact.fit_to` を assert
- `--export-fixture` で SC-008 用の凍結 fixture(適格レースの popularity / オッズ / 1-3着のみ +
  SHA-256)を出力する
- 出力: バンド別の n / S 中央値 / 各事象の実現率と予測率 / **reliability・Brier・log score**、
  開催日クラスタ bootstrap CI(seed 固定)、**頭数別**と **capture horizon 別**の内訳
- **AUC 単独で判断しない**
- **N のみのベースライン**と **`g(H, N)` ベースライン**を併記する
  (「エントロピーに同じ情報を与えたら」の公平な比較。実測 (H,N) = 0.7585)
- 頭数バケット内の AUC / proper score と、対比較差の開催日クラスタ CI を出す
- 最小陽性数未満は **NO_DECISION**
- pointwise CI に「多重比較未調整」ラベル
- `--persist` は 054 の `diagnostic_runs`(kind=`chaos_bands`)へ append-only 保存(**転記のみ**)
- **fit と diagnose を分離**し、このコマンドから境界を切り直せてはならない

---

## 4. `training chaos-bands prospective-report`

US5 の前向き検証。

```bash
uv run --project training training chaos-bands prospective-report --artifact <digest>
```

**規約**:

- 入力は `chaos_readouts` と、それに紐づく `chaos_snapshots` の凍結順位のみ。
  **S は凍結順位から算出**し、現在の `race_horses.popularity` を使ってはならない
- `capture_strength='confirmatory'` の行のみを確認コホートに入れる
- `valid_from` より前は除外(discovery の混入防止)
- **1 レース 1 行**(`status='active'`)。within-race 多点捕捉は SNAP-4 で禁止
- 昇格判定: **`p_s_ge_20` のみが支配**。`himo_are` は副次、`total_collapse` は λ 非適用のため
  対象外、`s_ge_30` は診断専用で昇格を阻害しない
- 最小陽性 100 / 最小開催日 60 / 較正の許容幅 / 多重比較方針 / **最終判定日**を artifact から読む
- 判定日までに満たさなければ **NO_DECISION** を返し、「主枠から撤去せよ」と明示する
- **捕捉カバレッジ**と除外レースの特性を必ず出力(選択バイアス検知)

---

## 5. `training chaos-bands coverage` — US6 の go/no-go パイロット

```bash
uv run --project training training chaos-bands coverage --from <d> --to <d>
```

実際の開催日で捕捉カバレッジ率・`seconds_to_post` 分布・**post_time 充足率**を出す。
事前登録した閾値を満たさなければ、凍結・確認機構を後回しにし
「監査されていない現在オッズ」ラベルの縮小版で出す判断材料にする。
