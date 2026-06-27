# Research: ライブ serving (019)

Phase 0。**核心発見**: `run_serving`(006) は既に「as-of 特徴量（build_feature_matrix(end_date=race_date)、
結果を読まない、同日除外、result-pending future race 安全）」で予測する。よって 019 は新規予測ロジックでは
なく、**guard → scrape(008) → run_serving(006) → recommend(011/016) → prospective ログ**の薄い結線層。
codex top-3（fail-closed / 使用オッズ値保存 / p パリティ分離）+ cutoff 補正（post_time 多く null→race_date）を反映。

---

## R1: orchestration 層（新規 `live/` パッケージ）

**Decision**: 新規パッケージ `horseracing-live`（`live/`）を追加し、scrape(008)/serving(006)/betting(011,016)/
db を import して結線。既存パッケージの依存グラフを汚さない（serving を betting/scrape に依存させない＝
概念的逆依存を避ける）。`orchestrate.live_serve()` が guard→scrape→run_serving→recommend→report を実行。

**Rationale**: live は scrape+serving+betting を同時に必要とする唯一の層。新パッケージが最も疎結合（018 が
deploy/ を足したのと同様、責務単位の追加）。

**Alternatives**: serving に betting/scrape 依存を足す→ serving が betting に逆依存（却下）。betting に
serving/scrape→ betting の責務肥大（却下）。

---

## R2: fail-closed ガード（codex #1）

**Decision**: 予測・推奨の前に全ガードを評価し、一つでも満たさなければ書き込まず拒否:
- **valid race_id**: `^[0-9]{12}$`（JRA-VAN、008 の fake ID 禁止と整合）。
- **result-pending**: `race_results` に当該 race の行が存在しない（存在＝走行済み→拒否、retrospective を案内）。
  「走行済み」は壁時計でなく**結果行不在**で判定（races にpost_time 多く null）。
- **entries 完全性**: scrape 後に started 馬が存在・horse_number 揃い・重複/頭数不整合なし。
- **odds presence**（推奨段のみ）: pre-race win オッズが対象出走集合に揃う。欠損→推奨を出さない（予測 p は可）。

**Rationale**: codex #1。不完全/走行済みデータで予測・推奨しない。result-pending が「未走」の堅牢な信号。

**Alternatives**: 壁時計/発走時刻で判定→ カラム無し・TZ 不明で脆弱（却下）。

---

## R3: scrape ステップ（008 再利用、URL/DB 状態駆動）

**Decision**: scrape は **URL 駆動 or 既存 DB 状態駆動**。operator が netkeiba URL を渡した場合のみ
`scrape_entries`/`scrape_odds`（008、urls+PoliteFetcher 前提）を実行（idempotent、ingestion_jobs audit、
pre-race オッズは result-pending のみ上書き＝008 が保証、netkeiba ID は id_mappings 経由）。URL 無指定時は
008 を別途実行済みの DB 状態で動作。scrape 失敗/部分は R2 のガードで fail-closed。**JRA-VAN race_id →
netkeiba URL の自動逆引きは deferred**（id_mappings は netkeiba→JRA-VAN 方向、逆引きは別設計）。

**Rationale**: 008 は URL 入力前提（race_id から自動取得しない）。逆引きを本 feature に抱えず、operator が
URL を渡す／008 を先に回す運用に分離。予測・推奨の可否はガードが DB 状態で決めるので URL 無しでも成立。

**Alternatives**: race_id→URL 自動逆引きを実装 → 別 feature 相当の scope、deferred。

---

## R4: 予測（run_serving 再利用、cutoff=race_date）

**Decision**: `run_serving(session, race_id=…, model_version=…)` をそのまま呼ぶ。features は
`build_feature_matrix(end_date=race_date)`（as-of、結果非参照、同日除外）。**cutoff は race_date**（004 と同一
日付粒度。post_time 多く null＝時刻粒度は deferred）。同日先行レース混入は 004 から継承の限界として開示。
run_serving は check_consistency（IV）も実施。

**Rationale**: 既存の leak-safe 経路を再利用。新規特徴量ロジックなし＝リーク面を増やさない。

**Alternatives**: live 専用 feature builder → 二重実装でリーク面増、却下。

---

## R5: 推奨（pre-race オッズ → 010/011/016、使用オッズ値保存）

**Decision**: run_serving が作った prediction_run に対し `generate_kelly_recommendations`（016）/
`generate_exotic_recommendations`（011）を呼ぶ。オッズは race_horses.odds（pre-race）→ 010 推定（実 exotic は
未来に無いので estimated=double-pseudo）。recommendations は **使用オッズ値**（estimated_market_odds_used 等）+
computed_at + logic_version を保存（codex #2: as_of だけに依存しない）。013/017 校正器 opt-in。live Kelly は
shadow（記録のみ、実資金執行なし、FR-016）。

**Rationale**: 011/016 をそのまま再利用。使用オッズ値は既存スキーマに保存されるので再現可能。

**Alternatives**: 実 exotic オッズ前提→ 未来レースに無い、却下（estimated double-pseudo を明示）。

---

## R6: 評価（結果不在 → パリティ + リーク + prospective、codex #3）

**Decision**:
- **p パリティ**: 過去レースで live_serve の予測（run_serving）== retrospective の predict（同じ as-of 経路）→
  予測 p 一致。**オッズ依存の推奨/EV は過去パリティ対象外**（過去 pre-race オッズは closing 上書きで非保持）。
- **リーク境界**: `race_results` を変更しても当該 race の予測が不変（features が結果を読まない）を機械検証。
- **prospective ログ**: 生成した予測・推奨を computed_at + 使用オッズ値で残し、後日結果確定後に既存 backtest
  （007/011/016）へ投入可能。

**Rationale**: codex #3/#D。未来は backtest 不能。p パリティ（リーク無し証明）+ prospective（事後評価）で代替。

---

## R7: スキーマ・運用境界

**Decision**: スキーマ変更なし（prediction_runs/race_predictions/recommendations + 008 テーブル + 016
stake_fraction 再利用）。手動 CLI 実行（自動 scheduler は deferred）。発走後（結果確定）に live-serve したら
result-pending ガードで拒否（retrospective を使う）。live Kelly は shadow 明示。

**Rationale**: 憲法 V/VI。最小変更。result-pending ガードが「発走後実行」を自然に防ぐ。

---

## 設計判断サマリ（codex 反映）

| 論点 | 採用 | codex |
|---|---|---|
| fail-closed | result-pending（結果行不在）+ valid id + 完全性 + odds presence | #1 → R2 |
| 使用オッズ保存 | recommendations に使用オッズ値（as_of 単独依存しない） | #2 → R5 |
| 評価分離 | p パリティ（過去 odds 再現せず）+ リーク + prospective | #3/#D → R6 |
| cutoff | race_date（post_time 多く null、004 継承、時刻粒度 deferred） | (補正) → R4 |
| odds 鮮度/欠損 | 欠損→推奨 fail-closed、予測は可 | #B → R2/R5 |
| 結線層 | 新規 live/ パッケージ（逆依存回避） | — → R1 |
