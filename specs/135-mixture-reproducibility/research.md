# 独立意見と判断

- model:131は平均win→Harville/Benter→元win復元。132Cと同λなら等価。差が出るのは校正材料/年次対日次/complete_results等。実運用latest材料を保存入力で再現したふりをせず、等価性と制御比較を行う。
- features/history:130cacheは予測のみで本体がない。129本体は8/23までの学習なので2026年次と混同しない。Step3は129基準+同期間候補1outerへ明示的に分け、基準6本の再学習を避ける。
- history:load_frames(end_date)でもhorses全件が読まれる。race-date上限だけで当時値を保証しない。時点付きfeature_snapshotsを第一候補とし、metadataだけを先に調査する。
- 親:Step2 head校正は両案同じ前年までの基準λを固定。候補専用の過去年OOSが不足するため、候補λ最適化を密かに足さない。seed集約は損失平均とし、別ensembleや3倍の独立標本を作らない。
- 採用案:比率1/7・seed42の直近候補は成績を見る前に固定。Step2で他seedが良くても交換しない。脚質と温度補正の再探索は今回から外す。
- 不採用案:現行DBlatest関数のそのまま実行、130年次モデルと129 finalの無断置換、現在masterの無制限利用、8/29〜9/6での再選択。
- Hooks:.specify/extensions.ymlはafter_specify/after_planのagent-context更新だけがoptional。追加確認は不要と判断し、AGENTSのmanaged plan参照を直接更新。他phaseのmandatory hook無し。

- history追加調査:対象4日144レースは129 rehearsalで既存参照済み。完全未使用holdoutとは呼ばない。当時094 snapshotは136/144に存在するが、jockey/trainerは既にTE済み。旧値群を新encoderの一意値へ正確に変換できる場合のみ採用し、曖昧・未知群はレース単位で除外する。旧未知IDのfallbackも値群に含める。現在masterからIDを推測しない。
- source管理:Step1完了後に同名runnerが別並行作業で変更された。実行時SHAと一致するsourceを専用名に隔離して証拠を保全し、外部runnerをこの検証では使用しない。
