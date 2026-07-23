"""
Bulk personalized email sender — Gmail / Outlook / custom SMTP.
Run: streamlit run app.py

Nothing is written to disk: credentials live only in this browser session's memory
for the duration of the run and are discarded when the app is closed/reloaded.
"""

import smtplib
import ssl
import string
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
import streamlit as st

PROVIDERS = {
    "Gmail": {"host": "smtp.gmail.com", "port": 587},
    "Outlook / Office365": {"host": "smtp.office365.com", "port": 587},
    "Custom": {"host": "", "port": 587},
}


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


st.set_page_config(page_title="Bulk Email Sender", page_icon="📧", layout="wide")
st.title("📧 Bulk Personalized Email Sender")

# ---------------- Sidebar: account ----------------
st.sidebar.header("1. Your email account")
provider_name = st.sidebar.selectbox("Provider", list(PROVIDERS.keys()))
default_host, default_port = PROVIDERS[provider_name]["host"], PROVIDERS[provider_name]["port"]

if provider_name == "Custom":
    smtp_host = st.sidebar.text_input("SMTP host")
    smtp_port = st.sidebar.number_input("SMTP port", value=587)
else:
    smtp_host = default_host
    smtp_port = default_port
    st.sidebar.caption(f"SMTP: {smtp_host}:{smtp_port}")

sender_email = st.sidebar.text_input("Your email address")
sender_password = st.sidebar.text_input(
    "App password",
    type="password",
    help=(
        "Gmail: needs 2-Step Verification ON, then create an App Password "
        "(Google Account → Security → App passwords). Your normal login password will NOT work.\n\n"
        "Outlook: use an App Password if your account has MFA/security defaults enabled."
    ),
)

with st.sidebar.expander("Sending speed"):
    delay_sec = st.number_input("Delay between emails (seconds)", min_value=0.0, value=2.0, step=0.5)
    st.caption("Keep a delay to avoid provider rate-limits / spam flags.")

st.sidebar.info(
    "Credentials are kept only in memory for this session — never saved to disk or logged.",
    icon="🔒",
)

# ---------------- Step 2: recipient data ----------------
st.header("2. Upload recipient data")
data_file = st.file_uploader("Excel (.xlsx) or CSV with one row per person", type=["xlsx", "xls", "csv"])

df = None
if data_file is not None:
    try:
        if data_file.name.lower().endswith(".csv"):
            df = pd.read_csv(data_file)
        else:
            df = pd.read_excel(data_file)
        df.columns = [str(c).strip() for c in df.columns]
        st.success(f"Loaded {len(df)} rows, columns: {', '.join(df.columns)}")
        st.dataframe(df.head(10), use_container_width=True)
    except Exception as e:
        st.error(f"Could not read file: {e}")

email_col = None
if df is not None:
    guess = next((c for c in df.columns if c.lower() == "email"), df.columns[0])
    email_col = st.selectbox("Which column is the email address?", df.columns, index=list(df.columns).index(guess))

# ---------------- Step 3: template ----------------
st.header("3. Compose template")
st.caption(
    "Use {column_name} placeholders anywhere (subject or body) to pull values from each row, "
    "e.g. {name}, {company}, {amount}."
)

is_html = st.checkbox("Body is HTML", value=True)
subject_tpl = st.text_input("Subject", value="Hello {name}")
body_tpl = st.text_area(
    "Body",
    height=260,
    value=(
        "<p>Hi {name},</p><p>Write your message for {company} here.</p><p>Regards,<br>Your Team</p>"
        if is_html
        else "Hi {name},\n\nWrite your message for {company} here.\n\nRegards,\nYour Team"
    ),
)

if df is not None and email_col:
    st.subheader("Preview (first row)")
    sample_row = df.iloc[0].to_dict()
    st.write("**Subject:**", render(subject_tpl, sample_row))
    if is_html:
        st.components.v1.html(render(body_tpl, sample_row), height=200, scrolling=True)
    else:
        st.text(render(body_tpl, sample_row))

# ---------------- Step 4: send ----------------
st.header("4. Send")

ready = bool(sender_email and sender_password and smtp_host and df is not None and email_col)
if not ready:
    st.warning("Fill in account details, upload data, and pick the email column to enable sending.")

if st.button("🚀 Send to all", disabled=not ready, type="primary"):
    total = len(df)
    progress = st.progress(0)
    status_area = st.empty()
    results = []

    try:
        context = ssl.create_default_context()
        server = smtplib.SMTP(smtp_host, int(smtp_port), timeout=30)
        server.starttls(context=context)
        server.login(sender_email, sender_password)
    except Exception as e:
        st.error(f"Login/connection failed — check email/app password/SMTP settings.\n\n{e}")
        server = None

    if server is not None:
        for i, (_, row) in enumerate(df.iterrows()):
            row_dict = row.to_dict()
            to_addr = str(row_dict.get(email_col, "")).strip()
            status = "skipped (no email)"
            if to_addr and "@" in to_addr:
                try:
                    subject = render(subject_tpl, row_dict)
                    body = render(body_tpl, row_dict)
                    msg = build_message(sender_email, to_addr, subject, body, is_html)
                    server.sendmail(sender_email, [to_addr], msg.as_string())
                    status = "sent"
                except Exception as e:
                    status = f"failed: {e}"
            results.append({"email": to_addr, "status": status})
            progress.progress((i + 1) / total)
            status_area.write(f"{i + 1}/{total}: {to_addr} — {status}")
            if delay_sec:
                time.sleep(delay_sec)

        try:
            server.quit()
        except Exception:
            pass

        result_df = pd.DataFrame(results)
        sent = (result_df["status"] == "sent").sum()
        st.success(f"Done. {sent}/{total} sent.")
        st.dataframe(result_df, use_container_width=True)
        st.download_button(
            "Download send log (CSV)",
            result_df.to_csv(index=False).encode("utf-8"),
            file_name="send_log.csv",
            mime="text/csv",
        )
