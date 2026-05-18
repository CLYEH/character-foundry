# T-086: MCP tool `motion.generate`（packaged, polymorphic）+ motion CRUD 1:1 wraps

**Status:** TODO
**Sprint:** 3.5b
**Est:** M
**Depends on:** T-080（MCP skeleton）、T-081（registry + CI guardrail）、T-083（endpoint mapping）、T-084（pattern reference；progress phase 命名 align）
**Related:** T-085（同 Wave B）、T-087（i2v 是 Last-Event-ID 最關鍵的對象——本單實作 progress 但不處理斷線重連，T-087 補上）

---

## Scope

Wave B 第 3 張：把 motion 領域全部 MCP tool 落地。motion.generate 是 polymorphic（target 可以是 base 或 alias），i2v 是本 sprint 最長 task（30-120s），progress notification 設計是後續 T-087 resumability 的測試對象。

**In scope:**

### Packaged tool — `motion.generate`
- Bundles（per T-083 §3）：
  - `POST /v1/bases/{base_id}/motions` **OR** `POST /v1/aliases/{alias_id}/motions`（target 二選一）
  - 內部 task polling（不對應獨立 endpoint，是 backend task subscription）
- Input schema：
  ```python
  class MotionGenerateIn(BaseModel):
      target_type: Literal["base", "alias"]
      target_id: UUID
      motion_type: Literal[
          "preset_wave", "preset_nod", "preset_shake_head",  # ... 完整 preset 清單 from /v1/meta
          "custom"
      ]
      name: str
      description: str | None = None   # custom only; preset 不需要
  ```
- Output schema：`{ motion: MotionDetail }`（i2v task 完成後的最終 motion；含 video URL）
- Scopes：`["character:write", "task:read"]`
- Async（Q3 Option A）：
  - 阻塞到 i2v task 完成（30-120s 觀察值）
  - Progress phase：`queueing` / `running_i2v` / `finalizing`
  - i2v 中段每 5-10s 推一條 progress notification（避免 nginx idle timeout，即使 T-082 已放寬 180s，progress 對 agent UX 仍是 first-class signal）
  - Veo RAI filter 命中（T-051 已分類）→ MCP error 含明確 `reason`（不是泛型 `model_invalid_request`），agent 可採取 prompt 調整
- Polymorphic target 在 MCP 層怎麼處理：
  - tool 內部根據 `target_type` 選 endpoint，agent 視角只有「一個 tool 兩種 target」
  - 不暴露兩個獨立 tool（`motion.generate_for_base` / `motion.generate_for_alias`）——agent 心智單位是「給某個視覺生動作」，target 是參數不是工具差異

### CRUD 1:1 wraps
- `motion.list_for_base`：wraps `GET /v1/bases/{base_id}/motions`，scope `character:read`
- `motion.list_for_alias`：wraps `GET /v1/aliases/{alias_id}/motions`，scope `character:read`
- `motion.get`：wraps `GET /v1/motions/{motion_id}`，scope `character:read`
- `motion.rename`：wraps `PATCH /v1/motions/{motion_id}`（custom only；preset 拒絕），scope `character:write`
- `motion.delete`：wraps `DELETE /v1/motions/{motion_id}`，scope `character:write`

### Preset 清單來源
- preset motion enum 從 `GET /v1/meta` 的 `preset_motions` 拿（既有 endpoint）
- MCP tool input schema 不 hardcode preset 清單，改用 `motion_type: str` + tool description 引導 agent 先呼 `meta.get`（T-083 mapping 內 `/v1/meta` 屬於 whitelist，會由獨立 1:1 tool 包，本單不負責）
- 或 input schema 用 Literal 但配 build-time 從 meta 拉取——本單**選後者**，把 preset 字串嵌進 schema 讓 agent 直接看 input_schema 就知道有哪些 preset 可選

### 既有 endpoint 補 `require_scope`
- 上述 endpoint 若未套 → 本單順手套（per S3.5-1 pattern）

### Tests
- `api/tests/mcp/tools/test_motion_generate.py`：
  - target=base + motion_type=preset_wave：progress 三 phase → 回 motion（含 video URL）
  - target=alias + motion_type=preset_nod：同上
  - target=base + motion_type=custom + description：同上（custom path）
  - Veo RAI filter 命中 → MCP error 含 reason=`rai_filter`
  - preset 拒絕 rename（CRUD test 內）
- `api/tests/mcp/tools/test_motion_crud.py`：CRUD 5 條 + scope check
- 用 T-029 既有 Veo stub fixture，不打真 provider

**Not in scope:**
- character / alias tool（T-084 / T-085）
- Last-Event-ID resumability（T-087）
- 新增 REST endpoint（純包裝）
- Veo 模型 / RAI filter 行為（T-029 / T-051 已落）
- `/v1/meta` MCP tool（mapping 內列為 1:1，由其他 ticket 涵蓋；本單只「consume」preset 清單）

---

## Planning refs

- `planning/agent-interface/endpoint-mcp-mapping.md`（T-083）
- `planning/agent-interface/open-questions.md` Round 1 Q2（packaging）、Q3（Option A + progress notification）
- `planning/backend/oauth-mcp-integration.md` §3
- `planning/backend/api-shape.md` §5.4（motions）、§5.5（tasks）、§5.9（/v1/meta preset_motions）
- T-029 / T-033 / T-051（Veo 既有實作 + RAI filter handling）
- T-084 / T-085（pattern reference，phase 命名 align）

---

## Acceptance criteria

- [ ] `motion.generate` packaged tool 註冊；bundles 與 T-083 §3 一致
- [ ] 5 條 CRUD 1:1 tool 註冊
- [ ] T-081 CI guardrail 2 pass
- [ ] target=base / target=alias / motion_type=preset / motion_type=custom 四個組合各一條 e2e test 綠
- [ ] Veo RAI filter test 綠（MCP error 含明確 reason，不是泛型）
- [ ] preset rename 拒絕 test 綠
- [ ] Motion 領域全部 endpoint 套 `require_scope`，T-081 scope coverage check pass
- [ ] `pytest api/tests/mcp/tools/test_motion_*.py` 全綠
- [ ] PR description 對照 T-083 §3 表逐條 check

---

## Files expected to touch

- `api/app/mcp/tools/motion.py` (new) — 6 個 tool（1 packaged generate + 5 CRUD）
- `api/app/mcp/schemas/motion.py` (new) — preset enum build-time 拉自 meta
- `api/app/api/routes/motions.py` (edit) — 補 `require_scope`
- `api/tests/mcp/tools/test_motion_generate.py` (new)
- `api/tests/mcp/tools/test_motion_crud.py` (new)
- `tickets/T-086-mcp-tool-motion-generate-and-crud.md` (new — 本單)
- `STATUS.md` (edit)

---

## OAuth scope required

本單**不新增 REST endpoint**（純包裝既有），但會**補 `require_scope`**：

| Endpoint | Scope |
|---|---|
| `GET /v1/bases/{id}/motions` | `character:read` |
| `GET /v1/aliases/{id}/motions` | `character:read` |
| `POST /v1/bases/{id}/motions` | `character:write` + `task:read` |
| `POST /v1/aliases/{id}/motions` | `character:write` + `task:read` |
| `GET /v1/motions/{id}` | `character:read` |
| `PATCH /v1/motions/{id}` | `character:write` |
| `DELETE /v1/motions/{id}` | `character:write` |

決策出處：`planning/agent-interface/endpoint-mcp-mapping.md` §2

---

## MCP tool delta

**新 tool（6 條）：**

| Name | Type | Bundles | Scopes |
|---|---|---|---|
| `motion.generate` | packaged（polymorphic） | base or alias motion create + task wait | `character:write` + `task:read` |
| `motion.list_for_base` | 1:1 | `GET /v1/bases/{id}/motions` | `character:read` |
| `motion.list_for_alias` | 1:1 | `GET /v1/aliases/{id}/motions` | `character:read` |
| `motion.get` | 1:1 | `GET /v1/motions/{id}` | `character:read` |
| `motion.rename` | 1:1 | `PATCH /v1/motions/{id}` | `character:write` |
| `motion.delete` | 1:1 | `DELETE /v1/motions/{id}` | `character:write` |

決策出處：`planning/agent-interface/endpoint-mcp-mapping.md` §3

---

## Notes

- **為什麼 motion.generate 是 packaging 即使只 1 個 POST endpoint**：
  - 它是長 task（30-120s），progress notification 是必要 UX
  - polymorphic target 隱藏 endpoint 差異（base vs alias）對 agent 是「一件事」
  - Per oauth-mcp-integration §3.3 判斷規則：「若 agent 為了完成一件事需要連呼 ≥2 個 endpoint，packaging」——這條規則 baseline 是「≥2 endpoint」，但 motion.generate 額外滿足「polymorphic + 長 async」兩條，packaging 仍對
- **為什麼不拆 `motion.generate_for_base` / `motion.generate_for_alias`**：agent 視角「給角色加動作」是單一概念，target_type 是維度而非工具差異。拆兩個 tool 等於把 implementation detail 推給 agent
- **progress 5-10s 間隔的理由**：T-082 nginx 已放到 180s，但 progress 本身對 agent UX 是 first-class signal；沒 progress 的 1.5 分鐘黑箱對 agent / 人都是 bad UX。Veo client（T-029）若沒原生 progress signal，tool 內部跑 timer 推 fake heartbeat（含 elapsed time），這條 heartbeat 機制 T-087 會用來做 Last-Event-ID
- **RAI filter 為什麼要明確 reason**：T-051 已修「returned 4xx」泛型訊息問題，本單 MCP tool 必須維持那條改進——error 給 agent 看的 `reason` 必須是 `rai_filter` / `quota_exceeded` / `model_unavailable` 等可機器讀字串，agent 才能採取對應 recovery action（如改 prompt / 等待 / 退到 fallback）
- **preset 清單 hardcode vs runtime fetch**：選 hardcode 進 schema（build-time 從 meta 拉），讓 agent `tools/list` 就能看到全部選項；runtime fetch 雖然動態但 agent 多一步前置呼叫。Phase 1 preset 不頻繁變動（per `api-shape.md` §5.9）
- **custom motion 為什麼可以 rename，preset 不行**：preset name 是平台固定字串（per `api-shape.md` §5.4 `PATCH ... # custom only; preset 不可改名`），本單只 wrap 既有行為
