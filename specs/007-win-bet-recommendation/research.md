# Research: 単勝 EV 推奨と疑似ROIバックテスト

Phase 0。NEEDS CLARIFICATION なし。codex second opinion を反映した設計判断を記録する。

## R1. 疑似評価(closing-oracle)の明示

- **Decision**: バックテストは `race_horses.odds`(確定単勝オッズ)を EV 入力と払戻の双方に使う closing-oracle
  簡略化であり、全レポート・監査・README・logic_version に **「疑似評価(pseudo evaluation)」** と明示する。
- **Rationale**: codex 最重要リスク — 確定オッズは賭け締切時に存在せず、これを「ROI」と呼ぶと実運用と乖離した
  楽観バイアスを記録に残す(憲法 V)。推定オッズ変換(未来レース用)は将来フィーチャー。
- **Alternatives considered**: 締切前推定オッズで評価 → 推定オッズ変換規則が未確定(P0)。本フィーチャーでは確定
  オッズ + 疑似明示に限定。

## R2. 母集団と確率の再正規化

- **Decision**: 母集団=`entry_status='started'`。生成時に取消・除外が判明した馬は win_prob を 0 にして、残存馬の
  win_prob を**再正規化(Σ=1)**してから EV を計算する。
- **Rationale**: 憲法 IV。`race_predictions.win_prob` は予測時の出走母集団で正規化済みだが、その後の除外で母集団が
  変わると確率整合が崩れるため再正規化が必要(codex R5/R4)。
- **Alternatives considered**: 予測時の win_prob をそのまま使う → 除外発生時に Σ≠1 で EV が歪む。

## R3. 疑似ROI 採点(勝ち/負け/DNF/取消/同着)

- **Decision**: flat stake。的中=`result_status='finished' かつ finish_order==1`、払戻=`stake×odds`、外れ=0。
  - DNF(出走・未完走/非1着)= 負け(払戻 0)。
  - 取消・除外(`entry_status!='started'`、race_results 無し)= 母集団から除外、ベット対象外、負けに数えない。
  - 同着 1 着 = 的中(確定オッズが同着控除済みである前提)。
- **Rationale**: codex R2 — 取消をベットに含めると分母が膨らみ的中率が不当に下がる。DNS と DNF を区別。
- **Alternatives considered**: 全出走馬を母集団に固定 → 取消混入でバイアス。

## R4. ROI baseline と成功基準

- **Decision**: ROI 専用 baseline を新設:
  - **FavoriteROIBaseline**: 各レースで人気1番(最低オッズ)を常に単勝で flat stake。
  - **UniformROIBaseline**: 各レースで全出走馬を単勝で均等に買う。
  EV 戦略と**同一レース集合・同一 stake**で比較。成功(必須)=両 baseline を回収率で上回る。`回収率>1.0` は控除率を
  超える展開候補の**参考バー**として別記録(必須でない)。
- **Rationale**: codex R3 — 確率品質 baseline(Feature 003 の market/uniform)は ROI 用途に流用不可。控除率
  (約 20–25%)下で `ROI>0` を合格条件にするのは不適。
- **Alternatives considered**: Feature 003 baseline 流用 → 目的(確率品質 vs 回収)が異なり誤り。

## R5. EV 計算と odds の扱い

- **Decision**: `EV = win_prob_renorm × odds`。`pseudo_odds = 1/win_prob_renorm`(モデル含意オッズ)、
  `pseudo_roi = win_prob_renorm × odds − 1`(意思決定時点の期待 ROI)。`market_odds_used = odds`、
  `is_estimated_odds=false`、`estimated_market_odds_used=null`。odds が null/`<=0`、win_prob=0 の馬は推奨しない
  (micro-fill しない)。
- **Rationale**: codex R6/R7。recommendations の pseudo_roi は意思決定時点の期待値、バックテストの回収率は実現値
  (払戻/賭金)で別物。両者を混同しない。
- **Alternatives considered**: MarketBaseline の micro-fill(1e-6)流用 → EV を歪めるため不可。

## R6. 全 EV>=閾値 を保存(1点に限定しない)

- **Decision**: 1 レースで EV>=閾値 の馬を**すべて**買い目として保存(ポートフォリオ)。既存 eval の operational
  simulator(1 レース最高 EV 1 頭)とは方針が異なる点を記録。
- **Rationale**: spec 確定。閾値で絞る設計の方が EV しきい値の効果を素直に評価できる。
- **Alternatives considered**: 1 レース 1 点(最高 EV)→ 将来オプションとして検討余地、本 MVP は閾値方式。

## R7. recommendations 契約と append-only

- **Decision**: `selection={horse_id, horse_number}`、`bet_type='win'`、上記 odds/pseudo フィールド、
  `logic_version` に EV 式・閾値・stake・除外ポリシー・版を埋め込む。保存は **append-only**(再生成は新しい
  recommendation 群、logic_version で区別。DB 制約ではなくアプリ規約)。
- **Rationale**: codex R7、憲法 V。監査で「どの式・閾値・オッズで出した買い目か」を後追いできる。
- **Alternatives considered**: 破壊的 upsert → 監査証跡喪失。

## R8. 予測の取得(serving 再利用)

- **Decision**: 推奨生成(US1)は既存 `prediction_run` の `race_predictions` を読む。バックテスト(US2)は
  `serving.load_serving_model` + `serving.predict_race` を **in-memory** で呼び、大量の prediction_runs を永続化せず
  予測を得る。features は `build_feature_matrix(end_date=対象日)` を期間で 1 度構築して再利用。
- **Rationale**: バックテストで毎レース prediction_runs を書くのは監査ノイズ。serving 純部品は session 非依存で再利用可。
- **Alternatives considered**: バックテストでも run_serving 永続化 → DB が膨れ、目的(集計指標)に不要。
