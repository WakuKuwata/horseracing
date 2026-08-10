# Quickstart: 予測根拠の実効寄与化(レース内センタリング)

**Feature**: 089-explanation-race-centering

前提: ローカルスタック起動済み(`scripts/stack.sh`・DB は localhost:15432)。

## 1. 単体テスト(計算契約)

```bash
uv run --project training pytest training/tests/unit/test_explanation.py -q
```

期待: v1 バイト同一(INV-E5)・センタリング手計算 fixture・レース内総和 0(INV-E4・
生成時検査)・raw 照合つき生加法性(INV-E1 強化)・v2 加法性(INV-E4b)・全馬同値特徴の
候補除外(全馬 NaN / 全馬同値・混在は非除外)・K 未満許容・タイ決定性・1 行バッチ
items 空・非有限混入でレース atomic None、すべて緑。

## 2. serving 回帰(予測バイト不変)

```bash
uv run --project serving pytest serving/tests -q
```

期待: predict_race の確率(win/top2/top3)・snapshots が変更前とバイト一致(INV-E2)、
非 offset の race-softmax モデルで v2・binary モデルで v1・**market-offset モデルで v1**
(offset は pred_contrib が説明できないため v2 対象外)。

## 3. 実 DB E2E(新馬戦で実効表示を確認)

serving CLI は console script ではなくモジュール実行(`python -m horseracing_serving`)で、
`weights_uri` が相対パスのため **cwd=serving/** が必要(ops/runner.py と同じ規約)。

```bash
cd serving && DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing \
  uv run --project . python -m horseracing_serving predict-backfill \
  --from 2026-08-02 --to 2026-08-02 --force \
  --database-url postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing
```

単一レースで足りる場合(検証はこちらが速い・同じ `predict_race` 経路):

```bash
cd serving && uv run --project . python -m horseracing_serving predict \
  --race-id 202604020403 \
  --database-url postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing
```

確認(SQL または API):
1. 再生成 run の explanation が `method_version=2` で items に `contribution_centered`、
   ルートに `score_centered` / `other_contribution_centered` /
   `centering_population_size` を持つ。
2. 新馬戦レースの top-5 に value が全馬 NaN の特徴(prev_finish / days_since_last /
   class_transition)が**1 件も現れない**(SC-001・候補除外の構造保証)。
3. 保存値から v2 加法性: score_centered ≈ Σitems.contribution_centered +
   other_contribution_centered(SC-003b)。
   ※特徴ごとのレース内総和 ≈ 0 は保存形式(top-K 切り詰め)から検証不能のため、
   手順 1 の生成時単体テストが正本(SC-003c)。
4. 確率のバイト一致は「同一入力での説明 on/off 比較」(手順 2 の回帰テスト)が正本
   (SC-002)。再生成 run と過去 run の比較はデータ訂正等で差が出うるため参考に留める。

## 4. API 契約

```bash
uv run --project api pytest api/tests -q
cd front && bash scripts/check-openapi.sh && node_modules/.bin/vitest run
cd ../admin && bash scripts/check-openapi.sh && node_modules/.bin/vitest run
```

期待: openapi additive 差分のみ・snapshot/型同期・drift 緑。v1 行は
`contribution_centered=null` で従来どおり返る。

**既知の先行失敗(089 と無関係)**: `api/tests/perf/test_chaos_readout_p95.py::
test_chaos_readout_full_path_p95` は suite 全体で実行すると失敗する(単独実行では緑)。
089 の変更を戻した pristine な状態でも同じ失敗を再現済み=先行の不具合。

worktree で front/admin を検証する場合は先に `pnpm install --frozen-lockfile` が必要
(worktree には node_modules が引き継がれない)。

## 5. front 表示

- レース詳細で v2 行(`method_version === 2` 厳密分岐): タイトル「レース内でのスコア
  寄与」・「同一レース内の平均に対する、レース内正規化前の相対スコア寄与」注記・
  「その他」行は `other_contribution_centered`・バーは centered 値。
- items 空(1 頭レース): 「このレースでは比較できる差がありません」。
- v1 行(未再生成の過去レース): 従来表示のまま(退行なし)。
- 未提供・未知 method_version・centered 欠落の v2: 「未提供」系表示(生値フォールバック
  なし)。

## 成功条件の対応

| SC | 検証手段 |
|---|---|
| SC-001 | 手順 3-2(全馬同値特徴の top-5 出現ゼロ=構造保証+実測) |
| SC-002 | 手順 2(同一入力での説明 on/off バイト一致回帰) |
| SC-003 | 手順 1(INV-E1 強化/E4 生成時/E4b)+ 3-3(保存値検証) |
| SC-004 | 手順 5(v1/v2 厳密分岐表示) |
| SC-005 | 手順 4(additive・drift 緑) |
| SC-006 | 手順 3-1(--force 再生成で v2 保存) |
| SC-007 | 手順 3 で経験馬レースも確認(実値差の特徴が残る) |
