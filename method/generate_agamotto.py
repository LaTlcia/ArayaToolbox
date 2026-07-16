# -*- coding: utf-8 -*-
"""
Agamotto — card-based action-timeline battle simulator generator
================================================================
Build a standalone HTML Monte-Carlo battle simulator (output/agamotto.html).

Unlike v1 (manual magnification entry), an action is now "player X uses card Y":
the full card pool is embedded (reusing generate_deck_builder.build_calc), and
the per-effect amounts are computed with the same region pipeline as the deck
builder's 牌効 calculator — except that every probabilistic region is *rolled*
per trial instead of using its expectation (the whole point of this tool):

  * UP passives (ダメージ/支援/回復UP): each deck card's passive procs
    independently at PBASE[plus] (+0.02 theme) x(1+rateUp)x(1-rateDown),
    adding coeff x 1.5 when it procs (binomial, not expectation).
  * コ:効果範囲+1: fires at most once per action, P = 1 - prod(1-pi); +1 target (cap 4).
  * Target count: uniform integer in the card's {min,max}; SD pins to max when
    the attribute-differs condition holds.
  * EH / SD: stateful — condition is "last card this player used has a
    different attribute"; tracked per player during the trial.
  * CT / 劣勢オーダー: per-side 劣勢 flag.
  * Damage: floor( max(1,(TotalAtk-2/3*TotalDef)*DamageMag*(1+0.05*min(ratio,10)))
    * Rand(0.9,1.0) + RandInt(1,200) ); Ba stack on the target -> x0.7, consume 1.
  * Buff/debuff points = floor(BuffMag x caster base stats) -> target buff% with
    decay bands (buff 50~75:x0.5, 75~100:x0.1 cap +100%; debuff -25~-50:x0.5,
    -50~-70:x0.1 floor -70%); elemental buffs never decay, clamped +-50%.
  * Heal = floor(HealMag x caster (DEF+Sp.DEF)/2 x Rand(0.9,1.0)); revives.
  * Marks Mt/An/Et/Ba: granted by cards (parsed stack counts; support/heal cards
    grant to their targets, others to self), consumed 1 per trigger.

The random-region 0.95 of the 牌効 rate is *excluded* from the deterministic
DamageMag/HealMag and rolled live instead, so the trial mean matches the deck
builder's expected rate without double-counting.

Per-player config: base stats + max HP, deck code (allb.game-db.tw URL, same
format the deck builder emits), CHARM%, ADX, theme, costume job. Per-side:
one active order (tactics) + 劣勢 flag.

i18n tokens: __AGT_*__ (agamotto.ui) plus reused __DBT_*__ (deck_builder.ui).
"""

import os as _os, sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import html
import json
import re

import config
from generate_card_list import build_lookups, build_entries
import generate_deck_builder as gdb

OUT = getattr(config, "AGAMOTTO_OUT", _os.path.join(config.OUTPUT_DIR, "agamotto.html"))

# ---------------------------------------------------------------------------
# Stack-grant parsing
# ---------------------------------------------------------------------------
# Grant clause forms observed in the masterdata (see the dev notes):
#   自身に「...スタック」を2回蓄積する
#   味方に「...スタック」を1回蓄積する                 (= the card's own targets)
#   味方2～3体に「...スタック」を2回蓄積する           (independent target range)
#   自身に「A」と「B」を3回蓄積する                    (two kinds in one clause)
# Kind order inside a clause is preserved — acquisition order matters for the
# 3-kind replacement rule in the battle engine.
RE_GRANT = re.compile(
    r"(自身|味方(?:(\d+)(?:[～〜](\d+))?体)?)に((?:「[^」]*スタック」(?:と)?)+)を(\d+)回蓄積")
RE_GRANT_NAME = re.compile(r"「([^」]*スタック)」")


def _stack_kind(name):
    """Stack name -> kind index (0 Mt / 1 An / 2 Et / 3 Ba); None if unknown."""
    if "被ダメージ" in name:
        return 3
    if "回復" in name:
        return 2
    if "支援/妨害" in name:
        return 1
    if "ダメージ" in name:
        return 0
    return None


def stack_grants(desc):
    """Parse a GVG skill description into grant records for the JS engine.

    Each record: [self(1/0), lo, hi, [[kind, layers], ...]]
      * self=1        -> granted to the actor (lo/hi unused)
      * lo=hi=0       -> granted to the card's own (ally) targets
      * lo/hi > 0     -> independently rolled ally target count
    """
    out = []
    for m in RE_GRANT.finditer(desc or ""):
        who, lo, hi, names, n = m.group(1), m.group(2), m.group(3), m.group(4), int(m.group(5))
        kinds = []
        for nm in RE_GRANT_NAME.findall(names):
            k = _stack_kind(nm)
            if k is not None:
                kinds.append([k, n])
        if not kinds:
            continue
        if who == "自身":
            out.append([1, 0, 0, kinds])
        elif lo is None:
            out.append([0, 0, 0, kinds])
        else:
            l, h = int(lo), int(hi) if hi else int(lo)
            out.append([0, l, h, kinds])
    return out


def build_card_db():
    """Compact per-card records for the embedded JS DB (one per uid.cardType face)."""
    cards, lbb, skill, legendary, ultimate, super_by_card = build_lookups()
    entries = build_entries(cards, lbb, skill, legendary, ultimate, super_by_card)
    units = gdb.build_units(entries)
    db = []
    for u in units:
        gdesc = (u["gvg"].get("desc", "") if u["gvg"] else "") or ""
        db.append({
            "k": "%s.%d" % (u["uid"], u["ct"]),
            "uid": u["uid"], "tw": u["tw"], "n": u["name"],
            "c": u["ct"], "a": u["attr"], "leg": 1 if u["leg"] else 0,
            "mg": stack_grants(gdesc),   # stack grants (self / targets / own range)
            "calc": u["calc"],
        })
    return db


def render_html():
    out = HTML_TEMPLATE
    out = out.replace("__HTML_LANG__", html.escape(config.html_lang()))
    out = out.replace("__T_TITLE__", html.escape(config.t("agamotto.title")))

    agt = config.section("agamotto.ui")
    for k in sorted(agt, key=len, reverse=True):
        out = out.replace("__AGT_%s__" % k, agt[k])
    dbt = config.section("deck_builder.ui")
    for k in sorted(dbt, key=len, reverse=True):
        out = out.replace("__DBT_%s__" % k, dbt[k])

    attr = config.int_label_map("attribute")
    ctype = config.int_label_map("card_type")
    stat = config.section("stat")
    out = out.replace("__JS_ATTR_ARR__", json.dumps([attr[i] for i in range(1, 6)], ensure_ascii=False))
    out = out.replace("__JS_TYPE_LABEL__", json.dumps({str(i): ctype[i] for i in range(1, 8)}, ensure_ascii=False))
    out = out.replace("__JS_STAT__", json.dumps({
        "pa": stat.get("pa", "ATK"), "ma": stat.get("ma", "Sp.ATK"),
        "pd": stat.get("pd", "DEF"), "md": stat.get("md", "Sp.DEF"),
        "aS": dbt.get("atk_suffix", "攻"), "dS": dbt.get("def_suffix", "防"),
    }, ensure_ascii=False))
    out = out.replace("__JS_CARDS__",
                      json.dumps(build_card_db(), separators=(",", ":"), ensure_ascii=False))
    out = out.replace("__JS_TACTICS__",
                      json.dumps(gdb.build_tactics_options(), separators=(",", ":"), ensure_ascii=False))
    return config.relocate_asset_urls(out)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="__HTML_LANG__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__T_TITLE__</title>
<style>
  :root { --head-bg:#5b6b8c; --line:#9aa3b8; --txt:#111; }
  * { box-sizing:border-box; }
  body { margin:0; background:#fff; color:var(--txt);
         font-family:"Segoe UI","Microsoft YaHei","Hiragino Sans","Meiryo",sans-serif; font-size:13px; }
  header { position:sticky; top:0; z-index:50; background:#dde2ea; border-bottom:1px solid #9aa3b8;
           padding:8px 14px; display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
  header h1 { font-size:16px; margin:0 8px 0 0; color:#222; }
  input, select, button.btn { background:#fff; color:#111; border:1px solid #9aa3b8;
           border-radius:6px; padding:5px 8px; font-size:13px; }
  button.btn { cursor:pointer; }
  button.btn.primary { background:#5b6b8c; color:#fff; border-color:#5b6b8c; font-weight:600; }
  button.btn.primary:hover { background:#6b7da0; }
  #cfg { width:240px; font-family:monospace; font-size:11px; }
  .wrap { max-width:1280px; margin:0 auto; padding:12px 14px 60px; }
  section.panelbox { border:1px solid #9aa3b8; border-radius:8px; background:#f7f8fb;
                     padding:10px 12px; margin-bottom:14px; }
  section.panelbox h2 { font-size:15px; margin:0 0 8px; color:#333; border-bottom:1px solid #c5ccda;
                        padding-bottom:4px; display:flex; align-items:center; gap:10px; }
  section.panelbox h2 .sp { flex:1; }
  .note { color:#777; font-size:12px; margin:6px 0 0; }
  details.help { margin-bottom:14px; border:1px solid #c5ccda; border-radius:8px; background:#fdfdf4; padding:6px 12px; }
  details.help summary { cursor:pointer; font-weight:600; color:#5b6b8c; }
  details.help ul { margin:8px 0 4px; padding-left:18px; }
  details.help li { margin-bottom:5px; line-height:1.5; color:#444; }

  /* players */
  .sides { display:flex; gap:14px; flex-wrap:wrap; }
  .sidebox { flex:1 1 460px; min-width:0; }
  .sidebox h3 { font-size:14px; margin:2px 0 6px; padding:3px 8px; border-radius:6px; color:#fff; }
  .sidebox h3.hA { background:#3d6da8; }
  .sidebox h3.hE { background:#a84a3d; }
  .sidebox h4 { font-size:12px; margin:8px 0 3px; color:#555; }
  table.ptbl { border-collapse:collapse; width:100%; }
  table.ptbl th, table.ptbl td { border:1px solid #c5ccda; padding:2px 4px; text-align:center; }
  table.ptbl th { background:#eef1f6; font-weight:600; font-size:12px; }
  table.ptbl input { width:100%; min-width:56px; border:0; padding:3px 4px; text-align:right;
                     font-variant-numeric:tabular-nums; border-radius:0; }
  table.ptbl tr.dim input { background:#f4f4f4; }
  table.ptbl td.dk { font-size:11px; color:#3a7; font-weight:600; white-space:nowrap; }

  /* per-player detail config */
  .pcfg { display:flex; flex-direction:column; gap:7px; }
  .pcfg-row { display:flex; align-items:center; gap:6px 12px; flex-wrap:wrap; }
  .pcfg-row > b { flex:0 0 96px; color:#555; font-size:12px; }
  .pcfg-row label { display:inline-flex; align-items:center; gap:3px; color:#333; font-size:12px; }
  .pcfg-row input[type=number] { width:56px; padding:2px 4px; }
  .pcfg-row #deckCode { flex:1 1 260px; min-width:180px; font-family:monospace; font-size:11px; }
  .deckstat { font-size:12px; color:#3a7; font-weight:600; }
  .deckstat.none { color:#b66; }

  /* side settings: orders */
  .ordwrap { display:flex; gap:14px; flex-wrap:wrap; }
  .ordbox { flex:1 1 460px; min-width:0; border:1px solid #c5ccda; border-radius:8px; background:#fff; padding:8px 10px; }
  .ordbox h4 { font-size:13px; margin:0 0 6px; }
  .ordbox h4.hA { color:#3d6da8; } .ordbox h4.hE { color:#a84a3d; }
  .ord-g { margin-bottom:6px; }
  .ord-g > span { display:block; font-size:11px; color:#5b6b8c; font-weight:600; margin-bottom:2px; }
  .taclist { display:flex; flex-wrap:wrap; gap:6px; max-height:130px; overflow:auto;
             border:1px solid #d5dae6; border-radius:6px; padding:4px; background:#fafbfd; }
  .taclist .muted { color:#aaa; font-size:11px; }
  .tac-ic { width:52px; height:52px; padding:0; border:0; background:transparent; cursor:pointer;
            position:relative; opacity:.45; transition:opacity .15s; }
  .tac-ic:hover { opacity:.8; }
  .tac-ic.on { opacity:1; }
  .tac-ic.on::after { content:''; position:absolute; inset:-2px; border:2px solid #e8902a;
                      border-radius:9px; pointer-events:none; }
  .tcimg { position:relative; display:block; width:52px; height:52px; }
  .tcimg .bg, .tcimg .art { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; border-radius:6px; }
  .tcimg .frame { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }

  /* action form */
  .af { display:flex; flex-direction:column; gap:8px; }
  .af-row { display:flex; align-items:flex-start; gap:6px 14px; flex-wrap:wrap; }
  .af-row > b { flex:0 0 96px; color:#555; font-size:12px; padding-top:5px; }
  .af-row label { display:inline-flex; align-items:center; gap:4px; color:#333; }
  #cardSel { min-width:340px; max-width:100%; }
  #cardQ { width:200px; }
  .pv { border:1px dashed #c5ccda; border-radius:6px; background:#fff; padding:6px 8px;
        font-size:12px; line-height:1.6; min-height:20px; flex:1 1 300px; }
  .pv .pvname { font-weight:700; }
  .pv .pvchip { display:inline-block; font-size:11px; margin:1px 3px 1px 0; padding:0 4px; border-radius:3px; }
  .hideme { display:none !important; }

  /* timeline */
  .tl { display:flex; flex-wrap:wrap; gap:8px; min-height:46px; align-items:center; }
  .empty-hint { color:#999; }
  .act { display:inline-flex; align-items:center; gap:5px; border:1px solid #b7bdcc; border-radius:8px;
         background:#fff; padding:4px 7px; cursor:grab; user-select:none; position:relative; }
  .act:active { cursor:grabbing; }
  .act.dragover { outline:2px solid #5b6b8c; outline-offset:-2px; }
  .act.editing { outline:2px solid #e8902a; }
  .act .no { background:#5b6b8c; color:#fff; border-radius:50%; min-width:20px; height:20px;
             display:inline-flex; align-items:center; justify-content:center; font-size:11px; font-weight:700; }
  .act .who { font-weight:700; }
  .act .who.sA { color:#3d6da8; }
  .act .who.sE { color:#a84a3d; }
  .act img.mi { width:26px; height:26px; border-radius:4px; object-fit:cover; }
  .act .dk { width:10px; height:10px; border-radius:3px; display:inline-block; }
  .act .ab { color:#999; cursor:pointer; font-weight:700; padding:0 2px; }
  .act .ab:hover { color:#333; }
  .k-dmg { background:#e46a6a; }
  .k-heal { background:#54b06e; }
  .k-buff { background:#5f8fd0; }
  .k-debuff { background:#9a5fc0; }
  .k-mark { background:#e8b23a; }
  .pv .k-dmg, .pv .k-debuff, .pv .k-heal, .pv .k-buff { color:#fff; }
  .legend { display:flex; gap:12px; flex-wrap:wrap; color:#666; font-size:11px; margin-top:6px; }
  .legend i { width:10px; height:10px; border-radius:3px; display:inline-block; vertical-align:-1px; margin-right:3px; }
  .bk { font-weight:600; font-size:11px; padding:1px 6px; border-radius:4px; color:#fff; }

  /* results */
  .simctl { display:flex; align-items:center; gap:14px; flex-wrap:wrap; margin-bottom:10px; }
  .tiles { display:flex; gap:12px; flex-wrap:wrap; margin:8px 0 14px; }
  .tile { flex:1 1 180px; border:1px solid #c5ccda; border-radius:8px; background:#fff; padding:10px 12px; }
  .tile .tv { font-size:22px; font-weight:700; font-variant-numeric:tabular-nums; }
  .tile .tk { color:#666; font-size:12px; margin-bottom:2px; }
  .tile.tw .tv { color:#1f7a3a; }
  .tile.tl2 .tv { color:#a01f1f; }
  .tile.td .tv { color:#26508a; }
  .tile .tc { color:#999; font-size:11px; }
  .charts { display:flex; gap:14px; flex-wrap:wrap; }
  .chartbox { flex:1 1 480px; min-width:0; border:1px solid #c5ccda; border-radius:8px; background:#fff; padding:8px 10px; }
  .chartbox h3 { font-size:13px; margin:0 0 6px; color:#333; }
  .chartbox canvas { width:100%; height:auto; display:block; }
  .cinfo { color:#666; font-size:12px; margin-top:4px; min-height:16px; font-variant-numeric:tabular-nums; }
  #prog { color:#555; font-variant-numeric:tabular-nums; }

  .slot-tip { position:fixed; z-index:10001; display:none; max-width:380px; padding:8px 10px;
              background:rgba(25,30,42,.96); color:#fff; border:1px solid #8f9bb3; border-radius:7px;
              box-shadow:0 4px 16px rgba(0,0,0,.32); font-size:12px; line-height:1.5; pointer-events:none; }
  .slot-tip.open { display:block; }
  .slot-tip .st-name { font-weight:700; font-size:13px; margin-bottom:3px; }

  .watermark { position:fixed; inset:0; z-index:9999; pointer-events:none; }
  .watermark img { width:100%; height:100%; object-fit:cover; opacity:.1; user-select:none; }

  @media (max-width:820px){
    body { font-size:12px; }
    .af-row > b, .pcfg-row > b { flex-basis:100%; padding-top:0; }
    #cfg { width:150px; }
    #cardSel { min-width:0; width:100%; }
  }
</style>
</head>
<body>
<header>
  <h1>__T_TITLE__</h1>
  <input id="cfg" type="text" placeholder="__AGT_cfg_ph__" spellcheck="false">
  <button class="btn" id="cfgLoad" type="button">__DBT_load__</button>
  <button class="btn" id="cfgCopy" type="button">__DBT_copy__</button>
</header>

<div class="wrap">

  <details class="help">
    <summary>__AGT_help_title__</summary>
    <ul>
      <li>__AGT_help_flow__</li>
      <li>__AGT_help_dmg__</li>
      <li>__AGT_help_buff__</li>
      <li>__AGT_help_heal__</li>
      <li>__AGT_help_up__</li>
      <li>__AGT_help_eh__</li>
      <li>__AGT_help_marks__</li>
      <li>__AGT_help_death__</li>
      <li>__AGT_help_target__</li>
    </ul>
  </details>

  <section class="panelbox">
    <h2>__AGT_players_title__</h2>
    <div class="sides">
      <div class="sidebox"><h3 class="hA">__AGT_side_ally__</h3><div id="sideA"></div></div>
      <div class="sidebox"><h3 class="hE">__AGT_side_enemy__</h3><div id="sideE"></div></div>
    </div>
    <div class="note">__AGT_back_note__</div>
  </section>

  <section class="panelbox">
    <h2>__AGT_pcfg_title__</h2>
    <div class="pcfg">
      <div class="pcfg-row"><b>__AGT_pcfg_pick__</b>
        <select id="pcPick"></select>
        <span class="deckstat" id="deckStat"></span>
      </div>
      <div class="pcfg-row"><b>__AGT_deck_code__</b>
        <input id="deckCode" type="text" placeholder="__DBT_code_ph__" spellcheck="false">
        <button class="btn" id="deckLoad" type="button">__DBT_load__</button>
      </div>
      <div class="pcfg-row"><b>CHARM%</b><div id="pcCharm"></div></div>
      <div class="pcfg-row"><b>ADX</b><div id="pcAdx"></div></div>
      <div class="pcfg-row"><b>__DBT_theme__</b><div id="pcTheme"></div></div>
      <div class="pcfg-row"><b>__DBT_specialty__</b>
        <select id="pcCost"></select>
      </div>
      <div class="pcfg-row"><b>__AGT_init_buff_lbl__</b><div id="pcIb"></div></div>
      <div class="pcfg-row"><b>__AGT_init_ea_lbl__</b><div id="pcIea"></div></div>
      <div class="pcfg-row"><b>__AGT_init_ed_lbl__</b><div id="pcIed"></div></div>
      <div class="pcfg-row"><b>__AGT_init_stack__</b><div id="pcStk"></div></div>
      <div class="note">__AGT_pcfg_note__</div>
      <div class="note">__AGT_stack_note__</div>
    </div>
  </section>

  <section class="panelbox">
    <h2>__AGT_orders_title__</h2>
    <div class="ordwrap">
      <div class="ordbox" data-side="A"><h4 class="hA">__AGT_side_ally__</h4>
        <label style="font-size:12px"><input type="checkbox" id="disA"> __AGT_disadv_lbl__</label>
        <div id="ordA"></div>
      </div>
      <div class="ordbox" data-side="E"><h4 class="hE">__AGT_side_enemy__</h4>
        <label style="font-size:12px"><input type="checkbox" id="disE"> __AGT_disadv_lbl__</label>
        <div id="ordE"></div>
      </div>
    </div>
    <div class="note">__AGT_orders_note__</div>
  </section>

  <section class="panelbox">
    <h2>__AGT_action_title__</h2>
    <div class="af">
      <div class="af-row"><b>__AGT_actor_lbl__</b>
        <select id="aActor"></select>
        <label class="chk" style="font-size:12px"><input type="checkbox" id="poolAll"> __AGT_pool_all__</label>
      </div>
      <div class="af-row"><b>__AGT_card_lbl__</b>
        <div style="flex:1 1 auto;min-width:280px">
          <input id="cardQ" type="text" placeholder="__DBT_search_ph__"><br>
          <select id="cardSel" size="8" style="margin-top:4px;width:100%"></select>
        </div>
        <div class="pv" id="cardPv">__AGT_pv_none__</div>
      </div>
      <div class="af-row">
        <button class="btn primary" id="actAdd" type="button">__AGT_add_btn__</button>
        <button class="btn hideme" id="actCancel" type="button">__AGT_cancel_btn__</button>
      </div>
    </div>
  </section>

  <section class="panelbox">
    <h2>__AGT_tl_title__ <span id="tlCount"></span><span class="sp"></span>
      <button class="btn" id="tlClear" type="button">__AGT_tl_clear__</button></h2>
    <div class="note" style="margin:0 0 8px">__AGT_tl_hint__</div>
    <div id="tl" class="tl"></div>
    <div class="legend">
      <span><i class="k-dmg"></i>__DBT_damage__</span>
      <span><i class="k-heal"></i>__AGT_heal_lbl__</span>
      <span><i class="k-buff"></i>__AGT_buff_lbl__</span>
      <span><i class="k-debuff"></i>__AGT_debuff_lbl__</span>
      <span><i class="k-mark"></i>__AGT_marks_title__</span>
    </div>
  </section>

  <section class="panelbox">
    <h2>__AGT_sim_title__</h2>
    <div class="simctl">
      <label>__AGT_trials_lbl__ <input id="trials" type="number" value="10000" min="100" max="1000000" step="100"></label>
      <label><input type="checkbox" id="autoRev"> __AGT_auto_revive__</label>
      <button class="btn primary" id="run" type="button">__AGT_run_btn__</button>
      <span id="prog"></span>
    </div>
    <div class="tiles" id="tiles"></div>
    <div class="charts">
      <div class="chartbox"><h3>__AGT_chart_enemy__</h3><canvas id="cE" width="720" height="280"></canvas><div class="cinfo" id="iE"></div></div>
      <div class="chartbox"><h3>__AGT_chart_ally__</h3><canvas id="cA" width="720" height="280"></canvas><div class="cinfo" id="iA"></div></div>
    </div>
  </section>

</div>
<div class="slot-tip" id="tip"></div>
<div class="watermark"><img src="assets/remote/Image/Card/Card020000216.jpg" alt=""></div>

<script>
  var ATTR=__JS_ATTR_ARR__;
  var TYPE_LABEL=__JS_TYPE_LABEL__;
  var STAT=__JS_STAT__;
  var CARDS=__JS_CARDS__;
  var TAC=__JS_TACTICS__;
  var LBL={sideA:'__AGT_side_ally__', sideE:'__AGT_side_enemy__', rowF:'__AGT_row_front__', rowB:'__AGT_row_back__'};
  var PBASE=[0.15,0.225,0.30];
  var ADX_LABEL=['0.95','1','1.05','1.05×0.95'];

  function esc(s){ return (s+'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function clamp(v,a,b){ return v<a?a:(v>b?b:v); }
  function num(id){ var v=parseFloat(document.getElementById(id).value); return isNaN(v)?0:v; }
  function iconUrl(uid){ return 'assets/remote/Image/CardIcon/S/CardIconS0'+uid+'.png'; }

  // ---------- card index ----------
  var byKey={}, twIndex={};
  CARDS.forEach(function(c){ byKey[c.k]=c; (twIndex[c.tw]=twIndex[c.tw]||[]).push(c.k); });

  // ---------- player keys ----------
  var PKEYS=[];
  ['A','E'].forEach(function(s){
    for(var i=1;i<=4;i++) PKEYS.push(s+'F'+i);
    for(var j=1;j<=5;j++) PKEYS.push(s+'B'+j);
  });
  function sideLbl(s){ return s==='A'?LBL.sideA:LBL.sideE; }
  function rowLbl(r){ return r==='F'?LBL.rowF:LBL.rowB; }
  function actorLbl(key){ return sideLbl(key.charAt(0))+' '+rowLbl(key.charAt(1))+key.slice(2); }
  function actorShort(key){ return sideLbl(key.charAt(0)).charAt(0)+key.charAt(1)+key.slice(2); }

  // per-player config store
  // ib = initial main-stat buff% [pa,ma,pd,md] (-70..100); iea/ied = initial
  // elemental buff% (1-indexed, -50..50); sk/sn = ordered initial stacks
  // (3 slots, kind -1 = empty, kinds 0 Mt/1 An/2 Et/3 Ba, layers 1-3).
  function newPC(){
    return {code:'', deck:[], charm:[0,0,0,0,0,0], adx:[0,1,1,1,1,1],
            theme:[false,false,false,false,false,false], cost:0,
            ib:[0,0,0,0], iea:[0,0,0,0,0,0], ied:[0,0,0,0,0,0],
            sk:[-1,-1,-1], sn:[1,1,1]};
  }
  var STK_NAME=['Mt','An','Et','Ba'];
  var STK_LABEL=['__DBT_stack_mt__','__DBT_stack_an__','__DBT_stack_et__','__DBT_stack_ba__'];
  var PC={};
  PKEYS.forEach(function(k){ PC[k]=newPC(); });

  // ---------- players stats tables ----------
  (function(){
    ['A','E'].forEach(function(s){
      var h='';
      [['F',4],['B',5]].forEach(function(rw){
        h+='<h4>'+rowLbl(rw[0])+'</h4><table class="ptbl"><thead><tr><th>__AGT_pos_lbl__</th>'
          +'<th>'+esc(STAT.pa)+'</th><th>'+esc(STAT.ma)+'</th><th>'+esc(STAT.pd)+'</th><th>'+esc(STAT.md)+'</th>'
          +'<th>__AGT_hp_max__</th><th>__AGT_deck_col__</th></tr></thead><tbody>';
        for(var i=1;i<=rw[1];i++){
          var key=s+rw[0]+i;
          h+='<tr'+(rw[0]==='B'?' class="dim"':'')+'><td>'+i+'</td>';
          ['pa','ma','pd','md','hp'].forEach(function(f){
            h+='<td><input id="p_'+key+'_'+f+'" type="number" value="0" min="0" step="1"></td>';
          });
          h+='<td class="dk" id="dk_'+key+'">—</td></tr>';
        }
        h+='</tbody></table>';
      });
      document.getElementById('side'+s).innerHTML=h;
    });
  })();
  function readPlayers(){
    var P={};
    PKEYS.forEach(function(k){
      P[k]={pa:num('p_'+k+'_pa'), ma:num('p_'+k+'_ma'), pd:num('p_'+k+'_pd'),
            md:num('p_'+k+'_md'), hp:num('p_'+k+'_hp')};
    });
    return P;
  }
  function refreshDeckCol(){
    PKEYS.forEach(function(k){
      var el=document.getElementById('dk_'+k);
      el.textContent=PC[k].deck.length? (PC[k].deck.length+'__AGT_deck_loaded__') : '—';
    });
  }

  // ---------- deck code decode (same format the deck builder emits) ----------
  var B62='0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ';
  function dec62(str){ var n=0; for(var i=0;i<str.length;i++){ var k=B62.indexOf(str.charAt(i));
    if(k<0) return NaN; n=n*62+k; } return n; }
  function decodeDeck(str){
    str=(str||'').trim(); if(!str) return null;
    var target=null, m=str.match(/[?&]v=([^&\\s]+)/);
    try{
      if(m) target=atob(m[1].replace(/ /g,'+'));
      else if(str.indexOf('|')!==-1) target=str;
      else target=atob(str.replace(/ /g,'+'));
    }catch(e){ return null; }
    var parts=target.split('|');
    if(parts.length<4) return null;
    var base=dec62(parts[0])+61; if(isNaN(base)) return null;
    var role=(parts[3].trim()==='0')?'F':'B';
    var out=[];
    [parts[1],parts[2]].forEach(function(g){
      if(!g) return;
      g.split(',').forEach(function(t){
        if(!t) return;
        var rel=dec62(t.slice(0,-1)); if(isNaN(rel)) return;
        var list=twIndex[rel-61+base]||[], pick=null;
        for(var i=0;i<list.length;i++){
          var isF=byKey[list[i]].c<=4;
          if((role==='F')===isF){ pick=list[i]; break; }
        }
        if(!pick && list.length) pick=list[0];
        if(pick) out.push(pick);
      });
    });
    return out.length?out:null;
  }

  // ---------- per-player detail editor ----------
  var pcCur='AF1';
  (function(){
    var h='';
    PKEYS.forEach(function(k){ h+='<option value="'+k+'">'+esc(actorLbl(k))+'</option>'; });
    document.getElementById('pcPick').innerHTML=h;
    var c='', x='', t='';
    for(var a=1;a<=5;a++){
      c+='<label>'+esc(ATTR[a-1])+'<input type="number" id="pcCharm'+a+'" value="0" step="1"></label> ';
      x+='<label>'+esc(ATTR[a-1])+'<select id="pcAdx'+a+'">';
      for(var i2=0;i2<4;i2++) x+='<option value="'+i2+'"'+(i2===1?' selected':'')+'>'+ADX_LABEL[i2]+'</option>';
      x+='</select></label> ';
      t+='<label>'+esc(ATTR[a-1])+'<input type="checkbox" id="pcTheme'+a+'"></label> ';
    }
    document.getElementById('pcCharm').innerHTML=c;
    document.getElementById('pcAdx').innerHTML=x;
    document.getElementById('pcTheme').innerHTML=t;
    var j='<option value="0">__DBT_none__</option>';
    for(var ct=1;ct<=7;ct++) j+='<option value="'+ct+'">'+esc(TYPE_LABEL[ct])+'</option>';
    document.getElementById('pcCost').innerHTML=j;
    // initial buffs: 4 main stats (-70..100) + elemental atk/def (-50..50)
    var ib='';
    [['pa',STAT.pa],['ma',STAT.ma],['pd',STAT.pd],['md',STAT.md]].forEach(function(f,i){
      ib+='<label>'+esc(f[1])+'<input type="number" id="pcIb'+i+'" value="0" step="1" min="-70" max="100"></label> ';
    });
    document.getElementById('pcIb').innerHTML=ib;
    var ea='', ed='';
    for(var a2=1;a2<=5;a2++){
      ea+='<label>'+esc(ATTR[a2-1])+'<input type="number" id="pcIea'+a2+'" value="0" step="1" min="-50" max="50"></label> ';
      ed+='<label>'+esc(ATTR[a2-1])+'<input type="number" id="pcIed'+a2+'" value="0" step="1" min="-50" max="50"></label> ';
    }
    document.getElementById('pcIea').innerHTML=ea;
    document.getElementById('pcIed').innerHTML=ed;
    // initial stacks: 3 ordered slots (slot 1 = earliest acquired)
    var sk='';
    for(var s2=0;s2<3;s2++){
      sk+='<label>'+(s2+1)+'. <select id="pcSk'+s2+'"><option value="-1">__DBT_none__</option>';
      for(var k2=0;k2<4;k2++) sk+='<option value="'+k2+'">'+esc(STK_LABEL[k2])+'</option>';
      sk+='</select><select id="pcSn'+s2+'">';
      for(var n2=1;n2<=3;n2++) sk+='<option value="'+n2+'">'+n2+'</option>';
      sk+='</select></label> ';
    }
    document.getElementById('pcStk').innerHTML=sk;
  })();
  function pcShow(key){
    pcCur=key;
    var c=PC[key];
    document.getElementById('pcPick').value=key;
    document.getElementById('deckCode').value=c.code;
    for(var a=1;a<=5;a++){
      document.getElementById('pcCharm'+a).value=c.charm[a];
      document.getElementById('pcAdx'+a).value=c.adx[a];
      document.getElementById('pcTheme'+a).checked=c.theme[a];
    }
    document.getElementById('pcCost').value=c.cost;
    for(var i=0;i<4;i++) document.getElementById('pcIb'+i).value=c.ib[i];
    for(var a2=1;a2<=5;a2++){
      document.getElementById('pcIea'+a2).value=c.iea[a2];
      document.getElementById('pcIed'+a2).value=c.ied[a2];
    }
    for(var s2=0;s2<3;s2++){
      document.getElementById('pcSk'+s2).value=c.sk[s2];
      document.getElementById('pcSn'+s2).value=c.sn[s2];
    }
    var st=document.getElementById('deckStat');
    if(c.deck.length){ st.textContent=c.deck.length+'__AGT_deck_loaded__'; st.className='deckstat'; }
    else { st.textContent='__AGT_deck_none__'; st.className='deckstat none'; }
  }
  function pcRead(){
    var c=PC[pcCur];
    for(var a=1;a<=5;a++){
      c.charm[a]=num('pcCharm'+a);
      c.adx[a]=+document.getElementById('pcAdx'+a).value;
      c.theme[a]=document.getElementById('pcTheme'+a).checked;
    }
    c.cost=+document.getElementById('pcCost').value;
    for(var i=0;i<4;i++) c.ib[i]=clamp(num('pcIb'+i),-70,100);
    for(var a2=1;a2<=5;a2++){
      c.iea[a2]=clamp(num('pcIea'+a2),-50,50);
      c.ied[a2]=clamp(num('pcIed'+a2),-50,50);
    }
    for(var s2=0;s2<3;s2++){
      c.sk[s2]=+document.getElementById('pcSk'+s2).value;
      c.sn[s2]=clamp(Math.floor(+document.getElementById('pcSn'+s2).value)||1,1,3);
    }
  }
  document.getElementById('pcPick').addEventListener('change', function(){ pcShow(this.value); rebuildCardSel(); });
  document.getElementById('deckLoad').addEventListener('click', function(){
    var s=document.getElementById('deckCode').value;
    var d=decodeDeck(s);
    if(s.trim() && !d){ alert('__AGT_deck_bad__'); return; }
    PC[pcCur].code=s.trim(); PC[pcCur].deck=d||[];
    pcShow(pcCur); refreshDeckCol(); rebuildCardSel(); saveCfg();
  });
  // any change inside the detail editor commits to the current player's config
  // (#pcPick has its own handler that switches players first; #deckCode commits via 読込)
  document.querySelector('.pcfg').addEventListener('change', function(e){
    if(e.target.id==='pcPick'||e.target.id==='deckCode') return;
    pcRead(); saveCfg();
  });

  // ---------- side orders ----------
  function tacIconUrl(uid){ return 'assets/remote/Image/TacticsIcon/S/TacticsIconS'+('00'+uid).slice(-3)+'.png'; }
  function tacFrame(r){ return 'assets/Sprite/IconRarity0'+(r===4?4:(r===5?5:6))+'LImage.png'; }
  var ORD_GROUPS=[
    ['my_attr','__DBT_attr_lbl__'], ['my_rate','__DBT_rate_up__'], ['my_eff','__DBT_eff__'],
    ['en_shield','__DBT_shield__'], ['en_rate','__DBT_rate_down__'], ['en_eff','__AGT_eff_down__']];
  function buildOrdBox(side){
    var h='';
    ORD_GROUPS.forEach(function(g){
      var arr=TAC[g[0]]||[];
      h+='<div class="ord-g"><span>'+g[1]+'</span><div class="taclist" data-side="'+side+'" data-g="'+g[0]+'">';
      if(!arr.length) h+='<span class="muted">__DBT_none_dash__</span>';
      arr.forEach(function(o,i){
        h+='<button type="button" class="tac-ic" data-side="'+side+'" data-g="'+g[0]+'" data-i="'+i+'" title="'+esc(o.name)+'">'
          +'<span class="tcimg">'
          +'<img class="bg" src="assets/Blank.png" alt="">'
          +'<img class="art" src="'+tacIconUrl(o.uid)+'" alt="" loading="lazy">'
          +'<img class="frame" src="'+tacFrame(o.rar)+'" alt="">'
          +'</span></button>';
      });
      h+='</div></div>';
    });
    return h;
  }
  document.getElementById('ordA').innerHTML=buildOrdBox('A');
  document.getElementById('ordE').innerHTML=buildOrdBox('E');
  var sideOrd={A:null, E:null};   // {g, i} — at most one active order per side
  ['ordA','ordE'].forEach(function(id){
    document.getElementById(id).addEventListener('click', function(e){
      var b=e.target.closest('.tac-ic'); if(!b) return;
      var side=b.dataset.side, wasOn=b.classList.contains('on');
      var ons=this.querySelectorAll('.tac-ic.on');
      for(var i=0;i<ons.length;i++) ons[i].classList.remove('on');
      if(!wasOn){ b.classList.add('on'); sideOrd[side]={g:b.dataset.g, i:+b.dataset.i}; }
      else sideOrd[side]=null;
      saveCfg();
    });
  });
  function ordInfo(side){
    var o=sideOrd[side]; if(!o) return null;
    var arr=TAC[o.g]||[]; return arr[o.i]?arr[o.i].info:null;
  }
  function sideDis(side){ return document.getElementById(side==='A'?'disA':'disE').checked; }

  // ---------- action form ----------
  (function(){
    var h='';
    PKEYS.forEach(function(k){ h+='<option value="'+k+'">'+esc(actorLbl(k))+'</option>'; });
    document.getElementById('aActor').innerHTML=h;
  })();
  function cardOptLabel(c){
    return ATTR[c.a-1]+' | '+c.n+' | '+TYPE_LABEL[c.c];
  }
  function rebuildCardSel(){
    var actor=document.getElementById('aActor').value;
    var all=document.getElementById('poolAll').checked;
    var q=document.getElementById('cardQ').value.trim().toLowerCase();
    var pool;
    if(!all && PC[actor].deck.length) pool=PC[actor].deck.map(function(k){ return byKey[k]; });
    else pool=CARDS;
    var sel=document.getElementById('cardSel'), h='', shown=0;
    for(var i=0;i<pool.length;i++){
      var c=pool[i];
      if(q && c.n.toLowerCase().indexOf(q)===-1) continue;
      h+='<option value="'+c.k+'">'+esc(cardOptLabel(c))+'</option>';
      if(++shown>=80 && (all||!PC[actor].deck.length)) break;
    }
    sel.innerHTML=h||('<option disabled>__DBT_none_dash__</option>');
    renderPreview();
  }
  document.getElementById('aActor').addEventListener('change', rebuildCardSel);
  document.getElementById('poolAll').addEventListener('change', rebuildCardSel);
  document.getElementById('cardQ').addEventListener('input', rebuildCardSel);
  document.getElementById('cardSel').addEventListener('change', renderPreview);

  function fmtNum(v){ return (Math.round(v*1e4)/1e4).toString(); }
  function cardMeta(c){
    var cc=c.calc, bits=[];
    var tn=cc.tn||[1,1];
    bits.push('__DBT_target_lbl__ '+(tn[0]===tn[1]?tn[0]:tn[0]+'~'+tn[1]));
    if(cc.sd) bits.push('SD');
    if(cc.eh>0) bits.push('EH×'+fmtNum(cc.eh));
    if(cc.ct>0) bits.push('CT×'+fmtNum(cc.ct));
    if(cc.ko!=null) bits.push('__AGT_ko_flag__');
    (c.mg||[]).forEach(function(g){
      var kinds=g[3].map(function(x){ return STK_NAME[x[0]]+'+'+x[1]; }).join(' ');
      var who=g[0]?'__AGT_mk_self__':(g[1]>0?('__AGT_mk_allies__'+(g[1]===g[2]?g[1]:g[1]+'~'+g[2])):'__AGT_mk_targets__');
      bits.push(who+': '+kinds);
    });
    return bits.join(' / ');
  }
  function renderPreview(){
    var k=document.getElementById('cardSel').value, box=document.getElementById('cardPv');
    var c=byKey[k];
    if(!c){ box.innerHTML='__AGT_pv_none__'; return; }
    var h='<span class="pvname">'+esc(c.n)+'</span><br>';
    c.calc.e.forEach(function(e){
      h+='<span class="pvchip k-'+e.k+'">'+esc(e.l)+' '+fmtNum(e.m)+'</span>';
    });
    h+='<br>'+esc(cardMeta(c));
    box.innerHTML=h;
  }

  // ---------- timeline ----------
  var acts=[];        // {actor, k}
  var editIdx=-1;
  function setEdit(i){
    editIdx=i;
    var b=document.getElementById('actAdd');
    b.textContent = i>=0 ? '__AGT_update_btn__' : '__AGT_add_btn__';
    document.getElementById('actCancel').classList.toggle('hideme', i<0);
    renderTL();
  }
  document.getElementById('actAdd').addEventListener('click', function(){
    var k=document.getElementById('cardSel').value;
    if(!byKey[k]){ alert('__AGT_err_no_card__'); return; }
    var a={actor:document.getElementById('aActor').value, k:k};
    if(editIdx>=0 && editIdx<acts.length) acts[editIdx]=a; else acts.push(a);
    setEdit(-1); saveCfg();
  });
  document.getElementById('actCancel').addEventListener('click', function(){ setEdit(-1); });
  document.getElementById('tlClear').addEventListener('click', function(){
    if(acts.length && confirm('__AGT_tl_clear_confirm__')){ acts=[]; setEdit(-1); saveCfg(); }
  });

  function actKinds(a){
    var c=byKey[a.k], h='', seen={};
    c.calc.e.forEach(function(e){ if(!seen[e.k]){ seen[e.k]=1; h+='<i class="dk k-'+e.k+'"></i>'; } });
    if((c.mg||[]).length) h+='<i class="dk k-mark"></i>';
    return h;
  }
  function renderTL(){
    var box=document.getElementById('tl');
    document.getElementById('tlCount').textContent=acts.length?('('+acts.length+')'):'';
    if(!acts.length){ box.innerHTML='<span class="empty-hint">__AGT_tl_empty__</span>'; return; }
    var h='';
    acts.forEach(function(a,i){
      var c=byKey[a.k];
      h+='<div class="act'+(i===editIdx?' editing':'')+'" draggable="true" data-i="'+i+'">'
        +'<span class="no">'+(i+1)+'</span>'
        +'<span class="who '+(a.actor.charAt(0)==='A'?'sA':'sE')+'">'+esc(actorShort(a.actor))+'</span>'
        +'<img class="mi" loading="lazy" src="'+iconUrl(c.uid)+'" alt="">'
        +actKinds(a)
        +'<span class="ab dup" title="⧉">⧉</span><span class="ab del" title="__DBT_remove__">×</span></div>';
    });
    box.innerHTML=h;
  }
  document.getElementById('tl').addEventListener('click', function(e){
    var el=e.target.closest('.act'); if(!el) return;
    var i=+el.dataset.i;
    if(e.target.classList.contains('del')){
      acts.splice(i,1); if(editIdx===i) editIdx=-1; else if(editIdx>i) editIdx--;
      setEdit(editIdx); saveCfg(); return;
    }
    if(e.target.classList.contains('dup')){
      acts.push({actor:acts[i].actor, k:acts[i].k}); renderTL(); saveCfg(); return;
    }
    // load into the form for editing
    document.getElementById('aActor').value=acts[i].actor;
    rebuildCardSel();
    document.getElementById('cardSel').value=acts[i].k;
    if(document.getElementById('cardSel').value!==acts[i].k){
      document.getElementById('poolAll').checked=true; rebuildCardSel();
      document.getElementById('cardSel').value=acts[i].k;
    }
    renderPreview();
    setEdit(i);
  });

  var dragI=null;
  var tlBox=document.getElementById('tl');
  tlBox.addEventListener('dragstart', function(e){
    var el=e.target.closest('.act'); if(!el) return;
    dragI=+el.dataset.i; e.dataTransfer.effectAllowed='move';
    try{ e.dataTransfer.setData('text/plain', String(dragI)); }catch(_e){}
  });
  tlBox.addEventListener('dragover', function(e){
    if(dragI===null) return;
    var el=e.target.closest('.act'); if(!el) return;
    e.preventDefault(); e.dataTransfer.dropEffect='move'; el.classList.add('dragover');
  });
  tlBox.addEventListener('dragleave', function(e){
    var el=e.target.closest('.act'); if(el && !el.contains(e.relatedTarget)) el.classList.remove('dragover');
  });
  tlBox.addEventListener('drop', function(e){
    if(dragI===null) return;
    var el=e.target.closest('.act'); if(!el){ dragI=null; return; }
    e.preventDefault();
    var to=+el.dataset.i, from=dragI; dragI=null;
    if(from===to){ renderTL(); return; }
    var a=acts.splice(from,1)[0];
    if(from<to) to--;
    acts.splice(to,0,a);
    setEdit(-1); saveCfg();
  });
  tlBox.addEventListener('dragend', function(){
    dragI=null;
    var ds=tlBox.querySelectorAll('.dragover');
    for(var i=0;i<ds.length;i++) ds[i].classList.remove('dragover');
  });

  // hover tooltip
  var tip=document.getElementById('tip');
  function actTipHtml(a){
    var c=byKey[a.k];
    var h='<div class="st-name">'+esc(actorLbl(a.actor))+' → '+esc(c.n)+'</div>';
    h+='<div>'+esc(ATTR[c.a-1])+' / '+esc(TYPE_LABEL[c.c])+'</div>';
    c.calc.e.forEach(function(e){
      h+='<div><span class="bk k-'+e.k+'">'+esc(e.l)+'</span> ×'+fmtNum(e.m)+'</div>';
    });
    h+='<div>'+esc(cardMeta(c))+'</div>';
    if(!PC[a.actor].deck.length) h+='<div style="color:#f2b3b3">__AGT_deck_none__</div>';
    return h;
  }
  function posTip(e){
    var gap=14, x=e.clientX+gap, y=e.clientY+gap, r=tip.getBoundingClientRect();
    if(x+r.width>window.innerWidth-8) x=Math.max(8,e.clientX-r.width-gap);
    if(y+r.height>window.innerHeight-8) y=Math.max(8,e.clientY-r.height-gap);
    tip.style.left=x+'px'; tip.style.top=y+'px';
  }
  tlBox.addEventListener('mouseover', function(e){
    var el=e.target.closest('.act'); if(!el) return;
    tip.innerHTML=actTipHtml(acts[+el.dataset.i]);
    tip.classList.add('open'); posTip(e);
  });
  tlBox.addEventListener('mousemove', function(e){ if(tip.classList.contains('open')) posTip(e); });
  tlBox.addEventListener('mouseout', function(e){
    var el=e.target.closest('.act');
    if(el && (!e.relatedTarget || !el.contains(e.relatedTarget))) tip.classList.remove('open');
  });

  // ---------- battle engine ----------
  function applyMainBuff(cur, rawFrac, isDebuff){
    var rem=rawFrac, c=cur, g=0;
    if(!isDebuff){
      while(rem>1e-12 && c<1-1e-9 && g++<60){
        var f, cap;
        if(c<0.5){ f=1; cap=0.5; } else if(c<0.75){ f=0.5; cap=0.75; } else { f=0.1; cap=1; }
        var need=(cap-c)/f;
        if(rem>=need){ c=cap; rem-=need; } else { c+=rem*f; rem=0; }
      }
      return Math.min(1,c);
    }
    while(rem>1e-12 && c>-0.7+1e-9 && g++<60){
      var f2, fl;
      if(c>-0.25){ f2=1; fl=-0.25; } else if(c>-0.5){ f2=0.5; fl=-0.5; } else { f2=0.1; fl=-0.7; }
      var need2=(c-fl)/f2;
      if(rem>=need2){ c=fl; rem-=need2; } else { c-=rem*f2; rem=0; }
    }
    return Math.max(-0.7,c);
  }

  // ---- stacks: ordered list [[kind, layers], ...] — max 3 kinds, 3 layers each.
  // Gaining a 4th kind evicts the EARLIEST-acquired kind (index 0); an emptied
  // kind frees its slot (loses its age). Kinds: 0 Mt / 1 An / 2 Et / 3 Ba.
  function stackGain(ps, kind, add){
    for(var i=0;i<ps.stk.length;i++){
      if(ps.stk[i][0]===kind){ ps.stk[i][1]=Math.min(3, ps.stk[i][1]+add); return; }
    }
    if(ps.stk.length>=3) ps.stk.shift();
    ps.stk.push([kind, Math.min(3,add)]);
  }
  function stackConsume(ps, kind){
    for(var i=0;i<ps.stk.length;i++){
      if(ps.stk[i][0]===kind){
        if(--ps.stk[i][1]<=0) ps.stk.splice(i,1);
        return true;
      }
    }
    return false;
  }

  function initState(P){
    var st={};
    PKEYS.forEach(function(k){
      var cfg=PC[k];
      var ps={hp:P[k].hp, dead:false, last:0,
              b:{pa:clamp(cfg.ib[0],-70,100)/100, ma:clamp(cfg.ib[1],-70,100)/100,
                 pd:clamp(cfg.ib[2],-70,100)/100, md:clamp(cfg.ib[3],-70,100)/100},
              ea:[0,0,0,0,0,0], ed:[0,0,0,0,0,0], stk:[]};
      for(var a=1;a<=5;a++){
        ps.ea[a]=clamp(cfg.iea[a],-50,50)/100;
        ps.ed[a]=clamp(cfg.ied[a],-50,50)/100;
      }
      for(var s=0;s<3;s++){ if(cfg.sk[s]>=0) stackGain(ps, cfg.sk[s], cfg.sn[s]); }
      st[k]=ps;
    });
    return st;
  }
  function present(P,k){ return P[k].hp>0; }
  function frontKeys(side){ return [side+'F1',side+'F2',side+'F3',side+'F4']; }
  function wiped(P,st,side){
    var any=false, ks=frontKeys(side);
    for(var i=0;i<4;i++){ var k=ks[i];
      if(present(P,k)){ any=true; if(!st[k].dead) return false; } }
    return any;
  }
  function frontHp(P,st,side){
    var s=0, ks=frontKeys(side);
    for(var i=0;i<4;i++){ if(present(P,ks[i])) s+=st[ks[i]].hp; }
    return s;
  }
  function totalAtk(P,st,k,type,attr){
    var code=(type===1)?'pa':'ma';
    var v=P[k][code]*(1+st[k].b[code]) + ((P[k].pa+P[k].ma)/2)*st[k].ea[attr];
    return v<1?1:v;
  }
  function totalDef(P,st,k,type,attr){
    var code=(type===1)?'pd':'md';
    var v=P[k][code]*(1+st[k].b[code]) + ((P[k].pd+P[k].md)/2)*st[k].ed[attr];
    return v<1?1:v;
  }
  function baseOfCode(P,k,code){
    if(code==='pa'||code==='ma'||code==='pd'||code==='md') return P[k][code];
    if(code.charAt(0)==='e' && code.charAt(1)==='a') return (P[k].pa+P[k].ma)/2;
    if(code.charAt(0)==='e' && code.charAt(1)==='d') return (P[k].pd+P[k].md)/2;
    return 0;
  }

  // ADX value: choice #0/#3 include a 0.95 that only applies to damage & debuff
  function adxVal(cfg, at, kind){
    var idx=cfg.adx[at], t=cfg.theme[at]?1.055:1.05, has95=(kind==='dmg'||kind==='debuff');
    if(idx===2) return t;
    if(idx===3) return has95?t*0.95:t;
    if(idx===0) return has95?0.95:1;
    return 1;
  }

  // per-player derived pools (UP passives / ko / legendary), depends on side orders
  function playerDerived(key){
    var cfg=PC[key], side=key.charAt(0), opp=side==='A'?'E':'A';
    var own=ordInfo(side), other=ordInfo(opp);
    var rateUp=own?(own.rateUp||0)/100:0, rateDown=other?(other.rateDown||0)/100:0;
    var up=[], leg=[], koNo=1, hasKo=false;
    cfg.deck.forEach(function(k){
      var cc=byKey[k]; if(!cc) return;
      var c=cc.calc;
      (c.pu||[]).forEach(function(p){
        var r=(PBASE[c.pp||0]||0.15)+(cfg.theme[c.a]?0.02:0);
        r=r*(1+rateUp)*(1-rateDown); r=clamp(r,0,1);
        up.push({k:p.k, add:p.c*1.5, r:r});
      });
      if(c.ko!=null){
        hasKo=true;
        var r2=(PBASE[c.ko]||0.15)+(cfg.theme[c.a]?0.02:0);
        r2=r2*(1+rateUp)*(1-rateDown); r2=clamp(r2,0,1);
        koNo*=(1-r2);
      }
      (c.lu||[]).forEach(function(l){ leg.push(l); });
    });
    return {up:up, koP:hasKo?(1-koNo):0, leg:leg};
  }

  // deterministic per-line pre-compute for one action (everything not rolled/stateful)
  function prepAction(a, derCache){
    var card=byKey[a.k], c=card.calc, cfg=PC[a.actor];
    var side=a.actor.charAt(0), opp=side==='A'?'E':'A';
    var own=ordInfo(side), other=ordInfo(opp);
    if(!derCache[a.actor]) derCache[a.actor]=playerDerived(a.actor);
    var der=derCache[a.actor];
    var dis=sideDis(side);
    var trig=(c.ut||[]).some(function(t){ return own && own.type===t; });
    var cos=(cfg.cost && cfg.cost===c.c)?1.15:1;
    var charmM=1+(cfg.charm[c.a]||0)/100, themeM=cfg.theme[c.a]?1.1:1;
    var attrB=0;
    if(own){
      var tm2=cfg.theme[c.a]?1.1:1;
      if(own.tAttr===c.a) attrB+=(own.up||0)/100*tm2;
      if(own.tAttr2===c.a) attrB+=(own.up2||0)/100*tm2;
    }
    var effUp=(own && own.tCard===c.c)?(own.up||0)/100:0;
    var effDown=(other && other.tCard===c.c)?(other.down||0)/100:0;
    var shB=0;
    if(other){
      if(other.tAttr===c.a) shB+=(other.down||0)/100;
      if(other.tAttr2===c.a) shB+=((other.down2||other.down)||0)/100;
    }
    var disadv=(dis && own)?(own.disadv||0)/100:0;
    var ctM=(dis && c.ct>0)?c.ct:1;
    var ehVal=c.eh>0?c.eh:1;
    var lines=[];
    var hasDmg=false, hasHeal=false, hasBuff=false, hasDebuff=false;
    c.e.forEach(function(e){
      var mag=(e.m+(trig?(c.am||0):0))*(1+(c.tm||0));
      var cmdDmgRed=0, cmdDis=0;
      if(e.k==='dmg'){
        cmdDmgRed=other?((e.t===2?(other.dmgRedM||0):(other.dmgRedP||0))/100):0;
        cmdDis=disadv;
        hasDmg=true;
      }
      if(e.k==='heal') hasHeal=true;
      if(e.k==='buff') hasBuff=true;
      if(e.k==='debuff') hasDebuff=true;
      var cmdShB=(e.k!=='heal')?shB:0;
      var cmd=1+attrB+effUp-effDown-cmdShB-cmdDmgRed+cmdDis;
      var adxM=adxVal(cfg, c.a, e.k);
      // NOTE: excludes the random region (rolled live), UP (rolled), marks & EH (stateful)
      var det=e.g*mag*1.5*cos*1.1*charmM*adxM*themeM*ctM*cmd;
      lines.push({k:e.k, t:e.t, s:e.s, det:det});
    });
    return {actor:a.actor, side:side, opp:opp, card:card, c:c, lines:lines,
            up:der.up, koP:der.koP, leg:der.leg, ehVal:ehVal,
            hasDmg:hasDmg, hasHeal:hasHeal, hasBuff:hasBuff, hasDebuff:hasDebuff,
            tgtAlly:(c.c===5||c.c===7)};
  }

  function legSum(legs, attr, kind, atk){
    var pk=(kind==='debuff')?'buff':kind, s=0;
    for(var i=0;i<legs.length;i++){
      var l=legs[i];
      if(l.a===attr && l.k===pk && (l.t===0||l.t===atk)) s+=l.p;
    }
    return s;
  }

  function pickTargets(P,st,side,n,withDead){
    var pool=[], ks=frontKeys(side);
    for(var i=0;i<4;i++){ var k=ks[i];
      if(present(P,k) && (withDead||!st[k].dead)) pool.push(k); }
    if(!pool.length) return pool;
    if(n>pool.length) n=pool.length;
    for(var s=0;s<n;s++){
      var r=s+Math.floor(Math.random()*(pool.length-s));
      var tmp=pool[s]; pool[s]=pool[r]; pool[r]=tmp;
    }
    return pool.slice(0,n);
  }

  function execAction(P,st,pa,autoRev){
    var me=pa.actor, a=st[me];
    if(me.charAt(1)==='F' && a.dead){
      if(autoRev){ a.hp=Math.max(1,Math.floor(P[me].hp*0.3)); a.dead=false; }
      return;   // the action is consumed either way
    }
    var c=pa.c, card=pa.card;
    // stateful conditions: EH / SD need "last used card attribute differs"
    var adiff=(a.last!==0 && a.last!==c.a);
    var ehM=adiff?pa.ehVal:1;
    // roll UP passives: one Bernoulli per deck passive per action
    var add={dmg:0,heal:0,buff:0};
    for(var i=0;i<pa.up.length;i++){
      var u=pa.up[i];
      if(Math.random()<u.r) add[u.k]+=u.add;
    }
    function upM(kind, atk){
      var pk=(kind==='debuff')?'buff':kind;
      return 1+(add[pk]||0)+legSum(pa.leg, c.a, kind, atk||0);
    }
    // roll target count: uniform in {min,max}; SD pins to max; コ:効果範囲+1 rolls +1 (cap 4)
    var tn=c.tn||[1,1];
    var lo=(c.sd&&adiff)?tn[1]:tn[0], hi=tn[1];
    var n=lo+(hi>lo?Math.floor(Math.random()*(hi-lo+1)):0);
    var koProc=false;
    if(pa.koP>0 && Math.random()<pa.koP){ koProc=true; n=Math.min(4,n+1); }
    // target groups
    var enemyT=null, allyT=null;
    function enemies(){ if(enemyT===null) enemyT=pickTargets(P,st,pa.opp,n,false); return enemyT; }
    function allies(){ if(allyT===null) allyT=pickTargets(P,st,pa.side,n,pa.hasHeal); return allyT; }
    // stack consumption: each trigger kind consumes 1 layer simultaneously
    // (a damage + debuff card consumes 1 Mt AND 1 An when held)
    var mtM=1, etM=1, anM=1;
    if(pa.hasDmg && stackConsume(a,0)) mtM=1.2;
    if(pa.hasHeal && stackConsume(a,2)) etM=1.3;
    if((pa.hasBuff||pa.hasDebuff) && stackConsume(a,1)) anM=1.3;

    // 1) damage first (never benefits from this card's own debuffs)
    for(var li=0;li<pa.lines.length;li++){
      var e=pa.lines[li];
      if(e.k!=='dmg') continue;
      var mag=e.det*upM('dmg',e.t)*mtM*ehM;
      var ts=enemies();
      var TA=totalAtk(P,st,me,e.t,c.a);
      for(var t=0;t<ts.length;t++){
        var k2=ts[t];
        var TD=totalDef(P,st,k2,e.t,c.a);
        var ratio=Math.floor(TA/TD); if(ratio>10) ratio=10;
        var core=(TA-(2/3)*TD)*mag*(1+0.05*ratio);
        if(core<1) core=1;
        var dmg=core*(0.9+Math.random()*0.1)+(1+Math.floor(Math.random()*200));
        // Ba protects only its holder: consumed per hit RECEIVED, one layer each
        if(stackConsume(st[k2],3)) dmg*=0.7;
        dmg=Math.floor(dmg);
        st[k2].hp-=dmg;
        if(st[k2].hp<=0){ st[k2].hp=0; st[k2].dead=true; }
      }
    }
    // 2) heal (before buffs so the revived receive them); attack cards self-heal
    for(var l2=0;l2<pa.lines.length;l2++){
      var e2=pa.lines[l2];
      if(e2.k!=='heal') continue;
      var hm=e2.det*upM('heal',0)*etM*ehM;
      var amt=Math.floor(hm*((P[me].pd+P[me].md)/2)*(0.9+Math.random()*0.1));
      if(amt<=0) continue;
      var hts=pa.tgtAlly?allies():[me];
      for(var t2=0;t2<hts.length;t2++){
        var k3=hts[t2];
        if(st[k3].dead){ st[k3].dead=false; st[k3].hp=Math.min(P[k3].hp,amt); }
        else st[k3].hp=Math.min(P[k3].hp, st[k3].hp+amt);
      }
    }
    // 3) buff lines (support/heal cards -> targets; attack/interference cards -> self)
    for(var l3=0;l3<pa.lines.length;l3++){
      var e3=pa.lines[l3];
      if(e3.k!=='buff' || !e3.s) continue;
      var bm=e3.det*upM('buff',0)*anM*ehM;
      var raw=Math.floor(bm*baseOfCode(P,me,e3.s));
      if(raw<=0) continue;
      var bts=pa.tgtAlly?allies():[me];
      applyStatLines(P,st,bts,e3.s,raw,false);
    }
    // 4) debuff lines -> enemies
    for(var l4=0;l4<pa.lines.length;l4++){
      var e4=pa.lines[l4];
      if(e4.k!=='debuff' || !e4.s) continue;
      var dm=e4.det*upM('debuff',0)*anM*ehM;
      var raw2=Math.floor(dm*baseOfCode(P,me,e4.s));
      if(raw2<=0) continue;
      applyStatLines(P,st,enemies(),e4.s,raw2,true);
    }
    // 5) stack grants, as parsed from the skill text:
    //    自身 -> the actor (unaffected by target count); 味方(=card targets) -> the
    //    rolled ally targets (incl. any 効果範囲+1 extra); 味方N~M体 -> its own roll
    //    (also +1 when 効果範囲+1 procced). Never granted to enemies.
    for(var g=0;g<card.mg.length;g++){
      var mg=card.mg[g], rcv;
      if(mg[0]===1) rcv=[me];
      else if(mg[1]>0){
        var n2=mg[1]+(mg[2]>mg[1]?Math.floor(Math.random()*(mg[2]-mg[1]+1)):0);
        if(koProc) n2=Math.min(4,n2+1);
        rcv=pickTargets(P,st,pa.side,n2,false);
      } else {
        rcv=pa.tgtAlly?allies():pickTargets(P,st,pa.side,n,false);
      }
      for(var r2=0;r2<rcv.length;r2++){
        var k5=rcv[r2];
        if(st[k5].dead) continue;
        for(var q=0;q<mg[3].length;q++) stackGain(st[k5], mg[3][q][0], mg[3][q][1]);
      }
    }
    a.last=c.a;
  }

  function applyStatLines(P,st,targets,code,raw,isDebuff){
    var isElem=(code.length===3);
    for(var t=0;t<targets.length;t++){
      var k=targets[t];
      if(st[k].dead) continue;
      if(!isElem){
        var tb=P[k][code]; if(tb<=0) continue;
        st[k].b[code]=applyMainBuff(st[k].b[code], raw/tb, isDebuff);
      } else {
        var ai=+code.charAt(2);
        var tb2=(code.charAt(1)==='a')?((P[k].pa+P[k].ma)/2):((P[k].pd+P[k].md)/2);
        if(tb2<=0) continue;
        var d=(raw/tb2)*(isDebuff?-1:1);
        if(code.charAt(1)==='a') st[k].ea[ai]=clamp(st[k].ea[ai]+d,-0.5,0.5);
        else st[k].ed[ai]=clamp(st[k].ed[ai]+d,-0.5,0.5);
      }
    }
  }

  function runOne(P,preps,autoRev){
    var st=initState(P);
    for(var i=0;i<preps.length;i++){
      if(wiped(P,st,'A')||wiped(P,st,'E')) break;
      execAction(P,st,preps[i],autoRev);
    }
    return {a:frontHp(P,st,'A'), e:frontHp(P,st,'E'),
            aw:wiped(P,st,'A'), ew:wiped(P,st,'E')};
  }

  // ---------- Monte-Carlo runner ----------
  var running=false;
  document.getElementById('run').addEventListener('click', function(){
    if(running) return;
    var P=readPlayers();
    if(!acts.length){ alert('__AGT_err_actions__'); return; }
    var okA=false, okE=false, i;
    for(i=1;i<=4;i++){ if(present(P,'AF'+i)) okA=true; if(present(P,'EF'+i)) okE=true; }
    if(!okA||!okE){ alert('__AGT_err_front__'); return; }
    var bad=[];
    acts.forEach(function(a){
      if(a.actor.charAt(1)==='F' && !present(P,a.actor) && bad.indexOf(a.actor)<0) bad.push(a.actor);
    });
    if(bad.length){ alert('__AGT_err_actor_nohp__ '+bad.map(actorLbl).join(', ')); return; }
    saveCfg();

    var N=Math.floor(clamp(num('trials'),100,1000000));
    document.getElementById('trials').value=N;
    var autoRev=document.getElementById('autoRev').checked;
    var derCache={};
    var preps=acts.map(function(a){ return prepAction(a, derCache); });
    var aArr=new Float64Array(N), eArr=new Float64Array(N);
    var win=0, lose=0, draw=0, done=0;
    var btn=this, prog=document.getElementById('prog');
    running=true; btn.disabled=true;
    function step(){
      var end=Math.min(N, done+10000);
      for(var j=done;j<end;j++){
        var r=runOne(P,preps,autoRev);
        aArr[j]=r.a; eArr[j]=r.e;
        if(r.ew) win++; else if(r.aw) lose++; else draw++;
      }
      done=end;
      prog.textContent='__AGT_running__ '+Math.round(100*done/N)+'%';
      if(done<N){ setTimeout(step,0); return; }
      running=false; btn.disabled=false; prog.textContent='';
      showResults(P,N,win,lose,draw,aArr,eArr);
    }
    step();
  });

  function pct(x,n){ return (100*x/n).toFixed(2)+'%'; }
  function totalMaxHp(P,side){
    var s=0; frontKeys(side).forEach(function(k){ if(present(P,k)) s+=P[k].hp; }); return s;
  }
  function showResults(P,N,win,lose,draw,aArr,eArr){
    document.getElementById('tiles').innerHTML=
      '<div class="tile tw"><div class="tk">__AGT_res_win__</div><div class="tv">'+pct(win,N)+'</div><div class="tc">'+win+' / '+N+'</div></div>'
     +'<div class="tile tl2"><div class="tk">__AGT_res_lose__</div><div class="tv">'+pct(lose,N)+'</div><div class="tc">'+lose+' / '+N+'</div></div>'
     +'<div class="tile td"><div class="tk">__AGT_res_draw__</div><div class="tv">'+pct(draw,N)+'</div><div class="tc">'+draw+' / '+N+'</div></div>';
    drawHist('cE','iE',eArr,totalMaxHp(P,'E'),'#a84a3d');
    drawHist('cA','iA',aArr,totalMaxHp(P,'A'),'#3d6da8');
  }

  // ---------- histogram ----------
  var CHART={};
  function fmtInt(v){ return Math.round(v).toString().replace(/\\B(?=(\\d{3})+(?!\\d))/g, ','); }
  function drawHist(cvId, infoId, arr, total, color){
    var cv=document.getElementById(cvId), ctx=cv.getContext('2d');
    var W=cv.width, H=cv.height, L=52, R=12, T=12, B=34;
    ctx.clearRect(0,0,W,H);
    var nb=48, bins=new Float64Array(nb), N=arr.length, mean=0;
    for(var i=0;i<N;i++){
      var v=arr[i]; mean+=v;
      var idx=total>0?Math.min(nb-1,Math.floor(v/total*nb)):0;
      bins[idx]++;
    }
    mean/=N;
    var pmax=0;
    for(var b=0;b<nb;b++){ bins[b]/=N; if(bins[b]>pmax) pmax=bins[b]; }
    if(pmax<=0) pmax=1;
    var ymax=Math.ceil(pmax*20)/20; if(ymax>1) ymax=1;
    var iw=(W-L-R)/nb;
    ctx.strokeStyle='#c5ccda'; ctx.fillStyle='#667'; ctx.font='11px sans-serif'; ctx.lineWidth=1;
    ctx.textAlign='right'; ctx.textBaseline='middle';
    for(var yt=0;yt<=4;yt++){
      var p=ymax*yt/4, y=H-B-(H-T-B)*(p/ymax);
      ctx.beginPath(); ctx.moveTo(L,y); ctx.lineTo(W-R,y); ctx.stroke();
      ctx.fillText((p*100).toFixed(1)+'%', L-5, y);
    }
    ctx.textAlign='center'; ctx.textBaseline='top';
    for(var xt=0;xt<=4;xt++){
      var xv=total*xt/4, x=L+(W-L-R)*xt/4;
      ctx.fillText(fmtInt(xv), Math.min(Math.max(x,L+14),W-R-14), H-B+6);
    }
    ctx.fillStyle=color;
    for(var b2=0;b2<nb;b2++){
      if(bins[b2]<=0) continue;
      var bh=(H-T-B)*(bins[b2]/ymax);
      ctx.fillRect(L+b2*iw+1, H-B-bh, Math.max(1,iw-2), bh);
    }
    if(total>0){
      var mx=L+(W-L-R)*(mean/total);
      ctx.strokeStyle='#333'; ctx.setLineDash([4,3]);
      ctx.beginPath(); ctx.moveTo(mx,T); ctx.lineTo(mx,H-B); ctx.stroke();
      ctx.setLineDash([]);
    }
    document.getElementById(infoId).textContent=
      '__AGT_mean_hp__: '+fmtInt(mean)+' / __AGT_total_hp_lbl__: '+fmtInt(total);
    CHART[cvId]={L:L,R:R,nb:nb,total:total,bins:bins,infoBase:document.getElementById(infoId).textContent, infoId:infoId};
    cv.onmousemove=function(e){
      var m=CHART[cvId]; if(!m) return;
      var rect=cv.getBoundingClientRect();
      var x=(e.clientX-rect.left)*(cv.width/rect.width);
      var bi=Math.floor((x-m.L)/((cv.width-m.L-m.R)/m.nb));
      var el=document.getElementById(m.infoId);
      if(bi<0||bi>=m.nb){ el.textContent=m.infoBase; return; }
      var lo=m.total*bi/m.nb, hi=m.total*(bi+1)/m.nb;
      el.textContent=m.infoBase+'    |    '+fmtInt(lo)+' ~ '+fmtInt(hi)+' HP: __AGT_prob_lbl__ '+(m.bins[bi]*100).toFixed(2)+'%';
    };
    cv.onmouseleave=function(){
      var m=CHART[cvId]; if(m) document.getElementById(m.infoId).textContent=m.infoBase;
    };
  }

  // ---------- config save / load ----------
  var LS_KEY='agamotto_cfg_v2';
  function collectCfg(){
    var p={}, pc={};
    PKEYS.forEach(function(k){
      p[k]=[num('p_'+k+'_pa'),num('p_'+k+'_ma'),num('p_'+k+'_pd'),num('p_'+k+'_md'),num('p_'+k+'_hp')];
      var c=PC[k];
      if(c.code||c.cost||c.charm.some(function(x){return x;})||c.theme.some(function(x){return x;})
         ||c.adx.some(function(x,i){return i>0&&x!==1;})
         ||c.ib.some(function(x){return x;})||c.iea.some(function(x){return x;})
         ||c.ied.some(function(x){return x;})||c.sk.some(function(x){return x>=0;}))
        pc[k]={code:c.code, charm:c.charm.slice(1), adx:c.adx.slice(1),
               theme:c.theme.slice(1).map(function(x){return x?1:0;}), cost:c.cost,
               ib:c.ib, iea:c.iea.slice(1), ied:c.ied.slice(1), sk:c.sk, sn:c.sn};
    });
    return {v:2, p:p, pc:pc, acts:acts.map(function(a){ return [a.actor,a.k]; }),
            ord:sideOrd, dis:[sideDis('A')?1:0, sideDis('E')?1:0],
            tr:Math.floor(num('trials')), ar:document.getElementById('autoRev').checked};
  }
  function applyCfg(c){
    if(!c || c.v!==2) return false;
    PKEYS.forEach(function(k){
      var row=(c.p&&c.p[k])||[0,0,0,0,0];
      ['pa','ma','pd','md','hp'].forEach(function(f,i){
        document.getElementById('p_'+k+'_'+f).value=row[i]||0;
      });
      var pcc=(c.pc&&c.pc[k]);
      var n=newPC();
      if(pcc){
        n.code=pcc.code||'';
        n.deck=decodeDeck(n.code)||[];
        for(var a=1;a<=5;a++){
          n.charm[a]=(pcc.charm&&pcc.charm[a-1])||0;
          n.adx[a]=(pcc.adx&&typeof pcc.adx[a-1]==='number')?pcc.adx[a-1]:1;
          n.theme[a]=!!(pcc.theme&&pcc.theme[a-1]);
          n.iea[a]=clamp((pcc.iea&&pcc.iea[a-1])||0,-50,50);
          n.ied[a]=clamp((pcc.ied&&pcc.ied[a-1])||0,-50,50);
        }
        for(var i2=0;i2<4;i2++) n.ib[i2]=clamp((pcc.ib&&pcc.ib[i2])||0,-70,100);
        for(var s2=0;s2<3;s2++){
          n.sk[s2]=(pcc.sk&&typeof pcc.sk[s2]==='number'&&pcc.sk[s2]>=0&&pcc.sk[s2]<=3)?pcc.sk[s2]:-1;
          n.sn[s2]=clamp(Math.floor((pcc.sn&&pcc.sn[s2])||1),1,3);
        }
        n.cost=pcc.cost||0;
      }
      PC[k]=n;
    });
    acts=(c.acts||[]).map(function(x){ return {actor:x[0], k:x[1]}; })
      .filter(function(a){ return PKEYS.indexOf(a.actor)>=0 && byKey[a.k]; });
    sideOrd={A:null,E:null};
    if(c.ord){
      ['A','E'].forEach(function(s){
        var o=c.ord[s];
        if(o && TAC[o.g] && TAC[o.g][o.i]) sideOrd[s]={g:o.g, i:o.i};
      });
    }
    ['A','E'].forEach(function(s){
      var box=document.getElementById('ord'+s);
      var ons=box.querySelectorAll('.tac-ic.on');
      for(var i=0;i<ons.length;i++) ons[i].classList.remove('on');
      var o=sideOrd[s];
      if(o){
        var b=box.querySelector('.tac-ic[data-g="'+o.g+'"][data-i="'+o.i+'"]');
        if(b) b.classList.add('on');
      }
    });
    document.getElementById('disA').checked=!!(c.dis&&c.dis[0]);
    document.getElementById('disE').checked=!!(c.dis&&c.dis[1]);
    if(c.tr) document.getElementById('trials').value=clamp(Math.floor(c.tr),100,1000000);
    document.getElementById('autoRev').checked=!!c.ar;
    pcShow(pcCur); refreshDeckCol(); rebuildCardSel(); setEdit(-1);
    return true;
  }
  function cfgCode(){
    return btoa(unescape(encodeURIComponent(JSON.stringify(collectCfg()))));
  }
  function saveCfg(){
    try{ localStorage.setItem(LS_KEY, JSON.stringify(collectCfg())); }catch(_e){}
    document.getElementById('cfg').value=cfgCode();
  }
  document.getElementById('cfgCopy').addEventListener('click', function(){
    saveCfg();
    var t=document.getElementById('cfg'); t.select();
    if(navigator.clipboard){ navigator.clipboard.writeText(t.value); } else { document.execCommand('copy'); }
    var b=this, o=b.textContent; b.textContent='__DBT_copied__'; setTimeout(function(){ b.textContent=o; },1000);
  });
  document.getElementById('cfgLoad').addEventListener('click', function(){
    var s=(document.getElementById('cfg').value||'').trim(); if(!s) return;
    var c=null;
    try{ c=JSON.parse(decodeURIComponent(escape(atob(s)))); }
    catch(e1){ try{ c=JSON.parse(s); }catch(e2){} }
    if(!c || !applyCfg(c)){ alert('__DBT_decode_fail__'); return; }
    saveCfg();
  });
  document.addEventListener('change', function(e){
    if(e.target.matches && e.target.matches('.ptbl input, #trials, #autoRev, #disA, #disE')) saveCfg();
  });

  // ---------- init ----------
  (function(){
    var raw=null;
    try{ raw=localStorage.getItem(LS_KEY); }catch(_e){}
    if(raw){ try{ applyCfg(JSON.parse(raw)); }catch(_e2){} }
    pcShow(pcCur); refreshDeckCol(); rebuildCardSel(); renderTL(); saveCfg();
  })();
</script>
</body>
</html>
"""


def main():
    html_text = render_html()
    config.ensure_output_dir()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html_text)
    print("Generated Agamotto simulator (card-based)")
    print("Output file: %s" % OUT)


if __name__ == "__main__":
    main()
