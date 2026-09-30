from __future__ import annotations

import logging
from pathlib import Path
import threading
import time

from flask import Response, has_request_context, jsonify, redirect, request, send_file

from argus_wiki.bootstrap import dimension_registry
from argus_wiki.render import feed as feed_mod
from dash_reports.sub_servers.argus_wiki_server.constants import URL_PATH
from dash_reports.sub_servers.argus_wiki_server.config_catalog import prewarm_relpaths
from dash_reports.sub_servers.argus_wiki_server import config as wiki_cfg
from dash_reports.sub_servers.argus_wiki_server import clicks
import dash_reports.sub_servers.argus_wiki_server.reader as reader

_WIKI_DOC_CACHE_CONTROL = "no-store, no-cache, must-revalidate, max-age=0"
_PLOTLY_ASSET = (
    Path(__file__).resolve().parents[3]
    / "argus_wiki"
    / "render"
    / "assets"
    / "plotly-basic-3.6.0.min.js"
)


def wiki_path_uses_static_cache(path: str) -> bool:
    base = URL_PATH.rstrip("/") or URL_PATH
    if path != base and not path.startswith(base + "/"):
        return False
    tail = path[len(base):].lstrip("/")
    parts = [p for p in tail.split("/") if p] if tail else []
    if tail == "_theme.css":
        return True
    if tail == "_assets/plotly-basic-3.6.0.min.js":
        return True
    if len(parts) >= 2 and parts[1] == "asset":
        return True
    if parts == ["raw"]:
        return True
    return False


def html_response(body: str, status: int = 200) -> Response:
    return Response(body, status=status, content_type="text/html; charset=utf-8")


def text_response(body: str, status: int = 200) -> Response:
    return Response(body, status=status, content_type="text/plain; charset=utf-8")


def _plotly_response() -> Response:
    if _PLOTLY_ASSET.is_file():
        return send_file(_PLOTLY_ASSET, mimetype="application/javascript", conditional=True)
    from plotly.offline import get_plotlyjs

    return Response(get_plotlyjs(), content_type="application/javascript; charset=utf-8")


def not_found(message: str) -> Response:
    return _error_response(message, 404)


def unavailable(message: str) -> Response:
    return _error_response(message, 503)


def _error_response(message: str, status: int) -> Response:
    from dash_reports.sub_servers.argus_wiki_server.recovery import error_page
    view = (request.view_args or {}).get('view', 'default') if has_request_context() else 'default'
    return html_response(error_page(message, URL_PATH, view), status)


def _route(kind: str, **params) -> str:
    return dimension_registry.route_for(kind, prefix=URL_PATH, **params)


def _alias_redirect(view: str, base_kind: str, current: str) -> str | None:
    """Return a 302 target when the leading segment of `current` matches a
    registered page-dir alias, else None.
    """
    aliases = dimension_registry.page_dir_aliases()
    if not aliases:
        return None
    text = current.replace("\\", "/").strip("/")
    if not text:
        return None
    head, _, tail = text.partition("/")
    target = aliases.get(head)
    if not target:
        return None
    new_path = f"{target}/{tail}" if tail else target
    if base_kind == "page":
        return _route("page", view=view, rel=new_path)
    return _route("folder", view=view, dir=new_path)


def _canonical_entity_redirect(view: str, current: str) -> str | None:
    """Redirect a configured entity alias even when its old page was retired.

    This is deliberately registry-driven and does not touch the vault.  It lets
    historical URLs survive entity merges while keeping business aliases in the
    wiki configuration rather than in the web server.
    """
    text = current.replace("\\", "/").strip("/")
    page_dir, separator, tail = text.partition("/")
    if not separator or not tail or "/" in tail:
        return None
    spec = dimension_registry.dimension_for_page_dir(page_dir)
    if spec is None or spec.type != "entity":
        return None

    from argus_wiki.entities import canonicalize
    from argus_wiki.render.entity_collection_pages import entity_filename_slug

    suffix = Path(tail).suffix
    stem = Path(tail).stem if suffix else tail
    label = stem.replace("_", " ")
    canonical = canonicalize.canonical_for(spec.name, label)
    if canonical == label:
        from argus_wiki.entities import entity_relationships

        canonical = entity_relationships.canonical_peer(spec.name, label) or canonical
    if not canonical:
        return None
    target_stem = entity_filename_slug(
        canonical,
        max_len=int(spec.raw.get("slug_max_len") or 80),
    )
    target = f"{page_dir}/{target_stem}{suffix}"
    if target.casefold() == text.casefold():
        return None
    return _route("page", view=view, rel=target)


def prewarm_all_views(render_hot_pages: bool = True) -> None:
    reader.ensure_views()

    # Build the manifest source-list index off the request path so the first
    # entity-page view doesn't pay the cold build synchronously.
    try:
        from argus_wiki.render import entity_source_sections

        entity_source_sections.warm_manifest_index()
    except Exception as exc:
        logging.warning("argus_wiki prewarm: manifest index warm unavailable: %s", exc)

    rels = prewarm_relpaths() if render_hot_pages else []

    def warm(view: reader.ViewState) -> None:
        started = time.time()
        logging.info("argus_wiki prewarm [%s]: building index ...", view.name)
        try:
            indexes = reader.build_indexes(view)
            lookup = indexes.get("lookup", {})
            notes = indexes.get("notes", [])
            logging.info("argus_wiki prewarm [%s]: indexed %d notes in %.1fs",
                         view.name, len(notes), time.time() - started)
            rendered = 0
            for rel in rels:
                note = view.notes_by_rel.get(rel)
                if note:
                    reader.render_cached(view, note, lookup)
                    rendered += 1
            if render_hot_pages:
                logging.info("argus_wiki prewarm [%s]: rendered %d hot pages", view.name, rendered)
            try:
                from argus_wiki.search import wiki_api

                from argus_wiki.index import elastic_search_index

                records = view.search_records or []
                if not elastic_search_index.enabled():
                    records = records or reader._search_records_from_notes(notes)
                    wiki_api.set_search_records(view.name, records)
                    wiki_api.prepare_search_records(records)
                if view.is_default:
                    reader._search_core(view.name, "credit")
                logging.info("argus_wiki prewarm [%s]: search cache loaded %d records",
                             view.name, len(records))
            except Exception as exc:
                logging.warning("argus_wiki prewarm [%s]: search cache unavailable: %s", view.name, exc)
            try:
                wiki_api.warm_semantic_indexes([view.name])
                logging.info("argus_wiki prewarm [%s]: semantic warm kicked", view.name)
            except Exception as exc:
                logging.warning("argus_wiki prewarm [%s]: semantic warm unavailable: %s", view.name, exc)
            try:
                from argus_wiki.index import reader_cache as rc

                rc.kick_refresh()
                logging.info("argus_wiki prewarm [%s]: reader_cache refresh kicked", view.name)
            except Exception as exc:
                logging.warning("argus_wiki prewarm [%s]: reader_cache kick unavailable: %s", view.name, exc)
        except OSError as exc:
            logging.warning("argus_wiki prewarm [%s] failed: %s", view.name, exc)

    # Eager-warm ES entity indexes for every view in the background. build_indexes
    # is ES-only now (no SMB vault walk), so warming macro/thematic is cheap and
    # kills the 50–75s cold first Today hit that used to block on sync ES load.
    # Delay the heavy default ES hydrate so the first post-restart Today paints
    # don't share the GIL with building ~30k Note objects.
    def _warm_all() -> None:
        delay = 60.0
        logging.info("argus_wiki prewarm: delaying index hydrate %.0fs for cold-request headroom", delay)
        time.sleep(delay)
        ordered = sorted(reader.VIEWS.values(), key=lambda v: (not v.is_default, v.name))
        for view in ordered:
            warm(view)

    threading.Thread(target=_warm_all, name="argus-wiki-prewarm", daemon=True).start()
    try:
        from argus_wiki.index import reader_cache as rc

        from argus_wiki.index import elastic_search_index

        rc.start_refresher(reader.refresh_reader_cache, hydrate=not elastic_search_index.enabled())
    except Exception as exc:
        logging.warning("argus_wiki prewarm: reader_cache refresher unavailable: %s", exc)


def setup_argus_wiki_routes(server) -> None:
    reader.discover_views()
    from argus_wiki.index import elastic_search_index

    if elastic_search_index.enabled():
        elastic_search_index.store().query_vectors_ready()

    @server.errorhandler(500)
    def wiki_load_error(error):
        if not request.path.startswith(URL_PATH + '/'):
            return error.get_response()
        if '/api/' in request.path:
            return jsonify({'error': 'Unable to load this data. Please retry.'}), 500
        return _error_response('This page could not be loaded. You can retry or return to browsing.', 500)

    # Relationship construction is deterministic and process-global. Hydrate it
    # before the listener is marked ready so the first company/entity request
    # never pays for thousands of provider edges.
    try:
        from argus_wiki.entities import entity_relationships

        started = time.time()
        entity_relationships.relationships()
        logging.info("argus_wiki relationship graph hydrated in %.2fs", time.time() - started)
    except Exception:
        logging.warning("argus_wiki relationship graph warm failed", exc_info=True)

    try:
        from argus_wiki.index import reader_cache as _rc

        _rc.set_persist(not elastic_search_index.enabled() and wiki_cfg.reader_cache_persist())
    except Exception:
        logging.warning("argus_wiki: could not set reader_cache persistence mode", exc_info=True)

    @server.after_request
    def _argus_wiki_cache_headers(response: Response):
        try:
            req_path = request.path or ""
        except RuntimeError:
            return response
        if not req_path.startswith(URL_PATH):
            return response
        if wiki_path_uses_static_cache(req_path):
            return response
        response.headers["Cache-Control"] = _WIKI_DOC_CACHE_CONTROL
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @server.route(URL_PATH, methods=["GET"])
    @server.route(f"{URL_PATH}/", methods=["GET"])
    def argus_wiki_home():
        default_name = dimension_registry.default_view_name()
        target = default_name if default_name in reader.VIEWS else (reader.VIEW_ORDER[0] if reader.VIEW_ORDER else default_name)
        return redirect(_route("page", view=target, rel=dimension_registry.home_page_rel()))

    @server.route(f"{URL_PATH}/views", methods=["GET"])
    def argus_wiki_views():
        return jsonify({"views": reader.VIEW_ORDER})

    @server.route(f"{URL_PATH}/_track", methods=["GET", "POST"])
    def argus_wiki_track():
        try:
            clicks.log_click(
                view=request.args.get("view", ""),
                to=request.args.get("to", ""),
                from_rel=request.args.get("from", ""),
                query=request.args.get("q", ""),
                title=request.args.get("title", ""),
            )
        except OSError as exc:
            logging.warning("argus_wiki click log failed: %s", exc)
        return ("", 204)

    @server.route(f"{URL_PATH}/raw", methods=["GET"])
    def argus_wiki_raw():
        try:
            return send_file(reader.raw_path(
                request.args.get("path", ""),
                ingest_source=request.args.get("ingest_source"),
            ))
        except FileNotFoundError:
            return not_found("Raw document not found")
        except OSError as exc:
            logging.warning("argus_wiki raw unavailable: %s", exc)
            return unavailable("Argus Wiki raw document unavailable")

    @server.route(f"{URL_PATH}/raw/open", methods=["GET"])
    def argus_wiki_raw_open():
        try:
            return html_response(reader.raw_preview(
                request.args.get("path", ""),
                ingest_source=request.args.get("ingest_source"),
            ))
        except FileNotFoundError:
            return not_found("Raw document not found")
        except UnicodeError as exc:
            logging.warning("argus_wiki raw preview decode failed: %s", exc)
            return unavailable("Argus Wiki raw preview unavailable")
        except OSError as exc:
            logging.warning("argus_wiki raw preview unavailable: %s", exc)
            return unavailable("Argus Wiki raw preview unavailable")

    @server.route(f"{URL_PATH}/_theme.css", methods=["GET"])
    def argus_wiki_theme_css():
        from argus_wiki.render import theme as theme_mod
        css = theme_mod.reload_theme()
        resp = Response(css, content_type="text/css; charset=utf-8")
        resp.headers["Cache-Control"] = "public, max-age=300"
        return resp

    @server.route(f"{URL_PATH}/_assets/plotly-basic-3.6.0.min.js", methods=["GET"])
    def argus_wiki_plotly_js():
        resp = _plotly_response()
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp

    @server.route(f"{URL_PATH}/_structured", methods=["GET"])
    def argus_wiki_structured_toggle():
        # Server-wide, in-memory toggle for the structured-data tiles (resets on restart).
        reader.set_structured_enabled(not reader.structured_enabled())
        nxt = request.args.get("next") or request.referrer or URL_PATH
        if not str(nxt).startswith(URL_PATH):  # same-app redirects only
            nxt = URL_PATH
        return redirect(nxt)

    @server.route(f"{URL_PATH}/<view>", methods=["GET"])
    def argus_wiki_view_home(view: str):
        try:
            state = reader.get_view(view)
        except KeyError:
            return not_found(f"unknown view: {view}")
        if not reader.is_ready(state):
            return html_response(reader.wip_html(view), 503)
        return redirect(_route("page", view=view, rel=dimension_registry.home_page_rel()))

    @server.route(f"{URL_PATH}/<view>/feed", methods=["GET"])
    def argus_wiki_feed_redirect(view: str):
        return redirect(_route("page", view=view, rel=dimension_registry.feed_page_rel()))

    @server.route(f"{URL_PATH}/<view>/status", methods=["GET"])
    def argus_wiki_status(view: str):
        try:
            return jsonify(reader.view_status(reader.get_view(view)))
        except KeyError:
            return jsonify({"ready": False, "error": f"unknown view: {view}"}), 404

    @server.route(f"{URL_PATH}/<view>/page/<path:rel>", methods=["GET"])
    def argus_wiki_page(view: str, rel: str):
        alias = _alias_redirect(view, "page", rel)
        if alias is not None:
            return redirect(alias, code=302)
        canonical = _canonical_entity_redirect(view, rel)
        if canonical is not None:
            return redirect(canonical, code=302)
        try:
            body, status = reader.render_page(view, rel, request.args.get("page", "1"))
            if status == 302:
                return redirect(body, code=302)
            return html_response(body, status)
        except KeyError:
            return not_found(f"unknown view: {view}")
        except FileNotFoundError:
            return not_found(f"Missing page: {rel}")
        except ValueError as exc:
            return not_found(str(exc))
        except OSError as exc:
            logging.warning("argus_wiki page unavailable view=%s rel=%s: %s", view, rel, exc)
            return unavailable("Argus Wiki page unavailable")

    @server.route(f"{URL_PATH}/<view>/folder/<path:folder>", methods=["GET"])
    def argus_wiki_folder(view: str, folder: str):
        alias = _alias_redirect(view, "folder", folder)
        if alias is not None:
            return redirect(alias, code=302)
        try:
            body, status = reader.render_folder(view, folder, request.args.get("page", "1"))
            return html_response(body, status)
        except KeyError:
            return not_found(f"unknown view: {view}")
        except OSError as exc:
            logging.warning("argus_wiki folder unavailable view=%s folder=%s: %s", view, folder, exc)
            return unavailable("Argus Wiki folder unavailable")

    @server.route(f"{URL_PATH}/<view>/search", methods=["GET"])
    def argus_wiki_search(view: str):
        try:
            body, status = reader.render_search(
                view, request.args.get("q", ""), request.args.get("page", "1"),
                filters=reader.parse_search_filters(request.args),
            )
            return html_response(body, status)
        except KeyError:
            return not_found(f"unknown view: {view}")
        except OSError as exc:
            logging.warning("argus_wiki search unavailable view=%s: %s", view, exc)
            return unavailable("Argus Wiki search unavailable")

    @server.route(f"{URL_PATH}/<view>/api/search", methods=["GET"])
    def argus_wiki_api_search(view: str):
        try:
            body, status = reader.search_api(
                view,
                request.args.get("q", ""),
                filters=reader.parse_search_filters(request.args),
            )
            return jsonify(body), status
        except KeyError:
            return jsonify({"error": f"unknown view: {view}"}), 404
        except OSError as exc:
            logging.warning("argus_wiki search unavailable view=%s: %s", view, exc)
            return jsonify({"error": "Argus Wiki search unavailable"}), 503

    @server.route(f"{URL_PATH}/<view>/api/ingest-audit", methods=["GET"])
    def argus_wiki_ingest_audit(view: str):
        try:
            reader.get_view(view)
            body, status = reader.ingest_audit_status()
            return jsonify(body), status
        except KeyError:
            return jsonify({"error": f"unknown view: {view}"}), 404

    @server.route(f"{URL_PATH}/<view>/api/sidebar-counts", methods=["GET"])
    def argus_wiki_sidebar_counts(view: str):
        try:
            body, status = reader.sidebar_counts(view)
            return jsonify(body), status
        except KeyError:
            return jsonify({"error": f"unknown view: {view}"}), 404

    @server.route(f"{URL_PATH}/<view>/api/sidebar", methods=["GET"])
    def argus_wiki_sidebar(view: str):
        try:
            body, status = reader.render_sidebar(view, request.args.get("rel", ""))
            return html_response(body, status)
        except KeyError:
            return not_found(f"unknown view: {view}")

    @server.route(f"{URL_PATH}/<view>/api/preview", methods=["GET"])
    def argus_wiki_preview(view: str):
        try:
            body, status = reader.preview_api(view, request.args.get("rel", ""))
            return jsonify(body), status
        except KeyError:
            return jsonify({"error": f"unknown view: {view}"}), 404

    @server.route(f"{URL_PATH}/<view>/api/source-section", methods=["GET"])
    def argus_wiki_source_section(view: str):
        try:
            body, status = reader.render_source_section(view, request.args.get("rel", ""))
            return html_response(body, status)
        except KeyError:
            return not_found(f"unknown view: {view}")
        except Exception as exc:  # never let a source-list hiccup break the page
            logging.warning("argus_wiki source-section failed view=%s: %s", view, exc)
            return html_response("", 200)

    @server.route(f"{URL_PATH}/<view>/api/suggest", methods=["GET"])
    def argus_wiki_suggest(view: str):
        try:
            return jsonify(reader.search_suggest(view, request.args.get("q", "")))
        except KeyError:
            return not_found(f"unknown view: {view}")
        except Exception as exc:
            logging.warning("argus_wiki suggest failed view=%s: %s", view, exc)
            return jsonify({"categories": [], "results": []})

    @server.route(f"{URL_PATH}/<view>/api/structured-section", methods=["GET"])
    def argus_wiki_structured_section(view: str):
        try:
            body, status = reader.render_structured_section(view, request.args.get("rel", ""))
            return html_response(body, status)
        except KeyError:
            return not_found(f"unknown view: {view}")
        except Exception as exc:  # never let a tiles hiccup break the page
            logging.warning("argus_wiki structured-section failed view=%s: %s", view, exc)
            return html_response("", 200)

    @server.route(f"{URL_PATH}/<view>/api/entity-sources", methods=["GET"])
    def argus_wiki_entity_sources(view: str):
        try:
            from argus_wiki.search import wiki_api
            body = wiki_api.list_entity_sources(
                folder=request.args.get("folder", ""),
                name=request.args.get("name") or request.args.get("entity", ""),
                offset=int(request.args.get("offset", "0")),
                limit=int(request.args.get("limit", "100")),
                since=request.args.get("since"),
                view=view,
            )
            return jsonify(body), 200
        except KeyError:
            return jsonify({"error": f"unknown view: {view}"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @server.route(f"{URL_PATH}/<view>/api/feed-search", methods=["GET"])
    def argus_wiki_feed_search(view: str):
        try:
            body, status = reader.feed_semantic_search(view, request.args.get("rel", ""), request.args.get("q", ""))
            return jsonify(body), status
        except KeyError:
            return jsonify({"error": f"unknown view: {view}"}), 404

    @server.route(f"{URL_PATH}/<view>/api/feed", methods=["GET"])
    def argus_wiki_api_feed(view: str):
        try:
            state = reader.get_view(view)
        except KeyError:
            return jsonify({"error": f"unknown view: {view}"}), 404
        if not reader.is_ready(state):
            return jsonify(reader.wip_json(view)), 503
        filter_keys = ["kind", "since"] + sorted({d.get("singular") or n.rstrip("s") for n, d in dimension_registry.dimensions().items()})
        filters = {key: request.args.get(key) for key in filter_keys if request.args.get(key)}
        for alias_key, target_key in dimension_registry.page_dir_aliases().items():
            singular_alias = alias_key.rstrip("s")
            if request.args.get(singular_alias):
                filters[target_key.rstrip("s")] = request.args.get(singular_alias)
        limit = max(0, min(request.args.get("limit", 50, type=int) or 50, 500))
        events = feed_mod.load_events(filters, limit=limit)
        return jsonify([
            {
                "kind": event.kind,
                "actor": event.actor,
                "when": event.when_label,
                "title": event.title_md,
                "detail": event.detail_md,
                "link": event.link_path,
                "tags": [{"kind": key, "value": value} for key, value in event.tags],
            }
            for event in events
        ])

    @server.route(f"{URL_PATH}/<view>/asset/<path:rel>", methods=["GET"])
    def argus_wiki_asset(view: str, rel: str):
        try:
            return send_file(reader.asset_path(view, rel))
        except KeyError:
            return not_found(f"unknown view: {view}")
        except FileNotFoundError:
            return not_found("Asset not found")

    @server.route(f"{URL_PATH}/<view>/content/<path:rel>", methods=["GET"])
    def argus_wiki_content(view: str, rel: str):
        try:
            return html_response(reader.wiki_content_preview(view, rel))
        except KeyError:
            return not_found(f"unknown view: {view}")
        except FileNotFoundError:
            return not_found("Content not found")
        except OSError as exc:
            logging.warning("argus_wiki content unavailable view=%s rel=%s: %s", view, rel, exc)
            return unavailable("Argus Wiki content unavailable")

    @server.route(f"{URL_PATH}/<view>/content-file/<path:rel>", methods=["GET"])
    def argus_wiki_content_file(view: str, rel: str):
        try:
            return send_file(reader.wiki_content_path(view, rel),
                             as_attachment=reader.wiki_content_as_attachment(rel))
        except KeyError:
            return not_found(f"unknown view: {view}")
        except FileNotFoundError:
            return not_found("Content not found")

    @server.route(f"{URL_PATH}/<view>/trending", methods=["GET"])
    def argus_wiki_trending(view: str):
        try:
            body, status = reader.render_trending(view)
            return html_response(body, status)
        except KeyError:
            return not_found(f"unknown view: {view}")
        except OSError as exc:
            logging.warning("argus_wiki trending unavailable view=%s: %s", view, exc)
            return unavailable("Argus Wiki trending unavailable")

    # Always build the in-memory note index + warm the manifest index in the
    # background: link resolution and entity hydration need it, and without it
    # every render falls back to per-link filesystem probing on the network
    # share (tens of seconds per page). The prewarm flag only controls the
    # extra, expensive hot-page pre-rendering — not the essential index build.
    hot_pages = wiki_cfg.prewarm_enabled()
    threading.Thread(
        target=prewarm_all_views,
        kwargs={"render_hot_pages": hot_pages},
        name="argus-wiki-prewarm",
        daemon=True,
    ).start()
    if not hot_pages:
        logging.info("argus_wiki prewarm: hot-page render disabled; building indexes only")
    logging.info("argus_wiki routes registered at %s with views=%s", URL_PATH, reader.VIEW_ORDER)
