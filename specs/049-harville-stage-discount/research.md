# Research: Harville stage 割引 (049)

Phase 0 — Technical Context の未知点を解消した決定記録。codex CLI はセッション内 3 回起動不可のため single-opinion(文献・既存前例で補強、各決定に却下代替案を明記)。

## D1: 割引関数形 — Benter 型 p^λ(ステージ別冪)

- **Decision**: j 着ステージ(j=2,3)の条件付き分布を `P(i|残存) = p_i^λ_j / Σ_{k∈残存} p_k^λ_j` とする。λ_1=1 固定。
- **Rationale**: (a) Benter が香港で実運用した確立された補正で、勝率モデルを触らず 2〜3 着の「圧縮」を 1 パラメータ/ステージで表せる。(b) PL の逐次構造を保つため Σexacta=1・Σtrifecta=1・Σtop2=2・Σtop3=3 が構成的に成立(各ステージ条件付きが正規化されている限り)。(c) 実測の reliability パターン(高帯過大・低帯過小の単調)は冪の平坦化(λ<1)がちょうど表せる形。
- **Alternatives considered**:
  - Henery/Stern の正規順序統計モデル — 逐次 PL 構造から離れ、009 joint(exacta/trifecta 格子)の閉形式が失われ整合性検証が壊れる。計算も重い。却下。
  - top2/top3 marginal への直接 isotonic — 「joint marginal == harville_topk」不変量を保てない(marginal だけ動かすと joint と乖離)。単調性・合計制約の射影も必要で筋が悪い。却下。
  - λ の条件別(頭数・馬場)フィット — サンプル分割で不安定。deferred(spec 記載)。

## D2: 実装配置 — eval に新モジュール、engine は同一実装を import

- **Decision**: 割引導出コアとフィットを `eval/src/horseracing_eval/stage_discount.py`(新規)に置く。`baselines.harville_topk` に `lambda2/lambda3` オプション引数を追加し、**λ=1.0 のときは既存コードパスを明示分岐で通す(バイト一致保証)**。`probability.engine.joint_probabilities` は `stage_discount` opt-in 引数で同じ重み関数を逐次分母に適用。`consistency.py` も同一 λ で検証。
- **Rationale**: 依存方向が probability→eval(既存)なので、共有実装は eval 側にしか置けない(逆は循環)。training predictor(`assemble_predictions`)も eval から import 済みで自然に λ を透過できる。λ=1 の明示分岐は「pow(x,1.0)==x の libm 依存」を排除し FR-001 のバイト一致を構造的に保証する。
- **Alternatives considered**: probability 側に置く — eval の A/B(training CLI 経由)が probability import を必要とし、training→probability の新依存が生まれる。却下。fl_bias._golden_min の再利用 — 同上の方向制約で不可。eval 内に決定論 golden-section を局所実装(≈15 行、同一許容誤差)し、docstring で 013 と同型であることを明記。重複は依存方向の代償として許容。

## D3: 評価時の λ フィット — 「前 fold までの pooled OOS 予測」で fit

- **Decision**: 18-fold expanding-yearly で fold(valid 年 Y)の λ は、**valid 年 < Y の全 fold の OOS 予測(win ベクトル)× 確定 1〜3 着**からフィット。fold 2008(先行 OOS なし)は identity(λ=1)。min_races=300 未満も identity。
- **Rationale**: λ は「OOS の p に対する導出補正」なので、フィット対象も OOS の p であるべき。fold 内 train レースの予測は in-sample で校正が OOS より良く、λ が過小(補正不足)側にずれる。厳密前条件(憲法 II)も fold 順序で自動的に満たす。
- **Alternatives considered**: fold 内 train レースへの in-sample 予測で fit — 上記バイアス。却下。全期間一括 fit — 選択リーク(評価データで fit)。憲法 II 違反。却下。

## D4: 製品(serving)時の λ フィット — 046 `_fit_product_p_calibrator` と同型

- **Decision**: 永続化済み prediction_runs × race_results から、対象レースより**厳密前**(`race_before`、race_id タイブレーク)の (win ベクトル, 1〜3 着) サンプルを構築する新 loader を追加(既存 `load_p_samples` は勝者のみで不変のまま)。run 選択は `load_p_samples` と同一規則(latest run)。serve=レース前フィット、backfill=日単位 1 回フィット(046 と同じ規律)。λ と n_races を logic_version に記録。
- **Rationale**: 046/048 で確立した製品 walk-forward フィットの規律・境界・fallback をそのまま流用でき、リーク面を増やさない。
- **分布一致原則(analyze I2/A1 の解消)**: λ の fit と apply は同一の p 分布で行う。(a) serving 永続化経路 — エンジン入力は素の p(isotonic+softmax、two_gamma なし)なので、fit も永続化済みの素の p で行う(そのまま一致)。(b) betting 推奨経路 — エンジン入力は two_gamma 適用後の p'(046/048)なので、fit サンプルの win ベクトルにも**同一の two_gamma 校正器を適用してから** λ をフィットする(校正器→λ の順にフィット、どちらも厳密前データのみ)。同一 λ̂ を異なる分布に流用しない。
- **Alternatives considered**: λ を定数として運用(eval フィット値を固定埋め込み)— 密度の変化に追従できず、046 の「レースごと walk-forward」規律とも不整合。却下(ただし eval レポートは参考値として全期間 λ̂ を出力する)。

## D5: 同着・データ品質の扱い

- **Decision**: λ_2 フィットは「1 着が一意 かつ 2 着が一意」のレースのみ、λ_3 は「1・2 着が一意 かつ 3 着が一意」のレースのみ使用。除外件数をレポートに表面化。取消・除外馬は既存 canonical field 規律(除外→再正規化)後のベクトルを使用。
- **Rationale**: 042 の PL top-k 学習(stage 非一意=break)・013 の dead-heat 除外と同じ規律。
- **Alternatives considered**: 同着を分数カウント — 複雑さに見合う情報量がない(発生率は極小)。却下。

## D6: A/B 評価ハーネス — 単一学習パスで両導出を採点

- **Decision**: 新 eval 関数(`evaluate_stage_discount`)が既存 expanding_folds/EvalRace を流用し、fold ごとに (1) 現行 predictor で valid 年を予測して**win ベクトルを収集**、(2) λ=1(baseline)と λ̂(D3)の両方で top2/top3 を導出、(3) 両者の LogLoss/ECE/reliability を fold 別+overall で採点、(4) 事前登録ゲートを機械判定したレポートを出力。training CLI `stage-discount-eval` から LightGBMPredictor を注入(feature-eval と同型の predictor-agnostic 構成)。
- **Rationale**: モデル・win 確率は両案で完全同一なので、学習は 1 パスで済み、差分は導出層の純効果として観測される(win 指標の一致検証も同時に出る)。評価入力は**素の model p**(predictor の isotonic+softmax 出力=race_predictions に永続化されるものと同一分布)であり、two_gamma は合成しない(two_gamma は betting 推奨経路のみの適用で、eval/serving の p 分布には存在しない — 分布一致原則、D4)。
- **Alternatives considered**: 既存 feature-eval を 2 predictor で回す — 学習 2 回で無駄な上、win まで再学習ノイズが乗り「導出層の純効果」でなくなる。却下。

## D7: exotic 非悪化ゲートの具体化

- **Decision**: 既存 exotic pseudo-ROI バックテスト(011/012/016 経路)を「同一レース集合・同一選定条件・同一オッズ」で λ=1 vs λ̂ の 2 構成実行し、**複勝・ワイド・三連複**の各 pseudo-ROI 差 ≥ −0.005 を MUST とする(改善は要求しない=非悪化。tol は spec US2 MUST に事前登録済み=単一の正)。比較は**製品構成の betting 経路**(two_gamma 込み)で行い、λ̂ は two_gamma 適用後の p' でフィットしたもの(D4 分布一致原則)。対象期間は永続化予測が存在する範囲(048 教訓: 実行前にサンプル密度を確認し、密度不足なら密集窓を採用— 結果を見ての変更は禁止)。
- **Rationale**: 割引は joint 全体に及ぶため、confirmation は marginal 指標(PRIMARY)だけでなく金銭指標の非悪化で担保する(017 の「校正改善でも Kelly 悪化なら不採用」と同じ思想)。馬単・三連単は stage 割引の影響が 2 着以降の順序に限定され、複勝系より感度が低いため AUX(参考)とする。
- **Alternatives considered**: 全 7 券種 MUST — 推定オッズ(double-pseudo)の雑音で偽陰性リスクが高い。複勝系 3 券種(割引が直撃する includes 系)に限定。

## D8: λ 探索範囲・最適化

- **Decision**: λ ∈ [0.1, 5.0]、golden-section(tol=1e-6、決定論)。λ_2 と λ_3 は独立に 1 次元最適化(それぞれの条件付き NLL は他方に依存しない)。境界張り付きは identity fallback + 警告。
- **Rationale**: 013/017/048 の γ と同一の範囲・手法で監査一貫性。λ_2 の NLL は勝者条件付きで λ_3 と分離、λ_3 は 1・2 着条件付きで λ_2 と分離 — 交互最適化不要(048 の 2 次元と違い真に分離可能)。
- **Alternatives considered**: 2 次元同時グリッド — 不要(目的関数が分離)。却下。
