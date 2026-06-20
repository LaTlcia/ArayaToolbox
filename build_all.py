# -*- coding: utf-8 -*-
"""
Rebuild all HTML in one shot
============================
Regenerates, in order:
  * card_list.html     -- card list (generate_card_list.py)
  * deck_builder.html  -- deck builder (generate_deck_builder.py)

After a data update, just run this script to refresh both pages:
    conda run -n localdb python localDB/build_all.py

It first syncs assets (full download the first time, deltas only afterwards) so the
pages work offline.
"""

import assets_sync
import generate_card_list
import generate_deck_builder


def main():
    print("=== sync assets ===")
    data = generate_card_list.build_lookups()
    entries = generate_card_list.build_entries(*data)
    assets_sync.sync({e["uniqueId"] for e in entries})

    print("\n=== build card list ===")
    generate_card_list.main()
    print("\n=== build deck builder ===")
    generate_deck_builder.main()
    print("\nAll done.")


if __name__ == "__main__":
    main()
