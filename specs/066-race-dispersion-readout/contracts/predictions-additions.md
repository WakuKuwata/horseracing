# API Contract: predictions 応答への純追加(race dispersion & divergence)

**方式**: 新エンドポイントを作らず、既存 `GET /api/v1/races/{race_id}/predictions`(021/040 が p/q/divergence を返す応答)に **2 つの nullable オブジェクトを純追加**する。スキーマ変更ゼロ・GET-only・OpenAPI 純追加・drift-check 緑。

## 追加フィールド(predictions 応答トップレベル)

```jsonc
{
  // ... 既存 021/040 フィールド(prediction_run_id, model_version, entries[], selection[] 等)は不変 ...

  "race_dispersion": {                    // 軸A(nullable。応答に純追加)
    "available": true,
    "unavailable_reason": null,           // "no_market_odds" | "partial_market_odds" | null
    "band": "somewhat_open",              // "firm"|"somewhat_firm"|"standard"|"somewhat_open"|"open" | null
    "normalized_entropy": 0.842,
    "favorite_win_prob": 0.31,
    "top3_cumulative": 0.68,
    "model_delta": {                      // 校正済み p 由来との差分のみ。canonical_consistent=false で null
      "normalized_entropy_delta": 0.021,
      "direction": "model_more_open"      // "model_more_open"|"model_more_firm"|"similar"
    },
    "odds_as_of": "2026-07-05T09:30:00Z",
    "odds_source": "final",              // 021 と同型 = "final" | "prerace"(closing-leaning=final を開示)。provenance 名(netkeiba 等)は入れない
    "is_pseudo": true,                    // market-derived 表示 → pseudo/source バッジ必須
    "boundary_version": "dispbands-v1"    // 境界 artifact 不在時は null
  },

  "race_divergence": {                    // 軸B(nullable。応答に純追加)
    "available": true,
    "summary": "本命(1番人気)をモデルは低評価",   // 中立文言 | null
    "favorite_direction": "model_lower",  // "model_higher"|"model_lower"|"similar" | null
    "underrated_longshots": [             // 事実リスト(「買い」ではない)
      { "horse_number": 12, "popularity_rank": 8, "p": 0.09, "q": 0.03 }
    ],
    "rank_agreement": 0.67,               // top3 集合一致率(model top3 と market top3 の重なり/3)。Kendall τ は不採用 | null
    "model_version": "lgbm-061"           // 057: どの選択モデルの p か
  }
}
```

## 契約ルール

1. **GET-only**: 本 feature は書込エンドポイントを追加しない。全 path GET のテストを維持。
2. **純追加**: 既存フィールド(per-horse `divergence`/`market_win_prob`/`canonical_consistent`/`odds_as_of` 等)は不変。既存 `divergence_band` を改変しない。
3. **null 契約**: `race_dispersion.available=false` の時 band/数値/model_delta は null(p フォールバックしない)。`race_divergence.available=false`(canonical_consistent=false or q欠損)で summary/direction/longshots/agreement は null。
4. **pseudo/source**: `race_dispersion.is_pseudo=true` かつ odds_source/odds_as_of を surface。front は pseudo/source バッジ必須・closing-leaning 可能性を開示。
5. **数値安全**: 全 number は nullable、front の中央 formatNum(→ `--`/未提供)で描画(015 規律)。
6. **OpenAPI**: 純追加のみ。`front/openapi.json` と `admin/openapi.json` を再生成し drift-check(`front/scripts/check-openapi.sh`)緑。生成型 `schema.d.ts` を commit。
7. **betting/training 非 import**: api は既存境界テスト(import-graph)を維持し betting/training を import しない。

## CLI Contract(training)

```
training dispersion-bands \
  --fit-from <date> --fit-to <date> \      # 凍結窓(結果非参照で境界フィット)
  [--diagnose-from <date> --diagnose-to <date>] \  # US3: OOS realized-chaos 診断
  [--field-buckets global|le8/9-13/ge14]   # v1=global(既定)、v2=バケット(トークン表記は data-model E3 と一致)
```

- 境界フィット出力 = DispersionBoundary artifact(metric/窓/as-of/version/edges)。
- `--diagnose-*` 指定時のみ DispersionBandDiagnostic(バンド別 n/本命敗北率/高配当率/CI/separated)を表示。
- **バンドは採否ゲート・閾値調整に使わない**(SECONDARY・047 規律)。`--persist`(diagnostic_runs 流用)は deferred。
