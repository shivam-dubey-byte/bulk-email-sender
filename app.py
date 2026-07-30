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

# ---------------- Access gate ----------------
# Set ACCESS_CODE in Streamlit Cloud → App settings → Secrets to lock this app down.
# If no ACCESS_CODE secret is configured (e.g. local dev), the gate is skipped.
required_code = st.secrets.get("ACCESS_CODE")
if required_code:
    if not st.session_state.get("unlocked"):
        st.title("🔒 Locked")
        entered = st.text_input("Access code", type="password")
        if st.button("Unlock"):
            if entered == required_code:
                st.session_state["unlocked"] = True
                st.rerun()
            else:
                st.error("Wrong code.")
        st.stop()

st.title("📧 Bulk Personalized Email Sender")

# ---------------- Sidebar: account ----------------
st.sidebar.header("1. Your email account")
provider_name = st.sidebar.selectbox("Provider", list(PROVIDERS.keys()), key="smtp_provider_name")
default_host, default_port = PROVIDERS[provider_name]["host"], PROVIDERS[provider_name]["port"]

if provider_name == "Custom":
    smtp_host = st.sidebar.text_input("SMTP host", key="smtp_host_custom")
    smtp_port = st.sidebar.number_input("SMTP port", value=587, key="smtp_port_custom")
else:
    smtp_host = default_host
    smtp_port = default_port
    st.sidebar.caption(f"SMTP: {smtp_host}:{smtp_port}")

# Stashed under stable keys (not just the widget's own auto key) so other pages
# (e.g. the Research-Based Outreach page) can reuse the same account without
# asking the user to re-enter it.
st.session_state["smtp_host"] = smtp_host
st.session_state["smtp_port"] = smtp_port

sender_email = st.sidebar.text_input("Your email address", key="sender_email")
sender_password = st.sidebar.text_input(
    "App password",
    type="password",
    key="sender_password",
    help=(
        "Gmail: needs 2-Step Verification ON, then create an App Password "
        "(Google Account → Security → App passwords). Your normal login password will NOT work.\n\n"
        "Outlook: use an App Password if your account has MFA/security defaults enabled."
    ),
)

with st.sidebar.expander("Sending speed"):
    delay_sec = st.number_input(
        "Delay between emails (seconds)", min_value=0.0, value=2.0, step=0.5, key="delay_sec"
    )
    st.caption("Keep a delay to avoid provider rate-limits / spam flags.")

st.sidebar.info(
    "Credentials are kept only in memory for this session — never saved to disk or logged.",
    icon="🔒",
)

# ---------------- Step 2: recipient data ----------------
st.header("2. Recipient data")
st.caption("Upload a file and/or type rows in by hand — the table below is fully editable, add/remove rows as needed.")

data_file = st.file_uploader("Excel (.xlsx) or CSV with one row per person (optional)", type=["xlsx", "xls", "csv"])

DEFAULT_COLS = ["email", "name", "company"]

if "recipients" not in st.session_state:
    st.session_state["recipients"] = pd.DataFrame(columns=DEFAULT_COLS)

if data_file is not None and st.session_state.get("last_loaded_file") != data_file.file_id:
    try:
        loaded = pd.read_csv(data_file) if data_file.name.lower().endswith(".csv") else pd.read_excel(data_file)
        loaded.columns = [str(c).strip() for c in loaded.columns]
        existing = st.session_state["recipients"]
        combined = pd.concat([existing, loaded], ignore_index=True) if not existing.empty else loaded
        st.session_state["recipients"] = combined.fillna("")
        st.session_state["last_loaded_file"] = data_file.file_id
        st.success(f"Loaded {len(loaded)} rows from file — merged into the table below.")
    except Exception as e:
        st.error(f"Could not read file: {e}")

st.write("Add, edit, or delete rows manually (use the **+** row at the bottom to add one email at a time):")
edited = st.data_editor(
    st.session_state["recipients"],
    num_rows="dynamic",
    use_container_width=True,
    key="recipients_editor",
)
st.session_state["recipients"] = edited

df = edited if not edited.empty else None

email_col = None
if df is not None and len(df.columns) > 0:
    guess = next((c for c in df.columns if c.lower() == "email"), df.columns[0])
    email_col = st.selectbox("Which column is the email address?", df.columns, index=list(df.columns).index(guess))
    df = df[df[email_col].astype(str).str.strip().str.contains("@", na=False)]
    if df.empty:
        df = None

# ---------------- Step 2b: AI company research (optional) ----------------
st.header("2b. AI company research (optional)")
st.caption(
    "Auto-fill a {company_info} column per row using an NVIDIA NIM agent that searches the web "
    "and can spawn sub-agents to split up the research."
)

nvidia_api_key = st.secrets.get("NVIDIA_API_KEY")
if not nvidia_api_key:
    st.info(
        "Research agent not configured. App owner: add NVIDIA_API_KEY in Streamlit Cloud → "
        "Settings → Secrets to enable this for everyone using the app.",
        icon="🔑",
    )

with st.expander("Set up research agent", expanded=not bool(nvidia_api_key)):
    try:
        from research_agent import RECOMMENDED_MODELS

        model_choice = st.selectbox(
            "Model", RECOMMENDED_MODELS, help="Ultra = most capable. Nano = fastest. All free endpoints."
        )
    except ImportError:
        model_choice = None
        st.warning("research_agent.py dependencies (`openai`, `ddgs`) not installed — see requirements.txt.")

    company_col = None
    if df is not None:
        candidates = [c for c in df.columns if c.lower() in ("company", "organization", "org")]
        guess_co = candidates[0] if candidates else df.columns[0]
        company_col = st.selectbox(
            "Which column is the company name?", df.columns, index=list(df.columns).index(guess_co)
        )

    run_research = st.button(
        "🔎 Research companies now",
        disabled=not (nvidia_api_key and model_choice and df is not None and company_col),
    )

if run_research:
    from research_agent import ResearchAgent

    agent = ResearchAgent(api_key=nvidia_api_key, model=model_choice)
    cache = st.session_state.setdefault("company_info_cache", {})
    unique_companies = [c for c in df[company_col].dropna().unique() if str(c).strip()]

    progress = st.progress(0)
    status = st.empty()
    for i, company in enumerate(unique_companies):
        if company not in cache:
            status.write(f"Researching {company}...")
            try:
                cache[company] = agent.research_company(str(company))
            except Exception as e:
                cache[company] = f"(research failed: {e})"
        progress.progress((i + 1) / max(len(unique_companies), 1))
    status.write("Done.")

    updated = st.session_state["recipients"].copy()
    updated["company_info"] = updated[company_col].map(lambda c: cache.get(c, ""))
    st.session_state["recipients"] = updated
    # The data_editor widget caches its own state under "recipients_editor"; without clearing
    # it, the widget ignores the new "recipients" dataframe on rerun and shows stale data.
    st.session_state.pop("recipients_editor", None)
    st.success(f"Filled company_info for {len(unique_companies)} companies — use {{company_info}} in your template.")
    st.rerun()

# ---------------- Step 3: template ----------------
st.header("3. Compose template")
available_cols = list(df.columns) if df is not None else []
st.caption(
    "Use {column_name} placeholders anywhere (subject or body) to pull values from each row. "
    + (f"Columns available right now: {', '.join('{' + c + '}' for c in available_cols)}." if available_cols else "")
)
if df is not None and "company_info" not in df.columns:
    st.caption("💡 No `company_info` column yet — run step 2b's research agent first if you want an AI-written company blurb to plug in here.")

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
        if int(smtp_port) == 465:
            # Implicit SSL (common for cPanel/Zoho/GoDaddy-hosted custom mail) — no STARTTLS step.
            server = smtplib.SMTP_SSL(smtp_host, int(smtp_port), timeout=30, context=context)
        else:
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
