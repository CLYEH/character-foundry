# Parallel Worktree Workflow

> 多張 ticket 同時並行用 git worktree 各自分一條 branch 實作，避免互相影響。
> 配合 Auto mode 與 Codex auto-review loop（CLAUDE.md），達到「我說 `start T-XXX`，agent 一路做到 merge」。

---

## 0. 為什麼用 worktree

`git worktree` 把同一個 repo 的不同 branch 同時 check out 到不同資料夾，共用 `.git` object database：

- **真正並行** — 不像 `git stash` 串行切 branch，多個 Claude Code session 可同時跑各自的 ticket 不踩到對方
- **共用物件** — disk 用量比 N 個 clone 省（Sprint 3 五個 worktree 多花約 100MB，不是 ×5）
- **乾淨刪除** — `git worktree remove` 一鍵收掉，不影響主 repo
- **branch 與目錄一一對應** — agent 開 session 時 `git branch --show-current` 就是該 ticket 的 branch，pre-flight 簡化

---

## 1. Sprint 3 dependency graph

```
                      Sprint 2 baseline (main)
                              │
        ┌─────────┬───────────┼─────────┬───────────────┐
        ▼         ▼           ▼         ▼               ▼
      T-029     T-030       T-035     T-036          T-040
   (veo client)(img edit) (preview ext)(alias page) (preview modal ext)
        │         │           │         │               │
        ▼         ▼           │         │               │
      T-033     T-031         │         │               │
   (motion gen)(alias gen)    │         │               │
        │         │           │         │               │
        ▼         ▼           │         │               │
      T-034     T-032         │         │               │
   (motion CRUD)(alias CRUD)  │         │               │
        │         │                                     │
        └────┬────┘                                     │
             ▼                                          │
         T-037 (detail aliases + motions skeleton)      │
             │                                          │
             ▼                                          │
         T-038 (motion preset gen UI)                   │
             │                                          │
             ▼                                          │
         T-039 (custom motion modal) ◄──────────────────┘
             │
             ▼
         T-041 (E2E smoke = M3 gate)
```

**規則：**
- 上方箭頭表「下游 ticket 需要上游 PR 已 merge」
- 同一橫排可平行（不同 worktree 同時開工）
- 跨橫排依序，但 frontend ticket 可在 backend ticket 「endpoint contract 可見」階段先用 MSW mock 起手（不必等 backend merge）

---

## 2. Wave grouping（建議的開工順序）

> **Current status**（每完成一個 wave 就更新本表）：
>
> - Wave A：**done**（2026-04-30 全部 5 張 squash-merged：PR #38 #39 #40 #41 #42）
> - Wave B：**done**（2026-04-30 全部 2 張 squash-merged：PR #46 T-031, PR #47 T-033）
> - Wave C：**待開工**（解鎖；可以開 T-032, T-034 兩個 worktree）
> - Wave D：blocked by Wave C
> - Wave E：blocked by Wave D
> - Wave F：blocked by Wave E
> - Wave G：M3 gate

| Wave | Tickets | 預期同時 worktree 數 | 解鎖條件 |
|---|---|---|---|
| **A** | T-029, T-030, T-035, T-036, T-040 | 5 | 立即可開（main = Sprint 2 done + 本 PR merge）|
| **B** | T-031, T-033 | 2 | T-029 / T-030 merge 後（T-031 需 T-030；T-033 需 T-029）|
| **C** | T-032, T-034 | 2 | T-031 / T-033 merge 後（CRUD 各自接生成 ticket 寫入的 model）|
| **D** | T-037 | 1 | T-032 / T-034 merge 後（detail 頁要 alias / motion list endpoint）|
| **E** | T-038 | 1 | T-037 merge 後（MotionRow / MotionCell 結構先到位）|
| **F** | T-039 | 1 | T-038 merge 後（reuse useGenerateMotion hook）|
| **G** | T-041 | 1 | 全部前置 merge 後（M3 gate）|

**Wave A 是 Sprint 3 最大平行寬度**——5 張 ticket 真正獨立（4 backend + 1 frontend，使用各自不同模組）。後續 wave 收斂成 chain，因為 alias / motion 業務邏輯本身就是 generation → CRUD → UI list → UI mutation 的線性 dependency；硬要平行只會製造 mock-then-rebase 的反覆工。

> Frontend 前置 mock：T-036 / T-040 在 Wave A 時 backend 尚未實裝，前端用 MSW 模擬 endpoint。Wave A merge 後若 contract 微調，前端 PR 內補一次 type 對齊（小改動，藉 openapi-typescript regen）。

### 2.1 「open wave X」入口指令（給未來 session）

使用者在新 session 說「開 Wave X」（X = A / B / C / ...）時，agent 該做：

1. 讀本檔 §2 的 Current status 區塊，確認該 wave 的解鎖條件已達成（若上一 wave 未全部 merge，停下回報）
2. 從本檔 §2 的表格找該 wave 的 ticket 清單
3. 依 §3 的指令把 ticket 對應 worktree 從最新 `main` 切出來
4. 印 worktree 路徑 + branch 名給使用者，並提醒「請在每個目錄手動開 Claude Code 並輸入 `start T-XXX`」
5. 完成後把 §2 的 Current status 區塊本 wave 改成「**進行中**」，順手送一個 docs PR（小改）。Wave 全部 merge 後再改成「done」。

---

## 3. 開 worktree（給「規劃 agent」用的步驟）

當有人說「幫我為 Wave X 開 worktree」（X 可為 A / B / C / D / E / F / G），agent 應做：

```bash
# 主 repo 路徑（用絕對路徑避免歧義）
cd C:/character-foundry

# 1. 主 worktree 已在 main → 先確保最新
git switch main && git pull

# 2. Worktree 根目錄（與主 repo 同層）
mkdir -p ../character-foundry.wt

# 3. 從 §2 表格取出本 wave 的 ticket 清單，每張開一個 worktree
#    Branch 命名：<type>/T-XXX-short-desc（type 依 ticket 性質取 feature / fix / chore / refactor，per CONTRIBUTING §1.1）
#    Sprint 3 全部 13 張都是 feature
#    short-desc 由 ticket 檔名末段抽（tickets/T-XXX-<short-desc>.md → short-desc）

# 範例（Wave A 對應指令；新 wave 改 ticket 編號 + short-desc 即可）：
git worktree add -b feature/T-029-veo-i2v-client          ../character-foundry.wt/T-029 main
git worktree add -b feature/T-030-image2image-inpaint     ../character-foundry.wt/T-030 main
git worktree add -b feature/T-035-prompt-preview-ext      ../character-foundry.wt/T-035 main
git worktree add -b feature/T-036-alias-edit-page         ../character-foundry.wt/T-036 main
git worktree add -b feature/T-040-prompt-preview-modal-ext ../character-foundry.wt/T-040 main

# 列出確認
git worktree list
```

**Branch 命名與 ticket 性質：** 全 5 張都是 `feature`（per CONTRIBUTING §1.1）。後續 wave 開 worktree 前要先看 ticket type（fix / chore / refactor / feature）。

**清理：** ticket merge 後 `git worktree remove ../character-foundry.wt/T-XXX`；branch 由 GitHub auto-delete head branches 處理（per CONTRIBUTING §1.3）。

---

## 4. 在 worktree 裡接到「start T-XXX」要做的事

每個 worktree 是獨立 Claude Code session。使用者在該 worktree 啟動 Claude Code 後給的第一句話通常就是「start T-XXX」（XXX 對應該 worktree 的 branch 名稱）。Agent 跑這條 SOP：

### 4.1 Pre-flight（**動 code 前必跑**）

```bash
# 確認當前 branch 與 ticket 對應
git branch --show-current
# 應該是 feature/T-XXX-...，不是 main
```

- 不對 → **停下** + 告知使用者「這個 worktree 的 branch 是 X，不是 T-XXX。要我建立另一個 worktree 嗎？」
- 對 → 繼續

```bash
# 確保起點是最新的 main
git fetch origin
git rebase origin/main      # 若 worktree 是新建的應該無 op；若已工作中，rebase 進新 main
```

如果 rebase 撞到 conflict：見 §6 衝突處理。

### 4.2 讀規格（緩存或重讀）

照 CLAUDE.md「做實作 / Feature 類任務」步驟：
1. `CLAUDE.md`、`DECISIONS.md`、`CONTRIBUTING.md` §1
2. `tickets/T-XXX-*.md`
3. 單裡列的 Planning refs
4. `STATUS.md`（看本 ticket 的依賴 / 並行情境）
5. **本檔（PARALLEL_WORKFLOW.md）**——確認本 ticket 屬於哪個 wave、上下游是誰

### 4.3 確認上游已 merge / 仍 pending

對每個 `Depends on:` 的 ticket：

```bash
# 上游 ticket 是否已進 main？
git log origin/main --oneline --grep="T-YYY"

# 若沒在 main，origin 是否還有 PR branch？
git ls-remote --heads origin | grep "T-YYY"
```

- 若都已 in `main` → 正常開工
- 若上游仍在 PR / 還沒開單 → 兩個合法選項：
  1. **等**：寫一行 「Blocked by T-YYY」回給使用者，**停**（不要硬幹自己 mock 上游內部結構）
  2. **用 contract mock**：若 ticket scope 允許（多數 frontend ticket 寫明可以用 MSW）→ 採此路徑，PR description 說「上游 contract 用 MSW mock，merge 前 fetch + retest」

預設規則：**backend ticket 永遠等上游 merge**；**frontend ticket 看 ticket 自己的 Notes 是否允許 mock**（T-036 / T-040 明寫可以）。

### 4.4 實作 → 測試 → commit

照 CLAUDE.md 既有流程。一般 ticket 一個 PR、一個 squash commit；中間可有 WIP commit。

### 4.5 Push + 開 PR

照 CLAUDE.md 既有流程（含 pre-push review gate）。Squash 合併由 Codex auto-review loop 觸發。

### 4.6 Auto-review loop with sync rebase（**並行情境的關鍵升級**）

CLAUDE.md 既有 `/loop 10m` 等 Codex `+1` 流程仍適用。**並行情境多一條：每 tick 開頭把 main 拉新並 rebase**。

每次 /loop 觸發時，按順序做：

```bash
# 1. 同步遠端
git fetch origin

# 2. main 是否動了？
LOCAL_BASE=$(git merge-base HEAD origin/main)
REMOTE_MAIN=$(git rev-parse origin/main)

if [ "$LOCAL_BASE" != "$REMOTE_MAIN" ]; then
    # 動了 → rebase
    git rebase origin/main
    # 衝突 → §6 處理
    # 沒衝突 → force push（per CONTRIBUTING §1.2 feature branch 可以 force push）
    git push --force-with-lease
    # Codex 看到新 commit 會重新 review，本 tick 不 merge，繼續 loop
    # （即便上一輪已 +1 也要等新一輪 review）
fi

# 3. 跑 CLAUDE.md 既有 reaction 判讀流程：
#    +1 → CI 全綠才 squash merge
#    eyes / 無 reaction → 繼續 loop
```

**為什麼 rebase 不是 merge：** repo 用 squash merge（CONTRIBUTING §5.1），main 線性。Feature branch rebase 維持線性、避免 merge commit 污染。

**為什麼 force-push 安全：** feature branch 只有自己（worktree）在動，不存在共享協作；用 `--force-with-lease` 防 race（CONTRIBUTING §9）。

### 4.7 Merge 後

```bash
# Codex / CI 通過後本 tick squash merge（CLAUDE.md 既有指令）
gh pr merge $PR --squash --delete-branch

# Loop 終止；ticket 文件已在 PR 內 git mv 至 DONE/、STATUS.md 已更新
# ⚠ 不要在 feature worktree 裡 `git switch main` —— main 已被主 worktree 佔用，git 會拒絕。
# 只 fast-forward 本 clone 的 main ref（不切 branch）：
git fetch origin main:main 2>/dev/null || true   # 若 main 已被 checkout 在主 worktree，這條會 noop；不影響流程

# 提示使用者本 worktree 已完工，可以收掉
echo "T-XXX merged. To remove worktree: cd C:/character-foundry && git worktree remove $(pwd)"
```

**為什麼不在 feature worktree 切回 main：** `git switch main` 會被 git 拒絕（一個 branch 不能同時 checkout 在兩個 worktree）。主 worktree 才是 main 的家；要看 main 的最新狀態請去 `C:/character-foundry`。

---

## 5. Ticket-internal mock 策略（frontend Wave A）

T-036 / T-040 在 Wave A 開工時 backend endpoint 尚未實裝。預期做法：

- 用 **MSW**（既有 frontend dev dependency）模擬 contract
- Mock handler 集中放 `web/src/test/mocks/`
- Backend ticket merge 後，frontend ticket 在自己 PR 內：
  1. `pnpm openapi-typescript` regenerate 對齊 type
  2. 跑 `pnpm test` 看 type 吃不吃 OpenAPI 變更
  3. 視差異補一輪 commit；MSW handler 仍保留以保證單元測試獨立性

不要把 mock handler 留到 production bundle —— vitest / dev only。

---

## 6. 同步衝突 / rebase conflict 處理

並行情境最常見衝突點（按概率排序）：

| 衝突來源 | 典型檔案 | 解法 |
|---|---|---|
| Router 註冊 | `api/app/main.py`、`web/src/routes/__root.tsx` | **加性合併**：兩條 `include_router(...)` / route entry 都保留，順序按 ticket 編號 |
| Worker job 註冊 | `api/app/workers/arq_worker.py` | 加性合併：兩個 job entry 都保留 |
| Schema discriminator 擴 | `api/app/schemas/prompt.py` | 由 T-035 owner 主導合併；只擴 union 不改既有欄位語義 |
| Test fixture 共用 | `api/tests/conftest.py`、`e2e/fixtures/` | 加性合併；命名 collision → 各自加前綴 |
| `pyproject.toml` / `package.json` | 新增 dep | 兩邊 dep 都保留；lock file 重生（`pnpm install` / `uv sync`）|
| 同檔同段 logic 改寫 | 罕見（兩 ticket 不該動同一行） | **停下** + 看清兩邊意圖 + judgment call + 在 commit message 寫清楚為何選 X |

### 6.1 衝突處理 SOP（在 worktree 內 rebase 撞牆時）

```bash
# rebase 卡住會列出衝突檔
git status

# 對每個衝突檔
#  1. 看 git log -1 origin/main -- <file>  ── 上游做了什麼
#  2. 看 git log HEAD@{1} -- <file>          ── 本 branch 做了什麼
#  3. 判斷是「加性」還是「替換」
#     - 加性 → 兩段都留，按邏輯順序排
#     - 替換 → 哪個比較新 / 比較對 → 留新的，舊的若有用就轉成補丁 commit 後再做
#  4. git add <file>

# 解完一個 commit
git rebase --continue
# 若還有 conflict 重複；全清完才結束 rebase
```

### 6.2 不可解的衝突

如果衝突看不懂、或兩邊改動的 semantics 真的不相容（例如 schema 欄位改名與另一邊新增該欄位 logic）：

1. `git rebase --abort`
2. 在 PR 留一條 thread 給使用者：「Rebase blocked by T-YYY 的 X 改動，需要你介入決策」
3. 結束 /loop（不要繼續打轉）
4. 等使用者裁示後再恢復

**不要**用 `--force` / `--theirs` / `--ours` 蓋過去——會吃掉真實的 logic 衝突。

---

## 7. Ground rules（並行 agent 守則）

1. **永遠在自己 worktree 內動**——不要 `cd` 到別 worktree 去看，更不要動別 worktree 的 branch
2. **Cross-worktree 溝通走 PR 與 GitHub**——不開兩個 worktree 互寫 file（會被 git 視為不一致）
3. **不要動 main**——main 只能透過 PR squash merge 進去
4. **每 loop tick rebase**——這是並行情境的核心新動作，不能省
5. **不要長時間擱置 PR**——branch 越久越容易 conflict；ticket 完工就盡快觸發 merge
6. **Conflict 看不懂就停**——停下問人，比硬解錯解傷得少
7. **Ticket scope 不擴**——並行時最容易 scope creep（順手改隔壁），不要

---

## 8. Docker + Worktree 注意事項（**動 docker 前必讀**）

> 來源：T-033 PR #47 在主 worktree 留下 motion 檔案的污染事件（2026-04-30）。Root cause 已確診；T-031 在同情境下用對的 pattern 過關，本節把學到的 do/don't 釘死。

### 8.1 為什麼這條會踩

`docker-compose.yml` + `docker-compose.override.yml` 寫死 bind-mount：

```yaml
api:
  volumes:
    - ./api/app:/app/app          # 解析 = 主 repo 的 api/app → container /app/app
    - ./api/alembic:/app/alembic
    - ./api/tests:/app/tests
```

**`./` 是 docker-compose.yml 所在目錄**——也就是**主 repo `C:/character-foundry`**，不是任何 worktree。整個 docker compose stack 的 `character-foundry-api-1` 永遠看到主 repo 的 code，跟你工作 cwd / 工作 branch 無關。

**含意：**
- Worktree 內改 code → `character-foundry-api-1` **看不到**（讀的是主 repo 的舊版本）
- 對 container 的 `/app/app` 寫東西 → 反向洩漏到主 repo 的 `api/app/`（因為是 bind-mount）

### 8.2 ❌ 不要做

```bash
# 把 worktree 的 code 推進共享 container ——bind-mount 反向洩漏到主 repo
docker cp C:/character-foundry.wt/T-XXX/api/app/. character-foundry-api-1:/app/app

# 對共享 container 寫檔到 /app/... ——同樣反向洩漏
docker exec character-foundry-api-1 sh -c 'echo "..." > /app/app/something.py'

# 從共享 container 跑 test 來「測 worktree 的 code」——你跑的是主 repo 的 code，不是 worktree 的
docker exec character-foundry-api-1 python -m pytest tests/aliases/
```

任何讓 container 寫 `/app/...` 的動作都算這類（含 `pytest --cov` 寫 cache 等）。

### 8.3 ✅ 正確做法（T-031 已驗證）

**起新 container，自己 bind-mount worktree**——不碰共享 stack 的 long-running container：

```bash
# 起 transient container 用 worktree 自己的 code，連到 stack 的 redis / postgres
MSYS_NO_PATHCONV=1 docker run --rm \
    --network character-foundry_default \
    -v "/c/character-foundry.wt/T-XXX/api:/app" \
    -w "/app" \
    -e DATABASE_URL="postgresql+asyncpg://cf_app:${POSTGRES_PASSWORD}@postgres:5432/character_foundry" \
    -e REDIS_URL="redis://:${REDIS_PASSWORD}@redis:6379/0" \
    -e PYTHONPATH=/app \
    character-foundry-api \
    python -m pytest tests/aliases/
```

關鍵：

1. `docker run --rm`：起新 container，跑完即丟（**不是** `docker exec` 進共享的）
2. `-v "/c/character-foundry.wt/T-XXX/api:/app"`：自己指定 bind-mount 到**自己的** worktree
3. `--network character-foundry_default`：連到 docker compose 的 redis / postgres（DB / cache 不寫 host 檔，沒 leak 風險）
4. `character-foundry-api`：reuse 同一份 image（dev parity），所以 deps 一致
5. `MSYS_NO_PATHCONV=1`（Windows / Git Bash only）：避免 `/c/...` 被 MSYS 翻譯成奇怪格式

**Pytest cache / `__pycache__` 寫回的位置 = bind-mount 來源 = 自己的 worktree。**乾淨。

### 8.4 替代做法：純 host-side test（無 docker）

如果不需要 redis / postgres，最簡單就是 host-side 跑：

```bash
cd C:/character-foundry.wt/T-XXX/api
uv run pytest tests/aliases/        # in-process，完全不碰 docker
```

只能測純單元；要碰 DB / Redis 還是回 §8.3。

### 8.5 替代做法：per-worktree docker stack（重武器，僅在 §8.3 不夠時）

如果非要起完整 stack（含 nginx / web / worker 都來自 worktree）：

```bash
cd C:/character-foundry.wt/T-XXX
docker compose -p t-xxx up -d        # -p 不同 project name → 獨立 stack
# 之後 docker exec t-xxx-api-1 ... 才安全（bind-mount 來自 worktree 自己的 docker-compose.yml）
```

代價：
- 多吃 ports（need 改 override.yml 避撞 host port 5432 / 8000 / 5173 / 80）
- redis / postgres 多開一份（disk + RAM）
- 不適合每個 ticket 都起；只在「真的要從 host 用瀏覽器測整套」時用

### 8.6 出錯了怎麼辦

如果你已經 `docker cp` 過、發現主 worktree 有非預期的 modifications + untracked files：

1. **不要急著 stash / commit**——先確認到底污染了什麼：
   ```bash
   cd C:/character-foundry
   git status --short
   ```
2. 如果污染內容跟你 worktree 裡已 commit / 已 push 的內容**一致**（用 `diff` 對比）：
   - 主 worktree 的污染**將在你 PR merge 後 self-resolve**（squash merge 把這些檔寫進 main HEAD，working tree 跟 HEAD 一致 → git status 自然乾淨）
   - **不必手動清**，等 merge 即可
3. 如果污染內容跟 worktree **不一致**（例如某些半成品中間態洩漏）：
   - 在主 repo `git stash --include-untracked -m "<reason>"` 暫存
   - 完工後決定 unstash 或 drop
4. **永遠不要**在主 repo 對污染檔 `git add` + `git commit`——污染會被你的 docs / planning PR 帶進 main，造成意料外的 main 改動

---

## 9. Cleanup / 收尾

Sprint 3 全部 ticket merge 後：

```bash
cd C:/character-foundry

# 列出仍存在的 worktree
git worktree list

# 一次刪掉所有 wt（branch 已被 GitHub auto-delete，本機 worktree 清掉即可）
for w in T-029 T-030 T-035 T-036 T-040 T-031 T-033 T-037 T-032 T-034 T-038 T-039 T-041; do
    git worktree remove ../character-foundry.wt/$w 2>/dev/null
done

# 若 worktree 仍有未提交變更會 refuse；確認該 ticket merged 後再加 --force
```

---

## 10. 關聯文件

- `tickets/README.md` — Ticket 工作流（單張、非並行情境）
- `CONTRIBUTING.md` — Git / PR / review 規則
- `CLAUDE.md` — 「做實作 / Feature 類任務」工作流（base SOP）
- `STATUS.md` — 當前 sprint 進度
- `DECISIONS.md` — 核心決策快查
