# -*- coding: utf-8 -*-
"""
Asset localization / incremental update
=======================================
Download the remote assets this toolset uses (per-card CardIconS icons + the page
watermark art) from allb.tqlwsl.moe to local disk, and keep them up to date with
"download deltas only" based on the site's /update records, so the pages work offline.

Local mirror: assets/remote/<remote-relative-path>   (e.g. assets/remote/Image/CardIcon/S/CardIconS0XXXXXXXX.png)
Version state: assets/remote/.sync_state.json         ({"baseline": "<latest applied update filename>"})

Strategy:
  * First run: download every needed asset (effectively "download all missing"),
    and record baseline as the newest record under /update.
  * Every later run: check whether /update has records newer than baseline; if so,
    read the files those records list, keep only the ones this toolset uses
    (CardIconS + watermark), and re-download just those (never a full download);
    then advance baseline to the newest.
  * Always also fill in needed-but-missing assets (auto-fetch icons when masterdata
    gains new cards; missing-only).
  * Offline / unable to reach /update: skip the update check and continue with the
    existing local assets (does not block the build).

Standard library only.
"""

import os as _os, sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import re
import json
import urllib.request
import concurrent.futures

import config


try:
    import direct_download as _direct
except Exception:
    _direct = None

# Source / location settings come from config.py; names below are local aliases.
REMOTE_BASE = config.ASSETS_REMOTE_BASE
UPDATE_DIR = config.ASSETS_UPDATE_DIR

LOCAL_DIR = config.ASSETS_LOCAL_DIR
STATE_FILE = config.ASSETS_STATE_FILE

# Remote assets this toolset uses (paths relative to the site root)
CARD_ICON_REL = config.ASSET_CARD_ICON_REMOTE       # % uniqueId (card)
TACTICS_ICON_REL = config.ASSET_TACTICS_ICON_REMOTE  # % uniqueId (tactics, zero-padded to 3 digits)
WATERMARK_REL = config.ASSET_WATERMARK_REMOTE

# Local relative paths referenced from the HTML (relative to the html file's dir)
CARD_ICON_LOCAL = config.URL_CARD_ICON
TACTICS_ICON_LOCAL = config.URL_TACTICS_ICON
WATERMARK_LOCAL = config.URL_WATERMARK

RE_UPDATE = re.compile(r"\d{4}-\d{2}-\d{2}-\d{6}_file_update\.txt")

_UA = {"User-Agent": config.USER_AGENT}


# ---------------------------------------------------------------------------
# Basic IO
# ---------------------------------------------------------------------------
def _http_get(url, timeout=config.HTTP_TIMEOUT):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _local_path(rel):
    return os.path.join(LOCAL_DIR, rel.replace("/", os.sep))


def _exists_ok(rel):
    p = _local_path(rel)
    return os.path.exists(p) and os.path.getsize(p) > 0


def _load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    os.makedirs(LOCAL_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# /update parsing
# ---------------------------------------------------------------------------
def _list_updates():
    """Return all *_file_update.txt filenames under /update (chronological; the
    filename itself sorts lexicographically = chronologically)."""
    html = _http_get(UPDATE_DIR).decode("utf-8", "replace")
    return sorted(set(RE_UPDATE.findall(html)))


def _changed_files(update_names):
    """Read the given update records and collect every site-relative path they list."""
    changed = set()
    for n in update_names:
        txt = _http_get(UPDATE_DIR + n).decode("utf-8", "replace")
        for line in txt.splitlines():
            line = line.strip()
            if line:
                changed.add(line)
    return changed


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def _download_one(rel, overwrite):
    dst = _local_path(rel)
    if not overwrite and _exists_ok(rel):
        return "skip", rel
    via_direct = False
    try:
        data = _http_get(REMOTE_BASE + rel)
    except Exception as e:                       # noqa: BLE001
        if _direct is None or not _direct.available():
            return "fail", "%s : %s" % (rel, e)
        try:
            data = _direct.fetch(rel)
            via_direct = True
        except Exception as e2:
            return "fail", "%s : mirror(%s) / direct(%s)" % (rel, e, e2)
    if not data:
        return "fail", "%s : empty" % rel
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dst)
    return ("ok_direct" if via_direct else "ok"), rel


def _download_many(rels, overwrite, label, workers=config.DOWNLOAD_WORKERS):
    rels = list(rels)
    total = len(rels)
    if not total:
        return 0, 0
    ok = fail = direct = 0
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_download_one, r, overwrite) for r in rels]
        for fu in concurrent.futures.as_completed(futs):
            status, info = fu.result()
            done += 1
            if status in ("ok", "ok_direct"):
                ok += 1
                if status == "ok_direct":
                    direct += 1
            elif status == "fail":
                fail += 1
                print("    ! failed:", info)
            if done % 100 == 0 or done == total:
                extra = (" / via official CDN %d" % direct) if direct else ""
                print("    %s %d/%d (ok %d / failed %d%s)" % (label, done, total, ok, fail, extra))
    return ok, fail


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------
def needed_assets(unique_ids, tactics_ids=()):
    """Set of remote-relative paths this toolset needs (card icons + tactics icons + watermark)."""
    rels = set(CARD_ICON_REL % u for u in unique_ids)
    rels.update(TACTICS_ICON_REL % u for u in tactics_ids)
    rels.add(WATERMARK_REL)
    return rels


def sync(unique_ids, tactics_ids=()):
    """Sync assets. unique_ids: iterable of card uniqueId; tactics_ids: iterable of
    tactics uniqueId. Returns True if the update check ran online."""
    needed = needed_assets(unique_ids, tactics_ids)
    state = _load_state()
    baseline = state.get("baseline")

    print("== asset sync == (local mirror: %s)" % LOCAL_DIR)
    try:
        updates = _list_updates()
        online = True
    except Exception as e:                        # noqa: BLE001
        print("WARN: cannot reach /update (offline?): %s\n  Skipping update check, "
              "continuing with existing local assets." % e)
        updates, online = [], False

    latest = updates[-1] if updates else baseline

    # 1) Incremental update: apply records newer than baseline, re-downloading only
    #    the assets we use that actually changed.
    if online and baseline and latest and latest != baseline:
        new_updates = [u for u in updates if u > baseline]
        print("Found %d new update record(s) (latest %s); reading change lists..."
              % (len(new_updates), latest))
        try:
            changed = _changed_files(new_updates)
        except Exception as e:                    # noqa: BLE001
            print("WARN: failed to read update records: %s" % e)
            changed = set()
        refresh = changed & needed
        if refresh:
            print("  %d changed asset(s) used by this tool; re-downloading..." % len(refresh))
            _download_many(refresh, overwrite=True, label="update")
        else:
            print("  This update does not touch any asset used by this tool.")
    elif online and not baseline:
        print("First sync: will download every needed asset.")

    # 2) Fill missing (first run = full; later = new-card gap-fill) -- missing only
    missing = [r for r in needed if not _exists_ok(r)]
    if missing:
        if online:
            print("Downloading %d missing asset(s)..." % len(missing))
            ok, fail = _download_many(missing, overwrite=False, label="download")
            if fail:
                print("  Note: %d asset(s) failed; re-run to retry." % fail)
        else:
            print("WARN: offline and %d asset(s) missing; those card images won't show." % len(missing))
    else:
        print("All needed assets present (%d item(s))." % len(needed))

    # 3) Advance the version record
    if online and latest:
        state["baseline"] = latest
        _save_state(state)
        print("Asset version record: baseline = %s" % latest)

    return online


# Run directly: resolve needed uniqueIds from masterdata and sync
def _all_unique_ids():
    from generate_card_list import build_lookups, build_entries
    data = build_lookups()
    entries = build_entries(*data)
    return {e["uniqueId"] for e in entries}


def _all_tactics_ids():
    from generate_tactics_list import load_tactics
    return {x["uniqueId"] for x in load_tactics()}


if __name__ == "__main__":
    sync(_all_unique_ids(), _all_tactics_ids())
