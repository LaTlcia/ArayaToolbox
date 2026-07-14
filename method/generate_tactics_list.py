# -*- coding: utf-8 -*-
"""
Tactics (commands / オーダー) list generator
============================================
Build an HTML list of every tactics rarity entry from the game masterdata JSON in
A.RA.YA/MasterdataBase/. Mirrors the card list visually (dense filterable/sortable table)
but for tactics, which have:
  * one entry per (tactics, rarity)  -- every rarity is its own row
  * a base panel + limit-break bonus, no awakening (simpler than cards)
  * two skills only: 対HUGE (questTacticsEffect) and GVG (gvgTacticsEffect); no Legendary
  * each skill shows a timing line (preparation / effect time / MP) before the description,
    and the description has its concrete numbers injected after 増加/減少/… from parameterText.

How to run (in the localdb conda env):
    conda run -n localdb python ArayaToolbox/generate_tactics_list.py
Then open the generated tactics_list.html in a browser.

All displayed game text is kept in the original Japanese (not translated).
Standard library only (plus the shared helpers imported from generate_card_list).
"""

import os as _os, sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import json
import html
import os
import re

import config
import card_markers
from generate_card_list import load_mst, fmt, build_dropdown, lbb_bonus

# ---------------------------------------------------------------------------
# Paths / behavior come from config.py
# ---------------------------------------------------------------------------
OUT = config.TACTICS_LIST_OUT

# Tactics icons come from the local mirror (downloaded by assets_sync.py; offline-capable).
# uniqueId is zero-padded to 3 digits (tactics uniqueIds are small, 1..~227).
TACTICS_ICON_URL = config.URL_TACTICS_ICON

# ---------------------------------------------------------------------------
# Stat fields (the four panels). bonusType 1/2/3/4 = 通攻/通防/特攻/特防 (same as cards).
# ---------------------------------------------------------------------------
STAT_FIELD = {
    1: "maxPhysicalAttack",    # 通攻
    2: "maxPhysicalDefense",   # 通防
    3: "maxMagicalAttack",     # 特攻
    4: "maxMagicalDefense",    # 特防
}

# ---------------------------------------------------------------------------
# Effect-category classification (the 効果類別 filter), keyed by the GVG effect's `type`.
# The GVG effect is the canonical レギオンマッチ effect these tactics are built around.
# ---------------------------------------------------------------------------
CAT_LABEL = config.section("tactics_list.cat")
CAT_DEFS = [(c, CAT_LABEL[c]) for c in ("attr", "shield", "rate", "buff", "eff", "mp", "other")]

_TYPE_CAT = {}
for _ty in (1, 2, 3, 31, 32, 33, 54, 55, 56):
    _TYPE_CAT[_ty] = "attr"
for _ty in (15, 16, 17, 18):
    _TYPE_CAT[_ty] = "shield"
for _ty in (10, 11):
    _TYPE_CAT[_ty] = "rate"
for _ty in [6, 7, 8, 9] + list(range(34, 54)):
    _TYPE_CAT[_ty] = "buff"
for _ty in (4, 59, 60, 64, 65):
    _TYPE_CAT[_ty] = "eff"
for _ty in (5, 12, 13, 14):
    _TYPE_CAT[_ty] = "mp"
for _ty in (19, 20, 21, 22, 23, 25):
    _TYPE_CAT[_ty] = "other"


def _effect_category(eff):
    if not eff:
        return "other"
    ty = eff.get("type", 0)
    if ty >= 1000:        # quest (対HUGE) variant types are GVG type + 1000; normalize
        ty -= 1000
    return _TYPE_CAT.get(ty, "other")


# ---------------------------------------------------------------------------
# Skill value injection: insert each effect's concrete numbers after 増加/減少/…
#
# Values live in parameterText (a JSON blob). Most effects use a single value (insert it
# after every effect verb). A few use two values; the bigger magnitude word maps to the
# bigger value (大増加 > 増加), and when magnitudes tie we fall back to source order.
# Keys that are targets/conditions (not display values) are ignored. 回復 is excluded:
# MP recovery already states its percentage inline (e.g. 「MPを20%回復」).
# ---------------------------------------------------------------------------
_META_KEYS = {
    "targetAttribute", "secondTargetAttribute", "enemyTargetAttribute", "targetCardType",
    "terminatedConditionRemainTime", "terminatedConditionGuildPointRatio",
    "overwritePreparationTime", "enemyOverwriteEffectTime", "effectNum",
}
_MAG_RANK = {"超特大": 8, "極大": 7, "特大": 6, "大": 5, "中": 4, "小": 2, "僅かに": 1, "僅か": 1}
_VERB_RE = re.compile(r"(超特大|極大|特大|大|中|小|僅かに|僅か)?(増加|減少|上昇|低下|軽減)")


def _mag_rank(prefix):
    return _MAG_RANK.get(prefix, 3) if prefix else 3


def _value_params(eff):
    """parameterText values that should be shown (excludes targets/conditions; numeric only)."""
    try:
        d = json.loads(eff.get("parameterText") or "{}")
    except Exception:
        return {}
    out = []
    for k, v in d.items():
        if k in _META_KEYS:
            continue
        s = str(v)
        if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
            out.append(s)
    return out


def annotate_effect_desc(eff):
    """Return the effect description with 「（NN%）」 inserted after each value-bearing verb."""
    desc = eff.get("description", "") or ""
    cut = desc.find("※")                       # notes after ※ are conditions, not effects to annotate
    main = desc if cut == -1 else desc[:cut]
    note = "" if cut == -1 else desc[cut:]

    vals = _value_params(eff)
    tokens = list(_VERB_RE.finditer(main))
    if not vals or not tokens:
        return desc

    distinct = list(dict.fromkeys(vals))       # source (insertion) order
    assign = {}
    if len(distinct) == 1:
        for i in range(len(tokens)):
            assign[i] = distinct[0]
    else:
        ranks = sorted({_mag_rank(t.group(1)) for t in tokens})
        ordered = [v for _, v in sorted({(float(v), v) for v in distinct})]  # by numeric value asc
        if len(ranks) == len(ordered):
            rank_to_val = {r: ordered[i] for i, r in enumerate(ranks)}
            for i, t in enumerate(tokens):
                assign[i] = rank_to_val[_mag_rank(t.group(1))]
        else:
            # magnitudes can't disambiguate -> match verbs left-to-right with source order
            for i in range(len(tokens)):
                assign[i] = distinct[min(i, len(distinct) - 1)]

    parts = []
    prev = 0
    for i, t in enumerate(tokens):
        parts.append(main[prev:t.end()])
        parts.append("（%s%%）" % assign[i])
        prev = t.end()
    parts.append(main[prev:])
    return "".join(parts) + note


# ---------------------------------------------------------------------------
# Data loading / entry building
# ---------------------------------------------------------------------------
def load_tactics():
    return load_mst("masterdata_api_mst_getTacticsMstList.json")


def build_entries():
    tactics = load_tactics()
    effects = {x["tacticsEffectMstId"]: x
               for x in load_mst("masterdata_api_mst_getTacticsEffectMstList.json")}
    lbb_list = load_mst("masterdata_api_mst_getLimitBreakBonusMstList.json")
    lbb = {x["limitBreakBonusMstId"]: x for x in lbb_list}

    entries = []
    for order, x in enumerate(tactics):
        lbb_entry = lbb.get(x["limitBreakBonusMstId"])
        stats = {t: x.get(STAT_FIELD[t], 0) + lbb_bonus(lbb_entry, t, False) for t in (1, 2, 3, 4)}
        quest = effects.get(x.get("questTacticsEffectMstId"))
        gvg = effects.get(x.get("gvgTacticsEffectMstId"))
        entries.append({
            "uniqueId": x["uniqueId"],
            "tacticsMstId": x["tacticsMstId"],
            "name": x["name"],
            "rarity": x["rarity"],
            "order": order,
            "pa": stats[1], "pd": stats[2], "ma": stats[3], "md": stats[4],
            "power": stats[1] + stats[2] + stats[3] + stats[4],
            "cat": _effect_category(gvg),
            "sp": (gvg.get("sp", 0) if gvg else 0),          # GVG MP cost (used for the MP filter)
            "mp": 1 if (gvg and gvg.get("sp", 0) > 0) else 0,  # 1 = consumes MP, 0 = free
            "quest": quest,
            "gvg": gvg,
        })
    return entries


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
RARITY_LABEL = config.int_label_map("rarity")


def render_skill_cell(eff, icon, col_class):
    """One skill cell: type icon + name + timing line (prep / effect / MP) + value-annotated desc."""
    if eff is None:
        return '<td class="skill-cell empty %s"></td>' % col_class
    name = fmt(eff.get("name", ""))
    meta = "準備%s秒・効果%s秒・消費MP%s" % (
        eff.get("preparationTime", 0), eff.get("effectTime", 0), eff.get("sp", 0))
    desc = fmt(annotate_effect_desc(eff))
    return (
        '<td class="skill-cell {col}">'
        '<div class="skill-name"><img class="skill-i" src="{icon}" alt="">{name}</div>'
        '<div class="skill-meta">{meta}</div>'
        '<div class="skill-desc">{desc}</div>'
        "</td>"
    ).format(col=col_class, icon=icon, name=name, meta=html.escape(meta), desc=desc)


def render_row(e):
    rarity = e["rarity"]
    icon_url = TACTICS_ICON_URL.format(uid=e["uniqueId"])
    marker = card_markers.marker_tactics()
    frame = card_markers.tactics_frame_rel(rarity)

    pa, ma, pd, md = e["pa"], e["ma"], e["pd"], e["md"]
    papd, mamd, pama, pdmd = pa + pd, ma + md, pa + ma, pd + md

    skill_cells = (
        render_skill_cell(e["quest"], "assets/Skill1.png", "col-quest")
        + render_skill_cell(e["gvg"], "assets/Skill2.png", "col-gvg")
    )

    return (
        '<tr class="card" data-power="{power}" data-rarity="{rarity}" data-cat="{cat}" '
        'data-mp="{mp}" data-sp="{sp}" data-order="{order}" '
        'data-pa="{pa}" data-ma="{ma}" data-pd="{pd}" data-md="{md}" '
        'data-papd="{papd}" data-mamd="{mamd}" data-pama="{pama}" data-pdmd="{pdmd}" '
        'data-name="{name_attr}">'
        '<td class="c-icon col-icon"><span class="cardimg">'
        '<img class="bg" src="assets/Blank.png" alt="">'
        '<img class="art" loading="lazy" src="{icon_url}" alt="" '
        'onerror="this.classList.add(\'broken\')">'
        '<img class="frame" src="{frame}" alt="">'
        '<img class="mark" src="{marker}" alt=""></span></td>'
        '<td class="c-name col-name">{name}</td>'
        '<td class="num col-pa">{pa}</td><td class="num col-ma">{ma}</td>'
        '<td class="num col-pd">{pd}</td><td class="num col-md">{md}</td>'
        '<td class="num power col-power">{power}</td>'
        '<td class="num col-papd">{papd}</td><td class="num col-mamd">{mamd}</td>'
        '<td class="num col-pama">{pama}</td><td class="num col-pdmd">{pdmd}</td>'
        '<td class="num col-mp">{sp}</td>'
        '{skill_cells}'
        '</tr>'
    ).format(
        power=e["power"], rarity=rarity, cat=e["cat"], mp=e["mp"], sp=e["sp"], order=e["order"],
        pa=pa, ma=ma, pd=pd, md=md, papd=papd, mamd=mamd, pama=pama, pdmd=pdmd,
        name_attr=html.escape(e["name"], quote=True),
        icon_url=icon_url, frame=frame, marker=marker,
        name=fmt(e["name"]),
        skill_cells=skill_cells,
    )


# Column defs: (key, header text, shown by default). Combined columns hidden by default.
_COL = [
    ("icon", True), ("name", True),
    ("pa", True), ("ma", True), ("pd", True), ("md", True),
    ("power", True),
    ("papd", False), ("mamd", False), ("pama", False), ("pdmd", False),
    ("mp", True),
    ("quest", True), ("gvg", True),
]
COL_DEFS = [(code, config.t("tactics_list.col." + code), dflt) for code, dflt in _COL]

# Per-column <thead> metadata: code -> (data-sort, data-get, is_num, icon_src).
# Note: name sorts/gets as "name" (not "attr"), and the MP column sorts on "sp".
_COL_META = {
    "icon": ("order", "num", False, None),
    "name": ("name", "name", False, None),
    "pa": ("pa", "num", True, None), "ma": ("ma", "num", True, None),
    "pd": ("pd", "num", True, None), "md": ("md", "num", True, None),
    "power": ("power", "num", True, None),
    "papd": ("papd", "num", True, None), "mamd": ("mamd", "num", True, None),
    "pama": ("pama", "num", True, None), "pdmd": ("pdmd", "num", True, None),
    "mp": ("sp", "num", True, None),
    "quest": ("quest", "cell", False, "assets/Skill1.png"),
    "gvg": ("gvg", "cell", False, "assets/Skill2.png"),
}


def build_thead():
    cells = []
    for code, label, _dflt in COL_DEFS:
        sort, get, is_num, icon = _COL_META[code]
        cls = "sortable" + (" num" if is_num else "") + " col-" + code
        img = '<img class="skh" src="%s" alt="">' % icon if icon else ""
        cells.append('  <th class="%s" data-sort="%s" data-get="%s">%s%s</th>'
                     % (cls, sort, get, img, html.escape(label)))
    return "\n".join(cells)


def render_html(entries):
    entries = sorted(entries, key=lambda e: e["order"], reverse=True)
    rows_html = "\n".join(render_row(e) for e in entries)

    col_checkboxes = "".join(
        '<label><input type="checkbox" data-col="{k}"{chk}> {l}</label>'.format(
            k=k, l=html.escape(lbl), chk=" checked" if d else "")
        for k, lbl, d in COL_DEFS
    )
    hidden = ",".join(".col-%s" % k for k, lbl, d in COL_DEFS if not d)
    col_init_style = (hidden + "{display:none}") if hidden else ""

    rarities = sorted({e["rarity"] for e in entries})

    dropdowns = {
        "__DD_RARITY__": build_dropdown("rarity", config.t("tactics_list.filter.rarity"), [(r, RARITY_LABEL.get(r, r)) for r in rarities]),
        "__DD_CAT__": build_dropdown("cat", config.t("tactics_list.filter.cat"), CAT_DEFS),
        "__DD_MP__": build_dropdown("mp", config.t("tactics_list.filter.mp"),
                                    [("1", config.t("tactics_list.mp_yes")), ("0", config.t("tactics_list.mp_no"))]),
    }

    chrome = {
        "__HTML_LANG__": config.html_lang(),
        "__T_TITLE__": config.t("tactics_list.title"),
        "__T_SEARCH__": config.t("tactics_list.search"),
        "__T_SEARCH_PH__": config.t("tactics_list.search_ph"),
        "__T_COLS__": config.t("tactics_list.cols"),
        "__T_CLEAR__": config.t("tactics_list.clear"),
        "__T_COUNT_SUFFIX__": config.t("tactics_list.count_suffix"),
    }

    out = HTML_TEMPLATE
    for token, frag in dropdowns.items():
        out = out.replace(token, frag)
    for token, val in chrome.items():
        out = out.replace(token, html.escape(val))
    out = out.replace("__THEAD__", build_thead())
    out = out.replace("__COL_CHECKBOXES__", col_checkboxes)
    out = out.replace("__COL_INIT_STYLE__", col_init_style)
    out = out.replace("__TOTAL__", str(len(entries)))
    out = out.replace("__ROWS__", rows_html)
    return config.relocate_asset_urls(out)


# The template uses __TOKEN__ placeholders + str.replace injection to avoid escaping CSS/JS braces.
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="__HTML_LANG__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__T_TITLE__</title>
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
  tr.card { content-visibility:auto; contain-intrinsic-height:auto 90px; }

  /* Tactics image stack: Blank backdrop + art + rarity frame + top-right tactics marker.
     The backdrop (Blank.png) sits underneath so pale / semi-transparent tactics icons stay legible. */
  .cardimg { position:relative; display:block; width:76px; height:76px; }
  .cardimg .bg { position:absolute; inset:0; width:100%; height:100%; object-fit:cover;
                 border-radius:6px; pointer-events:none; }
  .cardimg .art { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; display:block; border-radius:6px; }
  .cardimg .art.broken { visibility:hidden; }
  .cardimg .frame { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
  .cardimg .mark { position:absolute; top:-2px; right:-3px; max-height:38%;
                   height:auto; width:auto; pointer-events:none;
                   filter:drop-shadow(0 1px 1px rgba(0,0,0,.4)); }
  .c-icon { width:84px; }
  .c-name { font-weight:600; min-width:150px; max-width:240px; line-height:1.35; }
  .c-tag { white-space:nowrap; }
  .num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
  .num.power { font-weight:600; }

  .skill-cell { min-width:220px; max-width:340px; }
  .skill-name { font-weight:600; }
  .skill-name img.skill-i { width:15px; height:15px; object-fit:contain; vertical-align:-2px; margin-right:4px; }
  .skill-meta { color:#5b6b8c; font-size:11px; margin-top:2px; }
  .skill-desc { color:#333; line-height:1.4; margin-top:2px; }

  /* Global watermark: fixed, covers the whole viewport, top layer, very low opacity (uniqueId 20000216 full art) */
  .watermark { position:fixed; inset:0; z-index:9999; pointer-events:none; }
  .watermark img { width:100%; height:100%; object-fit:cover; opacity:.1; user-select:none; }

  /* ---------- Mobile / responsive ---------- */
  @media (max-width: 700px) {
    body { font-size:12px; }
    header { padding:6px 8px; gap:6px 8px; }
    header h1 { font-size:14px; flex-basis:100%; margin:0; }
    header label { margin-right:2px; }
    header input, header select, .ddbtn, #clear { font-size:12px; padding:4px 6px; }
    header input[type=text] { width:130px; }
    #count { flex-basis:100%; margin-left:0; }
    /* The wide table stays but becomes compact; the page scrolls sideways to reach far columns. */
    thead th { padding:5px 6px; font-size:12px; }
    tbody td { padding:4px 6px; }
    .cardimg { width:52px; height:52px; }
    .c-icon { width:58px; }
    .c-name { min-width:92px; max-width:150px; line-height:1.25; }
    .skill-cell { min-width:150px; max-width:220px; }
    .skill-desc { font-size:11px; }
    .skill-meta { font-size:10px; }
    .skill-name img.skill-i { width:13px; height:13px; }
  }
</style>
<style id="colstyle">__COL_INIT_STYLE__</style>
</head>
<body>
<header>
  <h1>__T_TITLE__</h1>
  <span><label>__T_SEARCH__</label><input id="q" type="text" placeholder="__T_SEARCH_PH__"></span>
  __DD_RARITY__
  __DD_CAT__
  __DD_MP__
  <span class="dd">
    <button class="ddbtn" type="button" data-dd="cols" data-label="__T_COLS__">__T_COLS__ ▾</button>
    <div class="ddpanel" data-ddp="cols">__COL_CHECKBOXES__</div>
  </span>
  <button id="clear" type="button">__T_CLEAR__</button>
  <span id="count"></span>
</header>
<table id="tbl">
<thead>
<tr>
__THEAD__
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

  // ---------- Filtering (each dropdown group = "row value in the selected set"; groups intersected) ----------
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
    var groups = {};
    var checked = document.querySelectorAll('input[data-f]:checked');
    for (var i = 0; i < checked.length; i++) {
      var g = checked[i].dataset.f;
      (groups[g] = groups[g] || []).push(checked[i].value);
    }
    var shown = 0;
    for (var r = 0; r < rows.length; r++) {
      var d = rows[r].dataset, ok = true;
      if (kw && d.name.toLowerCase().indexOf(kw) === -1) ok = false;
      if (ok) {
        for (var g in groups) {
          if (groups[g].indexOf(d[g]) === -1) { ok = false; break; }
        }
      }
      rows[r].classList.toggle('hidden', !ok);
      if (ok) shown++;
    }
    count.textContent = shown + ' / ' + TOTAL + ' __T_COUNT_SUFFIX__';
    updateBtns();
  }

  document.addEventListener('change', function(e) {
    if (e.target.matches && e.target.matches('input[data-col]')) applyCols();
    else if (e.target.matches && e.target.matches('input[data-f]')) applyFilter();
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
      if (sortGet === 'name') { va = a.dataset.name || ''; vb = b.dataset.name || ''; }
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
    entries = build_entries()
    html_text = render_html(entries)
    config.ensure_output_dir()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html_text)
    print("Generated %d tactics entries" % len(entries))
    print("Output file: %s" % OUT)


if __name__ == "__main__":
    main()
