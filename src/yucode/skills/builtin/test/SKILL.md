---
name: test
description: 隔离运行相关测试并返回测试结果摘要。
allowedTools:
  - read_file
  - find_files
  - run_command
mode: fork
history: 3
---
根据当前项目和用户要求选择最相关的测试命令，执行后如实总结通过、失败、取消和下一步。补充要求：$ARGUMENTS
