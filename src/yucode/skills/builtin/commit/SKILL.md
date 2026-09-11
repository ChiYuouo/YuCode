---
name: commit
description: 检查当前改动并在用户明确要求时创建 Git 提交。
allowedTools:
  - read_file
  - search_code
  - find_files
  - run_command
mode: inline
history: all
---
先检查当前工作区改动和相关测试状态。仅当用户明确要求提交时，使用合适工具创建提交；不要替用户猜测提交信息或提交无关改动。补充要求：$ARGUMENTS
