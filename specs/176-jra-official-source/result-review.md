# 176 実装の結果記録(2026-11-02・別 worktree・ブランチ `176-jra-official-source` にコミット・main 未マージ・本番未反映)

実装は `.claude/worktrees/176-jra-official-source`(起点 `8e373520` = 今日の 174 捕捉の譲り合い縮小を含む)。
稼働 checkout・本番 DB・policy・稼働プロセスには触れていない。開いているのは T050(policy へのキー追加)と
rollout の T100〜T107 だけで、どれも開催終了後に利用者の指示で行う。

## テスト

| パッケージ | 結果 | 備考 |
|---|---|---|
| scrape | 715 passed | ruff はパッケージ全体で緑 |
| db | 139 passed | 0023/0024 の往復・制約・downgrade 拒否 |
| ops | 263 passed | worktree に chaos artifact(3 ファイル・gitignore)をコピーして実行 |
| live | 188 passed | 同上 |
| features | 469 passed / 1 failed | 失敗は 175 の `eval/decision_quote_scoring.py` が `attention_picks` を参照する既存の不具合(176 は eval に触れていない) |
| api(OpenAPI drift) | 6 passed | api / front / admin / ops-openapi に差分なし(SC-009) |
| scripts(172/176 wrapper) | 77 passed | `PYTHONPATH=.` が要る。164 系ハーネスは worktree に無い保存 HTML を読むので対象外 |

## parity(netkeiba の保存 archive ↔ 公式ページ・SC-001 / SC-003)

archive(`artifacts/scrape_archive/`)から `scripts/export_parity_fixture.py` で書き出した。稼働 DB は読んでいない。

| | 比較できたレース | 一致 | 分母外 |
|---|---|---|---|
| 出馬表 2026-11-01 東京(結果前) | 12 / 12(隔離 0) | 803 / 803 項目 | 2 件=netkeiba の取得後に発表された出走取消(4R 5 番・6R 13 番)。DB には公式の取消が採用される |
| 結果 2026-10-31 東京(結果後) | 12 / 12 | 600 / 600 項目+単勝払戻 12 レース | なし。公式経路を後から流しても `results_mismatch` 0・払戻は同額の確認 12 件で行の増減 0 |

馬体重は netkeiba の結果 archive の解析値に無いので、SC-003 の体重の一致は未検証(公式経路の体重書込みは結合テストで確認)。

## 送信件数(SC-005・fixture fetcher・2 場 × 12R)

| コマンド | 送信 |
|---|---|
| jra-entries | 6(索引・開催日 2・出馬表 2・変更情報) |
| jra-odds(全 24 レース) | 27 |
| jra-results | 5(索引・開催日 2・結果 2) |
| jra-baba | 3 |

robots 1 件を足してオッズ除き 15 件(上限 20)、オッズ込み 42 件(上限 50)。辿り直しを 1 回入れると
entries が 9 件になり、それでも上限内。別プロセスとの競合でも送信間隔は 10 秒以上(`test_jra_send_control.py`)。

## 実装中に見つけて直したもの

- **辿り直しが同じ POST の再送になっていた**: 契約は「索引から辿り直す(同じ不正 cname は再送しない)」。
  `Walker` を索引からやり直す形に直し、出馬表系と単複オッズの両方で単体テストを足した。
- **騎手変更の件数が常に 0**: 更新後に「変わったか」を比べていて、ORM が新しい値に同期済みだった。判定を更新前に移した。
- **時刻不明のオッズの捨て方が summary に出ていなかった**: 共通 writer が理由 `no_official_at` を数えていなかった。
  `odds_skipped_stale` に数える(netkeiba・公式の両経路)。
- **db の往復テスト 5 本**が「ある版より後に作られた表」を直書きしていて、0022(174)の時点から一部が落ちていた。
  0022 と 176 の 3 表を足し、head の版名は直書きをやめて alembic から読む。

## tasks.md との差

- T019 のファイル名は `db/tests/integration/test_migration_176.py`(0023 と 0024 を 1 本で検査)。
- T038〜T040 は `scrape/tests/unit/test_jra_fetch.py` 1 本にまとめた。
- T073 の「1 場 2 件」は誤りで、結果は索引 → 開催日一覧 → 結果ページの 3 件(調査どおり)。
- 監査の「来歴不明」は分母から外さず比較し、`stored_source_unknown` で件数を分けて出す。導入前の稼働 DB は
  全行が来歴なしなので、外すと R3 の監査が空になるため。

## 残り(開催終了後・利用者の指示で)

T100(0023 適用と SC-008 の基準記録)→ 共通コードの反映と再起動 → T101(1 開催日の観察)→ T050(policy キー)
→ T102〜T105(収集・照合 → 限定投入 → 全レース → 結果)→ T106(0024 と馬場)→ T107(SC 実測の転記)。
