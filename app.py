# app.py
import streamlit as st
import pandas as pd
import requests
import json
from base64 import b64encode, b64decode
from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Optional
import io

# -----------------------
# CONFIG / HELPERS
# -----------------------

# Put these in Streamlit secrets (see README). Example:
# [github]
# token = "ghp_xxx"
# repo_owner = "your-username"
# repo_name = "lead-management-data"
# data_folder = "data"
#
# [admin]
# password = "supersecret"

GITHUB = st.secrets.get("github", {})
ADMIN = st.secrets.get("admin", {})

GITHUB_TOKEN = GITHUB.get("token", "")
REPO_OWNER = GITHUB.get("repo_owner", "")
REPO_NAME = GITHUB.get("repo_name", "")
DATA_FOLDER = GITHUB.get("data_folder", "data")

HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

# Small utilities
def gh_api_url(path: str) -> str:
    return f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"

def read_json_from_github(path: str) -> Any:
    url = gh_api_url(path)
    res = requests.get(url, headers=HEADERS)
    if res.status_code == 200:
        payload = res.json()
        content = b64decode(payload["content"]).decode()
        return json.loads(content), payload["sha"]
    elif res.status_code == 404:
        # Not found: return None to allow creating file
        return None, None
    else:
        st.error(f"GitHub read error: {res.status_code} {res.text}")
        return None, None

def write_json_to_github(path: str, data: Any, message: str, sha: Optional[str]=None) -> bool:
    url = gh_api_url(path)
    content = b64encode(json.dumps(data, indent=2, ensure_ascii=False).encode()).decode()
    body = {"message": message, "content": content}
    if sha:
        body["sha"] = sha
    res = requests.put(url, headers=HEADERS, data=json.dumps(body))
    if res.status_code in (200,201):
        return True
    else:
        st.error(f"GitHub write error: {res.status_code} {res.text}")
        return False

def ensure_year_file(year: int) -> None:
    path = f"{DATA_FOLDER}/leads/{year}.json"
    data, sha = read_json_from_github(path)
    if data is None:
        # create empty list file
        st.info(f"Creating leads file for year {year} in GitHub.")
        write_json_to_github(path, [], f"Initialize leads for {year}")

# -----------------------
# DATA ACCESS LAYERS
# -----------------------

def load_teams() -> List[Dict]:
    path = f"{DATA_FOLDER}/teams.json"
    data, _ = read_json_from_github(path)
    if data is None:
        return []
    return data

def load_members() -> List[Dict]:
    path = f"{DATA_FOLDER}/members.json"
    data, _ = read_json_from_github(path)
    if data is None:
        return []
    return data

def load_targets(year: int) -> List[Dict]:
    path = f"{DATA_FOLDER}/targets.json"
    data, _ = read_json_from_github(path)
    if data is None:
        return []
    # Filter for year (if targets include year)
    return [t for t in data if t.get("year", year) == year]

def load_leads_for_year(year: int) -> List[Dict]:
    path = f"{DATA_FOLDER}/leads/{year}.json"
    data, _ = read_json_from_github(path)
    if data is None:
        return []
    return data

def append_lead_entry(entry: Dict) -> bool:
    year = datetime.strptime(entry["date"], "%Y-%m-%d").year
    path = f"{DATA_FOLDER}/leads/{year}.json"
    data, sha = read_json_from_github(path)
    if data is None:
        data = []
        sha = None
    data.append(entry)
    success = write_json_to_github(path, data, f"Add lead {entry['member_id']} {entry['date']}", sha)
    return success

def save_teams(teams: List[Dict]) -> bool:
    path = f"{DATA_FOLDER}/teams.json"
    _, sha = read_json_from_github(path)
    return write_json_to_github(path, teams, "Update teams", sha)

def save_members(members: List[Dict]) -> bool:
    path = f"{DATA_FOLDER}/members.json"
    _, sha = read_json_from_github(path)
    return write_json_to_github(path, members, "Update members", sha)

def save_targets(all_targets: List[Dict]) -> bool:
    path = f"{DATA_FOLDER}/targets.json"
    _, sha = read_json_from_github(path)
    return write_json_to_github(path, all_targets, "Update targets", sha)

# -----------------------
# BUSINESS LOGIC
# -----------------------

def members_by_team(members: List[Dict], team_id: str) -> List[Dict]:
    return [m for m in members if m.get("team_id") == team_id]

def member_name(members: List[Dict], member_id: str) -> str:
    for m in members:
        if m.get("member_id") == member_id:
            return m.get("name")
    return member_id

def compute_weekly_monthly(df: pd.DataFrame, targets: List[Dict], year: int):
    # df: columns ['date','member_id','lead_count']
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()
    df['date'] = pd.to_datetime(df['date'])
    # Weekly: ISO week number + year
    df['iso_year'] = df['date'].dt.isocalendar().year
    df['iso_week'] = df['date'].dt.isocalendar().week
    weekly = df.groupby(['iso_year','iso_week','member_id'], as_index=False)['lead_count'].sum()

    # Monthly
    df['year'] = df['date'].dt.year
    df['month'] = df['date'].dt.month
    monthly = df.groupby(['year','month','member_id'], as_index=False)['lead_count'].sum()

    # attach targets
    t_df = pd.DataFrame(targets)
    if t_df.empty:
        t_df = pd.DataFrame(columns=['member_id','weekly_target','monthly_target','year'])
    weekly = weekly.merge(t_df[['member_id','weekly_target']], on='member_id', how='left')
    weekly['weekly_target'] = weekly['weekly_target'].fillna(0).astype(float)
    weekly['pct'] = (weekly['lead_count'] / weekly['weekly_target'].replace({0: pd.NA}))*100
    monthly = monthly.merge(t_df[['member_id','monthly_target','year']], on='member_id', how='left')
    monthly['monthly_target'] = monthly['monthly_target'].fillna(0).astype(float)
    monthly['pct'] = (monthly['lead_count'] / monthly['monthly_target'].replace({0: pd.NA}))*100
    return weekly, monthly

# -----------------------
# UI PAGES
# -----------------------

st.set_page_config(page_title="Lead Management", layout="wide")

st.title("📋 Lead Management — Streamlit")

# Ensure current year file exists
current_year = date.today().year
ensure_year_file(current_year)

tabs = st.tabs(["Daily Update", "Dashboard", "Report", "Admin Panel"])

# Load shared data
teams = load_teams()
members = load_members()
targets_all, _ = read_json_from_github(f"{DATA_FOLDER}/targets.json")  # may be None
if targets_all is None:
    targets_all = []
# targets_all is list of dicts
# (we'll filter per year where needed)

with tabs[0]:
    st.header("Daily Update")
    col1, col2 = st.columns([2,1])
    with col1:
        # Team select
        team_options = {t["team_name"]: t["team_id"] for t in teams}
        selected_team_name = st.selectbox("Select Team", ["-- Select Team --"] + list(team_options.keys()))
        selected_team_id = team_options.get(selected_team_name) if selected_team_name and selected_team_name != "-- Select Team --" else None

        # Members drop-down
        if selected_team_id:
            team_members = members_by_team(members, selected_team_id)
            member_map = {m["name"]: m["member_id"] for m in team_members}
            selected_member_name = st.selectbox("Select Member", ["-- Select Member --"] + list(member_map.keys()))
            selected_member_id = member_map.get(selected_member_name) if selected_member_name and selected_member_name != "-- Select Member --" else None
        else:
            selected_member_id = None
            selected_member_name = None

        dt = st.date_input("Date", value=date.today())
        lead_count = st.number_input("Lead Count", min_value=0, step=1, value=0)
        notes = st.text_area("Notes (optional)")

        if st.button("Submit Lead"):
            if not selected_member_id:
                st.error("Please select a team and member.")
            else:
                entry = {
                    "date": dt.strftime("%Y-%m-%d"),
                    "member_id": selected_member_id,
                    "lead_count": int(lead_count),
                    "notes": notes or ""
                }
                ok = append_lead_entry(entry)
                if ok:
                    st.success("Lead entry saved to GitHub.")
                else:
                    st.error("Failed to save. Check GitHub token/permissions.")

    with col2:
        st.markdown("#### Quick stats")
        # Show today's totals
        todays = load_leads_for_year(current_year)
        if todays:
            df_today = pd.DataFrame(todays)
            df_today['date'] = pd.to_datetime(df_today['date'])
            today_str = date.today().strftime("%Y-%m-%d")
            total_today = df_today[df_today['date'] == pd.to_datetime(today_str)]['lead_count'].sum()
            st.metric("Total leads today", int(total_today))
        else:
            st.write("No leads yet for the current year.")

with tabs[1]:
    st.header("Dashboard")
    # Load all needed data
    # Option to pick year / weekly/monthly filters
    selected_year = st.selectbox("Select Year", options=sorted([current_year] + [current_year-1, current_year-2]), index=0)
    leads = load_leads_for_year(int(selected_year))
    df = pd.DataFrame(leads) if leads else pd.DataFrame(columns=["date","member_id","lead_count"])
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
    # load targets for selected_year
    targets_for_year = [t for t in targets_all if t.get("year", selected_year) == int(selected_year)]

    st.subheader("Team and Individual Performance")
    # Compute team totals
    if df.empty:
        st.info("No lead data for selected year.")
    else:
        # member-level total for year
        member_totals = df.groupby('member_id', as_index=False)['lead_count'].sum()
        member_totals['member_name'] = member_totals['member_id'].apply(lambda x: member_name(members, x))
        # attach monthly/weekly target
        t_df = pd.DataFrame(targets_for_year)
        if t_df.empty:
            t_df = pd.DataFrame(columns=['member_id','weekly_target','monthly_target','year'])
        member_totals = member_totals.merge(t_df[['member_id','monthly_target']], on='member_id', how='left')
        member_totals['monthly_target'] = member_totals['monthly_target'].fillna(0)
        member_totals['pct_of_monthly'] = (member_totals['lead_count'] / member_totals['monthly_target'].replace({0: pd.NA}))*100

        st.dataframe(member_totals.rename(columns={
            "member_id":"Member ID", "member_name":"Name", "lead_count":"Leads (Year)", "monthly_target":"Monthly Target", "pct_of_monthly":"% of Monthly Target"
        }).fillna(""))

        # Team totals
        team_map = {t["team_id"]: t["team_name"] for t in teams}
        # map members to teams
        mem_df = pd.DataFrame(members)
        if not mem_df.empty:
            merged = member_totals.merge(mem_df[['member_id','team_id']], on='member_id', how='left')
            team_totals = merged.groupby('team_id', as_index=False)['lead_count'].sum()
            team_totals['team_name'] = team_totals['team_id'].map(team_map)
            st.subheader("Team Totals")
            st.table(team_totals[['team_name','lead_count']].rename(columns={'team_name':'Team','lead_count':'Leads (Year)'}))
        else:
            st.write("No member data to compute team totals.")

        # Weekly and monthly breakdowns
        weekly, monthly = compute_weekly_monthly(df, targets_for_year, int(selected_year))
        st.subheader("Recent Weekly Snapshot")
        # Show last 5 iso weeks
        if not weekly.empty:
            last_weeks = weekly.sort_values(['iso_year','iso_week'], ascending=[False,False]).head(12)
            st.dataframe(last_weeks)
        else:
            st.write("No weekly aggregates yet.")

        st.subheader("Recent Monthly Snapshot")
        if not monthly.empty:
            last_months = monthly.sort_values(['year','month'], ascending=[False,False]).head(12)
            st.dataframe(last_months)
        else:
            st.write("No monthly aggregates yet.")

with tabs[2]:
    st.header("Report")
    # Filters
    selected_year_report = st.selectbox("Year (report)", options=[current_year, current_year-1, current_year-2], index=0, key="report_year")
    leads_report = load_leads_for_year(int(selected_year_report))
    df_report = pd.DataFrame(leads_report) if leads_report else pd.DataFrame(columns=["date","member_id","lead_count","notes"])
    if not df_report.empty:
        df_report['date'] = pd.to_datetime(df_report['date'])
        # Add member name and team
        mem_df = pd.DataFrame(members)
        if not mem_df.empty:
            df_report = df_report.merge(mem_df[['member_id','name','team_id']], on='member_id', how='left')
            team_map = {t['team_id']: t['team_name'] for t in teams}
            df_report['team_name'] = df_report['team_id'].map(team_map)
        st.write("Filter results")
        c1, c2, c3 = st.columns([1,1,2])
        with c1:
            member_filter = st.selectbox("Member (optional)", options=["All"] + sorted(df_report['name'].dropna().unique().tolist()))
        with c2:
            team_filter = st.selectbox("Team (optional)", options=["All"] + sorted([t['team_name'] for t in teams]))
        with c3:
            date_range = st.date_input("Date range", value=(date(int(selected_year_report),1,1), date(int(selected_year_report),12,31)))

        filtered = df_report.copy()
        if member_filter and member_filter != "All":
            filtered = filtered[filtered['name']==member_filter]
        if team_filter and team_filter != "All":
            filtered = filtered[filtered['team_name']==team_filter]
        start_dt, end_dt = date_range
        filtered = filtered[(filtered['date'] >= pd.to_datetime(start_dt)) & (filtered['date'] <= pd.to_datetime(end_dt))]

        st.dataframe(filtered.sort_values('date', ascending=False).reset_index(drop=True))
        # CSV export
        csv = filtered.to_csv(index=False).encode()
        st.download_button(label="Download CSV", data=csv, file_name=f"leads_{selected_year_report}.csv", mime="text/csv")
    else:
        st.info("No lead data for selected year.")

with tabs[3]:
    st.header("Admin Panel")
    # Simple auth
    if "admin_authenticated" not in st.session_state:
        st.session_state["admin_authenticated"] = False
    if not st.session_state["admin_authenticated"]:
        pw = st.text_input("Admin password", type="password")
        if st.button("Login"):
            if ADMIN.get("password") and pw == ADMIN.get("password"):
                st.session_state["admin_authenticated"] = True
                st.success("Authenticated")
            else:
                st.error("Incorrect password. Set ADMIN password in Streamlit secrets.")
        st.stop()

    st.subheader("Teams")
    team_df = pd.DataFrame(teams) if teams else pd.DataFrame(columns=["team_id","team_name"])
    with st.form("teams_form", clear_on_submit=False):
        st.write("Existing teams")
        st.dataframe(team_df)
        new_id = st.text_input("New Team ID (e.g. T10)")
        new_name = st.text_input("New Team Name")
        if st.form_submit_button("Add Team"):
            if not new_id or not new_name:
                st.error("Provide both ID and name")
            else:
                teams.append({"team_id": new_id, "team_name": new_name})
                ok = save_teams(teams)
                if ok:
                    st.success("Team added.")
                    st.experimental_rerun()

    st.subheader("Members")
    members_df = pd.DataFrame(members) if members else pd.DataFrame(columns=["member_id","name","team_id","active"])
    with st.form("members_form", clear_on_submit=False):
        st.write("Existing members")
        st.dataframe(members_df)
        new_member_id = st.text_input("New Member ID (e.g. M10)")
        new_member_name = st.text_input("New Member Name")
        new_member_team = st.selectbox("Team for member", options=["-- Select --"] + [t["team_name"] for t in teams])
        is_active = st.checkbox("Active", value=True)
        if st.form_submit_button("Add Member"):
            if not new_member_id or not new_member_name or new_member_team == "-- Select --":
                st.error("Fill all details")
            else:
                # find team id
                team_id = next((t["team_id"] for t in teams if t["team_name"]==new_member_team), None)
                members.append({"member_id": new_member_id, "name": new_member_name, "team_id": team_id, "active": bool(is_active)})
                ok = save_members(members)
                if ok:
                    st.success("Member added.")
                    st.experimental_rerun()

    st.subheader("Targets")
    # targets_all is list (possibly None), we read again for fresh copy
    targets_all_latest, _ = read_json_from_github(f"{DATA_FOLDER}/targets.json")
    if targets_all_latest is None:
        targets_all_latest = []
    targ_df = pd.DataFrame(targets_all_latest) if targets_all_latest else pd.DataFrame(columns=["member_id","weekly_target","monthly_target","year"])
    st.write("Current targets")
    st.dataframe(targ_df)

    with st.form("targets_form", clear_on_submit=False):
        sel_member = st.selectbox("Member", options=["-- Select --"] + [m["name"] for m in members])
        sel_member_id = None
        if sel_member and sel_member != "-- Select --":
            sel_member_id = next((m["member_id"] for m in members if m["name"]==sel_member), None)
        new_weekly = st.number_input("Weekly target", min_value=0, value=0, step=1)
        new_monthly = st.number_input("Monthly target", min_value=0, value=0, step=1)
        target_year = st.number_input("Year for target", min_value=2000, max_value=2100, value=current_year)
        if st.form_submit_button("Save target"):
            if not sel_member_id:
                st.error("Select a member")
            else:
                # remove existing for same member+year
                updated = [t for t in targets_all_latest if not (t.get("member_id")==sel_member_id and t.get("year")==int(target_year))]
                updated.append({"member_id": sel_member_id, "weekly_target": int(new_weekly), "monthly_target": int(new_monthly), "year": int(target_year)})
                ok = save_targets(updated)
                if ok:
                    st.success("Saved target.")
                    st.experimental_rerun()

    st.subheader("Misc")
    st.write("Repository & data settings")
    st.write({
        "GitHub repo": f"{REPO_OWNER}/{REPO_NAME}",
        "Data folder": DATA_FOLDER,
        "Current year file": f"{DATA_FOLDER}/leads/{current_year}.json"
    })
    if st.button("Ensure year files for next 3 years"):
        for y in [current_year, current_year+1, current_year+2]:
            ensure_year_file(y)
        st.success("Ensured lead files for next 3 years.")
