#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""红警2多版本地图管理器 - XP 专用版（Tkinter）

Windows XP 上 Python 只能安装到 3.4.4。本版只依赖标准库 tkinter
（Python 自带），无需安装 PyQt5，打包更简单、体积更小。

功能与 MapManagerGUI.py（PyQt 版）一致：
1. 浏览 maps 文件夹中的地图库（支持子文件夹分类，自动解析地图真实名称）
2. 管理 config.ini 中的多版本游戏目录
3. 将选中的地图批量安装到指定游戏版本目录（按格式自动匹配）
4. 列出/移除游戏目录中已安装的第三方地图
5. 一键启动游戏（原版 ra2.exe，尤里复仇 ra2md.exe / yuri.exe）

兼容性：不使用 f-string 等新语法，可在 Windows XP + Python 3.4 + Tk 8.5 运行
"""

import os
import sys
import re
import subprocess
import ctypes

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, simpledialog, filedialog
except ImportError:
    sys.exit("缺少 tkinter（Python 自带，一般无需安装）")

import map_utils as MU

if getattr(sys, 'frozen', False):
    # PyInstaller 打包（--onefile/--onedir）后：数据文件定位到 exe 同目录，
    # 保证 config.ini / maps / installed_maps.json 可持久化。
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(APP_DIR, 'config.ini')
MAPS_DIR = os.path.join(APP_DIR, 'maps')
INSTALLED_DB = os.path.join(APP_DIR, 'installed_maps.json')

# 紧凑类型标签（树列显示用；详情/提示里仍用完整标签）
COMPACT_KIND = {
    'task': '任务',
    'ra2_skirmish': '原版',
    'yr_skirmish': '尤里',
}

# 已安装地图来源标签
SRC_LABELS = {
    'builtin': '官方内置',
    'installed': '本工具安装',
    'library': '与地图库同名',
    'other': '游戏自带/未知',
}


class ToolTip(object):
    """通用悬停提示：绑定到 Treeview / Listbox，随鼠标显示多行文本"""

    def __init__(self, widget, text_cb):
        self.widget = widget
        self.text_cb = text_cb
        self._tip = None
        widget.bind('<Motion>', self._motion)
        widget.bind('<Leave>', self._hide)
        widget.bind('<ButtonPress>', self._hide)

    def _motion(self, ev):
        text = self.text_cb(ev)
        if not text:
            self._hide()
            return
        if self._tip is None:
            self._tip = tk.Toplevel(self.widget)
            self._tip.wm_overrideredirect(True)
            self._tip.attributes('-topmost', True)
            label = tk.Label(self._tip, text='', justify='left',
                             background='#ffffe0', relief='solid', borderwidth=1,
                             font=('Tahoma', 8))
            label.pack(padx=2, pady=2)
            self._tip._label = label
        self._tip._label.config(text=text)
        x = ev.x_root + 14
        y = ev.y_root + 14
        self._tip.wm_geometry('+%d+%d' % (x, y))
        self._tip.deiconify()

    def _hide(self, _ev=None):
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class MapManagerTk(object):
    def __init__(self, root):
        self.root = root
        root.title("红警2多版本地图管理器（XP版）")
        root.minsize(780, 500)
        # 按屏幕可用区自适应窗口大小（小笔记本 1024x600 也能完整显示）
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = int(sw * 0.96), int(sh * 0.96)
        root.geometry('%dx%d+%d+%d' % (w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 2)))

        MU.ensure_default_config(CONFIG_FILE, MAPS_DIR)
        self.versions = MU.load_config(CONFIG_FILE)
        imported = MU.migrate_legacy_json(CONFIG_FILE)
        if imported:
            self.versions = MU.load_config(CONFIG_FILE)
            self.set_status("已从旧版 ra2_paths.json 导入 %d 个路径" % imported)

        self._library_rows = []
        self._installed_paths = []

        self._build_ui()
        self.refresh_all()
        self._check_write_permission()

    # ---------------------------------------------------------------- UI 构建
    def _build_ui(self):
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="刷新(&R)", command=self.refresh_all)
        file_menu.add_command(label="打开 maps 文件夹(&O)", command=lambda: self.open_folder(MAPS_DIR))
        file_menu.add_separator()
        file_menu.add_command(label="退出(&Q)", command=self.root.destroy)
        menubar.add_cascade(label="文件(&F)", menu=file_menu)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="关于(&A)", command=self.show_about)
        menubar.add_cascade(label="帮助(&H)", menu=help_menu)
        self.root.config(menu=menubar)

        # 状态栏（最底部）
        self.status_var = tk.StringVar()
        self.status_label = tk.Label(self.root, textvariable=self.status_var,
                                     anchor='w', relief='sunken', font=('Tahoma', 8))
        self.status_label.pack(side='bottom', fill='x')

        # 底部操作栏
        bottom = tk.Frame(self.root)
        bottom.pack(side='bottom', fill='x')
        self.var_keep_subdir = tk.BooleanVar(value=False)
        tk.Checkbutton(bottom, text="保留子文件夹结构(不推荐)",
                       variable=self.var_keep_subdir).pack(side='left', padx=4)
        tk.Button(bottom, text="刷新全部", command=self.refresh_all,
                  font=('Tahoma', 9)).pack(side='right', padx=4, pady=2)

        # 主分栏：左地图库 / 右版本+已安装
        main_pane = ttk.Panedwindow(self.root, orient='horizontal')
        main_pane.pack(fill='both', expand=True)

        left = ttk.Frame(main_pane)
        right = ttk.Frame(main_pane)
        main_pane.add(left, weight=7)
        main_pane.add(right, weight=4)

        # ----- 左侧：地图库 -----
        tk.Label(left, text="地图库（maps 文件夹）", font=('Tahoma', 10, 'bold'),
                 anchor='w').pack(fill='x', padx=4, pady=(4, 0))
        map_frame = ttk.Frame(left)
        map_frame.pack(fill='both', expand=True, padx=4, pady=2)
        self.map_tree = ttk.Treeview(map_frame, columns=('file', 'kind', 'players'),
                                     show='tree headings', selectmode='extended')
        self.map_tree.heading('#0', text='地图名称', command=lambda: self._sort_map_tree('name'))
        self.map_tree.column('#0', width=210, minwidth=110, stretch=True, anchor='w')
        self.map_tree.heading('file', text='文件', command=lambda: self._sort_map_tree('file'))
        self.map_tree.column('file', width=120, minwidth=80, stretch=True, anchor='w')
        self.map_tree.heading('kind', text='类型', command=lambda: self._sort_map_tree('kind'))
        self.map_tree.column('kind', width=60, minwidth=50, stretch=False, anchor='center')
        self.map_tree.heading('players', text='玩家', command=lambda: self._sort_map_tree('players'))
        self.map_tree.column('players', width=42, minwidth=36, stretch=False, anchor='center')
        map_scroll = ttk.Scrollbar(map_frame, orient='vertical', command=self.map_tree.yview)
        self.map_tree.config(yscrollcommand=map_scroll.set)
        self.map_tree.pack(side='left', fill='both', expand=True)
        map_scroll.pack(side='right', fill='y')
        self.map_tree.bind('<<TreeviewSelect>>', self._on_map_selected)
        ToolTip(self.map_tree, self._map_tip_cb)

        self.detail_var = tk.StringVar(value="在左侧选择地图查看详情")
        detail = tk.Label(left, textvariable=self.detail_var, justify='left', anchor='nw',
                          relief='groove', borderwidth=1, font=('Tahoma', 8), padx=4, pady=2)
        detail.pack(fill='x', padx=4, pady=(0, 4))

        # ----- 右侧：版本 + 已安装 -----
        # 小屏（1024x600）空间有限，用 pack 布局：版本区高度按内容自适应
        # （两排按钮永远不会被裁剪），已安装列表占满剩余空间。
        # 不用垂直 Panedwindow——其初始按 weight 分配高度，版本区 pane
        # 高度不足时会把“安装地图/启动游戏”按钮挤出可视区。
        version_frame = ttk.Frame(right)
        installed_frame = ttk.Frame(right)
        version_frame.pack(fill='x', side='top', padx=(0, 4))
        installed_frame.pack(fill='both', expand=True, side='bottom', padx=(0, 4))

        # 版本管理
        tk.Label(version_frame, text="游戏版本（config.ini）", font=('Tahoma', 9, 'bold'),
                 anchor='w').pack(fill='x', padx=4, pady=(4, 0))
        set_row = tk.Frame(version_frame)
        set_row.pack(fill='x', padx=4, pady=2)
        for text, cmd in (("浏览… 设置目录", self.set_selected_dir),
                          ("打开目录", self.open_selected_dir),
                          ("新增版本", self.add_version),
                          ("删除版本", self.del_version)):
            tk.Button(set_row, text=text, command=cmd,
                      font=('Tahoma', 8)).pack(side='left', padx=(0, 4))

        vtable = ttk.Frame(version_frame)
        vtable.pack(fill='both', expand=True, padx=4)
        self.version_tree = ttk.Treeview(vtable, columns=('name', 'dir', 'type'),
                                         show='headings', selectmode='extended',
                                         height=min(max(len(self.versions), 2), 6))
        self.version_tree.heading('name', text='版本名称')
        self.version_tree.column('name', width=90, minwidth=70, stretch=False, anchor='w')
        self.version_tree.heading('dir', text='游戏目录')
        self.version_tree.column('dir', width=220, minwidth=120, stretch=True, anchor='w')
        self.version_tree.heading('type', text='识别类型')
        self.version_tree.column('type', width=100, minwidth=80, stretch=False, anchor='center')
        vscroll = ttk.Scrollbar(vtable, orient='vertical', command=self.version_tree.yview)
        self.version_tree.config(yscrollcommand=vscroll.set)
        self.version_tree.pack(side='left', fill='both', expand=True)
        vscroll.pack(side='right', fill='y')
        self.version_tree.bind('<<TreeviewSelect>>', lambda _e: self.refresh_installed_list())
        self.version_tree.bind('<Double-1>', self._on_version_double)

        act_row = tk.Frame(version_frame)
        act_row.pack(fill='x', padx=4, pady=4)
        tk.Button(act_row, text="安装地图 ▶", command=self.install_maps,
                  font=('Tahoma', 9, 'bold')).pack(side='left', padx=(0, 4))
        # 两个启动按钮常驻：RA2 启动 ra2.exe，尤里启动 ra2md.exe/yuri.exe；
        # 所选版本目录中没有对应 exe 时按钮禁用（state=disabled）。
        self.btn_launch_ra2 = tk.Button(act_row, text="▶ 启动 RA2",
                                        command=lambda: self.launch_game(kind='ra2'),
                                        font=('Tahoma', 9, 'bold'))
        self.btn_launch_ra2.pack(side='left', padx=(0, 4))
        self.btn_launch_yr = tk.Button(act_row, text="▶ 启动尤里",
                                       command=lambda: self.launch_game(kind='yr'),
                                       font=('Tahoma', 9, 'bold'))
        self.btn_launch_yr.pack(side='left')

        # 已安装地图
        tk.Label(installed_frame, text="该版本目录下的地图（可多选后移除）",
                 font=('Tahoma', 9, 'bold'), anchor='w').pack(fill='x', padx=4, pady=(4, 0))
        hide_row = tk.Frame(installed_frame)
        hide_row.pack(fill='x', padx=4, pady=2)
        self.var_hide_builtin = tk.BooleanVar(value=True)
        tk.Checkbutton(hide_row, text="隐藏自带/未知地图",
                       variable=self.var_hide_builtin,
                       command=self.refresh_installed_list).pack(side='left')
        self.var_hide_mmx = tk.BooleanVar(value=False)
        tk.Checkbutton(hide_row, text="隐藏多图包(.mmx)",
                       variable=self.var_hide_mmx,
                       command=self.refresh_installed_list).pack(side='left', padx=(8, 0))

        ilist = ttk.Frame(installed_frame)
        ilist.pack(fill='both', expand=True, padx=4)
        self.installed_list = tk.Listbox(ilist, selectmode='extended',
                                         exportselection=False, font=('Tahoma', 8))
        iscroll = ttk.Scrollbar(ilist, orient='vertical', command=self.installed_list.yview)
        self.installed_list.config(yscrollcommand=iscroll.set)
        self.installed_list.pack(side='left', fill='both', expand=True)
        iscroll.pack(side='right', fill='y')
        ToolTip(self.installed_list, self._inst_tip_cb)

        inst_btn_row = tk.Frame(installed_frame)
        inst_btn_row.pack(fill='x', padx=4, pady=4)
        tk.Button(inst_btn_row, text="移除选中的地图", command=self.remove_selected_maps,
                  font=('Tahoma', 8)).pack(side='left', padx=(0, 4))
        tk.Button(inst_btn_row, text="刷新", command=self.refresh_installed_list,
                  font=('Tahoma', 8)).pack(side='left')

    # ---------------------------------------------------------------- 刷新
    def set_status(self, text):
        self.status_var.set(text)

    def refresh_all(self):
        self.versions = MU.load_config(CONFIG_FILE)
        self.refresh_version_table()
        self.refresh_map_tree()
        self.refresh_installed_list()
        self.set_status("已刷新：%d 个版本，config: %s" % (len(self.versions), CONFIG_FILE))

    def refresh_version_table(self):
        self.version_tree.delete(*self.version_tree.get_children(''))
        # 表格行数随版本数量自适应（2~6 行），避免表格过高挤压下方按钮
        self.version_tree.config(height=min(max(len(self.versions), 2), 6))
        for i, v in enumerate(self.versions):
            gd = v['gamedir'] or "（未设置，双击或点按钮选择）"
            game_type = MU.detect_game_type(v['gamedir']) if v['gamedir'] else None
            self.version_tree.insert('', 'end', iid='v%d' % i,
                                     values=(v['name'], gd, MU.type_label(game_type)))
        if not self.versions:
            self.installed_list.delete(0, 'end')
            self.installed_list.insert('end', "config.ini 中没有任何版本配置，请点击“新增版本”或手动编辑 config.ini。")
            self._installed_paths = [None]

    # ---------------------------------------------------------------- 地图库
    def _scan_library(self):
        """扫描地图库，返回 [(name, filename, kind_label, players, rel, edition)]"""
        rows = []
        if not os.path.isdir(MAPS_DIR):
            return rows
        for rel in MU.scan_map_folder(MAPS_DIR):
            abs_path = os.path.join(MAPS_DIR, rel)
            info = MU.read_map_info(abs_path) or {}
            filename = rel.split(os.sep)[-1]
            name = info.get('name') or self._clean_map_name(filename)
            players = str(info['players']) if info.get('players') else '-'
            kind = MU.map_kind(filename)
            kind_label = COMPACT_KIND.get(kind, '')
            edition = None
            if kind == 'task':
                edition = MU.detect_map_edition(abs_path)
                if edition:
                    kind_label += {'yr': '·YR', 'ra2': '·RA2'}.get(edition, '')
            rows.append((name, filename, kind_label, players, rel, edition))
        return rows

    def refresh_map_tree(self):
        self._library_rows = self._scan_library()
        self._rebuild_map_tree(self._library_rows)

    def _rebuild_map_tree(self, rows):
        self.map_tree.delete(*self.map_tree.get_children(''))
        self._map_nodes = {}
        for name, filename, kind_label, players, rel, edition in rows:
            parts = rel.split(os.sep)
            parent = self._ensure_dir_nodes(parts[:-1])
            iid = 'file:' + rel
            self.map_tree.insert(parent, 'end', iid=iid, text=name,
                                 values=(filename, kind_label, players))
            self._map_nodes[iid] = ('file', rel)
        # 展开一层目录
        for top in self.map_tree.get_children(''):
            self.map_tree.item(top, open=True)
        if not self.map_tree.get_children(''):
            self.map_tree.insert('', 'end', text="未找到 maps 文件夹：%s" % MAPS_DIR)

    def _ensure_dir_nodes(self, dirs):
        """在树中逐级找到/创建目录节点，返回最终目录节点 iid"""
        parent = ''
        for d in dirs:
            rel = d if not parent else parent[len('dir:'):] + os.sep + d
            iid = 'dir:' + rel
            if not self.map_tree.exists(iid):
                self.map_tree.insert(parent, 'end', iid=iid, text=d)
                self._map_nodes[iid] = ('dir', rel)
            parent = iid
        return parent

    def _sort_map_tree(self, col):
        if not self._library_rows:
            return
        idx = {'name': 0, 'file': 1, 'kind': 2, 'players': 3}[col]
        rows = sorted(self._library_rows, key=lambda r: r[idx])
        self._rebuild_map_tree(rows)

    def selected_map_files(self):
        """收集地图树中选中的文件相对路径；选中的目录节点会展开为其中所有文件"""
        result = set()
        for iid in self.map_tree.selection():
            self._collect_files(iid, result)
        return sorted(result)

    def _collect_files(self, iid, result):
        node = self._map_nodes.get(iid)
        if node is None:
            return
        if node[0] == 'file':
            result.add(node[1])
            return
        for child in self.map_tree.get_children(iid):
            self._collect_files(child, result)

    # ---------------------------------------------------------------- 地图详情
    def _on_map_selected(self, _ev=None):
        iid = None
        sel = self.map_tree.selection()
        for s in sel:
            node = self._map_nodes.get(s)
            if node and node[0] == 'file':
                iid = s
                break
        if iid is None:
            self.detail_var.set("在左侧选择地图查看详情")
            return
        rel = self._map_nodes[iid][1]
        abs_path = os.path.join(MAPS_DIR, rel)
        info = MU.read_map_info(abs_path)
        lines = [
            "名称：%s" % self.map_tree.item(iid, 'text'),
            "文件：%s" % rel,
            "大小：%s" % self._fmt_size(os.path.getsize(abs_path)),
        ]
        if info and info.get('players'):
            lines.append("玩家：%s" % info['players'])
        kind = MU.map_kind(rel)
        if kind:
            lines.append("格式：%s" % MU.KIND_LABELS.get(kind, ''))
            lines.append("说明：%s" % MU.KIND_DESCRIPTIONS.get(kind, ''))
            if kind == 'task':
                edition = MU.detect_map_edition(abs_path)
                if edition:
                    lines.append("版本：%s（需尤里的复仇，原版无法运行）" % MU.edition_label(edition))
        self.detail_var.set("\n".join(lines))

    def _map_tip_cb(self, ev):
        iid = self.map_tree.identify_row(ev.y)
        if not iid:
            return ''
        node = self._map_nodes.get(iid)
        if node is None:
            return ''
        if node[0] == 'dir':
            return '目录：' + node[1]
        rel = node[1]
        abs_path = os.path.join(MAPS_DIR, rel)
        name = self.map_tree.item(iid, 'text')
        info = MU.read_map_info(abs_path) or {}
        filename = rel.split(os.sep)[-1]
        kind = MU.map_kind(filename)
        lines = [name, "文件: %s" % rel,
                 "格式: %s" % MU.KIND_LABELS.get(kind, ''),
                 MU.KIND_DESCRIPTIONS.get(kind, ''),
                 "路径: %s" % abs_path]
        if kind == 'task':
            edition = MU.detect_map_edition(abs_path)
            if edition:
                lines.append("版本: %s（该任务地图依赖尤里复仇独有内容，原版无法运行）" % MU.edition_label(edition))
        return "\n".join(lines)

    @staticmethod
    def _fmt_size(size):
        for unit in ('B', 'KB', 'MB'):
            if size < 1024:
                return "%.0f %s" % (size, unit)
            size /= 1024
        return "%.1f GB" % size

    @staticmethod
    def _clean_map_name(filename):
        """地图文件名简化：去掉扩展名与常见编号前缀，如 '01_沙漠风暴.mpr' → '沙漠风暴'"""
        base = os.path.splitext(filename)[0]
        cleaned = re.sub(r'^[\d]{1,3}[\s._\-—]+', '', base).strip()
        return cleaned or base

    # ---------------------------------------------------------------- 版本操作
    def _selected_version_rows(self):
        rows = []
        for iid in self.version_tree.selection():
            if iid.startswith('v'):
                try:
                    rows.append(int(iid[1:]))
                except ValueError:
                    pass
        return sorted(set(rows))

    def selected_versions(self):
        """返回表格中选中的版本字典列表；未选中则返回全部"""
        rows = self._selected_version_rows()
        if not rows:
            return list(self.versions)
        return [self.versions[r] for r in rows if 0 <= r < len(self.versions)]

    def _selected_single(self):
        """返回第一个选中版本；未选中返回 None"""
        rows = self._selected_version_rows()
        if not rows:
            return None
        return self.versions[rows[0]]

    def _on_version_double(self, ev):
        iid = self.version_tree.identify_row(ev.y)
        if iid and iid.startswith('v'):
            self.version_tree.selection_set(iid)
            self.set_selected_dir()

    def set_selected_dir(self):
        v = self._selected_single()
        if v is None:
            messagebox.showinfo("提示", "请先在表格中选择一个游戏版本。")
            return
        start = v['gamedir'] or APP_DIR
        path = filedialog.askdirectory(title="选择 %s 的游戏根目录" % v['name'], initialdir=start)
        if not path:
            return
        v['gamedir'] = path
        MU.save_config(self.versions, CONFIG_FILE)
        self.refresh_version_table()
        self.refresh_installed_list()
        self.set_status("已设置 %s 目录: %s" % (v['name'], path))

    def open_selected_dir(self):
        v = self._selected_single()
        if v is None:
            messagebox.showinfo("提示", "请先选择一个游戏版本。")
            return
        if v['gamedir'] and os.path.isdir(v['gamedir']):
            self.open_folder(v['gamedir'])
        else:
            messagebox.showwarning("提示", "%s 的目录无效或未设置。" % v['name'])

    def launch_game(self, kind='auto'):
        """启动选中的游戏版本。
        kind: 'auto' 自动选择 / 'ra2' 强制启动 ra2.exe / 'yr' 强制启动 ra2md.exe 或 yuri.exe"""
        v = self._selected_single()
        if v is None:
            messagebox.showinfo("提示", "请先在表格中选择一个游戏版本。")
            return
        if not v['gamedir'] or not os.path.isdir(v['gamedir']):
            messagebox.showwarning("无法启动", "%s 的目录无效或未设置。" % v['name'])
            return
        if kind == 'ra2':
            launcher = MU.find_launcher_ra2(v['gamedir'])
        elif kind == 'yr':
            launcher = MU.find_launcher_yr(v['gamedir'])
        else:
            launcher = MU.find_launcher(v['gamedir'])
        if not launcher:
            messagebox.showwarning("无法启动",
                "在 %s 中未找到对应的启动程序。\n\n"
                "原版应包含 ra2.exe；尤里复仇应包含 ra2md.exe 或 yuri.exe。"
                % v['gamedir'])
            return
        # 权限检查：游戏目录不可写且程序未提权时，游戏启动/运行可能失败
        if os.name == 'nt' and '--elevated' not in sys.argv:
            bad = MU.unwritable_dirs([v])
            if bad:
                r = messagebox.askyesno("需要管理员权限",
                    "游戏目录没有写入权限，游戏启动或运行时可能报错：\n%s\n\n"
                    "这通常是因为游戏安装在系统保护目录（如 Program Files）。\n\n"
                    "是否以管理员身份重新运行本程序后再启动游戏？\n"
                    "（选“否”将以当前权限直接启动，可能遇到权限不足）"
                    % bad[0])
                if r:
                    if self._request_admin_restart():
                        self.set_status("正在以管理员身份重新启动...")
                        self.root.after(1500, self.root.destroy)
                    else:
                        messagebox.showwarning("无法提权",
                            "无法自动以管理员身份重新启动，\n"
                            "请关闭本程序后，右键本程序选择“以管理员身份运行”。")
                    return
        try:
            subprocess.Popen([launcher], cwd=v['gamedir'])
            self.set_status("正在启动 %s ..." % v['name'])
        except Exception as e:
            messagebox.showwarning("启动失败", "%s\n%s" % (launcher, e))

    def _update_launch_buttons(self):
        """按所选版本目录中实际存在的 exe 启用/禁用两个启动按钮：
        “启动 RA2”看 ra2.exe（备选 game.exe）；
        “启动尤里”看 ra2md.exe / yuri.exe / gamemd.exe。"""
        v = self._selected_single()
        ra2_ok = (v is not None and v['gamedir'] and os.path.isdir(v['gamedir'])
                  and MU.find_launcher_ra2(v['gamedir']))
        yr_ok = (v is not None and v['gamedir'] and os.path.isdir(v['gamedir'])
                 and MU.find_launcher_yr(v['gamedir']))
        self.btn_launch_ra2.config(state='normal' if ra2_ok else 'disabled')
        self.btn_launch_yr.config(state='normal' if yr_ok else 'disabled')

    def add_version(self):
        name = simpledialog.askstring("新增版本", "请输入版本名称（如：红色警戒2原版）:",
                                      parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        n = 1
        while True:
            sec = "ra2new%d" % n
            if not any(v['section'] == sec for v in self.versions):
                break
            n += 1
        self.versions.append({'section': sec, 'name': name, 'gamedir': ''})
        MU.save_config(self.versions, CONFIG_FILE)
        self.refresh_version_table()
        self.set_status("已新增版本: [%s] %s" % (sec, name))

    def del_version(self):
        rows = self._selected_version_rows()
        if not rows:
            messagebox.showinfo("提示", "请先选择要删除的游戏版本。")
            return
        names = "、".join(self.versions[r]['name'] for r in rows)
        if not messagebox.askyesno("确认删除",
                "确认删除版本：%s\n（仅移除配置，不会删除游戏文件）" % names):
            return
        for r in reversed(rows):
            del self.versions[r]
        MU.save_config(self.versions, CONFIG_FILE)
        self.refresh_version_table()
        self.refresh_installed_list()

    # ---------------------------------------------------------------- 安装/移除
    def install_maps(self):
        targets = self.selected_versions()
        valid_targets = [v for v in targets if v['gamedir'] and os.path.isdir(v['gamedir'])]
        if not valid_targets:
            messagebox.showwarning("无法安装",
                "没有有效的目标游戏目录。\n请在“游戏版本”表格中先设置游戏目录。")
            return

        files = self.selected_map_files()
        if not files:
            messagebox.showinfo("提示",
                "请先在左侧地图库中选择要安装的地图（可多选，也可选整个文件夹）。")
            return

        keep_subdir = self.var_keep_subdir.get()
        copied, skipped = 0, 0
        skipped_detail = []
        recorded = {}

        for v in valid_targets:
            game_type = MU.detect_game_type(v['gamedir'])
            for rel in files:
                src = os.path.join(MAPS_DIR, rel)
                # 按格式兼容性过滤：.yrm 只进尤里复仇；.mpr 原版与尤里复仇均可
                # （尤里复仇目录自带 ra2.exe，可运行 .mpr）；.map 按内容检测版本，.mmx 通用
                if not MU.can_install_to(rel, game_type, src):
                    skipped += 1
                    kind = MU.map_kind(rel)
                    desc = MU.KIND_DESCRIPTIONS.get(kind, '格式不兼容')
                    if kind == 'task':
                        edition = MU.detect_map_edition(src)
                        if edition == 'yr':
                            desc = "任务地图为 YR 专属，原版/共和国之辉无法运行"
                        elif edition == 'ra2':
                            desc = "任务地图为 RA2 专属，尤里复仇可兼容（已放行）"
                    skipped_detail.append("%s -> %s（%s）" % (rel, v['name'], desc))
                    continue
                try:
                    dst = MU.copy_map_to_game(src, v['gamedir'], keep_subdir=keep_subdir, rel_path=rel)
                    copied += 1
                    recorded.setdefault(v['gamedir'], []).append(
                        os.path.relpath(dst, v['gamedir']))
                except Exception as e:
                    tip = ""
                    if isinstance(e, PermissionError) and '--elevated' not in sys.argv:
                        tip = "\n\n提示：目录无写入权限时，请关闭本程序后右键它以管理员身份运行。"
                    messagebox.showwarning("复制失败", "%s -> %s\n%s%s" % (rel, v['gamedir'], e, tip))

        # 记录本次安装的地图，用于区分游戏自带地图
        for gd, rels in recorded.items():
            MU.record_installed_maps(gd, rels, INSTALLED_DB)
        self.refresh_installed_list()
        msg = "安装完成：成功复制 %d 个文件，跳过 %d 个。" % (copied, skipped)
        if skipped_detail:
            msg += "\n\n跳过明细：\n" + "\n".join(skipped_detail[:10])
            if len(skipped_detail) > 10:
                msg += "\n... 等共 %d 项" % len(skipped_detail)
        messagebox.showinfo("安装结果", msg)
        self.set_status(msg.split("\n")[0])

    def refresh_installed_list(self):
        self._update_launch_buttons()
        self.installed_list.delete(0, 'end')
        self._installed_paths = []
        rows = self._selected_version_rows()
        if not rows:
            self.installed_list.insert('end', "（请在上方选择一个游戏版本，查看其目录下的地图）")
            self._installed_paths.append(None)
            return
        v = self.versions[rows[0]]
        gamedir = v['gamedir']
        if not gamedir or not os.path.isdir(gamedir):
            self.installed_list.insert('end', "（%s 的目录无效或未设置）" % v['name'])
            self._installed_paths.append(None)
            return
        maps = MU.list_game_maps(gamedir)
        if not maps:
            self.installed_list.insert('end', "（该目录下没有找到地图）")
            self._installed_paths.append(None)
            return
        hide_builtin = self.var_hide_builtin.get()
        hide_mmx = self.var_hide_mmx.get()
        hidden, hidden_mmx = 0, 0
        for p in maps:
            src = MU.classify_game_map(gamedir, p, MAPS_DIR, INSTALLED_DB)
            if src in ('builtin', 'other') and hide_builtin:
                hidden += 1
                continue
            kind = MU.map_kind(p)
            if kind == 'multimap' and hide_mmx:
                hidden_mmx += 1
                continue
            name = MU.display_name(p)
            src_label = SRC_LABELS.get(src, '游戏自带/未知')
            tag = MU.KIND_LABELS.get(kind, '')
            if kind == 'task':
                edition = MU.detect_map_edition(p)
                if edition:
                    tag = "%s·%s" % (tag, MU.edition_label(edition))
            self.installed_list.insert('end',
                "【%s】%s    [%s]  %s" % (src_label, name, os.path.basename(p), tag))
            self._installed_paths.append(p)
        if hidden:
            self.installed_list.insert('end', "……已隐藏 %d 个游戏自带/未知来源的地图（取消上方勾选可显示）" % hidden)
            self._installed_paths.append(None)
        if hidden_mmx:
            self.installed_list.insert('end', "……已隐藏 %d 个多地图包（取消上方勾选可显示）" % hidden_mmx)
            self._installed_paths.append(None)

    def _inst_tip_cb(self, ev):
        idx = self.installed_list.nearest(ev.y)
        if not (0 <= idx < len(self._installed_paths)):
            return ''
        p = self._installed_paths[idx]
        if not p:
            return ''
        gamedir = None
        v = self._selected_single()
        if v:
            gamedir = v['gamedir']
        src = MU.classify_game_map(gamedir, p, MAPS_DIR, INSTALLED_DB) if gamedir else 'other'
        kind = MU.map_kind(p)
        lines = [p, "来源：%s" % SRC_LABELS.get(src, src)]
        if kind:
            lines.append(MU.KIND_DESCRIPTIONS.get(kind, ''))
            if kind == 'task':
                edition = MU.detect_map_edition(p)
                if edition:
                    lines.append("版本：%s（该任务地图依赖尤里复仇独有内容，原版无法运行）" % MU.edition_label(edition))
        return "\n".join(lines)

    def remove_selected_maps(self):
        sel = list(self.installed_list.curselection())
        if not sel:
            messagebox.showinfo("提示", "请先在下方的已安装列表中选择要移除的地图（可多选）。")
            return
        paths = [self._installed_paths[i] for i in sel
                 if 0 <= i < len(self._installed_paths) and self._installed_paths[i]]
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            messagebox.showwarning("提示", "所选文件不存在，可能已被删除。")
            self.refresh_installed_list()
            return
        v = self._selected_single()
        gamedir = v['gamedir'] if v else None
        # 官方内置地图一律保护，不允许删除
        protected, targets = [], []
        for p in paths:
            if gamedir and MU.classify_game_map(gamedir, p, MAPS_DIR, INSTALLED_DB) == 'builtin':
                protected.append(p)
            else:
                targets.append(p)
        if protected:
            messagebox.showinfo("已保护官方地图",
                "以下 %d 个文件是官方内置地图，已自动跳过（不会被删除）：\n\n%s" %
                (len(protected), "\n".join(os.path.basename(p) for p in protected)))
        if not targets:
            self.set_status("所选文件均为官方内置地图，未删除任何文件。")
            return
        names = "\n".join(os.path.basename(p) for p in targets)
        if not messagebox.askyesno("确认移除",
                "确认从游戏目录删除这 %d 个地图文件？\n\n%s\n\n"
                "注意：删除后无法恢复。若文件不在地图库(maps)中且不是本工具安装的，"
                "删除前请自行确认其来源。" % (len(targets), names)):
            return
        deleted, failed = 0, 0
        removed_rel = []
        for p in targets:
            try:
                os.remove(p)
                deleted += 1
                if gamedir:
                    removed_rel.append(os.path.relpath(p, gamedir))
            except Exception as e:
                failed += 1
                tip = ""
                if isinstance(e, PermissionError) and '--elevated' not in sys.argv:
                    tip = "\n\n提示：目录无写入权限时，请关闭本程序后右键它以管理员身份运行。"
                messagebox.showwarning("删除失败", "%s\n%s%s" % (p, e, tip))
        if removed_rel:
            MU.forget_installed_maps(gamedir, removed_rel, INSTALLED_DB)
        self.refresh_installed_list()
        msg = "已移除 %d 个地图文件" % deleted
        if failed:
            msg += "，%d 个失败" % failed
        self.set_status(msg)

    # ---------------------------------------------------------------- 提权处理
    def _check_write_permission(self):
        """检测游戏目录可写性：存在不可写目录时询问是否以管理员身份重启。

        仅 Windows 且未提权时触发；XP 无 UAC，几乎不会命中，命中也无副作用。
        """
        if os.name != 'nt' or '--elevated' in sys.argv:
            return
        bad = MU.unwritable_dirs(self.versions)
        if not bad:
            return
        r = messagebox.askyesno("需要管理员权限",
            "以下游戏目录没有写入权限，安装/移除地图将失败：\n\n%s\n\n"
            "这通常是因为游戏安装在系统保护目录（如 Program Files）。\n\n"
            "是否以管理员身份重新运行本程序？\n"
            "（若游戏装在 D 盘等普通位置，可点“否”继续使用）"
            % "\n".join(bad))
        if not r:
            return
        if self._request_admin_restart():
            self.set_status("正在以管理员身份重新启动...")
            self.root.after(1500, self.root.destroy)
        else:
            messagebox.showwarning("无法提权",
                "无法自动以管理员身份重新启动，\n"
                "请关闭本程序后，右键本程序选择“以管理员身份运行”。")

    @staticmethod
    def _request_admin_restart():
        """以管理员身份重新启动本程序；成功返回 True。

        通过 ShellExecuteW 的 "runas" 动词触发 UAC（Windows 7 及以上）；
        XP 无 UAC 时该调用直接以当前身份运行，不弹任何框。
        """
        if os.name != 'nt' or '--elevated' in sys.argv:
            return False
        try:
            if getattr(sys, 'frozen', False):
                exe = sys.executable
                params = ' '.join('"%s"' % a for a in sys.argv[1:] if a != '--elevated')
            else:
                exe = os.path.abspath(sys.executable)
                params = '"%s"' % os.path.abspath(__file__)
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
            return ret > 32
        except Exception:
            return False

    # ---------------------------------------------------------------- 工具
    @staticmethod
    def open_folder(path):
        if not os.path.isdir(path):
            return
        try:
            if os.name == 'nt':
                os.startfile(path)
            else:
                subprocess.Popen(['xdg-open', path])
        except Exception:
            pass

    def show_about(self):
        messagebox.showinfo("关于",
            "红警2多版本地图管理器（XP版）\n\n"
            "功能：\n"
            "· 浏览 maps 文件夹地图库（支持子文件夹、自动读取地图真实名称）\n"
            "· 管理 config.ini 中的多版本游戏目录\n"
            "· 批量安装地图到指定游戏版本（自动匹配：.mpr 可装到任何含 ra2.exe 的目录，.yrm 只进尤里复仇，.map/.mmx 通用）\n"
            "· 列出/移除游戏目录中的第三方地图\n"
            "· 一键启动游戏（原版 ra2.exe；尤里复仇 ra2md.exe / yuri.exe）\n\n"
            "地图格式：\n"
            "· .map 任务地图（单人战役，两版通用）\n"
            "· .mpr 原版遭遇战（尤里复仇目录含 ra2.exe 时也可使用）\n"
            "· .yrm 尤里遭遇战（原版不识别）\n"
            "· .mmx 多地图包（两版通用）\n\n"
            "界面库：Tkinter（Python 自带）\n"
            "配置文件：config.ini\n"
            "地图库：%s" % MAPS_DIR)


def main():
    root = tk.Tk()
    app = MapManagerTk(root)
    root.mainloop()


if __name__ == "__main__":
    main()
