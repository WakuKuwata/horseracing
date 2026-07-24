# 081 Phase 0 — Pre-registration record (append-only)

**Feature**: 081 folklore-residual-probe | **Frozen**: 2026-07-24 | **Kind**: pre-registration record

このファイルは **append-only** です。凍結後に数値・定義を書き換えてはなりません（憲法 III）。
訂正が必要な場合は、書き換えではなく新しい pre-registration を別 version として追加します。

## 凍結物

| 項目 | 値 |
|---|---|
| gate-config | [`gate-config.json`](gate-config.json) |
| `gate_config_hash` | `c696cec79c9591ed62f72ac0e285eeb7493dedbb38a68c12eefb527ee733d21e` |
| short | `c696cec79c95` |
| `evaluation_contract_version` | `phase0-screening-v1` |
| `can_adopt` | **false**（この契約は ADOPT を出せない） |
| 候補数 | 8（PRE_ENTRY 6 / POST_WEIGHT 2） |
| 評価窓 | 2019-01-01 〜 2026-07-12（073 と同一の development evidence 窓） |
| bootstrap | `race_day_cluster_bootstrap_ci_v1`, B=2000, seed=20260724, α=0.05 |

実行時は必ず以下で縛ること。ハッシュ不一致は `assert_confirmatory` が型付きエラーで落とす。

```
--gate-config specs/081-folklore-residual-probe/gate-config.json \
--confirmatory --gate-config-hash c696cec79c9591ed62f72ac0e285eeb7493dedbb38a68c12eefb527ee733d21e
```

## 改竄検知の実証（2026-07-24）

`gate_config_hash` は `_` 接頭辞キー（コメント）を除外した正準ハッシュ。実測：

| 操作 | ハッシュ | 結果 |
|---|---|---|
| baseline | `c696cec79c95` | — |
| `_comment` を書き換え | `c696cec79c95` | 不変（説明文の修正は許容） |
| `point_le` を −0.001 → −0.0005 に緩める | `ab18f1217829` | **検知** |
| bootstrap seed を引き直す | `1e3600d1d2a7` | **検知** |
| 候補の閾値を 70日 → 60日 に変える | `31907011ea5d` | **検知** |

## 意思決定の記録

### なぜ閾値が緩いか（ユーザー決定 2026-07-24）

`promotion_to_phase1` は `point_le = -0.001` / `ci_upper_le = 0.005` と意図的に緩い。根拠：

1. この契約は `can_adopt=false` の screening 専用。緩さは「Phase 1 の工数」を損なうだけで、
   **誤採用には決してつながらない**。
2. 2026-07-24 に実測したこの窓の開催日クラスタ SE は **0.0014〜0.0022**
   （073 paired-eval の CI 半値幅 0.004334 / 1.96 = 0.00221、070 の棄却バンドルから 0.00139）。
   80% 検出力の MDE は **0.0039〜0.0062**。過去採用実績（061 −0.00095、059 −0.00018、
   069 F02 −0.0057）の大半がこれを下回る＝**厳しい screen を置くと、性質を把握する前に
   全候補が消える**。
3. したがって Phase 0 は「採否」ではなく「どの軸にモデル残差が残っているかの地図作り」に徹する。

**この緩さを Phase 1 に持ち込んではならない**（`phase1_handoff` に明記）。

### 残り3点の決定

| 論点 | 決定 | 理由 |
|---|---|---|
| 多重比較 | screen 自体は無補正、Holm 調整値を診断として併記 | screening_only なので補正は不要だが、選択負荷を可視化して Phase 1 の事前登録に持ち越すため |
| POST_WEIGHT 因子 | 含めるが `timing_separation` で PRE_ENTRY と**プールしない** | 馬体重は発表が直前で、発表前 serving 経路では使えない（codex 指摘・registry の `AvailabilityTiming`） |
| 2026 subgroup | critical subgroup を**登録しない**（診断報告のみ） | 2026 が窓内 58 開催日で構造的に underpowered。2026-07-24 の 073 実行で全 critical subgroup が NO_DECISION になることを実証済み。screen をこれで縛ると信号の有無に関係なく全候補 NO_DECISION になる |

### 除外した候補と理由

| 候補 | 除外理由 |
|---|---|
| 距離短縮/延長 | 現モデルが既に中和済み（実測 model 0.998 / market 1.026）。033 の `dist_extension`/`dist_shortening` が存在 |
| 斤量変化 | `carried_weight_change` が正確な差分列として既存。再表現の利得が消えている（codex） |

## 前提（実行時に再確認すること）

- ベースは **`lgbm-065`**。ただし ordered feature list / drop list / calibration recipe は
  実行時に DB の active から再 attestation する（codex #2：073/074 は historical active を
  lgbm-063 と記録しており、モデル名で p⊥q を主張してはならない）。
- OOF は recipe-faithful strict-past（booster・TE・内部校正・as-of 特徴のすべてが対象レース日より前）。
- `2008-2026 は development evidence`（073 US4）。真の confirmatory は DORMANT の
  prospective holdout であり、本 feature では一切消費しない。

## 関連

- [073 evaluation contract v2](../073-eval-contract-correctness/gate-config.json)（Phase 1 の採否ゲート）
- [068 calib-split gate](../068-evaluation-contract-calibration/gate-config.json)（2026-07-24 実行、B/C-D とも NO_DECISION）
- 前例: 070（点推定 favorable・CI ゼロ跨ぎで 3 バンドル REJECT）
