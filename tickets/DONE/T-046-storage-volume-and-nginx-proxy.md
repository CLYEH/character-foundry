# T-046: shared `/storage` volume + nginx `/storage/` proxy

**Status:** DONE
**Sprint:** 3
**Est:** XS
**Depends on:** none
**Related:** T-005（StorageBackend / LocalFilesystemBackend）

---

## Scope

T-045 ship 完之後，frontend 從 UI 端可以走完整 pipeline 看到 checkpoint card 顯示「已完成」，但 **圖片預覽是破圖 icon**（陳列卡 + 大圖 modal 都壞）。Empirical via chrome-devtools network panel：

- `GET /storage/checkpoints/.../foo.png?token=...` → **HTTP 200 但 `content-type: text/html`**, body 564 bytes
- 那 564 bytes 是 vite 的 SPA fallback `index.html`

兩個結構性 infra bug 疊在一起：

### Bug A — `api` 跟 `worker` 沒共用 `/storage`

`docker-compose.yml` 的 `api` 和 `worker` 是同一個 image 不同 service，但**都沒掛 `/storage` volume**。各自走自己的 ephemeral filesystem。empirical：

```bash
docker compose exec worker ls //storage/checkpoints  # 有檔
docker compose exec api    ls //storage              # 是空的
```

直打 api 端點：
```
GET http://localhost:8000/storage/.../foo.png?token=... → 404 STORAGE_NOT_FOUND
```

— api 的 `LocalFilesystemBackend` 在自己的 fs 找不到 worker 寫的檔。

### Bug B — nginx 沒有 `/storage/` location block

`infra/nginx/nginx.conf` 只有 `/api/` proxy 和 catch-all `/`。`/storage/*` 落到 catch-all → 被導去 vite (web:5173) → vite 的 SPA fallback 回 index.html。

如果 Bug A 修了但 Bug B 沒修，UI 仍然破圖；如果 Bug B 修了但 Bug A 沒修，UI 拿到的是 api 的 `STORAGE_NOT_FOUND` JSON。一起修才會通。

**In scope:**
- `docker-compose.yml`：加 `storage_data` named volume；mount 到 `api` 和 `worker` 服務的 `/storage`
- `infra/nginx/nginx.conf`：加 `location /storage/ { proxy_pass http://api_upstream; ... }`（**無**結尾 slash，保留 `/storage/` 前綴讓 api top-level route 收到）
- 用 chrome-devtools 驗證：登入 → 建 checkpoint → 圖檔在 thumbnail 卡 + lightbox modal 都看得到，content-type 是 `image/png`

**Not in scope:**
- 把 storage backend 換成 S3 / minio（這是 dev 階段，local fs 夠用）
- bind-mount 改 named volume 之外的方案（host filesystem 友善但會有 Windows path quirks，先用 named volume 走標準路）
- 既存「漂在 worker fs 裡」的圖檔搶救——使用者會重生

---

## Planning refs

- `planning/backend/api-shape.md` §5.8 — storage URL 在 `/v1` 之外的設計
- `api/app/api/routes/storage.py` — top-level `/storage/{key:path}`
- `docker-compose.yml` 現況 + `infra/nginx/nginx.conf` 現況

---

## Acceptance criteria

- [x] `docker-compose.yml`：`storage_data` 在 `volumes:` 區塊；`api` 和 `worker` 都 `volumes: - storage_data:/storage`
- [x] `infra/nginx/nginx.conf` 增加 `location /storage/` block，proxy 到 `api_upstream`，**保留** `/storage/` 前綴
- [x] `docker compose down && up -d` 後：worker 寫到 `/storage/test-T046/marker.txt`，api `cat` 讀到 `from worker`（共用 volume 通了）
- [x] chrome-devtools 端到端：建 character `test-T-046` → 自由補述「驗證 T-046 storage 修復」→ checkpoint #1 完成；圖在 thumbnail 卡完整顯示；network 攔截到 thumbnail 請求是 **HTTP 200, content-type: `image/png`**（之前 `text/html`）
- [x] 單測沒受影響

---

## Files expected to touch

- `docker-compose.yml` (edit)
- `infra/nginx/nginx.conf` (edit)

---

## Notes

- 為什麼 named volume 不 bind-mount：bind-mount 在 Windows + Docker Desktop 路徑大小寫 / 權限 / 同步效率都會踩坑，名字 volume 是跨平台預設方案。要 host 上 `ls` 看圖再說（`docker volume inspect storage_data` 拿到 host path）
- 為什麼 nginx `proxy_pass http://api_upstream;` 不寫結尾 slash：寫了會把 `/storage/` 前綴砍掉送 `foo.png` 給 api，但 api 的 route 是 `/storage/{key:path}`，會 404。要對照 `/api/` block（那邊有寫 trailing slash 是因為 api routes 在 `/v1/...`，不需要 `/api` 前綴）
- `nginx.conf` 改完不需重 build image（`/etc/nginx/nginx.conf:ro` bind-mount），但要 `docker compose restart nginx` 或 `kill -HUP` 才會 reload
- 切到 named volume 後，現有 worker fs 內的圖在 container recreate 時消失。如要保留可 `docker cp worker:/storage/. /tmp/save && docker cp /tmp/save api:/storage/.` 但 acceptance 不要求
