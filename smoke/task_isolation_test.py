# -*- coding: utf-8 -*-
"""任务隔离冒烟（ORIGIN_SESSION=exclusive）——需要本机 Origin。

流程：COMSI 专用实例 → new 任务 → 写数/画图 → commit（原子落盘 + 关实例）
      → open 任务（读回刚落的 OPJU）→ abort（关实例、删临时工程）。

不会触碰用户手动打开的 Origin 窗口（COMSI 专用实例）。
运行：python smoke/task_isolation_test.py
"""
import json
import os
import sys
import tempfile
import time

os.environ["ORIGIN_SESSION"] = "exclusive"
os.environ["DSH_ORIGIN_AUTOKILL"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import origin_engine as engine  # noqa: E402

TARGET = os.path.join(tempfile.gettempdir(), "dsh_task_isolation_smoke.opju")
CRASH_TARGET = os.path.join(tempfile.gettempdir(), "dsh_task_crash_smoke.opju")


def _clean():
    try:
        if engine._TASK is not None:
            engine.task_abort()
    except Exception:
        pass
    try:
        engine.shutdown()
    except Exception:
        pass
    for p in (TARGET, CRASH_TARGET):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


def main():
    if not engine._exclusive_mode():
        print("RESULT: FAIL（未进入 exclusive 模式）")
        return 1
    _clean()
    failures = []

    def check(name, cond, detail=""):
        print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else " -> " + str(detail)))
        if not cond:
            failures.append(name)

    try:
        begin = engine.task_begin(mode="new", target_path=TARGET)
        check("task_begin(new) ok", begin.get("ok"), begin)
        check("begin pages readable", isinstance(begin.get("pages"), list))

        w = engine.write_data({"x": [1, 2, 3, 4], "y": [1, 4, 9, 16]})
        check("write_data ok", w.get("ok"), w)
        gp = engine.plot(w["worksheet"], x_column="x", y_columns=["y"],
                         plot_type="line")
        check("plot ok", gp.get("ok"), gp)

        # 回归：apply_style 未传 columns 时不得用 S1 覆盖标题，也不得谎报 applied
        engine.edit_axis(gp["graph"], axis="y", title="Intensity (counts)")
        style = engine.apply_style(gp["graph"], style_mode="journal")
        check("apply_style ok", style.get("ok"), style)
        check("apply_style does not fake y.title",
              not any("y.title" in op for op in (style.get("applied_ops") or [])),
              style.get("applied_ops"))
        inspect = engine.inspect_graph(gp["graph"])
        y_title = None
        try:
            y_title = inspect["layers"][0]["axes"]["y"]["title"]
        except Exception:
            pass
        check("existing y title preserved", y_title == "Intensity (counts)", y_title)

        # 回归：verify 的字号检查应叫 label（刻度标签），不再冒称 title
        ver = engine.verify_graph(gp["graph"], expected_y_title="Intensity (counts)")
        names = [c.get("name") for c in (ver.get("checks") or [])]
        check("verify uses label font check", "y_label_font_pt" in names, names[-8:])
        check("verify does not claim title font", "y_title_font_pt" not in names, names[-8:])

        commit = engine.task_commit()
        check("task_commit ok", commit.get("ok"), commit)
        check("target OPJU written", os.path.isfile(TARGET) and os.path.getsize(TARGET) > 0,
              TARGET)
        check("commit keeps instance by default",
              commit.get("instance_kept") is True and commit.get("instance_closed") is False,
              commit)
        check("commit reports pages", (commit.get("pages_count") or 0) >= 2,
              commit.get("pages"))

        opened = engine.task_begin(mode="open", target_path=TARGET)
        check("task_begin(open) ok", opened.get("ok"), opened)
        check("opened pages match", (opened.get("pages_count") or 0) >= 2,
              opened.get("pages"))
        aborted = engine.task_abort()
        check("task_abort ok", aborted.get("ok") and aborted.get("aborted"),
              aborted)

        # ---- P0-1/P0-3 回归：建图 → 快照 → kill 专用实例 → 自动恢复 → commit ----
        try:
            if os.path.exists(CRASH_TARGET):
                os.remove(CRASH_TARGET)
        except Exception:
            pass
        b2 = engine.task_begin(mode="new", target_path=CRASH_TARGET)
        check("crash drill begin ok", b2.get("ok"), b2)
        w2 = engine.write_data({"x": [1, 2, 3], "y": [2, 4, 6]})
        check("crash drill write ok", w2.get("ok"), w2)
        gp2 = engine.plot(w2["worksheet"], plot_type="line")
        check("crash drill plot ok", gp2.get("ok"), gp2)
        graph2 = gp2.get("graph")
        st = engine.task_status()
        check("crash drill snapshot exists", bool(st.get("snapshot")),
              st.get("snapshot"))
        live = engine._origin_proc_pids() & set(engine._TEST_STARTED_ORIGIN_PIDS)
        check("crash drill locate test instance", bool(live), sorted(live))
        for pid in sorted(live):
            os.popen("taskkill /F /PID %d /T >nul 2>&1" % pid).read()
        time.sleep(2.0)
        st2 = engine.task_status()          # 下一次调用应自动重建 + op.open(working)
        check("crash drill instance recovered",
              st2.get("instance_state") == "recovered",
              st2.get("instance_state"))
        check("crash drill pages restored", graph2 in (st2.get("pages") or []),
              {"expect": graph2, "pages": st2.get("pages")})
        c2 = engine.task_commit()
        check("crash drill commit ok", c2.get("ok"), c2)
        check("crash drill target written",
              os.path.isfile(CRASH_TARGET) and os.path.getsize(CRASH_TARGET) > 0,
              CRASH_TARGET)
    finally:
        _clean()

    print("RESULT: " + ("OK" if not failures else "FAIL"))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())