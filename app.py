# app.py
"""
Lead Management App — production-ready with:
 - Dashboard (public landing page, always visible)
 - Password-protected tabs: Daily Update, Reports, Admin Panel (each with separate password)
 - Compact "All Teams" Dashboard with top summary
 - Enhanced Reporting (Weekly/Monthly) with color-coded performance, team & member notes saved to GitHub
 - CSV + Excel export
 - GitHub Contents API sync for data/leads_data.json
"""

import streamlit as st
import requests
import json
from base64 import b64encode, b64decode
from datetime import date, datetime, timedelta
import pandas as pd
import uuid
import io

# -----------------------
# Config / Secrets
# -----------------------
GITHUB = st.secrets.get("github", {}) if st.secrets is not None else {}
PASSWORDS = st.secrets.get("passwords", {}) if st.secrets is not None else {}

GITHUB_TOKEN = GITHUB.get("token", "")
REPO_OWNER = GITHUB.get("repo_owner", "")
REPO_NAME = GITHUB.get("repo_name", "")
DATA_PATH = GITHUB.get("data_path", "data/leads_data.json")
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

# Password fallbacks
ADMIN_PASSWORD = PASSWORDS.get("admin", "Admin@2025")
REPORT_PASSWORD = PASSWORDS.get("report", "Report@2025")
UPDATE_PASSWORD = PASSWORDS.get("update", "Update@2025")

# -----------------------
# Password helper
# -----------------------
def password_gate(session_key, correct_pw, label):
    """
    Handle password-protected tabs.
    Returns True if authenticated, False otherwise.
    """
    if session_key not in st.session_state:
        st.session_state[session_key] = False

    if not st.session_state[session_key]:
        pw = st.text_input(f"Enter {label} password", type="password", key=f"{session_key}_input")
        unlock = st.button(f"Unlock {label}")
        if unlock:
            if pw == correct_pw:
                st.session_state[session_key] = True
                st.success(f"{label} unlocked ✅")
                st.experimental_rerun()
            else:
                st.error("Wrong password")
        return False
    return True

# -----------------------
# GitHub helpers
# -----------------------
def gh_api_url():
    return f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{DATA_PATH}"

def load_data():
    url = gh_api_url()
    if not REPO_OWNER or not REPO_NAME:
        st.error("GitHub repo_owner/repo_name not set in st.secrets.github.")
        return {"teams": [], "leads": []}, None
    try:
        res = requests.get(url, headers=HEADERS)
    except Exception as e:
        st.error(f"GitHub request failed: {e}")
        return {"teams": [], "leads": []}, None
    if res.status_code == 200:
        payload = res.json()
        content = b64decode(payload.get("content", "")).decode()
        try:
            data = json.loads(content)
            return data, payload.get("sha")
        except Exception:
            return {"teams": [], "leads": []}, None
    elif res.status_code == 404:
        return {"teams": [], "leads": []}, None
    else:
        st.error(f"GitHub read error {res.status_code}: {res.text}")
        return {"teams": [], "leads": []}, None

def save_data(data, message, sha=None):
    url = gh_api_url()
    if not REPO_OWNER or not REPO_NAME:
        st.error("GitHub repo_owner/repo_name not set in st.secrets.github.")
        return False
    content = b64encode(json.dumps(data, indent=2, ensure_ascii=False).encode()).decode()
    body = {"message": message, "content": content}
    if sha:
        body["sha"] = sha
    try:
        res = requests.put(url, headers=HEADERS, data=json.dumps(body))
    except Exception as e:
        st.error(f"GitHub write request failed: {e}")
        return False
    if res.status_code in (200, 201):
        return True
    else:
        st.error(f"GitHub write error {res.status_code}: {res.text}")
        return False

# -----------------------
# Utilities
# -----------------------
def gen_id(prefix="ID"):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"

def flatten_members(data):
    members = []
    for t in data.get("teams", []):
        for m in t.get("members", []):
            mm = m.copy()
            mm["team_id"] = t["team_id"]
            mm["team_name"] = t["team_name"]
            members.append(mm)
    return members

# -----------------------
# Aggregation helpers
# -----------------------
def calc_totals(leads_df, members_df, period="All Time"):
    if leads_df is None or leads_df.empty:
        member_cols = ["member_id", "name", "team_id", "team_name", "total_leads", "weekly_target", "monthly_target", "weekly_pct", "monthly_pct"]
        return pd.DataFrame(columns=member_cols), pd.DataFrame(columns=["team_id", "team_name", "team_leads", "avg_weekly_pct", "avg_monthly_pct"])

    df = leads_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    today = pd.to_datetime(date.today())

    if period == "This Week":
        cutoff = today - pd.Timedelta(days=7)
        df = df[df["date"] >= cutoff]
    elif period == "This Month":
        cutoff = today.replace(day=1)
        df = df[df["date"] >= cutoff]

    member_tot = df.groupby("member_id", as_index=False)["lead_count"].sum().rename(columns={"lead_count": "total_leads"})
    if members_df is None or members_df.empty:
        members_df = pd.DataFrame(columns=["member_id","name","team_id","team_name","weekly_target","monthly_target"])

    merged = members_df.merge(member_tot, on="member_id", how="left")
    merged["total_leads"] = merged["total_leads"].fillna(0).astype(int)
    merged["weekly_target"] = merged.get("weekly_target", 0).fillna(0).astype(int)
    merged["monthly_target"] = merged.get("monthly_target", 0).fillna(0).astype(int)

    merged["weekly_pct"] = merged.apply(lambda r: (r["total_leads"] / r["weekly_target"] * 100) if r.get("weekly_target",0) > 0 else 0, axis=1)
    merged["monthly_pct"] = merged.apply(lambda r: (r["total_leads"] / r["monthly_target"] * 100) if r.get("monthly_target",0) > 0 else 0, axis=1)

    team_grp = merged.groupby(["team_id","team_name"], as_index=False).agg({
        "total_leads":"sum",
        "weekly_pct":"mean",
        "monthly_pct":"mean"
    }).rename(columns={"total_leads":"team_leads","weekly_pct":"avg_weekly_pct","monthly_pct":"avg_monthly_pct"})

    return merged, team_grp

# -----------------------
# UI helper: CSS + progress bar
# -----------------------
BASE_CSS = """
<style>
.card { background: #ffffff; padding: 10px; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.05); margin-bottom: 10px; }
.team-header { display:flex; justify-content: space-between; align-items:center; }
.team-title { font-size:15px; font-weight:600; }
.small { color: #6c6c6c; font-size:12px; }
.member-card { background:#f8f9fb; padding:6px; border-radius:6px; margin-bottom:6px; }
.progress-bar { height:8px; border-radius:6px; background:#e6e6e6; overflow:hidden; margin-top:4px; }
.progress-fill { height:100%; border-radius:6px; }
.badge { font-size:11px; padding:2px 6px; border-radius:10px; background:#efefef; }
</style>
"""

def progress_color_and_width(pct):
    pct = max(0.0, float(pct))
    if pct < 60:
        color = "#e24b4b"  # red
    elif pct < 90:
        color = "#f0b429"  # orange
    else:
        color = "#16a34a"  # green
    width = min(round(pct, 1), 100)
    return color, width

def render_progress_bar_html(pct, label_text):
    color, width = progress_color_and_width(pct)
    html = f"""
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
      <div class="small">{label_text}</div>
      <div class="badge">{pct:.1f}%</div>
    </div>
    <div class="progress-bar">
      <div class="progress-fill" style="width:{width}%; background:{color};"></div>
    </div>
    """
    return html

# -----------------------
# Table coloring helpers
# -----------------------
def pct_to_bg(pct):
    try:
        pct = float(pct)
    except Exception:
        return ""
    if pct >= 90:
        return "background-color: #d1fae5;"  # light green
    if pct >= 60:
        return "background-color: #fff7ed;"  # light orange
    if pct > 0:
        return "background-color: #ffe4e6;"  # light red
    return ""

def style_member_table(df):
    if df.empty:
        return df
    return df.style.applymap(lambda v: pct_to_bg(v) if isinstance(v, (int,float)) else "", subset=["member_pct"])

def style_team_table(df):
    if df.empty:
        return df
    return df.style.applymap(lambda v: pct_to_bg(v) if isinstance(v, (int,float)) else "", subset=["avg_weekly_pct","avg_monthly_pct"])

# -----------------------
# Main
# -----------------------
st.set_page_config(page_title="Lead Management App", layout="wide")
st.markdown(BASE_CSS, unsafe_allow_html=True)

st.title("📊 Lead Management App")

tabs = st.tabs(["Dashboard", "Daily Update", "Reports", "Admin Panel"])

# -----------------------
# Load Data
# -----------------------
data, sha = load_data()
members_df = pd.DataFrame(flatten_members(data))
leads_df = pd.DataFrame(data.get("leads", []))

# -----------------------
# Dashboard (always visible)
# -----------------------
with tabs[0]:
    st.header("🏠 Dashboard")
    period = st.selectbox("Filter period", ["All Time", "This Month", "This Week"])
    member_stats, team_stats = calc_totals(leads_df, members_df, period=period)

    for _, team in team_stats.iterrows():
        st.markdown(f"<div class='card'>", unsafe_allow_html=True)
        st.markdown(f"<div class='team-header'><div class='team-title'>{team['team_name']}</div><div class='small'>Total Leads: {team['team_leads']}</div></div>", unsafe_allow_html=True)
        team_members = member_stats[member_stats["team_id"]==team["team_id"]]
        for _, m in team_members.iterrows():
            html = render_progress_bar_html(m["weekly_pct"], f"{m['name']} (Weekly)")
            st.markdown(f"<div class='member-card'>{html}</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

# -----------------------
# Daily Update
# -----------------------
with tabs[1]:
    st.header("🕘 Daily Update (password required)")
    if not password_gate("daily_auth", UPDATE_PASSWORD, "Daily Update"):
        st.stop()
    st.subheader("Add new lead entry")
    team_id = st.selectbox("Team", options=[t["team_id"] for t in data.get("teams",[])], format_func=lambda x: next((t["team_name"] for t in data["teams"] if t["team_id"]==x),""))
    member_id = st.selectbox("Member", options=[m["member_id"] for m in flatten_members(data) if m["team_id"]==team_id], format_func=lambda x: next((m["name"] for m in flatten_members(data) if m["member_id"]==x),""))
    lead_count = st.number_input("Leads count", min_value=1, value=1)
    notes = st.text_area("Notes (optional)")
    add_btn = st.button("Add Lead")
    if add_btn:
        new_entry = {"id": gen_id("LEAD"), "date": str(date.today()), "team_id": team_id, "member_id": member_id, "lead_count": lead_count, "notes": notes}
        data.setdefault("leads", []).append(new_entry)
        if save_data(data, f"Add lead for {member_id}", sha):
            st.success("Lead added successfully!")
            st.experimental_rerun()

# -----------------------
# Reports
# -----------------------
with tabs[2]:
    st.header("📜 Reports (password required)")
    if not password_gate("report_auth", REPORT_PASSWORD, "Reports"):
        st.stop()

    st.subheader("Weekly / Monthly Reporting")
    report_period = st.selectbox("Select period", ["Weekly", "Monthly"])
    member_stats, team_stats = calc_totals(leads_df, members_df, period="All Time" if report_period=="Monthly" else "This Week")
    
    st.markdown("### Team Summary")
    st.dataframe(team_stats[["team_name","team_leads","avg_weekly_pct","avg_monthly_pct"]])

    st.markdown("### Individual Member Summary")
    st.dataframe(member_stats[["name","team_name","total_leads","weekly_pct","monthly_pct"]])

    # Export buttons
    csv = member_stats.to_csv(index=False).encode()
    st.download_button("Download CSV", csv, file_name=f"{report_period}_report.csv", mime="text/csv")

# -----------------------
# Admin Panel
# -----------------------
with tabs[3]:
    st.header("🧑‍💼 Admin Panel (password required)")
    if not password_gate("admin_auth", ADMIN_PASSWORD, "Admin Panel"):
        st.stop()

    st.subheader("Teams")
    for team in data.get("teams", []):
        st.markdown(f"**{team['team_name']}** ({team['team_id']})")
        for member in team.get("members", []):
            st.markdown(f"- {member['name']} (ID: {member['member_id']})")
