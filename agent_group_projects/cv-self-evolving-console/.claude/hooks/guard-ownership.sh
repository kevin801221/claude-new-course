#!/usr/bin/env bash
# ============================================================
# Hook: guard-ownership
# 事件: PreToolUse
# matcher: "Write|Edit|MultiEdit"（五條規則共用這一個，見 .claude/settings.json）
#
# 這是做什麼用的
# ------------------------------------------------------------
# 在 Claude **要寫檔之前**攔截。五位專家平行開工，唯一不會互相
# 覆蓋的機制是「目錄不重疊」（見 _Context/team-roles.md §1）。
# 這支腳本把那張表裡最貴的五條線變成真的會 exit 2 的擋板：
#
#   1. claude-design_claude-code/  底下任何檔案  → 一律擋
#   2. runs/ 底下的 events.jsonl / runs.jsonl / state.json → 一律擋
#   3. .env / *.pem / credentials.json            → 一律擋
#   4. 共同地基（三份契約 + main/bus/registry + pyproject） → 非 console-owner 擋
#   5. prototype/index.html                       → 非 console-owner 擋
#
# ⚠️ 誠實揭露：這支腳本擋得住什麼、擋不住什麼
# ------------------------------------------------------------
# 它擋的是**三個工具**（Write / Edit / MultiEdit），不是檔案系統層的鎖。
# 檔案本身沒有 chmod -w、沒有 ACL、沒有 git hook —— 只要不經過那三個工具，
# 寫進去完全沒人攔：
#
#   Bash(cat > src/app/bus.py)、Bash(sed -i ...)、Bash(python -c "open(...,'w')")
#   → tool_name 是 Bash，下面 `case "$TOOL" in` 那段直接 exit 0，
#     五條規則一條都不會跑到。
#
# **五條規則的繞道能力完全一樣**，差別只有「要不要看 CV_ROLE」這一件事：
#
#   |         | 看 CV_ROLE 嗎 | 什麼情況擋 | Bash 繞得過嗎 |
#   |---|---|---|---|
#   | 規則 1/2/3 | 不看 | 任何人、任何角色一律擋 | 繞得過 |
#   | 規則 4/5   | 看   | CV_ROLE != console-owner 才擋 | 繞得過 |
#
# 規則 4/5 還多一個洞：**subagent 會繼承主 session 的環境變數** ——
# 你在主 session 開了 CV_ROLE=console-owner，spawn 出去的 dataset-truth 也是
# console-owner，照樣寫得進契約。
#
# 所以這支 hook 的定位是**防手滑**（忘了自己現在是哪個角色就開始打字、
# 順手改契約、順手動前端），不是權限系統。
# 不要在課堂上把它講成權限系統 —— 學生照著敲一行 heredoc 就打臉了，
# 當場沒有一句話救得回來；一開始就講清楚是擋板，反而沒人會失望。
#
# 三層防線，只有中間那層是真鎖
# ------------------------------------------------------------
#   | 層 | 在哪 | 擋什麼 | 強度 |
#   |---|---|---|---|
#   | 1 hook | 這支腳本 | 手滑：走 Write/Edit/MultiEdit 的誤寫 | 弱 —— Bash 繞得過 |
#   | 2 bus  | src/app/bus.py 的 TYPE_OWNERS（第 41 行） | 越權發言：actor 發別人前綴的事件 | **真鎖 —— append_event() 丟 ValueError，事件根本寫不進 events.jsonl** |
#   | 3 /gate | .claude/commands/gate.md | 身分：誰動了不屬於自己的檔案 | 事後抓 —— git status --short 逐檔對 team-roles.md 擁有權表，當場點名 |
#
# 繞過第 1 層硬寫進去的檔案，第 3 層照樣在 git status 裡現形。這就是
# 「弱擋板 + 事後對表」為什麼還是划算：它不是攔住你，是讓你賴不掉。
#
# 怎麼手動測（不靠 Claude）
# ------------------------------------------------------------
#   echo '{"tool_name":"Write","tool_input":{"file_path":"'"$PWD"'/src/app/bus.py"}}' \
#     | .claude/hooks/guard-ownership.sh ; echo "exit=$?"
#   # 預期：🔒 擋下共同地基 ／ exit=2
#
#   CV_ROLE=console-owner  同一條  → exit=0（放行）
#
#   echo '{"tool_name":"Bash","tool_input":{"command":"cat > src/app/bus.py"}}' \
#     | .claude/hooks/guard-ownership.sh ; echo "exit=$?"
#   # 預期：exit=0 而且一個字都不印 —— 這是上面講的繞道，不是腳本壞掉。
# ============================================================

set -uo pipefail

if ! command -v jq >/dev/null 2>&1; then
  echo "guard-ownership.sh: 需要 jq（brew install jq）" >&2
  exit 0   # 沒 jq 不擋，避免誤殺
fi

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
HOOK_CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

case "$TOOL" in
  Write|Edit|MultiEdit) ;;
  *) exit 0 ;;
esac

[[ -z "$FILE_PATH" ]] && exit 0

# 專案根從腳本自己的位置推導（.claude/hooks/ 往上兩層），不硬編絕對路徑
PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

# 相對路徑補成絕對：優先用 hook payload 的 cwd，沒有就用 PROJECT_ROOT
if [[ "$FILE_PATH" == /* ]]; then
  ABS="$FILE_PATH"
else
  ABS="${HOOK_CWD:-$PROJECT_ROOT}/$FILE_PATH"
fi
REL="${ABS#"$PROJECT_ROOT"/}"   # 專案內 → 相對路徑；專案外 → 維持絕對路徑
BASENAME=$(basename "$ABS")

# ------------------------------------------------------------
# 規則 1：Claude Design 教案成品唯讀
# ------------------------------------------------------------
case "$ABS" in
  */claude-design_claude-code/*)
    echo "⛔ guard-ownership.sh 擋下：$FILE_PATH" >&2
    echo "原因：claude-design_claude-code/ 是已發佈的 Claude Design 教案成品，全目錄唯讀（team-roles.md 紅線 2）。" >&2
    echo "正確作法：本專案的前端是複製過來再改的，要改就改 PROJECT_ROOT 的 prototype/index.html；" >&2
    echo "          design-system/tokens.css 只准取用 --ds-* 變數，不准回頭編輯原檔。" >&2
    exit 2
    ;;
esac

# ------------------------------------------------------------
# 規則 2：事件與帳本只能由程式寫
# ------------------------------------------------------------
case "$REL" in
  runs/*)
    case "$BASENAME" in
      events.jsonl|runs.jsonl|state.json)
        echo "🚫 guard-ownership.sh 擋下：$FILE_PATH" >&2
        echo "原因：事件只有 src/app/bus.py 能寫、帳本只有 src/app/registry.py 能寫（team-roles.md §2.1/§2.3）。" >&2
        echo "      手改會讓 seq 單調與事件重播語意直接死掉 —— selfcheck.py 第一條 assert 就是在守這件事。" >&2
        echo "正確作法：呼叫 bus.append_event(run_id, stage=..., type=..., actor=..., data={...}, text=...)；" >&2
        echo "          要留紀錄走 registry.py。要看內容用 Read/Bash，不要用 Write/Edit。" >&2
        exit 2
        ;;
    esac
    ;;
esac

# ------------------------------------------------------------
# 規則 3：秘密（本專案 cwd 不在上層 repo 根，所以自己要有一份）
# ------------------------------------------------------------
case "$BASENAME" in
  .env.example|.env.sample|.env.template|.env.*.example|.env.*.sample)
    ;;   # 範本放行
  .env|.env.*|*.pem|*.p12|*.pfx|credentials.json|*.key|id_rsa|id_ed25519)
    echo "🚫 guard-ownership.sh 擋下：$FILE_PATH" >&2
    echo "原因：符合敏感檔案模式。ROBOFLOW_API_KEY 只在 server 端讀，repo 只留 .env.example。" >&2
    echo "正確作法：要加設定項就改 .env.example（只寫變數名不寫值），真的 key 請使用者自己填進 .env。" >&2
    exit 2
    ;;
esac

# ------------------------------------------------------------
# 規則 4：共同地基 —— 只有 console-owner 能改
# 規則 5：前端 —— 唯一沒辦法用目錄切開的東西，只給一個擁有者
# （這兩條靠 CV_ROLE，是防手滑不是防越權，理由見檔頭）
# ------------------------------------------------------------
case "$REL" in
  _Context/api-contract.md|_Context/team-roles.md|_Context/class_table.schema.json|\
src/app/main.py|src/app/bus.py|src/app/registry.py|pyproject.toml)
    [[ "${CV_ROLE:-}" == "console-owner" ]] && exit 0
    echo "🔒 guard-ownership.sh 擋下：$REL" >&2
    echo "原因：這是共同地基，只有 console-owner 能改（team-roles.md 紅線 3）。" >&2
    echo "      五個人搶改同一份契約，整合當天就會發現四份實作照著四個版本寫。" >&2
    echo "正確作法：提契約變更請求（/contract-change），四段講完 —— 改哪一條 / 現有形狀為什麼做不到 /" >&2
    echo "          改完的完整 JSON / 誰要跟著改，console-owner 批准才動（api-contract.md §12）。" >&2
    echo "      （你確實是 console-owner 的話：CV_ROLE=console-owner 起 session。）" >&2
    exit 2
    ;;
  prototype/index.html)
    [[ "${CV_ROLE:-}" == "console-owner" ]] && exit 0
    echo "🔒 guard-ownership.sh 擋下：$REL" >&2
    echo "原因：前端只有一個擁有者 console-owner（team-roles.md 紅線 1）。" >&2
    echo "      其他四位一行前端都不准改 —— 五個人同時改同一個 index.html = 整合當天互相覆蓋。" >&2
    echo "正確作法：交 JSON 契約，不要交 HTML。要前端顯示什麼就提變更請求，" >&2
    echo "          由 console-owner 落筆（前端一個數字都不准自己算，全部吃後端快照）。" >&2
    exit 2
    ;;
esac

exit 0
