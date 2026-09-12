#!/usr/bin/env python3
# ============================================================
# Hook: humanizer-gate
# 事件: Stop
# matcher: "" 或 "*"
#
# 這是做什麼用的（影片案例二：Gary Chen 示範）
# ------------------------------------------------------------
# 屬於「質化品質審核 + 防死循環」。
# 當 Claude 做完這一輪工作，準備在 Stop 事件停下來時觸發。
# 1. 找出本次改動過且尚未通過審查的 Blog 文章（.md / .markdown）。
# 2. 攔截 Stop（回傳 exit 2），指示 Claude 開啟 Subagent 調用
#    【Humanizer 中文版】Skill 進行去 AI 味審核與修正。
# 3. 關鍵防死循環機制（影片 16:15 特別強調）：
#    - 計算文章內容的 MD5 Hash，若此版本已通過審核且未再修改，下次直接放行。
#    - 若連續修改檢查超過 3 輪仍未通過，終止退回，放行並轉交人工確認。
#
# 怎麼開啟（加進專案層 .claude/settings.json）
# ------------------------------------------------------------
# {
#   "hooks": {
#     "Stop": [
#       {
#         "hooks": [
#           {
#             "type": "command",
#             "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/humanizer-gate.py",
#             "statusMessage": "🧐 Humanizer Gate 正在檢查文章品質與 AI 腔調..."
#           }
#         ]
#       }
#     ]
#   }
# }
#
# 怎麼手動測試教學示範
# ------------------------------------------------------------
#   python3 .claude/hooks/humanizer-gate.py ; echo "exit=$?"
# ============================================================

import sys
import json
import os
import hashlib
import subprocess

STATE_FILE = ".claude/humanizer-state.json"
MAX_ROUNDS = 3  # 影片 [16:41] 規定：連續超過 3 輪未通過則交給人工確認

def get_file_hash(filepath):
    """計算檔案 MD5 Hash，用來判斷文章是否有被二次修改"""
    try:
        with open(filepath, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return ""

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"passed_hashes": {}, "rounds": {}}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def get_modified_blog_posts():
    """找出目前工作目錄中修改或新增的 Markdown / Blog 文章"""
    cmd = "git diff --name-only HEAD 2>/dev/null || git status --porcelain"
    try:
        res = subprocess.check_output(cmd, shell=True, text=True)
        files = []
        for line in res.splitlines():
            filepath = line.strip().split()[-1]
            if filepath.endswith((".md", ".markdown")) and os.path.exists(filepath):
                # 排除 .claude 內部檔案與 README
                if not filepath.startswith(".claude/") and "README" not in filepath:
                    files.append(filepath)
        return list(set(files))
    except Exception:
        return []

def main():
    state = load_state()
    modified_posts = get_modified_blog_posts()

    if not modified_posts:
        # 本次工作未修改任何文章，直接放行結束
        sys.exit(0)

    pending_review = []

    for post in modified_posts:
        current_hash = get_file_hash(post)
        passed_hash = state.get("passed_hashes", {}).get(post)

        # 若目前內容的 hash 與已通過的 hash 相同，代表未再被修改，直接放行
        if current_hash and current_hash == passed_hash:
            continue

        # 檢查是否已達到 3 輪重試上限 (影片 [16:41])
        round_count = state.get("rounds", {}).get(post, 0)
        if round_count >= MAX_ROUNDS:
            print(f"⚠️ [Humanizer Gate] 文章 {post} 已連續審核修改 {round_count} 輪，停止退回，放行交由人工確認。", file=sys.stderr)
            continue

        pending_review.append(post)

    if not pending_review:
        # 所有修改的文章都已通過審查或達到上限，放行 Stop
        sys.exit(0)

    # 累加審查輪數
    for post in pending_review:
        state.setdefault("rounds", {})[post] = state.get("rounds", {}).get(post, 0) + 1
    save_state(state)

    # 構造阻擋訊息，指示 Claude 啟動 Agent 調用 Humanizer 中文版 Skill (影片 [14:10] ~ [14:40])
    files_str = ", ".join(pending_review)
    instruction = (
        f"【Humanizer Gate 攔截】：偵測到本次工作修改了文章 [{files_str}]，尚未通過「AI 腔調／去寫作痕跡」審查！\n\n"
        f"請執行以下動作才能結束工作：\n"
        f"1. 請啟動一個 Subagent (Agent)。\n"
        f"2. 該 Agent 必須讀取這幾篇文章，並調用【Humanizer 中文版】Skill 檢查文章是否有過重 AI 腔（如「值得注意的是」、「總的來說」、「毫無疑問」等）。\n"
        f"3. 找出有問題的段落與原因，並完成自然的文句重寫與潤飾。\n"
        f"4. 修正完成後請確認文章語氣自然流暢，方可結束。\n"
    )

    # 輸出至 stderr 並以 Exit Code 2 阻止 Claude 停止
    print(instruction, file=sys.stderr)
    sys.exit(2)

if __name__ == "__main__":
    main()
