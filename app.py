import os
import re
import json
import base64
import pandas as pd
import streamlit as st
from datetime import datetime 
from io import BytesIO
import plotly.express as px

# ----------------- config -----------------
st.set_page_config(page_title="Vortex ATM Log Analyzer", page_icon="🏧", layout="wide")

CUSTOM_ERROR_FILE = "custom_errors.txt"
ACTION_POINTS_FILE = "action_points.json"
BACKGROUND_FILE = "background.jpg"

DEFAULT_ERRORS = [
    "CASH JAM", "DISPENSER ERROR", "CARD READER ERROR", "COMMUNICATION FAILURE",
    "RECEIPT PRINTER ERROR", "SHUTTER ERROR", "TRANSACTION FAILED", "UNABLE TO DISPENSE",
    "CASSETTE EMPTY", "POWER - POWER", "DISPENSER FAILURE (NO NOTES DISPENSED - 02)", "CASH TAKEN", "NO NOTES DISPENSED", "CASH NOT DISPENSED",
    "SWITCH CONN. FAILURE", "COMMAND REJECT", "PURGE BIN LID CLOSE FAIL",
    "SUSPECT", "MOTOR TIMEOUT", "REJECT BIN FULL", "FAILURE", "SUSPECT",
    "DISPENSED SUCCESSFULLY",
    "MOTOR TIMEOUT",
    "SENSOR FAILURE",
    "MAX TWIN LIMIT EXCEEDED",
    "PRESENTED SUCCESSFULLY",
    "PRESENTATION TRAY NOTHOMED",
    "BULKPURGE SUCCESSFULL",
    "MOTOR TIMEOUT NOTHOMED",
    "DISPENSER INITIALIZED",
    "DISPENSER NOTINITIALIZED",
    "CASH JAM",
    "NO CASH ON TRAY",
    "SUSPECT CASH PICKEDUP",
    "CASH JAM NOTHOMED",
    "CASSETTE NOT ENABLED",
    "NO NOTE LIMIT EXCEEDED",
    "NOTE THRESHOLD NOT SET",
    "DRIFT ERROR",
    "CALIB PROFILE MATCH",
    "CALIBRATION NOT DONE",
    "CALIB THRES SEL FAILED",
    "PROFILE ITER LIMIT EXCEEDED",
    "LESS SAMP ITR EXCEEDED",
    "CASSETTE TYPE SENSOR FAILURE PULLUP PULLDOWN",
    "FLASH ERROR",
    "COUNTING LEFT UPPER DRIFT",
    "COUNTING LEFT LOWER DRIFT",
    "COUNTING RIGHT UPPER DRIFT",
    "COUNTING RIGHT LOWER DRIFT",
    "REAR SKEW LEFT UPPER DRIFT",
    "REAR SKEW LEFT LOWER DRIFT",
    "REAR SKEW RIGHT UPPER DRIFT",
    "REAR SKEW RIGHT LOWER DRIFT",
    "PREV TRANS CASH IN TRAY",
    "DISP INIT SUCCESS PREV CASH BP SUCCESS",
    "DISP INIT FAIL PREV CASH BP SUCCESS",
    "DISP INIT FAIL PREV CASH BP FAIL",
    "PREV TRANS CASH TAKEN",
    "CARTRIDGE HOMED",
    "SAFE SHUTTER OPEN FAILS",
    "SAFE SHUTTER CLOSE FAILS",
    "CARTRIDGE POSITION ERROR",
    "CAM POSITION ERROR",
    "PURGE BIN NOT INSERTED",
    "CASSETTE INSERTION PROBLEM",
    "TWIN SENSOR NOISE UPPER DRIFT",
    "BILL MOVE TO PRESENTER FAIL",
    "PURGEBIN LID CONFIG MIS MATCH",
    "RETRACT CLR BEFORE PRESENT BLK",
    "TOTAL NOTE COUNT OUTOF RANGE",
    "NOTE WIDTH OUTOF RANGE",
    "CART IN SBL APP",
    "CART COMM ERROR",
    "CASSETTE TYPE SENSOR FAILURE COMBINATION",
    "INTER CASSETTE MOVE ERROR",
    "CALIB CASSETTE TYPE MISMATCH",
    "CARTRIDGE FWF UPDATE SUCCESS",
    "CARTRIDGE DATA SENT FAIL",
    "CARTRIDGE DATA RECEV FAIL",
    "CARTRIDGE INVALID DATA RECEIVED",
    "CARTRIDGE DATA SENT SUCCESS",
    "CARTRIDGE CHECKSUM FAIL",
    "MAX PEEP PURGE LIMIT EXCEED",
    "PURGE BIN LID OPEN FAIL",
    "PURGE BIN LID CLOSE FAIL",
    "ACK RX FAIL FOR SHUTTER OPEN",
    "COMMAND VERSION MISMATCH",
    "CCM VER UNSUPPORT MISMATCH",
    "FID A CARD REMOVE TIMEOUT",
    "NOT IN SESSION",
    "PAIRING REQUEST EXHAUSTED",
    "SESSION DETAILS MISMATCH",
    "DOOR OPEN IDENTIFIED",
    "NO CASH ON TRAY - PRESENT BLOCK BEFORE CASH PRESENT",
    "NO CASH ON TRAY - RETRACT CLEAR BEFORE CASH PRESENT",
    "CAM FIND HOME TO",
    "CAM DISPENSE TO CLAMP TO",
    "CAM CLAMP TO PRESENT TO",
    "CAM PRESENT TO BP TO",
    "CAM DISPENSE TO PURGE TO",
    "CAM BP TO DISPENSE TO",
    "CART FIND HOME TO",
    "CART C2P TO (CASSETTE TO PRESENT)",
    "I2C COMM ERROR (DLDR)",
    "LED FAILURE (DLDR)",
    "CART P2BP TO (PRESENT TO BULK PURGE)",
    "REQ NOT REACHED TO CDM",
    "UNKNOWN ERROR"
]

DEFAULT_ACTIONS = {
    "cash jam": "Clean dispenser area and check cassette alignment.",
    "dispenser error": "Check dispenser motor and sensor connections.",
    "unable to dispense": "Verify cash cassette presence and retry transaction.",
    "communication failure": "Check router / network / VPN connection.",
    "card reader error": "Clean or replace card reader module.",
    "receipt printer error": "Refill paper or check printer sensor.",
    "power": "Inspect UPS and power supply connections.",
    "cassette empty": "Refill cassette and confirm cash inventory.",
    "Note Pick time out" :"Physically ensure notes were properly panned and loaded."
}
    
 

# load persisted custom errors
if os.path.exists(CUSTOM_ERROR_FILE):
    with open(CUSTOM_ERROR_FILE, "r", encoding="utf-8") as f:
        CUSTOM_ERRORS = [line.strip() for line in f if line.strip()]
else:
    CUSTOM_ERRORS = []

# load persisted action points
if os.path.exists(ACTION_POINTS_FILE):
    with open(ACTION_POINTS_FILE, "r", encoding="utf-8") as f:
        ACTION_POINTS = json.load(f)
else:
    ACTION_POINTS = DEFAULT_ACTIONS.copy()

ERROR_KEYWORDS = list(dict.fromkeys(DEFAULT_ERRORS + CUSTOM_ERRORS))

# ----------------- helpers -----------------
def add_bg(image_path):
    if not os.path.exists(image_path):
        return
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    st.markdown(
        f"""
        <style>
        .stApp {{
            background-image: url("data:image/jpg;base64,{data}");
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
        }}
        </style>
        """, unsafe_allow_html=True
    )

def extract_date_from_filename(filename):
    m = re.search(r"(\d{4})[_\-](\d{2})[_\-](\d{2})", filename)
    if m:
        try:
            return datetime.strptime(f"{m[1]}-{m[2]}-{m[3]}", "%Y-%m-%d").date()
        except:
            return None
    return None

def get_atm_id(text):
    m = re.search(r'ATMID\s*[:=]\s*([A-Z0-9\-]+)', text, re.I)
    return m.group(1).upper() if m else "UNKNOWN"

def analyze_uploaded_logs(files):
    rows = []
    for f in files:
        raw = f.read().decode("utf-8", errors="ignore")
        fname = f.name
        fdate = extract_date_from_filename(fname) or datetime.now().date()
        dayname = fdate.strftime("%A")
        atm = get_atm_id(raw)
        found_any = False
        for kw in ERROR_KEYWORDS:
            pat = re.compile(re.escape(kw).replace("\\ ", r"[\s\-_]*"), re.IGNORECASE)
            cnt = len(re.findall(pat, raw))
            if cnt:
                rows.append({
                    "Date": fdate,
                    "Day": dayname,
                    "ATM ID": atm,
                    "File Name": fname,
                    "Error Keyword": kw,
                    "Count": cnt
                })
                found_any = True
        if not found_any:
            rows.append({
                "Date": fdate,
                "Day": dayname,
                "ATM ID": atm,
                "File Name": fname,
                "Error Keyword": "No error found",
                "Count": 0
            })
    return pd.DataFrame(rows)

def export_excel_bytes(df):
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Detailed")
        grouped = df.groupby(["Date","Day","ATM ID","Error Keyword"])["Count"].sum().reset_index()
        grouped.to_excel(writer, index=False, sheet_name="Grouped")
    return buf.getvalue()

# ----------------- styling -----------------
add_bg(BACKGROUND_FILE)

st.markdown("""
<style>
html, body, [class*="css"], .stApp { color: white !important; font-family: "Segoe UI", Tahoma, sans-serif !important; }
.main-header { background: linear-gradient(90deg,#0b3861,#2874A6); padding:16px; border-radius:8px; text-align:center; margin-bottom:12px; box-shadow:0 4px 12px rgba(0,0,0,0.25);}
.main-header h2{ margin:0; color:white; font-size:26px; font-weight:800;}
.main-header h5{ margin:6px 0 0; color:#f8f9f9; font-weight:400;}
.stFileUploader { background: white !important; color:#111 !important; border-radius:8px !important; padding:8px !important;}
.stFileUploader [role="button"] { color:#111 !important; font-weight:700 !important;}
.stFileUploader>div>button, .stButton>button { background-color:#0056D2 !important; color:white !important; font-weight:700 !important; border-radius:8px !important; }
.section-title { color:#0b5ed7; font-weight:800; margin-top:18px; margin-bottom:6px; }
.app-footer { text-align:center; color:white; font-weight:600; padding:12px 0; margin-top:20px; }
.stDataFrame table td, .stDataFrame table th { color: #111 !important; }
</style>
""", unsafe_allow_html=True)

# ----------------- header -----------------
st.markdown("""
<div class="main-header">
  <h2>🏧 VORTEX ATM LOG ANALYZER DASHBOARD</h2>
  <h5>Automated ATM EJ Log Processing & Error Summary</h5>
</div>
""", unsafe_allow_html=True)

# ----------------- uploader -----------------
uploaded_files = st.file_uploader("📂 Upload one or more ATM log files (.log / .txt)", type=["log","txt"], accept_multiple_files=True)

# ----------------- main logic -----------------
if uploaded_files:

    st.info(f"Processing {len(uploaded_files)} file(s)...")
    df_results = analyze_uploaded_logs(uploaded_files)

    st.success("Logs analyzed successfully ✅")
    st.dataframe(df_results, use_container_width=True, height=280)

    # ---------------- Top Errors ----------------
    st.markdown('<div class="section-title">📊 Top Error Occurrences</div>', unsafe_allow_html=True)
    top = df_results.groupby("Error Keyword")["Count"].sum().reset_index().sort_values("Count", ascending=False)

    if not top.empty:
        fig = px.bar(top, x="Error Keyword", y="Count", title="Top Error Occurrences")
        fig.update_layout(template="plotly_white")
        st.plotly_chart(fig, use_container_width=True)

    # Add Keyword Section
    st.markdown("<div style='margin-top:8px;'><b style='color:white'>➕ Add Custom Error Keyword</b></div>", unsafe_allow_html=True)
    c1, c2 = st.columns([3,1])
    new_kw = c1.text_input("Enter new keyword (example: 'Cash jam')")

    if c2.button("Add"):
        if new_kw.strip():
            low = [k.lower() for k in ERROR_KEYWORDS]
            if new_kw.lower() in low:
                st.warning("Keyword already exists.")
            else:
                CUSTOM_ERRORS.append(new_kw)
                with open(CUSTOM_ERROR_FILE, "w") as f:
                    for e in CUSTOM_ERRORS:
                        f.write(e + "\n")
                ERROR_KEYWORDS.append(new_kw)
                st.success(f"Added keyword: {new_kw}")
        else:
            st.error("Enter a valid keyword")

    # ---------------- Download Reports ----------------
    st.markdown("""
        <h3 style='color:#0b5ed7; font-weight:800; margin-top:25px;'>📥 Download Reports</h3>
    """, unsafe_allow_html=True)

    st.markdown("""
    <style>
    .stDownloadButton > button {
        background-color: #0056D2 !important;
        color: white !important;
        font-weight: 700 !important;
        border-radius: 8px !important;
        padding: 10px 18px !important;
        border: none !important;
        box-shadow: 0 4px 10px rgba(0,0,0,0.3);
    }
    .stDownloadButton > button:hover {
        background-color: #003C94 !important;
        transform: scale(1.03);
    }
    </style>
    """, unsafe_allow_html=True)

    excel_bytes = export_excel_bytes(df_results)

    st.download_button(
        label="⬇️ Download Excel Report",
        data=excel_bytes,
        file_name="ATM_Log_Analysis.xlsx"
    )

    # ---------------- Grouped Summary ----------------
    st.markdown('<div class="section-title">📅 Grouped Error Summary (ATM-wise + Day-wise)</div>', unsafe_allow_html=True)

    grouped = df_results.groupby(["Date","Day","ATM ID","Error Keyword"])["Count"] \
               .sum().reset_index().sort_values(["Date","ATM ID"])

    expanded_rows = []
    for (date, day, atm), g in grouped.groupby(["Date","Day","ATM ID"]):
        expanded_rows.append({"Date": date, "Day": day, "ATM ID": atm, "Error Keyword": "", "Count": ""})
        for _, r in g.iterrows():
            expanded_rows.append({
                "Date": "",
                "Day": "",
                "ATM ID": "",
                "Error Keyword": r["Error Keyword"],
                "Count": int(r["Count"])
            })

    df_grouped_display = pd.DataFrame(expanded_rows)
    st.dataframe(df_grouped_display, use_container_width=True, height=380)

    # ---------------- Action Points ----------------
    st.markdown('<div class="section-title">🛠 Action & Keywords</div>', unsafe_allow_html=True)

    left, right = st.columns([2,3])

    with left:
        st.write("**Active Keywords:**")
        for kw in ERROR_KEYWORDS:
            st.write("- " + kw)

    with right:
        st.write("**Action Points (editable):**")
        sel = right.selectbox("Choose an error:", sorted(list(ACTION_POINTS.keys())))
        txt = right.text_area("Edit Action Point:", ACTION_POINTS.get(sel, ""))

        if right.button("Save Action"):
            ACTION_POINTS[sel] = txt.strip()
            with open(ACTION_POINTS_FILE, "w") as f:
                json.dump(ACTION_POINTS, f, indent=2)
            st.success("Action saved successfully!")

else:
    st.warning("Please upload one or more EJ log files to start analysis.")

# ---------------- Footer ----------------
st.markdown("""
    <style>
        .app-footer {
            color: black; 
        }
    </style>

    <div class="app-footer">
      © 2025 Vortex ATM Log Analyzer
    </div>
""", unsafe_allow_html=True) 
