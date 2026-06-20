# -*- coding: utf-8 -*-
"""
卡牌列表生成脚本
================
根据 A.RA.YA/MasterdataBase/ 中的游戏 masterdata JSON 生成一个 HTML 卡牌列表。

运行方式（在 localdb conda 环境下）：
    conda run -n localdb python localDB/generate_card_list.py
然后用浏览器打开生成的 localDB/card_list.html 即可。

数据经常更新，每次更新后重新运行本脚本即可得到最新列表。
所有文字保留日文原文，不做翻译。

实现说明见同目录 plan 文件；面板计算规则已用以下两个示例核对通过：
  - uniqueId 10109004 通攻 = 6915
  - uniqueId 20000267 (cardType 7) 通攻 = 9207
仅依赖 Python 标准库（json / html / os）。
"""

import json
import html
import os
import re
import collections

import card_markers

# ---------------------------------------------------------------------------
# 路径（相对脚本自身解析，与当前工作目录无关）
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)                       # 工作区根目录
MST = os.path.join(ROOT, "A.RA.YA", "MasterdataBase")
OUT = os.path.join(SCRIPT_DIR, "card_list.html")

CARD_ICON_URL = "https://allb.tqlwsl.moe/Image/CardIcon/S/CardIconS0{uid}.png"

# ---------------------------------------------------------------------------
# 文案映射
# ---------------------------------------------------------------------------
CARD_TYPE_LABEL = {
    1: "通常単体", 2: "通常範囲", 3: "特殊単体", 4: "特殊範囲",
    5: "支援", 6: "妨害", 7: "回復",
}
ATTRIBUTE_LABEL = {1: "火", 2: "水", 3: "風", 4: "光", 5: "闇"}
GRADE_LABEL = {0: "普通", 1: "Legendary", 2: "Ultimate"}

# 四维：type -> (普通/觉醒 max 字段, 觉醒加成字段)
TYPE_FIELDS = {
    1: ("maxPhysicalAttack", "awakenedAddPhysicalAttack"),    # 通攻
    2: ("maxPhysicalDefense", "awakenedAddPhysicalDefense"),  # 通防
    3: ("maxMagicalAttack", "awakenedAddMagicalAttack"),      # 特攻
    4: ("maxMagicalDefense", "awakenedAddMagicalDefense"),    # 特防
}
# Ultimate 新增类别面板字段
ULT_TYPE_FIELDS = {
    1: ("awakenedAddMaxPhysicalAttack", "awakenedAddPhysicalAttack"),
    2: ("awakenedAddMaxPhysicalDefense", "awakenedAddPhysicalDefense"),
    3: ("awakenedAddMaxMagicalAttack", "awakenedAddMagicalAttack"),
    4: ("awakenedAddMaxMagicalDefense", "awakenedAddMagicalDefense"),
}


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
def load_mst(filename):
    path = os.path.join(MST, filename)
    with open(path, encoding="utf-8") as f:
        return json.load(f)["payload"]["mstList"]


def build_lookups():
    cards = load_mst("masterdata_api_mst_getCardMstList.json")
    lbb_list = load_mst("masterdata_api_mst_getLimitBreakBonusMstList.json")
    skill_list = load_mst("masterdata_api_mst_getSkillMstList.json")
    legendary_list = load_mst("masterdata_api_mst_getLegendarySkillGroupMstList.json")
    ult_list = load_mst("masterdata_api_mst_getUltimateCardMstList.json")
    super_list = load_mst("masterdata_api_mst_getCardSuperAwakeningCardTypeMstList.json")

    lbb = {x["limitBreakBonusMstId"]: x for x in lbb_list}
    skill = {x["skillMstId"]: x for x in skill_list}
    ultimate = {x["cardMstId"]: x for x in ult_list}

    # Legendary：每个 group 取 limitBreakCount 最高（最强）的那一级
    legendary = {}
    for x in legendary_list:
        gid = x["legendarySkillGroupMstId"]
        cur = legendary.get(gid)
        if cur is None or x["limitBreakCount"] > cur["limitBreakCount"]:
            legendary[gid] = x

    # 超觉醒：cardMstId -> [按 cardType 排序的记录]
    super_by_card = collections.defaultdict(list)
    for x in super_list:
        super_by_card[x["cardMstId"]].append(x)
    for recs in super_by_card.values():
        recs.sort(key=lambda r: r["cardType"])

    return cards, lbb, skill, legendary, ultimate, super_by_card


# ---------------------------------------------------------------------------
# 面板计算
# ---------------------------------------------------------------------------
def lbb_bonus(lbb_entry, t, awakened):
    """求某界限突破奖励中 type==t 的加成之和（base 或 awakenedAdd）。"""
    if not lbb_entry:
        return 0
    total = 0
    if awakened:
        for i in (1, 2, 3, 4):
            if lbb_entry.get("awakenedAddBonusType%d" % i) == t:
                total += lbb_entry.get("awakenedAddBonusValue%d" % i, 0)
    else:
        for i in (1, 2, 3, 4):
            if lbb_entry.get("bonusType%d" % i) == t:
                total += lbb_entry.get("bonusValue%d" % i, 0)
    return total


def compute_stats(max_obj, fields, add_obj, lbb_entry, include_awk):
    """返回 {1:通攻, 2:通防, 3:特攻, 4:特防} 的最终面板。

    各维 = max + (觉醒加成, 可选) + base 界限突破 + (觉醒界限突破, 可选)
    """
    stats = {}
    for t in (1, 2, 3, 4):
        max_field, add_field = fields[t]
        v = max_obj.get(max_field, 0)
        if add_obj is not None:
            v += add_obj.get(add_field, 0)
        v += lbb_bonus(lbb_entry, t, False)
        if include_awk:
            v += lbb_bonus(lbb_entry, t, True)
        stats[t] = v
    return stats


# ---------------------------------------------------------------------------
# 技能解析
# ---------------------------------------------------------------------------
def resolve_skill(skill, sid):
    if not sid:
        return None
    s = skill.get(sid)
    if not s:
        return {"name": "（不明: %s）" % sid, "desc": ""}
    return {"name": s.get("name", ""), "desc": s.get("description", "")}


def resolve_legendary(legendary, gid):
    if not gid:
        return None
    g = legendary.get(gid)
    if not g:
        return {"name": "（不明: %s）" % gid, "desc": ""}
    return {"name": g.get("name", ""), "desc": g.get("description", "")}


def make_skills(skill, legendary, quest_id, gvg_id, gvg_auto_id, legendary_id):
    return {
        "quest": resolve_skill(skill, quest_id),
        "gvg": resolve_skill(skill, gvg_id),
        "gvgAuto": resolve_skill(skill, gvg_auto_id),
        "legendary": resolve_legendary(legendary, legendary_id),
    }


# ---------------------------------------------------------------------------
# 技能文本解析（用于筛选）
# ---------------------------------------------------------------------------
_ROMAN = r"[Ⅰ-Ⅿ]"   # Ⅰ Ⅱ … Ⅹ …

# 四种属性敏感效果（异属性条件下触发），用其后果子句各自判断（一句可同时含多个）
_RE_MT = re.compile(r"次の攻撃時にダメージが[0-9.]+%アップするスタック")
_RE_AN = re.compile(r"次の支援/妨害時に支援/妨害効果が[0-9.]+%アップするスタック")
_RE_BA = re.compile(r"次の被ダメージ時に被ダメージを[0-9.]+%ダウンさせるスタック")
_RE_CT = re.compile(r"劣勢時は効果が[0-9.]+倍になる")
_RE_ET = re.compile(r"次の回復時に回復効果が[0-9.]+%アップするスタック")  # Et: 自身下次回复量↑
_RE_COND = re.compile(r"異なる場合、([^。]*)")           # 异属性条件后果子句
_RE_EH = re.compile(r"スキル効果が[0-9.]+倍に")
_RE_MN = re.compile(r"MP消費を(?:大幅に)?抑え")


def target_letter(name):
    """技能名末尾罗马数字前的字母（目标数 A-G）。"""
    m = re.search(r"([A-Z])\s*" + _ROMAN + r"+\+?\s*$", name or "")
    return m.group(1) if m else ""


def stat_flags(desc):
    """通攻/特攻/通防/特防/属攻/属防 的增减，编码：pa/ma/pd/md/ea/ed + (+/-)。

    每个属性 token 关联其所在句（到下一个「。」）内最近的 アップ/ダウン。
    """
    flags = set()
    toks = []
    for m in re.finditer(r"Sp\.ATK", desc):
        toks.append((m.start(), "ma"))
    for m in re.finditer(r"Sp\.DEF", desc):
        toks.append((m.start(), "md"))
    for m in re.finditer(r"ATK", desc):
        if desc[max(0, m.start() - 3):m.start()] != "Sp.":
            toks.append((m.start(), "pa"))
    for m in re.finditer(r"DEF", desc):
        if desc[max(0, m.start() - 3):m.start()] != "Sp.":
            toks.append((m.start(), "pd"))
    for m in re.finditer(r"属性攻撃力", desc):
        toks.append((m.start(), "ea"))
    for m in re.finditer(r"属性防御力", desc):
        toks.append((m.start(), "ed"))
    for pos, code in toks:
        end = desc.find("。", pos)
        if end == -1:
            end = len(desc)
        seg = desc[pos:end]
        up = seg.find("アップ")
        dn = seg.find("ダウン")
        cand = []
        if up != -1:
            cand.append((up, "+"))
        if dn != -1:
            cand.append((dn, "-"))
        if cand:
            cand.sort()
            flags.add(code + cand[0][1])
    return flags


def skill_feature_codes(sk):
    """QuestSkill / GvgSkill 的特性集合（用于技能特性筛选）。"""
    if not sk:
        return set()
    desc = sk.get("desc", "") or ""
    codes = set()
    if _RE_MT.search(desc):
        codes.add("Mt")
    if _RE_AN.search(desc):
        codes.add("An")
    if _RE_BA.search(desc):
        codes.add("Ba")
    if _RE_ET.search(desc):
        codes.add("Et")
    if _RE_CT.search(desc):
        codes.add("CT")
    for m in _RE_COND.finditer(desc):
        cons = m.group(1)
        if _RE_EH.search(cons):
            codes.add("EH")
        if "効果対象範囲が最大に" in cons:
            codes.add("SD")
        if _RE_MN.search(cons):
            codes.add("MN")
    codes |= stat_flags(desc)
    return codes


def gvgauto_codes(sk):
    """GvgAutoSkill 的辅助特性集合（按技能名识别）。"""
    if not sk:
        return set()
    name = sk.get("name", "") or ""
    codes = set()
    if "ダメージUP" in name:
        codes.add("dmgup")
    if "支援UP" in name:
        codes.add("supup")
    if "回復UP" in name:
        codes.add("healup")
    if "獲得マッチPtUP" in name:
        codes.add("ptup")
    if "効果範囲+1" in name:
        codes.add("rangeup")
    return codes


# ---------------------------------------------------------------------------
# 构建展示条目
# ---------------------------------------------------------------------------
def make_entry(card, card_type, stats, skills, order,
               awk="none", base_type=0, add_type=0, role=""):
    pa, pd, ma, md = stats[1], stats[2], stats[3], stats[4]
    q, g, ga = skills.get("quest"), skills.get("gvg"), skills.get("gvgAuto")
    return {
        "uniqueId": card["uniqueId"],
        "cardMstId": card["cardMstId"],
        "name": card["name"],
        "cardType": card_type,
        "attribute": card["attribute"],
        "gradeType": card["gradeType"],
        "cost": card["deckCost"],
        "pa": pa, "ma": ma, "pd": pd, "md": md,
        "power": pa + pd + ma + md,
        "order": order,   # 在 getCardMstList.json 中的位置，越大越新
        "skills": skills,
        # —— 角标合成所需：觉醒类型 + 原始/新增类别 + 本条目角色 ——
        "awk": awk, "baseType": base_type, "addType": add_type, "role": role,
        # —— 用于筛选的技能派生数据（Quest / Gvg 由开关切换；GvgAuto 独立）——
        "tq": target_letter(q["name"]) if q else "",
        "tg": target_letter(g["name"]) if g else "",
        "fq": skill_feature_codes(q),
        "fg": skill_feature_codes(g),
        "ga": gvgauto_codes(ga),
    }


def build_entries(cards, lbb, skill, legendary, ultimate, super_by_card):
    # 每个 uniqueId 取最高 rarity 的版本；order_of 记录其在 JSON 中的位置（越大越新）
    top = {}
    max_rarity = collections.defaultdict(int)
    order_of = {}
    for i, c in enumerate(cards):
        max_rarity[c["uniqueId"]] = max(max_rarity[c["uniqueId"]], c["rarity"])
        order_of[c["uniqueId"]] = i   # 最后一次出现的位置
    for c in cards:
        uid = c["uniqueId"]
        if c["rarity"] == max_rarity[uid]:
            top[uid] = c

    entries = []
    for uid, card in top.items():
        if max_rarity[uid] < 6:
            continue
        cmid = card["cardMstId"]
        order = order_of[uid]
        card_lbb = lbb.get(card["limitBreakBonusMstId"])

        if cmid in super_by_card:
            # —— 超觉醒卡：每个超觉醒类别各一条 ——
            for s in super_by_card[cmid]:
                s_lbb = lbb.get(s["limitBreakBonusMstId"])
                stats = compute_stats(s, TYPE_FIELDS, None, s_lbb, include_awk=True)
                skills = make_skills(
                    skill, legendary,
                    s.get("questSkillMstId"), s.get("gvgSkillMstId"),
                    s.get("gvgAutoSkillMstId"), s.get("legendarySkillGroupMstId"),
                )
                entries.append(make_entry(card, s["cardType"], stats, skills, order,
                                          awk="super"))

        elif card["awakenedAddCardType"] != 0:
            # —— 可觉醒卡：觉醒后的两个类别各一条 ——
            # (1) 原 cardType（觉醒后）
            base_stats = compute_stats(card, TYPE_FIELDS, card, card_lbb, include_awk=True)
            base_skills = make_skills(
                skill, legendary,
                card.get("awakenedQuestSkillMstId"), card.get("awakenedGvgSkillMstId"),
                card.get("awakenedGvgAutoSkillMstId"), card.get("awakenedLegendarySkillGroupMstId"),
            )
            entries.append(make_entry(
                card, card["cardType"], base_stats, base_skills, order,
                awk="awakening", base_type=card["cardType"],
                add_type=card["awakenedAddCardType"], role="base"))

            # (2) awakenedAddCardType
            add_skills = make_skills(
                skill, legendary,
                card.get("awakenedAddQuestSkillMstId"), card.get("awakenedAddGvgSkillMstId"),
                card.get("awakenedAddGvgAutoSkillMstId"), card.get("awakenedAddLegendarySkillGroupMstId"),
            )
            u = ultimate.get(cmid)
            if u is not None:
                # Ultimate 觉醒卡：新增类别面板独立
                u_lbb = lbb.get(u["awakenedAddLimitBreakBonusMstId"])
                add_stats = compute_stats(u, ULT_TYPE_FIELDS, u, u_lbb, include_awk=True)
            else:
                # 普通可觉醒卡：两个类别面板相同
                add_stats = base_stats
            entries.append(make_entry(
                card, card["awakenedAddCardType"], add_stats, add_skills, order,
                awk="awakening", base_type=card["cardType"],
                add_type=card["awakenedAddCardType"], role="add"))

        else:
            # —— 普通卡：单条 ——
            stats = compute_stats(card, TYPE_FIELDS, None, card_lbb, include_awk=False)
            skills = make_skills(
                skill, legendary,
                card.get("questSkillMstId"), card.get("gvgSkillMstId"),
                card.get("gvgAutoSkillMstId"), card.get("legendarySkillGroupMstId"),
            )
            entries.append(make_entry(card, card["cardType"], stats, skills, order))

    return entries


# ---------------------------------------------------------------------------
# 自检（与给定示例核对）
# ---------------------------------------------------------------------------
def self_check(entries):
    def find(uid, ct=None):
        for e in entries:
            if e["uniqueId"] == uid and (ct is None or e["cardType"] == ct):
                return e
        return None

    e1 = find(10109004)
    assert e1 is not None and e1["pa"] == 6915, \
        "自检失败: uniqueId 10109004 通攻 应为 6915, 实际 %s" % (e1 and e1["pa"])
    e2 = find(20000267, 7)
    assert e2 is not None and e2["pa"] == 9207, \
        "自检失败: uniqueId 20000267 cardType7 通攻 应为 9207, 实际 %s" % (e2 and e2["pa"])
    print("自检通过: 6915 / 9207 ✓")


# ---------------------------------------------------------------------------
# HTML 渲染
# ---------------------------------------------------------------------------
def fmt(text):
    """转义日文文本，并把换行（真实换行或字面 \\n）转为 <br>。"""
    if text is None:
        return ""
    text = text.replace("\\n", "\n")
    text = html.escape(text)
    return text.replace("\n", "<br>")


def render_skill_cell(sk, icon, col_class):
    """渲染一个技能单元格：类型图标 + 技能名 + 效果描述。"""
    if sk is None:
        return '<td class="skill-cell empty %s"></td>' % col_class
    name = fmt(sk["name"])
    desc = fmt(sk["desc"])
    return (
        '<td class="skill-cell {col}">'
        '<div class="skill-name"><img class="skill-i" src="{icon}" alt="">{name}</div>'
        '<div class="skill-desc">{desc}</div>'
        "</td>"
    ).format(col=col_class, icon=icon, name=name, desc=desc)


def render_row(e):
    ct = e["cardType"]
    attr = e["attribute"]
    grade = e["gradeType"]
    ct_label = CARD_TYPE_LABEL.get(ct, str(ct))
    attr_label = ATTRIBUTE_LABEL.get(attr, str(attr))
    icon_url = CARD_ICON_URL.format(uid=e["uniqueId"])
    marker = card_markers.marker_for(e, "list")
    frame = card_markers.frame_rel(grade == 2)

    grade_badge = ""
    if grade != 0:
        grade_badge = ' <span class="badge grade grade-{g}">{gl}</span>'.format(
            g=grade, gl=GRADE_LABEL.get(grade, str(grade)))

    pa, ma, pd, md = e["pa"], e["ma"], e["pd"], e["md"]
    papd, mamd, pama, pdmd = pa + pd, ma + md, pa + ma, pd + md

    sk = e["skills"]
    skill_cells = (
        render_skill_cell(sk.get("quest"), "assets/Skill1.png", "col-quest")
        + render_skill_cell(sk.get("gvg"), "assets/Skill2.png", "col-gvg")
        + render_skill_cell(sk.get("gvgAuto"), "assets/Skill3.png", "col-gvgAuto")
        + render_skill_cell(sk.get("legendary"), "assets/Skill4.png", "col-legendary")
    )

    return (
        '<tr class="card" data-power="{power}" data-cost="{cost}" data-attr="{attr}" '
        'data-type="{ct}" data-grade="{grade}" data-order="{order}" '
        'data-pa="{pa}" data-ma="{ma}" data-pd="{pd}" data-md="{md}" '
        'data-papd="{papd}" data-mamd="{mamd}" data-pama="{pama}" data-pdmd="{pdmd}" '
        'data-tq="{tq}" data-tg="{tg}" data-fq="{fq}" data-fg="{fg}" data-ga="{ga}" '
        'data-name="{name_attr}">'
        '<td class="c-icon col-icon"><span class="cardimg">'
        '<img class="art" loading="lazy" src="{icon_url}" alt="" '
        'onerror="this.classList.add(\'broken\')">'
        '<img class="frame" src="{frame}" alt="">'
        '<img class="mark" src="{marker}" alt=""></span></td>'
        '<td class="c-name col-name">{name}{grade_badge}</td>'
        '<td class="c-tag col-type"><img src="{ct_icon}" alt="">{ct_label}</td>'
        '<td class="c-tag attr-{attr} col-attr"><img src="{attr_icon}" alt="">{attr_label}</td>'
        '<td class="num col-pa">{pa}</td><td class="num col-ma">{ma}</td>'
        '<td class="num col-pd">{pd}</td><td class="num col-md">{md}</td>'
        '<td class="num power col-power">{power}</td><td class="num col-cost">{cost}</td>'
        '<td class="num col-papd">{papd}</td><td class="num col-mamd">{mamd}</td>'
        '<td class="num col-pama">{pama}</td><td class="num col-pdmd">{pdmd}</td>'
        '{skill_cells}'
        '</tr>'
    ).format(
        power=e["power"], cost=e["cost"], attr=attr, ct=ct, grade=grade, order=e["order"],
        pa=pa, ma=ma, pd=pd, md=md, papd=papd, mamd=mamd, pama=pama, pdmd=pdmd,
        tq=e["tq"], tg=e["tg"],
        fq=" ".join(sorted(e["fq"])), fg=" ".join(sorted(e["fg"])), ga=" ".join(sorted(e["ga"])),
        name_attr=html.escape(e["name"], quote=True),
        icon_url=icon_url, frame=frame, marker=marker,
        name=fmt(e["name"]), grade_badge=grade_badge,
        ct_icon="assets/CardType%d.png" % ct, ct_label=ct_label,
        attr_icon="assets/Attribute%d.png" % attr, attr_label=attr_label,
        skill_cells=skill_cells,
    )


# 列定义：(key, 表头文案, 默认是否显示)。4 个组合列默认隐藏，可在“表示列”中勾选。
COL_DEFS = [
    ("icon", "図", True), ("name", "名称", True), ("type", "類別", True), ("attr", "属性", True),
    ("pa", "通攻", True), ("ma", "特攻", True), ("pd", "通防", True), ("md", "特防", True),
    ("power", "戦闘力", True), ("cost", "Cost", True),
    ("papd", "通攻+通防", False), ("mamd", "特攻+特防", False),
    ("pama", "通攻+特攻", False), ("pdmd", "通防+特防", False),
    ("quest", "対HUGE技能", True), ("gvg", "GVG技能", True),
    ("gvgAuto", "辅助技能", True), ("legendary", "Legendary技能", True),
]

# 技能特性筛选项（针对 Quest/Gvg，由开关切换）：(code, label)
FEATURE_DEFS = [
    ("Mt", "Mt:「攻撃ダメ20%UP」スタック"),
    ("An", "An:「支援/妨害30%UP」スタック"),
    ("Ba", "Ba:「被ダメ30%DOWN」スタック"),
    ("Et", "Et:「次回復30%UP」スタック"),
    ("EH", "EH: 異属性でスキル効果UP"),
    ("SD", "SD: 異属性で効果範囲最大"),
    ("MN", "MN: 異属性でMP消費DOWN"),
    ("CT", "CT: 劣勢で効果UP"),
    ("pa+", "通攻UP"), ("pa-", "通攻DOWN"),
    ("ma+", "特攻UP"), ("ma-", "特攻DOWN"),
    ("pd+", "通防UP"), ("pd-", "通防DOWN"),
    ("md+", "特防UP"), ("md-", "特防DOWN"),
    ("ea+", "属攻UP"), ("ea-", "属攻DOWN"),
    ("ed+", "属防UP"), ("ed-", "属防DOWN"),
]
# GvgAuto 辅助特性筛选项：(code, label)
GA_DEFS = [
    ("dmgup", "ダメージUP"), ("supup", "支援UP"),
    ("healup", "回復UP"), ("ptup", "獲得マッチPtUP"),
    ("rangeup", "効果範囲+1"),
]


def build_dropdown(key, label, options):
    """生成一个复选框下拉筛选器。options: [(value, text), ...]，交给前端做交集筛选。"""
    boxes = "".join(
        '<label><input type="checkbox" data-f="{k}" value="{v}"> {t}</label>'.format(
            k=key, v=html.escape(str(v), quote=True), t=html.escape(str(t)))
        for v, t in options
    )
    return (
        '<span class="dd"><button class="ddbtn" type="button" data-dd="{k}" data-label="{lbl}">'
        '{lbl} ▾</button><div class="ddpanel" data-ddp="{k}">{boxes}</div></span>'
    ).format(k=key, lbl=html.escape(label), boxes=boxes)


def render_html(entries):
    # 默认按更新顺序降序（新 -> 旧）
    entries = sorted(entries, key=lambda e: e["order"], reverse=True)
    rows_html = "\n".join(render_row(e) for e in entries)

    # 列显隐复选框
    col_checkboxes = "".join(
        '<label><input type="checkbox" data-col="{k}"{chk}> {l}</label>'.format(
            k=k, l=html.escape(lbl), chk=" checked" if d else "")
        for k, lbl, d in COL_DEFS
    )
    hidden = ",".join(".col-%s" % k for k, lbl, d in COL_DEFS if not d)
    col_init_style = (hidden + "{display:none}") if hidden else ""

    # 动态选项：Cost 与 目標数（A-G）取自实际数据
    costs = sorted({e["cost"] for e in entries})
    targets = sorted({t for e in entries for t in (e["tq"], e["tg"]) if t})

    dropdowns = {
        "__DD_ATTR__": build_dropdown("attr", "属性", sorted(ATTRIBUTE_LABEL.items())),
        "__DD_TYPE__": build_dropdown("type", "類別", sorted(CARD_TYPE_LABEL.items())),
        "__DD_GRADE__": build_dropdown("grade", "等級", sorted(GRADE_LABEL.items())),
        "__DD_COST__": build_dropdown("cost", "Cost", [(c, c) for c in costs]),
        "__DD_TARGET__": build_dropdown("target", "タゲ数", [(t, t) for t in targets]),
        "__DD_FEAT__": build_dropdown("feat", "技能特性", FEATURE_DEFS),
        "__DD_GA__": build_dropdown("ga", "補助特性", GA_DEFS),
    }

    out = HTML_TEMPLATE
    for token, frag in dropdowns.items():
        out = out.replace(token, frag)
    out = out.replace("__COL_CHECKBOXES__", col_checkboxes)
    out = out.replace("__COL_INIT_STYLE__", col_init_style)
    out = out.replace("__TOTAL__", str(len(entries)))
    out = out.replace("__ROWS__", rows_html)
    return out


# 模板使用 __TOKEN__ 占位符 + str.replace 注入，避免 CSS/JS 大括号转义问题。
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>カードリスト</title>
<style>
  :root { --toolbar-h:46px; --line:#000; --head-bg:#5b6b8c; --head-hover:#6b7da0; --head-fg:#fff;
          --row1:#ffffff; --row2:#eeeeee; --txt:#111; }
  * { box-sizing: border-box; }
  body { margin:0; background:#fff; color:var(--txt);
         font-family: "Segoe UI", "Microsoft YaHei", "Hiragino Sans", "Meiryo", sans-serif; font-size:13px; }
  header { position:sticky; top:0; z-index:30; background:#dde2ea; border-bottom:1px solid #9aa3b8;
           padding:8px 14px; display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
  header h1 { font-size:16px; margin:0 12px 0 0; color:#222; }
  header label { color:#444; margin-right:4px; }
  header input, header select, .ddbtn, #clear { background:#fff; color:#111; border:1px solid #9aa3b8;
           border-radius:6px; padding:5px 8px; font-size:13px; }
  header input[type=text] { width:160px; }
  #clear { cursor:pointer; }
  #count { color:#444; margin-left:auto; }

  /* 通用复选框下拉（筛选 / 列显隐） */
  .dd { position:relative; }
  .ddbtn { cursor:pointer; }
  .ddbtn.active { background:#dce7f6; border-color:#5b6b8c; font-weight:600; }
  .ddpanel { display:none; position:absolute; top:calc(100% + 4px); left:0; z-index:60; background:#fff;
             border:1px solid #888; border-radius:6px; padding:6px 8px; max-height:72vh; overflow:auto;
             box-shadow:0 6px 16px rgba(0,0,0,.25); min-width:150px; }
  .ddpanel.open { display:block; }
  .ddpanel label { display:block; color:#111; margin:0; padding:3px 6px; white-space:nowrap; cursor:pointer; }
  .ddpanel label:hover { background:#eef; }
  .ddpanel input { margin-right:6px; }

  /* 表格：白/淡灰行 + 全黑网格线 */
  table { border-collapse: separate; border-spacing:0; width:100%;
          border-top:1px solid var(--line); border-left:1px solid var(--line); }
  th, td { border-right:1px solid var(--line); border-bottom:1px solid var(--line); }
  thead th { position:sticky; top:var(--toolbar-h); z-index:20; background:var(--head-bg); color:var(--head-fg);
             padding:8px 10px; text-align:left; white-space:nowrap; font-weight:600;
             cursor:pointer; user-select:none; }
  thead th:hover { background:var(--head-hover); }
  thead th.num { text-align:right; }
  thead th .arrow { font-size:11px; }
  thead th .skh { width:16px; height:16px; object-fit:contain; vertical-align:-3px; margin-right:4px; }

  tbody td { padding:7px 10px; vertical-align:middle; color:var(--txt); }
  tbody tr:nth-child(odd) { background:var(--row1); }
  tbody tr:nth-child(even) { background:var(--row2); }
  tbody tr:hover { background:#fff3cd; }
  tr.hidden { display:none; }

  /* 卡图叠放：卡图 + 稀有度边框 + 右上角类别角标
     角标比例受限：纵向 ≤1/4 高、横向 ≤1/2 宽（觉醒角标最宽，约 0.42 宽，满足） */
  .cardimg { position:relative; display:block; width:76px; height:76px; }
  .cardimg .art { width:100%; height:100%; object-fit:cover; display:block; border-radius:6px; }
  .cardimg .art.broken { visibility:hidden; }
  .cardimg .frame { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
  .cardimg .mark { position:absolute; top:-2px; right:-3px; max-height:38%;
                   height:auto; width:auto; pointer-events:none;
                   filter:drop-shadow(0 1px 1px rgba(0,0,0,.4)); }
  .c-icon { width:84px; }
  .c-name { font-weight:600; min-width:150px; max-width:240px; line-height:1.35; }
  .c-tag { white-space:nowrap; }
  .c-tag img { width:32px; height:32px; object-fit:contain; vertical-align:middle; margin-right:6px; }
  .num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
  .num.power { font-weight:600; }

  .badge { display:inline-flex; align-items:center; border:1px solid #999; border-radius:999px;
           padding:0 7px; font-size:11px; font-weight:600; }
  .grade-1 { background:#fff3cd; color:#8a6d00; border-color:#d9b54a; }
  .grade-2 { background:#ffd9e6; color:#a01f57; border-color:#e07ba6; }

  .skill-cell { min-width:200px; max-width:300px; }
  .skill-name { font-weight:600; }
  .skill-name img.skill-i { width:15px; height:15px; object-fit:contain; vertical-align:-2px; margin-right:4px; }
  .skill-desc { color:#333; line-height:1.4; margin-top:2px; }
</style>
<style id="colstyle">__COL_INIT_STYLE__</style>
</head>
<body>
<header>
  <h1>カードリスト</h1>
  <span><label>検索</label><input id="q" type="text" placeholder="名前で検索"></span>
  __DD_ATTR__
  __DD_TYPE__
  __DD_GRADE__
  __DD_COST__
  __DD_TARGET__
  __DD_FEAT__
  __DD_GA__
  <span><label>特性対象</label><select id="skillmode"><option value="g">GVG技能</option><option value="q">対HUGE技能</option></select></span>
  <span class="dd">
    <button class="ddbtn" type="button" data-dd="cols" data-label="表示列">表示列 ▾</button>
    <div class="ddpanel" data-ddp="cols">__COL_CHECKBOXES__</div>
  </span>
  <button id="clear" type="button">クリア</button>
  <span id="count"></span>
</header>
<table id="tbl">
<thead>
<tr>
  <th class="sortable col-icon" data-sort="order" data-get="num">図</th>
  <th class="sortable col-name" data-sort="name" data-get="attr">名称</th>
  <th class="sortable col-type" data-sort="type" data-get="num">類別</th>
  <th class="sortable col-attr" data-sort="attr" data-get="num">属性</th>
  <th class="sortable num col-pa" data-sort="pa" data-get="num">通攻</th>
  <th class="sortable num col-ma" data-sort="ma" data-get="num">特攻</th>
  <th class="sortable num col-pd" data-sort="pd" data-get="num">通防</th>
  <th class="sortable num col-md" data-sort="md" data-get="num">特防</th>
  <th class="sortable num col-power" data-sort="power" data-get="num">戦闘力</th>
  <th class="sortable num col-cost" data-sort="cost" data-get="num">Cost</th>
  <th class="sortable num col-papd" data-sort="papd" data-get="num">通攻+通防</th>
  <th class="sortable num col-mamd" data-sort="mamd" data-get="num">特攻+特防</th>
  <th class="sortable num col-pama" data-sort="pama" data-get="num">通攻+特攻</th>
  <th class="sortable num col-pdmd" data-sort="pdmd" data-get="num">通防+特防</th>
  <th class="sortable col-quest" data-sort="quest" data-get="cell"><img class="skh" src="assets/Skill1.png" alt="">対HUGE技能</th>
  <th class="sortable col-gvg" data-sort="gvg" data-get="cell"><img class="skh" src="assets/Skill2.png" alt="">GVG技能</th>
  <th class="sortable col-gvgAuto" data-sort="gvgAuto" data-get="cell"><img class="skh" src="assets/Skill3.png" alt="">辅助技能</th>
  <th class="sortable col-legendary" data-sort="legendary" data-get="cell"><img class="skh" src="assets/Skill4.png" alt="">Legendary技能</th>
</tr>
</thead>
<tbody id="rows">
__ROWS__
</tbody>
</table>
<script>
  var tbody = document.getElementById('rows');
  var header = document.querySelector('header');
  var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr.card'));
  var q = document.getElementById('q');
  var skillmode = document.getElementById('skillmode');
  var count = document.getElementById('count');
  var TOTAL = __TOTAL__;

  // 让粘性表头停在工具栏正下方
  function setHeadOffset() {
    document.documentElement.style.setProperty('--toolbar-h', header.offsetHeight + 'px');
  }
  window.addEventListener('resize', setHeadOffset);
  setHeadOffset();

  // ---------- 下拉面板开关 ----------
  function closePanels(except) {
    var ps = document.querySelectorAll('.ddpanel.open');
    for (var i = 0; i < ps.length; i++) if (ps[i] !== except) ps[i].classList.remove('open');
  }
  var ddbtns = document.querySelectorAll('.ddbtn');
  for (var i = 0; i < ddbtns.length; i++) {
    ddbtns[i].addEventListener('click', function(e) {
      var panel = document.querySelector('.ddpanel[data-ddp="' + this.dataset.dd + '"]');
      var willOpen = !panel.classList.contains('open');
      closePanels(panel);
      panel.classList.toggle('open', willOpen);
      e.stopPropagation();
    });
  }
  document.addEventListener('click', function(e) {
    if (e.target.closest && (e.target.closest('.ddpanel') || e.target.closest('.ddbtn'))) return;
    closePanels(null);
  });

  // ---------- 列显示控制 ----------
  var colStyle = document.getElementById('colstyle');
  var colCbs = document.querySelectorAll('input[data-col]');
  function applyCols() {
    var hidden = [];
    for (var i = 0; i < colCbs.length; i++) {
      if (!colCbs[i].checked) hidden.push('.col-' + colCbs[i].dataset.col);
    }
    colStyle.textContent = hidden.length ? hidden.join(',') + '{display:none}' : '';
  }

  // ---------- 过滤（各组之间取交集；组内单值列为「属于所选集合」，技能特性为「全部具备」）----------
  function selVals(group) {
    var arr = [], els = document.querySelectorAll('input[data-f="' + group + '"]:checked');
    for (var i = 0; i < els.length; i++) arr.push(els[i].value);
    return arr;
  }
  function hasAll(haveStr, need) {
    var have = haveStr.split(' ');
    for (var k = 0; k < need.length; k++) if (have.indexOf(need[k]) === -1) return false;
    return true;
  }
  function updateBtns() {
    var bs = document.querySelectorAll('.ddbtn[data-dd]');
    for (var i = 0; i < bs.length; i++) {
      var key = bs[i].dataset.dd;
      if (key === 'cols') continue;
      var n = document.querySelectorAll('input[data-f="' + key + '"]:checked').length;
      bs[i].textContent = bs[i].dataset.label + (n ? ' (' + n + ')' : '') + ' ▾';
      bs[i].classList.toggle('active', n > 0);
    }
  }
  function applyFilter() {
    var kw = q.value.trim().toLowerCase();
    var aS = selVals('attr'), tS = selVals('type'), gS = selVals('grade'), cS = selVals('cost'),
        tgS = selVals('target'), fS = selVals('feat'), gaS = selVals('ga');
    var mode = skillmode.value, shown = 0;
    for (var i = 0; i < rows.length; i++) {
      var d = rows[i].dataset, ok = true;
      if (aS.length && aS.indexOf(d.attr) === -1) ok = false;
      if (ok && tS.length && tS.indexOf(d.type) === -1) ok = false;
      if (ok && gS.length && gS.indexOf(d.grade) === -1) ok = false;
      if (ok && cS.length && cS.indexOf(d.cost) === -1) ok = false;
      if (ok && kw && d.name.toLowerCase().indexOf(kw) === -1) ok = false;
      if (ok && tgS.length) { var tv = (mode === 'q' ? d.tq : d.tg); if (tgS.indexOf(tv) === -1) ok = false; }
      if (ok && fS.length && !hasAll(mode === 'q' ? d.fq : d.fg, fS)) ok = false;
      if (ok && gaS.length && !hasAll(d.ga, gaS)) ok = false;
      rows[i].classList.toggle('hidden', !ok);
      if (ok) shown++;
    }
    count.textContent = shown + ' / ' + TOTAL + ' 件';
    updateBtns();
  }

  // 复选框变化：列显隐 / 筛选；技能特性对象切换
  document.addEventListener('change', function(e) {
    if (e.target.matches && e.target.matches('input[data-col]')) applyCols();
    else if (e.target.matches && e.target.matches('input[data-f]')) applyFilter();
    else if (e.target === skillmode) applyFilter();
  });
  q.addEventListener('input', applyFilter);
  document.getElementById('clear').addEventListener('click', function() {
    var cbs = document.querySelectorAll('input[data-f]');
    for (var i = 0; i < cbs.length; i++) cbs[i].checked = false;
    q.value = '';
    applyFilter();
  });

  // ---------- 排序（每列可点击）----------
  var sortTh = null, sortKey = 'order', sortGet = 'num', sortDir = -1;
  function cellText(row, idx) {
    var el = row.cells[idx].querySelector('.skill-name');
    return (el ? el.textContent : row.cells[idx].textContent) || '';
  }
  function doSort() {
    var idx = sortTh ? sortTh.cellIndex : 0;
    var numeric = (sortGet === 'num');
    rows.sort(function(a, b) {
      if (numeric)
        return ((parseInt(a.dataset[sortKey]) || 0) - (parseInt(b.dataset[sortKey]) || 0)) * sortDir;
      var va, vb;
      if (sortGet === 'attr') { va = a.dataset[sortKey] || ''; vb = b.dataset[sortKey] || ''; }
      else { va = cellText(a, idx); vb = cellText(b, idx); }
      return String(va).localeCompare(String(vb), 'ja') * sortDir;
    });
    var frag = document.createDocumentFragment();
    for (var i = 0; i < rows.length; i++) frag.appendChild(rows[i]);
    tbody.appendChild(frag);
    updateArrows();
  }
  function updateArrows() {
    var ths = document.querySelectorAll('thead th');
    for (var i = 0; i < ths.length; i++) {
      var ar = ths[i].querySelector('.arrow');
      if (ar) ar.remove();
    }
    if (sortTh) {
      var s = document.createElement('span');
      s.className = 'arrow';
      s.textContent = sortDir === 1 ? ' ▲' : ' ▼';
      sortTh.appendChild(s);
    }
  }
  var headThs = document.querySelectorAll('thead th.sortable');
  for (var i = 0; i < headThs.length; i++) {
    headThs[i].addEventListener('click', function() {
      if (sortTh === this) { sortDir = -sortDir; }
      else { sortTh = this; sortKey = this.dataset.sort; sortGet = this.dataset.get;
             sortDir = (sortGet === 'num') ? -1 : 1; }
      doSort();
    });
  }

  // 初始：列显隐 + 过滤 + 默认按「図」=更新順 降序
  applyCols();
  applyFilter();
  sortTh = document.querySelector('thead th[data-sort="order"]');
  doSort();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    cards, lbb, skill, legendary, ultimate, super_by_card = build_lookups()
    entries = build_entries(cards, lbb, skill, legendary, ultimate, super_by_card)
    self_check(entries)
    html_text = render_html(entries)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html_text)
    print("已生成 %d 条卡牌条目" % len(entries))
    print("输出文件: %s" % OUT)


if __name__ == "__main__":
    main()
