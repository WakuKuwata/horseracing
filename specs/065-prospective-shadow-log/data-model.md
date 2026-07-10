# Phase 1 Data Model: prospective shadow-betting log

**スキーマ変更なし・migration なし**(head 0011 不変)。既存テーブルのみ。

## エンティティ(既存・変更なし)

### recommendations(既存)
- 変更列なし。prospective 識別は `logic_version` に文字列マーカーで表現。
- prospective win 行: `bet_type=win`・`selection={horse_id, horse_number}`・`market_odds_used=凍結した発走前オッズ`・`is_estimated_odds=false`・`computed_at=決定時刻`・`logic_version` に `;prospective=1;odds_asof=<iso>`。
- backfill(044/064)win 行: marker 無し(`;prospective=1` を含まない)=機械的区別。

### race_predictions / race_horses / race_results(既存)
- 読み取りのみ。`race_horses.odds` は後で closing に上書きされうるが、prospective 推奨の評価は凍結 `market_odds_used` を使い現在オッズを読まない(closing-oracle 排除)。

## logic_version 文法(win 経路)

現行(064 まで):
```
ev=win_prob*odds;thr=<t>;stake=<s>;excl=...;renorm=started;v=<ver>[;kelly=...][;oddscap=<c>][;<pcal>]
```

追加(`prospective=True` のときのみ・off でバイト同等):
```
;prospective=1;odds_asof=<iso8601>     # 例 ;prospective=1;odds_asof=2026-08-01T09:30:00+00:00
```

- prospective off(既定)では一切追記しない → 既存 lv とバイト同等(INV-1)。
- 監査: marker + market_odds_used + computed_at から「いつ・どのオッズで前向きに出したか」を完全復元(V, SC-001)。

## エンティティ(feature 内の非永続オブジェクト)

### ShadowLogSummary(api/backtest.py・read-time 純・非永続)
- fields: `n_settled`・`n_hit`・`hit_rate`・`recovery_rate`(Σ realized_return / n_settled_valued=凍結オッズ回収)・`n_pending`(marker あり未確定=集計外)・`n_void`(hit=None=分母外)・`weak_pretime`(post_time 未知=弱保証の別掲)・`by_month`・`first_at`/`last_at`。**skip-rate は無し**(行が残らず分母不能=codex)。
- 対象(全 AND)= bet_type=win ∧ 厳密 marker(`;` split で `prospective=1`)∧ is_estimated_odds=False ∧ market_odds_used>0 ∧ estimated_market_odds_used=None ∧ 有効 WIN dict selection ∧ settled。既存 `win_realized`(凍結 market_odds_used)で per-rec realized を算出。**favorite_realized/現在オッズは読まない**。
- クエリは recommendations を **run 跨ぎで直接**(active-run scoped 表示クエリを使わない)。
- 永続化なし(recommendations が実体=既に永続)。集計スナップショットの時系列保存は deferred。

## 不変条件

- INV-1: `prospective=False`(既定)で recommendation 行・logic_version が現行とバイト同等。
- INV-2: prospective 推奨の realized は凍結 `market_odds_used` のみで評価され、記録後に `race_horses.odds` を closing へ更新しても**バイト不変**(closing-oracle 排除、SC-001)。
- INV-3: shadow_log_summary は marker あり ∧ settled ∧ win のみを集計し、backfill/未確定/exotic/疑似を1件も混ぜない(SC-002)。
- INV-4: prospective 生成は result-pending でのみ許可(fail-closed)。生成後に結果が入っても marker・凍結オッズは不変(SC-004)。
- INV-5: marker・凍結オッズ・結果のいずれもモデル特徴に流入しない(leak-guard, II)。
