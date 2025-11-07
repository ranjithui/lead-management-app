"""
Lead Management App — Open Access Version
-----------------------------------------
All passwords and login prompts removed.
Features:
 - Dashboard, Daily Update, Reports, Admin Panel always visible
 - GitHub sync for persistent data (leads_data.json)
 - Safe rerun for updates
"""

import streamlit as st
import pandas as pd
import json
import requests
from datetime import datetime, timedelta
from base64 import b64encode, b64decode

# -----------------------------
# GITHUB CONFIGURATION
# -----------------------------
GITHUB_TOKEN = st.secrets["github"]["token"]
REPO_OWNER = st.secrets["github"]["repo_owner"]
REPO_NAME = st.secrets["github"]["repo_name"]
FILE_PATH = "leads_data.json"


# -----------------------------
# SAFE RERUN WRAPPER
# -----------------------------
def safe_rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            st.warning("Please refresh manually to update the view.")


# -----------------------------
# GITHUB FUNCTIONS
# -----------------------------
def gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def gh_api_url():
    return f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{FILE_PATH}"


def load_data():
    """Load leads_data.json from GitHub"""
    url = gh_api_url()
    res = requests.get(url, headers=gh_headers())
    if res.status_code == 200:
        payload = res.json()
        data = json.loads(b64decode(payload["content"]).decode())
        sha = payload.get("sha")
        st.session_state["gh_sha"] = sha
        return data, sha
    elif res.status_code == 404:
        st.warning("No data file found on GitHub. Creating a new one...")
        ok, new_sha = save_data({"teams": [], "leads": []}, "Initialize data file")
        return {"teams": [], "leads": []}, new_sha
    else:
        st.error(f"Failed to load data: {res.status_code}")
        return {"teams": [], "leads": []}, None


def save_data(data, message, sha=None):
    """Save JSON data to GitHub and return (ok, new_sha)"""
    url = gh_api_url()
    content = b64encode(json.dumps(data, indent=2, ensure_ascii=False).encode()).decode()
    payload = {"message": message, "content": content}
    if sha:
        payload["sha"] = sha

    res = requests.put(url, headers=gh_headers(), data=json.dumps(payload))

    if res.status_code in (200, 201):
        new_sha = res.json().get("content", {}).get("sha")
        st.session_state["gh_sha"] = new_sha
        st.session_state["last_save"] = datetime.utcnow().isoformat()
        return True, new_sha
    else:
        st.error(f"GitHub save error {res.status_code}: {res.text}")
        return False, None


# -----------------------------
# INITIAL DATA LOAD
# -----------------------------
if "gh_sha" not in st.session_state:
    st.session_state["gh_sha"] = None

data, sha = load_data()
teams = data.get("teams", [])
leads = data.get("leads", [])


# -----------------------------
# SIDEBAR NAVIGATION
# -----------------------------
st.sidebar.title("📊 Lead Management App")
tabs = ["Dashboard", "Daily Update", "Reports", "Admin Panel"]
selected_tab = st.sidebar.radio("Navigate", tabs)


# -----------------------------
# DASHBOARD TAB
# -----------------------------
if selected_tab == "Dashboard":
    st.title("📈 Lead Management Dashboard")

    df = pd.DataFrame(leads)

    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
        today = datetime.today().date()
        week_start = today - timedelta(days=today.weekday())

        df_today = df[df["date"] == today]
        df_week = df[df["date"] >= week_start]

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Leads", len(df))
        col2.metric("Today's Leads", len(df_today))
        col3.metric("This Week", len(df_week))

        st.subheader("📋 All Leads")
        st.dataframe(df.sort_values("date", ascending=False), use_container_width=True)
    else:
        st.info("No leads yet. Add some in the Daily Update tab.")


# -----------------------------
# DAILY UPDATE TAB
# -----------------------------
elif selected_tab == "Daily Update":
    st.title("📝 Daily Lead Entry")

    if not teams:
        st.warning("No teams found. Please add teams in Admin Panel.")
    else:
        team_names = [t["name"] for t in teams]
        team_choice = st.selectbox("Select Team", team_names)
        team_members = next((t["members"] for t in teams if t["name"] == team_choice), [])
        member_choice = st.selectbox("Select Member", team_members)
        date_entry = st.date_input("Date", datetime.today())
        leads_count = st.number_input("Leads Generated", min_value=0, step=1)
        notes = st.text_area("Notes (optional)")

        if st.button("💾 Save Lead"):
            new_entry = {
                "team": team_choice,
                "member": member_choice,
                "date": str(date_entry),
                "count": int(leads_count),
                "notes": notes,
            }
            leads.append(new_entry)
            data["leads"] = leads
            ok, new_sha = save_data(
                data,
                f"Add lead: {member_choice} on {date_entry}",
                st.session_state.get("gh_sha"),
            )
            if ok:
                sha = new_sha
                st.success("Lead saved ✅")
                safe_rerun()


# -----------------------------
# REPORTS TAB
# -----------------------------
elif selected_tab == "Reports":
    st.title("📅 Reports")

    if not leads:
        st.info("No data available yet.")
    else:
        df = pd.DataFrame(leads)
        df["date"] = pd.to_datetime(df["date"]).dt.date

        today = datetime.today().date()
        week_start = today - timedelta(days=today.weekday())
        df_week = df[df["date"] >= week_start]
        df_month = df[df["date"] >= today.replace(day=1)]

        st.subheader("📊 This Week's Report")
        weekly = df_week.groupby(["team", "member"])["count"].sum().reset_index()
        st.dataframe(weekly, use_container_width=True)

        st.subheader("📆 This Month's Report")
        monthly = df_month.groupby(["team", "member"])["count"].sum().reset_index()
        st.dataframe(monthly, use_container_width=True)

        total_leads = df["count"].sum()
        st.metric("Total Leads Recorded", total_leads)


# -----------------------------
# ADMIN PANEL TAB
# -----------------------------
elif selected_tab == "Admin Panel":
    st.title("⚙️ Admin Panel")

    st.subheader("Teams & Members")

    if teams:
        for t in teams:
            with st.expander(f"Team: {t['name']}"):
                st.write("👥 Members:", ", ".join(t["members"]) if t["members"] else "No members yet.")
                new_member = st.text_input(f"Add member to {t['name']}", key=f"add_{t['name']}")
                if st.button(f"➕ Add Member to {t['name']}", key=f"btn_{t['name']}"):
                    if new_member:
                        t["members"].append(new_member)
                        data["teams"] = teams
                        ok, new_sha = save_data(
                            data,
                            f"Add member {new_member} to {t['name']}",
                            st.session_state.get("gh_sha"),
                        )
                        if ok:
                            sha = new_sha
                            st.success("Member added ✅")
                            safe_rerun()
    else:
        st.info("No teams created yet.")

    st.divider()
    st.subheader("Add New Team")

    new_team = st.text_input("Team Name")
    if st.button("🚀 Create Team"):
        if new_team:
            teams.append({"name": new_team, "members": []})
            data["teams"] = teams
            ok, new_sha = save_data(
                data,
                f"Add new team {new_team}",
                st.session_state.get("gh_sha"),
            )
            if ok:
                sha = new_sha
                st.success("Team added ✅")
                safe_rerun()
        else:
            st.error("Please enter a team name.")


# -----------------------------
# FOOTER
# -----------------------------
st.sidebar.markdown("---")
st.sidebar.caption("Lead Management App — Open Access Version | © 2025")

