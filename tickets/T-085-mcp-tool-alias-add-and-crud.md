# T-085: MCP tool `alias.add`（packaged）+ alias CRUD 1:1 wraps

**Status:** TODO
**Sprint:** 3.5b
**Est:** M
**Depends on:** T-080（MCP skeleton）、T-081（registry + CI guardrail）、T-083（endpoint mapping）、T-084（character.create pattern 是本單抄的對象）
**Related:** T-086（同 Wave B、共用 pattern）、T-087（progress 是 Last-Event-ID 對象）

---

## Scope

Wave B 第 2 張：把 alias 領域全部 MCP tool 落地。涵蓋 text / image / inpaint / mixed 四種 input mode，並支援 reference image 上傳 + alias 建立的 atomic packaging。

**In scope:**

### Packaged tool — `alias.add`
- Bundles（per T-083 endpoint-mcp-mapping.md §3）：
  - `POST /v1/creation-sessions/{id}/reference-images`（input_mode 為 image / inpaint / mixed 時呼）
  - **`POST /v1/characters/{character_id}/aliases/masks`（input_mode 為 inpaint / mixed 且 agent 帶 `mask_file` 時呼；回傳 `mask_id`，tool 內部塞進 alias create body 的 `{ mask: { mask_id } }`）**
  - `POST /v1/characters/{character_id}/aliases`（alias 建立 + async generation task）
- Input schema：
  ```python
  class AliasAddIn(BaseModel):
      character_id: UUID
      name: str
      input_mode: Literal["text", "image", "inpaint", "mixed"]
      freeform_note: str | None = None
      reference_images: list[bytes] | None = None   # base64 packed; required if mode in (image/inpaint/mixed)
      mask_file: bytes | None = None                # base64 packed mask PNG; required if mode = inpaint, optional for mixed
      mask_id: UUID | None = None                   # 已存在 mask（agent 上一輪重用）；mutually exclusive with mask_file
  ```
  - tool 內部處理：若帶 `mask_file` → 先 POST `/aliases/masks` → 拿 `mask_id`；無論 `mask_id` 從哪來，最終都嵌進 alias create body 的 `{ mask: { mask_id } }`（per `app/schemas/prompt.py::MaskInput`：wire field 只傳 `mask_id`，T-031 落地的合約）
  - 不要把 raw mask bytes 直接塞進 alias create body —— 違反既有 REST contract（會 422）
- Output schema：`{ alias: AliasDetail }`（task 完成後的最終 alias）
- Scopes：`["character:write", "task:read"]`
- Async（Q3 Option A）：
  - 阻塞到 task 完成
  - Progress phase：`uploading_references` / `uploading_mask` / `generating_alias`（後者一律送；前兩者按 input_mode 條件送）
  - 任一上傳失敗 → 不建 alias、回 MCP error 含 phase
  - Generation task 失敗 → MCP error 含 phase + underlying AgentError
- Reference image 上傳走哪個 creation-session：
  - 既有 design：alias 的 reference 透過 character 的 active creation session 上傳（per `api-shape.md` §5.2）
  - 若 character 沒有 active session：tool 內部先呼 `POST /v1/characters/{id}/sessions`（若 endpoint 存在）或回 MCP error 引導 agent 先建 session
  - **本單實作時若發現 design gap 需要新 endpoint，停下開 amendment ticket，不在本單擴張 REST**

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
  - image mode：含 reference upload → progress phase `uploading_references` → `generating_alias`
  - **inpaint mode（mask_file path）：tool 內部先上傳 mask 拿 mask_id → progress phase `uploading_references` → `uploading_mask` → `generating_alias`；assert backend 收到的 alias create body `{ mask: { mask_id: <UUID> } }`，不是 raw bytes**
  - **inpaint mode（mask_id path，agent 重用上輪 mask）**：tool 跳過 mask upload，直接用 agent 傳入的 `mask_id` → progress 只 `generating_alias` phase
  - mixed mode：image + freeform_note + optional mask 都有
  - 上傳 reference 失敗 → MCP error，phase=`uploading_references`，alias 沒建
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

- [ ] `alias.add` packaged tool 註冊；bundles 與 T-083 §3 一致（含 reference-images upload + **mask upload** + alias create 三條 endpoint）
- [ ] 4 條 CRUD 1:1 tool 註冊
- [ ] T-081 CI guardrail 2 對所有 tool pass（scope ⊆ union of bundle scopes）
- [ ] 4 種 input mode 各一條 e2e test 綠（含 progress notification + inpaint 走 mask_file vs mask_id 兩條 path）
- [ ] 失敗 path test 綠（reference upload 失敗 / mask upload 失敗 / generation 失敗 / mask_file+mask_id 同時傳）
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
| `POST /v1/characters/{id}/aliases` | `character:write` + `task:read` |
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
| `alias.add` | packaged | (optional) reference-images upload + (optional) mask upload + alias create | `character:write` + `task:read` |
| `alias.list` | 1:1 | `GET /v1/characters/{id}/aliases` | `character:read` |
| `alias.get` | 1:1 | `GET /v1/aliases/{id}` | `character:read` |
| `alias.rename` | 1:1 | `PATCH /v1/aliases/{id}` | `character:write` |
| `alias.delete` | 1:1 | `DELETE /v1/aliases/{id}` | `character:write` |

決策出處：`planning/agent-interface/endpoint-mcp-mapping.md` §3

---

## Notes

- **為什麼 alias.add 是 packaging（3 endpoint，非 2）**：reference image 上傳、mask upload、alias 建立都是獨立 endpoint。Inpaint mode 必須串完整三條 chain：upload mask → 拿 mask_id → 嵌進 alias create body。Text mode 雖然只呼 1 個 endpoint，但與其他三模 share 同一條 tool（input_mode discriminator），agent 不必感知 chain 結構
- **為什麼 mask 不能直接傳 raw bytes 給 alias create endpoint**：既有 REST 合約（per `app/schemas/prompt.py::MaskInput` + T-031 落地）規定 alias create body 的 `mask` 欄位是 `{ mask_id: UUID }`，raw bytes 走獨立 `POST /aliases/masks` upload。若 MCP tool 把 raw bytes 塞進 alias create body 會 422。把 mask upload 包進 packaging tool 是 agent-side ergonomics + 守 REST 合約的正確做法（Codex review #106 round-6 P1 抓到本單早期版本漏 mask upload endpoint，會讓 inpaint/mixed mode 在 MCP 層 unimplementable，已 reconcile）
- **為什麼支援 `mask_id` path 而非只支援 `mask_file`**：agent 多輪迭代場景常重用上一輪 mask（節省 upload + 維持一致 region）；只支援 `mask_file` 強迫每輪重傳浪費 bandwidth + 失去重用性。`mask_file` 與 `mask_id` 互斥（tool 入口拒同時傳）
- **MaskInput schema 為什麼 reuse 既有**：T-030 / T-031 / T-035 / T-036 已穩定，MCP 層不該另定一份；單一 source of truth 避免 schema drift
- **mixed mode 在 alias 是什麼**：image + freeform_note 都有的 case（per `api-shape.md` §5.3），T-030 已支援。本單 tool 只是 pass-through，alias generation backend 處理多模融合
- **reference-images endpoint 屬於 creation session 但 alias 不一定有 active session**：這條是 design ambiguity，本單實作時若遇 → 寫 amendment ticket，**不擴張 REST**。MCP tool 寧可暴露明確 error 引導 agent 先建 session，也不暗自開新 endpoint
- **alias 沒有 export / copy 對應 tool**：alias 是 character 的子資源，export / copy 都在 character 層級處理（M4 範圍，per scope.md §1，T-084 也已 defer）。本單範圍純 alias CRUD + add
- **progress phase 與 T-084 命名一致**：`uploading_references` / `uploading_mask` / `generating_alias` 與 character.create 同 prefix family，agent UI 顯示時容易統一渲染
