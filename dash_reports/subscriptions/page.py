import json
import logging
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, available_timezones

from dash import ALL, Input, Output, State, callback, ctx, dcc, html, no_update
from filelock import Timeout

from dash_reports.subscriptions.runtime import current_principal, get_service

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def subscription_url(report_id, filters=None):
    return "/email-subscriptions?" + urlencode({"report": report_id, "filters": json.dumps(filters or {})})


def subscription_link(report_id, filters=None, **kwargs):
    return dcc.Link("Subscribe by email", href=subscription_url(report_id, filters), **kwargs)


def history(value):
    events = (value or {}).get("history", [])
    if not events:
        return html.P("No deliveries yet. Preview the report before subscribing.")
    zone = ZoneInfo(value["schedule"]["timezone"])
    labels = {"sent": "Accepted by mail server", "skipped_empty": "Skipped: no matching data",
              "skipped_unchanged": "Skipped: no changes", "failed": "Delivery failed",
              "access_denied": "Paused: report access could not be verified",
              "unconfirmed": "Delivery unconfirmed; not retried to avoid duplicates"}
    return html.Ul([html.Li(f'{datetime.fromisoformat(event["at"]).astimezone(zone):%b %d, %Y %H:%M %Z} · '
                           + labels.get(event["status"], event["status"])) for event in reversed(events)])


def status(value):
    if not value or value.get("unsubscribed"):
        return "Not subscribed."
    if not value["enabled"]:
        return "Paused. Resume whenever you are ready."
    due = datetime.fromisoformat(value["next_due"]).astimezone(ZoneInfo(value["schedule"]["timezone"]))
    return f"Subscribed · next delivery around {due:%b %d, %Y %H:%M %Z}."


def overview(service, principal):
    items = []
    for report in service.registry.reports.values():
        value = service.get(principal, report.id)
        if value and not value.get("unsubscribed"):
            items.append(html.Li([dcc.Link(report.title, href=subscription_url(report.id)), " — ", status(value)]))
    return html.Ul(items) if items else html.P("You have no active or paused email subscriptions.")


def layout(query=None):
    query = query or {}
    try:
        principal, service = current_principal(), get_service()
        reports = [report for report in service.registry.reports.values()
                   if service.authorize(principal, report, False) or service.get(principal, report.id)]
    except (ValueError, PermissionError) as error:
        return html.Div([html.H2("Email subscriptions"), html.P(str(error))], className="subscriptions-page")
    options = [{"label": report.title, "value": report.id} for report in reports]
    selected = query.get("report")
    if selected not in {report.id for report in reports}:
        selected = reports[0].id if reports else None
    try:
        seed = json.loads(query.get("filters", "{}"))
    except (ValueError, TypeError):
        seed = {}
    return html.Main([
        html.H2("Email subscriptions"),
        html.P(["Choose what reaches your inbox and when. Delivery to ", html.Strong(principal.email), "."]),
        html.H3("My subscriptions"), html.Div(overview(service, principal), id="email-sub-overview"),
        html.H3("Report and delivery settings"),
        html.Label("Report", htmlFor="email-sub-report"),
        dcc.Dropdown(id="email-sub-report", options=options, value=selected, clearable=False),
        dcc.Store(id="email-sub-seed", data={"report": selected, "filters": seed}),
        html.Div(id="email-sub-editor"),
    ], className="subscriptions-page")


def filter_control(spec, value):
    identity = {"type": "email-sub-filter", "key": spec.key}
    if spec.kind in {"choice", "bool"}:
        choices = spec.choices if spec.kind == "choice" else ((False, "No"), (True, "Yes"))
        control = dcc.Dropdown(id=identity, options=[{"value": val, "label": label} for val, label in choices],
                               value=value, clearable=False)
    else:
        control = dcc.Input(id=identity, value=value, type="number" if spec.kind == "number" else "text",
                            **({"min": spec.minimum, "max": spec.maximum} if spec.kind == "number" else {"maxLength": 250}))
    return html.Div([html.Label(spec.label), control], className="email-sub-field")


@callback(Output("email-sub-editor", "children"), Input("email-sub-report", "value"), State("email-sub-seed", "data"))
def editor(report_id, seed):
    if not report_id:
        return html.P("No reports are available for subscription.")
    try:
        principal, service = current_principal(), get_service()
        report = service.registry.get(report_id)
        value = service.get(principal, report_id)
        filters = (value or {}).get("filters", {})
        if not value and seed and seed.get("report") == report_id:
            filters = report.clean_filters(seed.get("filters", {}))
        schedule = (value or {}).get("schedule", {"frequency": "weekdays", "time": "08:00", "timezone": "America/New_York", "days": [0]})
        return [html.P(report.description),
                html.Div([filter_control(spec, filters.get(spec.key, spec.default)) for spec in report.filters], className="email-sub-grid"),
                html.Div([
                    html.Div([html.Label("Frequency"), dcc.Dropdown(id="email-sub-frequency", clearable=False,
                        options=[{"value": key, "label": label} for key, label in [("daily", "Daily"), ("weekdays", "Weekdays"),
                                 ("weekly", "Weekly"), ("custom", "Choose days")]], value=schedule["frequency"])]),
                    html.Div([html.Label("Local delivery time (HH:MM)"), dcc.Input(id="email-sub-time", value=schedule["time"], type="text", maxLength=5)]),
                    html.Div([html.Label("Timezone"), dcc.Dropdown(id="email-sub-timezone", value=schedule["timezone"], clearable=False,
                              options=[{"value": zone, "label": zone.replace("_", " ")} for zone in sorted(available_timezones())])]),
                ], className="email-sub-grid"),
                html.Label("Delivery days (weekly: choose one; custom: choose any)"),
                dcc.Checklist(id="email-sub-days", options=[{"value": i, "label": day} for i, day in enumerate(DAYS)],
                              value=schedule["days"], inline=True),
                dcc.Checklist(id="email-sub-options", options=[{"value": "skip_empty", "label": "Skip when there is no matching data"},
                              {"value": "only_changes", "label": "Only email when report data changes"}],
                              value=[key for key in ("skip_empty", "only_changes") if (value or {}).get(key, True)]),
                html.P("Delivery runs about every five minutes. Times follow daylight saving changes. A missed run sends one current report when service resumes."),
                html.Div([html.Button(label, id=f"email-sub-{action}", n_clicks=0) for action, label in
                          [("preview", "Preview email"), ("save", "Save & subscribe"), ("pause", "Pause"),
                           ("resume", "Resume"), ("unsubscribe", "Unsubscribe")]], className="email-sub-actions"),
                html.Div(status(value), id="email-sub-status", role="status"),
                html.Div(id="email-sub-preview-body"), html.H3("Recent delivery history"),
                html.Div(history(value), id="email-sub-history")]
    except (ValueError, PermissionError) as error:
        return html.P(str(error))


@callback(Output("email-sub-status", "children"), Output("email-sub-preview-body", "children"),
          Output("email-sub-history", "children"), Output("email-sub-overview", "children"),
          *[Input(f"email-sub-{action}", "n_clicks") for action in ("preview", "save", "pause", "resume", "unsubscribe")],
          State("email-sub-report", "value"), State({"type": "email-sub-filter", "key": ALL}, "id"),
          State({"type": "email-sub-filter", "key": ALL}, "value"),
          State("email-sub-frequency", "value"), State("email-sub-time", "value"), State("email-sub-timezone", "value"),
          State("email-sub-days", "value"), State("email-sub-options", "value"), prevent_initial_call=True)
def act(preview, save, pause, resume, unsubscribe, report_id, identities, values, frequency, time, zone, days, options):
    if not any([preview, save, pause, resume, unsubscribe]):
        return (no_update,) * 4
    try:
        principal, service = current_principal(), get_service()
        filters = {identity["key"]: value for identity, value in zip(identities, values)}
        action = ctx.triggered_id.removeprefix("email-sub-")
        if action == "preview":
            digest = service.preview(principal, report_id, filters)
            frame = html.Iframe(srcDoc=digest.html, sandbox="", title="Email preview", className="email-sub-preview")
            return "Preview only; no email was sent.", frame, no_update, no_update
        if action == "save":
            value = service.save(principal, report_id, filters,
                {"frequency": frequency, "time": time, "timezone": zone, "days": days},
                {key: key in (options or []) for key in ("skip_empty", "only_changes")})
        else:
            value = service.set_status(principal, report_id, action)
        return status(value), [], history(value), overview(service, principal)
    except (ValueError, PermissionError) as error:
        return str(error), no_update, no_update, no_update
    except Timeout:
        return "A delivery is in progress. Please try again shortly.", no_update, no_update, no_update
    except Exception:
        logging.exception("Email subscription update failed")
        return "Could not complete this action. Please try again.", no_update, no_update, no_update
