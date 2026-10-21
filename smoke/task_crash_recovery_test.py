# -*- coding: utf-8 -*-
"""真机验收：2026-10-21 任务工程丢失事故的 P0/P1 回归（exclusive 专用实例）。

验收点（对应修复要求）：
1. rename 走 COM SetName：成功、不崩、返回 renamed_to；结构操作前有快照。
2. 正常 commit 落盘；随后清空工程再 commit 必须被 task_commit_refused_empty
   拒绝，目标 OPJU 的 mtime/size 保持不变（P0-2）。
3. 强制杀掉本测试启动的 Origin 专用实例（只杀 _TEST_STARTED_ORIGIN_PIDS 里
   的 PID，绝不碰用户实例），下一次调用必须自动 op.open(working) 恢复出原图页
   （P0-1 + P0-3）。

运行（必须用插件 venv）：
    C:/Users/wzxle/dsh_origin_plugin/.venv/Scripts/python.exe -X utf8 \
        smoke/task_crash_recovery_test.py

注意：脚本会启动自己的 COMSI 专用实例；退出时 atexit 只清理它自己启动的
Origin 进程。不会写 D:\\DSH\\WorkZone\\test。
"""
import json
import os
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ["ORIGIN_SESSION"] = "exclusive"
os.environ.setdefault("DSH_ORIGIN_AUTOKILL", "0")

import origin_engine as eng   # noqa: E402


def check(label, cond, detail=None):
    status = "PASS" if cond else "FAIL"
    extra = ""
    if detail is not None:
        extra = " :: " + json.dumps(detail, ensure_ascii=False, default=str)[:500]
    print("[%s] %s%s" % (status, label, extra))
    if not cond:
        raise AssertionError(label)


def main():
    work = tempfile.mkdtemp(prefix="dsh-origin-crash-test-")
    target = os.path.join(work, "out.opju")
    print("workdir:", work)

    # --- 1) begin + 建图 + rename（COM 通道；事故命令回归） ---
    r = eng.task_begin(mode="new", target_path=target)
    check("task_begin", r.get("ok") is True, r)
    check("begin 已初始化工作副本快照", bool(r.get("snapshot")), r.get("snapshot"))

    r = eng.write_data({"x": [1, 2, 3, 4], "y": [1, 4, 9, 16]})
    check("write_data", r.get("ok") is True, r)
    wks = r.get("worksheet")
    r = eng.plot(wks, plot_type="line")
    check("plot", r.get("ok") is True, r)
    graph = r.get("graph")
    check("plot 返回图短名", bool(graph), r)

    r = eng.manage_pages("rename", pages=[graph], new_name="ExScan_4tC2CN")
    res = (r.get("results") or [{}])[0]
    check("rename(status=applied)", res.get("status") == "applied", r)
    check("rename(renamed_to 精确匹配)", res.get("renamed_to") == "ExScan_4tC2CN", res)

    st = eng.task_status()
    snap = st.get("last_snapshot") or {}
    check("rename 前已保存工作副本快照", bool(snap), st)
    check("快照 reason 指向 manage_pages",
          str(snap.get("reason", "")).startswith("manage_pages"), snap)

    status = eng.status()
    check("rename 后实例仍存活", status.get("connected") is True, status)

    # --- 2) 正常 commit ---
    r = eng.task_commit()
    check("正常 commit 落盘", r.get("ok") is True, r)
    check("目标 OPJU 非空", os.path.getsize(target) > 0, {"size": os.path.getsize(target)})
    size0, mtime0 = os.path.getsize(target), os.path.getmtime(target)

    # --- 3) 空工程 commit 必须拒绝覆盖（P0-2） ---
    r = eng.task_begin(mode="new", target_path=target)
    check("begin(第二次)", r.get("ok") is True, r)
    r = eng.manage_pages("closeAll")
    check("closeAll", r.get("ok") is True, r)
    r = eng.task_commit()
    check("空工程 commit 被拒绝", r.get("error_code") == "task_commit_refused_empty", r)
    check("拒绝后目标 size 不变", os.path.getsize(target) == size0,
          {"before": size0, "after": os.path.getsize(target)})
    check("拒绝后目标 mtime 不变", os.path.getmtime(target) == mtime0,
          {"before": mtime0, "after": os.path.getmtime(target)})
    r = eng.task_abort()
    check("abort 清理任务", r.get("ok") is True, r)

    # --- 4) 杀专用实例 -> 下次调用自动恢复（P0-1 + P0-3） ---
    crash_target = os.path.join(work, "crash.opju")
    r = eng.task_begin(mode="new", target_path=crash_target)
    check("begin(崩溃演练)", r.get("ok") is True, r)
    r = eng.write_data({"x": [1, 2, 3, 4], "y": [2, 4, 6, 8]})
    check("write_data(崩溃演练)", r.get("ok") is True, r)
    r = eng.plot(r.get("worksheet"), plot_type="line")
    check("plot(崩溃演练)", r.get("ok") is True, r)
    graph3 = r.get("graph")
    st = eng.task_status()
    pages_before = st.get("pages") or []
    check("plot 后已刷新工作副本快照",
          (st.get("last_snapshot") or {}).get("reason") == "plot:created",
          st.get("last_snapshot"))
    check("崩溃前图页存在", graph3 in pages_before, pages_before)

    # rename 会再触发一次"结构操作前"快照（rename 前的短名进快照）
    r = eng.manage_pages("rename", pages=[graph3], new_name="CrashBeforeKill")
    check("崩溃演练 rename", (r.get("results") or [{}])[0].get("status") == "applied", r)
    st = eng.task_status()
    check("rename 前快照 reason 正确",
          (st.get("last_snapshot") or {}).get("reason") == "manage_pages:rename",
          st.get("last_snapshot"))

    live = eng._origin_proc_pids() & set(eng._TEST_STARTED_ORIGIN_PIDS)
    check("能定位本测试启动的专用实例 PID", bool(live), sorted(live))
    for pid in sorted(live):
        os.popen("taskkill /F /PID %d /T >nul 2>&1" % pid).read()
    time.sleep(2.0)
    check("专用实例已退出", not (eng._origin_proc_pids() & live), sorted(live))

    r = eng.task_status()          # 下一次调用：应自动重建实例 + op.open(working)
    check("实例死亡后自动恢复工作副本", r.get("recovered") is True, r)
    pages_after = r.get("pages") or []
    check("恢复出崩溃前的图页（rename 前短名）", graph3 in pages_after,
          {"expect": graph3, "pages": pages_after})
    check("恢复的是 rename 前快照（不含 rename 后名字）",
          "CrashBeforeKill" not in pages_after,
          {"pages": pages_after})
    check("恢复后任务仍可继续", r.get("ok") is True and r.get("active") is True, r)

    r = eng.task_abort()
    check("abort(崩溃演练)", r.get("ok") is True, r)

    print("ALL PASS")
    print("workdir:", work)


if __name__ == "__main__":
    main()
