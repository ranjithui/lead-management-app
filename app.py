# app.py
"""
Lead Management App — production version with:
 - Password protection for Daily Update & Admin Panel
 - Compact "All Teams" Dashboard with top summary
 - Reporting (weekly/monthly) with team & member notes saved to GitHub
 - Export CSV and Excel (.xlsx)
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
ADMIN = st.secrets.get("admin", {}) if st.secrets is not None else {}

GITHUB_TOKEN = GITHUB.get("token", "")
REPO_OWNER = GITHUB.get("repo_owner", "")
REPO_NAME = GITHUB.get("repo_name", "")
DATA_PATH = GITHUB.get("data_path", "data/leads_data.json")
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

# Admin password fallback
ADMIN_PASSWORD = ADMIN.get("password", "Admin@2025")

# -----------------------
# GitHub helpers
# -----------------------
def gh_api_url():
    """Construct GitHub Contents API url for the data file."""
    return f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{DATA_PATH}"

def load_data():
    """
    Load the unified JSON from GitHub.
    Returns: (data_dict, sha) — sha is needed for updating the same file.
    If file not present, returns default structure and sha None.
    """
    url = gh_api_url()
    if not REPO_OWNER or not REPO_NAME:
        st.error("GitHub repo_owner/repo_name not set in secrets. Please configure st.secrets.github.")
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
            # Malformed JSON — return defaults (safe fallback)
            return {"teams": [], "leads": []}, None
    elif res.status_code == 404:
        # File doesn't exist yet
        return {"teams": [], "leads": []}, None
    else:
        st.error(f"GitHub read error {res.status_code}: {res.text}")
        return {"teams": [], "leads": []}, None

def save_data(data, message, sha=None):
    """
    Save the entire unified data back to GitHub.
    message: commit message
    sha: if provided, used to update existing file
    Returns True on success.
    """
    url = gh_api_url()
    if not REPO_OWNER or not REPO_NAME:
        st.error("GitHub repo_owner/repo_name not set in secrets. Cannot save.")
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
    """Generate short random id with prefix."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"

def flatten_members(data):
    """
    Flatten teams -> members into a list of members with team metadata.
    Useful for merges and reporting.
    """
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
    """
    Calculate per-member totals and team aggregates for a period.
    - leads_df: DataFrame of leads (columns: date, member_id, lead_count)
    - members_df: DataFrame of members with targets
    - period: "All Time" | "This Month" | "This Week"
    Returns:
      - member_agg: DataFrame with member totals & percent columns
      - team_agg: DataFrame with team totals and average %s
    """
    # If no leads, prepare empty shapes
    if leads_df is None or leads_df.empty:
        member_cols = ["member_id", "name", "team_id", "team_name", "total_leads", "weekly_target", "monthly_target", "weekly_pct", "monthly_pct"]
        return pd.DataFrame(columns=member_cols), pd.DataFrame(columns=["team_id", "team_name", "team_leads", "avg_weekly_pct", "avg_monthly_pct"])

    df = leads_df.copy()
    df["date"] = pd.to_datetime(df["date"])

    today = pd.to_datetime(date.today())

    # Filter by period
    if period == "This Week":
        cutoff = today - pd.Timedelta(days=7)
        df = df[df["date"] >= cutoff]
    elif period == "This Month":
        cutoff = today.replace(day=1)
        df = df[df["date"] >= cutoff]
    # else All Time: no filter

    # Sum per member
    member_tot = df.groupby("member_id", as_index=False)["lead_count"].sum().rename(columns={"lead_count": "total_leads"})

    # Merge with members to keep targets and team info
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

    # Percentages relative to targets (avoid division by zero)
    merged["weekly_pct"] = merged.apply(lambda r: (r["total_leads"] / r["weekly_target"] * 100) if r.get("weekly_target",0) > 0 else 0, axis=1)
    merged["monthly_pct"] = merged.apply(lambda r: (r["total_leads"] / r["monthly_target"] * 100) if r.get("monthly_target",0) > 0 else 0, axis=1)

    # Team aggregation: team totals and average of member %s
    team_grp = merged.groupby(["team_id","team_name"], as_index=False).agg({
        "total_leads":"sum",
        "weekly_pct":"mean",
        "monthly_pct":"mean"
    }).rename(columns={"total_leads":"team_leads","weekly_pct":"avg_weekly_pct","monthly_pct":"avg_monthly_pct"})

    return merged, team_grp

# -----------------------
# UI helper: styling + progress bar rendering
# -----------------------
CARD_CSS = """
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
    """Return color hex and width percentage (clamped)."""
    pct = max(0.0, float(pct))
    if pct < 50:
        color = "#e24b4b"  # red
    elif pct < 80:
        color = "#f0b429"  # yellow
    else:
        color = "#16a34a"  # green
    width = min(round(pct, 1), 100)
    return color, width

def render_progress_bar_html(pct, label_text):
    """Return HTML for a labeled progress bar."""
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
# Main app layout
# -----------------------
st.set_page_config(page_title="Lead Management", layout="wide")
st.markdown(CARD_CSS, unsafe_allow_html=True)
st.title("📊 Lead Management — Unified Dashboard")

# Load data from GitHub
data, sha = load_data()

# If repository doesn't have file yet, create it (bootstrap)
if sha is None and data.get("teams", []) == [] and data.get("leads", []) == []:
    created = save_data({"teams": [], "leads": []}, "Initialize leads_data.json")
    if created:
        data, sha = load_data()

# Top-level tabs
tabs = st.tabs(["Daily Update", "Dashboard", "Reports", "Admin Panel"])

# -----------------------
# Tab: Daily Update (password protected)
# -----------------------
with tabs[0]:
    st.header("🕘 Daily Update")

    # Ensure session state
    if "daily_auth" not in st.session_state:
        st.session_state.daily_auth = False

    if not st.session_state.daily_auth:
        pw = st.text_input("Enter password to log leads", type="password", key="daily_pw")
        if st.button("🔓 Unlock Daily Input"):
            if pw == ADMIN_PASSWORD:
                st.session_state.daily_auth = True
                st.success("Access granted ✅")
                st.rerun()
            else:
                st.error("Wrong password ❌")
        st.stop()

    teams = data.get("teams", [])
    if not teams:
        st.info("No teams defined yet. Create them in Admin Panel before logging leads.")
    else:
        # Left: form. Right: quick stats
        col1, col2 = st.columns([2, 1])
        with col1:
            team_names = [t["team_name"] for t in teams]
            team_choice = st.selectbox("Select Team", team_names)
            team = next(t for t in teams if t["team_name"] == team_choice)
            member_names = [m["name"] for m in team.get("members", [])]
            member_choice = st.selectbox("Select Member", member_names)
            dt = st.date_input("Date", value=date.today())
            lead_count = st.number_input("Lead Count", min_value=0, value=0, step=1)
            notes = st.text_area("Notes (optional)", height=80)

            if st.button("💾 Save Lead"):
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
                leads_all["date"] = pd.to_datetime(leads_all["date"])
                today = pd.to_datetime(date.today())
                today_total = int(leads_all[leads_all["date"] == today]["lead_count"].sum())
                st.metric("Today's leads", today_total)

# -----------------------
# Tab: Dashboard (compact + summary + all teams visible)
# -----------------------
with tabs[1]:
    st.subheader("📈 Team Dashboard — All Teams Overview")

    # Period filter
    period = st.selectbox("Period filter", ["All Time", "This Month", "This Week"], index=0)

    leads_df = pd.DataFrame(data.get("leads", []))
    members_flat = flatten_members(data)
    members_df = pd.DataFrame(members_flat) if members_flat else pd.DataFrame(columns=["member_id","name","team_id","team_name","weekly_target","monthly_target"])

    if leads_df.empty:
        st.info("No leads logged yet — dashboard will populate once you add leads.")
    else:
        # Compute aggregates
        member_agg, team_agg = calc_totals(leads_df, members_df, period=period)
        teams = data.get("teams", [])

        if not teams:
            st.info("No teams defined. Add teams in the Admin Panel.")
        else:
            # === Summary Bar ===
            total_teams = len(teams)
            total_members = len(members_df)
            total_leads = int(leads_df["lead_count"].sum()) if not leads_df.empty else 0

            avg_team_pct = 0
            if not team_agg.empty:
                avg_team_pct = (team_agg["avg_weekly_pct"].mean() + team_agg["avg_monthly_pct"].mean()) / 2

            st.markdown("""
            <style>
            .summary-card {
                background: #f0f2f6;
                padding: 10px 14px;
                border-radius: 10px;
                display: flex;
                justify-content: space-around;
                margin-bottom: 15px;
                text-align: center;
                box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            }
            .summary-item {
                font-size: 14px;
                font-weight: 600;
                color: #333;
            }
            .summary-value {
                font-size: 18px;
                font-weight: 700;
                color: #0073e6;
            }
            .team-block {
                background: #f8f9fa;
                padding: 12px 14px;
                border-radius: 10px;
                margin-bottom: 10px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            }
            .team-title {
                font-size: 16px;
                font-weight: 600;
                color: #222;
                margin-bottom: 6px;
            }
            .member-card {
                background: white;
                border-radius: 8px;
                padding: 8px 10px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.05);
                margin-bottom: 6px;
            }
            .small { font-size: 12px; color: #666; }
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

            # === Teams Section ===
            for team in teams:
                t_id = team["team_id"]
                trow = team_agg[team_agg["team_id"] == t_id] if not team_agg.empty else pd.DataFrame()
                team_leads = int(trow["team_leads"].iloc[0]) if not trow.empty else 0
                avg_week = float(trow["avg_weekly_pct"].iloc[0]) if not trow.empty else 0.0
                avg_month = float(trow["avg_monthly_pct"].iloc[0]) if not trow.empty else 0.0
                team_avg = (avg_week + avg_month) / 2 if (avg_week or avg_month) else 0.0

                st.markdown(f"<div class='team-block'>", unsafe_allow_html=True)
                st.markdown(f"<div class='team-title'>🏷 {team['team_name']} — Leads: {team_leads}</div>", unsafe_allow_html=True)
                st.markdown(render_progress_bar_html(team_avg, "Team Avg (weekly + monthly)"), unsafe_allow_html=True)

                members_in_team = members_df[members_df["team_id"] == t_id].sort_values("name")
                if members_in_team.empty:
                    st.markdown("<div class='small'>No members yet</div>", unsafe_allow_html=True)
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

# -----------------------
# Tab: Reports (weekly/monthly by team & member) — with notes + CSV/XLSX export
# -----------------------
with tabs[2]:
    st.header("📜 Reports — Weekly / Monthly (Team & Member)")

    # --- Report type selection ---
    period_type = st.selectbox("Report type", ["Weekly", "Monthly"], index=0)

    # For weekly: pick any date inside the week; for monthly: pick month (use a date and treat as month)
    if period_type == "Weekly":
        ref_date = st.date_input("Select a date within the week", value=date.today(), key="report_week_date")
        # compute week start (Monday) and end (Sunday)
        weekday = ref_date.weekday()  # Monday=0
        start_dt = ref_date - timedelta(days=weekday)
        end_dt = start_dt + timedelta(days=6)
        period_label = f"Week {start_dt.strftime('%Y-%m-%d')} → {end_dt.strftime('%Y-%m-%d')}"
        # key suffix for notes storage
        period_key = f"weekly:{start_dt.strftime('%Y-%m-%d')}"
    else:
        ref_date = st.date_input("Select any date within the month", value=date.today(), key="report_month_date")
        start_dt = ref_date.replace(day=1)
        # compute last day of month
        next_month = (start_dt.replace(day=28) + timedelta(days=4)).replace(day=1)
        end_dt = next_month - timedelta(days=1)
        period_label = f"Month {start_dt.strftime('%Y-%m')}"
        period_key = f"monthly:{start_dt.strftime('%Y-%m')}"

    st.markdown(f"**Reporting period:** {period_label}")

    # Build leads and members dataframes
    leads_df = pd.DataFrame(data.get("leads", []))
    members_flat = flatten_members(data)
    members_df = pd.DataFrame(members_flat) if members_flat else pd.DataFrame(columns=["member_id","name","team_id","team_name","weekly_target","monthly_target"])

    # Filter leads to the chosen period
    if not leads_df.empty:
        leads_df["date"] = pd.to_datetime(leads_df["date"])
        mask = (leads_df["date"] >= pd.to_datetime(start_dt)) & (leads_df["date"] <= pd.to_datetime(end_dt))
        period_leads = leads_df.loc[mask].copy()
    else:
        period_leads = pd.DataFrame(columns=["date","team_id","member_id","lead_count","notes"])

    # Select team (All or specific)
    teams = data.get("teams", [])
    team_options = ["All Teams"] + [t["team_name"] for t in teams]
    team_sel = st.selectbox("Team", team_options, index=0)

    # Filter teams/members if specific selected
    if team_sel != "All Teams":
        sel_team = next((t for t in teams if t["team_name"] == team_sel), None)
        team_ids = [sel_team["team_id"]] if sel_team else []
    else:
        sel_team = None
        team_ids = [t["team_id"] for t in teams]

    # Prepare aggregations
    # Team-level summary
    team_rows = []
    member_rows = []

    # Helper: status remark
    def remark_from_pct(pct):
        if pct <= 0:
            return "No Target/No Work"
        if pct >= 100:
            return "Exceeded"
        if pct >= 80:
            return "On Track"
        return "Needs Focus"

    for t in teams:
        if t["team_id"] not in team_ids:
            continue

        # team-level leads
        t_leads_df = period_leads[period_leads["team_id"] == t["team_id"]] if not period_leads.empty else pd.DataFrame()
        team_total = int(t_leads_df["lead_count"].sum()) if not t_leads_df.empty else 0

        # compute team weekly/monthly target by summing member targets
        members = t.get("members", [])
        if period_type == "Weekly":
            team_target = sum(int(m.get("weekly_target", 0)) for m in members) if members else 0
        else:
            team_target = sum(int(m.get("monthly_target", 0)) for m in members) if members else 0

        team_pct = (team_total / team_target * 100) if team_target > 0 else 0.0
        team_rows.append({
            "team_id": t["team_id"],
            "team_name": t["team_name"],
            "team_total_leads": team_total,
            "team_target": team_target,
            "team_pct": round(team_pct, 1),
            "team_status": remark_from_pct(team_pct),
            "team_note": (t.get("report_notes", {}) or {}).get(period_key, "")
        })

        # member-level rows
        for m in members:
            m_leads = t_leads_df[t_leads_df["member_id"] == m["member_id"]]["lead_count"].sum() if not t_leads_df.empty else 0
            m_target = int(m.get("weekly_target", 0)) if period_type == "Weekly" else int(m.get("monthly_target", 0))
            m_pct = (m_leads / m_target * 100) if m_target > 0 else 0.0
            member_rows.append({
                "team_id": t["team_id"],
                "team_name": t["team_name"],
                "member_id": m["member_id"],
                "member_name": m["name"],
                "member_total_leads": int(m_leads),
                "member_target": m_target,
                "member_pct": round(m_pct, 1),
                "member_status": remark_from_pct(m_pct),
                "member_note": (m.get("report_notes", {}) or {}).get(period_key, "")
            })

    team_summary_df = pd.DataFrame(team_rows)
    member_summary_df = pd.DataFrame(member_rows)

    # Show summary metrics on top
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Teams in view", team_summary_df.shape[0])
    col2.metric("Members in view", member_summary_df.shape[0])
    total_leads_all = int(period_leads["lead_count"].sum()) if not period_leads.empty else 0
    col3.metric(f"Leads ({period_type})", total_leads_all)
    avg_perf = member_summary_df["member_pct"].mean() if not member_summary_df.empty else 0
    col4.metric("Avg Member %", f"{avg_perf:.1f}%")

    st.markdown("---")

    # Editable notes area
    st.markdown("### ✍️ Add/Edit Notes (saves to repo)")
    st.markdown("Notes are saved per period (weekly/monthly). Team notes are team-wide; member notes are per individual.")
    notes_col1, notes_col2 = st.columns([1, 1])

    # Team-level note editor (only if a specific team selected OR all teams: allow editing per team via select)
    if sel_team is None:
        # let user pick a team to edit notes for
        edit_team_name = st.selectbox("Pick team to edit team-note", ["(none)"] + [t["team_name"] for t in teams], index=0, key="edit_team_pick")
        if edit_team_name != "(none)":
            edit_team = next(t for t in teams if t["team_name"] == edit_team_name)
        else:
            edit_team = None
    else:
        edit_team = sel_team

    team_note_text = ""
    if edit_team:
        team_report_notes = edit_team.get("report_notes", {}) or {}
        team_note_text = team_report_notes.get(period_key, "")
        team_note_text = st.text_area(f"Team note for {edit_team['team_name']} ({period_label})", value=team_note_text, height=120, key=f"team_note_{edit_team['team_id']}")
    else:
        st.text("Select a team to edit its note.",)

    # Member-level notes editors: show table of members in current team view with editable textareas
    st.markdown("#### Member notes (edit then Save Notes)")
    member_note_edits = {}
    # show grouped by team for clarity
    for t in teams:
        if t["team_id"] not in team_ids:
            continue
        st.markdown(f"**{t['team_name']}**")
        members = t.get("members", [])
        if not members:
            st.markdown("_No members_")
            continue
        cols = st.columns([1, 1, 2])
        cols[0].markdown("**Member**")
        cols[1].markdown("**Leads**")
        cols[2].markdown("**Note**")
        for m in members:
            m_total = int(period_leads[(period_leads["member_id"] == m["member_id"]) & (period_leads["team_id"] == t["team_id"])]["lead_count"].sum()) if not period_leads.empty else 0
            prev_note = (m.get("report_notes", {}) or {}).get(period_key, "")
            cols = st.columns([1, 1, 2])
            cols[0].write(m["name"])
            cols[1].write(int(m_total))
            note_key = f"note_{t['team_id']}_{m['member_id']}_{period_key}"
            txt = cols[2].text_area(" ", value=prev_note, key=note_key, height=80)
            member_note_edits[(t["team_id"], m["member_id"])] = txt

    # Save notes button
    if st.button("💾 Save Notes to repo"):
        modified = False
        # Save team note if edited
        if edit_team:
            if "report_notes" not in edit_team:
                edit_team["report_notes"] = {}
            prev = edit_team["report_notes"].get(period_key, "")
            new = st.session_state.get(f"team_note_{edit_team['team_id']}", "")
            if new != prev:
                edit_team["report_notes"][period_key] = new
                modified = True

        # Save member notes
        for t in teams:
            for m in t.get("members", []):
                key = (t["team_id"], m["member_id"])
                if key in member_note_edits:
                    new_note = member_note_edits[key]
                    if "report_notes" not in m:
                        m["report_notes"] = {}
                    prev_note = m["report_notes"].get(period_key, "")
                    if new_note != prev_note:
                        m["report_notes"][period_key] = new_note
                        modified = True

        if modified:
            ok = save_data(data, f"Update report notes {period_key}", sha)
            if ok:
                st.success("Notes saved to GitHub ✅")
                # reload data & sha to ensure we have latest
                data, sha = load_data()
            else:
                st.error("Failed to save notes — check GitHub token & permissions.")
        else:
            st.info("No changes detected.")

    st.markdown("---")

    # Display report tables
    st.markdown("### Team Summary")
    if team_summary_df.empty:
        st.write("No teams in view / no data for the selected period.")
    else:
        st.dataframe(team_summary_df.sort_values("team_total_leads", ascending=False), use_container_width=True)

    st.markdown("### Member Details")
    if member_summary_df.empty:
        st.write("No members / no data for the selected period.")
    else:
        st.dataframe(member_summary_df.sort_values(["team_name","member_total_leads"], ascending=[True, False]), use_container_width=True)

    # --- Export section ---
    st.markdown("---")
    st.markdown("### Export Report")
    export_name = f"report_{period_key.replace(':','_')}"
    # CSV
    export_btn_col1, export_btn_col2 = st.columns([1,1])
    if export_btn_col1.button("⬇️ Download CSV"):
        # Combine team & member sheets into one CSV with markers
        out_rows = []
        for _, r in team_summary_df.iterrows():
            out_rows.append({
                "level":"team",
                "team_id": r["team_id"],
                "team_name": r["team_name"],
                "name":"",
                "total_leads": r["team_total_leads"],
                "target": r["team_target"],
                "pct": r["team_pct"],
                "status": r["team_status"],
                "note": r.get("team_note","")
            })
        for _, r in member_summary_df.iterrows():
            out_rows.append({
                "level":"member",
                "team_id": r["team_id"],
                "team_name": r["team_name"],
                "name": r["member_name"],
                "total_leads": r["member_total_leads"],
                "target": r["member_target"],
                "pct": r["member_pct"],
                "status": r["member_status"],
                "note": r.get("member_note","")
            })
        out_df = pd.DataFrame(out_rows)
        csv_bytes = out_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download CSV file", data=csv_bytes, file_name=f"{export_name}.csv", mime="text/csv")

    # Excel
    if export_btn_col2.button("⬇️ Download Excel (.xlsx)"):
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            if not team_summary_df.empty:
                team_summary_df.to_excel(writer, sheet_name="Teams", index=False)
            if not member_summary_df.empty:
                member_summary_df.to_excel(writer, sheet_name="Members", index=False)
            # no explicit writer.save() needed inside context manager
        output.seek(0)
        st.download_button("Download Excel file", data=output.getvalue(), file_name=f"{export_name}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    st.markdown("---")
    st.info("Notes are stored inside each team/member object under `report_notes` keyed by the period (e.g. `weekly:2025-11-03` or `monthly:2025-11`).")

# -----------------------
# Tab: Admin Panel (full CRUD) — password protected
# -----------------------
with tabs[3]:
    st.header("🧑‍💼 Admin Panel (Teams & Members)")

    # Simple admin auth (password in secrets or fallback)
    if "admin_auth" not in st.session_state:
        st.session_state.admin_auth = False

    if not st.session_state.admin_auth:
        pw = st.text_input("Admin Password", type="password")
        if st.button("🔐 Login"):
            if pw == ADMIN_PASSWORD:
                st.session_state.admin_auth = True
                st.success("Authenticated")
                st.rerun()
            else:
                st.error("Wrong password")
        st.stop()

    st.subheader("Manage existing teams")
    teams_list = data.get("teams", [])
    if not teams_list:
        st.info("No teams yet. Add one below.")
    else:
        # Show each team with editing controls
        for t_idx, team in enumerate(list(teams_list)):  # copy list to avoid runtime mutation issues
            with st.expander(f"🏷 {team['team_name']}"):
                # Edit team name
                new_tname = st.text_input("Team name", value=team["team_name"], key=f"tname_{t_idx}")
                if new_tname != team["team_name"]:
                    team["team_name"] = new_tname
                    if save_data(data, f"Rename team {new_tname}", sha):
                        st.success("Team name updated")
                        st.rerun()

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
                        if st.button("🗑️", key=f"delm_{t_idx}_{m_idx}"):
                            # Delete member
                            team["members"].pop(m_idx)
                            if save_data(data, f"Delete member {member['name']}", sha):
                                st.success("Member deleted")
                                st.rerun()

                    # If changed, save
                    if nm != member["name"] or wk != member.get("weekly_target", 0) or mo != member.get("monthly_target", 0):
                        member["name"] = nm
                        member["weekly_target"] = int(wk)
                        member["monthly_target"] = int(mo)
                        if save_data(data, f"Update member {nm}", sha):
                            st.success("Member updated")
                            st.rerun()

                # Add new member inside this team
                with st.expander("➕ Add member"):
                    add_name = st.text_input("Member name", key=f"addname_{t_idx}")
                    add_weekly = st.number_input("Weekly target", min_value=0, key=f"addwk_{t_idx}")
                    add_monthly = st.number_input("Monthly target", min_value=0, key=f"addmo_{t_idx}")
                    if st.button("Add member", key=f"addbtn_{t_idx}"):
                        if not add_name.strip():
                            st.error("Enter name")
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
                                st.rerun()

                st.markdown("---")
                # Delete entire team
                if st.button(f"🗑️ Delete team '{team['team_name']}'", key=f"delteam_{t_idx}"):
                    data["teams"].remove(team)
                    if save_data(data, f"Delete team {team['team_name']}", sha):
                        st.success("Team deleted")
                        st.rerun()

    # Section: Add new team
    st.divider()
    st.subheader("➕ Add new team")
    with st.form("add_team_form"):
        new_team_name = st.text_input("Team name")
        new_n = st.number_input("No. of members", min_value=1, max_value=50, value=2)
        new_members = []
        for i in range(int(new_n)):
            cols = st.columns([3, 1, 1])
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
            if not new_team_name.strip() or any(m["name"] == "" for m in new_members):
                st.error("Fill all fields")
            else:
                new_team = {"team_id": gen_id("T"), "team_name": new_team_name.strip(), "members": new_members}
                data.setdefault("teams", []).append(new_team)
                if save_data(data, f"Add team {new_team_name}", sha):
                    st.success("Team added")
                    st.rerun()

# End of app
