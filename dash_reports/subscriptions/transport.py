from email.message import EmailMessage
from html import escape
from smtplib import SMTP
from urllib.parse import urlencode, urlsplit

from dash_reports.subscriptions.models import Principal


class Mailer:
    def __init__(self, base_url, sender, host):
        parsed = urlsplit(base_url or "")
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("Configure an HTTPS website URL for email subscriptions.")
        Principal("sender", sender or "")
        if not host:
            raise ValueError("Configure the email subscription SMTP host.")
        self.base_url, self.sender, self.host = base_url.rstrip("/"), sender, host

    def __call__(self, principal, report, digest):
        manage = self.base_url + "/email-subscriptions?" + urlencode({"report": report.id})
        message = EmailMessage()
        message["From"], message["To"] = self.sender, principal.email
        message["Subject"] = f"[Argus] {report.title}"
        message.set_content(digest.text + "\n\nManage, pause or unsubscribe: " + manage)
        message.add_alternative(
            '<html><body style="font:14px Arial,sans-serif;color:#182b36">'
            f'<h2>{escape(report.title)}</h2>{digest.html}<hr>'
            f'<p>You subscribed to this report on Argus. <a href="{escape(manage, quote=True)}">'
            'Manage schedule, pause or unsubscribe</a>.</p></body></html>', subtype="html")
        with SMTP(self.host, timeout=30) as server:
            refused = server.send_message(message)
            if refused:
                raise RuntimeError("SMTP did not accept the subscription recipient.")
