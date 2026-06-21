# 競馬予測システム ドキュメント

このディレクトリをプロジェクト仕様の正とする。Obsidian Vault の元メモは初期入力として扱い、矛盾がある場合は `docs/` 配下の記述を優先する。

## ドキュメント構成

- [overview.md](overview.md): プロジェクト目的、MVP、決定事項、成功指標。
- [data-sources.md](data-sources.md): JRA-VAN と netkeiba の利用方針、ID方針、取込範囲。
- [database.md](database.md): 既存 `aiuma` DB を参考にした初期テーブル方針。
- [scraping-netkeiba.md](scraping-netkeiba.md): netkeiba スクレイピング対象、URL、raceId仕様。
- [modeling.md](modeling.md): 予測ラベル、特徴量、学習、walk-forward、リーク防止。
- [odds-roi.md](odds-roi.md): 疑似オッズ、疑似ROI、市場オッズ、買い目計算の方針。
- [architecture.md](architecture.md): RaceFront、AdminFront、API、スクレイピング、学習サーバーの責務。
- [open-decisions.md](open-decisions.md): これから設計する未決事項。

## 正とする基本方針

- 元の `競馬spec.md` を正とする。
- 予測対象は `1着率`、`2着以内率`、`3着以内率` とする。
- さらに券種別の組み合わせ確率を算出できるようにする。ただし具体的な結合確率モデルは後続設計で決める。
- オッズはスナップショット保存しない。取得できた最新オッズで上書きする。
- 未来レースの判断には、予測確率から算出した疑似オッズと疑似ROIを使用する。
- JRA-VAN データは 2007 年以降を使用する。2006 年以前は ID 体系が異なるため、初期取込・学習・評価対象から除外する。

