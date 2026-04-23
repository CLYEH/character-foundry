# Character Foundry

AI 角色生成平台。專案定位與 agent 切換見 [CLAUDE.md](./CLAUDE.md)，核心決策見 [DECISIONS.md](./DECISIONS.md)。

## Prerequisites

- Docker Engine 24+ 與 Docker Compose v2
- 本機直接開發時另需：Python 3.12+、Node 20 LTS、pnpm 9

## Quick start

```bash
cp .env.example .env
docker compose up -d
```

等待服務全部 healthy 後：

- Web UI：http://localhost/
- API health：http://localhost/api/health

驗證：

```bash
curl http://localhost/api/health
# → {"status":"ok"}
```

## Shutdown

```bash
docker compose down
```

Volumes（`pg_data`、`redis_data`）會保留；要清空請加 `--volumes`。

## Repo 結構

```
api/            FastAPI backend
web/            Vite + React 19 frontend
infra/nginx/    Reverse proxy 設定
planning/       各 agent 的規格書（source of truth）
tickets/        實作工單
```

## 下一步

這張 scaffolding ticket (T-001) 只確保 stack 起得來。Migrations、auth、真正的 health check 等在後續 ticket：見 `STATUS.md` 與 `tickets/`。
