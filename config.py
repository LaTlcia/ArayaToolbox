# -*- coding: utf-8 -*-
import os
import json as _json

# ===========================================================================
# Paths  (anchored to this file's folder = the project root, i.e. where
#         build_all.py and config.py live, and the parent of method/)
# ===========================================================================
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
METHOD_DIR = os.path.join(PROJECT_ROOT, "method")
ASSETS_DIR = os.path.join(PROJECT_ROOT, "data/assets")
LANGUAGE_DIR = os.path.join(PROJECT_ROOT, "data/language")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")   # generated *.html go here

# Generated page files
CARD_LIST_OUT = os.path.join(OUTPUT_DIR, "card_list.html")
TACTICS_LIST_OUT = os.path.join(OUTPUT_DIR, "tactics_list.html")
DECK_BUILDER_OUT = os.path.join(OUTPUT_DIR, "deck_builder.html")

# ===========================================================================
# Page language:  "cn" | "jp" | "en"   (files: ./language/<code>.json)
# ===========================================================================
LANGUAGE = (os.environ.get("ARAYA_LANG", "").strip().lower() or "jp")
LANGUAGE_FALLBACK = "jp"

# ===========================================================================
MASTERDATA_DB_DIR = os.path.join(os.path.dirname(PROJECT_ROOT), "A.RA.YA", "MasterdataBase")
MASTERDATA_CACHE_DIR = os.path.join(PROJECT_ROOT, "data/masterdata")

# ===========================================================================
GITHUB_OWNER = "LaTlcia"
GITHUB_REPO = "A.RA.YA"
GITHUB_BRANCH = "main"
GITHUB_SUBDIR = "MasterdataBase"
MASTERDATA_RAW_BASE = "https://raw.githubusercontent.com/%s/%s/%s/%s/" % (
    GITHUB_OWNER, GITHUB_REPO, GITHUB_BRANCH, GITHUB_SUBDIR)


MASTERDATA_FORCE_REFRESH = False
MASTERDATA_REFRESH_ENV = "MASTERDATA_REFRESH"
MASTERDATA_FILES = (
    "masterdata_api_mst_getCardMstList.json",
    "masterdata_api_mst_getLimitBreakBonusMstList.json",
    "masterdata_api_mst_getSkillMstList.json",
    "masterdata_api_mst_getLegendarySkillGroupMstList.json",
    "masterdata_api_mst_getUltimateCardMstList.json",
    "masterdata_api_mst_getCardSuperAwakeningCardTypeMstList.json",
    "masterdata_api_mst_getTacticsMstList.json",
    "masterdata_api_mst_getTacticsEffectMstList.json",
)

# ===========================================================================
ASSETS_REMOTE_BASE = "https://allb.tqlwsl.moe/"
ASSETS_UPDATE_DIR = ASSETS_REMOTE_BASE + "update/"
ASSETS_LOCAL_DIR = os.path.join(ASSETS_DIR, "remote")
ASSETS_STATE_FILE = os.path.join(ASSETS_LOCAL_DIR, ".sync_state.json")

SPRITE_DIR = os.path.join(ASSETS_DIR, "Sprite")
MARKER_DIR = os.path.join(ASSETS_DIR, "markers")

ASSET_CARD_ICON_REMOTE = "Image/CardIcon/S/CardIconS0%s.png"
ASSET_TACTICS_ICON_REMOTE = "Image/TacticsIcon/S/TacticsIconS%03d.png"
ASSET_WATERMARK_REMOTE = "Image/Card/Card020000216.jpg"

URL_CARD_ICON = "assets/remote/Image/CardIcon/S/CardIconS0{uid}.png"
URL_TACTICS_ICON = "assets/remote/Image/TacticsIcon/S/TacticsIconS{uid:03d}.png"
URL_WATERMARK = "assets/remote/" + ASSET_WATERMARK_REMOTE
URL_MARKER_DIR = "assets/markers"
URL_SPRITE_DIR = "assets/Sprite"

SKILL_ICON = "../data/assets/Sprite/BattleIconSkillImg%03d.png"
TGT_ICON = "../data/assets/Sprite/BattleIconTargetNumberImg%03d%03d.png"

# ===========================================================================
DOWNLOAD_WORKERS = 16
HTTP_TIMEOUT = 30
USER_AGENT = "ArayaToolbox/1.0"


# ===========================================================================
_lang_cache = {}


def _load_lang(code):
    if code not in _lang_cache:
        path = os.path.join(LANGUAGE_DIR, code + ".json")
        try:
            with open(path, encoding="utf-8") as f:
                _lang_cache[code] = _json.load(f)
        except (FileNotFoundError, ValueError):
            _lang_cache[code] = {}
    return _lang_cache[code]


def _dig(d, dotted):
    cur = d
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def t(key, default=None):
    val = _dig(_load_lang(LANGUAGE), key)
    if val is None and LANGUAGE != LANGUAGE_FALLBACK:
        val = _dig(_load_lang(LANGUAGE_FALLBACK), key)
    if val is None:
        return key if default is None else default
    return val


def section(name):
    merged = dict(_dig(_load_lang(LANGUAGE_FALLBACK), name) or {})
    merged.update(_dig(_load_lang(LANGUAGE), name) or {})
    return merged


def int_label_map(name):
    return {int(k): v for k, v in section(name).items()}


def html_lang():
    return t("_meta.html_lang", {"cn": "zh", "jp": "ja", "en": "en"}.get(LANGUAGE, "en"))


# ===================helpers============================================
def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR


def asset_url_prefix():
    return os.path.relpath(ASSETS_DIR, OUTPUT_DIR).replace(os.sep, "/") + "/"


def relocate_asset_urls(html_text):
    target = asset_url_prefix()
    for q in ('"', "'", "("):
        html_text = html_text.replace(q + "assets/", q + target)
    return html_text
