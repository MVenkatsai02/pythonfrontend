import os
import time
import json
from io import BytesIO
from datetime import date, datetime
from urllib.parse import urlencode

import streamlit as st
import requests
import pandas as pd
import qrcode
from PIL import Image

# ------------------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------------------
st.set_page_config(page_title="Smart QR HRMS", layout="wide")

# 🔧 Set your deployed backend here (Render FastAPI base URL)
BACKEND_URL = os.environ.get("BACKEND_URL", "https://python-c5i8.onrender.com").rstrip("/")

# Utility: standard headers with token
def auth_headers():
    token = st.session_state.get("hr_token")
    return {"Authorization": f"Bearer {token}"} if token else {}

# Safe API helpers (won't crash if server returns non-JSON/HTML)
def api_post(endpoint, json=None, data=None, files=None, headers=None, timeout=30):
    try:
        r = requests.post(f"{BACKEND_URL}{endpoint}", json=json, data=data, files=files, headers=headers, timeout=timeout)
        return r
    except Exception as e:
        st.error(f"POST {endpoint} failed: {e}")
        return None

def api_get(endpoint, headers=None, timeout=30):
    try:
        r = requests.get(f"{BACKEND_URL}{endpoint}", headers=headers, timeout=timeout)
        return r
    except Exception as e:
        st.error(f"GET {endpoint} failed: {e}")
        return None

def response_json_or_text(res):
    """Return (ok, data_or_text). ok=True => JSON dict/list; else text snippet."""
    if res is None:
        return False, "No response"
    try:
        return True, res.json()
    except Exception:
        return False, res.text[:400] if res.text else f"HTTP {res.status_code}"

def excel_download(df: pd.DataFrame, filename: str):
    bio = BytesIO()
    df.to_excel(bio, index=False)
    bio.seek(0)
    st.download_button(
        "📥 Download Excel",
        data=bio,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

# ------------------------------------------------------------------------------
# SESSION STATE
# ------------------------------------------------------------------------------
DEFAULTS = {
    "ui_view": "HOME",             # HOME | HR_AUTH | HR_DASH | EMPLOYEE | QR_DISPLAY
    "hr_authtab": "LOGIN",         # LOGIN | REGISTER
    "hr_token": None,              # JWT after login/register
    "hr_email": None,
    "hr_company": None,            # cached {"id", "name", ...}
    "qr_last_token": None,         # last seen token for QR display compare
    "frontend_base_url": "",       # For QR deep link; set your Streamlit URL here if deployed
}

for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ------------------------------------------------------------------------------
# NAVIGATION TOPBAR
# ------------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 🔗 Navigation")
    choice = st.radio(
        "Go to",
        ["🏠 Home", "👨‍💼 HR", "👷 Employee (QR only)", "🔳 QR Display"],
        index=0 if st.session_state["ui_view"] == "HOME"
        else 1 if st.session_state["ui_view"] in ["HR_AUTH", "HR_DASH"]
        else 2 if st.session_state["ui_view"] == "EMPLOYEE"
        else 3
    )

    if choice == "🏠 Home":
        st.session_state["ui_view"] = "HOME"
    elif choice == "👨‍💼 HR":
        # if logged in, go to dashboard; else to auth
        st.session_state["ui_view"] = "HR_DASH" if st.session_state["hr_token"] else "HR_AUTH"
    elif choice == "👷 Employee (QR only)":
        st.session_state["ui_view"] = "EMPLOYEE"
    elif choice == "🔳 QR Display":
        st.session_state["ui_view"] = "QR_DISPLAY"

    st.divider()
    st.caption("Backend:")
    st.code(BACKEND_URL)

# ------------------------------------------------------------------------------
# HOME
# ------------------------------------------------------------------------------
if st.session_state["ui_view"] == "HOME":
    st.title("Smart QR HRMS — Frontend")
    st.write("""
    Welcome! This frontend is tailored for your deployed FastAPI backend on Render.

    **Three perspectives:**
    - **HR**: Register/Login → Create Company (with geofence) → Add Employees (Excel/manual)
      → Regenerate QR → Monitor → Reports → Leave decisions → Export Excel
    - **Employee**: Accessible only via **QR scan**. Enter credentials + share location to **Check-In/Check-Out**, apply **Leave**.
    - **QR Display**: Shows scannable QR for employees. Polls every **30 seconds** for HR manual regeneration and naturally updates after midnight (IST).
    """)

    st.info("Use the left sidebar to navigate. Start with **HR** to register/login.")

# ------------------------------------------------------------------------------
# HR AUTH (Register or Login) — gated
# ------------------------------------------------------------------------------
elif st.session_state["ui_view"] == "HR_AUTH":
    st.title("👨‍💼 HR Access")

    tab_register, tab_login = st.tabs(["Register", "Login"])

    # Show only one at a time per your preference, but both tabs available for convenience.
    # If you want strictly one at a time, just use a radio and show selected.
    with tab_register:
        st.subheader("Create a new HR account")
        email = st.text_input("Email (for HR register)", key="reg_email")
        password = st.text_input("Password", type="password", key="reg_pwd")
        if st.button("Register", type="primary"):
            res = api_post("/auth/hr/register", json={"email": email, "password": password})
            ok, data = response_json_or_text(res)
            if res and res.status_code in (200, 201) and ok:
                st.success("✅ Registered successfully.")
                st.session_state["hr_token"] = data["access_token"]
                st.session_state["hr_email"] = email
                st.session_state["ui_view"] = "HR_DASH"
                st.rerun()
            else:
                st.error(data)

    with tab_login:
        st.subheader("Login (existing HR)")
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_pwd")
        if st.button("Login", type="primary"):
            res = api_post("/auth/hr/login", data={"username": email, "password": password})
            ok, data = response_json_or_text(res)
            if res and res.status_code == 200 and ok:
                st.success("✅ Logged in.")
                st.session_state["hr_token"] = data["access_token"]
                st.session_state["hr_email"] = email
                st.session_state["ui_view"] = "HR_DASH"
                st.rerun()
            else:
                st.error(data)

# ------------------------------------------------------------------------------
# HR DASHBOARD (only when logged in)
# ------------------------------------------------------------------------------
elif st.session_state["ui_view"] == "HR_DASH":
    if not st.session_state["hr_token"]:
        st.warning("Please login first.")
        st.session_state["ui_view"] = "HR_AUTH"
        st.stop()

    st.title("👨‍💼 HR Dashboard")
    colA, colB = st.columns([3, 1])
    with colA:
        st.caption(f"Signed in as: {st.session_state.get('hr_email')}")
    with colB:
        if st.button("🔒 Logout", use_container_width=True):
            st.session_state["hr_token"] = None
            st.session_state["hr_email"] = None
            st.session_state["hr_company"] = None
            st.session_state["ui_view"] = "HR_AUTH"
            st.rerun()


    # Sub-navigation within HR dashboard
    st.markdown("### Menu")
    hr_tab = st.radio(
        "Select area",
        ["🏢 Company", "👥 Employees", "🔳 QR", "📝 Leaves", "📊 Reports & Monitor"],
        horizontal=True
    )

    # ------------- COMPANY -------------
    if hr_tab == "🏢 Company":
        st.subheader("Company Management")
        with st.expander("Create Company (once per HR)"):
            name = st.text_input("Company Name")
            geo_lat = st.number_input("Latitude", format="%.6f")
            geo_lon = st.number_input("Longitude", format="%.6f")
            geo_radius = st.number_input("Radius (meters)", value=300, min_value=10, max_value=5000, step=10)
            if st.button("Create Company", type="primary"):
                res = api_post("/company/create",
                               json={"name": name, "geo_lat": geo_lat, "geo_lon": geo_lon, "geo_radius_m": geo_radius},
                               headers=auth_headers())
                ok, data = response_json_or_text(res)
                if res and res.status_code in (200, 201) and ok:
                    st.success("✅ Company created.")
                    st.session_state["hr_company"] = data
                else:
                    st.error(data)

        col1, col2 = st.columns(2)
        with col1:
            if st.button("View My Company"):
                res = api_get("/company/me", headers=auth_headers())
                ok, data = response_json_or_text(res)
                if res and res.status_code == 200 and ok:
                    st.session_state["hr_company"] = data
                    st.json(data)
                else:
                    st.error(data)
        with col2:
            if st.session_state.get("hr_company"):
                st.success(f"Current company: {st.session_state['hr_company']['name']} (ID {st.session_state['hr_company']['id']})")
            else:
                st.info("No company cached yet. Click **View My Company** to fetch.")

    # ------------- EMPLOYEES -------------
    elif hr_tab == "👥 Employees":
        st.subheader("Employee Management")

        st.markdown("**Add Single Employee**")
        name = st.text_input("Name")
        username = st.text_input("Username")
        password = st.text_input("Password")
        if st.button("Create Employee"):
            res_company = api_get("/company/me", headers=auth_headers())
            okc, datac = response_json_or_text(res_company)
            if res_company and res_company.status_code == 200 and okc:
                res = api_post("/employees/create",
                               json={"name": name, "username": username, "password": password, "active": True},
                               headers=auth_headers())
                ok, data = response_json_or_text(res)
                if res and res.status_code in (200, 201) and ok:
                    st.success("✅ Employee created.")
                else:
                    st.error(data)
            else:
                st.error("Please create/view company first.")

        st.divider()
        st.markdown("**Bulk Upload (Excel)**  — Columns: `name`, `username`, `password`")
        uploaded = st.file_uploader("Upload .xlsx", type=["xlsx"])
        if uploaded and st.button("Upload Excel"):
            files = {"file": (uploaded.name, uploaded.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
            res = requests.post(f"{BACKEND_URL}/employees/upload_excel", headers=auth_headers(), files=files, timeout=30)
            ok, data = response_json_or_text(res)
            if res and res.status_code == 200 and ok:
                st.success(f"✅ Uploaded. Created: {data.get('created')}, Skipped: {data.get('skipped')}")
            else:
                st.error(data)

        st.divider()
        if st.button("List Employees"):
            res = api_get("/employees/list", headers=auth_headers())
            ok, data = response_json_or_text(res)
            if res and res.status_code == 200 and ok:
                df = pd.DataFrame(data)
                st.dataframe(df, use_container_width=True)
                if not df.empty:
                    excel_download(df, "employees.xlsx")
            else:
                st.error(data)

    # ------------- QR -------------
    elif hr_tab == "🔳 QR":
        st.subheader("QR Management & Display Link")

        # Get my company
        res_company = api_get("/company/me", headers=auth_headers())
        okc, datac = response_json_or_text(res_company)
        if res_company and res_company.status_code == 200 and okc:
            company_id = datac["id"]
            st.info(f"Company ID: **{company_id}**")

            st.text_input(
                "Frontend Base URL to embed into QR deep-link (e.g., your Streamlit URL)",
                key="frontend_base_url",
                placeholder="https://your-frontend.streamlit.app"
            )

            col1, col2, col3 = st.columns(3)
            with col1:
                if st.button("Fetch Current QR"):
                    r = api_get(f"/qr/{company_id}/current")
                    ok, data = response_json_or_text(r)
                    if r and r.status_code == 200 and ok:
                        token = data["token"]
                        st.session_state["qr_last_token"] = token
                        params = {"company_id": company_id, "qr_token": token}
                        deep_link = f"{st.session_state['frontend_base_url'].rstrip('/')}/?{urlencode(params)}" if st.session_state["frontend_base_url"] else f"/?{urlencode(params)}"
                        # Generate QR
                        img = qrcode.make(deep_link)
                        st.image(img, caption=f"Current Token Date: {data['token_date']}", width=260)
                        st.code(deep_link)
                    else:
                        st.error(data)

            with col2:
                if st.button("Regenerate QR Now"):
                    r = api_post("/qr/regenerate", headers=auth_headers())
                    ok, data = response_json_or_text(r)
                    if r and r.status_code == 200 and ok:
                        st.success("✅ QR regenerated.")
                    else:
                        st.error(data)

            with col3:
                st.caption("Display page is in the **QR Display** section (left sidebar). It polls every 30s and redraws if HR changed the token.")

        else:
            st.error("Please create/view company first.")

    # ------------- LEAVES -------------
    elif hr_tab == "📝 Leaves":
        st.subheader("Leave Approvals")
        status_filter = st.selectbox("Filter", ["All", "Pending", "Approved", "Rejected"], index=1)

        q = "/leave/list"
        if status_filter != "All":
            q += f"?status_filter={status_filter}"

        r = api_get(q, headers=auth_headers())
        ok, data = response_json_or_text(r)
        if r and r.status_code == 200 and ok:
            df = pd.DataFrame(data)
            st.dataframe(df, use_container_width=True)
            if not df.empty:
                choice = st.selectbox("Select leave_id to decide", df["id"].tolist())
                dec = st.radio("Decision", ["Approved", "Rejected"], horizontal=True)
                if st.button("Submit Decision"):
                    rr = api_post("/leave/decide", headers=auth_headers(), json={"leave_id": int(choice), "decision": dec})
                    ok2, d2 = response_json_or_text(rr)
                    if rr and rr.status_code == 200 and ok2:
                        st.success("✅ Decision saved.")
                        st.rerun()
                    else:
                        st.error(d2)
        else:
            st.error(data)

    # ------------- REPORTS & MONITOR -------------
    elif hr_tab == "📊 Reports & Monitor":
        st.subheader("Monitor — Today Summary")
        if st.button("Refresh Today"):
            r = api_get("/monitor", headers=auth_headers())
            ok, data = response_json_or_text(r)
            if r and r.status_code == 200 and ok:
                st.json(data)
            else:
                st.error(data)

        st.divider()
        st.subheader("Attendance Reports (Excel export)")
        scope = st.radio("Scope", ["company", "employee"], horizontal=True)
        company_id = st.number_input("Company ID", min_value=1)
        employee_id = None
        if scope == "employee":
            employee_id = st.number_input("Employee ID", min_value=1)
        period_type = st.selectbox("Period", ["date", "week", "month"])
        start_d = st.date_input("Start Date", date.today())
        end_d = st.date_input("End Date (optional)", None)

        if st.button("Generate Report"):
            payload = {
                "scope": scope,
                "company_id": int(company_id),
                "employee_id": int(employee_id) if employee_id else None,
                "period_type": period_type,
                "start_date": str(start_d),
                "end_date": str(end_d) if end_d else None,
            }
            r = api_post("/reports/attendance", headers=auth_headers(), json=payload)
            ok, data = response_json_or_text(r)
            if r and r.status_code == 200 and ok:
                rows = data.get("rows", [])
                df = pd.DataFrame(rows)
                st.dataframe(df, use_container_width=True)
                if not df.empty:
                    excel_download(df, "attendance_report.xlsx")
                if data.get("summary"):
                    st.subheader("Summary")
                    st.json(data["summary"])
            else:
                st.error(data)

# ------------------------------------------------------------------------------
# EMPLOYEE PANEL (QR-scan only)
# ------------------------------------------------------------------------------
elif st.session_state["ui_view"] == "EMPLOYEE":
    st.title("👷 Employee Panel (QR Access Only)")

    # Streamlit's query params API
    params = st.query_params
    company_id = params.get("company_id", [None])[0]
    qr_token = params.get("qr_token", [None])[0]

    if not company_id or not qr_token:
        st.warning("⚠️ Access restricted. Please scan the official company QR code to continue.")
        st.stop()

    st.success(f"Company #{company_id} | token: {qr_token[:8]}...")

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    lat = st.number_input("Latitude", format="%.6f")
    lon = st.number_input("Longitude", format="%.6f")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Check In", type="primary"):
            payload = {
                "company_id": int(company_id),
                "qr_token": qr_token,
                "username": username,
                "password": password,
                "lat": lat,
                "lon": lon
            }
            r = api_post("/attendance/checkin", json=payload)
            ok, data = response_json_or_text(r)
            if r and r.status_code == 200 and ok:
                st.success("✅ Check-In recorded")
                st.json(data)
            else:
                st.error(data)

    with c2:
        if st.button("Check Out", type="primary"):
            payload = {
                "company_id": int(company_id),
                "qr_token": qr_token,
                "username": username,
                "password": password,
                "lat": lat,
                "lon": lon
            }
            r = api_post("/attendance/checkout", json=payload)
            ok, data = response_json_or_text(r)
            if r and r.status_code == 200 and ok:
                st.success("✅ Check-Out recorded")
                st.json(data)
            else:
                st.error(data)

    st.divider()
    st.subheader("📝 Apply for Leave")
    start_d = st.date_input("Start Date", date.today())
    end_d = st.date_input("End Date", date.today())
    reason = st.text_area("Reason (optional)")
    if st.button("Submit Leave"):
        payload = {
            "company_id": int(company_id),
            "qr_token": qr_token,
            "username": username,
            "password": password,
            "start_date": str(start_d),
            "end_date": str(end_d),
            "reason": reason or None
        }
        r = api_post("/leave/apply", json=payload)
        ok, data = response_json_or_text(r)
        if r and r.status_code == 200 and ok:
            st.success("✅ Leave submitted.")
            st.json(data)
        else:
            st.error(data)

# --------------------------------------------------------------------------
# QR DISPLAY (auto-refresh view)
# --------------------------------------------------------------------------
elif st.session_state["ui_view"] == "QR_DISPLAY":
    st.title("🔳 QR Display — Auto Update")

    company_id = st.number_input("Company ID", min_value=1, value=1)
    st.text_input(
        "Frontend Base URL to embed in QR link (e.g., your Streamlit app URL)",
        key="frontend_base_url",
        placeholder="https://your-frontend.streamlit.app"
    )

    st.caption(
        "This screen checks the **current QR version** every 30 seconds. "
        "If HR manually regenerates QR, it updates automatically. "
        "Daily midnight (IST) rotation is handled by the backend."
    )

    # 🔁 Auto-refresh every 30 seconds using Streamlit's built-in helper
    from streamlit_autorefresh import st_autorefresh
    count = st_autorefresh(interval=30 * 1000, key="qr_autorefresh")

    # --- Fetch current QR version ---
    res = api_get(f"/qr/{company_id}/version")
    ok, data = response_json_or_text(res)

    if res and res.status_code == 200 and ok:
        token = data["token"]
        token_date = data["token_date"]
        updated_at = data["updated_at"]

        # Build deep link
        params = {"company_id": company_id, "qr_token": token}
        frontend = st.session_state["frontend_base_url"].rstrip("/") if st.session_state["frontend_base_url"] else ""
        deep_link = f"{frontend}/?{urlencode(params)}" if frontend else f"/?{urlencode(params)}"

        # Draw QR only if token changed
        changed = token != st.session_state.get("qr_last_token")
        if changed:
            st.session_state["qr_last_token"] = token

        img = qrcode.make(deep_link)
        st.image(img, caption=f"Token Date: {token_date}", width=300)
        st.markdown(f"🔗 **Employee Deep Link:** `{deep_link}`")
        st.caption(f"Last backend update: {updated_at}")

    else:
        st.error(data)
