import html
from urllib.parse import quote


def error_page(message: str, prefix: str, view='default') -> str:
    home = html.escape(f'{prefix}/{quote(view, safe="")}', quote=True)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Unable to load this page</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;background:#f5f7f6;color:#243932;margin:0;padding:8vh 24px}}
main{{max-width:640px;margin:auto;background:white;border:1px solid #d5dfdb;padding:32px;border-radius:12px}}
nav{{display:flex;gap:16px;flex-wrap:wrap}}a{{color:#007b63}}h1{{font-size:24px}}</style></head>
<body><main><h1>Unable to load this page</h1><p role="alert">{html.escape(message)}</p>
<nav aria-label="Recovery"><a href="{home}" onclick="if(history.length > 1){{history.back();return false}}">Go back</a>
<a href="{home}">Home</a><a href="" onclick="location.reload();return false">Retry page</a></nav>
</main></body></html>'''
