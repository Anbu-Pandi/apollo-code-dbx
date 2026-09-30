"""VC Dashboard — Funding / Deals / News / Sources.

See docs/vc_dashboard/plan.md for the full design. Data lives in the ``vc_*`` external
Delta tables (``data_access.dbtables.vc``) + an ES ``vc_news`` index; served through the
thin ``vc_server`` Flight sub-server with a direct-query fallback.
"""
