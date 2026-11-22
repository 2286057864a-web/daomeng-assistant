"""gui.py —— 到梦空间助手（图形界面）。

双击 `启动.bat`，或者命令行 `python gui.py`。

界面流程：
    1. 点「扫描活动」→ 中间列表出现「名称 + 状态」
    2. 选中一条活动，右边按钮才可用：
         补签  → 先「打开未签到」看名单，再「按年级勾选」，最后「提交补签」
         录取  → 选名单 xlsx，点「演练」或「正式录取」
         学分  → 选名单 xlsx，点「演练」或「正式发放」
    3. 下方日志实时显示每一步；默认全部是演练，只有勾了「正式」并要求确认才真的提交。
"""

from __future__ import annotations

import io
import queue
import os
import sys
import threading
import traceback
import contextlib
import webbrowser
from pathlib import Path
from collections import Counter
from datetime import date
import re

import bootstrap

bootstrap.setup()

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter.scrolledtext import ScrolledText

import guard
import scan
import roster_workflow
import roster_device
import device_checks
import errcatch
import work_report

roster_workflow.BACKEND_FACTORY = roster_device.create_backend
from mark_export import load_want, load_got
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

SHOW_COMPARE = False  # 按用户要求隐藏「比对」页签（代码保留）
PROJECT_URL = "https://github.com/2286057864a-web/daomeng-assistant"

ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
def grade_choices(year=None):
    year = date.today().year if year is None else year
    return ["全部"] + [f"{value}级" for value in range(year + 1, year - 4, -1)]


GRADES = grade_choices()


def write_comparison_report(want_path, got_path, output_path):
    """复用现有名单解析，生成本地比对结果；不操作手机。"""
    output = Path(output_path).resolve()
    tables = []
    for label, source, loader in (("母表", want_path, load_want), ("导出表", got_path, load_got)):
        source = Path(source).resolve()
        if source == output:
            raise ValueError("结果路径不能与任一输入文件相同，请重新选择名单。")
        if source.suffix.lower() != ".xlsx":
            raise ValueError(f"{label}请选择 .xlsx 文件。")
        if not source.is_file():
            raise ValueError(f"{label}文件不存在，请重新选择：{source}")
        try:
            with source.open("rb") as stream:
                rows = loader(stream)
        except Exception as exc:
            raise ValueError(f"{label}读取失败，请检查文件格式、表头和读取权限：{exc}") from exc
        if not rows:
            raise ValueError(f"{label}没有有效人员，请检查表头及名单内容。")
        duplicates = [name for name, count in Counter(r["name"] for r in rows).items() if count > 1]
        if duplicates:
            raise ValueError(f"{label}存在重名：{'、'.join(duplicates)}。请先处理歧义后再比对。")
        tables.append(rows)
    want, got = tables
    by_name = {person["name"]: person for person in got}
    want_names = {person["name"] for person in want}
    extra = [person for person in got if person["name"] not in want_names]
    hit = sum(person["name"] in by_name for person in want)
    counts = {"母表总人数": len(want), "命中人数": hit, "未命中人数": len(want) - hit,
              "导出表行数": len(got), "导出表里但母表没有的人数": len(extra)}

    wb = Workbook()
    marked = wb.active
    marked.title = "标记结果"
    marked.append(["序号", "代表团", "姓名", "身份", "出现在导出表", "学号", "班级", "录取状态"])
    green = PatternFill("solid", fgColor="D9F2D9")
    red = PatternFill("solid", fgColor="FDE2E2")
    for person in want:
        match = by_name.get(person["name"])
        marked.append([person["seq"], person["team"], person["name"], person["role"],
                       "是" if match else "否", match["sid"] if match else "",
                       match["cls"] if match else "", match["admit"] if match else ""])
        for cell in marked[marked.max_row]:
            cell.fill = green if match else red
            cell.alignment = Alignment(vertical="center", horizontal="left" if cell.column == 3 else "center")
    marked.freeze_panes = "A2"
    for column, width in zip("ABCDEFGH", (7, 14, 11, 8, 14, 16, 22, 10)):
        marked.column_dimensions[column].width = width

    extras = wb.create_sheet("导出表里但母表没有")
    extras.append(["姓名", "学号", "班级", "报名状态", "录取状态"])
    for person in extra:
        extras.append([person["name"], person["sid"], person["cls"], person["sign"], person["admit"]])
    for column, width in zip("ABCDE", (10, 16, 22, 12, 12)):
        extras.column_dimensions[column].width = width

    summary = wb.create_sheet("汇总")
    summary.append(["项目", "人数"])
    for label, count in counts.items():
        summary.append([label, count])
    summary.column_dimensions["A"].width = 30
    summary.column_dimensions["B"].width = 16
    for sheet in wb:
        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="DDE6F5")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        wb.save(output)
    finally:
        wb.close()
    return counts


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.rows = []
        self.device = None
        self.running = False
        self._stop_event = threading.Event()
        self.mode = {"makeup": "dry"}  # 当前补签阶段：dry/opened
        self._q = queue.Queue()
        self._work_report_path = None
        self._grade_work_path = None

        root.title("到梦空间助手")
        root.geometry("1080x700")
        root.minsize(900, 600)

        self._build()
        self.root.after(30, self._drain_ui)
        self.root.after(120, self._check_device)  # 等主循环起来再检测，避免线程里调度界面

    # ---------------- UI ----------------
    def _rounded_image(self, fill, edge, radius, background):
        """生成 ttk 可拉伸的圆角底图，不增加图片文件或 UI 依赖。"""
        size = radius * 2 + 3
        image = tk.PhotoImage(master=self.root, width=size, height=size)
        image.put(background, to=(0, 0, size, size))
        for y in range(size):
            row = []
            for x in range(size):
                dx = max(radius - x, 0, x - (size - radius - 1))
                dy = max(radius - y, 0, y - (size - radius - 1))
                distance = dx * dx + dy * dy
                outside = distance > radius * radius
                border = distance > (radius - 1) ** 2 or x in (0, size - 1) or y in (0, size - 1)
                row.append(background if outside else edge if border else fill)
            image.put("{" + " ".join(row) + "}", to=(0, y))
        self._style_images.append(image)
        return image

    def _style(self):
        """统一字体、控件状态与屏幕缩放，只设置显示属性。"""
        from tkinter import font

        self._ui_scale = float(self.root.tk.call("tk", "scaling")) / (96 / 72)
        self._ui_px = lambda value: max(1, round(value * self._ui_scale))
        p = self._ui_px
        self._colors = {
            "bg": "#F0F4F9", "paper": "#FFFFFF", "ink": "#20334B",
            "muted": "#53647C", "line": "#DCE5F0", "blue": "#255ECC",
            "red": "#9B3946", "orange": "#8B5917", "header": "#173456",
        }
        c = self._colors
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            font.nametofont(name).configure(family="Microsoft YaHei UI", size=10)
        self.root.option_add("*TCombobox*Listbox.font", ("Microsoft YaHei UI", 10))
        self.root.configure(background=c["bg"])
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10), foreground=c["ink"],
                        background=c["bg"])
        style.configure("Workspace.TFrame", background=c["bg"])
        style.configure("Paper.TFrame", background=c["paper"])
        style.configure("Header.TFrame", background=c["header"])
        style.configure("Tabbar.TFrame", background="#EDF2F8")
        self._style_images = []
        radius = p(12)
        panel = self._rounded_image(c["paper"], c["line"], radius, c["bg"])
        style.element_create("Panel.surface", "image", panel, border=radius, sticky="nsew")
        style.layout("Panel.TFrame", [("Panel.surface", {"sticky": "nsew"})])
        style.configure("Panel.TFrame", background=c["bg"])
        style.configure("TLabel", background=c["paper"], foreground=c["ink"])
        style.configure("Title.TLabel", background=c["header"], foreground="white",
                        font=("Microsoft YaHei UI", 17, "bold"))
        style.configure("Section.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Muted.TLabel", foreground=c["muted"], font=("Microsoft YaHei UI", 9))
        style.configure("Device.TLabel", background=c["header"], foreground="#D2E2F6",
                        font=("Microsoft YaHei UI", 9))
        style.configure("Path.TLabel", background="#F5F7FB", foreground=c["muted"],
                        padding=(p(12), p(8)), font=("Microsoft YaHei UI", 9))
        # 只替换 ttk 按钮的绘制元素，保留原生命令、键盘和焦点行为。
        palettes = (
            ("TButton", "#F5F8FC", "#EAF1FB", "#DDE8F7", c["ink"], c["line"], c["paper"]),
            ("Primary.TButton", c["blue"], "#1E51B4", "#184394", "white", c["blue"], c["paper"]),
            ("Submit.TButton", "#FCF1F2", "#F8E5E8", "#F1D4DA", c["red"], "#EACDD3", c["paper"]),
            ("Header.TButton", "#26496D", "#315879", "#203E5E", "white", "#466582", c["header"]),
            ("HeaderPrimary.TButton", c["blue"], "#1E51B4", "#184394", "white", "#6088DD", c["header"]),
            ("WorkspaceTab.TButton", "#EDF2F8", "#E3EBF6", "#D8E4F5", c["muted"], "#EDF2F8", "#EDF2F8"),
        )
        for name, fill, hover, pressed, ink, edge, canvas in palettes:
            radius = p(7)
            base = self._rounded_image(fill, edge, radius, canvas)
            active = self._rounded_image(hover, hover, radius, canvas)
            down = self._rounded_image(pressed, pressed, radius, canvas)
            disabled = self._rounded_image("#E9EEF5", "#E9EEF5", radius, canvas)
            selected = self._rounded_image("white", "#D5E1F1", radius, canvas)
            element = name.replace(".", "_") + ".surface"
            style.element_create(element, "image", base, ("disabled", disabled),
                                 ("pressed", down), ("selected", selected), ("active", active),
                                 border=radius, padding=0, sticky="nsew")
            style.layout(name, [(element, {"sticky": "nsew", "children": [
                ("Button.focus", {"sticky": "nsew", "children": [
                    ("Button.padding", {"sticky": "nsew", "children": [
                        ("Button.label", {"sticky": "nsew"})]})]})]})])
            style.configure(name, background=canvas, foreground=ink, anchor="center",
                            padding=(p(14), p(9)), focusthickness=1,
                            focuscolor="white" if ink == "white" else c["blue"],
                            font=("Microsoft YaHei UI", 10, "bold" if "Primary" in name else "normal"))
            style.map(name, foreground=[("disabled", "#59697D"), ("selected", c["blue"]), ("!disabled", ink)])
        style.configure("Submit.TButton", justify="center")
        style.configure("TRadiobutton", background=c["paper"], foreground=c["muted"],
                        padding=(p(2), p(6)), focuscolor=c["blue"])
        style.map("TRadiobutton", background=[("active", c["paper"])],
                  foreground=[("selected", c["blue"]), ("active", c["ink"])])
        style.configure("TEntry", fieldbackground=c["paper"], bordercolor="#A7B7CD",
                        lightcolor=c["paper"], darkcolor=c["paper"],
                        padding=p(7), insertcolor=c["ink"])
        style.map("TEntry", bordercolor=[("focus", c["blue"])])
        style.configure("TCombobox", fieldbackground=c["paper"], background="#F0F4FA",
                        lightcolor=c["paper"], darkcolor=c["paper"],
                        bordercolor="#A7B7CD", padding=p(7), arrowsize=p(12))
        style.map("TCombobox", fieldbackground=[("readonly", c["paper"])],
                  foreground=[("readonly", c["ink"])], bordercolor=[("focus", c["blue"])])
        style.configure("Workspace.TNotebook", background=c["paper"], borderwidth=0,
                        bordercolor=c["paper"], lightcolor=c["paper"], darkcolor=c["paper"],
                        tabmargins=(0, 0, 0, 0))
        style.layout("Workspace.TNotebook.Tab", [])
        style.configure("WorkspaceTab.TButton", padding=(p(8), p(10)),
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Activity.Treeview", background=c["paper"], fieldbackground=c["paper"],
                        foreground=c["ink"], rowheight=p(44), borderwidth=0,
                        font=("Microsoft YaHei UI", 10))
        style.layout("Activity.Treeview", [("Treeview.padding", {"sticky": "nsew", "children": [
            ("Treeview.treearea", {"sticky": "nsew"})]})])
        style.configure("Activity.Treeview.Heading", background="#F2F6FB",
                        foreground=c["muted"], relief="flat", borderwidth=0,
                        padding=(p(8), p(10)), font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Activity.Treeview", background=[("selected", "#2458A6")],
                  foreground=[("selected", "#FFFFFF")])
        style.map("Activity.Treeview.Heading", background=[("active", "#E6ECF4")])
        style.configure("Vertical.TScrollbar", background="#C7D3E3", troughcolor=c["paper"],
                        bordercolor=c["paper"], lightcolor=c["paper"], darkcolor=c["paper"],
                        arrowcolor=c["muted"], borderwidth=0, arrowsize=p(11))
        style.configure("TSeparator", background=c["line"])

    def _wrap_label(self, parent, text, style="Muted.TLabel"):
        """让说明与文件路径按实际可用宽度换行。"""
        label = ttk.Label(parent, text=text, style=style, justify="left", anchor="w",
                          wraplength=self._ui_px(300))
        label.bind("<Configure>", lambda event: label.configure(
            wraplength=max(self._ui_px(80), event.width - self._ui_px(24))), add="+")
        return label

    def _build(self):
        icon = Path(__file__).resolve().parent / "assets" / "app.ico"
        if os.name == "nt" and icon.is_file():
            self.root.iconbitmap(default=str(icon))
        self._style()
        p, c = self._ui_px, self._colors
        self.root.geometry(f"{min(1180, self.root.winfo_screenwidth() - 40)}x"
                           f"{min(740, self.root.winfo_screenheight() - 80)}")
        self.root.minsize(1000, 640)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        top = ttk.Frame(self.root, style="Header.TFrame", padding=(p(22), p(9)))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        ttk.Label(top, text="到梦空间助手", style="Title.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, p(20)))
        self.lbl_dev = self._wrap_label(top, "设备：检测中…", "Device.TLabel")
        self.lbl_dev.grid(row=1, column=0, sticky="ew", padx=(0, p(20)), pady=(p(3), 0))
        ttk.Button(top, text="重新检测设备", command=self._check_device, style="Header.TButton").grid(
            row=0, column=1, rowspan=2, padx=(0, p(8)))
        ttk.Button(top, text="扫描活动列表", command=self._scan,
                   style="HeaderPrimary.TButton").grid(row=0, column=2, rowspan=2)
        ttk.Button(top, text="导出诊断包", command=self._export_diag, style="Header.TButton").grid(
            row=0, column=3, rowspan=2, padx=(p(8), 0))

        mid = ttk.Frame(self.root, style="Workspace.TFrame",
                        padding=(p(18), p(16), p(18), p(12)))
        mid.grid(row=1, column=0, sticky="nsew")
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)
        mid.columnconfigure(1, minsize=p(350))

        left = ttk.Frame(mid, style="Panel.TFrame", padding=p(16))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, p(14)))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text="「管理」列表（选中一条再操作）",
                  style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(p(2), p(14)))

        cols = ("idx", "name", "state", "todo")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=8,
                                 selectmode="browse", style="Activity.Treeview")
        for col, title, width in zip(cols, ("#", "活动名称", "状态", "处理"), (38, 330, 92, 54)):
            self.tree.heading(col, text=title, anchor="w" if col == "name" else "center")
            self.tree.column(col, width=p(width), minwidth=p(34 if col == "name" else width),
                             stretch=col == "name", anchor="w" if col == "name" else "center")
        self.tree.tag_configure("plain", background=c["paper"])
        self.tree.tag_configure("stripe", background="#F7F9FC")
        self.tree.grid(row=1, column=0, sticky="nsew")
        vs = ttk.Scrollbar(left, orient="vertical", command=self._scroll_activity)
        vs.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=lambda *args: (
            vs.set(*args), self.tree.after_idle(self._paint_activity_states)))
        self._state_labels = {}
        self._empty_activity = ttk.Label(self.tree, text="点击“扫描活动列表”读取手机活动",
                                         style="Muted.TLabel", anchor="center")
        self._empty_mark = tk.Canvas(self.tree, width=p(56), height=p(48),
                                     background=c["paper"], highlightthickness=0)
        for y in (10, 24, 38):
            self._empty_mark.create_rectangle(p(5), p(y - 3), p(11), p(y + 3),
                                               fill="#A9C2E5", outline="")
            self._empty_mark.create_line(p(20), p(y), p(50), p(y), fill="#A9C2E5",
                                         width=p(3), capstyle="round")
        for event in ("<Configure>", "<<TreeviewSelect>>", "<KeyRelease>", "<ButtonRelease-1>"):
            self.tree.bind(event, lambda event: self.tree.after_idle(self._paint_activity_states), add="+")

        right = ttk.Frame(mid, style="Panel.TFrame", padding=p(14), width=p(350))
        right.grid(row=0, column=1, rowspan=2, sticky="nsew")
        right.grid_propagate(False)  # 侧栏宽度稳定，长路径换行不反复撑开再收缩。
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        tabbar = ttk.Frame(right, style="Tabbar.TFrame")
        tabbar.grid(row=0, column=0, sticky="ew", pady=(0, p(10)))
        nb = ttk.Notebook(right, style="Workspace.TNotebook")
        nb.grid(row=1, column=0, sticky="nsew")
        self._notebook = nb

        mk = ttk.Frame(nb, style="Paper.TFrame")
        nb.add(mk, text="  补签  ")
        self.makeup_method = tk.StringVar(value="grade")
        choices = ttk.Frame(mk, style="Paper.TFrame")
        choices.pack(fill="x", pady=(p(2), p(14)))
        for value, text in (("grade", "按年级补签"), ("roster", "按名单补签")):
            ttk.Radiobutton(choices, text=text, variable=self.makeup_method, value=value,
                            command=self._show_makeup_method).pack(side="left", padx=(0, p(16)))
        self._makeup_grade_panel = ttk.Frame(mk, style="Paper.TFrame")
        self._makeup_roster_panel = ttk.Frame(mk, style="Paper.TFrame")
        grade_panel = self._makeup_grade_panel
        row1 = ttk.Frame(grade_panel, style="Paper.TFrame")
        row1.pack(fill="x", pady=(0, p(8)))
        ttk.Label(row1, text="年级：").pack(side="left", padx=(0, p(12)))
        self.cb_grade = ttk.Combobox(row1, values=grade_choices(), state="normal", width=10)
        self.cb_grade.current(0)
        self.cb_grade.pack(side="left", fill="x", expand=True)
        self._wrap_label(grade_panel, "可按照实际年份填写").pack(fill="x", pady=(0, p(12)))
        ttk.Button(grade_panel, text="按年级勾选", command=self._select_makeup,
                   style="Primary.TButton").pack(fill="x")
        self.btn_submit_mk = ttk.Button(grade_panel, text="提交补签\n（请自行在手机上完成提交）",
                                       command=self._submit_makeup_checked, style="Submit.TButton")
        self.btn_submit_mk.pack(fill="x", pady=(p(8), 0))
        roster_panel = self._makeup_roster_panel
        ttk.Button(roster_panel, text="选择名单 xlsx", command=lambda: self._pick_task_roster("makeup")).pack(fill="x")
        self.lbl_makeup_roster = self._wrap_label(roster_panel, "未选名单", "Path.TLabel")
        self.lbl_makeup_roster.pack(fill="x", pady=(p(4), p(12)))
        ttk.Button(roster_panel, text="按名单补签\n（请自行在手机上完成提交）", command=lambda: self._run_roster_task("makeup", True),
                   style="Submit.TButton").pack(fill="x")
        self._show_makeup_method()

        ad = ttk.Frame(nb, style="Paper.TFrame")
        nb.add(ad, text="  录取  ")
        self._admit_roster_panel = ttk.Frame(ad, style="Paper.TFrame")
        roster_panel = self._admit_roster_panel
        roster_panel.pack(fill="x", pady=(p(10), 0))
        ttk.Label(roster_panel, text="按名单录取", style="Section.TLabel").pack(
            anchor="w", pady=(0, p(14)))
        ttk.Button(roster_panel, text="选择名单 xlsx（学号选填）", command=lambda: self._pick_task_roster("admit")).pack(fill="x")
        self.lbl_admit_roster = self._wrap_label(roster_panel, "未选名单", "Path.TLabel")
        self.lbl_admit_roster.pack(fill="x", pady=(p(4), p(12)))
        ttk.Button(roster_panel, text="演练（只勾选，不提交）", command=lambda: self._run_roster_task("admit", False),
                   style="Primary.TButton").pack(fill="x")
        ttk.Button(roster_panel, text="正式录取\n（请自行在手机上完成提交）", command=lambda: self._run_roster_task("admit", True),
                   style="Submit.TButton").pack(fill="x", pady=(p(8), 0))

        cr = ttk.Frame(nb, style="Paper.TFrame")
        nb.add(cr, text="  学分  ")
        self.credit_method = tk.StringVar(value="roster")
        choices = ttk.Frame(cr, style="Paper.TFrame")
        choices.pack(fill="x", pady=(p(2), p(14)))
        for value, text in (("grade", "按年级发分"), ("roster", "按名单发分")):
            ttk.Radiobutton(choices, text=text, variable=self.credit_method, value=value,
                            command=self._show_credit_method).pack(side="left", padx=(0, p(16)))
        score_row = ttk.Frame(cr, style="Paper.TFrame")
        score_row.pack(fill="x", pady=(0, p(12)))
        ttk.Label(score_row, text="分值：").pack(side="left", padx=(0, p(12)))
        self.ent_score = ttk.Entry(score_row, width=8)
        self.ent_score.insert(0, "0.5")
        self.ent_score.pack(side="left")
        self._credit_grade_panel = ttk.Frame(cr, style="Paper.TFrame")
        self._credit_roster_panel = ttk.Frame(cr, style="Paper.TFrame")
        grade_panel = self._credit_grade_panel
        row = ttk.Frame(grade_panel, style="Paper.TFrame")
        row.pack(fill="x", pady=(0, p(8)))
        ttk.Label(row, text="年级：").pack(side="left", padx=(0, p(12)))
        self.cb_credit_grade = ttk.Combobox(row, values=grade_choices(), state="normal", width=10)
        self.cb_credit_grade.current(0)
        self.cb_credit_grade.pack(side="left", fill="x", expand=True)
        self._wrap_label(grade_panel, "可按照实际年份填写").pack(fill="x", pady=(0, p(12)))
        ttk.Button(grade_panel, text="按年级勾选", command=self._select_credit_grade,
                   style="Primary.TButton").pack(fill="x")
        self._wrap_label(grade_panel, "勾选完成后，请自行在手机上核对人数并完成提交。").pack(fill="x", pady=(p(8), 0))
        roster_panel = self._credit_roster_panel
        ttk.Button(roster_panel, text="选择名单 xlsx（学号选填）", command=lambda: self._pick_task_roster("credit")).pack(fill="x")
        self.lbl_roster = self._wrap_label(roster_panel, "未选名单", "Path.TLabel")
        self.lbl_roster.pack(fill="x", pady=(p(4), p(12)))
        ttk.Button(roster_panel, text="演练（不发分）", command=lambda: self._run_roster_task("credit", False),
                   style="Primary.TButton").pack(fill="x")
        ttk.Button(roster_panel, text="正式发放\n（请自行在手机上完成提交）", command=lambda: self._run_roster_task("credit", True),
                   style="Submit.TButton").pack(fill="x", pady=(p(8), 0))
        self._show_credit_method()

        # 保留本地比对页签。
        compare = ttk.Frame(nb, style="Paper.TFrame")
        if SHOW_COMPARE:
            nb.add(compare, text="  比对  ")
        self._wrap_label(compare, "本地 Excel 名单比对（无需连接手机）").pack(fill="x", pady=(0, p(14)))
        ttk.Button(compare, text="选择母表 xlsx", command=lambda: self._pick_compare("want")).pack(fill="x")
        self.lbl_compare_want = self._wrap_label(compare, "未选择母表", "Path.TLabel")
        self.lbl_compare_want.pack(fill="x", pady=(p(6), p(14)))
        ttk.Button(compare, text="选择导出表 xlsx", command=lambda: self._pick_compare("got")).pack(fill="x")
        self.lbl_compare_got = self._wrap_label(compare, "未选择导出表", "Path.TLabel")
        self.lbl_compare_got.pack(fill="x", pady=(p(6), p(16)))
        ttk.Button(compare, text="开始比对", command=self._compare,
                   style="Primary.TButton").pack(fill="x")

        self._tab_buttons = []
        for index, tab in enumerate(nb.tabs()):
            tabbar.columnconfigure(index, weight=1, uniform="tabs")
            button = ttk.Button(tabbar, text=nb.tab(tab, "text").strip(),
                                style="WorkspaceTab.TButton", command=lambda tab=tab: nb.select(tab))
            button.grid(row=0, column=index, sticky="ew")
            self._tab_buttons.append(button)
        nb.bind("<<NotebookTabChanged>>", self._sync_workspace_tabs)
        nb.enable_traversal()
        self._sync_workspace_tabs()

        self._work_result = ttk.Frame(right, style="Paper.TFrame")
        self._work_result.grid(row=2, column=0, sticky="ew", pady=(p(10), 0))
        self._work_result.columnconfigure(0, weight=1)
        self.lbl_work_result = self._wrap_label(self._work_result, "", "Section.TLabel")
        self.lbl_work_result.grid(row=0, column=0, sticky="ew", padx=(0, p(8)))
        self.lbl_work_activity = self._wrap_label(self._work_result, "")
        self.lbl_work_activity.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(p(8), 0))
        self.btn_export_work = ttk.Button(self._work_result, text="导出工作名单",
                                          command=self._export_work)
        self.btn_export_work.grid(row=0, column=1, sticky="e")
        self._work_result.grid_remove()

        logf = ttk.Frame(mid, style="Panel.TFrame", padding=(p(16), p(10)))
        logf.grid(row=1, column=0, sticky="ew", padx=(0, p(14)), pady=(p(12), 0))
        logf.columnconfigure(0, weight=1)
        ttk.Label(logf, text="日志", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, p(5)))
        self.btn_stop = ttk.Button(logf, text="停止任务", command=self._request_stop, state="disabled")
        self.btn_stop.grid(row=0, column=0, sticky="e", pady=(0, p(5)))
        self.log = ScrolledText(logf, height=3, state="disabled", wrap="word",
                                font=("Microsoft YaHei UI", 9), background=c["paper"],
                                foreground=c["muted"], borderwidth=0, relief="flat",
                                highlightthickness=0, padx=p(2), pady=p(2),
                                selectbackground="#D9E7FC", selectforeground=c["ink"],
                                insertbackground=c["blue"])
        self.log.grid(row=1, column=0, sticky="ew")
        self.log.vbar.configure(width=p(9), borderwidth=0, background="#C7D3E3",
                                activebackground="#A7BBD4", troughcolor=c["paper"], relief="flat")

        footer = ttk.Frame(self.root, style="Workspace.TFrame", padding=(p(18), 0, p(18), p(10)))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self._acknowledgments_window = None
        self.btn_acknowledgments = ttk.Button(footer, text="致谢", command=self._toggle_acknowledgments)
        self.btn_acknowledgments.grid(row=0, column=1, sticky="e")

        self.makeup_roster = None
        self.roster = None
        self.admit_roster = None
        self.compare_want = None
        self.compare_got = None
        self.root.after_idle(self._paint_activity_states)

    def _toggle_acknowledgments(self):
        window = self._acknowledgments_window
        if window is not None and window.winfo_exists():
            window.destroy()
            self._acknowledgments_window = None
            return
        window = self._acknowledgments_window = tk.Toplevel(self.root)
        window.title("致谢")
        window.transient(self.root)
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self._toggle_acknowledgments)
        window.bind("<Escape>", lambda event: self._toggle_acknowledgments())
        p = self._ui_px
        body = ttk.Frame(window, style="Paper.TFrame", padding=p(22))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="致谢", style="Section.TLabel").pack(anchor="w", pady=(0, p(16)))
        ttk.Label(body, text="项目主页", style="Section.TLabel").pack(anchor="w")
        ttk.Button(body, text=PROJECT_URL, command=lambda: webbrowser.open(PROJECT_URL, new=2)).pack(
            fill="x", pady=(p(8), p(6)))
        ttk.Label(body, text="源码、使用说明与软件下载", style="Muted.TLabel").pack(anchor="w")
        ttk.Separator(body).pack(fill="x", pady=p(16))
        for text in (
            "感谢原项目作者与贡献者的开源分享，让实用的想法得以延续。",
            "感谢南华大学易班工作站技术部、行政部，以及资环安团委在团学工作中的付出。",
            "也感谢每一位为团学工作付出时间与心力的同学。每一份认真与热忱，都是共同前行的力量。",
        ):
            ttk.Label(body, text=text, wraplength=p(440), justify="left").pack(
                anchor="w", pady=(0, p(12)))
        ttk.Button(body, text="参考项目：tangent2pi/daomeng", command=lambda: webbrowser.open(
            "https://github.com/tangent2pi/daomeng", new=2)).pack(anchor="w")
        ttk.Button(body, text="收起", command=self._toggle_acknowledgments).pack(
            anchor="e", pady=(p(16), 0))
        window.update_idletasks()
        x = self.root.winfo_rootx() + self.root.winfo_width() - window.winfo_reqwidth() - p(18)
        y = self.root.winfo_rooty() + self.root.winfo_height() - window.winfo_reqheight() - p(18)
        x = max(0, min(x, window.winfo_screenwidth() - window.winfo_reqwidth()))
        y = max(0, min(y, window.winfo_screenheight() - window.winfo_reqheight()))
        window.geometry(f"+{x}+{y}")

    def _show_makeup_method(self):
        if self.running:
            self.makeup_method.set(self._visible_makeup_method)
            return
        self._visible_makeup_method = self.makeup_method.get()
        self._makeup_grade_panel.pack_forget()
        self._makeup_roster_panel.pack_forget()
        panel = self._makeup_grade_panel if self._visible_makeup_method == "grade" else self._makeup_roster_panel
        panel.pack(fill="x")

    def _show_credit_method(self):
        if self.running:
            self.credit_method.set(self._visible_credit_method)
            return
        self._visible_credit_method = self.credit_method.get()
        self._credit_grade_panel.pack_forget()
        self._credit_roster_panel.pack_forget()
        panel = self._credit_grade_panel if self._visible_credit_method == "grade" else self._credit_roster_panel
        panel.pack(fill="x")

    def _select_credit_grade(self):
        if self.running:
            return
        sel = self.selected()
        if not sel:
            return
        if sel["state"] != "已结束":
            messagebox.showinfo("提示", "请选择已结束的活动。")
            return
        grade = self.cb_credit_grade.get().strip()
        if grade != "全部" and not re.fullmatch(r"20\d{2}级?", grade):
            messagebox.showerror("年级错误", "请选择「全部」或输入四位年级，例如 2027级。")
            return
        score = self.ent_score.get()
        try:
            roster_workflow.parse_score(score)
        except ValueError as exc:
            messagebox.showerror("分值错误", str(exc))
            return
        want = None if grade == "全部" else grade.removesuffix("级")
        self._work_result.grid_remove()

        def job():
            report = work_report.WorkReport(ROOT / "out", "发分", sel["name"], "按年级",
                                            condition=f"{grade} / {score}分")
            error, picked = "", []
            try:
                backend = roster_device.DeviceBackend(self.connect())
                backend.check_stop = self._check_stop
                self._check_stop()
                backend.open(task="credit", title=sel["name"], score=score)
                picked = backend.select_credit_grade(want, observe=report.observe)
            except Exception as exc:
                error = str(exc)
                if not isinstance(exc, device_checks.TaskStopped):
                    self._post(lambda message=error: messagebox.showerror("按年级发分已停止", message))
                raise
            finally:
                state = "本次工作已停止" if error else ("勾选完成，尚未提交" if picked else "本次工作完成")
                report.finish(state, error or ("请自行在手机上核对人数并完成提交。" if picked else "没有目标年级待发分人员；未提交。"))
                self._post(self._show_work_result, report.path)

        self.run(job, stoppable=True)

    def _sync_workspace_tabs(self, event=None):
        current = self._notebook.index("current")
        for index, button in enumerate(self._tab_buttons):
            button.state(["selected" if index == current else "!selected"])

    def _submit_makeup_checked(self):
        if self.running:
            return
        selected = self.selected()
        if not selected:
            return
        if selected["state"] != "已结束":
            messagebox.showinfo("提示", "请选择已结束的活动。")
            return
        title = selected["name"]
        if not messagebox.askyesno("确认补签", f"对「{title}」当前已勾选人员提交补签？\n\n提交后不可撤销。"):
            return
        report_path = self._grade_work_path
        self._work_result.grid_remove()

        def job():
            error = ""
            count = None
            try:
                count, record = device_checks.submit_selected_makeup(self.connect(), title, ROOT / "out")
                print(f"补签 {count} 人：已提交未核查，请在手机已签到列表核对。")
                print(f"提交记录：{record}")
            except Exception as exc:
                error = str(exc)
                print(error)
                self._post(lambda message=error: messagebox.showerror("补签任务停止", message))
            finally:
                output = work_report.makeup_submission(ROOT / "out", report_path, title, count, error)
                self._post(self._show_work_result, output)

        self.run(job)

    def _pick_task_roster(self, task):
        if self.running:
            return
        path = filedialog.askopenfilename(title="选择名单", filetypes=[("Excel 名单", "*.xlsx")])
        if not path:
            return
        attr, label = {"credit": ("roster", self.lbl_roster),
                       "admit": ("admit_roster", self.lbl_admit_roster),
                       "makeup": ("makeup_roster", self.lbl_makeup_roster)}[task]
        setattr(self, attr, str(Path(path).resolve()))
        label.configure(text=Path(path).name)

    def _run_roster_task(self, task, real):
        if self.running:
            return
        # 所有控件值只在主线程读取，后台只处理快照和设备接口。
        score = self.ent_score.get() if task == "credit" else None
        if task == "credit":
            try:
                roster_workflow.parse_score(score)
            except ValueError as exc:
                messagebox.showerror("分值错误", str(exc))
                return
        path = {"credit": self.roster, "admit": self.admit_roster,
                "makeup": self.makeup_roster}[task]
        if not path:
            messagebox.showinfo("提示", "请先选择名单 xlsx。")
            return
        sel = self.selected()
        if not sel:
            return
        required = "报名中" if task == "admit" else "已结束"
        if sel["state"] != required:
            messagebox.showinfo("提示", f"请选择「{required}」活动。")
            return
        title = sel["name"]
        label = roster_workflow.TASKS[task][0]
        if real and not messagebox.askyesno(
                f"确认{label}", f"对「{title}」按名单正式{label}？" +
                (f"\n统一分值：{score}，忽略名单里的分值列。" if task == "credit" else "") +
                "\n\n确认后将自动处理分组名单并提交。"):
            return
        self._grade_work_path = None
        self._work_result.grid_remove()

        def job():
            report = work_report.WorkReport(ROOT / "out", label, title,
                "正式" if real else "演练", condition=score or "", inputs=[path])
            error = ""
            try:
                result = roster_workflow.run_task(
                    path, task, title, ROOT / "out", score=score, real=real, confirmed=real,
                    backend_factory=lambda: roster_workflow.require_backend()(self.connect()),
                    check_stop=self._check_stop)
                report.read_feedback(result)
                error = result.error
                if result.error and not self._stop_event.is_set():
                    self._post(lambda message=result.error: messagebox.showerror("任务未完成", message))
            except Exception as exc:
                errcatch.record(type(exc), exc, exc.__traceback__, where="名单任务")
                error = f"{label}失败：{exc}"
                print(error)
                self._post(lambda message=error: messagebox.showerror("任务失败", message))
            finally:
                report.finish("本次工作已停止" if error else "本次工作完成", error or
                              ("正式提交状态请查看人员明细。" if real else "演练结束，未提交。"))
                self._post(self._show_work_result, report.path)

        self.run(job, stoppable=True)

    def _scroll_activity(self, *args):
        """滚动列表后同步状态文字的位置。"""
        self.tree.yview(*args)
        self.tree.after_idle(self._paint_activity_states)

    def _paint_activity_states(self):
        """仅覆盖状态单元格的颜色；保留原始表格数据和选择行为。"""
        if not self.tree.winfo_exists():
            return
        # 名称列吸收剩余宽度，状态与处理列在高缩放下仍完整可见。
        fixed = sum(self.tree.column(col, "width") for col in ("idx", "state", "todo"))
        name_width = max(self._ui_px(34), self.tree.winfo_width() - fixed - 2)
        if self.tree.column("name", "width") != name_width:
            self.tree.column("name", width=name_width)
        items = self.tree.get_children()
        for item in tuple(self._state_labels):
            if item not in items:
                self._state_labels.pop(item).destroy()
        if not items:
            self._empty_mark.place(relx=.5, rely=.40, anchor="s", y=-self._ui_px(22))
            self._empty_activity.place(relx=.5, rely=.40, anchor="center")
        else:
            self._empty_mark.place_forget()
            self._empty_activity.place_forget()
        colors = {"报名中": ("#255ECC", "#EAF1FF"), "已结束": ("#8B5917", "#FCF2DD"),
                  "已完结": ("#53647C", "#EEF2F7")}
        selection = self.tree.selection()
        for item in items:
            active = item in selection
            marker = "当前" if active else "处理"
            if self.tree.set(item, "todo") != marker:
                self.tree.set(item, "todo", marker)
            box = self.tree.bbox(item, "state")
            label = self._state_labels.get(item)
            if not box:
                if label:
                    label.place_forget()
                continue
            state = str(self.tree.item(item, "values")[2])
            if label is None:
                label = tk.Label(self.tree, font=("Microsoft YaHei UI", 9),
                                 borderwidth=0, anchor="center", takefocus=0)
                label.bind("<Button-1>", lambda event: self.tree.event_generate(
                    "<Button-1>", x=event.x + event.widget.winfo_x(),
                    y=event.y + event.widget.winfo_y(), state=event.state))
                label.bind("<MouseWheel>", lambda event: self._scroll_activity(
                    "scroll", -int(event.delta / 120), "units"))
                self._state_labels[item] = label
            ink, fill = colors.get(state, (self._colors["muted"], "#EEF2F7"))
            if active:
                ink, fill = "#FFFFFF", "#2458A6"
            label.configure(text=state, foreground=ink, background=fill)
            x, y, width, height = box
            label.place(x=x + self._ui_px(6), y=y + self._ui_px(9),
                        width=width - self._ui_px(12), height=height - self._ui_px(18))


    # ---------------- 本地名单比对 ----------------
    def _pick_compare(self, which):
        label = "母表" if which == "want" else "导出表"
        path = filedialog.askopenfilename(title=f"选择{label}", filetypes=[("Excel 表格", "*.xlsx")])
        if path:
            path = str(Path(path).resolve())
            if which == "want":
                self.compare_want = path
                self.lbl_compare_want.config(text=path)
            else:
                self.compare_got = path
                self.lbl_compare_got.config(text=path)

    def _compare(self):
        if self.running:
            return
        if not self.compare_want or not self.compare_got:
            messagebox.showinfo("提示", "请先选择母表和导出表（.xlsx）。")
            return
        want, got = self.compare_want, self.compare_got
        output = ROOT / "out" / "代表团名单-标记结果.xlsx"

        def job():
            try:
                counts = write_comparison_report(want, got, output)
            except PermissionError:
                error = "无法写入结果文件，请关闭 Excel 中已打开的结果表，并确认输出目录可写。"
            except (ValueError, OSError) as exc:
                error = str(exc)
            else:
                print(f"母表 {counts['母表总人数']} 人，导出表 {counts['导出表行数']} 行，命中 {counts['命中人数']} 人")
                print(f"比对完成：{output.resolve()}")
                return
            print(f"比对失败：{error}")
            self._post(lambda message=error: messagebox.showerror("比对失败", message))

        self.run(job)

    # ---------------- 基础设施 ----------------
    def _post(self, fn, *args):
        """后台线程回主线程：统一排队，由主线程泵出来执行。"""
        self._q.put((fn, args))

    def _drain_ui(self):
        for _ in range(100):  # 日志较多时也把事件循环交还给按钮和窗口
            try:
                fn, args = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except Exception as exc:  # noqa: BLE001
                errcatch.record(type(exc), exc, exc.__traceback__, where="界面回调", snapshot=False)
        self.root.after(40, self._drain_ui)

    def write(self, text):
        try:
            path = ROOT / "out" / "gui.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(text)
        except OSError as exc:
            text += f"\n日志保存失败：{exc}\n"
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def connect(self):
        if self.device is None:
            import uiautomator2 as u2

            self.device = u2.connect()
        return self.device

    def _check_device(self):
        if self.running:
            return

        def job():
            try:
                import adbutils

                devs = adbutils.adb.list()
                if not devs:
                    return "设备：未检测到（插线并打开 USB 调试）"
                self.device = None
                d = self.connect()
                return f"设备：{d.serial} 已连接"
            except Exception as exc:  # noqa: BLE001
                print(f"[设备检测失败] {type(exc).__name__}: {exc}")
                return f"设备：连接失败（{type(exc).__name__}: {exc}）"

        def done(text):
            self.lbl_dev.config(text=text)
            self.write(text + "\n")

        def worker():
            text = job()
            self._post(done, text)

        self.run(worker)

    def _request_stop(self):
        if self.running:
            self._stop_event.set()
            self.btn_stop.configure(text="正在停止…", state="disabled")
            self.write("\n已请求停止：等待当前操作核对结束，保留已勾选人员和工作记录。\n")

    def _check_stop(self):
        if self._stop_event.is_set():
            raise device_checks.TaskStopped("用户已停止任务；已完成记录和当前手机勾选保留。")

    def run(self, fn, *, stoppable=False):
        if self.running:
            return
        self.running = True
        self._stop_event.clear()
        self.btn_stop.configure(text="停止任务", state="normal" if stoppable else "disabled")

        class TaskOutput(io.TextIOBase):
            def write(output, text):
                self._post(self.write, text)
                return len(text)

            def flush(output):
                pass

        def job():
            try:
                # 每项任务重新建立连接；不重启手机应用，也不清除待提交的勾选。
                self.device = None
                with contextlib.redirect_stdout(TaskOutput()):
                    fn()
            except device_checks.TaskStopped as exc:
                self._post(self.write, "\n" + str(exc) + "\n")
            except Exception as exc:
                self._post(self.write, "\n" + traceback.format_exc())
                errcatch.record(type(exc), exc, exc.__traceback__, where="任务线程")
            finally:
                self._post(self._done, "\n已就绪，可选择活动继续下一项操作。\n")

        try:
            self.write("\n▶ 开始…\n")
            threading.Thread(target=job, daemon=True).start()
        except Exception:
            self.running = False
            self.btn_stop.configure(state="disabled")
            raise

    def _done(self, text):
        self.running = False
        self.btn_stop.configure(text="停止任务", state="disabled")
        self.write(text)

    def selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在左边选中一条活动")
            return None
        vals = self.tree.item(sel[0], "values")
        return {"name": vals[1], "state": vals[2]}

    # ---------------- 扫描 ----------------
    def _show_work_result(self, path):
        data = work_report.read_report(path)
        self._work_report_path = Path(path)
        self.lbl_work_result.configure(text=data["state"])
        self.lbl_work_activity.configure(text=f"{data['task']} · {data['title']}")
        self._work_result.grid()

    def _export_work(self):
        if self.running:
            messagebox.showinfo("提示", "请等待当前工作结束后再导出。")
            return
        path = self._work_report_path
        if not path:
            messagebox.showinfo("提示", "当前没有可导出的工作名单。")
            return
        destination = filedialog.asksaveasfilename(title="导出工作名单", defaultextension=".xlsx",
            filetypes=[("Excel 工作名单", "*.xlsx")], initialdir=str(ROOT / "out"),
            initialfile=f"工作名单-{Path(path).stem}.xlsx")
        if not destination:
            return

        def job():
            try:
                output = work_report.export_report(path, destination)
            except Exception as exc:
                error = "文件可能正被 Excel 占用或没有写入权限，请关闭文件或更换保存位置。" if isinstance(exc, PermissionError) else str(exc)
                self._post(lambda message=error: messagebox.showerror("导出失败", message))
                return
            print(f"工作名单已导出：{output}")
            self._post(lambda: messagebox.showinfo("导出完成", f"工作名单已保存到：{output}"))

        self.run(job)

    def _export_diag(self):
        """把日志/现场/提交记录打成一个 zip，方便直接发给开发。"""
        try:
            errcatch.capture("手动导出", self.device)
            path = errcatch.export_package()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", f"{type(exc).__name__}: {exc}")
            return
        self.write("[OK] 诊断包：" + str(path))
        messagebox.showinfo("已导出诊断包", "已保存到：" + str(path))

    def _scan(self):
        def job():
            d = self.connect()
            scan.navigate(d)
            self.rows = scan.scan(d)
            self._post(self._fill)

        self.run(job)

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        visible = [r for r in self.rows if r["state"] in ("报名中", "已结束")]
        for i, r in enumerate(visible, 1):
            self.tree.insert("", "end", values=(i, r["name"], r["state"], "处理"),
                             tags=("plain" if i % 2 else "stripe",))
        self.tree.after_idle(self._paint_activity_states)
        if not visible:
            self._empty_activity.config(text="没有需要处理的活动")

    # ---------------- 补签 ----------------
    def _open_unsigned(self):
        sel = self.selected()
        if not sel:
            return

        def job():
            import makeup

            d = self.connect()
            if not makeup.open_unsigned(d, sel["name"]):
                raise guard.GuardError("没能进入未签到列表")
            print("未签到名单已打开，请选择年级并开始勾选。")

        self.run(job)

    def _select_makeup(self):
        if self.running:
            return
        sel = self.selected()
        if not sel:
            return
        if sel["state"] != "已结束":
            messagebox.showinfo("提示", "请选择已结束的活动。")
            return
        grade = self.cb_grade.get().strip()
        if grade != "全部" and not re.fullmatch(r"20\d{2}级?", grade):
            messagebox.showerror("年级错误", "请选择「全部」或输入四位年级，例如 2027级。")
            return
        want = None if grade == "全部" else grade.removesuffix("级")
        self._grade_work_path = None
        self._work_result.grid_remove()

        def job():
            import makeup

            report = work_report.WorkReport(ROOT / "out", "补签", sel["name"], "按年级", condition=grade)
            error = ""
            picked = []
            try:
                d = self.connect()
                if not makeup.open_unsigned(d, sel["name"]):
                    raise guard.GuardError("没能进入目标活动的未签到列表。")
                picked = makeup.walk_select(d, want=want, observe=report.observe, check_stop=self._check_stop)
                self._grade_work_path = report.path
            except Exception as exc:
                error = str(exc)
                raise
            finally:
                state = "本次工作已停止" if error else ("勾选完成，尚未提交" if picked else "本次工作完成")
                report.finish(state, error or ("请确认后提交补签。" if picked else "没有目标年级待补签人员；未提交。"))
                self._post(self._show_work_result, report.path)

        self.run(job, stoppable=True)

    def _submit_makeup(self):
        if self.running:
            return
        n = messagebox.askyesno(
            "确认补签",
            "真的提交补签吗？\n\n提交后名单会进入「已签到」，不可撤销。",
        )
        if not n:
            return

        def job():
            import makeup

            d = self.connect()
            footer = makeup.snap(d)[1]
            cnt = makeup.count_of(footer)
            if not cnt:
                raise guard.GuardError("当前没有勾选任何人")
            guard.begin_submit("makeup", {"count": cnt})
            el = d(resourceId="com.jingcai.apps.qualitydev:id/tv_next")
            if not el.wait(3):
                raise guard.GuardError("找不到底部「补签 N 人」")
            el.click()
            import time

            time.sleep(1.5)
            xml = d.dump_hierarchy()
            if "补签" not in xml and "确认" not in xml:
                raise guard.GuardError("没出现确认弹窗，已停止")
            ok = d(resourceId="android:id/button1")
            if not ok.wait(3):
                raise guard.GuardError("找不到确认按钮")
            ok.click()
            time.sleep(2.5)
            print("[OK] 提交完成：", makeup.snap(d)[1])
            guard.end_submit("makeup")

        self.run(job)

    # ---------------- 按名单录取 ----------------
    def _pick_admit_roster(self):
        p = filedialog.askopenfilename(filetypes=[("名单", "*.xlsx *.xlsm"), ("全部", "*.*")])
        if p:
            self.admit_roster = p
            self.lbl_admit_roster.config(text=Path(p).name)

    def _roster_admit(self, real):
        sel = self.selected()
        if not sel:
            return
        if not self.admit_roster:
            messagebox.showinfo("提示", "先选名单 xlsx（列：序号 / 代表团 / 姓名 / 身份）")
            return
        if real and not messagebox.askyesno(
            "确认录取",
            f"按名单对「{sel['name']}」正式录取？\n\n名单里没有的会被跳过；提交后不可撤销。",
        ):
            return

        def job():
            import admit_roster

            argv = ["--roster", self.admit_roster, "--title", sel["name"], "--open"]
            argv.append("--yes" if real else "--dry")
            rc = admit_roster.main(argv)
            print(f"[OK] 按名单录取结束，返回码 {rc}")

        self.run(job)

    # ---------------- 学分 ----------------
    def _pick_roster(self):
        p = filedialog.askopenfilename(filetypes=[("名单", "*.xlsx *.xlsm *.csv"), ("全部", "*.*")])
        if p:
            self.roster = p
            self.lbl_roster.config(text=Path(p).name)

    def _credit(self, real):
        sel = self.selected()
        if not sel:
            return
        if not self.roster:
            messagebox.showinfo("提示", "先选名单 xlsx（列：姓名 / 学号 / 分值）")
            return
        if real and not messagebox.askyesno(
            "确认发放学分",
            f"对「{sel['name']}」真的发放学分？\n\n这一步会直接写进学生账号。",
        ):
            return

        def job():
            import credit

            d = self.connect()
            roster = credit.read_roster(self.roster)
            print(f"[OK] 名单 {len(roster)} 人")
            credit.enter_credit(d, sel["name"])
            items = credit.projects(d)
            if not items:
                raise guard.GuardError("没读到学分项目")
            print(f"[OK] {len(items)} 个学分项目")
            for p in items[:3]:
                print(f"     {p['name']}（积分 {p['score']}）")
            credit.grant_one(d, items[0], roster, real=real, yes=real)

        self.run(job)



def _diag() -> str:
    """逐项打印环境信息，用于排查「连不上手机」。"""
    import subprocess

    lines = []
    add = lines.append
    add(f"frozen={getattr(sys, 'frozen', False)}")
    add(f"executable={sys.executable}")
    add(f"cwd={os.getcwd()}")
    try:
        import bootstrap

        add(f"adb_dir={bootstrap.adb_dir()}")
    except Exception as exc:
        add(f"bootstrap 失败: {type(exc).__name__}: {exc}")
    for mod in ("adbutils", "uiautomator2", "openpyxl", "lxml", "yaml"):
        try:
            m = __import__(mod)
            add(f"import {mod}: OK {getattr(m, '__version__', '')}")
        except Exception as exc:
            add(f"import {mod}: {type(exc).__name__}: {exc}")
    try:
        import adb_boot

        add(f"adb_boot.ensure_adb_server()={adb_boot.ensure_adb_server()}")
        add(f"adb_boot.devices()={adb_boot.devices()}")
    except Exception as exc:
        add(f"adb_boot 失败: {type(exc).__name__}: {exc}")
    try:
        exe = str((bootstrap.adb_dir() or Path(".")) / "adb.exe")
        r = subprocess.run([exe, "devices"], capture_output=True, timeout=20, env=os.environ.copy())
        add(f"直接跑 adb devices: rc={r.returncode} out={r.stdout[:200]!r} err={r.stderr[:200]!r}")
    except Exception as exc:
        add(f"直接跑 adb 失败: {type(exc).__name__}: {exc}")
    try:
        import adbutils

        add(f"adbutils.adb.list()={adbutils.adb.list()}")
    except Exception as exc:
        add(f"adbutils.adb.list() 失败: {type(exc).__name__}: {exc}")
    try:
        import uiautomator2 as u2

        d = u2.connect()
        add(f"u2.connect() OK serial={d.serial} window={d.window_size()}")
    except Exception as exc:
        add(f"u2.connect() 失败: {type(exc).__name__}: {exc}")
    return chr(10).join(lines)


def _ensure_streams():
    """pythonw 下 sys.stdout/stderr 是 None，模块里任何 print 都会抛异常；落一份日志兜底。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_path = ROOT / "out" / "gui.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def main():
    try:
        _ensure_streams()
        errcatch.install()  # 未捕获异常自动落盘，不用用户复述
        try:  # 双击 exe 时先把 adb server 拉起来；失败不影响界面
            import adb_boot

            adb_boot.ensure_adb_server()
        except Exception:
            pass
        if "--diag" in sys.argv:
            report = _diag()
            out = ROOT / "out" / "diag.txt"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(report, encoding="utf-8")
            print(report)
            return

        if os.name == "nt":
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DaomengSpace.Assistant")
        root = tk.Tk()
        errcatch.register_window(root)
        App(root)  # 先打开界面；插线后点「重新检测设备」即可
        if "--selftest" in sys.argv:
            root.update_idletasks()
            print("SELFTEST OK")
            root.destroy()
            return
        root.mainloop()
    except Exception:
        err = ROOT / "out" / "gui-error.log"
        err.parent.mkdir(parents=True, exist_ok=True)
        err.write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
