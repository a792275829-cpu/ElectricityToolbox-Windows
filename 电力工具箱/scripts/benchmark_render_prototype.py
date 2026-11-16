"""Compare the scoped prototype without reading credentials or running tools.

Run baseline/prototype in separate processes and compare matching screenshots.
Measurements include Tk event processing and drawing submission, not the time
at which the compositor presents the final pixels on the screen.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from toolbox.app import ToolboxApp


def distribution(values):
    ordered = sorted(values)
    return {"median_ms": round(statistics.median(values), 3),
            "p95_ms": round(ordered[int((len(ordered) - 1) * .95)], 3)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "prototype"), required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--hover-interval-ms", type=int, default=35)
    args = parser.parse_args()
    with patch("toolbox.ui.credentials_panel.CredentialStore.load", return_value={}):
        app = ToolboxApp(render_prototype=args.mode == "prototype")
    app.geometry("1440x820+0+40")
    app.attributes("-topmost", True)
    app.lift()
    app.update()
    app.update()
    start = time.perf_counter()
    app.show_page("出清结果汇总上传")
    app.update()
    first = (time.perf_counter() - start) * 1000
    page = app.pages["出清结果汇总上传"]
    page.input_dir.set("/示例/复盘/2026-10-09")
    page.output_file.set("/示例/复盘/2026-10-09/2026-10-09_汇总.xlsx")
    app.diagnostic_center.summary_var.set("检查于 09:00")
    app.update()
    if app.surface_cache:
        app.surface_cache.flush()
        app.update()

    summary_switch, overview_switch = [], []
    for _ in range(60):
        for name, samples in (("今日概览", overview_switch), ("出清结果汇总上传", summary_switch)):
            start = time.perf_counter()
            app.show_page(name)
            app.update()
            samples.append((time.perf_counter() - start) * 1000)

    hover = []
    buttons = list(app.nav_buttons.values())
    for index in range(120):
        start = time.perf_counter()
        buttons[(index - 1) % len(buttons)].event_generate("<Leave>")
        buttons[index % len(buttons)].event_generate("<Enter>", x=20, y=10)
        app.update()
        hover.append((time.perf_counter() - start) * 1000)

    lag = []
    last = [time.perf_counter()]
    callbacks = []
    moving = [0]

    def heartbeat():
        now = time.perf_counter()
        lag.append(max(0, (now - last[0]) * 1000 - 5))
        last[0] = now
        callbacks.append(app.after(5, heartbeat))

    def move():
        index = moving[0]
        buttons[(index - 1) % len(buttons)].event_generate("<Leave>")
        buttons[index % len(buttons)].event_generate("<Enter>", x=20, y=10)
        moving[0] += 1
        callbacks.append(app.after(args.hover_interval_ms, move))

    callbacks.append(app.after(5, heartbeat))
    callbacks.append(app.after(args.hover_interval_ms, move))
    app.after(2000, app.quit)
    cpu_start = time.process_time()
    app.mainloop()
    cpu = (time.process_time() - cpu_start) * 1000
    for callback in callbacks:
        app.after_cancel(callback)
    result = {"mode": args.mode, "first_summary_ms": round(first, 3),
              "summary_switch": distribution(summary_switch),
              "overview_switch": distribution(overview_switch),
              "hover_draw": distribution(hover), "hover_event_lag": distribution(lag),
              "hover_2s_cpu_ms": round(cpu, 3), "hover_interval_ms": args.hover_interval_ms}
    if app.surface_cache:
        result["cache"] = {"entries": len(app.surface_cache.entries),
                           "pixels": app.surface_cache.pixel_count,
                           "builds": app.surface_cache.build_count}

    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
    if args.output:
        (args.output / f"{args.mode}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    app.destroy()


if __name__ == "__main__":
    main()
