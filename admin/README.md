# 運用コンソール (admin) — Feature 051

モデルレジストリ等の**運用管理 SPA**。end-user 向け `front/` とは独立(read-only 思想を混ぜない)。

## 重要: localhost 専用(認証なし)

認証は明示的に **deferred**(単一オペレータ・ローカル起動のみの運用)。構造的ガードとして
dev サーバは `127.0.0.1:5175` にバインドします(`vite.config.ts`)。**公開・0.0.0.0 バインド・
リバースプロキシ配下への配置は禁止**です。将来 adoption 操作(ロードマップ 5)を載せる前に
認証を導入します。

## 起動

```bash
# 前提: 014 API が localhost:8000 で稼働(DATABASE_URL を設定して api/ から起動)
pnpm install
pnpm dev        # http://127.0.0.1:5175
```

## 型生成 / 契約

`openapi.json` は front と同一の 014 read-only 契約のスナップショット(byte 一致をテストで固定)。
API 変更時は `pnpm gen:types`(要 API 起動)→ commit。`pnpm test` に drift-check 含む。

## ロードマップ(specs/051-admin-console/spec.md のプログラム全体像を参照)

1. 土台 + モデルレジストリ(本 feature) → 2. 被覆率+ジョブ履歴 → 3. アクション起動 →
4. 診断永続化+ビューア → 5. adoption 制御(認証導入後)
