# app.py
"""
Lead Management App — full production-ready version
Features:
- Landing page: overview of all teams & members
- Password-protected tabs: Daily Update, Reports, Admin Panel
- Daily/weekly/monthly lead tracking
- Team & member CRUD in Admin Panel
- Notes for team & member
- GitHub JSON sync for storage
- Safe rerun handling
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

ADMIN_PASSWORD = PASSWORDS.get("admin", "Admin@2025")
REPORT_PASSWORD = PASSWORDS.get("report", "Report@2025")
UPDATE_PASSWORD = PASSWORDS.get("update", "Update@2025")

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
    if "weekly_target" in merged:
        merged["weekly_target"] = merged["weekly_target"].fillna(0).astype(int)
    else:
        merged["weekly_target"] = 0
    if "monthly_target" in merged:
        merged["monthly_target"] = merged["monthly_target"].fillna(0).astype(int)
    else:
        merged["monthly_target"] = 0

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
        color = "#e24b4b"
    elif pct < 90:
        color = "#f0b429"
    else:
        color = "#16a34a"
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

def pct_to_bg(pct):
    try:
        pct = float(pct)
    except Exception:
        return ""
    if pct >= 90:
        return "background-color: #d1fae5;"
    if pct >= 60:
        return "background-color: #fff7ed;"
    if pct > 0:
        return "background-color: #ffe4e6;"
    return ""

def style_member_table(df):
    if df.empty:
        return df
    return df.style.applymap(lambda x: pct_to_bg(x) if isinstance(x, (int,float)) else "", subset=["weekly_pct","monthly_pct"])

# -----------------------
# Load initial data
# -----------------------
data, sha = load_data()

# -----------------------
# Streamlit Layout
# -----------------------
st.set_page_config(page_title="Lead Management", layout="wide")
st.markdown(BASE_CSS, unsafe_allow_html=True)

# Landing page
st.title("Lead Management Dashboard")
st.write("Overview (All teams & members)")

members_list = flatten_members(data)
members_df = pd.DataFrame(members_list)
leads_df = pd.DataFrame(data.get("leads", []))
members_df, teams_df = calc_totals(leads_df, members_df, period="All Time")

for _, team in teams_df.iterrows():
    st.markdown(f"<div class='card'><div class='team-header'><div class='team-title'>{team.team_name}</div><div class='badge'>Total Leads: {team.team_leads}</div></div></div>", unsafe_allow_html=True)

st.markdown("---")

# -----------------------
# Tabs: Password Protected
# -----------------------
tab_choice = st.sidebar.selectbox("Choose Panel", ["Daily Update", "Reports", "Admin Panel"])
password_input = st.sidebar.text_input("Enter Password", type="password")

# -----------------------
# Daily Update
# -----------------------
if tab_choice == "Daily Update":
    if password_input != UPDATE_PASSWORD:
        st.warning("Enter correct password to access Daily Update.")
        st.stop()
    st.header("Daily Lead Entry")

    team_choice = st.selectbox("Select Team", [t["team_name"] for t in data.get("teams", [])])
    t_obj = next((t for t in data["teams"] if t["team_name"]==team_choice), None)
    if t_obj is None:
        st.error("Team not found")
        st.stop()
    member_choice = st.selectbox("Select Member", [m["name"] for m in t_obj.get("members", [])])
    lead_count = st.number_input("Leads Generated Today", min_value=0, max_value=1000, value=0)
    entry_date = st.date_input("Date", value=date.today())
    if st.button("Save Lead"):
        member_obj = next((m for m in t_obj["members"] if m["name"]==member_choice), None)
        if member_obj is not None:
            new_entry = {
                "lead_id": gen_id("LEAD"),
                "member_id": member_obj["member_id"],
                "team_id": t_obj["team_id"],
                "lead_count": int(lead_count),
                "date": entry_date.isoformat()
            }
            data.setdefault("leads", []).append(new_entry)
            ok = save_data(data, f"Add lead: {member_choice} {entry_date}", sha)
            if ok:
                st.success("Lead saved ✅")
                data, sha = load_data()
                st.experimental_rerun()
            else:
                st.error("Failed to save lead.")

# -----------------------
# Reports
# -----------------------
elif tab_choice == "Reports":
    if password_input != REPORT_PASSWORD:
        st.warning("Enter correct password to access Reports.")
        st.stop()
    st.header("Reports / Notes")
    period_choice = st.selectbox("Select Period", ["All Time", "This Week", "This Month"])
    members_df, teams_df = calc_totals(leads_df, members_df, period_choice)
    st.subheader("Team Summary")
    st.dataframe(teams_df)
    st.subheader("Member Summary")
    styled_df = style_member_table(members_df)
    st.dataframe(styled_df)

    st.subheader("Add Notes")
    period_key = f"notes_{period_choice.replace(' ','_')}"
    existing_notes = data.get(period_key, {})
    team_note = st.text_area("Team Leader Note", value=existing_notes.get("team",""))
    member_note = st.text_area("Member Note", value=existing_notes.get("member",""))
    if st.button("Save Notes"):
        modified = False
        if team_note != existing_notes.get("team","") or member_note != existing_notes.get("member",""):
            modified = True
        if modified:
            data[period_key] = {"team":team_note,"member":member_note}
            ok = save_data(data, f"Update report notes {period_key}", sha)
            if ok:
                st.success("Notes saved ✅")
                data, sha = load_data()
                st.experimental_rerun()
            else:
                st.error("Failed to save notes.")

# -----------------------
# Admin Panel
# -----------------------
elif tab_choice == "Admin Panel":
    if password_input != ADMIN_PASSWORD:
        st.warning("Enter correct password to access Admin Panel.")
        st.stop()
    st.header("Admin Panel: Teams & Members")
    teams = data.get("teams", [])

    new_team_name = st.text_input("New Team Name")
    if st.button("Add Team"):
        if new_team_name:
            teams.append({"team_id": gen_id("TEAM"), "team_name": new_team_name, "members":[]})
            ok = save_data(data, f"Add team {new_team_name}", sha)
            if ok:
                st.success("Team added ✅")
                data, sha = load_data()
                st.experimental_rerun()
            else:
                st.error("Failed to add team.")

    st.markdown("---")
    for t in teams:
        st.subheader(f"Team: {t['team_name']}")
        rename_input = st.text_input(f"Rename {t['team_name']}", key=f"rename_{t['team_id']}")
        if st.button(f"Rename Team {t['team_name']}"):
            if rename_input:
                t["team_name"] = rename_input
                ok = save_data(data, f"Rename team {rename_input}", sha)
                if ok:
                    st.success("Team renamed ✅")
                    data, sha = load_data()
                    st.experimental_rerun()
                else:
                    st.error("Failed to rename team.")

        new_member_name = st.text_input(f"Add Member to {t['team_name']}", key=f"addmember_{t['team_id']}")
        weekly_target = st.number_input(f"Weekly Target for {t['team_name']}", min_value=0, max_value=1000, key=f"weekly_{t['team_id']}")
        monthly_target = st.number_input(f"Monthly Target for {t['team_name']}", min_value=0, max_value=5000, key=f"monthly_{t['team_id']}")
        if st.button(f"Add Member to {t['team_name']}"):
            if new_member_name:
                t["members"].append({
                    "member_id": gen_id("MEM"),
                    "name": new_member_name,
                    "weekly_target": weekly_target,
                    "monthly_target": monthly_target
                })
                ok = save_data(data, f"Add member {new_member_name}", sha)
                if ok:
                    st.success("Member added ✅")
                    data, sha = load_data()
                    st.experimental_rerun()
                else:
                    st.error("Failed to add member.")
