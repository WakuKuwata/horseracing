# Research: 過去走の市場評価 as-of 特徴(058)

Phase 0。非自明な設計判断。codex unavailable → single-opinion(既存 020/023/041/056/057 パターンとの整合で代替検証)。

## D1: leak-guard の型転換(grep 型 → 挙動型)

**Decision**: 従来の「モジュールソースに odds/payout/dividend/popularity トークン禁止」の grep 型 leak-guard を **past_market_features.py には適用しない**。代わりに挙動型で守る:
- 今走の人気を変えても特徴不変(strictly-before)
- 同日他レース・未来レースを変えても不変
- 過去走の人気を変えると特徴が**変わる**(positive test = 実際に過去人気を使っている証明)
- 特徴名は禁止トークン非含有(asof_mkt_rank_avg 等)→ 既存グローバル名検査(test_feature020 の model_input_features)を通過
- 今走の人気/オッズそのものは model_input_features に含まれない

**Rationale**: 058 は repo 初の「市場データを意図的に使う」特徴。モジュールが正当に `race_horses.popularity` を読むため grep 型は誤検知する。真の保護は「今走の値がリークしないこと」= 挙動不変であり、grep より本質的。041 が "gain"→"late_gain" で名前トークンを回避した前例に沿う。

**Alternatives considered**:
- grep 型を維持し popularity を別名でロード: 難読化で保守性低下。挙動テストの方が強い。
- 今走オッズ量も特徴化: serving で確定オッズ不在(B2)+ p が市場コピー化。スコープ外。

## D2: default モデルは past_market 非含有(p⊥q)

**Decision**: default(意思決定支援)モデルは `drop_features=past_market columns` で学習・serving。past_market は精度最優先モデル専用。

**Rationale**: 製品価値(p と市場 q の独立、021/040 の乖離表示)を維持。past_market を default に入れると p が市場に寄り乖離表示の情報量が落ちる(feature-020 メモの p×q blend α=0 所見)。057 の切替基盤で「独立 default + 精度最優先オプション」を両立。

**Alternatives considered**:
- past_market を全モデルに入れ default 昇格: 独立性喪失。ユーザー方針(B は別用途で共存)に反する。

## D3: 採用ゲートに top2/top3 非悪化 MUST を追加

**Decision**: PRIMARY(win LogLoss 改善 + ECE 非悪化 + fold guards)に加え、**top2/top3 平均 LogLoss 非悪化を MUST**。

**Rationale**: ユーザー目的が 1・2・3着の予測率。既存 harness は top2/top3 を Harville 導出で計算済み(feature_eval の label 引数 or overall[label])=読み取りのみで追加装置不要。042 でも top2/top3 non-regression を昇格ゲートにした前例。

## D4: binary spike の限界寄与を production で再確認

**Decision**: フル feature-eval(binary)採用後、production 構成(pl_topk+TE(jockey/trainer)+isotonic)で `model-eval` を回し win/top2/top3 の production 寄与を確認してから精度最優先モデルを登録。

**Rationale**: 020 教訓 —「弱いモデルで効く特徴は強い production モデルでの限界寄与が小さいことがある」。binary spike の −0.00028(win、059 の上の再測定)は方向確認であって production 値ではない。059 の前例(binary −0.00114 → pl_topk −0.00018 に 6 倍縮小)から production ではさらに縮む公算 → 登録前に production(pl_topk+TE)で確認。

## D5: 精度最優先モデルは非 active・057 で共存

**Decision**: 採用時、精度最優先モデルを model_versions に登録するが **非 active**。既存の active(意思決定支援モデル)は変えない。057 の `set-model-label` で用途ラベル付与、`predict-backfill --model-version` で予測生成 → レース詳細で切替可能。

**Rationale**: 057 FR-009(採用 ⊥ eval 合格)。default は独立性維持のため意思決定支援のまま。共存 UI/契約は 057 で実装済=本 feature で新設しない。

## fingerprint / parity メモ

- `source_fingerprint` は `_hash_frame(race_horses, list(columns))` = **全列ハッシュ**。loader に popularity を足したので **自動的に fingerprint 包含**(popularity backfill も fail-closed 検知)。明示変更不要。
- materialize parity: build_asof_features 単一源に結線済。実 DB で bit 一致を検証(FEATURE_VERSION 014・新 4 列)。

## セルフレビュー総括

- リーク: 挙動型テスト(今走/同日/未来不変 + 過去 positive)が本質保護。
- 契約: スキーマ/API/migration 変更なし(057 基盤利用)。
- 残リスク: production 寄与が binary より小さい可能性(D4 で登録前確認)。default 汚染は drop_features で防止(SC-005 で default 不変確認)。
