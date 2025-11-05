# app.py
"""
Lead Management App — One-page Modern Dashboard + Full CRUD Admin
- Single JSON data file on GitHub: data/leads_data.json
- Tabs: Daily Update, Dashboard (one-page card view), Reports, Admin (full CRUD)
- Filters on Dashboard: All Time | This Month | This Week
- Progress bars for weekly & monthly vs targets
- Uses Streamlit secrets for GitHub token and admin password

.streamlit/secrets.toml example:

[github]
token = "ghp_xxx"
repo_owner = "your-github-username"
repo_name = "your-repo-name"
data_path = "data/leads_data.json"

[admin]
password = "your-admin-password"
"""

import streamlit as st
import requests, json
from base64 import b64encode, b64decode
from datetime import date, datetime, timedelta
import pandas as pd
import uuid

# -----------------------
# Config / Secrets
# -----------------------
GITHUB = st.secrets.get("github", {})
ADMIN = st.secrets.get("admin", {})

GITHUB_TOKEN = GITHUB.get("token", "")
REPO_OWNER = GITHUB.get("repo_owner", "")
REPO_NAME = GITHUB.get("repo_name", "")
DATA_PATH = GITHUB.get("data_path", "data/leads_data.json")
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

# -----------------------
# GitHub helpers
# -----------------------
def gh_api_url():
    return f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{DATA_PATH}"

def load_data():
    """Return (data_dict, sha) or (default_data, None)"""
    url = gh_api_url()
    res = requests.get(url, headers=HEADERS)
    if res.status_code == 200:
        payload = res.json()
        content = b64decode(payload["content"]).decode()
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
    content = b64encode(json.dumps(data, indent=2, ensure_ascii=False).encode()).decode()
    body = {"message": message, "content": content}
    if sha:
        body["sha"] = sha
    res = requests.put(url, headers=HEADERS, data=json.dumps(body))
    return res.status_code in (200, 201)

# -----------------------
# Utilities
# -----------------------
def gen_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"

def flatten_members(data):
    """Return list of members with team info"""
    members = []
    for t in data.get("teams", []):
        for m in t.get("members", []):
            mm = m.copy()
            mm["team_id"] = t["team_id"]
            mm["team_name"] = t["team_name"]
            members.append(mm)
    return members

def ensure_data_file_created(data, sha):
    """If data is default (no teams/leads) and sha is None, create file in repo.
       This helps first-time bootstrap. Returns True if created."""
    if sha is None:
        ok = save_data(data, "Initialize leads_data.json", sha=None)
        return ok
    return False

# -----------------------
# Aggregation helpers
# -----------------------
def calc_totals(leads, members_df, period="All Time"):
    """
    leads: DataFrame with columns ['date','member_id','lead_count',...]
    members_df: DataFrame of members (member_id, name, team_id, team_name, weekly_target, monthly_target)
    period: "All Time" | "This Month" | "This Week"
    Returns aggregated DataFrame with totals per member and team-level summaries
    """
    if leads.empty:
        # return empty frames shaped appropriately
        cols = ["member_id","name","team_id","team_name","total_leads","weekly_target","monthly_target"]
        return pd.DataFrame(columns=cols), pd.DataFrame(columns=["team_id","team_name","team_leads","avg_weekly_pct","avg_monthly_pct"])

    df = leads.copy()
    df["date"] = pd.to_datetime(df["date"])

    today = pd.to_datetime(date.today())
    if period == "This Week":
        start = today - pd.Timedelta(days=7)
        df = df[df["date"] >= start]
    elif period == "This Month":
        start = today.replace(day=1)
        df = df[df["date"] >= start]
    # else All Time: no filtering

    member_tot = df.groupby("member_id", as_index=False)["lead_count"].sum().rename(columns={"lead_count":"total_leads"})
    merged = members_df.merge(member_tot, on="member_id", how="left")
    merged["total_leads"] = merged["total_leads"].fillna(0).astype(int)
    merged["weekly_target"] = merged["weekly_target"].fillna(0).astype(int)
    merged["monthly_target"] = merged["monthly_target"].fillna(0).astype(int)
    # percent (avoid div by zero)
    merged["weekly_pct"] = merged.apply(lambda r: (r["total_leads"]/r["weekly_target"]*100) if r["weekly_target"]>0 else 0, axis=1)
    merged["monthly_pct"] = merged.apply(lambda r: (r["total_leads"]/r["monthly_target"]*100) if r["monthly_target"]>0 else 0, axis=1)

    # team summary
    team_grp = merged.groupby(["team_id","team_name"], as_index=False).agg({
        "total_leads":"sum",
        "weekly_pct":"mean",
        "monthly_pct":"mean"
    }).rename(columns={"total_leads":"team_leads","weekly_pct":"avg_weekly_pct","monthly_pct":"avg_monthly_pct"})

    return merged, team_grp

# -----------------------
# UI helpers (cards / progress)
# -----------------------
CARD_STYLE = """
<style>
.card { background: #ffffff; padding: 16px; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); margin-bottom: 12px;}
.team-header { display:flex; justify-content: space-between; align-items:center; }
.team-title { font-size:18px; font-weight:600; }
.small { color: #6c6c6c; font-size:13px; }
.member-card { background:#f8f9fb; padding:10px; border-radius:8px; margin-bottom:8px; }
.progress-bar { height:12px; border-radius:8px; background:#e6e6e6; overflow:hidden; }
.progress-fill { height:100%; border-radius:8px; }
.badge { font-size:12px; padding:4px 8px; border-radius:12px; background:#efefef; }
</style>
"""

def progress_color_and_width(pct):
    """Return (color_hex, width_percent)"""
    if pct < 0:
        pct = 0
    if pct < 50:
        color = "#e24b4b"  # red
    elif pct < 80:
        color = "#f0b429"  # yellow/orange
    else:
        color = "#16a34a"  # green
    width = min(round(pct,1), 100)
    return color, width

def render_progress_bar(pct, target_label):
    color, width = progress_color_and_width(pct)
    bar_html = f"""
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <div class="small">{target_label}</div>
      <div class="badge">{pct:.1f}%</div>
    </div>
    <div class="progress-bar" role="progressbar" aria-valuenow="{pct}">
      <div class="progress-fill" style="width:{width}%; background:{color};"></div>
    </div>
    """
    return bar_html

# -----------------------
# App layout & logic
# -----------------------
st.set_page_config(page_title="Lead Management Dashboard", layout="wide")
st.markdown(CARD_STYLE, unsafe_allow_html=True)
st.title("📊 Lead Management — Dashboard")

# Load data
data, sha = load_data()
# auto-create data file if missing
if sha is None and data.get("teams") == [] and data.get("leads") == []:
    # create initial file if not exists (safe)
    created = ensure_created = False
    try:
        created = save_data({"teams": [], "leads": []}, "Initialize leads_data.json")
    except Exception:
        created = False
    if created:
        data, sha = load_data()

# Top-level tabs (Daily Update, Dashboard, Report, Admin)
tabs = st.tabs(["Daily Update", "Dashboard", "Report", "Admin Panel"])

# -----------------------
# Tab 1 — Daily Update
# -----------------------
with tabs[0]:
    st.header("🕘 Daily Update")
    teams = data.get("teams", [])
    if not teams:
        st.info("No teams defined yet. Create teams & members in Admin Panel.")
    else:
        col1, col2 = st.columns([2,1])
        with col1:
            team_names = [t["team_name"] for t in teams]
            team_sel = st.selectbox("Select Team", team_names)
            team = next(t for t in teams if t["team_name"] == team_sel)
            member_names = [m["name"] for m in team.get("members",[])]
            member_sel = st.selectbox("Select Member", member_names)
            dt = st.date_input("Date", value=date.today())
            cnt = st.number_input("Lead Count", min_value=0, value=0, step=1)
            notes = st.text_area("Notes (optional)", height=80)
            if st.button("💾 Save Lead"):
                entry = {"date": dt.strftime("%Y-%m-%d"),
                         "team_id": team["team_id"],
                         "member_id": next(m["member_id"] for m in team["members"] if m["name"]==member_sel),
                         "lead_count": int(cnt),
                         "notes": notes or ""}
                data.setdefault("leads", []).append(entry)
                if save_data(data, f"Add lead for {member_sel}", sha):
                    st.success("Lead saved ✅")
                    st.experimental_rerun()
                else:
                    st.error("Failed to save. Check GitHub token/permissions.")
        with col2:
            st.markdown("#### Quick Stats")
            leads_all = pd.DataFrame(data.get("leads", []))
            if not leads_all.empty:
                leads_all["date"] = pd.to_datetime(leads_all["date"])
                today = pd.to_datetime(date.today())
                today_total = int(leads_all[leads_all["date"]==today]["lead_count"].sum())
                st.metric("Today's leads", today_total)
            else:
                st.write("No leads yet.")

# -----------------------
# Tab 2 — Dashboard (one-page card)
# -----------------------
with tabs[1]:
    st.header("📈 One-page Dashboard")
    # Filter
    filter_opt = st.selectbox("Period filter", ["All Time", "This Month", "This Week"], index=0)
    # Prepare leads and members
    leads_raw = pd.DataFrame(data.get("leads", []))
    members_list = flatten_members(data)
    members_df = pd.DataFrame(members_list) if members_list else pd.DataFrame(columns=[
        "member_id","name","team_id","team_name","weekly_target","monthly_target"
    ])
    if leads_raw.empty:
        st.info("No leads yet — use Daily Update to add leads.")
    # Compute totals by chosen period
    member_agg, team_agg = calc_totals(leads_raw, members_df, period=filter_opt)

    # Render teams as cards in columns (2 per row)
    teams = data.get("teams", [])
    if not teams:
        st.info("No teams. Add teams in Admin panel.")
    else:
        cards_per_row = 2
        for i in range(0, len(teams), cards_per_row):
            cols = st.columns(cards_per_row)
            for col_idx, team in enumerate(teams[i:i+cards_per_row]):
                with cols[col_idx]:
                    # Team card
                    team_id = team["team_id"]
                    trow = team_agg[team_agg["team_id"]==team_id]
                    team_leads = int(trow["team_leads"].iloc[0]) if not trow.empty else 0
                    team_week_avg = float(trow["avg_weekly_pct"].iloc[0]) if not trow.empty else 0.0
                    team_month_avg = float(trow["avg_monthly_pct"].iloc[0]) if not trow.empty else 0.0

                    st.markdown(f"<div class='card'><div class='team-header'><div class='team-title'>🏷 {team['team_name']}</div><div class='small'>Total leads: <strong>{team_leads}</strong></div></div>", unsafe_allow_html=True)

                    # Team average bar (use average of weekly and monthly averages)
                    team_avg_combined = (team_week_avg + team_month_avg) / 2 if (team_week_avg or team_month_avg) else 0
                    team_bar_html = render_progress_bar(team_avg_combined, "Avg progress (weekly vs monthly)")
                    st.markdown(team_bar_html, unsafe_allow_html=True)

                    st.markdown("<div style='margin-top:8px; font-weight:600;'>Members</div>", unsafe_allow_html=True)
                    # Member cards inside
                    members_in_team = members_df[members_df["team_id"]==team_id]
                    if members_in_team.empty:
                        st.markdown("<div class='small'>No members yet</div>", unsafe_allow_html=True)
                    else:
                        for _, m in members_in_team.sort_values("name").iterrows():
                            mid = m["member_id"]
                            name = m["name"]
                            total = int(member_agg.loc[member_agg["member_id"]==mid, "total_leads"].iloc[0]) if not member_agg.empty and (mid in member_agg["member_id"].values) else 0
                            w_pct = float(member_agg.loc[member_agg["member_id"]==mid, "weekly_pct"].iloc[0]) if not member_agg.empty and (mid in member_agg["member_id"].values) else 0.0
                            mo_pct = float(member_agg.loc[member_agg["member_id"]==mid, "monthly_pct"].iloc[0]) if not member_agg.empty and (mid in member_agg["member_id"].values) else 0.0
                            weekly_target = int(m.get("weekly_target", 0))
                            monthly_target = int(m.get("monthly_target", 0))

                            member_html = f"""
                            <div class='member-card'>
                              <div style='display:flex; justify-content:space-between; align-items:center;'>
                                <div style='font-weight:600;'>{name}</div>
                                <div class='small'>Leads: <strong>{total}</strong></div>
                              </div>
                              <div style='margin-top:6px'>{render_progress_bar(w_pct, f'Weekly ({total}/{weekly_target})')}</div>
                              <div style='margin-top:6px'>{render_progress_bar(mo_pct, f'Monthly ({total}/{monthly_target})')}</div>
                            </div>
                            """
                            st.markdown(member_html, unsafe_allow_html=True)

                    st.markdown("</div>", unsafe_allow_html=True)  # close team card

# -----------------------
# Tab 3 — Reports
# -----------------------
with tabs[2]:
    st.header("📜 Reports")
    leads_df = pd.DataFrame(data.get("leads", []))
    if leads_df.empty:
        st.info("No leads to report.")
    else:
        leads_df["date"] = pd.to_datetime(leads_df["date"])
        members_df = pd.DataFrame(flatten_members(data))
        merged = leads_df.merge(members_df, on="member_id", how="left")
        start_dt, end_dt = st.date_input("Select date range", [date.today().replace(day=1), date.today()])
        mask = (merged["date"] >= pd.to_datetime(start_dt)) & (merged["date"] <= pd.to_datetime(end_dt))
        filtered = merged.loc[mask].sort_values("date", ascending=False)
        st.dataframe(filtered, use_container_width=True)
        csv = filtered.to_csv(index=False)
        st.download_button("⬇️ Download CSV", data=csv, file_name=f"leads_{start_dt}_{end_dt}.csv", mime="text/csv")

# -----------------------
# Tab 4 — Admin Panel (full CRUD)
# -----------------------
with tabs[3]:
    st.header("🧑‍💼 Admin Panel")
    if "admin_auth" not in st.session_state:
        st.session_state.admin_auth = False

    # login
    if not st.session_state.admin_auth:
        pw = st.text_input("Admin Password", type="password")
        if st.button("🔐 Login"):
            if pw == ADMIN.get("password"):
                st.session_state.admin_auth = True
                st.success("Authenticated")
                st.experimental_rerun()
            else:
                st.error("Wrong password")
        st.stop()

    st.subheader("Manage teams & members")

    # Show existing teams with expanders and editing controls
    if not data.get("teams"):
        st.info("No teams — add one below.")
    else:
        for t_idx, team in enumerate(list(data["teams"])):  # list() to avoid mutation issues
            with st.expander(f"🏷 {team['team_name']}"):
                # Edit team name
                new_team_name = st.text_input("Team name", value=team["team_name"], key=f"team_{t_idx}")
                if new_team_name != team["team_name"]:
                    team["team_name"] = new_team_name
                    if save_data(data, f"Rename team {new_team_name}", sha):
                        st.success("Team name updated")
                        st.experimental_rerun()

                st.markdown("### Members")
                for m_idx, member in enumerate(list(team.get("members", []))):
                    cols = st.columns([3,1,1,1])
                    with cols[0]:
                        new_name = st.text_input("Name", value=member["name"], key=f"name_{t_idx}_{m_idx}")
                    with cols[1]:
                        new_weekly = st.number_input("Weekly target", min_value=0, value=int(member.get("weekly_target",0)), key=f"w_{t_idx}_{m_idx}")
                    with cols[2]:
                        new_monthly = st.number_input("Monthly target", min_value=0, value=int(member.get("monthly_target",0)), key=f"m_{t_idx}_{m_idx}")
                    with cols[3]:
                        if st.button("🗑️", key=f"delmember_{t_idx}_{m_idx}"):
                            team["members"].pop(m_idx)
                            if save_data(data, f"Delete member {member['name']}", sha):
                                st.success("Member deleted")
                                st.experimental_rerun()

                    if new_name != member["name"] or new_weekly != member.get("weekly_target",0) or new_monthly != member.get("monthly_target",0):
                        member["name"] = new_name
                        member["weekly_target"] = int(new_weekly)
                        member["monthly_target"] = int(new_monthly)
                        if save_data(data, f"Update member {new_name}", sha):
                            st.success("Member updated")
                            st.experimental_rerun()

                # add member
                with st.expander("➕ Add member"):
                    add_name = st.text_input("Member name", key=f"add_name_{t_idx}")
                    add_weekly = st.number_input("Weekly", min_value=0, key=f"add_w_{t_idx}")
                    add_monthly = st.number_input("Monthly", min_value=0, key=f"add_m_{t_idx}")
                    if st.button("Add member", key=f"addbtn_{t_idx}"):
                        if not add_name.strip():
                            st.error("Enter member name")
                        else:
                            new_member = {
                                "name": add_name.strip(),
                                "member_id": gen_id("M"),
                                "weekly_target": int(add_weekly),
                                "monthly_target": int(add_monthly),
                            }
                            team["members"].append(new_member)
                            if save_data(data, f"Add member {add_name}", sha):
                                st.success("Member added")
                                st.experimental_rerun()

                # delete team
                if st.button(f"🗑️ Delete team '{team['team_name']}'", key=f"delteam_{t_idx}"):
                    data["teams"].remove(team)
                    if save_data(data, f"Delete team {team['team_name']}", sha):
                        st.success("Team deleted")
                        st.experimental_rerun()

    st.markdown("---")
    st.subheader("➕ Add new team")
    with st.form("add_team"):
        team_name = st.text_input("Team name")
        n_members = st.number_input("No. of members", min_value=1, max_value=50, value=2)
        new_members = []
        for i in range(int(n_members)):
            cols = st.columns([3,1,1])
            with cols[0]:
                nm = st.text_input(f"Member {i+1} name", key=f"new_nm_{i}")
            with cols[1]:
                nw = st.number_input("Weekly target", min_value=0, key=f"new_w_{i}")
            with cols[2]:
                nmth = st.number_input("Monthly target", min_value=0, key=f"new_m_{i}")
            new_members.append({
                "name": nm.strip(),
                "member_id": gen_id("M"),
                "weekly_target": int(nw),
                "monthly_target": int(nmth),
            })
        if st.form_submit_button("Save team"):
            if not team_name.strip() or any(m["name"]=="" for m in new_members):
                st.error("Fill all fields")
            else:
                new_team = {"team_id": gen_id("T"), "team_name": team_name.strip(), "members": new_members}
                data.setdefault("teams", []).append(new_team)
                if save_data(data, f"Add team {team_name}", sha):
                    st.success("Team added")
                    st.experimental_rerun()
                else:
                    st.error("Failed to save data")

# -----------------------
# End of app
# -----------------------
