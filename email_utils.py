"""
Small shared helpers for pages that send email. Deliberately NOT imported by
app.py (its inline copies of these are untouched) — this exists so the
Research-Based Outreach page doesn't duplicate this logic inline, and so
app.py never needs to be imported as a module (it's a top-level script full
of st.* calls, importing it would re-run the whole main page as a side effect).
"""

import string
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


class SafeDict(dict):
    """Missing template placeholders render as empty string instead of raising."""

    def __missing__(self, key):
        return ""


def render(template: str, row: dict) -> str:
    return string.Formatter().vformat(template, (), SafeDict(row))


def build_message(sender: str, to_addr: str, subject: str, body: str, is_html: bool) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html" if is_html else "plain"))
    return msg


def personalized_email_to_html(text: str) -> str:
    """"\n\n" -> paragraph breaks, single "\n" -> <br>, per-paragraph."""
    if not text:
        return ""
    paragraphs = str(text).split("\n\n")
    html_parts = [f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs if p.strip()]
    return "".join(html_parts)
