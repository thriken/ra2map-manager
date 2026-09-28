#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""红警2多版本地图管理器 - 图形界面版（PyQt5 优先，兼容 PyQt6）

功能：
1. 浏览 maps 文件夹中的地图库（支持子文件夹分类，自动解析地图真实名称）
2. 管理 config.ini 中的多版本游戏目录
3. 将选中的地图批量安装到指定游戏版本目录（按格式自动匹配）
4. 列出/移除游戏目录中已安装的第三方地图
5. 一键启动游戏（原版 ra2.exe，尤里复仇 ra2md.exe / yuri.exe）

兼容性：
- 优先使用 PyQt5，未安装时自动回退 PyQt6
- 不使用 f-string 等新语法，可在 Windows XP + Python 3.4 + PyQt5 5.6 上运行

依赖：PyQt5（pip install PyQt5；XP 环境使用 Python 3.4 32位 + PyQt5 5.6）
"""

import os
import sys
import subprocess
import ctypes
import re

try:
    from PyQt6.QtCore import Qt, QUrl, QTimer
    from PyQt6.QtGui import QAction, QDesktopServices
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QSplitter, QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
        QListWidget, QListWidgetItem, QPushButton, QLabel, QGroupBox,
        QFileDialog, QMessageBox, QHeaderView, QCheckBox, QAbstractItemView,
        QInputDialog,
    )
    PYQT6 = True
except ImportError:
    from PyQt5.QtCore import Qt, QUrl, QTimer
    from PyQt5.QtGui import QDesktopServices
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QSplitter, QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
        QListWidget, QListWidgetItem, QPushButton, QLabel, QGroupBox,
        QFileDialog, QMessageBox, QHeaderView, QCheckBox, QAbstractItemView,
        QInputDialog, QAction,
    )
    PYQT6 = False

# ---- PyQt5 / PyQt6 枚举差异兼容层 ----
if PYQT6:
    USER_ROLE = Qt.ItemDataRole.UserRole
    ORI_H = Qt.Orientation.Horizontal
    ORI_V = Qt.Orientation.Vertical
    SORT_ASC = Qt.SortOrder.AscendingOrder
    TEXT_SELECTABLE = Qt.TextInteractionFlag.TextSelectableByMouse
    HEADER_RESIZE = QHeaderView.ResizeMode.ResizeToContents
    HEADER_STRETCH = QHeaderView.ResizeMode.Stretch
    SEL_EXTENDED = QAbstractItemView.SelectionMode.ExtendedSelection
    SEL_ROWS = QAbstractItemView.SelectionBehavior.SelectRows
    NO_EDIT = QAbstractItemView.EditTrigger.NoEditTriggers
    MB_YES = QMessageBox.StandardButton.Yes
    ELIDE_MIDDLE = Qt.TextElideMode.ElideMiddle
else:
    USER_ROLE = Qt.UserRole
    ORI_H = Qt.Horizontal
    ORI_V = Qt.Vertical
    SORT_ASC = Qt.AscendingOrder
    TEXT_SELECTABLE = Qt.TextSelectableByMouse
    HEADER_RESIZE = QHeaderView.ResizeToContents
    HEADER_STRETCH = QHeaderView.Stretch
    SEL_EXTENDED = QAbstractItemView.ExtendedSelection
    SEL_ROWS = QAbstractItemView.SelectRows
    NO_EDIT = QAbstractItemView.NoEditTriggers
    MB_YES = QMessageBox.Yes
    ELIDE_MIDDLE = Qt.ElideMiddle

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

# 树节点 UserRole 数据格式
ROLE_DIR = 'dir'    # ('dir', 目录名)
ROLE_FILE = 'file'  # ('file', 相对路径)

# 小屏紧凑类型标签（树列显示用；详情/提示里仍用完整标签）
COMPACT_KIND = {
    'task': '任务',
    'ra2_skirmish': '原版',
    'yr_skirmish': '尤里',
}


class MapManagerWindow(QMainWindow):
    def __init__(self):
        super(MapManagerWindow, self).__init__()
        self.setWindowTitle("红警2多版本地图管理器")
        self.resize(1200, 800)

        MU.ensure_default_config(CONFIG_FILE, MAPS_DIR)
        self.versions = MU.load_config(CONFIG_FILE)
        imported = MU.migrate_legacy_json(CONFIG_FILE)
        if imported:
            self.versions = MU.load_config(CONFIG_FILE)
            self.statusBar().showMessage("已从旧版 ra2_paths.json 导入 %d 个路径" % imported, 5000)

        self._build_ui()
        self.refresh_all()
        self._check_write_permission()

    # ---------------------------------------------------------------- UI 构建
    def _build_ui(self):
        # 菜单栏
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件(&F)")
        act_refresh = QAction("刷新(&R)", self)
        act_refresh.triggered.connect(self.refresh_all)
        file_menu.addAction(act_refresh)
        act_open_maps = QAction("打开 maps 文件夹(&O)", self)
        act_open_maps.triggered.connect(lambda: self.open_folder(MAPS_DIR))
        file_menu.addAction(act_open_maps)
        file_menu.addSeparator()
        act_quit = QAction("退出(&Q)", self)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        help_menu = menubar.addMenu("帮助(&H)")
        act_about = QAction("关于(&A)", self)
        act_about.triggered.connect(self.show_about)
        help_menu.addAction(act_about)

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        splitter = QSplitter(ORI_H)
        root_layout.addWidget(splitter, 1)

        # ----- 左侧：地图库 -----
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_title = QLabel("地图库（maps 文件夹）")
        left_title.setStyleSheet("font-weight: bold;font-size: 14px;")
        left_layout.addWidget(left_title)

        self.map_tree = QTreeWidget()
        self.map_tree.setHeaderLabels(["地图名称", "文件", "类型", "玩家"])
        self.map_tree.setColumnWidth(0, 210)
        self.map_tree.setColumnWidth(1, 140)
        self.map_tree.setColumnWidth(2, 62)
        self.map_tree.setTextElideMode(ELIDE_MIDDLE)
        self.map_tree.setSelectionMode(SEL_EXTENDED)
        self.map_tree.setSortingEnabled(True)
        self.map_tree.sortByColumn(0, SORT_ASC)
        self.map_tree.currentItemChanged.connect(self.on_map_selected)
        left_layout.addWidget(self.map_tree, 1)

        self.detail_box = QGroupBox("地图详情")
        self.detail_box.setMaximumHeight(130)
        detail_layout = QVBoxLayout(self.detail_box)
        self.detail_label = QLabel("在左侧选择地图查看详情")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(TEXT_SELECTABLE)
        detail_layout.addWidget(self.detail_label)
        left_layout.addWidget(self.detail_box)

        splitter.addWidget(left_panel)

        # ----- 右侧：版本 + 已安装地图 -----
        right_splitter = QSplitter(ORI_V)
        splitter.addWidget(right_splitter)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 4)
        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 2)

        # 游戏版本管理
        version_box = QGroupBox("游戏版本（config.ini）")
        version_layout = QVBoxLayout(version_box)

        self.version_table = QTableWidget(0, 3)
        self.version_table.setHorizontalHeaderLabels(["版本名称", "游戏目录", "识别类型"])
        self.version_table.setSelectionBehavior(SEL_ROWS)
        self.version_table.setSelectionMode(SEL_EXTENDED)
        self.version_table.setEditTriggers(NO_EDIT)
        self.version_table.verticalHeader().setVisible(False)
        header = self.version_table.horizontalHeader()
        header.setSectionResizeMode(0, HEADER_RESIZE)
        header.setSectionResizeMode(1, HEADER_STRETCH)
        header.setSectionResizeMode(2, HEADER_RESIZE)
        self.version_table.itemSelectionChanged.connect(self.refresh_installed_list)
        self.version_table.cellDoubleClicked.connect(self.on_version_double_clicked)

        # 版本设置按钮行（表格上方）
        set_row = QHBoxLayout()
        btn_browse = QPushButton("浏览… 设置目录")
        btn_browse.clicked.connect(self.set_selected_dir)
        btn_open_dir = QPushButton("打开目录")
        btn_open_dir.clicked.connect(self.open_selected_dir)
        btn_add = QPushButton("新增版本")
        btn_add.clicked.connect(self.add_version)
        btn_del = QPushButton("删除版本")
        btn_del.clicked.connect(self.del_version)
        for b in (btn_browse, btn_open_dir, btn_add, btn_del):
            set_row.addWidget(b)
        set_row.addStretch(1)
        version_layout.addLayout(set_row)

        version_layout.addWidget(self.version_table, 1)

        # 操作按钮行（表格下方）：安装地图 → 启动 RA2 → 启动尤里
        # 两个启动按钮常驻，所选版本目录中没有对应 exe 时禁用（setEnabled(False)）
        act_row = QHBoxLayout()
        btn_install = QPushButton("安装地图 ▶")
        btn_install.setStyleSheet("font-weight: bold; padding: 6px 16px;font-size: 14px;")
        btn_install.clicked.connect(self.install_maps)
        self.btn_launch_ra2 = QPushButton("▶ 启动 RA2")
        self.btn_launch_ra2.setStyleSheet("font-weight: bold; color: #1a7f37;font-size: 14px;")
        self.btn_launch_ra2.clicked.connect(lambda: self.launch_game(kind='ra2'))
        self.btn_launch_yr = QPushButton("▶ 启动尤里")
        self.btn_launch_yr.setStyleSheet("font-weight: bold; color: #1a7f37;font-size: 14px;")
        self.btn_launch_yr.clicked.connect(lambda: self.launch_game(kind='yr'))
        act_row.addWidget(btn_install)
        act_row.addWidget(self.btn_launch_ra2)
        act_row.addWidget(self.btn_launch_yr)
        act_row.addStretch(1)
        version_layout.addLayout(act_row)

        right_splitter.addWidget(version_box)

        # 已安装地图管理
        installed_box = QGroupBox("该版本目录下的地图（可多选后移除）")
        installed_layout = QVBoxLayout(installed_box)

        hide_row = QHBoxLayout()
        self.hide_builtin = QCheckBox("隐藏自带/未知地图")
        self.hide_builtin.setChecked(True)
        self.hide_builtin.setToolTip("勾选后只显示本工具安装过、或与 maps 地图库中同名的地图，\n隐藏游戏自带的官方地图与 MOD 内置地图。\n官方内置地图(如 island.mpr、官方地图包)已内置保护，移除时自动跳过。")
        self.hide_builtin.toggled.connect(self.refresh_installed_list)
        hide_row.addWidget(self.hide_builtin)
        self.hide_mmx = QCheckBox("隐藏多图包(.mmx)")
        self.hide_mmx.setChecked(False)
        self.hide_mmx.toggled.connect(self.refresh_installed_list)
        hide_row.addWidget(self.hide_mmx)
        hide_row.addStretch(1)
        installed_layout.addLayout(hide_row)

        self.installed_list = QListWidget()
        self.installed_list.setSelectionMode(SEL_EXTENDED)
        installed_layout.addWidget(self.installed_list, 1)

        inst_btn_row = QHBoxLayout()
        btn_remove = QPushButton("移除选中的地图")
        btn_remove.clicked.connect(self.remove_selected_maps)
        btn_refresh_inst = QPushButton("刷新")
        btn_refresh_inst.clicked.connect(self.refresh_installed_list)
        inst_btn_row.addWidget(btn_remove)
        inst_btn_row.addWidget(btn_refresh_inst)
        inst_btn_row.addStretch(1)
        installed_layout.addLayout(inst_btn_row)

        right_splitter.addWidget(installed_box)

        # ----- 底部操作栏 -----
        bottom = QHBoxLayout()
        self.keep_subdir = QCheckBox("保留子文件夹结构(不推荐)")
        btn_refresh = QPushButton("刷新全部")
        btn_refresh.clicked.connect(self.refresh_all)
        bottom.addWidget(self.keep_subdir)
        bottom.addStretch(1)
        bottom.addWidget(btn_refresh)
        root_layout.addLayout(bottom)

    # ---------------------------------------------------------------- 刷新
    def refresh_all(self):
        """从磁盘重新加载配置和地图库"""
        self.versions = MU.load_config(CONFIG_FILE)
        self.refresh_version_table()
        self.refresh_map_tree()
        self.refresh_installed_list()
        self.statusBar().showMessage("已刷新：%d 个版本，config: %s" % (len(self.versions), CONFIG_FILE))

    def refresh_version_table(self):
        self.version_table.setRowCount(0)
        for v in self.versions:
            row = self.version_table.rowCount()
            self.version_table.insertRow(row)
            self.version_table.setItem(row, 0, QTableWidgetItem(v['name']))
            gd_item = QTableWidgetItem(v['gamedir'] or "（未设置，双击或点按钮选择）")
            gd_item.setToolTip(v['gamedir'])
            self.version_table.setItem(row, 1, gd_item)
            game_type = MU.detect_game_type(v['gamedir']) if v['gamedir'] else None
            self.version_table.setItem(row, 2, QTableWidgetItem(MU.type_label(game_type)))
        # 若没有版本，提示
        if not self.versions:
            self.installed_list.clear()
            self.installed_list.addItem("config.ini 中没有任何版本配置，请点击“新增版本”或手动编辑 config.ini。")

    def refresh_map_tree(self):
        self.map_tree.clear()
        if not os.path.isdir(MAPS_DIR):
            item = QTreeWidgetItem(["未找到 maps 文件夹：%s" % MAPS_DIR])
            item.setForeground(0, self.palette().windowText())
            self.map_tree.addTopLevelItem(item)
            return

        for rel in MU.scan_map_folder(MAPS_DIR):
            parts = rel.split(os.sep)
            parent_item = self._ensure_dir_nodes(parts[:-1])
            filename = parts[-1]
            abs_path = os.path.join(MAPS_DIR, rel)
            info = MU.read_map_info(abs_path) or {}
            name = info.get('name') or self._clean_map_name(filename)
            players = str(info['players']) if info.get('players') else '-'

            kind = MU.map_kind(filename)
            kind_label = COMPACT_KIND.get(kind, '')
            edition = None
            if kind == 'task':
                edition = MU.detect_map_edition(abs_path)
                if edition:
                    kind_label += {'yr': '·YR', 'ra2': '·RA2'}.get(edition, '')
            item = QTreeWidgetItem([name, filename, kind_label, players])
            item.setData(0, USER_ROLE, (ROLE_FILE, rel))
            tip = "%s\n文件: %s\n格式: %s\n%s\n路径: %s" % (
                name, rel, kind_label, MU.KIND_DESCRIPTIONS.get(kind, ''), abs_path)
            if edition:
                tip += "\n版本: %s（该任务地图依赖尤里复仇独有内容，原版无法运行）" % MU.edition_label(edition)
            item.setToolTip(0, tip)
            item.setToolTip(1, rel)
            parent_item.addChild(item)

        self.map_tree.expandToDepth(0)

    def _ensure_dir_nodes(self, dirs):
        """在树中逐级找到/创建目录节点，返回最终目录节点"""
        parent = self.map_tree.invisibleRootItem()
        for d in dirs:
            found = None
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.data(0, USER_ROLE) == (ROLE_DIR, d):
                    found = child
                    break
            if found is None:
                found = QTreeWidgetItem([d])
                found.setData(0, USER_ROLE, (ROLE_DIR, d))
                parent.addChild(found)
            parent = found
        return parent

    # ---------------------------------------------------------------- 地图详情
    def on_map_selected(self, current, _previous):
        if current is None:
            return
        role = current.data(0, USER_ROLE)
        if not role or role[0] != ROLE_FILE:
            self.detail_label.setText("在左侧选择地图查看详情")
            return
        rel = role[1]
        abs_path = os.path.join(MAPS_DIR, rel)
        info = MU.read_map_info(abs_path)
        lines = [
            "名称：%s" % current.text(0),
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
        self.detail_label.setText("\n".join(lines))

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
    def selected_versions(self):
        """返回表格中选中的版本字典列表；未选中则返回全部"""
        rows = {i.row() for i in self.version_table.selectedItems()}
        if not rows:
            return list(self.versions)
        return [self.versions[r] for r in sorted(rows) if 0 <= r < len(self.versions)]

    def _selected_single(self):
        """返回表格中第一个选中版本；未选中返回 None"""
        rows = sorted({i.row() for i in self.version_table.selectedItems()})
        if not rows:
            return None
        return self.versions[rows[0]]

    def set_selected_dir(self):
        v = self._selected_single()
        if v is None:
            QMessageBox.information(self, "提示", "请先在表格中选择一个游戏版本。")
            return
        start = v['gamedir'] or APP_DIR
        path = QFileDialog.getExistingDirectory(self, "选择 %s 的游戏根目录" % v['name'], start)
        if not path:
            return
        v['gamedir'] = path
        MU.save_config(self.versions, CONFIG_FILE)
        self.refresh_version_table()
        self.refresh_installed_list()
        self.statusBar().showMessage("已设置 %s 目录: %s" % (v['name'], path))

    def open_selected_dir(self):
        v = self._selected_single()
        if v is None:
            QMessageBox.information(self, "提示", "请先选择一个游戏版本。")
            return
        if v['gamedir'] and os.path.isdir(v['gamedir']):
            self.open_folder(v['gamedir'])
        else:
            QMessageBox.warning(self, "提示", "%s 的目录无效或未设置。" % v['name'])

    def launch_game(self, kind='auto'):
        """启动选中的游戏版本。
        kind: 'auto' 自动选择 / 'ra2' 强制启动 ra2.exe / 'yr' 强制启动 ra2md.exe 或 yuri.exe"""
        v = self._selected_single()
        if v is None:
            QMessageBox.information(self, "提示", "请先在表格中选择一个游戏版本。")
            return
        if not v['gamedir'] or not os.path.isdir(v['gamedir']):
            QMessageBox.warning(self, "无法启动", "%s 的目录无效或未设置。" % v['name'])
            return
        if kind == 'ra2':
            launcher = MU.find_launcher_ra2(v['gamedir'])
        elif kind == 'yr':
            launcher = MU.find_launcher_yr(v['gamedir'])
        else:
            launcher = MU.find_launcher(v['gamedir'])
        if not launcher:
            QMessageBox.warning(self, "无法启动",
                "在 %s 中未找到对应的启动程序。\n\n"
                "原版应包含 ra2.exe；尤里复仇应包含 ra2md.exe 或 yuri.exe。"
                % v['gamedir'])
            return
        # 权限检查：游戏目录不可写且程序未提权时，游戏启动/运行可能失败（如 ra2.exe 权限不足）
        if os.name == 'nt' and '--elevated' not in sys.argv:
            bad = MU.unwritable_dirs([v])
            if bad:
                r = QMessageBox.question(self, "需要管理员权限",
                    "游戏目录没有写入权限，游戏启动或运行时可能报错：\n%s\n\n"
                    "这通常是因为游戏安装在系统保护目录（如 Program Files）。\n\n"
                    "是否以管理员身份重新运行本程序后再启动游戏？\n"
                    "（选“否”将以当前权限直接启动，可能遇到权限不足）"
                    % bad[0])
                if r == MB_YES:
                    if self._request_admin_restart():
                        self.statusBar().showMessage("正在以管理员身份重新启动...")
                        QTimer.singleShot(1500, self.close)
                    else:
                        QMessageBox.warning(self, "无法提权",
                            "无法自动以管理员身份重新启动，\n"
                            "请关闭本程序后，右键本程序选择“以管理员身份运行”。")
                    return
        try:
            subprocess.Popen([launcher], cwd=v['gamedir'])
            self.statusBar().showMessage("正在启动 %s ..." % v['name'])
        except Exception as e:
            QMessageBox.warning(self, "启动失败", "%s\n%s" % (launcher, e))

    def _update_launch_buttons(self):
        """按所选版本目录中实际存在的 exe 启用/禁用两个启动按钮：
        “启动 RA2”看 ra2.exe（备选 game.exe）；
        “启动尤里”看 ra2md.exe / yuri.exe / gamemd.exe。"""
        v = self._selected_single()
        ra2_ok = (v is not None and v['gamedir'] and os.path.isdir(v['gamedir'])
                  and MU.find_launcher_ra2(v['gamedir']))
        yr_ok = (v is not None and v['gamedir'] and os.path.isdir(v['gamedir'])
                 and MU.find_launcher_yr(v['gamedir']))
        self.btn_launch_ra2.setEnabled(bool(ra2_ok))
        self.btn_launch_yr.setEnabled(bool(yr_ok))

    def on_version_double_clicked(self, row, _col):
        self.version_table.selectRow(row)
        self.set_selected_dir()

    def add_version(self):
        name, ok = QInputDialog.getText(self, "新增版本", "请输入版本名称（如：红色警戒2原版）:")
        if not ok or not name.strip():
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
        self.statusBar().showMessage("已新增版本: [%s] %s" % (sec, name))

    def del_version(self):
        rows = sorted({i.row() for i in self.version_table.selectedItems()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "提示", "请先选择要删除的游戏版本。")
            return
        names = "、".join(self.versions[r]['name'] for r in rows)
        if QMessageBox.question(self, "确认删除",
                "确认删除版本：%s\n（仅移除配置，不会删除游戏文件）" % names) != MB_YES:
            return
        for r in rows:
            del self.versions[r]
        MU.save_config(self.versions, CONFIG_FILE)
        self.refresh_version_table()
        self.refresh_installed_list()

    # ---------------------------------------------------------------- 安装/移除
    def selected_map_files(self):
        """收集地图树中选中的文件相对路径；选中的目录节点会展开为其中所有文件"""
        result = set()
        for item in self.map_tree.selectedItems():
            self._collect_files(item, result)
        return sorted(result)

    def _collect_files(self, item, result):
        role = item.data(0, USER_ROLE)
        if role and role[0] == ROLE_FILE:
            result.add(role[1])
            return
        for i in range(item.childCount()):
            self._collect_files(item.child(i), result)

    def install_maps(self):
        targets = self.selected_versions()
        valid_targets = [v for v in targets if v['gamedir'] and os.path.isdir(v['gamedir'])]
        if not valid_targets:
            QMessageBox.warning(self, "无法安装", "没有有效的目标游戏目录。\n请在“游戏版本”表格中先设置游戏目录。")
            return

        files = self.selected_map_files()
        if not files:
            QMessageBox.information(self, "提示", "请先在左侧地图库中选择要安装的地图（可多选，也可选整个文件夹）。")
            return

        keep_subdir = self.keep_subdir.isChecked()
        copied, skipped = 0, 0
        skipped_detail = []
        recorded = {}  # gamedir -> [相对路径列表]

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
                    QMessageBox.warning(self, "复制失败", "%s -> %s\n%s%s" % (rel, v['gamedir'], e, tip))

        # 记录本次安装的地图，用于区分游戏自带地图
        for gd, rels in recorded.items():
            MU.record_installed_maps(gd, rels, INSTALLED_DB)
        self.refresh_installed_list()
        msg = "安装完成：成功复制 %d 个文件，跳过 %d 个。" % (copied, skipped)
        if skipped_detail:
            msg += "\n\n跳过明细：\n" + "\n".join(skipped_detail[:10])
            if len(skipped_detail) > 10:
                msg += "\n... 等共 %d 项" % len(skipped_detail)
        QMessageBox.information(self, "安装结果", msg)
        self.statusBar().showMessage(msg.split("\n")[0])

    def refresh_installed_list(self):
        self._update_launch_buttons()
        self.installed_list.clear()
        rows = sorted({i.row() for i in self.version_table.selectedItems()})
        if not rows:
            self.installed_list.addItem("（请在上方选择一个游戏版本，查看其目录下的地图）")
            return
        v = self.versions[rows[0]]
        gamedir = v['gamedir']
        if not gamedir or not os.path.isdir(gamedir):
            self.installed_list.addItem("（%s 的目录无效或未设置）" % v['name'])
            return
        maps = MU.list_game_maps(gamedir)
        if not maps:
            self.installed_list.addItem("（该目录下没有找到地图）")
            return
        hide_builtin = self.hide_builtin.isChecked()
        hide_mmx = self.hide_mmx.isChecked()
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
            src_label = {
                'builtin': '官方内置',
                'installed': '本工具安装',
                'library': '与地图库同名',
                'other': '游戏自带/未知',
            }[src]
            tag = MU.KIND_LABELS.get(kind, '')
            edition = None
            if kind == 'task':
                edition = MU.detect_map_edition(p)
                if edition:
                    tag = "%s·%s" % (tag, MU.edition_label(edition))
            it = QListWidgetItem("【%s】%s    [%s]  %s" % (src_label, name, os.path.basename(p), tag))
            it.setData(USER_ROLE, p)
            tip = "%s\n来源：%s\n%s" % (
                p, src_label, MU.KIND_DESCRIPTIONS.get(kind, ''))
            if edition:
                tip += "\n版本：%s（该任务地图依赖尤里复仇独有内容，原版无法运行）" % MU.edition_label(edition)
            it.setToolTip(tip)
            self.installed_list.addItem(it)
        if hidden:
            self.installed_list.addItem("……已隐藏 %d 个游戏自带/未知来源的地图（取消上方勾选可显示）" % hidden)
        if hidden_mmx:
            self.installed_list.addItem("……已隐藏 %d 个多地图包（取消上方勾选可显示）" % hidden_mmx)

    def remove_selected_maps(self):
        items = self.installed_list.selectedItems()
        if not items:
            QMessageBox.information(self, "提示", "请先在下方的已安装列表中选择要移除的地图（可多选）。")
            return
        paths = [it.data(USER_ROLE) for it in items]
        paths = [p for p in paths if p and os.path.isfile(p)]
        if not paths:
            QMessageBox.warning(self, "提示", "所选文件不存在，可能已被删除。")
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
            QMessageBox.information(self, "已保护官方地图",
                "以下 %d 个文件是官方内置地图，已自动跳过（不会被删除）：\n\n%s" %
                (len(protected), "\n".join(os.path.basename(p) for p in protected)))
        if not targets:
            self.statusBar().showMessage("所选文件均为官方内置地图，未删除任何文件。")
            return
        names = "\n".join(os.path.basename(p) for p in targets)
        if QMessageBox.question(self, "确认移除",
                "确认从游戏目录删除这 %d 个地图文件？\n\n%s\n\n"
                "注意：删除后无法恢复。若文件不在地图库(maps)中且不是本工具安装的，"
                "删除前请自行确认其来源。" % (len(targets), names)) != MB_YES:
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
                QMessageBox.warning(self, "删除失败", "%s\n%s%s" % (p, e, tip))
        if removed_rel:
            MU.forget_installed_maps(gamedir, removed_rel, INSTALLED_DB)
        self.refresh_installed_list()
        msg = "已移除 %d 个地图文件" % deleted
        if failed:
            msg += "，%d 个失败" % failed
        self.statusBar().showMessage(msg)

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
        r = QMessageBox.question(self, "需要管理员权限",
            "以下游戏目录没有写入权限，安装/移除地图将失败：\n\n%s\n\n"
            "这通常是因为游戏安装在系统保护目录（如 Program Files）。\n\n"
            "是否以管理员身份重新运行本程序？\n"
            "（若游戏装在 D 盘等普通位置，可点“否”继续使用）"
            % "\n".join(bad))
        if r != MB_YES:
            return
        if self._request_admin_restart():
            self.statusBar().showMessage("正在以管理员身份重新启动...")
            QTimer.singleShot(1500, self.close)
        else:
            QMessageBox.warning(self, "无法提权",
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
        if os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def show_about(self):
        QMessageBox.about(self, "关于",
            "红警2多版本地图管理器\n\n"
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
            "界面库：%s\n"
            "配置文件：config.ini\n"
            "地图库：%s" % ("PyQt6" if PYQT6 else "PyQt5", MAPS_DIR))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("红警2多版本地图管理器")
    win = MapManagerWindow()
    win.show()
    if hasattr(app, 'exec_'):
        sys.exit(app.exec_())
    else:
        sys.exit(app.exec())


if __name__ == "__main__":
    main()
