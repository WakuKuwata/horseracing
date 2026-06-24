# Research: 結合確率エンジン

Phase 0。NEEDS CLARIFICATION なし。codex の確率レビューを反映した設計判断を記録する。

## R1. Plackett-Luce 導出と各券種の定義

- **Decision**: 正規化済み単勝確率 `p_i`(1着確率)から:
  - **馬単** `exacta(i,j) = p_i · p_j/(1−p_i)`。
  - **三連単** `trifecta(i,j,k) = p_i · p_j/(1−p_i) · p_k/(1−p_i−p_j)`。
  - **馬連** `quinella{i,j} = exacta(i,j) + exacta(j,i)`。
  - **三連複** `trio{i,j,k} = Σ(6 順序) trifecta(...)`。
  - **ワイド** `wide{i,j} = Σ_{k≠i,j} trio{i,j,k}`(= 2 頭がともに top3 に入る確率。**独立積
    `top3_i×top3_j` で近似しない**)。
  - **複勝** `place_i = `(8 頭以上)top3 包含、(5–7 頭)top2 包含、(≤4 頭)該当なし。top2/top3 包含は
    `harville_topk` の top2/top3 を直接採用。
  - **単勝** `win_i = p_i`(パススルー)。
- **Rationale**: codex — PL の逐次条件付きが復元なしサンプリングとして正しい。ワイドは ordered top-3 列挙の和
  (= trio の第3頭和)でなければ整合性が壊れる(独立仮定は憲法 IV 違反)。複勝=top-N 包含は `harville_topk` と一致。
- **Alternatives considered**: ワイド独立積 → 整合性破壊。複勝を独自計算 → harville と二重定義になり乖離リスク。

## R2. 再正規化と数値安定(順序固定)

- **Decision**: 固定順序 **「取消・除外を母集団から除去 → 残存馬の `p` を Σ=1 に再正規化 → `[eps,1-eps]` に clip →
  再度 Σ=1 に正規化 → PL 派生」**。PL 分母 `1−p_i`, `1−p_i−p_j` はこの正規化後の `p` で計算する。`eps=1e-9` 程度。
- **Rationale**: codex BLOCKER — 再正規化を分母計算より先に行わないと PL 式が壊れる。`harville_topk` の「分母≤eps を
  無視(skip)」する挙動は質量欠損を招くため**本計算に継承しない**(clip + 再正規化で安定化)。
- **Alternatives considered**: harville の skip をそのまま流用 → Σ<1 の過小和。生 `p` で分母計算 → 除外時に破綻。

## R3. 整合性の自己検査(必須テスト)

- **Decision**: 許容誤差付きで全 assert:
  `Σ_{i≠j} exacta=1`、`Σ_{順序付き3つ組} trifecta=1`、`quinella{i,j}=exacta(i,j)+exacta(j,i)`、
  `wide{i,j} >= quinella{i,j}`、`Σ_{i を含む順序付き3つ組} trifecta = harville_topk.top3[i]`、
  `Σ_j(exacta(i,j)+exacta(j,i)) = harville_topk.top2[i]`、`place_i ∈ [0,1]`、`p_i>=p_j ⟹ place_i>=place_j`。
  さらに N=3/4・一様の**手計算 golden**で全券種値を固定。
- **Rationale**: codex — 035/036 の確率校正ミス対策。joint と harville の独立計算が一致することが正しさの最強の証拠。
- **Alternatives considered**: 総和のみ検査 → 周辺の誤りを見逃す。

## R4. 計算量

- **Decision**: 分母の累積和を事前計算し、馬単/馬連 O(N^2)、三連単/三連複/ワイド O(N^3) を 1 パスで集計。N≤18 で
  三連単 ≈4900 項=trivial。
- **Rationale**: codex/Feature 003 と同様、JRA 頭数(≤18)で十分高速。
- **Alternatives considered**: モンテカルロ → 非決定論/遅い。閉形式列挙を採用。

## R5. 校正評価(評価先行)

- **Decision**: 過去レースの単勝確率と確定結果から、**実現した馬単/三連単の組み合わせ**に対する NLL/Brier を計測
  (`eval.metrics` の log_loss/brier を再利用)。**独立積 baseline**(`exacta∝p_i·p_j`、`trifecta∝p_i·p_j·p_k` を
  各々 Σ=1 に再正規化)と同一レース集合・同一条件で比較。確率導出は結果/オッズ非参照(採点のみ結果使用)。
- **Rationale**: codex/憲法 III — PL が理論的に正しいことを独立積 baseline との校正差で実証。疎な組み合わせはレース
  単位で集計。
- **Alternatives considered**: ROI 評価 → exotic オッズが無い(将来)。校正(NLL/Brier)を主指標に。

## R6. 同着と複勝頭数規則

- **Decision**: 複勝は JRA 規則(5–7 頭=top2, 8+=top3, ≤4=該当なし)。同着は**評価採点**で確定着順が券種条件を満たすかを
  JRA 規則で判定(同着 1 着は的中に含む)。**確率導出は連続**で同着を区別しない(同着確率は実質ゼロ)。
- **Rationale**: 実務の券種規則に整合。確率モデルは ties を持たない PL。
- **Alternatives considered**: 同着確率モデル → 複雑・低頻度、将来。
