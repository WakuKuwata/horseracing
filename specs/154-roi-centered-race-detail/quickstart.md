# 154 確認手順

```bash
cd front
pnpm exec vitest run --maxWorkers=2 --minWorkers=1
pnpm typecheck
pnpm lint
pnpm build
pnpm dev --host 127.0.0.1 --port 5173 --strictPort
```

既存APIがlocalhost:8000で動作する環境でレース詳細を開く。初期はROI出走表とオッズ。「予測モデルとの比較」を開くと旧勝率/買い目/生成操作へアクセスできる。旧予測の選択を変えても上の期待回収率は同じ系列を保持する。

比較のモデル情報・結合確率、購入記録・訂正はそれぞれ折りたたみを開く。実データで生成/記録ボタンを押すと従来のops操作になるため、UIの読み取り確認では押さない。ジョブ追跡・記録操作はMSWのテストで検証している。
