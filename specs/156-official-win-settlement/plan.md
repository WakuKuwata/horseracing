# 156 実装計画

既存FastAPI/SQLAlchemy + React/TypeScript。api/.venv、front pnpm。既存dirty領域を保持する。

1. purchase_view.race_contextで出馬と結果を別取得し、独立ID集合・馬番/状態・公式払戻を保持。単勝の検証・精算を一箇所にする。
2. settle_policy_netもsettle_betを共用。比較買い目の保存オッズを支払額から除く。
3. optional settlement_reasonをDTOから画面へ運ぶ。単勝公式精算と比較線の説明を更新し、OpenAPIのfront/admin snapshotと型を同期。
4. 純関数・PostgreSQL API・画面の回帰テスト、OpenAPI同期、typecheck/lint/buildを実施。独立レビューで差分確認。

155で固定した旧purchase_viewのbytesは変更前にartifacts/155-saved-odds-settlement-audit/source-at-audit/へ保存した。155のprotocolや監査結果を改変せず、今回の改修とは区別する。
