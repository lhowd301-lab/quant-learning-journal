# 跨 Agent 共享记忆总线

标准库 CLI：事件日志、增量读取游标、摘要与多 Agent 上下文交接。

## 使用

Python 3.10+，只使用标准库。

```bash
python bus.py log --agent codex --topic demo --text "已完成示例计算，下一步核对输入。"
python bus.py read --for hermes
python bus.py recent -n 10
python bus.py search 示例
python bus.py status
```

数据默认写到程序同目录；可用 `AGENT_MEMORY_HOME` 指定存储目录。JSONL 为主数据，Markdown 为视图，各 Agent 游标独立。公开仓库是空白工具，不包含真实记忆或读取游标。

`install` 仅处理 `AGENT_MEMORY_TARGETS` 指定的指令文件列表（用操作系统路径分隔符分开）。先运行 `install --dry-run` 预览，再自行选择安装位置。未设置目标时不会修改其他文件。公开版移除了个人电脑路径与默认全局接线目标。

## 项目说明

维护者：[lhowd301-lab](https://github.com/lhowd301-lab)。本仓库整理自既有课程、研究或工具项目，开发与文档整理使用了 AI 辅助。代码可运行性与研究结论有效性分别说明，不将历史模拟结果视为实盘业绩。
