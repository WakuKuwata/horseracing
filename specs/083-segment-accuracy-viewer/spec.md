# Feature Specification: セグメント精度計器の viewer (segment accuracy viewer)

**Feature Branch**: `083-segment-accuracy-viewer`

**Created**: 2026-07-25

**Status**: 実装済み(同ブランチでコミット)

**Input**: 082 の deferred「viewer」。codex 設計レビュー([`docs/plan/codex-083-review.md`](../../docs/plan/codex-083-review.md))
の結論「一括 GET・read-only・migration なしは採用、**契約型と順序保証を修正してから**進める
(dict + optional TS cast のままは NO-GO)」を全採用。

## 概要

082 segment accuracy readout(diagnostic_runs kind='segment_accuracy')を admin から見える化する。
054 パターン(オフライン計算→永続化→読むだけ)の薄い延長。migration なし・API read-only 不変。

- **API**: `GET /api/v1/diagnostics/segment-accuracy` — 最新 run を **typed v1 契約**
  (`SegmentAccuracyPayloadV1`、全モデル `extra="forbid"`、grain 判別 union)で転記。
  075 splat-null の対策は「型付けを避ける」でなく「forbid 付き model_validate」(codex P0#1)。
  未知 `metric_contract_version`・malformed payload = **typed 409 `diagnostic_contract_unsupported`**
  (silent-null 描画・古い run へのフォールバック禁止)。未永続化 = typed 404。
  envelope に **`diagnostic_run_id`**(082 discovery rule の必須ハンドル、codex P1#5)。
  404/409 を OpenAPI responses に宣言・`kind` は Literal(codex P1#6)。
- **admin**: DiagnosticsPage を **独立 2 セクション**に再構成(codex P0#4 — 054 側の 404/error が
  精度セクションを隠さない)。軸は payload(凍結ライブラリ)順・バケットは **固定コードポイント順**
  (JSONB は key 順を保存しない=codex P0#2 の当面策。値依存ソート・ソートUI・worst/rank・損益色なし)。
  SECONDARY/estimand/「run 生成時 recipe の歴史的 OOF であり現 artifact でない」/交絡注記は常時表示。
  監査: run id・確率段・bundle/attestation/code digest・契約 version/hash・seed/B・採点数・除外 ledger。
  race grain の citl は構造恒等(非表示+注記)。440KB は 1 GET(分割は整合性を壊す、codex)。

## 082 producer 側の同時修正(codex P0#3・payload 契約差分)

viewer が誤読を露出する前に producer を修正し再 run・re-persist:

1. **market 同一母集団**: `excess_nll_market` は model/market 両方を market-complete subset で計算
   (`winner_nll_market_subset` を明示・`n_market_complete_races / n_total_races` 併記)。
2. **exclusion ledger 完全化**: `load_eval_races` が先に落とす finished-label ゼロレースを
   `no_finished_label` として計上(Σ reconciliation の実体化)。
3. **ECE の cluster CI**(FR-005 未充足だった): 開催日×bin の十分統計で vectorized bootstrap
   (`ece_ci`、未調整ラベル付き)。
4. **race-grain citl ≡ 0 の恒等マーク**: 値 null + `citl_note`(「良好な較正」誤読防止)。
5. **surface 定義に 障 を追加**(実 payload に存在した domain 差分。definition_hash が変わる=
   比較キーが正しく分離)。

## codex レビュー採否

全採用(却下ゼロ)。特に: verbatim dict + 手書き TS cast 案は**却下された**(075 の罠を admin に
移すだけ)。「軸だけ型付け・バケット dict」の中間案も却下(主要数値が未検証のまま=最悪の中間)。
UI 加工の許容範囲(固定順ソート可・値依存ソート/色/rank 不可・SECONDARY 折りたたみ不可)を
テストで機械固定。

## Success Criteria(実装で検証済み)

- persist → JSONB → API の全経路で **canonical deep equality**(JSONB は key 順非保存のため
  意味的同一性; api integration test)。
- extra key / missing key / 未知 version / 空 payload → **409**(200 で silent-null にならない)。
- セクション独立(edge 404 × accuracy 200 / 逆方向)・バケット固定順(逆順 fixture)・
  n 列は grain 準拠(horse=n_horses)・sort UI/rank/worst 不在・NaN 不在。
- openapi 純追加・front/admin snapshot byte 一致・drift 緑・全 path GET 不変。

## スコープ外 (deferred)

- prospective operational readout(082 spec に既記載)・anomaly/alert(simultaneous CI 前提)。
- 054 segment-edge 側の typed 404 OpenAPI 宣言 retrofit(本 feature は新 endpoint のみ)。
