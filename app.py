# ==========================================
# Lead Management App (Final + Progress Bars)
# ==========================================
"""
Tabs:
1️⃣ Daily Update – log leads per member
2️⃣ Dashboard – visualize performance with progress bars
3️⃣ Reports – filter and export data
4️⃣ Admin Panel – manage teams and members

Data stored in GitHub JSON (data/leads_data.json)
Uses secrets.toml for GitHub token and admin password.
"""

import streamlit as st
import requests
import json
from base64 import b64encode, b64decode
from datetime import date
import pandas as pd
import uuid

# ------------------------------------------
# CONFIG
# ------------------------------------------
GITHUB = st.secrets.get("github", {})
ADMIN = st.secrets.get("admin", {})
GITHUB_TOKEN = GITHUB.get("token", "")
REPO_OWNER = GITHUB.get("repo_owner", "")
REPO_NAME = GITHUB.get("repo_name", "")
DATA_PATH = GITHUB.get("data_path", "data/leads_data.json")
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

# ------------------------------------------
# GITHUB DATA HELPERS
# ------------------------------------------
def gh_api_url() -> str:
    return f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{DATA_PATH}"

def load_data():
    url = gh_api_url()
    res = requests.get(url, headers=HEADERS)
    if res.status_code == 200:
        payload = res.json()
        content = b64decode(payload["content"]).decode()
        data = json.loads(content)
        return data, payload.get("sha")
    elif res.status_code == 404:
        return {"teams": [], "leads": []}, None
    else:
        st.error(f"GitHub error: {res.status_code}")
        return {"teams": [], "leads": []}, None

def save_data(data, message, sha=None):
    url = gh_api_url()
    content = b64encode(json.dumps(data, indent=2).encode()).decode()
    body = {"message": message, "content": content}
    if sha:
        body["sha"] = sha
    res = requests.put(url, headers=HEADERS, data=json.dumps(body))
    return res.status_code in (200, 201)

def gen_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"

def get_all_members(data):
    members = []
    for team in data.get("teams", []):
        for m in team.get("members", []):
            m_copy = m.copy()
            m_copy["team_id"] = team["team_id"]
            m_copy["team_name"] = team["team_name"]
            members.append(m_copy)
    return members

# ------------------------------------------
# STREAMLIT SETUP
# ------------------------------------------
st.set_page_config(page_title="Lead Management System", layout="wide")
st.title("📊 Unified Lead Management System")

data, sha = load_data()

# Tabs
tabs = st.tabs(["Daily Update", "Dashboard", "Report", "Admin Panel"])

# ------------------------------------------
# 1️⃣ DAILY UPDATE
# ------------------------------------------
with tabs[0]:
    st.header("🕘 Daily Update")
    teams = data.get("teams", [])
    if not teams:
        st.info("No teams yet. Please add one in the Admin Panel.")
    else:
        team_choice = st.selectbox("Select Team", [t["team_name"] for t in teams])
        team = next(t for t in teams if t["team_name"] == team_choice)
        member_choice = st.selectbox("Select Member", [m["name"] for m in team["members"]])
        date_sel = st.date_input("Date", value=date.today())
        leads_count = st.number_input("Lead Count", min_value=0, step=1)

        if st.button("💾 Save Lead"):
            entry = {
                "date": date_sel.strftime("%Y-%m-%d"),
                "team_id": team["team_id"],
                "member_id": next(m["member_id"] for m in team["members"] if m["name"] == member_choice),
                "lead_count": int(leads_count),
            }
            data.setdefault("leads", []).append(entry)
            if save_data(data, f"Add lead for {member_choice}", sha):
                st.success("✅ Lead saved successfully!")
                st.rerun()
            else:
                st.error("❌ Failed to save lead to GitHub.")

# ------------------------------------------
# 2️⃣ DASHBOARD
# ------------------------------------------
with tabs[1]:
    st.header("📈 Dashboard Overview")
    leads_df = pd.DataFrame(data.get("leads", []))
    if leads_df.empty:
        st.info("No leads recorded yet.")
    else:
        members_df = pd.DataFrame(get_all_members(data))
        leads_df["date"] = pd.to_datetime(leads_df["date"])
        leads_df = leads_df.merge(members_df, on="member_id", how="left")

        agg = leads_df.groupby(["team_name", "name"], as_index=False)["lead_count"].sum()
        agg.rename(columns={"name": "Member", "lead_count": "Total Leads"}, inplace=True)
        st.dataframe(agg, use_container_width=True)

        st.divider()
        st.subheader("🎯 Performance Progress")

        # Progress Bars
        for team in data["teams"]:
            st.markdown(f"### 🏆 {team['team_name']}")
            for m in team.get("members", []):
                total_leads = leads_df.loc[leads_df["member_id"] == m["member_id"], "lead_count"].sum()
                weekly_target = m.get("weekly_target", 0)
                monthly_target = m.get("monthly_target", 0)

                # Avoid division by zero
                weekly_pct = (total_leads / weekly_target * 100) if weekly_target > 0 else 0
                monthly_pct = (total_leads / monthly_target * 100) if monthly_target > 0 else 0

                # Color logic
                def bar_color(pct):
                    if pct < 50: return "🔴"
                    elif pct < 80: return "🟡"
                    else: return "🟢"

                st.write(f"**{m['name']}** — Total Leads: {int(total_leads)}")

                # Weekly Progress
                st.progress(min(weekly_pct / 100, 1.0), text=f"Weekly: {bar_color(weekly_pct)} {weekly_pct:.1f}% of {weekly_target}")

                # Monthly Progress
                st.progress(min(monthly_pct / 100, 1.0), text=f"Monthly: {bar_color(monthly_pct)} {monthly_pct:.1f}% of {monthly_target}")

                st.markdown("---")

# ------------------------------------------
# 3️⃣ REPORTS
# ------------------------------------------
with tabs[2]:
    st.header("📜 Reports")
    leads = pd.DataFrame(data.get("leads", []))
    if leads.empty:
        st.info("No data to report.")
    else:
        leads["date"] = pd.to_datetime(leads["date"])
        members = pd.DataFrame(get_all_members(data))
        leads = leads.merge(members, on="member_id", how="left")

        start, end = st.date_input("Date range", [date.today().replace(day=1), date.today()])
        mask = (leads["date"] >= pd.to_datetime(start)) & (leads["date"] <= pd.to_datetime(end))
        filtered = leads.loc[mask]

        st.dataframe(filtered.sort_values("date", ascending=False), use_container_width=True)
        st.download_button("⬇️ Download CSV", data=filtered.to_csv(index=False), file_name="lead_report.csv")

# ------------------------------------------
# 4️⃣ ADMIN PANEL
# ------------------------------------------
with tabs[3]:
    st.header("🧑‍💼 Admin Panel")

    if "admin_auth" not in st.session_state:
        st.session_state.admin_auth = False

    # --- Admin Login ---
    if not st.session_state.admin_auth:
        pw = st.text_input("Admin Password", type="password")
        if st.button("🔑 Login"):
            if pw == ADMIN.get("password"):
                st.session_state.admin_auth = True
                st.success("✅ Logged in!")
                st.rerun()
            else:
                st.error("Wrong password.")
        st.stop()

    # --- Team Management ---
    st.subheader("🏗️ Manage Teams & Members")
    for t_idx, team in enumerate(data.get("teams", [])):
        with st.expander(f"🏆 {team['team_name']}"):
            new_team_name = st.text_input("Team Name", value=team["team_name"], key=f"teamname_{t_idx}")
            if new_team_name != team["team_name"]:
                team["team_name"] = new_team_name
                save_data(data, f"Rename team {new_team_name}", sha)
                st.success("Team renamed.")
                st.rerun()

            st.markdown("### 👥 Members")
            for m_idx, m in enumerate(team.get("members", [])):
                cols = st.columns([3, 1, 1, 1])
                with cols[0]:
                    name = st.text_input("Name", value=m["name"], key=f"n_{t_idx}_{m_idx}")
                with cols[1]:
                    weekly = st.number_input("Weekly", min_value=0, value=int(m["weekly_target"]), key=f"w_{t_idx}_{m_idx}")
                with cols[2]:
                    monthly = st.number_input("Monthly", min_value=0, value=int(m["monthly_target"]), key=f"mo_{t_idx}_{m_idx}")
                with cols[3]:
                    if st.button("🗑️", key=f"delm_{t_idx}_{m_idx}"):
                        team["members"].pop(m_idx)
                        save_data(data, f"Delete member {m['name']}", sha)
                        st.success(f"Deleted {m['name']}")
                        st.rerun()

                if name != m["name"] or weekly != m["weekly_target"] or monthly != m["monthly_target"]:
                    m["name"] = name
                    m["weekly_target"] = weekly
                    m["monthly_target"] = monthly
                    save_data(data, f"Update member {name}", sha)
                    st.success(f"Updated {name}")
                    st.rerun()

            with st.expander("➕ Add Member"):
                new_name = st.text_input("Member Name", key=f"addn_{t_idx}")
                new_w = st.number_input("Weekly Target", min_value=0, key=f"addw_{t_idx}")
                new_m = st.number_input("Monthly Target", min_value=0, key=f"addmo_{t_idx}")
                if st.button("Add Member", key=f"adda_{t_idx}"):
                    if new_name.strip():
                        new_member = {
                            "name": new_name.strip(),
                            "member_id": gen_id("M"),
                            "weekly_target": new_w,
                            "monthly_target": new_m,
                        }
                        team["members"].append(new_member)
                        save_data(data, f"Add member {new_name}", sha)
                        st.success("Member added!")
                        st.rerun()
                    else:
                        st.error("Enter member name.")

            st.markdown("---")
            if st.button(f"🗑️ Delete Team '{team['team_name']}'", key=f"delteam_{t_idx}"):
                data["teams"].remove(team)
                save_data(data, f"Delete team {team['team_name']}", sha)
                st.success("Team deleted.")
                st.rerun()

    st.divider()
    st.subheader("➕ Add New Team")
    with st.form("add_team_form"):
        team_name = st.text_input("Team Name")
        n_members = st.number_input("No. of Members", min_value=1, max_value=20, value=2)
        members = []
        for i in range(int(n_members)):
            cols = st.columns([3, 1, 1])
            with cols[0]:
                name = st.text_input(f"Member {i+1} Name", key=f"nm_{i}")
            with cols[1]:
                weekly = st.number_input("Weekly", min_value=0, key=f"wk_{i}")
            with cols[2]:
                monthly = st.number_input("Monthly", min_value=0, key=f"mo_{i}")
            members.append({
                "name": name.strip(),
                "member_id": gen_id("M"),
                "weekly_target": weekly,
                "monthly_target": monthly,
            })

        if st.form_submit_button("💾 Save Team"):
            if not team_name or any(m["name"] == "" for m in members):
                st.error("Please complete all fields.")
            else:
                team_entry = {
                    "team_id": gen_id("T"),
                    "team_name": team_name,
                    "members": members,
                }
                data.setdefault("teams", []).append(team_entry)
                if save_data(data, f"Add team {team_name}", sha):
                    st.success("Team added!")
                    st.rerun()
