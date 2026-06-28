---
name: test-gen
description: 为目标函数自动生成单元测试（pytest 风格），覆盖正常路径和关键边界
---

# 测试生成工作流

1. 读目标函数的源码（用 read 工具）
2. 提取：
   - 函数签名
   - 正常路径（典型输入 → 期望输出）
   - 边界（空、None、极值、异常）
3. 生成 pytest 测试：
   - 命名：`test_<func>_<scenario>`
   - 用 parametrize 处理多场景
4. 把测试写到 `tests/test_<name>.py`

# 注意事项

- 不要 mock 自己写的内部函数，只 mock 外部依赖（HTTP、DB、文件系统）
- 每个测试独立，不依赖执行顺序
