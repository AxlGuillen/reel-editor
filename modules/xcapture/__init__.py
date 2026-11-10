"""Tweet captures from x.com to use as assets in the videos (reactions, previews).

    python -m modules.xcapture <url|id> [<url|id> ...] --out <folder>

For each tweet it reads the public embed data (no login), draws the card in X's dark theme in headless Edge and
saves a transparent PNG; a `galeria.html` in the same folder lets you pick which ones to keep.
"""
