# smoke 脚本运行须知（v2.8.0+）

这些脚本会建页、写数据、导出图片。**v2.8.0 起引擎会识别 `smoke/`、`tests/` 脚本，
在 attach 模式下直接拒绝连接**（错误码 `test_attach_blocked`），避免把测试页面写进
你正在使用的 Origin 工程。

## 正确跑法（隔离实例）

```bat
:: 1) 先关闭 Origin（隔离守卫检测到运行中的 Origin 会拒绝，避免劫持）
:: 2) 用隔离会话跑脚本
set ORIGIN_SESSION=isolated
python smoke/advanced_test.py
```

- 隔离实例由脚本自己启动；进程退出时引擎只清理**本次新启动的** Origin 进程，
  不会动你手动打开的实例。
- 导出默认落到 `%TEMP%\dsh-origin-tests\output`；要固定目录可设 `DSH_ORIGIN_OUTPUT_DIR`。
- 真机 pytest 还要额外设 `DSH_ORIGIN_LIVE_TESTS=1`（见 `tests/test_live.py` 文件头）。
- 任务隔离冒烟（COMSI 专用实例，不需要关闭你自己的 Origin）：
  `python smoke/task_isolation_test.py`（脚本内部设 `ORIGIN_SESSION=exclusive`）。

## 不要做的事

- **不要**设 `DSH_ORIGIN_ALLOW_ATTACH_TESTS=1` 去跑你正在编辑的工程
  （除非你明确接受把测试页写进去）。
- **不要**用 `closeAll` 清理疑似测试页：可能把用户自己的同名页（`Contour*` 等）删掉。
  正确做法是用 baseline 差集：`origin_pages_gc(baseline=[...])`。
