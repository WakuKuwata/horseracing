# Feature Specification: Harville stage 割引 — top2/top3(連対・複勝)確率の校正改善

**Feature Branch**: `049-harville-stage-discount`

**Created**: 2026-07-02

**Status**: **PARTIAL — PRIMARY 合格 / MUST 不合格(製品デフォルト非採用、opt-in で保全)**

## 実 DB 結果(2026-07-03、事前登録ゲート実行)

**PRIMARY(校正、18-fold OOS, pl_topk+TE+isotonic)= 圧勝で合格**:
- win LogLoss 0.21721 = 両者**完全一致**(max|Δ|=0、INV-S2 実証)
- top2 ECE 0.00735→**0.00249**(約3倍改善)、LogLoss 0.34169→0.34112
- top3 ECE 0.01914→**0.00385**(約5倍改善)、LogLoss 0.43219→0.42952
- 勝ちfold 17/18、worst-fold top3 dLL +0.00000(ガード内)
- λ̂ 収束・安定: λ₂≈0.82、λ₃≈0.70(1未満=過大 tail を平坦化、Benter 理論と整合)。fold 2008=identity(先行OOS無し)

**MUST(exotic pseudo-ROI 非悪化、2025 H1、λ̂=(0.871,0.712) n_fit=3441)= 不合格**:
- place ROI 1.0650→1.0803(+0.0153、改善)
- wide ROI 1.6536→1.9626(+0.3090、改善)
- **trio ROI 1.4096→0.9691(−0.4405、大幅悪化 ≪ 許容 −0.005)→ MUST=False**
- 機序: top3 割引が本命の複勝質量を穴へ再配分 → 三連複 EV 選定が穴目に偏り、(疑似)オッズ上で回収悪化(バグでなく実効果。Σtrifecta=1・joint 整合は全テスト緑)

**決定(憲法 III=事前登録ゲートを数値で動かさない)**: 製品デフォルトは **λ=1 のまま非採用**。実装は全て **opt-in(既定オフ)** で保全(FR-006、eval/校正インフラは無害でマージ可)。**残る判断: 表示(連対率/複勝率)の校正改善は大きく実在するのに、束ねた exotic trio ゲートが全体を veto している**——serving/表示のみのスコープ採用は spec が事前登録していないため、ユーザー判断事項(039 前例=機械ゲートのユーザー上書き)。

---

**Status(原文)**: Draft

**Input**: User description: "win, place, show すべての精度改善 — top2/top3(連対・複勝)確率の校正。lgbm-042 の 18-fold OOS で win ECE 0.00057 に対し top2 ECE 0.00735(13倍)・top3 ECE 0.01944(34倍)。原因は Harville 逐次条件付けの既知バイアス(強い馬の 2〜3着確率を過大評価: Henery 1981 / Stern 1990 / Benter 実務補正)。stage 割引 p^λ_j を walk-forward MLE でフィットして是正する。"

## 背景と根拠(実測済み)

**識別力は同水準なのに、導出確率の校正だけが系統的に悪い**(lgbm-042、18-fold OOS、86.6万頭):

| 指標 | win | top2 | top3 |
|---|---|---|---|
| AUC | 0.793 | 0.786 | 0.781 |
| **ECE** | **0.00057** | **0.00735(13倍)** | **0.01944(34倍)** |

win の p は softmax→isotonic 校正済みの本体出力だが、top2/top3 は `harville_topk`(eval/baselines)の**素の Harville 逐次条件付け**で導出しているだけで、top2/top3 自身への校正は存在しない。042 の PL top-k は学習教師のみで推論時導出は未着手の層。

**方向・帯の診断は既存の永続化 reliability bins(metrics_summary.eval.reliability、021 で全ラベル分計算済み)で確定済み** — 文献の Harville バイアスと完全一致する単調パターン:

| top3 帯 | 予測平均 | 実現率 | ずれ |
|---|---|---|---|
| 0.0–0.1 | 0.042 | 0.057 | 過小 |
| 0.3–0.4 | 0.348 | 0.342 | ≈(交差点) |
| 0.5–0.6 | 0.548 | 0.497 | 過大 +5pt |
| 0.7–0.8 | 0.746 | 0.659 | 過大 +8.7pt |
| 0.8–0.9 | 0.842 | 0.746 | **過大 +9.6pt** |
| 0.9–1.0 | 0.937 | 0.846 | 過大 +9.0pt |

top2 も同型(0.7–0.8 帯: 0.745→0.661 = +8.4pt 過大、低帯は過小)。「1着を取る強さ」と「2〜3着に残る傾向」を同一の強さパラメータで表す Harville の構造的問題であり、当初計画していた診断先行フェーズは**不要**(仮説は既存データで確認済み)。

**影響範囲**: 同じ逐次条件付けは 009 エンジンの全 exotic 券種(複勝・ワイド・馬連・三連複・馬単・三連単)の確率導出にも使われている。是正は表示中の連対率・複勝率だけでなく、これら券種の EV / Kelly の品質を直接改善する。

**位置づけ**: 2026-07 の「win 精度レバー全消化」クローズドリストは win の p の総括であり、導出層は未棚卸し=本 feature はリスト外の正当な新レバー。048(two_gamma)は p の校正層(エンジン入力前)、本 feature は導出層(エンジン内部の逐次分母)で、層が異なり重複しない。

**codex CLI は本セッションで起動不可を確認(3回連続)→ single-opinion**(文献裏付け: Henery/Stern/Benter の確立された補正、および 013/017/048 と同型のフィット・リーク規律)。

## 手法(事前登録・変更禁止)

**stage 割引付き Plackett-Luce/Harville**: j 着ステージ(j=2,3)の条件付き分布を
`P(i が j 着 | 上位確定) = p_i^λ_j / Σ_{k∈残存} p_k^λ_j`
とする(素の Harville は λ_j=1)。λ_2, λ_3 ∈ [0.1, 5.0] を **train 窓内のみ**で以下の条件付き NLL 最小化でフィット(決定論: 013/017/048 の golden-section 流用、各 λ は独立に 1 次元最適化):

- λ_2: 実際の勝者を条件に、実際の 2 着馬の `−log P(2着馬 | 勝者除外)` を全レースで総和
- λ_3: 実際の 1・2 着を条件に、実際の 3 着馬の `−log P(3着馬 | 上位2頭除外)` を全レースで総和

**stage 1(win)は λ_1=1 固定で一切触らない = win 確率はバイト不変**。適用先は `harville_topk` と 009 エンジンの逐次分母の**単一実装**(top2/top3 marginal と joint が同じ λ を共有しないと「joint marginal == harville_topk」の整合性不変量が壊れるため、片方だけの適用は禁止)。

Σtop2=2・Σtop3=3 は条件付き分布の形によらず構成的に保存される(各ステージの条件付き確率が正規化されている限り成立)。win≤top2≤top3 の単調性も加法構成で保存。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - stage 割引導出とλフィット (Priority: P1)

確率導出の利用者(eval・serving・betting)は、λ_2/λ_3 を指定した stage 割引付き top2/top3・joint 導出を opt-in で利用でき、λ を過去データから決定論的にフィットできる。

**Why this priority**: 全ての後続(評価・採否・製品結線)の土台。これ単体でも研究 CLI から効果を確認できる。

**Independent Test**: λ=1 で現行実装とバイト一致(後方互換)、λ<1 で本命の top2/top3 が下がり穴が上がること、フィットが決定論であることを単体テストで検証。

**Acceptance Scenarios**:

1. **Given** 正規化済み win ベクトル、**When** λ_2=λ_3=1 で導出、**Then** 既存 `harville_topk`・009 エンジン出力とバイト一致(既定値=現行挙動)。
2. **Given** 同一ベクトル、**When** λ_2,λ_3<1 で導出、**Then** win は不変のまま、高 p 馬の top2/top3 が単調に減少し低 p 馬が増加、Σtop2≈2・Σtop3≈3・win≤top2≤top3 を維持。
3. **Given** 過去レースの (win ベクトル, 確定 1〜3 着)、**When** λ をフィット、**Then** 同一入力で同一 λ(決定論)、対象レースより厳密前(race_id タイブレーク)のデータのみ使用、サンプル不足(事前固定の min_races 未満)なら λ=1 に fallback。

---

### User Story 2 - 事前登録ゲートでの採否判定 (Priority: P2)

モデル運用者は、18-fold walk-forward OOS で「fold ごとに train 窓から λ をフィット→valid 年の top2/top3 を stage 割引で導出」した candidate を baseline(λ=1)と比較し、事前登録ゲートで機械的に採否を決められる。

**Why this priority**: 憲法 III(評価先行)。効果の実証と採用判断が製品結線の前提。

**Independent Test**: 実 DB で feature-eval 同型の比較レポートが出力され、ゲート判定が spec の条件と一致する。

**Acceptance Scenarios**:

1. **Given** 18-fold 評価、**When** candidate=stage 割引 vs baseline=λ=1 を同一 fold・同一予測 p で比較、**Then** win の全指標が両者で完全一致(stage 1 不変の証明)し、top2/top3 の LogLoss/ECE/reliability が fold 別・overall で報告される。
2. **Given** 評価結果、**When** 事前登録ゲート(下記)を適用、**Then** 採否が機械判定され、判定根拠(数値)がレポートに記録される。

**採用ゲート(事前登録 — 実行前に固定、数値を見て動かさない)**:
- **PRIMARY**: overall top2 LogLoss 改善 かつ top3 LogLoss 改善 かつ top2/top3 ECE 改善、勝ち fold が strict majority(top3 LogLoss 基準)
- **MUST**: win 指標が baseline と完全一致(バイト不変)/ 009 不変量(単調・Σtop2≈2・Σtop3≈3・joint marginal == 割引版 harville_topk・決定論)全テスト緑 / exotic pseudo-ROI バックテスト(複勝・ワイド・三連複、011/016 同条件・製品構成=two_gamma 込みの betting 経路)で**各券種の pseudo-ROI 差 ≥ −0.005**(非悪化、tol は本 spec で事前固定)
- **ガード**: worst-fold top3 dLogLoss ≤ +5e-3(020/023 同型の単一 fold blip 許容)
- 不採用 → λ=1 既定のまま negative result を記録(導出 opt-in 拡張は無害なのでマージ可、027 のようなブランチ保全は不要)

---

### User Story 3 - 採用時の製品結線 (Priority: P3)

採用された場合、serving の top2/top3 永続化と betting の推奨経路(複勝等の exotic 確率)が stage 割引済みの値になり、画面の連対率・複勝率が校正済みの数字になる。

**Why this priority**: 採用が決まって初めて意味を持つ結線。ゲート不通過なら実施しない。

**Independent Test**: 実 DB E2E — serve 実行で λ が walk-forward フィットされ、logic_version に記録され、race_predictions の top2/top3 が割引済みで永続化され、API/画面に透過する。

**Acceptance Scenarios**:

1. **Given** 採用済み構成、**When** レースを serve、**Then** λ_2/λ_3 が対象レースより厳密前のデータからフィットされ(046 の `_fit_product_p_calibrator` と同型の走査・境界)、使用 λ とフィット窓が logic_version に記録される。
2. **Given** 割引済み予測、**When** API/画面で表示、**Then** win_prob は従来とバイト一致、top2/top3 のみ変化、監査情報から λ を再現できる。

---

### Edge Cases

- **同着(dead heat)・着順非一意**: λ フィットから該当レースを除外し件数を表面化(013/017 同様)。2着同着なら λ_2 サンプルから除外、3着同着なら λ_3 のみ除外。
- **head数 <3**: stage 3 サンプルなし → そのレースは λ_3 フィットに寄与しない。導出側は field_size ルール(5–7=top2, 8+=top3)既存踏襲。
- **残存質量が eps 以下**(1−Σp が微小): 既存 `_EPS` スキップ規律を割引版でも同一に踏襲(denom-skip を継承しない 009 の教訓は正規化順序の話であり、ここは分母ガード)。
- **λ 境界張り付き**: フィット結果が探索境界 [0.1, 5.0] に張り付いた場合は identity fallback(λ=1)とし警告を記録(境界解は誤特定の兆候)。
- **サンプル不足**: min_races(事前固定: 300 レース、λ_2/λ_3 それぞれ判定)未満 → λ=1 fallback(017 の identity fallback 前例)。
- **取消・除外馬**: 既存 canonical field 規律(除外→再正規化→clip→導出)を変更しない。λ は正規化後ベクトルに適用。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: stage 割引は win 確率を一切変更してはならない(λ_1=1 固定)。λ_2=λ_3=1 のとき既存導出とバイト一致(後方互換・既定値)。
- **FR-002**: 割引は top2/top3 marginal 導出と 009 joint エンジンの**単一実装**の逐次分母に適用し、「joint marginal == harville_topk(同一 λ)」「Σexacta=1・Σtrifecta=1」「win≤top2≤top3」「Σtop2≈2・Σtop3≈3(spec 許容誤差)」「決定論」の全不変量を維持する(憲法 IV)。
- **FR-003**: λ フィットは対象(レース/評価 fold)より**厳密前**のデータのみ使用(race_id タイブレーク、日付レベル <= 禁止)。フィット入力は永続化済み/fold 内で再現可能な win ベクトルと確定着順のみで、オッズ・市場情報を使わない(p≠q・憲法 II)。割引後の値・λ はモデル特徴に還流しない(leak-guard テスト)。
- **FR-004**: 採否は US2 の事前登録ゲートに完全一致で機械判定する(憲法 III)。データを見た後のゲート・探索範囲・min_races 変更は禁止。
- **FR-005**: スキーマ・API・openapi 契約変更なし。λ・フィット窓・fallback 発動は logic_version / レポートに記録し再現可能とする(憲法 V)。
- **FR-006**: 不採用時も opt-in 実装(λ 引数既定 1)はマージ可。製品既定の切替は ADOPTED 時のみ。

### Key Entities

- **stage 割引 λ (λ_2, λ_3)**: 2着・3着ステージの逐次条件付き分布に適用する冪指数。1=素の Harville。走行時フィット値であり永続テーブルは持たない(logic_version 記録のみ)。
- **λ フィットサンプル**: (正規化済み win ベクトル, 確定 1〜3 着) の組。結果はフィットのラベルとしてのみ使用(選定・特徴に不使用)。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 18-fold OOS で top3 ECE が 0.019 から有意に改善(目安: 半減以下)し、top2 ECE も改善、top2/top3 LogLoss が baseline(λ=1)を下回る。win 指標は完全一致。
- **SC-002**: reliability bins の高予測帯(top3 0.7 以上)の「予測−実現」乖離が baseline の +9〜10pt から明確に縮小する。
- **SC-003**: 全確率整合性テスト(単調・合計・joint 整合・決定論・λ=1 後方互換)が緑。probability/eval/serving/betting 既存スイートが緑。
- **SC-004**: exotic pseudo-ROI バックテスト(複勝・ワイド・三連複)が baseline 比非悪化。
- **SC-005**: (採用時) 実 DB E2E で serve→永続化→API 透過と logic_version の λ 監査記録を確認。

## Assumptions

- 評価は既存 18-fold expanding-yearly ハーネス(eval.harness)と同一分割・同一予測 p を流用し、導出のみ差し替えて比較する(モデル再学習なし=比較は導出層の純効果)。
- λ フィットの実装インフラは 013(_golden_min)/017/048 の校正フィットを流用できる。
- min_races=300・探索範囲 [0.1, 5.0]・ゲート閾値は本 spec で事前固定(FR-004)。
- 診断フェーズ(reliability の方向確認)は既存永続データで完了済みのためスコープ外。q(市場)側の同型補正、Stern の順序統計モデル等の代替関数形、λ の条件別(頭数・馬場等)フィットは deferred。
- 048(two_gamma、エンジン入力前の p 校正)とは層が異なり独立。**λ の fit と apply は同一の p 分布で行う(分布一致原則)**: 18-fold 評価と serving は素の model p(=race_predictions に永続化される p、two_gamma なし)で fit/apply。betting 推奨経路(two_gamma 適用後の p' がエンジンに入る)では、fit サンプルの win ベクトルにも同一の two_gamma 校正器を適用してから λ をフィットし、p' に apply する。
- λ フィットサンプルのレースごとの prediction_run 選択は既存 `load_p_samples` と同一規則(latest run)を踏襲する(046/048 との監査一貫性)。モデル版混在(lgbm-039/041/042 等)による λ の希釈は既知の限界として受容する。
