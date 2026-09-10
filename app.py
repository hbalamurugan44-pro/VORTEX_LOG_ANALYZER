import os
import re
import json
import base64
import zipfile
import gzip
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st
import plotly.express as px

# ----------------- config -----------------
st.set_page_config(page_title="Vortex ATM Log Analyzer", page_icon="🏧", layout="wide")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CUSTOM_ERROR_FILE = os.path.join(BASE_DIR, "custom_errors.txt")
ACTION_POINTS_FILE = os.path.join(BASE_DIR, "action_points.json")
ERROR_SOLUTIONS_FILE = os.path.join(BASE_DIR, "error_solutions.json")
BACKGROUND_FILE = os.path.join(BASE_DIR, "background.jpg")

DEFAULT_ERRORS = [
    "cash jam", "dispenser error", "card reader error", "communication failure",
    "receipt printer error", "shutter error", "transaction failed", "unable to dispense",
    "cassette empty", "POWER - POWER", "DISPENSER FAILURE (NO NOTES DISPENSED - 02)",
    "cash taken", "no notes dispensed", "cash not dispensed",
    "switch conn. failure", "command reject", "purge bin lid close fail",
    "suspect", "motor timeout", "reject bin full", "failure",
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
    "note pick time out": "Physically ensure notes were properly panned and loaded.",
}


# ----------------- persistence helpers -----------------
def load_lines(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    return []


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default.copy()
    return default.copy()


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# session-backed state so edits persist for the session without re-reading disk each rerun
if "custom_errors" not in st.session_state:
    st.session_state.custom_errors = load_lines(CUSTOM_ERROR_FILE)

if "action_points" not in st.session_state:
    merged = DEFAULT_ACTIONS.copy()
    merged.update(load_json(ERROR_SOLUTIONS_FILE, {}))
    merged.update(load_json(ACTION_POINTS_FILE, {}))
    st.session_state.action_points = merged

ERROR_KEYWORDS = list(dict.fromkeys(DEFAULT_ERRORS + st.session_state.custom_errors))


# ----------------- analysis helpers -----------------
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
        except ValueError:
            return None
    return None


def extract_date_from_content(text):
    m = re.search(r"DATE\s*:\s*(\d{2})/(\d{2})/(\d{2,4})", text, re.I)
    if m:
        day, month, year = m.groups()
        if len(year) == 2:
            year = "20" + year
        try:
            return datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def get_atm_id(text):
    m = re.search(r"ATMID\s*[:=]\s*([A-Z0-9\-]+)", text, re.I)
    return m.group(1).upper() if m else "UNKNOWN"


def find_solution(keyword, action_points):
    key = keyword.lower().strip()
    if key in action_points:
        return action_points[key]
    for k, v in action_points.items():
        if k.lower() in key or key in k.lower():
            return v
    return "No documented fix yet — add one in the Action Points panel."


MAX_LOG_SIZE_BYTES = 8 * 1024 * 1024  # skip any single decompressed log bigger than 8 MB
MAX_ZIP_DEPTH = 4  # how many levels of nested zips to unpack (kernel_log.zip inside the main upload, etc.)


class _ExtractedFile:
    """Mimics Streamlit's UploadedFile interface (.name / .read()) for a file pulled out of a zip."""
    def __init__(self, name, data):
        self.name = name
        self._data = data

    def read(self):
        return self._data


def expand_zip_files(files, _depth=0):
    """Given a mix of plain log/txt uploads, .zip uploads (possibly nested), and .gz-compressed
    logs, return a flat list of log/txt-like file objects ready for analysis."""
    expanded = []
    for f in files:
        name = f.name
        lower = name.lower()
        data = f.read()

        if lower.endswith(".zip"):
            if _depth >= MAX_ZIP_DEPTH:
                st.warning(f"'{name}' is nested too deeply — skipping.")
                continue
            try:
                with zipfile.ZipFile(BytesIO(data)) as zf:
                    inner = [
                        _ExtractedFile(os.path.basename(info.filename), zf.read(info))
                        for info in zf.infolist() if not info.is_dir()
                    ]
                expanded.extend(expand_zip_files(inner, _depth + 1))
            except zipfile.BadZipFile:
                st.error(f"'{name}' doesn't look like a valid zip file — skipping it.")

        elif lower.endswith(".gz"):
            try:
                decompressed = gzip.decompress(data)
            except OSError:
                st.warning(f"Couldn't decompress '{name}' — skipping.")
                continue
            inner_name = name[:-3] if lower.endswith(".gz") else name  # strip trailing .gz
            expanded.extend(expand_zip_files([_ExtractedFile(inner_name, decompressed)], _depth))

        elif lower.endswith(".log") or lower.endswith(".txt"):
            if len(data) > MAX_LOG_SIZE_BYTES:
                st.warning(f"Skipping '{name}' — {len(data) / 1e6:.1f} MB is larger than this app's "
                           f"{MAX_LOG_SIZE_BYTES / 1e6:.0f} MB per-file limit (likely a system/kernel log, "
                           f"not an ATM error log).")
                continue
            expanded.append(_ExtractedFile(name, data))
        # anything else (e.g. tcpdump captures with no useful extension) is silently skipped

    return expanded


def analyze_uploaded_logs(files, keywords, action_points):
    rows = []
    for f in files:
        raw = f.read().decode("utf-8", errors="ignore")
        fname = f.name
        fdate = extract_date_from_content(raw) or extract_date_from_filename(fname) or datetime.now().date()
        dayname = fdate.strftime("%A")
        atm = get_atm_id(raw)
        found_any = False
        for kw in keywords:
            pat = re.compile(re.escape(kw).replace(r"\ ", r"[\s\-_]*"), re.IGNORECASE)
            cnt = len(re.findall(pat, raw))
            if cnt:
                rows.append({
                    "Date": fdate,
                    "Day": dayname,
                    "ATM ID": atm,
                    "File Name": fname,
                    "Error Keyword": kw,
                    "Count": cnt,
                    "Suggested Fix": find_solution(kw, action_points),
                })
                found_any = True
        if not found_any:
            rows.append({
                "Date": fdate,
                "Day": dayname,
                "ATM ID": atm,
                "File Name": fname,
                "Error Keyword": "No error found",
                "Count": 0,
                "Suggested Fix": "-",
            })
    return pd.DataFrame(rows)


def export_excel_bytes(df):
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Detailed Logs")
        grouped = (
            df.groupby(["Date", "Day", "ATM ID", "Error Keyword"])["Count"]
            .sum().reset_index().sort_values(["Date", "ATM ID"])
        )
        grouped.to_excel(writer, index=False, sheet_name="Grouped Summary")
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
.app-footer { text-align:center; color:#111; font-weight:600; padding:12px 0; margin-top:20px; }
.stDataFrame table td, .stDataFrame table th { color: #111 !important; }
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
}
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
uploaded_files = st.file_uploader(
    "📂 Upload ATM EJ log files (.log / .txt) or a .zip containing them",
    type=["log", "txt", "zip"], accept_multiple_files=True
)

# ----------------- main logic -----------------
if uploaded_files:
    log_files = expand_zip_files(uploaded_files)
    if not log_files:
        st.warning("No .log or .txt files were found inside the uploaded zip(s).")
        st.stop()
    st.info(f"Processing {len(log_files)} log file(s)...")
    df_results = analyze_uploaded_logs(log_files, ERROR_KEYWORDS, st.session_state.action_points)

    st.success("Logs analyzed successfully ✅")
    st.dataframe(df_results, use_container_width=True, height=280)

    # ---------------- Top Errors ----------------
    st.markdown('<div class="section-title">📊 Top Error Occurrences</div>', unsafe_allow_html=True)
    top = (
        df_results[df_results["Error Keyword"] != "No error found"]
        .groupby("Error Keyword")["Count"].sum().reset_index()
        .sort_values("Count", ascending=False)
    )

    if not top.empty:
        fig = px.bar(top, x="Error Keyword", y="Count", title="Top Error Occurrences")
        fig.update_layout(template="plotly_white")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.write("No errors detected across the uploaded files. 🎉")

    # ---------------- Add Keyword ----------------
    st.markdown("<div style='margin-top:8px;'><b>➕ Add Custom Error Keyword</b></div>", unsafe_allow_html=True)
    c1, c2 = st.columns([3, 1])
    new_kw = c1.text_input("Enter new keyword (example: 'Cash jam')", label_visibility="collapsed",
                            placeholder="Enter new keyword (example: 'Cash jam')")

    if c2.button("Add"):
        if new_kw.strip():
            low = [k.lower() for k in ERROR_KEYWORDS]
            if new_kw.lower() in low:
                st.warning("Keyword already exists.")
            else:
                st.session_state.custom_errors.append(new_kw)
                with open(CUSTOM_ERROR_FILE, "w", encoding="utf-8") as f:
                    for e in st.session_state.custom_errors:
                        f.write(e + "\n")
                st.success(f"Added keyword: {new_kw}. Re-upload files (or click below) to include it.")
                st.rerun()
        else:
            st.error("Enter a valid keyword")

    # ---------------- Download Reports ----------------
    st.markdown("<h3 class='section-title'>📥 Download Reports</h3>", unsafe_allow_html=True)

    excel_bytes = export_excel_bytes(df_results)
    st.download_button(
        label="⬇️ Download Excel Report",
        data=excel_bytes,
        file_name=f"ATM_Log_Analysis_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
    )

    # ---------------- Grouped Summary ----------------
    st.markdown('<div class="section-title">📅 Grouped Error Summary (ATM-wise + Day-wise)</div>', unsafe_allow_html=True)

    grouped = (
        df_results.groupby(["Date", "Day", "ATM ID", "Error Keyword"])["Count"]
        .sum().reset_index().sort_values(["Date", "ATM ID"])
    )

    expanded_rows = []
    for (date, day, atm), g in grouped.groupby(["Date", "Day", "ATM ID"]):
        expanded_rows.append({"Date": date, "Day": day, "ATM ID": atm, "Error Keyword": "", "Count": ""})
        for _, r in g.iterrows():
            expanded_rows.append({
                "Date": "", "Day": "", "ATM ID": "",
                "Error Keyword": r["Error Keyword"],
                "Count": int(r["Count"]),
            })

    df_grouped_display = pd.DataFrame(expanded_rows)
    st.dataframe(df_grouped_display, use_container_width=True, height=380)

    # ---------------- Action Points ----------------
    st.markdown('<div class="section-title">🛠 Action & Keywords</div>', unsafe_allow_html=True)

    left, right = st.columns([2, 3])

    with left:
        st.write("**Active Keywords:**")
        for kw in ERROR_KEYWORDS:
            st.write("- " + kw)

    with right:
        st.write("**Action Points (editable):**")
        all_keys = sorted(set(list(st.session_state.action_points.keys()) + [k.lower() for k in ERROR_KEYWORDS]))
        sel = right.selectbox("Choose an error:", all_keys)
        txt = right.text_area("Edit Action Point:", st.session_state.action_points.get(sel, ""))

        if right.button("Save Action"):
            st.session_state.action_points[sel] = txt.strip()
            save_json(ACTION_POINTS_FILE, st.session_state.action_points)
            st.success("Action saved successfully!")

else:
    st.warning("Please upload one or more EJ log files to start analysis.")
    st.caption("Try the sample file in `sample_data/EJ_2025_11_14.log` if you just want to test the app.")

# ---------------- Footer ----------------
st.markdown("""
    <div class="app-footer">
      © 2025 Vortex ATM Log Analyzer
    </div>
""", unsafe_allow_html=True)
