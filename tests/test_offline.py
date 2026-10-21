# -*- coding: utf-8 -*-
"""离线单测（P2-3）：不连 Origin，任何机器可跑（CI 友好）。

覆盖：错误码表一致性 / LabTalk 门禁 tokenizer / 路径白名单 / FigureSpec
校验与闭环 / plan 构建与 PLAN_STALE / 工具目录完整性。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import origin_errors as oerr          # noqa: E402
import origin_plan as oplan          # noqa: E402
import origin_spec as ospec          # noqa: E402


# ---------------------------------------------------------------------------
# 错误码表
# ---------------------------------------------------------------------------
def test_recovery_map_covers_all_codes():
    for code in oerr.CODE_META:
        assert code in oerr.RECOVERY_MAP, f"{code} 缺恢复映射"


def test_p0_new_codes_present():
    for code in ("com_blocked_by_dialog", "labtalk_blocked", "path_not_allowed"):
        assert code in oerr.CODE_META, f"缺少 {code}"


# ---------------------------------------------------------------------------
# LabTalk 门禁（P0-2）
# ---------------------------------------------------------------------------
def test_gate_blocks_destructive():
    for script in ("delete Book1;", "exit", "doc -s;", "win -c Book1;",
                   "kill dataset1;"):
        bad = oerr.__dict__.get("_labtalk_gate") or \
            getattr(__import__("origin_engine"), "_labtalk_gate")
        r = bad(script)
        assert r is not None, f"{script!r} 应被拦截"


def test_gate_allows_normal_and_strings():
    gate = getattr(__import__("origin_engine"), "_labtalk_gate")
    for script in ("page.nlayers", 'wks.colWidth$ = "delete";',
                   "layer.x.from = 0;", "win -a Book1;"):
        assert gate(script) is None, f"{script!r} 不应被拦截"


# ---------------------------------------------------------------------------
# 路径白名单（P2-8）
# ---------------------------------------------------------------------------
def test_path_guard_default_open(monkeypatch):
    monkeypatch.delenv("DSH_ORIGIN_ALLOWED_ROOTS", raising=False)
    eng = __import__("origin_engine")
    assert eng._path_guard(r"D:\any\where.png") is None


def test_path_guard_blocks_outside(monkeypatch):
    monkeypatch.setenv("DSH_ORIGIN_ALLOWED_ROOTS", r"D:\allowed;D:\also")
    eng = __import__("origin_engine")
    r = eng._path_guard(r"C:\Windows\evil.png")
    assert r is not None and r["error_code"] == "path_not_allowed"
    assert eng._path_guard(r"D:\allowed\ok.png") is None


# ---------------------------------------------------------------------------
# FigureSpec（P1-1）
# ---------------------------------------------------------------------------
def test_spec_roundtrip_plan_id_stable():
    plan = oplan.build_plan({"x": [1, 2, 3], "y": [4.0, 5.0, 6.0]},
                            plot_type="line")
    spec = ospec.spec_from_plan(plan)
    r = ospec.spec_import(spec)
    assert r.get("ok") and r["plan_id"] == plan["plan_id"]


def test_spec_validate_rejects_bad_kind():
    r = ospec.spec_import({"spec_version": 1,
                           "data": {"columns": {"x": [1]}},
                           "plot": {"kind": "hologram"}})
    assert not r.get("ok")


# ---------------------------------------------------------------------------
# plan / PLAN_STALE
# ---------------------------------------------------------------------------
def test_plan_stale_on_data_change():
    p1 = oplan.build_plan({"x": [1, 2], "y": [1.0, 2.0]}, plot_type="line")
    oplan.build_plan({"x": [1, 2], "y": [9.0, 9.0]}, plot_type="line")  # 同签名更新
    p1c = oplan.get_plan(p1["plan_id"])
    assert oplan.check_stale(p1c) is not None       # 有更新计划 => stale
    assert oplan.check_stale(p1c, force=True) is None


def test_plan_cache_bounded():
    assert oplan.cache_size() <= oplan.PLAN_CACHE_MAX


# ---------------------------------------------------------------------------
# 工具目录
# ---------------------------------------------------------------------------
def test_tool_catalog_structure():
    import origin_mcp_server as oms
    registry = oms._build_tool_registry()
    names = list(registry)
    assert len(names) >= 59, f"注册工具 {len(names)} < 59"
    assert len(set(names)) == len(names), "注册表重名"
    # 目录条目与注册表一一对应（目录有的必须能注册）
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "origin_mcp_server.py"),
        encoding="utf-8").read()
    import re
    blocks = re.findall(r'"name":\s*"(origin_[a-z0-9_]+)"', src)
    for n in set(blocks):
        assert n in names, f"目录条目 {n} 未注册"


# ---------------------------------------------------------------------------
# 进程入口：Ctrl+C 静默退出（DSH 停机/重启时控制台会把 Ctrl+C 广播给子进程）
# ---------------------------------------------------------------------------
def test_main_swallows_keyboard_interrupt(monkeypatch):
    import origin_mcp_server as oms

    def boom():
        raise KeyboardInterrupt

    monkeypatch.setattr(oms, "_sync_stdio_server", boom)
    assert oms.main([]) == 0


def test_main_still_dispatches_flags(monkeypatch, capsys):
    import origin_mcp_server as oms

    called = []
    monkeypatch.setattr(oms, "_selftest", lambda: called.append("selftest"))
    oms.main(["--selftest"])
    assert called == ["selftest"]


# ---------------------------------------------------------------------------
# 测试/冒烟防污染守卫（issue：attach 模式跑 pytest 会把页面写进用户工程）
# ---------------------------------------------------------------------------
def test_test_attach_guard_blocks_attach(monkeypatch):
    import origin_engine as eng

    monkeypatch.delenv("DSH_ORIGIN_ALLOW_ATTACH_TESTS", raising=False)
    monkeypatch.setenv("ORIGIN_SESSION", "attach")
    r = eng._test_attach_guard()
    assert r is not None and r.get("error_code") == "test_attach_blocked"
    assert r.get("recoverable") is False


def test_test_attach_guard_allows_isolated_and_pins_no_autokill(monkeypatch):
    import origin_engine as eng

    monkeypatch.delenv("DSH_ORIGIN_ALLOW_ATTACH_TESTS", raising=False)
    monkeypatch.delenv("DSH_ORIGIN_AUTOKILL", raising=False)
    monkeypatch.setenv("ORIGIN_SESSION", "isolated")
    assert eng._test_attach_guard() is None
    # 测试进程绝不允许 autokill 用户 Origin
    assert os.environ["DSH_ORIGIN_AUTOKILL"] == "0"


def test_test_attach_guard_explicit_allow(monkeypatch):
    import origin_engine as eng

    monkeypatch.setenv("DSH_ORIGIN_ALLOW_ATTACH_TESTS", "1")
    monkeypatch.setenv("ORIGIN_SESSION", "attach")
    assert eng._test_attach_guard() is None


def test_connect_refuses_attach_under_tests(monkeypatch):
    """真实入口验证：测试进程在 attach 下连接会被拒，且不碰 Origin。"""
    import origin_engine as eng

    monkeypatch.setattr(eng, "_connected", False, raising=False)
    monkeypatch.setattr(eng, "_origin_app", None, raising=False)
    monkeypatch.delenv("DSH_ORIGIN_ALLOW_ATTACH_TESTS", raising=False)
    monkeypatch.setenv("ORIGIN_SESSION", "attach")
    ok, r = eng._connect_impl()
    assert not ok and r.get("error_code") == "test_attach_blocked"


# ---------------------------------------------------------------------------
# 编码探测：latin-1/cp1252 的 µ 不能被 GBK 抢先生效（真机 issue）
# ---------------------------------------------------------------------------
def test_decode_bytes_cp1252_micro_sign():
    import origin_fileio as fio
    raw = b"Labels,x\r\nLamp,\xb5F,\r\n300,1.0\r\n"
    text, enc = fio._decode_bytes(raw)
    assert enc == "cp1252"
    assert "Lamp,\u00b5F" in text


def test_decode_bytes_gbk_chinese_still_gbk():
    import origin_fileio as fio
    raw = "标签,数值\n温度,25\n".encode("gbk")
    text, enc = fio._decode_bytes(raw)
    assert enc == "gbk"
    assert "标签" in text


def test_decode_bytes_utf8_and_utf16():
    import origin_fileio as fio
    text, enc = fio._decode_bytes("标签,数值\n".encode("utf-8"))
    assert enc == "utf-8" and "标签" in text
    text, enc = fio._decode_bytes("标签,数值\n".encode("utf-16"))
    assert enc == "utf-16" and "标签" in text


# ---------------------------------------------------------------------------
# JSON 序列化：NaN/Inf 必须变成 null（真机 issue：JSON.parse Unexpected token 'N'）
# ---------------------------------------------------------------------------
def test_strict_json_sanitizes_nan_and_inf():
    import json as _json
    import origin_mcp_server as oms

    text = oms._dumps({"a": float("nan"), "b": [float("inf"), -float("inf")], "c": 1})
    assert "NaN" not in text and "Infinity" not in text
    assert _json.loads(text) == {"a": None, "b": [None, None], "c": 1}


# ---------------------------------------------------------------------------
# 连接活性探测：死 COM 代理必须被识别，缓存要被清掉
# ---------------------------------------------------------------------------
def test_probe_connection_detects_dead_proxy(monkeypatch):
    import origin_engine as eng

    class DeadApp:
        def path(self):
            raise RuntimeError("RPC 服务器不可用")

    monkeypatch.setattr(eng, "_origin_app", DeadApp(), raising=False)
    monkeypatch.setattr(eng, "_connected", True, raising=False)
    alive, err = eng._probe_connection()
    assert alive is False and "RPC" in str(err)
    eng._reset_connection_cache("test")
    assert eng._connected is False and eng._origin_app is None


# ---------------------------------------------------------------------------
# 任务隔离（exclusive）：入口校验与临时目录布局
# ---------------------------------------------------------------------------
def test_task_begin_requires_exclusive(monkeypatch):
    import origin_engine as eng

    monkeypatch.setenv("ORIGIN_SESSION", "attach")
    monkeypatch.setattr(eng, "_TASK", None, raising=False)
    r = eng._task_begin_impl(mode="new", target_path=None)
    assert not r.get("ok") and r.get("error_code") == "task_isolation_unavailable"


def test_task_dir_layout_is_next_to_target():
    import origin_engine as eng

    target = os.path.join("D:" + os.sep, "work", "figure.opju")
    d = eng._task_dir("abcd1234", target)
    assert d == os.path.join(os.path.dirname(target), ".dsh-origin-task-abcd1234")


def test_shutdown_without_thread_does_not_poison_com_queue():
    """回归：无线程时 shutdown 不应留下 None 哨兵，否则下一次调用假死 105s。"""
    import origin_engine as eng

    eng.shutdown()
    assert eng._run_on_com_thread(lambda: 42) == 42
    eng.shutdown()

# ---------------------------------------------------------------------------
# 任务加固（P0/P1）离线回归
# ---------------------------------------------------------------------------
def test_page_type_map_matches_origin_ext():
    import origin_edit as oedit
    # OriginExt: OPT_NONE=0 OPT_MATRIX=1 OPT_WORKSHEET=2 OPT_GRAPH=3
    #            OPT_LAYOUT=4 OPT_NOTES=5 OPT_EXTERNAL=6 OPT_IMAGE=29
    assert oedit._PAGE_TYPE[1] == "matrix"
    assert oedit._PAGE_TYPE[2] == "worksheet"
    assert oedit._PAGE_TYPE[3] == "graph"
    assert oedit._PAGE_TYPE[4] == "layout"
    assert oedit._PAGE_TYPE[5] == "notes"


def test_rename_name_validation():
    import origin_edit as oedit
    assert oedit.validate_new_page_name("Graph_1") is None
    assert oedit.validate_new_page_name("x" * 41) is not None
    assert oedit.validate_new_page_name("bad name") is not None
    assert oedit.validate_new_page_name("123") is not None
    assert oedit.validate_new_page_name("Taken", existing={"Taken"}) is not None
    assert oedit.validate_new_page_name("Graph1", existing={"Graph1"},
                                        current="Graph1") is None


def test_task_commit_refuses_empty_before_save(monkeypatch, tmp_path):
    import origin_engine as eng
    monkeypatch.delenv("DSH_ORIGIN_ALLOWED_ROOTS", raising=False)
    target = tmp_path / "figure.opju"
    target.write_bytes(b"ORIGINAL")
    task = {"id": "t1", "target": str(target), "dir": str(tmp_path),
            "working": str(tmp_path / "working.opju"), "pages_before": 2,
            "removed_pages": [], "last_snapshot": None, "snapshot_count": 0,
            "recovery_count": 0, "instance_lost": None}
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    monkeypatch.setattr(eng, "_list_pages_impl",
                        lambda: {"ok": True, "pages": []})
    r = eng._task_commit_impl()
    assert r["error_code"] == "task_commit_refused_empty"
    assert target.read_bytes() == b"ORIGINAL"
