# T-085: MCP tool `alias.add`（packaged）+ alias CRUD 1:1 wraps

**Status:** TODO
**Sprint:** 3.5b
**Est:** M
**Depends on:** T-080（MCP skeleton）、T-081（registry + CI guardrail）、T-083（endpoint mapping）、T-084（character.create pattern 是本單抄的對象）、**T-088**（task endpoint `require_scope` 必須先 land 才能讓 alias.add 的 `task:read` 通過 T-081 guardrail 2 的 union check）
**Related:** T-086（同 Wave B、共用 pattern）、T-087（progress 是 Last-Event-ID 對象）

---

## Scope

Wave B 第 2 張：把 alias 領域全部 MCP tool 落地。涵蓋 text / image / inpaint / mixed 四種 input mode，並支援 (inpaint/mixed) mask 上傳 + alias 建立的 atomic packaging。**image / mixed mode 只消費既有 `reference_image_ids`（from Base 的 source creation session），不接受 brand-new reference upload**——`/v1/creation-sessions/{id}/reference-images` 要求 `in_progress` session，alias 建立發生在 `select-base` 之後 session 已 `completed`，不能再寫。Brand-new reference upload at alias time 等 M4 character-scoped upload endpoint（T-083 §6 Q-D7）。

**In scope:**

### Packaged tool — `alias.add`
- Bundles（per T-083 endpoint-mcp-mapping.md §3；scope = union of bundle endpoint scopes per `oauth-mcp-integration.md §3.4`）：
  - **`POST /v1/characters/{character_id}/aliases/masks`（input_mode 為 inpaint / mixed 且 agent 帶 `mask_file` 時呼；回傳 `mask_id`，tool 內部塞進 alias create body 的 `{ mask: { mask_id } }`）**
  - `POST /v1/characters/{character_id}/aliases`（alias 建立 + async generation task）
  - `GET /v1/tasks/{task_id}`（內部 polling 用，等 alias generation task 跑完；scope `task:read` 從這條進 union）
  - ⚠ **`POST /v1/creation-sessions/{id}/reference-images` 不在 bundle**（T-083 §6 Q-D7）：endpoint 要求 `session.status == "in_progress"`，但 alias 建立發生在 `select-base` 之後 session 已 `completed`，呼了會 422 `CONFLICT_SESSION_NOT_ACTIVE`。Phase 1 沒有 character-scoped reference upload endpoint，brand-new reference upload at alias time blocked on Q-D7 / M4
- Input schema：
  ```python
  class AliasAddIn(BaseModel):
      character_id: UUID
      name: str
      input_mode: Literal["text", "image", "inpaint", "mixed"]
      freeform_note: str | None = None
      reference_image_ids: list[UUID] | None = None  # existing ids from Base's source creation session; REQUIRED only for image mode. For mixed mode optional — backend `alias_service._validate_input_mode_matrix` only requires `at least one of (note / refs / mask)`. See T-083 §6 Q-D7.
      mask_file: bytes | None = None                 # base64 packed mask PNG; required if mode = inpaint, optional for mixed
      mask_id: UUID | None = None                    # 已存在 mask（agent 上一輪重用）；mutually exclusive with mask_file
  ```
  - **不接受 `reference_images: list[bytes]` 在 image / mixed mode 直接上傳新 reference** —— Phase 1 backend 無 character-scoped upload endpoint（per T-083 §6 Q-D7）。Agent 必須傳 `reference_image_ids` 指向 character 的 Base 在它的 source creation session 期間已上傳過的 reference（`alias_service._resolve_reference_keys` enforce 同 session 限制）。Tool 入口若收到 `reference_images: list[bytes]` for image/mixed mode → 直接回 MCP error 引導 agent 改用 `reference_image_ids`
  - tool 內部處理：若帶 `mask_file` → 先 POST `/aliases/masks` → 拿 `mask_id`；無論 `mask_id` 從哪來，最終都嵌進 alias create body 的 `{ mask: { mask_id } }`（per `app/schemas/prompt.py::MaskInput`：wire field 只傳 `mask_id`，T-031 落地的合約）
  - 不要把 raw mask bytes 直接塞進 alias create body —— 違反既有 REST contract（會 422）
- Output schema：`{ alias: AliasDetail }`（task 完成後的最終 alias）
- Scopes：`["character:write", "task:read"]`
- Async（Q3 Option A）：
  - 阻塞到 task 完成
  - Progress phase：`uploading_mask` / `generating_alias`（後者一律送；前者只在 inpaint/mixed mode 帶 `mask_file` 才送）
  - 任一上傳失敗 → 不建 alias、回 MCP error 含 phase
  - Generation task 失敗 → MCP error 含 phase + underlying AgentError

### CRUD 1:1 wraps
- `alias.list`：wraps `GET /v1/characters/{character_id}/aliases`，scope `character:read`
- `alias.get`：wraps `GET /v1/aliases/{alias_id}`，scope `character:read`
- `alias.rename`：wraps `PATCH /v1/aliases/{alias_id}`，scope `character:write`
- `alias.delete`：wraps `DELETE /v1/aliases/{alias_id}`，scope `character:write`

### 既有 endpoint 補 `require_scope`
- 上述 endpoint 若未套 → 本單順手套（per S3.5-1 pattern）

### Tests
- `api/tests/mcp/tools/test_alias_add.py`：
  - text mode：純 freeform note → progress 只有 `generating_alias` phase → 回 alias
  - image mode（用既有 reference_image_ids，from Base's source session）：progress 只有 `generating_alias` phase → 回 alias
  - **inpaint mode（mask_file path）：tool 內部先上傳 mask 拿 mask_id → progress phase `uploading_mask` → `generating_alias`；assert backend 收到的 alias create body `{ mask: { mask_id: <UUID> } }`，不是 raw bytes**
  - **inpaint mode（mask_id path，agent 重用上輪 mask）**：tool 跳過 mask upload，直接用 agent 傳入的 `mask_id` → progress 只 `generating_alias` phase
  - mixed mode：existing reference_image_ids + freeform_note + optional mask 都有
  - **負例**：image / mixed mode 傳 `reference_images: list[bytes]`（試圖上傳新 reference）→ MCP error 引導改用 `reference_image_ids`（Phase 1 Q-D7 constraint）
  - **負例**：image / mixed mode `reference_image_ids` 不屬於 character 的 Base source session → backend 回 `NOT_FOUND_REFERENCE_IMAGE` → tool surface MCP error
  - **上傳 mask 失敗 → MCP error，phase=`uploading_mask`，alias 沒建**
  - generation 失敗 → MCP error，phase=`generating_alias`
  - **負例**：同時傳 `mask_file` 與 `mask_id` → MCP error（mutually exclusive，tool 入口拒絕）
- `api/tests/mcp/tools/test_alias_crud.py`：四條 CRUD happy path + scope check

**Not in scope:**
- character / motion tool（T-084 / T-086）
- Last-Event-ID resumability（T-087）
- 新增 REST endpoint（若 design 有 gap → 開 amendment ticket，不在本單）
- Alias 既有 inpaint canvas / mask 演算法（T-030 / T-031 / T-036 已落）

---

## Planning refs

- `planning/agent-interface/endpoint-mcp-mapping.md`（T-083 交付；bundle list 對齊）
- `planning/agent-interface/open-questions.md` Round 1 Q2（packaging）、Q3（Option A）
- `planning/backend/oauth-mcp-integration.md` §3、§3.4
- `planning/backend/api-shape.md` §5.3（aliases）、§5.2（creation session / reference-images）
- T-084 ticket（pattern reference，特別是 progress phase 命名與失敗 cleanup）

---

## Acceptance criteria

- [ ] `alias.add` packaged tool 註冊；bundles 與 T-083 §3 一致（**mask upload + alias create + `GET /v1/tasks/{task_id}` polling 三條 endpoint**；reference-images upload **不在 bundle** per T-083 §6 Q-D7。task GET 進 bundle 才能讓 `task:read` scope 通過 T-081 CI guardrail 2 的 union check）
- [ ] 4 條 CRUD 1:1 tool 註冊
- [ ] T-081 CI guardrail 2 對所有 tool pass（scope ⊆ union of bundle scopes）
- [ ] 4 種 input mode 各一條 e2e test 綠（含 progress notification + inpaint 走 mask_file vs mask_id 兩條 path；image / mixed mode 用既有 `reference_image_ids`，不嘗試 brand-new upload）
- [ ] Q-D7 constraint 守住：image / mixed mode 傳 `reference_images: list[bytes]` 被 tool 拒絕、引導 agent 改用 `reference_image_ids`
- [ ] 失敗 path test 綠（mask upload 失敗 / generation 失敗 / mask_file+mask_id 同時傳 / reference_image_id 不屬於 Base source session）
- [ ] Inpaint mode 經由 tool 上傳 mask file 後，assert backend 收到的 alias create body 是 `{ mask: { mask_id: <UUID> } }` 不是 raw bytes（合約一致性 lock-in）
- [ ] Alias 領域全部 endpoint 套 `require_scope`（含 `/aliases/masks`），T-081 scope coverage check pass
- [ ] `pytest api/tests/mcp/tools/test_alias_*.py` 全綠
- [ ] PR description 對照 T-083 §2 表逐條 check

---

## Files expected to touch

- `api/app/mcp/tools/alias.py` (new) — 5 個 tool（1 packaged add + 4 CRUD）
- `api/app/mcp/schemas/alias.py` (new) — reuse `app/schemas/alias.py` MaskInput
- `api/app/api/routes/aliases.py` (edit) — 補 `require_scope`
- `api/tests/mcp/tools/test_alias_add.py` (new)
- `api/tests/mcp/tools/test_alias_crud.py` (new)
- `tickets/T-085-mcp-tool-alias-add-and-crud.md` (new — 本單)
- `STATUS.md` (edit)

---

## OAuth scope required

本單**不新增 REST endpoint**（純包裝既有），但會**補 `require_scope`**：

| Endpoint | Scope |
|---|---|
| `GET /v1/characters/{id}/aliases` | `character:read` |
| `POST /v1/characters/{id}/aliases` | `character:write` |
| `POST /v1/characters/{id}/aliases/masks` | `character:write` |
| `GET /v1/aliases/{id}` | `character:read` |
| `PATCH /v1/aliases/{id}` | `character:write` |
| `DELETE /v1/aliases/{id}` | `character:write` |

決策出處：`planning/agent-interface/endpoint-mcp-mapping.md` §2

---

## MCP tool delta

**新 tool（5 條）：**

| Name | Type | Bundles | Scopes |
|---|---|---|---|
| `alias.add` | packaged | (optional) mask upload + alias create（reference-images upload **not** bundled per T-083 §6 Q-D7 — image/mixed mode consume existing `reference_image_ids`） | `character:write` + `task:read` |
| `alias.list` | 1:1 | `GET /v1/characters/{id}/aliases` | `character:read` |
| `alias.get` | 1:1 | `GET /v1/aliases/{id}` | `character:read` |
| `alias.rename` | 1:1 | `PATCH /v1/aliases/{id}` | `character:write` |
| `alias.delete` | 1:1 | `DELETE /v1/aliases/{id}` | `character:write` |

決策出處：`planning/agent-interface/endpoint-mcp-mapping.md` §3

---

## Notes

- **為什麼 alias.add 是 packaging（3 endpoint：mask upload + alias create + 內部 task GET polling；inpaint 必用 mask / mixed 帶 mask 時也用）**：mask upload + alias 建立是兩條獨立 REST endpoint，再加 tool 內部一律呼 `GET /v1/tasks/{task_id}` 等 async generation task 完成（per T-083 §3 bundle 列表，`task:read` scope 從這條進 union）。**inpaint** 必須串：upload mask → 拿 mask_id → 嵌進 alias create body → poll task；**mixed** 在 agent 帶 `mask_file` 時也走同一條 mask chain（per `alias_service._validate_input_mode_matrix` 與 T-085 schema：mixed mode `mask` 是 optional，但只要有就必須 upload 拿 mask_id 才能塞進 create body）。**text / image** 與不帶 mask 的 mixed mode 跳過 mask upload，但 alias create + task GET 仍是必經 path。**reference image upload 不在 bundle**（per T-083 §6 Q-D7）—— 詳見下方對應條目。
- **為什麼 mask 不能直接傳 raw bytes 給 alias create endpoint**：既有 REST 合約（per `app/schemas/prompt.py::MaskInput` + T-031 落地）規定 alias create body 的 `mask` 欄位是 `{ mask_id: UUID }`，raw bytes 走獨立 `POST /aliases/masks` upload。若 MCP tool 把 raw bytes 塞進 alias create body 會 422。把 mask upload 包進 packaging tool 是 agent-side ergonomics + 守 REST 合約的正確做法（Codex review #106 round-6 P1 抓到本單早期版本漏 mask upload endpoint，會讓 inpaint/mixed mode 在 MCP 層 unimplementable，已 reconcile）
- **為什麼支援 `mask_id` path 而非只支援 `mask_file`**：agent 多輪迭代場景常重用上一輪 mask（節省 upload + 維持一致 region）；只支援 `mask_file` 強迫每輪重傳浪費 bandwidth + 失去重用性。`mask_file` 與 `mask_id` 互斥（tool 入口拒同時傳）
- **MaskInput schema 為什麼 reuse 既有**：T-030 / T-031 / T-035 / T-036 已穩定，MCP 層不該另定一份；單一 source of truth 避免 schema drift
- **mixed mode 在 alias 是什麼**：per `alias_service._validate_input_mode_matrix` doc string「mixed → at least one of (note / refs / mask)」—— 任何 note / refs / mask 組合都 valid（不是「image + freeform_note」窄定義）。T-030 已支援。本單 tool 只是 pass-through，alias generation backend 處理多模融合。tool 入口僅 enforce「at least one signal」與 image-mode `reference_image_ids` 必填，不對 mixed 額外要求
- **為什麼不在 bundle 裡 reference-image upload（T-083 §6 Q-D7 的 Phase 1 constraint）**：`POST /v1/creation-sessions/{id}/reference-images` 要求 `session.status == "in_progress"`（`assert_session_writable` enforce），但 alias 建立發生在 `select-base` 鎖死 Base 之後，character 的 source creation session 已 `completed`。呼這個 endpoint 必 fail `CONFLICT_SESSION_NOT_ACTIVE`。Phase 1 沒有 character-scoped reference upload endpoint（SPA `web/src/api/endpoints/aliases.ts:83` 的 `uploadCharacterReference` 打的 `/v1/characters/{id}/reference-images` 在 backend 不存在 —— 也是 T-083 §6 Q-D7 flag 的 SPA dead-code suspicion）。Result：`image` / `mixed` mode 在 Phase 1 只能消費 character 的 Base source session 期間上傳過的 `reference_image_ids`（`alias_service._resolve_reference_keys` enforce 同 session 限制）。Brand-new reference upload at alias time = M4 work（per Q-D7 recommended work：加 `POST /v1/characters/{id}/reference-images` character-scoped endpoint）
- **alias 沒有 export / copy 對應 tool**：alias 是 character 的子資源，export / copy 都在 character 層級處理（M4 範圍，per scope.md §1，T-084 也已 defer）。本單範圍純 alias CRUD + add
- **progress phase 與 T-084 命名一致**：`uploading_mask` / `generating_alias` 與 character.create 同 prefix family，agent UI 顯示時容易統一渲染。本單沒有 `uploading_references` phase（per Q-D7：reference upload at alias time 整個 Phase 1 不存在）
