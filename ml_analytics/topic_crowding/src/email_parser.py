# src/email_parser.py
import email
from datetime import datetime
from email import policy

import pandas as pd


class EmailParser:
    def parse_email(self, email_content):
        """Parse email content and extract metadata."""
        msg = email.message_from_string(email_content.strip())

        date = email.utils.parsedate_to_datetime(msg['date'])
        subject = msg['subject']
        body = ''

        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode()
        else:
            body = msg.get_payload(decode=True).decode()

        return {
            'date': date,
            'subject': subject,
            'body': body,
            'full_text': f"{subject} {body}"
        }

    def process_emails(self, email_list):
        """Process a list of emails into DataFrame."""
        processed_data = []
        for email_content in email_list:
            data = self.parse_email(email_content)
            processed_data.append(data)
        return pd.DataFrame(processed_data)
