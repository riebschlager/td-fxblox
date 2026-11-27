"""Shared color data for the FX Blox builder."""

# Five 8-bit RGB stops per palette, in menu order. The Matplotlib maps are sampled
# at quarter intervals. ColorBrewer maps use its published five-class colors, with
# the two sequential maps ordered from dark to light.
# https://matplotlib.org/stable/gallery/color/colormap_reference.html
# https://colorbrewer2.org/export/colorbrewer.json
PALETTE_HEX = {
    "Viridis":   "440154 3b528b 21918c 5ec962 fde725",
    "Plasma":    "0d0887 7e03a8 cc4778 f89540 f0f921",
    "Inferno":   "000004 57106e bc3754 f98e09 fcffa4",
    "Magma":     "000004 51127c b73779 fc8961 fcfdbf",
    "Cividis":   "00224e 434e6c 7d7c78 bcae6c fee838",
    "Turbo":     "30123b 28bceb a4fc3c fb7e21 7a0403",
    "Spectral":  "d7191c fdae61 ffffbf abdda4 2b83ba",
    "RdBu":      "ca0020 f4a582 f7f7f7 92c5de 0571b0",
    "PuOr":      "e66101 fdb863 f7f7f7 b2abd2 5e3c99",
    "PiYG":      "d01c8b f1b6da f7f7f7 b8e186 4dac26",
    "YlGnBu":    "253494 2c7fb8 41b6c4 a1dab4 ffffcc",
    "YlOrRd":    "bd0026 f03b20 fd8d3c fecc5c ffffb2",
}

# Original brand swatches and their standard chromatic order.
SWATCH_HEX = {
    "blue": "2573ff",
    "pink": "ff8dff",
    "yellow": "f3dd3e",
    "red": "ff424d",
    "cyan": "44f8ff",
    "white": "ffffff",
    "black": "000000",
}
BRAND_COLORS = ("blue", "pink", "yellow", "red", "cyan")
PRISM_SILVER = (0.92, 0.95, 1.0)

PALETTE_LABELS = {label.lower(): label for label in PALETTE_HEX}
if not PALETTE_LABELS or len(PALETTE_LABELS) != len(PALETTE_HEX):
    raise ValueError("Define at least one palette and give each palette a unique name.")
PALETTE_INDEX = {name: index for index, name in enumerate(PALETTE_LABELS)}
PALETTE_RGB8 = {
    label.lower(): tuple(bytes.fromhex(color) for color in row.split())
    for label, row in PALETTE_HEX.items()
}
PALETTE_RGB = {
    name: tuple(tuple(channel / 255 for channel in color) for color in row)
    for name, row in PALETTE_RGB8.items()
}
SWATCH_RGB8 = {name: bytes.fromhex(color) for name, color in SWATCH_HEX.items()}
SWATCH_RGB = {
    name: tuple(channel / 255 for channel in color)
    for name, color in SWATCH_RGB8.items()
}
# The layout effects use five color slots.
STOP_COUNT = 5
for name, row in PALETTE_RGB8.items():
    if len(row) != STOP_COUNT or len(set(row)) != STOP_COUNT or any(len(color) != 3 for color in row):
        raise ValueError(f"{name} must have five distinct 8-bit RGB stops.")

# Accent colors stay visible over black ink and shadows.
PALETTE_HIGHLIGHT_STOPS = {
    name: max(range(STOP_COUNT), key=lambda i: sum(
        channel * weight for channel, weight in zip(row[i], (0.2126, 0.7152, 0.0722))
    ))
    for name, row in PALETTE_RGB8.items()
}
PALETTE_HIGHLIGHTS = {
    name: PALETTE_RGB[name][stop] for name, stop in PALETTE_HIGHLIGHT_STOPS.items()
}
