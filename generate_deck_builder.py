# -*- coding: utf-8 -*-
"""
组卡器（卡组编辑器）生成脚本
============================
根据 A.RA.YA/MasterdataBase/ 中的游戏 masterdata 生成一个 HTML 卡组编辑器。
复用 generate_card_list.py 的数据层（build_lookups / build_entries），不改动其文件。

运行方式（在 localdb conda 环境下）：
    conda run -n localdb python localDB/generate_deck_builder.py
然后用浏览器打开生成的 localDB/deck_builder.html。
（也可运行 build_all.py 一键更新列表 + 组卡器两个 HTML。）

规则要点：
  * 一套卡组 = 最多 5 张 Legendary 卡（gradeType==1）+ 最多 20 张其他卡；每个 uniqueId 只能 1 张。
  * 卡组分前衛 / 後衛：前衛只允许 Type 1-4，後衛只允许 Type 5-7（开关切换）。
  * 选卡列表只展示 图 / GvgSkill / GvgAutoSkill；Legendary 与其他卡分两列表，均按更新顺序（新→旧），
    其他卡中 Ultimate（gradeType==2）排在普通卡之前。
  * 统计：類別 / 属性 数量；Mt/An/Ba（卡数 + 总标记数）/EH/SD/MN/CT 数量；五种被动技能数量；
    四种被动技能（除 効果範囲+1）的逐等级数量。「副」级别 = 罗马数字 -1（保留「+」）。
  * 卡组唯一字符串：前/后卫 + 排序后的 (uniqueId.cardType) 列表，base64 编码；可一键还原。
所有文字保留日文原文，不做翻译。
"""

import os
import re
import html

from generate_card_list import (
    build_lookups, build_entries,
    CARD_ICON_URL, CARD_TYPE_LABEL, ATTRIBUTE_LABEL,
    FEATURE_DEFS, GA_DEFS, build_dropdown, fmt, stat_flags,
)
import card_markers

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(SCRIPT_DIR, "deck_builder.html")

# ---------------------------------------------------------------------------
# 被动技能（GvgAuto）等级 / 标记解析
# ---------------------------------------------------------------------------
ROMAN_VAL = {"Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5,
             "Ⅵ": 6, "Ⅶ": 7, "Ⅷ": 8, "Ⅸ": 9, "Ⅹ": 10}
VAL_ROMAN = {v: k for k, v in ROMAN_VAL.items()}
RE_LV = re.compile(r"([Ⅰ-Ⅹ])(\++)?\s*$")   # 末尾罗马数字 + 任意个「+」(Ⅴ+/Ⅴ++ …)

# 四种有等级的被动（按名称识别）；効果範囲+1 无等级，单独计数
PASSIVE_KEYS = [
    ("dmgup", "ダメージUP"), ("supup", "支援UP"),
    ("healup", "回復UP"), ("ptup", "獲得マッチPtUP"),
]

# Gvg 技能里 Mt/An/Ba 标记短语 + 蓄積回数
RE_MT = re.compile(r"次の攻撃時にダメージが[0-9.]+%アップするスタック")
RE_AN = re.compile(r"次の支援/妨害時に支援/妨害効果が[0-9.]+%アップするスタック")
RE_BA = re.compile(r"次の被ダメージ時に被ダメージを[0-9.]+%ダウンさせるスタック")
RE_KAI = re.compile(r"(\d+)回蓄積")


def lv_label(value, plus):
    """等级数值 + 「+」标记 -> 显示标签（如 5,'+' -> 'Ⅴ+'；0 -> '0'）。"""
    roman = VAL_ROMAN.get(value, str(value)) if value >= 1 else "0"
    return roman + plus


def passive_levels(ga_skill):
    """从 GvgAuto 技能名解析四种被动的 (code, 等级标签)。「副」段落等级 = 罗马数字 -1（保留 +）。"""
    if not ga_skill:
        return []
    name = ga_skill.get("name", "") or ""
    m = RE_LV.search(name)
    if not m:
        return []
    base = ROMAN_VAL[m.group(1)]
    plus = m.group(2) or ""
    out = []
    for code, jp in PASSIVE_KEYS:
        if jp not in name:
            continue
        is_fuku = any((jp in seg and "副" in seg) for seg in name.split("/"))
        value = base - 1 if is_fuku else base
        out.append((code, lv_label(value, plus)))
    return out


def stack_count(gvg_skill, phrase_re):
    """某类标记（Mt/An/Ba）的总蓄積回数：每个标记短语后最近的「N回蓄積」。"""
    if not gvg_skill:
        return 0
    desc = gvg_skill.get("desc", "") or ""
    total = 0
    for m in phrase_re.finditer(desc):
        k = RE_KAI.search(desc, m.end())
        total += int(k.group(1)) if k else 1
    return total


# ---------------------------------------------------------------------------
# GvgSkill -> 战斗图标（目标数 / 数值变动 / 特效 / 标记），仅用于卡组编辑器
# ---------------------------------------------------------------------------
SKILL_ICON = "assets/Sprite/BattleIconSkillImg%03d.png"
TGT_ICON = "assets/Sprite/BattleIconTargetNumberImg%03d%03d.png"  # (max, min)

# 四主属性（攻防/特攻防）单图标：上/下
MAIN_UP = {"pa": 1, "pd": 2, "ma": 3, "md": 4}
MAIN_DN = {"pa": 5, "pd": 6, "ma": 7, "md": 8}
MAIN_ORDER = ["pa", "pd", "ma", "md"]   # 普通攻-普通防-属性攻(Sp.ATK)-属性防(Sp.DEF)
# 两主属性同向组合图标
COMBO_UP = {frozenset(["pa", "pd"]): 39, frozenset(["pa", "ma"]): 40, frozenset(["pa", "md"]): 41,
            frozenset(["pd", "ma"]): 42, frozenset(["ma", "md"]): 43, frozenset(["pd", "md"]): 44}
COMBO_DN = {k: v + 6 for k, v in COMBO_UP.items()}
# 属性（火水風光闇）攻防图标：base + (攻0/防2) + (上0/下1)
ELEM_BASE = {1: 18, 2: 22, 3: 26, 4: 30, 5: 34}
ELEM_CHAR = {"火": 1, "水": 2, "風": 3, "光": 4, "闇": 5}

RE_TAI = re.compile(r"(\d+)(?:[～〜](\d+))?体")
RE_ELEM = re.compile(r"([火水風光闇])属性(攻撃力|防御力)")
RE_ET = re.compile(r"次の回復時に回復効果が[0-9.]+%アップするスタック")  # Et: 自身下次回复量↑
RE_MAXHP = re.compile(r"最大HP[^。]*アップ")
RE_SELFHEAL = re.compile(r"自身のHP[^。]*回復")   # 012: 造成伤害同时回复自身HP
# 同句内 HP…回復（含 大/特大 回復；不会匹配 MP回復）。前衛=自身回复 / 後衛=味方回复
RE_HP_HEAL = re.compile(r"HP[^。]*?回復")


def target_icon(desc):
    """技能首个「N(～M)体」-> 目标数图标 (max,min)。"""
    m = RE_TAI.search(desc)
    if not m:
        return ""
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    if hi > 4:
        return ""
    return TGT_ICON % (hi, lo)


def _dir_after(desc, start):
    """从 start 到本句末，最近的 アップ(+)/ダウン(-)；都没有返回 None。"""
    end = desc.find("。", start)
    seg = desc[start:(end if end != -1 else len(desc))]
    up = seg.find("アップ")
    dn = seg.find("ダウン")
    if up == -1 and dn == -1:
        return None
    if dn == -1:
        return "+"
    if up == -1:
        return "-"
    return "+" if up < dn else "-"


def gvg_battle_icons(gvg_skill, fg):
    """返回 (目标数图标, 数值行, 特效行, 标记行)；后三者为图标路径列表。"""
    desc = (gvg_skill.get("desc", "") if gvg_skill else "") or ""
    fgs = set(fg)

    # —— 数值行（2.1）：主属性(含组合) -> 属性攻防 -> 最大HP ——
    stat = []
    flags = stat_flags(desc)
    dirs = {}
    for s in MAIN_ORDER:
        if s + "+" in flags:
            dirs[s] = "+"
        elif s + "-" in flags:
            dirs[s] = "-"
    for sign, combo_map, single_map in (("+", COMBO_UP, MAIN_UP), ("-", COMBO_DN, MAIN_DN)):
        grp = [s for s in MAIN_ORDER if dirs.get(s) == sign]
        i = 0
        while i + 1 < len(grp):           # 同向两两组合，优先组合图标
            stat.append(combo_map[frozenset([grp[i], grp[i + 1]])])
            i += 2
        if i < len(grp):
            stat.append(single_map[grp[i]])
    # 属性攻防（火水風光闇），攻在前防在后
    elem_atk, elem_def = [], []
    for m in RE_ELEM.finditer(desc):
        el = ELEM_CHAR[m.group(1)]
        atk = (m.group(2) == "攻撃力")
        d = _dir_after(desc, m.start())
        if d is None:
            continue
        num = ELEM_BASE[el] + (0 if atk else 2) + (0 if d == "+" else 1)
        (elem_atk if atk else elem_def).append((el, num))
    seen = set()
    for _, n in sorted(elem_atk) + sorted(elem_def):
        if n not in seen:
            seen.add(n)
            stat.append(n)
    if RE_MAXHP.search(desc):
        stat.append(38)

    # —— 特效行（2.2）——
    special = []
    if RE_SELFHEAL.search(desc):
        special.append(12)
    if "CT" in fgs:
        special.append(17)
    if "EH" in fgs:
        special.append(68)
    if "MN" in fgs:
        special.append(69)
    if "SD" in fgs:
        special.append(70)

    # —— 标记行（2.3）——
    mark = []
    if "Mt" in fgs:
        mark.append(51)
    if "Ba" in fgs:
        mark.append(54)
    if RE_ET.search(desc):
        mark.append(55)
    if "An" in fgs:
        mark.append(57)

    paths = lambda lst: [SKILL_ICON % n for n in lst]
    return target_icon(desc), paths(stat), paths(special), paths(mark)


def stat_change_set(gvg_skill):
    """卡片数值变动涉及的「单项」图标编号集合（不做组合、不含最大HP）。
    主属性 4 种各上/下 -> 1-8；五属性攻防各上/下 -> 18-37；共 28 个可能值。
    一张卡可同时命中多项。"""
    desc = (gvg_skill.get("desc", "") if gvg_skill else "") or ""
    nums = set()
    flags = stat_flags(desc)
    for s in MAIN_ORDER:                 # pa pd ma md
        if s + "+" in flags:
            nums.add(MAIN_UP[s])
        if s + "-" in flags:
            nums.add(MAIN_DN[s])
    for m in RE_ELEM.finditer(desc):     # 火水風光闇 × 攻/防
        el = ELEM_CHAR[m.group(1)]
        atk = (m.group(2) == "攻撃力")
        d = _dir_after(desc, m.start())
        if d is None:
            continue
        nums.add(ELEM_BASE[el] + (0 if atk else 2) + (0 if d == "+" else 1))
    return nums


# ---------------------------------------------------------------------------
# twdb（allb.game-db.tw）卡片编号
# ---------------------------------------------------------------------------
def tw_full_id(e):
    """twdb 卡片编号 = cardMstId * 10 + 变体位。
    变体位：普通卡=0；可觉醒卡=1（两个条目同号）；超觉醒卡=该条目 cardType(1-7)。"""
    awk = e.get("awk", "none")
    if awk == "awakening":
        digit = 1
    elif awk == "super":
        digit = e["cardType"]
    else:
        digit = 0
    return e["cardMstId"] * 10 + digit


# ---------------------------------------------------------------------------
# 右上角被动小圆（仅组卡器）
# ---------------------------------------------------------------------------
def passive_dot(e):
    """根据被动技能（GvgAuto 名）返回右上角小圆的颜色（无则空字符串）。
    効果範囲+1 → #e377c2（品红）；副援:支援UP → #f6c2dd（很浅的粉色）；都不是 → 不加圆。"""
    ga = e["skills"].get("gvgAuto")
    name = (ga.get("name", "") if ga else "") or ""
    if "効果範囲+1" in name:
        return "#e377c2"
    if "副援:支援UP" in name:
        return "#f6c2dd"
    return ""


# ---------------------------------------------------------------------------
# 构建选卡单元
# ---------------------------------------------------------------------------
def build_units(entries):
    units = []
    for e in entries:
        gvg = e["skills"].get("gvg")
        ga = e["skills"].get("gvgAuto")
        tgt, sk1, sk2, sk3 = gvg_battle_icons(gvg, e["fg"])
        gdesc = (gvg.get("desc", "") if gvg else "") or ""
        units.append({
            "uid": e["uniqueId"],
            "tw": tw_full_id(e),
            "name": e["name"],
            "ct": e["cardType"],
            "attr": e["attribute"],
            "grade": e["gradeType"],
            "leg": e["gradeType"] == 1,
            "ult": e["gradeType"] == 2,
            "order": e["order"],
            "tg": e["tg"],
            "fg": sorted(e["fg"]),
            "ga_codes": sorted(e["ga"]),
            "mt": stack_count(gvg, RE_MT),
            "an": stack_count(gvg, RE_AN),
            "ba": stack_count(gvg, RE_BA),
            "et": stack_count(gvg, RE_ET),
            "lv": ["%s:%s" % (c, l) for c, l in passive_levels(ga)],
            "gvg": gvg,
            "ga_skill": ga,
            "legendary": e["skills"].get("legendary"),
            "mark": card_markers.marker_for(e, "deck"),
            "pdot": passive_dot(e),
            "tgt": tgt, "sk1": sk1, "sk2": sk2, "sk3": sk3,
            "sc": sorted(stat_change_set(gvg)),       # 单项数值变动图标编号
            "heal": 1 if RE_HP_HEAL.search(gdesc) else 0,
        })
    return units


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def render_mini_skill(sk, icon):
    if sk is None:
        return '<div class="u-skill empty"><img class="u-si" src="%s" alt=""></div>' % icon
    return (
        '<div class="u-skill">'
        '<div class="u-sname"><img class="u-si" src="{icon}" alt="">{name}</div>'
        '<div class="u-sdesc">{desc}</div>'
        "</div>"
    ).format(icon=icon, name=fmt(sk["name"]), desc=fmt(sk["desc"]))


def pdot_html(color):
    """右上角被动小圆（圆心=卡图右上顶点，可略微溢出卡框）。"""
    return ('<span class="pdot" style="background:%s"></span>' % color) if color else ""


def render_overlay(tgt, sk1, sk2, sk3):
    """卡图上的战斗图标：左上目标数 / 右下数值横排 / 右侧居中特效竖列 / 左侧居中标记竖列。"""
    def grp(cls, lst):
        if not lst:
            return ""
        return '<div class="%s">%s</div>' % (
            cls, "".join('<img src="%s" alt="">' % p for p in lst))
    h = ""
    if tgt:
        h += '<img class="tgt" src="%s" alt="">' % tgt
    h += grp("sk-stat", sk1)       # 数值变动：右下横排
    h += grp("sk-special", sk2)    # 特效：右侧居中竖列
    h += grp("sk-mark", sk3)       # 标记：左侧居中竖列
    return h


def render_unit(u):
    icon = CARD_ICON_URL.format(uid=u["uid"])
    return (
        '<div class="unit" data-uid="{uid}" data-tw="{tw}" data-ct="{ct}" data-attr="{attr}" '
        'data-grade="{grade}" data-leg="{leg}" data-ult="{ult}" data-order="{order}" '
        'data-name="{name_attr}" data-tg="{tg}" data-fg="{fg}" data-ga="{ga_codes}" '
        'data-mt="{mt}" data-an="{an}" data-ba="{ba}" data-et="{et}" data-lv="{lv}" '
        'data-mark="{mark}" data-frame="{frame}" data-pdot="{pdot_color}" data-tgt="{tgt}" '
        'data-sk1="{sk1}" data-sk2="{sk2}" data-sk3="{sk3}" data-sc="{sc}" data-heal="{heal}">'
        '<div class="u-top">'
        '<span class="cardimg" title="{name_attr}">'
        '<img class="art" loading="lazy" src="{icon}" alt="" onerror="this.classList.add(\'broken\')">'
        '<img class="frame" src="{frame}" alt="">'
        '{pdot}'
        '<img class="mark" src="{mark}" alt="">'
        '{overlay}'
        '</span>'
        '<div class="u-meta">'
        '<img class="u-tag" src="assets/CardType{ct}.png" alt="" title="類別">'
        '<img class="u-tag" src="assets/Attribute{attr}.png" alt="" title="属性">'
        '</div>'
        '<button class="u-add" type="button">＋ 追加</button>'
        '</div>'
        '{gvg_cell}{ga_cell}{leg_cell}'
        '</div>'
    ).format(
        uid=u["uid"], tw=u["tw"], ct=u["ct"], attr=u["attr"], grade=u["grade"],
        leg=1 if u["leg"] else 0, ult=1 if u["ult"] else 0, order=u["order"],
        name_attr=html.escape(u["name"], quote=True),
        tg=u["tg"], fg=" ".join(u["fg"]), ga_codes=" ".join(u["ga_codes"]),
        mt=u["mt"], an=u["an"], ba=u["ba"], et=u["et"], lv=" ".join(u["lv"]),
        mark=u["mark"], frame=card_markers.frame_rel(u["ult"]),
        pdot=pdot_html(u["pdot"]), pdot_color=u["pdot"],
        tgt=u["tgt"], sk1=" ".join(u["sk1"]), sk2=" ".join(u["sk2"]), sk3=" ".join(u["sk3"]),
        sc=" ".join(str(n) for n in u["sc"]), heal=u["heal"],
        overlay=render_overlay(u["tgt"], u["sk1"], u["sk2"], u["sk3"]),
        icon=icon,
        gvg_cell=render_mini_skill(u["gvg"], "assets/Skill2.png"),
        ga_cell=render_mini_skill(u["ga_skill"], "assets/Skill3.png"),
        leg_cell=(render_mini_skill(u["legendary"], "assets/Skill4.png")
                  if u["legendary"] else ""),
    )


def render_html(units):
    legendary = sorted((u for u in units if u["leg"]),
                       key=lambda u: u["order"], reverse=True)
    others = sorted((u for u in units if not u["leg"]),
                    key=lambda u: (u["ult"], u["order"]), reverse=True)

    leg_html = "\n".join(render_unit(u) for u in legendary)
    oth_html = "\n".join(render_unit(u) for u in others)

    targets = sorted({u["tg"] for u in units if u["tg"]})

    dropdowns = {
        "__DD_TYPE__": build_dropdown("type", "類別", sorted(CARD_TYPE_LABEL.items())),
        "__DD_ATTR__": build_dropdown("attr", "属性", sorted(ATTRIBUTE_LABEL.items())),
        "__DD_TARGET__": build_dropdown("target", "タゲ数", [(t, t) for t in targets]),
        "__DD_FEAT__": build_dropdown("feat", "技能特性", FEATURE_DEFS),
        "__DD_GA__": build_dropdown("ga", "補助特性", GA_DEFS),
    }

    out = HTML_TEMPLATE
    for token, frag in dropdowns.items():
        out = out.replace(token, frag)
    out = out.replace("__LEG_UNITS__", leg_html)
    out = out.replace("__OTH_UNITS__", oth_html)
    out = out.replace("__LEG_TOTAL__", str(len(legendary)))
    out = out.replace("__OTH_TOTAL__", str(len(others)))
    return out


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>デッキビルダー</title>
<style>
  :root { --head-bg:#5b6b8c; --head-hover:#6b7da0; --head-fg:#fff;
          --line:#000; --row1:#ffffff; --row2:#eeeeee; --txt:#111; --toolbar-h:46px; }
  * { box-sizing: border-box; }
  body { margin:0; background:#fff; color:var(--txt);
         font-family:"Segoe UI","Microsoft YaHei","Hiragino Sans","Meiryo",sans-serif; font-size:13px; }

  header { position:sticky; top:0; z-index:50; background:#dde2ea; border-bottom:1px solid #9aa3b8;
           padding:8px 14px; display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
  header h1 { font-size:16px; margin:0 8px 0 0; color:#222; }
  header input, header select, .ddbtn, button.btn { background:#fff; color:#111; border:1px solid #9aa3b8;
           border-radius:6px; padding:5px 8px; font-size:13px; }
  button.btn, .ddbtn { cursor:pointer; }
  header input#code { width:260px; font-family:monospace; }

  /* 前衛/後衛 开关 */
  .roleSw { display:inline-flex; border:1px solid #5b6b8c; border-radius:8px; overflow:hidden; }
  .roleSw button { border:0; background:#fff; color:#333; padding:6px 14px; cursor:pointer; font-weight:600; }
  .roleSw button.on { background:#5b6b8c; color:#fff; }
  header label.chk { display:inline-flex; align-items:center; gap:4px; cursor:pointer; color:#444; }

  /* 通用复选框下拉 */
  .dd { position:relative; }
  .ddbtn.active { background:#dce7f6; border-color:#5b6b8c; font-weight:600; }
  .ddpanel { display:none; position:absolute; top:calc(100% + 4px); left:0; z-index:60; background:#fff;
             border:1px solid #888; border-radius:6px; padding:6px 8px; max-height:72vh; overflow:auto;
             box-shadow:0 6px 16px rgba(0,0,0,.25); min-width:160px; }
  .ddpanel.open { display:block; }
  .ddpanel label { display:block; color:#111; margin:0; padding:3px 6px; white-space:nowrap; cursor:pointer; }
  .ddpanel label:hover { background:#eef; }
  .ddpanel label.disabled { color:#bbb; cursor:not-allowed; }
  .ddpanel input { margin-right:6px; }

  /* 主体两栏：左卡组面板（粘性），右选卡区 */
  .layout { display:flex; align-items:flex-start; gap:14px; padding:12px 14px; }
  .deckpane { flex:0 0 510px; position:sticky; top:calc(var(--toolbar-h) + 12px);
              max-height:calc(100vh - var(--toolbar-h) - 24px); overflow:auto;
              border:1px solid #9aa3b8; border-radius:8px; background:#f7f8fb; padding:10px; }
  .pickpane { flex:1 1 auto; min-width:0; }
  .deckpane #code { flex:1 1 200px; min-width:150px; font-family:monospace; font-size:11px; }

  .deck-group { margin-bottom:12px; }
  .deck-group h3 { font-size:14px; margin:0 0 6px; color:#333; border-bottom:1px solid #c5ccda; padding-bottom:3px; }
  .slots { display:grid; grid-template-columns:repeat(5, 88px); gap:6px; justify-content:start; }
  .slot { position:relative; width:88px; height:88px; border:1px solid #b7bdcc; border-radius:6px;
          background:#fff; overflow:visible; }   /* visible: 让被动小圆能出框显示 */
  .slot.empty { background:#fff; }
  .slot.empty .blank { width:100%; height:100%; object-fit:cover; display:block; border-radius:6px; }
  .slot.filled { cursor:grab; }
  .slot.filled:active { cursor:grabbing; }
  .slot.dragover { outline:2px solid #5b6b8c; outline-offset:-2px; }
  .slot.dragging { opacity:.35; }
  .slot .cardimg { width:100%; height:100%; }
  .slot .x { position:absolute; top:0; right:0; z-index:2; background:rgba(180,0,0,.85); color:#fff;
             font-size:16px; font-weight:700; line-height:1; padding:5px 10px;
             border-bottom-left-radius:8px; border-top-right-radius:6px;
             opacity:0; cursor:pointer; }
  .slot:hover .x { opacity:1; }
  .empty-hint { color:#999; align-self:center; }

  /* 卡图叠放：卡图 + 稀有度边框 + 右上角类别角标（列表/组卡器共用）
     角标比例受限：纵向 ≤1/4 高、横向 ≤1/2 宽 */
  .cardimg { position:relative; display:block; width:88px; height:88px; }
  .cardimg .art { width:100%; height:100%; object-fit:cover; display:block; border-radius:6px; }
  .cardimg .art.broken { visibility:hidden; }
  .cardimg .frame { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
  .cardimg .mark { position:absolute; top:-2px; right:-3px; max-height:38%;
                   height:auto; width:auto; pointer-events:none;
                   filter:drop-shadow(0 1px 1px rgba(0,0,0,.4)); }
  .slot .cardimg .mark { top:1px; right:1px; }   /* 类别角标在格子内不溢出 */
  /* 被动小圆：圆心=卡图右上顶点，略微溢出卡框；放到最顶层完整显示（小圆很小，不会挡住其他元素）*/
  .cardimg .pdot { position:absolute; top:-7px; right:-7px; width:14px; height:14px; z-index:10;
                   border-radius:50%; border:2px solid #fff; box-sizing:border-box;
                   box-shadow:0 1px 2px rgba(0,0,0,.55); pointer-events:none; }
  /* 左上：目标数（与角标同高 38%）；数值=右下横排；特效=右侧居中竖列；标记=左侧居中竖列 */
  .cardimg .tgt { position:absolute; left:-2px; top:-2px; max-height:38%; height:auto; width:auto;
                  pointer-events:none; filter:drop-shadow(0 1px 1px rgba(0,0,0,.45)); }
  .slot .cardimg .tgt { left:1px; top:1px; }
  .cardimg .sk-stat { position:absolute; right:1px; bottom:1px; display:flex; gap:1px;
                      justify-content:flex-end; pointer-events:none; }
  /* 左右两列向下对齐，但底部留出一行(18px)给数值变动行 */
  .cardimg .sk-special { position:absolute; right:1px; bottom:18px;
                         display:flex; flex-direction:column; align-items:flex-end; gap:1px; pointer-events:none; }
  .cardimg .sk-mark { position:absolute; left:1px; bottom:18px;
                      display:flex; flex-direction:column; align-items:flex-start; gap:1px; pointer-events:none; }
  .cardimg .sk-stat img, .cardimg .sk-special img, .cardimg .sk-mark img {
                      height:15px; width:auto; filter:drop-shadow(0 1px 1px rgba(0,0,0,.55)); }

  /* 统计 */
  .stats h3 { font-size:14px; margin:10px 0 6px; color:#333; border-bottom:1px solid #c5ccda; padding-bottom:3px; }
  .chips { display:flex; flex-wrap:wrap; gap:5px 10px; }
  .chip { display:inline-flex; align-items:center; gap:3px; background:#fff; border:1px solid #cfd5e2;
          border-radius:999px; padding:1px 8px 1px 4px; }
  .chip img { width:22px; height:22px; object-fit:contain; }
  .chip b { font-variant-numeric:tabular-nums; }
  .chip.zero { opacity:.4; }
  .chip .scicon { width:20px; height:20px; object-fit:contain; vertical-align:middle; }
  .sclbl { font-size:12px; color:#666; margin:5px 0 2px; }
  .skcls { display:flex; flex-wrap:wrap; gap:5px 8px; }
  .skcls .muted { color:#999; font-size:12px; }
  .statline { line-height:1.9; }
  .statline .k { display:inline-block; min-width:38px; font-weight:600; }
  .lvtbl { border-collapse:collapse; margin:3px 0 8px; }
  .lvtbl th, .lvtbl td { border:1px solid #c5ccda; padding:2px 8px; text-align:center; font-variant-numeric:tabular-nums; }
  .lvtbl th { background:#eef1f6; }
  .lvname { font-weight:600; }

  /* 选卡单元 */
  .pickpane h2 { font-size:15px; margin:4px 0 8px; color:#222; }
  .units { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:18px; }
  .unit { width:300px; border:1px solid #b7bdcc; border-radius:8px; background:#fff; padding:8px;
          display:flex; flex-direction:column; gap:6px; }
  .unit.hidden { display:none; }
  .unit.in-deck { outline:2px solid #4a8f4a; background:#f0f8f0; }
  .u-top { display:flex; align-items:center; gap:8px; }
  .u-top .cardimg { flex:0 0 auto; }
  .u-meta { display:flex; flex-direction:column; gap:3px; }
  .u-tag { width:26px; height:26px; object-fit:contain; }
  .u-add { margin-left:auto; background:#5b6b8c; color:#fff; border:0; border-radius:6px;
           padding:7px 12px; cursor:pointer; font-weight:600; white-space:nowrap; }
  .u-add:hover { background:#6b7da0; }
  .unit.in-deck .u-add { background:#9bb39b; cursor:default; }
  .u-skill { border-top:1px dashed #d4d9e4; padding-top:4px; }
  .u-skill.empty { color:#bbb; }
  .u-sname { font-weight:600; }
  .u-si { width:15px; height:15px; object-fit:contain; vertical-align:-2px; margin-right:4px; }
  .u-sdesc { color:#333; line-height:1.4; margin-top:2px; }

  #pcount { color:#444; }
</style>
</head>
<body>
<header>
  <h1>デッキビルダー</h1>
  <div class="roleSw">
    <button type="button" id="roleF" class="on">前衛</button>
    <button type="button" id="roleB">後衛</button>
  </div>
  <span><label>検索</label><input id="q" type="text" placeholder="名前で検索"></span>
  __DD_TYPE__
  __DD_ATTR__
  __DD_TARGET__
  __DD_FEAT__
  __DD_GA__
  <label class="chk"><input type="checkbox" id="deckOnly"> デッキ内のみ</label>
  <button class="btn" id="clearFilter" type="button">筛选クリア</button>
  <span id="pcount"></span>
</header>

<div class="layout">
  <aside class="deckpane">
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px;">
      <input id="code" type="text" placeholder="デッキコード (game-db URL)" spellcheck="false">
      <button class="btn" id="loadCode" type="button">読込</button>
      <button class="btn" id="copyCode" type="button">コピー</button>
      <button class="btn" id="clearDeck" type="button">デッキクリア</button>
    </div>

    <div class="deck-group">
      <h3>Legendary <span id="legCount">0</span>/5</h3>
      <div id="legSlots" class="slots"><span class="empty-hint">— なし —</span></div>
    </div>
    <div class="deck-group">
      <h3>メイン <span id="othCount">0</span>/20</h3>
      <div id="othSlots" class="slots"><span class="empty-hint">— なし —</span></div>
    </div>

    <div class="stats" id="stats">
      <h3>類別</h3><div id="stType" class="chips"></div>
      <h3>属性</h3><div id="stAttr" class="chips"></div>
      <h3>目標数</h3><div id="stTarget" class="chips"></div>
      <h3>数値変動（単項）</h3>
      <div class="sclbl">増加</div><div id="stScUp" class="chips sc"></div>
      <div class="sclbl">減少</div><div id="stScDn" class="chips sc"></div>
      <h3>スキル分類（buff変動別）</h3><div id="stSkillCls" class="skcls"></div>
      <h3>スタック (枚数 / 総スタック数)</h3><div id="stStack" class="statline"></div>
      <h3>特性</h3><div id="stFeat" class="statline"></div>
      <h3>補助スキル (枚数)</h3><div id="stGa" class="statline"></div>
      <h3>補助スキルレベル別</h3><div id="stLevels"></div>
    </div>
  </aside>

  <main class="pickpane">
    <h2>Legendary カード (<span>__LEG_TOTAL__</span>)
      <button class="btn" id="toggleLeg" type="button">折りたたむ</button></h2>
    <div class="units" id="legUnits">
__LEG_UNITS__
    </div>
    <h2>メインカード (<span>__OTH_TOTAL__</span>)</h2>
    <div class="units" id="othUnits">
__OTH_UNITS__
    </div>
  </main>
</div>

<script>
  var header = document.querySelector('header');
  var q = document.getElementById('q');
  var pcount = document.getElementById('pcount');
  var units = Array.prototype.slice.call(document.querySelectorAll('.unit'));

  var TYPE_LABEL = {1:'通常単体',2:'通常範囲',3:'特殊単体',4:'特殊範囲',5:'支援',6:'妨害',7:'回復'};
  var GA_LABEL = {dmgup:'ダメージUP',supup:'支援UP',healup:'回復UP',ptup:'獲得マッチPtUP',rangeup:'効果範囲+1'};
  var ROMAN = {'Ⅰ':1,'Ⅱ':2,'Ⅲ':3,'Ⅳ':4,'Ⅴ':5,'Ⅵ':6,'Ⅶ':7,'Ⅷ':8,'Ⅸ':9,'Ⅹ':10};

  // 数値変動（単項）：14 种 × 上/下 = 28 项。图标 = BattleIconSkillImg{n}
  var SC_UP=[1,2,3,4,18,20,22,24,26,28,30,32,34,36];
  var SC_DN=[5,6,7,8,19,21,23,25,27,29,31,33,35,37];
  var SC_NAME={1:'ATK↑',2:'DEF↑',3:'Sp.ATK↑',4:'Sp.DEF↑',5:'ATK↓',6:'DEF↓',7:'Sp.ATK↓',8:'Sp.DEF↓',
    18:'火攻↑',19:'火攻↓',20:'火防↑',21:'火防↓',22:'水攻↑',23:'水攻↓',24:'水防↑',25:'水防↓',
    26:'風攻↑',27:'風攻↓',28:'風防↑',29:'風防↓',30:'光攻↑',31:'光攻↓',32:'光防↑',33:'光防↓',
    34:'闇攻↑',35:'闇攻↓',36:'闇防↑',37:'闇防↓'};
  function scIcon(n){ return 'assets/Sprite/BattleIconSkillImg'+('00'+n).slice(-3)+'.png'; }
  function healIcon(){ return role==='F' ? scIcon(12) : 'assets/CardType7.png'; }

  function setHeadOffset(){ document.documentElement.style.setProperty('--toolbar-h', header.offsetHeight+'px'); }
  window.addEventListener('resize', setHeadOffset); setHeadOffset();

  // ---------- 单元数据缓存 ----------
  var unitByKey = {};           // 'uid.ct' -> element
  function parseUnit(el){
    var d = el.dataset;
    return { uid:d.uid, tw:+d.tw, ct:+d.ct, attr:+d.attr, grade:+d.grade, leg:d.leg==='1',
             name:d.name, tg:d.tg||'', mark:d.mark, frame:d.frame, pdot:d.pdot||'',
             tgt:d.tgt||'', sk1:d.sk1?d.sk1.split(' '):[], sk2:d.sk2?d.sk2.split(' '):[], sk3:d.sk3?d.sk3.split(' '):[],
             sc:d.sc?d.sc.split(' ').map(Number):[], heal:d.heal==='1',
             fg:d.fg?d.fg.split(' '):[], ga:d.ga?d.ga.split(' '):[],
             mt:+d.mt, an:+d.an, ba:+d.ba, et:+d.et, lv:d.lv?d.lv.split(' '):[], el:el };
  }
  units.forEach(function(el){ unitByKey[el.dataset.uid+'.'+el.dataset.ct] = el; });
  // twdb 编号 -> 单元（可觉醒卡两条目同号，故为数组）
  var twIndex={};
  units.forEach(function(el){ var t=el.dataset.tw; if(t) (twIndex[t]=twIndex[t]||[]).push(el); });

  // ---------- 下拉面板开关 ----------
  function closePanels(except){
    var ps=document.querySelectorAll('.ddpanel.open');
    for(var i=0;i<ps.length;i++) if(ps[i]!==except) ps[i].classList.remove('open');
  }
  var ddbtns=document.querySelectorAll('.ddbtn');
  for(var i=0;i<ddbtns.length;i++){
    ddbtns[i].addEventListener('click', function(e){
      var panel=document.querySelector('.ddpanel[data-ddp="'+this.dataset.dd+'"]');
      var willOpen=!panel.classList.contains('open');
      closePanels(panel); panel.classList.toggle('open', willOpen); e.stopPropagation();
    });
  }
  document.addEventListener('click', function(e){
    if(e.target.closest && (e.target.closest('.ddpanel')||e.target.closest('.ddbtn'))) return;
    closePanels(null);
  });

  // ---------- 角色（前衛/後衛）----------
  var role = 'F';
  function validTypes(){ return role==='F' ? [1,2,3,4] : [5,6,7]; }
  function isValidType(t){ return validTypes().indexOf(+t) !== -1; }

  function applyRoleToTypeFilter(){
    // 关闭不属于当前角色的類別选项
    var boxes=document.querySelectorAll('input[data-f="type"]');
    for(var i=0;i<boxes.length;i++){
      var ok=isValidType(boxes[i].value);
      boxes[i].disabled=!ok;
      if(!ok) boxes[i].checked=false;
      boxes[i].parentNode.classList.toggle('disabled', !ok);
    }
  }
  function setRole(r){
    if(r===role){ return; }
    if(deckCards().length && !confirm('前衛/後衛を切り替えると現在のデッキはクリアされます。よろしいですか？')){
      return;
    }
    role=r;
    document.getElementById('roleF').classList.toggle('on', r==='F');
    document.getElementById('roleB').classList.toggle('on', r==='B');
    clearSlots(); applyRoleToTypeFilter(); renderDeck(); applyFilter();
  }
  document.getElementById('roleF').addEventListener('click', function(){ setRole('F'); });
  document.getElementById('roleB').addEventListener('click', function(){ setRole('B'); });

  // ---------- 筛选 ----------
  function selVals(group){
    var arr=[], els=document.querySelectorAll('input[data-f="'+group+'"]:checked');
    for(var i=0;i<els.length;i++) arr.push(els[i].value);
    return arr;
  }
  function hasAll(have, need){
    for(var k=0;k<need.length;k++) if(have.indexOf(need[k])===-1) return false;
    return true;
  }
  function updateBtns(){
    var bs=document.querySelectorAll('.ddbtn[data-dd]');
    for(var i=0;i<bs.length;i++){
      var key=bs[i].dataset.dd;
      var n=document.querySelectorAll('input[data-f="'+key+'"]:checked').length;
      bs[i].textContent=bs[i].dataset.label+(n?' ('+n+')':'')+' ▾';
      bs[i].classList.toggle('active', n>0);
    }
  }
  function applyFilter(){
    var kw=q.value.trim().toLowerCase();
    var tS=selVals('type'), aS=selVals('attr'), tgS=selVals('target'),
        fS=selVals('feat'), gaS=selVals('ga');
    var dOnly=document.getElementById('deckOnly').checked, dmap={}, shown=0;
    if(dOnly) deckCards().forEach(function(c){ dmap[c.uid]=c; });
    for(var i=0;i<units.length;i++){
      var d=units[i].dataset, ok=isValidType(d.ct);
      if(ok && dOnly){ var dc=dmap[d.uid]; if(!dc || dc.ct!==+d.ct) ok=false; }
      if(ok && tS.length && tS.indexOf(d.ct)===-1) ok=false;
      if(ok && aS.length && aS.indexOf(d.attr)===-1) ok=false;
      if(ok && kw && d.name.toLowerCase().indexOf(kw)===-1) ok=false;
      if(ok && tgS.length && tgS.indexOf(d.tg)===-1) ok=false;
      if(ok && fS.length && !hasAll(d.fg?d.fg.split(' '):[], fS)) ok=false;
      if(ok && gaS.length && !hasAll(d.ga?d.ga.split(' '):[], gaS)) ok=false;
      units[i].classList.toggle('hidden', !ok);
      if(ok) shown++;
    }
    pcount.textContent=shown+' 件表示';
    updateBtns();
  }
  document.addEventListener('change', function(e){
    if(e.target.matches && e.target.matches('input[data-f]')) applyFilter();
    else if(e.target.id==='deckOnly') applyFilter();
  });
  q.addEventListener('input', applyFilter);
  document.getElementById('clearFilter').addEventListener('click', function(){
    var cbs=document.querySelectorAll('input[data-f]:checked');
    for(var i=0;i<cbs.length;i++) cbs[i].checked=false;
    document.getElementById('deckOnly').checked=false;
    q.value=''; applyFilter();
  });
  document.getElementById('toggleLeg').addEventListener('click', function(){
    var box=document.getElementById('legUnits'), hide=box.style.display!=='none';
    box.style.display=hide?'none':''; this.textContent=hide?'展開する':'折りたたむ';
  });

  // ---------- 卡组（5 Legendary + 20 メイン 固定格子，可自由拖动重排）----------
  var LEG_MAX=5, MAIN_MAX=20;
  var legSlots=[], mainSlots=[];
  for(var _i=0;_i<LEG_MAX;_i++) legSlots.push(null);
  for(var _j=0;_j<MAIN_MAX;_j++) mainSlots.push(null);
  function slotsOf(leg){ return leg?legSlots:mainSlots; }
  function slotArr(grp){ return grp==='L'?legSlots:mainSlots; }
  function deckCards(){ return legSlots.concat(mainSlots).filter(Boolean); }
  function hasUid(uid){ return deckCards().some(function(c){ return c.uid===uid; }); }
  function clearSlots(){ for(var i=0;i<legSlots.length;i++) legSlots[i]=null;
                         for(var j=0;j<mainSlots.length;j++) mainSlots[j]=null; }

  function addUnit(el, silent){
    var c=parseUnit(el);
    if(hasUid(c.uid)){ if(!silent) flash(el); return false; }      // 同卡只能 1 张
    if(!isValidType(c.ct)){ return false; }
    var arr=slotsOf(c.leg), idx=arr.indexOf(null);
    if(idx===-1){ if(!silent) alert(c.leg?'Legendary は最大 5 枚です':'メインカードは最大 20 枚です'); return false; }
    arr[idx]=c; renderDeck(); return true;
  }
  function removeUid(uid){
    [legSlots,mainSlots].forEach(function(arr){
      for(var i=0;i<arr.length;i++) if(arr[i]&&arr[i].uid===uid) arr[i]=null;
    });
    renderDeck();
  }
  function flash(el){ el.style.transition='none'; el.style.background='#ffd9d9';
    setTimeout(function(){ el.style.transition='background .6s'; el.style.background=''; }, 30); }

  // 选卡区「追加」按钮
  document.querySelector('.pickpane').addEventListener('click', function(e){
    var btn=e.target.closest('.u-add'); if(!btn) return;
    addUnit(btn.closest('.unit'), false);
  });

  function overlayHtml(c){
    function grp(cls,lst){ if(!lst||!lst.length) return ''; var s=''; lst.forEach(function(p){ s+='<img src="'+p+'" alt="">'; }); return '<div class="'+cls+'">'+s+'</div>'; }
    var h='';
    if(c.tgt) h+='<img class="tgt" src="'+c.tgt+'" alt="">';
    h+=grp('sk-stat',c.sk1)+grp('sk-special',c.sk2)+grp('sk-mark',c.sk3);
    return h;
  }
  function renderSlotGroup(container, arr, grp){
    var h='';
    for(var i=0;i<arr.length;i++){
      var c=arr[i];
      if(c){
        h+='<div class="slot filled" draggable="true" data-grp="'+grp+'" data-idx="'+i+'" data-uid="'+c.uid+'" '
          +'title="'+escAttr(c.name)+' ('+TYPE_LABEL[c.ct]+')">'
          +'<span class="cardimg">'
          +'<img class="art" loading="lazy" src="'+iconUrl(c.uid)+'" alt="" onerror="this.style.visibility=\\'hidden\\'">'
          +'<img class="frame" src="'+c.frame+'" alt="">'
          +(c.pdot?'<span class="pdot" style="background:'+c.pdot+'"></span>':'')
          +'<img class="mark" src="'+c.mark+'" alt="">'
          +overlayHtml(c)
          +'</span>'
          +'<span class="x" title="外す">×</span></div>';
      } else {
        h+='<div class="slot empty" data-grp="'+grp+'" data-idx="'+i+'">'
          +'<img class="blank" src="assets/Blank.png" alt=""></div>';
      }
    }
    container.innerHTML=h;
  }
  function renderDeck(){
    document.getElementById('legCount').textContent=legSlots.filter(Boolean).length;
    document.getElementById('othCount').textContent=mainSlots.filter(Boolean).length;
    renderSlotGroup(document.getElementById('legSlots'), legSlots, 'L');
    renderSlotGroup(document.getElementById('othSlots'), mainSlots, 'M');
    // 标记选卡区中已在卡组里的单元
    var inUids={}; deckCards().forEach(function(c){ inUids[c.uid]=1; });
    for(var i=0;i<units.length;i++){
      var inDeck=!!inUids[units[i].dataset.uid];
      units[i].classList.toggle('in-deck', inDeck);
      var btn=units[i].querySelector('.u-add'); if(btn) btn.textContent=inDeck?'✓ 編成済':'＋ 追加';
    }
    renderStats(); syncCode();
    if(document.getElementById('deckOnly').checked) applyFilter();
  }

  // 卡组面板：点击「×」移除
  document.querySelector('.deckpane').addEventListener('click', function(e){
    var x=e.target.closest('.slot .x'); if(!x) return;
    var slot=x.closest('.slot'); if(slot && slot.dataset.uid) removeUid(slot.dataset.uid);
  });
  document.getElementById('clearDeck').addEventListener('click', function(){
    if(deckCards().length && confirm('デッキを全てクリアしますか？')){ clearSlots(); renderDeck(); }
  });

  // 拖拽重排（仅同组内：空格子放置 / 与目标格交换）
  var dragSrc=null;
  ['legSlots','othSlots'].forEach(function(id){
    var box=document.getElementById(id);
    box.addEventListener('dragstart', function(e){
      var s=e.target.closest('.slot.filled'); if(!s) return;
      dragSrc={grp:s.dataset.grp, idx:+s.dataset.idx};
      e.dataTransfer.effectAllowed='move';
      try{ e.dataTransfer.setData('text/plain', String(dragSrc.idx)); }catch(_e){}
      s.classList.add('dragging');
    });
    box.addEventListener('dragend', function(){
      var ds=box.querySelectorAll('.dragging,.dragover');
      for(var i=0;i<ds.length;i++) ds[i].classList.remove('dragging','dragover');
      dragSrc=null;
    });
    box.addEventListener('dragover', function(e){
      if(!dragSrc) return;
      var s=e.target.closest('.slot'); if(!s || s.dataset.grp!==dragSrc.grp) return;
      e.preventDefault(); e.dataTransfer.dropEffect='move';
    });
    box.addEventListener('dragenter', function(e){
      var s=e.target.closest('.slot'); if(dragSrc && s && s.dataset.grp===dragSrc.grp) s.classList.add('dragover');
    });
    box.addEventListener('dragleave', function(e){
      var s=e.target.closest('.slot'); if(s && !s.contains(e.relatedTarget)) s.classList.remove('dragover');
    });
    box.addEventListener('drop', function(e){
      if(!dragSrc) return;
      var s=e.target.closest('.slot'); if(!s || s.dataset.grp!==dragSrc.grp){ dragSrc=null; return; }
      e.preventDefault();
      var arr=slotArr(dragSrc.grp), from=dragSrc.idx, to=+s.dataset.idx;
      if(from!==to){ var tmp=arr[to]; arr[to]=arr[from]; arr[from]=tmp; }
      dragSrc=null; renderDeck();
    });
  });

  // ---------- 统计 ----------
  function lvSortKey(lab){ var plus=(lab.match(/\\+/g)||[]).length; var r=lab.replace(/\\+/g,''); return (ROMAN[r]||0)*10+plus; }
  function renderStats(){
    var list=deckCards();
    var byType={}, byAttr={}, byTarget={}, feat={}, ga={}, marks={Mt:0,An:0,Ba:0,Et:0};
    var lv={dmgup:{},supup:{},healup:{},ptup:{}};
    list.forEach(function(c){
      byType[c.ct]=(byType[c.ct]||0)+1;
      byAttr[c.attr]=(byAttr[c.attr]||0)+1;
      if(c.tg) byTarget[c.tg]=(byTarget[c.tg]||0)+1;
      c.fg.forEach(function(f){ feat[f]=(feat[f]||0)+1; });
      c.ga.forEach(function(g){ ga[g]=(ga[g]||0)+1; });
      marks.Mt+=c.mt; marks.An+=c.an; marks.Ba+=c.ba; marks.Et+=c.et;
      c.lv.forEach(function(t){ var p=t.split(':'); if(lv[p[0]]) lv[p[0]][p[1]]=(lv[p[0]][p[1]]||0)+1; });
    });

    // 類別
    document.getElementById('stType').innerHTML = validTypes().map(function(t){
      var n=byType[t]||0;
      return '<span class="chip'+(n?'':' zero')+'"><img src="assets/CardType'+t+'.png" alt="">'
        +TYPE_LABEL[t]+' <b>'+n+'</b></span>';
    }).join('');
    // 属性
    document.getElementById('stAttr').innerHTML = [1,2,3,4,5].map(function(a){
      var n=byAttr[a]||0;
      return '<span class="chip'+(n?'':' zero')+'"><img src="assets/Attribute'+a+'.png" alt=""><b>'+n+'</b></span>';
    }).join('');
    // 目標数
    var tks=Object.keys(byTarget).sort();
    document.getElementById('stTarget').innerHTML = tks.length ? tks.map(function(t){
      return '<span class="chip"><b>'+t+'</b> '+byTarget[t]+'</span>';
    }).join('') : '<span class="empty-hint">—</span>';

    // 数値変動（単項）：每个图标统计命中卡数（一张卡可命中多项）
    var scCount={};
    list.forEach(function(c){ c.sc.forEach(function(n){ scCount[n]=(scCount[n]||0)+1; }); });
    function scChips(arr){ return arr.map(function(n){ var v=scCount[n]||0;
      return '<span class="chip'+(v?'':' zero')+'" title="'+SC_NAME[n]+'">'
        +'<img class="scicon" src="'+scIcon(n)+'" alt=""> <b>'+v+'</b></span>'; }).join(''); }
    document.getElementById('stScUp').innerHTML = scChips(SC_UP);
    document.getElementById('stScDn').innerHTML = scChips(SC_DN);

    // スキル分類：buff 变动完全一致（+ 是否含HP回复）归为一类，每卡仅属一类
    var groups={};
    list.forEach(function(c){
      var sig=c.sk1.join(' ')+(c.heal?'|H':'');
      if(!groups[sig]) groups[sig]={sk1:c.sk1, heal:c.heal, n:0};
      groups[sig].n++;
    });
    var garr=Object.keys(groups).map(function(k){ return groups[k]; })
      .sort(function(a,b){ return b.n-a.n || a.sk1.length-b.sk1.length; });
    document.getElementById('stSkillCls').innerHTML = garr.length ? garr.map(function(g){
      var ic=g.sk1.map(function(p){ return '<img class="scicon" src="'+p+'" alt="">'; }).join('');
      if(g.heal) ic+='<img class="scicon" src="'+healIcon()+'" alt="" title="HP回復">';
      if(!ic) ic='<span class="muted">変動なし</span>';
      return '<span class="chip">'+ic+' <b>'+g.n+'</b></span>';
    }).join('') : '<span class="empty-hint">—</span>';

    // スタック Mt/An/Ba
    document.getElementById('stStack').innerHTML = ['Mt','An','Ba','Et'].map(function(k){
      return '<div><span class="k">'+k+'</span> '+(feat[k]||0)+' 枚 / <b>'+marks[k]+'</b> スタック</div>';
    }).join('');
    // EH/SD/MN/CT
    document.getElementById('stFeat').innerHTML = ['EH','SD','MN','CT'].map(function(k){
      return '<div><span class="k">'+k+'</span> '+(feat[k]||0)+' 枚</div>';
    }).join('');
    // 五种被动
    document.getElementById('stGa').innerHTML = ['dmgup','supup','healup','ptup','rangeup'].map(function(k){
      return '<div><span class="k" style="min-width:120px">'+GA_LABEL[k]+'</span> '+(ga[k]||0)+' 枚</div>';
    }).join('');
    // 逐等级（4 种，除 効果範囲+1）
    document.getElementById('stLevels').innerHTML = ['dmgup','supup','healup','ptup'].map(function(code){
      var m=lv[code]; var keys=Object.keys(m).sort(function(a,b){return lvSortKey(a)-lvSortKey(b);});
      if(!keys.length) return '<div class="lvname">'+GA_LABEL[code]+'：—</div>';
      var head='', body='';
      keys.forEach(function(k){ head+='<th>'+k+'</th>'; body+='<td>'+m[k]+'</td>'; });
      return '<div class="lvname">'+GA_LABEL[code]+'</div>'
        +'<table class="lvtbl"><tr>'+head+'</tr><tr>'+body+'</tr></table>';
    }).join('');
  }

  // ---------- デッキコード（allb.game-db.tw デッキビルダー URL）----------
  // 形式：base64( enc62(base-61) | LG... | メイン... | role ) を ?v= に載せる。
  //   base    = デッキ内 twdb 番号(cardMstId*10+変体)の最小値
  //   各カード = enc62(番号 - base + 61) + "4"（末尾 "4" は限界突破位）
  //   role    = 前衛 0 / 後衛 1
  var B62='0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ';
  var TW_URL='https://allb.game-db.tw/deckbuilder?v=';
  function enc62(num){ num=Math.floor(num); if(num<=0) return '0';
    var s=''; while(num>0){ s=B62.charAt(num%62)+s; num=Math.floor(num/62); } return s; }
  function dec62(str){ var n=0; for(var i=0;i<str.length;i++){ var k=B62.indexOf(str.charAt(i));
    if(k<0) return NaN; n=n*62+k; } return n; }

  function deckCode(){
    var cards=deckCards(); if(!cards.length) return '';
    var lg=[], nml=[];
    cards.forEach(function(c){ (c.leg?lg:nml).push(c.tw); });
    lg.sort(function(a,b){return a-b;}); nml.sort(function(a,b){return a-b;});  // 顺序无关
    var base=Math.min.apply(null, lg.concat(nml));
    var tok=function(id){ return enc62(id-base+61)+'4'; };
    var target=enc62(base-61)+'|'+lg.map(tok).join(',')+'|'+nml.map(tok).join(',')
               +'|'+(role==='F'?0:1);
    return TW_URL+btoa(target);
  }
  function syncCode(){ document.getElementById('code').value=deckCode(); }
  function loadCode(str){
    str=(str||'').trim(); if(!str){ return; }
    var target, m=str.match(/[?&]v=([^&\\s]+)/);
    try {
      if(m) target=atob(m[1].replace(/ /g,'+'));
      else if(str.indexOf('|')!==-1) target=str;       // 直接贴 target 文本
      else target=atob(str.replace(/ /g,'+'));         // 直接贴 base64
    } catch(e){ alert('コードのデコードに失敗しました'); return; }
    var parts=target.split('|');
    var base=dec62(parts[0])+61;
    if(parts.length<4 || isNaN(base)){ alert('コード形式が不正です'); return; }
    var r=(parts[3].trim()==='0')?'F':'B';
    role=r; clearSlots();
    document.getElementById('roleF').classList.toggle('on', r==='F');
    document.getElementById('roleB').classList.toggle('on', r==='B');
    applyRoleToTypeFilter();
    var miss=0;
    function place(groupStr){
      if(!groupStr) return;
      groupStr.split(',').forEach(function(t){
        if(!t) return;
        var rel=dec62(t.slice(0,-1));                  // 去掉末尾 "4"
        if(isNaN(rel)){ miss++; return; }
        var list=twIndex[rel-61+base]||[], el=null;
        for(var i=0;i<list.length;i++){ if(isValidType(list[i].dataset.ct)){ el=list[i]; break; } }
        if(!el && list.length) el=list[0];             // 觉醒卡两面都不合当前角色时退而取一
        if(!(el && addUnit(el, true))) miss++;
      });
    }
    place(parts[1]); place(parts[2]);
    renderDeck(); applyFilter();
    if(miss) alert(miss+' 枚のカードが復元できませんでした（データ更新やタイプ不一致の可能性）');
  }
  document.getElementById('loadCode').addEventListener('click', function(){ loadCode(document.getElementById('code').value); });
  document.getElementById('code').addEventListener('keydown', function(e){ if(e.key==='Enter') loadCode(this.value); });
  document.getElementById('copyCode').addEventListener('click', function(){
    var t=document.getElementById('code'); t.select();
    if(navigator.clipboard){ navigator.clipboard.writeText(t.value); } else { document.execCommand('copy'); }
    var b=this, o=b.textContent; b.textContent='コピー済'; setTimeout(function(){ b.textContent=o; }, 1000);
  });

  // ---------- 工具 ----------
  function iconUrl(uid){ return 'https://allb.tqlwsl.moe/Image/CardIcon/S/CardIconS0'+uid+'.png'; }
  function escAttr(s){ return (s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'); }

  // ---------- 初始化 ----------
  applyRoleToTypeFilter();
  applyFilter();
  renderDeck();
</script>
</body>
</html>
"""


def main():
    cards, lbb, skill, legendary, ultimate, super_by_card = build_lookups()
    entries = build_entries(cards, lbb, skill, legendary, ultimate, super_by_card)
    units = build_units(entries)
    html_text = render_html(units)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html_text)
    leg = sum(1 for u in units if u["leg"])
    print("已生成卡组编辑器：Legendary %d 个单元 / 其他 %d 个单元（共 %d）"
          % (leg, len(units) - leg, len(units)))
    print("输出文件: %s" % OUT)


if __name__ == "__main__":
    main()
