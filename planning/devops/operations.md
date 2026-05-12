# Character Foundry — Operations

> **Status:** Draft v0.1 · 2026-04-23
> **Owner:** DevOps Agent
> **Scope:** Scheduled jobs、monitoring、backup、incident response

---

## 1. Scheduled Jobs 清單

使用 **arq cron** 管理（跟 task queue 共用 Redis，免裝 cron daemon）。

所有 job 實作在 `app/scheduler/` 下，透過 `scheduler` service 執行。

### 1.1 完整排程表

| 頻率 | Job | 目的 | 來源文件 |
|---|---|---|---|
| 每 3 秒 | `publish_queue_positions` | SSE 推播 queue_position | task-queue.md §6.3 |
| 每 15 分 | `mark_stuck_tasks_failed` | 掃 running > 1hr 的 task 標 failed | lifecycle.md §2.5.6 |
| 每小時 | `cleanup_terminal_tasks` | 刪 completed/failed/cancelled > 24h 的 task | lifecycle.md §2.5.4 |
| 每小時 | `refresh_usage_summary`（如果用 materialized view）| REFRESH 物化視圖 | db-schema.md §3.10 |
| 每日 03:00 | `cleanup_abandoned_sessions` | 刪 abandoned > 7 天的 session | lifecycle.md §2.1 |
| 每日 03:15 | `hard_delete_expired_soft_deletes` | 30 天前 soft-deleted 的 Character/Alias/Motion | lifecycle.md §4.1 |
| 每日 03:30 | `cleanup_expired_exports` | 刪 > 7 天的 export ZIP | functional-scope.md F-30 |
| 每日 04:00 | `backup_database` | pg_dump 到 /backups | 本文件 §4 |
| 每日 04:30 | `backup_storage_incremental` | tar 增量備份 storage volume | 本文件 §4 |
| 每週日 05:00 | `reconcile_orphan_files` | 掃 storage 找 DB 沒對應的檔案 | lifecycle.md §5.3 |
| 每月 1 日 06:00 | `rotate_generation_log_partitions` | 建下月 partition、detach/dump/drop 舊的 | lifecycle.md §4.2 |
| 每週一 09:00 | `backup_verification_report` | Email 報告最近一週備份狀態 | 本文件 §4.6 |

### 1.2 實作範例（arq cron）

```python
# app/scheduler/__init__.py
from arq.cron import cron

async def cleanup_terminal_tasks(ctx):
    async with ctx['db_pool'].acquire() as conn:
        deleted = await conn.execute("""
            DELETE FROM tasks
            WHERE status IN ('completed','failed','cancelled')
              AND completed_at < NOW() - INTERVAL '24 hours'
        """)
    logger.info(f"Cleaned up {deleted} terminal tasks")

class SchedulerSettings:
    cron_jobs = [
        cron(cleanup_terminal_tasks,    minute={0}),          # 每小時 0 分
        cron(mark_stuck_tasks_failed,   minute={0, 15, 30, 45}),
        cron(cleanup_abandoned_sessions, hour=3, minute=0),
        cron(hard_delete_expired,       hour=3, minute=15),
        cron(backup_database,           hour=4, minute=0),
        cron(backup_storage,            hour=4, minute=30),
        cron(reconcile_orphan_files,    hour=5, minute=0, day_of_week='sun'),
        cron(rotate_log_partitions,     hour=6, minute=0, day=1),
        # ...
    ]
    redis_settings = RedisSettings.from_dsn(os.environ['REDIS_URL'])
```

### 1.3 Job 失敗處理

- 每個 job 自己 wrap try/except，寫 log 不拋
- 失敗超過 3 次連續 → 透過 monitoring alert 通知
- 關鍵 job（backup）失敗 → 立即 page（見 §3.5 alerts）

---

## 2. Logging

### 2.1 格式

**JSON structured logs**（方便 Loki 索引）：

```json
{
  "timestamp": "2026-04-23T10:30:00Z",
  "level": "INFO",
  "logger": "app.api.characters",
  "message": "Created alias",
  "request_id": "req_abc123",
  "user_id": "uuid",
  "character_id": "uuid",
  "alias_id": "uuid",
  "duration_ms": 1230
}
```

### 2.2 Python 設定

```python
# app/logging_config.py
LOGGING = {
    "version": 1,
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(name)s %(levelname)s %(message)s",
        }
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        }
    },
    "root": {"level": os.environ.get("LOG_LEVEL", "INFO"), "handlers": ["stdout"]},
}
```

### 2.3 輸出

- 全部寫 stdout → docker log driver 收 → Loki 拉
- 不寫本地檔（容器重啟會丟、且難清理）

### 2.4 Request ID 注入

FastAPI middleware：

```python
@app.middleware("http")
async def inject_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    request.state.request_id = request_id

    with contextvars.copy_context():
        logging_ctx.set({"request_id": request_id})
        response = await call_next(request)

    response.headers["X-Request-ID"] = request_id
    return response
```

`AgentError.request_id` 就從 `request.state.request_id` 來。

### 2.5 保留策略

- **即時 log（Loki）：** 30 天
- **重要 event（audit）：** 寫進 DB GenerationLog 已涵蓋主要審計需求
- **Application logs：** 30 天後 rotation 刪除

---

## 3. Monitoring / Metrics

### 3.1 Stack

```
┌──────────────┐
│ api / worker │──┐
└──────────────┘  │
                  │ /metrics (Prometheus format)
                  ▼
              ┌─────────────┐
              │ Prometheus  │
              └─────────────┘
                  │
                  ▼
              ┌─────────────┐
              │  Grafana    │◀── 使用者瀏覽 dashboards
              └─────────────┘
```

### 3.2 加 docker compose

```yaml
services:
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    # 僅內網可達

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3001:3000"  # 內網 port，不經 nginx
    environment:
      - GF_SECURITY_ADMIN_PASSWORD
    volumes:
      - grafana_data:/var/lib/grafana

  loki:
    image: grafana/loki:latest
    volumes:
      - loki_data:/loki

  promtail:
    image: grafana/promtail:latest
    volumes:
      - /var/log:/var/log:ro
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
      - ./promtail.yml:/etc/promtail/promtail.yml
```

### 3.3 核心 Metrics（Backend 提供）

```
# HTTP
http_requests_total{method, path, status}
http_request_duration_seconds{method, path, quantile}

# Tasks
task_queue_depth
task_duration_seconds{task_type, status, quantile}
task_failures_total{task_type, error_code}

# AI
ai_call_total{model, status}
ai_call_duration_seconds{model, quantile}
ai_circuit_breaker_state{model}   # 0=closed, 1=half, 2=open

# Storage
storage_operation_total{op, status}
storage_signed_url_generated_total

# DB
db_pool_size
db_pool_in_use
db_query_duration_seconds{quantile}
```

FastAPI 接 `prometheus_client` + `prometheus-fastapi-instrumentator`。

### 3.4 Grafana Dashboards（Phase 1 三個）

1. **Overview**：HTTP 請求速率 / 錯誤率 / p95 latency / queue depth / AI call rate
2. **AI Health**：各模型成功率、latency、circuit breaker state、cost trend
3. **Infrastructure**：DB pool、Redis memory、disk usage、容器 restart count

### 3.5 Alerts（Alertmanager）

| Alert | 條件 | 嚴重度 | 通知 |
|---|---|---|---|
| Backup failed | 每日 backup job 連續失敗 2 次 | **Critical** | Email + Slack |
| Disk usage > 85% | STORAGE_ROOT partition | **Critical** | Email + Slack |
| Disk usage > 95% | 任一 partition | **Critical** | Email + SMS |
| API error rate > 5% | 5 分鐘窗口 | Warning | Slack |
| API p95 > 2s | 5 分鐘窗口 | Warning | Slack |
| Queue depth > 50 | 5 分鐘持續 | Warning | Slack |
| Circuit breaker OPEN | 任一模型 | Warning | Slack |
| Worker down | 容器重啟超過 3 次 / 10min | **Critical** | Slack |
| DB down | pg_isready 失敗 | **Critical** | Email + SMS |

Alertmanager 設定 Slack webhook + SMTP。

---

## 4. Backup & Restore

### 4.1 什麼要備份

| 項目 | 頻率 | 工具 | 保留 |
|---|---|---|---|
| PostgreSQL | 每日 04:00 | `pg_dump -Fc` | 30 天 |
| STORAGE_ROOT | 每日 04:30 | `tar --listed-incremental` | 完整 7 天 + 增量 30 天 |
| Secrets / config | 手動 + 加密外存 | `gpg` | 長期 |
| Docker compose / nginx config | Git | - | git history |

### 4.2 Backup script

`scripts/backup.sh`：

```bash
#!/bin/bash
set -euo pipefail

BACKUP_DIR=/srv/character-foundry/backups
DATE=$(date +%Y-%m-%d)
DEST="$BACKUP_DIR/$DATE"
mkdir -p "$DEST"

# 1. PostgreSQL
docker exec character-foundry-postgres-1 \
    pg_dump -Fc -U cf_app character_foundry \
    > "$DEST/db.dump"

# 2. Storage (incremental)
tar --listed-incremental=$BACKUP_DIR/storage-snapshot \
    -czf "$DEST/storage-$DATE.tar.gz" \
    /srv/character-foundry/storage

# 3. 驗證
test -s "$DEST/db.dump" || { echo "DB backup empty!"; exit 1; }
test -s "$DEST/storage-$DATE.tar.gz" || { echo "Storage backup empty!"; exit 1; }

# 4. 清理過期
find $BACKUP_DIR -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;

echo "Backup OK: $DEST"
```

交由 arq 的 `backup_database` cron job 呼叫（或用 systemd timer）。

### 4.3 Storage 位置

- **Phase 1：** 同機器不同 volume（另一顆實體磁碟）
- **Phase 2：** 異地同步（rsync 到內網另一台 / 或 upload 到雲端 cold storage）

### 4.4 Restore 流程

`scripts/restore.sh`：

```bash
#!/bin/bash
# 使用：./restore.sh 2026-04-20
set -euo pipefail

DATE=$1
SRC=/srv/character-foundry/backups/$DATE

# 1. 停服務
docker compose stop api worker scheduler

# 2. Restore DB
docker exec -i character-foundry-postgres-1 \
    pg_restore -U cf_app -d character_foundry --clean \
    < "$SRC/db.dump"

# 3. Restore storage
tar -xzf "$SRC/storage-$DATE.tar.gz" -C /

# 4. 重啟
docker compose up -d

echo "Restore OK: $DATE"
```

### 4.5 Restore drill

**每季度**做一次 restore 演練：
1. 準備獨立測試機（或跑 smoke test 環境）
2. Restore 最新 backup
3. 執行 E2E smoke test（登入、開 Character、下載 ZIP）
4. 量測 RTO（recovery time objective），目標 < 1 小時

記錄於 `ops-log/restore-drills.md`。

### 4.6 Weekly report

`backup_verification_report` job 每週一 09:00 寄 email：
- 過去 7 天 backup 成功 / 失敗次數
- 最新 backup 大小
- 磁碟使用趨勢
- 下次 restore drill 提醒（若 > 90 天）

---

## 5. Disk Space 管理

### 5.1 監控門檻

| Partition | Warning | Critical |
|---|---|---|
| `/` (OS) | 80% | 90% |
| `/srv/character-foundry/storage` | 75% | 85% |
| `/srv/character-foundry/backups` | 80% | 90% |
| `pg_data` volume | 75% | 85% |

### 5.2 清理策略

當 storage 到 warning：
1. 檢查 orphan files（reconciliation job 跑過了嗎？）
2. 檢查是否有 soft-deleted 還在等 30 天
3. 檢查 export ZIP 過期
4. 看是否該擴容

當 backup partition 滿：
- 檢查是否 30 天 rotation 有跑
- 考慮 offsite 備份後刪本地

---

## 6. Incident Response

### 6.1 Runbook 建議

建 `planning/devops/runbooks/` 下的 markdown 檔，每個 alert 對應一份：

- `runbooks/backup-failed.md`
- `runbooks/disk-full.md`
- `runbooks/api-error-rate-high.md`
- `runbooks/circuit-breaker-open.md`
- `runbooks/db-down.md`

每份內容：
- 觸發條件
- 診斷步驟
- 可能原因
- 解決方式
- 升級對象

### 6.2 On-call（Phase 1）

內部工具，Phase 1 可能不需要嚴格 on-call rotation。但建議：
- 關鍵 alert（backup failed / disk full / DB down）寄 email + Slack
- 每週有個固定 OPS owner，檢查 Grafana dashboard 5 分鐘

---

## 7. Provider contract replay 維運 SOP（T-058）

Nightly GitHub Actions job `.github/workflows/provider-contract.yml` 對三個 AI provider（gpt-image-2 / gpt-5-mini / Veo 3.1）跑最便宜的真 call，**只斷言 response shape**，shape drift 自動開 `provider-drift` issue。背景見 `planning/harness/roadmap.md` §1 A1；測試本體在 `api/tests/ai/test_real_provider_contract.py`。

### 7.1 為什麼存在

T-042（gpt-image 多圖 multipart）/ T-045（gpt-5-mini reasoning model contract drift）/ T-051（Veo RAI filter shape）連三次同款 failure mode：stub 跟真 provider 漂移、PR CI 全綠但 prod 失敗。Nightly replay 是這條 pattern 的 sensor，先 GitHub 通知再讓人類介入。

### 7.2 測試 API key 註冊

每個 provider **獨立** test 帳號或 project，**不要共用 dev key**（會把 dev quota 吃掉，failure mode 互相干擾）。

| Secret 名稱 | Provider | 怎麼拿 |
|---|---|---|
| `PROVIDER_CONTRACT_OPENAI_KEY` | gpt-image-2 + gpt-5-mini | OpenAI dashboard 開新 project「`cf-contract-test`」→ 該 project 下開 API key |
| `PROVIDER_CONTRACT_VEO_KEY` | Veo 3.1 | Google AI Studio 開獨立 API key（建議掛到專屬 GCP project）|

設定步驟：

1. 開新 OpenAI project + 新 Google AI Studio key（不要重用既有）
2. 設 spending cap（§7.3）
3. GitHub repo Settings → Secrets and variables → Actions → New repository secret
4. 名稱用上表，值貼進去
5. `gh workflow run provider-contract.yml` 手動跑一次驗證

### 7.3 Spending cap 設定

每個 provider account 都要設**月度 spending cap**，避免 nightly run 失控時把整個 quota 燒乾：

- **OpenAI project：** Settings → Limits → Monthly budget = **$5/month**, hard cap（block requests when exceeded）。
- **Google AI Studio / GCP：** Billing → Budgets & alerts → Budget = **$5/month**, alert thresholds at 50% / 100%。GCP 沒有原生 hard cap，靠 budget alert + 手動 disable API。

每次 nightly run 預估成本：

| Provider | Call | 預估 cost |
|---|---|---|
| gpt-image-2 (text2image, 1024×1024, quality=low) | 1 | ~$0.01 |
| gpt-5-mini (chat completions, ~100 tokens in/out) | 1 | < $0.01 |
| Veo 3.1 i2v (duration=3s, smallest legal) | 1 | ~$0.30 |

月度上限 ≈ 31 × $0.32 ≈ $10。$5 cap 留一倍 margin；若連續多次 drift 觸發手動 dispatch 把月底吃滿，pytest 第 N+1 個 call 會收 4xx，自動進 drift issue 流程。

### 7.4 失敗 issue 觸發後的 triage

Nightly job 失敗 → `actions/github-script` 自動開 issue（label `provider-drift`），body 包含 truncated 60 KB pytest output + run URL。On-call 拿到通知後：

1. **看 issue body 內的 raw payload。** Test code 用 `ContractDriftError("<message>\n\nraw payload:\n<payload>")`——payload 全文直接黏在錯誤訊息裡。
2. **三選一判讀：**
   - **Real drift**（provider 改 schema）→ 開新 ticket 修對應 client（gpt-image-2 / reconciler / veo-3.1 client）+ 對應 shape assertion function，commit body 連 provider 公告連結。Close issue 留 reference 到新 ticket。
   - **Transient provider 5xx**（502 / 503 / rate limit）→ 手動 `gh workflow run provider-contract.yml` 重跑一次驗證。連兩次同類失敗 → 視為 partial outage，開 ticket 追 SLA / retry policy。
   - **Test infra 壞掉**（GH Actions runner / secret expired / spending cap hit）→ 修 infra，補 secret，補額度。Close issue 留 root cause 註記。
3. **不要直接關 issue 不處理**——`provider-drift` label 累積數據後可以看 drift 頻率，作為 sprint retro 的素材。

### 7.5 第一次跑後的 baseline 鎖定

T-058 land 後第一次 nightly green run 就是 baseline。之後若 shape 變動（即使 test pass），review 時要意識到 shape function 的判定條件夠不夠嚴——例如 `_videos_present` 接受 `videos[]` 或 `generateVideoResponse.generatedSamples[].video` 兩種 nesting，若有第三種出現要併進來。

### 7.6 PR CI 互動

`pyproject.toml` 的 `[tool.pytest.ini_options]` 設 `addopts = ["-m", "not real_provider"]`，所以 `pr.yml` 跑 `pytest` 時自動 skip 這層。手動本機跑：`cd api && pytest -m real_provider`（需要設好上述兩個 env var）。

---

## 8. 關聯文件

- `deployment.md` — 部署架構
- `environment-variables.md` — env var 清單
- `ci-cd.md` — build / deploy pipeline
- `../data/lifecycle.md` — 資料層清理邏輯
- `../backend/task-queue.md` — Scheduled job 的 arq 實作
