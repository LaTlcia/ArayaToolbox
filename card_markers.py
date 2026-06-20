# -*- coding: utf-8 -*-
"""
Card corner marker / frame compositing (shared by the list and the deck builder)
================================================================================
Composite the sprites under assets/Sprite/ into "top-right category marker" PNGs,
written to assets/markers/. The card art itself is a (now local) image; the frame
(IconRarity) and the marker are layered over the card art in the HTML.

Marker rules:
  * Base ring color follows attribute: IconType{001..005}L...  (1=fire 2=water 3=wind 4=light 5=dark)
  * Non-awakening: IconType{a}LImage.png(75x75) with CardIcon{ct}LImage.png(60x60) centered
  * Awakening: IconType{a}LImageAwakening.png(129x76)
        right (big circle, center 91,38, diameter 74) <- CardIcon{original ct}(60x60)
        left  (small circle, center 32,32, diameter 63) <- CardIcon{awakening-added ct} scaled to 51x51
        in the deck builder only one is drawn per entry (base = big circle only / add = small circle only)
  * Super-awakening: IconType{a}LImageSuperAwakening001.png(90x90) with CardIcon{ct}(60x60) centered
Frame: Ultimate (gradeType==2) uses IconRarity08L, otherwise IconRarity06L (covers the whole card).
"""

import os
from PIL import Image

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPRITE_DIR = os.path.join(SCRIPT_DIR, "assets", "Sprite")
MARKER_DIR = os.path.join(SCRIPT_DIR, "assets", "markers")
MARKER_REL = "assets/markers"

# Composite positions (center coords) / sizes (measured from the sprites)
PLAIN_C = (37, 37)       # 75x75 single circle
SUPER_C = (45, 45)       # 90x90 single diamond
AWK_BIG = (91, 38)       # 129x76 right big circle (diameter 74) -> original category CardIcon 60x60
AWK_SMALL = (32, 32)     # 129x76 left small circle (diameter 63) -> awakening-added category CardIcon, scaled
AWK_SMALL_SIZE = 51      # 60 * 63/74 ~= 51, so the small-circle icon ratio matches the big one

_disk_cache = {}       # fname -> rel path (already written this run)
_img_cache = {}


def _img(name):
    if name not in _img_cache:
        _img_cache[name] = Image.open(os.path.join(SPRITE_DIR, name)).convert("RGBA")
    return _img_cache[name].copy()


def _paste_center(base, icon_name, center, size=None):
    ic = _img(icon_name)
    if size and size != ic.width:
        ic = ic.resize((size, size), Image.LANCZOS)
    x = int(round(center[0] - ic.width / 2.0))
    y = int(round(center[1] - ic.height / 2.0))
    base.alpha_composite(ic, (x, y))


def _ensure(fname, builder):
    if fname in _disk_cache:
        return _disk_cache[fname]
    os.makedirs(MARKER_DIR, exist_ok=True)
    builder().save(os.path.join(MARKER_DIR, fname))
    rel = MARKER_REL + "/" + fname
    _disk_cache[fname] = rel
    return rel


def marker_none(attr, ctype):
    def b():
        base = _img("IconType%03dLImage.png" % attr)
        _paste_center(base, "CardIcon%03dLImage.png" % ctype, PLAIN_C)
        return base
    return _ensure("mk_n_%d_%d.png" % (attr, ctype), b)


def marker_super(attr, ctype):
    def b():
        base = _img("IconType%03dLImageSuperAwakening001.png" % attr)
        _paste_center(base, "CardIcon%03dLImage.png" % ctype, SUPER_C)
        return base
    return _ensure("mk_s_%d_%d.png" % (attr, ctype), b)


def marker_awakening(attr, base_type, add_type, mode):
    """mode: 'full' (both circles) / 'base' (big circle only) / 'add' (small circle only)."""
    def b():
        base = _img("IconType%03dLImageAwakening.png" % attr)
        if mode in ("full", "base"):
            _paste_center(base, "CardIcon%03dLImage.png" % base_type, AWK_BIG)
        if mode in ("full", "add"):
            _paste_center(base, "CardIcon%03dLImage.png" % add_type, AWK_SMALL, AWK_SMALL_SIZE)
        return base
    if mode == "base":
        key = "mk_ab_%d_%d.png" % (attr, base_type)
    elif mode == "add":
        key = "mk_aa_%d_%d.png" % (attr, add_type)
    else:
        key = "mk_a_%d_%d_%d.png" % (attr, base_type, add_type)
    return _ensure(key, b)


def marker_for(entry, context):
    """Return the marker's relative path for an entry. context: 'list' (awakening draws
    both circles) / 'deck' (one circle per entry)."""
    attr = entry["attribute"]
    ct = entry["cardType"]
    awk = entry.get("awk", "none")
    if awk == "super":
        return marker_super(attr, ct)
    if awk == "awakening":
        bt, at = entry["baseType"], entry["addType"]
        if context == "list":
            return marker_awakening(attr, bt, at, "full")
        # Deck builder: both awakening entries use the same single-circle marker as a
        # normal card, each showing its own category.
        return marker_none(attr, bt if entry.get("role") == "base" else at)
    return marker_none(attr, ct)


def frame_rel(is_ultimate):
    return "assets/Sprite/IconRarity0%dLImage.png" % (8 if is_ultimate else 6)
