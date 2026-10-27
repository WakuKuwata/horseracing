# 154 実装計画

React18/TypeScript/Vite5、pnpm10、Vitest2/Testing Library/MSW。front既存依存のみ。DB/ML/ops処理変更なし。共有領域の既存変更を保護。

1. RaceDetailPageをROI主領域と下部オッズ/比較へ整理。比較は初回open後mountを保持。旧UIをLegacyPredictionComparisonへ抽出し、選択/生成・実行記録・診断を集約する。
2. HorseEntriesTableへROI表示variantを追加し、馬番昇順・旧p列を隠す。既存prediction表示は維持する。
3. RecommendationPanel/BetSlip/BudgetInputへ比較文脈を追加。旧方策の金額を仮予算表示にし、記録操作は折りたたみへ分ける。ExpectedReturnNoteの旧主表を前提とする説明を更新。
4. JointPanelへmodelVersionを受け渡し、旧予測の選択モデルと結合確率を一致させる。既存props省略時のactive挙動を維持。
5. ページ/比較/ソート/ジョブ継続の回帰テストと既存front一式、型・lint・build。ブラウザで幅と表示状態を確認。

## Constitution check

ID・確率・母集団・モデル/閾値は変更なし。表示は永続値/既存定義のみ。推定と実績、ROI計算時と現在値を区別。UIのAPI/DB契約はspecで確定済み。既存ops以外の書込追加なし。独立second opinionはdesign_prediction_ablationが担当し、現在オッズ時刻の混同防止、active生成と表示切替の違い、タブ移動時のジョブ追跡保持、表示規律の維持を採用。
