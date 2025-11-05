# app.py
"""
Lead Management Streamlit App (Unified Data Model)
- Tabs: Daily Update, Dashboard, Report, Admin
- All data stored in one JSON file in GitHub under `data/leads_data.json`
- Admin can create team + members + targets in one step
- Uses GitHub Contents API (GitHub token in Streamlit secrets)
- Designed for long-term use (years)

📁 .streamlit/secrets.toml example:

[github]
token = "ghp_xxx"
repo_owner = "your-github-username"
repo_name = "your-repo-name"
data_path = "data/leads_data.json"

[admin]
password = "your-admin-password"
"""

import streamlit as st
import requests
import json
from base64 import b64encode, b64decode
from datetime import date, datetime
import pandas as pd
import uuid

# --------------------
# Config & Secrets
# --------------------
GITHUB = st.secrets.get("github", {})
ADMIN = st.secrets.get("admin", {})

GITHUB_TOKEN = GITHUB.get("token", "")
REPO_OWNER = GITHUB.get("repo_owner", "")
REPO_NAME = GITHUB.get("repo_name", "")
DATA_PATH = GITHUB.get("data_path", "data/leads_data.json")

HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

# --------------------
# GitHub Helpers
# --------------------
def gh_api_url() -> str:
    return f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{DATA_PATH}"

def load_data() -> dict:
    """Load entire leads data structure from GitHub JSON"""
    url = gh_api_url()
    res = requests.get(url, headers=HEADERS)
    if res.status_code == 200:
        payload = res.json()
        content = b64decode(payload["content"]).decode()
        try:
            data = json.loads(content)
            sha = payload.get("sha")
            return data, sha
        except Exception:
            return {"teams": [], "leads": []}, None
    elif res.status_code == 404:
        return {"teams": [], "leads": []}, None
    else:
        st.error(f"GitHub read error {res.status_code}: {res.text}")
        return {"teams": [], "leads": []}, None

def save_data(data: dict, message: str, sha: str = None) -> bool:
    """Save entire JSON back to GitHub"""
    url = gh_api_url()
    content = b64encode(json.dumps(data, indent=2, ensure_ascii=False).encode()).decode()
    body = {"message": message, "content": content}
    if sha:
        body["sha"] = sha
    res = requests.put(url, headers=HEADERS, data=json.dumps(body))
    return res.status_code in (200, 201)

def gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"

# --------------------
# Data Helpers
# --------------------
def get_all_members(data):
    members = []
    for team in data.get("teams", []):
        for m in team.get("members", []):
            m_copy = m.copy()
            m_copy["team_id"] = team["team_id"]
            m_copy["team_name"] = team["team_name"]
            members.append(m_copy)
    return members

# --------------------
# Streamlit App
# --------------------
st.set_page_config(page_title="Lead Management", layout="wide")
st.title("📊 Unified Lead Management System")

data, sha = load_data()
tabs = st.tabs(["Daily Update", "Dashboard", "Report", "Admin Panel"])

# ==========================================
# DAILY UPDATE TAB
# ==========================================
with tabs[0]:
    st.header("Daily Update")

    teams = data.get("teams", [])
    if not teams:
        st.info("No teams yet. Please create one in the Admin Panel.")
    else:
        team_names = [t["team_name"] for t in teams]
        team_choice = st.selectbox("Select Team", team_names)
        team = next(t for t in teams if t["team_name"] == team_choice)

        members = team.get("members", [])
        member_names = [m["name"] for m in members]
        member_choice = st.selectbox("Select Member", member_names)

        date_sel = st.date_input("Date", value=date.today())
        leads_count = st.number_input("Lead Count", min_value=0, value=0, step=1)

        if st.button("Save Lead"):
            entry = {
                "date": date_sel.strftime("%Y-%m-%d"),
                "team_id": team["team_id"],
                "member_id": next(m["member_id"] for m in members if m["name"] == member_choice),
                "lead_count": int(leads_count),
            }
            data.setdefault("leads", []).append(entry)
            if save_data(data, f"Add lead for {member_choice}", sha):
                st.success("Lead saved successfully!")
                st.rerun()
            else:
                st.error("Failed to save lead to GitHub.")

# ==========================================
# DASHBOARD TAB
# ==========================================
with tabs[1]:
    st.header("Dashboard")

    leads = pd.DataFrame(data.get("leads", []))
    if leads.empty:
        st.info("No lead data available.")
    else:
        members = pd.DataFrame(get_all_members(data))
        leads["date"] = pd.to_datetime(leads["date"])
        leads = leads.merge(members, on="member_id", how="left")

        agg = leads.groupby(["team_name", "name"], as_index=False)["lead_count"].sum()
        agg.rename(columns={"name": "Member", "lead_count": "Total Leads"}, inplace=True)

        st.subheader("📌 Team & Member Lead Summary")
        st.dataframe(agg)

        # Team totals
        team_total = agg.groupby("team_name", as_index=False)["Total Leads"].sum()
        st.subheader("🏆 Team Totals")
        st.table(team_total)

# ==========================================
# REPORT TAB
# ==========================================
with tabs[2]:
    st.header("Reports")

    leads = pd.DataFrame(data.get("leads", []))
    if leads.empty:
        st.info("No data to report.")
    else:
        leads["date"] = pd.to_datetime(leads["date"])
        members = pd.DataFrame(get_all_members(data))
        leads = leads.merge(members, on="member_id", how="left")

        start, end = st.date_input(
            "Select date range", [date.today().replace(day=1), date.today()]
        )

        mask = (leads["date"] >= pd.to_datetime(start)) & (leads["date"] <= pd.to_datetime(end))
        filtered = leads.loc[mask]

        st.dataframe(filtered.sort_values("date", ascending=False))
        st.download_button(
            "Download CSV",
            data=filtered.to_csv(index=False),
            file_name="lead_report.csv",
            mime="text/csv",
        )

# ==========================================
# ADMIN PANEL TAB
# ==========================================
with tabs[3]:
    st.header("Admin Panel")

    if "admin_auth" not in st.session_state:
        st.session_state.admin_auth = False

    if not st.session_state.admin_auth:
        pw = st.text_input("Admin Password", type="password")
        if st.button("Login"):
            if pw == ADMIN.get("password"):
                st.session_state.admin_auth = True
                st.success("Logged in successfully!")
                st.rerun()
            else:
                st.error("Invalid password.")
        st.stop()

    st.subheader("Create New Team with Members & Targets")
    with st.form("create_team"):
        team_name = st.text_input("Team Name")
        n_members = st.number_input("Number of Members", min_value=1, max_value=50, value=3)

        new_members = []
        for i in range(int(n_members)):
            cols = st.columns([3, 1, 1])
            with cols[0]:
                name = st.text_input(f"Member {i+1} Name", key=f"m_{i}")
            with cols[1]:
                weekly = st.number_input("Weekly Target", min_value=0, value=0, key=f"w_{i}")
            with cols[2]:
                monthly = st.number_input("Monthly Target", min_value=0, value=0, key=f"mo_{i}")
            new_members.append({
                "name": name.strip(),
                "member_id": gen_id("M"),
                "weekly_target": weekly,
                "monthly_target": monthly,
            })

        submit = st.form_submit_button("Save Team")
        if submit:
            if not team_name or any(m["name"] == "" for m in new_members):
                st.error("Please fill all required fields.")
            else:
                team_entry = {
                    "team_id": gen_id("T"),
                    "team_name": team_name,
                    "members": new_members,
                }
                data.setdefault("teams", []).append(team_entry)
                if save_data(data, f"Add team {team_name}", sha):
                    st.success(f"Team '{team_name}' saved successfully!")
                    st.rerun()
                else:
                    st.error("Failed to save data to GitHub.")

    st.markdown("---")
    st.subheader("Existing Data Overview")
    st.json(data, expanded=False)
