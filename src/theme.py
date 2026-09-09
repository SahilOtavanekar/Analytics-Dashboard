"""Palette, derived from demandai.net/insyte and validated rather than eyeballed.

The site's own tokens, read off the live page rather than sampled by eye:

    --t #056e6e   --t2 #4fb0b0   --tbright #22c7b8
    --ink #0b1a1a --muted #546a6a --line #e6ecec   Inter, white surfaces

Two computed findings shaped everything below.

**The brand teal cannot be a series colour.** At hue 195 sRGB runs out of gamut
around chroma 0.09, so #056e6e sits under the categorical chroma floor and reads
grey beside other hues. It is used where a single colour carries identity - UI
chrome and the one-series bar fill - while the categorical set starts from a
validated teal in the same family.

**Holding lightness constant breaks colour-blind separation.** A first attempt put
all eight hues at one lightness for even visual weight; deutan separation between
green and red collapsed to dE 0.9. Hue alone is no cue for those readers, so
lightness is staggered below and confusable pairs differ in both.

Both palettes pass the lightness band, chroma floor, CVD separation and
normal-vision checks. Each carries one sub-3:1 contrast slot against its surface,
permitted only because every chart using these colours ships a legend, direct
labels and a table view - that relief is a requirement, not a nicety.
"""

import streamlit as st

# --------------------------------------------------------------------- brand
BRAND_TEAL = "#056e6e"      # --t       primary; 6.07:1 under white text
BRAND_AQUA = "#22c7b8"      # --tbright accent; 2.11:1 - never carries text
INK = "#0b1a1a"             # --ink

# ----------------------------------------------------------------- categorical
# Slot order is fixed and interleaved by hue so adjacent series - the ones a reader
# compares first - are furthest apart. Never reordered by rank.
# validate_palette.js --mode light: CVD dE 12.7, normal 27.7, chroma and band pass.
CATEGORICAL_LIGHT = [
    "#1d9485",  # teal (brand family)
    "#913797",  # magenta
    "#a9881b",  # olive
    "#0d53af",  # blue
    "#c43d4d",  # red
    "#41ba5d",  # green
    "#7455c7",  # violet
    "#c66a19",  # orange
]

# Re-selected against the dark surface, not flipped: the dark band is L 0.48-0.67
# against light's 0.43-0.77, so the stagger compresses and every step moves.
# validate_palette.js --mode dark: CVD dE 10.5, normal 27.8.
CATEGORICAL_DARK = [
    "#1baa9e",
    "#973d9d",
    "#a38213",
    "#145ec1",
    "#b93345",
    "#29a84c",
    "#7658cb",
    # Darkened one step from #bf6512, where neither white (4.13:1) nor ink (4.32:1)
    # cleared 4.5 - that slot had no legible on-fill label at all.
    "#ae5b0f",
]

# The one-series fill. Brand teal on light; on dark it must clear the surface
# rather than white text, so the accent aqua takes over (8.6:1 against #0c1313).
SERIES_LIGHT = BRAND_TEAL
SERIES_DARK = BRAND_AQUA

SURFACE_LIGHT = "#ffffff"
SURFACE_DARK = "#0c1313"


def _is_dark() -> bool:
    """True when the viewer is in dark mode.

    st.context is unavailable outside a script run - plain imports, AppTest
    probes, notebooks - so this degrades to light rather than raising. Charts are
    built during a run on every real path.
    """
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return False


def categorical() -> list[str]:
    """Fixed-order categorical hues for the active mode."""
    return CATEGORICAL_DARK if _is_dark() else CATEGORICAL_LIGHT


def series_color() -> str:
    """Fill for single-series marks in the active mode."""
    return SERIES_DARK if _is_dark() else SERIES_LIGHT


def surface() -> str:
    return SURFACE_DARK if _is_dark() else SURFACE_LIGHT


def positive_color() -> str:
    """Growth. The green slot, so it stays inside the validated set."""
    return categorical()[5]


def negative_color() -> str:
    """Decline. The red slot."""
    return categorical()[4]


def lum_of(hex_color: str) -> float:
    """WCAG relative luminance."""
    raw = hex_color.lstrip("#")
    channels = [int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def readable_on(hex_color: str) -> str:
    """Ink or white, whichever is legible on the given fill.

    Stacked segments print their share directly on the fill. Hard-coding white
    made the label unreadable on lighter slots - and worse, it forced the whole
    palette darker to compensate, which is what destroyed colour-blind separation
    in the first place. Choosing per fill decouples the two.
    """
    raw = hex_color.lstrip("#")
    channels = [int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    # Whichever of the two actually contrasts more, rather than "white unless it
    # fails". Thresholding with a fixed fallback can return the WORSE option on a
    # mid-lightness fill - which is how the dark orange slot ended up with a 4.13:1
    # label when 4.32:1 was available, and neither was good enough.
    on_white = 1.05 / (luminance + 0.05)
    on_ink = (luminance + 0.05) / (lum_of(INK) + 0.05)
    return "#ffffff" if on_white >= on_ink else INK


