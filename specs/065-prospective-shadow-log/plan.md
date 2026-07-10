# Implementation Plan: prospective shadow-betting log

**Branch**: `065-prospective-shadow-log` | **Date**: 2026-07-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/065-prospective-shadow-log/spec.md`

## Summary

過去の realized ROI は全て closing 一括オッズ由来(DB に発走前オッズ0件)で楽観バイアス。「発走前に約定できたオッズで利益が出るか」を正直に測る唯一の道は prospective(前向き): 結果ペンディング時に発走前オッズで win 買い目を生成し、**決定時点オッズを凍結(既存 market_odds_used)+ prospective マーカー(logic_version)** で記録、結果確定後に**凍結オッズで**精算(既存 win_realized 049)。集計は recommendations からの **read-time**(049 同型・新テーブルなし)。**スキーマ変更ゼロ・migration なし・新予測/精算ロジックゼロ**。計器は空で始まり、発走前オッズフィード(scrape 008・未来レース ingest)が来て初めて埋まる going-forward 装置。利益は主張しない。

## Technical Context

**Language/Version**: Python 3.12(betting/live/api)、TypeScript + React(front)

**Primary Dependencies**: live(019 orchestrate: guards.is_result_pending + capture 規律の新結線)、scrape(008 発走前オッズ fresh 取得=capture 時刻の源)、betting(045 generate_recommendations が market_odds_used 凍結・064 policy-aware 冪等)、api(049 win_realized read-time 純関数)、front(Vitest+MSW)

**Storage**: PostgreSQL 既存。**migration なし**(prospective 識別=logic_version マーカー、集計=recommendations read-time)。head 0011 不変。

**Testing**: pytest + testcontainers、Vitest+RTL+MSW。leak-guard・byte-parity(marker off)・冪等・契約 drift-check。

**Target Platform**: ローカル/staging。CLI(前向き収集)+ read-only API + SPA。

**Project Type**: 既存パッケージ(live/betting/api/front)への薄い結線。

**Performance Goals**: マーカーは logic_version 追記のみ=生成レイテンシ不変。read-time 集計は少量 prospective 推奨で軽量。

**Constraints**: marker off でバイト同等(後方互換)。オッズ/結果/marker はモデル特徴に流入しない(II)。**closing-oracle を構造的に排除**(凍結オッズ market_odds_used で評価・現在の closing を読まない=049 の性質を継承)。

**Scale/Scope**: win のみ。exotic prospective deferred(real exotic 配当 012 前提)。

## Constitution Check

*GATE: Phase 0 前に PASS。Phase 1 後に再確認。*

- [x] **I. データ契約**: raceId/ID/ラベル不変(新データ源なし)。PASS。
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: オッズ・結果・prospective marker は記録/評価/表示にのみ使用、モデル特徴に戻さない(leak-guard)。selection は race_results を読まない(既存 007/049 の性質)。p≠q 分離(計器は市場を特徴化しない)。PASS。
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 本 feature は「利益を主張する」ものでなく「closing-oracle 無しで利益が出るか正直に測る計器」。ROI>1 を前提にしない。凍結オッズ評価で楽観バイアスを構造排除。PASS。
- [x] **IV. 確率整合性**: 予測 p は既存経路のまま不変。marker は選定・確率導出を変えない(logic_version 追記のみ)。PASS。
- [x] **V. 再現性・監査**: 凍結オッズ(market_odds_used)・決定時刻(computed_at)・odds_asof・prospective marker・モデル/policy を記録。pseudo は必ずラベル。PASS。
- [x] **VI. feature 分割規律**: **スキーマ変更なし**(marker + read-time)。表示に新 API フィールドが要る場合のみ OpenAPI 純追加で契約先行・front snapshot/drift-check。UI は契約確定後。PASS。
- [x] **品質ゲート**: codex second opinion を plan 段で並走(betting/live/eval/表示=高リスク)。両案差分と採否を research.md に記録。PASS。

**Gate result: PASS(スキーマ変更ゼロ・新リーク面ゼロ・marker off バイト同等)。**

## Project Structure

### Documentation (this feature)

```text
specs/065-prospective-shadow-log/
├── spec.md
├── plan.md              # This file
├── research.md          # Phase 0: 設計判断(marker/odds_asof/result-pending保証/read-time非混同/closing-oracle)
├── data-model.md        # Phase 1: スキーマ不変・logic_version 文法・エンティティ・不変条件
├── contracts/           # Phase 1: betting/live 生成・api 集計・display
│   ├── generation.md
│   ├── aggregation.md
│   └── display.md
├── quickstart.md        # Phase 1: 検証手順(marker off バイト同等・前向き記録・精算・空状態)
└── checklists/requirements.md
```

### Source (touched)

```text
betting/src/horseracing_betting/
├── recommend.py         # generate_recommendations(..., prospective=False, odds_asof=None) → logic_version に ;prospective=1;odds_asof=<ts>(off でバイト同等)
└── cli.py               # _generate_product_set / recommend-serve に prospective 透過 + policy-aware 冪等(marker 込み)
live/src/horseracing_live/
├── orchestrate.py       # collect_prospective(session, date/range): capture 規律(fresh scrape→capture時刻を odds_asof→post_time前→pending 再確認→advisory-lock)→ WIN 推奨を prospective marker 付きで生成
└── cli.py               # `live collect-prospective` ワンショット(冪等・既存 scrape/settle 束ね)
api/src/horseracing_api/
├── backtest.py          # shadow_log_summary(prospective settled win のみ集計)= 049 win_realized 流用・betting 非 import
├── schemas.py/queries.py/routers  # GET /shadow-log(read-only 純追加・prospective 実績)
front/src/components/
└── ShadowLogPanel.tsx   # prospective 実績ビュー(凍結オッズ realized・正直ラベル・空状態・closing backtest と分離)
```

## Phase 0 / Phase 1

- **Phase 0 → research.md**: 設計判断確定(prospective flag を独立引数にする理由 / marker off バイト同等 / odds_asof=fresh scrape の capture 時刻(updated_at は却下=codex) / result-pending は必要条件だが発走前の十分条件でない→capture 規律 / read-time 集計の非混同担保 / closing-oracle 排除の証明 / run 跨ぎ冪等キー)。codex second opinion 反映。
- **Phase 1 → data-model.md, contracts/, quickstart.md**: スキーマ不変・logic_version 文法・契約・検証手順。

## Complexity Tracking

- スキーマ変更なし・新テーブルなし・新予測/精算ロジックなし。
- 新規は「marker 付与(1 引数)」「read-time 集計(049 流用)」「表示」「運用結線(既存 CLI 束ね)」のみ。
- 最大の非コード前提: 発走前オッズフィード(scrape 008)+未来レース ingest。無ければ計器は空のまま(FR-006 空状態で正しく動く)= 実装のブロッカーではなく運用前提。
