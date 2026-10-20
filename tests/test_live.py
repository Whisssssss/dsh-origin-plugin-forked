# -*- coding: utf-8 -*-
"""真机集成测试（P2-3）——默认跳过，防止污染用户正在使用的 Origin 工程。

背景（issue 实测）：attach 模式下跑 pytest 会往用户当前工程里写图页/工作簿
（Contour*/Graph*/M*），且不会自动清理。因此这里改成显式准入：

跑法（推荐，隔离实例）：
    # 先关闭 Origin，避免隔离守卫拒绝
    set ORIGIN_SESSION=isolated
    set DSH_ORIGIN_LIVE_TESTS=1
    python -m pytest tests/test_live.py -v

风险自负地跑在用户当前工程（不推荐）：
    set DSH_ORIGIN_LIVE_TESTS=1
    set DSH_ORIGIN_ALLOW_ATTACH_TESTS=1
    python -m pytest tests/test_live.py -v
    # 本模块会记录 baseline，收尾只关闭本次新增的页面（绝不 closeAll）。

无论哪种模式，conftest.py 都会把 DSH_ORIGIN_AUTOKILL 固定为 0：
测试进程永远不会 taskkill 用户手动打开的 Origin。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("originpro", reason="需要 originpro（Origin 自动化包）")

_LIVE_FLAG = os.environ.get("DSH_ORIGIN_LIVE_TESTS", "").strip().lower()
_LIVE_ON = _LIVE_FLAG in ("1", "true", "yes", "on")
_MODE = os.environ.get("ORIGIN_SESSION", "attach").strip().lower()
_ATTACH_OK = os.environ.get("DSH_ORIGIN_ALLOW_ATTACH_TESTS", "").strip().lower() in (
    "1", "true", "yes", "on")

if not _LIVE_ON:
    pytest.skip(
        "真机用例默认跳过（防污染用户工程）：需要时设 DSH_ORIGIN_LIVE_TESTS=1 "
        "且 ORIGIN_SESSION=isolated 再跑",
        allow_module_level=True)

if _MODE != "isolated" and not _ATTACH_OK:
    pytest.skip(
        "真机用例要求隔离会话（ORIGIN_SESSION=isolated）；若确认要在当前工程上跑，"
        "显式设 DSH_ORIGIN_ALLOW_ATTACH_TESTS=1（收尾只删本次新增页）",
        allow_module_level=True)

import origin_engine as engine  # noqa: E402


def _page_names():
    lp = engine.list_pages()
    if not lp.get("ok"):
        return None
    return {p["name"] for p in (lp.get("pages") or []) if p.get("name")}


@pytest.fixture(scope="module", autouse=True)
def _origin_session():
    """连接 + baseline 快照；收尾只关闭本次新增页面，绝不 closeAll。"""
    ok, conn = engine.connect()
    if not ok:
        code = conn.get("error_code") if isinstance(conn, dict) else conn
        pytest.skip("Origin 连接不可用（%s）" % code)
    baseline = _page_names() or set()
    try:
        yield
    finally:
        residual = set()
        try:
            current = _page_names()
            if current is not None:
                added = current - baseline
                if added:
                    engine.manage_pages("close", pages=sorted(added))
                after = _page_names() or set()
                residual = after - baseline
        finally:
            engine.shutdown()
        print("[origin-live] baseline pages: %d | pages diff: %d"
              % (len(baseline), len(residual)))
        assert not residual, "测试残留页面未清理：%s" % sorted(residual)


def test_write_plot_export_roundtrip(tmp_path):
    w = engine.write_data({"x": [1, 2, 3, 4], "y": [1, 4, 9, 16]})
    assert w.get("ok")
    gp = engine.plot(w["worksheet"], y_columns=["y"], x_column="x",
                     plot_type="line")
    assert gp.get("ok"), gp
    out = os.path.join(str(tmp_path), "t.png")
    r = engine.export(gp["graph"], fmt="png", file_path=out)
    assert r.get("ok") and os.path.getsize(out) > 0


def test_labtalk_gate_live():
    r = engine.labtalk("delete SomeBook;")
    assert (not r.get("ok")) and r.get("error_code") == "labtalk_blocked"


def test_release_reconnect_live():
    r = engine.release()
    assert r.get("ok") and r.get("released")
    st = engine.status()
    assert st.get("ok") and st.get("connected")


def test_matrix_write_plot_live(tmp_path):
    z = [[float(i * j) for j in range(4)] for i in range(3)]
    w = engine.matrix_write({"z": z})
    assert w.get("ok") and w.get("writeback_consistent")
    out = os.path.join(str(tmp_path), "contour.png")
    p = engine.matrix_plot(w["matrix"], plot_type="contour", fmt="png",
                           file_path=out)
    assert p.get("ok"), p
    assert os.path.getsize(out) > 0
