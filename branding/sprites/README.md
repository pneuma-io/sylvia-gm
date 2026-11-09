# Sylvia's tutorial artwork

These are the four complete approved transparent sheets supplied for this work on 9 November 2026.
Each original is preserved at 1448 × 1086 in RGBA, with fifteen poses. Original filenames and exact
source and derivative hashes are recorded in provenance.json. The opaque and earlier source
variants remain with the supplied provenance collection; they are not the app's artwork inputs.

The app uses selected existing poses from all four sheets. Run this from the repository root:

    uv run --with pillow python scripts/extract_tutorial_sprites.py

This reproduces the PNGs and their manifest. Extraction uses original pixels without redrawing or
resizing them. Component masks separate irregular silhouettes; authored effect regions retain each
pose's own sparkles, gesture marks and thought bubbles without borrowing a neighbour's effects.
Six transparent pixels surround the extracted bounds. The complete originals remain untouched.

Runtime crops live in src-tauri/frontend/assets/tutorial/ and are explicitly included in
src-tauri/webui-data-files.json. The full source sheets are retained here for future expression
choices and are not required in the app bundle.
