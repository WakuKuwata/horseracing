# Research: exotic EV 推奨と疑似ROIバックテスト

009(結合確率)× 010(推定市場オッズ)を組み合わせ exotic EV を計算する設計判断。codex second opinion(plan.md の表)を反映。

## R1. p/q 同一 canonical 出走母集団(最重要 BLOCKER)

- **Decision**: P_model(009)と O_est(010)を**同一 canonical 母集団**で計算する。母集団は「`win_prob` が有効(>0)**かつ** `odds` が有効(>0、非取消・非除外)」の馬集合。p 側・q 側のどちらかが欠ける馬は母集団から**除外**し、各エンジンへの入力を**それぞれ再正規化**してから渡す。EV は同一キー空間の券種で対応付けて積を取る。
- **Rationale**: 009 は与えた win 確率を正規化、010 は無効オッズを落として再正規化する。別々に呼ぶと「p の母集団 ≠ q の母集団」になり、`EV=P_model(c)×O_est(c)` が**異なる母集団の積**になってキー不整合・確率破綻を生む(codex BLOCKER)。先に共通 canonical を確定すれば、両エンジンの券種キー(exacta tuple / trio frozenset 等)が一致し積が定義可能。
- **Alternatives**:
  - 各々独立に全馬で呼び後で交差 → キー集合がずれ、欠損馬を含む券種が一方にだけ存在。却下。
  - 欠損を確率/オッズ 0 で埋める → 010 が推定不能、009 が縮退。却下。
- **実装**: `canonical_field(predictions, odds)` が `(horse_keys, p_norm, odds_norm, excluded[])` を返し、`joint_probabilities(p_norm, field_size=len)` と `estimate_market_odds(odds_norm, field_size=len)` に同じ母集団を渡す。除外馬は監査ログ。

## R2. selection の JSONB 安全シリアライズ(BLOCKER)

- **Decision**: `recommendations.selection`(JSONB)には **frozenset/tuple を保存しない**。券種別に正準化した**配列**で保存:
  - 順序券種(exacta/trifecta): **順序付き配列** `[i, j]` / `[i, j, k]`(着順を保持)。
  - 無順序券種(quinella/wide/trio): **整列済み配列**(horse_number 昇順)`[i, j]` / `[i, j, k]`。
  - 単一(place): 単一要素 `[i]`。
  - 形式は**素の JSON 配列**(ラッパオブジェクトなし)。horse_number(整数)を使い selection 自体で自己完結。
    順序性は `recommendations.bet_type` 列から導出(冗長保存しない、spec.md AC3 と一致)。
- **Rationale**: 009/010 のキーは Python の frozenset/tuple で JSONB 非直列化。安定比較・往復・重複排除のため正準配列にする(codex BLOCKER)。順序要否は bet_type で既知のため配列のみで足りる。
- **Alternatives**: 文字列連結キー("3-7-1") → パースが脆い。却下。set 保存 → JSONB 非対応。却下。

## R3. 券種別 的中判定(BLOCKER)

- **Decision**: `exotic_selection.is_hit(selection, finish_order)` を券種別に実装:
  - exacta: 上位2着が `[i,j]` と順序一致。
  - trifecta: 上位3着が `[i,j,k]` と順序一致。
  - quinella: 上位2着の集合が `{i,j}` に一致(順不同)。
  - trio: 上位3着の集合が `{i,j,k}` に一致(順不同)。
  - wide: `{i,j}` が上位3着(field により 8頭+:top3、5–7頭:top2…009 と同じ field 規則)に**両方含まれる**。
  - place: `i` が払戻対象順位内(field 規則 top2/top3)。
- **Rationale**: 既存 roi(007)は単勝1頭専用で exotic を採点できない(codex BLOCKER)。009 の field-size 規則(5–7=top2, 8+=top3, ≤4=none)を wide/place の的中境界と共有して整合。
- **Alternatives**: 払戻表マスタ参照 → 実 exotic オッズ無し(本 feature 範囲外)。却下。

## R4. 複勝/ワイドの複数当たり(ベット単位)

- **Decision**: 複勝・ワイドは**ベット(selection)単位**で採点。1レースで複数の place/wide selection がそれぞれ的中しうるが、各 recommendation 行は独立に hit/miss と払戻を持つ。ROI 集計はベット行の総和。
- **Rationale**: 複勝・ワイドは構造上 1レース複数的中が起こりうる(codex)。レース単位で1勝敗にすると過小評価。ベット単位なら他券種と一様に総払戻/総賭金で ROI を算出できる。
- **Alternatives**: レース単位最良1点 → 情報損失。却下。

## R5. EV≥閾値 上位 K による組み合わせ抑制

- **Decision**: 各(レース, 券種)で全候補の EV を計算し、`EV ≥ threshold` を満たすものを `(-EV, selection_key)` で**決定論整列**して**上位 K** に制限(K 設定可能、券種別 K も許容)。0件ならその券種は見送り。
- **Rationale**: 三連単 ~ P(N,3)(18頭で 4896)を全保存すると行爆発(codex)。EV 上位 K で価値ある買い目に限定。タイブレークに selection_key(正準配列の辞書順)を使い決定論担保。
- **Alternatives**: 全保存 → 行爆発。確率上位 K → EV 基準と乖離。却下。

## R6. exotic baseline(評価先行 III)

- **Decision**: 券種別 baseline 2 種を**同一条件**(同一レース・同一 canonical 母集団・同一 stake・同一 K・推定オッズ採点)で比較:
  - **最低 O_est baseline**: 各券種で推定オッズ最小(=市場最有力)の組み合わせを K 点。
  - **均等(uniform) baseline**: 候補から決定論シードで K 点を均等選択。
  - 成功判定 = EV 戦略が**baseline を上回る**(回収率/的中率)。絶対 >1.0 ではない(二重疑似のため)。
- **Rationale**: 010 同様、推定オッズ ROI は二重疑似で絶対水準の信頼性が低い。相対比較で推奨ロジックの優位を測る(憲法 III、codex)。
- **Alternatives**: >1.0 を成功 → 二重疑似で誤誘導。却下。人気=低オッズは「最低 O_est」で代理。

## R7. リーク境界・決定論・二重疑似明示

- **Decision**: 買い目決定は `win_prob`(p)+ `odds`(q)+ entry_status のみを入力とし、結果(着順)は**採点段階でのみ**参照。`recommendations` は append-only。保存は `is_estimated_odds=true`、`market_odds_used=null`、`estimated_market_odds_used=O_est`、`pseudo_odds=1/P_model`、`pseudo_roi=EV−1`。全バックテスト出力に **「二重疑似(推定オッズ + PL 外挿)」** ラベル。`logic_version` に EV 式・閾値・K・stake・控除率・q ソース・cap・母集団ポリシー・009/010 版を含める。
- **Rationale**: 憲法 II(リーク防止)/ V(監査)/ codex。p と q を別経路で保ち混同しない。二重疑似を明示し過信を防ぐ。
- **Alternatives**: market_odds_used に O_est を入れる → 実オッズ由来と誤認。却下。

## まとめ(設計判断 → 要件)

| 研究項目 | 対応 FR / SC |
|---|---|
| R1 canonical 母集団 | FR-002 / SC-002 |
| R2 selection JSONB | FR-005 / SC-003 |
| R3 券種別的中 | FR-007 / SC-004 |
| R4 複数当たり | FR-008 |
| R5 上位 K | FR-003 |
| R6 baseline | FR-009 / SC-005 |
| R7 リーク/監査/二重疑似 | FR-004 / FR-010 / FR-012 / SC-006 |
