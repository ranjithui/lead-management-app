# app.py
"""
Lead Management Streamlit App
- 4 tabs: Daily Update, Dashboard, Report, Admin
- Data stored as JSON files in the same GitHub repo under `data/`
- Admin can create team + members + targets in one form
- Uses GitHub Contents API; token stored in Streamlit secrets

Place this file in your app repository. Make sure .streamlit/secrets.toml contains:

[github]
token = "ghp_xxx"
repo_owner = "your-github-username"
repo_name = "your-repo-name"
data_folder = "data"

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
from typing import Any, Tuple, List, Dict, Optional
import io

# --------------------
# Config & secrets
# --------------------
GITHUB = st.secrets.get("github", {})
ADMIN = st.secrets.get("admin", {})

GITHUB_TOKEN = GITHUB.get("token", "")
REPO_OWNER = GITHUB.get("repo_owner", "")
REPO_NAME = GITHUB.get("repo_name", "")
DATA_FOLDER = GITHUB.get("data_folder", "data")

HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

# --------------------
# GitHub helpers
# --------------------

def gh_api_url(path: str) -> str:
    return f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"


def read_json_from_github(path: str) -> Tuple[Optional[Any], Optional[str]]:
    """Return (data, sha) or (None, None) if not found or error"""
    url = gh_api_url(path)
    res = requests.get(url, headers=HEADERS)
    if res.status_code == 200:
        payload = res.json()
        content = b64decode(payload["content"]).decode()
        try:
            return json.loads(content), payload.get("sha")
        except Exception:
            return None, None
    elif res.status_code == 404:
        return None, None
    else:
        st.error(f"GitHub read error {res.status_code}: {res.text}")
        return None, None


def write_json_to_github(path: str, data: Any, message: str, sha: Optional[str] = None) -> bool:
    url = gh_api_url(path)
    content = b64encode(json.dumps(data, indent=2, ensure_ascii=False).encode()).decode()
    body = {"message": message, "content": content}
    if sha:
        body["sha"] = sha
    res = requests.put(url, headers=HEADERS, data=json.dumps(body))
    if res.status_code in (200, 201):
        return True
    else:
        st.error(f"GitHub write error {res.status_code}: {res.text}")
        return False


def ensure_year_file(year: int) -> None:
    path = f"{DATA_FOLDER}/leads/{year}.json"
    data, sha = read_json_from_github(path)
    if data is None:
        write_json_to_github(path, [], f"Initialize leads for {year}")

# --------------------
# Utility helpers
# --------------------

def gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def safe_rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass

# --------------------
# Data loaders
# --------------------

def load_teams() -> List[Dict]:
    data, _ = read_json_from_github(f"{DATA_FOLDER}/teams.json")
    return data or []


def load_members() -> List[Dict]:
    data, _ = read_json_from_github(f"{DATA_FOLDER}/members.json")
    return data or []


def load_targets() -> List[Dict]:
    data, _ = read_json_from_github(f"{DATA_FOLDER}/targets.json")
    return data or []


def load_leads_for_year(year: int) -> List[Dict]:
    data, _ = read_json_from_github(f"{DATA_FOLDER}/leads/{year}.json")
    return data or []

# Save functions that preserve sha to reduce conflicts

def save_teams(teams: List[Dict]) -> bool:
    _, sha = read_json_from_github(f"{DATA_FOLDER}/teams.json")
    return write_json_to_github(f"{DATA_FOLDER}/teams.json", teams, "Update teams", sha)


def save_members(members: List[Dict]) -> bool:
    _, sha = read_json_from_github(f"{DATA_FOLDER}/members.json")
    return write_json_to_github(f"{DATA_FOLDER}/members.json", members, "Update members", sha)


def save_targets(targets: List[Dict]) -> bool:
    _, sha = read_json_from_github(f"{DATA_FOLDER}/targets.json")
    return write_json_to_github(f"{DATA_FOLDER}/targets.json", targets, "Update targets", sha)


def append_lead(entry: Dict) -> bool:
    year = datetime.strptime(entry["date"], "%Y-%m-%d").year
    path = f"{DATA_FOLDER}/leads/{year}.json"
    data, sha = read_json_from_github(path)
    if data is None:
        data = []
        sha = None
    data.append(entry)
    return write_json_to_github(path, data, f"Add lead {entry.get('member_id')} {entry.get('date')}", sha)

# --------------------
# Business helpers
# --------------------

def member_name(members: List[Dict], member_id: str) -> str:
    for m in members:
        if m.get("member_id") == member_id:
            return m.get("name")
    return member_id

# Compute weekly/monthly aggregates

def compute_aggregates(leads: List[Dict], targets: List[Dict]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.DataFrame(leads) if leads else pd.DataFrame(columns=["date", "member_id", "lead_count"])
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df["lead_count"] = df["lead_count"].astype(int)
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["iso_year"] = df["date"].dt.isocalendar().year
    df["iso_week"] = df["date"].dt.isocalendar().week

    weekly = df.groupby(["iso_year", "iso_week", "member_id"], as_index=False)["lead_count"].sum()
    monthly = df.groupby(["year", "month", "member_id"], as_index=False)["lead_count"].sum()

    tdf = pd.DataFrame(targets) if targets else pd.DataFrame(columns=["member_id", "weekly_target", "monthly_target", "year"])
    weekly = weekly.merge(tdf[["member_id", "weekly_target"]], on="member_id", how="left")
    weekly["weekly_target"] = weekly["weekly_target"].fillna(0)
    weekly["pct"] = weekly.apply(lambda r: (r["lead_count"] / r["weekly_target"] * 100) if r["weekly_target"]>0 else None, axis=1)

    monthly = monthly.merge(tdf[["member_id", "monthly_target", "year"]], on="member_id", how="left")
    monthly["monthly_target"] = monthly["monthly_target"].fillna(0)
    monthly["pct"] = monthly.apply(lambda r: (r["lead_count"] / r["monthly_target"] * 100) if r["monthly_target"]>0 else None, axis=1)

    return weekly, monthly

# --------------------
# Streamlit App UI
# --------------------

st.set_page_config(page_title="Lead Management", layout="wide")
st.title("📈 Lead Management System")

# Ensure current year file exists
current_year = date.today().year
ensure_year_file(current_year)

tabs = st.tabs(["Daily Update", "Dashboard", "Report", "Admin Panel"])

# Load shared data
teams = load_teams()
members = load_members()
targets = load_targets()

# ----- Daily Update -----
with tabs[0]:
    st.header("Daily Update")
    col1, col2 = st.columns([3,1])
    with col1:
        team_options = {t["team_name"]: t["team_id"] for t in teams}
        team_choice = st.selectbox("Select Team", ["-- Select --"] + list(team_options.keys()))
        team_id = team_options.get(team_choice) if team_choice and team_choice != "-- Select --" else None

        member_map = {m["name"]: m["member_id"] for m in members if m.get("team_id") == team_id} if team_id else {}
        member_choice = st.selectbox("Select Member", ["-- Select --"] + list(member_map.keys()))
        member_id = member_map.get(member_choice) if member_choice and member_choice != "-- Select --" else None

        dt = st.date_input("Date", value=date.today())
        lead_count = st.number_input("Lead Count", min_value=0, value=0, step=1)
        notes = st.text_area("Notes (optional)")

        if st.button("Submit Lead"):
            if not member_id:
                st.error("Select a team and member before submitting.")
            else:
                entry = {
                    "date": dt.strftime("%Y-%m-%d"),
                    "member_id": member_id,
                    "lead_count": int(lead_count),
                    "notes": notes or ""
                }
                ok = append_lead(entry)
                if ok:
                    st.success("Lead saved.")
                    safe_rerun()
                else:
                    st.error("Failed to save lead. Check GitHub settings.")
    with col2:
        st.markdown("#### Quick stats")
        leads_this_year = load_leads_for_year(current_year)
        if leads_this_year:
            dfy = pd.DataFrame(leads_this_year)
            dfy["date"] = pd.to_datetime(dfy["date"])
            today_str = pd.to_datetime(date.today())
            tot_today = int(dfy[dfy["date"]==today_str]["lead_count"].sum())
            st.metric("Total today", tot_today)
        else:
            st.write("No data for current year yet.")

# ----- Dashboard -----
with tabs[1]:
    st.header("Dashboard")
    year_options = sorted(list({current_year, current_year-1, current_year-2}), reverse=True)
    selected_year = st.selectbox("Select Year", options=year_options, index=0)

    leads = load_leads_for_year(int(selected_year))
    weekly_df, monthly_df = compute_aggregates(leads, targets)

    st.subheader("Team & Individual Summary")
    if leads:
        df_all = pd.DataFrame(leads)
        df_sum = df_all.groupby('member_id', as_index=False)['lead_count'].sum()
        df_sum['name'] = df_sum['member_id'].apply(lambda x: member_name(members, x))
        # merge monthly target
        tdf = pd.DataFrame(targets)
        if not tdf.empty:
            tdf_y = tdf[tdf['year'] == int(selected_year)]
            df_sum = df_sum.merge(tdf_y[['member_id','monthly_target']], on='member_id', how='left')
            df_sum['monthly_target'] = df_sum['monthly_target'].fillna(0)
            df_sum['pct_month'] = df_sum.apply(lambda r: (r['lead_count']/r['monthly_target']*100) if r['monthly_target']>0 else None, axis=1)
        st.dataframe(df_sum.rename(columns={'lead_count':'Leads (Year)','name':'Member'}))

        # team totals
        mem_df = pd.DataFrame(members)
        if not mem_df.empty:
            merged = df_sum.merge(mem_df[['member_id','team_id']], on='member_id', how='left')
            team_map = {t['team_id']: t['team_name'] for t in teams}
            team_totals = merged.groupby('team_id', as_index=False)['Leads (Year)'].sum()
            if not team_totals.empty:
                team_totals['team_name'] = team_totals['team_id'].map(team_map)
                st.table(team_totals[['team_name','Leads (Year)']].rename(columns={'team_name':'Team'}))

        st.subheader('Monthly snapshot (last months)')
        if not monthly_df.empty:
            st.dataframe(monthly_df.sort_values(['year','month'], ascending=[False,False]).head(12))
        else:
            st.write('No monthly aggregates yet.')

        st.subheader('Weekly snapshot (recent)')
        if not weekly_df.empty:
            st.dataframe(weekly_df.sort_values(['iso_year','iso_week'], ascending=[False,False]).head(12))
        else:
            st.write('No weekly aggregates yet.')
    else:
        st.info('No lead data for selected year.')

# ----- Report -----
with tabs[2]:
    st.header("Report")
    report_year = st.selectbox("Report Year", options=year_options, index=0, key='report_year')
    leads_rep = load_leads_for_year(int(report_year))
    df_rep = pd.DataFrame(leads_rep) if leads_rep else pd.DataFrame(columns=['date','member_id','lead_count','notes'])
    if not df_rep.empty:
        df_rep['date'] = pd.to_datetime(df_rep['date'])
        # add member & team
        mem_df = pd.DataFrame(members)
        if not mem_df.empty:
            df_rep = df_rep.merge(mem_df[['member_id','name','team_id']], on='member_id', how='left')
            team_map = {t['team_id']:t['team_name'] for t in teams}
            df_rep['team_name'] = df_rep['team_id'].map(team_map)

        c1,c2,c3 = st.columns([1,1,2])
        with c1:
            mfilter = st.selectbox('Member (optional)', options=['All'] + sorted(df_rep['name'].dropna().unique().tolist()), key='rep_m')
        with c2:
            tfilter = st.selectbox('Team (optional)', options=['All'] + sorted([t['team_name'] for t in teams]), key='rep_t')
        with c3:
            date_range = st.date_input('Date range', value=(date(int(report_year),1,1), date(int(report_year),12,31)), key='rep_range')

        filtered = df_rep.copy()
        if mfilter and mfilter != 'All':
            filtered = filtered[filtered['name']==mfilter]
        if tfilter and tfilter != 'All':
            filtered = filtered[filtered['team_name']==tfilter]
        start_dt, end_dt = date_range
        filtered = filtered[(filtered['date']>=pd.to_datetime(start_dt)) & (filtered['date']<=pd.to_datetime(end_dt))]

        st.dataframe(filtered.sort_values('date', ascending=False).reset_index(drop=True))
        csv = filtered.to_csv(index=False).encode()
        st.download_button('Download CSV', data=csv, file_name=f'leads_{report_year}.csv', mime='text/csv')
    else:
        st.info('No data for selected year.')

# ----- Admin Panel (single-flow team + members + targets) -----
with tabs[3]:
    st.header('Admin Panel')
    # simple password auth
    if 'admin_auth' not in st.session_state:
        st.session_state['admin_auth'] = False

    if not st.session_state['admin_auth']:
        pw = st.text_input('Admin password', type='password')
        if st.button('Login'):
            if ADMIN.get('password') and pw == ADMIN.get('password'):
                st.session_state['admin_auth'] = True
                st.success('Authenticated')
                safe_rerun()
            else:
                st.error('Incorrect password. Set the admin.password in Streamlit secrets.')
        st.stop()

    st.write('Create a new team, add members and set targets in one go.')
    with st.form('create_team_form'):
        team_name = st.text_input('Team Name', placeholder='e.g., North Zone')
        num_members = st.number_input('Number of members to add', min_value=1, max_value=50, value=3)

        members_input = []
        for i in range(int(num_members)):
            cols = st.columns([3,1,1])
            with cols[0]:
                mname = st.text_input(f'Member {i+1} Name', key=f'mname_{i}')
            with cols[1]:
                weekly = st.number_input(f'Weekly target', min_value=0, value=0, key=f'weekly_{i}')
            with cols[2]:
                monthly = st.number_input(f'Montly target', min_value=0, value=0, key=f'monthly_{i}')
            members_input.append({'name': mname.strip(), 'weekly': int(weekly), 'monthly': int(monthly)})

        submit = st.form_submit_button('Save Team & Members')

        if submit:
            if not team_name or any(m['name']=='' for m in members_input):
                st.error('Please provide a team name and fill all member names.')
            else:
                # load existing datasets
                teams_list = load_teams()
                members_list = load_members()
                targets_list = load_targets()

                # generate team id
                new_team_id = gen_id('T')
                teams_list.append({'team_id': new_team_id, 'team_name': team_name})

                current_year = date.today().year
                for mi in members_input:
                    new_mid = gen_id('M')
                    members_list.append({'member_id': new_mid, 'name': mi['name'], 'team_id': new_team_id, 'active': True})
                    targets_list.append({'member_id': new_mid, 'weekly_target': mi['weekly'], 'monthly_target': mi['monthly'], 'year': current_year})

                ok1 = save_teams(teams_list)
                ok2 = save_members(members_list)
                ok3 = save_targets(targets_list)

                if ok1 and ok2 and ok3:
                    st.success(f"Team '{team_name}' and {len(members_input)} members saved.")
                    safe_rerun()
                else:
                    st.error('Failed to save data to GitHub. Check settings.')

    st.markdown('---')
    st.subheader('Existing Teams & Members')
    st.write('Teams')
    st.dataframe(pd.DataFrame(load_teams()))
    st.write('Members')
    st.dataframe(pd.DataFrame(load_members()))
    st.write('Targets')
    st.dataframe(pd.DataFrame(load_targets()))


# End of file
