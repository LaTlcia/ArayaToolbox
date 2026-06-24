# -*- coding: utf-8 -*-
"""
Deck builder generator
======================
Build an HTML deck builder from the game masterdata in A.RA.YA/MasterdataBase/.
Reuses the data layer from generate_card_list.py (build_lookups / build_entries)
without modifying that file.

How to run (in the localdb conda env):
    conda run -n localdb python localDB/generate_deck_builder.py
Then open the generated localDB/deck_builder.html in a browser.
(You can also run build_all.py to refresh both the list and the deck builder.)

Key rules:
  * A deck = up to 5 Legendary cards (gradeType==1) + up to 20 other cards; one copy per uniqueId.
  * Decks split into 前衛 / 後衛: 前衛 allows Type 1-4, 後衛 allows Type 5-7 (toggle).
  * The picker only shows art / GvgSkill / GvgAutoSkill; Legendary and other cards are listed
    separately, both by update order (new -> old); among the others, Ultimate (gradeType==2)
    comes before normal cards.
  * Stats: counts by category / attribute; Mt/An/Ba (card count + total marks) / EH/SD/MN/CT;
    counts of the five passives; per-level counts of the four leveled passives (excluding 効果範囲+1);
    plus per-stat change counts and a buff-combination breakdown. ("副" level = roman numeral - 1, keeping "+".)
  * Deck code: an allb.game-db.tw deck-builder URL (see deckCode/loadCode); one-click restore.
All displayed text is kept in the original Japanese (not translated).
"""

import os as _os, sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import re
import html
import json

import config
from generate_card_list import (
    build_lookups, build_entries, load_mst,
    CARD_ICON_URL, CARD_TYPE_LABEL, ATTRIBUTE_LABEL,
    FEATURE_DEFS, GA_DEFS, build_dropdown, fmt, stat_flags,
)
import card_markers
import skill_calc as sc
import generate_tactics_list as gtl

OUT = config.DECK_BUILDER_OUT
SKILL_ICON = config.SKILL_ICON
TGT_ICON = config.TGT_ICON
# ---------------------------------------------------------------------------
# Passive (GvgAuto) level / mark parsing
# ---------------------------------------------------------------------------
ROMAN_VAL = {"Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5,
             "Ⅵ": 6, "Ⅶ": 7, "Ⅷ": 8, "Ⅸ": 9, "Ⅹ": 10}
VAL_ROMAN = {v: k for k, v in ROMAN_VAL.items()}
RE_LV = re.compile(r"([Ⅰ-Ⅹ])(\++)?\s*$")   # trailing roman numeral + any number of "+" (e.g. Ⅴ+/Ⅴ++)

# The four leveled passives (identified by name); 効果範囲+1 has no level, counted separately
PASSIVE_KEYS = [
    ("dmgup", "ダメージUP"), ("supup", "支援UP"),
    ("healup", "回復UP"), ("ptup", "獲得マッチPtUP"),
]

# Mt/An/Ba mark phrases + stack counts in a Gvg skill
RE_MT = re.compile(r"次の攻撃時にダメージが[0-9.]+%アップするスタック")
RE_AN = re.compile(r"次の支援/妨害時に支援/妨害効果が[0-9.]+%アップするスタック")
RE_BA = re.compile(r"次の被ダメージ時に被ダメージを[0-9.]+%ダウンさせるスタック")
RE_KAI = re.compile(r"(\d+)回蓄積")


def lv_label(value, plus):
    """Level value + "+" marker -> display label (e.g. 5,'+' -> 'Ⅴ+'; 0 -> '0')."""
    roman = VAL_ROMAN.get(value, str(value)) if value >= 1 else "0"
    return roman + plus


def passive_levels(ga_skill):
    """Parse the four passives' (code, level label) from a GvgAuto skill name. A "副" segment's level = roman numeral - 1 (keeping +)."""
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
    """Total stack count for a mark type (Mt/An/Ba): the nearest 「N回蓄積」 after each mark phrase."""
    if not gvg_skill:
        return 0
    desc = gvg_skill.get("desc", "") or ""
    total = 0
    for m in phrase_re.finditer(desc):
        k = RE_KAI.search(desc, m.end())
        total += int(k.group(1)) if k else 1
    return total


# Four main stats (phys atk/def, mag atk/def) single icons: up/down
MAIN_UP = {"pa": 1, "pd": 2, "ma": 3, "md": 4}
MAIN_DN = {"pa": 5, "pd": 6, "ma": 7, "md": 8}
MAIN_ORDER = ["pa", "pd", "ma", "md"]   # phys-atk - phys-def - sp.atk(Sp.ATK) - sp.def(Sp.DEF)
# Combo icons for two same-direction main stats
COMBO_UP = {frozenset(["pa", "pd"]): 39, frozenset(["pa", "ma"]): 40, frozenset(["pa", "md"]): 41,
            frozenset(["pd", "ma"]): 42, frozenset(["ma", "md"]): 43, frozenset(["pd", "md"]): 44}
COMBO_DN = {k: v + 6 for k, v in COMBO_UP.items()}
# Element (fire/water/wind/light/dark) atk/def icons: base + (atk 0/def 2) + (up 0/down 1)
ELEM_BASE = {1: 18, 2: 22, 3: 26, 4: 30, 5: 34}
ELEM_CHAR = {"火": 1, "水": 2, "風": 3, "光": 4, "闇": 5}

RE_TAI = re.compile(r"(\d+)(?:[～〜](\d+))?体")
RE_ELEM = re.compile(r"([火水風光闇])属性(攻撃力|防御力)")
RE_ET = re.compile(r"次の回復時に回復効果が[0-9.]+%アップするスタック")  # Et: self's next heal amount up
RE_MAXHP = re.compile(r"最大HP[^。]*アップ")
RE_SELFHEAL = re.compile(r"自身のHP[^。]*回復")   # 012: heal self's HP while dealing damage
# HP...heal within one clause (incl. 大/特大 回復; won't match MP回復). 前衛 = self-heal / 後衛 = ally heal
RE_HP_HEAL = re.compile(r"HP[^。]*?回復")


def target_icon(desc):
    """The skill's first 「N(～M)体」 -> target-count icon (max,min)."""
    m = RE_TAI.search(desc)
    if not m:
        return ""
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    if hi > 4:
        return ""
    return TGT_ICON % (hi, lo)


def _dir_after(desc, start):
    """From start to the end of the sentence, the nearest アップ(+)/ダウン(-); None if neither."""
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
    """Return (target-count icon, stat row, special-effect row, mark row); the last three are icon-path lists."""
    desc = (gvg_skill.get("desc", "") if gvg_skill else "") or ""
    fgs = set(fg)

    # -- stat row (2.1): main stats (incl. combos) -> element atk/def -> max HP --
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
        while i + 1 < len(grp):           # pair up same-direction stats, preferring the combo icon
            stat.append(combo_map[frozenset([grp[i], grp[i + 1]])])
            i += 2
        if i < len(grp):
            stat.append(single_map[grp[i]])
    # Element atk/def (fire/water/wind/light/dark), atk before def
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

    # -- special-effect row (2.2) --
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

    # -- mark row (2.3) --
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
    """Set of "individual" icon numbers a card's stat changes involve (no combos, no max HP).
    4 main stats up/down -> 1-8; 5 elements atk/def up/down -> 18-37; 28 possible values.
    A single card may hit several."""
    desc = (gvg_skill.get("desc", "") if gvg_skill else "") or ""
    nums = set()
    flags = stat_flags(desc)
    for s in MAIN_ORDER:                 # pa pd ma md
        if s + "+" in flags:
            nums.add(MAIN_UP[s])
        if s + "-" in flags:
            nums.add(MAIN_DN[s])
    for m in RE_ELEM.finditer(desc):     # fire/water/wind/light/dark x atk/def
        el = ELEM_CHAR[m.group(1)]
        atk = (m.group(2) == "攻撃力")
        d = _dir_after(desc, m.start())
        if d is None:
            continue
        nums.add(ELEM_BASE[el] + (0 if atk else 2) + (0 if d == "+" else 1))
    return nums


# ---------------------------------------------------------------------------
# twdb (allb.game-db.tw) card id
# ---------------------------------------------------------------------------
def tw_full_id(e):
    """twdb card id = cardMstId * 10 + variant digit.
    Variant: normal = 0; awakenable = 1 (both entries share it); super-awakening = that entry's cardType(1-7)."""
    awk = e.get("awk", "none")
    if awk == "awakening":
        digit = 1
    elif awk == "super":
        digit = e["cardType"]
    else:
        digit = 0
    return e["cardMstId"] * 10 + digit


# ---------------------------------------------------------------------------
# Top-right passive dot (deck builder only)
# ---------------------------------------------------------------------------
def passive_dot(e):
    """Return the top-right dot color based on the passive (GvgAuto name); empty string if none.
    効果範囲+1 -> #e377c2 (magenta); 副援:支援UP -> #f6c2dd (very light pink); otherwise -> no dot."""
    ga = e["skills"].get("gvgAuto")
    name = (ga.get("name", "") if ga else "") or ""
    if "効果範囲+1" in name:
        return "#e377c2"
    if "副援:支援UP" in name:
        return "#f6c2dd"
    return ""


# ---------------------------------------------------------------------------
# Build picker units
# ---------------------------------------------------------------------------
def build_calc(e, gvg, ga):
    """Static 牌効 data for one card (the live formula runs in JS). See skill_calc.py.
    Compact keys keep the per-unit data-calc blob small."""
    se = sc.skill_effects(gvg)
    return {
        "a": e["attribute"], "c": e["cardType"], "r": 0 if e["cardType"] <= 4 else 1,
        "e": [{"k": x["kind"], "l": x["label"], "m": x["mag"],
               "g": x["gvg"], "n": x["rand"], "t": x["atk"]} for x in se["effs"]],
        "am": se["addMag"], "ut": se["upT"], "tm": se["timeMax"], "eh": se["eh"], "ct": se["ct"],
        "pu": [{"k": p["kind"], "c": p["coeff"]} for p in sc.passive_up(ga)],
        "pp": sc.passive_plus(ga),
        "lu": [{"a": l["attr"], "k": l["kind"], "t": l["atk"], "p": l["pct"]}
               for l in sc.legendary_up(e["skills"].get("legendary"))],
    }


def build_units(entries):
    units = []
    for e in entries:
        gvg = e["skills"].get("gvg")
        ga = e["skills"].get("gvgAuto")
        tgt, sk1, sk2, sk3 = gvg_battle_icons(gvg, e["fg"])
        gdesc = (gvg.get("desc", "") if gvg else "") or ""
        units.append({
            "calc": build_calc(e, gvg, ga),
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
            "sc": sorted(stat_change_set(gvg)),       # individual stat-change icon numbers
            "heal": 1 if RE_HP_HEAL.search(gdesc) else 0,
        })
    return units


# ---------------------------------------------------------------------------
# Rendering
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
    """Top-right passive dot (center = card art's top-right corner, may slightly overflow the frame)."""
    return ('<span class="pdot" style="background:%s"></span>' % color) if color else ""


def render_overlay(tgt, sk1, sk2, sk3):
    """Battle icons over the card art: top-left target count / bottom-right stat row / right-center special column / left-center mark column."""
    def grp(cls, lst):
        if not lst:
            return ""
        return '<div class="%s">%s</div>' % (
            cls, "".join('<img src="%s" alt="">' % p for p in lst))
    h = ""
    if tgt:
        h += '<img class="tgt" src="%s" alt="">' % tgt
    h += grp("sk-stat", sk1)       # stat changes: bottom-right horizontal row
    h += grp("sk-special", sk2)    # special effects: right-center vertical column
    h += grp("sk-mark", sk3)       # marks: left-center vertical column
    return h


def render_unit(u):
    icon = CARD_ICON_URL.format(uid=u["uid"])
    return (
        '<div class="unit" data-uid="{uid}" data-tw="{tw}" data-ct="{ct}" data-attr="{attr}" '
        'data-grade="{grade}" data-leg="{leg}" data-ult="{ult}" data-order="{order}" '
        'data-name="{name_attr}" data-tg="{tg}" data-fg="{fg}" data-ga="{ga_codes}" '
        'data-mt="{mt}" data-an="{an}" data-ba="{ba}" data-et="{et}" data-lv="{lv}" '
        'data-mark="{mark}" data-frame="{frame}" data-pdot="{pdot_color}" data-tgt="{tgt}" '
        'data-sk1="{sk1}" data-sk2="{sk2}" data-sk3="{sk3}" data-sc="{sc}" data-heal="{heal}" '
        'data-calc="{calc_json}">'
        '<div class="u-top">'
        '<span class="cardimg" title="{name_attr}">'
        '<img class="art" loading="lazy" src="{icon}" alt="" onerror="this.classList.add(\'broken\')">'
        '<img class="frame" src="{frame}" alt="">'
        '{pdot}'
        '<img class="mark" src="{mark}" alt="">'
        '{overlay}'
        '</span>'
        '<div class="u-meta">'
        '<img class="u-tag" src="assets/CardType{ct}.png" alt="" title="{t_type}">'
        '<img class="u-tag" src="assets/Attribute{attr}.png" alt="" title="{t_attr}">'
        '</div>'
        '<button class="u-add" type="button">{t_add}</button>'
        '</div>'
        '{gvg_cell}{ga_cell}{leg_cell}'
        '</div>'
    ).format(
        t_type=config.t("deck_builder.ui.type_lbl"), t_attr=config.t("deck_builder.ui.attr_lbl"),
        t_add=config.t("deck_builder.ui.add"),
        uid=u["uid"], tw=u["tw"], ct=u["ct"], attr=u["attr"], grade=u["grade"],
        leg=1 if u["leg"] else 0, ult=1 if u["ult"] else 0, order=u["order"],
        name_attr=html.escape(u["name"], quote=True),
        tg=u["tg"], fg=" ".join(u["fg"]), ga_codes=" ".join(u["ga_codes"]),
        mt=u["mt"], an=u["an"], ba=u["ba"], et=u["et"], lv=" ".join(u["lv"]),
        mark=u["mark"], frame=card_markers.frame_rel(u["ult"]),
        pdot=pdot_html(u["pdot"]), pdot_color=u["pdot"],
        tgt=u["tgt"], sk1=" ".join(u["sk1"]), sk2=" ".join(u["sk2"]), sk3=" ".join(u["sk3"]),
        sc=" ".join(str(n) for n in u["sc"]), heal=u["heal"],
        calc_json=html.escape(json.dumps(u["calc"], separators=(",", ":"), ensure_ascii=False), quote=True),
        overlay=render_overlay(u["tgt"], u["sk1"], u["sk2"], u["sk3"]),
        icon=icon,
        gvg_cell=render_mini_skill(u["gvg"], "assets/Skill2.png"),
        ga_cell=render_mini_skill(u["ga_skill"], "assets/Skill3.png"),
        leg_cell=(render_mini_skill(u["legendary"], "assets/Skill4.png")
                  if u["legendary"] else ""),
    )


def build_tactics_options():
    """Group the real tactics for the 牌効 active-tactics icon pickers. Only the **no-MP**
    (sp==0) version of each GVG effect is listed (when both free and MP variants exist), one
    entry per distinct effect with a representative tactics uniqueId (highest rarity) for its icon.
    my side: 属性 (attr) / 発動率↑ (rate, rateUp) / 特効 (eff up); enemy side: 盾 (shield) /
    発動率↓ (rate, rateDown) / 特効 (eff down = enemy 支援/妨害 reduction)."""
    effects = {x["tacticsEffectMstId"]: x
               for x in load_mst("masterdata_api_mst_getTacticsEffectMstList.json")}
    tactics = gtl.load_tactics()
    # names that exist as a no-MP (sp==0) effect; an MP effect is hidden only if it has such a twin
    free_names = set()
    for t in tactics:
        eff = effects.get(t.get("gvgTacticsEffectMstId"))
        if eff and eff.get("sp", 0) == 0:
            free_names.add(eff.get("name"))
    best = {}   # gvg effect id -> representative tactics (highest rarity)
    for t in tactics:
        gid = t.get("gvgTacticsEffectMstId")
        eff = effects.get(gid)
        if not eff:
            continue
        if eff.get("sp", 0) != 0 and eff.get("name") in free_names:
            continue   # MP version that has a fully-equivalent no-MP twin -> skip
        if gid not in best or t["rarity"] > best[gid]["rarity"]:
            best[gid] = t
    groups = {"my_attr": [], "my_rate": [], "my_eff": [], "en_shield": [], "en_rate": [], "en_eff": []}
    for gid, t in best.items():
        eff = effects[gid]
        cat = gtl._effect_category(eff)
        info = sc.tactics_effect_info(eff)
        opt = {"uid": t["uniqueId"], "rar": t["rarity"], "name": eff.get("name", ""), "info": info}
        if cat == "attr":
            groups["my_attr"].append(opt)
        elif cat == "eff":
            groups["en_eff" if info["down"] > 0 else "my_eff"].append(opt)
        elif cat == "shield":
            groups["en_shield"].append(opt)
        elif cat == "rate":
            if info["rateUp"] > 0:
                groups["my_rate"].append(opt)
            if info["rateDown"] > 0:
                groups["en_rate"].append(opt)
    for arr in groups.values():
        arr.sort(key=lambda o: (o["info"].get("tAttr", 0), o["name"]))
    return groups


def _build_sc_name():
    """Stat-change display names (the JS SC_NAME map) keyed by id, built from the active
    language's attribute labels + atk/def suffixes so they follow the chosen language.
    ids 1-8 = generic ATK/DEF/Sp.ATK/Sp.DEF; ids 18-37 = per-attribute atk/def up/down."""
    attr = config.int_label_map("attribute")
    aS = config.t("deck_builder.ui.atk_suffix")
    dS = config.t("deck_builder.ui.def_suffix")
    UP, DN = "\u2191", "\u2193"
    m = {1: "ATK" + UP, 2: "DEF" + UP, 3: "Sp.ATK" + UP, 4: "Sp.DEF" + UP,
         5: "ATK" + DN, 6: "DEF" + DN, 7: "Sp.ATK" + DN, 8: "Sp.DEF" + DN}
    for a in range(1, 6):
        base = 18 + (a - 1) * 4
        m[base] = attr[a] + aS + UP
        m[base + 1] = attr[a] + aS + DN
        m[base + 2] = attr[a] + dS + UP
        m[base + 3] = attr[a] + dS + DN
    return {str(k): v for k, v in m.items()}


def render_html(units):
    legendary = sorted((u for u in units if u["leg"]),
                       key=lambda u: u["order"], reverse=True)
    others = sorted((u for u in units if not u["leg"]),
                    key=lambda u: (u["ult"], u["order"]), reverse=True)

    leg_html = "\n".join(render_unit(u) for u in legendary)
    oth_html = "\n".join(render_unit(u) for u in others)

    targets = sorted({u["tg"] for u in units if u["tg"]})

    dropdowns = {
        "__DD_TYPE__": build_dropdown("type", config.t("card_list.filter.type"), sorted(CARD_TYPE_LABEL.items())),
        "__DD_ATTR__": build_dropdown("attr", config.t("card_list.filter.attr"), sorted(ATTRIBUTE_LABEL.items())),
        "__DD_TARGET__": build_dropdown("target", config.t("card_list.filter.target"), [(t, t) for t in targets]),
        "__DD_FEAT__": build_dropdown("feat", config.t("card_list.filter.feat"), FEATURE_DEFS),
        "__DD_GA__": build_dropdown("ga", config.t("card_list.filter.ga"), GA_DEFS),
    }

    # JS-side label tables (injected as JSON so they follow the active language).
    js_type_label = {str(k): v for k, v in CARD_TYPE_LABEL.items()}
    js_ga_label = {c: config.t("card_list.ga." + c) for c in ("dmgup", "supup", "healup", "ptup", "rangeup")}

    # deck_builder JS label maps, built from existing sections so they follow the language
    attr = config.int_label_map("attribute")
    ctype = config.int_label_map("card_type")
    js_sc_name = _build_sc_name()
    js_attr_map = {str(i): attr[i] for i in range(1, 6)}
    js_attr_arr = [attr[i] for i in range(1, 6)]
    js_costume_f = [[i, ctype[i]] for i in range(1, 5)]
    js_costume_b = [[i, ctype[i]] for i in range(5, 8)]
    js_up_jp = {"dmg": js_ga_label["dmgup"], "heal": js_ga_label["healup"], "buff": js_ga_label["supup"]}
    js_kind_jp = {"dmg": config.t("deck_builder.ui.damage"),
                  "heal": ctype[7], "buff": ctype[5], "debuff": ctype[6]}

    chrome = {
        "__HTML_LANG__": config.html_lang(),
        "__T_TITLE__": config.t("deck_builder.title"),
    }

    out = HTML_TEMPLATE
    for token, frag in dropdowns.items():
        out = out.replace(token, frag)
    for token, val in chrome.items():
        out = out.replace(token, html.escape(val))
    out = out.replace("__JS_TYPE_LABEL__", json.dumps(js_type_label, ensure_ascii=False))
    out = out.replace("__JS_GA_LABEL__", json.dumps(js_ga_label, ensure_ascii=False))
    out = out.replace("__DBJS_SC_NAME__", json.dumps(js_sc_name, ensure_ascii=False))
    out = out.replace("__DBJS_ATTR_MAP__", json.dumps(js_attr_map, ensure_ascii=False))
    out = out.replace("__DBJS_ATTR_ARR__", json.dumps(js_attr_arr, ensure_ascii=False))
    out = out.replace("__DBJS_COSTUME_F__", json.dumps(js_costume_f, ensure_ascii=False))
    out = out.replace("__DBJS_COSTUME_B__", json.dumps(js_costume_b, ensure_ascii=False))
    out = out.replace("__DBJS_UP_JP__", json.dumps(js_up_jp, ensure_ascii=False))
    out = out.replace("__DBJS_KIND_JP__", json.dumps(js_kind_jp, ensure_ascii=False))
    db_ui = config.section("deck_builder.ui")
    for _suffix in sorted(db_ui, key=len, reverse=True):
        out = out.replace("__DBT_%s__" % _suffix, db_ui[_suffix])
    for _i in range(1, 6):
        out = out.replace("__DBT_attr%d__" % _i, attr[_i])
    out = out.replace("__LEG_UNITS__", leg_html)
    out = out.replace("__OTH_UNITS__", oth_html)
    out = out.replace("__LEG_TOTAL__", str(len(legendary)))
    out = out.replace("__OTH_TOTAL__", str(len(others)))
    out = out.replace("__PME_TACTICS__",
                      json.dumps(build_tactics_options(), separators=(",", ":"), ensure_ascii=False))
    return config.relocate_asset_urls(out)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="__HTML_LANG__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__T_TITLE__</title>
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

  /* __DBT_role_front__/__DBT_role_back__ toggle */
  .roleSw { display:inline-flex; border:1px solid #5b6b8c; border-radius:8px; overflow:hidden; }
  .roleSw button { border:0; background:#fff; color:#333; padding:6px 14px; cursor:pointer; font-weight:600; }
  .roleSw button.on { background:#5b6b8c; color:#fff; }
  header label.chk { display:inline-flex; align-items:center; gap:4px; cursor:pointer; color:#444; }

  /* Generic checkbox dropdown */
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

  /* Two-column body: deck panel on the left (sticky), picker on the right */
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
          background:#fff; overflow:visible; }   /* visible: lets the passive dot show outside the frame */
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

  /* Card image stack: art + rarity frame + top-right category marker (shared by list/deck builder)
     Marker size is bounded: <=1/4 height, <=1/2 width */
  .cardimg { position:relative; display:block; width:88px; height:88px; }
  .cardimg .art { width:100%; height:100%; object-fit:cover; display:block; border-radius:6px; }
  .cardimg .art.broken { visibility:hidden; }
  .cardimg .frame { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
  .cardimg .mark { position:absolute; top:-2px; right:-3px; max-height:38%;
                   height:auto; width:auto; pointer-events:none;
                   filter:drop-shadow(0 1px 1px rgba(0,0,0,.4)); }
  .slot .cardimg .mark { top:1px; right:1px; }   /* keep the category marker inside the slot */
  /* Passive dot: center = card art's top-right corner, slightly overflows the frame; rendered topmost so it shows fully (it's tiny and won't block anything) */
  .cardimg .pdot { position:absolute; top:-7px; right:-7px; width:14px; height:14px; z-index:10;
                   border-radius:50%; border:2px solid #fff; box-sizing:border-box;
                   box-shadow:0 1px 2px rgba(0,0,0,.55); pointer-events:none; }
  /* Top-left: target count (same 38% height as the marker); stats = bottom-right row; specials = right-center column; marks = left-center column */
  .cardimg .tgt { position:absolute; left:-2px; top:-2px; max-height:38%; height:auto; width:auto;
                  pointer-events:none; filter:drop-shadow(0 1px 1px rgba(0,0,0,.45)); }
  .slot .cardimg .tgt { left:1px; top:1px; }
  .cardimg .sk-stat { position:absolute; right:1px; bottom:1px; display:flex; gap:1px;
                      justify-content:flex-end; pointer-events:none; }
  /* The left/right columns align downward but leave a row (18px) at the bottom for the stat row */
  .cardimg .sk-special { position:absolute; right:1px; bottom:18px;
                         display:flex; flex-direction:column; align-items:flex-end; gap:1px; pointer-events:none; }
  .cardimg .sk-mark { position:absolute; left:1px; bottom:18px;
                      display:flex; flex-direction:column; align-items:flex-start; gap:1px; pointer-events:none; }
  .cardimg .sk-stat img, .cardimg .sk-special img, .cardimg .sk-mark img {
                      height:15px; width:auto; filter:drop-shadow(0 1px 1px rgba(0,0,0,.55)); }

  /* Stats */
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

  /* Picker unit */
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

  /* 牌効 calculator: settings panel sits between the deck slots and the stats; results show under each deck card */
  #pmeToggle.active { background:#5b6b8c; color:#fff; border-color:#5b6b8c; }
  #pmePanel { display:none; }
  .deckpane.pme-on #pmePanel { display:block; }
  .pme { margin:6px 0 12px; border-top:2px solid #9aa3b8; padding:8px 0 10px; }
  .pme h3 { font-size:14px; margin:2px 0 8px; color:#333; border-bottom:1px solid #c5ccda; padding-bottom:4px; }
  .pme h4 { font-size:12px; margin:0 0 4px; color:#444; }
  .pme-grid { display:flex; flex-direction:column; gap:6px; }
  .pme-blk { display:flex; align-items:flex-start; gap:6px; flex-wrap:wrap; }
  .pme-blk > b { flex:0 0 60px; color:#555; font-size:12px; padding-top:3px; }
  .pme-attrs { display:flex; flex-wrap:wrap; gap:4px 8px; align-items:center; }
  .pme-attrs label { display:inline-flex; align-items:center; gap:2px; color:#333; font-size:12px; }
  .pme-attrs input[type=number] { width:46px; padding:2px 4px; border:1px solid #9aa3b8; border-radius:5px; font-size:12px; }
  .pme-attrs select { padding:2px 4px; border:1px solid #9aa3b8; border-radius:5px; font-size:12px; }
  .pme-tac { display:flex; gap:10px; margin-top:8px; flex-wrap:wrap; }
  .pme-tcol { flex:1 1 220px; min-width:0; }
  .pme-tg { margin-bottom:6px; }
  .pme-tg > span { display:block; font-size:11px; color:#5b6b8c; font-weight:600; margin-bottom:2px; }
  /* mini tactics list = clickable framed icons (same look as the tactics page) */
  .taclist { display:flex; flex-wrap:wrap; gap:6px; max-height:170px; overflow:auto;
             border:1px solid #c5ccda; border-radius:6px; padding:5px; background:#fff; }
  .taclist .muted { color:#aaa; font-size:11px; }
  .tac-ic { width:62px; height:62px; padding:0; border:0; background:transparent; cursor:pointer;
            position:relative; opacity:.45; transition:opacity .15s; }
  .tac-ic:hover { opacity:.8; }
  .tac-ic.on { opacity:1; }
  .tac-ic.on::after { content:''; position:absolute; inset:-2px; border:2px solid #e8902a;
                      border-radius:9px; pointer-events:none; }
  .tcimg { position:relative; display:block; width:62px; height:62px; }
  .tcimg .bg, .tcimg .art { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; border-radius:6px; }
  .tcimg .frame { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
  .tcimg .mark { position:absolute; top:-2px; right:-3px; max-height:38%; width:auto; height:auto;
                 pointer-events:none; filter:drop-shadow(0 1px 1px rgba(0,0,0,.4)); }
  /* per-effect 牌効 chips (under each deck card) */
  .pme-eff { display:inline-block; font-size:10px; margin:1px 2px 0 0; padding:0 3px; border-radius:3px;
             font-variant-numeric:tabular-nums; line-height:1.5; cursor:pointer; }
  .pme-eff:hover { outline:1px solid rgba(0,0,0,.35); }
  .pme-eff b { font-weight:700; }
  .k-dmg { background:#ffe1e1; color:#a01f1f; }
  .k-heal { background:#e1f3e6; color:#1f7a3a; }
  .k-buff { background:#e3ecfb; color:#26508a; }
  .k-debuff { background:#efe1f7; color:#6a2a8a; }
  /* grand total of all deck cards' effect amounts (bottom of the simulator panel) */
  .pme-total { margin-top:10px; padding-top:8px; border-top:1px dashed #b9c0d0; }
  .pme-total .pt-h { font-size:12px; font-weight:700; color:#333; margin-bottom:4px; }
  .pme-total .pt-row { display:flex; flex-wrap:wrap; gap:4px 6px; }
  .pme-total .pt-k { display:inline-block; font-size:12px; padding:1px 6px; border-radius:3px;
                     font-variant-numeric:tabular-nums; }
  .pme-total .pt-k b { font-weight:700; }
  /* deck slot cell = the square slot + its 牌効 results below (only while the calculator is on) */
  .slotcell { display:flex; flex-direction:column; align-items:stretch; }
  .slot-pme { display:none; margin-top:2px; }
  .deckpane.pme-on .slot-pme { display:block; }

  /* 牌効 breakdown popup (click a chip -> per-region calculation) */
  .bd-modal { display:none; position:fixed; inset:0; z-index:10000; background:rgba(0,0,0,.45);
              align-items:center; justify-content:center; padding:16px; }
  .bd-modal.open { display:flex; }
  .bd-box { background:#fff; border-radius:10px; box-shadow:0 8px 30px rgba(0,0,0,.4);
            max-width:520px; width:100%; max-height:86vh; overflow:auto; }
  .bd-head { display:flex; align-items:center; gap:8px; padding:10px 12px; border-bottom:1px solid #d8dde8;
             position:sticky; top:0; background:#fff; font-size:14px; font-weight:700; }
  .bd-head #bdTitle { flex:1; }
  .bd-head .bk { font-weight:600; font-size:11px; padding:1px 6px; border-radius:4px; }
  .bd-head #bdClose { border:none; background:#eef1f6; border-radius:6px; width:26px; height:26px;
                      font-size:18px; line-height:1; cursor:pointer; color:#444; }
  .bd-head #bdClose:hover { background:#dde3ee; }
  .bd-table { width:100%; border-collapse:collapse; font-size:12px; }
  .bd-table th { text-align:left; padding:5px 12px; background:#f4f6fa; color:#566; font-weight:600;
                 border-bottom:1px solid #e2e6ee; }
  .bd-table td { padding:5px 12px; border-bottom:1px solid #eef0f5; vertical-align:top; }
  .bd-table .bn { white-space:nowrap; font-weight:600; color:#333; }
  .bd-table .bv { white-space:nowrap; font-variant-numeric:tabular-nums; color:#1a5; font-weight:700; }
  .bd-table .bd { color:#667; line-height:1.45; }
  .bd-total { padding:10px 12px; font-size:15px; font-weight:700; text-align:right;
              border-top:2px solid #9aa3b8; font-variant-numeric:tabular-nums; }

  /* Global watermark: fixed, covers the whole viewport, top layer, very low opacity (uniqueId 20000216 full art); always visible while scrolling and never blocks interaction */
  .watermark { position:fixed; inset:0; z-index:9999; pointer-events:none; }
  .watermark img { width:100%; height:100%; object-fit:cover; opacity:.1; user-select:none; }

  /* ---------- Mobile / responsive ---------- */
  @media (max-width: 820px) {
    body { font-size:12px; }
    header { padding:6px 8px; gap:6px 8px; }
    header h1 { font-size:14px; flex-basis:100%; margin:0; }
    header input, header select, .ddbtn, button.btn { font-size:12px; padding:4px 6px; }
    .roleSw button { padding:5px 10px; }
    #pcount { flex-basis:100%; }

    /* Stack the deck panel above the picker; the deck panel is no longer sticky. */
    .layout { flex-direction:column; gap:10px; padding:10px; }
    .deckpane { flex:1 1 auto; width:100%; position:static; max-height:none; overflow:visible; }
    .pickpane { width:100%; }
    .deckpane #code { min-width:120px; }

    /* Fixed 5-wide slot grids shrink to fit; slots stay square via aspect-ratio. */
    .slots { grid-template-columns:repeat(5, minmax(0,1fr)); gap:4px; }
    .slot { width:auto; height:auto; aspect-ratio:1; }

    /* Picker units one per row. */
    .units { gap:8px; }
    .unit { width:100%; }
  }
</style>
</head>
<body>
<header>
  <h1>__T_TITLE__</h1>
  <div class="roleSw">
    <button type="button" id="roleF" class="on">__DBT_role_front__</button>
    <button type="button" id="roleB">__DBT_role_back__</button>
  </div>
  <span><label>__DBT_search__</label><input id="q" type="text" placeholder="__DBT_search_ph__"></span>
  __DD_TYPE__
  __DD_ATTR__
  __DD_TARGET__
  __DD_FEAT__
  __DD_GA__
  <label class="chk"><input type="checkbox" id="deckOnly"> __DBT_deck_only__</label>
  <button class="btn" id="clearFilter" type="button">__DBT_filter_clear__</button>
  <button class="btn" id="pmeToggle" type="button">__DBT_sim_label__ OFF</button>
  <span id="pcount"></span>
</header>

<div class="layout">
  <aside class="deckpane">
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px;">
      <input id="code" type="text" placeholder="__DBT_code_ph__" spellcheck="false">
      <button class="btn" id="loadCode" type="button">__DBT_load__</button>
      <button class="btn" id="copyCode" type="button">__DBT_copy__</button>
      <button class="btn" id="clearDeck" type="button">__DBT_deck_clear__</button>
    </div>

    <div class="deck-group">
      <h3>Legendary <span id="legCount">0</span>/5</h3>
      <div id="legSlots" class="slots"><span class="empty-hint">__DBT_none_dash__</span></div>
    </div>
    <div class="deck-group">
      <h3>__DBT_main__ <span id="othCount">0</span>/20</h3>
      <div id="othSlots" class="slots"><span class="empty-hint">__DBT_none_dash__</span></div>
    </div>

    <div class="pme" id="pmePanel">
      <h3>__DBT_sim_settings__</h3>
      <div class="pme-grid">
        <div class="pme-blk"><b>CHARM%</b>
          <div class="pme-attrs">
            <label>__DBT_attr1__<input type="number" id="charm1" value="0" step="1"></label>
            <label>__DBT_attr2__<input type="number" id="charm2" value="0" step="1"></label>
            <label>__DBT_attr3__<input type="number" id="charm3" value="0" step="1"></label>
            <label>__DBT_attr4__<input type="number" id="charm4" value="0" step="1"></label>
            <label>__DBT_attr5__<input type="number" id="charm5" value="0" step="1"></label>
          </div>
        </div>
        <div class="pme-blk"><b>ADX</b>
          <div class="pme-attrs" id="adxRow"></div>
        </div>
        <div class="pme-blk"><b>__DBT_theme__</b>
          <div class="pme-attrs">
            <label class="chk">__DBT_attr1__<input type="checkbox" id="theme1"></label>
            <label class="chk">__DBT_attr2__<input type="checkbox" id="theme2"></label>
            <label class="chk">__DBT_attr3__<input type="checkbox" id="theme3"></label>
            <label class="chk">__DBT_attr4__<input type="checkbox" id="theme4"></label>
            <label class="chk">__DBT_attr5__<input type="checkbox" id="theme5"></label>
          </div>
        </div>
        <div class="pme-blk"><b>__DBT_specialty__</b>
          <div class="pme-attrs"><label><select id="costJob"></select></label></div>
        </div>
        <div class="pme-blk"><b>__DBT_stack_title__</b>
          <div class="pme-attrs">
            <label class="chk"><input type="checkbox" id="sMt"> __DBT_stack_mt__</label>
            <label class="chk"><input type="checkbox" id="sAn"> __DBT_stack_an__</label>
            <label class="chk"><input type="checkbox" id="sEt"> __DBT_stack_et__</label>
          </div>
        </div>
        <div class="pme-blk"><b>__DBT_eff__</b>
          <div class="pme-attrs"><label class="chk"><input type="checkbox" id="ehct"> EH/CT </label></div>
        </div>
      </div>

      <div class="pme-tac">
        <div class="pme-tcol"><h4>__DBT_ally_orders__</h4>
          <div class="pme-tg"><span>__DBT_attr_lbl__</span><div class="taclist" id="tacMyAttr"></div></div>
          <div class="pme-tg"><span>__DBT_rate_up__</span><div class="taclist" id="tacMyRate"></div></div>
          <div class="pme-tg"><span>__DBT_eff__</span><div class="taclist" id="tacMyEff"></div></div>
        </div>
        <div class="pme-tcol"><h4>__DBT_enemy_orders__</h4>
          <div class="pme-tg"><span>__DBT_shield__</span><div class="taclist" id="tacEnShield"></div></div>
          <div class="pme-tg"><span>__DBT_rate_down__</span><div class="taclist" id="tacEnRate"></div></div>
          <div class="pme-tg"><span>__DBT_eff__</span><div class="taclist" id="tacEnEff"></div></div>
        </div>
      </div>
      <div class="pme-total" id="pmeTotal"></div>
    </div>

    <div class="stats" id="stats">
      <h3>__DBT_type_lbl__</h3><div id="stType" class="chips"></div>
      <h3>__DBT_attr_lbl__</h3><div id="stAttr" class="chips"></div>
      <h3>__DBT_target_lbl__</h3><div id="stTarget" class="chips"></div>
      <h3>__DBT_stat_change__</h3>
      <div class="sclbl">__DBT_increase__</div><div id="stScUp" class="chips sc"></div>
      <div class="sclbl">__DBT_decrease__</div><div id="stScDn" class="chips sc"></div>
      <h3>__DBT_skill_cls__</h3><div id="stSkillCls" class="skcls"></div>
      <h3>__DBT_stack_count__</h3><div id="stStack" class="statline"></div>
      <h3>__DBT_feature_lbl__</h3><div id="stFeat" class="statline"></div>
      <h3>__DBT_ga_count__</h3><div id="stGa" class="statline"></div>
      <h3>__DBT_ga_levels__</h3><div id="stLevels"></div>
    </div>
  </aside>

  <main class="pickpane">
    <h2>__DBT_leg_cards__ (<span>__LEG_TOTAL__</span>)
      <button class="btn" id="toggleLeg" type="button">__DBT_collapse__</button></h2>
    <div class="units" id="legUnits">
__LEG_UNITS__
    </div>
    <h2>__DBT_main_cards__ (<span>__OTH_TOTAL__</span>)</h2>
    <div class="units" id="othUnits">
__OTH_UNITS__
    </div>
  </main>
</div>
<div class="watermark"><img src="assets/remote/Image/Card/Card020000216.jpg" alt=""></div>

<div class="bd-modal" id="bdModal">
  <div class="bd-box">
    <div class="bd-head">
      <span id="bdTitle"></span>
      <button type="button" id="bdClose" title="__DBT_close__">×</button>
    </div>
    <table class="bd-table">
      <thead><tr><th>__DBT_bd_region__</th><th>__DBT_bd_coeff__</th><th>__DBT_bd_detail__</th></tr></thead>
      <tbody id="bdBody"></tbody>
    </table>
    <div class="bd-total" id="bdTotal"></div>
  </div>
</div>

<script>
  var header = document.querySelector('header');
  var q = document.getElementById('q');
  var pcount = document.getElementById('pcount');
  var units = Array.prototype.slice.call(document.querySelectorAll('.unit'));

  var TYPE_LABEL = __JS_TYPE_LABEL__;
  var GA_LABEL = __JS_GA_LABEL__;
  var ROMAN = {'Ⅰ':1,'Ⅱ':2,'Ⅲ':3,'Ⅳ':4,'Ⅴ':5,'Ⅵ':6,'Ⅶ':7,'Ⅷ':8,'Ⅸ':9,'Ⅹ':10};

  // Stat changes (individual): 14 stats x up/down = 28 items. Icon = BattleIconSkillImg{n}
  var SC_UP=[1,2,3,4,18,20,22,24,26,28,30,32,34,36];
  var SC_DN=[5,6,7,8,19,21,23,25,27,29,31,33,35,37];
  var SC_NAME=__DBJS_SC_NAME__;
  function scIcon(n){ return 'assets/Sprite/BattleIconSkillImg'+('00'+n).slice(-3)+'.png'; }
  function healIcon(){ return role==='F' ? scIcon(12) : 'assets/CardType7.png'; }

  function setHeadOffset(){ document.documentElement.style.setProperty('--toolbar-h', header.offsetHeight+'px'); }
  window.addEventListener('resize', setHeadOffset); setHeadOffset();

  // ---------- Unit data cache ----------
  var unitByKey = {};           // 'uid.ct' -> element
  function parseUnit(el){
    var d = el.dataset;
    return { uid:d.uid, tw:+d.tw, ct:+d.ct, attr:+d.attr, grade:+d.grade, leg:d.leg==='1',
             name:d.name, tg:d.tg||'', mark:d.mark, frame:d.frame, pdot:d.pdot||'',
             tgt:d.tgt||'', sk1:d.sk1?d.sk1.split(' '):[], sk2:d.sk2?d.sk2.split(' '):[], sk3:d.sk3?d.sk3.split(' '):[],
             sc:d.sc?d.sc.split(' ').map(Number):[], heal:d.heal==='1',
             fg:d.fg?d.fg.split(' '):[], ga:d.ga?d.ga.split(' '):[],
             mt:+d.mt, an:+d.an, ba:+d.ba, et:+d.et, lv:d.lv?d.lv.split(' '):[],
             calc:d.calc?JSON.parse(d.calc):null, el:el };
  }
  units.forEach(function(el){ unitByKey[el.dataset.uid+'.'+el.dataset.ct] = el; });
  // twdb id -> unit(s) (awakenable cards have two entries sharing an id, hence an array)
  var twIndex={};
  units.forEach(function(el){ var t=el.dataset.tw; if(t) (twIndex[t]=twIndex[t]||[]).push(el); });

  // ---------- Dropdown panel toggle ----------
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

  // ---------- Role (__DBT_role_front__/__DBT_role_back__) ----------
  var role = 'F';
  function validTypes(){ return role==='F' ? [1,2,3,4] : [5,6,7]; }
  function isValidType(t){ return validTypes().indexOf(+t) !== -1; }

  function applyRoleToTypeFilter(){
    // disable category options not belonging to the current role
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
    if(deckCards().length && !confirm('__DBT_role_switch_confirm__')){
      return;
    }
    role=r;
    document.getElementById('roleF').classList.toggle('on', r==='F');
    document.getElementById('roleB').classList.toggle('on', r==='B');
    rebuildCostJob();
    clearSlots(); applyRoleToTypeFilter(); renderDeck(); applyFilter();
  }
  document.getElementById('roleF').addEventListener('click', function(){ setRole('F'); });
  document.getElementById('roleB').addEventListener('click', function(){ setRole('B'); });

  // ---------- Filtering ----------
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
    pcount.textContent=shown+' __DBT_shown_suffix__';
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
    box.style.display=hide?'none':''; this.textContent=hide?'__DBT_expand__':'__DBT_collapse__';
  });

  // ---------- Deck (5 Legendary + 20 __DBT_main__ fixed slots, freely draggable to reorder) ----------
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
    if(hasUid(c.uid)){ if(!silent) flash(el); return false; }      // only one copy of the same card
    if(!isValidType(c.ct)){ return false; }
    var arr=slotsOf(c.leg), idx=arr.indexOf(null);
    if(idx===-1){ if(!silent) alert(c.leg?'__DBT_leg_max__':'__DBT_main_max__'); return false; }
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

  // Picker "追加" (add) buttons
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
        h+='<div class="slotcell">'
          +'<div class="slot filled" draggable="true" data-grp="'+grp+'" data-idx="'+i+'" data-uid="'+c.uid+'" '
          +'title="'+escAttr(c.name)+' ('+TYPE_LABEL[c.ct]+')">'
          +'<span class="cardimg">'
          +'<img class="art" loading="lazy" src="'+iconUrl(c.uid)+'" alt="" onerror="this.style.visibility=\\'hidden\\'">'
          +'<img class="frame" src="'+c.frame+'" alt="">'
          +(c.pdot?'<span class="pdot" style="background:'+c.pdot+'"></span>':'')
          +'<img class="mark" src="'+c.mark+'" alt="">'
          +overlayHtml(c)
          +'</span>'
          +'<span class="x" title="__DBT_remove__">×</span></div>'
          +'<div class="slot-pme" data-uid="'+c.uid+'"></div></div>';
      } else {
        h+='<div class="slotcell"><div class="slot empty" data-grp="'+grp+'" data-idx="'+i+'">'
          +'<img class="blank" src="assets/Blank.png" alt=""></div></div>';
      }
    }
    container.innerHTML=h;
  }
  function renderDeck(){
    document.getElementById('legCount').textContent=legSlots.filter(Boolean).length;
    document.getElementById('othCount').textContent=mainSlots.filter(Boolean).length;
    renderSlotGroup(document.getElementById('legSlots'), legSlots, 'L');
    renderSlotGroup(document.getElementById('othSlots'), mainSlots, 'M');
    // mark picker units already in the deck
    var inUids={}; deckCards().forEach(function(c){ inUids[c.uid]=1; });
    for(var i=0;i<units.length;i++){
      var inDeck=!!inUids[units[i].dataset.uid];
      units[i].classList.toggle('in-deck', inDeck);
      var btn=units[i].querySelector('.u-add'); if(btn) btn.textContent=inDeck?'__DBT_in_deck__':'__DBT_add__';
    }
    renderStats(); syncCode();
    if(pmeOn()) recalcAll();
    if(document.getElementById('deckOnly').checked) applyFilter();
  }

  // Deck panel: click "×" to remove
  document.querySelector('.deckpane').addEventListener('click', function(e){
    var x=e.target.closest('.slot .x'); if(!x) return;
    var slot=x.closest('.slot'); if(slot && slot.dataset.uid) removeUid(slot.dataset.uid);
  });
  document.getElementById('clearDeck').addEventListener('click', function(){
    if(deckCards().length && confirm('__DBT_clear_deck_confirm__')){ clearSlots(); renderDeck(); }
  });

  // Drag to reorder (within the same group only: drop into an empty slot / swap with the target slot)
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

  // ---------- Stats ----------
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

    // category
    document.getElementById('stType').innerHTML = validTypes().map(function(t){
      var n=byType[t]||0;
      return '<span class="chip'+(n?'':' zero')+'"><img src="assets/CardType'+t+'.png" alt="">'
        +TYPE_LABEL[t]+' <b>'+n+'</b></span>';
    }).join('');
    // attribute
    document.getElementById('stAttr').innerHTML = [1,2,3,4,5].map(function(a){
      var n=byAttr[a]||0;
      return '<span class="chip'+(n?'':' zero')+'"><img src="assets/Attribute'+a+'.png" alt=""><b>'+n+'</b></span>';
    }).join('');
    // target count
    var tks=Object.keys(byTarget).sort();
    document.getElementById('stTarget').innerHTML = tks.length ? tks.map(function(t){
      return '<span class="chip"><b>'+t+'</b> '+byTarget[t]+'</span>';
    }).join('') : '<span class="empty-hint">—</span>';

    // Stat changes (individual): count cards hitting each icon (a card may hit several)
    var scCount={};
    list.forEach(function(c){ c.sc.forEach(function(n){ scCount[n]=(scCount[n]||0)+1; }); });
    function scChips(arr){ return arr.map(function(n){ var v=scCount[n]||0;
      return '<span class="chip'+(v?'':' zero')+'" title="'+SC_NAME[n]+'">'
        +'<img class="scicon" src="'+scIcon(n)+'" alt=""> <b>'+v+'</b></span>'; }).join(''); }
    document.getElementById('stScUp').innerHTML = scChips(SC_UP);
    document.getElementById('stScDn').innerHTML = scChips(SC_DN);

    // Skill class: cards with identical buff changes (+ whether they include HP heal) form one class; each card in exactly one
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
      if(g.heal) ic+='<img class="scicon" src="'+healIcon()+'" alt="" title="__DBT_hp_heal__">';
      if(!ic) ic='<span class="muted">__DBT_no_change__</span>';
      return '<span class="chip">'+ic+' <b>'+g.n+'</b></span>';
    }).join('') : '<span class="empty-hint">—</span>';

    // stacks Mt/An/Ba
    document.getElementById('stStack').innerHTML = ['Mt','An','Ba','Et'].map(function(k){
      return '<div><span class="k">'+k+'</span> '+(feat[k]||0)+' __DBT_cards_unit__ / <b>'+marks[k]+'</b> __DBT_stack_title__</div>';
    }).join('');
    // EH/SD/MN/CT
    document.getElementById('stFeat').innerHTML = ['EH','SD','MN','CT'].map(function(k){
      return '<div><span class="k">'+k+'</span> '+(feat[k]||0)+' __DBT_cards_unit__</div>';
    }).join('');
    // five passives
    document.getElementById('stGa').innerHTML = ['dmgup','supup','healup','ptup','rangeup'].map(function(k){
      return '<div><span class="k" style="min-width:120px">'+GA_LABEL[k]+'</span> '+(ga[k]||0)+' __DBT_cards_unit__</div>';
    }).join('');
    // per-level (4 types, excluding 効果範囲+1)
    document.getElementById('stLevels').innerHTML = ['dmgup','supup','healup','ptup'].map(function(code){
      var m=lv[code]; var keys=Object.keys(m).sort(function(a,b){return lvSortKey(a)-lvSortKey(b);});
      if(!keys.length) return '<div class="lvname">'+GA_LABEL[code]+'：—</div>';
      var head='', body='';
      keys.forEach(function(k){ head+='<th>'+k+'</th>'; body+='<td>'+m[k]+'</td>'; });
      return '<div class="lvname">'+GA_LABEL[code]+'</div>'
        +'<table class="lvtbl"><tr>'+head+'</tr><tr>'+body+'</tr></table>';
    }).join('');
  }

  // ---------- Deck code (allb.game-db.tw deck-builder URL) ----------
  // Format: base64( enc62(base-61) | LG... | __DBT_main__... | role ) carried in ?v=.
  //   base     = min twdb id (cardMstId*10+variant) in the deck
  //   per card = enc62(id - base + 61) + "4" (trailing "4" is the limit-break digit)
  //   role     = __DBT_role_front__ 0 / __DBT_role_back__ 1
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
    lg.sort(function(a,b){return a-b;}); nml.sort(function(a,b){return a-b;});  // order-independent
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
      else if(str.indexOf('|')!==-1) target=str;       // pasted raw target text
      else target=atob(str.replace(/ /g,'+'));         // pasted raw base64
    } catch(e){ alert('__DBT_decode_fail__'); return; }
    var parts=target.split('|');
    var base=dec62(parts[0])+61;
    if(parts.length<4 || isNaN(base)){ alert('__DBT_bad_format__'); return; }
    var r=(parts[3].trim()==='0')?'F':'B';
    role=r; clearSlots();
    document.getElementById('roleF').classList.toggle('on', r==='F');
    document.getElementById('roleB').classList.toggle('on', r==='B');
    rebuildCostJob();
    applyRoleToTypeFilter();
    var miss=0;
    function place(groupStr){
      if(!groupStr) return;
      groupStr.split(',').forEach(function(t){
        if(!t) return;
        var rel=dec62(t.slice(0,-1));                  // drop the trailing "4"
        if(isNaN(rel)){ miss++; return; }
        var list=twIndex[rel-61+base]||[], el=null;
        for(var i=0;i<list.length;i++){ if(isValidType(list[i].dataset.ct)){ el=list[i]; break; } }
        if(!el && list.length) el=list[0];             // fall back to one entry when neither awakening face fits the current role
        if(!(el && addUnit(el, true))) miss++;
      });
    }
    place(parts[1]); place(parts[2]);
    renderDeck(); applyFilter();
    if(miss) alert(miss+' __DBT_restore_fail__');
  }
  document.getElementById('loadCode').addEventListener('click', function(){ loadCode(document.getElementById('code').value); });
  document.getElementById('code').addEventListener('keydown', function(e){ if(e.key==='Enter') loadCode(this.value); });
  document.getElementById('copyCode').addEventListener('click', function(){
    var t=document.getElementById('code'); t.select();
    if(navigator.clipboard){ navigator.clipboard.writeText(t.value); } else { document.execCommand('copy'); }
    var b=this, o=b.textContent; b.textContent='__DBT_copied__'; setTimeout(function(){ b.textContent=o; }, 1000);
  });

  // ---------- Utilities ----------
  function iconUrl(uid){ return 'assets/remote/Image/CardIcon/S/CardIconS0'+uid+'.png'; }
  function escAttr(s){ return (s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'); }

  // ---------- 牌効 calculator ----------
  // Per-effect conversion rate = product of ~13 regions (value region fixed at 1). Static per-card
  // data comes from data-calc (skill_calc.py); the deck-wide UP region + all user settings are applied here.
  var PME_TACTICS = __PME_TACTICS__;
  var deckpane = document.querySelector('.deckpane');
  function pmeOn(){ return deckpane.classList.contains('pme-on'); }
  function pesc(s){ return (s+'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

  // adx selectors (theme turns the 1.05-based options into 1.055-based)
  var ADX_LABEL=['0.95','1','1.05','1.05×0.95'];
  (function(){
    var row=document.getElementById('adxRow'), names=__DBJS_ATTR_ARR__, h='';
    for(var a=1;a<=5;a++){
      h+='<label>'+names[a-1]+'<select id="adx'+a+'">';
      for(var c=0;c<4;c++) h+='<option value="'+c+'"'+(c===1?' selected':'')+'>'+ADX_LABEL[c]+'</option>';
      h+='</select></label>';
    }
    row.innerHTML=h;
  })();

  // costume main job — only the options for the current role (__DBT_role_front__ 1-4 / __DBT_role_back__ 5-7)
  function rebuildCostJob(){
    var sel=document.getElementById('costJob');
    var opts = role==='F' ? __DBJS_COSTUME_F__
                          : __DBJS_COSTUME_B__;
    var h='<option value="0">__DBT_none__</option>';
    opts.forEach(function(o){ h+='<option value="'+o[0]+'">'+o[1]+'</option>'; });
    sel.innerHTML=h;
  }
  rebuildCostJob();

  // active-tactics pickers = a mini tactics list (clickable framed icons, like the tactics page)
  function tacIconUrl(uid){ return 'assets/remote/Image/TacticsIcon/S/TacticsIconS'+('00'+uid).slice(-3)+'.png'; }
  function tacFrame(r){ return 'assets/Sprite/IconRarity0'+(r===4?4:(r===5?5:6))+'LImage.png'; }
  function buildTacIcons(elId, arr){
    var box=document.getElementById(elId), h='';
    for(var i=0;i<arr.length;i++){
      var o=arr[i];
      h+='<button type="button" class="tac-ic" data-i="'+i+'" title="'+escAttr(o.name)+'">'
        +'<span class="tcimg">'
        +'<img class="bg" src="assets/Blank.png" alt="">'
        +'<img class="art" src="'+tacIconUrl(o.uid)+'" alt="" loading="lazy">'
        +'<img class="frame" src="'+tacFrame(o.rar)+'" alt="">'
        +'<img class="mark" src="assets/markers/mk_tactics.png" alt="">'
        +'</span></button>';
    }
    box.innerHTML = h || '<span class="muted">__DBT_none_dash__</span>';
  }
  buildTacIcons('tacMyAttr', PME_TACTICS.my_attr);
  buildTacIcons('tacMyRate', PME_TACTICS.my_rate);
  buildTacIcons('tacMyEff', PME_TACTICS.my_eff);
  buildTacIcons('tacEnShield', PME_TACTICS.en_shield);
  buildTacIcons('tacEnRate', PME_TACTICS.en_rate);
  buildTacIcons('tacEnEff', PME_TACTICS.en_eff);
  function selTac(elId, arr){
    var out=[], bs=document.querySelectorAll('#'+elId+' .tac-ic.on');
    for(var i=0;i<bs.length;i++) out.push(arr[+bs[i].dataset.i].info);
    return out;
  }

  function pnum(id){ var v=parseFloat(document.getElementById(id).value); return isNaN(v)?0:v; }
  function pchk(id){ return document.getElementById(id).checked; }

  var PBASE=[0.15,0.225,0.30];   // passive activation rate by "+" count (0/1/2)
  function recalcAll(){
    if(!pmeOn()) return;
    var deck=deckCards(), a;
    var charm={},adx={},theme={};
    for(a=1;a<=5;a++){ charm[a]=pnum('charm'+a); adx[a]=+document.getElementById('adx'+a).value; theme[a]=pchk('theme'+a); }
    var costJob=+document.getElementById('costJob').value;
    var sMt=pchk('sMt'), sAn=pchk('sAn'), sEt=pchk('sEt'), ehct=pchk('ehct');
    var myAttr=selTac('tacMyAttr',PME_TACTICS.my_attr), myRate=selTac('tacMyRate',PME_TACTICS.my_rate),
        myEff=selTac('tacMyEff',PME_TACTICS.my_eff), enShield=selTac('tacEnShield',PME_TACTICS.en_shield),
        enRate=selTac('tacEnRate',PME_TACTICS.en_rate), enEff=selTac('tacEnEff',PME_TACTICS.en_eff);

    // tactics aggregates (same-effect values additive per 11.1; activation rate multiplicative)
    // __DBT_eff__ matches by card TYPE (支援/妨害効果 = the skill effect of 支援/妨害 cards), keyed by cardType
    var attrBoost={1:0,2:0,3:0,4:0,5:0}, shieldDown={1:0,2:0,3:0,4:0,5:0};
    var effUp={}, effDown={};   // keyed by targetCardType
    var disadv=0, dmgRedP=0, dmgRedM=0, myRateUp=0, enRateDown=0, activeTypes={};
    function addAttr(at, v){ if(at){ if(theme[at]) v*=1.1; attrBoost[at]+=v; } }   // dual attr: both attributes boosted
    myAttr.forEach(function(t){ addAttr(t.tAttr, t.up/100); addAttr(t.tAttr2, t.up2/100); activeTypes[t.type]=1; });
    myRate.forEach(function(t){ myRateUp+=t.rateUp/100; activeTypes[t.type]=1; });
    myEff.forEach(function(t){ if(t.tCard) effUp[t.tCard]=(effUp[t.tCard]||0)+t.up/100; if(t.disadv) disadv+=t.disadv/100; activeTypes[t.type]=1; });
    enShield.forEach(function(t){ if(t.tAttr) shieldDown[t.tAttr]+=t.down/100; if(t.tAttr2) shieldDown[t.tAttr2]+=(t.down2||t.down)/100;
                                  dmgRedP+=(t.dmgRedP||0)/100; dmgRedM+=(t.dmgRedM||0)/100; });
    enRate.forEach(function(t){ enRateDown+=t.rateDown/100; });
    enEff.forEach(function(t){ if(t.tCard) effDown[t.tCard]=(effDown[t.tCard]||0)+t.down/100; });

    // deck-aggregate UP pools (every deck card's passive + Legendary)
    var passPool=[], legPool=[];
    deck.forEach(function(c){ if(!c.calc) return;
      (c.calc.pu||[]).forEach(function(p){ passPool.push({k:p.k,coeff:p.c,host:c.calc.a,plus:c.calc.pp||0}); });
      (c.calc.lu||[]).forEach(function(l){ legPool.push(l); });
    });
    function passUP(kind, detail){
      // The 支援UP passive boosts BOTH 支援(buff) and 妨害(debuff) effect lines
      // (incl. buff changes carried by damage / heal cards), so debuff draws the buff pool.
      var pk=(kind==='debuff')?'buff':kind, s=0;
      passPool.forEach(function(p){ if(p.k!==pk) return;
        var r=(PBASE[p.plus]||0.15)+(theme[p.host]?0.02:0);
        r=r*(1+myRateUp)*(1-enRateDown); if(r<0)r=0; if(r>1)r=1;
        var add=p.coeff*1.5*r;
        if(detail) detail.push({coeff:p.coeff, rate:r, plus:p.plus, host:p.host, add:add, kind:p.k});
        s+=add; });
      return s;
    }
    function legUP(at,kind,atk,detail){ var s=0; legPool.forEach(function(l){ if(l.a===at&&l.k===kind&&(l.t===0||l.t===atk)){ s+=l.p; if(detail) detail.push(l); } }); return s; }
    // ADX 4-choice: 0.95 / 1 / (1.05, or 1.055 with theme) / that ×0.95.
    // The 0.95 component (choice #0 and #3) applies only to ダメージ·妨害; 支援·回復 drop it.
    function adxVal(at, kind){
      var idx=adx[at], t=theme[at]?1.055:1.05, has95=(kind==='dmg'||kind==='debuff');
      if(idx===2) return t;
      if(idx===3) return has95?t*0.95:t;
      if(idx===0) return has95?0.95:1;
      return 1;
    }

    // clear previous results, then write each card's per-effect rates under its slot
    BREAKDOWN={};
    var allp=document.querySelectorAll('.slot-pme');
    for(var i=0;i<allp.length;i++) allp[i].innerHTML='';
    var totL={}, nseen=0, anyE=false;
    deck.forEach(function(c){ if(!c.calc||!c.calc.e.length) return;
      var at=c.calc.a, ct=c.calc.c;
      var trig=(c.calc.ut||[]).some(function(t){ return activeTypes[t]; });
      var cos=(costJob&&costJob===ct)?1.15:1;
      var charmM=1+(charm[at]||0)/100, themeM=theme[at]?1.1:1;
      var attrB=attrBoost[at]||0, shB=shieldDown[at]||0;
      var ehMul=ehct?((c.calc.eh>0?c.calc.eh:1)*(c.calc.ct>0?c.calc.ct:1)):1;
      var parts='';
      c.calc.e.forEach(function(e, ei){
        var addMag=trig?(c.calc.am||0):0, tm=c.calc.tm||0;
        var mag=(e.m+addMag)*(1+tm);
        var stack=e.k==='dmg'?(sMt?1.2:1):(e.k==='heal'?(sEt?1.3:1):(sAn?1.3:1));
        var pdet=[], ldet=[], pUp=passUP(e.k,pdet), lUp=legUP(at,e.k,e.t,ldet), up=1+pUp+lUp;
        // __DBT_bd_order__: attribute boost (all kinds) + __DBT_eff__ by card type − 相手 __DBT_eff__ by card type;
        // attribute shield skips 回復; damage shield + __DBT_bd_disadv__ only hit damage
        var cmdAttr=attrB, cmdEffUp=effUp[ct]||0, cmdEffDown=effDown[ct]||0,
            cmdShB=(e.k!=='heal')?shB:0, cmdDmgRed=0, cmdDis=0;
        if(e.k==='dmg'){ cmdDmgRed=(e.t===2?dmgRedM:dmgRedP); cmdDis=disadv; }
        var cmd=1+cmdAttr+cmdEffUp-cmdEffDown-cmdShB-cmdDmgRed+cmdDis;
        var adxM=adxVal(at, e.k);   // 0.95 component is damage/debuff only
        var rate=e.g*mag*1.5*cos*1.1*stack*charmM*adxM*themeM*up*ehMul*cmd*e.n;
        if(!totL[e.l]){ totL[e.l]={v:0,k:e.k,i:nseen++}; } totL[e.l].v+=rate; anyE=true;
        // store the per-region breakdown for the click-to-explain popup
        var R=[
          {n:'__DBT_bd_numeric__', v:1, note:'__DBT_bd_fixed_conv__'},
          {n:'__DBT_bd_gvg__', v:e.g, note:e.k==='dmg'?'__DBT_bd_dmg01__':'__DBT_bd_nondmg1__'},
          {n:'__DBT_bd_skillcoef__', v:mag, note:magNote(e.m, addMag, tm)},
          {n:'__DBT_bd_skillgrade__', v:1.5, note:'__DBT_bd_fixed_lvmax__'},
          {n:'__DBT_specialty__', v:cos, note:cos>1?('__DBT_bd_match_open__'+ct+')'):'__DBT_bd_nomatch1__'},
          {n:'__DBT_bd_grace__', v:1.1, note:'__DBT_bd_fixed__'},
          {n:'__DBT_stack_title__', v:stack, note:stackNote(e.k,sMt,sAn,sEt)},
          {n:'CHARM', v:charmM, note:'__DBT_attr_lbl__'+ATTR_JP[at]+' +'+(charm[at]||0)+'%'},
          {n:'ADX', v:adxM, note:adxNote(at, e.k)},
          {n:'__DBT_theme__', v:themeM, note:theme[at]?('__DBT_attr_lbl__'+ATTR_JP[at]+'__DBT_bd_theme_match__'):'__DBT_bd_nomatch1__'},
          {n:'__DBT_bd_up__', v:up, note:upNote(pdet,ldet)},
          {n:'__DBT_eff__', v:ehMul, note:ehct?('ON: EH×'+(c.calc.eh>0?c.calc.eh:1)+' × CT×'+(c.calc.ct>0?c.calc.ct:1)):'OFF → 1'},
          {n:'__DBT_bd_order__', v:cmd, note:cmdNote(cmdAttr,cmdEffUp,cmdEffDown,cmdShB,cmdDmgRed,cmdDis)},
          {n:'__DBT_bd_random__', v:e.n, note:(e.k==='dmg'||e.k==='heal')?'__DBT_bd_dmgheal095__':'__DBT_bd_nondmg1__'}
        ];
        BREAKDOWN[c.uid+'#'+ei]={card:c.name, label:e.l, kind:e.k, R:R, rate:rate};
        parts+='<span class="pme-eff k-'+e.k+'" data-bd="'+c.uid+'#'+ei+'" title="__DBT_click_breakdown__">'+pesc(e.l)+' <b>'+rate.toFixed(3)+'</b></span>';
      });
      var box=document.querySelector('.slot-pme[data-uid="'+c.uid+'"]');
      if(box) box.innerHTML=parts;
    });
    var tb=document.getElementById('pmeTotal');
    if(tb){
      if(!anyE){ tb.innerHTML='<span class="muted">__DBT_none_dash__</span>'; }
      else {
        var KP={dmg:0,heal:1,buff:2,debuff:3};
        var labels=Object.keys(totL).sort(function(x,y){ return (KP[totL[x].k]-KP[totL[y].k])||(totL[x].i-totL[y].i); });
        var rows='';
        labels.forEach(function(lbl){ var o=totL[lbl];
          rows+='<span class="pt-k k-'+o.k+'">'+pesc(lbl)+' <b>'+o.v.toFixed(3)+'</b></span>';
        });
        tb.innerHTML='<div class="pt-h">__DBT_total_effect__</div><div class="pt-row">'+rows+'</div>';
      }
    }
  }

  // ----- breakdown popup (click a 牌効 chip to see how the number was produced) -----
  var BREAKDOWN={};
  var ATTR_JP=__DBJS_ATTR_MAP__;
  function fmtNum(v){ var s=(Math.round(v*1e6)/1e6).toString(); return s; }
  function magNote(base, add, tm){
    var s='__DBT_bd_base_word__ '+fmtNum(base);
    if(add) s+=' + __DBT_bd_command_word__ '+fmtNum(add);
    if(tm) s+=' ×(1+__DBT_bd_charge_word__ '+fmtNum(tm)+')';
    return s;
  }
  function stackNote(kind,sMt,sAn,sEt){
    if(kind==='dmg') return sMt?'Mt ON → 1.2':'Mt OFF → 1';
    if(kind==='heal') return sEt?'Et ON → 1.3':'Et OFF → 1';
    return sAn?'An ON → 1.3':'An OFF → 1';
  }
  function adxNote(at, kind){
    var s='__DBT_bd_adx_select__'+adxIdx(at)+' (__DBT_attr_lbl__'+ATTR_JP[at]+(themeSel(at)?'__DBT_bd_theme_has__':'')+')';
    if((kind!=='dmg'&&kind!=='debuff')&&(adxIdx(at)===0||adxIdx(at)===3)) s+='__DBT_bd_excl095__';
    return s;
  }
  function adxIdx(at){ return +document.getElementById('adx'+at).value; }
  function themeSel(at){ return document.getElementById('theme'+at).checked; }
  var UP_JP=__DBJS_UP_JP__;
  function upNote(pdet,ldet){
    var lines=['1'];
    pdet.forEach(function(p){ lines.push('+ '+(UP_JP[p.kind]||p.kind)+' __DBT_bd_coeff__'+fmtNum(p.coeff)+'×1.5×__DBT_bd_proc_word__'+fmtNum(p.rate)+' = '+fmtNum(p.add)); });
    ldet.forEach(function(l){ lines.push('+ Legendary '+fmtNum(l.p)); });
    if(lines.length===1) lines.push('__DBT_bd_no_up__');
    return lines.join('\\n');
  }
  function cmdNote(attr,effUp,effDown,shB,dmgRed,dis){
    var lines=['1'];
    if(attr) lines.push('+ __DBT_attr_lbl__ '+fmtNum(attr));
    if(effUp) lines.push('+ __DBT_bd_ally_eff__ '+fmtNum(effUp));
    if(effDown) lines.push('− __DBT_bd_enemy_eff__ '+fmtNum(effDown));
    if(shB) lines.push('− __DBT_bd_attr_shield__ '+fmtNum(shB));
    if(dmgRed) lines.push('− __DBT_bd_dmg_shield__ '+fmtNum(dmgRed));
    if(dis) lines.push('+ __DBT_bd_disadv__ '+fmtNum(dis));
    if(lines.length===1) lines.push('__DBT_bd_no_order__');
    return lines.join('\\n');
  }
  function showBreakdown(key){
    var bd=BREAKDOWN[key]; if(!bd) return;
    var rows='';
    bd.R.forEach(function(r){
      rows+='<tr><td class="bn">'+pesc(r.n)+'</td><td class="bv">×'+fmtNum(r.v)+'</td>'
           +'<td class="bd">'+pesc(r.note).replace(/\\n/g,'<br>')+'</td></tr>';
    });
    var kindJp=__DBJS_KIND_JP__[bd.kind]||bd.kind;
    document.getElementById('bdTitle').innerHTML=pesc(bd.card)+' <span class="bk k-'+bd.kind+'">'+pesc(bd.label)+' ('+kindJp+')</span>';
    document.getElementById('bdBody').innerHTML=rows;
    document.getElementById('bdTotal').textContent='__DBT_bd_effect_total__ = '+bd.rate.toFixed(4);
    document.getElementById('bdModal').classList.add('open');
  }
  function hideBreakdown(){ document.getElementById('bdModal').classList.remove('open'); }
  document.addEventListener('click', function(e){
    var chip=e.target.closest('.pme-eff'); if(chip&&chip.dataset.bd){ showBreakdown(chip.dataset.bd); return; }
    if(e.target.id==='bdModal'||e.target.closest('#bdClose')) hideBreakdown();
  });
  document.addEventListener('keydown', function(e){ if(e.key==='Escape') hideBreakdown(); });

  document.getElementById('pmeToggle').addEventListener('click', function(){
    var on=!deckpane.classList.contains('pme-on');
    deckpane.classList.toggle('pme-on', on);
    this.textContent='__DBT_sim_label__ '+(on?'ON':'OFF');
    this.classList.toggle('active', on);
    if(on) recalcAll();
  });
  document.getElementById('pmePanel').addEventListener('change', recalcAll);
  document.getElementById('pmePanel').addEventListener('input', function(e){ if(e.target.type==='number') recalcAll(); });
  document.getElementById('pmePanel').addEventListener('click', function(e){
    var b=e.target.closest('.tac-ic'); if(!b) return;
    // only one active tactics per side (column), regardless of type
    var col=b.closest('.pme-tcol'), wasOn=b.classList.contains('on');
    if(col){ var ons=col.querySelectorAll('.tac-ic.on'); for(var i=0;i<ons.length;i++) ons[i].classList.remove('on'); }
    if(!wasOn) b.classList.add('on');
    recalcAll();
  });

  // ---------- Init ----------
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
    config.ensure_output_dir()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html_text)
    leg = sum(1 for u in units if u["leg"])
    print("Generated deck builder: Legendary %d units / other %d units (total %d)"
          % (leg, len(units) - leg, len(units)))
    print("Output file: %s" % OUT)


if __name__ == "__main__":
    main()
