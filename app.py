"""
Lead Management App — production-ready with some fixes & improvements
- Centralized sidebar auth so password login is visible across all tabs
- More robust GitHub auth header (Bearer)
- load_data returns decoded data and sha reliably
- "This Week" uses start-of-week (Monday) instead of 'last 7 days'
- Today's leads calculation uses date equality (not timestamp equality)
- Small UX: require confirmation checkbox before destructive deletes
- Minor validation on add team/members
- Additional bug fixes & hardening
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
HEADERS = {"Authorization": f"Bearer {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

ADMIN_PASSWORD = PASSWORDS.get("admin", "Admin@2025")
REPORT_PASSWORD = PASSWORDS.get("report", "Report@2025")
UPDATE_PASSWORD = PASSWORDS.get("update", "Update@2025")

# -----------------------
# GitHub helpers
# -----------------------

def gh_api_url():
    return f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{DATA_PATH}"


def load_data():
    """Return (data_dict, sha) — if repo missing or errors, returns empty structure and None sha."""
    url = gh_api_url()
    if not REPO_OWNER or not REPO_NAME:
        st.error("GitHub repo_owner/repo_name not set in st.secrets.github.")
        return {"teams": [], "leads": []}, None
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
    except Exception as e:
        st.error(f"GitHub request failed: {e}")
        return {"teams": [], "leads": []}, None
    if res.status_code == 200:
        payload = res.json()
        content_b64 = payload.get("content", "")
        try:
            # GitHub may include newlines; b64decode handles them
            content = b64decode(content_b64).decode()
            data = json.loads(content)
            # Ensure minimum structure
            if not isinstance(data, dict):
                data = {"teams": [], "leads": []}
            data.setdefault("teams", [])
            data.setdefault("leads", [])
            return data, payload.get("sha")
        except Exception as e:
            st.error(f"Failed to parse repo content: {e}")
            # return empty-but-valid structure and still return sha if present
            return {"teams": [], "leads": []}, payload.get("sha")
    elif res.status_code == 404:
        # file not present yet
        return {"teams": [], "leads": []}, None
    else:
        st.error(f"GitHub read error {res.status_code}: {res.text}")
        return {"teams": [], "leads": []}, None


def save_data(data, message, sha=None):
    url = gh_api_url()
    if not REPO_OWNER or not REPO_NAME:
        st.error("GitHub repo_owner/repo_name not set in st.secrets.github.")
        return False
    try:
        content = b64encode(json.dumps(data, indent=2, ensure_ascii=False).encode()).decode()
        body = {"message": message, "content": content}
        if sha:
            body["sha"] = sha
        res = requests.put(url, headers=HEADERS, data=json.dumps(body), timeout=15)
    except Exception as e:
        st.error(f"GitHub write request failed: {e}")
        return False
    if res.status_code in (200, 201):
        # optionally update an in-session marker
        st.session_state["last_save"] = datetime.utcnow().isoformat()
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
    """
    Return list of member dicts with team context.
    Defensive: ensures weekly/monthly targets exist and are ints.
    """
    members = []
    for t in data.get("teams", []):
        team_id = t.get("team_id")
        team_name = t.get("team_name", "")
        for m in t.get("members", []):
            mm = m.copy()
            mm.setdefault("weekly_target", 0)
            mm.setdefault("monthly_target", 0)
            try:
                mm["weekly_target"] = int(mm.get("weekly_target", 0))
            except Exception:
                mm["weekly_target"] = 0
            try:
                mm["monthly_target"] = int(mm.get("monthly_target", 0))
            except Exception:
                mm["monthly_target"] = 0
            mm["team_id"] = team_id
            mm["team_name"] = team_name
            members.append(mm)
    return members

# -----------------------
# Aggregation helpers
# -----------------------

def calc_totals(leads_df, members_df, period="All Time"):
    """
    Returns (members_agg_df, team_agg_df)
    members_agg columns: member_id, name, team_id, team_name, total_leads, weekly_target, monthly_target, weekly_pct, monthly_pct
    team_agg columns: team_id, team_name, team_leads, avg_weekly_pct, avg_monthly_pct
    """
    # Handle empty leads
    if leads_df is None or leads_df.empty:
        member_cols = ["member_id", "name", "team_id", "team_name", "total_leads", "weekly_target", "monthly_target", "weekly_pct", "monthly_pct"]
        return pd.DataFrame(columns=member_cols), pd.DataFrame(columns=["team_id", "team_name", "team_leads", "avg_weekly_pct", "avg_monthly_pct"])

    df = leads_df.copy()
    # normalize date column to date objects
    df["date"] = pd.to_datetime(df["date"]).dt.date
    today = date.today()

    if period == "This Week":
        # start of current ISO week (Monday)
        start_of_week = today - timedelta(days=today.weekday())
        df = df[df["date"] >= start_of_week]
    elif period == "This Month":
        start_of_month = today.replace(day=1)
        df = df[df["date"] >= start_of_month]

    # sum leads per member
    if "lead_count" not in df.columns:
        df["lead_count"] = 0
    member_tot = df.groupby("member_id", as_index=False)["lead_count"].sum().rename(columns={"lead_count": "total_leads"})

    # Ensure members_df exists and has expected columns
    if members_df is None or members_df.empty:
        members_df = pd.DataFrame(columns=["member_id", "name", "team_id", "team_name", "weekly_target", "monthly_target"])

    merged = members_df.merge(member_tot, on="member_id", how="left")
    merged["total_leads"] = merged["total_leads"].fillna(0).astype(int)

    # Ensure target columns exist and are integer
    for col in ["weekly_target", "monthly_target"]:
        if col not in merged.columns:
            merged[col] = 0
        # coerce to numeric safely
        merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0).astype(int)

    # safe percentage calculation
    def safe_pct(numer, denom):
        try:
            denom = int(denom)
            if denom <= 0:
                return 0.0
            return (float(numer) / denom) * 100.0
        except Exception:
            return 0.0

    merged["weekly_pct"] = merged.apply(lambda r: safe_pct(r["total_leads"], r["weekly_target"]), axis=1)
    merged["monthly_pct"] = merged.apply(lambda r: safe_pct(r["total_leads"], r["monthly_target"]), axis=1)

    # team aggregation
    team_grp = merged.groupby(["team_id", "team_name"], as_index=False).agg({
        "total_leads": "sum",
        "weekly_pct": "mean",
        "monthly_pct": "mean"
    }).rename(columns={"total_leads": "team_leads", "weekly_pct": "avg_weekly_pct", "monthly_pct": "avg_monthly_pct"})

    # fill NaNs with zeros (when team has no members, etc)
    team_grp["team_leads"] = team_grp["team_leads"].fillna(0).astype(int)
    team_grp["avg_weekly_pct"] = team_grp["avg_weekly_pct"].fillna(0.0)
    team_grp["avg_monthly_pct"] = team_grp["avg_monthly_pct"].fillna(0.0)

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
    try:
        pct = float(pct)
    except Exception:
        pct = 0.0
    pct = max(0.0, pct)
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

# -----------------------
# Small helpers for table coloring (pandas Styler)
# -----------------------

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
    # ensure the column exists
    if "member_pct" not in df.columns:
        return df
    sty = df.style.applymap(lambda v: pct_to_bg(v) if isinstance(v, (int, float)) else "", subset=["member_pct"])
    return sty


def style_team_table(df):
    if df.empty:
        return df
    if "team_pct" not in df.columns:
        return df
    sty = df.style.applymap(lambda v: pct_to_bg(v) if isinstance(v, (int, float)) else "", subset=["team_pct"])
    return sty

# -----------------------
# Main layout
# -----------------------
st.set_page_config(page_title="Lead Management", layout="wide")
st.markdown(BASE_CSS, unsafe_allow_html=True)
st.title("📊 Lead Management — Unified Dashboard (fixed)")

# Load data
data, sha = load_data()

# Bootstrap file if none exists
if sha is None and data.get("teams", []) == [] and data.get("leads", []) == []:
    created = save_data({"teams": [], "leads": []}, "Initialize leads_data.json")
    if created:
        data, sha = load_data()

# -----------------------
# Sidebar: centralized auth controls (always visible)
# -----------------------
if "daily_auth" not in st.session_state:
    st.session_state["daily_auth"] = False
if "report_auth" not in st.session_state:
    st.session_state["report_auth"] = False
if "admin_auth" not in st.session_state:
    st.session_state["admin_auth"] = False

st.sidebar.header("🔐 Authentication")

# Daily Update auth
with st.sidebar.expander("Daily Update", expanded=True):
    if st.session_state.daily_auth:
        st.success("Daily Update: unlocked")
        if st.button("Lock Daily Update"):
            st.session_state.daily_auth = False
            st.experimental_rerun()
    else:
        daily_pw = st.text_input("Daily password", type="password", key="sidebar_daily_pw")
        if st.button("Unlock Daily Update"):
            if daily_pw == UPDATE_PASSWORD:
                st.session_state.daily_auth = True
                st.success("Daily Update unlocked ✅")
                st.rerun()
            else:
                st.error("Wrong password")

# Reports auth
with st.sidebar.expander("Reports", expanded=False):
    if st.session_state.report_auth:
        st.success("Reports: unlocked")
        if st.button("Lock Reports"):
            st.session_state.report_auth = False
            st.experimental_rerun()
    else:
        report_pw = st.text_input("Reports password", type="password", key="sidebar_report_pw")
        if st.button("Unlock Reports"):
            if report_pw == REPORT_PASSWORD:
                st.session_state.report_auth = True
                st.success("Reports unlocked ✅")
                st.rerun()
            else:
                st.error("Wrong password")

# Admin auth
with st.sidebar.expander("Admin Panel", expanded=False):
    if st.session_state.admin_auth:
        st.success("Admin: unlocked")
        if st.button("Lock Admin Panel"):
            st.session_state.admin_auth = False
            st.experimental_rerun()
    else:
        admin_pw = st.text_input("Admin password", type="password", key="sidebar_admin_pw")
        if st.button("Unlock Admin Panel"):
            if admin_pw == ADMIN_PASSWORD:
                st.session_state.admin_auth = True
                st.success("Admin unlocked ✅")
                st.rerun()
            else:
                st.error("Wrong password")

# Tabs
tabs = st.tabs(["Dashboard", "Daily Update", "Reports", "Admin Panel"]) 

# Dashboard
with tabs[0]:
    st.subheader("📈 Dashboard — Overview (public)")
    period = st.selectbox("Period filter", ["All Time", "This Month", "This Week"], index=0, key="dash_period")

    leads_df = pd.DataFrame(data.get("leads", []))
    members_flat = flatten_members(data)
    members_df = pd.DataFrame(members_flat) if members_flat else pd.DataFrame(columns=["member_id","name","team_id","team_name","weekly_target","monthly_target"])

    if leads_df.empty:
        st.info("No leads logged yet — dashboard will populate once you add leads.")
    else:
        member_agg, team_agg = calc_totals(leads_df, members_df, period=period)
        teams = data.get("teams", [])
        total_teams = len(teams)
        total_members = len(members_df)
        total_leads = int(leads_df["lead_count"].sum()) if "lead_count" in leads_df.columns and not leads_df.empty else 0
        avg_team_pct = 0
        if not team_agg.empty:
            avg_team_pct = (team_agg["avg_weekly_pct"].mean() + team_agg["avg_monthly_pct"].mean()) / 2

        st.markdown("""
        <style>
        .summary-card { background: #f0f2f6; padding: 10px 14px; border-radius: 10px; display: flex; justify-content: space-around; margin-bottom: 15px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
        .summary-item { font-size: 14px; font-weight: 600; color: #333; }
        .summary-value { font-size: 18px; font-weight: 700; color: #0073e6; }
        </style>
        """, unsafe_allow_html=True)

        st.markdown(f"""
        <div class='summary-card'>
            <div class='summary-item'>🏢 Teams<br><span class='summary-value'>{total_teams}</span></div>
            <div class='summary-item'>👥 Members<br><span class='summary-value'>{total_members}</span></div>
            <div class='summary-item'>📊 Total Leads<br><span class='summary-value'>{total_leads}</span></div>
            <div class='summary-item'>⭐ Avg Performance<br><span class='summary-value'>{avg_team_pct:.1f}%</span></div>
        </div>
        """, unsafe_allow_html=True)

        for team in teams:
            t_id = team["team_id"]
            trow = team_agg[team_agg["team_id"] == t_id] if not team_agg.empty else pd.DataFrame()
            team_leads = int(trow["team_leads"].iloc[0]) if not trow.empty else 0
            avg_week = float(trow["avg_weekly_pct"].iloc[0]) if not trow.empty else 0.0
            avg_month = float(trow["avg_monthly_pct"].iloc[0]) if not trow.empty else 0.0
            team_avg = (avg_week + avg_month) / 2 if (avg_week or avg_month) else 0.0

            st.markdown(f"<div class='card'>", unsafe_allow_html=True)
            st.markdown(f"<div style='display:flex; justify-content:space-between; align-items:center;'><div style='font-weight:600;'>{team['team_name']}</div><div class='small'>Leads: <b>{team_leads}</b></div></div>", unsafe_allow_html=True)
            st.markdown(render_progress_bar_html(team_avg, "Team Avg (weekly + monthly)"), unsafe_allow_html=True)

            members_in_team = members_df[members_df["team_id"] == t_id].sort_values("name")
            if members_in_team.empty:
                st.markdown("<div class='small'>No members</div>", unsafe_allow_html=True)
            else:
                cols = st.columns(3)
                for i, (_, m) in enumerate(members_in_team.iterrows()):
                    mid = m["member_id"]
                    name = m["name"]
                    weekly_target = int(m.get("weekly_target", 0))
                    monthly_target = int(m.get("monthly_target", 0))
                    total_leads = 0
                    weekly_pct = 0.0
                    monthly_pct = 0.0
                    if not member_agg.empty and mid in member_agg["member_id"].values:
                        row = member_agg[member_agg["member_id"] == mid].iloc[0]
                        total_leads = int(row["total_leads"])
                        weekly_pct = float(row["weekly_pct"])
                        monthly_pct = float(row["monthly_pct"])

                    member_html = f"""
                    <div class='member-card'>
                      <div style='font-weight:600;'>{name}</div>
                      <div class='small'>Leads: <b>{total_leads}</b></div>
                      <div style='margin-top:4px'>{render_progress_bar_html(weekly_pct, f'Weekly ({total_leads}/{weekly_target})')}</div>
                      <div style='margin-top:4px'>{render_progress_bar_html(monthly_pct, f'Monthly ({total_leads}/{monthly_target})')}</div>
                    </div>
                    """
                    cols[i % 3].markdown(member_html, unsafe_allow_html=True)

            st.markdown("</div>", unsafe_allow_html=True)

# Daily Update
with tabs[1]:
    st.header("🕘 Daily Update (password required)")

    # Check centralized auth state (sidebar)
    if not st.session_state.daily_auth:
        st.warning("Daily Update is locked. Unlock it using the Authentication panel in the sidebar.")
        st.stop()

    teams = data.get("teams", [])
    if not teams:
        st.info("No teams defined. Create them in Admin Panel before logging leads.")
    else:
        col1, col2 = st.columns([2, 1])
        with col1:
            team_names = [t["team_name"] for t in teams]
            team_choice = st.selectbox("Select Team", team_names, key="daily_team_choice")
            team = next(t for t in teams if t["team_name"] == team_choice)
            member_names = [m["name"] for m in team.get("members", [])]
            member_choice = st.selectbox("Select Member", member_names, key="daily_member_choice")
            dt = st.date_input("Date", value=date.today(), key="daily_date")
            lead_count = st.number_input("Lead Count", min_value=0, value=0, step=1, key="daily_lead_count")
            notes = st.text_area("Notes (optional)", height=80, key="daily_notes")

            if st.button("Save Lead"):
                entry = {
                    "date": dt.strftime("%Y-%m-%d"),
                    "team_id": team["team_id"],
                    "member_id": next(m["member_id"] for m in team["members"] if m["name"] == member_choice),
                    "lead_count": int(lead_count),
                    "notes": notes or ""
                }
                data.setdefault("leads", []).append(entry)
                if save_data(data, f"Add lead: {member_choice} {entry['date']}", sha):
                    st.success("Lead saved ✅")
                    st.rerun()

                else:
                    st.error("Failed to save lead. Check GitHub permissions & token.")

        with col2:
            st.markdown("#### Quick Stats")
            leads_all = pd.DataFrame(data.get("leads", []))
            if leads_all.empty:
                st.write("No leads yet.")
            else:
                leads_all["date"] = pd.to_datetime(leads_all["date"]).dt.date
                today_total = int(leads_all[leads_all["date"] == date.today()]["lead_count"].sum())
                st.metric("Today's leads", today_total)

# Reports
# -----------------------
# Reports (Improved Layout)
# -----------------------
with tabs[2]:
    st.header("📜 Reports — Weekly / Monthly")

    # Auth check
    if not st.session_state.report_auth:
        st.warning("Reports are locked. Unlock them using the Authentication panel in the sidebar.")
        st.stop()

    st.markdown("View team and member performance for a selected period. Add notes and export summaries.")

    # === Period Selection ===
    with st.expander("🗓️ Report Period", expanded=True):
        period_type = st.radio("Report type", ["Weekly", "Monthly"], horizontal=True)

        if period_type == "Weekly":
            ref_date = st.date_input("Select a date within the week", value=date.today())
            start_dt = ref_date - timedelta(days=ref_date.weekday())
            end_dt = start_dt + timedelta(days=6)
            period_label = f"Week {start_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d}"
            period_key = f"weekly:{start_dt:%Y-%m-%d}"
        else:
            ref_date = st.date_input("Select any date within the month", value=date.today())
            start_dt = ref_date.replace(day=1)
            next_month = (start_dt.replace(day=28) + timedelta(days=4)).replace(day=1)
            end_dt = next_month - timedelta(days=1)
            period_label = f"Month {start_dt:%Y-%m}"
            period_key = f"monthly:{start_dt:%Y-%m}"

        st.markdown(f"**📅 Reporting period:** {period_label}")

    # === Data Preparation ===
    leads_df = pd.DataFrame(data.get("leads", []))
    members_flat = flatten_members(data)
    members_df = pd.DataFrame(members_flat)

    if leads_df.empty:
        st.info("No leads data found for reporting.")
        st.stop()

    leads_df["date"] = pd.to_datetime(leads_df["date"]).dt.date
    mask = (leads_df["date"] >= start_dt) & (leads_df["date"] <= end_dt)
    period_leads = leads_df.loc[mask].copy()

    teams = data.get("teams", [])
    team_names = ["All Teams"] + [t["team_name"] for t in teams]
    team_sel = st.selectbox("Filter by team", team_names)

    if team_sel == "All Teams":
        team_ids = [t["team_id"] for t in teams]
    else:
        sel_team = next(t for t in teams if t["team_name"] == team_sel)
        team_ids = [sel_team["team_id"]]

    # === Helper ===
    def remark_from_pct(pct):
        if pct <= 0:
            return "No Target / No Work"
        if pct >= 100:
            return "Exceeded"
        if pct >= 90:
            return "On Track"
        if pct >= 60:
            return "Needs Improvement"
        return "Underperforming"

    # === Aggregate ===
    team_rows, member_rows = [], []
    for t in teams:
        if t["team_id"] not in team_ids:
            continue
        members = t.get("members", [])
        t_leads = period_leads[period_leads["team_id"] == t["team_id"]]
        total = int(t_leads["lead_count"].sum())
        target = sum(int(m.get("weekly_target" if period_type=="Weekly" else "monthly_target", 0)) for m in members)
        pct = (total / target * 100) if target > 0 else 0
        team_rows.append({
            "Team": t["team_name"],
            "Total Leads": total,
            "Target": target,
            "% Achieved": round(pct, 1),
            "Status": remark_from_pct(pct),
        })
        for m in members:
            m_leads = int(t_leads[t_leads["member_id"] == m["member_id"]]["lead_count"].sum())
            m_target = int(m.get("weekly_target" if period_type=="Weekly" else "monthly_target", 0))
            m_pct = (m_leads / m_target * 100) if m_target > 0 else 0
            member_rows.append({
                "Team": t["team_name"],
                "Member": m["name"],
                "Leads": m_leads,
                "Target": m_target,
                "% Achieved": round(m_pct, 1),
                "Status": remark_from_pct(m_pct),
            })

    team_df = pd.DataFrame(team_rows)
    member_df = pd.DataFrame(member_rows)

    # === Summary Cards ===
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Teams in View", len(team_df))
    c2.metric("Members in View", len(member_df))
    c3.metric(f"Total Leads ({period_type})", int(period_leads["lead_count"].sum()))
    c4.metric("Avg Performance", f"{member_df['% Achieved'].mean():.1f}%" if not member_df.empty else "0.0%")

    # === Notes ===
    st.markdown("### 📝 Notes & Feedback")
    with st.expander("✍️ Edit Notes (Teams & Members)", expanded=False):
        edit_team_name = st.selectbox("Select a team to add/edit notes", ["(none)"] + [t["team_name"] for t in teams])
        member_note_edits = {}
        edit_team = next((t for t in teams if t["team_name"] == edit_team_name), None) if edit_team_name != "(none)" else None

        if edit_team:
            t_notes = edit_team.get("report_notes", {}) or {}
            prev_team_note = t_notes.get(period_key, "")
            new_team_note = st.text_area(f"Team note for {edit_team['team_name']} ({period_label})", value=prev_team_note, height=120)
        else:
            new_team_note = None

        for t in teams:
            if t["team_id"] not in team_ids:
                continue
            st.markdown(f"#### {t['team_name']}")
            for m in t.get("members", []):
                prev_note = (m.get("report_notes", {}) or {}).get(period_key, "")
                key = f"{t['team_id']}_{m['member_id']}_{period_key}"
                note = st.text_area(f"{m['name']}", value=prev_note, height=60, key=key)
                member_note_edits[(t["team_id"], m["member_id"])] = note

        if st.button("💾 Save Notes"):
            modified = False
            if edit_team and new_team_note is not None:
                if "report_notes" not in edit_team:
                    edit_team["report_notes"] = {}
                edit_team["report_notes"][period_key] = new_team_note
                modified = True

            for t in teams:
                for m in t.get("members", []):
                    key = (t["team_id"], m["member_id"])
                    if key in member_note_edits:
                        new_note = member_note_edits[key]
                        if "report_notes" not in m:
                            m["report_notes"] = {}
                        if new_note != m["report_notes"].get(period_key, ""):
                            m["report_notes"][period_key] = new_note
                            modified = True

            if modified:
                ok = save_data(data, f"Update report notes {period_key}", sha)
                if ok:
                    st.success("Notes saved to GitHub ✅")
                    data, sha = load_data()
                else:
                    st.error("Failed to save notes.")
            else:
                st.info("No changes detected.")

    # === Tables ===
    st.markdown("### 📊 Performance Summary")
    colA, colB = st.tabs(["Team Summary", "Member Details"])

    with colA:
        if team_df.empty:
            st.info("No data for this period.")
        else:
            st.dataframe(team_df.style.applymap(lambda v: pct_to_bg(v) if isinstance(v, (int, float)) else "", subset=["% Achieved"]), use_container_width=True)

    with colB:
        if member_df.empty:
            st.info("No data for this period.")
        else:
            st.dataframe(member_df.style.applymap(lambda v: pct_to_bg(v) if isinstance(v, (int, float)) else "", subset=["% Achieved"]), use_container_width=True)

    # === Export ===
    st.markdown("### 📤 Export Report")
    export_name = f"report_{period_key.replace(':','_')}"

    col1, col2 = st.columns(2)
    if col1.download_button("⬇️ Download CSV", data=member_df.to_csv(index=False).encode("utf-8"),
                             file_name=f"{export_name}.csv", mime="text/csv"):
        st.success("CSV ready for download")

    # === Export ===
st.markdown("### 📤 Export Report")
export_name = f"report_{period_key.replace(':','_')}"

col1, col2 = st.columns(2)

# CSV Export
col1.download_button(
    "⬇️ Download CSV",
    data=member_df.to_csv(index=False).encode("utf-8"),
    file_name=f"{export_name}.csv",
    mime="text/csv",
)

# Excel Export
excel_buffer = io.BytesIO()
with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
    if not team_df.empty:
        team_df.to_excel(writer, sheet_name="Teams", index=False)
    if not member_df.empty:
        member_df.to_excel(writer, sheet_name="Members", index=False)
excel_buffer.seek(0)

col2.download_button(
    "⬇️ Download Excel (.xlsx)",
    data=excel_buffer.getvalue(),
    file_name=f"{export_name}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)


# Admin Panel
with tabs[3]:
    st.header("🧑‍💼 Admin Panel (password required)")

    # centralized auth
    if not st.session_state.admin_auth:
        st.warning("Admin Panel is locked. Unlock it using the Authentication panel in the sidebar.")
        st.stop()

    st.subheader("Manage existing teams")
    teams_list = data.get("teams", [])
    if not teams_list:
        st.info("No teams yet. Add one below.")
    else:
        for t_idx, team in enumerate(list(teams_list)):
            with st.expander(f"🏷 {team['team_name']}"):
                new_tname = st.text_input("Team name", value=team["team_name"], key=f"tname_{t_idx}")
                if new_tname != team["team_name"]:
                    # ensure unique team names
                    if any(t2["team_name"] == new_tname for t2 in teams_list if t2 is not team):
                        st.error("A team with this name already exists.")
                    else:
                        team["team_name"] = new_tname
                        if save_data(data, f"Rename team {new_tname}", sha):
                            st.success("Team name updated")
                            st.experimental_rerun()

                st.markdown("### Members")
                for m_idx, member in enumerate(list(team.get("members", []))):
                    cols = st.columns([3, 1, 1, 1])
                    with cols[0]:
                        nm = st.text_input("Name", value=member["name"], key=f"name_{t_idx}_{m_idx}")
                    with cols[1]:
                        wk = st.number_input("Weekly target", min_value=0, value=int(member.get("weekly_target", 0)), key=f"wk_{t_idx}_{m_idx}")
                    with cols[2]:
                        mo = st.number_input("Monthly target", min_value=0, value=int(member.get("monthly_target", 0)), key=f"mo_{t_idx}_{m_idx}")
                    with cols[3]:
                        confirm_del = st.checkbox("Confirm", key=f"confirm_del_{t_idx}_{m_idx}")
                        if st.button("🗑️ Delete", key=f"delm_{t_idx}_{m_idx}"):
                            if confirm_del:
                                team["members"].pop(m_idx)
                                if save_data(data, f"Delete member {member['name']}", sha):
                                    st.success("Member deleted")
                                    st.experimental_rerun()
                            else:
                                st.error("Tick 'Confirm' to delete member")

                    if nm != member["name"] or wk != member.get("weekly_target", 0) or mo != member.get("monthly_target", 0):
                        member["name"] = nm
                        member["weekly_target"] = int(wk)
                        member["monthly_target"] = int(mo)
                        if save_data(data, f"Update member {nm}", sha):
                            st.success("Member updated")
                            st.experimental_rerun()

                with st.expander("➕ Add member"):
                    add_name = st.text_input("Member name", key=f"addname_{t_idx}")
                    add_weekly = st.number_input("Weekly target", min_value=0, key=f"addwk_{t_idx}")
                    add_monthly = st.number_input("Monthly target", min_value=0, key=f"addmo_{t_idx}")
                    if st.button("Add member", key=f"addbtn_{t_idx}"):
                        if not add_name.strip():
                            st.error("Enter name")
                        elif any(m.get("name", "").strip().lower() == add_name.strip().lower() for m in team.get("members", [])):
                            st.error("Member with this name already exists in the team.")
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

                st.markdown("---")
                confirm_team_del = st.checkbox("Confirm", key=f"confirm_delteam_{t_idx}")
                if st.button(f"🗑️ Delete team '{team['team_name']}'", key=f"delteam_{t_idx}"):
                    if confirm_team_del:
                        data["teams"].remove(team)
                        if save_data(data, f"Delete team {team['team_name']}", sha):
                            st.success("Team deleted")
                            st.experimental_rerun()
                    else:
                        st.error("Tick 'Confirm' to delete team")

    st.divider()
    st.subheader("➕ Add new team")
    with st.form("add_team_form"):
        new_team_name = st.text_input("Team name")
        new_n = st.number_input("No. of members", min_value=1, max_value=50, value=2)
        new_members = []
        duplicate_name = False
        seen = set()
        for i in range(int(new_n)):
            cols = st.columns([3, 1, 1])
            with cols[0]:
                nm = st.text_input(f"Member {i+1} name", key=f"new_nm_{i}")
            with cols[1]:
                nw = st.number_input("Weekly target", min_value=0, key=f"new_w_{i}")
            with cols[2]:
                nmth = st.number_input("Monthly target", min_value=0, key=f"new_m_{i}")
            nm_str = nm.strip()
            if nm_str.lower() in (s.lower() for s in seen if s):
                duplicate_name = True
            seen.add(nm_str)
            new_members.append({
                "name": nm_str,
                "member_id": gen_id("M"),
                "weekly_target": int(nw),
                "monthly_target": int(nmth),
            })
        if st.form_submit_button("Save team"):
            if not new_team_name.strip() or any(m["name"] == "" for m in new_members):
                st.error("Fill all fields")
            elif duplicate_name:
                st.error("Duplicate member names detected")
            elif any(t["team_name"].strip().lower() == new_team_name.strip().lower() for t in data.get("teams", [])):
                st.error("A team with this name already exists.")
            else:
                new_team = {"team_id": gen_id("T"), "team_name": new_team_name.strip(), "members": new_members}
                data.setdefault("teams", []).append(new_team)
                if save_data(data, f"Add team {new_team_name}", sha):
                    st.success("Team added")
                    st.experimental_rerun()

# End of file
