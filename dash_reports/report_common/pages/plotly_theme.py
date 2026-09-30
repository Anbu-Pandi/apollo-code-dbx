"""Central Plotly theme for dash_reports — the 'apollo' template.

One place to make every chart look consistent and clean, mirroring what
genie_admin.css does for the DOM. Importing this module registers the template
and sets it as the process-wide default, so any figure that doesn't pass its own
`template=` picks it up automatically (no per-chart edits). Palette matches
apollo_home.css so charts, tables and pages all agree.
"""

import plotly.graph_objects as go
import plotly.io as pio

# Apollo palette (from apollo_home.css)
GREEN = "#007B63"
SAXE = "#395878"
ORANGE = "#EC9A5F"
TEXT = "#1a1a1a"
MUTED = "#5a5d5f"
BORDER = "#E5E0DB"
GRID = "rgba(90,93,95,0.12)"
SANS = '"Segoe UI", Calibri, Helvetica, Arial, sans-serif'

# harmonious categorical sequence, green-led
APOLLO_COLORWAY = [GREEN, SAXE, ORANGE, "#5B9279", "#7BA7BF", "#C99A2E",
                   "#9B6A9E", "#C0392B", "#2E86AB", "#8A8D8F"]

# green -> saxe continuous scale for heatmaps/continuous
APOLLO_COLORSCALE = [[0.0, "#EDF5F3"], [0.5, "#5B9279"], [1.0, GREEN]]


def _build_template() -> go.layout.Template:
    axis = dict(
        gridcolor=GRID, griddash="solid", zeroline=False,
        linecolor=BORDER, linewidth=1, ticks="outside", tickcolor=BORDER,
        tickfont=dict(color=MUTED, size=11),
        title=dict(font=dict(color=MUTED, size=12)),
        automargin=True,
    )
    return go.layout.Template(
        layout=dict(
            font=dict(family=SANS, color=TEXT, size=12),
            paper_bgcolor="#FFFFFF",
            plot_bgcolor="#FFFFFF",
            colorway=APOLLO_COLORWAY,
            colorscale=dict(sequential=APOLLO_COLORSCALE),
            xaxis=axis, yaxis=axis,
            title=dict(font=dict(color=TEXT, size=16), x=0.01, xanchor="left"),
            legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=MUTED, size=11),
                        title=dict(font=dict(color=MUTED, size=11))),
            hoverlabel=dict(bgcolor="#FFFFFF", bordercolor=BORDER,
                            font=dict(family=SANS, color=TEXT, size=12)),
            margin=dict(l=56, r=24, t=48, b=48),
            coloraxis=dict(colorbar=dict(outlinewidth=0, tickfont=dict(color=MUTED))),
        )
    )


def register(set_default: bool = True) -> None:
    """Register the 'apollo' template and (default) make it the process default."""
    pio.templates["apollo"] = _build_template()
    if set_default:
        # layer on plotly_white so structural defaults are sane, apollo wins
        pio.templates.default = "plotly_white+apollo"


# self-register on import
register()
