# Feature Specification: margin-aware 教師信号(PL top-k ステージ損失の着差減衰)

**Feature Branch**: `099-margin-teacher-signal`

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "margin-aware 教師信号(PL top-k のステージ損失を着差で減衰)の本実装と事前登録採用ゲート"

## 背景(なぜ今この feature か)

現行の PL top-3 教師信号は、ハナ差の 2 着と 10 馬身差の 2 着に同じ重みを与える。実測
(≤2023・着順ステージ 1..3 の次着との時計差)では中央値 0.1〜0.2 秒、**22〜25% が 0.0 秒**
(計時解像度 0.1s 未満の接戦)= 教師信号の約 1/4 が実質コイントスの順位に満額の重みを持つ。
着差でステージ損失を減衰すれば、順位ノイズから能力差を分離できる。

kill-test spike(2026-08-24・`scripts/margin_teacher_spike.py` c823b73・証跡
`evidence/margin-teacher-spike-*.json` a05a643)は **GO**: rounds=300・3 fold
(2024/2025/2026)で V1 pooled winner NLL **−0.00337**(校正前 −0.00442)・**全 fold 負**。
教師信号・目的関数の系統は 039(採用)→ 042(採用)→ arm E(−0.0128)と全勝しており、
特徴軸が実測で枯れた後も効き続けた唯一の鉱脈である。

**spike で凍結済み・本 feature で再検討しない事項**:

- 変調形 **V1** = ステージ 2,3 のみ `g(m) = clip(m / 0.2s, 0.25, 1.0)` で減衰。勝者ステージ
  不変・増幅なし・次の完走馬なし/時計欠損はスケール 1.0(中立)。M0=0.2s / GMIN=0.25 は
  ≤2023 分布から事前選択済み(spike 窓を見ていない)
- **V2(全ステージ変調)は spike で棄却済み**(2025 fold で +0.0010 に符号反転 = 097 の
  REJECT 症状と同型)。ゲートで復活させない
- spike run 1 の既知バグ: margin 取得 SQL の `WHERE finish_order <= 3` が window 関数の
  前に効き、3 着の「次の完走馬」が消えてステージ 3 が全レース中立になった(ステージ別
  スケール平均の印字で発覚)。本実装はこの形を構造的に再発させないこと

## User Scenarios & Testing *(mandatory)*

### User Story 1 - production の margin-aware 教師信号(Priority: P1)

モデル開発者として、`pl_topk` の教師信号を着差で減衰した学習を production の学習経路
(recipe → predictor → objective)から再現可能に実行できる。OFF(既定)は現行と完全に
同一で、既存モデル・既存レシピには 1 バイトの影響もない。

**Why this priority**: ゲート(US2)はこの実装の上でしか回せない。また OFF 時の完全不変が
崩れると、既存の全モデル・全 verdict の再現性が壊れる(017 型の事故)。

**Independent Test**: 単体テストのみで完結 — OFF 時に現行 objective と勾配ビット一致、
ON 時にステージ限定で勾配が変わる、レシピ hash が OFF で不変・ON で変わる、を機械で固定
できる。実 DB 不要。

**Acceptance Scenarios**:

1. **Given** stage_scales 未指定(既定)、**When** 現行と同一データで学習勾配を計算、
   **Then** 現行 `pl_topk_objective` と勾配・ヘシアンが**ビット一致**する
2. **Given** V1 の変調を有効化、**When** 実データ形状で margin スケールを算出、**Then**
   ステージ 2 とステージ 3 の**両方**のスケール平均が 1.0 を大きく下回る(≈0.66〜0.69。
   run 1 のバグ = ステージ 3 平均 ≈1.0 を構造的に検出する)
3. **Given** margin-aware ON のレシピ、**When** recipe_hash を計算、**Then** 既存レシピと
   異なる hash になる(モデル同一性)。OFF のレシピは既存 hash と**不変**
4. **Given** 学習完了、**When** fit 情報を読む、**Then** ステージ別スケール平均・変調
   レース数などの分布統計が記録されている(「実際に変調されたか」が事後検証可能)

---

### User Story 2 - 事前登録採用ゲート(Priority: P2)

モデル開発者として、margin-aware V1 を現行教師信号と v4 契約の confirmatory paired 評価で
比較し、事前登録した単一の判定式で ADOPT / REJECT / NO_DECISION を得る。

**Why this priority**: spike は GO/NO-GO の de-risk であって採用証拠ではない(rounds=300・
3 fold のみ)。採否は rounds=900・全期間 walk-forward の事前登録ゲートのみが決める。

**Independent Test**: gate-config を凍結し hash を照合した上で smoke(事前登録外の窓・
効果数値非表示)で配線を確認 → 本実行 → verdict JSON。判定は harness の組込み式のみで、
運用者の裁量が入らないことをレポート構造で確認できる。

**Acceptance Scenarios**:

1. **Given** OOS 実行前に凍結した gate-config、**When** confirmatory 実行、**Then**
   config hash・評価窓の照合が通らない限り 1 レースも採点されない(fail-closed)
2. **Given** candidate(margin-aware V1)と active(現行教師信号)の両アーム、**When**
   paired 評価、**Then** 両アームの差は教師信号のみ(同一 seed・同一 mask・同一 TE・
   同一校正・同一 snapshot)で、**非ゼロ差のレースが 1 件以上ある**ことを実行時に assert
   する(差が厳密に 0 なら故障として abort = 097 の教訓)
3. **Given** 評価完了、**When** verdict を読む、**Then** ADOPT / REJECT / NO_DECISION の
   三値が harness の組込み式から機械的に出ており、個別数値の事後読み替えの余地がない

---

### User Story 3 - verdict 分岐の後始末(Priority: P3)

モデル開発者として、どの verdict でも一貫した後始末が行われる: ADOPT なら candidate 登録
(自動 active 化しない)、REJECT なら結線 revert + モジュール非結線保全 + 数値の記録。

**Why this priority**: 「際どい通過ライン」を事前に認めている feature なので、REJECT 分岐が
きれいに閉じることは ADOPT 分岐と同等に重要(null-is-success)。

**Independent Test**: ADOPT 分岐 = 登録されたモデルが candidate であり serving がロード
できることを実 DB で確認。REJECT 分岐 = revert 後に既存 active の予測がバイト不変で、
モジュール+テストが非結線で緑のまま残ることを確認。

**Acceptance Scenarios**:

1. **Given** ADOPT、**When** モデルを登録、**Then** adoption_status は candidate であり、
   active 昇格は別途の明示判断(prospective 等)を要する
2. **Given** REJECT、**When** 後始末完了、**Then** 学習経路の結線は revert され、objective
   拡張+単体テストは非結線で保全され(062/070/090 同型)、測定数値が spec 末尾に転記される
3. **Given** どちらの verdict でも、**When** 既存 active モデルで予測、**Then** 予測は
   マージ前とバイト一致する(教師信号は学習時のみ・serving 経路不変)

---

### Edge Cases

- **同着(dead heat)**: 着差 0.0 秒 → スケール GMIN=0.25。ただしステージの的中馬が一意で
  ない場合の中立化/break(039/042 規則)は**現行のまま**変更しない(変調は重みのみ)
- **完走 3 頭以下のレース**: ステージ 3 の「次の完走馬」が存在しない → スケール 1.0(中立)。
  ステージ発火条件(remaining ≥ 2)は現行のまま
- **時計欠損・部分取込**: 対象ステージの margin が計算できない行 → スケール 1.0(中立)。
  レースが margin 表に無い → 全ステージ 1.0
- **勝者不在・未確定レース**: 現行どおり group 中立化(sum(y) != 1 → 勾配 0)。スケールは
  読まれない
- **計時解像度**: finish_time は 0.1 秒刻み。0.0 秒 = 「0.1 秒未満」の意味であり、これを
  接戦として最小重みに落とすのは意図どおり(spike の機構仮説そのもの)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: production の PL top-k objective は、レース×ステージのスケール(既定 None)を
  受け取れなければならない。**None のとき現行実装と勾配・ヘシアンがビット一致**すること
  (spike の selftest と同じ表明を production の単体テストに移す)
- **FR-002**: 変調形は V1 で固定: ステージ 2,3 のみ `g(m) = clip(m / 0.2, 0.25, 1.0)`
  (m は秒)。M0 / GMIN / 対象ステージは実装内の凍結定数とし、ゲート実行時に調整可能な
  パラメータとして露出してはならない(spike の事前選択を唯一の出所とする)
- **FR-003**: margin は `race_results` の `finish_time` の**全完走馬に対する**隣接差分から
  算出しなければならない。着順 1..3 への制限は隣接差分の計算**後**に適用すること。実データ
  形状のテストで「ステージ 3 のスケール平均が実質的に 1.0 未満」を表明し、run 1 のバグ形
  (window 前のフィルタ)を構造的に検出すること
- **FR-004**: margin・スケールは**ラベル側のみ**(finish_rank と同じ契約)。特徴列・
  feature_hash・feature_snapshots に一切入れてはならない(leak-guard テストで機械固定)。
  FEATURE_VERSION は不変
- **FR-005**: 変調はステージ重みのみに作用し、ステージ発火条件・中立化・break 規則
  (039/042)を変更してはならない
- **FR-006**: レシピは margin-aware の ON/OFF をモデル同一性として持つ。OFF(既定)は
  **既存レシピの hash を 1 つも変えない**(weight_mask と同じ back-compat 省略方式)。
  ON は distinct な recipe_hash になる
- **FR-007**: 学習の fit 情報と保存 artifact のメタデータに、ステージ別スケール平均・
  変調が効いたレース数の分布統計を記録しなければならない(「実際に変調されたか」の事後
  監査。spike run 1 はこの統計で発覚した)
- **FR-008**: 採用ゲートは evaluation contract v4 の confirmatory paired 評価とする:
  gate-config を OOS 実行前に凍結し hash 照合で fail-closed、rounds=900・全期間
  walk-forward、両アームの差は教師信号のみ(同一 seed・同一 weight mask・同一 TE・同一
  校正方式)、入力は snapshot 固定(pin)。凍結値には seed_noise(再学習分散)を含める
- **FR-009**: verdict は harness の組込み三値(ADOPT / REJECT / NO_DECISION)を正本とし、
  個別カットオフ・個別 fold の数値による事後の読み替えを禁止する(068 C2)
- **FR-010**: 評価 driver は実行時に「両アームの予測に非ゼロ差のレースが 1 件以上ある」
  ことと「candidate 側のステージ別スケール平均が変調を示す」ことを assert しなければ
  ならない(差 0.000000 と変調不発は結果ではなく故障)
- **FR-011**: ADOPT の場合、モデルは candidate として登録し、自動で active に昇格しては
  ならない(昇格は prospective を含む別段の明示判断)
- **FR-012**: REJECT の場合、学習経路の結線のみを revert し、objective 拡張+単体テストは
  非結線で保全する(062/070/090 同型)。測定数値は本 spec 末尾に転記する
- **FR-013**: どの verdict でも、既存 active モデルの serving 予測はマージ前とバイト一致
  すること(教師信号は学習時のみ・予測経路/スキーマ/API/OpenAPI/DB 不変・migration なし)

### Key Entities

- **ステージスケール**: レース × ステージ(2,3)ごとの重み係数 [0.25, 1.0]。着差の
  凍結関数。学習時のみ存在し、永続化されるのは分布統計のみ
- **margin(着差)**: ステージ的中馬と次の完走馬の確定走破時計差(秒)。結果由来の
  ラベル側データ。特徴量ではない
- **gate-config**: 凍結された評価契約(v4)の実体。hash で改変検出
- **verdict**: harness が出す三値+根拠。spec 末尾に転記され不変の記録になる

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: margin-aware OFF の学習は現行と完全一致する — 勾配ビット一致テストが緑、
  既存レシピの recipe_hash が 1 つも変わらない、既存テストスイートが無改修で緑
- **SC-002**: 実データ形状でステージ 2・3 の両方が実際に変調される(スケール平均 ≈0.66〜
  0.69・run 1 のバグ形ならテストが赤)
- **SC-003**: ゲートが完走し、単一の事前登録式から三値 verdict が機械的に得られる。配線
  起因の NO_DECISION を出さない(smoke で配線を事前確認)
- **SC-004**: リーク境界が機械固定される — margin/スケールが特徴列・feature_hash・
  feature_snapshots に現れないことをテストが表明し、既存 active モデルの予測がマージ前後で
  バイト一致する
- **SC-005**: 測定の全数値(pooled 点推定・標本 CI・総 CI・fold 別・校正前)が spec 末尾に
  転記され、ADOPT / REJECT いずれでも軸が数値つきで閉じる
- **SC-006**: REJECT の場合も保全モジュールの単体テストが非結線で緑であり、将来の再測定
  (例: 容量やデータ量が変わった時)を 1 コマンドで再開できる

## Assumptions

- **効果の見通しは際どい**(spec として明記): spike の点推定 −0.00337(rounds=300)に対し、
  全期間ゲートの総 CI 半幅は ~0.0024 と見込まれ、上限 ≈ −0.001。097 型(点は良いが CI
  ゼロ跨ぎ)で REJECT はあり得る。REJECT でも null-is-success として扱う
- rounds=900 と教師信号の相互作用(信号が細かくなるほど容量が効く可能性)は未測定の
  上振れ要因。spike が 300 で測ったことはゲートの前提条件でなく限界として記録する
- margin の充足率は実測 100%(**測定は 2023〜2026**・finished 行)。本実行の 2019+ と
  smoke の 2016-2017 は未測定だが、欠損は中立 1.0 に落ちるだけで安全側であり、実際の率は
  fit_info 統計(neutral_missing_time)で可視化される
- 両アームとも fold ごとに再学習する(保存 booster 不使用・068 C1)。評価は materialized
  snapshot を固定して行う(実行中に DB が動く影響の排除・091 D16)
- ゲートの実行時間は同規模の前例(097: 2 アーム × 3 窓で ~2〜3.5h)から、2 アーム × 全期間
  walk-forward で数時間〜十数時間と見積もる(実績比で見積もる = 095 の教訓)
- 憲法の codex second-opinion ゲートは plan 段階で試行する。本日 6/6 run がインフラ
  エラーで死んでおり、復旧しない場合はセルフレビュー checklist で代替し
  `codex unavailable: 理由` を plan に記録する(080/067 前例)

## Scope Out(本 feature でやらないこと)

- V2(全ステージ変調)の再測定 — spike で符号反転により棄却済み
- M0 / GMIN / 対象ステージのチューニング — spike の事前選択が唯一の出所
- 変調関数の別形(ソフト飽和・馬身数ベース等)— 本 verdict の後に別 spec で再事前登録
- active への昇格 — candidate 止まり。昇格は prospective を含む別段の判断
- binary / cond_logit(top-1)への変調適用 — pl_topk のみ
- 特徴量・スキーマ・API・serving・betting・front の変更 — 一切なし


## 実測結果(2026-08-25・T020 confirmatory・凍結 gate hash d8c479dea834…)

**verdict = REJECT(cause=gate_hard_fail・harness v4 組込み式・事後読み替えなし)**

| 項目 | 値 |
|---|---|
| 窓 / 規模 | 2019-01-01..2026-08-23・26,482 レース・825 開催日・8 fold |
| pooled winner NLL 差(candidate−active) | **+0.000501**(候補が悪い) |
| 標本 CI | [−0.001155, +0.002190] |
| **総 CI(v4・seed_noise 込み)** | **[−0.001579, +0.002607]**(ゼロ跨ぎ) |
| fold 窓別 | recent_3y +0.000245 / recent_5y +0.000949(いずれも非劣性 PASS・margin 0.005) |
| top2 / top3 差 | +0.000298(PASS)/ **+0.000634(FAIL・許容 0.0005)= hard fail** |
| 校正(ECE) | 非劣性 PASS・緊急停止なし |
| critical subgroups | recent_year_only / nk / recent_year_nk **全 PASS(full assurance)** — 害はどこにも無い |
| 構造 assert | 実行前(アーム同一性・実効 recipe 照合)・実行後(非ゼロ差・変調統計)全通過 = 正当な測定 |

**解釈(事前登録の範囲内)**: spike(rounds=300・holdout isotonic・直近 3 fold)の
pooled −0.00337 は、**本番構成(arm E OOF isotonic + rounds 900 + 全期間)で再現しなかった**
— 点推定は符号ごと反転した。spec の Assumptions が事前に認めた「rounds=900 との相互作用は
未測定」「spike は複製経路であり production 経路での再現保証はない」がそのまま現実化した形。
候補は有害ではない(subgroup 全 PASS)が優れてもおらず、top3 の教師信号を弱めた分だけ
top3 導出が僅かに劣化した(hard fail の実体)。**null-is-success**: この軸(着差による
ステージ減衰 V1)は本番構成で数値つきで閉じる。V2 は spike で棄却済み。別の変調形は
別 spec の再事前登録のみ。

**後始末(FR-012)**: 結線(dataset aux 列生成・recipe field・CLI mteach セグメント・
predictor 配線)は revert。`pl_topk_objective` の `stage_scales` 引数と win_model の検証
つき整列(いずれも None 既定=現行とビット一致)+それらの単体テストは保全。T015a
(CalibSplitFactory への snapshot pin)と T016 の wmask=/params carriage 修正は margin と
独立の実バグ修正なので**残す**。両系 recipe hash のスナップショットテストも将来の
フィールド追加事故(codex P0-1 型)の常設ガードとして残す。
