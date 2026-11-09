"""Market input dialog using the toolbox's existing themed Tk widgets."""
from __future__ import annotations

from datetime import timedelta
import tkinter as tk
from tkinter import messagebox, ttk


def market_module():
    from .runtime import load_module
    from pathlib import Path
    return load_module(
        "toolbox_market_manual", Path(__file__).resolve().parents[2]
        / "南方分公司电力市场日报/gd_power_report/manual_market.py",
    )


class MarketEditor(tk.Toplevel):
    def __init__(self, parent, report_date, initial, on_save):
        super().__init__(parent)
        self.report_date = report_date
        self.on_save = on_save
        self.kernel = market_module()
        self.values = {}
        self.deltas = {}
        self._refresh_id = None
        self._fit_id = None
        self._market_views = []
        self.title("日前及实时市场补录")
        self.geometry("880x650")
        self.minsize(800, 610)
        self.transient(parent.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self.close)
        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        ttk.Label(body, text="市场情况补录", style="PageTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text="网站有效值优先，手填补齐缺项；增减自动计算。", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 14))
        tabs = ttk.Notebook(body)
        tabs.grid(row=2, column=0, sticky="nsew")
        canvases = {}
        for period, name in (("day_ahead", "日前市场"), ("realtime", "实时市场")):
            viewport = ttk.Frame(tabs)
            viewport.rowconfigure(0, weight=1)
            viewport.columnconfigure(0, weight=1)
            tabs.add(viewport, text=name)
            canvas = tk.Canvas(viewport, highlightthickness=0, borderwidth=0,
                               background=ttk.Style(self).lookup("TFrame", "background"),
                               width=800, height=500)
            canvas.grid(row=0, column=0, sticky="nsew")
            vertical = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
            vertical.grid(row=0, column=1, sticky="ns")
            horizontal = ttk.Scrollbar(viewport, orient="horizontal", command=canvas.xview)
            horizontal.grid(row=1, column=0, sticky="ew")
            canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            page = ttk.Frame(canvas, padding=14)
            item = canvas.create_window(0, 0, window=page, anchor="nw")
            page.bind("<Configure>", lambda _event, c=canvas: c.configure(scrollregion=c.bbox("all")))
            canvas.bind("<Configure>", lambda event, c=canvas, p=page, i=item:
                        c.itemconfigure(i, width=max(event.width, p.winfo_reqwidth())))
            canvases[str(viewport)] = canvas
            self._market_views.append((canvas, page))
            current = report_date if period == "day_ahead" else report_date - timedelta(days=2)
            baseline = report_date - timedelta(days=1) if period == "day_ahead" else current
            ttk.Label(page, text=f"当前：{current.isoformat()} {'日前' if period == 'day_ahead' else '实时'}    对比：{baseline.isoformat()} 日前", style="Muted.TLabel").grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 14))
            for col, title in enumerate(("指标", "单位", "当前值", "对比值", "增减（自动）")):
                ttk.Label(page, text=title).grid(row=1, column=col, sticky="w", padx=(0, 12), pady=(0, 8))
            page.columnconfigure(0, weight=1)
            self.values[period] = {}
            self.deltas[period] = {}
            for row, field in enumerate(self.kernel.MARKET_FIELDS, 2):
                ttk.Label(page, text=field.name).grid(row=row, column=0, sticky="w", pady=7)
                ttk.Label(page, text=field.unit, style="Muted.TLabel").grid(row=row, column=1, sticky="w", padx=(0, 12))
                pair = {}
                for col, key in ((2, "value"), (3, "comparison")):
                    var = tk.StringVar(self, value=initial.get(period, {}).get(field.key, {}).get(key, ""))
                    pair[key] = var
                    ttk.Entry(page, textvariable=var, width=12, justify="right").grid(row=row, column=col, padx=(0, 12))
                    var.trace_add("write", self.schedule_refresh)
                self.values[period][field.key] = pair
                delta = tk.StringVar(self, value="—")
                self.deltas[period][field.key] = delta
                ttk.Label(page, textvariable=delta, width=13, anchor="e").grid(row=row, column=4, sticky="e")
            ttk.Button(page, text="清空本页", command=lambda p=period: self.clear(p)).grid(row=11, column=0, sticky="w", pady=(14, 0))
        self.status = tk.StringVar(self, value="0 是有效值；煤机、气机填非负整数，均价允许负数。")
        ttk.Label(body, textvariable=self.status, style="Muted.TLabel", wraplength=800).grid(row=3, column=0, sticky="w", pady=(12, 8))
        actions = ttk.Frame(body)
        actions.grid(row=4, column=0, sticky="ew")
        ttk.Button(actions, text="保存并返回", command=self.save).pack(side="right")
        ttk.Button(actions, text="取消", command=self.close).pack(side="right", padx=8)
        def scroll(event):
            if event.num in (4, 5):
                units = -1 if event.num == 4 else 1
            else:
                units = -int(event.delta / 120) if abs(event.delta) >= 120 else -int(event.delta)
            canvases[tabs.select()].yview_scroll(units, "units")
            return "break"
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind(sequence, scroll)
        # Point-sized fonts grow with display scaling; fixed pixel geometry
        # otherwise clips the last rows and the save/cancel controls.
        self._fit_to_content()
        self._fit_id = self.after_idle(self._fit_to_content)
        self.refresh()
        self.grab_set()

    def _fit_to_content(self):
        self._fit_id = None
        self.update_idletasks()
        for canvas, page in self._market_views:
            canvas.configure(width=page.winfo_reqwidth(), height=page.winfo_reqheight())
        self.update_idletasks()
        width = min(max(880, self.winfo_reqwidth()), self.winfo_screenwidth() - 80)
        height = min(max(650, self.winfo_reqheight()), self.winfo_screenheight() - 100)
        self.geometry(f"{width}x{height}")
        self.minsize(min(800, width), min(610, height))

    def snapshot(self):
        return {"report_date": self.report_date.isoformat(), **{
            period: {key: {field: var.get() for field, var in pair.items()} for key, pair in fields.items()}
            for period, fields in self.values.items()
        }}

    def schedule_refresh(self, *_):
        if self._refresh_id is not None:
            self.after_cancel(self._refresh_id)
        self._refresh_id = self.after(180, self.refresh)

    def refresh(self):
        if self._refresh_id is not None:
            self.after_cancel(self._refresh_id)
        self._refresh_id = None
        try:
            result = self.kernel.preview_manual_market(self.snapshot(), self.report_date)
            for period, fields in self.deltas.items():
                for key, var in fields.items():
                    delta = result["deltas"][period][key]
                    var.set("—" if delta is None else f"{delta:+g}" if delta else "0")
            self.status.set("0 是有效值；煤机、气机填非负整数，均价允许负数。切换报告日期会清空补录。")
        except (ValueError, RuntimeError) as exc:
            for fields in self.deltas.values():
                for var in fields.values():
                    var.set("—")
            self.status.set(str(exc))

    def clear(self, period):
        for pair in self.values[period].values():
            for var in pair.values():
                var.set("")

    def save(self):
        try:
            data = self.kernel.normalize_manual_market(self.snapshot(), self.report_date)
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("输入错误", str(exc), parent=self)
            return
        self.on_save(data)
        self.close()

    def close(self):
        self.destroy()

    def destroy(self):
        if self._fit_id is not None:
            self.after_cancel(self._fit_id)
            self._fit_id = None
        if self._refresh_id is not None:
            self.after_cancel(self._refresh_id)
            self._refresh_id = None
        super().destroy()
