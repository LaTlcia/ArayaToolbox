# -*- coding: utf-8 -*-
"""
牌効 (card-effect conversion rate) — skill parsing helpers
=========================================================
Pure parsing used by the deck builder's 牌効 calculator. We only parse the static,
per-card pieces here; the live formula (CHARM / adx / theme / costume / tactics / deck-wide
UP region) runs in the page's JavaScript, fed by the data these functions produce.

The 牌効 of one effect = product of ~13 regions, with the value region (数値区) fixed at 1:
  GVG補正 × 技能系数 × 技能等級(1.5) × 衣装 × 恩惠(1.1) × stack × CHARM × adx × 主题
          × UP区 × 牌効技能(EH·CT) × 指令加成 × 乱数
See the plan file for the full mapping. This module exposes:
  * skill_effects(gvg)   -> per-effect lines + skill-level modifiers (mag/ADD/TIME/EH/CT)
  * passive_up(gvgAuto)  -> this card's GvgAuto UP coefficients (for the deck-wide UP pool)
  * passive_plus(gvgAuto)-> the "+" count on the passive name (activation tier 0/1/2)
  * legendary_up(leg)    -> this card's [レギオンマッチ] Legendary UP entries (attr/kind/pct)
  * tactics_effect_info(eff) -> a tactics' gvg-effect calc data (for the active-tactics pickers)

Functions accept either raw masterdata skill dicts (parameterText / description) or the
resolved skills carried on card entries (pt / desc), so they are easy to unit-test.
Standard library only.
"""

import json
import re

# ---------------------------------------------------------------------------
# Small accessors (work on raw masterdata or resolved-entry skill dicts)
# ---------------------------------------------------------------------------
def _pt(sk):
    if not sk:
        return {}
    pt = sk.get("pt")
    if pt is not None:
        return pt
    raw = sk.get("parameterText")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return raw or {}


def _desc(sk):
    return (sk.get("desc") or sk.get("description") or "") if sk else ""


def _name(sk):
    return (sk.get("name") or "") if sk else ""


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# GVG skill -> effect lines + skill-level modifiers
# ---------------------------------------------------------------------------
# kind: 'dmg' (damage) / 'heal' / 'buff' (ally 支援) / 'debuff' (enemy 妨害)
# gvg correction: damage 0.1, everything else 1. random: damage & heal 0.95, buff/debuff 1.
_ATTR_NAME = {1: "火", 2: "水", 3: "風", 4: "光", 5: "闇"}
_BUFF_LABELS = [
    ("BUFFER_PHYSICAL_ATTACK_MAGNIFICATION", "ATK"),
    ("BUFFER_MAGICAL_ATTACK_MAGNIFICATION", "Sp.ATK"),
    ("BUFFER_PHYSICAL_DEFENSE_MAGNIFICATION", "DEF"),
    ("BUFFER_MAGICAL_DEFENSE_MAGNIFICATION", "Sp.DEF"),
    ("BUFFER_ATTRIBUTE_ATTACK_MAGNIFICATION", "属性攻"),
    ("BUFFER_ATTRIBUTE_DEFENSE_MAGNIFICATION", "属性防"),
    ("BUFFER_MAX_HP_MAGNIFICATION", "最大HP"),
]
_DEBUFF_LABELS = [
    ("DEBUFFER_PHYSICAL_ATTACK_MAGNIFICATION", "敵ATK"),
    ("DEBUFFER_MAGICAL_ATTACK_MAGNIFICATION", "敵Sp.ATK"),
    ("DEBUFFER_PHYSICAL_DEFENSE_MAGNIFICATION", "敵DEF"),
    ("DEBUFFER_MAGICAL_DEFENSE_MAGNIFICATION", "敵Sp.DEF"),
    ("DEBUFFER_ATTRIBUTE_ATTACK_MAGNIFICATION", "敵属性攻"),
    ("DEBUFFER_ATTRIBUTE_DEFENSE_MAGNIFICATION", "敵属性防"),
]

# EH: 異属性で「スキル効果がN倍」.  CT: 劣勢で「効果がN倍」 (the ADD_MAGNIFICATION_AT_LOSING mechanic).
_RE_EH = re.compile(r"異なる場合[^。]*?スキル効果が([0-9.]+)倍")
_RE_CT = re.compile(r"劣勢[^。]*?効果が([0-9.]+)倍")


def _mag_lines(pt, key, kind, label, gvg, rand):
    """One or more effect lines for a magnification key (value may be a [{attribute,value}] list)."""
    if key not in pt:
        return []
    v = pt[key]
    out = []
    if isinstance(v, list):
        for d in v:
            lab = label
            # attribute-keyed magnification entries carry their attribute -> spell it out
            # (e.g. 「属性防」 -> 「水属性防」, debuff 「敵属性攻」 -> 「敵火属性攻」)
            if "attribute" in d and "属性" in label:
                lab = label.replace("属性", _ATTR_NAME.get(d["attribute"], "") + "属性", 1)
            out.append({"kind": kind, "label": lab, "mag": _num(d.get("value")),
                        "gvg": gvg, "rand": rand, "atk": 0})
    else:
        out.append({"kind": kind, "label": label, "mag": _num(v), "gvg": gvg, "rand": rand, "atk": 0})
    return out


def skill_effects(gvg):
    """Parse a GVG skill into {effs, addMag, upT, timeMax, eh, ct}.

    effs: list of {kind,label,mag,gvg,rand,atk}. Skill-level modifiers (addMag from
    ADD_MAGNIFICATION_BY_TACTICS, upT = EFFECT_UP_TACTICS types, timeMax = max-charge bonus,
    eh/ct = the 牌効技能 multipliers) apply to every effect line and are resolved in JS.
    """
    pt = _pt(gvg)
    desc = _desc(gvg)
    atk_type = pt.get("ATTACK_TYPE") or 0     # 1 = 通常 (physical), 2 = 特殊 (magical)
    effs = []
    if "ATTACK_MAGNIFICATION" in pt:
        effs.append({"kind": "dmg", "label": "ダメージ", "mag": _num(pt["ATTACK_MAGNIFICATION"]),
                     "gvg": 0.1, "rand": 0.95, "atk": atk_type})
    effs += _mag_lines(pt, "RECOVERY_MAGNIFICATION", "heal", "回復", 1.0, 0.95)
    for key, label in _BUFF_LABELS:
        effs += _mag_lines(pt, key, "buff", label, 1.0, 1.0)
    for key, label in _DEBUFF_LABELS:
        effs += _mag_lines(pt, key, "debuff", label, 1.0, 1.0)

    m = _RE_EH.search(desc)
    eh = _num(m.group(1)) if m else 0.0
    m = _RE_CT.search(desc)
    ct = _num(m.group(1)) if m else 0.0
    return {
        "effs": effs,
        "addMag": _num(pt.get("ADD_MAGNIFICATION_BY_TACTICS", 0)),
        "upT": list(pt.get("EFFECT_UP_TACTICS", []) or []),
        "timeMax": _num(pt.get("TIME_EFFECT_MAX_SKILL_EFFECT_VALUE", 0)),
        "eh": eh, "ct": ct,
    }


# ---------------------------------------------------------------------------
# Passive (GvgAuto) UP coefficients  (UP区: deck-wide pool)
# ---------------------------------------------------------------------------
# group key in parameterText -> (effect kind it boosts, magnification key)
_PASS_UP = [
    ("ATTACK", "dmg", "ATTACK_UP_MAGNIFICATION"),
    ("RECOVERY", "heal", "RECOVERY_UP_MAGNIFICATION"),
    ("BUFFER", "buff", "BUFFER_UP_MAGNIFICATION"),
]
_RE_PLUS = re.compile(r"[Ⅰ-Ⅹ](\++)?\s*$")   # trailing roman numeral + any "+"


def passive_up(ga):
    """[{kind, coeff}] for the card's GvgAuto UP passives (ダメージ/回復/支援 UP)."""
    pt = _pt(ga)
    out = []
    for grp, kind, mkey in _PASS_UP:
        sub = pt.get(grp)
        if isinstance(sub, dict) and mkey in sub:
            out.append({"kind": kind, "coeff": _num(sub[mkey])})
    return out


def passive_plus(ga):
    """The "+" count on the passive name -> activation tier (0=15% / 1=22.5% / 2=30%)."""
    m = _RE_PLUS.search(_name(ga))
    return min(len(m.group(1)), 2) if (m and m.group(1)) else 0


# ---------------------------------------------------------------------------
# Legendary UP  (UP区: deck-wide pool; only [レギオンマッチ] skills count for GVG)
# ---------------------------------------------------------------------------
_ATTR_CH = {"火": 1, "水": 2, "風": 3, "光": 4, "闇": 5}
_RE_LEG = re.compile(
    r"([火水風光闇](?:/[火水風光闇])*)属性の(通常攻撃|特殊攻撃|支援|妨害|回復)[^。]*?([0-9.]+)%アップ")
_LEG_KIND = {"通常攻撃": ("dmg", 1), "特殊攻撃": ("dmg", 2),
             "支援": ("buff", 0), "妨害": ("debuff", 0), "回復": ("heal", 0)}


def legendary_up(leg):
    """[{attr, kind, atk, pct}] for a [レギオンマッチ] Legendary skill (empty otherwise)."""
    if not leg:
        return []
    name, desc = _name(leg), _desc(leg)
    if "レギオンマッチ" not in name and "レギオンマッチ" not in desc:
        return []
    out = []
    for m in _RE_LEG.finditer(desc):
        kind, atk = _LEG_KIND[m.group(2)]
        pct = _num(m.group(3)) / 100.0
        for ch in m.group(1).split("/"):
            if ch in _ATTR_CH:
                out.append({"attr": _ATTR_CH[ch], "kind": kind, "atk": atk, "pct": pct})
    return out


# クリ率UP: 「さらにクリティカル発動確率を2.5%アップさせる」 — additive to the 5% base
# crit rate, condition scope = the main sentence's attributes / card kind.
_RE_LEG_CRIT = re.compile(r"クリティカル発動確率[をが]([0-9.]+)%アップ")


def legendary_crit(leg):
    """[{attr, kind, atk, pct}] crit-rate additions of a [レギオンマッチ] Legendary skill."""
    if not leg:
        return []
    name, desc = _name(leg), _desc(leg)
    if "レギオンマッチ" not in name and "レギオンマッチ" not in desc:
        return []
    mc = _RE_LEG_CRIT.search(desc)
    if not mc:
        return []
    pct = _num(mc.group(1)) / 100.0
    out = []
    for m in _RE_LEG.finditer(desc):
        kind, atk = _LEG_KIND[m.group(2)]
        for ch in m.group(1).split("/"):
            if ch in _ATTR_CH:
                out.append({"attr": _ATTR_CH[ch], "kind": kind, "atk": atk, "pct": pct})
    return out


# ---------------------------------------------------------------------------
# Tactics gvg-effect -> calc data (for the active-tactics pickers)
# ---------------------------------------------------------------------------
def tactics_effect_info(eff):
    """Calc-relevant numbers of a tactics' GVG effect.

    up / up2   : primary / secondary skill-effect-up % (属性 / 特効 boost). Dual-attribute
                 tactics (火水… type 31-56) boost two attributes: up for tAttr, up2 for tAttr2.
    down / down2 : skill-effect-down % (盾 / enemy 特効), primary / secondary.
    tAttr / tAttr2 : target / second-target attribute (1-5, 0 = none) -> match by card attribute.
    tCard      : target card type (1-7, 0 = none)            -> 特効 match by effect kind.
    rateUp/rateDown : auto-skill activation %                 -> 発動率 (UP区 activation).
    disadv     : attackEffectUpRateUnderDisadvantage %        -> 劣勢時 attack boost.
    dmgRedP/dmgRedM : physical / magical damage-reduction %   -> ダメージ盾 (reduces our damage).
    type       : the gvg effect `type` (for EFFECT_UP_TACTICS / ADD_MAGNIFICATION trigger).
    """
    try:
        pt = json.loads(eff.get("parameterText") or "{}")
    except Exception:
        pt = {}

    def _i(key):
        try:
            return int(pt[key])
        except (KeyError, ValueError, TypeError):
            return 0

    def _max(prefix):
        return max([int(v) for k, v in pt.items()
                    if k.startswith(prefix) and str(v).lstrip("-").isdigit()], default=0)

    up = _max("skillEffectUp")
    up2 = _max("secondSkillEffectUp")
    down = max(_max("skillEffectDown"), _i("attributeGuardRate"))
    down2 = _max("secondSkillEffectDown")
    return {
        "type": eff.get("type", 0),
        "up": up, "up2": up2, "down": down, "down2": down2,
        "tAttr": _i("targetAttribute"), "tAttr2": _i("secondTargetAttribute"),
        "tCard": _i("targetCardType"),
        "rateUp": _i("autoSkillProbabilityUpRate"),
        "rateDown": _i("autoSkillProbabilityDownRate"),
        "disadv": _i("attackEffectUpRateUnderDisadvantage"),
        "dmgRedP": _i("physicalDamageReductionRate"),
        "dmgRedM": _i("magicalDamageReductionRate"),
    }
