# Quickstart: prospective shadow-betting log

前提: ローカル DB、API :8000、front dev。**注意**: 実データで計器を埋めるには result-pending の未来レース + 発走前オッズが要る(現在 DB は 2026-07-05 で停止=空状態が正常)。手順は合成/少数レースで契約を検証する。

## 1. marker off バイト同等(後方互換)

```
betting recommend-serve --race-id <RID>            # prospective 未指定=現行
```
- 期待: recommendation 行・logic_version が現行(065 前)と完全一致(`test_prospective_off_is_byte_identical` 緑)。

## 2. 前向き記録(result-pending)

```
live collect-prospective --date <未来日>            # または --from --to
```
- 期待: result-pending かつ発走前オッズのあるレースに **WIN 推奨**が `;prospective=1;odds_asof=<ts>` 付きで生成。結果ありレースは skip(理由付き)。再実行で重複なし(冪等)。

## 3. closing-oracle 排除の確認(核心)

- 前向き記録後、そのレースの `race_horses.odds` を closing 値へ更新 → `GET /api/v1/shadow-log` の realized が**バイト不変**(凍結 market_odds_used で評価・SC-001)。

## 4. 精算と集計

- 結果確定後、shadow-log は marker あり settled win のみを凍結オッズで集計(回収率/的中率/確定数/未確定数/時系列)。backfill・未確定・exotic・疑似は0件混入(SC-002)。

## 5. 表示(正直な意思決定支援)

- front ShadowLogPanel: prospective 実績が closing backtest と別セクション・正直ラベル(real 約定可能・前向き・利益を約束しない)・**空状態も正直**(データ0で「収集はこれから」)。

## 合格判定(DoD 抜粋)

- SC-001 凍結オッズ評価がバイト不変(closing-oracle 排除)。SC-002 非混同ゼロ。SC-003 表示で「real前向き/closingでない/利益約束せず/収集中か」が常時可視。SC-004 冪等・result-pending 以外は生成拒否。SC-005 leak-guard 緑・selection results 非参照・api betting 非 import。marker off バイト同等。
