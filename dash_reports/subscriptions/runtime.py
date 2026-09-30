from functools import lru_cache
from importlib import import_module

from dash_reports.subscriptions.models import Principal, Registry
from dash_reports.subscriptions.service import Service
from dash_reports.subscriptions.store import Store

APP = "dash_reports"


def current_principal():
    from flask import current_app
    provider = current_app.extensions.get("email_subscription_identity")
    if provider is None:
        raise PermissionError("Sign in to manage your email subscriptions.")
    return provider()


def okta_principal(auth):
    from flask import session
    from dash_reports.app_utils.app_factory import _okta_user_and_groups
    _okta_user_and_groups(auth)
    identity = session.get("_okta_userinfo", {})
    groups = identity.get("groups") or []
    return Principal(identity.get("subject", ""), identity.get("email", ""),
                     tuple([groups] if isinstance(groups, str) else groups))


def authorize(principal, report, delivery):
    from data_access.dbtables.dash_reports_misc import access_logs
    frame = access_logs.get_dash_app_access()
    routes = frame["link"].astype(str).str.rstrip("/")
    matches = routes.map(lambda route: report.path == route or report.path.startswith(route + "/"))
    allowed = frame[matches & frame["access_level"].eq("read")]
    if allowed.empty:
        return True
    if delivery:
        return bool(report.delivery_authorizer and report.delivery_authorizer(principal))
    return allowed["ad_group"].isin(principal.groups).any()


@lru_cache(maxsize=1)
def get_service():
    from apo_config.config import config
    from dash_reports.subscriptions.transport import Mailer
    root = config.getconfig(APP, "email_subscriptions_root")
    if not root:
        raise ValueError("Email subscriptions have not been configured for this environment.")
    modules = config.getconfig(APP, "email_subscription_providers") or "dash_reports.vc_dashboard.subscriptions"
    registry = Registry()
    for module in modules.split(","):
        import_module(module.strip()).register(registry)
    return Service(Store(root), registry, authorize, Mailer(
        config.getconfig(APP, "email_subscriptions_url"),
        config.getconfig(APP, "email_subscriptions_sender"),
        config.getconfig(APP, "email_subscriptions_smtp_host")))
