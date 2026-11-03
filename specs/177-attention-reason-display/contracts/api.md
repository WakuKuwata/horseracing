# Contract: `GET /api/v1/races/{race_id}/attention` への純追加(177)

path・メソッド・既存項目は不変。`AttentionHorse` に `reason` を 1 項目追加する(required・nullable・既定値なし)。OpenAPI は `components/schemas` の追加のみ(`AttentionReason` / `AttentionReasonUnavailable` / `EvReasonComponent` / `OddsBandReasonComponent` / `GapReasonComponent` / `ReasonValue` / `AlsoApplicable`)。既存 property の名前・required・nullable は不変(`git diff --numstat front/openapi.json` の削除行 0)。

## 応答の例(抜粋・S1 チップ・現在値あり)

```json
{
  "horse_id": "2021104567",
  "horse_number": 7,
  "applicable": ["S1", "S2", "S3", "S4", "S5"],
  "chip_rule": "S1",
  "chip_s2": true,
  "chip_now": "matches",
  "judged": { "ens_expected_return": 1.31, "odds": 32.5, "computed_at": "2026-11-08T00:12:00Z", "...": "..." },
  "current": { "ens_expected_return": 1.246, "odds": 30.8, "computed_at": "2026-11-08T04:40:00Z", "...": "..." },
  "reason": {
    "status": "available",
    "rule_id": "S1",
    "rule_set_version": "attention-S1-S5-v1",
    "components": [
      { "kind": "ens_expected_return", "ev_gt": 1.2, "is_pseudo": true,
        "judged": { "value": 1.31, "status": "met" },
        "current": { "value": 1.246, "status": "met" } },
      { "kind": "win_odds", "lo": 20.0, "hi": 40.0,
        "judged": { "value": 32.5, "status": "met" },
        "current": { "value": 30.8, "status": "met" } },
      { "kind": "days_since_last", "lo": 14, "hi": 112,
        "judged": { "value": 41.0, "status": "met" },
        "current": { "value": 41.0, "status": "met" }, "current_source": "pick_frozen" }
    ],
    "also_applicable": [
      { "rule_id": "S2", "note_ev_gt": 1.3 },
      { "rule_id": "S3", "note_ev_gt": null },
      { "rule_id": "S4", "note_ev_gt": null }
    ]
  }
}
```

## 規則

| 状況 | `reason` | 部品の `current` | `chip_now` |
|---|---|---|---|
| チップなし(該当なし・S5 のみ・全部 void) | `null` | — | `null` |
| チップあり・`current` あり・全部品 met | あり | あり | `matches` |
| チップあり・`current` あり・odds が帯の外(45.0) | あり | odds 部品 `not_met` | `no_longer` |
| チップあり・`current` あり・pick の間隔が null(S1 で理論上存在しないが) | あり | 間隔部品 `missing` | `no_longer` |
| チップあり・`current` なし(再計算待ち・オッズ欠落・出走馬変更) | あり | 全部品 `null` | `unknown` |
| S3 チップ | あり(部品は EV の 1 つ) | EV のみ | 規則どおり |
| 取消馬(pick が void 済み) | `null`(チップなし) | — | `null` |
| 取消馬(void 未追記・API はまだ pick として返す) | あり(front は `cancelled` で描かない) | 規則どおり | 規則どおり |
| チップあり・builder の整合検査(V2〜V10)に落ちた | `{"status":"unavailable","reason":"inconsistent"}` + WARNING ログ | — | 既存どおり(触らない) |
| チップなしの馬の現在値が条件を満たすようになった | `null` のまま(チップは初回計算で固定・新規に付かない) | — | `null` |

- `judged.value` は `judged` snapshot の同じ項目と一致(V3)。`current.value` も同様(V5)。
- `also_applicable` は **判断時点の該当**(`applicable`)のうち対照でなくチップでない条件を rank 順・重複なしに。`note_ev_gt` はその条件がチップの条件と `uses_ensemble`・`odds_band`・`gap_days` が等しく `ev_gt` だけ違うときのみ。
- `reason` は `uses_ensemble=True` の条件にだけ作る(チップは対照にならないので常に該当。単 seed の値で説明する部品は無い)。
- 応答の整合(data-model §2 の V1〜V10)は builder の検査と応答モデルの validator で強制する。builder で違反を見つけたら `unavailable` + WARNING ログ(500 にしない・黙って `null` にしない)。validator は直接構築された矛盾を拒む(テスト用の契約)。

## 変わらないもの

- `GET /api/v1/attention-rules`・`GET /api/v1/attention/day` の応答。
- `EvSnapshot` / `AttentionLevels` / `StageDetail` / `AttentionAvailable` / `AttentionUnavailable` の項目。
- path 一覧(`front/src/api/openapi.test.ts`・`api/tests/integration/test_openapi_contract.py`)。
