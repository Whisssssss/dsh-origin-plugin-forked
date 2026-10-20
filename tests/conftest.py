# -*- coding: utf-8 -*-
"""pytest 全局配置：测试默认不得触碰用户正在使用的 Origin 工程。

背景（issue 实测）：默认 attach 模式下，pytest/smoke 建页会直接写进用户打开的
工程（Contour*/Graph*/M* 等 12 个图页残留，一次保存把 21 KB 撑到 400 KB）。

这里做三件事（必须在 import origin_engine 之前生效，conftest 先于测试模块导入）：
1. DSH_ORIGIN_AUTOKILL=0：测试进程永远不 taskkill 用户手动打开的 Origin；
2. DSH_ORIGIN_OUTPUT_DIR 指到系统临时目录：测试导出的图片/OPJU 不落用户交付目录；
3. 真机用例的准入在 tests/test_live.py：默认跳过，需显式 DSH_ORIGIN_LIVE_TESTS=1
   且建议 ORIGIN_SESSION=isolated。
"""
import os
import tempfile

os.environ.setdefault("DSH_ORIGIN_AUTOKILL", "0")
if not os.environ.get("DSH_ORIGIN_OUTPUT_DIR"):
    os.environ["DSH_ORIGIN_OUTPUT_DIR"] = os.path.join(
        tempfile.gettempdir(), "dsh-origin-tests")
