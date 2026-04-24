# Character Foundry — Deployment Architecture

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** DevOps Agent
> **Based on:** B3 内網自架 server、Backend + Data + Frontend stack

---

## 1. 服務拓撲（Phase 1 單機）

```
                         Internet / VPN
                               │
                        ┌──────┴──────┐
                        │   nginx      │  reverse proxy + TLS + static serve
                        │   (host port)│
                        └──────┬──────┘
                               │
           ┌───────────────────┼──────────────────────┐
           │                   │                      │
           ▼                   ▼                      ▼
     /                     /api/*              /storage/*
     static SPA            FastAPI             FastAPI (signed URL serve)
                           (uvicorn)           本機檔案 stream
                           :8000
                               │
         ┌─────────────────────┼─────────────────────────┐
         │                     │                         │
         ▼                     ▼                         ▼
    ┌─────────┐          ┌─────────┐               ┌─────────┐
    │  Postgres│         │  Redis   │              │ Worker  │
    │   15     │         │   7      │              │  (arq)  │
    │ :5432    │         │  :6379   │              │ async   │
    └─────────┘          └──────────┘              │ tasks   │
         ↑                                          └─────────┘
         │ persistent volume                             │
    ┌────┴────┐                          ┌──────────────┘
    │ pg_data │                          │
    └─────────┘                          ▼
                                  ┌─────────┐
                                  │Scheduler│  arq cron（排程工作）
                                  │ (arq)   │
                                  └─────────┘

                  Shared volume: /srv/character-foundry/storage
                  ────────────────────────────────────────────
                  由 api + worker 共用（hardlink 需要同 filesystem）
```

**設計要點：**
- 全部服務跑在**同一台機器**，Phase 1 不做 clustering
- **Worker 與 Scheduler 分開**（worker 吃 long task、scheduler 跑 cron job），不互相餓死
- **Storage volume 共用**：api 服務讀（signed URL serve）+ worker 寫（生成產物）+ 兩者都做 hardlink（Copy）需要同一 filesystem
- **Postgres + Redis 跟 app 同機**（內網低延遲）；未來要擴展可拆外部 DB

---

## 2. 硬體 / OS 需求

### 2.1 最低配置

| 項目 | 需求 | 理由 |
|---|---|---|
| CPU | 4 vCore | FastAPI + 2 worker + PG 同機 |
| RAM | 16 GB | PG shared_buffers 4G + Redis 1G + Python 3G + buffer |
| Disk | **250 GB SSD** | STORAGE_ROOT 200G + OS 30G + DB 20G（data-md §7）|
| Network | 1 Gbps 內網 | 對外 API（OpenAI / Google Veo）走這條 |
| GPU | **不需要** | Phase 1 AI 全走外部 API |

### 2.2 建議配置（運行順暢）

- CPU 8 vCore
- RAM 32 GB
- Disk 500 GB SSD（NVMe 更好，i2v 影片 I/O 頻繁）
- 獨立 backup volume（另一顆 200 GB HDD/SSD）

### 2.3 OS

- **Ubuntu 22.04 LTS** 或 **24.04 LTS**
- Docker Engine 24+ + docker compose v2
- SELinux / AppArmor 預設狀態即可
- 防火牆只開 nginx 對外 port（80 / 443）

---

## 3. Docker Compose 結構

### 3.1 Services

```yaml
# docker-compose.yml 概覽（完整版 DevOps 實作時寫）

services:
  nginx:
    image: nginx:1.27-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./web/dist:/var/www/html:ro  # Frontend build output
      - ./certs:/etc/nginx/certs:ro
    depends_on:
      - api

  api:
    image: character-foundry-api:${APP_VERSION}
    environment:
      - DATABASE_URL
      - REDIS_URL
      - STORAGE_ROOT=/storage
      - JWT_SECRET
      - STORAGE_SIGNED_URL_SECRET
      - OPENAI_API_KEY
      - VEO_API_KEY
      - VEO_API_URL
      # ... (see environment-variables.md)
    volumes:
      - storage:/storage
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
    restart: unless-stopped

  worker:
    image: character-foundry-api:${APP_VERSION}
    command: ["arq", "app.worker.WorkerSettings"]
    environment:
      <<: *api_environment
    volumes:
      - storage:/storage
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
    deploy:
      replicas: 2  # 可開 2 個 worker 並行處理
    restart: unless-stopped

  scheduler:
    image: character-foundry-api:${APP_VERSION}
    command: ["arq", "app.scheduler.SchedulerSettings"]
    environment:
      <<: *api_environment
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
    restart: unless-stopped

  postgres:
    image: pgvector/pgvector:pg15  # 含 pgvector extension
    environment:
      - POSTGRES_DB=character_foundry
      - POSTGRES_USER=cf_app
      - POSTGRES_PASSWORD
    volumes:
      - pg_data:/var/lib/postgresql/data
      - ./postgres-init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U cf_app"]
      interval: 10s
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "--pass", "${REDIS_PASSWORD}", "ping"]
      interval: 10s
    restart: unless-stopped

volumes:
  pg_data:
  redis_data:
  storage:
    driver: local
    driver_opts:
      type: none
      device: /srv/character-foundry/storage  # 指向獨立 disk partition
      o: bind
```

### 3.2 網路

- 預設 `character-foundry_default` bridge network
- 只 nginx 對外，其他服務**不 publish ports**（只在 internal network 可達）

### 3.3 Healthcheck 與重啟

- 所有服務 `restart: unless-stopped`
- DB / Redis 有 healthcheck，確保 api / worker 等它們 ready
- api 自己提供 `/health` endpoint，可加 healthcheck

---

## 4. Volume 規劃

| Volume / Path | 類型 | 用途 | 備份 |
|---|---|---|---|
| `pg_data` | Named volume | PostgreSQL data | **必備**（每日 pg_dump）|
| `redis_data` | Named volume | Redis persistence（AOF）| 可選（reconstructible from DB）|
| `/srv/character-foundry/storage` | Bind mount（獨立磁碟）| 所有生成圖 / 影片 / exports | **必備**（每日 tar）|
| `/srv/character-foundry/backups` | Bind mount（獨立磁碟）| Backup 存放 | - |
| `/srv/character-foundry/logs` | Bind mount | 日誌 | 保留 30 天 |

**建議：** Storage volume **獨立 partition 或獨立磁碟**。理由：
1. 滿了不會撐爆 OS
2. Disk I/O 影響隔離
3. 備份時 `tar` 整個 mount point 更乾淨

---

## 5. Secrets 管理

### 5.1 Phase 1：`.env` 檔 + 檔案權限

```bash
# /srv/character-foundry/.env (chmod 600, owner = app_user)
POSTGRES_PASSWORD=xxx
REDIS_PASSWORD=xxx
JWT_SECRET=xxx
STORAGE_SIGNED_URL_SECRET=xxx
OPENAI_API_KEY=xxx
VEO_API_KEY=xxx
VEO_API_URL=https://generativelanguage.googleapis.com/v1beta
```

Docker compose 用 `env_file:` 載入，**不進 git**（repo 只保留 `.env.example`）。

### 5.2 Secret 產生

初次部署時產生 secret：

```bash
# JWT_SECRET, STORAGE_SIGNED_URL_SECRET
openssl rand -hex 32

# POSTGRES_PASSWORD, REDIS_PASSWORD
openssl rand -base64 32
```

### 5.3 外部 API key 來源

- OpenAI API key：自家帳號申請
- （Reconciler 走 `OPENAI_API_KEY`，同 gpt-image-2）
- Google Veo API key：GCP / Gemini API 帳號申請

**每個 key 限定用在 Character Foundry** 專用帳號，不跟其他專案共用（方便追蹤使用量 + 限額管理）。

### 5.4 輪替

- JWT_SECRET 換 → 所有 session 失效（使用者重新登入），可接受
- API key 換 → 環境變數改 + 服務重啟即可
- DB password 換 → 需同步更新 PG 使用者與 .env，然後重啟

### 5.5 Phase 2 升級：HashiCorp Vault / Doppler

當 secret 數量超過 10 個或需要多人協作管理時再升級。

---

## 6. TLS / 對內網站點

### 6.1 內網 hostname

假設使用者透過 VPN 連到內網：`character-foundry.internal`

### 6.2 TLS 選項

| 選項 | 適用 |
|---|---|
| **內部 CA 自簽** ⭐ | 有 IT 團隊 manage 內部 CA，頒發給該 hostname 的憑證 |
| Let's Encrypt（需公開 DNS）| 若 hostname 可解析外部 DNS 且機器能聽 80 port，走 DNS challenge |
| 純 HTTP | **僅 Phase 1 PoC 階段可接受**，登入後有 JWT 仍有風險 |

**推薦：** 使用內部 CA 自簽憑證，把 root CA 放進使用者機器的 trust store。

nginx 設定參考：

```nginx
server {
    listen 443 ssl http2;
    server_name character-foundry.internal;

    ssl_certificate /etc/nginx/certs/cert.pem;
    ssl_certificate_key /etc/nginx/certs/key.pem;

    location / {
        root /var/www/html;
        try_files $uri /index.html;
    }

    location /api/ {
        proxy_pass http://api:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /storage/ {
        proxy_pass http://api:8000/storage/;
        proxy_request_buffering off;  # 大檔 stream
        client_max_body_size 100m;    # 大檔上傳
    }

    # SSE 需要特殊設定
    location /api/v1/tasks/ {
        proxy_pass http://api:8000/v1/tasks/;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;          # SSE 不能 buffer
        proxy_read_timeout 300s;      # 長連線
    }
}
```

### 6.3 HTTP → HTTPS redirect

```nginx
server {
    listen 80;
    server_name character-foundry.internal;
    return 301 https://$host$request_uri;
}
```

---

## 7. 初次部署 Runbook

```bash
# 1. 準備機器
ssh ubuntu@character-foundry.internal
sudo apt update && sudo apt install -y docker-ce docker-compose-plugin

# 2. 磁碟分割（若獨立 storage disk）
sudo mkfs.ext4 /dev/sdb
sudo mkdir /srv/character-foundry
sudo mount /dev/sdb /srv/character-foundry
echo "/dev/sdb /srv/character-foundry ext4 defaults 0 2" | sudo tee -a /etc/fstab

# 3. Clone repo
git clone git@internal:team/character-foundry.git
cd character-foundry

# 4. 產生 secrets
cp .env.example .env
./scripts/generate-secrets.sh >> .env   # 產 JWT / signed URL secret
# 手動填 API keys

# 5. 放 TLS 憑證
sudo cp cert.pem key.pem /srv/character-foundry/certs/

# 6. 初次啟動
docker compose up -d postgres redis
docker compose run --rm api alembic upgrade head  # run migrations

# 7. 建第一個 user（admin CLI 或 SQL）
docker compose exec postgres psql -U cf_app -d character_foundry \
    -c "INSERT INTO users (...) VALUES (...);"

# 8. 啟動所有服務
docker compose up -d

# 9. 驗證
curl https://character-foundry.internal/api/health
# → {"status":"ok","db":"ok","storage":"ok"}
```

---

## 8. 更新部署（zero-downtime？）

### 8.1 Phase 1：短暫 downtime 可接受

```bash
cd /opt/character-foundry
git pull
docker compose pull                    # 拉新 image
docker compose run --rm api alembic upgrade head  # migration
docker compose up -d                   # 重啟所有服務（< 10s downtime）
```

### 8.2 Phase 2：rolling update（若需要）

- api 開兩個 instance（nginx load balance）
- 逐一重啟
- 需要 session 共享（JWT 本身是 stateless，OK）

---

## 9. GPU 需求說明

**Phase 1：不需要 GPU。** 所有 AI 模型都透過外部 API：
- OpenAI gpt-image-2（image gen）
- OpenAI gpt-5-mini（prompt reconciler）
- Google Veo 3.1（i2v）

Phase 2 若評估自架本地模型（例：本地 Flux / Hunyuan3D），屆時再規劃 GPU 機器（建議 A100 80G / H100 / L40S）。

---

## 10. 關聯文件

- `environment-variables.md` — 完整環境變數清單
- `operations.md` — Scheduled jobs + monitoring + backup
- `ci-cd.md` — Build / test / deploy pipeline
- `../backend/` — Backend 服務實作細節
- `../data/storage-layout.md` — Storage path 約定
