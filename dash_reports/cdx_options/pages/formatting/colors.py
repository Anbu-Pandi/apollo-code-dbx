import pandas as pd


GRAD_GREEN_RED = [
    (0.0, (99, 190, 123)),
    (0.5, (255, 255, 255)),
    (1.0, (248, 105, 107)),
]

GRAD_RED_GREEN = [
    (0.0, (248, 105, 107)),
    (0.5, (255, 255, 255)),
    (1.0, (99, 190, 123)),
]

GRAD_BLUE_RED = [
    (0.0, (91, 155, 213)),
    (0.5, (255, 255, 255)),
    (1.0, (248, 105, 107)),
]

GRAD_WHITE_GREEN = [
    (0.0, (255, 255, 255)),
    (1.0, (99, 190, 123)),
]

GRAD_WHITE_RED = [
    (0.0, (255, 255, 255)),
    (1.0, (248, 105, 107)),
]


def lerp_color(palette, ratio):
    ratio = max(0.0, min(1.0, ratio))
    for i in range(len(palette) - 1):
        lo_r, lo_c = palette[i]
        hi_r, hi_c = palette[i + 1]
        if ratio <= hi_r:
            t = (ratio - lo_r) / max(hi_r - lo_r, 1e-9)
            r = int(lo_c[0] + (hi_c[0] - lo_c[0]) * t)
            g = int(lo_c[1] + (hi_c[1] - lo_c[1]) * t)
            b = int(lo_c[2] + (hi_c[2] - lo_c[2]) * t)
            return f'rgb({r},{g},{b})'
    return f'rgb({palette[-1][1][0]},{palette[-1][1][1]},{palette[-1][1][2]})'


def rgb_gradient(val, vmin, vmax, palette=None):
    palette = palette or GRAD_RED_GREEN
    if pd.isna(val):
        return None
    if vmax == vmin:
        return lerp_color(palette, 0.5)
    ratio = (val - vmin) / (vmax - vmin)
    return lerp_color(palette, ratio)


def text_color_for_bg(bg_str):
    if bg_str is None:
        return '#222'
    try:
        nums = bg_str.replace('rgb(', '').replace(')', '').split(',')
        r, g, b = int(nums[0]), int(nums[1]), int(nums[2])
    except Exception:
        return '#222'
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return '#222' if lum > 150 else '#fff'
