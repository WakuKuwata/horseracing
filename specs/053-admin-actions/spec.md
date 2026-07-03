# Feature Specification: 運用コンソール第3回 — アクション起動 (Range Refresh Action)

**Feature Branch**: `053-admin-actions` / **Created**: 2026-07-03 / **Status**: Draft
**Input**: 運用コンソール プログラム第 3 回(全体像は `specs/051-admin-console/spec.md` 参照)。052 の被覆マップで backfill の穴は見えるようになったが、埋める手段は CLI(`live refresh --from --to`、050)のみ。admin から範囲更新を**ops ジョブ経由で**起動できるようにする — admin 初の書き込みアクション(ただし書き込み面は ops に既存のジョブモデルを 1 種増やすだけ)。

## スコープ
- **US1 (P1) ops 範囲更新ジョブ**: `POST /ops/v1/refresh-range` body `{date_from, date_to}` → 202 JobAccepted。新 job_type `refresh_range`(scope="range"・scope_value="from..to")。worker が **live CLI を subprocess**(`uv run --project live … refresh --from --to`、028/043 と同一の境界パターン=ops は live/serving/betting を import しない)で実行し、exit code を SUCCEEDED/FAILED にマップ(出力 tail を summary/error_message に保存)。**ガード**: from≤to・**範囲 ≤35 日**(typed 422、実行時間の上限を構造的に保証=1 開催日 ≈ 45 秒 × 最大 ~12 開催日/35 暦日)・ACTIVE ジョブの dedup(advisory lock、二重クリック安全)。タイムアウト 3600 秒。
- **US2 (P2) admin からの起動**: CoveragePage に (a) 日行ごとの「この日を更新」(from=to=当日)、(b) 期間一括「この範囲を更新」ボタン(**実行前確認必須**)。投入後は job_id とジョブ履歴ページへの誘導を表示(052 のジョブ一覧で進捗確認)。admin に ops proxy(/ops→8001)+ ops-openapi snapshot/型(front 同型)。

## Requirements
- **FR-001**: ops は live を import しない(subprocess のみ=既存境界)。read-only api は不変(書き込みは ops のみ)。
- **FR-002**: 範囲ガード(>35 日・from>to・不正日付は typed 422)。dedup は同一 scope_value の ACTIVE ジョブ再利用(完了済みは再利用しない=明示クリックは「今すぐ再実行」、predict 前例)。
- **FR-003**: ジョブは冪等な 050 `live refresh` を呼ぶだけ(生成の冪等・順序・例外隔離は 050 で担保済み・本 feature は新ロジック禁止)。
- **FR-004**: admin の書き込みボタンは**確認ステップ必須**+ pending 中 disabled。結果は非同期(202)=ジョブ履歴ページで確認(051 の localhost 前提は不変)。
- **FR-005**: ops-openapi 契約先行: 純追加 → front/admin の ops snapshot・型を再生成(front は endpoint 追加の透過反映のみ・UI 変更なし)。
- **FR-006**: スキーマ変更なし(ingestion_jobs 既存列のみ)・migration なし。

## Success Criteria
- **SC-001**: `POST /ops/v1/refresh-range` が 202+job_id を返し、worker 実行で live refresh が走り SUCCEEDED(実 DB: 既 backfill 日は skip 系カウントで成功)。二重投入は reused=true。
- **SC-002**: >35 日・from>to は 422。live 非ゼロ exit は FAILED + error tail。
- **SC-003**: admin 被覆ページから日/範囲の更新を起動でき、確認→202→ジョブ履歴で状態が見える。
- **SC-004**: ops/admin/front スイート緑・ops 境界テスト(live 非 import)緑・両 openapi drift 緑。

## Assumptions
- 大範囲(>35 日)は CLI(`live refresh`)の領分 — admin は運用の日常(穴埋め・直近更新)用。
- codex CLI 本セッション 3 回起動失敗 → 見送り宣言・single-opinion(028/043 subprocess ジョブ+050 CLI の薄い結線)。

## Deferred
ロードマップ 4-5・ジョブの進捗ストリーミング/キャンセル・force オプション UI・被覆ページの自動リフレッシュ・scrape 起動(netkeiba 状況依存)
