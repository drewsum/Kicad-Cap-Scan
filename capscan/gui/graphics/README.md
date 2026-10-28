# Tooltip graphics

Drawings shown under tooltips that explain a visual idea (#16): a trace crossing a plane
split, a via changing reference plane, an edge rate. Every tooltip drawing lives here;
`capscan/gui/tooltip_art.py` renders them and `art_tip(text, name, *highlight)` makes the
tooltip. Both follow KiPI's (`kipi/gui/graphics/`); keep the conventions the same.

When you write a tooltip for a setting whose meaning is easier to see than to read (a
cross-section, a top view, a curve), add a drawing for it here.

Conventions:

- SVG, about 280 x 140 to 190 px (`width`/`height` on the `<svg>` element, matched by
  `viewBox`), `font-family="sans-serif"`, `font-size="11"`.
- Colours are tokens filled from the window's palette, never fixed colours, so a drawing
  matches the light or dark GUI:
  - `{bg}` the background, a shade darker than the window: start with
    `<rect ... rx="8" fill="{bg}"/>` over the whole drawing;
  - `{fg}` text and lines, `{muted}` axes and guides, `{grid}` faint fills,
    `{accent}` emphasis, `{warn}` a warning tint.
- Any other token (`{cap}`, `{gap}`, `{dist}`) names a part a tooltip can point at: drawn
  in `{accent}` when the tooltip passes its name in `highlight`, else in `{fg}`. One
  drawing can serve several settings that way.
- Each drawing must be used by a tooltip (`tests/gui/test_tooltip_art.py` checks).
