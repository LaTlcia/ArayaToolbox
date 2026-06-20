# -*- coding: utf-8 -*-
"""
Card list generator
====================
Build an HTML card list from the game masterdata JSON in A.RA.YA/MasterdataBase/.

How to run (in the localdb conda env):
    conda run -n localdb python localDB/generate_card_list.py
Then open the generated localDB/card_list.html in a browser.

The data updates regularly; re-run this script after each update for the latest list.
All displayed text is kept in the original Japanese (not translated).

See the plan file in this directory for details; the stat math is verified against
these two examples:
  - uniqueId 10109004 phys-atk = 6915
  - uniqueId 20000267 (cardType 7) phys-atk = 9207
Standard library only (json / html / os).
"""

import json
import html
import os
import re
import collections

import card_markers

# ---------------------------------------------------------------------------
# Paths (resolved relative to this script, independent of the cwd)
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)                       # workspace root
MST = os.path.join(ROOT, "A.RA.YA", "MasterdataBase")
OUT = os.path.join(SCRIPT_DIR, "card_list.html")

# Card icons now come from the local mirror (downloaded into assets/remote/ by assets_sync.py; offline-capable)
CARD_ICON_URL = "assets/remote/Image/CardIcon/S/CardIconS0{uid}.png"

# ---------------------------------------------------------------------------
# Label maps
# ---------------------------------------------------------------------------
CARD_TYPE_LABEL = {
    1: "通常単体", 2: "通常範囲", 3: "特殊単体", 4: "特殊範囲",
    5: "支援", 6: "妨害", 7: "回復",
}
ATTRIBUTE_LABEL = {1: "火", 2: "水", 3: "風", 4: "光", 5: "闇"}
GRADE_LABEL = {0: "普通", 1: "Legendary", 2: "Ultimate"}

# Four stats: type -> (normal/awakened max field, awakening bonus field)
TYPE_FIELDS = {
    1: ("maxPhysicalAttack", "awakenedAddPhysicalAttack"),    # phys-atk
    2: ("maxPhysicalDefense", "awakenedAddPhysicalDefense"),  # phys-def
    3: ("maxMagicalAttack", "awakenedAddMagicalAttack"),      # mag-atk
    4: ("maxMagicalDefense", "awakenedAddMagicalDefense"),    # mag-def
}
# Ultimate added-category stat fields
ULT_TYPE_FIELDS = {
    1: ("awakenedAddMaxPhysicalAttack", "awakenedAddPhysicalAttack"),
    2: ("awakenedAddMaxPhysicalDefense", "awakenedAddPhysicalDefense"),
    3: ("awakenedAddMaxMagicalAttack", "awakenedAddMagicalAttack"),
    4: ("awakenedAddMaxMagicalDefense", "awakenedAddMagicalDefense"),
}


# ---------------------------------------------------------------------------
# Data loading
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

    # Legendary: per group, take the highest (strongest) limitBreakCount tier
    legendary = {}
    for x in legendary_list:
        gid = x["legendarySkillGroupMstId"]
        cur = legendary.get(gid)
        if cur is None or x["limitBreakCount"] > cur["limitBreakCount"]:
            legendary[gid] = x

    # Super-awakening: cardMstId -> [records sorted by cardType]
    super_by_card = collections.defaultdict(list)
    for x in super_list:
        super_by_card[x["cardMstId"]].append(x)
    for recs in super_by_card.values():
        recs.sort(key=lambda r: r["cardType"])

    return cards, lbb, skill, legendary, ultimate, super_by_card


# ---------------------------------------------------------------------------
# Stat computation
# ---------------------------------------------------------------------------
def lbb_bonus(lbb_entry, t, awakened):
    """Sum of bonuses with type==t in a limit-break bonus entry (base or awakenedAdd)."""
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
    """Return the final stats {1:phys-atk, 2:phys-def, 3:mag-atk, 4:mag-def}.

    Each = max + (awakening bonus, optional) + base limit-break + (awakening limit-break, optional)
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
# Skill resolution
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
# Skill text parsing (for filtering)
# ---------------------------------------------------------------------------
_ROMAN = r"[Ⅰ-Ⅿ]"   # Ⅰ Ⅱ … Ⅹ …

# Four attribute-sensitive effects (triggered when attributes differ); judged by each consequence clause (a sentence may contain several)
_RE_MT = re.compile(r"次の攻撃時にダメージが[0-9.]+%アップするスタック")
_RE_AN = re.compile(r"次の支援/妨害時に支援/妨害効果が[0-9.]+%アップするスタック")
_RE_BA = re.compile(r"次の被ダメージ時に被ダメージを[0-9.]+%ダウンさせるスタック")
_RE_CT = re.compile(r"劣勢時は効果が[0-9.]+倍になる")
_RE_ET = re.compile(r"次の回復時に回復効果が[0-9.]+%アップするスタック")  # Et: self's next heal amount up
_RE_COND = re.compile(r"異なる場合、([^。]*)")           # consequence clause of the different-attribute condition
_RE_EH = re.compile(r"スキル効果が[0-9.]+倍に")
_RE_MN = re.compile(r"MP消費を(?:大幅に)?抑え")


def target_letter(name):
    """The letter before the trailing roman numeral in a skill name (target count A-G)."""
    m = re.search(r"([A-Z])\s*" + _ROMAN + r"+\+?\s*$", name or "")
    return m.group(1) if m else ""


def stat_flags(desc):
    """Up/down of phys-atk/mag-atk/phys-def/mag-def/elem-atk/elem-def, coded pa/ma/pd/md/ea/ed + (+/-).

    Each stat token is tied to the nearest アップ/ダウン within its sentence (up to the next 「。」).
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
    """Feature set of a QuestSkill / GvgSkill (for the skill-feature filter)."""
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
    """Support-feature set of a GvgAutoSkill (identified by skill name)."""
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
# Build display entries
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
        "order": order,   # position in getCardMstList.json; larger = newer
        "skills": skills,
        # -- needed for marker compositing: awakening type + original/added category + this entry's role --
        "awk": awk, "baseType": base_type, "addType": add_type, "role": role,
        # -- skill-derived data for filtering (Quest / Gvg toggled by a switch; GvgAuto independent) --
        "tq": target_letter(q["name"]) if q else "",
        "tg": target_letter(g["name"]) if g else "",
        "fq": skill_feature_codes(q),
        "fg": skill_feature_codes(g),
        "ga": gvgauto_codes(ga),
    }


def build_entries(cards, lbb, skill, legendary, ultimate, super_by_card):
    # For each uniqueId take the highest-rarity version; order_of records its position in the JSON (larger = newer)
    top = {}
    max_rarity = collections.defaultdict(int)
    order_of = {}
    for i, c in enumerate(cards):
        max_rarity[c["uniqueId"]] = max(max_rarity[c["uniqueId"]], c["rarity"])
        order_of[c["uniqueId"]] = i   # position of its last occurrence
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
            # -- Super-awakening card: one entry per super-awakening category --
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
            # -- Awakenable card: one entry per the two post-awakening categories --
            # (1) original cardType (after awakening)
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
                # Ultimate awakening card: the added category has its own stats
                u_lbb = lbb.get(u["awakenedAddLimitBreakBonusMstId"])
                add_stats = compute_stats(u, ULT_TYPE_FIELDS, u, u_lbb, include_awk=True)
            else:
                # Normal awakenable card: both categories share the same stats
                add_stats = base_stats
            entries.append(make_entry(
                card, card["awakenedAddCardType"], add_stats, add_skills, order,
                awk="awakening", base_type=card["cardType"],
                add_type=card["awakenedAddCardType"], role="add"))

        else:
            # -- Normal card: single entry --
            stats = compute_stats(card, TYPE_FIELDS, None, card_lbb, include_awk=False)
            skills = make_skills(
                skill, legendary,
                card.get("questSkillMstId"), card.get("gvgSkillMstId"),
                card.get("gvgAutoSkillMstId"), card.get("legendarySkillGroupMstId"),
            )
            entries.append(make_entry(card, card["cardType"], stats, skills, order))

    return entries


# ---------------------------------------------------------------------------
# Self-check (against the given examples)
# ---------------------------------------------------------------------------
def self_check(entries):
    def find(uid, ct=None):
        for e in entries:
            if e["uniqueId"] == uid and (ct is None or e["cardType"] == ct):
                return e
        return None

    e1 = find(10109004)
    assert e1 is not None and e1["pa"] == 6915, \
        "self-check failed: uniqueId 10109004 phys-atk should be 6915, got %s" % (e1 and e1["pa"])
    e2 = find(20000267, 7)
    assert e2 is not None and e2["pa"] == 9207, \
        "self-check failed: uniqueId 20000267 cardType7 phys-atk should be 9207, got %s" % (e2 and e2["pa"])
    print("self-check passed: 6915 / 9207 ✓")


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------
def fmt(text):
    """Escape Japanese text and turn newlines (real or literal \\n) into <br>."""
    if text is None:
        return ""
    text = text.replace("\\n", "\n")
    text = html.escape(text)
    return text.replace("\n", "<br>")


def render_skill_cell(sk, icon, col_class):
    """Render one skill cell: type icon + skill name + effect description."""
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


# Column defs: (key, header text, shown by default). The 4 combined columns are hidden by default; toggle them in "表示列".
COL_DEFS = [
    ("icon", "図", True), ("name", "名称", True), ("type", "類別", True), ("attr", "属性", True),
    ("pa", "通攻", True), ("ma", "特攻", True), ("pd", "通防", True), ("md", "特防", True),
    ("power", "戦闘力", True), ("cost", "Cost", True),
    ("papd", "通攻+通防", False), ("mamd", "特攻+特防", False),
    ("pama", "通攻+特攻", False), ("pdmd", "通防+特防", False),
    ("quest", "対HUGE技能", True), ("gvg", "GVG技能", True),
    ("gvgAuto", "辅助技能", True), ("legendary", "Legendary技能", True),
]

# Skill-feature filter options (for Quest/Gvg, toggled by a switch): (code, label)
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
# GvgAuto support-feature filter options: (code, label)
GA_DEFS = [
    ("dmgup", "ダメージUP"), ("supup", "支援UP"),
    ("healup", "回復UP"), ("ptup", "獲得マッチPtUP"),
    ("rangeup", "効果範囲+1"),
]


def build_dropdown(key, label, options):
    """Build a checkbox dropdown filter. options: [(value, text), ...], intersected on the front end."""
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
    # Default sort: by update order descending (new -> old)
    entries = sorted(entries, key=lambda e: e["order"], reverse=True)
    rows_html = "\n".join(render_row(e) for e in entries)

    # Column show/hide checkboxes
    col_checkboxes = "".join(
        '<label><input type="checkbox" data-col="{k}"{chk}> {l}</label>'.format(
            k=k, l=html.escape(lbl), chk=" checked" if d else "")
        for k, lbl, d in COL_DEFS
    )
    hidden = ",".join(".col-%s" % k for k, lbl, d in COL_DEFS if not d)
    col_init_style = (hidden + "{display:none}") if hidden else ""

    # Dynamic options: Cost and target count (A-G) taken from the actual data
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


# The template uses __TOKEN__ placeholders + str.replace injection to avoid escaping CSS/JS braces.
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

  /* Generic checkbox dropdown (filters / column show-hide) */
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

  /* Table: white/light-grey rows + all-black grid lines */
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

  /* Card image stack: art + rarity frame + top-right category marker
     Marker size is bounded: <=1/4 height, <=1/2 width (awakening marker is widest, ~0.42 width, OK) */
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

  /* Global watermark: fixed, covers the whole viewport, top layer, very low opacity (uniqueId 20000216 full art); always visible while scrolling and never blocks interaction */
  .watermark { position:fixed; inset:0; z-index:9999; pointer-events:none; }
  .watermark img { width:100%; height:100%; object-fit:cover; opacity:.1; user-select:none; }
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
<div class="watermark"><img src="assets/remote/Image/Card/Card020000216.jpg" alt=""></div>
<script>
  var tbody = document.getElementById('rows');
  var header = document.querySelector('header');
  var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr.card'));
  var q = document.getElementById('q');
  var skillmode = document.getElementById('skillmode');
  var count = document.getElementById('count');
  var TOTAL = __TOTAL__;

  // Keep the sticky header right below the toolbar
  function setHeadOffset() {
    document.documentElement.style.setProperty('--toolbar-h', header.offsetHeight + 'px');
  }
  window.addEventListener('resize', setHeadOffset);
  setHeadOffset();

  // ---------- Dropdown panel toggle ----------
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

  // ---------- Column visibility ----------
  var colStyle = document.getElementById('colstyle');
  var colCbs = document.querySelectorAll('input[data-col]');
  function applyCols() {
    var hidden = [];
    for (var i = 0; i < colCbs.length; i++) {
      if (!colCbs[i].checked) hidden.push('.col-' + colCbs[i].dataset.col);
    }
    colStyle.textContent = hidden.length ? hidden.join(',') + '{display:none}' : '';
  }

  // ---------- Filtering (groups intersected; single-value groups = "in the selected set", skill features = "has all") ----------
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

  // Checkbox changes: column show-hide / filter; skill-feature target switch
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

  // ---------- Sorting (each column clickable) ----------
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

  // Init: columns + filter + default sort by 「図」 = update order descending
  applyCols();
  applyFilter();
  sortTh = document.querySelector('thead th[data-sort="order"]');
  doSort();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    cards, lbb, skill, legendary, ultimate, super_by_card = build_lookups()
    entries = build_entries(cards, lbb, skill, legendary, ultimate, super_by_card)
    self_check(entries)
    html_text = render_html(entries)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html_text)
    print("Generated %d card entries" % len(entries))
    print("Output file: %s" % OUT)


if __name__ == "__main__":
    main()
