"""Shared dark/light theme kit for every dash_reports rendering surface.

There is no single HTML shell across dash_reports: the main Dash app and each
standalone Flask sub-server (Argus Wiki reader, MCP guide, pred-tracker,
html-sub-server, industry-tracker) each build their own ``<head>``. This module
is the ONE source of truth for the theme toggle so all of them behave the same:

  * a floating toggle button, fixed bottom-right, on every page;
  * theme resolved as ``localStorage['apollo-theme']`` else the OS preference
    (``prefers-color-scheme``) — i.e. "follow OS" until the user chooses;
  * the choice is stored in ``localStorage`` and, because every surface is served
    same-origin behind the reverse proxy, it is SHARED across all of them and
    synced live across tabs via the ``storage`` event;
  * a complete ``html[data-theme="dark"]`` stylesheet that flips the CSS custom
    properties every surface already uses (``--apollo-*`` and the semantic
    ``--bg``/``--surface``/``--text`` set), plus targeted overrides for the
    handful of hardcoded light colors;
  * a client-side Plotly recolor shim so server-rendered charts (which bake in a
    white ``paper_bgcolor``) follow the theme without a server round-trip.

Usage
-----
Pure strings, no Dash/Flask import — safe to use from any process.

  * Dash main app: interpolate :func:`head_tags` into ``app.index_string``
    (after the ``{%css%}`` placeholder so the dark rules win).
  * Flask sub-servers: drop ``{theme_head}`` into their ``<head>`` f-string,
    with ``theme_head = theme.head_tags()`` computed first.

The snippet is self-contained (inline ``<style>`` + ``<script>``) so it needs no
extra asset route — important because the sub-servers run as separate processes.
"""

# Dark palette — mirrors argus_wiki/themes/apollo.css so every surface agrees.
_DARK_TOKENS = {
    # semantic tokens (apollo_home.css, genie_admin.css, data_catalog.css, wiki)
    "--bg": "#0E1311",
    "--surface": "#161B1A",
    "--bg-elev": "#1B2120",
    "--border": "#2A302E",
    "--border-light": "#1F2624",
    "--border-strong": "#353C3A",
    "--text": "#E8E8E6",
    "--text-secondary": "#A5A8A8",
    "--text-muted": "#767A79",
    "--header-bg": "#1A201E",
    "--section-bg": "#1E2A28",
    "--summary-bg": "#181E1C",
    "--row-hover": "#1F2826",
    "--light-grey": "#1B2120",
    # styles.css --apollo-* aliases (main Dash app)
    "--apollo-bg": "#0E1311",
    "--apollo-surface": "#161B1A",
    "--apollo-section-bg": "#1E2A28",
    "--apollo-row-hover": "#1F2826",
    "--apollo-border": "#2A302E",
    "--apollo-border-light": "#1F2624",
    "--apollo-text": "#E8E8E6",
    "--apollo-text-muted": "#A5A8A8",
    # accents lifted slightly so Apollo green pops on a dark surface
    "--apollo-green": "#2DBDA0",
    "--apollo-green-dark": "#25A98F",
    "--apollo-green-light": "#3FD3B6",
    "--accent": "#2DBDA0",
    "--positive": "#2DBDA0",
}

# Chart colors handed to the Plotly relayout shim (kept in sync with the CSS).
_PLOTLY_DARK = {
    "paper": "#161B1A", "plot": "#161B1A", "font": "#E8E8E6",
    "grid": "rgba(255,255,255,0.08)", "line": "#353C3A", "muted": "#A5A8A8",
}
_PLOTLY_LIGHT = {
    "paper": "#FFFFFF", "plot": "#FFFFFF", "font": "#1a1a1a",
    "grid": "rgba(90,93,95,0.12)", "line": "#E5E0DB", "muted": "#5a5d5f",
}


def _tokens_css() -> str:
    body = "".join(f"{k}:{v};" for k, v in _DARK_TOKENS.items())
    return f"html[data-theme=\"dark\"]{{{body}color-scheme:dark;}}"


# Targeted overrides for hardcoded (non-token) light colors across surfaces.
# Selectors are prefixed with html[data-theme="dark"] so they only bite in dark
# mode and always out-specify the light rules regardless of load order. Classes
# that don't exist on a given surface are simply inert there.
_DARK_OVERRIDES = """
html[data-theme="dark"] body{background:var(--apollo-bg,#0E1311);color:var(--apollo-text,#E8E8E6);}
html[data-theme="dark"] a:link,html[data-theme="dark"] a:visited{color:#2DBDA0;}
html[data-theme="dark"] a:hover,html[data-theme="dark"] a:active{color:#3FD3B6;}
/* main Dash app: report "pages" hardcode a white sheet + light shadow */
html[data-theme="dark"] .page,
html[data-theme="dark"] .sankey_page,
html[data-theme="dark"] .pagetwo{background:var(--apollo-surface,#161B1A);box-shadow:0 0 10px rgba(0,0,0,.5);border-color:var(--apollo-border,#2A302E);}
html[data-theme="dark"] .modal{--bs-modal-bg:#161B1A;--bs-modal-color:#E8E8E6;}
html[data-theme="dark"] .btn-close{filter:invert(1) grayscale(100%) brightness(200%);}
html[data-theme="dark"] ._dash-loading-callback{background-color:rgba(14,19,17,0.6)!important;}
/* Dash DataTable. The VC sector heatmap is exempt: its per-cell gradient is a
   theme-aware inline color-mix() that this blanket !important would erase. */
html[data-theme="dark"] .dash-spreadsheet-container .dash-spreadsheet-inner td:not(#vc-heatmap-table td),
html[data-theme="dark"] .dash-spreadsheet-container .dash-spreadsheet-inner th{background:var(--apollo-surface,#161B1A)!important;color:var(--apollo-text,#E8E8E6)!important;}
/* html-sub-server directory browser (colors hardcoded in its own stylesheet) */
html[data-theme="dark"] .wrap{background:#161B1A!important;border-color:#2A302E!important;}
html[data-theme="dark"] .head{border-bottom-color:#1F2624!important;}
html[data-theme="dark"] .row{color:#E8E8E6!important;}
html[data-theme="dark"] .row:hover{background:#1F2826!important;}
/* pred-tracker + mcp guide single pages: neutralize the hardcoded white body.
   Their inner cards mostly inherit; a QA pass may add more per-class rules. */
html[data-theme="dark"] .container,
html[data-theme="dark"] .card,
html[data-theme="dark"] .panel{background:#161B1A!important;color:#E8E8E6!important;border-color:#2A302E!important;}
"""

_BUTTON_CSS = """
.apollo-theme-toggle{position:fixed;right:16px;bottom:16px;z-index:2147483000;width:38px;height:38px;
border-radius:50%;border:1px solid var(--apollo-border,#CAC2BA);background:var(--apollo-surface,#FFFFFF);
color:var(--apollo-text,#1a1a1a);cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.18);display:flex;
align-items:center;justify-content:center;font-size:17px;line-height:1;padding:0;
transition:background .15s ease,color .15s ease,border-color .15s ease,transform .1s ease;}
.apollo-theme-toggle:hover{border-color:var(--apollo-green,#007B63);color:var(--apollo-green,#007B63);}
.apollo-theme-toggle:active{transform:scale(.92);}
@media print{.apollo-theme-toggle{display:none!important;}}
"""

# Client script. Runs inline in <head> so data-theme is set before first paint
# (no flash). Guards every browser API so it is safe on old/edge renderers.
_JS = """
(function(){
  var KEY='apollo-theme';
  function osDark(){try{return window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches;}catch(e){return false;}}
  function stored(){try{var v=localStorage.getItem(KEY);return (v==='light'||v==='dark')?v:null;}catch(e){return null;}}
  function resolve(){return stored()||(osDark()?'dark':'light');}
  function current(){return document.documentElement.getAttribute('data-theme')||resolve();}
  var PD=__PLOTLY_DARK__, PL=__PLOTLY_LIGHT__;
  var raf=false;
  function recolor(t){
    if(!window.Plotly||!window.Plotly.relayout)return;
    var d=(t==='dark'), c=d?PD:PL;
    var upd={'paper_bgcolor':c.paper,'plot_bgcolor':c.plot,'font.color':c.font,
      'xaxis.gridcolor':c.grid,'yaxis.gridcolor':c.grid,'xaxis.linecolor':c.line,'yaxis.linecolor':c.line,
      'xaxis.tickcolor':c.line,'yaxis.tickcolor':c.line,'xaxis.zerolinecolor':c.grid,'yaxis.zerolinecolor':c.grid,
      'legend.font.color':c.muted};
    var gds=document.querySelectorAll('.js-plotly-plot');
    for(var i=0;i<gds.length;i++){var gd=gds[i];if(gd._apolloTheme===t)continue;
      try{window.Plotly.relayout(gd,upd);gd._apolloTheme=t;}catch(e){}}
  }
  function scheduleRecolor(){if(raf)return;raf=true;
    (window.requestAnimationFrame||function(f){setTimeout(f,16);})(function(){raf=false;recolor(current());});}
  function apply(t){
    document.documentElement.setAttribute('data-theme',t);
    var btn=document.getElementById('apollo-theme-toggle');
    if(btn){var lbl=(t==='dark')?'Switch to light theme':'Switch to dark theme';
      btn.setAttribute('aria-label',lbl);btn.title=lbl;
      var ic=btn.firstChild;if(ic)ic.textContent=(t==='dark')?'\\u2600\\uFE0E':'\\u263D';}
    recolor(t);
  }
  apply(resolve()); // pre-paint, before <body> exists
  function toggle(){
    var next=(current()==='dark')?'light':'dark';
    try{localStorage.setItem(KEY,next);}catch(e){}
    apply(next);
  }
  function mkButton(){
    if(!document.body||document.getElementById('apollo-theme-toggle'))return;
    var b=document.createElement('button');
    b.id='apollo-theme-toggle';b.type='button';b.className='apollo-theme-toggle';
    var s=document.createElement('span');b.appendChild(s);
    b.addEventListener('click',toggle);
    document.body.appendChild(b);
    apply(current());
  }
  function onReady(){mkButton();scheduleRecolor();}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',onReady);
  else onReady();
  // OS change only steers us while the user has made no explicit choice.
  try{var mq=window.matchMedia('(prefers-color-scheme: dark)');
    var h=function(){if(!stored())apply(resolve());};
    if(mq.addEventListener)mq.addEventListener('change',h);else if(mq.addListener)mq.addListener(h);}catch(e){}
  // Cross-surface / cross-tab sync (same-origin localStorage).
  window.addEventListener('storage',function(e){if(e.key===KEY)apply(resolve());});
  // Dash swaps page content and renders charts via callbacks; keep the button
  // alive and recolor freshly-drawn plots (debounced, skips already-correct ones).
  try{var mo=new MutationObserver(function(){mkButton();scheduleRecolor();});
    mo.observe(document.documentElement,{childList:true,subtree:true});}catch(e){}
})();
"""


def _js() -> str:
    import json
    return (
        _JS.replace("__PLOTLY_DARK__", json.dumps(_PLOTLY_DARK))
           .replace("__PLOTLY_LIGHT__", json.dumps(_PLOTLY_LIGHT))
    )


def head_tags() -> str:
    """Return the inline ``<style>`` + ``<script>`` to drop into any ``<head>``.

    Idempotent and self-contained. Place it AFTER a surface's own stylesheet so
    the dark overrides win (they also out-specify via ``html[data-theme]``).
    """
    css = _BUTTON_CSS + _tokens_css() + _DARK_OVERRIDES
    return f"<style>{css}</style>\n<script>{_js()}</script>"


def dash_index_string() -> str:
    """A full Dash ``index_string`` with the theme kit wired into ``<head>``.

    Mirrors Dash's default template and appends :func:`head_tags` after the
    ``{%css%}`` placeholder so asset stylesheets load first and the dark rules
    override them.
    """
    return (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        "{%metas%}\n<title>{%title%}</title>\n{%favicon%}\n{%css%}\n"
        + head_tags()
        + "\n</head>\n<body>\n{%app_entry%}\n<footer>\n{%config%}\n{%scripts%}\n"
        "{%renderer%}\n</footer>\n</body>\n</html>"
    )
