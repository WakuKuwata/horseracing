# 174 発走直前(T−3 分)の判断記録 `decision_quotes`

**Status: 実装完了・ローカル本番 DB に 0022 適用済み・`capture_only_dates`=10/31・11/1・11/7・11/8 登録済み(2026-10-29・main 未 push)。運用は `manual.md`・実装の記録は `result-review.md`。利用者の決定: ①憲法 V は「表ごとに 1 レース 1 行の判断記録」として可(憲法 1.0.1 の注記)②1 日単位の明示起動は手動実行の範囲 ③T−10 の 2 点目は v1 で持たない。capture-only モードも承認。判断材料は `docs/plan/t3-capture-decision-materials-20261029.md`。**

## 目的

注目条件(138)と期待回収率列(137/141)について、**買える価格に近い時点(発走 3 分前)の quote と同時刻の予測を 1 レース 1 行で凍結**し、公式払戻(139)で精算できる前向きの記録を作る。173 で「価格ずれは判断時モデルでは埋まらず、支払価格が判断価格と違うことが正体」と確定したことを受けた、唯一残る測定手段。利益の改善は約束しない。

## スコープ

- 新表 `decision_quotes`(migration 0022)と、開催日に人が起動する長時間プロセス `live near-post-capture --date`。
- 読み取り API/front ページは後段 spec(B8 と同型)。本 spec は記録まで。
- 2 点目(T−10)は含めない(v1)。

## 用語

- **T−3 捕捉**: `post_time − 180 秒 ± 60 秒` の窓で 1 回だけ quote を取り保存すること。同時刻の 2 レースは race_id 昇順で 2 本目を T−4 に割り当てる(`capture_target`)。
- **判断記録**: その quote から計算した ens15(15 seed)・単 seed・141 混合の全馬の p̂ と EV。

## 機能要件

- FR-001 1 レースにつき `decision_quotes` の `active` 行は生涯 1 行(`UNIQUE(race_id)` の部分 index・append-only トリガ)。再捕捉しない。void は `scratched`/`field_changed` のみ追記。
- FR-002 捕捉前の判定順(086 と同型・fetch は最後): 結果未確定 → post_time 既知 → 発走前 → 窓内(T−3±1 分) → 出走表完全 → 行が無い → advisory lock → 共有リミッタの枠 → fetch → 出走構成の再確認 → 保存。窓外・発走後は `skipped`。
- FR-003 保存する値: `captured_at`・payload の `official_datetime`(`official_at`)・`seconds_to_post`・馬ごとの単勝(低/高)・複勝(低/高)・人気・出走馬集合の digest、同時刻の p̂_ens15・EV・p̂_mix・EV_mix・p̂_single・EV_single、`market_ev` の manifest sha・特徴行の sha・コード sha・`policy_version`。
- FR-004 精算は保存しない。公式単勝払戻(`official_win_payouts`)を読み取り時に結合し、未保存/不整合はレース単位で集計外(156 と同じ)。
- FR-005 特徴は当日朝に 1 回構築(`market_ev.load_rows → build_features`、対象日の started 馬に限定)し、T−3 では市場 12 列のみ `odds` から再計算する(140 `build_judged_features` と同じ経路)。朝の構築 hash を行に残す。
- FR-006 礼儀: 共有リミッタ(172・60 秒)と robots(171・24 時間 TTL)を経由。1 レース 1 req・締切 18 秒(`start_new_session`+`killpg`)。拒否(`FetchRefused`)で当日のプロセスを停止し、以後のレースは `skipped(source_refused)`。
- FR-007 運用モード: `config/source-operations.json` の `capture_only_dates` に列挙された日だけ動く。列挙に無い日・`collection_paused` 以外の状態・profile 欠落は起動を拒否。
- FR-008 `attention_picks`・`chaos_snapshots`・`race_horses.odds` は変更しない(本表は別系統の判断記録)。
- FR-009 受入(前向き計測・毎開催日に記録): 窓内の捕捉率 ≥95%・`official_at − captured_at` の中央値 ≤1 分・再捕捉 0・全行の再現(hash 一致)。
- FR-010 測定(2〜3 か月後・判定しない): σ(T−3)=log(確定/T−3) の分布(帯別)・S1〜S5 の T−3 該当の締切保持率・Σwon/Σp̂(T−3)・公式払戻精算の回収率と開催日ブロック区間。採否の規則は登録しない(138 の 300/600 点は検出力が無い)。

## 憲法との関係

- V: 新表は「判断の記録」で、`race_horses.odds` の履歴化ではない。前例 007/084/138 と同じ「1 判断 1 凍結値」。同じレースに chaos 行(予測時)が並ぶ事実は開示し、価格ずれ測定に使う。本文は変えず注記で読みを固定する(決定 1)。
- II: 保存値は特徴に戻さない(leak-guard)。
- III: 事前登録は測るものだけ(FR-010)。回収率の判定を約束しない。
- 技術制約(手動実行): 1 日単位の明示起動・翌日に持ち越さない(決定 2)。

## 非対象

T−10 の 2 点目・買い目の提示・実購入の自動化・スケジューラ・複勝/ワイドの EV(quote は保存するが v1 では評価しない)。

## 未決

試運転は 10/31(dry-run を先に・同日に本番)。11/1 が 2 日目、11/7・11/8 は続き。
