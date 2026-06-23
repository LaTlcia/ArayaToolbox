# -*- coding: utf-8 -*-
import os as _os, sys as _sys

_ROOT = _os.path.dirname(_os.path.abspath(__file__))
_METHOD = _os.path.join(_ROOT, "method")
for _p in (_ROOT, _METHOD):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import config
import masterdata_sync
import assets_sync
import generate_card_list
import generate_deck_builder
import generate_tactics_list


def main():
    print("=== ArayaToolbox build ===")
    print("language: %s (%s)  |  set ARAYA_LANG=cn/jp/en or edit config.LANGUAGE"
          % (config.LANGUAGE, config.t("_meta.name", config.LANGUAGE)))

    print("\n=== sync masterdata ===")

    masterdata_sync.sync()

    print("\n=== sync assets ===")
    data = generate_card_list.build_lookups()
    entries = generate_card_list.build_entries(*data)
    tactics_ids = {x["uniqueId"] for x in generate_tactics_list.load_tactics()}
    assets_sync.sync({e["uniqueId"] for e in entries}, tactics_ids)

    print("\n=== build card list ===")
    generate_card_list.main()
    print("\n=== build deck builder ===")
    generate_deck_builder.main()
    print("\n=== build tactics list ===")
    generate_tactics_list.main()
    print("\nAll done.")


if __name__ == "__main__":
    main()
