# T-048: sync planning docs to today's contract changes + yaml bind-mount

**Status:** DONE
**Sprint:** 3
**Est:** S
**Depends on:** T-042 / T-045 / T-046 / T-047（這張就是把這四張的 contract drift 一次同步進 planning docs）
**Related:** T-043（gpt-image-only doc sync 的 backlog；scope 完全被 T-048 吸收，本張關閉時 supersede 它）

---

## Scope

今天連續 ship 了 4 張 fix / feature ticket，但 planning docs 沒同步。任何人未來 copy-paste 範例 body 又會中同樣的 bug（T-042 一開始的源頭）。

**In scope:**

### Doc updates

| Doc | 內容 |
|---|---|
| `planning/backend/ai-integration.md` §3 | 範例 body 移除 `response_format` / `seed` / `quality=hd`；multi-image edits 標 `image[]`；reconciler 範例改 `max_completion_tokens` 並移除 `temperature`；audit row `parameters` 範例補 `aspect_ratio` |
| `planning/backend/api-shape.md` | `POST /v1/creation-sessions/{id}/checkpoints` body schema 加 `aspect_ratio: 'auto' \| '1:1' \| '2:3' \| '3:2'`，default `2:3`，註解 enum 對應 OpenAI gpt-image legal sizes |
| `planning/ux/wireframes.md` P-04 ASCII 面板 | 「髮型」之後加「畫面比例」dropdown |
| `planning/frontend/component-map.md` | 必要時補 aspectRatio dropdown 對應 Select 元件（已有但欄目可清楚化）|

### Code change（infra coherence，跟 doc 同次處理避免再開單）

- `docker-compose.override.yml` 把 `./api/platform_constraints.yaml:/app/platform_constraints.yaml` 加進 `api` + `worker` bind-mount，讓本機 yaml edit 不需要 rebuild image

### Ticket housekeeping

- `tickets/T-043-...md`（仍在 `tickets/` 下，TODO 狀態）→ 移到 `tickets/DONE/` 並改 status `SUPERSEDED`，body 加註「scope absorbed by T-048」

**Not in scope:**

- `planning/devops/deployment.md`：已正確（prod 規劃早就含 `storage_data` named volume + nginx `/storage/`），T-046 是把 dev 對齊 prod，prod 文件不動
- `planning/backend/prompt-reconciler.md`：grep 沒找到 `max_tokens` / `response_format` wire-level 範例，doc 只引用 env vars + model name，沒 drift
- Veo 部分（`ai-integration.md` §4 的 `aspectRatio: "9:16"` 等）：Veo 自己的 size enum 跟 gpt-image 不同，要 sync 是另一條 ticket
- `errors.py:121` 中文錯誤訊息「請縮短輸入或調高 max_tokens」：operator 看的是 env var 名 `RECONCILER_MAX_TOKENS`，wording 不需要動
- T-044（contract test）：留下原樣，本 ticket 不動 test code

---

## Planning refs

- 今天 ship 的 4 張 ticket（`tickets/DONE/T-042/T-045/T-046/T-047-*.md`）
- 對應 commit history（main `5f627ce` 前 4 個 squash 合併）

---

## Acceptance criteria

- [x] `ai-integration.md` §3 範例 body 不含 `response_format` / `seed` / `quality=hd`；reconciler 範例用 `max_completion_tokens`、不含 `temperature`；audit row `parameters` 範例補 `aspect_ratio` + image_mode
- [x] `api-shape.md` checkpoint create body 含 `aspect_ratio` 欄位 + enum 註解
- [x] `wireframes.md` P-04 ASCII 面板含「畫面比例」dropdown（直立 2:3 default）
- [x] `docker-compose.override.yml` `api` + `worker` 區塊 bind-mount `platform_constraints.yaml`；驗證：`docker compose up -d --force-recreate api worker` 後 `docker compose exec api head /app/platform_constraints.yaml` 看到 v1.1（新 yaml），test_meta 10 passed
- [x] `tickets/T-043-*.md` 移到 DONE/、status `SUPERSEDED by T-048`、加 supersede 註
- [x] STATUS.md：T-048 加進 Sprint 3 列、T-043 改成 SUPERSEDED
- [ ] CI 綠（pending push）

---

## Files expected to touch

- `planning/backend/ai-integration.md` (edit)
- `planning/backend/api-shape.md` (edit)
- `planning/ux/wireframes.md` (edit)
- `planning/frontend/component-map.md` (edit, possibly)
- `docker-compose.override.yml` (edit, +1 line per service)
- `tickets/T-043-...md` (mv to DONE/ + status update)
- `STATUS.md` (edit)

---

## Notes

- 為什麼把 yaml bind-mount fold 進 doc-sync ticket：兩者同主題（"infra coherence"，把 dev 環境跟 spec / prod 對齊）；分兩 ticket 等於兩輪 review / CI 但無 review value。Reviewer 點頭就一起 ship
- T-043 supersede 處理用「mv 到 DONE/ + body 標記」而非刪除：保留 audit trail，後續 grep 仍找得到 T-043 編號
- Veo 部分 doc 沒 sync 是因為 Veo 的 i2v API 我們今天沒實際打過真機（T-029 是純單元測試），doc 的 size enum 沒 empirical 證據糾正；要修先 smoke 一次再開 ticket
