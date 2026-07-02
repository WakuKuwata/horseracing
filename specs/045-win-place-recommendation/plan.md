# Implementation Plan: 単勝推奨の製品結線 (045)

**Branch**: `045-win-place-recommendation`(worktree, base=main f46c259) | **Spec**: [spec.md](spec.md)

## Summary
win(単勝)推奨 0 行の 3 点欠落(生成・読み出し・表示)を結線。読み出し先行(043 教訓): api の EXOTIC フィルタを ALL に広げ dict selection→[horse_number] 正規化。生成: 007 に KellyConfig opt-in(016 single_kelly/allocate_kelly 再利用、cfg=None は従来 flat)を足し、recommend-serve/backfill の冪等を bet_type 群単位(win/exotic)に細分化して両群生成。スキーマ変更なし・014 read-only 不変・新 EV ロジックなし。codex 利用不可=single-opinion。

## 設計判断
1. **読み出し正規化**(書式統一 migration せず): router で win 行 selection dict → [int(horse_number)]。horse_number 欠損は除外。007 契約・既存テスト不変。
2. **win Kelly を含める**: real オッズ=Kelly が最も信頼できる券種で、exotic に Kelly があり win に無いのは画面上不整合。016 の純関数を 007 に opt-in 注入(新ロジックでなく再利用)。win 同士は相互排他 → allocate_kelly(単一群)適用。既定 cfg=None=flat(後方互換)。
3. **群単位冪等**: `_has_group(run, WIN)` / `_has_group(run, EXOTIC)` で無い群のみ生成 → 043/044 で populate 済み run へ win 追補可能・重複なし。

## 変更ファイル
```
betting/recommend.py        # generate_recommendations(cfg: KellyConfig|None) — Kelly opt-in
betting/cli.py              # recommend-serve/backfill: 群単位冪等 + win 生成呼び出し
api/queries.py              # exotic_recommendations → recommendations_for_run(ALL bet types)
api/routers/recommendations.py  # win selection 正規化・除外規則
front/…                     # 変更なし見込み(betTypes に win あり・real/pseudo 既存分岐)
```

## Constitution Check
- [x] II: 結果非参照(007 既存)・推奨値は特徴に戻さない・リーク境界不変
- [x] III: 不変条件テスト(群冪等・正規化・real 表示)。EV ロジック変更なし
- [x] V: append-only・logic_version(win は 007 既存 lv、Kelly 時は cfg を lv に反映)
- [x] VI: migration なし・014 read-only 不変(読み出し整形のみ)
- [x] 品質ゲート: codex 利用不可を明示(2 回試行・companion runtime 不足)
