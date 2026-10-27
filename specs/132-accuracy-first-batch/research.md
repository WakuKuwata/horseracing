# Research decisions

- D1: cutoff9/6、保存実末日8/23。既存SHA固定snapshotを用い範囲を表示。最新DBへのfallbackなし。
- D2: challenge_features: Unknown行dropは窓を変えるため不可。既存_pace_runs/_rolling_asof/scenario共用で8列限定overlay。IDをsourceに読み替えない。採用。
- D3: challenge_model: 既存topk driverはbooster fitとfinished-onlyを含む。pure fit_stage_discount/Harvilleのみ共用。2020warmup、C fallbackはB。採用。
- D4: challenge_history:126はseed42/full-infoのみ、boosterなし。4固定混合を学習0で比較。年別係数固定移送を別candidateとして記録。採用。
- D5: styleはraw138単独で変更を分離。baseline130 anchor42再利用は同一性を検査。2024/2026を固定し良否で年を変更しない。
- D6: 非改善も全比較完了で価値ある結論。研究stage校正の改善を現runtimeの実測改善と呼ばない。
- D7: before hooksなし。after_specify/planのoptional agent-contextはAGENTSのplan参照更新で満たす。
