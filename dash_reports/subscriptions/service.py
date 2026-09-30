import logging
from datetime import datetime

from filelock import Timeout

from dash_reports.subscriptions.schedule import UTC, clean_schedule, next_due


class Service:
    def __init__(self, store, registry, authorize, send):
        self.store, self.registry, self.authorize, self.send = store, registry, authorize, send

    def report(self, principal, report_id, delivery=False):
        report = self.registry.get(report_id)
        if principal is None or not self.authorize(principal, report, delivery):
            raise PermissionError("This report is not available for your email subscription.")
        return report

    def get(self, principal, report_id):
        if principal is None:
            raise PermissionError("Sign in to manage your subscriptions.")
        return self.store.read(self.store.key(principal.subject, report_id))

    def save(self, principal, report_id, filters, schedule, options, now=None):
        report = self.report(principal, report_id)
        filters, schedule = report.clean_filters(filters), clean_schedule(schedule)
        if not isinstance(options, dict) or any(type(options.get(key, True)) is not bool for key in ("skip_empty", "only_changes")):
            raise ValueError("Invalid delivery options.")
        now = now or datetime.now(UTC)
        key = self.store.key(principal.subject, report_id)
        with self.store.locked(key):
            value = self.store.read(key) or {"history": [], "last_fingerprint": None}
            if value.get("filters") != filters:
                value["last_fingerprint"] = None
            due = value.get("next_due") if value.get("enabled") and value.get("schedule") == schedule else None
            value.update(subject=principal.subject, email=principal.email, report_id=report_id,
                         filters=filters, schedule=schedule, enabled=True, unsubscribed=False,
                         skip_empty=options.get("skip_empty", True), only_changes=options.get("only_changes", True),
                         updated_at=now.isoformat(),
                         next_due=due or next_due(schedule, now).isoformat())
            self.store.write(key, value)
        return value

    def set_status(self, principal, report_id, action, now=None):
        if action not in {"pause", "resume", "unsubscribe"}:
            raise ValueError("Invalid subscription action.")
        if action == "resume":
            self.report(principal, report_id)
        value = self.get(principal, report_id)
        if not value:
            raise ValueError("Save a subscription first.")
        now = now or datetime.now(UTC)
        key = self.store.key(principal.subject, report_id)
        with self.store.locked(key):
            value = self.store.read(key)
            value.update(enabled=action == "resume", unsubscribed=action == "unsubscribe",
                         updated_at=now.isoformat())
            if action == "resume":
                value.update(email=principal.email, next_due=next_due(value["schedule"], now).isoformat())
            self.store.write(key, value)
        return value

    def preview(self, principal, report_id, filters, now=None):
        report = self.report(principal, report_id)
        return report.build(report.clean_filters(filters), now or datetime.now(UTC))

    def deliver(self, key, now=None):
        from dash_reports.subscriptions.models import Principal
        now = now or datetime.now(UTC)
        with self.store.locked(key):
            value = self.store.read(key)
            if not value or not value["enabled"] or datetime.fromisoformat(value["next_due"]) > now:
                return "not_due"
            event = {"at": now.isoformat(), "scheduled_for": value["next_due"], "status": "unconfirmed"}
            value["history"] = (value["history"] + [event])[-30:]
            value["next_due"] = next_due(value["schedule"], now).isoformat()
            self.store.write(key, value)
            try:
                principal = Principal(value["subject"], value["email"])
                report = self.report(principal, value["report_id"], delivery=True)
                digest = report.build(report.clean_filters(value["filters"]), now)
                if value["skip_empty"] and not digest.count:
                    event["status"] = "skipped_empty"
                elif value["only_changes"] and digest.fingerprint == value.get("last_fingerprint"):
                    event["status"] = "skipped_unchanged"
                else:
                    self.send(principal, report, digest)
                    event["status"] = "sent"
                    value["last_fingerprint"] = digest.fingerprint
            except PermissionError:
                event["status"] = "access_denied"
                value["enabled"] = False
            except Exception:
                event["status"] = "failed"
                logging.exception("Email subscription delivery failed: %s", key)
            self.store.write(key, value)
            return event["status"]

    def run_due(self, now=None):
        results = {}
        for key in self.store.keys():
            try:
                results[key] = self.deliver(key, now)
            except Timeout:
                results[key] = "busy"
            except Exception:
                logging.exception("Could not process email subscription: %s", key)
                results[key] = "failed"
        return results
