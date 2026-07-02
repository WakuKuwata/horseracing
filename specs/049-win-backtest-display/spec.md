# Feature Specification: win 的中/回収バックテスト表示 (Win Backtest Display)

**Feature Branch**: `049-win-backtest-display` / **Created**: 2026-07-03 / **Status**: Draft
**Input**: 044/048 の backfill で 2024–2025 の win 推奨が実データで揃った(6,453 レース・win 36,811 件、全て real 単勝オッズ)。しかし製品は「予測時の期待値(pseudo_roi)」しか出しておらず、**その推奨が実際に当たったか・real オッズでいくら回収したか**という事後の事実(retrospective)を表示できていない。read-only で per-win-recommendation の 的中(hit)/実現回収(realized return)を追加する。

## 背景と目的
045 で「win は real 単勝オッズ(race_horses.odds、is_estimated_odds=False)を使う唯一の券種」と確立済み。よって win 推奨は**実配当ベースの正直な事後評価**が唯一できる券種。`race_results.finish_order==1`(result_status='finished')が勝者 → 推奨馬 selection.horse_id と join すれば、pseudo でない実現回収が出せる。
**製品目的の継承(021/047 規律)**: これは「市場に勝てる」主張でも買いシグナルでもなく、**正直な意思決定支援の一部としての過去実績表示**。予測時の pseudo_roi(期待値・疑似)と、結果の realized_roi(実績・real)を**視覚的に明確分離**し、集計は「retrospective・in-sample・将来利益を示さない」と常時明示する。

## スコープ
- **win のみ**(real 単勝オッズを持つ唯一の券種)。exotic は推定オッズ(double-pseudo)+ real 配当(exotic_odds)がほぼ空 → 実現回収が誤解を招くため **スコープ外(deferred)**。
- read-only 表示のみ。**スキーマ変更なし・migration なし・書き込み経路なし**。API は既存 `RecommendationRow` に nullable フィールド追加、front は RecommendationPanel 拡張。
- 的中判定は **api 内の純粋比較**(選択馬の finish_order==1)。api は read-only 境界で **betting(書き込みパッケージ)を import しない** — betting/roi.py の score_backtest(horse_id∈winners・DNF=loss)と同一意味論を api 側に最小実装(1 述語、ドリフト面は definitional なため無し)。

## 実現値の定義(win・settled 時のみ)
race_id ごとに race_results を 1 回ロードし、各 win 推奨について:
- **settled**: 当該レースに公式結果あり(finished 行が存在)
- **hit**: 選択馬(selection.horse_id)の result 行が finish_order==1(同着含む)。settled かつ選択馬の result 行なし(推奨後取消等)= **void(hit=null)**、DNF(stopped/finish_order 欠)= 不的中
- **dead_heat**: 選択馬が finish_order==1 だが同一レースに finish_order==1 が複数(87 レース該当)= 実配当は分割されるため注記用フラグ
- **realized_return**: 的中=`market_odds_used`(real 単勝オッズ, per-unit 回収倍率)、不的中=0.0、void/unsettled=null
- **realized_roi**: `realized_return − 1`(的中で odds−1、不的中で −1、void/unsettled=null)
- per-unit(stake 非依存)を主とする。stake_fraction 加重は US2 の集計側でのみ扱う(null stake=330 件は集計から除外・明示)。

## User Stories
- **US1 (P1)**: race 詳細の推奨パネルで、settled な win 推奨行に「的中/不的中/void」バッジ + 実現回収(×odds)+ realized_roi を表示。予測時 pseudo 列(pseudo_odds/pseudo_roi、疑似バッジ維持)と**結果列(実績・real・バッジ無し)を列グループで分離**、同着は注記。
- **US2 (P2)**: 当該レースの win 推奨の**過去実績サマリ**(n_settled/n_hit/hit_rate/mean realized_roi/recovery_rate=Σ realized_return÷n)を、**「過去実績・参考(retrospective, in-sample、将来の利益を示すものではない)」の必須ラベル**付き・損益色なし・ソートなしで表示。

## Requirements
- **FR-001**: 実現フィールド(settled/hit/dead_heat/realized_return/realized_roi)は win 推奨のみ populate、非 win はすべて null(exotic スコープ外)。
- **FR-002**: 的中判定は read-only api 内の純粋比較で行い、**betting を import しない**(読取専用境界)。全 API path が GET のまま(drift-check・read-only test 維持)。
- **FR-003**: realized_* は real(real odds・real result)= **pseudo バッジを付けない**が、必ず「実績/結果」ラベル下に置き、予測時 pseudo_roi と**同一視されない**視覚分離(列グループ + 実績マーカー)。pseudo 値は従来どおり PseudoValue/PseudoBadge を経由(V の "no pseudo without badge" 不変・named test 維持)。
- **FR-004**: void(settled だが選択馬 result 無し)は不的中と区別(hit=null、回収に算入しない)。DNF(stopped)= settled かつ不的中(realized_roi=−1)。同着 win は的中 + dead_heat フラグ(実配当分割の注記)。
- **FR-005**: realized_* は**表示専用**で feature に戻さない(憲法 II leak 境界)。read 時計算のみ・永続化しない・feature_snapshots に混入しない(既存 leak-guard で担保、新リーク面ゼロ)。
- **FR-006**: US2 集計は事実記述に限定(n・hit_rate 併記必須、損益色・利益語・ソート・将来射影の禁止=021 規律継承)。
- **FR-007**: スキーマ・migration なし。OpenAPI 契約は先行更新(committed front/openapi.json + 型再生成 + drift-check)。

## Success Criteria
- **SC-001**: settled な win 推奨行に hit/実現回収/realized_roi が表示され、的中=×odds・不的中=−100%・void/未 settled=「—」で描画される。
- **SC-002**: 同着 win(87 レース該当の 1 例)で hit=true・dead_heat=true・注記が出る。stopped 馬で不的中(realized_roi=−1)。
- **SC-003**: 予測時 pseudo_roi(疑似バッジ)と実績 realized_roi(バッジ無し・実績ラベル)が別列グループで、pseudo-label 不変テストが緑(実績 real 値は data-pseudo を持たない)。
- **SC-004**: API 全 path GET のまま・drift-check 緑・migration head 不変。api は betting を import しない(境界テスト)。
- **SC-005**: 実 DB E2E で settled レースの win 推奨に的中/回収が出る(2025-01-05 等)。api/front スイート緑。

## Assumptions
- realized_return は per-unit(1 単位賭けの回収倍率)。stake 加重の bankroll シミュ(007/016 の walk-forward + baseline)は本 feature のスコープ外(あれは採否ゲート用の厳密版)。
- 同着 win の実配当は分割されるが DB は単一 recorded odds のみ保持 → dead_heat フラグで注記し recorded odds をそのまま表示(限界を開示)。
- codex CLI はセッション内 3 回起動失敗 → single-opinion(read-only 境界・021 表示規律・045 real-odds 前例に基づく)。

## Deferred
exotic の的中/回収(real 配当 exotic_odds のフル取得が前提)・stake 加重 bankroll バックテスト表示・複数レース横断の実績ダッシュボード(日付/会場フィルタ集計)・race 詳細への一般的 finish_order 表示・的中率の walk-forward vs baseline 比較(007/016 の領域)。
