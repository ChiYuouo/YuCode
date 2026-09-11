---
name: review
description: 隔离审查当前工作区改动并返回问题摘要。
allowedTools:
  - read_file
  - search_code
  - find_files
  - run_command
mode: fork
history: 3
---
只读审查当前工作区的相关改动。只报告可验证的问题、位置、影响和建议，不实施修复。补充重点：$ARGUMENTS
