# netkeiba スクレイピング仕様

## 対象ページ

開催日のレース一覧ページ:

```text
https://race.netkeiba.com/top/race_list.html?kaisai_date={YYYYMMDD}
```

レースの出馬ページ:

```text
https://race.netkeiba.com/race/shutuba.html?race_id={raceId}
```

レースの結果ページ:

```text
https://race.netkeiba.com/race/result.html?race_id={raceId}
```

レースのオッズページ:

```text
https://race.netkeiba.com/odds/index.html?race_id={raceId}
```

馬データ:

```text
https://db.netkeiba.com/horse/{horseId}
```

騎手データ:

```text
https://db.netkeiba.com/jockey/{jockeyId}
```

調教師データ:

```text
https://db.netkeiba.com/trainer/{trainerId}
```

## 実装方針

netkeiba の表示は JavaScript により生成されるため、スクレイピングには Playwright を使用する。

取得したレースデータは、まず以下のテーブルに反映する。

- `races`
- `race_horses`
- `race_results`

出走表や結果ページから参照される `horses`、`jockeys`、`trainers` が DB に存在しない場合は、リンク先ページから取得して追加する。

## raceId 仕様

`raceId` は開催日の `YYYYMMDD` と混同しない。

形式:

```text
YYYYVVKKDDRR
```

12 桁固定。

- `YYYY`: 年 4 桁。
- `VV`: 場所コード。例: `05` 東京、`06` 中山。
- `KK`: 開催回次。
- `DD`: 日次。
- `RR`: レース番号。`01` から `12`。

## 取込方針

- 同一 `raceId` の再取得は upsert とし、最新値で更新する。
- オッズはスナップショット保存せず、`race_horses.odds` などの最新値を上書きする。
- 人気、馬体重、馬体重増減、騎手、取消状態も最新値で更新する。
- 結果確定後は `race_results` を更新する。
- 取得失敗、セレクタ変更、ページ構造変更を検知できるように、ジョブ状態とエラー理由を記録する。

## 注意事項

- スクレイピング頻度、リトライ、待機時間、並列数は後続設計で決める。
- netkeiba と JRA-VAN の ID 対応が取れない場合の扱いは後続設計で決める。
- 未来レースでは結果ページ由来の情報を予測特徴量に混入させない。

