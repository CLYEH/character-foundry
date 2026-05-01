# T-047: aspect_ratio dropdown + reconciler framing guidance

**Status:** DONE
**Sprint:** 3
**Est:** S
**Depends on:** T-046（real-AI 端到端通了，畫面看得到圖才知道 framing 不對）
**Related:** T-014（gpt-image-2 client `_SIZE_MAP`）、T-015（reconciler）、T-022 / T-023（前端輸入面板）

---

## Scope

T-046 ship 完之後 user 在 chrome 看到生成的角色圖**頭被 1:1 正方形 frame 截掉**。Empirical：

- 原圖 `1024×1024` RGBA，thumbnail 等比例 `512×512`，**沒做任何 crop**——前後處理乾淨
- 截頭是 OpenAI gpt-image 在 1:1 frame 內畫全身角色的 framing 限制
- 我們現在 hard-code 預設 `aspect_ratio="1:1"`、UI 沒給使用者選

**In scope:**
- **Frontend**：建立角色 / Creation Session 頁面加 size dropdown，4 個 OpenAI gpt-image 合法 enum：`auto`、`1:1`（正方形）、`2:3`（直立）、`3:2`（橫向）。**預設 `2:3`**
- **Backend schema**：`CreateCheckpointRequest` 加 `aspect_ratio: Literal["auto","1:1","2:3","3:2"]` 欄位、default `"2:3"`
- **Worker**：把 `aspect_ratio` 從 payload 讀出來，傳給 `ai_client.generate_image_text2image(..., aspect_ratio=...)`（client 簽章已支援）
- **AI client `_SIZE_MAP`**：加 `"auto": "auto"` 對應；同時砍掉 `9:16` / `16:9`（gpt-image 不收，dall-e-3 legacy）
- **`generation_logs.parameters`**：worker 多寫一個 `aspect_ratio` key，給 retry_same / remix 重現條件用
- **`retry_same` / `remix` 模式**：直接吃 request 帶上來的 aspect_ratio（fresh 同邏輯）。前端 retry_same 按鈕重發 mutation 時會帶當下 dropdown 值，等於使用者「想用什麼比例 retry 就用什麼」；要做「真正繼承來源」需要 generation_log_repo 加查詢函式，scope creep 留待 follow-up
- **`platform_constraints.yaml`**：在 `base_creation` 加 framing 條目（"head and feet fully visible within frame, no cropping at edges, generous head room"），bump version v1 → v1.1
- **Tests**：
  - schema 驗證 enum / default
  - worker 把 aspect_ratio plumb 進 outgoing AI call body（可在現有 mock-based test 裡 assert）
  - 既有 frontend `__tests__` 不破

**Not in scope:**
- 把 aspect_ratio 變成獨立 DB column（用 `generation_log.parameters` JSON 即可，新欄位太重）
- Alias / motion pipeline 的 aspect_ratio（alias 走 `edit_image2image`，size 由 base 圖片決定，不需要這個下拉；motion 是 i2v 另一套 size）
- 把 framing guidance 加到 reconciler `SYSTEM_PROMPT`——`platform_constraints.yaml` 是更對的單一真相來源（reconciler 會把 constraints 串進 final_prompt）

---

## Planning refs

- `planning/backend/api-shape.md` — `CreateCheckpointRequest` schema（新欄位）
- `planning/ux/wireframes.md` — 建立角色頁的輸入面板 layout（新 dropdown）
- `planning/backend/prompt-reconciler.md` §3 — platform constraints 機制
- `api/platform_constraints.yaml` — 既有 framing 規則（"character centered in frame", "full body shot..."）
- `api/app/ai/gpt_image_2.py:53-59` `_SIZE_MAP`

---

## Acceptance criteria

- [x] Frontend：建立角色 session 頁面有「畫面比例」dropdown（4 個 enum，default `直立 2:3 (1024×1536)`）
- [x] Backend：`POST /v1/creation-sessions/{id}/checkpoints` 接受 `aspect_ratio` 欄位、enum 驗證、default `2:3`
- [x] Worker：outgoing OpenAI call 帶上 mapped size；`generation_logs.parameters` 寫入 `aspect_ratio`
- [x] `platform_constraints.yaml` v1.1，加 "head and feet fully visible..." + "generous head room..." 條目
- [x] 真機：chrome-devtools 走全流程，dropdown 顯示對、default 「直立 2:3」、輸出 PNG 維度 **1024×1536**、`generation_logs.parameters` 確認 `{"aspect_ratio": "2:3"}`
- [x] Backend pytest：168 pass / 23 skip；Frontend vitest：154 pass；typecheck 綠
- [-] retry_same / remix inherit：留 follow-up（service 不查 source generation_log，request value 直接吃；scope 變更已記在 ticket Notes）

### 已知 caveat

- Framing 文字 constraint 是 gpt-image 的軟約束。實測 2:3 portrait 比 1:1 改善很多，偶爾仍會微截頂（見 chrome smoke 第二次生成的 robot 角色頂端少許殘影）。要更嚴格還能：強化 prompt 措辭、加 post-gen crop 偵測、調 quality。本 ticket 不在 scope，留待產品反饋再開單

---

## Files expected to touch

- `api/app/schemas/checkpoint.py` (edit)
- `api/app/services/checkpoint_service.py` (edit) — propagate aspect_ratio into payload
- `api/app/workers/jobs/create_checkpoint.py` (edit)
- `api/app/ai/gpt_image_2.py` (edit) — `_SIZE_MAP` 加 auto / 砍 9:16+16:9
- `api/platform_constraints.yaml` (edit) — version + framing constraint
- `web/src/components/creation/TemplateInputPanel.tsx` (edit)
- `web/src/components/creation/ReferenceInputPanel.tsx` (edit)
- `web/src/api/mutations/useCreateCheckpoint.ts` (edit) — payload field
- 對應 unit tests

---

## Notes

- Default 為什麼選 `2:3` 不是 `auto`：產品決策——絕大多數使用情境是「直立全身角色立繪」，2:3 預設正中靶心；`auto` 留給對 framing 沒主張的使用者
- 為什麼 framing 進 yaml 不進 SYSTEM_PROMPT：constraints YAML 是「平台層固定的 image 約束」單一真相來源，reconciler `_compose_output` 會把它接到 final_prompt 開頭，等於每個生成都自帶 framing guidance；放 SYSTEM_PROMPT 只影響 reconciled_note_en 的翻譯/篩選，guidance 不會傳到圖片模型
- `generation_logs.parameters` 已經是 JSON，加 aspect_ratio 不需 migration
- retry_same / remix 直接吃 request 的 aspect_ratio（不繼承來源）：UX 上前端 retry_same 按鈕重新發 mutation 時帶當下 dropdown 值，使用者體感是「改變 ratio 後 retry 會用新 ratio」，跟「menu / freeform 強制繼承」不同——這是刻意的 trade-off。若日後產品決定要 strict-inherit aspect_ratio，再開 follow-up 加 generation_log_repo.get_by_entity 即可
