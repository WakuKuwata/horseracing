# Front image (Feature 018). Build context = repository ROOT (so both front/ and deploy/nginx.conf
# are reachable). Static Vite build served by nginx, which also reverse-proxies /api/v1/* to the API
# (single origin → no CORS; resolves 015's deferred). Reproducibility: pinned base tags
# (digest-pin for release), frozen lockfile.
FROM node:22-bookworm-slim AS builder
WORKDIR /app
ENV CI=1
RUN corepack enable
# lockfile-first for cache; build uses the committed openapi.json snapshot (015 type sync)
COPY front/package.json front/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY front/ ./
RUN pnpm build   # tsc -b && vite build → /app/dist

FROM nginx:1.27-alpine AS runtime
LABEL org.opencontainers.image.title="horseracing-front"
COPY --from=builder /app/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
