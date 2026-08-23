# codex 設計レビュー(spec 段階)— 採否

**取得経路**: `codex:codex-rescue` agent は 49 秒で `turn_aborted (interrupted)`・応答ゼロ(既知)。
`codex exec --sandbox read-only --skip-git-repo-check` 直叩きで再試行 1 回 → 成功(4 問すべて回答)。
生出力: [codex-review-raw.md](codex-review-raw.md)。リポジトリは読ませず、必要情報はプロンプトに埋め込んだ。

| # | codex の指摘 | 採否 | 反映先 |
|---|---|---|---|
| Q1 | 主測定は実窓(切替後の再学習 A/B)であるべき。B−A は「デプロイ時の総効果」で交絡ではなく estimand。擬似カットオフは netkeiba 切替と交換可能でない | **不採用(ユーザー決定 2026-08-23・2 回確認)** — 実窓単独は MDE≈0.009 で NO_DECISION が既定になる。懸念(非交換性)は Q4 の transportability ゲートで受け、実窓の明確な悪化は REJECT | FR-006/006a/007/007a・Assumptions |
| Q1 | 2×2(正規化 × 供給元レジーム指示子)で綴り効果と旗効果を分離 | **不採用** — 計算 2 倍・指示子は採用しない診断のみ。REJECT 時の follow-up 候補として記録 | Assumptions |
| Q2 | 「データ修復・bump なし」は擁護不能。replay +0.029 はモデルから見える意味変更。in-place backfill はミスマッチ窓とロールバック不能を生む。正準表現を旧表現と並置し、artifact に版+変換 hash+順序つき語彙 hash を束ね、serving は不一致を拒否、モデル+表現を原子的に切替 | **採用(ユーザー決定)** — 正規化を特徴層に置き FEATURE_VERSION bump(017 型)。DB は生のまま(provenance)・取込変更なし・backfill なし。旧モデルは旧表現で serve・切替/切戻しはモデルのみ | US2・FR-003/008/009/010/011/014・SC-003..006 |
| Q3 | 正準は JRA-VAN トークン、NFKC は照合キーのみ | **採用(既定と一致)** | FR-001 |
| Q3 | netkeiba `オープン` は `ｵｰﾌﾟﾝ`+`OP(L)` の混合なので `ｵｰﾌﾟﾝ` への正規化は綴りの統一でなく意味変更。`重賞` は別名でない粗い旧値 | **採用** — 変換表を `１勝/２勝/３勝` に限定、`オープン` は据え置き(US3 でリステッドが復元できた場合のみ別途事前登録)、`重賞` 据え置き | FR-002・Key Entities・Edge Cases |
| Q3 | LightGBM のカテゴリコードは artifact ローカル。各 artifact に順序つき `pandas_categorical` を持たせ、再読込して予測一致を検証 | **採用** | FR-011・SC-004 |
| Q4 | 最大の誤判定リスク=非交換なデータ品質レジームへの平均効果の移転。pool が符号反転を隠す。各カットオフ・層・leave-one-cutoff-out で方向一致を事前登録、反転は NO_DECISION | **採用** | FR-007a |

**残リスク(明示)**: シミュレーション主測定は「綴り分裂そのもののコスト」を測り、実際の切替に伴う
レジーム差は再現しない。実窓ガード+transportability で方向だけは担保するが、実窓の効果量の
推定は検出力不足のまま(反実仮想として報告)。

## plan 段階(`codex exec` 直叩き 1 回・成功・5 問)

生出力: [codex-review-plan-raw.md](codex-review-plan-raw.md)。採否の表は research.md D12。要点:
採用 3(artifact 主導の dispatch+allowlist+golden / カテゴリ化前の語彙外監査+注入テスト /
明示引数+NaN 不増 assert)・部分採用 1(実窓の層別は報告のみ)・不採用 1(移行期窓=配備経路で
発生しない衝撃)。
