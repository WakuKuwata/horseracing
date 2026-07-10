# Quickstart: race dispersion & p/q divergence readout の検証

実 DB E2E で計器が正しく・正直に動くことを確認する手順。実装コードは含めない(tasks.md / 実装フェーズ)。

## 前提

- ローカル製品スタック([[local-db-setup]]): Postgres(docker)+ API(:8000)+ 予測済みレース。
- 対象レースに active モデルの prediction_run と win odds があること(無ければ `serving predict-backfill` / scrape で埋める)。

## 1. 境界フィット(凍結窓・結果非参照)

```
uv run --project training training dispersion-bands --fit-from 2020-01-01 --fit-to 2023-12-31
```

**期待**: DispersionBoundary artifact(metric=normalized_entropy・fit_window・as_of・version=dispbands-v1・quintile_edges 4値・n_races_fit)が出力される。edges は正規化エントロピー分布の5分位で、**結果(荒れたか)を一切参照していない**こと(ログに勝敗集計が現れない)。

## 2. 軸A の API 応答(started 全頭にオッズ)

```
curl -s localhost:8000/api/v1/races/<race_id>/predictions | jq '.race_dispersion'
```

**期待**:
- `available=true`、`band` が 5段のいずれか、`normalized_entropy`/`favorite_win_prob`/`top3_cumulative` が生数値で入る。
- `model_delta.direction` が校正済み p 由来との差分として入る(生 p 単独の集中度は応答に無い)。
- `is_pseudo=true`・`odds_as_of`/`odds_source` が入る・`boundary_version="dispbands-v1"`。

## 3. 軸A unavailable(q 欠損)

オッズ欠損 or 部分欠損レースで:

```
curl -s localhost:8000/api/v1/races/<race_no_odds>/predictions | jq '.race_dispersion'
```

**期待**: `available=false`・`unavailable_reason` が `no_market_odds`/`partial_market_odds`・band/数値/model_delta は全て null。**p 由来へフォールバックしていない**(SC-002)。

## 4. 軸B の3層

```
curl -s localhost:8000/api/v1/races/<race_id>/predictions | jq '{div: .race_divergence, per_horse: [.entries[].divergence]}'
```

**期待**:
- `race_divergence.summary` が中立文言、`favorite_direction`、`underrated_longshots`(事実リスト)、`rank_agreement`、`model_version`。
- per-horse の既存 `divergence_band` が**未変更**で出る(040 無改変)。
- `canonical_consistent=false` のレースでは `race_divergence.available=false`(SC-003)。

## 5. US3 診断(SECONDARY・OOS)

```
uv run --project training training dispersion-bands \
  --fit-from 2020-01-01 --fit-to 2023-12-31 \
  --diagnose-from 2024-01-01 --diagnose-to 2024-12-31
```

**期待**: バンド別 n/本命敗北率/高配当率/CI/separated が出る。fit 窓内レースが OOS に混ざらない。隣接バンドが CI で区別不能なら「有意差なし」と開示し、**境界を再フィット・併合しない**(SC-006)。dead heat/void の扱いが事前定義済み。

## 6. front 表示規律

```
cd front && pnpm test        # 不変テスト
cd front && pnpm dev         # 目視: RaceDispersionPanel / RaceDivergenceSummary
```

**期待**:
- 損益色・妙味/危険/edge/value 語・荒れ度/乖離ソートが無い(SC-007)。
- q 集計に pseudo/source バッジ・closing-leaning 開示。
- 軸A unavailable/軸B suppressed が正直な空状態で出る。
- バンド横に生数値併記(偽精度緩和)。

## 7. リーク境界・契約

```
cd features && uv run pytest -k leak_guard          # display-axis token が registry/materialized に無い
cd api && uv run pytest -k "boundary or import"      # api が betting/training 非 import・GET-only
cd front && bash scripts/check-openapi.sh            # OpenAPI 純追加 drift-check 緑
```

**期待**:
- 表示軸の計算を変えても decision-support 経路の選択 p がバイト不変(SC-004)。
- 「全 odds 変更が全モデル不変」は主張しない(060 market-offset があるため)。主張は「新 display 集計が feature/training に入らない」に限定。
- OpenAPI 純追加・drift-check 緑(SC-005)。
