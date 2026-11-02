# codex 設計レビュー(2026-11-01・spec 段階)

依頼: 暫定設計(公式 ID の直接利用・既存契約への書込み・二重情報源の優先順位・政治性・運用・範囲)を攻撃させた。
codex は提示情報と 4 ファイル(調査記録・pipeline.py・politeness.py・constitution.md)のみを読み、実装なし。

| # | 重要度 | 指摘 | 採否 | spec への反映 |
|---|---|---|---|---|
| 1 | HIGH | 「同じ発行元」だけでは不足。エンティティ別の名前空間契約(桁・先頭ゼロ・リンク形式・未知 ID)を明記。名前一致は異常検知のみ | 採用 | FR-009/010・Key Entities「公式 ID の名前空間契約」 |
| 2 | BLOCKER | `nk:` を自動統合しなくても同一レースへの書込みで衝突(二重出走行・暗黙の ID 変更)。馬番一致だけで ID を書き換えない・隔離 | 採用 | FR-011・US1 シナリオ 6・SC-011 |
| 3 | HIGH | 名前検査では名前空間の混入を検出できない。試験項目(ゼロ保持・同名別 ID・同 ID 別名・騎手/調教師同数値・netkeiba 同数値) | 採用 | FR-010・Key Entities・plan のテスト列挙へ |
| 4 | BLOCKER | 結果の「公式優先上書き」は INSERT-ONLY と両立しない。NULL 補完も自動許可しない。初回到着順を「公式優先」と呼ばない | 採用 | FR-014・US2 シナリオ 2 |
| 5 | BLOCKER | 優先規則は netkeiba 側 writer にも共通適用・DB 内で原子的に。項目別規則(結果保持/払戻は公式/取消・体重は状態遷移/オッズは official_at) | 採用 | FR-016(利用者確認の NEEDS CLARIFICATION を残す) |
| 6 | HIGH | 来歴は行単位では不足。`race_horses` は可変項目群ごとに source・時刻・ジョブ参照。`_run_job` の Source 固定を再利用しない | 採用 | FR-006/017 |
| 7 | BLOCKER | 表示時刻は `official_at`、取得時刻は `observed_at`。暫定案の「observed_at=ページ時刻」は 140 と意味衝突 | 採用(暫定案を撤回) | FR-013・US1 シナリオ 4/5 |
| 8 | HIGH | 開催日ページを 12 レースの完全写しと扱わない。レースごとに状態判定・日付から race_id を推測しない・PARTIAL | 採用 | FR-008・Edge Cases |
| 9 | HIGH | 取消馬の馬番を行順から復元しない。変更情報と公式 ID の整合で解決、不能なら隔離。古い出馬表で取消が戻らない試験 | 採用 | Edge Cases・FR-016(c) |
| 10 | HIGH | 払戻は券種ごとの集合として検証。`official_win_payouts` は単勝のみ。全券種の写像・返還・不成立を固定。訂正時の旧行 | 採用 | FR-015 |
| 11 | HIGH | 公式結果に確定単勝オッズが無いのは能力差。払戻だけを精算経路に接続。前日最終を確定値にしない | 採用 | FR-019 |
| 12 | HIGH | fixture parity は同じレース・同じ公開段階・解決済み ID。fixture の保存・取得時刻・文字コード・ハッシュを受入条件に | 採用 | FR-007/023 |
| 13 | BLOCKER | 二重投入・逆順・並行投入を契約試験に | 採用 | SC-010 |
| 14 | BLOCKER | 馬場情報は measured_at / fetched_at / published_at を分離。v1 は特徴量接続禁止 | 採用 | FR-020/021 |
| 15 | HIGH | 通信・失敗試験は HTTP 単位(全ホップの共有スロット・別プロセス競合・DB 障害・Retry-After・capture window) | 採用 | FR-003/004・SC-012 |
| 16 | BLOCKER | canonical 置換を含む 2026 backfill は別 spec(067 系 ID 移行) | 採用(推奨を既定に) | FR-024(利用者確認の NEEDS CLARIFICATION を残す) |
| 17 | MEDIUM | 過去日の索引到達性は未確認。v1 は当日・到達可能範囲 | 採用 | FR-022・Edge Cases |
| 18 | HIGH | 共有 throttle は netkeiba 以外の下限が 0.0。slot 追加だけでは間隔が保証されない | 採用 | FR-003(設定経路と短縮不可を受入条件に) |
| 19 | MEDIUM | パラメータエラーと拒否を分離。POST キャッシュは cname を鍵に | 採用 | FR-005・Key Entities |

不採用: なし。#5 の項目別規則と #16 の分離は利用者が 2026-11-01 に A(codex 推奨)で確定した(spec FR-016 / FR-024)。

**plan 段階のレビュー(2026-11-01・BLOCKER 9 / HIGH 17 / MEDIUM 3・不採用 0・部分採用 2)**は [research.md 末尾の表](research.md#codex-plan-レビューの採否2026-11-01) に記録した。暫定案から改めた主な点: 来歴は馬単位+レース単位の 2 表、衝突検査と優先規則は両経路がロック下で共通に通る、結果はレース単位の初回挿入、払戻は 7 券種で券種ブロックの状態を判定、取得は JRA 専用アダプタ、監査は収集と照合に分離、migration は 2 本、導入は開催終了後の Phase 1 から。
Claude 案との差分: 暫定案 3 点(observed_at=ページ時刻・結果は公式優先・backfill は v1 候補)を撤回し、`nk:` 衝突の隔離と払戻の集合検証を追加した。

**tasks 段階のレビュー(2026-11-02・Q1 8 / Q2 8 / SC 12・不採用 0・部分採用 1)**は [research.md 末尾の表](research.md#codex-tasks-レビューの採否2026-11-02) に記録した。主な修正: db 既存モジュールの ⏸・CLI 骨格の分離(T047a)・dry-run の分岐点・parity fixture を netkeiba archive から・`jra-fetch` の kind 許可制・R1 は revision ID 指定・同時刻同値の採用元優先・results/payouts の job 分離。
