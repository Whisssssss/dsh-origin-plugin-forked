# -*- coding: utf-8 -*-
"""P0/P1 加固回归（离线，不连接 Origin、不碰用户工程）。

覆盖：
- P1  页面类型映射（OPT_MATRIX=1 / OPT_WORKSHEET=2 / OPT_GRAPH=3 / ...）
- P1  rename 走 COM Page.SetName（不再执行 window -r）
- P0-1 探活短重试、实例丢失不静默重建、工作副本自动恢复
- P0-2 commit 拒绝空/退化工程（真 OPJU mtime/size 不变）、force 显式覆盖
- P0-3 begin 初始化快照、结构操作前快照、快照失败只 warning
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import origin_engine as eng          # noqa: E402
import origin_edit as oedit          # noqa: E402
import origin_errors as oerr         # noqa: E402


# ---------------------------------------------------------------------------
# fake Origin 对象（只覆盖被测路径用到的属性/方法）
# ---------------------------------------------------------------------------
class FakePage:
    def __init__(self, name, ptype=3, taken=None):
        self._name = str(name)
        self._type = int(ptype)
        self._taken = taken if taken is not None else set()

    def GetName(self):
        return self._name

    def GetLongName(self):
        return ""

    def SetName(self, value):
        # 模拟 Origin：短名冲突时自动顺延为 next available（Test -> Test1）
        value = str(value)
        if value in self._taken and value != self._name:
            i = 1
            while "%s%d" % (value, i) in self._taken:
                i += 1
            value = "%s%d" % (value, i)
        self._taken.add(value)
        self._name = value

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self.SetName(value)

    @property
    def Type(self):
        return self._type

    @property
    def LongName(self):
        return ""


class FakePages:
    def __init__(self, pages):
        self._pages = list(pages)

    @property
    def Count(self):
        return len(self._pages)

    def __call__(self, key):
        if isinstance(key, int):
            return self._pages[key]
        for p in self._pages:
            if p.GetName() == key:
                return p
        raise KeyError(key)


class FakeConfig:
    def __init__(self):
        self.exit_calls = []

        def _exit(*args, **kwargs):
            self.exit_calls.append(args)
            return True

        self.po = types.SimpleNamespace(
            Pages=FakePages([]),
            Exit=_exit,
            _app=None,
        )


class FakeOp:
    def __init__(self, pages=None, open_ok=True, save_ok=True, new_fail=False):
        self.pages = FakePages(pages or [])
        self.po = types.SimpleNamespace(Pages=self.pages)
        self.open_ok = open_ok
        self.save_ok = save_ok
        self.new_fail = new_fail
        self.opened = []
        self.saved = []
        self.new_calls = 0
        self.lt_calls = []
        self.po.LT_execute = lambda s: self.lt_calls.append(s)
        self.po.LT_get_str = lambda k: None
        self.po.LT_get_var = lambda k: None

    def path(self):
        return ""

    def open(self, path):
        self.opened.append(path)
        return self.open_ok

    def new(self):
        self.new_calls += 1
        if self.new_fail:
            raise RuntimeError("服务器出现意外情况")
        return True

    def save(self, path):
        self.saved.append(path)
        if not self.save_ok:
            return False
        try:
            with open(path, "wb") as fh:
                fh.write(b"FAKE-OPJU")
        except Exception:  # noqa: BLE001
            return False
        return True

    def find_graph(self, name=None):
        for p in self.pages._pages:
            if p.GetName() == name and p.Type == 3:
                return p
        return None


def install_fake_origin(monkeypatch, op=None):
    """替换 originpro / originpro.config / OriginExt 为 fake（不启动 Origin）。"""
    op = op or FakeOp()
    mod = types.ModuleType("originpro")
    cfg_mod = types.ModuleType("originpro.config")
    cfg = FakeConfig()
    cfg.po.Pages = op.pages
    cfg.po.LT_execute = op.po.LT_execute
    cfg_mod.po = cfg.po
    mod.config = cfg_mod
    mod.path = op.path
    mod.open = op.open
    mod.new = op.new
    mod.save = op.save
    mod.find_graph = op.find_graph
    mod.po = op.po

    origin_ext = types.ModuleType("OriginExt")

    class _Capp:
        def PutVisible(self, _v):
            pass

    origin_ext.ApplicationCOMSI = lambda: _Capp()
    origin_ext.ApplicationSI = lambda: _Capp()

    monkeypatch.setitem(sys.modules, "originpro", mod)
    monkeypatch.setitem(sys.modules, "originpro.config", cfg_mod)
    monkeypatch.setitem(sys.modules, "OriginExt", origin_ext)
    return op, cfg


def _task(working, target=None, pages_before=2):
    return {"id": "t1", "mode": "new", "target": (str(target) if target else None),
            "source": None, "dir": os.path.dirname(str(working)),
            "working": str(working), "pages_before": pages_before,
            "removed_pages": [], "last_snapshot": None, "snapshot_count": 0,
            "recovery_count": 0, "instance_lost": None}


# ---------------------------------------------------------------------------
# P1：页面类型映射
# ---------------------------------------------------------------------------
def test_page_type_mapping_matches_origin_ext_constants():
    # OriginExt: OPT_NONE=0 OPT_MATRIX=1 OPT_WORKSHEET=2 OPT_GRAPH=3
    #            OPT_LAYOUT=4 OPT_NOTES=5 OPT_EXTERNAL=6 OPT_IMAGE=29
    assert oedit._PAGE_TYPE[1] == "matrix"
    assert oedit._PAGE_TYPE[2] == "worksheet"
    assert oedit._PAGE_TYPE[3] == "graph"
    assert oedit._PAGE_TYPE[4] == "layout"
    assert oedit._PAGE_TYPE[5] == "notes"
    assert oedit._PAGE_TYPE[6] == "external"
    assert oedit._PAGE_TYPE[29] == "image"


def test_list_pages_recognizes_graph_and_worksheet():
    pages = [FakePage("Graph3", 3), FakePage("Book1", 2), FakePage("MBook1", 1)]
    op = types.SimpleNamespace(po=types.SimpleNamespace(
        Pages=FakePages(pages),
        LT_execute=lambda s: None,
        LT_get_str=lambda k: None))
    r = oedit.list_pages(op, op.po)
    assert r["ok"] is True
    assert r["graphs"] == ["Graph3"]
    assert r["workbooks"] == ["Book1"]
    by_name = {p["name"]: p for p in r["pages"]}
    assert by_name["Graph3"]["type_code"] == 3
    assert by_name["Graph3"]["type_raw"] == 3
    assert by_name["Graph3"]["type"] == "graph"
    assert by_name["Book1"]["type"] == "worksheet"


# ---------------------------------------------------------------------------
# P1：rename 走 COM（弃用 window -r）
# ---------------------------------------------------------------------------
def test_rename_graph_uses_com_setname_not_labtalk():
    gp = FakePage("Graph2", 3)
    op = FakeOp([gp])
    op.find_graph = lambda name=None: gp if name == "Graph2" else None
    r = oedit.manage_pages(op, op.po, "rename", ["Graph2"], "ExScan_4tC2CN")
    assert r["ok"] is True
    assert r["results"][0]["status"] == "applied"
    assert r["results"][0]["renamed_to"] == "ExScan_4tC2CN"
    assert gp.GetName() == "ExScan_4tC2CN"
    # 关键回归：绝不能再用 window -r（事故触发命令）
    assert op.lt_calls == []
    assert all("window -r" not in s for s in op.lt_calls)


def test_rename_workbook_falls_back_to_com_page_setname():
    book = FakePage("Book1", 2)
    op = FakeOp([book])
    op.find_graph = lambda name=None: None
    r = oedit.manage_pages(op, op.po, "rename", ["Book1"], "BookNew")
    assert r["results"][0]["status"] == "applied"
    assert book.GetName() == "BookNew"
    assert op.lt_calls == []


def test_rename_duplicate_name_rejected_before_com():
    pages = [FakePage("Graph2", 3), FakePage("ExScan", 3)]
    gp = pages[0]
    op = FakeOp(pages)
    op.find_graph = lambda name=None: gp if name == "Graph2" else None
    r = oedit.manage_pages(op, op.po, "rename", ["Graph2"], "ExScan")
    assert r["error_code"] == "invalid_request"
    assert "已被现有页面占用" in r["error"]
    assert gp.GetName() == "Graph2"               # 未调用 SetName
    assert op.lt_calls == []


def test_rename_suffix_defensive_branch_still_applied():
    # 防御分支：existing 未看到冲突但 Origin SetName 顺延（并发/缓存），
    # 仍应 applied + actual_name + note，不能谎报失败。
    gp = FakePage("Graph2", 3, taken={"ExScan"})
    op = FakeOp([gp])
    op.find_graph = lambda name=None: gp if name == "Graph2" else None
    r = oedit.manage_pages(op, op.po, "rename", ["Graph2"], "ExScan")
    res = r["results"][0]
    assert res["status"] == "applied"
    assert res["actual_name"] == "ExScan1"
    assert "自动改名" in res["note"]
    assert op.lt_calls == []


def test_rename_name_validation_rules():
    assert oedit.validate_new_page_name("Valid_Name-1") is None
    assert oedit.validate_new_page_name("") is not None
    assert oedit.validate_new_page_name("x" * 41) is not None
    assert oedit.validate_new_page_name("has space") is not None
    assert oedit.validate_new_page_name("中文") is not None
    assert oedit.validate_new_page_name("12345") is not None
    assert oedit.validate_new_page_name("Taken", existing={"Taken"}) is not None
    assert oedit.validate_new_page_name("Graph1", existing={"Graph1"},
                                        current="Graph1") is None


# ---------------------------------------------------------------------------
# P0-1：探活短重试与重启计数
# ---------------------------------------------------------------------------
def test_probe_retries_transient_failure(monkeypatch):
    class Flaky:
        def __init__(self):
            self.n = 0

        def path(self):
            self.n += 1
            if self.n < 3:
                raise RuntimeError("RPC 服务器不可用")
            return ""

    monkeypatch.setattr(eng, "_origin_app", Flaky())
    alive, err = eng._probe_connection(retries=2, delay=0)
    assert alive is True and err is None


def test_probe_gives_up_after_retries(monkeypatch):
    class Dead:
        def path(self):
            raise RuntimeError("RPC 服务器不可用")

    monkeypatch.setattr(eng, "_origin_app", Dead())
    alive, err = eng._probe_connection(retries=2, delay=0)
    assert alive is False and "RPC" in err


def test_reset_cache_records_restart_count(monkeypatch):
    monkeypatch.setattr(eng, "_CONN_STATS", {
        "restart_count": 0, "last_reset_reason": None,
        "last_reset_at": None, "last_recovery": None})
    eng._reset_connection_cache("stale COM proxy: RPC 服务器不可用")
    assert eng._CONN_STATS["restart_count"] == 1
    assert "RPC" in eng._CONN_STATS["last_reset_reason"]
    assert eng._CONN_STATS["last_reset_at"]


# ---------------------------------------------------------------------------
# P0-1：实例丢失不得静默重建
# ---------------------------------------------------------------------------
def _wire_connection_mocks(monkeypatch):
    monkeypatch.setenv("ORIGIN_SESSION", "exclusive")
    monkeypatch.delenv("DSH_ORIGIN_ALLOW_ATTACH_TESTS", raising=False)
    monkeypatch.setattr(eng, "_origin_proc_count", lambda: 0)
    monkeypatch.setattr(eng, "_origin_proc_pids", lambda: set())
    monkeypatch.setattr(eng, "_origin_running", lambda: True)
    monkeypatch.setattr(eng, "_arm_test_origin_cleanup", lambda: None)


def test_connect_refuses_empty_instance_when_no_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "_PROBE_RETRIES", 0)
    monkeypatch.setattr(eng, "_PROBE_RETRY_DELAY", 0)
    monkeypatch.setattr(eng, "_CONN_STATS", {
        "restart_count": 0, "last_reset_reason": None,
        "last_reset_at": None, "last_recovery": None})
    _wire_connection_mocks(monkeypatch)

    class Dead:
        def path(self):
            raise RuntimeError("RPC 服务器不可用")

    task = _task(tmp_path / "missing.opju")
    monkeypatch.setattr(eng, "_origin_app", Dead(), raising=False)
    monkeypatch.setattr(eng, "_connected", True, raising=False)
    monkeypatch.setattr(eng, "_TASK", task, raising=False)

    ok, r = eng._connect_impl()
    assert ok is False
    assert r["error_code"] == "task_instance_lost"
    assert r["recoverable"] is True
    assert r["instance_lost"]["working_exists"] is False
    assert task["instance_lost"] is not None
    # 没有快照时不得启动新实例
    assert eng._origin_app is None


def test_connect_recovers_task_from_working_copy(monkeypatch, tmp_path):
    _wire_connection_mocks(monkeypatch)
    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    working = taskdir / "working.opju"
    working.write_bytes(b"OLD")
    op = FakeOp([FakePage("Graph1", 3), FakePage("Book1", 2)])
    op, _ = install_fake_origin(monkeypatch, op=op)

    task = _task(working)
    monkeypatch.setattr(eng, "_origin_app", None, raising=False)
    monkeypatch.setattr(eng, "_connected", False, raising=False)
    monkeypatch.setattr(eng, "_TASK", task, raising=False)

    ok, info = eng._connect_impl()
    assert ok is True
    assert info["recovered"] is True
    assert info["recovered_from"] == str(working)
    assert info["recovered_pages"] == 2
    assert op.opened == [str(working)]
    assert task["instance_lost"] is None
    assert task["recovery_count"] == 1
    assert eng._CONN_STATS["last_recovery"]["from"] == str(working)


def test_connect_marks_lost_when_reopen_fails(monkeypatch, tmp_path):
    _wire_connection_mocks(monkeypatch)
    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    working = taskdir / "working.opju"
    working.write_bytes(b"OLD")
    op = FakeOp([], open_ok=False)
    op, _ = install_fake_origin(monkeypatch, op=op)

    task = _task(working)
    monkeypatch.setattr(eng, "_origin_app", None, raising=False)
    monkeypatch.setattr(eng, "_connected", False, raising=False)
    monkeypatch.setattr(eng, "_TASK", task, raising=False)

    ok, r = eng._connect_impl()
    assert ok is False
    assert r["error_code"] == "task_instance_lost"
    assert task["instance_lost"] is not None
    assert "reopen_working_failed" in task["instance_lost"]["reason"]
    assert eng._origin_app is None  # 不留下"已连接"的假象


# ---------------------------------------------------------------------------
# P0-2：commit 拒绝覆盖空/退化工程
# ---------------------------------------------------------------------------
def _wire_tmp_task(monkeypatch, tmp_path, pages_before=2):
    monkeypatch.delenv("DSH_ORIGIN_ALLOWED_ROOTS", raising=False)
    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    working = taskdir / "working.opju"
    working.write_bytes(b"OLD")
    target = tmp_path / "figure.opju"
    target.write_bytes(b"ORIGINAL")
    task = _task(working, target=target, pages_before=pages_before)
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    return task, target


def test_commit_refuses_empty_pages_and_keeps_target_untouched(monkeypatch, tmp_path):
    op, _ = install_fake_origin(monkeypatch)
    _, target = _wire_tmp_task(monkeypatch, tmp_path)
    monkeypatch.setattr(eng, "_list_pages_impl",
                        lambda: {"ok": True, "pages": []})
    before = target.stat()
    r = eng._task_commit_impl()
    after = target.stat()
    assert r["error_code"] == "task_commit_refused_empty"
    assert r["force_available"] is True
    assert r["pages_now"] == 0
    assert op.saved == []                       # 不写临时文件
    assert target.read_bytes() == b"ORIGINAL"   # 真 OPJU 未被覆盖
    assert (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)


def test_commit_refuses_degraded_pages(monkeypatch, tmp_path):
    install_fake_origin(monkeypatch)
    _, target = _wire_tmp_task(monkeypatch, tmp_path, pages_before=3)
    monkeypatch.setattr(eng, "_list_pages_impl",
                        lambda: {"ok": True, "pages": [{"name": "Graph1"}]})
    r = eng._task_commit_impl()
    assert r["error_code"] == "task_commit_refused_empty"
    assert r["pages_before"] == 3 and r["pages_now"] == 1
    assert target.read_bytes() == b"ORIGINAL"


def test_commit_allows_degradation_with_explicit_deletions(monkeypatch, tmp_path):
    install_fake_origin(monkeypatch)
    task, target = _wire_tmp_task(monkeypatch, tmp_path, pages_before=3)
    task["removed_pages"] = ["Graph2", "Graph3"]
    monkeypatch.setattr(eng, "_list_pages_impl",
                        lambda: {"ok": True, "pages": [{"name": "Graph1"}]})
    r = eng._task_commit_impl()
    assert r["ok"] is True
    assert r["instance_kept"] is True
    assert r["instance_closed"] is False
    assert target.read_bytes() == b"FAKE-OPJU"
    assert eng._TASK is None


def test_commit_aborts_before_save_when_page_enumeration_fails(monkeypatch, tmp_path):
    op, _ = install_fake_origin(monkeypatch)
    _, target = _wire_tmp_task(monkeypatch, tmp_path)
    monkeypatch.setattr(eng, "_list_pages_impl", lambda: {
        "ok": False, "error_code": "task_instance_lost", "error": "实例丢失",
        "recoverable": True})
    r = eng._task_commit_impl()
    assert r["error_code"] == "task_instance_lost"
    assert r["commit_aborted_before_save"] is True
    assert op.saved == []
    assert target.read_bytes() == b"ORIGINAL"


def test_commit_force_marks_forced_and_overwrites(monkeypatch, tmp_path):
    install_fake_origin(monkeypatch)
    _, target = _wire_tmp_task(monkeypatch, tmp_path)
    monkeypatch.setattr(eng, "_list_pages_impl",
                        lambda: {"ok": True, "pages": []})
    r = eng._task_commit_impl(force=True)
    assert r["ok"] is True
    assert r["forced"] is True
    assert "用户显式要求覆盖" in r["detail"]
    assert target.read_bytes() == b"FAKE-OPJU"


# ---------------------------------------------------------------------------
# P0-3：工作副本快照
# ---------------------------------------------------------------------------
def test_snapshot_working_records_metadata(monkeypatch, tmp_path):
    op, _ = install_fake_origin(monkeypatch)
    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    working = taskdir / "working.opju"
    task = _task(working)
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    monkeypatch.setattr(eng, "_connected", True, raising=False)
    monkeypatch.setattr(eng, "_origin_app", op, raising=False)
    ok, err = eng._snapshot_working("rename")
    assert ok is True and err is None
    assert op.saved == [str(working)]
    assert task["last_snapshot"]["reason"] == "rename"
    assert task["snapshot_count"] == 1
    assert os.path.getsize(working) > 0


def test_snapshot_failure_only_records_warning(monkeypatch, tmp_path):
    op = FakeOp()
    op.save_ok = False
    install_fake_origin(monkeypatch, op=op)
    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    task = _task(taskdir / "working.opju")
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    monkeypatch.setattr(eng, "_connected", True, raising=False)
    monkeypatch.setattr(eng, "_origin_app", op, raising=False)
    monkeypatch.setattr(eng, "_SNAPSHOT_WARNINGS", [])
    ok = eng._snapshot_before_structural("rename")
    assert ok is False
    assert eng._SNAPSHOT_WARNINGS
    assert "快照失败" in eng._SNAPSHOT_WARNINGS[0]


def test_task_begin_initializes_snapshot(monkeypatch, tmp_path):
    _wire_connection_mocks(monkeypatch)
    monkeypatch.delenv("DSH_ORIGIN_ALLOWED_ROOTS", raising=False)
    op, _ = install_fake_origin(monkeypatch)
    monkeypatch.setattr(eng, "_connect_impl", lambda: (True, {"ok": True}))
    monkeypatch.setattr(eng, "_list_pages_impl",
                        lambda: {"ok": True,
                                 "pages": [{"name": "Book1"}, {"name": "Graph1"}]})
    monkeypatch.setattr(eng, "_TASK", None, raising=False)
    target = tmp_path / "out.opju"
    r = eng._task_begin_impl(mode="new", target_path=str(target))
    assert r["ok"] is True
    assert r["pages_count"] == 2
    assert r["snapshot"]["reason"] == "task_begin"
    assert os.path.isfile(r["working_copy"])
    assert eng._TASK["pages_before"] == 2
    assert eng._TASK["snapshot_count"] == 1


def test_task_begin_fails_when_initial_snapshot_fails(monkeypatch, tmp_path):
    _wire_connection_mocks(monkeypatch)
    op = FakeOp(save_ok=False)
    install_fake_origin(monkeypatch, op=op)
    monkeypatch.setattr(eng, "_connect_impl", lambda: (True, {"ok": True}))
    monkeypatch.setattr(eng, "_list_pages_impl", lambda: {"ok": True, "pages": []})
    monkeypatch.setattr(eng, "_TASK", None, raising=False)
    r = eng._task_begin_impl(mode="new", target_path=str(tmp_path / "out.opju"))
    assert r["error_code"] == "task_begin_failed"
    assert eng._TASK is None
    assert list(tmp_path.glob(".dsh-origin-task-*")) == []


def test_track_page_changes_records_explicit_deletions(monkeypatch):
    task = {"removed_pages": []}
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    eng._track_page_changes("close", {"results": [
        {"page": "Graph2", "status": "applied"},
        {"page": "Book3", "status": "rejected"}]})
    eng._track_page_changes("closeall", {"removed": ["Graph4"]})
    assert task["removed_pages"] == ["Graph2", "Graph4"]


# ---------------------------------------------------------------------------
# 观测：origin_status 输出重启/恢复/丢失信息
# ---------------------------------------------------------------------------
def test_status_reports_restart_and_instance_lost(monkeypatch):
    monkeypatch.setattr(eng, "_connect_impl",
                        lambda: (True, {"ok": True, "connected": True}))
    monkeypatch.setattr(eng, "_capabilities_impl", lambda: {"ok": True})
    monkeypatch.setattr(eng, "_CONN_STATS", {
        "restart_count": 3,
        "last_reset_reason": "stale COM proxy: RPC 服务器不可用",
        "last_reset_at": "2026-10-21T13:55:24+0800",
        "last_recovery": {"at": "t", "from": "w.opju", "pages": 2},
    })
    monkeypatch.setattr(eng, "_TASK", {
        "id": "t1", "instance_lost": {"at": "t"},
        "last_snapshot": None, "pages_before": 2,
        "removed_pages": ["Graph2"]}, raising=False)
    info = eng._status_impl()
    assert info["restart_count"] == 3
    assert info["last_reset_reason"].startswith("stale")
    assert info["last_recovery"]["pages"] == 2
    assert info["instance_lost"]["at"] == "t"
    assert info["removed_pages"] == ["Graph2"]


# ---------------------------------------------------------------------------
# 错误码表
# ---------------------------------------------------------------------------
def test_new_task_error_codes_registered():
    for code in ("task_instance_lost", "task_commit_refused_empty"):
        assert code in oerr.CODE_META
        assert code in oerr.RECOVERY_MAP


# ---------------------------------------------------------------------------
# 工具注册：force 参数已暴露在 origin_task_commit 的 schema 里
# ---------------------------------------------------------------------------
def test_task_commit_tool_exposes_force():
    import inspect
    import origin_mcp_server as oms
    sig = inspect.signature(oms.origin_task_commit)
    assert "force" in sig.parameters
    assert sig.parameters["force"].default is False

# ---------------------------------------------------------------------------
# 审查回补（reviewer P0/P1/P2）：pending_move / 快照防污染 / 通配删除 / 接线
# ---------------------------------------------------------------------------
def test_snapshot_refuses_empty_overwrite(monkeypatch, tmp_path):
    op, _ = install_fake_origin(monkeypatch)
    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    task = _task(taskdir / "working.opju")
    task["last_snapshot"] = {"pages": 3}
    op.pages._pages = []
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    monkeypatch.setattr(eng, "_connected", True, raising=False)
    monkeypatch.setattr(eng, "_origin_app", op, raising=False)
    ok, err = eng._snapshot_working("release")
    assert ok is False and "空工程" in err
    assert op.saved == []


def test_snapshot_refuses_when_disconnected(monkeypatch, tmp_path):
    op, _ = install_fake_origin(monkeypatch)
    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    task = _task(taskdir / "working.opju")
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    monkeypatch.setattr(eng, "_connected", False, raising=False)
    monkeypatch.setattr(eng, "_origin_app", None, raising=False)
    ok, err = eng._snapshot_working("release")
    assert ok is False and "未连接" in err
    assert op.saved == []


def test_pending_move_blocks_write_entrypoints(monkeypatch, tmp_path):
    task = _task(tmp_path / "working.opju")
    task["pending_move"] = (str(tmp_path / "t.tmp.opju"), str(tmp_path / "t.opju"))
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    block = eng._pending_move_block("plot")
    assert block and block["error_code"] == "task_commit_failed"
    assert "改动不会被保存" in block["error"]
    # 无 pending 时不拦
    task.pop("pending_move")
    assert eng._pending_move_block("plot") is None


def test_pending_move_blocks_manage_pages_before_side_effect(monkeypatch, tmp_path):
    install_fake_origin(monkeypatch)
    task = _task(tmp_path / "working.opju")
    task["pending_move"] = ("a", "b")
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    monkeypatch.setattr(eng, "_connect_impl", lambda: (True, {"ok": True}))
    called = []

    def fake_manage(*a, **k):
        called.append(1)
        return {"ok": True}

    import origin_edit as oe
    monkeypatch.setattr(oe, "manage_pages", fake_manage)
    r = eng._manage_pages_impl("rename", pages=["Graph1"], new_name="X")
    assert r["error_code"] == "task_commit_failed"
    assert called == []


def test_commit_replace_failure_restores_from_tmp(monkeypatch, tmp_path):
    monkeypatch.delenv("DSH_ORIGIN_ALLOWED_ROOTS", raising=False)
    op, _ = install_fake_origin(monkeypatch)
    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    working = taskdir / "working.opju"
    working.write_bytes(b"OLD")
    target = tmp_path / "figure.opju"
    target.write_bytes(b"ORIGINAL")
    task = _task(working, target=target, pages_before=1)
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    monkeypatch.setattr(eng, "_list_pages_impl",
                        lambda: {"ok": True, "pages": [{"name": "Graph1"}]})

    def boom(src, dst):
        raise OSError("另一个程序正在使用此文件")

    monkeypatch.setattr(eng.os, "replace", boom)
    r = eng._task_commit_impl()
    assert r["error_code"] == "task_commit_failed"
    assert r.get("recovered_from_tmp") is True
    assert "pending_move" not in task
    assert op.opened and op.opened[-1].endswith(".tmp.opju")
    assert target.read_bytes() == b"ORIGINAL"


def test_commit_replace_failure_keeps_pending_when_restore_fails(monkeypatch, tmp_path):
    monkeypatch.delenv("DSH_ORIGIN_ALLOWED_ROOTS", raising=False)
    op = FakeOp(open_ok=False)
    op, _ = install_fake_origin(monkeypatch, op=op)
    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    working = taskdir / "working.opju"
    working.write_bytes(b"OLD")
    target = tmp_path / "figure.opju"
    target.write_bytes(b"ORIGINAL")
    task = _task(working, target=target, pages_before=1)
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    monkeypatch.setattr(eng, "_list_pages_impl",
                        lambda: {"ok": True, "pages": [{"name": "Graph1"}]})

    def boom(src, dst):
        raise OSError("另一个程序正在使用此文件")

    monkeypatch.setattr(eng.os, "replace", boom)
    r = eng._task_commit_impl()
    assert r["error_code"] == "task_commit_failed"
    assert task.get("pending_move") is not None
    assert target.read_bytes() == b"ORIGINAL"


def test_manage_pages_close_wildcard_expands_and_tracks():
    pages = [FakePage("Book1", 2), FakePage("Book2", 2), FakePage("Graph1", 3)]
    op = FakeOp(pages)

    def fake_lt(script):
        op.lt_calls.append(script)
        if script.startswith("window -c "):
            name = script.split()[2].rstrip(";")
            op.pages._pages = [p for p in op.pages._pages
                               if p.GetName() != name]

    op.po.LT_execute = fake_lt
    r = oedit.manage_pages(op, op.po, "close", ["Book*"])
    assert r["ok"] is True
    assert r["n_applied"] == 2
    assert [x["page"] for x in r["results"]] == ["Book1", "Book2"]
    assert [x["status"] for x in r["results"]] == ["applied", "applied"]


def test_manage_pages_close_wildcard_no_match_reports_rejected():
    op = FakeOp([FakePage("Graph1", 3)])
    r = oedit.manage_pages(op, op.po, "close", ["Book*"])
    assert r["results"][0]["status"] == "rejected"
    assert "通配" in r["results"][0]["reason"]


def test_plot_success_snapshots_after_creation(monkeypatch, tmp_path):
    op, _ = install_fake_origin(monkeypatch)
    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    task = _task(taskdir / "working.opju")
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    monkeypatch.setattr(eng, "_connected", True, raising=False)
    monkeypatch.setattr(eng, "_origin_app", op, raising=False)
    r = eng._plot_success({"ok": True, "graph": "Graph1"})
    assert r["graph"] == "Graph1"
    assert task["last_snapshot"]["reason"] == "plot:created"
    assert task["last_snapshot"]["pages"] == 0


def test_connect_settles_after_instance_exit(monkeypatch):
    _wire_connection_mocks(monkeypatch)
    install_fake_origin(monkeypatch)
    sleeps = []
    monkeypatch.setattr(eng.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(eng, "_LAST_INSTANCE_EXIT_AT", eng.time.time(),
                        raising=False)
    monkeypatch.setattr(eng, "_TASK", None, raising=False)
    monkeypatch.setattr(eng, "_connected", False, raising=False)
    monkeypatch.setattr(eng, "_origin_app", None, raising=False)
    ok, info = eng._connect_impl()
    assert ok is True
    assert any(s >= 1.0 for s in sleeps)


def test_synchronized_bubbles_warning_and_recovery(monkeypatch):
    monkeypatch.setattr(eng, "_SNAPSHOT_WARNINGS", [])
    monkeypatch.setattr(eng, "_PENDING_RECOVERY", None)

    @eng._synchronized
    def probe():
        eng._SNAPSHOT_WARNINGS.append("快照失败X")
        eng._PENDING_RECOVERY = {"from": "w.opju", "pages": 2}
        return {"ok": True}

    r = probe()
    assert r["recovered"] is True
    assert r["recovered_from"] == "w.opju"
    assert r["recovered_pages"] == 2
    assert "快照失败X" in r["warning"]

    @eng._synchronized
    def probe2():
        return {"ok": True}

    r2 = probe2()
    assert "recovered" not in r2 and "warning" not in r2


def test_as_bool_parses_strings_strictly():
    import origin_mcp_server as oms
    assert oms._as_bool("false") is False
    assert oms._as_bool("0") is False
    assert oms._as_bool("true") is True
    assert oms._as_bool("1") is True
    assert oms._as_bool(0) is False
    assert oms._as_bool("") is False

# ---------------------------------------------------------------------------
# 实例生命周期：默认复用（避免 2024b Exit(False) 退出崩溃）
# ---------------------------------------------------------------------------
def test_exit_instance_flag_maps_to_releaseonly(monkeypatch):
    op, cfg = install_fake_origin(monkeypatch)
    monkeypatch.setattr(eng, "_origin_app", op, raising=False)
    monkeypatch.setattr(eng, "_connected", True, raising=False)
    monkeypatch.setattr(eng, "_LAST_INSTANCE_EXIT_AT", None, raising=False)
    eng._exit_exclusive_instance(close=False)
    assert cfg.exit_calls[-1] == (True,)    # Exit(bReleaseOnly=True)：不退出
    eng._exit_exclusive_instance(close=True)
    assert cfg.exit_calls[-1] == (False,)   # 真正退出


def test_abort_keeps_connection_and_does_not_exit(monkeypatch, tmp_path):
    op, cfg = install_fake_origin(monkeypatch)
    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    task = _task(taskdir / "working.opju")
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    monkeypatch.setattr(eng, "_connected", True, raising=False)
    monkeypatch.setattr(eng, "_origin_app", op, raising=False)
    r = eng._task_abort_impl()
    assert r["ok"] is True
    assert eng._TASK is None
    assert eng._connected is True          # 连接保留，下次复用
    assert op.new_calls >= 1               # 清空工程
    assert cfg.exit_calls == []            # 不退出 Origin


def test_closeall_warns_about_instance_rebuild(monkeypatch):
    op, _ = install_fake_origin(monkeypatch)
    monkeypatch.setattr(eng, "_connect_impl", lambda: (True, {"ok": True}))
    monkeypatch.setattr(eng, "_origin_app", op, raising=False)
    monkeypatch.setattr(eng, "_snapshot_before_structural", lambda *a, **k: True)
    import origin_edit as oe
    monkeypatch.setattr(oe, "manage_pages",
                        lambda *a, **k: {"ok": True, "removed": ["Book1"],
                                         "n_removed": 1})
    r = eng._manage_pages_impl("closeAll")
    assert r["ok"] is True, r
    assert "重建实例" in r["warning"]

# ---------------------------------------------------------------------------
# 第二轮审查回补：写工具统一拦截 / close_instance 解析 / op.new 失败
# ---------------------------------------------------------------------------
def test_synchronized_blocks_all_write_tools_in_pending(monkeypatch, tmp_path):
    task = _task(tmp_path / "working.opju")
    task["pending_move"] = (str(tmp_path / "t.tmp.opju"), str(tmp_path / "t.opju"))
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    for name in ("plot", "load_file", "save_project", "matrix_write",
                 "import_matplotlib", "fit", "manage_pages"):
        fn = getattr(eng, name)
        r = fn("dummy") if name not in ("save_project",) else fn(str(tmp_path / "x.opju"))
        assert r.get("error_code") == "task_commit_failed", (name, r)
        assert "pending" in (r.get("error") or "") or "改动不会被保存" in (r.get("error") or "")


def test_synchronized_allows_read_tools_in_pending(monkeypatch, tmp_path):
    task = _task(tmp_path / "working.opju")
    task["pending_move"] = (str(tmp_path / "t.tmp.opju"), str(tmp_path / "t.opju"))
    monkeypatch.setattr(eng, "_TASK", task, raising=False)

    @eng._synchronized
    def read_only_tool():
        return {"ok": True}

    r = read_only_tool()
    assert r["ok"] is True


def test_commit_string_false_close_instance_does_not_exit(monkeypatch, tmp_path):
    monkeypatch.delenv("DSH_ORIGIN_ALLOWED_ROOTS", raising=False)
    op, cfg = install_fake_origin(monkeypatch)
    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    working = taskdir / "working.opju"
    working.write_bytes(b"OLD")
    target = tmp_path / "figure.opju"
    target.write_bytes(b"ORIGINAL")
    task = _task(working, target=target, pages_before=1)
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    monkeypatch.setattr(eng, "_list_pages_impl",
                        lambda: {"ok": True, "pages": [{"name": "Graph1"}]})
    r = eng._task_commit_impl(close_instance="false")
    assert r["ok"] is True
    assert r["instance_kept"] is True
    assert cfg.exit_calls == []          # 字符串 false 不得触发真退出


def test_commit_op_new_failure_keeps_pending_and_target(monkeypatch, tmp_path):
    monkeypatch.delenv("DSH_ORIGIN_ALLOWED_ROOTS", raising=False)
    op = FakeOp(new_fail=True)
    install_fake_origin(monkeypatch, op=op)
    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    working = taskdir / "working.opju"
    working.write_bytes(b"OLD")
    target = tmp_path / "figure.opju"
    target.write_bytes(b"ORIGINAL")
    task = _task(working, target=target, pages_before=1)
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    monkeypatch.setattr(eng, "_list_pages_impl",
                        lambda: {"ok": True, "pages": [{"name": "Graph1"}]})
    r = eng._task_commit_impl()
    assert r["error_code"] == "task_commit_failed"
    assert r.get("pending_move") is True
    assert task.get("pending_move") is not None
    assert target.read_bytes() == b"ORIGINAL"


def test_abort_removes_pending_tmp(monkeypatch, tmp_path):
    op, _ = install_fake_origin(monkeypatch)
    taskdir = tmp_path / "taskdir"
    taskdir.mkdir()
    tmp_out = tmp_path / "t.tmp.opju"
    tmp_out.write_bytes(b"TMP")
    task = _task(taskdir / "working.opju")
    task["pending_move"] = (str(tmp_out), str(tmp_path / "t.opju"))
    monkeypatch.setattr(eng, "_TASK", task, raising=False)
    monkeypatch.setattr(eng, "_connected", True, raising=False)
    monkeypatch.setattr(eng, "_origin_app", op, raising=False)
    r = eng._task_abort_impl()
    assert r["ok"] is True
    assert not tmp_out.exists()


def test_close_wildcard_is_case_insensitive():
    pages = [FakePage("Book1", 2), FakePage("Book2", 2), FakePage("Graph1", 3)]
    op = FakeOp(pages)

    def fake_lt(script):
        op.lt_calls.append(script)
        if script.startswith("window -c "):
            name = script.split()[2].rstrip(";")
            op.pages._pages = [p for p in op.pages._pages
                               if p.GetName() != name]

    op.po.LT_execute = fake_lt
    r = oedit.manage_pages(op, op.po, "close", ["book*"])
    assert r["n_applied"] == 2

def test_write_tool_names_all_are_synchronized_functions():
    """写名单必须全部对应真实的 @_synchronized 函数，防止死名漏拦（P0-1）。"""
    for name in eng._WRITE_TOOL_NAMES:
        fn = getattr(eng, name, None)
        assert fn is not None, "写名单里有死名（无对应函数）: %s" % name
        assert getattr(fn, "_dsh_synchronized", False), \
            "写名单项未被 _synchronized 包装: %s" % name


def test_plot_template_task_guarded_and_plot_template_not_dead_name():
    assert "_plot_template_task" in eng._WRITE_TOOL_NAMES
    assert "plot_template" not in eng._WRITE_TOOL_NAMES
