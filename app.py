from flask import Flask, jsonify, request, render_template_string
import sys
import io
import sqlite3
import ssl
import os
import glob
import shutil
import time
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import folium
import json
import hashlib
import html
import re
from folium import FeatureGroup
from folium.plugins import GroupedLayerControl, MarkerCluster

# ตั้งค่า Encoding สำหรับ Server / Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

app = Flask(__name__)
status_hash_map = {}

# --- 1. ตั้งค่าโปรเจกต์พื้นฐาน (Configuration) ---
CACHE_DIR = os.environ.get('SCADA_CACHE_DIR', '/tmp' if os.path.isdir('/tmp') else '.')
DB_PATH = os.environ.get('SCADA_DB_PATH', os.path.join(CACHE_DIR, 'scada_real.db'))
LOCAL_SEED_DB = 'scada_real.db'
CACHE_HTML_FILE = os.path.join(CACHE_DIR, 'scada_cache.html')
CACHE_META_FILE = os.path.join(CACHE_DIR, 'scada_meta.json')
LOCK_FILE = os.path.join(CACHE_DIR, 'updating.lock')
CACHE_DURATION = 300  # อัปเดตข้อมูลอัตโนมัติทุกๆ 5 นาที

# ตั้งค่า Google Sheets สำหรับดึงข้อมูลลงฐานข้อมูล SQLite (อัปเดตอัตโนมัติจาก Google Sheets / Google Form)
SPREADSHEET_ID = '10QuVWnj2BCPpNqrXpBM8sbARmKGTksQ1fxUYx2Xaa8Q'
SHEET1_GID = '639012274'
DATA_CHECK_GID = '2024067511'
UPDATE_STATE_GID = '0'
FORM_SOURCE_ID = '1Qp-T7x891juPqp8co-dv7bV1pnPeeHFVQtE8JZpAqvg'

SHEET1_CANONICAL_COLS = [
    'รหัสสั่งการ',
    'Site ID',
    'สถานที่',
    'State SCADA',
    'State SCADA\n (หลังตรวจสอบ)',
    'ชนิดอุปกรณ์',
    'รายละเอียดการแก้ไขของ ผอส.ฉ.2',
    'รายละเอียดการเข้าแก้ไข (กรณีอุปกรณ์สื่อสารชำรุด)',
    'LAT, LONG',
    'รอ ผอส. เข้าแก้ไข (ยังไม่ออนไลน์)',
    'รอ ผบอ. เข้าแก้ไข (ยังไม่ออนไลน์)',
    'การไฟฟ้า',
    'วันที่เข้าตรวจสอบ',
    'สถานะการเข้าแก้ไขและออนไลน์ โดย ผบอ.',
    'ซ้ำ',
    'เช็ครหัส',
]

SHEET1_URLS = [
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:json&headers=0&gid={SHEET1_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=0&gid={SHEET1_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={SHEET1_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=1&gid={SHEET1_GID}',
]

DATA_CHECK_URLS = [
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=1&gid={DATA_CHECK_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:json&headers=0&gid={DATA_CHECK_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=0&gid={DATA_CHECK_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={DATA_CHECK_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet=Data_Check',
    f'https://docs.google.com/spreadsheets/d/{FORM_SOURCE_ID}/gviz/tq?tqx=out:csv&sheet=Form3_Responses',
    f'https://docs.google.com/spreadsheets/d/{FORM_SOURCE_ID}/export?format=csv',
]

UPDATE_STATE_URLS = [
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:json&headers=0&gid={UPDATE_STATE_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=0&gid={UPDATE_STATE_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={UPDATE_STATE_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=1&gid={UPDATE_STATE_GID}',
]

# รายชื่อมาตรฐานตาม Google Form (ผู้เข้าแก้ไข ผอส.ฉ.2)
GOOGLE_FORM_WORKERS = [
    "นายณัฐกานต์ ป้อมไชยา",
    "นางปนัดดา ชาติสมร",
    "นายวรรณพงษ์ คลังทอง",
    "นายพนมสินธุ์ ผาสุข",
    "นายประเดิม อิ่มโค่น",
]

# แปลงชื่อย่อหรือชื่อที่ถูกตัดในชีตให้ตรงกับชื่อเต็มมาตรฐาน
KNOWN_WORKER_ALIASES = {
    "ณัฐกานต์": "นายณัฐกานต์ ป้อมไชยา",
    "ปนัดดา": "นางปนัดดา ชาติสมร",
    "วรรณพงษ์": "นายวรรณพงษ์ คลังทอง",
    "ณพงษ์ คลังทอง": "นายวรรณพงษ์ คลังทอง",
    "พนมสินธุ์": "นายพนมสินธุ์ ผาสุข",
    "ประเดิม": "นายประเดิม อิ่มโค่น",
    "คณิน": "นายคณิน วาจนสุนทร",
}

# --- 2. ฟังก์ชันตรวจสอบและจำแนกสถานะ ---
def get_status_config(status_text):
    status_upper = str(status_text).strip().upper()
    if status_upper.startswith('TELEMETRY'):
        return "Telemetry Failure", "gold", "wrench"
    elif status_upper.startswith('CONNECTING'):
        raw_parent = "Connecting"
        if 'ระบบสื่อสาร' in status_upper:
            color = "purple"
        elif 'ผอส.' in status_upper and 'ผบอ.' not in status_upper:
            color = "purple"
        else:
            color = "orange"
        if 'เคยแก้ไข' in status_upper:
            icon = "history"
        elif 'ผบอ.' in status_upper and 'ผอส.' in status_upper:
            icon = "user"
        elif 'ผบอ.' in status_upper or 'ผอส.' in status_upper:
            icon = "check"
        else:
            icon = "wrench"
        return raw_parent, color, icon
    elif status_upper.startswith('OFFLINE'):
        raw_parent = "Offline"
        color = "red"
        if 'เคยแก้ไข' in status_upper:
            icon = "history"
        elif 'ผบอ.' in status_upper and 'ผอส.' in status_upper:
            icon = "user"
        elif 'ผบอ.' in status_upper or 'ผอส.' in status_upper:
            icon = "check"
        else:
            icon = "times"
        return raw_parent, color, icon
    elif status_upper.startswith('ONLINE'):
        raw_parent = "Online"
        color = "brightgreen"
        icon = "history" if 'เคยแก้ไข' in status_upper else "check"
        return raw_parent, color, icon
    elif status_upper.startswith('INITIALIZING'):
        raw_parent = "Initializing"
        color = "lightgreen"
        if 'เคยแก้ไข' in status_upper:
            icon = "history"
        elif 'ผบอ.' in status_upper or 'ผอส.' in status_upper:
            icon = "check"
        else:
            icon = "wrench"
        return raw_parent, color, icon
    else:
        return "สถานะอื่นๆ", "gray", "info-circle"

def get_hash(text):
    if text not in status_hash_map:
        status_hash_map[text] = hashlib.md5(text.encode('utf-8')).hexdigest()[:8]
    return status_hash_map[text]

def get_actual_col_name(df_columns, keywords, exclude=None):
    for col in df_columns:
        if all(kw.lower() in str(col).lower() for kw in keywords):
            if exclude and any(ex.lower() in str(col).lower() for ex in exclude):
                continue
            return col
    return None

def parse_thai_date(date_str):
    if not date_str or pd.isna(date_str):
        return ""
    date_str = str(date_str).strip()
    if date_str.lower() in ['nan', 'none', '-', '', 'ไม่มีค่า']:
        return ""
    if re.match(r'^\d{4}-\d{2}-\d{2}', date_str):
        return date_str[:10]

    thai_months = {
        "ม.ค.": "01", "ก.พ.": "02", "มี.ค.": "03", "เม.ย.": "04", "พ.ค.": "05", "มิ.ย.": "06",
        "ก.ค.": "07", "ส.ค.": "08", "ก.ย.": "09", "ต.ค.": "10", "พ.ย.": "11", "ธ.ค.": "12",
        "มกราคม": "01", "กุมภาพันธ์": "02", "มีนาคม": "03", "เมษายน": "04", "พฤษภาคม": "05", "มิถุนายน": "06",
        "กรกฎาคม": "07", "สิงหาคม": "08", "กันยายน": "09", "ตุลาคม": "10", "พฤศจิกายน": "11", "ธันวาคม": "12"
    }

    match = re.search(r'(\d+)\s*([ก-๙\.]+)\s*(\d+)', date_str)
    if match:
        day, month_th, year_th = match.groups()
        month = thai_months.get(month_th, "01")
        year = int(year_th)
        if year < 100:
            year = (year + 2000 - 43) if year >= 60 else (year + 2000)
        elif year > 2400:
            year -= 543
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"

    # รองรับรูปแบบ Timestamp เช่น 30/9/2026, 19:58:06 หรือ 14/10/2026
    slash_match = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{2,4})', date_str)
    if slash_match:
        d_str, m_str, y_str = slash_match.groups()
        y_val = int(y_str)
        if y_val < 100:
            y_val = (y_val + 2000 - 43) if y_val >= 60 else (y_val + 2000)
        elif y_val > 2400:
            y_val -= 543
        return f"{y_val}-{m_str.zfill(2)}-{d_str.zfill(2)}"

    try:
        dt = pd.to_datetime(date_str, dayfirst=True)
        if pd.notna(dt):
            y_val = dt.year - 543 if dt.year > 2400 else dt.year
            return f"{y_val:04d}-{dt.month:02d}-{dt.day:02d}"
    except Exception:
        pass
    return ""

def normalize_single_worker(name):
    n = re.sub(r'\s+', ' ', str(name or '').strip())
    if not n or n.lower() in ['nan', 'none', '-', 'ไม่มีค่า']:
        return ""
    for alias_key, full_name in KNOWN_WORKER_ALIASES.items():
        if alias_key in n:
            return full_name
    return n

def clean_worker_names(workers_str):
    if not workers_str or pd.isna(workers_str):
        return ""
    raw = str(workers_str).strip()
    if raw.lower() in ["nan", "none", "-", "ไม่มีค่า", ""]:
        return ""
    cleaned = re.sub(r'[\n\r;|]+', ',', raw)
    names = []
    for token in cleaned.split(','):
        norm_name = normalize_single_worker(token)
        if norm_name and norm_name not in names:
            names.append(norm_name)
    return ", ".join(names)

def normalize_station_key(val):
    if not val or pd.isna(val):
        return ""
    s = str(val).strip().upper()
    return "" if s in ["NAN", "NONE", "-", "ไม่มีค่า", "UNKNOWN"] else s

def _s(v):
    if v is None or pd.isna(v):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ["nan", "none", "nat"] else s

def _build_url_opener():
    ssl_ctx = ssl._create_unverified_context()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(),
        urllib.request.HTTPSHandler(context=ssl_ctx),
    )

def parse_gviz_json_bytes(raw_bytes):
    text = raw_bytes.decode('utf-8', errors='replace')
    m = re.search(r'setResponse\((.*)\)\s*;?\s*$', text, re.DOTALL)
    if not m:
        return None
    payload = json.loads(m.group(1))
    table = payload.get('table', {})
    cols = [c.get('label', '') or '' for c in table.get('cols', [])]
    rows_data = []
    for r in table.get('rows', []):
        cells = r.get('c', []) or []
        row_vals = []
        for idx in range(len(cols)):
            cell = cells[idx] if idx < len(cells) else None
            if not cell:
                row_vals.append('')
            else:
                val = cell.get('f') if cell.get('f') is not None else cell.get('v')
                if val is None:
                    row_vals.append('')
                else:
                    s_val = str(val)
                    dm = re.match(r'^Date\((\d+),(\d+),(\d+)', s_val)
                    if dm:
                        y, m_zero, d = int(dm.group(1)), int(dm.group(2)) + 1, int(dm.group(3))
                        s_val = f'{y:04d}-{m_zero:02d}-{d:02d}'
                    row_vals.append(s_val)
        rows_data.append(row_vals)
    if any(c.strip() for c in cols):
        return pd.DataFrame(rows_data, columns=cols)
    return pd.DataFrame(rows_data)

def normalize_sheet1_dataframe(df_raw):
    if df_raw is None or len(df_raw) == 0:
        return None

    cols_lower = [str(c).strip().lower() for c in df_raw.columns]
    has_site = any('site id' in c for c in cols_lower)
    has_lat = any('lat' in c and 'long' in c for c in cols_lower)
    if has_site and has_lat:
        site_c = next(c for c in df_raw.columns if 'site id' in str(c).lower())
        if 'ne2-' not in str(site_c).lower():
            return df_raw

    for idx in range(min(5, len(df_raw))):
        row_vals = [_s(v) for v in df_raw.iloc[idx].values]
        row_lower = [v.lower() for v in row_vals]
        if any('site id' in v for v in row_lower) and not any('ne2-' in v for v in row_lower):
            new_cols = []
            for col_i, val in enumerate(row_vals):
                if val and not val.lower().startswith('unnamed'):
                    new_cols.append(val)
                elif col_i < len(SHEET1_CANONICAL_COLS):
                    new_cols.append(SHEET1_CANONICAL_COLS[col_i])
                else:
                    new_cols.append(f'col_{col_i}')
            df_clean = df_raw.iloc[idx + 1:].copy().reset_index(drop=True)
            df_clean.columns = new_cols
            return df_clean

    cand_headers = [_s(c) for c in df_raw.columns]
    data_start_idx = 0
    if not any('site id' in c.lower() for c in cand_headers) and len(df_raw) > 0:
        row0 = [_s(v) for v in df_raw.iloc[0].values]
        if any('site id' in v.lower() for v in row0):
            cand_headers = row0
            data_start_idx = 1

    if any('site id' in c.lower() for c in cand_headers):
        site_col_idx = next((i for i, c in enumerate(cand_headers) if 'site id' in c.lower()), None)
        merged_sites = re.findall(r'NE2-[A-Z0-9]+-\d+', cand_headers[site_col_idx]) if site_col_idx is not None else []
        n_merged = len(merged_sites)

        new_cols = [
            SHEET1_CANONICAL_COLS[col_i] if col_i < len(SHEET1_CANONICAL_COLS) else c_str
            for col_i, c_str in enumerate(cand_headers)
        ]

        df_body = df_raw.iloc[data_start_idx:].copy().reset_index(drop=True)
        df_body.columns = new_cols

        if n_merged > 0:
            cmd_tokens = re.findall(r'[A-Z0-9]{4,}-[A-Z0-9-]+', cand_headers[0]) if len(cand_headers) > 0 else []
            lat_tokens = re.findall(r'\d{1,2}\.\d+\s*,\s*\d{2,3}\.\d+', cand_headers[8]) if len(cand_headers) > 8 else []
            state_tokens = re.findall(r'(?:Connecting|Online|Offline|Initializing|Telemetry Failure)', cand_headers[3]) if len(cand_headers) > 3 else []
            loc_str = re.sub(r'^สถานที่\s*', '', cand_headers[2]).strip() if len(cand_headers) > 2 else ''
            loc_tokens = loc_str.split() if loc_str else []

            recovered_rows = []
            for m_i in range(n_merged):
                r_dict = {c: '' for c in new_cols}
                r_dict['Site ID'] = merged_sites[m_i]
                if m_i < len(cmd_tokens):
                    r_dict['รหัสสั่งการ'] = cmd_tokens[m_i]
                if m_i < len(lat_tokens):
                    r_dict['LAT, LONG'] = lat_tokens[m_i]
                if m_i < len(state_tokens):
                    r_dict['State SCADA'] = state_tokens[m_i]
                    r_dict['State SCADA\n (หลังตรวจสอบ)'] = state_tokens[m_i]
                if m_i < len(loc_tokens):
                    r_dict['สถานที่'] = loc_tokens[m_i]
                recovered_rows.append(r_dict)
            df_rec = pd.DataFrame(recovered_rows, columns=new_cols)
            df_body = pd.concat([df_rec, df_body], ignore_index=True)

        return df_body

    return None

def normalize_generic_dataframe(df_raw, header_keywords):
    if df_raw is None or len(df_raw) == 0:
        return None
    cols_lower = [str(c).strip().lower() for c in df_raw.columns]
    if any(any(kw in c for kw in header_keywords) for c in cols_lower) and not all(c.startswith('unnamed') for c in cols_lower):
        return df_raw

    for idx in range(min(5, len(df_raw))):
        row_vals = [_s(v) for v in df_raw.iloc[idx].values]
        row_lower = [v.lower() for v in row_vals]
        if any(any(kw in v for kw in header_keywords) for v in row_lower):
            new_cols = [val if val else f'col_{i}' for i, val in enumerate(row_vals)]
            df_clean = df_raw.iloc[idx + 1:].copy().reset_index(drop=True)
            df_clean.columns = new_cols
            return df_clean
    return df_raw

def _is_valid_sheet1_df(df_cand):
    if df_cand is None or len(df_cand) < 100:
        return False
    lat_col = get_actual_col_name(df_cand.columns, ["lat", "long"])
    site_col = get_actual_col_name(df_cand.columns, ["site id"])
    if not lat_col or not site_col:
        return False
    valid_coords = df_cand[lat_col].dropna().astype(str).str.contains(',')
    return int(valid_coords.sum()) >= 100

def _is_valid_update_state_df(df_cand):
    if df_cand is None or len(df_cand) < 100:
        return False
    return bool(get_actual_col_name(df_cand.columns, ["site id"]) and get_actual_col_name(df_cand.columns, ["state scada"]))

def _is_valid_data_check_df(df_cand):
    if df_cand is None or len(df_cand) == 0:
        return False
    col_check = (
        get_actual_col_name(df_cand.columns, ["ผู้เข้าแก้ไข"])
        or get_actual_col_name(df_cand.columns, ["ผู้ปฏิบัติงาน"])
        or get_actual_col_name(df_cand.columns, ["workers"])
    )
    site_check = (
        get_actual_col_name(df_cand.columns, ["site id"])
        or get_actual_col_name(df_cand.columns, ["equipment_id"])
        or get_actual_col_name(df_cand.columns, ["site"])
    )
    return bool(col_check and site_check)

def fetch_remote_dataframe(urls, validator_fn, normalizer_fn=None, sheet_name=None):
    opener = _build_url_opener()
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with opener.open(req, timeout=25) as resp:
                raw_bytes = resp.read()
            if b'setResponse(' in raw_bytes[:300] or 'tqx=out:json' in url:
                df_raw = parse_gviz_json_bytes(raw_bytes)
            else:
                df_raw = pd.read_csv(io.BytesIO(raw_bytes), header=None, dtype=str)
            df_cand = normalizer_fn(df_raw) if normalizer_fn else df_raw
            if validator_fn(df_cand):
                return df_cand
            df_std = pd.read_csv(io.BytesIO(raw_bytes), dtype=str)
            df_std = normalizer_fn(df_std) if normalizer_fn else df_std
            if validator_fn(df_std):
                return df_std
        except Exception:
            continue

    if sheet_name:
        try:
            import gspread
            json_files = glob.glob('*.json') + glob.glob('*/*.json') + glob.glob('../*.json')
            for jf in json_files:
                try:
                    with open(jf, 'r', encoding='utf-8') as f:
                        jdata = json.load(f)
                    if jdata.get('type') == 'service_account':
                        gc = gspread.service_account(filename=jf)
                        sh = gc.open_by_key(SPREADSHEET_ID)
                        ws = sh.worksheet(sheet_name)
                        records = ws.get_all_values()
                        if len(records) > 1:
                            df_cand = pd.DataFrame(records[1:], columns=records[0])
                            if validator_fn(df_cand):
                                return df_cand
                except Exception:
                    continue
        except Exception:
            pass
    return None

def enrich_sheet1_with_update_and_check(df_s1, df_up, df_chk):
    if df_s1 is None or len(df_s1) == 0:
        return df_s1

    cols = list(df_s1.columns)
    site_col = get_actual_col_name(cols, ["site id"]) or 'Site ID'
    cmd_col = get_actual_col_name(cols, ["รหัสสั่งการ"]) or 'รหัสสั่งการ'
    loc_col = get_actual_col_name(cols, ["สถานที่"]) or 'สถานที่'
    state_col = get_actual_col_name(cols, ["state scada"], exclude=["หลัง"]) or 'State SCADA'
    after_col = get_actual_col_name(cols, ["state scada", "หลัง"]) or 'State SCADA\n (หลังตรวจสอบ)'
    type_col = get_actual_col_name(cols, ["ชนิดอุปกรณ์"]) or 'ชนิดอุปกรณ์'
    pea_col = get_actual_col_name(cols, ["การไฟฟ้า"]) or 'การไฟฟ้า'
    date_col = get_actual_col_name(cols, ["วันที่เข้าตรวจสอบ"]) or 'วันที่เข้าตรวจสอบ'
    col_g_name = get_actual_col_name(cols, ["รายละเอียด", "การแก้ไข"], exclude=["เข้า"]) or 'รายละเอียดการแก้ไขของ ผอส.ฉ.2'
    col_h_name = get_actual_col_name(cols, ["รายละเอียด", "การเข้าแก้ไข"]) or 'รายละเอียดการเข้าแก้ไข (กรณีอุปกรณ์สื่อสารชำรุด)'
    col_j_name = get_actual_col_name(cols, ["รอ ผอส"]) or 'รอ ผอส. เข้าแก้ไข (ยังไม่ออนไลน์)'
    col_k_name = get_actual_col_name(cols, ["รอ ผบอ"]) or 'รอ ผบอ. เข้าแก้ไข (ยังไม่ออนไลน์)'
    col_n_name = get_actual_col_name(cols, ["โดย ผบอ"]) or 'สถานะการเข้าแก้ไขและออนไลน์ โดย ผบอ.'

    re_online_comm = re.compile(r'✅|ไม่มีอุปกรณ์สื่อสารชำรุด')
    re_strip_marks = re.compile(r'[✅❌]')
    re_good_clause = re.compile(r'✅[^❌]*')

    up_map = {}
    if df_up is not None and len(df_up) > 0:
        up_site_col = get_actual_col_name(df_up.columns, ["site id"]) or df_up.columns[0]
        for r in df_up.to_dict('records'):
            sid = _s(r.get(up_site_col)).upper()
            if sid:
                up_map[sid] = r

    chk_map = {}
    if df_chk is not None and len(df_chk) > 0:
        chk_cols = list(df_chk.columns)
        chk_site_col = get_actual_col_name(chk_cols, ["site id"]) or (chk_cols[2] if len(chk_cols) > 2 else chk_cols[0])
        sid_idx = chk_cols.index(chk_site_col)
        for row_vals in df_chk.itertuples(index=False, name=None):
            sid = _s(row_vals[sid_idx] if sid_idx < len(row_vals) else '').upper()
            if sid:
                chk_map[sid] = row_vals

    records = df_s1.to_dict('records')
    for row in records:
        sid = _s(row.get(site_col)).upper()
        if not sid:
            continue

        if sid in up_map:
            ur = up_map[sid]
            u_state = _s(ur.get('State SCADA'))
            if u_state:
                row[state_col] = u_state
            if not _s(row.get(cmd_col)):
                u_cmd = _s(ur.get('รหัสสั่งการ')).replace('-OLTC', '')
                if u_cmd:
                    row[cmd_col] = u_cmd
            if not _s(row.get(type_col)) and _s(ur.get('ชนิดอุปกรณ์')):
                row[type_col] = _s(ur.get('ชนิดอุปกรณ์'))
            if not _s(row.get(pea_col)) and _s(ur.get('การไฟฟ้า')):
                row[pea_col] = _s(ur.get('การไฟฟ้า'))
            if not _s(row.get(loc_col)) and _s(ur.get('สถานที่')):
                row[loc_col] = _s(ur.get('สถานที่'))

        if sid in chk_map:
            cr = chk_map[sid]
            raw_d = _s(cr[3] if len(cr) > 3 else '')
            raw_f = _s(cr[5] if len(cr) > 5 else '')
            raw_g = _s(cr[6] if len(cr) > 6 else '')
            raw_i = _s(cr[8] if len(cr) > 8 else '')
            raw_j = _s(cr[9] if len(cr) > 9 else '')
            raw_k = _s(cr[10] if len(cr) > 10 else '')
            raw_l = _s(cr[11] if len(cr) > 11 else '')
            raw_p = _s(cr[15] if len(cr) > 15 else '')
            raw_q = _s(cr[16] if len(cr) > 16 else '')

            pos_g = f'{raw_i} {raw_j}'.strip()
            val_g = '' if not pos_g else ('✅ ระบบสื่อสาร ONLINE' if re_online_comm.search(pos_g) else re_strip_marks.sub('', pos_g).strip())

            pos_h = ' '.join(x for x in [raw_f, raw_g, raw_j, raw_l] if x).strip()
            if not pos_h:
                val_h = ''
            else:
                bad_h = re_good_clause.sub('', pos_h)
                clean_bad_h = ', '.join(p.strip() for p in bad_h.split('❌') if p.strip())
                val_h = clean_bad_h if clean_bad_h else ('✅ ระบบสื่อสาร ONLINE' if re_online_comm.search(pos_h) else re_strip_marks.sub('', pos_h).strip())

            pos_j = ' '.join(x for x in [raw_p, raw_j] if x).strip()
            if not pos_j:
                val_j = ''
            elif re_online_comm.search(pos_j):
                val_j = '✅ ระบบสื่อสาร ONLINE'
            else:
                bad_j = re_good_clause.sub('', pos_j)
                joined_j = ', ❌ '.join(p.strip() for p in bad_j.split('❌') if p.strip())
                val_j = '' if not joined_j else (joined_j if joined_j.startswith('❌') else '❌ ' + joined_j)

            pbo_k = ' '.join(x for x in [raw_k, raw_q] if x).strip()
            if not pbo_k:
                val_k = ''
            else:
                bad_k = re_good_clause.sub('', pbo_k)
                joined_k = '\n❌ '.join(p.strip() for p in bad_k.split('❌') if p.strip())
                val_k = '' if not joined_k else (joined_k if joined_k.startswith('❌') else '❌ ' + joined_k)

            if val_g:
                row[col_g_name] = val_g
            if val_h:
                row[col_h_name] = val_h
            if val_j:
                row[col_j_name] = val_j
            if val_k:
                row[col_k_name] = val_k
            if raw_d:
                row[date_col] = raw_d

        d_val = _s(row.get(state_col))
        j_val = _s(row.get(col_j_name))
        k_val = _s(row.get(col_k_name))
        n_val = _s(row.get(col_n_name))

        j_is_online = (j_val == '✅ ระบบสื่อสาร ONLINE')
        k_is_normal = (k_val == '' or k_val == '✅อุปกรณ์ FRTU ปกติ')
        j_need = (j_val != '' and not j_is_online)
        k_need = (k_val != '' and k_val != '✅อุปกรณ์ FRTU ปกติ')

        if not d_val:
            e_val = ''
        elif d_val == 'Online':
            e_val = 'Online'
        elif n_val and d_val != 'Online':
            e_val = f'{d_val} ผบอ. เคยแก้ไขแล้วกลับมา Offline'
        elif j_need and k_need:
            e_val = f'{d_val} รอ ผบอ. และ ผอส. เข้าแก้ไข'
        elif j_need:
            e_val = f'{d_val} รอ ผอส. เข้าแก้ไข'
        elif k_need:
            e_val = f'{d_val} รอ ผบอ. เข้าแก้ไข'
        elif j_is_online and k_is_normal:
            e_val = f'{d_val} ระบบสื่อสารเคยแก้ไขแล้ว กลับมา Offline'
        else:
            e_val = d_val

        if e_val:
            row[after_col] = e_val

    return pd.DataFrame(records, columns=cols)

def _is_db_recently_updated(db_path, max_age_sec=180):
    try:
        if not os.path.exists(db_path):
            return False
        import time as _time
        return (_time.time() - os.path.getmtime(db_path)) <= max_age_sec
    except Exception:
        return False

def load_update_state_df(conn, prefer_local=False):
    if prefer_local:
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='update_state'")
            if cur.fetchone():
                df_local = pd.read_sql("SELECT * FROM update_state", conn)
                if _is_valid_update_state_df(df_local):
                    return df_local
        except Exception:
            pass

    df_remote = fetch_remote_dataframe(
        UPDATE_STATE_URLS,
        _is_valid_update_state_df,
        normalizer_fn=lambda d: normalize_generic_dataframe(d, ['site id', 'state scada']),
        sheet_name='Update State',
    )
    if df_remote is not None:
        print(f"[OK] ดึงข้อมูล Update State (gid=0) สำเร็จ! พบ {len(df_remote)} แถว")
        try:
            df_remote.to_sql('update_state', conn, if_exists='replace', index=False)
        except Exception:
            pass
        return df_remote

    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='update_state'")
        if cur.fetchone():
            df_local = pd.read_sql("SELECT * FROM update_state", conn)
            if _is_valid_update_state_df(df_local):
                return df_local
    except Exception:
        pass
    return None

def load_data_check_df(conn, prefer_local=False):
    if prefer_local:
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='data_check'")
            if cur.fetchone():
                df_check = pd.read_sql("SELECT * FROM data_check", conn)
                if _is_valid_data_check_df(df_check):
                    print(f"[OK] โหลดข้อมูลจากตาราง data_check ในฐานข้อมูล SQLite สำเร็จ! พบ {len(df_check)} แถว")
                    return df_check
        except Exception:
            pass

    df_remote = fetch_remote_dataframe(
        DATA_CHECK_URLS,
        _is_valid_data_check_df,
        normalizer_fn=lambda d: normalize_generic_dataframe(d, ['site id', 'equipment_id', 'ผู้เข้าแก้ไข']),
        sheet_name='Data_Check',
    )
    if df_remote is not None:
        print(f"[OK] ดึงข้อมูลจาก Google Sheets (Data_Check) สำเร็จ! พบ {len(df_remote)} แถว")
        try:
            df_remote.to_sql('data_check', conn, if_exists='replace', index=False)
        except Exception:
            pass
        return df_remote

    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='data_check'")
        if cur.fetchone():
            df_check = pd.read_sql("SELECT * FROM data_check", conn)
            if _is_valid_data_check_df(df_check):
                print(f"[OK] โหลดข้อมูลจากตาราง data_check ในฐานข้อมูล SQLite สำเร็จ! พบ {len(df_check)} แถว")
                return df_check
    except Exception:
        pass

    try:
        df_fb = build_data_check_fallback_dataframe()
        if _is_valid_data_check_df(df_fb):
            print(f"[OK] กู้คืนข้อมูล Data_Check สำรองสำเร็จ! พบ {len(df_fb)} แถว")
            try:
                df_fb.to_sql('data_check', conn, if_exists='replace', index=False)
            except Exception:
                pass
            return df_fb
    except Exception:
        pass

    print("คำเตือน: ไม่สามารถดึงข้อมูล Data_Check ได้ จะใช้ข้อมูลเท่าที่มีใน ชีต1")
    return None


_GIS_MASTER_B64 = "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"
_DATA_CHECK_B64 = "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"

def build_gis_base_dataframe():
    """สร้างตารางโครงสร้างพิกัด GIS พื้นฐาน (เฉพาะพิกัดอุปกรณ์ในระบบ) เพื่อใช้ผสานกับข้อมูลสดจาก Update State (gid=0) และ Data_Check (gid=2024067511) กรณีตาราง scada_stations เดิมไม่มีคอลัมน์ LAT, LONG"""
    import zlib, base64
    rows = json.loads(zlib.decompress(base64.b64decode(_GIS_MASTER_B64)).decode('utf-8'))
    return pd.DataFrame(rows, columns=SHEET1_CANONICAL_COLS)

def build_data_check_fallback_dataframe():
    """สร้างตาราง Data_Check สำรองในกรณีที่เครือข่ายไม่สามารถเชื่อมต่อ Google Sheets ได้ชั่วคราว"""
    import zlib, base64
    rows = json.loads(zlib.decompress(base64.b64decode(_DATA_CHECK_B64)).decode('utf-8'))
    cols = ['Timestamp', 'Equipment_ID', 'Site ID', 'วันที่เข้าตรวจสอบ', 'ผู้เข้าแก้ไข (ผอส.ฉ.2)', 'รายละเอียดการแก้ไข']
    return pd.DataFrame(rows, columns=cols)

def load_sheet1_df(conn, df_update=None, df_check=None, prefer_local=False):
    """โหลดข้อมูลอุปกรณ์ในระบบจาก Google Sheets (ชีต1) หรือจากฐานข้อมูล SQLite แล้วผสานสถานะล่าสุดจาก Update State (gid=0) และ Data_Check (gid=2024067511)"""
    df_local = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scada_stations'")
        if cur.fetchone():
            df_local = pd.read_sql("SELECT * FROM scada_stations", conn)
    except Exception:
        df_local = None

    if prefer_local and _is_valid_sheet1_df(df_local):
        return df_local

    df_base = None
    if _is_valid_sheet1_df(df_local):
        df_base = df_local
    else:
        print("กำลังดึงข้อมูลอุปกรณ์ในระบบและพิกัด (ชีต1) จาก Google Sheets เข้าสู่ฐานข้อมูล...")
        df_remote = fetch_remote_dataframe(
            SHEET1_URLS,
            _is_valid_sheet1_df,
            normalizer_fn=normalize_sheet1_dataframe,
            sheet_name='ชีต1',
        )
        if df_remote is not None:
            print(f"[OK] ดึงข้อมูล ชีต1 จาก Google Sheets สำเร็จ! พบ {len(df_remote)} อุปกรณ์")
            df_base = df_remote
        else:
            df_base = build_gis_base_dataframe()
            print(f"[OK] กู้คืนโครงสร้างพิกัดอุปกรณ์ในระบบ (GIS Master) สำเร็จ: {len(df_base)} อุปกรณ์")

    df_enriched = enrich_sheet1_with_update_and_check(df_base, df_update, df_check)
    try:
        df_enriched.to_sql('scada_stations', conn, if_exists='replace', index=False)
    except Exception:
        pass
    return df_enriched


def get_meta():
    try:
        with open(CACHE_META_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'version': 0, 'last_update': 0}

def _ensure_seed_db():
    """หากในระบบยังไม่มีไฟล์ฐานข้อมูลใน /tmp แต่มีไฟล์ scada_real.db ในโฟลเดอร์โปรเจกต์ ให้คัดลอกมาเป็นฐานข้อมูลตั้งต้น"""
    try:
        if not os.path.exists(DB_PATH) and os.path.exists(LOCAL_SEED_DB):
            shutil.copy2(LOCAL_SEED_DB, DB_PATH)
    except Exception:
        pass

def generate_map():
    global status_hash_map
    status_hash_map = {}
    print("กำลังเชื่อมต่อฐานข้อมูลและดึงข้อมูลล่าสุดจาก Google Sheets...")
    _ensure_seed_db()

    # ดึงข้อมูล Update State (gid=0) และ Data_Check (gid=2024067511) แบบขนานเพื่อความรวดเร็วบน Render
    with ThreadPoolExecutor(max_workers=2) as executor:
        fut_up = executor.submit(
            fetch_remote_dataframe,
            UPDATE_STATE_URLS,
            _is_valid_update_state_df,
            lambda d: normalize_generic_dataframe(d, ['site id', 'state scada']),
            'Update State',
        )
        fut_chk = executor.submit(
            fetch_remote_dataframe,
            DATA_CHECK_URLS,
            _is_valid_data_check_df,
            lambda d: normalize_generic_dataframe(d, ['site id', 'equipment_id', 'ผู้เข้าแก้ไข']),
            'Data_Check',
        )
        df_update_remote = fut_up.result()
        df_check_remote = fut_chk.result()

    conn = sqlite3.connect(DB_PATH)
    try:
        if df_update_remote is not None:
            print(f"[OK] ดึงข้อมูล Update State (gid=0) สำเร็จ! พบ {len(df_update_remote)} แถว")
            try:
                df_update_remote.to_sql('update_state', conn, if_exists='replace', index=False)
            except Exception:
                pass
            df_update = df_update_remote
        else:
            df_update = load_update_state_df(conn, prefer_local=True)

        if df_check_remote is not None:
            print(f"[OK] ดึงข้อมูลจาก Google Sheets (Data_Check) สำเร็จ! พบ {len(df_check_remote)} แถว")
            try:
                df_check_remote.to_sql('data_check', conn, if_exists='replace', index=False)
            except Exception:
                pass
            df_check = df_check_remote
        else:
            df_check = load_data_check_df(conn, prefer_local=True)

        df = load_sheet1_df(conn, df_update=df_update, df_check=df_check, prefer_local=False)
    finally:
        conn.close()

    print(f"กำลังประมวลผลข้อมูลอุปกรณ์ในระบบทั้งหมด {len(df)} แถว...")
    # --- 4. สร้างแผนที่ตั้งต้น ---
    m = folium.Map(location=[15.2282, 104.8563], zoom_start=8, zoom_control=False, tiles=None, prefer_canvas=True, max_zoom=22)

    base_layer_terrain = folium.TileLayer('https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}', attr='Google', name='แผนที่ภูมิประเทศ (Google Terrain)', overlay=False, control=True, max_zoom=22, show=True).add_to(m)
    base_layer_street = folium.TileLayer('OpenStreetMap', name='แผนที่ถนน (Street Map)', overlay=False, control=True, max_zoom=22, show=False).add_to(m)
    base_layer_satellite = folium.TileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', attr='Google', name='ภาพดาวเทียมล้วน (Google Satellite)', overlay=False, control=True, max_zoom=22, show=False).add_to(m)
    base_layer_hybrid = folium.TileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', attr='Google', name='ภาพดาวเทียม + ถนน (Google Hybrid)', overlay=False, control=True, max_zoom=22, show=False).add_to(m)
    base_layer_esri = folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr='Esri', name='ภาพดาวเทียม (Esri World Imagery)', overlay=False, control=True, max_zoom=22, show=False).add_to(m)

    # --- 5. จัดเตรียมโครงสร้างและการแสดงผล (Pre-resolved Columns เพื่อความเร็วสูงสุด) ---
    raw_parent_keys = {
        "Telemetry Failure": ("#ffc107", "Telemetry Failure"),
        "Offline": ("#d33d2a", "Offline"),
        "Online": ("#008000", "Online"),
        "Initializing": ("#82c91e", "Initializing"),
        "Connecting": ("#f3943b", "Connecting"),
        "สถานะอื่นๆ": ("#575757", "สถานะอื่นๆ")
    }
    hex_color_map = {
        'red': '#d33d2a', 'darkred': '#8b0000', 'orange': '#f3943b',
        'brightgreen': '#008000', 'green': '#008000',
        'lightgreen': '#82c91e',
        'blue': '#38aadd', 'darkblue': '#0067a3', 'purple': '#9b59b6',
        'black': '#333333', 'gray': '#575757', 'lightgray': '#a3a3a3',
        'beige': '#f5c07f', 'gold': '#ffc107'
    }
    display_fields = [
        ("รหัสสั่งการ", ["รหัสสั่งการ"], []),
        ("สถานที่", ["สถานที่"], []),
        ("State SCADA", ["state scada"], ["หลัง"]),
        ("State SCADA (หลังตรวจสอบ)", ["state scada", "หลัง"], []),
        ("ชนิดอุปกรณ์", ["ชนิดอุปกรณ์"], []),
        ("รายละเอียดการแก้ไขข้อขัดข้อง", ["รายละเอียด", "การแก้ไข"], ["เข้า"]),
        ("รายละเอียดการเข้าแก้ไข", ["รายละเอียด", "การเข้าแก้ไข"], []),
        ("LAT/LONG", ["lat", "long"], []),
        ("รอ ผอส. เข้าแก้ไข", ["รอ ผอส"], []),
        ("รอ ผบอ. เข้าแก้ไข", ["รอ ผบอ"], []),
        ("การไฟฟ้า", ["การไฟฟ้า"], []),
        ("วันที่เข้าตรวจสอบ", ["วันที่เข้าตรวจสอบ"], [])
    ]

    resolved_display_cols = []
    for display_name, keywords, excludes in display_fields:
        actual_col = get_actual_col_name(df.columns, keywords, exclude=excludes) or (get_actual_col_name(df.columns, ["รหัสอุปกรณ์"]) if "รหัสสั่งการ" in keywords else None)
        resolved_display_cols.append((display_name, actual_col))

    status_hash_map = {}
    processed_nodes = []
    status_counts = {}
    parent_counts = {k: 0 for k in raw_parent_keys.keys()}
    seen_coords = {}

    cmd_col_report = get_actual_col_name(df.columns, ["รหัสสั่งการ"]) or get_actual_col_name(df.columns, ["รหัสอุปกรณ์"]) or get_actual_col_name(df.columns, ["Equipment_ID"])
    site_col_report = get_actual_col_name(df.columns, ["site id"])
    loc_col_report = get_actual_col_name(df.columns, ["สถานที่"])
    lat_col_main = get_actual_col_name(df.columns, ["lat", "long"])
    date_col_report = get_actual_col_name(df.columns, ["วันที่เข้าตรวจสอบ"])
    worker_col_report = (
        get_actual_col_name(df.columns, ["ผู้เข้าแก้ไข"])
        or get_actual_col_name(df.columns, ["ผู้ปฏิบัติงาน"])
        or get_actual_col_name(df.columns, ["ผู้แก้ไข"])
    )

    state_scada_col = get_actual_col_name(df.columns, ["state scada"], exclude=["หลัง"])
    state_after_col = get_actual_col_name(df.columns, ["state scada", "หลัง"])
    wait_po_col = get_actual_col_name(df.columns, ["รอ ผอส"])
    wait_bo_col = get_actual_col_name(df.columns, ["รอ ผบอ"])

    col_H = df.columns[7] if len(df.columns) > 7 else None
    col_J = df.columns[9] if len(df.columns) > 9 else None
    col_K = df.columns[10] if len(df.columns) > 10 else None
    target_detail_cols = [c for c in [col_H, col_J, col_K] if c is not None]

    re_clean_symbols = re.compile(r'[\☑\☒\☐\✔\✘\✓\❌\✅\✖\⚠️\u26A0\uFE0F\❗️\❓\‼️\⁉️]')
    re_clean_coords = re.compile(r'\d{2}\.\d{4,},\s*\d{3}\.\d{4,}')
    re_multi_space = re.compile(r'\s+')
    re_search_clean = re.compile(r'[\s._-]+')
    re_tone_marks = re.compile(r'[่-๋]')

    def _pre_norm_search(val):
        return re_search_clean.sub('', str(val or '').lower())

    def _pre_tone_search(val):
        return re_tone_marks.sub('', _pre_norm_search(val))

    def extract_sheet1_station_info(row):
        """ดึงข้อมูลสำหรับทำรายงานจากแถวของ ชีต1"""
        base_status = str(row.get(state_scada_col)).strip() if state_scada_col and pd.notna(row.get(state_scada_col)) else 'Unknown'
        check_status = str(row.get(state_after_col)).strip() if state_after_col and pd.notna(row.get(state_after_col)) else ''

        details = []
        for d_col in target_detail_cols:
            val = str(row.get(d_col)).strip() if pd.notna(row.get(d_col)) else ""
            if val and val.lower() not in ['nan', 'none', '-', '', 'ไม่มีค่า']:
                clean_val = re_clean_symbols.sub('', val).strip()
                clean_val = re_clean_coords.sub('', clean_val).strip()
                clean_val = re_multi_space.sub(' ', clean_val)
                if clean_val and clean_val not in details:
                    details.append(clean_val)

        detail_str = "\n".join(details) if details else "-"

        resp_parts = []
        if check_status and check_status.lower() not in ['nan', 'none']:
            if "ผบอ" in check_status:
                resp_parts.append("ผบอ.กบษ.ฉ.2")
            if "ผอส" in check_status:
                resp_parts.append("ผอส.กสฟ.ฉ.2")

        val_bo = str(row.get(wait_bo_col)).strip() if wait_bo_col and pd.notna(row.get(wait_bo_col)) else ""
        has_bo = val_bo and val_bo.lower() not in ['nan', 'none', '-', '', 'ไม่มีค่า', 'false', '0']

        val_po = str(row.get(wait_po_col)).strip() if wait_po_col and pd.notna(row.get(wait_po_col)) else ""
        has_po = val_po and val_po.lower() not in ['nan', 'none', '-', '', 'ไม่มีค่า', 'false', '0']

        if has_bo and "ผบอ.กบษ.ฉ.2" not in resp_parts:
            resp_parts.append("ผบอ.กบษ.ฉ.2")
        if has_po and "ผอส.กสฟ.ฉ.2" not in resp_parts:
            resp_parts.append("ผอส.กสฟ.ฉ.2")

        if "ระบบสื่อสาร ONLINE" in detail_str.upper():
            if "ผอส.กสฟ.ฉ.2" in resp_parts:
                resp_parts.remove("ผอส.กสฟ.ฉ.2")

        final_resp = []
        for r in resp_parts:
            if r not in final_resp:
                final_resp.append(r)

        responsible = " และ ".join(final_resp) if final_resp else "-"
        final_status = base_status if base_status.lower() not in ['nan', 'none', 'unknown'] else "-"

        cmd_val = str(row.get(cmd_col_report)).strip() if cmd_col_report and pd.notna(row.get(cmd_col_report)) else "-"
        site_val = str(row.get(site_col_report)).strip() if site_col_report and pd.notna(row.get(site_col_report)) else "-"
        loc_val = str(row.get(loc_col_report)).strip() if loc_col_report and pd.notna(row.get(loc_col_report)) else "-"
        raw_date = str(row.get(date_col_report)).strip() if date_col_report and pd.notna(row.get(date_col_report)) else ""
        iso_date = parse_thai_date(raw_date)
        workers_val = clean_worker_names(row.get(worker_col_report) if worker_col_report and pd.notna(row.get(worker_col_report)) else "")

        return {
            "iso_date": iso_date,
            "cmd": cmd_val if cmd_val.lower() not in ['nan', 'none', ''] else "-",
            "site": site_val if site_val.lower() not in ['nan', 'none', ''] else "-",
            "loc": loc_val if loc_val.lower() not in ['nan', 'none', ''] else "-",
            "detail": detail_str,
            "resp": responsible,
            "workers": workers_val,
            "status": final_status
        }

    # --- 6. ลูปจัดเตรียมข้อมูลแผนที่ และสร้าง Index อุปกรณ์ในระบบจาก ชีต1 ---
    station_lookup = {}
    sheet1_report_fallback = []

    for row in df.to_dict('records'):
        st_info = extract_sheet1_station_info(row)
        c_key = normalize_station_key(st_info["cmd"])
        s_key = normalize_station_key(st_info["site"])
        if c_key:
            station_lookup[c_key] = st_info
        if s_key:
            station_lookup[s_key] = st_info
        if st_info["iso_date"] or st_info["workers"]:
            sheet1_report_fallback.append(st_info)

        lat_lng_str = str(row.get(lat_col_main)) if lat_col_main and pd.notna(row.get(lat_col_main)) else ''
        if not lat_lng_str or ',' not in lat_lng_str:
            continue
        try:
            parts = lat_lng_str.split(',')
            lat, lon = float(parts[0].strip()), float(parts[1].strip())
        except ValueError:
            continue

        coord_key = (round(lat, 5), round(lon, 5))
        if coord_key in seen_coords:
            seen_coords[coord_key] += 1
            cnt = seen_coords[coord_key]
            offsets = [(0.00005, 0.00005), (-0.00005, -0.00005), (0.00005, -0.00005), (-0.00005, 0.00005), (0.00008, 0.0), (0.0, 0.00008), (-0.00008, 0.0), (0.0, -0.00008)]
            lat += offsets[(cnt - 1) % len(offsets)][0]
            lon += offsets[(cnt - 1) % len(offsets)][1]
        else:
            seen_coords[coord_key] = 0

        base_status = str(row.get(state_scada_col)).strip() if state_scada_col and pd.notna(row.get(state_scada_col)) else 'Unknown'
        check_status = str(row.get(state_after_col)).strip() if state_after_col and pd.notna(row.get(state_after_col)) else ''

        active_status = check_status if check_status and check_status.lower() not in ['nan', 'ไม่มีค่า', 'none'] else base_status
        raw_parent, color, icon_name = get_status_config(active_status)
        active_status_display = active_status

        status_counts[active_status] = status_counts.get(active_status, 0) + 1
        parent_counts[raw_parent] += 1

        processed_nodes.append({
            'row': row, 'lat': lat, 'lon': lon,
            'active_status': active_status,
            'active_status_display': active_status_display,
            'raw_parent': raw_parent, 'color': color, 'icon_name': icon_name
        })

    # --- 7. สร้างข้อมูล Report โดยยึดรายการจาก Data_Check (gid=2024067511) แล้วดึงรายละเอียดอุปกรณ์ในระบบจาก ชีต1 ---
    report_data_list = []

    if df_check is not None and len(df_check) > 0:
        chk_ts_col = get_actual_col_name(df_check.columns, ["timestamp"]) or (df_check.columns[0] if len(df_check.columns) > 0 else None)
        chk_eq_col = (
            get_actual_col_name(df_check.columns, ["equipment_id"])
            or get_actual_col_name(df_check.columns, ["รหัสอุปกรณ์"])
            or get_actual_col_name(df_check.columns, ["รหัสสั่งการ"])
            or (df_check.columns[1] if len(df_check.columns) > 1 else None)
        )
        chk_site_col = (
            get_actual_col_name(df_check.columns, ["site id"])
            or (df_check.columns[2] if len(df_check.columns) > 2 else None)
        )
        chk_date_col = (
            get_actual_col_name(df_check.columns, ["วันที่เข้าตรวจสอบ"])
            or (df_check.columns[3] if len(df_check.columns) > 3 else None)
        )
        chk_worker_col = (
            get_actual_col_name(df_check.columns, ["ผู้เข้าแก้ไข"])
            or get_actual_col_name(df_check.columns, ["ผู้ปฏิบัติงาน"])
            or get_actual_col_name(df_check.columns, ["ผอส"])
            or (df_check.columns[4] if len(df_check.columns) > 4 else None)
        )
        chk_detail_col = (
            get_actual_col_name(df_check.columns, ["รายละเอียดการแก้ไข"])
            or (df_check.columns[5] if len(df_check.columns) > 5 else None)
        )
        chk_ava_col = get_actual_col_name(df_check.columns, ["สถานะจาก ava"]) or get_actual_col_name(df_check.columns, ["status"])
        chk_latest_col = get_actual_col_name(df_check.columns, ["latest"])

        for seq_idx, crow in enumerate(df_check.to_dict('records')):
            raw_eq = str(crow.get(chk_eq_col)).strip() if chk_eq_col and pd.notna(crow.get(chk_eq_col)) else ""
            raw_site = str(crow.get(chk_site_col)).strip() if chk_site_col and pd.notna(crow.get(chk_site_col)) else ""
            raw_date = str(crow.get(chk_date_col)).strip() if chk_date_col and pd.notna(crow.get(chk_date_col)) else ""
            raw_ts = str(crow.get(chk_ts_col)).strip() if chk_ts_col and pd.notna(crow.get(chk_ts_col)) else ""
            raw_workers = str(crow.get(chk_worker_col)).strip() if chk_worker_col and pd.notna(crow.get(chk_worker_col)) else ""
            raw_chk_detail = str(crow.get(chk_detail_col)).strip() if chk_detail_col and pd.notna(crow.get(chk_detail_col)) else ""
            raw_ava = str(crow.get(chk_ava_col)).strip() if chk_ava_col and pd.notna(crow.get(chk_ava_col)) else ""
            raw_latest = crow.get(chk_latest_col) if chk_latest_col and pd.notna(crow.get(chk_latest_col)) else 1
            try:
                latest_val = int(float(raw_latest))
            except Exception:
                latest_val = 1

            k_eq = normalize_station_key(raw_eq)
            k_site = normalize_station_key(raw_site)
            iso_date = parse_thai_date(raw_date) or parse_thai_date(raw_ts)
            workers_str = clean_worker_names(raw_workers)

            if not k_eq and not k_site and not iso_date and not workers_str:
                continue

            st_info = station_lookup.get(k_site) or station_lookup.get(k_eq)

            if st_info:
                cmd_val = st_info["cmd"] if st_info["cmd"] != "-" else (raw_eq if k_eq else "-")
                site_val = st_info["site"] if st_info["site"] != "-" else (raw_site if k_site else "-")
                loc_val = st_info["loc"]
                detail_val = st_info["detail"]
                if detail_val == "-" and raw_chk_detail and raw_chk_detail.lower() not in ['nan', 'none', '-']:
                    detail_val = raw_chk_detail
                resp_val = st_info["resp"]
                status_val = st_info["status"] if st_info["status"] != "-" else (raw_ava if raw_ava and raw_ava.lower() not in ['nan', 'none', '-'] else "-")
                if not iso_date:
                    iso_date = st_info["iso_date"]
                if not workers_str:
                    workers_str = st_info["workers"]
            else:
                cmd_val = raw_eq if k_eq else "-"
                site_val = raw_site if k_site else "-"
                loc_val = "-"
                detail_val = raw_chk_detail if raw_chk_detail and raw_chk_detail.lower() not in ['nan', 'none', '-'] else "-"
                resp_val = "-"
                status_val = raw_ava if raw_ava and raw_ava.lower() not in ['nan', 'none', '-'] else "-"

            report_data_list.append({
                "seq": seq_idx,
                "ts": raw_ts if raw_ts.lower() not in ['nan', 'none'] else "",
                "iso_date": iso_date,
                "cmd": cmd_val,
                "site": site_val,
                "loc": loc_val,
                "detail": detail_val,
                "resp": resp_val,
                "workers": workers_str,
                "status": status_val,
                "latest": latest_val
            })
    else:
        report_data_list = sheet1_report_fallback

    print(f"[OK] เตรียมข้อมูลสำหรับ Report จาก Data_Check สำเร็จ: ทั้งหมด {len(report_data_list)} รายการ")

    grouped_layers = {f"<span style='display:flex; justify-content:space-between; align-items:center; width:100%; padding: 10px 16px; border-bottom: 1px solid #444746;'><span style='display:flex; align-items:center;'><span style='color:{raw_parent_keys[k][0]}; font-size:16px; margin-right:8px; line-height:1;'>●</span><span class='parent-text' data-parent='{k}' style='font-size:14px; font-weight:600; color:#e3e3e3;'>{raw_parent_keys[k][1]}</span></span><span style='color:#9aa0a6; font-size:12px;'>({parent_counts[k]})</span></span>": [] for k in raw_parent_keys.keys() if parent_counts[k] > 0}

    mc_groups, search_data, export_data_list = {}, [], []
    thai_months_short = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]

    for node_idx, node in enumerate(processed_nodes):
        row, lat, lon, active_status, active_status_display = node['row'], node['lat'], node['lon'], node['active_status'], node['active_status_display']
        raw_parent, icon_name, h_color = node['raw_parent'], node['icon_name'], hex_color_map.get(node['color'], '#575757')

        c_hex, label = raw_parent_keys[raw_parent]
        p_html = f"<span style='display:flex; justify-content:space-between; align-items:center; width:100%; padding: 10px 16px; border-bottom: 1px solid #444746;'><span style='display:flex; align-items:center;'><span style='color:{c_hex}; font-size:16px; margin-right:8px; line-height:1;'>●</span><span class='parent-text' data-parent='{raw_parent}' style='font-size:14px; font-weight:600; color:#e3e3e3;'>{label}</span></span><span style='color:#9aa0a6; font-size:12px;'>({parent_counts[raw_parent]})</span></span>"
        safe_status, safe_parent, safe_active_status_attr = "s_" + get_hash(active_status), "p_" + get_hash(raw_parent), html.escape(active_status)

        if active_status not in mc_groups:
            c_count = status_counts[active_status]
            layer_name = f"<span style='display:flex; justify-content:space-between; align-items:flex-start; width:100%;'><span style='display:flex; align-items:flex-start; flex:1;'><i class='fa fa-{icon_name}' style='color:{h_color}; width:16px; text-align:center; margin-right:12px; margin-top:3px; flex-shrink:0;'></i><span class='status-text' data-status='{safe_active_status_attr}' style='font-size:13.5px; color:#e3e3e3; line-height:1.4; word-break:keep-all; overflow-wrap:break-word; text-wrap:balance;'>{active_status_display}</span></span><span style='color:#9aa0a6; font-size:12px; margin-left:8px; flex-shrink:0;'>({c_count})</span></span>"
            custom_cluster_js = f"function(c) {{ var count = c.getChildCount(); return new L.DivIcon({{ html: '<div class=\"map-cluster-inner {safe_status} {safe_parent}\" style=\"background-color:{h_color};\"><i class=\"fa fa-{icon_name} map-cluster-icon\"></i><span class=\"map-cluster-count\">' + count + '</span></div>', className: 'custom-cluster-marker', iconSize: new L.Point(44, 44), iconAnchor: new L.Point(22, 22) }}); }}"
            mc = MarkerCluster(
                name=layer_name,
                show=True,
                icon_create_function=custom_cluster_js,
                control=False,
                options={
                    'disableClusteringAtZoom': 17,
                    'maxClusterRadius': 50,
                    'chunkedLoading': True,
                    'chunkInterval': 100,
                    'chunkDelay': 15,
                    'animate': False,
                    'animateAddingMarkers': False,
                    'showCoverageOnHover': False,
                    'removeOutsideVisibleBounds': True,
                },
            )
            mc_groups[active_status] = mc
            grouped_layers[p_html].append((active_status, mc))

        target_group = mc_groups[active_status]

        site_id = str(row.get(site_col_report)) if site_col_report and pd.notna(row.get(site_col_report)) else 'Unknown'
        safe_site_id = "id_" + get_hash(site_id + str(lat))
        cmd_code = str(row.get(cmd_col_report)) if cmd_col_report and pd.notna(row.get(cmd_col_report)) else ''
        location_name = str(row.get(loc_col_report)) if loc_col_report and pd.notna(row.get(loc_col_report)) else ''

        export_row_arr = [active_status, site_id]

        for display_name, actual_col in resolved_display_cols:
            val = row.get(actual_col) if actual_col and pd.notna(row.get(actual_col)) else ''
            val_s = str(val).strip()
            if not val_s or val_s.lower() in ('nan', 'none', 'nat', 'ไม่มีค่า'):
                val_s = ''
            elif "วันที่" in display_name:
                iso_d = parse_thai_date(val_s)
                if iso_d and len(iso_d) >= 10:
                    try:
                        y_p, m_p, d_p = int(iso_d[:4]), int(iso_d[5:7]), int(iso_d[8:10])
                        val_s = f"{d_p} {thai_months_short[m_p]} {str(y_p)[-2:]}"
                    except Exception:
                        pass
            export_row_arr.append(val_s)

        export_data_list.append(export_row_arr)

        search_data.append({
            "id": site_id,
            "code": cmd_code,
            "name": location_name,
            "lat": round(lat, 7),
            "lon": round(lon, 7),
            "safe_id": safe_site_id,
            "status": active_status_display,
            "color": h_color,
            "icon": icon_name,
            "idx": node_idx,
            "norm": [_pre_norm_search(site_id), _pre_norm_search(cmd_code), _pre_norm_search(location_name)],
            "tone": [_pre_tone_search(site_id), _pre_tone_search(cmd_code), _pre_tone_search(location_name)],
        })

        popup_stub = f'<div class="custom-popup-card" data-safe-id="{safe_site_id}"></div>'
        pin_html = f'<div class="map-pin-inner {safe_status} {safe_parent} pin-site-{safe_site_id}"><div class="pin-shape" style="background-color:{h_color};"></div><i class="fa fa-{icon_name} map-pin-icon"></i></div>'
        folium.Marker(
            location=[lat, lon],
            popup=folium.Popup(popup_stub, autoPan=False),
            tooltip=f"{site_id} ({location_name})",
            icon=folium.DivIcon(html=pin_html, icon_size=(30, 42), icon_anchor=(15, 42), popup_anchor=(0, -42)),
        ).add_to(target_group)

    active_grouped_layers = {}
    for p_html, items_list in grouped_layers.items():
        if len(items_list) > 0:
            def custom_sort(x):
                s = x[0].upper()
                w = 100
                if s in ["ONLINE", "OFFLINE", "INITIALIZING", "CONNECTING", "TELEMETRY FAILURE"]:
                    w = 1
                elif "รอ ผบอ. เข้าแก้ไข" in s and "ผอส" not in s:
                    w = 2
                elif "รอ ผบอ. และ ผอส." in s or "และ ผอส" in s:
                    w = 3
                elif "รอ ผอส." in s:
                    w = 4
                elif "ผบอ. เคยแก้ไข" in s:
                    w = 5
                elif "ระบบสื่อสาร" in s and "เคยแก้ไข" in s:
                    w = 6
                else:
                    w = 10
                return (w, len(s), s)
            items_list.sort(key=custom_sort)
            sorted_mcs = []
            for active_status, mc in items_list:
                m.add_child(mc)
                sorted_mcs.append(mc)
            active_grouped_layers[p_html] = sorted_mcs

    folium.LayerControl(position='topleft', collapsed=True).add_to(m)
    GroupedLayerControl(groups=active_grouped_layers, exclusive_groups=False, collapsed=True).add_to(m)

    search_json = json.dumps(search_data, ensure_ascii=False, separators=(',', ':'))
    export_json = json.dumps(export_data_list, ensure_ascii=False, separators=(',', ':'))
    status_hash_json = json.dumps(status_hash_map, ensure_ascii=False, separators=(',', ':'))
    report_json_data = json.dumps(report_data_list, ensure_ascii=False, separators=(',', ':'))
    base_layer_names_json = json.dumps({
        "terrain": base_layer_terrain.get_name(),
        "street": base_layer_street.get_name(),
        "satellite": base_layer_satellite.get_name(),
        "hybrid": base_layer_hybrid.get_name(),
        "esri": base_layer_esri.get_name(),
    }, ensure_ascii=False, separators=(',', ':'))

    # --- 8. UI CSS & JavaScript ---
    custom_ui_html = f"""
    <link href="https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;500;600&display=swap" rel="stylesheet">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/exceljs/4.3.0/exceljs.min.js"></script>

    <style>
    /* --- ตั้งค่า Scrollbar ให้เป็น Global --- */
    * {{ font-family: 'Prompt', sans-serif; outline: none !important; -webkit-tap-highlight-color: transparent !important; box-sizing: border-box; scrollbar-width: thin; scrollbar-color: rgba(154, 160, 166, 0.4) transparent; }}
    ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
    ::-webkit-scrollbar-track {{ background: transparent; }}
    ::-webkit-scrollbar-thumb {{ background-color: rgba(154, 160, 166, 0.4); border-radius: 10px; }}
    ::-webkit-scrollbar-thumb:hover {{ background-color: rgba(154, 160, 166, 0.7); }}

    .leaflet-top {{ z-index: 999 !important; }}
    .leaflet-bottom {{ z-index: 998 !important; }}
    .leaflet-top.leaflet-left .leaflet-control-layers, .leaflet-top.leaflet-right .leaflet-control-layers {{ display: none !important; }}

    .leaflet-popup {{ visibility: hidden !important; opacity: 0 !important; pointer-events: none !important; position: absolute !important; }}

    /* --- Shared GPU-Accelerated Pin & Cluster Classes (ลด Inline Style ซ้ำซ้อน 1,495 จุด) --- */
    .map-pin-inner {{ position: relative; width: 30px; height: 42px; display: flex; justify-content: center; transition: transform 0.16s ease, opacity 0.16s ease; will-change: transform, opacity; contain: layout style; }}
    .pin-shape {{ position: absolute; top: 0; left: 0; width: 30px; height: 30px; border: 2px solid #ffffff; border-radius: 50% 50% 50% 0; transform: rotate(-45deg); box-shadow: 1px 2px 5px rgba(0,0,0,0.38); }}
    .map-pin-icon {{ position: relative; color: #ffffff; font-size: 14px; margin-top: 6px; z-index: 1; pointer-events: none; }}
    .map-cluster-inner {{ color: #ffffff; border-radius: 50%; width: 44px; height: 44px; display: flex; flex-direction: column; justify-content: center; align-items: center; font-family: 'Prompt', sans-serif; font-weight: 600; border: 2px solid #ffffff; box-shadow: 0 2px 5px rgba(0,0,0,0.38); text-shadow: 1px 1px 2px rgba(0,0,0,0.65); transition: transform 0.16s ease, opacity 0.16s ease; will-change: transform, opacity; contain: layout style; }}
    .map-cluster-icon {{ font-size: 12px; margin-bottom: 2px; pointer-events: none; }}
    .map-cluster-count {{ font-size: 13px; line-height: 1; pointer-events: none; }}

    .selected-pin-glow {{ transform: scale(1.4) !important; z-index: 100000 !important; }}
    .selected-pin-glow .pin-shape {{ box-shadow: 0 0 0 3px #ffffff, 0 0 18px 6px rgba(66, 133, 244, 0.8) !important; border-color: #4285F4 !important; }}
    .selected-pin-glow i {{ text-shadow: 0 0 5px rgba(255,255,255,0.8); }}

    body.filter-hover-active .map-pin-inner, body.filter-hover-active .map-cluster-inner {{ opacity: 0.18; }}
    body.filter-hover-active .map-pin-inner.highlight-active, body.filter-hover-active .map-cluster-inner.highlight-active {{ opacity: 1 !important; transform: scale(1.22); }}
    body.filter-hover-active .map-pin-inner.highlight-active .pin-shape, body.filter-hover-active .map-cluster-inner.highlight-active {{ box-shadow: 0 0 10px 4px rgba(255, 255, 255, 0.88), 0 0 4px rgba(0,0,0,0.45) !important; }}

    .g-search-container {{ position: fixed; z-index: 100005; top: max(20px, env(safe-area-inset-top, 20px)); left: 16px; width: 380px; margin: 0; pointer-events: none; }}
    .g-search-box {{ pointer-events: auto; background: #282a2d; border-radius: 24px; box-shadow: 0 2px 6px rgba(0,0,0,0.3); display: flex; align-items: center; padding: 0 14px; height: 48px; border: 1px solid #444746; }}
    .g-search-box:hover, .g-search-box.focus {{ border-color: #8ab4f8 !important; }}
    .g-search-icon {{ display: flex; align-items: center; justify-content: center; width: 24px; height: 24px; color: #9aa0a6; }}
    input#searchInput.g-search-input {{ flex: 1 !important; border: none !important; outline: none !important; background: transparent !important; background-color: transparent !important; font-size: 14px !important; color: #e8eaed !important; margin-left: 10px !important; width: 100% !important; font-family: 'Prompt', sans-serif !important; pointer-events: auto !important; box-shadow: none !important; -webkit-appearance: none !important; }}
    input#searchInput.g-search-input::placeholder {{ color: #9aa0a6 !important; font-weight: 400 !important; opacity: 1 !important; }}
    .g-search-clear {{ display: none; color: #9aa0a6; font-size: 22px; cursor: pointer; padding: 0 8px; line-height: 1; pointer-events: auto; }}
    .g-search-results {{ position: absolute; top: 54px; left: 0; width: 100%; background: #282a2d; border-radius: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.4); display: none; overflow: hidden; padding: 0; max-height: 400px; overflow-y: auto; border: 1px solid #444746; -webkit-overflow-scrolling: touch; pointer-events: auto; }}

    .top-action-btn {{ position: fixed; top: max(20px, env(safe-area-inset-top, 20px)); width: 48px; height: 48px; background: #282a2d; border-radius: 50%; box-shadow: 0 4px 12px rgba(0,0,0,0.3); display: flex; align-items: center; justify-content: center; cursor: pointer; z-index: 100000; transition: all 0.2s; border: 1px solid #444746; pointer-events: auto; }}
    .top-action-btn:hover {{ background-color: #3c4043; transform: translateY(-2px); }}
    .top-action-btn:active {{ transform: scale(0.92); }}
    .standalone-filter-btn {{ right: 16px; }}
    .standalone-report-btn {{ right: 74px; background-color: #1a73e8; border-color: #1a73e8; }}
    .standalone-report-btn:hover {{ background-color: #1557b0; border-color: #1557b0; }}
    .top-action-btn svg {{ fill: none; stroke: #e3e3e3; stroke-width: 2.2; width: 22px; height: 22px; pointer-events: none; }}
    .standalone-report-btn svg {{ stroke: #ffffff; width: 20px; height: 20px; }}

    .custom-filter-wrapper {{ display: none; flex-direction: column; position: fixed; top: calc(max(20px, env(safe-area-inset-top, 20px)) + 60px); right: 16px; width: 340px; max-height: calc(100dvh - 100px) !important; background-color: #282a2d; border-radius: 16px; box-shadow: 0 8px 24px rgba(0,0,0,0.5); border: 1px solid #444746; overflow: hidden; z-index: 999998; pointer-events: auto; padding-top: 8px; }}
    .custom-filter-wrapper.show {{ display: flex !important; overflow-y: auto !important; overflow-x: hidden !important; }}
    .custom-filter-wrapper form {{ display: flex !important; flex-direction: column !important; margin: 0 !important; padding: 0 !important; height: auto !important; min-height: 0 !important; overflow: visible !important; }}
    .custom-filter-wrapper .leaflet-control-layers-list {{ overflow: visible !important; max-height: none !important; height: auto !important; padding: 0 0 12px 0 !important; margin: 0 !important; }}
    .custom-filter-wrapper .leaflet-control-layers-overlays {{ display: flex !important; flex-direction: column !important; width: 100% !important; overflow: visible !important; }}
    .custom-filter-wrapper .leaflet-control-layers-group {{ display: flex !important; flex-direction: column !important; width: 100% !important; margin-bottom: 8px !important; clear: both !important; align-items: stretch !important; }}
    .custom-filter-wrapper label.leaflet-control-layers-group-label,
    .custom-filter-wrapper label {{ display: flex !important; flex-direction: row !important; align-items: flex-start !important; width: 100% !important; padding: 6px 16px !important; margin: 0 !important; cursor: pointer !important; float: none !important; clear: both !important; box-sizing: border-box !important; }}
    .custom-filter-wrapper label:hover {{ background-color: #35363a !important; }}
    .custom-filter-wrapper label > span {{ display: flex !important; flex-direction: row !important; align-items: flex-start !important; width: 100% !important; white-space: normal !important; }}
    .custom-filter-wrapper input[type="checkbox"] {{ flex-shrink: 0 !important; -webkit-appearance: none !important; -moz-appearance: none !important; appearance: none !important; width: 18px !important; height: 18px !important; border: 2px solid #8e918f !important; border-radius: 3px !important; margin: 2px 14px 0 0 !important; position: relative !important; cursor: pointer; outline: none; transition: all 0.2s ease; background-color: transparent !important; box-shadow: none !important; }}
    .custom-filter-wrapper input[type="checkbox"]:checked {{ background-color: #8ab4f8 !important; border-color: #8ab4f8 !important; }}
    .custom-filter-wrapper input[type="checkbox"]:checked::after {{ content: ''; position: absolute; top: 1px; left: 5px; width: 4px; height: 8px; border: solid #202124; border-width: 0 2px 2px 0; transform: rotate(45deg); }}

    .report-modal-overlay {{ display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; width: 100vw; height: 100dvh; background: rgba(0,0,0,0.5); backdrop-filter: blur(4px); z-index: 9999990 !important; justify-content: center; align-items: center; opacity: 0; transition: opacity 0.3s ease; pointer-events: auto; }}
    .report-modal-overlay.show {{ display: flex !important; opacity: 1 !important; }}

    .custom-alert-overlay {{ display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; width: 100vw; height: 100dvh; background: rgba(0,0,0,0.65); backdrop-filter: blur(8px); z-index: 10000000 !important; justify-content: center; align-items: center; opacity: 0; transition: opacity 0.3s ease; pointer-events: auto; }}
    .custom-alert-overlay.show {{ display: flex !important; opacity: 1 !important; }}

    .custom-alert-box {{ background: #202124; width: 90%; max-width: 340px; border-radius: 20px; padding: 32px 24px 24px 24px; box-shadow: 0 20px 50px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.05); text-align: center; transform: scale(0.95) translateY(10px); transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275); margin: auto; position: relative; }}
    .custom-alert-overlay.show .custom-alert-box {{ transform: scale(1) translateY(0); }}

    .custom-alert-icon {{ width: 56px; height: 56px; background: rgba(234, 67, 53, 0.15); border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 20px auto; border: 4px solid rgba(234, 67, 53, 0.1); }}
    .custom-alert-icon svg {{ width: 28px; height: 28px; fill: #ea4335; }}
    .custom-alert-title {{ color: #e8eaed; font-size: 18px; font-weight: 600; margin-bottom: 10px; letter-spacing: 0.3px; }}
    .custom-alert-msg {{ color: #9aa0a6; font-size: 14px; margin-bottom: 28px; line-height: 1.5; }}
    .custom-alert-btn {{ background: #1a73e8; color: white; border: none; padding: 12px 24px; border-radius: 10px; font-size: 15px; font-weight: 600; cursor: pointer; transition: all 0.2s; width: 100%; box-shadow: 0 4px 12px rgba(26, 115, 232, 0.3); }}
    .custom-alert-btn:hover {{ background: #1557b0; box-shadow: 0 6px 16px rgba(26, 115, 232, 0.4); transform: translateY(-1px); }}
    .custom-alert-btn:active {{ transform: translateY(1px); box-shadow: 0 2px 8px rgba(26, 115, 232, 0.3); }}

    .report-modal {{ background: linear-gradient(160deg, #2e3035 0%, #25272a 100%); width: min(94vw, 640px); max-width: 640px; max-height: calc(100dvh - 28px); overflow-y: auto; border-radius: 22px; padding: 28px; box-shadow: 0 24px 80px rgba(0,0,0,0.48), 0 0 0 1px rgba(255,255,255,0.06); border: 1px solid #484b50; transform: translateY(18px) scale(.985); transition: transform 0.25s ease; margin: auto; position: relative; }}
    .report-modal-overlay.show .report-modal {{ transform: translateY(0) scale(1); }}
    .report-modal-top {{ display: flex; align-items: flex-start; gap: 16px; margin-bottom: 24px; padding-right: 30px; }}
    .report-modal-icon {{ width: 48px; height: 48px; flex: 0 0 48px; border-radius: 14px; background: linear-gradient(145deg, #2684ff, #1765cc); display: flex; align-items: center; justify-content: center; box-shadow: 0 7px 18px rgba(26,115,232,.25); }}
    .report-modal-icon svg {{ width: 25px; height: 25px; stroke: #fff; fill: none; stroke-width: 1.8; }}
    .report-eyebrow {{ margin: 0 0 4px; color: #8ab4f8; font-size: 10px; font-weight: 600; letter-spacing: 1.2px; }}
    .report-modal h3 {{ color: #f1f3f4; margin: 0; font-size: 22px; font-weight: 600; letter-spacing: -.3px; text-align: left; line-height: 1.35; }}
    .report-subtitle {{ color: #aeb2b8; margin: 5px 0 0; font-size: 12.5px; line-height: 1.55; }}
    .report-close-btn {{ position: absolute; top: 20px; right: 20px; display: flex; align-items: center; justify-content: center; width: 34px; height: 34px; border: 1px solid #484b50; border-radius: 10px; color: #bdc1c6; background: #303237; font-size: 22px; cursor: pointer; transition: .2s; }}
    .report-close-btn:hover {{ color: #fff; background: #41444a; }}
    .report-section {{ padding: 18px; margin: 0 0 14px; background: rgba(32,33,36,.55); border: 1px solid #414449; border-radius: 16px; }}
    .report-section-heading {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 14px; }}
    .report-section-title {{ color: #f1f3f4; font-size: 14px; font-weight: 600; display: block; }}
    .report-section-help {{ color: #9aa0a6; font-size: 11.5px; margin-top: 3px; line-height: 1.45; }}
    .report-optional-tag, .report-worker-count {{ display: inline-flex; align-items: center; border: 1px solid #4a4d52; border-radius: 999px; padding: 4px 9px; background: #303237; color: #b8bdc4; font-size: 10.5px; white-space: nowrap; }}
    .date-input-group {{ display: flex; flex-direction: column; gap: 6px; flex: 1; min-width: 0; }}
    .date-input-group label {{ color: #c7cacf; font-size: 12px; font-weight: 500; }}
    .date-input {{ width: 100%; padding: 11px 12px; border-radius: 10px; border: 1px solid #4d5157; background: #202124; color: #e8eaed; font-size: 13px; outline: none; font-family: 'Prompt', sans-serif; color-scheme: dark; transition: border-color .2s, box-shadow .2s; -webkit-appearance: none; box-shadow: none; min-height: 43px; }}
    .date-input::placeholder {{ color: #858b93; font-weight: 400; opacity: 1; }}
    .date-input:focus {{ border-color: #8ab4f8; box-shadow: 0 0 0 3px rgba(138,180,248,.14); }}
    .date-flex-container {{ display: flex; gap: 12px; margin-bottom: 0; }}
    .report-worker-toolbar {{ display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }}
    .report-worker-search-wrap {{ display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; padding: 0 11px; min-height: 40px; background: #202124; border: 1px solid #4d5157; border-radius: 10px; }}
    .report-worker-search-wrap svg {{ width: 17px; height: 17px; fill: #9aa0a6; flex: 0 0 auto; }}
    .report-worker-search {{ flex: 1; min-width: 0; color: #e8eaed; background: transparent; border: 0; outline: none; font: 12px 'Prompt',sans-serif; }}
    .report-worker-search::placeholder {{ color: #858b93; }}
    .report-mini-btn {{ white-space: nowrap; border: 1px solid #4d5157; background: #33363b; color: #dfe2e6; padding: 9px 11px; border-radius: 9px; font: 11px 'Prompt',sans-serif; cursor: pointer; transition: .2s; }}
    .report-mini-btn:hover {{ background: #41454b; border-color: #686d75; }}
    .report-worker-list {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; max-height: 190px; min-height: 56px; overflow-y: auto; padding: 2px 3px 2px 2px; }}
    .worker-option {{ display: flex; align-items: center; gap: 9px; min-width: 0; padding: 10px; border: 1px solid #414449; border-radius: 10px; background: #2a2c30; cursor: pointer; transition: background .16s, border-color .16s, transform .16s; }}
    .worker-option:hover {{ background: #33363c; border-color: #626772; }}
    .worker-option:has(input:checked) {{ background: rgba(26,115,232,.16); border-color: #5b9cf6; }}
    .worker-option input {{ width: 16px; height: 16px; flex: 0 0 16px; accent-color: #4285f4; margin: 0; cursor: pointer; }}
    .worker-avatar {{ width: 28px; height: 28px; display: flex; flex: 0 0 28px; align-items: center; justify-content: center; border-radius: 9px; background: #3b414a; color: #b6d2ff; font-size: 12px; font-weight: 600; }}
    .worker-option:has(input:checked) .worker-avatar {{ background: #1a73e8; color: #fff; }}
    .worker-name {{ color: #e8eaed; font-size: 11.5px; line-height: 1.4; overflow-wrap: anywhere; }}
    .report-worker-empty {{ grid-column: 1 / -1; padding: 18px; color: #9aa0a6; text-align: center; font-size: 12px; border: 1px dashed #4d5157; border-radius: 10px; }}
    .report-selection-footer {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; color: #9aa0a6; font-size: 11px; margin-top: 11px; }}
    .report-selection-footer strong {{ color: #b7d2ff; font-weight: 500; }}
    .report-export-summary {{ display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-radius: 12px; background: rgba(26,115,232,.10); border: 1px solid rgba(91,156,246,.25); margin: 4px 0 20px; }}
    .report-export-summary svg {{ flex: 0 0 18px; width: 18px; height: 18px; stroke: #8ab4f8; fill: none; stroke-width: 2; }}
    .report-export-summary-text {{ flex: 1; color: #c9dafa; font-size: 11.5px; line-height: 1.5; }}
    .report-preview-count {{ color: #fff; font-size: 12px; font-weight: 600; white-space: nowrap; }}
    .report-note {{ font-size: 11px; color: #9aa0a6; margin: 12px 0 0; line-height: 1.45; }}
    .report-btn-group {{ display: flex; gap: 10px; }}
    .report-btn {{ flex: 1; min-height: 44px; padding: 11px 16px; border-radius: 11px; border: none; font: 500 13px 'Prompt',sans-serif; cursor: pointer; transition: .2s; display: flex; justify-content: center; align-items: center; gap: 8px; }}
    .report-btn-cancel {{ background: #303237; color: #d4d7dc; border: 1px solid #4d5157; }}
    .report-btn-cancel:hover {{ background: #41444a; }}
    .report-btn-dl {{ background: linear-gradient(135deg, #2b83f6, #1765cc); color: white; box-shadow: 0 6px 16px rgba(26,115,232,.18); }}
    .report-btn-dl:hover {{ background: linear-gradient(135deg, #428ff8, #1f70db); transform: translateY(-1px); }}
    .report-btn-dl:disabled {{ background: #5f6368; cursor: wait; box-shadow: none; transform: none; }}

    .custom-info-panel {{ display: none; position: fixed; top: 74px; left: 16px; width: 360px; max-width: calc(100vw - 32px); max-height: calc(100dvh - 88px); background: #fff; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.25); z-index: 99999; flex-direction: column; overflow: hidden; animation: slideDownFade 0.2s ease-out; pointer-events: auto; }}

    .g-export-container {{ flex: 0 0 auto !important; padding: 16px !important; background-color: #282a2d !important; border-top: 1px solid #444746; display: flex; justify-content: center; }}
    .g-export-btn {{ background-color: #3c4043 !important; color: #e3e3e3 !important; border: none !important; border-radius: 24px !important; height: 48px !important; width: 100% !important; font-size: 14px !important; font-weight: 500 !important; cursor: pointer !important; display: flex !important; align-items: center !important; justify-content: center !important; gap: 10px !important; transition: background-color 0.2s !important; outline: none !important; font-family: 'Prompt', sans-serif !important; }}
    .g-export-btn:hover {{ background-color: #4a4d51 !important; }}
    .g-export-btn:active {{ transform: scale(0.97) !important; }}
    .g-export-btn svg {{ width: 20px !important; height: 20px !important; fill: currentColor !important; flex-shrink: 0 !important; }}

    .g-search-item {{ padding: 12px 16px; display: flex; flex-direction: column; cursor: pointer; border-bottom: 1px solid #35363a; transition: background-color 0.2s, border-left 0.2s; border-left: 3px solid transparent; gap: 6px; }}
    .g-search-item:last-child {{ border-bottom: none; }}
    .g-search-item:hover {{ background: #3c4043; border-left-color: #8ab4f8; }}

    @media (max-width: 768px) {{
        .g-search-container {{ width: calc(100vw - 146px) !important; max-width: none !important; }}
        .custom-filter-wrapper {{ top: calc(max(20px, env(safe-area-inset-top, 20px)) + 60px) !important; right: 16px !important; left: 16px !important; width: auto !important; max-height: calc(100dvh - 110px) !important; }}
        .custom-info-panel {{ top: auto !important; bottom: 0 !important; left: 0 !important; width: 100vw !important; max-width: 100vw !important; max-height: 55vh !important; border-radius: 20px 20px 0 0 !important; animation: slideUpFade 0.3s ease-out; border-bottom: none !important; padding-bottom: env(safe-area-inset-bottom, 0px); }}
        .popup-body {{ max-height: calc(55vh - 125px - env(safe-area-inset-bottom, 0px)) !important; }}
        .date-flex-container {{ flex-direction: column; }}
        .report-modal {{ padding: 20px; border-radius: 18px; width: calc(100vw - 24px); max-height: calc(100dvh - 24px); }}
        .report-modal-top {{ gap: 12px; margin-bottom: 18px; }}
        .report-modal-icon {{ width: 42px; height: 42px; flex-basis: 42px; border-radius: 12px; }}
        .report-modal h3 {{ font-size: 19px; }}
        .report-section {{ padding: 14px; }}
        .report-worker-toolbar {{ flex-wrap: wrap; }}
        .report-worker-search-wrap {{ flex-basis: 100%; }}
        .report-worker-list {{ grid-template-columns: 1fr; max-height: 175px; }}
        .report-export-summary {{ align-items: flex-start; flex-wrap: wrap; }}
        .report-preview-count {{ width: 100%; padding-left: 28px; }}
    }}

    @keyframes slideDownFade {{ from {{ opacity: 0; transform: translateY(-15px); }} to {{ opacity: 1; transform: translateY(0); }} }}
    @keyframes slideUpFade {{ from {{ opacity: 0; transform: translateY(100%); }} to {{ opacity: 1; transform: translateY(0); }} }}

    .panel-close-btn {{ position: absolute; top: 12px; right: 12px; width: 28px; height: 28px; background: rgba(0,0,0,0.3); border-radius: 50%; display: flex; align-items: center; justify-content: center; cursor: pointer; z-index: 1000; transition: 0.2s; }}
    .panel-close-btn:hover {{ background: rgba(0,0,0,0.6); }}
    .custom-popup-card {{ display: flex; flex-direction: column; background: #fff; width: 100%; height: 100%; }}
    .popup-header {{ padding: 12px 16px; display: flex; align-items: center; gap: 12px; position: relative; }}
    .popup-icon {{ width: 40px; height: 40px; background: rgba(255,255,255,0.25); border-radius: 50%; display: flex; align-items: center; justify-content: center; flex-shrink: 0; box-shadow: 0 4px 10px rgba(0,0,0,0.1); border: 2px solid rgba(255,255,255,0.4); }}
    .popup-icon i {{ font-size: 18px; color: #fff; }}
    .popup-title {{ flex: 1; padding-right: 28px; overflow: hidden; }}
    .popup-title h3 {{ margin: 0; font-size: 16px; font-weight: 600; color: #fff; line-height: 1.2; word-break: break-word; white-space: normal; }}
    .popup-title span {{ font-size: 12.5px; color: rgba(255,255,255,0.95); font-weight: 400; display: block; margin-top: 2px; word-break: break-word; white-space: normal; line-height: 1.35; }}

    .popup-actions {{ display: flex; gap: 8px; padding: 12px 16px; background: #f8f9fa; border-bottom: 1px solid #e0e0e0; flex-shrink: 0; }}
    .btn-map {{ flex: 1; padding: 8px; border-radius: 6px; font-size: 12px; font-weight: 500; text-decoration: none; display: flex; align-items: center; justify-content: center; gap: 6px; transition: all 0.2s; color: white !important; }}
    .btn-google {{ background-color: #4285F4; box-shadow: 0 2px 4px rgba(66,133,244,0.3); }} .btn-google:hover {{ background-color: #3367D6; }}
    .btn-apple {{ background-color: #000000; box-shadow: 0 2px 4px rgba(0,0,0,0.3); }} .btn-apple:hover {{ background-color: #333333; }}

    .popup-body {{ padding: 0 16px 16px; max-height: calc(100dvh - 220px); overflow-y: auto; background: #fff; }}
    .popup-table {{ width: 100%; border-collapse: collapse; }}
    .popup-table tr {{ border-bottom: 1px solid #f1f3f4; }}
    .popup-table tr:last-child {{ border-bottom: none; }}
    .popup-table td {{ padding: 8px 0; font-size: 12.5px; line-height: 1.4; word-break: keep-all; overflow-wrap: break-word; text-wrap: balance; }}
    .popup-table td:first-child {{ color: #5f6368; font-weight: 500; width: 45%; vertical-align: top; padding-right: 8px; }}
    .popup-table td:last-child {{ color: #202124; font-weight: 500; text-align: right; vertical-align: top; }}

    @media (max-width: 768px) {{ .g-zoom-container {{ display: none !important; }} }}
    .custom-right-controls {{ display: flex; flex-direction: column; gap: 12px; align-items: center; margin-right: 16px; margin-bottom: 24px; z-index: 500 !important; position: relative; }}
    .g-zoom-container {{ width: 40px; background-color: #fff; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.3); display: flex; flex-direction: column; overflow: hidden; }}
    .g-zoom-btn {{ width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; font-size: 22px; color: #5f6368; background: transparent; cursor: pointer; user-select: none; transition: background-color 0.2s; font-weight: 400; margin: 0; border: none; padding: 0; outline: none; }}
    .g-zoom-btn:hover {{ background-color: #f1f3f4; color: #202124; }}
    .g-zoom-in {{ border-bottom: 1px solid #e8eaed; }}

    .g-locate-btn {{ width: 44px !important; height: 44px !important; background-color: #ffffff !important; color: #1a73e8 !important; border-radius: 50% !important; box-shadow: 0 2px 6px rgba(0,0,0,0.3) !important; display: flex !important; align-items: center; justify-content: center; cursor: pointer; transition: background-color 0.2s; text-decoration: none !important; }}
    @media (min-width: 769px) {{ .g-locate-btn {{ width: 40px !important; height: 40px !important; border-radius: 8px !important; }} }}
    .g-locate-btn:hover {{ background-color: #f1f3f4 !important; }}
    .g-locate-btn:active {{ transform: scale(0.95); }}
    .g-locate-btn svg {{ width: 22px; height: 22px; fill: #1a73e8; transform: rotate(45deg) translate(-1px, 2px); }}

    .my-location-container {{ position: relative; width: 60px; height: 60px; display: flex; align-items: center; justify-content: center; }}
    .my-location-cone {{ position: absolute; width: 60px; height: 60px; border-radius: 50%; background: conic-gradient(from 225deg at 50% 50%, rgba(66, 133, 244, 0) 0deg, rgba(66, 133, 244, 0.4) 45deg, rgba(66, 133, 244, 0) 90deg, transparent 90deg); z-index: 1; pointer-events: none; animation: cone-breathe 2.5s infinite ease-in-out; }}
    @keyframes cone-breathe {{ 0% {{ transform: scale(0.85); opacity: 0.7; }} 50% {{ transform: scale(1.1); opacity: 1; }} 100% {{ transform: scale(0.85); opacity: 0.7; }} }}
    .my-location-dot {{ position: absolute; background-color: #4285F4; width: 16px; height: 16px; border-radius: 50%; border: 3px solid #fff; box-shadow: 0 2px 6px rgba(0,0,0,0.4); z-index: 2; }}

    .g-layer-container {{ position: absolute; bottom: calc(24px + env(safe-area-inset-bottom, 0px)); left: 16px; z-index: 9999; display: flex; align-items: flex-end; font-family: 'Prompt', sans-serif; pointer-events: none; }}
    .g-layer-main-btn {{ pointer-events: auto; width: 56px; height: 56px; border-radius: 12px; border: 2px solid rgba(255,255,255,0.8); box-shadow: 0 4px 12px rgba(0,0,0,0.3); background-size: cover; background-position: center; cursor: pointer; position: relative; overflow: hidden; transition: all 0.2s ease; }}
    .g-layer-main-btn:hover {{ transform: scale(1.05); box-shadow: 0 6px 16px rgba(0,0,0,0.4); }}
    .g-layer-label {{ position: absolute; bottom: 0; left: 0; right: 0; background: rgba(0,0,0,0.6); color: #fff; font-size: 10px; text-align: center; padding: 4px 0; font-weight: 500; backdrop-filter: blur(2px); }}
    .g-layer-panel {{ pointer-events: auto; background: rgba(40, 42, 45, 0.95); border-radius: 16px; display: flex; gap: 12px; padding: 0; max-width: 0; overflow: hidden; opacity: 0; transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1); height: 80px; align-items: center; margin-left: 12px; border: 1px solid rgba(255,255,255,0.1); box-shadow: 0 8px 24px rgba(0,0,0,0.4); backdrop-filter: blur(8px); }}
    .g-layer-panel.show {{ max-width: 500px !important; padding: 0 20px !important; opacity: 1 !important; }}
    .g-layer-container:hover .g-layer-panel:not(.show) {{ max-width: 0 !important; padding: 0 !important; opacity: 0 !important; }}
    .g-layer-item {{ display: flex; flex-direction: column; align-items: center; cursor: pointer; gap: 6px; padding: 4px; border-radius: 10px; transition: background-color 0.2s; }}
    .g-layer-item:hover {{ background-color: rgba(255,255,255,0.1); }}
    .g-layer-thumb {{ width: 44px; height: 44px; border-radius: 10px; border: 2px solid transparent; background-size: cover; background-position: center; box-shadow: 0 2px 6px rgba(0,0,0,0.2); transition: all 0.2s; }}
    .g-layer-item.active .g-layer-thumb {{ border-color: #8ab4f8; box-shadow: 0 0 0 2px rgba(138,180,248,0.4); transform: scale(1.05); }}
    .g-layer-name {{ font-size: 11px; color: #e8eaed; font-weight: 500; white-space: nowrap; }}

    /* Flat, consistent SCADA design tokens (Vercel / Linear / Notion Minimalist Dark UI) */
    * {{ scrollbar-width: thin !important; scrollbar-color: #555 transparent !important; }}
    ::-webkit-scrollbar {{ width: 6px !important; height: 6px !important; }}
    ::-webkit-scrollbar-track {{ background: transparent !important; }}
    ::-webkit-scrollbar-thumb {{ background: #555 !important; border-radius: 6px !important; }}
    ::-webkit-scrollbar-thumb:hover {{ background: #707070 !important; }}
    .report-modal-overlay {{ background: rgba(0,0,0,.68) !important; backdrop-filter: none !important; z-index: 9999990 !important; }}
    .report-modal {{ background: #141414 !important; border: 1px solid #333 !important; border-radius: 10px !important; box-shadow: 0 16px 40px rgba(0,0,0,.45) !important; width: min(95vw, 650px) !important; max-width: 650px !important; padding: 20px 22px !important; }}
    .report-modal-top {{ display: flex; align-items: center; gap: 13px; margin-bottom: 16px; padding-bottom: 14px; border-bottom: 1px solid #262626; padding-right: 40px; }}
    .report-modal-icon {{ width: 40px; height: 40px; flex: 0 0 40px; background: #1f1f1f !important; border: 1px solid #333 !important; border-radius: 8px !important; box-shadow: none !important; }}
    .report-modal-icon svg {{ width: 20px; height: 20px; stroke: #8ab4f8; }}
    .report-eyebrow-row {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 2px; }}
    .report-eyebrow {{ color: #8ab4f8 !important; letter-spacing: .8px !important; font-size: 10px !important; margin: 0 !important; }}
    .report-source-badge {{ display: inline-flex; align-items: center; gap: 4px; padding: 1px 7px; border-radius: 4px; background: #1f242d; border: 1px solid #2f3b4c; color: #9bbef5; font-size: 10px; font-weight: 500; }}
    .report-modal h3 {{ font-size: 18px !important; color: #f1f3f4 !important; line-height: 1.25 !important; }}
    .report-subtitle {{ font-size: 11.5px !important; color: #9aa0a6 !important; margin-top: 2px !important; }}
    .report-close-btn {{ top: 18px !important; right: 18px !important; width: 30px !important; height: 30px !important; background: #1f1f1f !important; border: 1px solid #333 !important; border-radius: 6px !important; box-shadow: none !important; font-size: 18px !important; }}
    .report-close-btn:hover {{ background: #2b2b2b !important; border-color: #444 !important; }}
    .report-section {{ background: #191919 !important; border: 1px solid #2e2e2e !important; border-radius: 8px !important; padding: 13px 14px !important; margin: 0 0 10px !important; }}
    .report-section-heading {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }}
    .report-section-title-wrap {{ display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }}
    .report-section-title {{ color: #e8eaed; font-size: 13px; font-weight: 600; }}
    .report-section-help {{ color: #8a9099; font-size: 11px; margin: 0; }}
    .date-preset-group {{ display: inline-flex; align-items: center; gap: 4px; flex-wrap: wrap; }}
    .date-preset-btn {{ border: 1px solid #333; background: #202124; color: #9aa0a6; border-radius: 5px; padding: 3px 8px; font: 500 10.5px 'Prompt',sans-serif; cursor: pointer; transition: all .15s; }}
    .date-preset-btn:hover {{ color: #e8eaed; border-color: #4a4d52; background: #282a2d; }}
    .date-preset-btn.is-active {{ color: #8ab4f8; border-color: #3b5984; background: #1d2736; }}
    .date-input-wrap {{ position: relative; display: flex; align-items: center; }}
    .date-input {{ background: #141414 !important; border: 1px solid #333 !important; border-radius: 6px !important; padding: 8px 30px 8px 10px !important; font-size: 12px !important; min-height: 36px !important; color: #e8eaed !important; }}
    .date-input:hover {{ border-color: #444 !important; }}
    .date-input:focus {{ border-color: #8ab4f8 !important; box-shadow: none !important; }}
    .date-clear-btn {{ position: absolute; right: 8px; width: 18px; height: 18px; border: 0; border-radius: 4px; background: transparent; color: #858b93; font-size: 14px; line-height: 1; cursor: pointer; display: none; align-items: center; justify-content: center; }}
    .date-clear-btn.show {{ display: inline-flex; }}
    .date-clear-btn:hover {{ background: #2c2e33; color: #e8eaed; }}
    .report-worker-mode-row {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 0 0 9px; min-width: 0; }}
    .report-mode-title {{ color: #9aa0a6; font-size: 11.5px; flex: 0 0 auto; }}
    .report-worker-mode-switch {{ display: inline-flex; align-items: stretch; flex: 0 1 auto; min-width: 0; border: 1px solid #333; background: #141414; border-radius: 6px; padding: 2px; gap: 2px; }}
    .report-mode-btn {{ display: inline-flex; align-items: center; justify-content: center; gap: 4px; white-space: nowrap; border: 1px solid transparent; background: transparent; color: #8a9099; padding: 4px 9px; border-radius: 4px; font: 500 11px 'Prompt',sans-serif; cursor: pointer; }}
    .report-mode-btn span {{ color: inherit; font-weight: 400; }}
    .report-mode-btn:hover {{ color: #e8eaed; background: #202124; }}
    .report-mode-btn.is-active {{ background: #282a2d; border-color: #444; color: #e8eaed; }}
    .report-worker-picker {{ position: relative; width: 100%; }}
    .report-worker-tags {{ display: flex; align-items: center; align-content: flex-start; flex-wrap: wrap; gap: 5px; min-height: 38px; width: 100%; padding: 5px 8px; background: #141414; border: 1px solid #333; border-radius: 6px; transition: border-color .15s; cursor: text; }}
    .report-worker-tags:hover {{ border-color: #444; }}
    .report-worker-tags:focus-within {{ border-color: #8ab4f8; }}
    .worker-search-icon {{ width: 14px; height: 14px; fill: #777d84; flex: 0 0 14px; margin-right: 2px; pointer-events: none; }}
    .report-worker-tags .report-worker-search {{ width: auto; flex: 1 1 170px; min-width: 120px; min-height: 24px; padding: 2px 4px; font: 12px 'Prompt',sans-serif; color: #e8eaed; background: transparent; border: 0; outline: none; }}
    .worker-chip {{ display: inline-flex; align-items: center; gap: 5px; max-width: 100%; min-width: 0; padding: 3px 6px 3px 8px; border: 1px solid #3c4043; border-radius: 5px; background: #242629; color: #e8eaed; font-size: 11px; line-height: 1.3; }}
    .worker-chip-avatar {{ width: 16px; height: 16px; border-radius: 4px; background: #1a73e8; color: #fff; font-size: 9.5px; font-weight: 600; display: inline-flex; align-items: center; justify-content: center; flex: 0 0 16px; }}
    .worker-chip-label {{ min-width: 0; overflow-wrap: anywhere; }}
    .worker-chip-remove {{ display: inline-flex; align-items: center; justify-content: center; flex: 0 0 16px; width: 16px; height: 16px; border: 0; border-radius: 3px; background: transparent; color: #9aa0a6; cursor: pointer; font-size: 14px; line-height: 1; padding: 0; }}
    .worker-chip-remove:hover {{ background: #3c4043; color: #fff; }}
    .report-worker-dropdown {{ display: none; position: fixed; max-height: 240px; overflow-y: auto; z-index: 9999999; padding: 4px; background: #191919; border: 1px solid #3c4043; border-radius: 8px; box-shadow: 0 10px 28px rgba(0,0,0,.45); }}
    .report-worker-dropdown.show {{ display: block; }}
    .worker-dropdown-header {{ display: flex; align-items: center; justify-content: space-between; padding: 5px 8px 6px; border-bottom: 1px solid #2a2a2a; margin-bottom: 3px; font-size: 10.5px; color: #8a9099; }}
    .worker-dropdown-actions {{ display: inline-flex; gap: 8px; }}
    .worker-dropdown-action-link {{ border: 0; background: transparent; color: #8ab4f8; font: 500 10.5px 'Prompt',sans-serif; cursor: pointer; padding: 0; }}
    .worker-dropdown-action-link:hover {{ text-decoration: underline; }}
    .worker-dropdown-option {{ display: flex; align-items: center; gap: 8px; width: 100%; padding: 6px 8px; border: 1px solid transparent; border-radius: 5px; background: transparent; text-align: left; cursor: pointer; color: #e8eaed; margin-bottom: 1px; }}
    .worker-dropdown-option:hover {{ background: #242629; }}
    .worker-dropdown-option.is-selected {{ background: #1c2838; border-color: #2f496e; }}
    .worker-dropdown-option .worker-avatar {{ width: 24px; height: 24px; flex: 0 0 24px; border-radius: 5px; background: #282a2d; color: #c7cacf; font-size: 11px; font-weight: 600; display: flex; align-items: center; justify-content: center; border: 1px solid #3c4043; }}
    .worker-dropdown-option.is-selected .worker-avatar {{ background: #1a73e8; border-color: #4285f4; color: #fff; }}
    .worker-dropdown-info {{ flex: 1; min-width: 0; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }}
    .worker-dropdown-name {{ color: #e8eaed; font-size: 12px; line-height: 1.35; overflow-wrap: anywhere; }}
    .worker-dropdown-tag {{ font-size: 9.5px; padding: 1px 5px; border-radius: 3px; background: #242629; border: 1px solid #333; color: #8a9099; }}
    .worker-dropdown-count {{ font-size: 10.5px; color: #9aa0a6; background: #202124; border: 1px solid #333; border-radius: 4px; padding: 1px 6px; white-space: nowrap; }}
    .worker-dropdown-option.is-selected .worker-dropdown-count {{ background: #22354f; border-color: #395780; color: #b8d4ff; }}
    .worker-dropdown-mark {{ color: #8a9099; font-size: 14px; width: 18px; text-align: center; flex: 0 0 18px; font-weight: 600; }}
    .worker-dropdown-option.is-selected .worker-dropdown-mark {{ color: #8ab4f8; }}
    .report-selection-footer {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 8px !important; }}
    .report-selection-footer > span {{ min-width: 0; overflow-wrap: anywhere; line-height: 1.4; font-size: 11px; color: #8a9099; }}
    .report-footer-btns {{ display: inline-flex; align-items: center; gap: 5px; flex-shrink: 0; }}
    .report-mini-btn {{ border-radius: 5px !important; background: #202124 !important; border: 1px solid #333 !important; padding: 4px 9px !important; font-size: 10.5px !important; color: #c7cacf !important; }}
    .report-mini-btn:hover {{ background: #2b2c2f !important; border-color: #4a4d52 !important; color: #fff !important; }}

    /* Single-Bar Collapsible Advance Filter (แสดงเฉพาะแถบเดียว กดแล้วจึงกางตัวเลือกออก) */
    .adv-filter-container {{ margin: 0 0 10px; background: #191919; border: 1px solid #2e2e2e; border-radius: 8px; overflow: hidden; transition: border-color .15s; }}
    .adv-filter-container.is-open, .adv-filter-container.has-active {{ border-color: #3b5984; }}
    .adv-filter-trigger {{ width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 9px 13px; background: transparent; border: 0; color: #e8eaed; cursor: pointer; text-align: left; font: 500 12px 'Prompt',sans-serif; transition: background .15s; }}
    .adv-filter-trigger:hover {{ background: #202124; }}
    .adv-trigger-left {{ display: flex; align-items: center; gap: 8px; min-width: 0; flex-wrap: wrap; }}
    .adv-trigger-title {{ display: inline-flex; align-items: center; gap: 6px; font-weight: 600; color: #e8eaed; font-size: 12px; }}
    .adv-trigger-title svg {{ width: 14px; height: 14px; stroke: #8ab4f8; fill: none; stroke-width: 2; flex-shrink: 0; }}
    .adv-active-pill {{ display: inline-flex; align-items: center; padding: 1px 8px; border-radius: 4px; background: #202124; border: 1px solid #333; color: #9aa0a6; font-size: 10.5px; font-weight: 400; }}
    .adv-filter-container.has-active .adv-active-pill {{ background: #1d2736; border-color: #3b5984; color: #9bbef5; }}
    .adv-trigger-right {{ display: inline-flex; align-items: center; gap: 5px; color: #8a9099; font-size: 11px; white-space: nowrap; flex-shrink: 0; }}
    .adv-drawer {{ display: none; flex-direction: column; gap: 10px; padding: 11px 13px 13px; border-top: 1px solid #262626; background: #151515; }}
    .adv-drawer.show {{ display: flex; }}
    .adv-dedupe-row {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }}
    .adv-dedupe-label {{ font-size: 11.5px; color: #c7cacf; font-weight: 500; }}
    .adv-dedupe-group {{ display: inline-flex; align-items: center; background: #111; border: 1px solid #333; border-radius: 6px; padding: 2px; gap: 2px; flex-wrap: wrap; }}
    .adv-dedupe-btn {{ border: 1px solid transparent; background: transparent; color: #8a9099; padding: 4px 10px; border-radius: 4px; font: 500 11px 'Prompt',sans-serif; cursor: pointer; white-space: nowrap; transition: all .15s; }}
    .adv-dedupe-btn:hover {{ color: #e8eaed; background: #1e1f22; }}
    .adv-dedupe-btn.is-active {{ background: #253346; border-color: #3b5984; color: #9bbef5; }}
    .adv-grid-row {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }}
    .adv-field {{ display: flex; flex-direction: column; gap: 4px; min-width: 0; }}
    .adv-field label {{ font-size: 10.5px; color: #8a9099; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .adv-select {{ width: 100%; background: #191919; border: 1px solid #333; border-radius: 5px; color: #e8eaed; padding: 6px 8px; font: 11px 'Prompt',sans-serif; outline: none; cursor: pointer; }}
    .adv-select:hover {{ border-color: #444; }}
    .adv-select:focus {{ border-color: #8ab4f8; }}

    .report-export-summary {{ background: #191919 !important; border: 1px solid #2e2e2e !important; border-radius: 8px !important; padding: 10px 13px !important; margin: 0 0 14px !important; display: flex; align-items: center; justify-content: space-between; gap: 10px; }}
    .report-export-summary-left {{ display: flex; align-items: center; gap: 10px; min-width: 0; flex: 1; }}
    .report-export-summary-text {{ color: #e8eaed !important; font-size: 12px !important; font-weight: 500; line-height: 1.35; }}
    .report-export-subtext {{ color: #8a9099; font-size: 10.5px; margin-top: 2px; line-height: 1.35; }}
    .report-preview-count {{ background: #1d2736 !important; border: 1px solid #3b5984 !important; color: #9bbef5 !important; padding: 5px 11px !important; border-radius: 6px !important; font-size: 12.5px !important; font-weight: 600 !important; flex-shrink: 0; }}
    .report-preview-count.is-invalid {{ background: #3a2424 !important; border-color: #754343 !important; color: #f28b82 !important; white-space: normal !important; }}
    .report-btn {{ border-radius: 7px !important; box-shadow: none !important; transform: none !important; min-height: 40px !important; padding: 9px 14px !important; font-size: 12.5px !important; }}
    .report-btn-dl, .report-btn-dl:hover {{ background: #1a73e8 !important; border: 1px solid #2b83f6 !important; box-shadow: none !important; transform: none !important; }}
    .report-btn-cancel {{ background: #202124 !important; border: 1px solid #333 !important; color: #c7cacf !important; flex: 0 0 auto !important; min-width: 96px; }}
    .report-btn-cancel:hover {{ background: #282a2d !important; color: #fff !important; }}
    .report-note {{ font-size: 10.5px !important; color: #777d84 !important; margin: 9px 0 0 !important; }}
    .custom-alert-overlay {{ backdrop-filter: none !important; background: rgba(0,0,0,.65) !important; z-index: 10000000 !important; }}
    .custom-alert-box {{ background: #202124 !important; border: 1px solid #444 !important; border-radius: 10px !important; box-shadow: 0 12px 32px rgba(0,0,0,.35) !important; }}
    .custom-alert-btn {{ border-radius: 7px !important; box-shadow: none !important; }}
    @media (max-width: 768px) {{
      .report-worker-mode-row {{ gap: 6px; align-items: flex-start; flex-wrap: wrap; }}
      .report-worker-tags .report-worker-search {{ flex-basis: 120px; min-width: 105px; }}
      .report-selection-footer {{ flex-wrap: wrap; }}
      .adv-grid-row {{ grid-template-columns: 1fr; }}
      .report-export-summary {{ flex-wrap: wrap; }}
    }}

    </style>

    <!-- UI ปุ่มเปิด-ปิด -->
    <div id="standaloneReportBtn" class="top-action-btn standalone-report-btn" title="ดาวน์โหลดรายงาน Excel">
        <svg viewBox="0 0 24 24"><path d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/></svg>
    </div>
    <div id="standaloneFilterBtn" class="top-action-btn standalone-filter-btn" title="ตัวกรองสถานะ">
        <svg viewBox="0 0 24 24"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"></polygon></svg>
    </div>

    <!-- Export Center: กรองช่วงวันที่ ผู้ปฏิบัติงาน และ Advance Filter -->
    <div id="reportModalOverlay" class="report-modal-overlay">
        <div class="report-modal" role="dialog" aria-modal="true" aria-labelledby="reportModalTitle">
            <button class="report-close-btn" type="button" onclick="closeReportModal()" aria-label="ปิดหน้าต่าง">×</button>
            <div class="report-modal-top">
                <div class="report-modal-icon"><svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h8M8 9h2"/></svg></div>
                <div>
                    <div class="report-eyebrow-row">
                        <p class="report-eyebrow">SCADA · EXPORT CENTER</p>
                        <span class="report-source-badge" id="reportSourceBadge">Data_Check</span>
                    </div>
                    <h3 id="reportModalTitle">ส่งออกรายงานการตรวจสอบและแก้ไขอุปกรณ์</h3>
                    <p class="report-subtitle">กรองตามช่วงวันที่ รายชื่อผู้ปฏิบัติงาน (Google Form) และเงื่อนไขขั้นสูงเพื่อส่งออกรายงาน Excel (.xlsx)</p>
                </div>
            </div>

            <section class="report-section" aria-labelledby="reportDateHeading">
                <div class="report-section-heading">
                    <div class="report-section-title-wrap">
                        <span class="report-section-title" id="reportDateHeading">ช่วงวันที่เข้าตรวจสอบ</span>
                        <span class="report-section-help">เว้นว่างเพื่อรวมทุกวันที่</span>
                    </div>
                    <div class="date-preset-group" role="group" aria-label="ตัวเลือกช่วงวันที่ด่วน">
                        <button type="button" class="date-preset-btn is-active" data-preset="all" onclick="applyReportDatePreset('all')">ทุกวันที่</button>
                        <button type="button" class="date-preset-btn" data-preset="oct2026" onclick="applyReportDatePreset('oct2026')">ต.ค. 2026</button>
                        <button type="button" class="date-preset-btn" data-preset="sep2026" onclick="applyReportDatePreset('sep2026')">ก.ย. 2026</button>
                        <button type="button" class="date-preset-btn" data-preset="aug2026" onclick="applyReportDatePreset('aug2026')">ส.ค. 2026</button>
                    </div>
                </div>
                <div class="date-flex-container">
                    <div class="date-input-group">
                        <label for="reportStartDate">จากวันที่</label>
                        <div class="date-input-wrap">
                            <input type="text" id="reportStartDate" class="date-input" placeholder="เลือกวันที่เริ่มต้น" onfocus="this.type='date'; if(this.showPicker){{try{{this.showPicker();}}catch(e){{}}}}" onblur="if(!this.value) this.type='text'; updateReportPreview();" onchange="syncDatePresetButtons(); updateReportPreview();" oninput="syncDatePresetButtons(); updateReportPreview();">
                            <button type="button" id="clearStartDateBtn" class="date-clear-btn" onclick="clearSingleReportDate('reportStartDate')" title="ล้างวันที่เริ่มต้น">×</button>
                        </div>
                    </div>
                    <div class="date-input-group">
                        <label for="reportEndDate">ถึงวันที่</label>
                        <div class="date-input-wrap">
                            <input type="text" id="reportEndDate" class="date-input" placeholder="เลือกวันที่สิ้นสุด" onfocus="this.type='date'; if(this.showPicker){{try{{this.showPicker();}}catch(e){{}}}}" onblur="if(!this.value) this.type='text'; updateReportPreview();" onchange="syncDatePresetButtons(); updateReportPreview();" oninput="syncDatePresetButtons(); updateReportPreview();">
                            <button type="button" id="clearEndDateBtn" class="date-clear-btn" onclick="clearSingleReportDate('reportEndDate')" title="ล้างวันที่สิ้นสุด">×</button>
                        </div>
                    </div>
                </div>
            </section>

            <section class="report-section" aria-labelledby="reportWorkerHeading">
                <div class="report-section-heading">
                    <div class="report-section-title-wrap">
                        <span class="report-section-title" id="reportWorkerHeading">ผู้ปฏิบัติงาน (ผอส.ฉ.2)</span>
                        <span class="report-section-help">รายชื่อตาม Google Form และฐานข้อมูล Data_Check</span>
                    </div>
                    <span id="reportWorkerCount" class="report-worker-count">ยังไม่เลือกชื่อ</span>
                </div>
                <div class="report-worker-mode-row">
                    <span class="report-mode-title">ตรรกะการกรองรายชื่อ</span>
                    <div class="report-worker-mode-switch" role="group" aria-label="เงื่อนไขรายชื่อผู้ปฏิบัติงาน">
                        <button id="reportModeOr" class="report-mode-btn is-active" type="button" aria-pressed="true">OR <span>คนใดคนหนึ่ง</span></button>
                        <button id="reportModeAnd" class="report-mode-btn" type="button" aria-pressed="false">AND <span>ต้องครบทุกคน</span></button>
                    </div>
                </div>
                <div class="report-worker-picker" id="reportWorkerPicker">
                    <div class="report-worker-tags" id="reportWorkerTags">
                        <svg class="worker-search-icon" viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
                        <input id="reportWorkerSearch" class="report-worker-search" type="search" placeholder="พิมพ์ค้นหาชื่อ หรือคลิกเลือกผู้ปฏิบัติงาน..." autocomplete="off" aria-label="ค้นหาและเลือกผู้ปฏิบัติงาน">
                    </div>
                </div>
                <div class="report-selection-footer">
                    <span id="reportFilterSummary">ยังไม่เลือกชื่อ — ส่งออกรายการของทุกคน</span>
                    <div class="report-footer-btns">
                        <button id="selectAllReportWorkers" class="report-mini-btn" type="button">เลือกทั้งหมด</button>
                        <button id="clearReportWorkers" class="report-mini-btn" type="button">ล้างชื่อที่เลือก</button>
                    </div>
                </div>
            </section>

            <!-- Single-Bar Collapsible Advance Filter (แสดงเป็นแถบเดียว กดแล้วจึงแสดงตัวเลือกขึ้นมา) -->
            <div id="advFilterContainer" class="adv-filter-container">
                <button type="button" id="advExpandBtn" class="adv-filter-trigger" onclick="toggleAdvFilterDrawer()" aria-expanded="false">
                    <div class="adv-trigger-left">
                        <span class="adv-trigger-title">
                            <svg viewBox="0 0 24 24"><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/></svg>
                            Advance Filter
                        </span>
                        <span id="advSummaryPill" class="adv-active-pill">รวมข้อมูลซ้ำทั้งหมด · มาตรฐาน</span>
                    </div>
                    <span id="advExpandLabel" class="adv-trigger-right">ตั้งค่าตัวกรอง ▾</span>
                </button>
                <div id="advFilterDrawer" class="adv-drawer">
                    <div class="adv-dedupe-row">
                        <span class="adv-dedupe-label">การจัดการข้อมูลซ้ำ (Site ID):</span>
                        <div class="adv-dedupe-group" role="group" aria-label="การจัดการข้อมูลอุปกรณ์ซ้ำ">
                            <button type="button" id="advDedupeAll" class="adv-dedupe-btn is-active" onclick="setAdvDedupeMode('all')">รวมทั้งหมด</button>
                            <button type="button" id="advDedupeLatest" class="adv-dedupe-btn" onclick="setAdvDedupeMode('latest')">ไม่เอาซ้ำ · เอาตัวล่าสุด</button>
                            <button type="button" id="advDedupeEarliest" class="adv-dedupe-btn" onclick="setAdvDedupeMode('earliest')">ไม่เอาซ้ำ · เอาตัวแรก</button>
                        </div>
                    </div>
                    <div class="adv-grid-row">
                        <div class="adv-field">
                            <label for="advStatusFilter">สถานะหลังดำเนินการ</label>
                            <select id="advStatusFilter" class="adv-select" onchange="updateReportPreview()">
                                <option value="all">ทุกสถานะ (ทั้งหมด)</option>
                                <option value="online">เฉพาะ Online</option>
                                <option value="not_online">เฉพาะที่ยังไม่ Online</option>
                                <option value="connecting">เฉพาะ Connecting</option>
                                <option value="offline">เฉพาะ Offline</option>
                            </select>
                        </div>
                        <div class="adv-field">
                            <label for="advRespFilter">ผู้รับผิดชอบดำเนินการต่อ</label>
                            <select id="advRespFilter" class="adv-select" onchange="updateReportPreview()">
                                <option value="all">ทุกหน่วยงาน (ทั้งหมด)</option>
                                <option value="bo">รอ ผบอ.กบษ.ฉ.2</option>
                                <option value="po">รอ ผอส.กสฟ.ฉ.2</option>
                                <option value="done">แก้ไขเรียบร้อยแล้ว (-)</option>
                            </select>
                        </div>
                        <div class="adv-field">
                            <label for="advSortOrder">เรียงลำดับในรายงาน Excel</label>
                            <select id="advSortOrder" class="adv-select" onchange="updateReportPreview()">
                                <option value="status_online">สถานะ (Online ขึ้นก่อน · มาตรฐาน)</option>
                                <option value="date_desc">วันที่ตรวจสอบ (ล่าสุด → เก่าสุด)</option>
                                <option value="date_asc">วันที่ตรวจสอบ (เก่าสุด → ล่าสุด)</option>
                                <option value="site_asc">รหัส Site ID (A → Z)</option>
                            </select>
                        </div>
                    </div>
                </div>
            </div>

            <div class="report-export-summary">
                <div class="report-export-summary-left">
                    <svg viewBox="0 0 24 24" width="18" height="18" stroke="#8ab4f8" fill="none" stroke-width="2"><path d="M12 8v4l2.5 2.5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"/></svg>
                    <div>
                        <div class="report-export-summary-text">ตัวอย่างจำนวนรายการที่จะส่งออกตามตัวกรองปัจจุบัน</div>
                        <div id="reportPreviewSubtext" class="report-export-subtext">อ้างอิงข้อมูลจากแท็บ Data_Check เชื่อมรายละเอียดอุปกรณ์ในระบบจาก ชีต1</div>
                    </div>
                </div>
                <div id="reportPreviewCount" class="report-preview-count">กำลังตรวจสอบ...</div>
            </div>

            <div class="report-btn-group">
                <button class="report-btn report-btn-cancel" type="button" onclick="resetAllReportFilters()">รีเซ็ตค่า</button>
                <button class="report-btn report-btn-cancel" type="button" onclick="closeReportModal()">ยกเลิก</button>
                <button id="execReportBtn" class="report-btn report-btn-dl" type="button" onclick="generateExcelReport()">
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="white" stroke-width="2"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg> ดาวน์โหลด Excel (.xlsx)
                </button>
            </div>
            <p class="report-note">* ข้อมูลอัปเดตจาก Google Sheets ลงฐานข้อมูล SQLite อัตโนมัติ สามารถกดที่แถบ Advance Filter เพื่อตั้งค่าตัดข้อมูลซ้ำ (เอาตัวล่าสุด) ได้</p>
        </div>
        <div id="reportWorkerDropdown" class="report-worker-dropdown" role="listbox" aria-label="ผลการค้นหาผู้ปฏิบัติงาน"></div>
    </div>

    <!-- Modal แจ้งเตือนแบบ Custom -->
    <div id="customAlertOverlay" class="custom-alert-overlay">
        <div class="custom-alert-box">
            <div class="custom-alert-icon">
                <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
            </div>
            <div class="custom-alert-title">แจ้งเตือน</div>
            <div class="custom-alert-msg" id="customAlertMsg">ไม่พบข้อมูลในช่วงวันที่ที่คุณเลือกครับ</div>
            <button class="custom-alert-btn" onclick="document.getElementById('customAlertOverlay').classList.remove('show');">ตกลง</button>
        </div>
    </div>

    <div id="customFilterWrapper" class="custom-filter-wrapper"></div>
    <div id="customInfoPanel" class="custom-info-panel">
        <div class="panel-close-btn" id="closeInfoPanelBtn" onclick="hideCustomPanel();"><svg viewBox="0 0 24 24" width="18" height="18" fill="white"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg></div>
        <div id="customInfoContent" style="display:flex; flex-direction:column; height:100%; width:100%;"></div>
    </div>
    <div class="g-search-container">
        <div class="g-search-box" id="searchBox">
            <div class="g-search-icon"><svg focusable="false" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" style="width:20px; height:20px;"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"></path></svg></div>
            <input type="text" id="searchInput" class="g-search-input" placeholder="ค้นหา Site ID, รหัสสั่งการ, หรือ สถานที่..." autocomplete="off">
            <div class="g-search-clear" id="searchClear" onclick="this.previousElementSibling.value=''; this.style.display='none'; document.getElementById('searchResults').style.display='none'; hideCustomPanel();">×</div>
        </div>
        <div id="searchResults" class="g-search-results"></div>
    </div>
    <div class="g-layer-container" id="gLayerContainer">
        <div class="g-layer-main-btn" id="gLayerMainBtn"><div class="g-layer-label" id="gLayerMainLabel">...</div></div>
        <div class="g-layer-panel" id="gLayerPanel"></div>
    </div>

    <script>
    var statusHashMap = {status_hash_json};
    var expDataCompact = {export_json};
    var sData = {search_json};
    var reportRawData = {report_json_data};
    var baseLayerNames = {base_layer_names_json};
    var currentHoveredSelector = null;
    var activeHoverElements = [];
    window.currentSelectedSafeId = null;

    var DISPLAY_FIELD_NAMES = [
        "รหัสสั่งการ",
        "สถานที่",
        "State SCADA",
        "State SCADA (หลังตรวจสอบ)",
        "ชนิดอุปกรณ์",
        "รายละเอียดการแก้ไขข้อขัดข้อง",
        "รายละเอียดการเข้าแก้ไข",
        "LAT/LONG",
        "รอ ผอส. เข้าแก้ไข",
        "รอ ผบอ. เข้าแก้ไข",
        "การไฟฟ้า",
        "วันที่เข้าตรวจสอบ"
    ];

    var EXP_ALL_KEYS = ["_layerName", "Site ID"].concat(DISPLAY_FIELD_NAMES);
    var expData = expDataCompact.map(function(arr) {{
        var obj = {{ "_layerName": arr[0] || "", "Site ID": arr[1] || "" }};
        for (var i = 0; i < DISPLAY_FIELD_NAMES.length; i++) {{
            var v = arr[i + 2];
            obj[DISPLAY_FIELD_NAMES[i]] = (v !== undefined && v !== "") ? v : "ไม่มีค่า";
        }}
        return obj;
    }});

    var stationBySafeId = Object.create(null);
    sData.forEach(function(item) {{
        stationBySafeId[item.safe_id] = item;
    }});

    var GOOGLE_MAP_SVG = '<svg viewBox="0 0 24 24" width="15" height="15" fill="white" style="margin-right:6px;"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg>';
    var APPLE_MAP_SVG = '<svg viewBox="0 0 384 512" style="width: 13px; height: auto; margin-right: 6px; margin-bottom: 2px;" fill="white"><path d="M318.7 268.7c-.2-36.7 16.4-64.4 50-84.8-18.8-26.9-47.2-41.7-84.7-44.6-35.5-2.8-74.3 20.7-88.5 20.7-15 0-49.4-19.7-76.4-19.7C63.3 141.2 4 184.8 4 273.5q0 39.3 14.4 81.2c12.8 36.7 59 126.7 107.2 125.2 25.2-.6 43-17.9 75.8-17.9 31.8 0 48.3 17.9 76.4 17.3 48.6-.7 90.4-84.3 103-119.3-34.6-18.6-53.6-45.9-52.7-81.5zM266.4 87.8C282.5 67 292.7 39.2 292.7 11.5c0-3.3-.2-5.5-.6-7.8-33.1 1.4-62.7 20-80.8 42.1-15 18.5-26.4 46.1-24.9 71.6 3.3.4 5.5.6 7.8.6 30.5-.3 57.7-18.7 71.6-40.2z"/></svg>';

    function buildStationPopupHtml(safeId) {{
        var m = stationBySafeId[safeId];
        if (!m) return '';
        var row = expData[m.idx] || {{}};
        var rowsHtml = '';
        for (var i = 0; i < DISPLAY_FIELD_NAMES.length; i++) {{
            var fName = DISPLAY_FIELD_NAMES[i];
            var fVal = row[fName] !== undefined ? row[fName] : 'ไม่มีค่า';
            rowsHtml += '<tr><td>' + fName + '</td><td>' + fVal + '</td></tr>';
        }}
        var gUrl = 'https://www.google.com/maps/dir/?api=1&destination=' + m.lat + ',' + m.lon;
        var aUrl = 'http://maps.apple.com/?daddr=' + m.lat + ',' + m.lon;
        return '<div class="custom-popup-card" data-safe-id="' + m.safe_id + '">' +
            '<div class="popup-header" style="background-color:' + m.color + ';">' +
                '<div class="popup-icon"><i class="fa fa-' + m.icon + '"></i></div>' +
                '<div class="popup-title"><h3>' + m.id + '</h3><span>' + m.status + '</span></div>' +
            '</div>' +
            '<div class="popup-actions">' +
                '<a href="' + gUrl + '" target="_blank" class="btn-map btn-google">' + GOOGLE_MAP_SVG + ' Google Maps</a>' +
                '<a href="' + aUrl + '" target="_blank" class="btn-map btn-apple">' + APPLE_MAP_SVG + ' Apple Maps</a>' +
            '</div>' +
            '<div class="popup-body"><table class="popup-table">' + rowsHtml + '</table></div>' +
        '</div>';
    }}

    var GOOGLE_FORM_WORKER_LIST = [
        "นายณัฐกานต์ ป้อมไชยา",
        "นางปนัดดา ชาติสมร",
        "นายวรรณพงษ์ คลังทอง",
        "นายพนมสินธุ์ ผาสุข",
        "นายประเดิม อิ่มโค่น"
    ];

    var JS_WORKER_ALIASES = {{
        "ณัฐกานต์": "นายณัฐกานต์ ป้อมไชยา",
        "ปนัดดา": "นางปนัดดา ชาติสมร",
        "วรรณพงษ์": "นายวรรณพงษ์ คลังทอง",
        "ณพงษ์ คลังทอง": "นายวรรณพงษ์ คลังทอง",
        "พนมสินธุ์": "นายพนมสินธุ์ ผาสุข",
        "ประเดิม": "นายประเดิม อิ่มโค่น",
        "คณิน": "นายคณิน วาจนสุนทร"
    }};

    function getWorkerInitial(name) {{
        var clean = String(name || '').replace(/^(นาย|นางสาว|นาง|น\\.ส\\.|ว่าที่\\s*ร\\.ต\\.|จ\\.ส\\.อ\\.)\\s*/g, '').trim();
        return clean ? clean.charAt(0) : (String(name || '?').charAt(0) || '?');
    }}

    function closeReportWorkerDropdown() {{
        var dropdown = document.getElementById('reportWorkerDropdown');
        if (dropdown) dropdown.classList.remove('show');
    }}

    function closeFloatingSurfaces() {{
        var fw = document.getElementById('customFilterWrapper');
        if (fw) fw.classList.remove('show');
        if (typeof closeReportModal === 'function') closeReportModal();
        var alertOverlay = document.getElementById('customAlertOverlay');
        if (alertOverlay) alertOverlay.classList.remove('show');
        var results = document.getElementById('searchResults');
        if (results) results.style.display = 'none';
        var layerPanel = document.getElementById('gLayerPanel');
        if (layerPanel) layerPanel.classList.remove('show');
        var box = document.getElementById('searchBox');
        if (box) box.classList.remove('focus');
        closeReportWorkerDropdown();
        if (typeof hideCustomPanel === 'function') hideCustomPanel();
    }}

    document.addEventListener('keydown', function(e) {{
        if (e.key === 'Escape' || e.keyCode === 27) {{
            closeFloatingSurfaces();
        }}
    }});

    function showCustomAlert(msg) {{
        document.getElementById('customAlertMsg').innerText = msg;
        document.getElementById('customAlertOverlay').classList.add('show');
    }}

    var reportModal = document.getElementById('reportModalOverlay');
    var reportBtn = document.getElementById('standaloneReportBtn');

    if (reportBtn) {{
        reportBtn.addEventListener('click', function(e) {{
            e.preventDefault(); e.stopPropagation();
            var fw = document.getElementById('customFilterWrapper'); if (fw) fw.classList.remove('show');
            document.getElementById('customAlertOverlay').classList.remove('show');
            document.getElementById('searchResults').style.display = 'none';
            document.getElementById('gLayerPanel').classList.remove('show');
            closeReportWorkerDropdown();
            hideCustomPanel();
            var sd = document.getElementById('reportStartDate');
            var ed = document.getElementById('reportEndDate');
            sd.value = ''; sd.type = 'text';
            ed.value = ''; ed.type = 'text';
            var drawer = document.getElementById('advFilterDrawer');
            var container = document.getElementById('advFilterContainer');
            var expandBtn = document.getElementById('advExpandBtn');
            if (drawer) drawer.classList.remove('show');
            if (container) container.classList.remove('is-open');
            if (expandBtn) expandBtn.setAttribute('aria-expanded', 'false');
            reportModal.classList.add('show');
            initializeReportWorkerFilter();
        }});
    }}

    function closeReportModal() {{ if (reportModal) reportModal.classList.remove('show'); closeReportWorkerDropdown(); }}

    function normalizeWorkerToken(value) {{
        var s = String(value || '').normalize('NFC').trim().replace(/\\s+/g, ' ');
        if (!s) return '';
        for (var aliasKey in JS_WORKER_ALIASES) {{
            if (s.includes(aliasKey)) return JS_WORKER_ALIASES[aliasKey];
        }}
        return s;
    }}

    function splitOperatorNames(value) {{
        if (!value) return [];
        var seen = new Set();
        var result = [];
        String(value).split(/[,;|\\n\\r]+/).forEach(function(raw) {{
            var name = normalizeWorkerToken(raw);
            var ln = name.toLowerCase();
            if (name && ln !== '-' && ln !== 'ไม่มีค่า' && ln !== 'nan' && ln !== 'none' && !seen.has(name)) {{
                seen.add(name);
                result.push(name);
            }}
        }});
        return result;
    }}

    function workerMatchesName(rowWorkerList, targetName) {{
        var normTarget = normalizeWorkerToken(targetName);
        if (!normTarget) return false;
        return rowWorkerList.some(function(rw) {{
            var normRw = normalizeWorkerToken(rw);
            return normRw === normTarget || normRw.includes(normTarget) || normTarget.includes(normRw);
        }});
    }}

    var reportWorkerNames = [];
    var selectedReportWorkers = new Set();
    var reportWorkerMatchMode = 'OR';
    var advDedupeMode = 'all'; // 'all' | 'latest' | 'earliest'

    function getSelectedReportWorkers() {{
        return Array.from(selectedReportWorkers);
    }}

    function parseLocalReportDate(value) {{
        if (!value) return null;
        var str = String(value).trim();
        var y = 0, m = 0, d = 0;
        if (/^\\d{{4}}-\\d{{1,2}}-\\d{{1,2}}$/.test(str)) {{
            var p1 = str.split('-').map(Number);
            y = p1[0]; m = p1[1]; d = p1[2];
        }} else if (/^\\d{{1,2}}\\/\\d{{1,2}}\\/\\d{{4}}$/.test(str)) {{
            var p2 = str.split('/').map(Number);
            d = p2[0]; m = p2[1]; y = p2[2];
        }} else {{
            return null;
        }}
        if (y > 2400) y -= 543;
        var date = new Date(y, m - 1, d);
        if (date.getFullYear() !== y || date.getMonth() !== m - 1 || date.getDate() !== d) return null;
        return date;
    }}

    function validateReportDateRange(startInput, endInput) {{
        var startDate = parseLocalReportDate(startInput);
        var endDate = parseLocalReportDate(endInput);
        if (startInput && !startDate) return 'วันที่เริ่มต้นไม่ถูกต้อง กรุณาเลือกวันที่จากปฏิทิน';
        if (endInput && !endDate) return 'วันที่สิ้นสุดไม่ถูกต้อง กรุณาเลือกวันที่จากปฏิทิน';
        if (startDate && endDate && startDate > endDate) return 'วันที่เริ่มต้นต้องไม่มากกว่าวันที่สิ้นสุด';
        return '';
    }}

    function matchesBaseRowFilters(row, startInput, endInput, selectedWorkers, statusFilter, respFilter) {{
        var dateMatch = true;
        if (startInput || endInput) {{
            if (!row.iso_date) {{
                dateMatch = false;
            }} else {{
                var rowDate = parseLocalReportDate(row.iso_date);
                var startDate = parseLocalReportDate(startInput);
                var endDate = parseLocalReportDate(endInput);
                if (!rowDate) {{
                    dateMatch = false;
                }} else {{
                    if (startDate && rowDate < startDate) dateMatch = false;
                    if (endDate && rowDate > endDate) dateMatch = false;
                }}
            }}
        }}
        if (!dateMatch) return false;

        if (statusFilter && statusFilter !== 'all') {{
            var stUpper = String(row.status || '').toUpperCase();
            if (statusFilter === 'online' && !stUpper.includes('ONLINE')) return false;
            if (statusFilter === 'not_online' && stUpper.includes('ONLINE')) return false;
            if (statusFilter === 'connecting' && !stUpper.includes('CONNECTING')) return false;
            if (statusFilter === 'offline' && !stUpper.includes('OFFLINE')) return false;
        }}

        if (respFilter && respFilter !== 'all') {{
            var respStr = String(row.resp || '-').trim();
            if (respFilter === 'bo' && !respStr.includes('ผบอ')) return false;
            if (respFilter === 'po' && !respStr.includes('ผอส')) return false;
            if (respFilter === 'done' && respStr !== '-' && respStr !== '') return false;
        }}

        var workerMatch = true;
        if (selectedWorkers && selectedWorkers.length > 0) {{
            var rowWorkers = splitOperatorNames(row.workers);
            if (rowWorkers.length === 0) {{
                workerMatch = false;
            }} else if (reportWorkerMatchMode === 'AND') {{
                workerMatch = selectedWorkers.every(function(selectedName) {{
                    return workerMatchesName(rowWorkers, selectedName);
                }});
            }} else {{
                workerMatch = selectedWorkers.some(function(selectedName) {{
                    return workerMatchesName(rowWorkers, selectedName);
                }});
            }}
        }}

        return workerMatch;
    }}

    function getStationUniqueKey(row) {{
        var s = String(row.site || '').trim().toUpperCase();
        if (s && s !== '-' && s !== 'NONE' && s !== 'NAN') return 'SITE:' + s;
        var c = String(row.cmd || '').trim().toUpperCase();
        if (c && c !== '-' && c !== 'NONE' && c !== 'NAN') return 'CMD:' + c;
        return 'SEQ:' + String(row.seq || 0);
    }}

    function compareRowChronology(a, b) {{
        var dA = String(a.iso_date || '');
        var dB = String(b.iso_date || '');
        if (dA !== dB) return dA.localeCompare(dB);
        var seqA = typeof a.seq === 'number' ? a.seq : 0;
        var seqB = typeof b.seq === 'number' ? b.seq : 0;
        return seqA - seqB;
    }}

    function deduplicateReportRows(rows, mode) {{
        if (!mode || mode === 'all') return rows.slice();
        var stationMap = new Map();
        rows.forEach(function(row) {{
            var key = getStationUniqueKey(row);
            if (!stationMap.has(key)) {{
                stationMap.set(key, row);
            }} else {{
                var existing = stationMap.get(key);
                var cmp = compareRowChronology(row, existing);
                if (mode === 'latest' && cmp >= 0) {{
                    stationMap.set(key, row);
                }} else if (mode === 'earliest' && cmp < 0) {{
                    stationMap.set(key, row);
                }}
            }}
        }});
        return Array.from(stationMap.values());
    }}

    function sortReportRows(rows, sortMode) {{
        var copy = rows.slice();
        if (sortMode === 'date_desc') {{
            copy.sort(function(a, b) {{ return compareRowChronology(b, a); }});
        }} else if (sortMode === 'date_asc') {{
            copy.sort(function(a, b) {{ return compareRowChronology(a, b); }});
        }} else if (sortMode === 'site_asc') {{
            copy.sort(function(a, b) {{
                return String(a.site || '').localeCompare(String(b.site || ''), 'en', {{ numeric: true, sensitivity: 'base' }});
            }});
        }} else {{
            copy.sort(function(a, b) {{
                var sA = String(a.status || '').toUpperCase();
                var sB = String(b.status || '').toUpperCase();
                if (sA.includes("ONLINE") && !sB.includes("ONLINE")) return -1;
                if (!sA.includes("ONLINE") && sB.includes("ONLINE")) return 1;
                var sc = sA.localeCompare(sB);
                if (sc !== 0) return sc;
                return String(a.site || '').localeCompare(String(b.site || ''), 'en', {{ numeric: true, sensitivity: 'base' }});
            }});
        }}
        return copy;
    }}

    function getCurrentFilterParams() {{
        var startEl = document.getElementById('reportStartDate');
        var endEl = document.getElementById('reportEndDate');
        var statusEl = document.getElementById('advStatusFilter');
        var respEl = document.getElementById('advRespFilter');
        var sortEl = document.getElementById('advSortOrder');
        return {{
            startInput: startEl ? startEl.value.trim() : '',
            endInput: endEl ? endEl.value.trim() : '',
            selectedWorkers: getSelectedReportWorkers(),
            statusFilter: statusEl ? statusEl.value : 'all',
            respFilter: respEl ? respEl.value : 'all',
            sortOrder: sortEl ? sortEl.value : 'status_online',
            dedupeMode: advDedupeMode
        }};
    }}

    function getFilteredReportResult(customWorkersOverride) {{
        var params = getCurrentFilterParams();
        var workersToUse = customWorkersOverride !== undefined ? customWorkersOverride : params.selectedWorkers;
        var baseMatched = reportRawData.filter(function(row) {{
            return matchesBaseRowFilters(row, params.startInput, params.endInput, workersToUse, params.statusFilter, params.respFilter);
        }});
        var uniqueLatest = deduplicateReportRows(baseMatched, 'latest');
        var finalRows = deduplicateReportRows(baseMatched, params.dedupeMode);
        return {{
            rawMatchedCount: baseMatched.length,
            uniqueStationCount: uniqueLatest.length,
            duplicateCount: Math.max(0, baseMatched.length - uniqueLatest.length),
            rows: sortReportRows(finalRows, params.sortOrder)
        }};
    }}

    function setAdvDedupeMode(mode) {{
        advDedupeMode = (mode === 'latest' || mode === 'earliest') ? mode : 'all';
        var btnAll = document.getElementById('advDedupeAll');
        var btnLatest = document.getElementById('advDedupeLatest');
        var btnEarliest = document.getElementById('advDedupeEarliest');
        if (btnAll) btnAll.classList.toggle('is-active', advDedupeMode === 'all');
        if (btnLatest) btnLatest.classList.toggle('is-active', advDedupeMode === 'latest');
        if (btnEarliest) btnEarliest.classList.toggle('is-active', advDedupeMode === 'earliest');
        updateAdvExpandBadge();
        updateReportPreview();
        if (document.getElementById('reportWorkerDropdown').classList.contains('show')) renderReportWorkerDropdown();
    }}

    function toggleAdvFilterDrawer() {{
        var drawer = document.getElementById('advFilterDrawer');
        var container = document.getElementById('advFilterContainer');
        var btn = document.getElementById('advExpandBtn');
        if (!drawer || !btn) return;
        var isShowing = drawer.classList.toggle('show');
        if (container) container.classList.toggle('is-open', isShowing);
        btn.setAttribute('aria-expanded', isShowing ? 'true' : 'false');
        updateAdvExpandBadge();
    }}

    function updateAdvExpandBadge() {{
        var container = document.getElementById('advFilterContainer');
        var label = document.getElementById('advExpandLabel');
        var pill = document.getElementById('advSummaryPill');
        var drawer = document.getElementById('advFilterDrawer');
        var params = getCurrentFilterParams();

        var parts = [];
        if (params.dedupeMode === 'latest') parts.push('ไม่เอาซ้ำ (เอาตัวล่าสุด)');
        else if (params.dedupeMode === 'earliest') parts.push('ไม่เอาซ้ำ (เอาตัวแรก)');
        else parts.push('รวมข้อมูลซ้ำทั้งหมด');

        if (params.statusFilter !== 'all') parts.push('กรองสถานะ');
        if (params.respFilter !== 'all') parts.push('กรองผู้รับผิดชอบ');
        if (params.sortOrder !== 'status_online') parts.push('เรียงลำดับกำหนดเอง');

        var hasActive = params.dedupeMode !== 'all' || params.statusFilter !== 'all' || params.respFilter !== 'all' || params.sortOrder !== 'status_online';
        if (pill) pill.textContent = parts.join(' · ');
        if (container) container.classList.toggle('has-active', hasActive);

        var isOpen = drawer && drawer.classList.contains('show');
        if (label) label.textContent = isOpen ? 'ซ่อนตัวกรอง ▴' : 'ตั้งค่าตัวกรอง ▾';
    }}

    function syncDatePresetButtons() {{
        var startEl = document.getElementById('reportStartDate');
        var endEl = document.getElementById('reportEndDate');
        var s = startEl ? startEl.value.trim() : '';
        var e = endEl ? endEl.value.trim() : '';
        var clearS = document.getElementById('clearStartDateBtn');
        var clearE = document.getElementById('clearEndDateBtn');
        if (clearS) clearS.classList.toggle('show', Boolean(s));
        if (clearE) clearE.classList.toggle('show', Boolean(e));

        var activePreset = '';
        if (!s && !e) activePreset = 'all';
        else if (s === '2026-10-01' && e === '2026-10-31') activePreset = 'oct2026';
        else if (s === '2026-09-01' && e === '2026-09-30') activePreset = 'sep2026';
        else if (s === '2026-08-01' && e === '2026-08-31') activePreset = 'aug2026';

        document.querySelectorAll('.date-preset-btn').forEach(function(btn) {{
            btn.classList.toggle('is-active', btn.getAttribute('data-preset') === activePreset);
        }});
    }}

    function applyReportDatePreset(preset) {{
        var sd = document.getElementById('reportStartDate');
        var ed = document.getElementById('reportEndDate');
        if (!sd || !ed) return;
        if (preset === 'all') {{
            sd.value = ''; sd.type = 'text';
            ed.value = ''; ed.type = 'text';
        }} else if (preset === 'oct2026') {{
            sd.type = 'date'; sd.value = '2026-10-01';
            ed.type = 'date'; ed.value = '2026-10-31';
        }} else if (preset === 'sep2026') {{
            sd.type = 'date'; sd.value = '2026-09-01';
            ed.type = 'date'; ed.value = '2026-09-30';
        }} else if (preset === 'aug2026') {{
            sd.type = 'date'; sd.value = '2026-08-01';
            ed.type = 'date'; ed.value = '2026-08-31';
        }}
        syncDatePresetButtons();
        updateReportPreview();
        if (document.getElementById('reportWorkerDropdown').classList.contains('show')) renderReportWorkerDropdown();
    }}

    function clearSingleReportDate(inputId) {{
        var el = document.getElementById(inputId);
        if (!el) return;
        el.value = '';
        el.type = 'text';
        syncDatePresetButtons();
        updateReportPreview();
        if (document.getElementById('reportWorkerDropdown').classList.contains('show')) renderReportWorkerDropdown();
    }}

    function resetAllReportFilters() {{
        var sd = document.getElementById('reportStartDate');
        var ed = document.getElementById('reportEndDate');
        if (sd) {{ sd.value = ''; sd.type = 'text'; }}
        if (ed) {{ ed.value = ''; ed.type = 'text'; }}
        var statusEl = document.getElementById('advStatusFilter');
        var respEl = document.getElementById('advRespFilter');
        var sortEl = document.getElementById('advSortOrder');
        if (statusEl) statusEl.value = 'all';
        if (respEl) respEl.value = 'all';
        if (sortEl) sortEl.value = 'status_online';
        advDedupeMode = 'all';
        setAdvDedupeMode('all');
        initializeReportWorkerFilter();
        syncDatePresetButtons();
    }}

    function updateReportPreview() {{
        var params = getCurrentFilterParams();
        var validationMessage = validateReportDateRange(params.startInput, params.endInput);

        var result = validationMessage ? {{ rawMatchedCount: 0, uniqueStationCount: 0, duplicateCount: 0, rows: [] }} : getFilteredReportResult();
        var matchingCount = result.rows.length;
        var totalNames = reportWorkerNames.length;

        var countLabel = document.getElementById('reportWorkerCount');
        var summaryLabel = document.getElementById('reportFilterSummary');
        var previewLabel = document.getElementById('reportPreviewCount');
        var subtextLabel = document.getElementById('reportPreviewSubtext');
        var sourceBadge = document.getElementById('reportSourceBadge');

        if (sourceBadge) {{
            sourceBadge.textContent = 'Data_Check · ' + new Intl.NumberFormat('th-TH').format(reportRawData.length) + ' รายการ';
        }}
        if (countLabel) {{
            countLabel.textContent = params.selectedWorkers.length
                ? ('เลือกแล้ว ' + params.selectedWorkers.length + ' คน')
                : ('รายชื่อทั้งหมด ' + totalNames + ' คน');
        }}
        if (summaryLabel) {{
            if (!params.selectedWorkers.length) {{
                summaryLabel.textContent = 'ยังไม่เลือกชื่อ — ส่งออกรายการของทุกคนตามเงื่อนไขที่กำหนด';
            }} else {{
                summaryLabel.textContent = 'เลือก ' + params.selectedWorkers.join(', ') + ' · ' + (reportWorkerMatchMode === 'AND' ? 'ต้องมีชื่อครบทุกคน (AND)' : 'มีชื่อคนใดคนหนึ่ง (OR)');
            }}
        }}
        if (previewLabel) {{
            previewLabel.textContent = validationMessage || (new Intl.NumberFormat('th-TH').format(matchingCount) + ' รายการ');
            previewLabel.classList.toggle('is-invalid', Boolean(validationMessage));
        }}
        if (subtextLabel) {{
            if (validationMessage) {{
                subtextLabel.textContent = 'กรุณาตรวจสอบช่วงวันที่เริ่มต้นและสิ้นสุดอีกครั้ง';
            }} else if (params.dedupeMode === 'latest') {{
                subtextLabel.textContent = 'โหมดไม่เอาข้อมูลซ้ำ (เอาตัวล่าสุด) · ตัดรายการอุปกรณ์ซ้ำออก ' + result.duplicateCount + ' รายการ (จาก ' + result.rawMatchedCount + ' รายการ)';
            }} else if (params.dedupeMode === 'earliest') {{
                subtextLabel.textContent = 'โหมดไม่เอาข้อมูลซ้ำ (เอาตัวแรกสุด) · ตัดรายการอุปกรณ์ซ้ำออก ' + result.duplicateCount + ' รายการ (จาก ' + result.rawMatchedCount + ' รายการ)';
            }} else {{
                subtextLabel.textContent = 'โหมดรวมทุกรายการตาม Data_Check (' + result.rawMatchedCount + ' รายการ · อุปกรณ์ไม่ซ้ำ ' + result.uniqueStationCount + ' อุปกรณ์, ซ้ำ ' + result.duplicateCount + ' รายการ)';
            }}
        }}
        updateAdvExpandBadge();
    }}

    function positionReportWorkerDropdown() {{
        var picker = document.getElementById('reportWorkerPicker');
        var dropdown = document.getElementById('reportWorkerDropdown');
        if (!picker || !dropdown) return;
        var rect = picker.getBoundingClientRect();
        var dropdownHeight = Math.min(240, Math.max(120, window.innerHeight - 24));
        var top = rect.bottom + 4;
        if (top + dropdownHeight > window.innerHeight - 8) top = Math.max(8, rect.top - dropdownHeight - 4);
        dropdown.style.left = Math.max(8, rect.left) + 'px';
        dropdown.style.top = top + 'px';
        dropdown.style.width = Math.min(rect.width, window.innerWidth - 16) + 'px';
    }}

    function renderReportWorkerTags() {{
        var tags = document.getElementById('reportWorkerTags');
        var input = document.getElementById('reportWorkerSearch');
        if (!tags || !input) return;
        tags.querySelectorAll('.worker-chip').forEach(function(chip) {{ chip.remove(); }});
        getSelectedReportWorkers().forEach(function(name) {{
            var chip = document.createElement('span');
            chip.className = 'worker-chip';
            var avatar = document.createElement('span');
            avatar.className = 'worker-chip-avatar';
            avatar.textContent = getWorkerInitial(name);
            var label = document.createElement('span');
            label.className = 'worker-chip-label';
            label.textContent = name;
            var remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'worker-chip-remove';
            remove.setAttribute('aria-label', 'นำ ' + name + ' ออก');
            remove.textContent = '×';
            remove.addEventListener('click', function(e) {{
                e.preventDefault(); e.stopPropagation();
                selectedReportWorkers.delete(name);
                renderReportWorkerTags();
                updateReportPreview();
                if (document.getElementById('reportWorkerDropdown').classList.contains('show')) renderReportWorkerDropdown();
                input.focus();
            }});
            chip.appendChild(avatar);
            chip.appendChild(label);
            chip.appendChild(remove);
            tags.insertBefore(chip, input);
        }});
        input.placeholder = selectedReportWorkers.size ? 'พิมพ์ค้นหาชื่อเพิ่มเติม...' : 'พิมพ์ค้นหาชื่อ หรือคลิกเลือกผู้ปฏิบัติงาน...';
    }}

    function getWorkerMatchingCount(workerName) {{
        var params = getCurrentFilterParams();
        var validationMessage = validateReportDateRange(params.startInput, params.endInput);
        if (validationMessage) return 0;
        var singleMatched = reportRawData.filter(function(row) {{
            return matchesBaseRowFilters(row, params.startInput, params.endInput, [workerName], params.statusFilter, params.respFilter);
        }});
        var deduped = deduplicateReportRows(singleMatched, params.dedupeMode);
        return deduped.length;
    }}

    function renderReportWorkerDropdown() {{
        var dropdown = document.getElementById('reportWorkerDropdown');
        var input = document.getElementById('reportWorkerSearch');
        if (!dropdown || !input) return;

        function normalizeForSearch(val) {{
            return String(val || '').toLocaleLowerCase('th-TH').normalize('NFC').replace(/[\\s._-่-๋]/g, '');
        }}

        var rawQuery = String(input.value || '').trim();
        var query = normalizeForSearch(rawQuery);
        var matches = reportWorkerNames.filter(function(name) {{
            return !query || normalizeForSearch(name).includes(query);
        }}).slice(0, 30);

        dropdown.innerHTML = '';

        var header = document.createElement('div');
        header.className = 'worker-dropdown-header';
        var headerTitle = document.createElement('span');
        headerTitle.textContent = rawQuery ? ('ผลการค้นหา "' + rawQuery + '" (' + matches.length + ' คน)') : ('รายชื่อผู้ปฏิบัติงานทั้งหมด (' + reportWorkerNames.length + ' คน)');
        var headerActions = document.createElement('div');
        headerActions.className = 'worker-dropdown-actions';
        var btnAll = document.createElement('button');
        btnAll.type = 'button';
        btnAll.className = 'worker-dropdown-action-link';
        btnAll.textContent = 'เลือกทั้งหมด';
        btnAll.addEventListener('mousedown', function(e) {{ e.preventDefault(); }});
        btnAll.addEventListener('click', function(e) {{
            e.preventDefault(); e.stopPropagation();
            setAllReportWorkers(true);
            renderReportWorkerDropdown();
        }});
        var btnClear = document.createElement('button');
        btnClear.type = 'button';
        btnClear.className = 'worker-dropdown-action-link';
        btnClear.textContent = 'ล้างที่เลือก';
        btnClear.addEventListener('mousedown', function(e) {{ e.preventDefault(); }});
        btnClear.addEventListener('click', function(e) {{
            e.preventDefault(); e.stopPropagation();
            setAllReportWorkers(false);
            renderReportWorkerDropdown();
        }});
        headerActions.appendChild(btnAll);
        headerActions.appendChild(btnClear);
        header.appendChild(headerTitle);
        header.appendChild(headerActions);
        dropdown.appendChild(header);

        if (!matches.length && !rawQuery) {{
            var empty = document.createElement('div');
            empty.className = 'report-worker-empty';
            empty.textContent = 'ไม่พบรายชื่อผู้ปฏิบัติงานในข้อมูล';
            dropdown.appendChild(empty);
        }} else {{
            matches.forEach(function(name) {{
                var isSelected = selectedReportWorkers.has(name);
                var isGoogleForm = GOOGLE_FORM_WORKER_LIST.indexOf(name) >= 0;
                var countVal = getWorkerMatchingCount(name);

                var option = document.createElement('button');
                option.type = 'button';
                option.className = 'worker-dropdown-option' + (isSelected ? ' is-selected' : '');
                option.setAttribute('role', 'option');
                option.setAttribute('aria-selected', isSelected ? 'true' : 'false');

                var avatar = document.createElement('span');
                avatar.className = 'worker-avatar';
                avatar.textContent = getWorkerInitial(name);

                var infoWrap = document.createElement('div');
                infoWrap.className = 'worker-dropdown-info';
                var nameSpan = document.createElement('span');
                nameSpan.className = 'worker-dropdown-name';
                nameSpan.textContent = name;
                var sourceTag = document.createElement('span');
                sourceTag.className = 'worker-dropdown-tag';
                sourceTag.textContent = isGoogleForm ? 'Google Form' : 'Data_Check';
                infoWrap.appendChild(nameSpan);
                infoWrap.appendChild(sourceTag);

                var countBadge = document.createElement('span');
                countBadge.className = 'worker-dropdown-count';
                countBadge.textContent = countVal + ' รายการ';

                var mark = document.createElement('span');
                mark.className = 'worker-dropdown-mark';
                mark.textContent = isSelected ? '✓' : '+';

                option.appendChild(avatar);
                option.appendChild(infoWrap);
                option.appendChild(countBadge);
                option.appendChild(mark);

                option.addEventListener('mousedown', function(e) {{ e.preventDefault(); }});
                option.addEventListener('click', function(e) {{
                    e.preventDefault(); e.stopPropagation();
                    if (selectedReportWorkers.has(name)) selectedReportWorkers.delete(name);
                    else selectedReportWorkers.add(name);
                    input.value = '';
                    renderReportWorkerTags();
                    updateReportPreview();
                    renderReportWorkerDropdown();
                    input.focus();
                }});
                dropdown.appendChild(option);
            }});

            var normCustom = normalizeWorkerToken(rawQuery);
            if (normCustom && reportWorkerNames.indexOf(normCustom) === -1) {{
                var customOpt = document.createElement('button');
                customOpt.type = 'button';
                customOpt.className = 'worker-dropdown-option';
                var cAvatar = document.createElement('span');
                cAvatar.className = 'worker-avatar';
                cAvatar.textContent = '+';
                var cInfo = document.createElement('div');
                cInfo.className = 'worker-dropdown-info';
                var cName = document.createElement('span');
                cName.className = 'worker-dropdown-name';
                cName.textContent = 'ค้นหาด้วยชื่อ: "' + normCustom + '"';
                cInfo.appendChild(cName);
                var cCount = document.createElement('span');
                cCount.className = 'worker-dropdown-count';
                cCount.textContent = getWorkerMatchingCount(normCustom) + ' รายการ';
                customOpt.appendChild(cAvatar);
                customOpt.appendChild(cInfo);
                customOpt.appendChild(cCount);
                customOpt.addEventListener('mousedown', function(e) {{ e.preventDefault(); }});
                customOpt.addEventListener('click', function(e) {{
                    e.preventDefault(); e.stopPropagation();
                    if (reportWorkerNames.indexOf(normCustom) === -1) reportWorkerNames.push(normCustom);
                    selectedReportWorkers.add(normCustom);
                    input.value = '';
                    renderReportWorkerTags();
                    updateReportPreview();
                    renderReportWorkerDropdown();
                    input.focus();
                }});
                dropdown.appendChild(customOpt);
            }}
        }}
        positionReportWorkerDropdown();
        dropdown.classList.add('show');
    }}

    function openReportWorkerDropdown() {{
        renderReportWorkerDropdown();
    }}

    function filterReportWorkerOptions() {{ renderReportWorkerDropdown(); }}

    function initializeReportWorkerFilter() {{
        selectedReportWorkers.clear();
        reportWorkerMatchMode = 'OR';
        var orButton = document.getElementById('reportModeOr');
        var andButton = document.getElementById('reportModeAnd');
        if (orButton) {{ orButton.classList.add('is-active'); orButton.setAttribute('aria-pressed', 'true'); }}
        if (andButton) {{ andButton.classList.remove('is-active'); andButton.setAttribute('aria-pressed', 'false'); }}

        var input = document.getElementById('reportWorkerSearch');
        if (input) input.value = '';

        var dynamicNames = reportRawData.flatMap(function(row) {{ return splitOperatorNames(row.workers); }});
        var mergedNames = GOOGLE_FORM_WORKER_LIST.slice();
        dynamicNames.forEach(function(dn) {{
            if (dn && mergedNames.indexOf(dn) === -1) {{
                mergedNames.push(dn);
            }}
        }});

        reportWorkerNames = mergedNames;

        syncDatePresetButtons();
        renderReportWorkerTags();
        closeReportWorkerDropdown();
        updateReportPreview();
    }}

    function setAllReportWorkers(checked) {{
        if (checked) reportWorkerNames.forEach(function(name) {{ selectedReportWorkers.add(name); }});
        else selectedReportWorkers.clear();
        var input = document.getElementById('reportWorkerSearch');
        if (input) input.value = '';
        renderReportWorkerTags();
        updateReportPreview();
    }}

    function setReportWorkerMode(mode) {{
        reportWorkerMatchMode = mode === 'AND' ? 'AND' : 'OR';
        var orButton = document.getElementById('reportModeOr');
        var andButton = document.getElementById('reportModeAnd');
        if (orButton) {{ orButton.classList.toggle('is-active', reportWorkerMatchMode === 'OR'); orButton.setAttribute('aria-pressed', reportWorkerMatchMode === 'OR' ? 'true' : 'false'); }}
        if (andButton) {{ andButton.classList.toggle('is-active', reportWorkerMatchMode === 'AND'); andButton.setAttribute('aria-pressed', reportWorkerMatchMode === 'AND' ? 'true' : 'false'); }}
        updateReportPreview();
        if (document.getElementById('reportWorkerDropdown').classList.contains('show')) renderReportWorkerDropdown();
    }}

    document.getElementById('reportWorkerSearch').addEventListener('input', function() {{
        filterReportWorkerOptions();
        updateReportPreview();
    }});
    document.getElementById('reportWorkerSearch').addEventListener('search', function() {{
        filterReportWorkerOptions();
        updateReportPreview();
    }});
    document.getElementById('reportWorkerSearch').addEventListener('focus', openReportWorkerDropdown);
    document.getElementById('reportWorkerSearch').addEventListener('keydown', function(e) {{
        if (e.key === 'Enter') {{
            e.preventDefault();
            var dropdown = document.getElementById('reportWorkerDropdown');
            var firstOpt = dropdown ? dropdown.querySelector('.worker-dropdown-option') : null;
            if (firstOpt) firstOpt.click();
        }} else if (e.key === 'Backspace' && !this.value && selectedReportWorkers.size > 0) {{
            var arr = getSelectedReportWorkers();
            selectedReportWorkers.delete(arr[arr.length - 1]);
            renderReportWorkerTags();
            updateReportPreview();
            if (document.getElementById('reportWorkerDropdown').classList.contains('show')) renderReportWorkerDropdown();
        }}
    }});

    document.getElementById('reportWorkerPicker').addEventListener('click', function(e) {{
        if (!e.target.closest('.worker-chip-remove')) {{
            var input = document.getElementById('reportWorkerSearch');
            input.focus();
            if (!document.getElementById('reportWorkerDropdown').classList.contains('show')) {{
                openReportWorkerDropdown();
            }}
        }}
    }});

    document.getElementById('selectAllReportWorkers').addEventListener('click', function() {{ setAllReportWorkers(true); }});
    document.getElementById('clearReportWorkers').addEventListener('click', function() {{ setAllReportWorkers(false); }});
    document.getElementById('reportModeOr').addEventListener('click', function() {{ setReportWorkerMode('OR'); }});
    document.getElementById('reportModeAnd').addEventListener('click', function() {{ setReportWorkerMode('AND'); }});

    ['input', 'change', 'blur', 'keyup'].forEach(function(evt) {{
        document.getElementById('reportStartDate').addEventListener(evt, function() {{ syncDatePresetButtons(); updateReportPreview(); }});
        document.getElementById('reportEndDate').addEventListener(evt, function() {{ syncDatePresetButtons(); updateReportPreview(); }});
    }});

    window.addEventListener('resize', function() {{ if (document.getElementById('reportWorkerDropdown').classList.contains('show')) positionReportWorkerDropdown(); }});
    window.addEventListener('scroll', function() {{ if (document.getElementById('reportWorkerDropdown').classList.contains('show')) positionReportWorkerDropdown(); }}, true);

    async function generateExcelReport() {{
        var btn = document.getElementById('execReportBtn');
        btn.disabled = true; btn.innerHTML = 'กำลังสร้าง...';

        var params = getCurrentFilterParams();
        var startInput = params.startInput;
        var endInput = params.endInput;
        var selectedWorkers = params.selectedWorkers;

        var dateValidationMessage = validateReportDateRange(startInput, endInput);
        if (dateValidationMessage) {{
            showCustomAlert(dateValidationMessage);
            btn.disabled = false;
            btn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="white" stroke-width="2"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg> ดาวน์โหลด Excel (.xlsx)';
            return;
        }}

        var filteredResult = getFilteredReportResult();
        var filteredData = filteredResult.rows;

        if (filteredData.length === 0) {{
            showCustomAlert("ไม่พบข้อมูลรายงานที่ตรงกับช่วงวันที่และตัวกรองที่เลือก");
            btn.disabled = false;
            btn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="white" stroke-width="2"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg> ดาวน์โหลด Excel (.xlsx)';
            return;
        }}

        var workbook = new ExcelJS.Workbook();

        var ws = workbook.addWorksheet('Report', {{
            pageSetup: {{
                paperSize: 9, orientation: 'landscape',
                fitToPage: true, fitToWidth: 1, fitToHeight: 0,
                printTitlesRow: '1:2',
                margins: {{ left: 0.25, right: 0.25, top: 0.5, bottom: 0.5, header: 0.2, footer: 0.2 }}
            }}
        }});

        ws.mergeCells('A1:H1');
        var titleCell = ws.getCell('A1');
        titleCell.value = "รายละเอียดการตรวจสอบ/แก้ไขอุปกรณ์ FDCU OFFLine";
        titleCell.font = {{ name: 'TH SarabunPSK', size: 20, bold: true }};
        titleCell.alignment = {{ vertical: 'middle', horizontal: 'center' }};

        var headers = ['ลำดับ', 'รหัสสั่งการ\\n(ข้อมูล GIS)', 'Site ID', 'สถานที่\\n(ข้อมูล GIS)', 'รายละเอียดการตรวจสอบ/แก้ไข', 'ผู้รับผิดชอบและดำเนินการแก้ไขต่อไป', 'ผู้ปฏิบัติงาน (ผอส.ฉ.2)', 'สถานะหลังดำเนินการ'];
        var headerRow = ws.addRow(headers);
        headerRow.eachCell((cell) => {{
            cell.font = {{ name: 'TH SarabunPSK', size: 16, bold: true }};
            cell.alignment = {{ vertical: 'middle', horizontal: 'center', wrapText: true }};
            cell.border = {{ top: {{style:'thin'}}, left: {{style:'thin'}}, bottom: {{style:'thin'}}, right: {{style:'thin'}} }};
        }});

        var startMergeRow = 3;
        var currentStatus = filteredData[0].status;

        filteredData.forEach((row, i) => {{
            var dataRow = ws.addRow([ i+1, row.cmd, row.site, row.loc, row.detail, row.resp, splitOperatorNames(row.workers).join('\\n') || '-', row.status ]);
            dataRow.eachCell((cell, colNumber) => {{
                cell.font = {{ name: 'TH SarabunPSK', size: 16 }};
                cell.border = {{ top: {{style:'thin'}}, left: {{style:'thin'}}, bottom: {{style:'thin'}}, right: {{style:'thin'}} }};
                if (colNumber === 5 || colNumber === 7) {{ cell.alignment = {{ vertical: 'top', horizontal: 'left', wrapText: true }}; }}
                else {{ cell.alignment = {{ vertical: 'middle', horizontal: 'center', wrapText: true }}; }}
            }});

            var currentRow = i + 3;
            var nextStatus = (i < filteredData.length - 1) ? filteredData[i+1].status : null;

            if (nextStatus !== currentStatus || i === filteredData.length - 1) {{
                if (currentRow > startMergeRow) {{
                    ws.mergeCells(`H${{startMergeRow}}:H${{currentRow}}`);
                }}
                startMergeRow = currentRow + 1;
                currentStatus = nextStatus;
            }}
        }});

        ws.getColumn(1).width = 6; ws.getColumn(2).width = 18; ws.getColumn(3).width = 18; ws.getColumn(4).width = 25; ws.getColumn(5).width = 55; ws.getColumn(6).width = 30; ws.getColumn(7).width = 32; ws.getColumn(8).width = 20;

        var buffer = await workbook.xlsx.writeBuffer();
        var blob = new Blob([buffer], {{ type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }});
        var link = document.createElement('a');
        link.href = URL.createObjectURL(blob);

        var safeFilenameDate = "All_Dates";
        if (startInput || endInput) {{
            var sName = startInput ? startInput : "Start";
            var eName = endInput ? endInput : "End";
            safeFilenameDate = sName + "_to_" + eName;
        }}
        var workerFileSuffix = selectedWorkers.length
            ? '_Selected_' + selectedWorkers.length + '_Workers'
            : '_All_Workers';
        var dedupeSuffix = params.dedupeMode === 'latest' ? '_UniqueLatest' : (params.dedupeMode === 'earliest' ? '_UniqueFirst' : '');
        link.download = 'SCADA_Report_' + safeFilenameDate + workerFileSuffix + dedupeSuffix + '.xlsx';

        document.body.appendChild(link); link.click(); document.body.removeChild(link);

        btn.disabled = false; btn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="white" stroke-width="2"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg> ดาวน์โหลด Excel (.xlsx)';
        closeReportModal();
    }}

    function hideCustomPanel() {{
        var panel = document.getElementById('customInfoPanel');
        if (panel) panel.style.display = 'none';
        clearHighlight();
        for (var key in window) {{
            if (key.startsWith('map_')) {{
                var map = window[key];
                if (map && typeof map.closePopup === 'function') {{ map.closePopup(); }}
            }}
        }}
    }}

    function highlightPin(safeId) {{
        document.querySelectorAll('.selected-pin-glow').forEach(function(el) {{ el.classList.remove('selected-pin-glow'); }});
        if (safeId) {{
            window.currentSelectedSafeId = safeId;
            var attempts = 0;
            var tryHighlight = setInterval(function() {{
                var targetPins = document.querySelectorAll('.pin-site-' + safeId);
                if (targetPins.length > 0) {{
                    targetPins.forEach(function(el) {{ el.classList.add('selected-pin-glow'); if(el.parentElement) el.parentElement.style.zIndex = 100000; }});
                    clearInterval(tryHighlight);
                }}
                attempts++; if (attempts > 15) clearInterval(tryHighlight);
            }}, 100);
        }}
    }}

    function clearHighlight() {{
        document.querySelectorAll('.selected-pin-glow').forEach(function(el) {{ el.classList.remove('selected-pin-glow'); }});
        window.currentSelectedSafeId = null; document.body.classList.remove('filter-hover-active');
    }}

    function reapplyHighlight() {{
        if (currentHoveredSelector && document.body.classList.contains('filter-hover-active')) {{
            requestAnimationFrame(function() {{ document.querySelectorAll(currentHoveredSelector).forEach(function(el) {{ el.classList.add('highlight-active'); if(el.parentElement) el.parentElement.style.zIndex = 9999; }}); }});
        }}
        if(window.currentSelectedSafeId) {{ highlightPin(window.currentSelectedSafeId); }}
    }}

    var checkMapReady = setInterval(function() {{
        var targetForm = null;
        var forms = document.querySelectorAll('.leaflet-control-layers form');

        forms.forEach(function(f) {{
            if (f.querySelectorAll('input[type="checkbox"]').length > 0) {{
                targetForm = f;
            }}
        }});
        if (!targetForm && forms.length > 0) {{
            targetForm = forms[forms.length - 1];
        }}

        var globalMap = null;
        for (var key in window) {{ if (key.startsWith('map_')) {{ globalMap = window[key]; break; }} }}

        if (globalMap && targetForm && document.getElementById('customFilterWrapper')) {{
            clearInterval(checkMapReady);
            var filterWrapper = document.getElementById('customFilterWrapper');

            filterWrapper.addEventListener('mouseenter', function () {{ globalMap.scrollWheelZoom.disable(); }});
            filterWrapper.addEventListener('mouseleave', function () {{ globalMap.scrollWheelZoom.enable(); }});

            var hiddenBaseSection = targetForm.querySelector('.leaflet-control-layers-base');
            if (hiddenBaseSection) hiddenBaseSection.remove();
            targetForm.querySelectorAll('.leaflet-control-layers-separator').forEach(function(separator) {{ separator.remove(); }});
            filterWrapper.appendChild(targetForm);

            var controlList = filterWrapper.querySelector('.leaflet-control-layers-overlays');
            if (controlList) {{
                var groups = Array.from(controlList.querySelectorAll('.leaflet-control-layers-group'));
                groups.forEach(function(group) {{
                    var itemLabels = Array.from(group.querySelectorAll('label:not(.leaflet-control-layers-group-label)'));

                    itemLabels.sort(function(a, b) {{
                        var snA = a.querySelector('.status-text'); var snB = b.querySelector('.status-text');
                        var sA = snA ? snA.getAttribute('data-status').toUpperCase() : a.textContent.trim().toUpperCase();
                        var sB = snB ? snB.getAttribute('data-status').toUpperCase() : b.textContent.trim().toUpperCase();

                        function getWeight(s) {{
                            if (["ONLINE", "OFFLINE", "INITIALIZING", "CONNECTING", "TELEMETRY FAILURE"].includes(s)) return 1;
                            if (s.includes("รอ ผบอ. เข้าแก้ไข") && !s.includes("ผอส")) return 2;
                            if (s.includes("รอ ผบอ. และ ผอส.") || s.includes("และ ผอส")) return 3;
                            if (s.includes("รอ ผอส.")) return 4;
                            if (s.includes("ผบอ. เคยแก้ไข")) return 5;
                            if (s.includes("ระบบสื่อสาร") && s.includes("เคยแก้ไข")) return 6;
                            return 10;
                        }}
                        var weightA = getWeight(sA); var weightB = getWeight(sB);
                        if (weightA !== weightB) return weightA - weightB;
                        if (sA.length !== sB.length) return sA.length - sB.length;
                        return sA.localeCompare(sB);
                    }});
                    itemLabels.forEach(function(label) {{ group.appendChild(label); }});
                }});
            }}

            function syncFilterGroupCheckboxes() {{
                filterWrapper.querySelectorAll('.leaflet-control-layers-group').forEach(function(group) {{
                    var header = group.querySelector('.leaflet-control-layers-group-label input[type="checkbox"]');
                    var children = Array.from(group.querySelectorAll('input.leaflet-control-layers-selector[type="checkbox"]'))
                        .filter(function(cb) {{ return !cb.closest('.leaflet-control-layers-group-label'); }});
                    if (!header || !children.length) return;
                    var checkedCount = children.filter(function(cb) {{ return cb.checked; }}).length;
                    header.checked = checkedCount === children.length;
                    header.indeterminate = checkedCount > 0 && checkedCount < children.length;
                }});
            }}
            filterWrapper.querySelectorAll('.leaflet-control-layers-selector').forEach(function(cb) {{
                cb.addEventListener('change', function() {{ syncFilterGroupCheckboxes(); reapplyHighlight(); }});
            }});
            syncFilterGroupCheckboxes();

            function clearActiveHoverElements() {{
                for (var i = 0; i < activeHoverElements.length; i++) {{
                    var el = activeHoverElements[i];
                    el.classList.remove('highlight-active');
                    if (el.parentElement) el.parentElement.style.zIndex = '';
                }}
                activeHoverElements = [];
            }}

            function applyHoverSelector(selector) {{
                clearActiveHoverElements();
                currentHoveredSelector = selector;
                if (!selector) return;
                document.body.classList.add('filter-hover-active');
                var nodes = document.querySelectorAll(selector);
                for (var i = 0; i < nodes.length; i++) {{
                    var el = nodes[i];
                    el.classList.add('highlight-active');
                    if (el.parentElement) el.parentElement.style.zIndex = 9999;
                    activeHoverElements.push(el);
                }}
            }}

            filterWrapper.querySelectorAll('label').forEach(function(lbl) {{
                lbl.addEventListener('mouseenter', function() {{
                    var statusNode = this.querySelector('.status-text');
                    var targetStr = statusNode ? statusNode.getAttribute('data-status') : this.textContent.replace(/●/g,'').trim();
                    if (statusHashMap[targetStr]) {{
                        applyHoverSelector(".s_" + statusHashMap[targetStr]);
                    }}
                }});
                lbl.addEventListener('mouseleave', function() {{
                    currentHoveredSelector = null;
                    if (!window.currentSelectedSafeId) {{ document.body.classList.remove('filter-hover-active'); }}
                    clearActiveHoverElements();
                }});
            }});

            filterWrapper.querySelectorAll('.leaflet-control-layers-group-label').forEach(function(lbl) {{
                lbl.title = 'คลิกเพื่อเลือกหรือยกเลิกสถานะย่อยทั้งหมดในกลุ่มนี้';
                lbl.addEventListener('click', function(e) {{
                    e.preventDefault(); e.stopPropagation();
                    var parentGroup = this.closest('.leaflet-control-layers-group') || this.parentElement.parentElement;
                    if (!parentGroup) return;
                    var checkboxes = Array.from(parentGroup.querySelectorAll('input.leaflet-control-layers-selector[type="checkbox"]'))
                        .filter(function(cb) {{ return !cb.closest('.leaflet-control-layers-group-label'); }});
                    if (!checkboxes.length) return;
                    var allChecked = checkboxes.every(function(cb) {{ return cb.checked; }});
                    var shouldCheck = !allChecked;
                    checkboxes.forEach(function(cb) {{ if (cb.checked !== shouldCheck) cb.click(); }});
                    var groupCheckbox = this.querySelector('input[type="checkbox"]');
                    if (groupCheckbox) {{ groupCheckbox.checked = shouldCheck; groupCheckbox.indeterminate = false; }}
                    syncFilterGroupCheckboxes();
                    reapplyHighlight();
                }});
                lbl.addEventListener('mouseenter', function() {{
                    var parentNode = this.querySelector('.parent-text');
                    if (parentNode) {{
                        var parentStr = parentNode.getAttribute('data-parent');
                        if (statusHashMap[parentStr]) {{
                            applyHoverSelector(".p_" + statusHashMap[parentStr]);
                        }}
                    }}
                }});
                lbl.addEventListener('mouseleave', function() {{
                    currentHoveredSelector = null;
                    if (!window.currentSelectedSafeId) {{ document.body.classList.remove('filter-hover-active'); }}
                    clearActiveHoverElements();
                }});
            }});

            if(!document.getElementById('exportCsvBtn')) {{
                var exportDiv = document.createElement('div'); exportDiv.className = 'g-export-container';
                exportDiv.innerHTML = '<button id="exportCsvBtn" class="g-export-btn" type="button"><svg xmlns="http://www.w3.org/2000/svg" height="24" viewBox="0 -960 960 960" width="24"><path d="M480-320 280-520l56-58 104 104v-326h80v326l104-104 56 58-200 200ZM240-160q-33 0-56.5-23.5T160-240v-120h80v120h480v-120h80v120q0 33-23.5 56.5T720-160H240Z"/></svg>ส่งออกข้อมูล (CSV)</button>';
                targetForm.appendChild(exportDiv);

                document.getElementById('exportCsvBtn').onclick = function(e) {{
                    e.preventDefault(); e.stopPropagation();
                    var activeStatuses = [];
                    filterWrapper.querySelectorAll('.leaflet-control-layers-selector:checked').forEach(function(inp) {{
                        var lbl = inp.nextElementSibling || inp.parentElement;
                        var statusNode = lbl.querySelector('.status-text');
                        activeStatuses.push(statusNode ? statusNode.getAttribute('data-status') : lbl.textContent.replace(/●/g,'').trim());
                    }});
                    var filtered = expData.filter(function(d) {{ return activeStatuses.includes(d._layerName.trim()); }});
                    if(filtered.length === 0) {{ showCustomAlert('ไม่พบข้อมูล (กรุณาติ๊กเลือกอย่างน้อย 1 สถานะ)'); return; }}
                    var headers = Object.keys(filtered[0]).filter(function(k) {{ return k !== '_layerName'; }});
                    var csv = headers.map(function(h) {{ return '"' + h + '"'; }}).join(',') + '\\r\\n';
                    filtered.forEach(function(row) {{ csv += headers.map(function(h) {{ return '"' + (row[h] ? row[h].toString().replace(/"/g, '""') : '') + '"'; }}).join(',') + '\\r\\n'; }});
                    var blob = new Blob(["\\uFEFF" + csv], {{ type: 'text/csv;charset=utf-8;' }});
                    var link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = "SCADA_Export_Data.csv";
                    document.body.appendChild(link); link.click(); document.body.removeChild(link);
                }};
            }}

            var filterBtn = document.getElementById('standaloneFilterBtn');
            if (filterBtn) {{
                filterBtn.addEventListener('click', function(e) {{
                    e.preventDefault(); e.stopPropagation();
                    if (filterWrapper.classList.contains('show')) {{ filterWrapper.classList.remove('show'); return; }}
                    closeReportModal();
                    document.getElementById('customAlertOverlay').classList.remove('show');
                    document.getElementById('searchResults').style.display = 'none';
                    document.getElementById('gLayerPanel').classList.remove('show');
                    closeReportWorkerDropdown();
                    hideCustomPanel();
                    filterWrapper.classList.add('show');
                }});
                L.DomEvent.disableClickPropagation(filterBtn);
                L.DomEvent.disableClickPropagation(filterWrapper);
                filterWrapper.addEventListener('wheel', function(e) {{ e.stopPropagation(); }}, {{passive: false}});
                filterWrapper.addEventListener('touchmove', function(e) {{ e.stopPropagation(); }}, {{passive: false}});
            }}

            globalMap.on('click dragstart popupopen', function() {{
                var fw = document.getElementById('customFilterWrapper');
                if (fw) fw.classList.remove('show');
            }});

            globalMap.on('popupopen', function(e){{
                closeReportModal();
                document.getElementById('customAlertOverlay').classList.remove('show');
                document.getElementById('searchResults').style.display = 'none';
                closeReportWorkerDropdown();
                document.getElementById('gLayerPanel').classList.remove('show');
                var content = e.popup.getContent();
                var rawHtml = typeof content === 'string' ? content : (content ? content.outerHTML || content.innerHTML : '');
                var idMatch = rawHtml.match(/data-safe-id="([^"]+)"/);
                var safeId = idMatch ? idMatch[1] : null;
                var fullPopupHtml = safeId ? buildStationPopupHtml(safeId) : rawHtml;
                document.getElementById('customInfoContent').innerHTML = fullPopupHtml;
                document.getElementById('customInfoPanel').style.display = 'flex';
                if (safeId) {{ highlightPin(safeId); }}

                var targetZoom = 18;
                var isMobile = window.innerWidth <= 768;
                var offsetX = isMobile ? 0 : -180;
                var offsetY = isMobile ? 120 : 0;
                var targetLatlng = e.popup.getLatLng();
                var targetPoint = globalMap.project(targetLatlng, targetZoom);
                targetPoint.x += offsetX; targetPoint.y += offsetY;
                var newCenter = globalMap.unproject(targetPoint, targetZoom);
                globalMap.flyTo(newCenter, targetZoom, {{animate: true, duration: 0.65}});
            }});

            globalMap.on('click', function() {{ hideCustomPanel(); }});

            var CustomControls = L.Control.extend({{
                options: {{ position: 'bottomright' }},
                onAdd: function (map) {{
                    var container = L.DomUtil.create('div', 'custom-right-controls');
                    container.innerHTML = `<a href="#" id="locTargetBtn" class="g-locate-btn" title="ตำแหน่งของฉัน"><svg viewBox="0 0 24 24"><path d="M12 2L4.5 20.29l.71.71L12 18l6.79 3 .71-.71z"/></svg></a><div class="g-zoom-container"><button id="customZoomIn" class="g-zoom-btn g-zoom-in" title="ซูมเข้า">+</button><button id="customZoomOut" class="g-zoom-btn" title="ซูมออก">−</button></div>`;
                    L.DomEvent.disableClickPropagation(container); return container;
                }}
            }});
            globalMap.addControl(new CustomControls());

            document.getElementById('customZoomIn').onclick = function(e) {{ e.stopPropagation(); globalMap.zoomIn(); }};
            document.getElementById('customZoomOut').onclick = function(e) {{ e.stopPropagation(); globalMap.zoomOut(); }};

            var myLocMarker = null; var userLatLng = null; var pendingFlyToLoc = false;

            globalMap.on('locationfound', function(e) {{
                userLatLng = e.latlng;
                if (!myLocMarker) {{
                    myLocMarker = L.marker(e.latlng, {{ icon: L.divIcon({{ className: '', html: '<div class="my-location-container"><div class="my-location-cone"></div><div class="my-location-dot"></div></div>', iconSize: [60,60], iconAnchor: [30,30] }}), zIndexOffset: 9999 }}).addTo(globalMap);
                }} else {{ myLocMarker.setLatLng(e.latlng); }}
                if (pendingFlyToLoc) {{ globalMap.flyTo(userLatLng, 16); pendingFlyToLoc = false; }}
            }});

            document.getElementById('locTargetBtn').onclick = function(e) {{
                e.preventDefault(); e.stopPropagation();
                if (userLatLng) {{
                    globalMap.flyTo(userLatLng, 16);
                }} else {{
                    pendingFlyToLoc = true;
                    globalMap.locate({{watch: false, setView: false, enableHighAccuracy: true}});
                }}
            }};

            var mapConfigs = [
                {{ id: "terrain", name: "ภูมิประเทศ", keyword: "Google Terrain", urlHint: "lyrs=p", thumb: "https://mt1.google.com/vt/lyrs=p&x=130&y=119&z=8" }},
                {{ id: "street", name: "แผนที่ถนน", keyword: "Street Map", urlHint: "tile.openstreetmap.org", thumb: "https://mt1.google.com/vt/lyrs=m&x=130&y=119&z=8" }},
                {{ id: "satellite", name: "ดาวเทียม", keyword: "Google Satellite", urlHint: "lyrs=s", thumb: "https://mt1.google.com/vt/lyrs=s&x=130&y=119&z=8" }},
                {{ id: "hybrid", name: "ดาวเทียม+ถนน", keyword: "Google Hybrid", urlHint: "lyrs=y", thumb: "https://mt1.google.com/vt/lyrs=y&x=130&y=119&z=8" }},
                {{ id: "esri", name: "Esri", keyword: "Esri World Imagery", urlHint: "World_Imagery/MapServer/tile", thumb: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/8/119/130" }}
            ];

            var currentMapIndex = 0;
            var panel = document.getElementById('gLayerPanel');
            mapConfigs.forEach(function(conf, idx) {{
                var div = document.createElement('div'); div.className = 'g-layer-item';
                div.innerHTML = '<div class="g-layer-thumb" style="background-image: url(' + conf.thumb + ')"></div><div class="g-layer-name">' + conf.name + '</div>';
                div.onclick = function(e) {{ e.stopPropagation(); closeOtherMapPopups(); switchMapLayer(idx); panel.classList.remove('show'); }};
                panel.appendChild(div);
            }});

            function getAllKnownBaseLayers() {{
                var layers = [];
                function addCandidate(layer) {{
                    if (layer && layer._url && typeof layer.addTo === 'function' && layers.indexOf(layer) === -1) layers.push(layer);
                }}
                Object.keys(window).forEach(function(key) {{
                    try {{ addCandidate(window[key]); }} catch (err) {{ }}
                }});
                globalMap.eachLayer(function(layer) {{ addCandidate(layer); }});
                return layers.filter(function(layer) {{
                    var url = String(layer._url || '');
                    return url.indexOf('lyrs=p') >= 0 || url.indexOf('tile.openstreetmap.org') >= 0 ||
                        url.indexOf('lyrs=s') >= 0 || url.indexOf('lyrs=y') >= 0 ||
                        url.indexOf('World_Imagery/MapServer/tile') >= 0;
                }});
            }}

            function getBaseLayerForConfig(conf) {{
                var namedLayer = baseLayerNames[conf.id] ? window[baseLayerNames[conf.id]] : null;
                if (namedLayer && namedLayer._url) return namedLayer;
                var candidates = getAllKnownBaseLayers();
                for (var i = 0; i < candidates.length; i++) {{
                    if (String(candidates[i]._url || '').indexOf(conf.urlHint) >= 0) return candidates[i];
                }}
                return null;
            }}

            function syncHiddenBaseRadio(conf) {{
                var labels = document.querySelectorAll('.leaflet-control-layers-base label');
                labels.forEach(function(label) {{
                    var radio = label.querySelector('input[type="radio"]');
                    if (!radio) return;
                    var isTarget = label.textContent.indexOf(conf.keyword) >= 0;
                    radio.checked = isTarget;
                }});
            }}

            function switchMapLayer(idx) {{
                currentMapIndex = idx;
                var conf = mapConfigs[idx];
                var targetLayer = getBaseLayerForConfig(conf);
                if (targetLayer) {{
                    getAllKnownBaseLayers().forEach(function(layer) {{
                        if (layer !== targetLayer && globalMap.hasLayer(layer)) globalMap.removeLayer(layer);
                    }});
                    if (!globalMap.hasLayer(targetLayer)) globalMap.addLayer(targetLayer);
                }} else {{
                    var labels = document.querySelectorAll('.leaflet-control-layers-base label');
                    labels.forEach(function(label) {{
                        if (label.textContent.indexOf(conf.keyword) >= 0) {{
                            var radio = label.querySelector('input[type="radio"]');
                            if (radio) {{ radio.click(); }}
                        }}
                    }});
                }}
                syncHiddenBaseRadio(conf);
                panel.querySelectorAll('.g-layer-item').forEach(function(item, i) {{
                    if (i === idx) item.classList.add('active'); else item.classList.remove('active');
                }});
                document.getElementById('gLayerMainBtn').style.backgroundImage = 'url(' + conf.thumb + ')';
                document.getElementById('gLayerMainLabel').innerText = conf.name;
            }}

            function closeOtherMapPopups() {{
                var fw = document.getElementById('customFilterWrapper'); if (fw) fw.classList.remove('show');
                closeReportModal();
                document.getElementById('customAlertOverlay').classList.remove('show');
                document.getElementById('searchResults').style.display = 'none';
                closeReportWorkerDropdown();
                panel.classList.remove('show');
                hideCustomPanel();
            }}

            document.getElementById('gLayerMainBtn').onclick = function(e) {{
                e.preventDefault(); e.stopPropagation();
                var shouldOpen = !panel.classList.contains('show');
                closeOtherMapPopups();
                if (shouldOpen) {{
                    var fw = document.getElementById('customFilterWrapper'); if (fw) fw.classList.remove('show');
                    closeReportModal();
                    document.getElementById('customAlertOverlay').classList.remove('show');
                    document.getElementById('searchResults').style.display = 'none';
                    closeReportWorkerDropdown();
                    hideCustomPanel();
                    panel.classList.add('show');
                }}
            }};
            switchMapLayer(0);
        }}
    }}, 100);

    // --- ระบบค้นหาอัจฉริยะ ---
    var box = document.getElementById('searchBox');
    var inp = document.getElementById('searchInput');
    var res = document.getElementById('searchResults');
    var clr = document.getElementById('searchClear');

    function handleSearchFocus() {{
        box.classList.add('focus');
        closeReportModal();
        document.getElementById('customAlertOverlay').classList.remove('show');
        closeReportWorkerDropdown();
        document.getElementById('gLayerPanel').classList.remove('show');
        hideCustomPanel();
        var fw = document.getElementById('customFilterWrapper');
        if (fw) fw.classList.remove('show');
        triggerSearch();
    }}

    function normalizeSearchText(value) {{
        return String(value || '').toLocaleLowerCase('th-TH')
            .normalize('NFC')
            .replace(/\\s+/g, '').replace(/[._-]/g, '');
    }}

    function normalizeToneInsensitive(value) {{
        return normalizeSearchText(value).replace(/[่-๋]/g, '');
    }}

    function isOneEditAway(a, b) {{
        if (a === b) return true;
        if (Math.abs(a.length - b.length) > 1) return false;
        var i = 0, j = 0, edits = 0;
        while (i < a.length && j < b.length) {{
            if (a.charAt(i) === b.charAt(j)) {{ i++; j++; continue; }}
            edits++;
            if (edits > 1) return false;
            if (a.length > b.length) i++;
            else if (b.length > a.length) j++;
            else {{ i++; j++; }}
        }}
        if (i < a.length || j < b.length) edits++;
        return edits <= 1;
    }}

    function hasNearMatch(query, target) {{
        if (query.length < 4 || !target) return false;
        var minLen = Math.max(1, query.length - 1);
        var maxLen = Math.min(target.length, query.length + 1);
        for (var len = minLen; len <= maxLen; len++) {{
            for (var start = 0; start + len <= target.length; start++) {{
                if (isOneEditAway(query, target.slice(start, start + len))) return true;
            }}
        }}
        return false;
    }}

    function triggerSearch() {{
        var rawVal = inp.value.trim();
        clr.style.display = rawVal.length > 0 ? 'block' : 'none';
        res.innerHTML = '';
        if (rawVal.length < 1) {{ res.style.display = 'none'; return; }}

        var query = normalizeSearchText(rawVal);
        var toneQuery = normalizeToneInsensitive(rawVal);
        var scoredMatches = [];
        var fuzzyCandidates = [];

        for (var i = 0; i < sData.length; i++) {{
            var item = sData[i];
            var normalized = item.norm || [normalizeSearchText(item.id), normalizeSearchText(item.code), normalizeSearchText(item.name)];
            var toneNormalized = item.tone || [normalizeToneInsensitive(item.id), normalizeToneInsensitive(item.code), normalizeToneInsensitive(item.name)];
            var score = 0;
            if (normalized[0] === query || normalized[1] === query) score = 100;
            else if (normalized[0].indexOf(query) >= 0 || normalized[1].indexOf(query) >= 0 || normalized[2].indexOf(query) >= 0) score = 80;
            else if (toneQuery.length >= 3 && (toneNormalized[0].indexOf(toneQuery) >= 0 || toneNormalized[1].indexOf(toneQuery) >= 0 || toneNormalized[2].indexOf(toneQuery) >= 0)) score = 60;
            else if (query.length >= 4) fuzzyCandidates.push({{ item: item, norm: normalized }});

            if (score > 0) scoredMatches.push({{ item: item, score: score }});
        }}

        if (scoredMatches.length < 8 && query.length >= 4) {{
            for (var j = 0; j < fuzzyCandidates.length; j++) {{
                var fc = fuzzyCandidates[j];
                if (fc.norm.some(function(value) {{ return hasNearMatch(query, value); }})) {{
                    scoredMatches.push({{ item: fc.item, score: 40 }});
                }}
            }}
        }}

        scoredMatches.sort(function(a, b) {{
            if (b.score !== a.score) return b.score - a.score;
            return String(a.item.id || '').localeCompare(String(b.item.id || ''), 'en', {{ numeric: true, sensitivity: 'base' }});
        }});

        var matches = scoredMatches.slice(0, 8).map(function(match) {{ return match.item; }});
        if (!matches.length) {{ res.style.display = 'none'; return; }}
        res.style.display = 'block';
        var frag = document.createDocumentFragment();
        matches.forEach(function(m) {{
            var div = document.createElement('div'); div.className = 'g-search-item';
            div.innerHTML = `<div style="display:flex;flex-direction:column;width:100%;gap:6px;">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;width:100%;gap:8px;">
                <span style="color:#e3e3e3;font-size:14.5px;font-weight:600;line-height:1.2;">${{m.id}}</span>
                ${{m.code ? `<span style="color:#8ab4f8;font-size:11px;font-weight:500;background:rgba(138,180,248,.1);padding:2px 6px;border-radius:4px;border:1px solid rgba(138,180,248,.2);flex-shrink:0;margin-top:1px;">${{m.code}}</span>` : ''}}
              </div>
              <div style="display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;width:100%;">
                <span style="background-color:${{m.color}};color:#fff;padding:3px 8px;border-radius:6px;font-size:10px;font-weight:500;line-height:1.2;">${{m.status}}</span>
                <span style="color:#babbbe;font-size:11.5px;display:flex;align-items:flex-start;gap:4px;line-height:1.3;flex:1;min-width:120px;">
                  <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" style="flex-shrink:0;margin-top:1px;"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg>
                  <span style="word-break:break-word;">${{m.name ? m.name : 'ไม่มีชื่อสถานที่'}}</span>
                </span>
              </div>
            </div>`;
            div.addEventListener('click', function(e) {{
                e.preventDefault(); e.stopPropagation();
                closeReportModal();
                document.getElementById('customAlertOverlay').classList.remove('show');
                var fw = document.getElementById('customFilterWrapper'); if (fw) fw.classList.remove('show');
                closeReportWorkerDropdown();
                hideCustomPanel();
                for (var key in window) {{
                    if (key.startsWith('map_')) {{
                        var map = window[key];
                        var targetZoom = 18;
                        var isMobile = window.innerWidth <= 768;
                        var offsetX = isMobile ? 0 : -180;
                        var offsetY = isMobile ? 120 : 0;
                        var targetPoint = map.project([m.lat, m.lon], targetZoom);
                        targetPoint.x += offsetX; targetPoint.y += offsetY;
                        var newCenter = map.unproject(targetPoint, targetZoom);
                        map.flyTo(newCenter, targetZoom, {{ animate: true, duration: 0.65 }});
                        document.getElementById('customInfoContent').innerHTML = buildStationPopupHtml(m.safe_id);
                        document.getElementById('customInfoPanel').style.display = 'flex';
                        highlightPin(m.safe_id);
                    }}
                }}
                res.style.display = 'none';
            }});
            frag.appendChild(div);
        }});
        res.appendChild(frag);
    }}

    var searchRafId = null;
    inp.addEventListener('input', function() {{
        if (searchRafId) cancelAnimationFrame(searchRafId);
        searchRafId = requestAnimationFrame(triggerSearch);
    }});
    inp.addEventListener('focus', handleSearchFocus);
    inp.addEventListener('click', handleSearchFocus);
    inp.addEventListener('blur', function() {{ box.classList.remove('focus'); setTimeout(function() {{ if (!box.contains(document.activeElement)) res.style.display = 'none'; }}, 180); }});

    document.addEventListener('click', function(e) {{
        var target = e.target;
        var fw = document.getElementById('customFilterWrapper');
        var filterBtn = document.getElementById('standaloneFilterBtn');
        if (fw && fw.classList.contains('show') && !fw.contains(target) && !filterBtn.contains(target)) fw.classList.remove('show');

        if (reportModal && reportModal.classList.contains('show') && target === reportModal) closeReportModal();
        var alertOverlay = document.getElementById('customAlertOverlay');
        if (alertOverlay && alertOverlay.classList.contains('show') && target === alertOverlay) alertOverlay.classList.remove('show');

        var picker = document.getElementById('reportWorkerPicker');
        var workerDropdown = document.getElementById('reportWorkerDropdown');
        if (workerDropdown && workerDropdown.classList.contains('show') && !picker.contains(target) && !workerDropdown.contains(target)) closeReportWorkerDropdown();

        var searchContainer = document.querySelector('.g-search-container');
        if (res && res.style.display === 'block' && !searchContainer.contains(target)) res.style.display = 'none';

        var layerPicker = document.getElementById('gLayerContainer');
        var layerPanel = document.getElementById('gLayerPanel');
        if (layerPanel && layerPanel.classList.contains('show') && !layerPicker.contains(target)) layerPanel.classList.remove('show');

        var infoPanel = document.getElementById('customInfoPanel');
        var isMapMarker = target.closest && target.closest('.leaflet-marker-icon, .leaflet-interactive');
        var isSearchResult = target.closest && target.closest('.g-search-item');
        var isMapControl = target.closest && target.closest('#gLayerContainer, #standaloneReportBtn, #standaloneFilterBtn, #customFilterWrapper, .g-search-container');
        if (infoPanel && infoPanel.style.display === 'flex' && !infoPanel.contains(target) && !isMapMarker && !isSearchResult && !isMapControl) hideCustomPanel();
    }});
    </script>
    """
    m.get_root().html.add_child(folium.Element(custom_ui_html))

    return m.get_root().render()

def background_task():
    try:
        print("กำลังดึงข้อมูลและสร้างแผนที่เบื้องหลัง...")
        new_html = generate_map()
        tmp_html = CACHE_HTML_FILE + '.tmp'
        with open(tmp_html, 'w', encoding='utf-8') as f:
            f.write(new_html)
        os.replace(tmp_html, CACHE_HTML_FILE)
        new_version = int(time.time())
        tmp_meta = CACHE_META_FILE + '.tmp'
        with open(tmp_meta, 'w', encoding='utf-8') as f:
            json.dump({'version': new_version, 'last_update': time.time()}, f)
        os.replace(tmp_meta, CACHE_META_FILE)
        print(f"อัปเดตแผนที่เสร็จสมบูรณ์! (เวอร์ชัน {new_version})")
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการรันเบื้องหลัง: {e}")
        # หากมีไฟล์ Cache เดิมอยู่แล้ว ไม่ต้องเขียนทับด้วยหน้า Error เพื่อให้ผู้ใช้ยังใช้งานแผนที่เดิมต่อได้
        if not os.path.exists(CACHE_HTML_FILE):
            error_html = f"""
            <div style='display:flex;flex-direction:column;justify-content:center;align-items:center;height:100vh;background:#282a2d;color:white;font-family:sans-serif;'>
                <h2 style='color:#d33d2a;'>เกิดข้อผิดพลาดในการโหลดข้อมูลแผนที่</h2>
                <p style='color:#9aa0a6;max-width:80%;text-align:center;'>{html.escape(str(e))}</p>
                <p style='color:#8ab4f8;font-size:14px;margin-top:20px;cursor:pointer;' onclick='location.reload()'>↻ รีเฟรชหน้าเว็บใหม่อีกครั้ง</p>
            </div>
            """
            tmp_html = CACHE_HTML_FILE + '.tmp'
            with open(tmp_html, 'w', encoding='utf-8') as f:
                f.write(error_html)
            os.replace(tmp_html, CACHE_HTML_FILE)
            new_version = int(time.time())
            tmp_meta = CACHE_META_FILE + '.tmp'
            with open(tmp_meta, 'w', encoding='utf-8') as f:
                json.dump({'version': new_version, 'last_update': time.time()}, f)
            os.replace(tmp_meta, CACHE_META_FILE)
    finally:
        if os.path.exists(LOCK_FILE):
            try:
                os.remove(LOCK_FILE)
            except Exception:
                pass

def trigger_update_if_needed():
    meta = get_meta()
    cache_missing = not os.path.exists(CACHE_HTML_FILE)
    if cache_missing or (time.time() - meta['last_update'] > CACHE_DURATION):
        if os.path.exists(LOCK_FILE) and (time.time() - os.path.getmtime(LOCK_FILE) > 300):
            try:
                os.remove(LOCK_FILE)
            except Exception:
                pass
        if not os.path.exists(LOCK_FILE):
            try:
                open(LOCK_FILE, 'w').close()
                t = threading.Thread(target=background_task, daemon=True)
                t.start()
            except Exception:
                pass

@app.route('/map-data')
def map_data():
    try:
        with open(CACHE_HTML_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        trigger_update_if_needed()
        return "<style>body{background:#282a2d;}</style>", 503

@app.route('/api/version')
def api_version():
    trigger_update_if_needed()
    meta = get_meta()
    return jsonify({"version": meta['version']})

@app.route('/')
def index():
    trigger_update_if_needed()
    html_wrapper = """
    <!DOCTYPE html>
    <html lang="th">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>SCADA Map Dashboard</title>
        <link href="https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;500;600&display=swap" rel="stylesheet">
        <style>
            body, html { margin: 0; padding: 0; height: 100%; width: 100%; overflow: hidden; background-color: #282a2d; font-family: 'Prompt', sans-serif; }
            .map-layer { position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: none; }
            .layer-active { z-index: 2; opacity: 1; transition: opacity 0.6s ease-in-out; }
            .layer-hidden { z-index: 1; opacity: 0; pointer-events: none; }
            #loader { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: white; z-index: 0; text-align: center; transition: opacity 0.4s; }
        </style>
    </head>
    <body>
        <div id="loader"><h2 id="loading-text">กำลังเตรียมข้อมูล SCADA...</h2><p>รอการเชื่อมต่อแผนที่ครั้งแรก</p></div>
        <iframe id="layer1" class="map-layer layer-hidden" src="about:blank"></iframe>
        <iframe id="layer2" class="map-layer layer-hidden" src="about:blank"></iframe>

        <script>
            var currentVersion = 0;
            var pendingVersion = 0;
            var activeLayer = 1;
            var isSwapping = false;

            var dotCount = 0;
            setInterval(() => {
                var loaderText = document.getElementById('loading-text');
                if (loaderText && currentVersion === 0) {
                    dotCount = (dotCount + 1) % 4;
                    loaderText.innerText = "กำลังเตรียมข้อมูล SCADA" + ".".repeat(dotCount);
                }
            }, 500);

            window.addEventListener('resize', () => {
                document.querySelectorAll('iframe').forEach(ifr => {
                    var map = getMapInstance(ifr);
                    if (map) setTimeout(() => map.invalidateSize(), 200);
                });
            });
            window.addEventListener('orientationchange', () => {
                document.querySelectorAll('iframe').forEach(ifr => {
                    var map = getMapInstance(ifr);
                    if (map) setTimeout(() => map.invalidateSize(), 300);
                });
            });

            function getMapInstance(iframe) {
                try {
                    var win = iframe.contentWindow;
                    for (var key in win) { if (key.startsWith('map_')) return win[key]; }
                } catch(e) {}
                return null;
            }

            function isUserBusyInIframe(iframe) {
                try {
                    var doc = iframe.contentWindow.document;
                    if (!doc) return false;
                    var reportModal = doc.getElementById('reportModalOverlay');
                    if (reportModal && reportModal.classList.contains('show')) return true;
                    var alertModal = doc.getElementById('customAlertOverlay');
                    if (alertModal && alertModal.classList.contains('show')) return true;
                    var filterPanel = doc.getElementById('customFilterWrapper');
                    if (filterPanel && filterPanel.classList.contains('show')) return true;
                    var infoPanel = doc.getElementById('customInfoPanel');
                    if (infoPanel && infoPanel.style.display === 'flex') return true;
                    var searchBox = doc.getElementById('searchBox');
                    if (searchBox && searchBox.classList.contains('focus')) return true;
                } catch(e) {}
                return false;
            }

            function checkUpdate() {
                fetch('/api/version?t=' + new Date().getTime(), { cache: 'no-store' })
                    .then(res => res.json())
                    .then(data => {
                        if (data.version > 0 && data.version !== currentVersion) {
                            pendingVersion = data.version;
                            tryApplyPendingSwap();
                        }
                    }).catch(e => console.log(e));
            }

            function tryApplyPendingSwap() {
                if (!pendingVersion || pendingVersion === currentVersion || isSwapping) return;
                var activeIframe = document.getElementById('layer' + activeLayer);
                if (currentVersion > 0 && isUserBusyInIframe(activeIframe)) {
                    // รอให้ผู้ใช้ปิดหน้าต่าง Export / Filter / Info ก่อนจึงค่อยสลับแผนที่เบื้องหลัง
                    return;
                }
                currentVersion = pendingVersion;
                swapMap();
            }

            function swapMap() {
                isSwapping = true;
                var nextLayer = activeLayer === 1 ? 2 : 1;
                var activeIframe = document.getElementById('layer' + activeLayer);
                var nextIframe = document.getElementById('layer' + nextLayer);

                nextIframe.src = '/map-data?v=' + currentVersion + '&t=' + new Date().getTime();

                nextIframe.onload = function() {
                    var checkReady = setInterval(function() {
                        var newMap = getMapInstance(nextIframe);
                        var oldMap = getMapInstance(activeIframe);

                        var hasErrorPage = false;
                        try { hasErrorPage = nextIframe.contentWindow.document.body.innerHTML.includes('เกิดข้อผิดพลาด'); } catch(e) {}

                        if (newMap || hasErrorPage) {
                            clearInterval(checkReady);
                            if (oldMap && newMap) {
                                newMap.setView(oldMap.getCenter(), oldMap.getZoom(), {animate: false});
                            }

                            nextIframe.className = 'map-layer layer-active';
                            activeIframe.className = 'map-layer layer-hidden';
                            activeLayer = nextLayer;
                            isSwapping = false;

                            var loader = document.getElementById('loader');
                            if (loader) {
                                loader.style.opacity = '0';
                                setTimeout(() => loader.style.display = 'none', 500);
                            }

                            setTimeout(function() { activeIframe.src = 'about:blank'; }, 1200);
                        }
                    }, 150);
                    setTimeout(() => { clearInterval(checkReady); isSwapping = false; }, 10000);
                };
            }

            // ตรวจสอบทันทีเมื่อเปิดหน้าเว็บ ไม่ต้องรอ 3 วินาทีแรก
            checkUpdate();

            var initialPoll = setInterval(() => {
                if (currentVersion === 0) checkUpdate();
                else clearInterval(initialPoll);
            }, 1500);

            setInterval(() => {
                checkUpdate();
                tryApplyPendingSwap();
            }, 15000);

            // ส่งคำสั่งจับปุ่ม ESC ให้ iframe ลูก
            document.addEventListener('keydown', function(e) {
                if (e.key === 'Escape' || e.keyCode === 27) {
                    var ifr = document.getElementById('layer' + activeLayer);
                    if (ifr && ifr.contentWindow) {
                        ifr.contentWindow.document.dispatchEvent(new KeyboardEvent('keydown', {'key': 'Escape', 'keyCode': 27}));
                    }
                }
            });
        </script>
    </body>
    </html>
    """
    return html_wrapper

# สั่งเตรียมข้อมูลแผนที่เบื้องหลังทันทีเมื่อ Server เริ่มทำงาน (เช่น บน Render / Gunicorn)
trigger_update_if_needed()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)