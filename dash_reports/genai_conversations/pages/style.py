"""Conversation pages reuse the shared Genie Admin UI kit (class-based, styled by
assets/genie_admin.css). Thin re-export so pages import from one place."""

from dash_reports.report_common.pages.genie_ui import (  # noqa: F401
    ACCENT, BLUE, BORDER, GREEN, GREEN_DARK, INK, MUTED, ORANGE, PANEL, PANEL2,
    SECTION_BG, SERIES, SEVERITY_COLOR, SURFACE, TEXT,
    badge, cls_chip, eyebrow, kpi_card, note, panel, s, sev_chip, shell,
    style_fig, subnav,
)
