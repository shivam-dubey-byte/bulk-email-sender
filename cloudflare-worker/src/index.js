// bulkemail.shivamrajdubey.com -> the actual Streamlit Cloud app.
// Streamlit Cloud doesn't support custom domains directly, so this Worker
// is just a vanity-domain redirect in front of it.

const TARGET = "https://bulk-email-sender-srd.streamlit.app/";

export default {
  async fetch(request) {
    const url = new URL(request.url);
    return Response.redirect(TARGET + url.search, 302);
  },
};
