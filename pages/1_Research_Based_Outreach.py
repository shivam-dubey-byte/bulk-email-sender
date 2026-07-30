"""
Research-Based Outreach — sends pre-written, per-lead personalized emails from
a "Personalized Outreach" sheet (research/lead-gen export). Independent page,
own URL. Reuses the SMTP account + sending-speed configured on the main Bulk
Email Sender page via st.session_state, so credentials aren't re-entered here.
"""

import smtplib
import ssl
import time

import pandas as pd
import streamlit as st

from email_utils import build_message, personalized_email_to_html, render

st.set_page_config(page_title="Research-Based Outreach", page_icon="🎯", layout="wide")
st.title("🎯 Research-Based Outreach")
st.caption(
    "Send the pre-written, per-lead emails from a research/lead-gen export. "
    "Uses the SMTP account configured on the main Bulk Email Sender page — no re-entry needed."
)

SHEET_NAME = "Personalized Outreach"
EMAIL_STATUS_COL = "Email Status"
REQUIRED_STATUS = "Verified"
EXPECTED_COLUMNS = [
    "First Name", "Last Name", "Title", "Company", "Email", "Industry", "Location",
    "Company Summary", "Personalization Hook", "Likely Business Needs",
    "Recommended Services", "Subject Line", "Personalized Email", "Website", "LinkedIn",
]

# ---------------- Reused account / sending settings ----------------
sender_email = st.session_state.get("sender_email")
sender_password = st.session_state.get("sender_password")
smtp_host = st.session_state.get("smtp_host")
smtp_port = st.session_state.get("smtp_port")
delay_sec = st.session_state.get("delay_sec", 2.0)

if not (sender_email and sender_password and smtp_host):
    st.warning(
        "No SMTP account configured yet. Open the main **Bulk Email Sender** page, "
        "fill in your email/app password there, then come back — no need to re-enter it here."
    )
else:
    st.success(f"Using account: {sender_email} ({smtp_host}:{smtp_port})")

# ---------------- Step 1: upload ----------------
st.header("1. Upload leads")
data_file = st.file_uploader(f'Excel file with a "{SHEET_NAME}" sheet', type=["xlsx", "xls"])

df = None
if data_file is not None:
    try:
        df = pd.read_excel(data_file, sheet_name=SHEET_NAME, engine="openpyxl")
        df.columns = [str(c).strip() for c in df.columns]
    except ValueError as e:
        st.error(f'Could not find a "{SHEET_NAME}" sheet in this file (other sheets are ignored): {e}')
    except Exception as e:
        st.error(f"Could not read file: {e}")

    if df is not None and "Email" not in df.columns:
        st.error('This sheet has no "Email" column — cannot send.')
        df = None

if df is not None:
    st.success(f"Loaded {len(df)} leads from '{SHEET_NAME}'.")

    found = [c for c in EXPECTED_COLUMNS if c in df.columns]
    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    st.caption("Available placeholders: " + ", ".join(f"{{{c}}}" for c in found))
    if missing:
        st.caption("Not present in this sheet (fine if unused): " + ", ".join(f"{{{c}}}" for c in missing))

    st.dataframe(df.head(10), use_container_width=True)

    # ---------------- Step 2: verified vs needs-review ----------------
    if EMAIL_STATUS_COL in df.columns:
        is_verified = df[EMAIL_STATUS_COL].astype(str).str.strip().str.casefold() == REQUIRED_STATUS.casefold()
    else:
        is_verified = pd.Series([False] * len(df), index=df.index)
        st.warning(f'No "{EMAIL_STATUS_COL}" column found — nothing will send until this column exists.')

    verified_df = df[is_verified].reset_index(drop=True)
    needs_review_df = df[~is_verified].reset_index(drop=True)

    st.header("2. Needs review (not sent)")
    if needs_review_df.empty:
        st.caption("None — every row is Verified.")
    else:
        review_display = needs_review_df.copy()
        if EMAIL_STATUS_COL in review_display.columns:
            review_display["Reason"] = "Email Status: " + review_display[EMAIL_STATUS_COL].fillna("(blank)").astype(str)
        else:
            review_display["Reason"] = f'No "{EMAIL_STATUS_COL}" column'
        st.dataframe(review_display, use_container_width=True)
        st.caption(f'{len(needs_review_df)} row(s) skipped — Email Status isn\'t "{REQUIRED_STATUS}".')

    # ---------------- Step 3: compose ----------------
    st.header("3. Compose")
    st.caption(
        "Each verified row already has a fully written email — Subject defaults to {Subject Line} "
        "and Body to {Personalized Email}. Edit if you want to override. Body always renders as HTML; "
        "{Personalized Email} specifically has its blank-line paragraphs and line breaks converted for you."
    )
    subject_tpl = st.text_input("Subject", value="{Subject Line}")
    body_tpl = st.text_area("Body", value="{Personalized Email}", height=160)

    def _render_row_for_body(row_dict: dict) -> dict:
        out = dict(row_dict)
        if "Personalized Email" in out:
            out["Personalized Email"] = personalized_email_to_html(out.get("Personalized Email", ""))
        return out

    if not verified_df.empty:
        st.subheader("Preview (first verified row)")
        sample_row = verified_df.iloc[0].to_dict()
        st.write("**Subject:**", render(subject_tpl, sample_row))
        st.components.v1.html(render(body_tpl, _render_row_for_body(sample_row)), height=250, scrolling=True)

    # ---------------- Step 4: send ----------------
    st.header("4. Send")
    ready = bool(sender_email and sender_password and smtp_host and not verified_df.empty)
    if not ready:
        st.warning("Need a configured SMTP account (main page) and at least one Verified lead to send.")

    if st.button("🚀 Send to verified leads", disabled=not ready, type="primary"):
        total = len(verified_df)
        progress = st.progress(0)
        status_area = st.empty()
        results = []

        try:
            context = ssl.create_default_context()
            if int(smtp_port) == 465:
                server = smtplib.SMTP_SSL(smtp_host, int(smtp_port), timeout=30, context=context)
            else:
                server = smtplib.SMTP(smtp_host, int(smtp_port), timeout=30)
                server.starttls(context=context)
            server.login(sender_email, sender_password)
        except Exception as e:
            st.error(f"Login/connection failed — check the account on the main Bulk Email Sender page.\n\n{e}")
            server = None

        if server is not None:
            for i, (_, row) in enumerate(verified_df.iterrows()):
                row_dict = row.to_dict()
                to_addr = str(row_dict.get("Email", "")).strip()
                status = "skipped (no email)"
                if to_addr and "@" in to_addr:
                    try:
                        subject = render(subject_tpl, row_dict)
                        body = render(body_tpl, _render_row_for_body(row_dict))
                        msg = build_message(sender_email, to_addr, subject, body, is_html=True)
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
                file_name="outreach_send_log.csv",
                mime="text/csv",
            )
