# Research: race dispersion & p/q divergence readout

Phase 0。設計判断と、codex 設計レビュー(session 019f4a8d)の突合。全 NEEDS CLARIFICATION は解決済み。

## D1. 軸A の主役: q由来 か 校正済み p由来 か

- **Decision**: **q由来を本体、校正済み p(048 two_gamma)由来は q との差分のみ**。生 p 単独の集中度は表示しない。
- **Rationale**: 実際の荒れの予測は q が優る(047: 全セグメントで市場 q が LogLoss で p に勝ち、特に本命帯でよく校正)。生 p は本命 tail 圧縮(047: 本命帯 p≈0.185 vs 市場 0.405 ≈ 実現 0.413)で荒れを系統的に過大評価する。p と q を対等な2数値として並べて「お好きな方で」とやると確証バイアスで動機づけられた推論をロンダリングする(021 が避けたい形)。
- **Alternatives**: (a) p主 → 読みやすさ予測で q に劣ると分かっている以上、誠実さで劣る(却下)。(b) 両者対等並列 → 上記バイアス理由で却下。
- **codex 1**: 支持。追加で「q 欠損/stale は 021 `canonical_consistent`(p/q 母集団不一致)とは別のデータ可用性失敗。明示 unavailable 理由を足し、p 差分も抑制せよ」→ 採用(FR-005)。

## D2. 軸A の主指標: max(q) か 正規化エントロピー か

- **Decision**: **バンドの見出し = 正規化エントロピー `H = -Σ q·ln q / ln N`。生数値として max(q)(本命勝率)と上位3頭累積を併記**。競合する2つのバンドラベルは作らない。
- **Rationale**: max(q) は直感的で longshot tail のノイズに頑健だが**頭数依存**で少頭数で退化。正規化エントロピーは ≤5 と 16+ を跨いで比較可能(log N で割る)。codex 3 の推奨どおり「見出しは正規化エントロピー1本・生数値に max(q)」で頭数頑健性と直感性を両立。
- **Alternatives**: max(q) 単独 → 頭数跨ぎ比較不可(却下)。HHI → エントロピーと情報同等で直感性劣る(不採用)。
- **偽精度緩和**: バンド横に必ず生数値併記(021 `prior_starts_band` が裏に件数を持つのと同型)。合成単一「荒れ指数」は作らない(分解した事実を出す)。

## D3. 5段バンド境界の決め方(結果リーク回避)

- **Decision**: **凍結した過去窓での正規化エントロピーの5分位**。境界フィットは表示対象レースより厳密前(`(race_date, race_id)` タイブレーク、013/017 規律)のレースのみ。結果(荒れたか)は境界決定に一切使わない(047/048 事前登録)。artifact に metric/頭数バケット/フィット窓/as-of/version を記録(憲法 V)。
- **Rationale**: 予測子(エントロピー)の分布だけを見て分位を切る=結果を見ないので outcome リークなし。眼分量閾値より根拠が強い。047「結果を見る前にセグメント定義固定」と同型。
- **codex 2**: 「global 5分位は頭数と相互作用。正規化エントロピーを global に使うか、頭数バケット(047 の ≤8/9-13/≥14)内で凍結分位を取るか」→ **primary = 正規化エントロピーの global 凍結5分位**(エントロピーが log N で頭数を大方吸収)。**field-size 残留依存は US3 診断でチェックし、出たら field-size バケット内5分位への v2 を training 窓で事前登録**(codex 6 の v2 規律)。
- **Alternatives**: max(q) の分位 → 頭数依存が強く不採用。結果ベース(荒れた率の分位)→ outcome リークで却下。

## D4. 隣接バンドが統計的に区別できない場合

- **Decision**: **結果を見てからバンドを併合しない**。Wilson / race-cluster bootstrap CI で隣接バンドの realized-chaos が区別不能なら「隣接バンドは有意差なし」と正直に開示し記述ラベルは維持。段数変更は training 窓での事前登録 v2 として later OOS 窓で検証。
- **Rationale**: OOS 結果を見て段数/境界を動かす=047 事前登録規律違反(チューニング=リーク)。
- **codex 6**: 支持(そのまま採用、FR-014)。

## D5. 軸B(p vs q 意見差)の見せ方

- **Decision**: **3層プログレッシブ開示**。(1)race-level 中立サマリ(本命=q1位 に対するモデルの向き・モデル上位N頭に入る人気薄の有無・p順位とq順位の一致度)、(2)既存 040 per-horse `divergence_band` バッジ(**無改変**)、(3)全馬 p/q テーブル展開(040 row-expand 同型)。
- **Rationale**: 040 の既存 UI(行展開)に自然に載る。段階開示で過負荷回避。全て事実文言、「モデルが正しい」「買い」は言わない(040 継承)。
- **codex 4/5/7**: 「per-horse divergence_band は改変せず race-level nullable + 展開のみ追加」「canonical_consistent=false は軸B と校正済み p 差分を抑制」「057 複数モデル時はどの選択モデルの p か明示」「q 集計は market-derived pseudo/表示 → 015/021 の pseudo/source バッジ経路、pseudo_roi 流用禁止・真確率示唆禁止」→ 全採用(FR-006/010/011)。

## D6. リーク境界の機械固定

- **Decision**: token 禁止(registry・materialized columns に軸フィールド名を出さない)+ import-graph ガード(api が betting/training を import しない)+ **behavioral 不変テスト**(表示軸の計算を変えても model input features と decision-support 経路の選択 p がバイト不変)。
- **codex 4(重要な微修正)**: 「grep/token 禁止は必要だが不十分。020/023/026 は behavioral leak-guard。**ただし『全 odds 変更が全モデルを不変にする』とは主張するな**(060 に market-offset candidate があり、今走オッズがモデルに入る経路が存在)。主張は『本 feature の新 display 集計が feature/training 経路に入らない』に限定せよ」→ 採用。テストは「display-axis mutation が decision-support p を変えない」「display-axis token が registry/materialized_columns に無い」に限定。
- **Rationale**: 060 の存在を無視した過剰主張は誤り。表示計器のリーク面は「表示派生値が特徴に戻らないこと」であって「オッズが全モデルに影響しないこと」ではない。

## D7. スキーマ/契約

- **Decision**: **スキーマ変更ゼロ・migration なし**。既存 predictions 応答に `race_dispersion` / `race_divergence` の nullable オブジェクトを純追加。API GET-only。OpenAPI 純追加で front/admin snapshot + drift-check 緑。betting/training を api から import しない(既存境界テスト)。
- **codex 5**: 「pure-additive なら schema 変更なしで着地可。021 の market_win_prob/canonical_consistent/odds_as_of を再利用、040 の中立 divergence を race-level に拡張、049 の read-time 表示規律に従う。既存 per-horse divergence_band を改変するな。損益色/edge/value 語なし・乖離/荒れソートなし・API GET-only・OpenAPI 純追加」→ 採用(FR-018)。

## codex 見落とし制約(全採用)

- 境界 artifact に metric/バケット/フィット窓/as-of/version 記録(憲法 V)→ data-model.md の DispersionBoundary。
- 取消/非 starter は q 正規化前に除外(010/021)→ FR-004。
- 境界フィット窓内の過去レースを OOS とラベルしない → FR-013。
- 057 複数モデル: 軸B はどの選択モデル p か明示 → FR-011。
- 診断の chaos-rate は dead heat/cancellation/void を評価前に予約定義 → FR-013 / US3-4。

## 残論点(plan では確定せず deferred)

- field-size バケット内5分位への v2 移行: US3 診断で残留依存が確認された場合に training 窓で事前登録(今は global エントロピー分位で開始)。
- 荒れ度スナップショットの時系列永続化・複数レース横断ダッシュボード。
- 軸A×軸B を組み合わせた「見送り推奨」表示(買いシグナル化リスクで別 spec)。
