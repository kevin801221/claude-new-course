#!/usr/bin/env bash
# 批次摘要：對每個檔案各跑一次 claude -p（一次性執行，不進聊天），結果接到同一份 md。
#
#   ./scripts/batch-summarize.sh 'src/*.ts'            # 預設輸出 摘要.md
#   ./scripts/batch-summarize.sh '**/*.py' notes.md    # 指定輸出檔
#   MODEL=sonnet ./scripts/batch-summarize.sh 'src/*.ts'
#   DRY_RUN=1 ./scripts/batch-summarize.sh 'src/*.ts'  # 只印會跑什麼，不燒 token
#
# 記住一句話：for 迴圈 + claude -p + 便宜模型（haiku）= 一行指令跑完整個資料夾。

set -uo pipefail

pattern="${1:?用法: $0 '<glob>' [輸出檔]}"
out="${2:-摘要.md}"
model="${MODEL:-haiku}"

shopt -s nullglob
shopt -s globstar 2>/dev/null || true   # bash 3.2（macOS 內建）沒有 globstar，** 就退化成 *
files=($pattern)
(( ${#files[@]} )) || { echo "沒有符合的檔案：$pattern" >&2; exit 1; }

: > "$out"
ok=0; fail=0
for f in "${files[@]}"; do
  [[ -f $f ]] || continue
  printf '==> %s\n' "$f"
  printf '## %s\n\n' "$f" >> "$out"

  if [[ -n ${DRY_RUN:-} ]]; then
    printf '(dry-run) claude -p --model %s --fallback-model sonnet\n\n' "$model" >> "$out"
    ((ok++)); continue
  fi

  # --fallback-model：主模型過載自動換備用模型，整批不會中斷
  if claude -p --model "$model" --fallback-model sonnet \
       "用三行繁體中文說明這個檔案在做什麼，不要客套話：

$(cat "$f")" >> "$out"; then
    ((ok++))
  else
    printf '(此檔摘要失敗，略過)\n' >> "$out"
    ((fail++))
  fi
  printf '\n' >> "$out"
done

printf '完成 → %s（成功 %d、失敗 %d）\n' "$out" "$ok" "$fail"
