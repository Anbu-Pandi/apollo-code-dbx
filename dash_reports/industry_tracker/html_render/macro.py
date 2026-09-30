def render() -> str:
    return """<section class="tab-section" id="tab-macro" hidden>
    <div class="subtitle-bar">ALTD macro overview &mdash; placeholder</div>
    <div class="placeholder">
        <h2>Macro Overview Coming Soon</h2>
        <p>This tab will host the ALTD macro charts (consumer sentiment, retail sales, FX baskets, ALTD card-spend nowcasts).</p>
        <p style="margin-top:8px;color:var(--grey);font-size:11px">
            Wire ALTD macro feeds into <code>data_loader.refresh_industry_data</code> to populate.
        </p>
    </div>
</section>"""
