# Research: 意思決定支援の表示強化 (021)

codex second opinion（spec/plan 段階）で出た 10 リスク + 抜け表示を踏まえた技術判断。各項は Decision / Rationale / Alternatives。

## R1: p と q の母集団一致（Critical, 憲法 IV）
- **Decision**: q は p と同一 canonical field（スクラッチ除外＋再正規化した出走馬集合）の win オッズに `market_implied_win_probs`(010) を適用し、その field 上で再正規化する。p（`canonical_win_probs`）と q を同じ predictions エンドポイントで co-locate する。
- **Rationale**: 別母集団で算出した p と q の差は数学的に無意味。同一関数経路で field を共有すれば構造的に一致を保証できる。
- **Alternatives**: odds エンドポイントで q を返す → p と field がずれるリスク（却下）。front で odds から q を計算 → canonical 再正規化の二重実装・ずれリスク（却下）。

## R2: reliability の出所（Critical, 憲法 III）
- **Decision**: walk-forward OOS の reliability bins を eval harness で算出し、adoption 時に `model_versions.metrics_summary`（既存 JSONB）へ追記。API はそれを read するだけ（再計算しない）。
- **Rationale**: 永続化済み serving 予測は過去レースに対し in-sample（モデルが学習で見た）→ 校正が楽観的に見える。walk-forward OOS のみが「実予測時点」の校正を表す。API は学習を走らせない（read-only・高速）。
- **Alternatives**: 永続 race_predictions を結果と join して都度計算 → in-sample 楽観 + API が重い（却下）。新テーブルに reliability を保存 → スキーマ変更不要な JSONB 追記で足りる（却下）。

## R3: p−q の中立提示（High, 誤誘導防止）
- **Decision**: p−q は「モデルと市場の意見の相違」として中立提示。利益示唆の語（買い/お買い得）・損益色（緑赤）・p−q によるソート/ハイライトを禁止。EV を出す場合も控除率と「p 誤差感応」を併記。
- **Rationale**: 020 で市場 q がモデル p より予測上手いと実証済み。p−q>0 を買いシグナルと短絡させると損失誘導になる。
- **Alternatives**: edge ランキング表示（却下: 誤誘導）。

## R4: 生 q と FL 補正 q'(013) の分離（High, 憲法 II/IV/V）
- **Decision**: 市場比較の主表示は生 q。q'(013) を出す場合は独立フィールド + 独立ラベル。同じ `q` ラベルを使い回さない。本 feature では q' 併記は deferred（生 q のみ）。
- **Rationale**: 生 q と q' は別の問いに答える。サイレント置換は監査・整合性を壊す。
- **Alternatives**: q を q' に置換 → 何を見ているか不明瞭（却下）。

## R5: reliability bin 設計（High, 過信防止）
- **Decision**: 等幅 bin（既存 ECE binning を流用）。各 bin の件数を前面表示。少数件 bin は統合 or「件数不足」で抑制。ECE は記述的診断として不確実性付きで提示。
- **Rationale**: 高確率帯は数頭しかなく実現勝率が不安定。件数なしの曲線は誤読を招く。
- **Alternatives**: 等頻度 bin（将来検討、まず等幅で eval 既存実装に整合）。

## R6 / US3: 「データ裏付け」→ 中立な出走歴インジケータに着地（High, 過信防止 + 憲法 II/III）
- **当初 Decision**: 「データ裏付け（条件カバレッジ）」= 馬の過去出走数の粗いカテゴリ。採用条件 = 過去 OOS で「裏付け弱群の校正が悪い」（weak ECE − strong ECE > 0.002）。
- **T016 検証結果（walk-forward OOS 2008–2024, 馬 prior-start 数で層別）**: weak(≤1) ECE 0.00969 / medium(2–5) 0.00949 / strong(≥6) 0.00758、weak−strong = **+0.00211**（閾値 +0.002 をわずか +0.00011 超過）。ECE は単調、weak は系統的に過大予測（mean_p 0.0685 > realized 0.0649）。
- **codex 確認（second opinion）**: margin +0.00011 は薄すぎ、race-level bootstrap CI / fold 別単調性なしでは「校正が悪い＝確率の信頼性が低い」という**校正シグナルとしての採用は NG**（035/036 の片側判断リスク）。ただし「**中立な出走歴の事実**（少/中/多、赤緑なし・ソートなし・『的中確信ではない』明示）」としてなら導入可。
- **最終 Decision**: **校正信頼度の主張は付けない**。US3 は `prior_starts_band`（few≤1 / some2–5 / many≥6）= **純粋な事実の出走歴インジケータ**として実装（weak/medium/strong の語・損益色・ソートを使わない、title で「予測精度の保証ではない」明示）。事実表示なので校正 robustness 検証は不要。**校正信頼度としての解釈（「この確率は当てにならない」表示）は deferred**（race-level bootstrap CI + fold 別単調性 + 境界感度の確認待ち）。
- **Alternatives**: (a) US3 完全 defer（codex も「統計的に妥当」）→ ただしユーザーは US3 着手を選択、出走歴の事実表示自体に意思決定支援価値があるため中立版を採用。(b) モデル分散ベース不確実性 → 単一 LightGBM では実装過剰・別 spec（defer）。

## R7: API 契約を UI 前に確定（High, 憲法 VI）
- **Decision**: q（`market_win_prob`）・data_backing・reliability エンドポイントの OpenAPI（フィールド・nullability・source ラベル・監査メタ・警告セマンティクス）を先に確定し、front 型は committed openapi から自動生成 + drift-check（015 既存機構）。
- **Rationale**: 契約先行で UI 手戻りと型乖離を防ぐ。
- **Alternatives**: front 先行（却下: 憲法 VI 違反）。

## R8: reliability の scope（Medium, 憲法 V）
- **Decision**: reliability は model_version（+ その walk-forward 評価の期間/出典）にスコープ。カバレッジ不完全なら「範囲外」を明示しバックフィルしない。
- **Rationale**: 異なるモデル/期間の混在は無意味。監査可能な単位に限定。

## R9: 表示派生値の非流用（Medium, 憲法 II）
- **Decision**: q/q'/p−q/reliability/EV/data_backing は read-only 一方向出力。leak-guard test で「これら表示派生値・オッズ・結果がモデル `model_input_features` に出現しない」ことを assert。
- **Rationale**: 表示値を特徴に流用するとリーク。

## R10: オッズ欠損・スクラッチ・as_of（Medium, 憲法 IV/V）
- **Decision**: q 欠損馬は 0 補完せず未提供（--）。as_of・オッズ source（確定/事前推定）・スクラッチ状態を常に表示。部分母集団で乖離を計算しない（R1 と連動）。
- **Rationale**: ゼロ補完や非比較値の表示は誤読を招く。

## 抜け表示（codex Missing Displays）
- **Decision**: (a) 「市場 q の方がモデル p より予測上手い（020 実証）」事実を画面に明示（FR-017）。(b) EV 表示時は控除率（takeout）と出典 + 「EV は p 誤差に敏感（O−1 小で敏感）」を併記（FR-018）。(c) オッズの取得時刻・source を表示（FR-015）。
- **Rationale**: 意思決定支援として前提条件を隠さない（正直さ）。

## 既存資産の再利用（新規実装を最小化）
- p: `api/selection.canonical_win_probs`、joint: `probability.engine.joint_probabilities`（009）
- q: `probability.market_odds.market_implied_win_probs`（010）
- reliability binning: eval `harness` の ECE 計算を拡張（独自指標を作らない、憲法 III）
- pseudo ラベル: front 既存 `PseudoBadge`/`SourceBadge` 単一描画経路（015）
- 型同期: 015 の committed `openapi.json` + 生成型 + drift-check
