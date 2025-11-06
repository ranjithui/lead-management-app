# app_fixed.py
"""
Lead Management App — Public Access Version (No Logins)
- All tabs visible from startup (no password/login)
- Dashboard, Daily Update, Reports, and Admin Panel unified
- GitHub data sync (load/save)
- CSV + Excel export
- One JSON file: data/leads_data.json
- Team and member management integrated
"""

import streamlit as st
import pandas as pd
import json
import os
import datetime
from io import BytesIO
import base64
import requests

# =========================================
# GitHub Setup (Modify with your repo info)
# =========================================
GITHUB_REPO = "yourusername/lead-management"
DATA_PATH = "data/leads_data.json"
GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN", "")

# =========================================
# GitHub Data Handling
# =========================================
def load_data():
    """Load leads_data.json from GitHub or local"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_PATH}"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
    try:
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        data_json = json.loads(base64.b64decode(res.json()["content"]).decode("utf-8"))
        sha = res.json()["sha"]
        return data_json, sha
    except Exception:
        if os.path.exists("data/leads_data.json"):
            with open("data/leads_data.json", "r") as f:
                return json.load(f), None
        return {"teams": {}, "leads": []}, None


def save_data(data, sha=None):
    """Save to GitHub repo (or local fallback)"""
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DATA_PATH}"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Content-Type": "application/json",
        }
        content_b64 = base64.b64encode(json.dumps(data, indent=2).encode("utf-8")).decode("utf-8")
        message = f"Update leads_data.json — {datetime.datetime.now().isoformat()}"
        payload = {
            "message": message,
            "content": content_b64,
        }
        if sha:
            payload["sha"] = sha
        requests.put(url, headers=headers, data=json.dumps(payload))
    except Exception:
        os.makedirs("data", exist_ok=True)
        with open("data/leads_data.json", "w") as f:
            json.dump(data, f, indent=2)


# =========================================
# Data Init
# =========================================
data, sha = load_data()
teams = data.get("teams", {})
leads = data.get("leads", [])

# =========================================
# Helper Functions
# =========================================
def get_team_summary():
    df = pd.DataFrame(leads)
    if df.empty:
        return pd.DataFrame(columns=["Team", "Member", "Total Leads", "Date"])
    summary = df.groupby(["team", "member"], as_index=False)["leads"].sum()
    summary.rename(columns={"leads": "Total Leads", "team": "Team", "member": "Member"}, inplace=True)
    return summary

def export_data(df):
    buffer = BytesIO()
    df.to_excel(buffer, index=False)
    st.download_button("📥 Download Excel", data=buffer.getvalue(), file_name="leads_report.xlsx")

# =========================================
# Streamlit UI
# =========================================
st.set_page_config("Lead Management Dashboard", layout="wide")

st.title("📊 Lead Management System (Public Access)")

tabs = st.tabs(["🏠 Dashboard", "📝 Daily Update", "📈 Reports", "⚙️ Admin Panel"])

# =========================================
# Dashboard Tab
# =========================================
with tabs[0]:
    st.subheader("Overall Team Performance")

    df_summary = get_team_summary()
    if df_summary.empty:
        st.info("No leads data yet.")
    else:
        st.dataframe(df_summary, use_container_width=True)
        total_leads = df_summary["Total Leads"].sum()
        st.metric("Total Leads (All Teams)", total_leads)

# =========================================
# Daily Update Tab
# =========================================
with tabs[1]:
    st.subheader("📝 Add Daily Lead Update")

    if not teams:
        st.warning("No teams available. Please add teams in Admin Panel.")
    else:
        team = st.selectbox("Select Team", list(teams.keys()))
        member = st.selectbox("Select Member", teams.get(team, []))
        leads_count = st.number_input("Leads Achieved Today", min_value=0, step=1)
        note = st.text_area("Notes (optional)")
        if st.button("Submit Lead Entry"):
            new_entry = {
                "team": team,
                "member": member,
                "leads": leads_count,
                "date": datetime.date.today().isoformat(),
                "note": note,
            }
            leads.append(new_entry)
            data["leads"] = leads
            save_data(data, sha)
            st.success("✅ Lead entry saved successfully!")

# =========================================
# Reports Tab
# =========================================
with tabs[2]:
    st.subheader("📈 Performance Reports")

    if not leads:
        st.info("No data to show yet.")
    else:
        df = pd.DataFrame(leads)
        df["date"] = pd.to_datetime(df["date"])
        time_filter = st.selectbox("Select Report Range", ["This Week", "This Month", "All Time"])

        today = datetime.date.today()
        if time_filter == "This Week":
            start_of_week = today - datetime.timedelta(days=today.weekday())
            df = df[df["date"].dt.date >= start_of_week]
        elif time_filter == "This Month":
            df = df[df["date"].dt.month == today.month]

        st.dataframe(df, use_container_width=True)
        st.bar_chart(df.groupby("team")["leads"].sum())

        export_data(df)

# =========================================
# Admin Panel Tab
# =========================================
with tabs[3]:
    st.subheader("⚙️ Team & Member Management")

    # Add Team
    new_team = st.text_input("Add New Team")
    if st.button("➕ Add Team") and new_team:
        if new_team not in teams:
            teams[new_team] = []
            data["teams"] = teams
            save_data(data, sha)
            st.success(f"Team '{new_team}' added!")
        else:
            st.warning("Team already exists.")

    # Add Member
    if teams:
        team_sel = st.selectbox("Select Team to Add Member", list(teams.keys()))
        new_member = st.text_input("Add Member to Team")
        if st.button("👤 Add Member") and new_member:
            if new_member not in teams[team_sel]:
                teams[team_sel].append(new_member)
                data["teams"] = teams
                save_data(data, sha)
                st.success(f"Member '{new_member}' added to '{team_sel}'!")
            else:
                st.warning("Member already exists in this team.")

    # Delete Team or Member
    st.divider()
    st.subheader("🗑️ Delete Data")
    del_team = st.selectbox("Select Team to Delete", [""] + list(teams.keys()))
    if del_team and st.button("❌ Delete Team"):
        teams.pop(del_team, None)
        data["teams"] = teams
        save_data(data, sha)
        st.warning(f"Team '{del_team}' deleted!")

    if teams:
        del_team2 = st.selectbox("Select Team to Delete Member From", list(teams.keys()))
        del_member = st.selectbox("Select Member", teams.get(del_team2, []))
        if st.button("❌ Delete Member"):
            teams[del_team2].remove(del_member)
            data["teams"] = teams
            save_data(data, sha)
            st.warning(f"Member '{del_member}' removed from '{del_team2}'!")

# =========================================
# End of App
# =========================================
st.caption("Built with ❤️ using Streamlit — Public Access Edition")
