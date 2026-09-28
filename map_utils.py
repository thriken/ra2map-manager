#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""红警2多版本地图管理器 - 公共工具模块

被图形界面版(MapManagerGUI.py)使用。
"""

import os
import re
import json
import shutil
import configparser

# 常见地图文件扩展名（大小写不敏感）
MAP_EXTENSIONS = ('.mpr', '.map', '.mmx', '.yrm')

# 配置文件
CONFIG_FILE = 'config.ini'
LEGACY_JSON_FILE = 'ra2_paths.json'

# 首次运行自动生成的默认配置模板
DEFAULT_CONFIG = """[ra2]
name=红色警戒2原版
gamedir=

[ra2md]
name=红色警戒2：尤里的复仇
gamedir=

[ra2glory]
name=红色警戒2共和国之辉
gamedir=

[ra2xx]
name=红色警戒2xxMOD
gamedir=
"""


def ensure_default_config(config_file=CONFIG_FILE, maps_dir=None):
    """确保配置文件与地图库目录存在。

    首次运行（尤其是打包后的 exe）没有 config.ini / maps 时，
    自动生成默认配置和目录，避免出现空配置状态。
    """
    if not os.path.exists(config_file):
        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                f.write(DEFAULT_CONFIG)
        except OSError:
            pass
    if maps_dir and not os.path.isdir(maps_dir):
        try:
            os.makedirs(maps_dir)
        except OSError:
            pass


def is_map_file(filename):
    """判断文件名是否为地图文件"""
    return filename.lower().endswith(MAP_EXTENSIONS)


# 地图编辑器生成的占位名称，视为"无名称"
_PLACEHOLDER_NAMES = {'no name', 'noname', 'untitled', 'no-name'}


def read_map_info(filepath):
    """尝试解析地图文件头部的名称/玩家数等信息。

    支持两种格式：
    - 标准地图（含 [Basic] 段，如 .mpr/.map/.yrm）：返回 name/players
    - MultiMap 多地图包（[MultiMaps] 结构，如 .mmx）：返回 players（名称回退用文件名）

    返回 dict（含 name / players 等字段）或 None（非地图文件 / 解析失败）。
    """
    if not os.path.isfile(filepath) or not is_map_file(filepath):
        return None

    content = None
    for enc in ('utf-8-sig', 'utf-8', 'gb18030', 'latin-1'):
        try:
            with open(filepath, 'r', encoding=enc, errors='ignore') as f:
                content = f.read(262144)  # 读前 256KB，足以覆盖头部各信息段
            break
        except OSError:
            continue
    if not content:
        return None

    info = {}

    # 标准地图：[Basic] 段
    m = re.search(r'\[Basic\]([^\[]*)', content)
    if m:
        section = m.group(1)
        name_m = re.search(r'^\s*Name\s*=\s*(.+?)\s*$', section, re.M)
        if name_m:
            name = name_m.group(1).strip().strip('"').strip("'")
            # 忽略编辑器的占位名（如 "No name"）
            if name.lower() not in _PLACEHOLDER_NAMES:
                info['name'] = name
        player_m = re.search(r'^\s*Player\s*=\s*(\d+)\s*$', section, re.M)
        if player_m:
            info['players'] = int(player_m.group(1))

    # MultiMap 多地图包（.mmx）：[MultiMaps] -> 1=子地图名 -> [子地图] MinPlayers/MaxPlayers
    mm = re.search(r'\[MultiMaps\]([^\[]*)', content)
    if mm:
        entry = re.search(r'^\s*\d+\s*=\s*(\S+?)\s*$', mm.group(1), re.M)
        if entry:
            sub = entry.group(1)
            sub_m = re.search(r'\[' + re.escape(sub) + r'\]([^\[]*)', content)
            if sub_m:
                sec = sub_m.group(1)
                max_p = re.search(r'^\s*MaxPlayers\s*=\s*(\d+)\s*$', sec, re.M)
                min_p = re.search(r'^\s*MinPlayers\s*=\s*(\d+)\s*$', sec, re.M)
                if max_p:
                    info['players'] = int(max_p.group(1))
                elif min_p:
                    info['players'] = int(min_p.group(1))

    return info or None


def display_name(filepath):
    """地图的展示名称：优先取文件内 Name=，失败则回退到文件名"""
    info = read_map_info(filepath)
    if info and info.get('name'):
        return info['name']
    return os.path.splitext(os.path.basename(filepath))[0]


def scan_map_folder(root):
    """递归扫描地图库文件夹，返回所有地图文件的相对路径列表（已排序）"""
    result = []
    if not os.path.isdir(root):
        return result
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            if is_map_file(fn):
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                result.append(rel)
    return result


def list_game_maps(gamedir):
    """列出游戏目录中已安装的地图（根目录 + maps 子目录），返回绝对路径列表"""
    result = []
    if not os.path.isdir(gamedir):
        return result
    dirs = [gamedir]
    maps_sub = os.path.join(gamedir, 'maps')
    if os.path.isdir(maps_sub):
        dirs.append(maps_sub)
    for d in dirs:
        try:
            for fn in os.listdir(d):
                if is_map_file(fn):
                    result.append(os.path.join(d, fn))
        except OSError:
            continue
    return sorted(result)


def detect_game_type(gamedir):
    """识别游戏目录类型：
    - 'yr'    : 尤里复仇及其MOD
    - 'glory' : 共和国之辉（RA2 MOD，通过 glory.xmlf / Ecache01.mix+expand01.mix 标记识别）
    - 'ra2'   : 原版RA2及其MOD
    - None    : 目录无效
    """
    if not os.path.isdir(gamedir):
        return None
    for marker in ('gamemd.exe', 'yuri.dll'):
        if os.path.exists(os.path.join(gamedir, marker)):
            return 'yr'
    if (os.path.exists(os.path.join(gamedir, 'glory.xmlf')) or
            (os.path.exists(os.path.join(gamedir, 'Ecache01.mix')) and
             os.path.exists(os.path.join(gamedir, 'expand01.mix')))):
        return 'glory'
    return 'ra2'


def type_label(game_type):
    """类型显示文本"""
    return {
        'yr': '尤里复仇/MOD',
        'glory': '共和国之辉',
        'ra2': '原版/MOD',
    }.get(game_type, '未设置')


# 各类型目录的启动程序候选（按优先级）
RA2_LAUNCHERS = ('ra2.exe', 'game.exe')
YR_LAUNCHERS = ('ra2md.exe', 'yuri.exe', 'gamemd.exe')


def find_launcher(gamedir):
    """在游戏目录中查找可启动游戏的程序，返回绝对路径；未找到返回 None。

    规则：
    - 原版（ra2）：ra2.exe（备选 game.exe）
    - 尤里复仇/MOD（yr）：ra2md.exe / yuri.exe（MOD 备选 gamemd.exe）
    """
    if not os.path.isdir(gamedir):
        return None
    # 有 ra2md.exe 即判定为尤里复仇（部分精简版无 gamemd.exe / yuri.dll，
    # detect_game_type 会误判为原版，导致错误启动 ra2.exe）
    if os.path.isfile(os.path.join(gamedir, 'ra2md.exe')):
        candidates = YR_LAUNCHERS
    else:
        game_type = detect_game_type(gamedir)
        candidates = YR_LAUNCHERS if game_type == 'yr' else RA2_LAUNCHERS
    for exe in candidates:
        p = os.path.join(gamedir, exe)
        if os.path.isfile(p):
            return p
    return None


def find_launcher_ra2(gamedir):
    """在目录中查找原版红警2启动程序（ra2.exe，备选 game.exe）"""
    if not os.path.isdir(gamedir):
        return None
    for exe in RA2_LAUNCHERS:
        p = os.path.join(gamedir, exe)
        if os.path.isfile(p):
            return p
    return None


def find_launcher_yr(gamedir):
    """在目录中查找尤里复仇启动程序（ra2md.exe / yuri.exe / gamemd.exe）"""
    if not os.path.isdir(gamedir):
        return None
    for exe in YR_LAUNCHERS:
        p = os.path.join(gamedir, exe)
        if os.path.isfile(p):
            return p
    return None


def map_kind(filename):
    """返回地图格式类型。

    - 'task'           : .map  任务地图（原版与尤里复仇通用，仅单人战役）
    - 'ra2_skirmish'   : .mpr  原版遭遇战地图（由 ra2.exe 运行，含 ra2.exe 的尤里复仇目录也可用）
    - 'yr_skirmish'    : .yrm  尤里复仇遭遇战地图（YR/MOD 专属）
    - 'multimap'       : .mmx  多地图包（MultiMap）
    - None             : 无法识别
    """
    return {
        '.map': 'task',
        '.mpr': 'ra2_skirmish',
        '.yrm': 'yr_skirmish',
        '.mmx': 'multimap',
    }.get(os.path.splitext(filename)[1].lower())


# 地图格式显示标签
KIND_LABELS = {
    'task': '任务地图(.map)',
    'ra2_skirmish': '原版遭遇战(.mpr)',
    'yr_skirmish': '尤里遭遇战(.yrm)',
    'multimap': '多地图包(.mmx)',
}

# 地图格式说明（用于界面提示）
KIND_DESCRIPTIONS = {
    'task': '原版与尤里复仇通用的任务地图，仅用于单人战役，不能作为遭遇战对战地图',
    'ra2_skirmish': 'RA2原版遭遇战地图，由 ra2.exe 加载；含 ra2.exe 的尤里复仇目录同样可用',
    'yr_skirmish': '尤里复仇专属遭遇战地图，原版红警2无法识别',
    'multimap': '多地图包（MultiMap），内含多个遭遇战地图，原版与尤里复仇通用',
}


# ---------------------------------------------------------------- 地图版本(RA2/YR)检测
# YR 独有对象段名（小写）：原版 RA2 中不存在这些单位/建筑/武器，
# 因此 RA2 地图不会引用它们。命中即视为 YR 专属。
_YR_ONLY_SECTIONS = frozenset({
    'boris',           # 鲍里斯（英雄）
    'yuriprime',       # 尤里X
    'mastermind',      # 心灵控制车
    'geneticmutator',  # 基因突变器（建筑）
    'psychicdominator',  # 心灵终结仪（建筑）
    'psychicr',        # 心灵感应器（建筑）
    'clonepod',        # 复制中心（建筑）
    'tort',            # 瘫痪坦克
    'boomer',          # 尤里飞碟
    'slv',             # 奴隶矿车
    'desolator',       # 辐射工兵
    'flakt',           # 盖特坦克
    'gattle',          # 盖特机炮（建筑）
    'gattling',        # 盖特机炮（建筑，别名）
    'aggattling',      # 盖特机炮（自定义升级段）
    'mirv',            # MIRV 导弹车
    'radarbuggy',      # 雷达车
    'rob',             # 遥控坦克
    'shadow',          # 暗影装甲车
    'amcv',            # 幻影坦克
    'lashtank',        # 拉斯塔坦克
    'bfrt',            # 战斗要塞
    'brute',           # 尤里新兵
    'yuri',            # 尤里（单位）
    'pyrot',           # 喷火坦克
    'borischemmissile',  # 鲍里斯化学导弹（自定义武器段）
    'blackhawkcannon',   # 黑鹰机炮（自定义武器段）
})

# YR 独有国家（小写）：原版 RA2 的 9 国之外新增的国家
_YR_ONLY_COUNTRIES = frozenset({
    'canada', 'chile', 'israel', 'italy', 'ukraine',
    'croatia', 'angola', 'argentina',
})

# 版本显示标签
EDITION_LABELS = {
    'yr': 'YR专属',
    'ra2': 'RA2专属',
}


def edition_label(edition):
    """版本标签文本（edition: 'yr'/'ra2'/None）"""
    return EDITION_LABELS.get(edition, '通用')


def detect_map_edition(filepath):
    """通过地图文件内容判断其所属游戏版本（主要用于 .map 任务地图）。

    原理：YR 是 RA2 的超集，RA2 地图不引用任何 YR 独有对象/国家；
    因此只要内容中检测到 YR 独有对象段名或 [Countries] 中的 YR 独有国家，
    即可判定为 YR 专属（FinalAlert2 正是据此提示"需要尤里的复仇"）。

    返回：
    - 'yr'  : YR 专属，原版 RA2 无法运行
    - 'ra2' : 原版专属（当前不产生该值，YR 为超集，保留供扩展）
    - None  : 无法确定，按通用任务地图处理
    """
    if not os.path.isfile(filepath):
        return None
    content = None
    for enc in ('utf-8-sig', 'utf-8', 'gb18030', 'latin-1'):
        try:
            with open(filepath, 'r', encoding=enc, errors='ignore') as f:
                content = f.read(2 * 1024 * 1024)  # 读前 2MB 足以覆盖各信息段
            break
        except OSError:
            continue
    if not content:
        return None

    # 1) YR 独有对象段名（如 [BORIS]、[BFRT]、[BRUTE]）
    for m in re.finditer(r'^\s*\[([A-Za-z0-9_]+)\]\s*$', content, re.M):
        if m.group(1).lower() in _YR_ONLY_SECTIONS:
            return 'yr'

    # 2) [Countries] 段中出现 YR 独有国家
    cm = re.search(r'\[Countries\]([^\[]*)', content)
    if cm:
        for line in cm.group(1).splitlines():
            mm = re.match(r'^\s*\d+\s*=\s*([A-Za-z0-9_]+)\s*$', line)
            if mm and mm.group(1).lower() in _YR_ONLY_COUNTRIES:
                return 'yr'
    return None


def can_install_to(filename, game_type, src_path=None):
    """判断某地图文件能否安装到指定类型(game_type: 'ra2'/'glory'/'yr'/None)的游戏目录。

    兼容规则：
    - .map  任务地图     -> 默认通用；提供 src_path 时可检测内容判定 YR/RA2 专属
    - .mpr  原版遭遇战   -> 原版/尤里复仇/MOD 目录均可（尤里复仇目录常自带 ra2.exe，
                            .mpr 由 ra2.exe 运行）
    - .yrm  尤里遭遇战   -> 仅尤里复仇/MOD 目录
    - .mmx  多地图包     -> 原版与尤里复仇均可
    """
    kind = map_kind(filename)
    if kind == 'task':
        if src_path:
            edition = detect_map_edition(src_path)
            if edition == 'yr':
                return game_type == 'yr'
            if edition == 'ra2':
                return game_type != 'yr'
        return True
    if kind == 'yr_skirmish':
        return game_type == 'yr'
    # .mpr / .mmx：所有目录均可接收
    # （.mpr 原版遭遇战由 ra2.exe 运行；尤里复仇目录通常同时含 ra2.exe 与 ra2md.exe）
    return True


def load_config(config_file=CONFIG_FILE):
    """读取 config.ini，返回 [{'section','name','gamedir'}]"""
    cp = configparser.ConfigParser()
    cp.read(config_file, encoding='utf-8')
    versions = []
    for sec in cp.sections():
        versions.append({
            'section': sec,
            'name': cp.get(sec, 'name', fallback=sec),
            'gamedir': cp.get(sec, 'gamedir', fallback='').strip(),
        })
    return versions


def unwritable_dirs(versions):
    """返回没有写入权限（需管理员权限才能写入）的游戏目录列表。

    游戏安装在 Program Files 等系统保护目录时，普通权限下安装/移除
    地图会失败；GUI 据此提示用户以管理员身份运行本程序。
    """
    bad = []
    for v in versions:
        gd = v['gamedir']
        if gd and os.path.isdir(gd) and not os.access(gd, os.W_OK):
            bad.append(gd)
    return bad


def save_config(versions, config_file=CONFIG_FILE):
    """把版本列表写回 config.ini（保留文件中用户手工添加的额外字段）"""
    cp = configparser.ConfigParser()
    cp.read(config_file, encoding='utf-8')

    # 删除已不存在的版本
    existing = {v['section'] for v in versions}
    for sec in list(cp.sections()):
        if sec not in existing:
            cp.remove_section(sec)

    # 更新/新增
    for v in versions:
        if not cp.has_section(v['section']):
            cp.add_section(v['section'])
        cp.set(v['section'], 'name', v['name'])
        cp.set(v['section'], 'gamedir', v['gamedir'])

    with open(config_file, 'w', encoding='utf-8') as f:
        cp.write(f)


def migrate_legacy_json(config_file=CONFIG_FILE):
    """把旧版 ra2_paths.json 中的路径导入 config.ini，返回导入数量。

    兼容早期命令行版使用 json 配置的历史数据。
    """
    legacy_file = os.path.join(os.path.dirname(os.path.abspath(config_file)), 'ra2_paths.json')
    if not os.path.exists(legacy_file):
        return 0
    try:
        with open(legacy_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return 0

    versions = load_config(config_file)
    used = {v['section'] for v in versions}
    imported = 0

    base = ['ra2', 'ra2md', 'ra2glory']
    counter = 0

    for item in data.get('paths', []):
        path = str(item.get('path', '')).strip()
        if not path or not os.path.isdir(path):
            continue
        if any(v['gamedir'].lower() == path.lower() for v in versions):
            continue

        # 优先填入空的 gamedir，否则新增 section
        target = None
        for v in versions:
            if not v['gamedir']:
                target = v
                break
        if target is None:
            while True:
                sec = base[counter] if counter < len(base) else 'ra2mod' + str(counter - len(base) + 1)
                counter += 1
                if sec not in used:
                    break
            target = {'section': sec, 'name': sec, 'gamedir': ''}
            versions.append(target)
            used.add(sec)

        target['gamedir'] = path
        imported += 1

    if imported:
        save_config(versions, config_file)
    return imported


def copy_map_to_game(src_path, gamedir, keep_subdir=False, rel_path=None):
    """把单个地图复制到游戏目录，返回目标路径；失败抛异常。

    src_path : 源地图绝对路径
    gamedir  : 目标游戏根目录
    keep_subdir : True 时按 rel_path 保留子目录结构（游戏通常不识别，默认 False）
    rel_path : 地图在地图库中的相对路径（keep_subdir 时需要）
    """
    if keep_subdir and rel_path:
        dst = os.path.join(gamedir, rel_path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
    else:
        dst = os.path.join(gamedir, os.path.basename(src_path))
    shutil.copy2(src_path, dst)
    return dst


# ---------------------------------------------------------------- 安装记录
# 记录"通过本工具安装到游戏目录"的地图，用于在列表中区分游戏自带地图。
INSTALLED_DB = 'installed_maps.json'


def _load_installed_db(db_file=INSTALLED_DB):
    """读取安装记录文件 -> {gamedir_abs_lower: [相对路径...]}"""
    if not os.path.exists(db_file):
        return {}
    try:
        with open(db_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_installed_db(db, db_file=INSTALLED_DB):
    try:
        with open(db_file, 'w', encoding='utf-8') as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def load_installed_maps(gamedir, db_file=INSTALLED_DB):
    """返回该游戏目录下由本工具安装过的地图相对路径集合（已统一大小写）"""
    if not gamedir:
        return set()
    key = os.path.normcase(os.path.abspath(gamedir))
    db = _load_installed_db(db_file)
    return set(os.path.normcase(p) for p in db.get(key, []))


def record_installed_maps(gamedir, relpaths, db_file=INSTALLED_DB):
    """记录本工具新安装的地图（相对游戏目录的路径列表）"""
    key = os.path.normcase(os.path.abspath(gamedir))
    db = _load_installed_db(db_file)
    cur = {os.path.normcase(os.path.normpath(p)) for p in db.get(key, [])}
    cur.update(os.path.normcase(os.path.normpath(p)) for p in relpaths)
    db[key] = sorted(cur)
    _save_installed_db(db, db_file)


def forget_installed_maps(gamedir, relpaths, db_file=INSTALLED_DB):
    """从安装记录中移除（如用户已删除这些地图文件）"""
    key = os.path.normcase(os.path.abspath(gamedir))
    db = _load_installed_db(db_file)
    cur = {os.path.normcase(os.path.normpath(p)) for p in db.get(key, [])}
    targets = {os.path.normcase(os.path.normpath(p)) for p in relpaths}
    cur.difference_update(targets)
    if cur:
        db[key] = sorted(cur)
    else:
        db.pop(key, None)
    _save_installed_db(db, db_file)


# ---------------------------------------------------------------- 内置地图保护
# 已知的官方地图文件名（小写，不带路径）。
# 说明：红警2/尤里的复仇本体自带的遭遇战与战役地图实际打包在
# multimd.mix、maps01~12.mix 等数据包内，游戏目录中通常不会出现这些独立文件；
# 这里主要收录"官方地图包"等会以独立文件形式发布的官方地图，避免被误删。
BUILTIN_MAP_FILES = frozenset([
    # 原版 RA2 官方遭遇战地图（标准文件名）
    'island.mpr',
    'countr01.mpr', 'countr02.mpr', 'countr03.mpr', 'countr04.mpr',
    'countr05.mpr', 'countr06.mpr', 'countr07.mpr', 'countr08.mpr',
    # YR 官方地图包（Westwood 2002 年发布的 13 张地图，.yro 分发格式）
    'deepfrze.yro', 'mojosprt.yro', 'monsterm.yro', 'riverram.yro',
    'sinkswim.yro', 'transylv.yro', 'irvineca.yro', 'moonpatr.yro',
    'unrepent.yro', 'isleland.yro', 'ice_age.yro', 'crctbrd.yro',
    'highexpr.yro',
])


# 游戏/MOD 核心数据文件（非地图格式，但同样受保护、禁止删除）。
# 这些文件不会出现在地图列表中（扩展名不在地图格式内），加入清单作为双重保险，
# 防止将来扫描范围变化时误删而损坏游戏/MOD。
BUILTIN_CORE_FILES = frozenset([
    # 共和国之辉 MOD 核心文件
    'ecache01.mix',   # 共和国之辉缓存包
    'expand01.mix',   # MOD 扩展包
    'glory.xmlf',     # 共和国之辉专属数据
    'ra2.csf',        # 共和国之辉文本/语言资源
])


def is_builtin_map(filename):
    """判断文件是否为受保护的官方/核心文件（不区分大小写）。

    包含两类：
    - BUILTIN_MAP_FILES：官方地图文件（地图格式）
    - BUILTIN_CORE_FILES：游戏/MOD 核心数据文件（非地图，同样不可删除）
    """
    base = os.path.basename(filename).lower()
    return base in BUILTIN_MAP_FILES or base in BUILTIN_CORE_FILES


def classify_game_map(gamedir, filepath, maps_dir=None, db_file=INSTALLED_DB):
    """判断游戏目录中某地图文件的来源，用于区分"游戏自带"与"自己安装的"。

    - 'builtin'   : 文件名命中官方内置清单（官方地图，应受保护、禁止删除）
    - 'installed' : 本工具安装过（有安装记录）
    - 'library'   : 与地图库(maps 文件夹)中某文件同名，可能是手动从库复制安装
    - 'other'     : 其他（通常是游戏自带未知地图 / MOD 内置地图 / 手动拷贝的第三方地图）
    """
    if is_builtin_map(filepath):
        return 'builtin'
    rel = os.path.normcase(os.path.normpath(os.path.relpath(filepath, gamedir)))
    if rel in load_installed_maps(gamedir, db_file):
        return 'installed'
    if maps_dir and os.path.isdir(maps_dir):
        base_lower = os.path.basename(filepath).lower()
        for lib_rel in scan_map_folder(maps_dir):
            if os.path.basename(lib_rel).lower() == base_lower:
                return 'library'
    return 'other'
