# Character Foundry — CI/CD

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** DevOps Agent
> **Assumption:** GitHub Actions（若內部用 GitLab CI / Gitea Actions，語法類似可轉）

---

## 1. Pipeline 總覽

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│  Git push / PR                                                   │
│       │                                                          │
│       ▼                                                          │
│  ┌─────────────┐                                                │
│  │  lint       │  ESLint + Prettier + Ruff (Python) + SQL linter│
│  └─────┬───────┘                                                │
│        │                                                        │
│  ┌─────▼───────┐                                                │
│  │  typecheck  │  tsc + mypy                                    │
│  └─────┬───────┘                                                │
│        │                                                        │
│  ┌─────▼───────┐                                                │
│  │  unit test  │  Vitest + pytest                               │
│  └─────┬───────┘                                                │
│        │                                                        │
│  ┌─────▼───────┐                                                │
│  │  e2e test   │  Playwright（docker compose 起整套）            │
│  └─────┬───────┘                                                │
│        │                                                        │
│        │  若是 main branch push                                 │
│        ▼                                                        │
│  ┌─────────────┐                                                │
│  │  build image│  docker build + push 到 GHCR                   │
│  └─────┬───────┘                                                │
│        │                                                        │
│  ┌─────▼───────┐                                                │
│  │ deploy      │  SSH 到 server + docker compose pull + up      │
│  └─────────────┘                                                │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Repo 結構假設

```
character-foundry/
├─ .github/
│  └─ workflows/
│     ├─ pr.yml           # PR 跑 lint + test
│     ├─ main.yml         # main push 跑 build + deploy
│     └─ nightly.yml      # 每日 3am 跑完整 integration + backup verify
├─ api/                   # Backend
│  ├─ app/
│  ├─ tests/
│  ├─ pyproject.toml
│  └─ Dockerfile
├─ web/                   # Frontend
│  ├─ src/
│  ├─ tests/
│  ├─ package.json
│  └─ Dockerfile（optional，Phase 1 nginx serve static）
├─ infra/
│  ├─ docker-compose.yml
│  ├─ docker-compose.override.yml  # local dev
│  ├─ nginx/
│  ├─ prometheus/
│  └─ scripts/
├─ planning/              # 本 planning 文件
└─ README.md
```

---

## 3. PR Workflow（`.github/workflows/pr.yml`）

```yaml
name: PR Checks

on:
  pull_request:
    branches: [main]

jobs:
  backend-lint-test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg15
        env:
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
        ports: ["5432:5432"]
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install deps
        working-directory: api
        run: pip install -e ".[dev]"
      - name: Lint
        working-directory: api
        run: |
          ruff check .
          ruff format --check .
      - name: Type check
        working-directory: api
        run: mypy app/
      - name: Run migrations
        working-directory: api
        env:
          DATABASE_URL: postgresql+asyncpg://postgres:test@localhost:5432/test
        run: alembic upgrade head
      - name: Test
        working-directory: api
        env:
          DATABASE_URL: postgresql+asyncpg://postgres:test@localhost:5432/test
          REDIS_URL: redis://localhost:6379/0
        run: pytest --cov=app

  frontend-lint-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with: { version: 9 }
      - uses: actions/setup-node@v4
        with: { node-version: 20, cache: pnpm }
      - name: Install
        working-directory: web
        run: pnpm install --frozen-lockfile
      - name: Lint
        working-directory: web
        run: pnpm lint
      - name: Type check
        working-directory: web
        run: pnpm tsc --noEmit
      - name: Test
        working-directory: web
        run: pnpm test --run

  e2e:
    needs: [backend-lint-test, frontend-lint-test]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Start stack
        run: docker compose -f infra/docker-compose.yml up -d --build
      - name: Wait for services
        run: |
          for i in $(seq 1 30); do
            curl -sf http://localhost/api/health && break
            sleep 2
          done
      - uses: pnpm/action-setup@v4
        with: { version: 9 }
      - uses: actions/setup-node@v4
        with: { node-version: 20, cache: pnpm }
      - name: Install Playwright
        working-directory: web
        run: |
          pnpm install --frozen-lockfile
          pnpm exec playwright install --with-deps
      - name: Run E2E
        working-directory: web
        env:
          PLAYWRIGHT_BASE_URL: http://localhost
        run: pnpm e2e
      - name: Upload screenshots on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-report
          path: web/playwright-report/
```

**跳過條件：** PR 只改 `planning/**` 或 `README.md` 可跳過 CI（用 `paths-ignore`）。

---

## 4. Main Branch Workflow（`.github/workflows/main.yml`）

```yaml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    permissions:
      packages: write
      contents: read
    steps:
      - uses: actions/checkout@v4
      - name: Determine version
        id: ver
        run: echo "tag=$(git rev-parse --short HEAD)" >> $GITHUB_OUTPUT

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build API image
        uses: docker/build-push-action@v6
        with:
          context: api
          push: true
          tags: |
            ghcr.io/your-org/character-foundry-api:${{ steps.ver.outputs.tag }}
            ghcr.io/your-org/character-foundry-api:latest

      - name: Build Web bundle
        uses: pnpm/action-setup@v4
        with: { version: 9 }
      - uses: actions/setup-node@v4
        with: { node-version: 20 }
      - name: Build web
        working-directory: web
        env:
          VITE_APP_VERSION: ${{ steps.ver.outputs.tag }}
        run: |
          pnpm install --frozen-lockfile
          pnpm build

      - name: Package web dist
        run: |
          tar -czf web-dist.tar.gz -C web/dist .

      - name: Upload to deploy host
        uses: appleboy/scp-action@v0.1.7
        with:
          host: ${{ secrets.DEPLOY_HOST }}
          username: ${{ secrets.DEPLOY_USER }}
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          source: "web-dist.tar.gz,infra/docker-compose.yml,infra/nginx/nginx.conf"
          target: /opt/character-foundry/staging/

      - name: Deploy on host
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.DEPLOY_HOST }}
          username: ${{ secrets.DEPLOY_USER }}
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          script: |
            set -euo pipefail
            cd /opt/character-foundry

            # Extract new web dist
            tar -xzf staging/web-dist.tar.gz -C web-dist.new/
            mv web-dist web-dist.old
            mv web-dist.new web-dist

            # Pull new API image
            docker compose pull api worker scheduler

            # Run migrations
            docker compose run --rm api alembic upgrade head

            # Restart services (downtime < 15s)
            docker compose up -d

            # Smoke test
            sleep 5
            curl -fsS http://localhost/api/health || {
              echo "Health check failed, rolling back"
              mv web-dist web-dist.failed
              mv web-dist.old web-dist
              docker compose up -d
              exit 1
            }

            # Clean old
            rm -rf web-dist.old staging/*
```

---

## 5. Nightly Workflow（`.github/workflows/nightly.yml`）

```yaml
name: Nightly

on:
  schedule:
    - cron: "0 19 * * *"   # UTC 19:00 = 台北 03:00
  workflow_dispatch:

jobs:
  integration:
    # 跑更重的 integration test（真 external API，少量 call）
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run integration tests
        env:
          OPENAI_API_KEY: ${{ secrets.INTEGRATION_OPENAI_KEY }}
          # ... 其他真 API key（獨立專案用）
        run: docker compose -f infra/docker-compose.integration.yml run --rm api pytest tests/integration

  backup-verify:
    # SSH 到 server 驗證昨日 backup
    runs-on: ubuntu-latest
    steps:
      - uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.DEPLOY_HOST }}
          username: ${{ secrets.DEPLOY_USER }}
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          script: |
            cd /srv/character-foundry/backups
            YESTERDAY=$(date -d yesterday +%Y-%m-%d)
            [ -s "$YESTERDAY/db.dump" ] || { echo "DB backup missing!"; exit 1; }
            [ -s "$YESTERDAY/storage-$YESTERDAY.tar.gz" ] || { echo "Storage backup missing!"; exit 1; }
            echo "Backups OK for $YESTERDAY"
```

---

## 6. Secrets 在 GitHub

`Repo Settings → Secrets and variables → Actions`：

| Secret | 用途 |
|---|---|
| `DEPLOY_HOST` | internal server hostname |
| `DEPLOY_USER` | SSH 使用者（應 create a dedicated `deploy` user with limited sudo）|
| `DEPLOY_SSH_KEY` | SSH private key（ed25519 建議）|
| `INTEGRATION_OPENAI_KEY` | Nightly integration test 用 |
| `INTEGRATION_SEEDANCE_KEY` | 同上 |
| `INTEGRATION_ANTHROPIC_KEY` | 同上 |

**注意：** `INTEGRATION_*` key 跟正式部署 key **分開**，避免 CI 破壞 production 配額。

---

## 7. 版本管理

### 7.1 Image tag 策略

- `latest` → 最新 main build
- `{git-sha-short}` → 精確版本，用於 rollback
- `v{major}.{minor}.{patch}` → release tag

### 7.2 Rollback

```bash
ssh deploy@character-foundry.internal
cd /opt/character-foundry
docker compose pull api:abc1234   # 回到上一版
docker compose up -d
```

或 CI `workflow_dispatch` 觸發 `rollback.yml`（帶 tag 參數）。

### 7.3 DB migration 相容性

- **向前相容原則**：新 API 要能讀舊 schema（過渡期）
- 破壞性 migration（刪欄位、改 type）必須分兩次 release：
  1. Release A：新欄位加進來、雙寫、新 API 同時讀新舊
  2. Release B：切 read 到新欄位、停雙寫、刪舊欄位

---

## 8. Development Environment

### 8.1 Local docker compose

`infra/docker-compose.override.yml`（自動疊加）：

```yaml
services:
  api:
    build: ../api
    volumes:
      - ../api:/app  # bind mount for hot reload
    command: ["uvicorn", "app.main:app", "--reload", "--host", "0.0.0.0"]
  worker:
    build: ../api
    volumes:
      - ../api:/app
  web:
    build: ../web
    volumes:
      - ../web:/app
    command: ["pnpm", "dev", "--host", "0.0.0.0"]
    ports: ["5173:5173"]
```

### 8.2 Dev 跑法

```bash
# 啟 DB + Redis + (無 reload 的) api
docker compose up -d postgres redis

# Backend 本機跑（快）
cd api && uvicorn app.main:app --reload

# Frontend 本機跑（Vite HMR 超快）
cd web && pnpm dev

# E2E 測試
cd web && pnpm e2e
```

### 8.3 Seed data

`api/scripts/seed.py`：建 default team + 一個 admin user + 兩個 dev user，方便 local 測試。

---

## 9. Release 流程

1. 開 PR → CI pass → review → merge
2. Merge 觸發 main workflow → build + deploy
3. 若 release tag `v0.2.0`：額外打 git tag、release notes 從 CHANGELOG 生成
4. Grafana 觀察 10 分鐘（`api_error_rate` / `task_failures`）
5. 若有異常 → rollback

---

## 10. 關聯文件

- `deployment.md` — 部署架構
- `environment-variables.md` — env var 清單
- `operations.md` — Scheduled jobs + monitoring + backup
