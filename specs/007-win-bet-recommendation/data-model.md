# Data Model: 単勝 EV 推奨と疑似ROIバックテスト

新テーブルは作らない。既存を読み、`recommendations` に書く。バックテストはレポート(非永続)を返す。

## 入力

| 用途 | 取得元 |
|---|---|
| win 確率 | `race_predictions.win_prob`(Feature 006、出走母集団で正規化済み) |
| オッズ | `race_horses.odds`(確定単勝、Feature 002 取込) |
| 母集団 | `race_horses.entry_status='started'`(取消・除外を除外) |
| 採点(疑似ROIのみ) | `race_results`(result_status/finish_order) |

## 論理エンティティ

- **Bet**: 1 つの単勝買い目。horse_id・horse_number・win_prob(再正規化後)・odds・ev・stake。
- **EV 戦略**: 母集団除外→再正規化→`EV=win_prob×odds`→`EV>=閾値` を Bet 集合に。閾値・stake 設定可能。
- **ROI baseline**: `FavoriteROIBaseline`(人気1番=最低 odds を常時単勝)/ `UniformROIBaseline`(全出走馬均等)。
- **疑似ROIレポート**: 戦略ごとの 賭金合計・払戻合計・回収率・的中率・見送り率・最大DD・最大連敗(疑似評価)。
- **Recommendation**(`recommendations`): 永続化される単勝買い目(下記)。

## EV / 推奨生成の不変条件

- **INV-B1**: 母集団=started。取消・除外は除外し、残存馬の win_prob を Σ=1 に再正規化してから EV を計算(IV)。
- **INV-B2**: 買い目選択は `win_prob×odds` のみ。`race_results`(着順)を一切参照しない(リーク境界、II)。
- **INV-B3**: odds が null/`<=0`、win_prob=0 の馬は推奨しない(micro-fill しない)。
- **INV-B4**: `EV>=閾値` の馬を**すべて**保存(1 レース複数可)。
- **INV-B5**: 保存は append-only。同一レース再生成は新しい recommendation 群(logic_version で区別)。

## 疑似ROI 採点(R3)

```
stake = flat（既定固定額）
hit(bet)     = race_results.result_status=='finished' and finish_order==1
payout(bet)  = stake * odds   if hit else 0
bet_pnl      = payout - stake
取消・除外    = 母集団から除外（ベットしない、負けにも数えない）
DNF          = 負け（payout 0）
同着 1 着     = hit（確定オッズは同着控除済みとみなす）

回収率(recovery_rate) = Σ payout / Σ stake          （疑似評価）
的中率(hit_rate)      = #hit bets / #bets
見送り率(skip_rate)   = #races with no bet / #races
最大DD / 最大連敗      = 「実際に賭けたレースのみ」の系列で計算（見送りは含めない, R3/FR-009）
```

## recommendations 書き込み(既存スキーマ)

| 列 | 値 |
|---|---|
| recommendation_id | uuid(自動) |
| prediction_run_id | 入力の prediction_run(FK) |
| race_id | 対象レース(FK) |
| bet_type | `'win'`(BetType.WIN) |
| selection | `{"horse_id": ..., "horse_number": ...}`(jsonb) |
| market_odds_used | `odds` |
| estimated_market_odds_used | `null` |
| is_estimated_odds | `false` |
| pseudo_odds | `1 / win_prob_renorm`(モデル含意オッズ) |
| pseudo_roi | `win_prob_renorm × odds − 1`(意思決定時点の期待 ROI) |
| logic_version | EV 式・閾値・stake・除外ポリシー・版を含む文字列 |
| computed_at | now |

## logic_version

```
logic_version = f"ev=win_prob*odds;thr={threshold};stake={stake};excl=scratch+nullodds+zeroprob;v={BETTING_LOGIC_VERSION}"
```

## スコープ外(将来)

- 複勝・馬連・三連複(結合確率エンジン、憲法 P0)。
- 推定市場オッズ変換(未来レース用、確定オッズに依存しない疑似ROI)。
- Kelly 等の資金管理(本 MVP は flat stake)。
