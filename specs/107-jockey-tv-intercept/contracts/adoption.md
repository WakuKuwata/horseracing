# Contract: ADOPT 分岐の本実装(概略・verdict=ADOPT のときのみ確定)

verdict が ADOPT でない限りこの契約は発効しない(閉鎖時は本ファイルを「不発効」と注記)。

## 原則
- **artifact 凍結**: b(騎手 ID→実数の全表)・λ(raw/クランプ後)・τ̂²・window 定義
  (W・train_end・推定行数)・MIN_RIDES を model artifact に保存。読込時に整合検証
  (b の鍵数・数値有限性・メタデータ突き合わせ)。serving は artifact の値のみを使う
  (実行時再推定禁止)
- **opt-in 構造**: ModelRecipe に既定 None のフィールドを追加し
  `NEW_HASH_DEFAULT_OMISSIONS`(099 機構)で既存 recipe_hash を不変に保つ。
  両系 recipe_hash スナップショットテスト必須
- **serving**: raw score への外部加算 → レース内 softmax → isotonic(artifact の校正器)。
  b に無い騎手は 0。logic_version に `;jti=w730` マーカー(冪等キー参加は 076/091 の
  regime marker 前例に従う)
- **昇格**: 候補モデル登録(active にしない)→ 標準昇格ゲート(標準窓非劣化+本
  verdict+直近窓・nk: 可搬性の確認)をすべて通過した場合のみユーザー承認で昇格
- **運用規約**: b は再学習サイクル(年 1 回目安)で更新。estimand は「年次更新手続き」で
  あり、放置された b の性能は測定されていないことを運用文書に明記

---

## 不発効(2026-09-02)

confirmatory の verdict は **REJECT**(diff −0.001902・total CI [−0.005029, +0.001248]・
gate_hard_fail)。本契約は発効しない。騎手軸はこの設計族
(加法切片・730 日窓・30 騎乗ゲート・年次更新・isotonic 再 fit)について閉鎖された。
再開には新規の事前登録が必要。
