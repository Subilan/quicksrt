# quicksrt 开发规范

## 提交信息约束

所有提交必须遵循 Conventional Commits 1.0.0 格式：

```
<type>[optional scope]: <description>
```

**type（必填）**，本项目中所使用的包括：

- `fix`：修复 bug
- `feat`：新增功能
- `refactor`：重构，行为不变
- `docs`：仅文档变更
- `chore`：杂项（依赖、构建配置等）

**scope（可选）**：影响范围，放在括号里，如 `fix(cli): xxx`、`feat(api): xxx`。

**description（必填）**：简洁描述，小写开头，祈使句，末尾不加句号，如 `fix: correct ass timestamp carry`。