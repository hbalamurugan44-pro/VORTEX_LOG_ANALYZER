"""
================================================================================
ZEN VORTEX — ATM Intelligence Dashboard  |  app.py
================================================================================

DATA SOURCES:
  - zen_data.xlsx   → Zen PM Report (MASTER: docket, ATM ID, region, bank, date)
  - crm_data.csv    → QR CRM Live Summary (COMPONENTS: all peripheral details)

QR DATA FIELD REFERENCE (from ADAPT_ACT_QR_v3 splitter):
  Each field comes from the QR scanned at the end of a breakdown call.

  START QR fields (captured when engineer arrives):
    [0]  Date Time
    [1]  Service Id
    [2]  ATM Id
    [3]  ATM Serial No
    [4]  Main App Version       → format: "prev;datetime | current;datetime"
    [5]  Patch Version          → format: "prev;datetime | current;datetime"
    [6]  CDM FW Version         → format: "prev;datetime | current;datetime"
    [7]  CCM FW Version         → format: "prev;datetime | current;datetime"
    [8]  CDM Board Flash ID     → format: "prev;datetime | current;datetime"
    [9]  CCM Board Flash ID     → format: "prev;datetime | current;datetime"
    [10] CAM1 Internal          → format: "CAM1:model|status"
    [11] CAM2 External          → format: "CAM2:model|status"
    [12] CAM3 Cash Side         → format: "CAM3:model|status"
    [13] Card Reader            → format: "CR:model|status"
    [14] EPP / Keypad           → format: "EPP:model|status"
    [15] Motherboard            → format: "MB:model|status"
    [16] Receipt Printer        → format: "RP:model|status"
    [17] Journal Printer        → format: "JP:model|status"
    [18] Fingerprint Scanner    → format: "FP:model|status"
    [19] ASD (Anti Skimming)    → format: "ASD:model|status"
    [20] HDD1 Serial No         → format: "HDD1:serial"
    [21] HDD2 Serial No         → format: "HDD2:serial"
    [22] Error Code Summary 1
    [23] Error Code Summary 2
    [24] Dashboard Summary

  END QR fields (captured after issue is resolved):
    [25] ATM IP
    [26] ATM Gateway
    [27] Switch IP
    [28] Switch Port
    [29] MSP IP 1
    [30] MSP Port 1
    [31] MSP IP 2
    [32] MSP Port 2
    [33] Mandos Port
    [34] Cartridge Type
    [35-38] Cassette Calibration Info (Type 1-4)
    [39] HDD Error Data
    [40] Error Codes
    [41] ASD FW Version         → format: "prev;datetime | current;datetime"
    [42] ASD Calibration Info
    [43] EMV Status             → format: "EMV:1" (1=Enabled, 0=Disabled)
    [44] Host Pairing Info      → format: "CDM_HP:1" (1=Configured, 0=Not)
    [45] Peripheral Serial Numbers
    [46] OS Version | Kernel    → format: "VersionX.Y|kernel_string"
    [47] Agent Version          → format: "prev;datetime | current;datetime"
    [48] List of Private Patches
    [49] Card Reader Read/Error Count
    [50] EPP CMOS Battery Status → 2=OK, 0=Low/Bad
    [51] Bills Dispensation History
    [52] TLS Status             → format: "TLS:2" (2=TLS2, 0=Disabled, 1=TLS1)
    [53] CR Related Failures    (new field)
    [54] Power Failures         (new field)
    [55] NFC Status             (new field)
    [56] QR Reader Status       (new field)
    [57] Cash Low Sensor Status (new field)
    [58] ACT Anti Cash Trapping (new field)

COMPONENT STATUS RULES (for conditional formatting):
  CRM field p_xxx_status values:
    GREEN  → CARD_RD_SUCCESS, KEY_SUCCESS, CAPTURE_SUCCESS, FP_SUCCESS,
             SUCCESS_STATE, Print_success, Working, Matched
    RED    → CAPTURE_FAILED, DEVICE_PORT_ERR, COMM_ERR, COMM_ERROR,
             NOT_CONNECTED, DEV_NOT_CONNECTED, DEVICE_TIMEOUT, Not Matched,
             ICC_ATR_ERR, RESPONSE_ERR, LOW_PAPER, BM_ERROR, LICENSE_ERR,
             NO_DEVICE, METAL_DETECTED, COMM_ERR_STATE, ASD_SENSOR_FAILURE
    GRAY   → Disabled, Disable, NFC_DISABLED, LCS_Disable, ACT_Disable

  emv / hostparing / tls:
    GREEN  → "Enable" / "Configured" / "TLS 2"
    RED    → "Disable" / "Not Configured"

FUTURE API INTEGRATION (Zen Vortex database):
  When connecting to the Zen Vortex API, replace load_data() with:
    zen_df  = fetch_from_api("zen_pm_report", filters={date_range})
    crm_df  = fetch_from_api("qr_crm_live",   filters={atm_id, date})
  All field mappings below will remain the same — only the data source changes.
================================================================================
"""

from flask import Flask, render_template, jsonify, request, send_file
import pandas as pd
import os, functools, io

app = Flask(__name__)
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# ── STATUS CLASSIFICATION ────────────────────────────────────────────────────
# Used for conditional formatting in the UI
# Returns: "ok" (green) | "fail" (red) | "disabled" (gray) | "unknown" (gray)

SUCCESS_VALS = {
    'card_rd_success', 'key_success', 'capture_success', 'fp_success',
    'success_state', 'print_success', 'working', 'matched', 'enable',
    'enabled', 'configured', 'tls 2', 'fp_success  ', 'applied'
}
FAIL_VALS = {
    'capture_failed', 'device_port_err', 'comm_err', 'comm_error',
    'not_connected', 'dev_not_connected', 'device_timeout', 'not matched',
    'icc_atr_err', 'response_err', 'low_paper', 'bm_error', 'license_err',
    'no_device', 'metal_detected', 'comm_err_state', 'asd_sensor_failure',
    'card_timeout', 'no_paper', 'rev_motor_err'
}
DISABLED_VALS = {
    'disabled', 'disable', 'nfc_disabled', 'lcs_disable', 'act_disable',
    'nfc_disabled', 'device_disabled', 'board not replaced'
}

def classify_status(val):
    """
    Classify a CRM status string into: ok | fail | disabled | unknown
    This drives the red/green/gray conditional formatting in the UI.
    """
    if not val or str(val).strip() in ('', '—', 'nan', 'None'):
        return 'unknown'
    v = str(val).strip().lower()
    if v in SUCCESS_VALS:
        return 'ok'
    if v in FAIL_VALS:
        return 'fail'
    if v in DISABLED_VALS:
        return 'disabled'
    # Partial match fallbacks
    if 'success' in v or 'matched' in v or 'working' in v or 'enable' in v:
        return 'ok'
    if 'fail' in v or 'err' in v or 'not matched' in v or 'error' in v:
        return 'fail'
    if 'disab' in v or 'disable' in v:
        return 'disabled'
    return 'unknown'

def safe(val):
    """Return clean string or '—' for missing values."""
    if val is None:
        return '—'
    s = str(val).strip()
    return s if s not in ('', 'nan', 'None', 'NaN') else '—'

def parse_version_field(val):
    """
    Parse version fields that contain prev;datetime | current;datetime format.
    Returns dict: {current, current_date, previous, previous_date}
    """
    s = safe(val)
    if s == '—':
        return {'current': '—', 'current_date': '—', 'previous': '—', 'previous_date': '—'}
    # Try splitting on ' | ' or '|'
    parts = s.split('|')
    result = {'current': s, 'current_date': '—', 'previous': '—', 'previous_date': '—'}
    if len(parts) >= 2:
        # Format: "prev;datetime | current;datetime"
        prev_parts = parts[0].split(';')
        curr_parts = parts[1].split(';') if len(parts) > 1 else []
        if len(prev_parts) >= 1:
            result['previous'] = prev_parts[0].strip()
        if len(prev_parts) >= 2:
            result['previous_date'] = prev_parts[1].strip()
        if len(curr_parts) >= 1:
            result['current'] = curr_parts[0].strip()
        if len(curr_parts) >= 2:
            result['current_date'] = curr_parts[1].strip()
    return result

def parse_status_field(val):
    """
    Parse Matched;version or Not Matched;version format from CRM status columns.
    Returns dict: {label, version, status_class}
    """
    s = safe(val)
    if s == '—':
        return {'label': '—', 'version': '—', 'status_class': 'unknown'}
    parts = s.split(';')
    label = parts[0].strip() if parts else s
    version = parts[1].strip() if len(parts) > 1 else '—'
    return {
        'label': label,
        'version': version,
        'status_class': classify_status(label)
    }

# ── DATA LOADING ─────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def load_data():
    """
    Load Zen (master) and CRM (component details) data.

    FUTURE API: Replace this function body with API calls to Zen Vortex DB:
        zen = api.get('zen_pm_report', params={...})
        crm = api.get('qr_crm_live',   params={...})
    All downstream logic stays the same.
    """
    zen = pd.read_excel(os.path.join(DATA_DIR, 'zen_data.xlsx'))
    zen.rename(columns={'\t\tDate': 'Date'}, inplace=True)
    zen['Docketno'] = zen['Docketno'].str.strip()
    zen['Atmid']    = zen['Atmid'].str.strip()
    zen['Date']     = pd.to_datetime(zen['Date'], errors='coerce')

    crm = pd.read_csv(os.path.join(DATA_DIR, 'crm_data.csv'), low_memory=False)
    crm['docketno'] = crm['docketno'].str.strip()
    crm['status']   = crm['status'].str.strip().str.title()
    crm['calldate'] = pd.to_datetime(crm['calldate'], errors='coerce', format='%Y/%m/%d %H:%M')
    crm['completiondate'] = pd.to_datetime(crm['completiondate'], errors='coerce', format='%m/%d/%Y %H:%M')
    crm['mttr_hours'] = (crm['completiondate'] - crm['calldate']).dt.total_seconds() / 3600

    str_cols = [
        'p_mb_make','p_mb_status','p_cr_make','p_cr_status',
        'p_epp_make','p_epp_status','p_rp_make','p_rp_status',
        'p_cam1_make','p_cam1_status','p_cam2_make','p_cam2_status',
        'p_cam3_make','p_cam3_status','p_fp_make','p_fp_status',
        'p_asd_make','p_asd_status','p_nfc_make','p_nfc_status',
        'p_qrr_make','p_qrr_status','p_lcs_make','p_lcs_status',
        'p_act_make','p_act_status','p_jp_make','p_jp_status',
        'emv','hostparing','tls','cdm_board','ccm_board',
        'mainapp_ver','mainapp_status','agent_ver','agent_status',
        'os_ver','os_status','cdm_ver','cdm_fw_status',
        'ccm_ver','ccm_fw_status','asd_ver','asd_status',
        'patch_ver','patch_status','hdd_primary','hdd_secondary',
    ]
    for col in str_cols:
        if col in crm.columns:
            crm[col] = crm[col].astype(str).str.strip().replace('nan', '')
    return zen, crm


# ── ROUTES ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/search')
def search():
    """
    Main ATM search endpoint.
    Input:  ?atm_id=XXXX
    Source: ATM info from Zen (master). All component/versioning from CRM latest call.

    FUTURE API: swap load_data() with zen_api.lookup(atm_id) + crm_api.latest(atm_id)
    """
    atm_id = request.args.get('atm_id', '').strip().upper()
    if not atm_id:
        return jsonify({'error': 'Please enter an ATM ID'}), 400

    zen, crm = load_data()
    zen_rows = zen[zen['Atmid'].str.upper() == atm_id].sort_values('Date', ascending=False)
    if zen_rows.empty:
        return jsonify({'error': f'ATM "{atm_id}" not found in Zen master data'}), 404

    dockets    = zen_rows['Docketno'].tolist()
    crm_rows   = crm[crm['docketno'].isin(dockets)].sort_values('calldate', ascending=False)
    lc         = crm_rows.iloc[0] if not crm_rows.empty else None  # latest CRM call

    def v(col):
        """Get value from latest CRM row."""
        if lc is None: return '—'
        val = getattr(lc, col, None) if hasattr(lc, col) else lc.get(col, None)
        return safe(val)

    # ── SOFTWARE VERSIONING ──────────────────────────────────────────────────
    # mainapp_status format: "Matched;05.03-SP4-RC1" or "Not Matched;05.03-SP4-RC1"
    # The part before ';' is Matched/Not Matched, after ';' is the target version
    mainapp_status = parse_status_field(v('mainapp_status'))
    agent_status   = parse_status_field(v('agent_status'))
    os_status      = parse_status_field(v('os_status'))

    software_versioning = {
        'main_app':    {'current': v('mainapp_ver'), 'target': mainapp_status['version'],
                        'status': mainapp_status['label'], 'status_class': mainapp_status['status_class']},
        'agent':       {'current': v('agent_ver'),   'target': agent_status['version'],
                        'status': agent_status['label'],  'status_class': agent_status['status_class']},
        'os':          {'current': v('os_ver'),       'target': os_status['version'],
                        'status': os_status['label'],     'status_class': os_status['status_class']},
    }

    # ── PATCH HISTORY ────────────────────────────────────────────────────────
    patch_status = parse_status_field(v('patch_status'))
    patch_history = {
        'latest_patch':    v('patch_ver'),
        'patch_status':    patch_status['label'],
        'patch_status_class': patch_status['status_class'],
        'target_patch':    patch_status['version'],
    }

    # ── NETWORK CONFIGURATION ────────────────────────────────────────────────
    # From CRM (populated from QR End fields 25-32)
    # FUTURE: Directly from Zen Vortex ATM configuration API
    network = {
        'atm_ip':      '—',   # QR field [25] ATM IP
        'gateway':     '—',   # QR field [26] ATM Gateway
        'switch_ip':   '—',   # QR field [27] Switch IP
        'msp_ip_p':    '—',   # QR field [29] MSP IP 1 (Primary)
        'msp_ip_s':    '—',   # QR field [31] MSP IP 2 (Secondary)
        'switch_port': '—',   # QR field [28] Switch Port
        'note': 'Network fields will be populated via Zen Vortex API integration'
    }

    # ── DISPENSER INFO ───────────────────────────────────────────────────────
    # CDM Serial, CCM Board info, HDD serials, cartridge type
    # cdm_board: "Board Not Replaced" or "Board Replaced"
    dispenser = {
        'cdm_board':       v('cdm_board'),
        'cdm_board_class': classify_status(v('cdm_board')),
        'ccm_board':       v('ccm_board'),
        'ccm_board_class': classify_status(v('ccm_board')),
        'hdd_primary':     v('hdd_primary'),
        'hdd_secondary':   v('hdd_secondary'),
        # FUTURE: cartridge, calibration info from QR fields [34-38]
    }

    # ── FIRMWARE MATRIX ──────────────────────────────────────────────────────
    cdm_status = parse_status_field(v('cdm_fw_status'))
    ccm_status = parse_status_field(v('ccm_fw_status'))
    asd_status_p = parse_status_field(v('asd_status'))
    firmware = {
        'cdm_version':    {'current': v('cdm_ver'), 'target': cdm_status['version'],
                           'status': cdm_status['label'], 'status_class': cdm_status['status_class']},
        'ccm_version':    {'current': v('ccm_ver'), 'target': ccm_status['version'],
                           'status': ccm_status['label'], 'status_class': ccm_status['status_class']},
        'asd_firmware':   {'current': v('asd_ver'), 'target': asd_status_p['version'],
                           'status': asd_status_p['label'], 'status_class': asd_status_p['status_class']},
        'os_version':     v('os_ver'),
        'agent_version':  v('agent_ver'),
    }

    # ── SECURITY MATRIX ──────────────────────────────────────────────────────
    # emv: "Enable"/"Disable"
    # hostparing: "Enable"/"Disable"  (CDM Host Pairing)
    # tls: "Enable"/"Disable"  → display as TLS 2 / Disabled
    emv_val  = v('emv')
    tls_val  = v('tls')
    hp_val   = v('hostparing')
    security = {
        'tls':          {'value': 'TLS 2' if tls_val.lower() == 'enable' else tls_val,
                         'status_class': classify_status(tls_val)},
        'emv':          {'value': emv_val, 'status_class': classify_status(emv_val)},
        'host_pairing': {'value': 'Configured' if hp_val.lower() == 'enable' else hp_val,
                         'status_class': classify_status(hp_val)},
        'private_patches': '—',   # QR field [48] List of Private Patches
        # FUTURE: populated from QR field [52] TLS:2 and [44] CDM_HP:1
    }

    # ── COMPONENTS (peripheral status with conditional formatting) ───────────
    # Status classification: ok=green, fail=red, disabled=gray, unknown=gray
    # Model comes from p_xxx_make, status from p_xxx_status
    # Status values documented in SUCCESS_VALS / FAIL_VALS / DISABLED_VALS above
    def comp(make_col, status_col):
        make   = v(make_col)
        status = v(status_col)
        return {'make': make, 'status': status, 'status_class': classify_status(status)}

    components = {
        'motherboard':      comp('p_mb_make',   'p_mb_status'),
        'card_reader':      comp('p_cr_make',   'p_cr_status'),
        'epp':              comp('p_epp_make',  'p_epp_status'),
        'receipt_printer':  comp('p_rp_make',   'p_rp_status'),
        'journal_printer':  comp('p_jp_make',   'p_jp_status'),
        'fingerprint':      comp('p_fp_make',   'p_fp_status'),
        'camera1':          comp('p_cam1_make', 'p_cam1_status'),
        'camera2':          comp('p_cam2_make', 'p_cam2_status'),
        'camera3':          comp('p_cam3_make', 'p_cam3_status'),
        'asd':              comp('p_asd_make',  'p_asd_status'),
        'nfc':              comp('p_nfc_make',  'p_nfc_status'),
        'qr_reader':        comp('p_qrr_make',  'p_qrr_status'),
        'cash_low_sensor':  comp('p_lcs_make',  'p_lcs_status'),
        'act':              comp('p_act_make',  'p_act_status'),
    }

    # ── CALL HISTORY ────────────────────────────────────────────────────────
    history = []
    for _, r in zen_rows.iterrows():
        c  = crm_rows[crm_rows['docketno'] == r['Docketno']]
        cr = c.iloc[0] if not c.empty else None
        mttr = round(float(cr['mttr_hours']), 1) if cr is not None and pd.notna(cr['mttr_hours']) else '—'
        history.append({
            'docketno':  r['Docketno'],
            'date':      str(r['Date'])[:16],
            'status':    safe(cr['status']) if cr is not None else '—',
            'engineer':  safe(cr['engineer']) if cr is not None else '—',
            'mttr':      mttr,
            'completed': str(cr['completiondate'])[:16] if cr is not None and pd.notna(cr['completiondate']) else '—',
        })

    return jsonify({
        # ── ZEN MASTER ──
        'atm_id':       atm_id,
        'location':     safe(zen_rows.iloc[0]['Atm Location']),
        'region':       safe(zen_rows.iloc[0]['Region']),
        'bank':         safe(zen_rows.iloc[0]['Bank']),
        'total_calls':  len(zen_rows),
        'last_call':    str(zen_rows.iloc[0]['Date'])[:16],
        'engineer':     v('engineer'),
        'engineer_mobile': v('engineermobileno'),
        # ── SECTIONS ──
        'software_versioning': software_versioning,
        'patch_history':       patch_history,
        'network':             network,
        'dispenser':           dispenser,
        'firmware':            firmware,
        'security':            security,
        'components':          components,
        'history':             history,
    })


# ── COMPONENT MATRIX ─────────────────────────────────────────────────────────

COMP_COLS = {
    'Motherboard':    'p_mb_make',
    'Card Reader':    'p_cr_make',
    'EPP / Keypad':   'p_epp_make',
    'Receipt Printer':'p_rp_make',
    'Camera':         'p_cam1_make',
    'Fingerprint':    'p_fp_make',
}

@app.route('/api/matrix')
def matrix():
    comp = request.args.get('component', 'Motherboard')
    col  = COMP_COLS.get(comp, 'p_mb_make')
    zen, crm = load_data()
    zen_map = zen[['Docketno','Region']].drop_duplicates('Docketno')
    df = crm.merge(zen_map, left_on='docketno', right_on='Docketno', how='left')
    df = df[df['Region'].notna() & df[col].notna() & (df[col] != '') & (df[col] != 'nan')]
    df[col] = df[col].str.strip()
    pivot  = df.groupby([col,'Region']).size().reset_index(name='count')
    result = pivot.pivot_table(index=col, columns='Region', values='count', fill_value=0).reset_index()
    result.columns.name = None
    for r in ['North','South','East','West']:
        if r not in result.columns:
            result[r] = 0
    result['Total'] = result[['North','South','East','West']].sum(axis=1)
    result = result.sort_values('Total', ascending=False)
    result.rename(columns={col: 'make'}, inplace=True)
    result['component'] = comp
    return jsonify(result.to_dict(orient='records'))

@app.route('/api/components')
def components():
    return jsonify(list(COMP_COLS.keys()))


# ── EXPORT ────────────────────────────────────────────────────────────────────

@app.route('/api/export/atm')
def export_atm():
    """Export single ATM full report to Excel (multiple sheets)."""
    atm_id = request.args.get('atm_id', '').strip().upper()
    zen, crm = load_data()
    zen_rows = zen[zen['Atmid'].str.upper() == atm_id].sort_values('Date', ascending=False)
    if zen_rows.empty:
        return jsonify({'error': 'ATM not found'}), 404

    dockets  = zen_rows['Docketno'].tolist()
    crm_rows = crm[crm['docketno'].isin(dockets)].sort_values('calldate', ascending=False)
    lc       = crm_rows.iloc[0] if not crm_rows.empty else None

    def v(col):
        if lc is None: return '—'
        return safe(getattr(lc, col, None) if hasattr(lc, col) else lc.get(col, None))

    from openpyxl.styles import Font, PatternFill, Alignment
    HDR_FILL = PatternFill('solid', fgColor='1A5FA8')
    HDR_FONT = Font(bold=True, color='FFFFFF', size=11)
    OK_FILL  = PatternFill('solid', fgColor='D4EDDA')
    ERR_FILL = PatternFill('solid', fgColor='FDDEDE')
    DIS_FILL = PatternFill('solid', fgColor='F0EEEA')

    def style_header(ws, widths):
        for cell in ws[1]:
            cell.fill = HDR_FILL; cell.font = HDR_FONT
            cell.alignment = Alignment(horizontal='left')
        for col_letter, w in zip('ABCDEFGHIJ', widths):
            ws.column_dimensions[col_letter].width = w

    def status_fill(status_class):
        if status_class == 'ok':       return OK_FILL
        if status_class == 'fail':     return ERR_FILL
        if status_class == 'disabled': return DIS_FILL
        return None

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:

        # Sheet 1: ATM Summary + all sections
        summary = {
            'Section': [], 'Field': [], 'Value': [], 'Status': []
        }
        def add(section, field, value, status=''):
            summary['Section'].append(section)
            summary['Field'].append(field)
            summary['Value'].append(str(value))
            summary['Status'].append(status)

        # ATM Info (from Zen)
        add('ATM Info (Zen Master)', 'ATM ID',      atm_id)
        add('ATM Info (Zen Master)', 'Location',    zen_rows.iloc[0]['Atm Location'])
        add('ATM Info (Zen Master)', 'Region',      zen_rows.iloc[0]['Region'])
        add('ATM Info (Zen Master)', 'Bank',        zen_rows.iloc[0]['Bank'])
        add('ATM Info (Zen Master)', 'Total Calls', len(zen_rows))
        add('ATM Info (Zen Master)', 'Last Call',   str(zen_rows.iloc[0]['Date'])[:16])
        add('ATM Info (Zen Master)', 'Engineer',    v('engineer'))
        add('ATM Info (Zen Master)', 'Eng. Mobile', v('engineermobileno'))
        # Software
        ms = parse_status_field(v('mainapp_status'))
        add('Software Versioning', 'Main App Current', v('mainapp_ver'), ms['label'])
        add('Software Versioning', 'Main App Target',  ms['version'],   ms['label'])
        add('Software Versioning', 'Agent Version',    v('agent_ver'),  parse_status_field(v('agent_status'))['label'])
        add('Software Versioning', 'OS Version',       v('os_ver'),     parse_status_field(v('os_status'))['label'])
        # Patch
        ps = parse_status_field(v('patch_status'))
        add('Patch History', 'Latest Patch', v('patch_ver'), ps['label'])
        add('Patch History', 'Target Patch', ps['version'],  ps['label'])
        # Firmware
        add('Firmware Matrix', 'CDM Version', v('cdm_ver'), parse_status_field(v('cdm_fw_status'))['label'])
        add('Firmware Matrix', 'CCM Version', v('ccm_ver'), parse_status_field(v('ccm_fw_status'))['label'])
        add('Firmware Matrix', 'ASD FW',      v('asd_ver'), parse_status_field(v('asd_status'))['label'])
        # Security
        add('Security Matrix', 'TLS',          v('tls'),        classify_status(v('tls')))
        add('Security Matrix', 'EMV Status',   v('emv'),        classify_status(v('emv')))
        add('Security Matrix', 'Host Pairing', v('hostparing'), classify_status(v('hostparing')))
        # Dispenser
        add('Dispenser Info', 'CDM Board',     v('cdm_board'),     classify_status(v('cdm_board')))
        add('Dispenser Info', 'CCM Board',     v('ccm_board'),     classify_status(v('ccm_board')))
        add('Dispenser Info', 'HDD Primary',   v('hdd_primary'))
        add('Dispenser Info', 'HDD Secondary', v('hdd_secondary'))

        df_summary = pd.DataFrame(summary)
        df_summary.to_excel(writer, sheet_name='ATM Summary', index=False)
        ws = writer.sheets['ATM Summary']
        style_header(ws, [22, 22, 38, 16])
        STATUS_COL = 4
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            status_val = str(row[STATUS_COL-1].value or '').lower()
            sc = 'ok' if 'match' in status_val and 'not' not in status_val else \
                 'fail' if 'not match' in status_val or 'fail' in status_val or 'err' in status_val else \
                 'disabled' if 'disab' in status_val else ''
            fill = status_fill(sc)
            if fill:
                for cell in row:
                    cell.fill = fill

        # Sheet 2: Components
        comp_data = []
        comp_map = [
            ('Motherboard',    'p_mb_make',   'p_mb_status'),
            ('Card Reader',    'p_cr_make',   'p_cr_status'),
            ('EPP / Keypad',   'p_epp_make',  'p_epp_status'),
            ('Receipt Printer','p_rp_make',   'p_rp_status'),
            ('Journal Printer','p_jp_make',   'p_jp_status'),
            ('Fingerprint',    'p_fp_make',   'p_fp_status'),
            ('Camera 1 (Int)', 'p_cam1_make', 'p_cam1_status'),
            ('Camera 2 (Ext)', 'p_cam2_make', 'p_cam2_status'),
            ('Camera 3 (CS)',  'p_cam3_make', 'p_cam3_status'),
            ('ASD',            'p_asd_make',  'p_asd_status'),
            ('NFC',            'p_nfc_make',  'p_nfc_status'),
            ('QR Reader',      'p_qrr_make',  'p_qrr_status'),
            ('Cash Low Sensor','p_lcs_make',  'p_lcs_status'),
            ('ACT',            'p_act_make',  'p_act_status'),
        ]
        for label, mk, st in comp_map:
            make   = v(mk)
            status = v(st)
            comp_data.append({'Component': label, 'Make / Model': make,
                               'Status': status, 'Condition': classify_status(status).upper()})
        df_comp = pd.DataFrame(comp_data)
        df_comp.to_excel(writer, sheet_name='Components', index=False)
        ws2 = writer.sheets['Components']
        style_header(ws2, [20, 28, 28, 14])
        for row in ws2.iter_rows(min_row=2, max_row=ws2.max_row):
            sc = classify_status(str(row[2].value or ''))
            fill = status_fill(sc)
            if fill:
                for cell in row:
                    cell.fill = fill

        # Sheet 3: Call History
        hist_rows = []
        for _, r in zen_rows.iterrows():
            c  = crm_rows[crm_rows['docketno'] == r['Docketno']]
            cr = c.iloc[0] if not c.empty else None
            mttr = round(float(cr['mttr_hours']),1) if cr is not None and pd.notna(cr['mttr_hours']) else '—'
            hist_rows.append({
                'Docket No':    r['Docketno'],
                'Call Date':    str(r['Date'])[:16],
                'Status':       safe(cr['status']) if cr is not None else '—',
                'Engineer':     safe(cr['engineer']) if cr is not None else '—',
                'MTTR (hours)': mttr,
                'Completed':    str(cr['completiondate'])[:16] if cr is not None and pd.notna(cr['completiondate']) else '—',
            })
        pd.DataFrame(hist_rows).to_excel(writer, sheet_name='Call History', index=False)
        ws3 = writer.sheets['Call History']
        style_header(ws3, [18, 18, 12, 22, 13, 18])

    buf.seek(0)
    return send_file(buf, download_name=f'ATM_{atm_id}_Report.xlsx',
                     as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@app.route('/api/export/matrix')
def export_matrix():
    """Export component matrix (all components × all regions) to Excel."""
    zen, crm = load_data()
    zen_map = zen[['Docketno','Region']].drop_duplicates('Docketno')
    from openpyxl.styles import Font, PatternFill, Alignment
    HDR_FILL = PatternFill('solid', fgColor='1A5FA8')
    HDR_FONT = Font(bold=True, color='FFFFFF', size=11)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        for comp, col in COMP_COLS.items():
            df = crm.merge(zen_map, left_on='docketno', right_on='Docketno', how='left')
            df = df[df['Region'].notna() & df[col].notna() & (df[col] != '') & (df[col] != 'nan')]
            df[col] = df[col].str.strip()
            pivot  = df.groupby([col,'Region']).size().reset_index(name='count')
            result = pivot.pivot_table(index=col, columns='Region', values='count', fill_value=0).reset_index()
            result.columns.name = None
            for r in ['North','South','East','West']:
                if r not in result.columns: result[r] = 0
            result['Total'] = result[['North','South','East','West']].sum(axis=1)
            result = result.sort_values('Total', ascending=False)
            result.rename(columns={col: 'Make / Model'}, inplace=True)
            result = result[['Make / Model','North','South','East','West','Total']]
            safe_name = comp.replace('/', '-').replace('\\', '-').replace('*', '-').replace('?', '-').replace('[', '').replace(']', '')[:31]
            result.to_excel(writer, sheet_name=safe_name, index=False)
            ws = writer.sheets[comp[:31]]
            ws.column_dimensions['A'].width = 32
            for letter in ['B','C','D','E','F']:
                ws.column_dimensions[letter].width = 12
            for cell in ws[1]:
                cell.fill = HDR_FILL; cell.font = HDR_FONT
                cell.alignment = Alignment(horizontal='center')
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    cell.alignment = Alignment(horizontal='center' if cell.column > 1 else 'left')

    buf.seek(0)
    return send_file(buf, download_name='Component_Matrix_Region_Wise.xlsx',
                     as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


if __name__ == '__main__':
    app.run(debug=True, port=5000)
