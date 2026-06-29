# Phase 0 Research: 展開・ペース構成特徴 (031)

## R1: 何を作るか（field-composition の最小列）
**Decision**: pace_scenario group = 7 列。field 3(`field_front_rate_ex_self`/`field_closer_rate_ex_self`/`pace_imbalance_ex_self`) + 相互作用 3(`front_pressure`/`closer_setup`/`style_mismatch`) + 1(`field_style_coverage`)。全て連続値・float64。
**Rationale**: codex の独立判断「カテゴリ化(想定ペースの離散ラベル)は 027 同型の疎な特徴になり落ちやすい→leave-one-out 連続量にせよ」。フィールド集計単独は position_style への上積みが薄い恐れ→相互作用を主役に。
**Alternatives considered**: (a) 想定ペースの離散ラベル(高/中/低)→疎・027 の二の舞で却下。(b) field 集計のみ→相互作用なしでは識別力が薄い見込みで却下。(c) 全頭ペア相性行列→次元爆発・スパースで却下。

## R2: own 値の供給元（二重実装の回避）
**Decision**: own.front_runner_rate/closer_rate/rel_corner_pos_avg は 023 `build_pace_features` の出力をそのまま使う。`pace_scenario_features.build_pace_scenario_features(frames)` は内部で build_pace_features を呼ぶ。
**Rationale**: 脚質の as-of 定義(逃げ/先行=front, 差し/追込/マクリ=closer; STARTED 過去レースで rolling; merge_asof allow_exact_matches=False)は 023 で確立・テスト済。再実装するとドリフト/リーク再導入リスク。憲法 II/V。
**Alternatives**: 生 running_style を pace_scenario 内で再集計 → 二重実装・今走脚質を誤って読むリスク → 却下。

## R3: リーク境界（field-composition は repo 初の「他馬の値を読む」型）
**Decision**: field 集計は同レース他馬の **strictly-before の as-of 脚質**(build_pace_features 出力)のみ。今走の race_results/result_status/finish_order/corner_orders/running_style は読まない。自馬 leave-one-out 除外、他馬は同日除外を継承(各馬の as-of 値が既に同日除外済み)。
**Rationale**: 人間ハンデキャッパーが出馬表時点で読む「展開」と同じ。他馬の過去実績は予測時点で既知。build_pace_features の出力だけを入力に取ることで、生の今走列に触れない設計にし、リーク面を 023 の as-of 機構に閉じ込める。leak-guard test(自馬今走/他馬今走/同日/未来 を変えても本群不変 + ソース grep で running_style/corner を生参照しない)で担保。
**Alternatives**: 今走の枠順/オッズで展開を推定 → オッズは非特徴(II)、枠順は別 feature(枠×脚質)に切り出すため本群では使わない。

## R4: フィールド母集団（取消の扱い）
**Decision**: フィールド = entry_status==STARTED の馬。今走 result_status は使わない。
**Rationale**: codex 指摘「取消確定タイミングが serving と一致しているか要確認」。result_status(完走/取消)は結果情報でリーク。entry_status は出馬表段階の確定情報で、023 の field_size も同基準。serving(未来レース)でも entry_status は既知。
**Alternatives**: 完走馬のみで集計 → 結果リーク → 却下。全エントリ(取消含む) → 取消馬が展開に影響しないので除外が自然。

## R5: NaN 規律（0 埋め禁止 + coverage 列）
**Decision**: 他馬 0 頭/全 null → ex_self=NaN。own が null(デビュー等) → 相互作用=NaN。`field_style_coverage`=nonnull(front_runner_rate)/field_size を別列で明示。0 埋めしない。
**Rationale**: codex「0 埋めは Unknown を偽の情報にする。coverage を別特徴にして Unknown 多発レースをモデルに知らせよ」。026/030 の NaN 伝播方針と一致。LightGBM は NaN を native 分岐できる。
**Alternatives**: 0 埋め → 「先行馬ゼロ」と「データ無し」を混同 → 却下。レース平均で補完 → 漏れ・歪み → 却下。

## R6: 採用プロトコル（事前登録 bundle, codex Q4）
**Decision**: pace_scenario を 1 bundle として features-008 vs features-009 を walk-forward OOS で評価(feature-eval の既定 --drop-groups を pace_scenario に)。ablation(field_only/interaction_only/diversity_only)は diagnostic 専用。bundle 採用後に OOS を見て列を削るのは禁止(削るなら次版で再事前登録)。
**Rationale**: codex「§3 は弱い相互作用の束→単 column gate では 027 同様落ちやすい→bundle で事前登録。kitchen-sink は不可、Q3 の他候補は別 bundle/別 feature」。030 で「per-group では落ちるが bundle で採用」の前例([[feature-030-lowcost-result]])があり、相互作用は群内で効く想定。
**Alternatives**: per-column 採否 → スパース相互作用が個別に落ちる → 却下。全候補(Q3 含む)を 1 bundle → kitchen-sink で交絡 → 却下(Q3 は別 feature)。

## R7: 採用閾値
**Decision**: 020/023/030 と同型を流用(事前登録)。primary = 平均 win LogLoss 改善 かつ ECE 非悪化。fold ガード = strict majority(n_win*2>n_folds) + worst-fold ECE tol 2e-3 + worst-fold dLogLoss tol 5e-3。
**Rationale**: 既存ゲートと整合。閾値を本 feature 用に緩めない(選択リーク回避)。
**Alternatives**: 閾値変更 → 事前登録原則に反する → 却下。

## 実データ結果（T013, 18 fold walk-forward OOS, baseline=features-008）
**bundle（pace_scenario 全7列）= ADOPTED=True**: win LogLoss 0.23277→**0.23200**(−0.00077, 本シリーズ最大級の単群ゲイン)・AUC 0.74810→**0.75143**(+0.0033)・Brier 0.06220→0.06205・ECE 0.00893→**0.00878**(改善)・**17/18 fold 勝ち**・worst_dLogLoss +0.00013(<5e-3)・worst_dECE +0.00125(<2e-3)・primary_pass=True。
**決定: 採用**(features-009=008+pace_scenario, lgbm-031 再学習・active 昇格, lgbm-030 retired)。**特筆**: 020/023 は識別力↑だが ECE 悪化(discrimination↔calibration トレードオフ)だったのに対し、展開シグナルは **AUC↑かつ ECE↓** を両立。市場が織り込みにくいレース内相互作用(展開)が、単独馬の能力特徴では届かない新情報を加えたことを示す。「単独能力」から「組合せ(誰と走るか)」へ軸を移したことが効いた。実 DB カバレッジ: field_*_ex_self 91.9%・相互作用 89.6%・field_style_coverage 100%。market_edge は SECONDARY(採否外)。

## Codex second opinion（取得・反映済み）
- Q1(リーク): build_pace_features の as-of 出力を race 内集約なら II 安全。生 running_style を今走で読むのは禁止。他馬も同日除外、entry_status の取消タイミングを serving と一致。→ R3/R4 反映。
- Q2(限界寄与/設計): カテゴリ化回避、leave-one-out 連続量(field_front/closer_rate_ex_self, pace_imbalance_ex_self)、相互作用主役(front_pressure, closer_setup, style_mismatch)、0埋め禁止 + field_style_coverage。→ R1/R5 反映。
- Q3(他の中コスト候補): 距離替わり×末脚・クラス替わり×時計・枠順×脚質×コース・斤量変化×能力・低履歴×血統/人 → 本 feature の scope 外、§3 後続の別 feature/別 bundle。
- Q4(事前登録): per-column 採否に反対、pace_scenario bundle で features-008 と比較、ablation は diagnostic、kitchen-sink 不可。→ R6 反映。
**reconcile 差分**: 当初案の「相性カテゴリ」を全廃し連続 leave-one-out + 相互作用に。coverage 列を追加。採用は bundle 事前登録で確定。
