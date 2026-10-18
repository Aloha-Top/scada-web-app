from flask import Flask, jsonify, request, render_template_string
import sys
import io
import sqlite3
import ssl
import os
import glob
import shutil
import time
import tempfile
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import json
import hashlib
import html
import re
import uuid

try:
    import folium
    from folium import FeatureGroup
    from folium.plugins import GroupedLayerControl, MarkerCluster
except ImportError:
    class _MiniElement:
        def __init__(self, html_str=""):
            self.html_str = html_str
            self.children = []
        def add_child(self, child):
            self.children.append(child)
            return self

    class _MiniRoot:
        def __init__(self, map_parent=None):
            self.html = _MiniElement()
            self._map_parent = map_parent
        def render(self):
            return self._map_parent._render_html()

    class _MiniTileLayer:
        def __init__(self, tiles='OpenStreetMap', attr='', name='', overlay=False, control=True, max_zoom=22, show=True):
            self.id = "tile_layer_" + uuid.uuid4().hex
            self.url = "https://tile.openstreetmap.org/{z}/{x}/{y}.png" if tiles == "OpenStreetMap" else tiles
            self.attr = attr or ("OpenStreetMap" if tiles == "OpenStreetMap" else "Map")
            self.name = name
            self.max_zoom = max_zoom
            self.show = show
        def get_name(self):
            return self.id
        def add_to(self, parent):
            parent.tile_layers.append(self)
            return self

    class _MiniPopup:
        def __init__(self, html_content, autoPan=False):
            self.html_content = html_content
            self.autoPan = autoPan

    class _MiniDivIcon:
        def __init__(self, html="", icon_size=(30, 42), icon_anchor=(15, 42), popup_anchor=(0, -42)):
            self.html = html
            self.icon_size = list(icon_size)
            self.icon_anchor = list(icon_anchor)
            self.popup_anchor = list(popup_anchor)

    class _MiniMarker:
        def __init__(self, location, popup=None, tooltip="", icon=None):
            self.id = "marker_" + uuid.uuid4().hex
            self.location = list(location)
            self.popup = popup
            self.tooltip = tooltip
            self.icon = icon
        def add_to(self, cluster):
            cluster.markers.append(self)
            return self

    class MarkerCluster:
        def __init__(self, name="", show=True, icon_create_function="", control=False, options=None):
            self.id = "marker_cluster_" + uuid.uuid4().hex
            self.name = name
            self.show = show
            self.icon_create_function = icon_create_function
            self.options = options or {}
            self.markers = []
        def get_name(self):
            return self.id

    class _MiniLayerControl:
        def __init__(self, position='topleft', collapsed=True):
            self.position = position
            self.collapsed = collapsed
        def add_to(self, parent):
            parent.layer_control = self
            return self

    class GroupedLayerControl:
        def __init__(self, groups=None, exclusive_groups=False, collapsed=True):
            self.groups = groups or {}
            self.collapsed = collapsed
        def add_to(self, parent):
            parent.grouped_control = self
            return self

    class FeatureGroup:
        pass

    class _MiniMap:
        def __init__(self, location=[15.2282, 104.8563], zoom_start=8, zoom_control=False, tiles=None, prefer_canvas=True, max_zoom=22):
            self.id = "map_" + uuid.uuid4().hex
            self.location = location
            self.zoom_start = zoom_start
            self.zoom_control = zoom_control
            self.prefer_canvas = prefer_canvas
            self.max_zoom = max_zoom
            self.tile_layers = []
            self.clusters = []
            self.layer_control = None
            self.grouped_control = None
            self._root = _MiniRoot(self)
        def get_root(self):
            return self._root
        def add_child(self, child):
            if isinstance(child, MarkerCluster):
                self.clusters.append(child)
            return self
        def _render_html(self):
            custom_html = "".join(c.html_str for c in self._root.html.children)
            js_lines = []
            js_lines.append(f'var {self.id} = L.map("{self.id}", {{ center: {json.dumps(self.location)}, crs: L.CRS.EPSG3857, zoom: {self.zoom_start}, zoomControl: {"true" if self.zoom_control else "false"}, preferCanvas: {"true" if self.prefer_canvas else "false"} }});')
            for tl in self.tile_layers:
                js_lines.append(f'var {tl.id} = L.tileLayer({json.dumps(tl.url)}, {{"minZoom": 0, "maxZoom": {tl.max_zoom}, "maxNativeZoom": {tl.max_zoom}, "noWrap": false, "attribution": {json.dumps(tl.attr)}, "subdomains": "abc", "detectRetina": false, "tms": false, "opacity": 1}});')
                if tl.show:
                    js_lines.append(f'{tl.id}.addTo({self.id});')
            for mc in self.clusters:
                js_lines.append(f'var {mc.id} = L.markerClusterGroup({json.dumps(mc.options)});')
                if mc.icon_create_function:
                    js_lines.append(f'{mc.id}.options.iconCreateFunction = {mc.icon_create_function};')
                for mk in mc.markers:
                    icon_js = f'L.divIcon({{"html": {json.dumps(mk.icon.html)}, "iconSize": {json.dumps(mk.icon.icon_size)}, "iconAnchor": {json.dumps(mk.icon.icon_anchor)}, "popupAnchor": {json.dumps(mk.icon.popup_anchor)}, "className": "empty"}})'
                    popup_html = mk.popup.html_content if mk.popup else ""
                    tip_html = f"<div>{html.escape(str(mk.tooltip))}</div>"
                    js_lines.append(
                        f'L.marker({json.dumps(mk.location)}, {{"icon": {icon_js}}})'
                        f'.bindPopup({json.dumps(popup_html)}, {{"maxWidth": "100%", "autoPan": false}})'
                        f'.bindTooltip({json.dumps(tip_html)}, {{"sticky": true}})'
                        f'.addTo({mc.id});'
                    )
                if mc.show:
                    js_lines.append(f'{mc.id}.addTo({self.id});')
            if self.layer_control:
                base_entries = ", ".join(f'{json.dumps(tl.name)}: {tl.id}' for tl in self.tile_layers)
                js_lines.append(f'L.control.layers({{{base_entries}}}, {{}}, {{"position": "topleft", "collapsed": true, "autoZIndex": true}}).addTo({self.id});')
            if self.grouped_control:
                grp_parts = []
                for g_label, mcs in self.grouped_control.groups.items():
                    inner = ", ".join(f'{json.dumps(mc.name)}: {mc.id}' for mc in mcs)
                    grp_parts.append(f'{json.dumps(g_label)}: {{{inner}}}')
                grp_js = "{" + ", ".join(grp_parts) + "}"
                js_lines.append(f'L.control.groupedLayers(null, {grp_js}, {{"collapsed": true}}).addTo({self.id});')
            script_body = "\n".join(js_lines)
            return (
                '<!DOCTYPE html>\n<html lang="th">\n<head>\n'
                '    <meta http-equiv="content-type" content="text/html; charset=UTF-8" />\n'
                '    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />\n'
                '    <title>รายงานความพร้อมใช้งานอุปกรณ์ในระบบ SCADA (%AVA)</title>\n'
                '    <script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.js"></script>\n'
                '    <script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>\n'
                '    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.2.2/dist/js/bootstrap.bundle.min.js"></script>\n'
                '    <script src="https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.js"></script>\n'
                '    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.css"/>\n'
                '    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.2.2/dist/css/bootstrap.min.css"/>\n'
                '    <link rel="stylesheet" href="https://netdna.bootstrapcdn.com/bootstrap/3.0.0/css/bootstrap-glyphicons.css"/>\n'
                '    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.2.0/css/all.min.css"/>\n'
                '    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.css"/>\n'
                '    <link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/python-visualization/folium/folium/templates/leaflet.awesome.rotate.min.css"/>\n'
                f'    <style>#{self.id} {{ position: relative; width: 100.0%; height: 100.0%; left: 0.0%; top: 0.0%; }} .leaflet-container {{ font-size: 1rem; }} html, body {{ width: 100%; height: 100%; margin: 0; padding: 0; }} #map {{ position:absolute; top:0; bottom:0; right:0; left:0; }}</style>\n'
                '    <script>L_NO_TOUCH = false; L_DISABLE_3D = false;</script>\n'
                '    <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.1.0/leaflet.markercluster.js"></script>\n'
                '    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.1.0/MarkerCluster.css"/>\n'
                '    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.1.0/MarkerCluster.Default.css"/>\n'
                '    <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet-groupedlayercontrol/0.6.1/leaflet.groupedlayercontrol.min.js"></script>\n'
                '    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet-groupedlayercontrol/0.6.1/leaflet.groupedlayercontrol.min.css"/>\n'
                '</head>\n<body>\n'
                + custom_html + '\n'
                + f'<div class="folium-map" id="{self.id}"></div>\n<script>\n'
                + script_body + '\n</script>\n</body>\n</html>'
            )
        def save(self, filepath):
            with open(filepath, "w", encoding="utf-8") as f_out:
                f_out.write(self._render_html())

    class _FoliumNamespace:
        Map = _MiniMap
        TileLayer = _MiniTileLayer
        Marker = _MiniMarker
        Popup = _MiniPopup
        DivIcon = _MiniDivIcon
        LayerControl = _MiniLayerControl
        Element = _MiniElement

    folium = _FoliumNamespace()

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
RTU_SUMMARY_GID = '747048278'
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
    'IP',
    'Commu.',
    'Item',
    'DNP',
    'FRTU Model',
    'WRL Master',
    'ซ้ำ',
    'เช็ครหัส',
    'ชนิดผลิตภัณฑ์ของ FRTU',
    'ชนิดของอุปกรณ์สื่อสาร',
    'ประเภทย่อยอุปกรณ์สื่อสาร (Item)',
    'แม่ข่ายสื่อสาร (WRL Master / Base)',
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

RTU_SUMMARY_URLS = [
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=1&gid={RTU_SUMMARY_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:json&headers=0&gid={RTU_SUMMARY_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=0&gid={RTU_SUMMARY_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={RTU_SUMMARY_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet=RTU%20Summary',
]

GOOGLE_FORM_WORKERS = [
    "นายณัฐกานต์ ป้อมไชยา",
    "นางปนัดดา ชาติสมร",
    "นายวรรณพงษ์ คลังทอง",
    "นายพนมสินธุ์ ผาสุข",
    "นายประเดิม อิ่มโค่น",
]

KNOWN_WORKER_ALIASES = {
    "ณัฐกานต์": "นายณัฐกานต์ ป้อมไชยา",
    "ปนัดดา": "นางปนัดดา ชาติสมร",
    "วรรณพงษ์": "นายวรรณพงษ์ คลังทอง",
    "ณพงษ์ คลังทอง": "นายวรรณพงษ์ คลังทอง",
    "พนมสินธุ์": "นายพนมสินธุ์ ผาสุข",
    "ประเดิม": "นายประเดิม อิ่มโค่น",
    "คณิน": "นายคณิน วาจนสุนทร",
}

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

def _is_valid_rtu_summary_df(df_cand):
    if df_cand is None or len(df_cand) < 100:
        return False
    has_name = bool(get_actual_col_name(df_cand.columns, ["name"], exclude=["dau"]) or get_actual_col_name(df_cand.columns, ["site id"]))
    has_comm = bool(get_actual_col_name(df_cand.columns, ["commu"]) or get_actual_col_name(df_cand.columns, ["ip"]))
    return has_name and has_comm

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

def clean_dnp_str(val):
    s = _s(val)
    if not s:
        return ""
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except Exception:
        pass
    return re.sub(r'\.0$', '', s)

def parse_rtu_alias(alias_str):
    """แยกค่า WRL Master และ ประเภทผลิตภัณฑ์ (FRTU Model) จากคอลัมน์ Alias ในชีต RTU Summary (gid=747048278)"""
    s = _s(alias_str)
    if not s or s.upper() == 'FRTU':
        return "", ""
    parts = [p.strip() for p in s.split('_') if p.strip()]
    if not parts:
        return "", ""
    if parts[0].upper() == 'FRTU':
        wrl = parts[1] if len(parts) >= 2 else ""
        model = parts[2] if len(parts) >= 3 else ""
        return wrl, model
    elif len(parts) >= 2:
        return parts[0], parts[1]
    return "", parts[0]

def fetch_remote_dataframe(urls, validator_fn, normalizer_fn=None, sheet_name=None):
    opener = _build_url_opener()
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with opener.open(req, timeout=20) as resp:
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

def enrich_sheet1_with_update_and_check(df_s1, df_up, df_chk, df_rtu=None):
    """ผสานข้อมูลทุกชีตโดยใช้ Site ID เป็นเกณฑ์หลัก:
    - Update State (gid=0): State SCADA, รหัสสั่งการ, ชนิดอุปกรณ์, การไฟฟ้า, สถานที่
    - RTU Summary (gid=747048278): IP, DNP (Address), Commu., Item, ประเภทผลิตภัณฑ์ (FRTU Model), เชื่อมกับ WRL Master
    - Data_Check (gid=2024067511): ข้อมูลตรวจสอบ/แก้ไขหน้างานจาก Google Form
    """
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

    ip_col = get_actual_col_name(cols, ["ip"], exclude=["site", "script", "description"]) or 'IP'
    dnp_col = get_actual_col_name(cols, ["dnp"]) or 'DNP'
    commu_col = get_actual_col_name(cols, ["commu"]) or 'Commu.'
    item_col = get_actual_col_name(cols, ["item"]) or 'Item'
    frtu_model_col = get_actual_col_name(cols, ["frtu model"]) or 'FRTU Model'
    wrl_master_col = get_actual_col_name(cols, ["wrl master"]) or 'WRL Master'

    frtu_prod_col = 'ชนิดผลิตภัณฑ์ของ FRTU'
    comm_type_col = 'ชนิดของอุปกรณ์สื่อสาร'
    comm_item_col = 'ประเภทย่อยอุปกรณ์สื่อสาร (Item)'
    comm_ref_col = 'แม่ข่ายสื่อสาร (WRL Master / Base)'

    for extra_c in [ip_col, dnp_col, commu_col, item_col, frtu_model_col, wrl_master_col, frtu_prod_col, comm_type_col, comm_item_col, comm_ref_col]:
        if extra_c not in cols:
            cols.append(extra_c)

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

    rtu_map = {}
    if df_rtu is not None and len(df_rtu) > 0:
        rtu_site_col = (
            get_actual_col_name(df_rtu.columns, ["name"], exclude=["dau"])
            or get_actual_col_name(df_rtu.columns, ["site id"])
            or df_rtu.columns[0]
        )
        for r in df_rtu.to_dict('records'):
            sid = _s(r.get(rtu_site_col)).upper()
            if sid:
                rtu_map[sid] = r

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

        # 1. อัปเดตจาก Update State (gid=0) โดยยึด Site ID เป็นเกณฑ์
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

        # 2. อัปเดตจาก RTU Summary (gid=747048278) โดยยึด Site ID เป็นเกณฑ์
        if sid in rtu_map:
            rr = rtu_map[sid]
            r_ip = _s(rr.get('IP'))
            r_commu = _s(rr.get('Commu.'))
            r_item = _s(rr.get('Item'))
            r_dnp = clean_dnp_str(rr.get('Address') if 'Address' in rr else rr.get('DNP'))
            r_wrl, r_model = parse_rtu_alias(rr.get('Alias'))

            if r_ip:
                row[ip_col] = r_ip
            if r_commu:
                row[commu_col] = r_commu
            if r_item:
                row[item_col] = r_item
            if r_dnp:
                row[dnp_col] = r_dnp
            if r_model:
                row[frtu_model_col] = r_model
            if r_wrl:
                row[wrl_master_col] = r_wrl

        # ทำความสะอาดค่า DNP และซิงค์คอลัมน์มาตรฐานสำหรับแสดงผลบนหมุดแผนที่และ Dashboard
        row[dnp_col] = clean_dnp_str(row.get(dnp_col))
        row[frtu_prod_col] = _s(row.get(frtu_model_col)) or _s(row.get(frtu_prod_col))
        row[comm_type_col] = _s(row.get(commu_col)) or _s(row.get(comm_type_col))
        row[comm_item_col] = _s(row.get(item_col)) or _s(row.get(comm_item_col))
        row[comm_ref_col] = _s(row.get(wrl_master_col)) or _s(row.get(comm_ref_col))

        # 3. อัปเดตจาก Data_Check (gid=2024067511) โดยยึด Site ID เป็นเกณฑ์
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

_GIS_MASTER_B64 = "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"
_DATA_CHECK_MASTER_B64 = "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"

def build_gis_base_dataframe():
    """สร้างตารางโครงสร้างพิกัด GIS พื้นฐานพร้อมข้อมูล RTU Summary (gid=747048278) และ Update State (gid=0)"""
    import zlib, base64
    rows = json.loads(zlib.decompress(base64.b64decode(_GIS_MASTER_B64)).decode('utf-8'))
    records = []
    for r in rows:
        sid, cmd, loc, eq_type, lat_lon, pea, pbo_date, init_state = r[:8]
        ip = r[8] if len(r) > 8 else ''
        commu = r[9] if len(r) > 9 else ''
        item = r[10] if len(r) > 10 else ''
        dnp = r[11] if len(r) > 11 else ''
        frtu_model = r[12] if len(r) > 12 else ''
        wrl_master = r[13] if len(r) > 13 else ''
        records.append({
            'รหัสสั่งการ': cmd,
            'Site ID': sid,
            'สถานที่': loc,
            'State SCADA': init_state,
            'State SCADA\n (หลังตรวจสอบ)': init_state,
            'ชนิดอุปกรณ์': eq_type,
            'รายละเอียดการแก้ไขของ ผอส.ฉ.2': '',
            'รายละเอียดการเข้าแก้ไข (กรณีอุปกรณ์สื่อสารชำรุด)': '',
            'LAT, LONG': lat_lon,
            'รอ ผอส. เข้าแก้ไข (ยังไม่ออนไลน์)': '',
            'รอ ผบอ. เข้าแก้ไข (ยังไม่ออนไลน์)': '',
            'การไฟฟ้า': pea,
            'วันที่เข้าตรวจสอบ': '',
            'สถานะการเข้าแก้ไขและออนไลน์ โดย ผบอ.': pbo_date,
            'IP': ip,
            'Commu.': commu,
            'Item': item,
            'DNP': dnp,
            'FRTU Model': frtu_model,
            'WRL Master': wrl_master,
            'ซ้ำ': 'ปกติ',
            'เช็ครหัส': 'ตรงกัน',
            'ชนิดผลิตภัณฑ์ของ FRTU': frtu_model,
            'ชนิดของอุปกรณ์สื่อสาร': commu,
            'ประเภทย่อยอุปกรณ์สื่อสาร (Item)': item,
            'แม่ข่ายสื่อสาร (WRL Master / Base)': wrl_master,
        })
    return pd.DataFrame(records, columns=SHEET1_CANONICAL_COLS)

def build_data_check_fallback_dataframe():
    import zlib, base64
    payload = json.loads(zlib.decompress(base64.b64decode(_DATA_CHECK_MASTER_B64)).decode('utf-8'))
    return pd.DataFrame(payload['rows'], columns=payload['cols'])

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
        print(f"[OK] ดึงข้อมูล Update State (gid=0) จาก Google Sheets สำเร็จ! พบ {len(df_remote)} แถว")
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
                print(f"[OK] โหลดข้อมูล Update State จากฐานข้อมูล SQLite สำเร็จ! พบ {len(df_local)} แถว")
                return df_local
    except Exception:
        pass
    return None

def load_rtu_summary_df(conn, prefer_local=False):
    if prefer_local:
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rtu_summary'")
            if cur.fetchone():
                df_local = pd.read_sql("SELECT * FROM rtu_summary", conn)
                if _is_valid_rtu_summary_df(df_local):
                    return df_local
        except Exception:
            pass

    df_remote = fetch_remote_dataframe(
        RTU_SUMMARY_URLS,
        _is_valid_rtu_summary_df,
        normalizer_fn=lambda d: normalize_generic_dataframe(d, ['name', 'commu', 'item', 'ip']),
        sheet_name='RTU Summary',
    )
    if df_remote is not None:
        print(f"[OK] ดึงข้อมูลอุปกรณ์และสื่อสาร RTU Summary (gid=747048278) สำเร็จ! พบ {len(df_remote)} แถว")
        try:
            df_remote.to_sql('rtu_summary', conn, if_exists='replace', index=False)
        except Exception:
            pass
        return df_remote

    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rtu_summary'")
        if cur.fetchone():
            df_local = pd.read_sql("SELECT * FROM rtu_summary", conn)
            if _is_valid_rtu_summary_df(df_local):
                print(f"[OK] โหลดข้อมูล RTU Summary จากฐานข้อมูล SQLite สำเร็จ! พบ {len(df_local)} แถว")
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
        print(f"[OK] ดึงข้อมูลจาก Google Sheets (Data_Check gid=2024067511) สำเร็จ! พบ {len(df_remote)} แถว")
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
            df_fb.to_sql('data_check', conn, if_exists='replace', index=False)
            print(f"[OK] กู้คืนข้อมูล Data_Check สำรองสำเร็จ! พบ {len(df_fb)} แถว")
            return df_fb
    except Exception:
        pass

    print("คำเตือน: ไม่สามารถดึงข้อมูล Data_Check ได้ จะใช้ข้อมูลเท่าที่มีใน ชีต1")
    return None

def load_sheet1_df(conn, df_update=None, df_check=None, df_rtu=None, prefer_local=False):
    """โหลดข้อมูลอุปกรณ์ในระบบและผสานข้อมูลจาก Update State (gid=0), RTU Summary (gid=747048278) และ Data_Check (gid=2024067511) โดยใช้ Site ID เป็นเกณฑ์หลัก"""
    df_base = build_gis_base_dataframe()
    if not prefer_local:
        print("กำลังดึงข้อมูลอุปกรณ์และพิกัด (ชีต1) จาก Google Sheets เข้าสู่ฐานข้อมูล...")
        df_remote = fetch_remote_dataframe(
            SHEET1_URLS,
            _is_valid_sheet1_df,
            normalizer_fn=normalize_sheet1_dataframe,
            sheet_name='ชีต1',
        )
        if df_remote is not None:
            print(f"[OK] ดึงข้อมูล ชีต1 จาก Google Sheets สำเร็จ! พบ {len(df_remote)} อุปกรณ์")
            # ผสานคอลัมน์จาก GIS Master กรณีบางคอลัมน์ใน CSV ขาดหาย
            df_base = enrich_sheet1_with_update_and_check(df_base, df_remote, None, None)
        else:
            print(f"[OK] ใช้โครงสร้างพิกัดและข้อมูลอุปกรณ์ฐาน (GIS Master + RTU Summary): {len(df_base)} อุปกรณ์")

    df_enriched = enrich_sheet1_with_update_and_check(df_base, df_update, df_check, df_rtu=df_rtu)
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
    """หากในระบบยังไม่มีไฟล์ฐานข้อมูลใน CACHE_DIR แต่มีไฟล์ scada_real.db ในโฟลเดอร์โปรเจกต์ ให้คัดลอกมาเป็นฐานข้อมูลตั้งต้น"""
    try:
        if not os.path.exists(DB_PATH) and os.path.exists(LOCAL_SEED_DB):
            shutil.copy2(LOCAL_SEED_DB, DB_PATH)
    except Exception:
        pass

def generate_map():
    global status_hash_map
    status_hash_map = {}
    print("กำลังเชื่อมต่อฐานข้อมูลและดึงข้อมูลล่าสุดจาก Google Sheets (ใช้ Site ID เป็นเกณฑ์หลัก)...")
    _ensure_seed_db()

    # ดึงข้อมูล Update State (gid=0), RTU Summary (gid=747048278) และ Data_Check (gid=2024067511) แบบขนาน
    with ThreadPoolExecutor(max_workers=3) as executor:
        fut_up = executor.submit(
            fetch_remote_dataframe,
            UPDATE_STATE_URLS,
            _is_valid_update_state_df,
            lambda d: normalize_generic_dataframe(d, ['site id', 'state scada']),
            'Update State',
        )
        fut_rtu = executor.submit(
            fetch_remote_dataframe,
            RTU_SUMMARY_URLS,
            _is_valid_rtu_summary_df,
            lambda d: normalize_generic_dataframe(d, ['name', 'commu', 'item', 'ip']),
            'RTU Summary',
        )
        fut_chk = executor.submit(
            fetch_remote_dataframe,
            DATA_CHECK_URLS,
            _is_valid_data_check_df,
            lambda d: normalize_generic_dataframe(d, ['site id', 'equipment_id', 'ผู้เข้าแก้ไข']),
            'Data_Check',
        )
        df_update_remote = fut_up.result()
        df_rtu_remote = fut_rtu.result()
        df_check_remote = fut_chk.result()

    use_temp_db = False
    work_db_path = DB_PATH
    try:
        test_c = sqlite3.connect(DB_PATH, timeout=20)
        test_c.execute("CREATE TABLE IF NOT EXISTS _ping (id INT)")
        test_c.close()
    except Exception:
        use_temp_db = True
        work_db_path = os.path.join(tempfile.gettempdir(), "scada_real_server_work.db")
        if os.path.exists(DB_PATH):
            try:
                shutil.copy2(DB_PATH, work_db_path)
            except Exception:
                pass

    conn = sqlite3.connect(work_db_path, timeout=30)
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

        if df_rtu_remote is not None:
            print(f"[OK] ดึงข้อมูล RTU Summary (gid=747048278) สำเร็จ! พบ {len(df_rtu_remote)} แถว")
            try:
                df_rtu_remote.to_sql('rtu_summary', conn, if_exists='replace', index=False)
            except Exception:
                pass
            df_rtu = df_rtu_remote
        else:
            df_rtu = load_rtu_summary_df(conn, prefer_local=True)

        if df_check_remote is not None:
            print(f"[OK] ดึงข้อมูล Data_Check (gid=2024067511) สำเร็จ! พบ {len(df_check_remote)} แถว")
            try:
                df_check_remote.to_sql('data_check', conn, if_exists='replace', index=False)
            except Exception:
                pass
            df_check = df_check_remote
        else:
            df_check = load_data_check_df(conn, prefer_local=True)

        df = load_sheet1_df(conn, df_update=df_update, df_check=df_check, df_rtu=df_rtu, prefer_local=False)
    finally:
        conn.close()
        if use_temp_db and os.path.exists(work_db_path):
            try:
                shutil.copy2(work_db_path, DB_PATH)
            except Exception:
                pass

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
        'brightgreen': '#008000',
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
        ("ประเภทผลิตภัณฑ์", ["frtu model"], []),
        ("อุปกรณ์สื่อสาร (Commu.)", ["commu."], []),
        ("ประเภทย่อยสื่อสาร (Item)", ["item"], []),
        ("เชื่อมกับ WRL Master", ["wrl master"], []),
        ("IP", ["ip"], ["site", "script", "description"]),
        ("DNP", ["dnp"], []),
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
        actual_col = get_actual_col_name(df.columns, keywords, exclude=excludes)
        if not actual_col:
            if "รหัสสั่งการ" in keywords:
                actual_col = get_actual_col_name(df.columns, ["รหัสอุปกรณ์"])
            elif display_name == "ประเภทผลิตภัณฑ์":
                actual_col = (
                    get_actual_col_name(df.columns, ["ชนิดผลิตภัณฑ์ของ frtu"])
                    or get_actual_col_name(df.columns, ["ผลิตภัณฑ์"], exclude=["รอ ผบอ", "รายละเอียด"])
                )
            elif display_name == "อุปกรณ์สื่อสาร (Commu.)":
                actual_col = get_actual_col_name(df.columns, ["ชนิดของอุปกรณ์สื่อสาร"])
            elif display_name == "ประเภทย่อยสื่อสาร (Item)":
                actual_col = get_actual_col_name(df.columns, ["ประเภทย่อยอุปกรณ์สื่อสาร"])
            elif display_name == "เชื่อมกับ WRL Master":
                actual_col = get_actual_col_name(df.columns, ["แม่ข่ายสื่อสาร"])
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
    all_stations_raw_rows = []

    for row in df.to_dict('records'):
        all_stations_raw_rows.append(row)
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

        prod_val_s = export_row_arr[2 + 5] if len(export_row_arr) > 7 else ''
        comm_val_s = export_row_arr[2 + 6] if len(export_row_arr) > 8 else ''
        item_val_s = export_row_arr[2 + 7] if len(export_row_arr) > 9 else ''
        ref_val_s = export_row_arr[2 + 8] if len(export_row_arr) > 10 else ''
        ip_val_s = export_row_arr[2 + 9] if len(export_row_arr) > 11 else ''
        dnp_val_s = export_row_arr[2 + 10] if len(export_row_arr) > 12 else ''
        extra_search_str = f"{ip_val_s} {dnp_val_s} {prod_val_s} {comm_val_s} {item_val_s} {ref_val_s}".strip()

        search_data.append({
            "id": site_id,
            "code": cmd_code,
            "name": location_name,
            "lat": round(lat, 7),
            "lon": round(lon, 7),
            "safe_id": safe_site_id,
            "status": active_status_display,
            "raw_parent": raw_parent,
            "color": h_color,
            "icon": icon_name,
            "idx": node_idx,
            "norm": [_pre_norm_search(site_id), _pre_norm_search(cmd_code), _pre_norm_search(location_name), _pre_norm_search(extra_search_str)],
            "tone": [_pre_tone_search(site_id), _pre_tone_search(cmd_code), _pre_tone_search(location_name), _pre_tone_search(extra_search_str)],
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

    # สร้างข้อมูลสำหรับ Dashboard ให้ครบทุกแถวในระบบ (รวมอุปกรณ์ที่ยังไม่มีพิกัด LAT, LONG เช่น Circuit Switcher 115 kV)
    _safe_id_by_site = {item["id"]: item["safe_id"] for item in search_data if item.get("id")}
    dash_all_data_list = []
    for _r in all_stations_raw_rows:
        _sid = str(_r.get(site_col_report)).strip() if site_col_report and pd.notna(_r.get(site_col_report)) else 'Unknown'
        if not _sid or _sid.lower() in ('nan', 'none', ''):
            continue
        _base_st = str(_r.get(state_scada_col)).strip() if state_scada_col and pd.notna(_r.get(state_scada_col)) else 'Unknown'
        _chk_st = str(_r.get(state_after_col)).strip() if state_after_col and pd.notna(_r.get(state_after_col)) else ''
        _act_st = _chk_st if _chk_st and _chk_st.lower() not in ['nan', 'ไม่มีค่า', 'none'] else _base_st
        _arr = [_act_st, _sid, _safe_id_by_site.get(_sid, '')]
        for _d_name, _a_col in resolved_display_cols:
            _v = _r.get(_a_col) if _a_col and pd.notna(_r.get(_a_col)) else ''
            _vs = str(_v).strip()
            if not _vs or _vs.lower() in ('nan', 'none', 'nat', 'ไม่มีค่า'):
                _vs = ''
            _arr.append(_vs)
        dash_all_data_list.append(_arr)

    dash_all_json = json.dumps(dash_all_data_list, ensure_ascii=False, separators=(',', ':'))
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
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Prompt:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/exceljs/4.3.0/exceljs.min.js"></script>

    <style>
    :root {{
        --bg-canvas: #f0f2f5;
        --bg-surface: #ffffff;
        --bg-subtle: #f8f9fa;
        --bg-hover: #f1f3f4;
        --bg-active: #e8f0fe;
        --border-hairline: #dadce0;
        --border-subtle: #e4e6eb;
        --text-primary: #1c1e21;
        --text-secondary: #5f6368;
        --text-muted: #80868b;
        --accent-primary: #1a73e8;
        --accent-hover: #1557b0;
        --shadow-card: 0 1px 2px rgba(60,64,67,0.08), 0 1px 3px rgba(60,64,67,0.04);
        --shadow-float: 0 4px 16px rgba(0,0,0,0.12), 0 1px 3px rgba(0,0,0,0.08);
        --seg-bg: #e4e6eb;
        --seg-pill: #ffffff;
    }}

    body.theme-dark {{
        --bg-canvas: #18191a;
        --bg-surface: #242526;
        --bg-subtle: #1c1d1e;
        --bg-hover: #303031;
        --bg-active: rgba(69,153,255,0.16);
        --border-hairline: #3e4042;
        --border-subtle: #323436;
        --text-primary: #e4e6eb;
        --text-secondary: #b0b3b8;
        --text-muted: #8a8d91;
        --accent-primary: #4599ff;
        --accent-hover: #2d88ff;
        --shadow-card: 0 1px 3px rgba(0,0,0,0.35);
        --shadow-float: 0 8px 24px rgba(0,0,0,0.5);
        --seg-bg: #3a3b3c;
        --seg-pill: #4e4f50;
    }}

    @page {{
        size: A4 landscape;
        margin: 10mm;
    }}
    @media print {{
        .top-nav-switcher, .standalone-filter-btn, .standalone-report-btn, .g-search-container, .g-layer-container, .dash-toolbar-card {{
            display: none !important;
        }}
        .scada-dashboard-view {{
            position: static !important;
            height: auto !important;
            overflow: visible !important;
            background: #ffffff !important;
        }}
    }}

    * {{
        font-family: 'Prompt', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        outline: none !important;
        -webkit-tap-highlight-color: transparent !important;
        box-sizing: border-box;
        scrollbar-width: thin;
        scrollbar-color: rgba(128,134,139,0.35) transparent;
    }}
    ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
    ::-webkit-scrollbar-track {{ background: transparent; }}
    ::-webkit-scrollbar-thumb {{ background-color: rgba(128,134,139,0.35); border-radius: 999px; }}
    ::-webkit-scrollbar-thumb:hover {{ background-color: rgba(128,134,139,0.6); }}

    .leaflet-top {{ z-index: 999 !important; }}
    .leaflet-bottom {{ z-index: 998 !important; }}
    .leaflet-top.leaflet-left .leaflet-control-layers, .leaflet-top.leaflet-right .leaflet-control-layers {{ display: none !important; }}
    .leaflet-popup {{ visibility: hidden !important; opacity: 0 !important; pointer-events: none !important; position: absolute !important; }}

    /* --- Map Pins & Clusters --- */
    .map-pin-inner {{ position: relative; width: 30px; height: 42px; display: flex; justify-content: center; transition: transform 0.16s ease, opacity 0.16s ease; will-change: transform, opacity; contain: layout style; }}
    .pin-shape {{ position: absolute; top: 0; left: 0; width: 30px; height: 30px; border: 2px solid #ffffff; border-radius: 50% 50% 50% 0; transform: rotate(-45deg); box-shadow: 0 2px 5px rgba(0,0,0,0.32); }}
    .map-pin-icon {{ position: relative; color: #ffffff; font-size: 14px; margin-top: 6px; z-index: 1; pointer-events: none; }}
    .map-cluster-inner {{ color: #ffffff; border-radius: 50%; width: 44px; height: 44px; display: flex; flex-direction: column; justify-content: center; align-items: center; font-weight: 600; border: 2px solid #ffffff; box-shadow: 0 2px 6px rgba(0,0,0,0.32); text-shadow: 0 1px 2px rgba(0,0,0,0.5); transition: transform 0.16s ease, opacity 0.16s ease; will-change: transform, opacity; contain: layout style; }}
    .map-cluster-icon {{ font-size: 12px; margin-bottom: 2px; pointer-events: none; }}
    .map-cluster-count {{ font-size: 13px; line-height: 1; pointer-events: none; }}

    .selected-pin-glow {{ transform: scale(1.38) !important; z-index: 100000 !important; }}
    .selected-pin-glow .pin-shape {{ box-shadow: 0 0 0 3px #ffffff, 0 0 16px 5px rgba(26,115,232,0.85) !important; border-color: #1a73e8 !important; }}

    body.filter-hover-active .map-pin-inner, body.filter-hover-active .map-cluster-inner {{ opacity: 0.18; }}
    body.filter-hover-active .map-pin-inner.highlight-active, body.filter-hover-active .map-cluster-inner.highlight-active {{ opacity: 1 !important; transform: scale(1.2); }}

    /* --- Google Maps Style Search Box --- */
    .g-search-container {{ position: fixed; z-index: 100005; top: max(14px, env(safe-area-inset-top, 14px)); left: 16px; width: 360px; margin: 0; pointer-events: none; }}
    .g-search-box {{ pointer-events: auto; background: var(--bg-surface); border-radius: 999px; box-shadow: var(--shadow-float); display: flex; align-items: center; padding: 0 14px; height: 44px; border: 1px solid var(--border-hairline); transition: border-color 0.15s, box-shadow 0.15s; }}
    .g-search-box:hover, .g-search-box.focus {{ border-color: var(--accent-primary) !important; }}
    .g-search-icon {{ display: flex; align-items: center; justify-content: center; width: 22px; height: 22px; color: var(--text-secondary); flex-shrink: 0; }}
    input#searchInput.g-search-input {{ flex: 1 !important; border: none !important; outline: none !important; background: transparent !important; font-size: 13.5px !important; color: var(--text-primary) !important; margin-left: 8px !important; width: 100% !important; pointer-events: auto !important; box-shadow: none !important; min-width: 0 !important; }}
    input#searchInput.g-search-input::placeholder {{ color: var(--text-secondary) !important; opacity: 1 !important; }}
    .g-search-clear {{ display: none; color: var(--text-secondary); font-size: 20px; cursor: pointer; padding: 0 6px; line-height: 1; pointer-events: auto; }}
    .g-search-results {{ position: absolute; top: 50px; left: 0; width: 100%; background: var(--bg-surface); border-radius: 14px; box-shadow: var(--shadow-float); display: none; overflow: hidden; max-height: 380px; overflow-y: auto; border: 1px solid var(--border-hairline); pointer-events: auto; }}
    .g-search-item {{ padding: 10px 14px; display: flex; flex-direction: column; cursor: pointer; border-bottom: 1px solid var(--border-subtle); transition: background-color 0.15s; gap: 4px; color: var(--text-primary); }}
    .g-search-item:last-child {{ border-bottom: none; }}
    .g-search-item:hover {{ background: var(--bg-hover); }}

    /* --- Top Action Buttons (Map View) --- */
    .top-action-btn {{ position: fixed; top: max(14px, env(safe-area-inset-top, 14px)); width: 44px; height: 44px; background: var(--bg-surface); border-radius: 50%; box-shadow: var(--shadow-float); display: flex; align-items: center; justify-content: center; cursor: pointer; z-index: 100000; transition: all 0.15s ease; border: 1px solid var(--border-hairline); pointer-events: auto; }}
    .top-action-btn:hover {{ background-color: var(--bg-hover); transform: translateY(-1px); }}
    .top-action-btn:active {{ transform: scale(0.95); }}
    .standalone-filter-btn {{ right: 16px; }}
    .standalone-report-btn {{ right: 68px; background-color: var(--accent-primary) !important; border-color: var(--accent-primary) !important; }}
    .standalone-report-btn:hover {{ background-color: var(--accent-hover) !important; }}
    .top-action-btn svg {{ fill: none; stroke: var(--text-primary); stroke-width: 2; width: 20px; height: 20px; pointer-events: none; }}
    .standalone-report-btn svg {{ stroke: #ffffff !important; width: 19px; height: 19px; }}

    /* --- Segmented Control (Maps / Dashboard) & Apple iOS Mode Switch --- */
    .top-nav-switcher {{
        position: fixed;
        top: max(14px, env(safe-area-inset-top, 14px));
        right: 124px;
        height: 44px;
        background: var(--bg-surface);
        border: 1px solid var(--border-hairline);
        border-radius: 999px;
        padding: 4px 6px 4px 4px;
        display: flex;
        align-items: center;
        gap: 8px;
        z-index: 100008;
        box-shadow: var(--shadow-float);
        pointer-events: auto;
    }}
    body.dashboard-mode-active .g-search-container,
    body.dashboard-mode-active .standalone-filter-btn,
    body.dashboard-mode-active .standalone-report-btn,
    body.dashboard-mode-active .g-layer-container {{
        display: none !important;
    }}
    body.dashboard-mode-active .top-nav-switcher {{
        position: fixed;
        top: 12px;
        right: 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }}

    .segmented-control {{
        display: inline-flex;
        align-items: center;
        background: var(--seg-bg);
        border-radius: 999px;
        padding: 3px;
        gap: 2px;
    }}
    .nav-switch-btn {{
        border: none;
        background: transparent;
        color: var(--text-secondary);
        height: 30px;
        padding: 0 12px;
        border-radius: 999px;
        font-size: 12.5px;
        font-weight: 500;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        cursor: pointer;
        transition: all 0.2s cubic-bezier(0.2, 0.8, 0.2, 1);
        white-space: nowrap;
    }}
    .nav-switch-btn svg {{
        width: 14px;
        height: 14px;
        stroke: currentColor;
        fill: none;
        stroke-width: 2;
        flex-shrink: 0;
    }}
    .nav-switch-btn:hover:not(.is-active) {{
        color: var(--text-primary);
    }}
    .nav-switch-btn.is-active {{
        background: var(--bg-surface);
        color: var(--text-primary);
        font-weight: 600;
        box-shadow: 0 1px 3px rgba(0,0,0,0.14);
    }}
    body.theme-dark .nav-switch-btn.is-active {{
        background: #1a73e8;
        color: #ffffff;
    }}

    .nav-ava-pill {{
        padding: 1px 6px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 600;
        background: #e6f4ea;
        color: #137333;
        font-variant-numeric: tabular-nums;
    }}
    body.theme-dark .nav-switch-btn.is-active .nav-ava-pill {{
        background: rgba(255,255,255,0.22);
        color: #ffffff;
    }}
    .nav-ava-pill.ava-tier-amber {{ background: #fef7e0; color: #b06000; }}
    .nav-ava-pill.ava-tier-orange {{ background: #fce8e6; color: #c5221f; }}
    .nav-ava-pill.ava-tier-red {{ background: #fce8e6; color: #c5221f; }}

    /* --- Apple iOS Sliding Toggle Switch (Dark / Light Mode) --- */
    .apple-theme-switch {{
        position: relative;
        width: 54px;
        height: 30px;
        border-radius: 999px;
        background: #e4e6eb;
        border: 1px solid rgba(0,0,0,0.08);
        padding: 2px;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: space-between;
        transition: background-color 0.28s cubic-bezier(0.4, 0, 0.2, 1), border-color 0.28s ease;
        flex-shrink: 0;
        user-select: none;
    }}
    body.theme-dark .apple-theme-switch {{
        background: #34c759;
        border-color: rgba(255,255,255,0.14);
    }}
    .apple-switch-track-icon {{
        width: 24px;
        height: 24px;
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 1;
        color: #5f6368;
        transition: opacity 0.2s ease;
        pointer-events: none;
    }}
    .apple-switch-track-icon svg {{
        width: 13px;
        height: 13px;
    }}
    body.theme-dark .apple-switch-track-icon.sun-icon {{ color: rgba(255,255,255,0.85); }}
    .apple-switch-thumb {{
        position: absolute;
        top: 2px;
        left: 2px;
        width: 24px;
        height: 24px;
        border-radius: 50%;
        background: #ffffff;
        box-shadow: 0 2px 5px rgba(0,0,0,0.24), 0 0 1px rgba(0,0,0,0.12);
        transition: transform 0.28s cubic-bezier(0.32, 0.72, 0, 1);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 2;
        pointer-events: none;
    }}
    body.theme-dark .apple-switch-thumb {{
        transform: translateX(24px);
        background: #ffffff;
    }}
    .apple-switch-thumb svg {{
        width: 13px;
        height: 13px;
    }}

    /* --- Map Filter Panel --- */
    .custom-filter-wrapper {{ display: none; flex-direction: column; position: fixed; top: calc(max(14px, env(safe-area-inset-top, 14px)) + 52px); right: 16px; width: 336px; max-height: calc(100dvh - 96px) !important; background-color: var(--bg-surface); border-radius: 16px; box-shadow: var(--shadow-float); border: 1px solid var(--border-hairline); overflow: hidden; z-index: 999998; pointer-events: auto; padding-top: 6px; }}
    .custom-filter-wrapper.show {{ display: flex !important; overflow-y: auto !important; overflow-x: hidden !important; }}
    .custom-filter-wrapper form {{ display: flex !important; flex-direction: column !important; margin: 0 !important; padding: 0 !important; }}
    .custom-filter-wrapper .leaflet-control-layers-list {{ overflow: visible !important; padding: 0 0 10px 0 !important; margin: 0 !important; }}
    .custom-filter-wrapper .leaflet-control-layers-overlays {{ display: flex !important; flex-direction: column !important; width: 100% !important; }}
    .custom-filter-wrapper .leaflet-control-layers-group {{ display: flex !important; flex-direction: column !important; width: 100% !important; margin-bottom: 6px !important; }}
    .custom-filter-wrapper label {{ display: flex !important; flex-direction: row !important; align-items: flex-start !important; width: 100% !important; padding: 6px 14px !important; margin: 0 !important; cursor: pointer !important; }}
    .custom-filter-wrapper label:hover {{ background-color: var(--bg-hover) !important; }}
    .custom-filter-wrapper .status-text, .custom-filter-wrapper .parent-text {{ color: var(--text-primary) !important; }}
    .custom-filter-wrapper input[type="checkbox"] {{ flex-shrink: 0 !important; width: 17px !important; height: 17px !important; margin: 2px 12px 0 0 !important; accent-color: var(--accent-primary) !important; cursor: pointer; }}
    .g-export-container {{ padding: 12px 14px !important; background-color: var(--bg-surface) !important; border-top: 1px solid var(--border-subtle); display: flex; gap: 8px; }}
    .g-export-btn {{ background-color: var(--bg-subtle) !important; color: var(--text-primary) !important; border: 1px solid var(--border-hairline) !important; border-radius: 999px !important; height: 40px !important; width: 100% !important; font-size: 13px !important; font-weight: 500 !important; cursor: pointer !important; display: flex !important; align-items: center !important; justify-content: center !important; gap: 8px !important; transition: background-color 0.15s !important; }}
    .g-export-btn:hover {{ background-color: var(--bg-hover) !important; }}
    .g-export-btn svg {{ width: 18px !important; height: 18px !important; fill: currentColor !important; }}

    /* --- Export Report Modal (Clean Google/Meta Dialog) --- */
    .report-modal-overlay, .custom-alert-overlay {{ display: none; position: fixed; inset: 0; width: 100vw; height: 100dvh; background: rgba(0,0,0,0.5); z-index: 9999990 !important; justify-content: center; align-items: center; opacity: 0; transition: opacity 0.2s ease; pointer-events: auto; padding: 12px; }}
    .report-modal-overlay.show, .custom-alert-overlay.show {{ display: flex !important; opacity: 1 !important; }}
    .custom-alert-overlay {{ z-index: 10000000 !important; }}
    .custom-alert-box {{ background: var(--bg-surface); color: var(--text-primary); width: 100%; max-width: 340px; border-radius: 16px; padding: 24px 20px 20px; box-shadow: var(--shadow-float); border: 1px solid var(--border-hairline); text-align: center; }}
    .custom-alert-icon {{ width: 48px; height: 48px; background: rgba(217,48,37,0.12); border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 14px; }}
    .custom-alert-icon svg {{ width: 24px; height: 24px; fill: #d93025; }}
    .custom-alert-title {{ color: var(--text-primary); font-size: 16px; font-weight: 600; margin-bottom: 6px; }}
    .custom-alert-msg {{ color: var(--text-secondary); font-size: 13.5px; margin-bottom: 20px; line-height: 1.5; }}
    .custom-alert-btn {{ background: var(--accent-primary); color: #ffffff; border: none; padding: 10px 20px; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; width: 100%; }}

    .report-modal {{ background: var(--bg-surface); color: var(--text-primary); width: min(96vw, 620px); max-height: calc(100dvh - 24px); overflow-y: auto; border-radius: 16px; padding: 20px; box-shadow: var(--shadow-float); border: 1px solid var(--border-hairline); position: relative; }}
    .report-modal-top {{ display: flex; align-items: center; gap: 12px; margin-bottom: 14px; padding-bottom: 12px; border-bottom: 1px solid var(--border-subtle); padding-right: 36px; }}
    .report-modal-icon {{ width: 40px; height: 40px; flex: 0 0 40px; border-radius: 10px; background: var(--bg-active); display: flex; align-items: center; justify-content: center; }}
    .report-modal-icon svg {{ width: 20px; height: 20px; stroke: var(--accent-primary); fill: none; stroke-width: 2; }}
    .report-eyebrow-row {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 2px; }}
    .report-eyebrow {{ margin: 0; color: var(--accent-primary); font-size: 10.5px; font-weight: 600; letter-spacing: 0.5px; }}
    .report-source-badge {{ display: inline-flex; align-items: center; padding: 1px 7px; border-radius: 999px; background: var(--bg-subtle); border: 1px solid var(--border-hairline); color: var(--text-secondary); font-size: 10.5px; font-weight: 500; }}
    .report-modal h3 {{ color: var(--text-primary); margin: 0; font-size: 17px; font-weight: 600; line-height: 1.3; }}
    .report-subtitle {{ color: var(--text-secondary); margin: 2px 0 0; font-size: 12px; line-height: 1.45; }}
    .report-close-btn {{ position: absolute; top: 16px; right: 16px; display: flex; align-items: center; justify-content: center; width: 32px; height: 32px; border: 1px solid var(--border-hairline); border-radius: 50%; color: var(--text-secondary); background: var(--bg-subtle); font-size: 18px; cursor: pointer; }}
    .report-close-btn:hover {{ background: var(--bg-hover); color: var(--text-primary); }}

    .report-section {{ padding: 12px 14px; margin: 0 0 10px; background: var(--bg-subtle); border: 1px solid var(--border-subtle); border-radius: 12px; }}
    .report-section-heading {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }}
    .report-section-title-wrap {{ display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }}
    .report-section-title {{ color: var(--text-primary); font-size: 13px; font-weight: 600; }}
    .report-section-help {{ color: var(--text-secondary); font-size: 11px; margin: 0; }}
    .report-worker-count {{ display: inline-flex; align-items: center; border: 1px solid var(--border-hairline); border-radius: 999px; padding: 2px 9px; background: var(--bg-surface); color: var(--text-secondary); font-size: 11px; }}

    .date-preset-group {{ display: inline-flex; align-items: center; gap: 4px; flex-wrap: wrap; }}
    .date-preset-btn {{ border: 1px solid var(--border-hairline); background: var(--bg-surface); color: var(--text-secondary); border-radius: 6px; padding: 3px 8px; font-size: 11px; font-weight: 500; cursor: pointer; }}
    .date-preset-btn:hover {{ background: var(--bg-hover); color: var(--text-primary); }}
    .date-preset-btn.is-active {{ color: var(--accent-primary); border-color: var(--accent-primary); background: var(--bg-active); }}

    .date-flex-container {{ display: flex; gap: 10px; }}
    .date-input-group {{ display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 0; }}
    .date-input-group label {{ color: var(--text-secondary); font-size: 11.5px; font-weight: 500; }}
    .date-input-wrap {{ position: relative; display: flex; align-items: center; }}
    .date-input {{ width: 100%; padding: 8px 28px 8px 10px; border-radius: 8px; border: 1px solid var(--border-hairline); background: var(--bg-surface); color: var(--text-primary); font-size: 12.5px; min-height: 36px; }}
    .date-input:focus {{ border-color: var(--accent-primary); }}
    .date-clear-btn {{ position: absolute; right: 6px; width: 20px; height: 20px; border: 0; border-radius: 50%; background: transparent; color: var(--text-secondary); font-size: 14px; cursor: pointer; display: none; align-items: center; justify-content: center; }}
    .date-clear-btn.show {{ display: inline-flex; }}

    .report-worker-mode-row {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }}
    .report-mode-title {{ color: var(--text-secondary); font-size: 11.5px; }}
    .report-worker-mode-switch {{ display: inline-flex; border: 1px solid var(--border-hairline); background: var(--bg-surface); border-radius: 8px; padding: 2px; gap: 2px; }}
    .report-mode-btn {{ border: none; background: transparent; color: var(--text-secondary); padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 500; cursor: pointer; }}
    .report-mode-btn.is-active {{ background: var(--bg-active); color: var(--accent-primary); font-weight: 600; }}

    .report-worker-picker {{ position: relative; width: 100%; }}
    .report-worker-tags {{ display: flex; align-items: center; flex-wrap: wrap; gap: 5px; min-height: 38px; width: 100%; padding: 5px 8px; background: var(--bg-surface); border: 1px solid var(--border-hairline); border-radius: 8px; cursor: text; }}
    .report-worker-tags:focus-within {{ border-color: var(--accent-primary); }}
    .worker-search-icon {{ width: 15px; height: 15px; fill: var(--text-secondary); flex-shrink: 0; }}
    .report-worker-tags .report-worker-search {{ flex: 1 1 150px; min-width: 110px; min-height: 24px; padding: 2px 4px; font-size: 12px; color: var(--text-primary); background: transparent; border: 0; }}
    .worker-chip {{ display: inline-flex; align-items: center; gap: 5px; padding: 2px 6px 2px 8px; border: 1px solid var(--border-hairline); border-radius: 999px; background: var(--bg-subtle); color: var(--text-primary); font-size: 11px; }}
    .worker-chip-avatar {{ width: 16px; height: 16px; border-radius: 50%; background: var(--accent-primary); color: #fff; font-size: 9.5px; font-weight: 600; display: inline-flex; align-items: center; justify-content: center; }}
    .worker-chip-remove {{ border: 0; background: transparent; color: var(--text-secondary); cursor: pointer; font-size: 14px; line-height: 1; padding: 0 2px; }}

    .report-worker-dropdown {{ display: none; position: fixed; max-height: 230px; overflow-y: auto; z-index: 9999999; padding: 6px; background: var(--bg-surface); border: 1px solid var(--border-hairline); border-radius: 10px; box-shadow: var(--shadow-float); }}
    .report-worker-dropdown.show {{ display: block; }}
    .worker-dropdown-header {{ display: flex; align-items: center; justify-content: space-between; padding: 4px 8px 6px; border-bottom: 1px solid var(--border-subtle); margin-bottom: 4px; font-size: 11px; color: var(--text-secondary); }}
    .worker-dropdown-actions {{ display: inline-flex; gap: 8px; }}
    .worker-dropdown-action-link {{ border: 0; background: transparent; color: var(--accent-primary); font-size: 11px; font-weight: 500; cursor: pointer; padding: 0; }}
    .worker-dropdown-option {{ display: flex; align-items: center; gap: 8px; width: 100%; padding: 6px 8px; border: 1px solid transparent; border-radius: 6px; background: transparent; text-align: left; cursor: pointer; color: var(--text-primary); }}
    .worker-dropdown-option:hover {{ background: var(--bg-hover); }}
    .worker-dropdown-option.is-selected {{ background: var(--bg-active); border-color: rgba(26,115,232,0.3); }}
    .worker-dropdown-option .worker-avatar {{ width: 24px; height: 24px; flex: 0 0 24px; border-radius: 50%; background: var(--bg-subtle); color: var(--text-primary); font-size: 11px; font-weight: 600; display: flex; align-items: center; justify-content: center; border: 1px solid var(--border-hairline); }}
    .worker-dropdown-option.is-selected .worker-avatar {{ background: var(--accent-primary); color: #fff; border-color: var(--accent-primary); }}
    .worker-dropdown-info {{ flex: 1; min-width: 0; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }}
    .worker-dropdown-name {{ color: var(--text-primary); font-size: 12px; }}
    .worker-dropdown-tag {{ font-size: 10px; padding: 1px 6px; border-radius: 4px; background: var(--bg-subtle); color: var(--text-secondary); }}
    .worker-dropdown-count {{ font-size: 11px; color: var(--text-secondary); background: var(--bg-subtle); border-radius: 4px; padding: 1px 6px; white-space: nowrap; }}
    .worker-dropdown-mark {{ color: var(--accent-primary); font-size: 13px; width: 18px; text-align: center; font-weight: 600; }}

    .report-selection-footer {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 8px; flex-wrap: wrap; font-size: 11px; color: var(--text-secondary); }}
    .report-footer-btns {{ display: inline-flex; gap: 6px; }}
    .report-mini-btn {{ border-radius: 6px; background: var(--bg-surface); border: 1px solid var(--border-hairline); padding: 4px 10px; font-size: 11px; color: var(--text-primary); cursor: pointer; }}
    .report-mini-btn:hover {{ background: var(--bg-hover); }}

    /* --- Advance Filter in Map Export Modal --- */
    .adv-filter-container {{ margin: 0 0 10px; background: var(--bg-subtle); border: 1px solid var(--border-subtle); border-radius: 12px; overflow: hidden; }}
    .adv-filter-container.is-open, .adv-filter-container.has-active {{ border-color: var(--accent-primary); }}
    .adv-filter-trigger {{ width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 14px; background: transparent; border: 0; color: var(--text-primary); cursor: pointer; text-align: left; font-size: 12.5px; font-weight: 500; }}
    .adv-filter-trigger:hover {{ background: var(--bg-hover); }}
    .adv-trigger-left {{ display: flex; align-items: center; gap: 8px; min-width: 0; flex-wrap: wrap; }}
    .adv-trigger-title {{ display: inline-flex; align-items: center; gap: 6px; font-weight: 600; color: var(--text-primary); font-size: 12.5px; }}
    .adv-trigger-title svg {{ width: 15px; height: 15px; stroke: var(--accent-primary); fill: none; stroke-width: 2; }}
    .adv-active-pill {{ display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 999px; background: var(--bg-surface); border: 1px solid var(--border-hairline); color: var(--text-secondary); font-size: 10.5px; }}
    .adv-filter-container.has-active .adv-active-pill {{ background: var(--bg-active); border-color: rgba(26,115,232,0.35); color: var(--accent-primary); font-weight: 500; }}
    .adv-trigger-right {{ color: var(--text-secondary); font-size: 11px; white-space: nowrap; }}
    .adv-drawer {{ display: none; flex-direction: column; gap: 10px; padding: 12px 14px; border-top: 1px solid var(--border-subtle); background: var(--bg-surface); }}
    .adv-drawer.show {{ display: flex; }}
    .adv-dedupe-row {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }}
    .adv-dedupe-label {{ font-size: 12px; color: var(--text-primary); font-weight: 500; }}
    .adv-dedupe-group {{ display: inline-flex; align-items: center; background: var(--bg-subtle); border: 1px solid var(--border-hairline); border-radius: 8px; padding: 2px; gap: 2px; flex-wrap: wrap; }}
    .adv-dedupe-btn {{ border: none; background: transparent; color: var(--text-secondary); padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 500; cursor: pointer; }}
    .adv-dedupe-btn.is-active {{ background: var(--bg-surface); color: var(--accent-primary); font-weight: 600; box-shadow: 0 1px 2px rgba(0,0,0,0.08); }}
    .adv-grid-row {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }}
    .adv-field {{ display: flex; flex-direction: column; gap: 4px; min-width: 0; }}
    .adv-field label {{ font-size: 11px; color: var(--text-secondary); font-weight: 500; }}
    .adv-select {{ width: 100%; background: var(--bg-subtle); border: 1px solid var(--border-hairline); border-radius: 8px; color: var(--text-primary); padding: 6px 8px; font-size: 11.5px; cursor: pointer; }}

    .report-export-summary {{ background: var(--bg-subtle); border: 1px solid var(--border-subtle); border-radius: 10px; padding: 10px 14px; margin: 0 0 14px; display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }}
    .report-export-summary-left {{ display: flex; align-items: center; gap: 10px; min-width: 0; flex: 1; }}
    .report-export-summary-text {{ color: var(--text-primary); font-size: 12px; font-weight: 500; }}
    .report-export-subtext {{ color: var(--text-secondary); font-size: 11px; margin-top: 2px; }}
    .report-preview-count {{ background: var(--bg-active); color: var(--accent-primary); padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap; }}
    .report-preview-count.is-invalid {{ background: #fce8e6; color: #c5221f; white-space: normal; }}

    .report-btn-group {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .report-btn {{ flex: 1; min-height: 40px; padding: 8px 14px; border-radius: 8px; border: none; font-size: 12.5px; font-weight: 500; cursor: pointer; display: flex; justify-content: center; align-items: center; gap: 6px; }}
    .report-btn-cancel {{ background: var(--bg-subtle); color: var(--text-primary); border: 1px solid var(--border-hairline); flex: 0 0 auto; }}
    .report-btn-cancel:hover {{ background: var(--bg-hover); }}
    .report-btn-dl {{ background: var(--accent-primary); color: #ffffff; font-weight: 600; }}
    .report-btn-dl:hover {{ background: var(--accent-hover); }}
    .report-note {{ font-size: 11px; color: var(--text-secondary); margin: 8px 0 0; line-height: 1.45; }}

    /* --- Map Station Popup Card --- */
    .custom-info-panel {{ display: none; position: fixed; top: 68px; left: 16px; width: 360px; max-width: calc(100vw - 32px); max-height: calc(100dvh - 88px); background: var(--bg-surface); color: var(--text-primary); border-radius: 16px; box-shadow: var(--shadow-float); border: 1px solid var(--border-hairline); z-index: 99999; flex-direction: column; overflow: hidden; pointer-events: auto; }}
    .panel-close-btn {{ position: absolute; top: 12px; right: 12px; width: 28px; height: 28px; background: rgba(0,0,0,0.28); border-radius: 50%; display: flex; align-items: center; justify-content: center; cursor: pointer; z-index: 1000; }}
    .custom-popup-card {{ display: flex; flex-direction: column; background: var(--bg-surface); width: 100%; height: 100%; }}
    .popup-header {{ padding: 12px 16px; display: flex; align-items: center; gap: 12px; position: relative; }}
    .popup-icon {{ width: 38px; height: 38px; background: rgba(255,255,255,0.22); border-radius: 50%; display: flex; align-items: center; justify-content: center; flex-shrink: 0; border: 1.5px solid rgba(255,255,255,0.4); }}
    .popup-icon i {{ font-size: 16px; color: #fff; }}
    .popup-title {{ flex: 1; padding-right: 28px; overflow: hidden; }}
    .popup-title h3 {{ margin: 0; font-size: 15.5px; font-weight: 600; color: #fff; line-height: 1.25; }}
    .popup-title span {{ font-size: 12px; color: rgba(255,255,255,0.95); display: block; margin-top: 2px; line-height: 1.35; }}
    .popup-actions {{ display: flex; gap: 8px; padding: 10px 14px; background: var(--bg-subtle); border-bottom: 1px solid var(--border-subtle); flex-shrink: 0; }}
    .btn-map {{ flex: 1; padding: 7px 10px; border-radius: 8px; font-size: 12px; font-weight: 500; text-decoration: none; display: flex; align-items: center; justify-content: center; gap: 5px; color: #ffffff !important; }}
    .btn-google {{ background-color: #1a73e8; }}
    .btn-apple {{ background-color: #202124; }}
    .popup-body {{ padding: 0 14px 14px; max-height: calc(100dvh - 210px); overflow-y: auto; background: var(--bg-surface); }}
    .popup-tech-card {{ margin: 10px 0 8px; padding: 10px 12px; border-radius: 10px; background: var(--bg-subtle); border: 1px solid var(--border-subtle); }}
    .popup-tech-title {{ font-size: 11px; font-weight: 600; color: var(--accent-primary); margin-bottom: 6px; }}
    .popup-tech-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }}
    .popup-tech-chip {{ background: var(--bg-surface); border: 1px solid var(--border-subtle); border-radius: 8px; padding: 5px 8px; display: flex; flex-direction: column; gap: 1px; min-width: 0; }}
    .popup-tech-chip.full-span {{ grid-column: 1 / -1; }}
    .popup-tech-chip span {{ font-size: 10px; color: var(--text-secondary); }}
    .popup-tech-chip strong {{ font-size: 11.5px; color: var(--text-primary); font-weight: 600; word-break: break-word; }}
    .popup-table {{ width: 100%; border-collapse: collapse; }}
    .popup-table tr {{ border-bottom: 1px solid var(--border-subtle); }}
    .popup-table tr:last-child {{ border-bottom: none; }}
    .popup-table td {{ padding: 7px 0; font-size: 12px; line-height: 1.4; word-break: break-word; }}
    .popup-table td:first-child {{ color: var(--text-secondary); font-weight: 500; width: 44%; vertical-align: top; padding-right: 8px; }}
    .popup-table td:last-child {{ color: var(--text-primary); font-weight: 500; text-align: right; vertical-align: top; }}

    /* --- Map Bottom Layer Switcher & Right Controls --- */
    .custom-right-controls {{ display: flex; flex-direction: column; gap: 10px; align-items: center; margin-right: 14px; margin-bottom: 24px; z-index: 500 !important; }}
    .g-zoom-container {{ width: 40px; background-color: var(--bg-surface); border-radius: 10px; box-shadow: var(--shadow-float); border: 1px solid var(--border-hairline); display: flex; flex-direction: column; overflow: hidden; }}
    .g-zoom-btn {{ width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; font-size: 20px; color: var(--text-primary); background: transparent; cursor: pointer; border: none; }}
    .g-zoom-btn:hover {{ background-color: var(--bg-hover); }}
    .g-zoom-in {{ border-bottom: 1px solid var(--border-subtle); }}
    .g-locate-btn {{ width: 40px !important; height: 40px !important; background-color: var(--bg-surface) !important; color: var(--accent-primary) !important; border-radius: 10px !important; box-shadow: var(--shadow-float) !important; border: 1px solid var(--border-hairline); display: flex !important; align-items: center; justify-content: center; cursor: pointer; text-decoration: none !important; }}
    .g-locate-btn svg {{ width: 20px; height: 20px; fill: var(--accent-primary); transform: rotate(45deg) translate(-1px, 2px); }}

    .my-location-container {{ position: relative; width: 60px; height: 60px; display: flex; align-items: center; justify-content: center; }}
    .my-location-cone {{ position: absolute; width: 60px; height: 60px; border-radius: 50%; background: conic-gradient(from 225deg at 50% 50%, rgba(26,115,232,0) 0deg, rgba(26,115,232,0.38) 45deg, rgba(26,115,232,0) 90deg, transparent 90deg); pointer-events: none; }}
    .my-location-dot {{ position: absolute; background-color: #1a73e8; width: 16px; height: 16px; border-radius: 50%; border: 3px solid #fff; box-shadow: 0 2px 6px rgba(0,0,0,0.35); }}

    .g-layer-container {{ position: fixed; bottom: calc(20px + env(safe-area-inset-bottom, 0px)); left: 16px; z-index: 9999; display: flex; align-items: flex-end; pointer-events: none; }}
    .g-layer-main-btn {{ pointer-events: auto; width: 52px; height: 52px; border-radius: 12px; border: 2px solid #ffffff; box-shadow: var(--shadow-float); background-size: cover; background-position: center; cursor: pointer; position: relative; overflow: hidden; }}
    .g-layer-label {{ position: absolute; bottom: 0; left: 0; right: 0; background: rgba(0,0,0,0.65); color: #fff; font-size: 9.5px; text-align: center; padding: 3px 0; font-weight: 500; }}
    .g-layer-panel {{ pointer-events: auto; background: var(--bg-surface); border-radius: 14px; display: flex; gap: 10px; padding: 0; max-width: 0; overflow: hidden; opacity: 0; transition: all 0.25s ease; height: 74px; align-items: center; margin-left: 10px; border: 1px solid var(--border-hairline); box-shadow: var(--shadow-float); }}
    .g-layer-panel.show {{ max-width: 460px !important; padding: 0 14px !important; opacity: 1 !important; }}
    .g-layer-item {{ display: flex; flex-direction: column; align-items: center; cursor: pointer; gap: 4px; padding: 4px; border-radius: 8px; }}
    .g-layer-thumb {{ width: 40px; height: 40px; border-radius: 8px; border: 2px solid transparent; background-size: cover; background-position: center; }}
    .g-layer-item.active .g-layer-thumb {{ border-color: var(--accent-primary); }}
    .g-layer-name {{ font-size: 10.5px; color: var(--text-primary); font-weight: 500; white-space: nowrap; }}

    /* === SCADA DASHBOARD (WORLD-CLASS GOOGLE / META CLEAN PRODUCT DESIGN) === */
    .scada-dashboard-view {{
        display: none;
        position: fixed;
        inset: 0;
        width: 100vw;
        height: 100dvh;
        background: var(--bg-canvas);
        color: var(--text-primary);
        z-index: 100006;
        overflow-y: auto;
        overflow-x: hidden;
        padding: 0 0 40px 0;
        pointer-events: auto;
    }}
    .scada-dashboard-view.show {{ display: block !important; }}

    .dash-sticky-topbar {{
        position: sticky;
        top: 0;
        z-index: 100;
        background: var(--bg-surface);
        border-bottom: 1px solid var(--border-subtle);
        padding: 14px 20px;
        margin-bottom: 16px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.04);
    }}
    .dash-sticky-inner {{
        max-width: 1440px;
        margin: 0 auto;
        padding-right: 320px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        flex-wrap: wrap;
    }}
    .dash-sticky-title {{
        margin: 0;
        font-size: 18px;
        font-weight: 600;
        color: var(--text-primary);
        letter-spacing: -0.2px;
        line-height: 1.35;
    }}

    .dash-container {{
        max-width: 1440px;
        margin: 0 auto;
        padding: 0 20px;
        display: flex;
        flex-direction: column;
        gap: 14px;
    }}

    /* Toolbar Card */
    .dash-toolbar-card {{
        background: var(--bg-surface);
        border: 1px solid var(--border-subtle);
        border-radius: 12px;
        box-shadow: var(--shadow-card);
        padding: 12px 16px;
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
    }}
    .dash-controls-bar {{
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 8px;
        min-width: 0;
    }}
    .dash-ctrl-group {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        background: var(--bg-subtle);
        border: 1px solid var(--border-hairline);
        border-radius: 8px;
        padding: 5px 10px;
        max-width: 100%;
        min-width: 0;
    }}
    .dash-ctrl-group label {{
        font-size: 12px;
        color: var(--text-secondary);
        font-weight: 500;
        white-space: nowrap;
        flex-shrink: 0;
    }}
    .dash-select {{
        background: transparent;
        border: none;
        color: var(--text-primary);
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        min-width: 0;
        max-width: 100%;
        text-overflow: ellipsis;
    }}
    .dash-select option {{
        background: var(--bg-surface);
        color: var(--text-primary);
    }}
    .dash-action-btn {{
        border: 1px solid var(--border-hairline);
        background: var(--bg-surface);
        color: var(--text-primary);
        border-radius: 8px;
        padding: 6px 12px;
        font-size: 12px;
        font-weight: 500;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        transition: background-color 0.15s, border-color 0.15s;
        white-space: nowrap;
    }}
    .dash-action-btn:hover {{ background: var(--bg-hover); }}
    .dash-action-btn.btn-primary {{
        background: var(--accent-primary);
        border-color: var(--accent-primary);
        color: #ffffff;
        font-weight: 600;
    }}
    .dash-action-btn.btn-primary:hover {{ background: var(--accent-hover); }}

    /* Formula & Legend Banner */
    .dash-formula-banner {{
        background: var(--bg-surface);
        border: 1px solid var(--border-subtle);
        border-radius: 12px;
        box-shadow: var(--shadow-card);
        padding: 10px 16px;
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        font-size: 12px;
        color: var(--text-secondary);
    }}
    .formula-code {{
        background: var(--bg-subtle);
        border: 1px solid var(--border-subtle);
        border-radius: 6px;
        padding: 2px 8px;
        color: var(--text-primary);
        font-weight: 500;
        display: inline-block;
        word-break: break-word;
    }}
    .ava-legend-group {{
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
        font-size: 11.5px;
        color: var(--text-secondary);
    }}
    .ava-legend-item {{ display: inline-flex; align-items: center; gap: 5px; }}
    .ava-legend-box {{ width: 9px; height: 9px; border-radius: 50%; display: inline-block; }}

    /* KPI Grid */
    .dash-kpi-grid {{
        display: grid;
        grid-template-columns: 1.35fr repeat(5, 1fr);
        gap: 10px;
    }}
    .dash-kpi-card {{
        background: var(--bg-surface);
        border: 1px solid var(--border-subtle);
        border-radius: 12px;
        box-shadow: var(--shadow-card);
        padding: 14px 15px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        transition: border-color 0.15s, transform 0.15s;
        min-width: 0;
    }}
    .dash-kpi-card.clickable {{ cursor: pointer; }}
    .dash-kpi-card.clickable:hover {{ border-color: var(--accent-primary); }}
    .dash-kpi-card.is-selected {{
        border-color: var(--accent-primary) !important;
        background: var(--bg-active);
    }}
    .kpi-top-row {{ display: flex; align-items: center; justify-content: space-between; gap: 6px; }}
    .kpi-label {{ font-size: 12px; font-weight: 600; color: var(--text-secondary); line-height: 1.3; }}
    .kpi-dot {{ width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }}
    .kpi-main-val {{
        font-size: 26px;
        font-weight: 700;
        color: var(--text-primary);
        line-height: 1.15;
        margin: 6px 0 4px;
        font-variant-numeric: tabular-nums;
    }}
    .dash-kpi-ava .kpi-main-val {{ font-size: 28px; }}
    .kpi-sub-row {{
        font-size: 11px;
        color: var(--text-secondary);
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 4px;
        flex-wrap: wrap;
    }}
    .kpi-bar-bg {{
        width: 100%;
        height: 5px;
        background: var(--bg-subtle);
        border-radius: 999px;
        overflow: hidden;
        margin-top: 8px;
        display: flex;
    }}
    .kpi-bar-fill {{ height: 100%; transition: width 0.25s ease; }}

    /* Dashboard Cards & Grids */
    .dash-grid-2col {{ display: grid; grid-template-columns: 1.25fr 1fr; gap: 14px; }}
    .dash-grid-equal {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .dash-card {{
        scroll-margin-top: 72px;
        background: var(--bg-surface);
        border: 1px solid var(--border-subtle);
        border-radius: 12px;
        box-shadow: var(--shadow-card);
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 10px;
        min-width: 0;
    }}
    .dash-card-header {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        border-bottom: 1px solid var(--border-subtle);
        padding-bottom: 10px;
        flex-wrap: wrap;
    }}
    .dash-card-title {{ margin: 0; font-size: 14.5px; font-weight: 600; color: var(--text-primary); line-height: 1.35; }}
    .dash-card-hint {{ font-size: 11.5px; color: var(--text-secondary); }}

    /* Tables & Sortable Headers */
    .dash-table-wrap {{ width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: 8px; }}
    .dash-table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
    .dash-table th {{
        text-align: left;
        padding: 9px 10px;
        color: var(--text-secondary);
        font-weight: 600;
        font-size: 11.5px;
        border-bottom: 1px solid var(--border-hairline);
        background: var(--bg-subtle);
        white-space: nowrap;
        user-select: none;
    }}
    .dash-table th.sortable-th {{ cursor: pointer; transition: color 0.15s, background-color 0.15s; }}
    .dash-table th.sortable-th:hover {{ color: var(--accent-primary); background: var(--bg-hover); }}
    .dash-table th.num, .dash-table td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .dash-table td {{
        padding: 9px 10px;
        border-bottom: 1px solid var(--border-subtle);
        color: var(--text-primary);
        vertical-align: middle;
    }}
    .dash-table tbody tr.clickable-row {{ cursor: pointer; transition: background-color 0.12s; }}
    .dash-table tbody tr.clickable-row:hover {{ background-color: var(--bg-hover); }}
    .dash-table tbody tr.is-active-filter {{ background-color: var(--bg-active) !important; }}
    .dash-table tbody tr.is-active-filter td:first-child {{ border-left: 3px solid var(--accent-primary); padding-left: 7px; }}
    .dash-table tfoot td {{
        padding: 10px;
        font-weight: 600;
        background: var(--bg-subtle);
        border-top: 1.5px solid var(--border-hairline);
        color: var(--text-primary);
    }}

    .mobile-sub-breakdown {{
        display: none;
        font-size: 10.5px;
        color: var(--text-secondary);
        margin-top: 2px;
        line-height: 1.35;
    }}

    /* %AVA Badges */
    .ava-badge {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 2px 8px;
        border-radius: 999px;
        font-weight: 600;
        font-size: 11.5px;
        font-variant-numeric: tabular-nums;
        white-space: nowrap;
    }}
    .ava-tier-green  {{ background: #e6f4ea; color: #137333; }}
    .ava-tier-amber  {{ background: #fef7e0; color: #b06000; }}
    .ava-tier-orange {{ background: #fef0e6; color: #c25100; }}
    .ava-tier-red    {{ background: #fce8e6; color: #c5221f; }}
    body.theme-dark .ava-tier-green  {{ background: rgba(22,163,74,0.22); color: #4ade80; }}
    body.theme-dark .ava-tier-amber  {{ background: rgba(245,158,11,0.22); color: #fbbf24; }}
    body.theme-dark .ava-tier-orange {{ background: rgba(234,88,12,0.22); color: #fb923c; }}
    body.theme-dark .ava-tier-red    {{ background: rgba(220,38,38,0.22); color: #f87171; }}

    .mini-progress {{
        width: 56px;
        height: 5px;
        background: var(--bg-subtle);
        border-radius: 999px;
        overflow: hidden;
        display: inline-flex;
        vertical-align: middle;
        margin-left: 6px;
    }}
    .mini-progress-bar {{ height: 100%; }}

    .status-pill-sm {{
        display: inline-flex;
        align-items: center;
        padding: 2px 8px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 600;
        color: #ffffff;
        white-space: nowrap;
    }}
    .btn-jump-map {{
        border: 1px solid var(--border-hairline);
        background: var(--bg-surface);
        color: var(--accent-primary);
        border-radius: 6px;
        padding: 3px 8px;
        font-size: 11px;
        font-weight: 500;
        cursor: pointer;
        white-space: nowrap;
    }}
    .btn-jump-map:hover {{ background: var(--accent-primary); border-color: var(--accent-primary); color: #ffffff; }}

    .explorer-active-filters {{
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 6px;
        padding: 8px 12px;
        background: var(--bg-active);
        border-radius: 8px;
        font-size: 12px;
        color: var(--accent-primary);
    }}
    .filter-chip-badge {{
        display: inline-flex;
        align-items: center;
        gap: 5px;
        background: var(--bg-surface);
        border: 1px solid rgba(26,115,232,0.3);
        color: var(--text-primary);
        padding: 2px 8px;
        border-radius: 999px;
        font-size: 11.5px;
    }}
    .filter-chip-badge button {{ border: none; background: transparent; color: var(--text-secondary); cursor: pointer; font-size: 14px; line-height: 1; padding: 0 2px; }}
    .explorer-toolbar {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: space-between; }}
    .explorer-search-input {{
        flex: 1 1 240px;
        min-width: 0;
        background: var(--bg-subtle);
        border: 1px solid var(--border-hairline);
        border-radius: 8px;
        padding: 7px 12px;
        color: var(--text-primary);
        font-size: 12.5px;
    }}
    .explorer-search-input:focus {{ border-color: var(--accent-primary); background: var(--bg-surface); }}

    /* === RESPONSIVE MOBILE BREAKPOINTS (แก้ปัญหาดูบนมือถือไม่ครบ 100%) === */
    @media (max-width: 1180px) {{
        .dash-kpi-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
        .dash-grid-2col, .dash-grid-equal {{ grid-template-columns: 1fr; }}
        .dash-sticky-inner {{ padding-right: 0; }}
        body.dashboard-mode-active .top-nav-switcher {{ position: static; margin-left: auto; }}
    }}

    @media (max-width: 768px) {{
        .g-zoom-container {{ display: none !important; }}
        .g-search-container {{
            top: max(10px, env(safe-area-inset-top, 10px)) !important;
            left: 10px !important;
            width: calc(100vw - 114px) !important;
        }}
        .g-search-box {{ height: 42px !important; padding: 0 11px !important; }}
        input#searchInput.g-search-input {{ font-size: 13px !important; margin-left: 6px !important; }}
        .top-action-btn {{
            top: max(10px, env(safe-area-inset-top, 10px)) !important;
            width: 42px !important;
            height: 42px !important;
        }}
        .standalone-filter-btn {{ right: 10px !important; }}
        .standalone-report-btn {{ right: 58px !important; }}

        /* ในโหมดแผนที่บนมือถือ: วางปุ่มสลับหน้าไว้กึ่งกลางด้านล่างแบบกะทัดรัด ไม่ล้นจอ */
        body:not(.dashboard-mode-active) .top-nav-switcher {{
            top: auto !important;
            bottom: calc(16px + env(safe-area-inset-bottom, 0px)) !important;
            left: 72px !important;
            right: 10px !important;
            height: 44px !important;
            justify-content: space-between !important;
            padding: 3px 5px !important;
            gap: 4px !important;
        }}
        /* ในโหมดแดชบอร์ดบนมือถือ: ฝังแถบสลับหน้าไว้ด้านบนสุดใน Header ไม่ลอยทับตารางด้านล่าง */
        body.dashboard-mode-active .top-nav-switcher {{
            position: fixed !important;
            top: max(8px, env(safe-area-inset-top, 8px)) !important;
            left: 10px !important;
            right: 10px !important;
            bottom: auto !important;
            height: 42px !important;
            justify-content: space-between !important;
            padding: 3px 6px !important;
            z-index: 100010 !important;
        }}
        body.dashboard-mode-active .dash-sticky-topbar {{
            padding: calc(max(8px, env(safe-area-inset-top, 8px)) + 48px) 12px 10px 12px !important;
            margin-bottom: 10px !important;
        }}
        .segmented-control {{ flex: 1; min-width: 0; justify-content: stretch; }}
        .nav-switch-btn {{
            flex: 1;
            justify-content: center;
            padding: 0 8px !important;
            font-size: 11.5px !important;
            height: 30px !important;
            gap: 4px !important;
            min-width: 0;
        }}
        .nav-ava-pill {{ font-size: 10.5px !important; padding: 1px 5px !important; }}

        .dash-sticky-title {{ font-size: 15px !important; }}
        .dash-container {{ padding: 0 10px !important; gap: 10px !important; }}
        .dash-toolbar-card {{ padding: 10px 12px !important; }}
        .dash-controls-bar {{ width: 100% !important; }}
        .dash-ctrl-group {{ width: 100% !important; justify-content: space-between; }}
        .dash-select {{ flex: 1; text-align: right; max-width: calc(100% - 75px); }}
        .dash-action-btn {{ flex: 1; justify-content: center; }}

        .dash-kpi-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)) !important; gap: 8px !important; }}
        .dash-kpi-card {{ padding: 11px 12px !important; }}
        .kpi-main-val {{ font-size: 21px !important; }}
        .dash-kpi-ava .kpi-main-val {{ font-size: 23px !important; }}
        .kpi-label {{ font-size: 11px !important; }}

        .dash-card {{ padding: 12px !important; }}
        .dash-card-header {{ gap: 8px !important; }}
        .dash-card-title {{ font-size: 13.5px !important; }}

        /* บนมือถือ: ซ่อนคอลัมน์ย่อยที่ทำให้ตารางล้นจอ แล้วแสดงสรุปย่อยใต้ชื่อรายการแทน เพื่อให้เห็น %AVA ครบทุกช่องในหน้าเดียว */
        .dash-table th.hide-on-mobile, .dash-table td.hide-on-mobile {{ display: none !important; }}
        .mobile-sub-breakdown {{ display: block !important; }}
        .mini-progress {{ display: none !important; }}
        .dash-table th, .dash-table td {{ padding: 7px 6px !important; font-size: 11.5px !important; }}
        .ava-badge {{ padding: 2px 6px !important; font-size: 11px !important; }}

        .custom-filter-wrapper {{ top: calc(max(10px, env(safe-area-inset-top, 10px)) + 48px) !important; right: 10px !important; left: 10px !important; width: auto !important; max-height: calc(100dvh - 120px) !important; }}
        .custom-info-panel {{ top: auto !important; bottom: 0 !important; left: 0 !important; width: 100vw !important; max-width: 100vw !important; max-height: 60vh !important; border-radius: 18px 18px 0 0 !important; padding-bottom: env(safe-area-inset-bottom, 0px); }}
        .popup-body {{ max-height: calc(60vh - 120px - env(safe-area-inset-bottom, 0px)) !important; }}
        .date-flex-container {{ flex-direction: column; }}
        .adv-grid-row {{ grid-template-columns: 1fr; }}
        .report-modal {{ padding: 16px; width: calc(100vw - 16px); }}
    }}
    </style>

    <!-- แถบสลับหน้า แผนที่ (Maps) <-> แดชบอร์ด (%AVA) แบบ Segmented Control + สวิตช์โหมดมืด/สว่างสไตล์ Apple -->
    <div class="top-nav-switcher" id="topNavSwitcher">
        <div class="segmented-control" role="tablist" aria-label="สลับมุมมองหลัก">
            <button type="button" class="nav-switch-btn is-active" id="navBtnMap" onclick="window.switchMainView('map'); return false;">
                <svg viewBox="0 0 24 24"><polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/><line x1="8" y1="2" x2="8" y2="18"/><line x1="16" y1="6" x2="16" y2="22"/></svg>
                <span>แผนที่</span>
            </button>
            <button type="button" class="nav-switch-btn" id="navBtnDash" onclick="window.switchMainView('dashboard'); return false;">
                <svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>
                <span>แดชบอร์ด</span>
                <span class="nav-ava-pill" id="navAvaBadge">--%</span>
            </button>
        </div>
        <button type="button" class="apple-theme-switch" id="themeToggleBtn" onclick="window.toggleScadaTheme(); return false;" title="สลับโหมดสว่าง / โหมดมืด" aria-label="สลับโหมดสว่างและโหมดมืด">
            <span class="apple-switch-track-icon sun-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32l1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.32-11.32l1.41-1.41"/></svg>
            </span>
            <span class="apple-switch-track-icon moon-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
            </span>
            <span class="apple-switch-thumb" id="themeSwitchThumb">
                <svg id="themeThumbSvg" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2.2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32l1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.32-11.32l1.41-1.41"/></svg>
            </span>
        </button>
    </div>

    <!-- หน้า Dashboard ภาพรวมระบบ SCADA (%AVA) -->
    <div id="scadaDashboardView" class="scada-dashboard-view" aria-label="SCADA Availability Dashboard">
        <div class="dash-sticky-topbar">
            <div class="dash-sticky-inner">
                <h1 class="dash-sticky-title">รายงานความพร้อมใช้งานอุปกรณ์ในระบบ SCADA (%AVA)</h1>
            </div>
        </div>

        <div class="dash-container">
            <div class="dash-toolbar-card">
                <div class="dash-controls-bar">
                    <div class="dash-ctrl-group">
                        <label for="dashStateBasis">สถานะอ้างอิง:</label>
                        <select id="dashStateBasis" class="dash-select" onchange="window.renderScadaDashboard()">
                            <option value="raw" selected>State SCADA</option>
                        </select>
                    </div>
                    <div class="dash-ctrl-group">
                        <label for="dashEqScope">กลุ่มอุปกรณ์:</label>
                        <select id="dashEqScope" class="dash-select" onchange="window.renderScadaDashboard()">
                            <option value="all">ทั้งหมดในระบบ (1,571 ชุด)</option>
                            <option value="core4">เฉพาะ 4 ชนิดหลัก (Recloser, LBS, CS)</option>
                            <option value="mapped">เฉพาะที่มีพิกัด GIS (1,495 ชุด)</option>
                        </select>
                    </div>
                    <div class="dash-ctrl-group">
                        <label for="dashPeaFilter">การไฟฟ้า:</label>
                        <select id="dashPeaFilter" class="dash-select" onchange="window.renderScadaDashboard()">
                            <option value="all">ทุกการไฟฟ้า</option>
                        </select>
                    </div>
                </div>
                <div class="dash-controls-bar">
                    <button type="button" class="dash-action-btn btn-primary" onclick="window.exportDashboardExcel()">
                        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg>
                        ส่งออก Excel ขนาด A4 (.xlsx)
                    </button>
                </div>
            </div>

            <!-- แถบเกณฑ์และสูตรคำนวณ %AVA -->
            <div class="dash-formula-banner">
                <div>
                    <strong>สูตร %AVA:</strong>
                    <span class="formula-code" id="dashFormulaText">Online / ทั้งหมด × 100</span>
                    <span id="dashFormulaRatioText" style="margin-left:6px;"></span>
                </div>
                <div class="ava-legend-group">
                    <strong>เกณฑ์ตัดสิน (80%):</strong>
                    <span class="ava-legend-item"><span class="ava-legend-box" style="background:#16a34a;"></span> &ge; 80% ผ่านเกณฑ์</span>
                    <span class="ava-legend-item"><span class="ava-legend-box" style="background:#f59e0b;"></span> 70–79.99%</span>
                    <span class="ava-legend-item"><span class="ava-legend-box" style="background:#ea580c;"></span> 50–69.99%</span>
                    <span class="ava-legend-item"><span class="ava-legend-box" style="background:#dc2626;"></span> &lt; 50%</span>
                </div>
            </div>

            <!-- KPI Cards 6 ใบ -->
            <div class="dash-kpi-grid">
                <div class="dash-kpi-card dash-kpi-ava clickable" onclick="window.selectSummaryItem('status', 'all')" id="kpiCardAva">
                    <div class="kpi-top-row">
                        <span class="kpi-label">ความพร้อมใช้งาน (%AVA)</span>
                        <span class="ava-badge ava-tier-green" id="kpiAvaStatusPill">ผ่านเกณฑ์</span>
                    </div>
                    <div class="kpi-main-val" id="kpiAvaVal" style="color:#137333;">--%</div>
                    <div class="kpi-sub-row">
                        <span id="kpiTotalCount">ทั้งหมด -- ชุด</span>
                        <span id="kpiOfflineTotalText" style="color:#d93025;font-weight:500;">ไม่ออนไลน์ -- ชุด</span>
                    </div>
                    <div class="kpi-bar-bg" id="kpiStackedBar"></div>
                </div>

                <div class="dash-kpi-card clickable" onclick="window.selectSummaryItem('status', 'Online')" id="kpiCardOnline">
                    <div class="kpi-top-row">
                        <span class="kpi-label">Online</span>
                        <span class="kpi-dot" style="background:#16a34a;"></span>
                    </div>
                    <div class="kpi-main-val" id="kpiOnlineCount" style="color:#137333;">--</div>
                    <div class="kpi-sub-row">
                        <span id="kpiOnlinePct">--% ของทั้งหมด</span>
                    </div>
                    <div class="kpi-bar-bg"><div class="kpi-bar-fill" id="kpiOnlineBar" style="background:#16a34a;width:0%;"></div></div>
                </div>

                <div class="dash-kpi-card clickable" onclick="window.selectSummaryItem('status', 'Connecting')" id="kpiCardConnecting">
                    <div class="kpi-top-row">
                        <span class="kpi-label">Connecting</span>
                        <span class="kpi-dot" style="background:#ea580c;"></span>
                    </div>
                    <div class="kpi-main-val" id="kpiConnectingCount" style="color:#c25100;">--</div>
                    <div class="kpi-sub-row">
                        <span id="kpiConnectingPct">--% ของทั้งหมด</span>
                        <span id="kpiConnectingShareOffline">--% ที่ไม่ออนไลน์</span>
                    </div>
                    <div class="kpi-bar-bg"><div class="kpi-bar-fill" id="kpiConnectingBar" style="background:#ea580c;width:0%;"></div></div>
                </div>

                <div class="dash-kpi-card clickable" onclick="window.selectSummaryItem('status', 'Offline')" id="kpiCardOffline">
                    <div class="kpi-top-row">
                        <span class="kpi-label">Offline</span>
                        <span class="kpi-dot" style="background:#dc2626;"></span>
                    </div>
                    <div class="kpi-main-val" id="kpiOfflineCount" style="color:#c5221f;">--</div>
                    <div class="kpi-sub-row">
                        <span id="kpiOfflinePct">--% ของทั้งหมด</span>
                        <span id="kpiOfflineShareOffline">--% ที่ไม่ออนไลน์</span>
                    </div>
                    <div class="kpi-bar-bg"><div class="kpi-bar-fill" id="kpiOfflineBar" style="background:#dc2626;width:0%;"></div></div>
                </div>

                <div class="dash-kpi-card clickable" onclick="window.selectSummaryItem('status', 'Initializing')" id="kpiCardInitializing">
                    <div class="kpi-top-row">
                        <span class="kpi-label">Initializing</span>
                        <span class="kpi-dot" style="background:#65a30d;"></span>
                    </div>
                    <div class="kpi-main-val" id="kpiInitCount" style="color:#4d7c0f;">--</div>
                    <div class="kpi-sub-row">
                        <span id="kpiInitPct">--% ของทั้งหมด</span>
                        <span id="kpiInitShareOffline">--% ที่ไม่ออนไลน์</span>
                    </div>
                    <div class="kpi-bar-bg"><div class="kpi-bar-fill" id="kpiInitBar" style="background:#65a30d;width:0%;"></div></div>
                </div>

                <div class="dash-kpi-card clickable" onclick="window.selectSummaryItem('status', 'Telemetry Failure')" id="kpiCardTelemetry">
                    <div class="kpi-top-row">
                        <span class="kpi-label">Telemetry Failure</span>
                        <span class="kpi-dot" style="background:#d97706;"></span>
                    </div>
                    <div class="kpi-main-val" id="kpiTeleCount" style="color:#b06000;">--</div>
                    <div class="kpi-sub-row">
                        <span id="kpiTelePct">--% ของทั้งหมด</span>
                        <span id="kpiTeleShareOffline">--% ที่ไม่ออนไลน์</span>
                    </div>
                    <div class="kpi-bar-bg"><div class="kpi-bar-fill" id="kpiTeleBar" style="background:#d97706;width:0%;"></div></div>
                </div>
            </div>

            <!-- แถวที่ 1: หัวข้อที่ 1 และ หัวข้อที่ 2 -->
            <div class="dash-grid-2col">
                <div class="dash-card">
                    <div class="dash-card-header">
                        <h2 class="dash-card-title">1. ความพร้อมใช้งาน (%AVA) แยกตามชนิดอุปกรณ์</h2>
                        <div class="dash-ctrl-group">
                            <label for="dashEqSort">เรียงตาม:</label>
                            <select id="dashEqSort" class="dash-select" onchange="window.renderScadaDashboard()">
                                <option value="ava_desc" selected>%AVA (มาก → น้อย)</option>
                                <option value="ava_asc">%AVA (น้อย → มาก)</option>
                                <option value="total_desc">ทั้งหมด (มาก → น้อย)</option>
                                <option value="offline_desc">ไม่ออนไลน์ (มาก → น้อย)</option>
                            </select>
                        </div>
                    </div>
                    <div class="dash-table-wrap">
                        <table class="dash-table" id="dashEqTypeTable">
                            <thead>
                                <tr>
                                    <th class="sortable-th" onclick="window.toggleSectionSort('dashEqSort', 'name_asc', 'name_desc')">ชนิดอุปกรณ์</th>
                                    <th class="num sortable-th" onclick="window.toggleSectionSort('dashEqSort', 'total_desc', 'total_asc')">ทั้งหมด</th>
                                    <th class="num sortable-th" style="color:#16a34a;" onclick="window.toggleSectionSort('dashEqSort', 'online_desc', 'online_asc')">Online</th>
                                    <th class="num sortable-th" style="color:#dc2626;" onclick="window.toggleSectionSort('dashEqSort', 'offline_desc', 'offline_asc')">ไม่ออนไลน์</th>
                                    <th class="num hide-on-mobile" style="color:#d97706;">Telemetry</th>
                                    <th class="num hide-on-mobile" style="color:#dc2626;">Offline</th>
                                    <th class="num hide-on-mobile" style="color:#65a30d;">Init</th>
                                    <th class="num hide-on-mobile" style="color:#ea580c;">Connecting</th>
                                    <th class="num sortable-th" onclick="window.toggleSectionSort('dashEqSort', 'ava_desc', 'ava_asc')">%AVA</th>
                                </tr>
                            </thead>
                            <tbody id="dashEqTypeTbody"></tbody>
                            <tfoot id="dashEqTypeTfoot"></tfoot>
                        </table>
                    </div>
                </div>

                <div class="dash-card">
                    <div class="dash-card-header">
                        <h2 class="dash-card-title">2. สถานะการติดตามหน้างานของอุปกรณ์ที่ไม่ออนไลน์</h2>
                        <div class="dash-ctrl-group">
                            <label for="dashRespSort">เรียงตาม:</label>
                            <select id="dashRespSort" class="dash-select" onchange="window.renderScadaDashboard()">
                                <option value="count_desc" selected>จำนวน / สัดส่วน (มาก → น้อย)</option>
                                <option value="count_asc">จำนวน / สัดส่วน (น้อย → มาก)</option>
                            </select>
                        </div>
                    </div>
                    <div class="dash-table-wrap">
                        <table class="dash-table">
                            <thead>
                                <tr>
                                    <th>สถานะการติดตาม / ผู้รับผิดชอบ</th>
                                    <th class="num sortable-th" onclick="window.toggleSectionSort('dashRespSort', 'count_desc', 'count_asc')">จำนวน (ชุด)</th>
                                    <th class="num sortable-th" onclick="window.toggleSectionSort('dashRespSort', 'count_desc', 'count_asc')">สัดส่วน</th>
                                    <th class="num">ดูข้อมูล</th>
                                </tr>
                            </thead>
                            <tbody id="dashRespTbody"></tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- แถวที่ 2: หัวข้อที่ 3 และ หัวข้อที่ 4 -->
            <div id="dashFutureColsNotice"></div>
            <div class="dash-grid-equal">
                <div class="dash-card">
                    <div class="dash-card-header">
                        <h2 class="dash-card-title">3. แยกตามประเภทผลิตภัณฑ์ของ FRTU</h2>
                        <div class="dash-ctrl-group">
                            <label for="dashProdSort">เรียงตาม:</label>
                            <select id="dashProdSort" class="dash-select" onchange="window.renderScadaDashboard()">
                                <option value="ava_desc" selected>%AVA (มาก → น้อย)</option>
                                <option value="ava_asc">%AVA (น้อย → มาก)</option>
                                <option value="total_desc">ทั้งหมด (มาก → น้อย)</option>
                                <option value="offline_desc">ไม่ออนไลน์ (มาก → น้อย)</option>
                            </select>
                        </div>
                    </div>
                    <div class="dash-table-wrap" style="max-height: 360px; overflow-y: auto;">
                        <table class="dash-table">
                            <thead style="position: sticky; top: 0; z-index: 2;">
                                <tr>
                                    <th class="sortable-th" onclick="window.toggleSectionSort('dashProdSort', 'name_asc', 'name_desc')">ประเภทผลิตภัณฑ์</th>
                                    <th class="num sortable-th" onclick="window.toggleSectionSort('dashProdSort', 'total_desc', 'total_asc')">ทั้งหมด</th>
                                    <th class="num sortable-th" style="color:#16a34a;" onclick="window.toggleSectionSort('dashProdSort', 'online_desc', 'online_asc')">Online</th>
                                    <th class="num sortable-th" style="color:#dc2626;" onclick="window.toggleSectionSort('dashProdSort', 'offline_desc', 'offline_asc')">ไม่ออนไลน์</th>
                                    <th class="num hide-on-mobile">สถานะย่อย</th>
                                    <th class="num sortable-th" onclick="window.toggleSectionSort('dashProdSort', 'ava_desc', 'ava_asc')">%AVA</th>
                                </tr>
                            </thead>
                            <tbody id="dashProductTbody"></tbody>
                        </table>
                    </div>
                </div>

                <div class="dash-card">
                    <div class="dash-card-header">
                        <h2 class="dash-card-title">4. แยกตามอุปกรณ์สื่อสาร (Commu. / Item) และ WRL Master</h2>
                        <div class="dash-controls-bar">
                            <div class="dash-ctrl-group">
                                <label for="dashCommGroupMode">มุมมอง:</label>
                                <select id="dashCommGroupMode" class="dash-select" onchange="window.renderScadaDashboard()">
                                    <option value="commu_item" selected>Commu. &bull; Item</option>
                                    <option value="type">ประเภทสื่อสารหลัก (Commu.)</option>
                                    <option value="item">ประเภทย่อย (Item)</option>
                                    <option value="master">แม่ข่าย (WRL Master)</option>
                                </select>
                            </div>
                            <div class="dash-ctrl-group">
                                <label for="dashCommSort">เรียงตาม:</label>
                                <select id="dashCommSort" class="dash-select" onchange="window.renderScadaDashboard()">
                                    <option value="ava_desc" selected>%AVA (มาก → น้อย)</option>
                                    <option value="ava_asc">%AVA (น้อย → มาก)</option>
                                    <option value="total_desc">ทั้งหมด (มาก → น้อย)</option>
                                    <option value="offline_desc">ไม่ออนไลน์ (มาก → น้อย)</option>
                                </select>
                            </div>
                        </div>
                    </div>
                    <div class="dash-table-wrap" style="max-height: 360px; overflow-y: auto;">
                        <table class="dash-table">
                            <thead style="position: sticky; top: 0; z-index: 2;">
                                <tr>
                                    <th class="sortable-th" onclick="window.toggleSectionSort('dashCommSort', 'name_asc', 'name_desc')">อุปกรณ์สื่อสาร / แม่ข่าย</th>
                                    <th class="num sortable-th" onclick="window.toggleSectionSort('dashCommSort', 'total_desc', 'total_asc')">ทั้งหมด</th>
                                    <th class="num sortable-th" style="color:#16a34a;" onclick="window.toggleSectionSort('dashCommSort', 'online_desc', 'online_asc')">Online</th>
                                    <th class="num sortable-th" style="color:#dc2626;" onclick="window.toggleSectionSort('dashCommSort', 'offline_desc', 'offline_asc')">ไม่ออนไลน์</th>
                                    <th class="num sortable-th" onclick="window.toggleSectionSort('dashCommSort', 'ava_desc', 'ava_asc')">%AVA</th>
                                </tr>
                            </thead>
                            <tbody id="dashCommTbody"></tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- แถวที่ 3: หัวข้อที่ 5 แยกตามสังกัดการไฟฟ้า (กฟจ./กฟส.) -->
            <div class="dash-card">
                <div class="dash-card-header">
                    <h2 class="dash-card-title">5. ความพร้อมใช้งาน (%AVA) แยกตามสังกัดการไฟฟ้า (กฟจ. / กฟส.)</h2>
                    <div class="dash-ctrl-group">
                        <label for="dashPeaSort">เรียงตาม:</label>
                        <select id="dashPeaSort" class="dash-select" onchange="window.renderScadaDashboard()">
                            <option value="total_desc" selected>ทั้งหมด (มาก → น้อย)</option>
                            <option value="total_asc">ทั้งหมด (น้อย → มาก)</option>
                            <option value="ava_desc">%AVA (มาก → น้อย)</option>
                            <option value="ava_asc">%AVA (น้อย → มาก)</option>
                            <option value="online_desc">Online (มาก → น้อย)</option>
                            <option value="offline_desc">ไม่ออนไลน์รวม (มาก → น้อย)</option>
                            <option value="name_asc">ชื่อการไฟฟ้า (ก → ฮ)</option>
                        </select>
                    </div>
                </div>
                <div class="dash-table-wrap" style="max-height: 380px; overflow-y: auto;">
                    <table class="dash-table">
                        <thead style="position: sticky; top: 0; z-index: 2;">
                            <tr>
                                <th class="sortable-th" onclick="window.toggleSectionSort('dashPeaSort', 'name_asc', 'name_desc')">การไฟฟ้า</th>
                                <th class="num sortable-th" onclick="window.toggleSectionSort('dashPeaSort', 'total_desc', 'total_asc')">ทั้งหมด</th>
                                <th class="num sortable-th" style="color:#16a34a;" onclick="window.toggleSectionSort('dashPeaSort', 'online_desc', 'online_asc')">Online</th>
                                <th class="num sortable-th" style="color:#dc2626;" onclick="window.toggleSectionSort('dashPeaSort', 'offline_desc', 'offline_asc')">ไม่ออนไลน์</th>
                                <th class="num hide-on-mobile" style="color:#d97706;">Telemetry</th>
                                <th class="num hide-on-mobile" style="color:#dc2626;">Offline</th>
                                <th class="num hide-on-mobile" style="color:#65a30d;">Init</th>
                                <th class="num hide-on-mobile" style="color:#ea580c;">Connecting</th>
                                <th class="num sortable-th" onclick="window.toggleSectionSort('dashPeaSort', 'ava_desc', 'ava_asc')">%AVA</th>
                            </tr>
                        </thead>
                        <tbody id="dashPeaTbody"></tbody>
                    </table>
                </div>
            </div>

            <!-- แถวที่ 4: หัวข้อที่ 6 ตารางข้อมูลอุปกรณ์และการสื่อสาร -->
            <div class="dash-card" id="dashExplorerCard">
                <div class="dash-card-header">
                    <div>
                        <h2 class="dash-card-title">6. ตารางข้อมูลอุปกรณ์และการสื่อสาร</h2>
                        <span class="dash-card-hint" id="dashExplorerSubtitle">แสดงรายการอุปกรณ์ตามตัวกรองที่เลือก</span>
                    </div>
                    <div class="dash-controls-bar">
                        <button type="button" class="dash-action-btn" onclick="window.showAllExplorerRows()">แสดงครบทุกแถว</button>
                        <button type="button" class="dash-action-btn" onclick="window.resetDashExplorerFilters()">ล้างตัวกรอง</button>
                        <button type="button" class="dash-action-btn" onclick="document.getElementById('scadaDashboardView').scrollTo({{top: 0, behavior: 'smooth'}})">↑ บนสุด</button>
                    </div>
                </div>

                <div id="dashActiveFilterBar" class="explorer-active-filters" style="display:none;"></div>

                <div class="explorer-toolbar">
                    <input type="search" id="dashExplorerSearch" class="explorer-search-input" placeholder="ค้นหา Site ID, รหัสสั่งการ, สถานที่, การไฟฟ้า, IP, DNP, ผลิตภัณฑ์, Commu., Item, WRL Master..." oninput="window.renderDashExplorerTable()">
                    <div class="dash-ctrl-group">
                        <label for="dashExplorerStatus">State SCADA:</label>
                        <select id="dashExplorerStatus" class="dash-select" onchange="window.onExplorerDropdownChange()">
                            <option value="all">ทุกสถานะ</option>
                            <option value="ALL_OFFLINE">ไม่ออนไลน์ทั้งหมด</option>
                            <option value="Online">Online</option>
                            <option value="Connecting">Connecting</option>
                            <option value="Offline">Offline</option>
                            <option value="Initializing">Initializing</option>
                            <option value="Telemetry Failure">Telemetry Failure</option>
                        </select>
                    </div>
                    <div class="dash-ctrl-group">
                        <label for="dashExplorerEq">ชนิดอุปกรณ์:</label>
                        <select id="dashExplorerEq" class="dash-select" onchange="window.onExplorerDropdownChange()">
                            <option value="all">ทุกชนิดอุปกรณ์</option>
                        </select>
                    </div>
                    <div class="dash-ctrl-group">
                        <label for="dashExplorerProd">ผลิตภัณฑ์:</label>
                        <select id="dashExplorerProd" class="dash-select" onchange="window.onExplorerDropdownChange()">
                            <option value="all">ทุกผลิตภัณฑ์</option>
                        </select>
                    </div>
                    <div class="dash-ctrl-group">
                        <label for="dashExplorerComm">Commu./Item:</label>
                        <select id="dashExplorerComm" class="dash-select" onchange="window.onExplorerDropdownChange()">
                            <option value="all">ทุกประเภทสื่อสาร</option>
                        </select>
                    </div>
                    <div class="dash-ctrl-group">
                        <label for="dashExplorerWrl">WRL Master:</label>
                        <select id="dashExplorerWrl" class="dash-select" onchange="window.onExplorerDropdownChange()">
                            <option value="all">ทุกแม่ข่าย</option>
                        </select>
                    </div>
                </div>

                <div class="dash-table-wrap" style="max-height: 520px; overflow-y: auto;">
                    <table class="dash-table">
                        <thead style="position: sticky; top: 0; z-index: 2;">
                            <tr>
                                <th>ลำดับ</th>
                                <th>Site ID</th>
                                <th>รหัสสั่งการ</th>
                                <th>สถานที่</th>
                                <th>การไฟฟ้า</th>
                                <th>ชนิดอุปกรณ์</th>
                                <th class="hide-on-mobile">ประเภทผลิตภัณฑ์</th>
                                <th class="hide-on-mobile">Commu.</th>
                                <th class="hide-on-mobile">Item</th>
                                <th class="hide-on-mobile">WRL Master</th>
                                <th class="hide-on-mobile">IP</th>
                                <th class="hide-on-mobile">DNP</th>
                                <th>State SCADA</th>
                                <th>แผนที่</th>
                            </tr>
                        </thead>
                        <tbody id="dashExplorerTbody"></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <!-- ปุ่มลอยบนหน้าแผนที่ -->
    <div id="standaloneReportBtn" class="top-action-btn standalone-report-btn" title="ส่งออกรายงาน Excel (A4)">
        <svg viewBox="0 0 24 24"><path d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/></svg>
    </div>
    <div id="standaloneFilterBtn" class="top-action-btn standalone-filter-btn" title="ตัวกรองสถานะบนแผนที่">
        <svg viewBox="0 0 24 24"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"></polygon></svg>
    </div>

    <!-- หน้าต่างส่งออกรายงานของหน้าแผนที่ (Maps Export Report - ขนาด A4) -->
    <div id="reportModalOverlay" class="report-modal-overlay">
        <div class="report-modal" role="dialog" aria-modal="true" aria-labelledby="reportModalTitle">
            <button class="report-close-btn" type="button" onclick="closeReportModal()" aria-label="ปิดหน้าต่าง">×</button>
            <div class="report-modal-top">
                <div class="report-modal-icon"><svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h8M8 9h2"/></svg></div>
                <div>
                    <div class="report-eyebrow-row">
                        <p class="report-eyebrow">EXPORT REPORT (A4)</p>
                        <span class="report-source-badge" id="reportSourceBadge">Data_Check</span>
                    </div>
                    <h3 id="reportModalTitle">ส่งออกรายงานการตรวจสอบและแก้ไขอุปกรณ์</h3>
                    <p class="report-subtitle">กรองตามช่วงวันที่ ผู้ปฏิบัติงาน และตั้งค่า Advance Filter เพื่อส่งออกไฟล์ Excel ขนาดมาตรฐาน A4 (.xlsx)</p>
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
                        <span class="report-section-title" id="reportWorkerHeading">กรองตามผู้ปฏิบัติงาน</span>
                        <span class="report-section-help">เลือกชื่อเพื่อกรองเฉพาะงานของผู้ปฏิบัติงานที่ต้องการ</span>
                    </div>
                    <span id="reportWorkerCount" class="report-worker-count">ยังไม่เลือกชื่อ</span>
                </div>
                <div class="report-worker-mode-row">
                    <span class="report-mode-title">เงื่อนไขการกรองรายชื่อ</span>
                    <div class="report-worker-mode-switch" role="group" aria-label="เงื่อนไขรายชื่อผู้ปฏิบัติงาน">
                        <button id="reportModeOr" class="report-mode-btn is-active" type="button" aria-pressed="true">OR (คนใดคนหนึ่ง)</button>
                        <button id="reportModeAnd" class="report-mode-btn" type="button" aria-pressed="false">AND (ต้องครบทุกคน)</button>
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

            <!-- Advance Filter (รวมตัวเลือกการแสดงคอลัมน์ผู้ปฏิบัติงาน โดยค่าเริ่มต้น Normally คือไม่แสดง) -->
            <div id="advFilterContainer" class="adv-filter-container">
                <button type="button" id="advExpandBtn" class="adv-filter-trigger" onclick="toggleAdvFilterDrawer()" aria-expanded="false">
                    <div class="adv-trigger-left">
                        <span class="adv-trigger-title">
                            <svg viewBox="0 0 24 24"><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/></svg>
                            Advance Filter
                        </span>
                        <span id="advSummaryPill" class="adv-active-pill">รวมข้อมูลซ้ำทั้งหมด · ไม่แสดงคอลัมน์ผู้ปฏิบัติงาน</span>
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
                            <label for="advWorkerColToggle">คอลัมน์ผู้ปฏิบัติงานในไฟล์ Excel</label>
                            <select id="advWorkerColToggle" class="adv-select" onchange="updateReportPreview()">
                                <option value="hide" selected>ไม่แสดงคอลัมน์ผู้ปฏิบัติงาน (ค่าเริ่มต้น)</option>
                                <option value="show">เพิ่มคอลัมน์ผู้ปฏิบัติงาน (ผอส.ฉ.2)</option>
                            </select>
                        </div>
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
                    <svg viewBox="0 0 24 24" width="18" height="18" stroke="var(--accent-primary)" fill="none" stroke-width="2"><path d="M12 8v4l2.5 2.5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z"/></svg>
                    <div>
                        <div class="report-export-summary-text">จำนวนรายการที่จะส่งออก (จัดหน้าขนาด A4 อัตโนมัติ)</div>
                        <div id="reportPreviewSubtext" class="report-export-subtext">อ้างอิงข้อมูลจาก Data_Check เชื่อมรายละเอียดอุปกรณ์จาก ชีต1</div>
                    </div>
                </div>
                <div id="reportPreviewCount" class="report-preview-count">กำลังตรวจสอบ...</div>
            </div>

            <div class="report-btn-group">
                <button class="report-btn report-btn-cancel" type="button" onclick="resetAllReportFilters()">รีเซ็ตค่า</button>
                <button class="report-btn report-btn-cancel" type="button" onclick="closeReportModal()">ยกเลิก</button>
                <button id="execReportBtn" class="report-btn report-btn-dl" type="button" onclick="generateExcelReport()">
                    <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="white" stroke-width="2"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg> ดาวน์โหลด Excel ขนาด A4 (.xlsx)
                </button>
            </div>
            <p class="report-note">* ไฟล์รายงานตั้งค่าหน้ากระดาษขนาด A4 แนวนอน (Fit to 1 Page Wide) พร้อมพิมพ์ทันที</p>
        </div>
        <div id="reportWorkerDropdown" class="report-worker-dropdown" role="listbox" aria-label="ผลการค้นหาผู้ปฏิบัติงาน"></div>
    </div>

    <!-- Modal แจ้งเตือน -->
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
            <div class="g-search-icon"><svg focusable="false" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" style="width:19px; height:19px;"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"></path></svg></div>
            <input type="text" id="searchInput" class="g-search-input" placeholder="ค้นหา Site ID, รหัสสั่งการ, หรือสถานที่..." autocomplete="off">
            <div class="g-search-clear" id="searchClear" onclick="this.previousElementSibling.value=''; this.style.display='none'; document.getElementById('searchResults').style.display='none'; hideCustomPanel();">×</div>
        </div>
        <div class="g-search-results" id="searchResults"></div>
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
        "ประเภทผลิตภัณฑ์",
        "อุปกรณ์สื่อสาร (Commu.)",
        "ประเภทย่อยสื่อสาร (Item)",
        "เชื่อมกับ WRL Master",
        "IP",
        "DNP",
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
        var ipVal = (row["IP"] && row["IP"] !== 'ไม่มีค่า') ? row["IP"] : '-';
        var dnpVal = (row["DNP"] && row["DNP"] !== 'ไม่มีค่า') ? row["DNP"] : '-';
        var prodVal = (row["ประเภทผลิตภัณฑ์"] && row["ประเภทผลิตภัณฑ์"] !== 'ไม่มีค่า') ? row["ประเภทผลิตภัณฑ์"] : '-';
        var commuVal = (row["อุปกรณ์สื่อสาร (Commu.)"] && row["อุปกรณ์สื่อสาร (Commu.)"] !== 'ไม่มีค่า') ? row["อุปกรณ์สื่อสาร (Commu.)"] : '-';
        var itemVal = (row["ประเภทย่อยสื่อสาร (Item)"] && row["ประเภทย่อยสื่อสาร (Item)"] !== 'ไม่มีค่า') ? row["ประเภทย่อยสื่อสาร (Item)"] : '-';
        var wrlVal = (row["เชื่อมกับ WRL Master"] && row["เชื่อมกับ WRL Master"] !== 'ไม่มีค่า') ? row["เชื่อมกับ WRL Master"] : '-';
        var scadaStateVal = (row["State SCADA"] && row["State SCADA"] !== 'ไม่มีค่า') ? row["State SCADA"] : m.raw_parent;

        var techCardHtml =
            '<div class="popup-tech-card">' +
                '<div class="popup-tech-title">ข้อมูลอุปกรณ์และระบบสื่อสาร (อ้างอิง Site ID)</div>' +
                '<div class="popup-tech-grid">' +
                    '<div class="popup-tech-chip"><span>IP Address</span><strong>' + ipVal + '</strong></div>' +
                    '<div class="popup-tech-chip"><span>DNP Address</span><strong>' + dnpVal + '</strong></div>' +
                    '<div class="popup-tech-chip"><span>ประเภทผลิตภัณฑ์</span><strong>' + prodVal + '</strong></div>' +
                    '<div class="popup-tech-chip"><span>State SCADA</span><strong>' + scadaStateVal + '</strong></div>' +
                    '<div class="popup-tech-chip full-span"><span>อุปกรณ์สื่อสาร (Commu. / Item)</span><strong>' + commuVal + ' &bull; ' + itemVal + '</strong></div>' +
                    '<div class="popup-tech-chip full-span"><span>เชื่อมกับ WRL Master</span><strong>' + wrlVal + '</strong></div>' +
                '</div>' +
            '</div>';

        var gUrl = 'https://www.google.com/maps/dir/?api=1&destination=' + m.lat + ',' + m.lon;
        var aUrl = 'http://maps.apple.com/?daddr=' + m.lat + ',' + m.lon;
        return '<div class="custom-popup-card" data-safe-id="' + m.safe_id + '">' +
            '<div class="popup-header" style="background-color:' + m.color + ';">' +
                '<div class="popup-icon"><i class="fa fa-' + m.icon + '"></i></div>' +
                '<div class="popup-title"><h3>' + m.id + '</h3><span>State SCADA: ' + scadaStateVal + (m.status !== scadaStateVal ? ' (' + m.status + ')' : '') + '</span></div>' +
            '</div>' +
            '<div class="popup-actions">' +
                '<a href="' + gUrl + '" target="_blank" class="btn-map btn-google">' + GOOGLE_MAP_SVG + ' Google Maps</a>' +
                '<a href="' + aUrl + '" target="_blank" class="btn-map btn-apple">' + APPLE_MAP_SVG + ' Apple Maps</a>' +
            '</div>' +
            '<div class="popup-body">' + techCardHtml + '<table class="popup-table">' + rowsHtml + '</table></div>' +
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
            var workerColSel = document.getElementById('advWorkerColToggle');
            if (workerColSel) workerColSel.value = 'hide';
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
    var advDedupeMode = 'all';

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
        var workerColEl = document.getElementById('advWorkerColToggle');
        return {{
            startInput: startEl ? startEl.value.trim() : '',
            endInput: endEl ? endEl.value.trim() : '',
            selectedWorkers: getSelectedReportWorkers(),
            statusFilter: statusEl ? statusEl.value : 'all',
            respFilter: respEl ? respEl.value : 'all',
            sortOrder: sortEl ? sortEl.value : 'status_online',
            includeWorkerCol: workerColEl ? (workerColEl.value === 'show') : false,
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
        if (params.dedupeMode === 'latest') parts.push('ไม่เอาซ้ำ (ล่าสุด)');
        else if (params.dedupeMode === 'earliest') parts.push('ไม่เอาซ้ำ (ตัวแรก)');
        else parts.push('รวมซ้ำทั้งหมด');

        if (params.includeWorkerCol) parts.push('มีคอลัมน์ผู้ปฏิบัติงาน');
        else parts.push('ไม่มีคอลัมน์ผู้ปฏิบัติงาน');

        if (params.statusFilter !== 'all') parts.push('กรองสถานะ');
        if (params.respFilter !== 'all') parts.push('กรองผู้รับผิดชอบ');
        if (params.sortOrder !== 'status_online') parts.push('เรียงลำดับกำหนดเอง');

        var hasActive = params.dedupeMode !== 'all' || params.includeWorkerCol || params.statusFilter !== 'all' || params.respFilter !== 'all' || params.sortOrder !== 'status_online';
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
        var workerColEl = document.getElementById('advWorkerColToggle');
        if (statusEl) statusEl.value = 'all';
        if (respEl) respEl.value = 'all';
        if (sortEl) sortEl.value = 'status_online';
        if (workerColEl) workerColEl.value = 'hide';
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
            var colInfo = params.includeWorkerCol ? 'รวมคอลัมน์ผู้ปฏิบัติงาน (8 คอลัมน์)' : 'ไม่รวมคอลัมน์ผู้ปฏิบัติงาน (7 คอลัมน์ · มาตรฐาน)';
            if (validationMessage) {{
                subtextLabel.textContent = 'กรุณาตรวจสอบช่วงวันที่เริ่มต้นและสิ้นสุดอีกครั้ง';
            }} else if (params.dedupeMode === 'latest') {{
                subtextLabel.textContent = 'ไม่เอาซ้ำ (เอาตัวล่าสุด) · ตัดซ้ำ ' + result.duplicateCount + ' รายการ · ' + colInfo;
            }} else if (params.dedupeMode === 'earliest') {{
                subtextLabel.textContent = 'ไม่เอาซ้ำ (เอาตัวแรก) · ตัดซ้ำ ' + result.duplicateCount + ' รายการ · ' + colInfo;
            }} else {{
                subtextLabel.textContent = 'รวมทั้งหมด (' + result.rawMatchedCount + ' รายการ · ไม่ซ้ำ ' + result.uniqueStationCount + ' อุปกรณ์) · ' + colInfo;
            }}
        }}
        updateAdvExpandBadge();
    }}

    function positionReportWorkerDropdown() {{
        var picker = document.getElementById('reportWorkerPicker');
        var dropdown = document.getElementById('reportWorkerDropdown');
        if (!picker || !dropdown) return;
        var rect = picker.getBoundingClientRect();
        var dropdownHeight = Math.min(230, Math.max(120, window.innerHeight - 24));
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
            empty.style.padding = '14px';
            empty.style.textAlign = 'center';
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
        }}
        positionReportWorkerDropdown();
        dropdown.classList.add('show');
    }}

    function openReportWorkerDropdown() {{ renderReportWorkerDropdown(); }}
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
            if (dn && mergedNames.indexOf(dn) === -1) mergedNames.push(dn);
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

    document.getElementById('reportWorkerSearch').addEventListener('input', function() {{ filterReportWorkerOptions(); updateReportPreview(); }});
    document.getElementById('reportWorkerSearch').addEventListener('search', function() {{ filterReportWorkerOptions(); updateReportPreview(); }});
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
            if (!document.getElementById('reportWorkerDropdown').classList.contains('show')) openReportWorkerDropdown();
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
        btn.disabled = true; btn.innerHTML = 'กำลังสร้างไฟล์ A4...';

        var params = getCurrentFilterParams();
        var startInput = params.startInput;
        var endInput = params.endInput;
        var selectedWorkers = params.selectedWorkers;
        var includeWorkerCol = params.includeWorkerCol; // Normally false (ไม่มีคอลัมน์ผู้ปฏิบัติงาน)

        var dateValidationMessage = validateReportDateRange(startInput, endInput);
        if (dateValidationMessage) {{
            showCustomAlert(dateValidationMessage);
            btn.disabled = false;
            btn.innerHTML = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="white" stroke-width="2"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg> ดาวน์โหลด Excel ขนาด A4 (.xlsx)';
            return;
        }}

        var filteredResult = getFilteredReportResult();
        var filteredData = filteredResult.rows;

        if (filteredData.length === 0) {{
            showCustomAlert("ไม่พบข้อมูลรายงานที่ตรงกับช่วงวันที่และตัวกรองที่เลือก");
            btn.disabled = false;
            btn.innerHTML = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="white" stroke-width="2"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg> ดาวน์โหลด Excel ขนาด A4 (.xlsx)';
            return;
        }}

        var workbook = new ExcelJS.Workbook();
        var ws = workbook.addWorksheet('Report', {{
            pageSetup: {{
                paperSize: 9, // A4 Standard
                orientation: 'landscape',
                fitToPage: true,
                fitToWidth: 1,
                fitToHeight: 0,
                horizontalCentered: true,
                printTitlesRow: '1:2',
                margins: {{ left: 0.25, right: 0.25, top: 0.4, bottom: 0.4, header: 0.2, footer: 0.2 }}
            }}
        }});

        var lastColLetter = includeWorkerCol ? 'H' : 'G';
        ws.mergeCells('A1:' + lastColLetter + '1');
        var titleCell = ws.getCell('A1');
        titleCell.value = "รายละเอียดการตรวจสอบ/แก้ไขอุปกรณ์ FDCU OFFLine";
        titleCell.font = {{ name: 'TH SarabunPSK', size: 20, bold: true }};
        titleCell.alignment = {{ vertical: 'middle', horizontal: 'center' }};
        ws.getRow(1).height = 30;

        var headers = includeWorkerCol
            ? ['ลำดับ', 'รหัสสั่งการ\\n(ข้อมูล GIS)', 'Site ID', 'สถานที่\\n(ข้อมูล GIS)', 'รายละเอียดการตรวจสอบ/แก้ไข', 'ผู้รับผิดชอบและดำเนินการแก้ไขต่อไป', 'ผู้ปฏิบัติงาน (ผอส.ฉ.2)', 'สถานะหลังดำเนินการ']
            : ['ลำดับ', 'รหัสสั่งการ\\n(ข้อมูล GIS)', 'Site ID', 'สถานที่\\n(ข้อมูล GIS)', 'รายละเอียดการตรวจสอบ/แก้ไข', 'ผู้รับผิดชอบและดำเนินการแก้ไขต่อไป', 'สถานะหลังดำเนินการ'];

        var headerRow = ws.addRow(headers);
        headerRow.height = 36;
        headerRow.eachCell(function(cell) {{
            cell.font = {{ name: 'TH SarabunPSK', size: 16, bold: true }};
            cell.fill = {{ type: 'pattern', pattern: 'solid', fgColor: {{ argb: 'FFF1F3F4' }} }};
            cell.alignment = {{ vertical: 'middle', horizontal: 'center', wrapText: true }};
            cell.border = {{ top: {{style:'thin'}}, left: {{style:'thin'}}, bottom: {{style:'thin'}}, right: {{style:'thin'}} }};
        }});

        var startMergeRow = 3;
        var currentStatus = filteredData[0].status;

        filteredData.forEach(function(row, i) {{
            var rowValues = includeWorkerCol
                ? [ i+1, row.cmd, row.site, row.loc, row.detail, row.resp, splitOperatorNames(row.workers).join('\\n') || '-', row.status ]
                : [ i+1, row.cmd, row.site, row.loc, row.detail, row.resp, row.status ];

            var dataRow = ws.addRow(rowValues);
            dataRow.eachCell(function(cell, colNumber) {{
                cell.font = {{ name: 'TH SarabunPSK', size: 16 }};
                cell.border = {{ top: {{style:'thin'}}, left: {{style:'thin'}}, bottom: {{style:'thin'}}, right: {{style:'thin'}} }};
                if (colNumber === 5 || (includeWorkerCol && colNumber === 7)) {{
                    cell.alignment = {{ vertical: 'top', horizontal: 'left', wrapText: true }};
                }} else {{
                    cell.alignment = {{ vertical: 'middle', horizontal: 'center', wrapText: true }};
                }}
            }});

            var currentRow = i + 3;
            var nextStatus = (i < filteredData.length - 1) ? filteredData[i+1].status : null;

            if (nextStatus !== currentStatus || i === filteredData.length - 1) {{
                if (currentRow > startMergeRow) {{
                    ws.mergeCells(lastColLetter + startMergeRow + ':' + lastColLetter + currentRow);
                }}
                startMergeRow = currentRow + 1;
                currentStatus = nextStatus;
            }}
        }});

        if (includeWorkerCol) {{
            ws.getColumn(1).width = 7;
            ws.getColumn(2).width = 18;
            ws.getColumn(3).width = 18;
            ws.getColumn(4).width = 25;
            ws.getColumn(5).width = 52;
            ws.getColumn(6).width = 28;
            ws.getColumn(7).width = 30;
            ws.getColumn(8).width = 20;
        }} else {{
            ws.getColumn(1).width = 8;
            ws.getColumn(2).width = 20;
            ws.getColumn(3).width = 20;
            ws.getColumn(4).width = 28;
            ws.getColumn(5).width = 62;
            ws.getColumn(6).width = 32;
            ws.getColumn(7).width = 22;
        }}

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
        link.download = 'SCADA_Report_A4_' + safeFilenameDate + workerFileSuffix + dedupeSuffix + '.xlsx';

        document.body.appendChild(link); link.click(); document.body.removeChild(link);

        btn.disabled = false; btn.innerHTML = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="white" stroke-width="2"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg> ดาวน์โหลด Excel ขนาด A4 (.xlsx)';
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
                exportDiv.innerHTML = '<button id="exportCsvBtn" class="g-export-btn" type="button"><svg xmlns="http://www.w3.org/2000/svg" height="20" viewBox="0 -960 960 960" width="20"><path d="M480-320 280-520l56-58 104 104v-326h80v326l104-104 56 58-200 200ZM240-160q-33 0-56.5-23.5T160-240v-120h80v120h480v-120h80v120q0 33-23.5 56.5T720-160H240Z"/></svg>ส่งออก Excel ขนาด A4 (.xlsx)</button>';
                targetForm.appendChild(exportDiv);

                document.getElementById('exportCsvBtn').onclick = async function(e) {{
                    e.preventDefault(); e.stopPropagation();
                    var activeStatuses = [];
                    filterWrapper.querySelectorAll('.leaflet-control-layers-selector:checked').forEach(function(inp) {{
                        var lbl = inp.nextElementSibling || inp.parentElement;
                        var statusNode = lbl.querySelector('.status-text');
                        activeStatuses.push(statusNode ? statusNode.getAttribute('data-status') : lbl.textContent.replace(/●/g,'').trim());
                    }});
                    var filtered = expData.filter(function(d) {{ return activeStatuses.includes(d._layerName.trim()); }});
                    if(filtered.length === 0) {{ showCustomAlert('ไม่พบข้อมูล (กรุณาติ๊กเลือกอย่างน้อย 1 สถานะ)'); return; }}

                    var wb = new ExcelJS.Workbook();
                    var ws = wb.addWorksheet('SCADA_Stations_A4', {{
                        pageSetup: {{
                            paperSize: 9,
                            orientation: 'landscape',
                            fitToPage: true,
                            fitToWidth: 1,
                            fitToHeight: 0,
                            horizontalCentered: true,
                            printTitlesRow: '1:2',
                            margins: {{ left: 0.25, right: 0.25, top: 0.4, bottom: 0.4, header: 0.2, footer: 0.2 }}
                        }}
                    }});
                    var headers = Object.keys(filtered[0]).filter(function(k) {{ return k !== '_layerName'; }});
                    ws.mergeCells(1, 1, 1, headers.length);
                    var titleC = ws.getCell(1, 1);
                    titleC.value = 'ข้อมูลสถานะอุปกรณ์ในระบบ SCADA (ตามตัวกรองแผนที่)';
                    titleC.font = {{ name: 'TH SarabunPSK', size: 18, bold: true }};
                    titleC.alignment = {{ vertical: 'middle', horizontal: 'center' }};
                    ws.getRow(1).height = 28;

                    var hRow = ws.addRow(headers);
                    hRow.height = 28;
                    hRow.eachCell(function(c) {{
                        c.font = {{ name: 'TH SarabunPSK', size: 14, bold: true }};
                        c.fill = {{ type: 'pattern', pattern: 'solid', fgColor: {{ argb: 'FFF1F3F4' }} }};
                        c.alignment = {{ vertical: 'middle', horizontal: 'center', wrapText: true }};
                        c.border = {{ top: {{style:'thin'}}, left: {{style:'thin'}}, bottom: {{style:'thin'}}, right: {{style:'thin'}} }};
                    }});

                    filtered.forEach(function(row) {{
                        var rVals = headers.map(function(h) {{ return row[h] || ''; }});
                        var dRow = ws.addRow(rVals);
                        dRow.eachCell(function(c) {{
                            c.font = {{ name: 'TH SarabunPSK', size: 14 }};
                            c.alignment = {{ vertical: 'middle', horizontal: 'left', wrapText: true }};
                            c.border = {{ top: {{style:'thin'}}, left: {{style:'thin'}}, bottom: {{style:'thin'}}, right: {{style:'thin'}} }};
                        }});
                    }});
                    headers.forEach(function(_, i) {{ ws.getColumn(i + 1).width = 18; }});
                    var buf = await wb.xlsx.writeBuffer();
                    var blob = new Blob([buf], {{ type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }});
                    var link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = "SCADA_Filtered_Map_Data_A4.xlsx";
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

            function switchMapLayer(idx) {{
                currentMapIndex = idx;
                var conf = mapConfigs[idx];
                var targetLayer = getBaseLayerForConfig(conf);
                if (targetLayer) {{
                    getAllKnownBaseLayers().forEach(function(layer) {{
                        if (layer !== targetLayer && globalMap.hasLayer(layer)) globalMap.removeLayer(layer);
                    }});
                    if (!globalMap.hasLayer(targetLayer)) globalMap.addLayer(targetLayer);
                }}
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
                if (shouldOpen) panel.classList.add('show');
            }};
            switchMapLayer(0);
        }}
    }}, 100);

    // --- ระบบค้นหา ---
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
        return String(value || '').toLocaleLowerCase('th-TH').normalize('NFC').replace(/\\s+/g, '').replace(/[._-]/g, '');
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
            div.innerHTML = `<div style="display:flex;flex-direction:column;width:100%;gap:5px;">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;width:100%;gap:8px;">
                <span style="font-size:14px;font-weight:600;line-height:1.2;">${{m.id}}</span>
                ${{m.code ? `<span style="color:var(--accent-primary);font-size:11px;font-weight:500;background:var(--bg-active);padding:2px 6px;border-radius:4px;flex-shrink:0;">${{m.code}}</span>` : ''}}
              </div>
              <div style="display:flex;flex-wrap:wrap;align-items:center;gap:6px;width:100%;">
                <span style="background-color:${{m.color}};color:#fff;padding:2px 7px;border-radius:999px;font-size:10px;font-weight:500;">${{m.status}}</span>
                <span style="color:var(--text-secondary);font-size:11.5px;flex:1;min-width:120px;">${{m.name ? m.name : 'ไม่มีชื่อสถานที่'}}</span>
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
        var isMapControl = target.closest && target.closest('#gLayerContainer, #standaloneReportBtn, #standaloneFilterBtn, #customFilterWrapper, .g-search-container, #topNavSwitcher, #scadaDashboardView');
        if (infoPanel && infoPanel.style.display === 'flex' && !infoPanel.contains(target) && !isMapMarker && !isSearchResult && !isMapControl) hideCustomPanel();
    }});
    </script>

    <script>
    // === ระบบจัดการหน้า Dashboard ภาพรวม (%AVA & Analytics) + Apple iOS Theme Switch ===
    var currentScadaTheme = 'light';

    var SUN_THUMB_SVG = '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32l1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.32-11.32l1.41-1.41"/>';
    var MOON_THUMB_SVG = '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>';

    function applyScadaTheme(themeName) {{
        currentScadaTheme = (themeName === 'dark') ? 'dark' : 'light';
        document.body.classList.toggle('theme-dark', currentScadaTheme === 'dark');
        document.body.classList.toggle('theme-light', currentScadaTheme === 'light');
        var thumbSvg = document.getElementById('themeThumbSvg');
        if (thumbSvg) {{
            if (currentScadaTheme === 'dark') {{
                thumbSvg.setAttribute('stroke', '#1a73e8');
                thumbSvg.innerHTML = MOON_THUMB_SVG;
            }} else {{
                thumbSvg.setAttribute('stroke', '#f59e0b');
                thumbSvg.innerHTML = SUN_THUMB_SVG;
            }}
        }}
        try {{
            localStorage.setItem('scada_theme_mode', currentScadaTheme);
        }} catch (e) {{}}
    }}

    window.toggleScadaTheme = function() {{
        applyScadaTheme(currentScadaTheme === 'dark' ? 'light' : 'dark');
    }};

    (function initSavedTheme() {{
        var saved = 'light';
        try {{
            saved = localStorage.getItem('scada_theme_mode') || 'light';
        }} catch (e) {{}}
        applyScadaTheme(saved);
    }})();

    var dashAllCompact = {dash_all_json};
    var dashAllRecords = dashAllCompact.map(function(arr) {{
        var obj = {{ "_layerName": arr[0] || "", "Site ID": arr[1] || "", "_safeId": arr[2] || "" }};
        for (var i = 0; i < DISPLAY_FIELD_NAMES.length; i++) {{
            var v = arr[i + 3];
            obj[DISPLAY_FIELD_NAMES[i]] = (v !== undefined && v !== "") ? v : "ไม่มีค่า";
        }}
        return obj;
    }});

    var dashDemoMode = false;
    var dashExplorerLimit = 250;

    var explorerFilterState = {{
        status: 'all',
        eq: 'all',
        prod: 'all',
        comm: 'all',
        wrl: 'all',
        resp: ''
    }};

    function classifyRawParentState(stText) {{
        var u = String(stText || '').trim().toUpperCase();
        if (u.startsWith('ONLINE')) return 'Online';
        if (u.startsWith('TELEMETRY')) return 'Telemetry Failure';
        if (u.startsWith('OFFLINE')) return 'Offline';
        if (u.startsWith('INITIALIZING')) return 'Initializing';
        if (u.startsWith('CONNECTING')) return 'Connecting';
        return 'สถานะอื่นๆ';
    }}

    function normalizeEqCategory(rawEq) {{
        var s = String(rawEq || '').trim();
        var u = s.toUpperCase();
        if (!s || s === 'ไม่มีค่า' || u === 'NAN' || u === 'NONE') return 'ไม่ระบุชนิดอุปกรณ์';
        if (u.includes('RECLOSER')) return 'Recloser';
        if (u.includes('VSPP')) return 'VSPP LBS : SF6';
        if (u.includes('LOAD BREAK SWITCH') || (u.includes('LBS') && u.includes('SF6')) || u.includes('22KV') || u.includes('22 KV')) {{
            return 'Load Break Switch 22kV (SF6)';
        }}
        if (u.includes('LBS') && u.includes('115')) return 'LBS 115 kV';
        if (u.includes('CIRCUIT SWITCHER') || (u.includes('CS') && u.includes('115'))) return 'Circuit Switcher 115 kV';
        if (u === 'AVR' || u.includes('AVR')) return 'AVR';
        return s;
    }}

    function isCore4Equipment(normEq) {{
        return [
            'Recloser',
            'Load Break Switch 22kV (SF6)',
            'LBS 115 kV',
            'Circuit Switcher 115 kV'
        ].indexOf(normEq) >= 0;
    }}

    function getEffectiveRowData(idx) {{
        var raw = dashAllRecords[idx] || {{}};
        var rawScadaState = raw["State SCADA"] && raw["State SCADA"] !== 'ไม่มีค่า' ? raw["State SCADA"] : raw["_layerName"];
        var afterScadaState = raw["State SCADA (หลังตรวจสอบ)"] && raw["State SCADA (หลังตรวจสอบ)"] !== 'ไม่มีค่า' ? raw["State SCADA (หลังตรวจสอบ)"] : rawScadaState;
        var chosenStateText = rawScadaState;
        var parentState = classifyRawParentState(chosenStateText);

        var prod = raw["ประเภทผลิตภัณฑ์"] && raw["ประเภทผลิตภัณฑ์"] !== 'ไม่มีค่า' ? raw["ประเภทผลิตภัณฑ์"] : '';
        var commType = raw["อุปกรณ์สื่อสาร (Commu.)"] && raw["อุปกรณ์สื่อสาร (Commu.)"] !== 'ไม่มีค่า' ? raw["อุปกรณ์สื่อสาร (Commu.)"] : '';
        var commItem = raw["ประเภทย่อยสื่อสาร (Item)"] && raw["ประเภทย่อยสื่อสาร (Item)"] !== 'ไม่มีค่า' ? raw["ประเภทย่อยสื่อสาร (Item)"] : '';
        var commRef = raw["เชื่อมกับ WRL Master"] && raw["เชื่อมกับ WRL Master"] !== 'ไม่มีค่า' ? raw["เชื่อมกับ WRL Master"] : '';
        var ip = raw["IP"] && raw["IP"] !== 'ไม่มีค่า' ? raw["IP"] : '';
        var dnp = raw["DNP"] && raw["DNP"] !== 'ไม่มีค่า' ? raw["DNP"] : '';

        var finalProd = prod || 'ไม่ระบุประเภทผลิตภัณฑ์';
        var finalCommType = commType || 'ไม่ระบุ Commu.';
        var finalCommItem = commItem || 'ไม่ระบุ Item';
        var finalCommRef = commRef || 'ไม่ระบุ WRL Master';
        var commuAndItemKey = finalCommType + ' • ' + finalCommItem;

        return {{
            idx: idx,
            safe_id: raw["_safeId"] || '',
            site_id: raw["Site ID"] || '-',
            cmd: raw["รหัสสั่งการ"] && raw["รหัสสั่งการ"] !== 'ไม่มีค่า' ? raw["รหัสสั่งการ"] : '-',
            loc: raw["สถานที่"] && raw["สถานที่"] !== 'ไม่มีค่า' ? raw["สถานที่"] : '-',
            pea: raw["การไฟฟ้า"] && raw["การไฟฟ้า"] !== 'ไม่มีค่า' ? raw["การไฟฟ้า"] : 'ไม่ระบุการไฟฟ้า',
            raw_eq: raw["ชนิดอุปกรณ์"] && raw["ชนิดอุปกรณ์"] !== 'ไม่มีค่า' ? raw["ชนิดอุปกรณ์"] : 'ไม่ระบุชนิดอุปกรณ์',
            norm_eq: normalizeEqCategory(raw["ชนิดอุปกรณ์"]),
            state_full: chosenStateText || '-',
            after_full: afterScadaState || '-',
            parent_state: parentState,
            prod: finalProd,
            ip: ip || '-',
            dnp: dnp || '-',
            comm_type: finalCommType,
            comm_item: finalCommItem,
            comm_ref: finalCommRef,
            comm_combo: commuAndItemKey
        }};
    }}

    function getAvaTierInfo(avaVal) {{
        if (avaVal >= 80.0) {{
            return {{ cls: 'ava-tier-green', barColor: '#16a34a', textColor: '#137333', gradeText: 'ผ่านเกณฑ์' }};
        }} else if (avaVal >= 70.0) {{
            return {{ cls: 'ava-tier-amber', barColor: '#f59e0b', textColor: '#b06000', gradeText: 'ต่ำกว่าเกณฑ์ (ลำดับ 1)' }};
        }} else if (avaVal >= 50.0) {{
            return {{ cls: 'ava-tier-orange', barColor: '#ea580c', textColor: '#c25100', gradeText: 'ต่ำกว่าเกณฑ์ (ลำดับ 2)' }};
        }} else {{
            return {{ cls: 'ava-tier-red', barColor: '#dc2626', textColor: '#c5221f', gradeText: 'ต่ำกว่าเกณฑ์ (ลำดับ 3)' }};
        }}
    }}

    function formatAvaBadge(avaVal) {{
        var t = getAvaTierInfo(avaVal);
        return '<span class="ava-badge ' + t.cls + '" title="' + t.gradeText + '">' + avaVal.toFixed(2) + '%</span>' +
               '<span class="mini-progress"><span class="mini-progress-bar" style="width:' + Math.min(100, Math.max(0, avaVal)).toFixed(1) + '%;background:' + t.barColor + ';"></span></span>';
    }}

    window.toggleSectionSort = function(selectId, primarySort, altSort) {{
        var sel = document.getElementById(selectId);
        if (!sel) return;
        var nextVal = (sel.value === primarySort) ? altSort : primarySort;
        var hasOpt = Array.from(sel.options).some(function(o) {{ return o.value === nextVal; }});
        if (!hasOpt) {{
            var opt = document.createElement('option');
            opt.value = nextVal;
            opt.textContent = nextVal;
            sel.appendChild(opt);
        }}
        sel.value = nextVal;
        window.renderScadaDashboard();
    }};

    function sortSummaryKeys(keys, dataMap, sortMode) {{
        var mode = sortMode || 'ava_desc';
        return keys.slice().sort(function(a, b) {{
            var da = dataMap[a], db = dataMap[b];
            var totA = da.total || 0, totB = db.total || 0;
            var onA = da.Online || 0, onB = db.Online || 0;
            var offA = (da.OfflineSum !== undefined) ? da.OfflineSum : (totA - onA);
            var offB = (db.OfflineSum !== undefined) ? db.OfflineSum : (totB - onB);
            var avaA = totA > 0 ? (onA / totA) * 100 : 0;
            var avaB = totB > 0 ? (onB / totB) * 100 : 0;

            if (mode === 'ava_desc') return (avaB - avaA) || (totB - totA);
            if (mode === 'ava_asc') return (avaA - avaB) || (offB - offA);
            if (mode === 'total_desc') return (totB - totA) || (avaB - avaA);
            if (mode === 'total_asc') return (totA - totB) || (avaB - avaA);
            if (mode === 'online_desc') return (onB - onA) || (totB - totA);
            if (mode === 'online_asc') return (onA - onB) || (totA - totB);
            if (mode === 'offline_desc') return (offB - offA) || (totB - totA);
            if (mode === 'offline_asc') return (offA - offB) || (totA - totB);
            if (mode === 'name_asc') return String(a).localeCompare(String(b), 'th');
            if (mode === 'name_desc') return String(b).localeCompare(String(a), 'th');
            return (avaB - avaA) || (totB - totA);
        }});
    }}

    function populateDashboardDropdownsOnce() {{
        var peaSelect = document.getElementById('dashPeaFilter');
        if (!peaSelect || peaSelect.dataset.initialized === '1') return;

        var peaSet = new Set();
        for (var i = 0; i < dashAllRecords.length; i++) {{
            var p = dashAllRecords[i]["การไฟฟ้า"];
            if (p && p !== 'ไม่มีค่า') peaSet.add(p);
        }}
        Array.from(peaSet).sort(function(a, b) {{ return a.localeCompare(b, 'th'); }}).forEach(function(p) {{
            var opt = document.createElement('option');
            opt.value = p;
            opt.textContent = p;
            peaSelect.appendChild(opt);
        }});
        peaSelect.dataset.initialized = '1';
    }}

    function syncExplorerFilterDropdowns(rows) {{
        var eqSel = document.getElementById('dashExplorerEq');
        var prodSel = document.getElementById('dashExplorerProd');
        var commSel = document.getElementById('dashExplorerComm');
        var wrlSel = document.getElementById('dashExplorerWrl');
        var stSel = document.getElementById('dashExplorerStatus');

        var eqSet = new Set();
        var prodSet = new Set();
        var commSet = new Set();
        var wrlSet = new Set();

        rows.forEach(function(r) {{
            if (r.norm_eq) eqSet.add(r.norm_eq);
            if (r.prod) prodSet.add(r.prod);
            if (r.comm_combo) commSet.add(r.comm_combo);
            if (r.comm_type) commSet.add(r.comm_type);
            if (r.comm_item) commSet.add(r.comm_item);
            if (r.comm_ref) wrlSet.add(r.comm_ref);
        }});

        function rebuildSelect(sel, allLabel, itemsSet, currentVal) {{
            if (!sel) return;
            sel.innerHTML = '';
            var optAll = document.createElement('option');
            optAll.value = 'all';
            optAll.textContent = allLabel;
            sel.appendChild(optAll);
            Array.from(itemsSet).sort(function(a, b) {{ return String(a).localeCompare(String(b), 'th'); }}).forEach(function(val) {{
                var opt = document.createElement('option');
                opt.value = val;
                opt.textContent = val;
                sel.appendChild(opt);
            }});
            if (currentVal && currentVal !== 'all' && !itemsSet.has(currentVal)) {{
                var extraOpt = document.createElement('option');
                extraOpt.value = currentVal;
                extraOpt.textContent = currentVal;
                sel.appendChild(extraOpt);
            }}
            sel.value = currentVal || 'all';
        }}

        rebuildSelect(eqSel, 'ทุกชนิดอุปกรณ์', eqSet, explorerFilterState.eq);
        rebuildSelect(prodSel, 'ทุกผลิตภัณฑ์', prodSet, explorerFilterState.prod);
        rebuildSelect(commSel, 'ทุกประเภทสื่อสาร', commSet, explorerFilterState.comm);
        rebuildSelect(wrlSel, 'ทุกแม่ข่าย', wrlSet, explorerFilterState.wrl);
        if (stSel) stSel.value = explorerFilterState.status || 'all';
    }}

    var cachedDashboardRows = [];
    var cachedSummaryExports = {{ eq: [], resp: [], prod: [], comm: [], pea: [] }};

    window.renderScadaDashboard = function() {{
        populateDashboardDropdownsOnce();
        var eqScope = (document.getElementById('dashEqScope') || {{}}).value || 'all';
        var peaFilter = (document.getElementById('dashPeaFilter') || {{}}).value || 'all';

        var rows = [];
        for (var i = 0; i < dashAllRecords.length; i++) {{
            var r = getEffectiveRowData(i);
            if (eqScope === 'core4' && !isCore4Equipment(r.norm_eq)) continue;
            if (eqScope === 'mapped' && !r.safe_id) continue;
            if (peaFilter !== 'all' && r.pea !== peaFilter) continue;
            rows.push(r);
        }}
        cachedDashboardRows = rows;
        syncExplorerFilterDropdowns(rows);

        var counts = {{
            total: rows.length,
            Online: 0,
            'Telemetry Failure': 0,
            Offline: 0,
            Initializing: 0,
            Connecting: 0,
            other: 0
        }};
        rows.forEach(function(r) {{
            if (counts[r.parent_state] !== undefined) counts[r.parent_state]++;
            else counts.other++;
        }});

        var offlineTotal = counts['Telemetry Failure'] + counts.Offline + counts.Initializing + counts.Connecting + counts.other;
        var denom = counts.Online + offlineTotal;
        var avaPct = denom > 0 ? (counts.Online / denom) * 100 : 0;
        var tier = getAvaTierInfo(avaPct);

        var navBadge = document.getElementById('navAvaBadge');
        if (navBadge) {{
            navBadge.textContent = avaPct.toFixed(2) + '%';
            navBadge.className = 'nav-ava-pill ' + tier.cls;
        }}

        var kpiAvaValEl = document.getElementById('kpiAvaVal');
        var kpiAvaStatusPillEl = document.getElementById('kpiAvaStatusPill');
        if (kpiAvaValEl) {{
            kpiAvaValEl.textContent = avaPct.toFixed(2) + '%';
            kpiAvaValEl.style.color = tier.textColor;
        }}
        if (kpiAvaStatusPillEl) {{
            kpiAvaStatusPillEl.textContent = tier.gradeText;
            kpiAvaStatusPillEl.className = 'ava-badge ' + tier.cls;
        }}

        document.getElementById('kpiTotalCount').textContent = 'ทั้งหมด ' + counts.total.toLocaleString() + ' ชุด';
        document.getElementById('kpiOfflineTotalText').textContent = 'ไม่ออนไลน์ ' + offlineTotal.toLocaleString() + ' ชุด (' + (denom > 0 ? ((offlineTotal / denom) * 100).toFixed(2) : '0.00') + '%)';
        document.getElementById('dashFormulaRatioText').innerHTML =
            'Online <strong>' + counts.Online.toLocaleString() + '</strong> | ' +
            'ไม่ออนไลน์ <strong>' + offlineTotal.toLocaleString() + '</strong> ' +
            '(&bull; <strong>' + tier.gradeText + '</strong>)';

        var stackedBar = document.getElementById('kpiStackedBar');
        if (stackedBar && denom > 0) {{
            stackedBar.innerHTML =
                '<div style="width:' + ((counts.Online / denom) * 100) + '%;background:#16a34a;" title="Online"></div>' +
                '<div style="width:' + ((counts.Connecting / denom) * 100) + '%;background:#ea580c;" title="Connecting"></div>' +
                '<div style="width:' + ((counts.Offline / denom) * 100) + '%;background:#dc2626;" title="Offline"></div>' +
                '<div style="width:' + ((counts.Initializing / denom) * 100) + '%;background:#65a30d;" title="Initializing"></div>' +
                '<div style="width:' + ((counts['Telemetry Failure'] / denom) * 100) + '%;background:#d97706;" title="Telemetry Failure"></div>';
        }}

        function setKpiBox(cntId, pctId, shareId, barId, val) {{
            var pctAll = denom > 0 ? (val / denom) * 100 : 0;
            var pctOff = offlineTotal > 0 ? (val / offlineTotal) * 100 : 0;
            document.getElementById(cntId).textContent = val.toLocaleString();
            document.getElementById(pctId).textContent = pctAll.toFixed(2) + '% ของทั้งหมด';
            if (shareId) document.getElementById(shareId).textContent = pctOff.toFixed(1) + '% ที่ไม่ออนไลน์';
            document.getElementById(barId).style.width = Math.min(100, pctAll).toFixed(1) + '%';
        }}
        setKpiBox('kpiOnlineCount', 'kpiOnlinePct', null, 'kpiOnlineBar', counts.Online);
        setKpiBox('kpiConnectingCount', 'kpiConnectingPct', 'kpiConnectingShareOffline', 'kpiConnectingBar', counts.Connecting);
        setKpiBox('kpiOfflineCount', 'kpiOfflinePct', 'kpiOfflineShareOffline', 'kpiOfflineBar', counts.Offline);
        setKpiBox('kpiInitCount', 'kpiInitPct', 'kpiInitShareOffline', 'kpiInitBar', counts.Initializing);
        setKpiBox('kpiTeleCount', 'kpiTelePct', 'kpiTeleShareOffline', 'kpiTeleBar', counts['Telemetry Failure']);

        // 1. ตารางแยกตามชนิดอุปกรณ์ (Sort by %AVA เป็นค่าเริ่มต้น)
        var eqMap = {{}};
        rows.forEach(function(r) {{
            var k = r.norm_eq;
            if (!eqMap[k]) eqMap[k] = {{ total: 0, Online: 0, 'Telemetry Failure': 0, Offline: 0, Initializing: 0, Connecting: 0 }};
            eqMap[k].total++;
            if (eqMap[k][r.parent_state] !== undefined) eqMap[k][r.parent_state]++;
        }});

        var eqSortMode = (document.getElementById('dashEqSort') || {{}}).value || 'ava_desc';
        var sortedEqKeys = sortSummaryKeys(Object.keys(eqMap), eqMap, eqSortMode);
        var eqTbody = document.getElementById('dashEqTypeTbody');
        eqTbody.innerHTML = '';
        cachedSummaryExports.eq = [];

        sortedEqKeys.forEach(function(k) {{
            var d = eqMap[k];
            if (d.total === 0) return;
            var offSum = d['Telemetry Failure'] + d.Offline + d.Initializing + d.Connecting;
            var eqAva = d.total > 0 ? (d.Online / d.total) * 100 : 0;
            cachedSummaryExports.eq.push([k, d.total, d.Online, offSum, d['Telemetry Failure'], d.Offline, d.Initializing, d.Connecting, eqAva.toFixed(2) + '%']);
            var isSelected = (explorerFilterState.eq === k);
            var tr = document.createElement('tr');
            tr.className = 'clickable-row' + (isSelected ? ' is-active-filter' : '');
            tr.onclick = function() {{ window.selectSummaryItem('eq', k); }};
            tr.innerHTML =
                '<td><strong>' + k + '</strong>' +
                    '<div class="mobile-sub-breakdown">Conn ' + d.Connecting + ' · Off ' + d.Offline + ' · Init ' + d.Initializing + ' · Tele ' + d['Telemetry Failure'] + '</div>' +
                '</td>' +
                '<td class="num">' + d.total.toLocaleString() + '</td>' +
                '<td class="num" style="color:#16a34a;font-weight:600;">' + d.Online.toLocaleString() + '</td>' +
                '<td class="num" style="color:#dc2626;font-weight:600;">' + offSum.toLocaleString() + '</td>' +
                '<td class="num hide-on-mobile">' + d['Telemetry Failure'].toLocaleString() + '</td>' +
                '<td class="num hide-on-mobile">' + d.Offline.toLocaleString() + '</td>' +
                '<td class="num hide-on-mobile">' + d.Initializing.toLocaleString() + '</td>' +
                '<td class="num hide-on-mobile">' + d.Connecting.toLocaleString() + '</td>' +
                '<td class="num">' + formatAvaBadge(eqAva) + '</td>';
            eqTbody.appendChild(tr);
        }});

        var eqTfoot = document.getElementById('dashEqTypeTfoot');
        eqTfoot.innerHTML =
            '<tr>' +
                '<td>รวมทั้งหมด</td>' +
                '<td class="num">' + counts.total.toLocaleString() + '</td>' +
                '<td class="num" style="color:#16a34a;">' + counts.Online.toLocaleString() + '</td>' +
                '<td class="num" style="color:#dc2626;">' + offlineTotal.toLocaleString() + '</td>' +
                '<td class="num hide-on-mobile">' + counts['Telemetry Failure'].toLocaleString() + '</td>' +
                '<td class="num hide-on-mobile">' + counts.Offline.toLocaleString() + '</td>' +
                '<td class="num hide-on-mobile">' + counts.Initializing.toLocaleString() + '</td>' +
                '<td class="num hide-on-mobile">' + counts.Connecting.toLocaleString() + '</td>' +
                '<td class="num">' + formatAvaBadge(avaPct) + '</td>' +
            '</tr>';

        // 2. ตารางสถานะการติดตามหน้างาน
        var respBuckets = [
            {{ key: 'รอ ผบอ. เข้าแก้ไข', label: 'รอ ผบอ.กบษ.ฉ.2 เข้าแก้ไข (FRTU/อุปกรณ์หลัก)', count: 0, color: '#ea580c' }},
            {{ key: 'รอ ผอส. เข้าแก้ไข', label: 'รอ ผอส.กสฟ.ฉ.2 เข้าแก้ไข (ระบบสื่อสาร)', count: 0, color: '#7c3aed' }},
            {{ key: 'รอ ผบอ. และ ผอส. เข้าแก้ไข', label: 'รอทั้ง ผบอ. และ ผอส. เข้าแก้ไขร่วมกัน', count: 0, color: '#dc2626' }},
            {{ key: 'ผบอ. เคยแก้ไขแล้วกลับมา Offline', label: 'ผบอ. เคยเข้าแก้ไขแล้ว กลับมาไม่ออนไลน์', count: 0, color: '#0284c7' }},
            {{ key: 'ระบบสื่อสารเคยแก้ไขแล้ว กลับมา Offline', label: 'ระบบสื่อสารเคยแก้ไขแล้ว กลับมาไม่ออนไลน์', count: 0, color: '#9333ea' }},
            {{ key: 'รอตรวจสอบสาเหตุ', label: 'ไม่ออนไลน์ (อยู่ระหว่างรอเข้าตรวจสอบสาเหตุ)', count: 0, color: '#64748b' }}
        ];
        rows.forEach(function(r) {{
            if (r.parent_state === 'Online') return;
            var af = r.after_full;
            if (af.includes('รอ ผบอ. และ ผอส.')) respBuckets[2].count++;
            else if (af.includes('รอ ผบอ.')) respBuckets[0].count++;
            else if (af.includes('รอ ผอส.')) respBuckets[1].count++;
            else if (af.includes('ผบอ. เคยแก้ไข')) respBuckets[3].count++;
            else if (af.includes('ระบบสื่อสารเคยแก้ไข')) respBuckets[4].count++;
            else respBuckets[5].count++;
        }});
        var respSortMode = (document.getElementById('dashRespSort') || {{}}).value || 'count_desc';
        respBuckets.sort(function(a, b) {{
            return respSortMode === 'count_asc' ? (a.count - b.count) : (b.count - a.count);
        }});

        var respTbody = document.getElementById('dashRespTbody');
        respTbody.innerHTML = '';
        cachedSummaryExports.resp = [];
        respBuckets.forEach(function(b) {{
            var share = offlineTotal > 0 ? (b.count / offlineTotal) * 100 : 0;
            cachedSummaryExports.resp.push([b.label, b.count, share.toFixed(1) + '%']);
            var isSelected = (explorerFilterState.resp === b.key);
            var tr = document.createElement('tr');
            tr.className = 'clickable-row' + (isSelected ? ' is-active-filter' : '');
            tr.onclick = function() {{ window.selectSummaryItem('resp', b.key); }};
            tr.innerHTML =
                '<td><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + b.color + ';margin-right:7px;"></span>' + b.label + '</td>' +
                '<td class="num"><strong>' + b.count.toLocaleString() + '</strong></td>' +
                '<td class="num">' + share.toFixed(1) + '%</td>' +
                '<td class="num"><span class="btn-jump-map">ดูรายการ</span></td>';
            respTbody.appendChild(tr);
        }});

        // 3. ตารางแยกตามประเภทผลิตภัณฑ์ FRTU (Sort by %AVA เป็นค่าเริ่มต้น)
        var prodMap = {{}};
        rows.forEach(function(r) {{
            var k = r.prod;
            if (!prodMap[k]) prodMap[k] = {{ total: 0, Online: 0, OfflineSum: 0, Connecting: 0, Offline: 0, Init: 0, Tele: 0 }};
            prodMap[k].total++;
            if (r.parent_state === 'Online') prodMap[k].Online++;
            else {{
                prodMap[k].OfflineSum++;
                if (r.parent_state === 'Connecting') prodMap[k].Connecting++;
                else if (r.parent_state === 'Offline') prodMap[k].Offline++;
                else if (r.parent_state === 'Initializing') prodMap[k].Init++;
                else if (r.parent_state === 'Telemetry Failure') prodMap[k].Tele++;
            }}
        }});
        var prodSortMode = (document.getElementById('dashProdSort') || {{}}).value || 'ava_desc';
        var sortedProdKeys = sortSummaryKeys(Object.keys(prodMap), prodMap, prodSortMode);
        var prodTbody = document.getElementById('dashProductTbody');
        prodTbody.innerHTML = '';
        cachedSummaryExports.prod = [];
        sortedProdKeys.forEach(function(k) {{
            var d = prodMap[k];
            var pAva = d.total > 0 ? (d.Online / d.total) * 100 : 0;
            var subBreak = 'Conn ' + d.Connecting + ' · Off ' + d.Offline + ' · Init ' + d.Init + ' · Tele ' + d.Tele;
            cachedSummaryExports.prod.push([k, d.total, d.Online, d.OfflineSum, subBreak, pAva.toFixed(2) + '%']);
            var isSelected = (explorerFilterState.prod === k);
            var tr = document.createElement('tr');
            tr.className = 'clickable-row' + (isSelected ? ' is-active-filter' : '');
            tr.onclick = function() {{ window.selectSummaryItem('prod', k); }};
            tr.innerHTML =
                '<td><strong>' + k + '</strong><div class="mobile-sub-breakdown">' + subBreak + '</div></td>' +
                '<td class="num">' + d.total.toLocaleString() + '</td>' +
                '<td class="num" style="color:#16a34a;font-weight:600;">' + d.Online.toLocaleString() + '</td>' +
                '<td class="num" style="color:#dc2626;font-weight:600;">' + d.OfflineSum.toLocaleString() + '</td>' +
                '<td class="num hide-on-mobile" style="font-size:11px;color:var(--text-secondary);">' + subBreak + '</td>' +
                '<td class="num">' + formatAvaBadge(pAva) + '</td>';
            prodTbody.appendChild(tr);
        }});

        // 4. ตารางแยกตามอุปกรณ์สื่อสารและแม่ข่าย (Sort by %AVA เป็นค่าเริ่มต้น)
        var commMode = (document.getElementById('dashCommGroupMode') || {{}}).value || 'commu_item';
        var commMap = {{}};
        rows.forEach(function(r) {{
            var k = '';
            var subText = '';
            var filterTargetType = 'comm';
            if (commMode === 'commu_item') {{
                k = r.comm_combo;
                subText = 'Commu.: ' + r.comm_type + ' | Item: ' + r.comm_item;
            }} else if (commMode === 'type') {{
                k = r.comm_type;
                subText = 'ประเภทอุปกรณ์สื่อสารหลัก (Commu.)';
            }} else if (commMode === 'item') {{
                k = r.comm_item;
                subText = 'ประเภทย่อย (Item)';
            }} else {{
                k = r.comm_ref;
                subText = 'แม่ข่ายสื่อสาร (WRL Master)';
                filterTargetType = 'wrl';
            }}
            if (!commMap[k]) commMap[k] = {{ label: k, sub: subText, fType: filterTargetType, total: 0, Online: 0, OfflineSum: 0 }};
            commMap[k].total++;
            if (r.parent_state === 'Online') commMap[k].Online++;
            else commMap[k].OfflineSum++;
        }});
        var commSortMode = (document.getElementById('dashCommSort') || {{}}).value || 'ava_desc';
        var sortedCommKeys = sortSummaryKeys(Object.keys(commMap), commMap, commSortMode);
        var commTbody = document.getElementById('dashCommTbody');
        commTbody.innerHTML = '';
        cachedSummaryExports.comm = [];
        sortedCommKeys.forEach(function(k) {{
            var d = commMap[k];
            var cAva = d.total > 0 ? (d.Online / d.total) * 100 : 0;
            cachedSummaryExports.comm.push([d.label, d.total, d.Online, d.OfflineSum, cAva.toFixed(2) + '%']);
            var isSelected = (d.fType === 'wrl' ? explorerFilterState.wrl === k : explorerFilterState.comm === k);
            var tr = document.createElement('tr');
            tr.className = 'clickable-row' + (isSelected ? ' is-active-filter' : '');
            tr.onclick = function() {{ window.selectSummaryItem(d.fType, k); }};
            tr.innerHTML =
                '<td><strong>' + d.label + '</strong><div style="font-size:10.5px;color:var(--text-secondary);">' + d.sub + '</div></td>' +
                '<td class="num">' + d.total.toLocaleString() + '</td>' +
                '<td class="num" style="color:#16a34a;font-weight:600;">' + d.Online.toLocaleString() + '</td>' +
                '<td class="num" style="color:#dc2626;font-weight:600;">' + d.OfflineSum.toLocaleString() + '</td>' +
                '<td class="num">' + formatAvaBadge(cAva) + '</td>';
            commTbody.appendChild(tr);
        }});

        // 5. ตารางแยกตามการไฟฟ้า (กฟจ. / กฟส.) (Sort by ทั้งหมด และ %AVA ได้ครบถ้วน)
        var peaMap = {{}};
        rows.forEach(function(r) {{
            var k = r.pea;
            if (!peaMap[k]) peaMap[k] = {{ total: 0, Online: 0, 'Telemetry Failure': 0, Offline: 0, Initializing: 0, Connecting: 0 }};
            peaMap[k].total++;
            if (peaMap[k][r.parent_state] !== undefined) peaMap[k][r.parent_state]++;
        }});
        var peaSortMode = (document.getElementById('dashPeaSort') || {{}}).value || 'total_desc';
        var peaKeys = sortSummaryKeys(Object.keys(peaMap), peaMap, peaSortMode);
        var peaTbody = document.getElementById('dashPeaTbody');
        peaTbody.innerHTML = '';
        cachedSummaryExports.pea = [];
        peaKeys.forEach(function(k) {{
            var d = peaMap[k];
            var offSum = d['Telemetry Failure'] + d.Offline + d.Initializing + d.Connecting;
            var pAva = d.total > 0 ? (d.Online / d.total) * 100 : 0;
            cachedSummaryExports.pea.push([k, d.total, d.Online, offSum, d['Telemetry Failure'], d.Offline, d.Initializing, d.Connecting, pAva.toFixed(2) + '%']);
            var tr = document.createElement('tr');
            tr.className = 'clickable-row';
            tr.onclick = function() {{
                var pSel = document.getElementById('dashPeaFilter');
                if (pSel) {{ pSel.value = (pSel.value === k ? 'all' : k); window.renderScadaDashboard(); }}
            }};
            tr.innerHTML =
                '<td><strong>' + k + '</strong>' +
                    '<div class="mobile-sub-breakdown">Conn ' + d.Connecting + ' · Off ' + d.Offline + ' · Init ' + d.Initializing + ' · Tele ' + d['Telemetry Failure'] + '</div>' +
                '</td>' +
                '<td class="num">' + d.total.toLocaleString() + '</td>' +
                '<td class="num" style="color:#16a34a;font-weight:600;">' + d.Online.toLocaleString() + '</td>' +
                '<td class="num" style="color:#dc2626;font-weight:600;">' + offSum.toLocaleString() + '</td>' +
                '<td class="num hide-on-mobile">' + d['Telemetry Failure'].toLocaleString() + '</td>' +
                '<td class="num hide-on-mobile">' + d.Offline.toLocaleString() + '</td>' +
                '<td class="num hide-on-mobile">' + d.Initializing.toLocaleString() + '</td>' +
                '<td class="num hide-on-mobile">' + d.Connecting.toLocaleString() + '</td>' +
                '<td class="num">' + formatAvaBadge(pAva) + '</td>';
            peaTbody.appendChild(tr);
        }});

        window.renderDashExplorerTable();
    }};

    window.selectSummaryItem = function(filterType, value) {{
        dashExplorerLimit = 250;
        var isSame = false;
        if (filterType === 'eq') isSame = (explorerFilterState.eq === value);
        else if (filterType === 'prod') isSame = (explorerFilterState.prod === value);
        else if (filterType === 'comm') isSame = (explorerFilterState.comm === value);
        else if (filterType === 'wrl') isSame = (explorerFilterState.wrl === value);
        else if (filterType === 'resp') isSame = (explorerFilterState.resp === value);
        else if (filterType === 'status') isSame = (explorerFilterState.status === value && value !== 'all');

        explorerFilterState.status = 'all';
        explorerFilterState.eq = 'all';
        explorerFilterState.prod = 'all';
        explorerFilterState.comm = 'all';
        explorerFilterState.wrl = 'all';
        explorerFilterState.resp = '';

        if (!isSame) {{
            if (filterType === 'eq') explorerFilterState.eq = value;
            else if (filterType === 'prod') explorerFilterState.prod = value;
            else if (filterType === 'comm') explorerFilterState.comm = value;
            else if (filterType === 'wrl') explorerFilterState.wrl = value;
            else if (filterType === 'resp') {{
                explorerFilterState.resp = value;
                explorerFilterState.status = 'ALL_OFFLINE';
            }} else if (filterType === 'status') {{
                explorerFilterState.status = value;
            }}
        }}

        window.renderScadaDashboard();
        var card = document.getElementById('dashExplorerCard');
        if (card) card.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
    }};

    window.onExplorerDropdownChange = function() {{
        var stSel = document.getElementById('dashExplorerStatus');
        var eqSel = document.getElementById('dashExplorerEq');
        var prodSel = document.getElementById('dashExplorerProd');
        var commSel = document.getElementById('dashExplorerComm');
        var wrlSel = document.getElementById('dashExplorerWrl');

        explorerFilterState.status = stSel ? stSel.value : 'all';
        explorerFilterState.eq = eqSel ? eqSel.value : 'all';
        explorerFilterState.prod = prodSel ? prodSel.value : 'all';
        explorerFilterState.comm = commSel ? commSel.value : 'all';
        explorerFilterState.wrl = wrlSel ? wrlSel.value : 'all';
        dashExplorerLimit = 250;
        window.renderScadaDashboard();
    }};

    window.clearSingleExplorerFilter = function(filterKey) {{
        if (filterKey === 'resp') explorerFilterState.resp = '';
        else if (filterKey === 'search') {{
            var s = document.getElementById('dashExplorerSearch');
            if (s) s.value = '';
        }} else {{
            explorerFilterState[filterKey] = 'all';
        }}
        window.renderScadaDashboard();
    }};

    window.resetDashExplorerFilters = function() {{
        explorerFilterState.status = 'all';
        explorerFilterState.eq = 'all';
        explorerFilterState.prod = 'all';
        explorerFilterState.comm = 'all';
        explorerFilterState.wrl = 'all';
        explorerFilterState.resp = '';
        dashExplorerLimit = 250;
        var s = document.getElementById('dashExplorerSearch');
        if (s) s.value = '';
        window.renderScadaDashboard();
    }};

    window.showAllExplorerRows = function() {{
        dashExplorerLimit = 99999;
        window.renderDashExplorerTable();
    }};

    function renderActiveFilterChips(q) {{
        var bar = document.getElementById('dashActiveFilterBar');
        if (!bar) return;
        var chips = [];

        if (explorerFilterState.eq && explorerFilterState.eq !== 'all') {{
            chips.push({{ key: 'eq', label: 'ชนิดอุปกรณ์: ' + explorerFilterState.eq }});
        }}
        if (explorerFilterState.prod && explorerFilterState.prod !== 'all') {{
            chips.push({{ key: 'prod', label: 'ผลิตภัณฑ์: ' + explorerFilterState.prod }});
        }}
        if (explorerFilterState.comm && explorerFilterState.comm !== 'all') {{
            chips.push({{ key: 'comm', label: 'สื่อสาร: ' + explorerFilterState.comm }});
        }}
        if (explorerFilterState.wrl && explorerFilterState.wrl !== 'all') {{
            chips.push({{ key: 'wrl', label: 'WRL Master: ' + explorerFilterState.wrl }});
        }}
        if (explorerFilterState.status && explorerFilterState.status !== 'all') {{
            var stLabel = explorerFilterState.status === 'ALL_OFFLINE' ? 'ไม่ออนไลน์ทั้งหมด' : explorerFilterState.status;
            chips.push({{ key: 'status', label: 'สถานะ: ' + stLabel }});
        }}
        if (explorerFilterState.resp) {{
            chips.push({{ key: 'resp', label: 'การติดตาม: ' + explorerFilterState.resp }});
        }}
        if (q) {{
            chips.push({{ key: 'search', label: 'ค้นหา: "' + q + '"' }});
        }}

        if (chips.length === 0) {{
            bar.style.display = 'none';
            bar.innerHTML = '';
            return;
        }}

        bar.style.display = 'flex';
        var html = '<strong>ตัวกรอง:</strong>';
        chips.forEach(function(c) {{
            html += '<span class="filter-chip-badge">' + c.label +
                    '<button type="button" data-k="' + c.key + '" onclick="window.clearSingleExplorerFilter(this.getAttribute(&quot;data-k&quot;))" title="ลบตัวกรอง">×</button></span>';
        }});
        bar.innerHTML = html;
    }}

    window.renderDashExplorerTable = function() {{
        var q = String((document.getElementById('dashExplorerSearch') || {{}}).value || '').trim().toLowerCase();
        var stFilter = explorerFilterState.status || 'all';
        var eqFilter = explorerFilterState.eq || 'all';
        var prodFilter = explorerFilterState.prod || 'all';
        var commFilter = explorerFilterState.comm || 'all';
        var wrlFilter = explorerFilterState.wrl || 'all';
        var respFilter = explorerFilterState.resp || '';

        renderActiveFilterChips(q);

        var matched = cachedDashboardRows.filter(function(r) {{
            if (stFilter === 'ALL_OFFLINE' && r.parent_state === 'Online') return false;
            else if (stFilter !== 'all' && stFilter !== 'ALL_OFFLINE' && r.parent_state !== stFilter) return false;

            if (eqFilter !== 'all' && r.norm_eq !== eqFilter) return false;
            if (prodFilter !== 'all' && r.prod !== prodFilter) return false;
            if (commFilter !== 'all' && r.comm_combo !== commFilter && r.comm_type !== commFilter && r.comm_item !== commFilter) return false;
            if (wrlFilter !== 'all' && r.comm_ref !== wrlFilter) return false;

            if (respFilter) {{
                if (respFilter === 'รอตรวจสอบสาเหตุ') {{
                    if (r.after_full.includes('ผบอ.') || r.after_full.includes('ผอส.') || r.after_full.includes('ระบบสื่อสารเคยแก้ไข')) return false;
                }} else if (!r.after_full.includes(respFilter)) {{
                    return false;
                }}
            }}

            if (q) {{
                var hay = (r.site_id + ' ' + r.cmd + ' ' + r.loc + ' ' + r.pea + ' ' + r.norm_eq + ' ' + r.prod + ' ' + r.ip + ' ' + r.dnp + ' ' + r.comm_type + ' ' + r.comm_item + ' ' + r.comm_ref + ' ' + r.state_full).toLowerCase();
                if (hay.indexOf(q) === -1) return false;
            }}
            return true;
        }});

        var shownCount = Math.min(matched.length, dashExplorerLimit);
        var subEl = document.getElementById('dashExplorerSubtitle');
        if (subEl) {{
            subEl.textContent = 'พบข้อมูล ' + matched.length.toLocaleString() + ' ชุด (แสดง ' + shownCount.toLocaleString() + ' รายการ)';
        }}

        var tbody = document.getElementById('dashExplorerTbody');
        if (!tbody) return;
        tbody.innerHTML = '';

        var colorByParent = {{
            'Online': '#16a34a',
            'Connecting': '#ea580c',
            'Offline': '#dc2626',
            'Initializing': '#65a30d',
            'Telemetry Failure': '#d97706',
            'สถานะอื่นๆ': '#64748b'
        }};

        if (matched.length === 0) {{
            var emptyTr = document.createElement('tr');
            emptyTr.innerHTML = '<td colspan="14" style="text-align:center;padding:24px;">ไม่พบรายการอุปกรณ์ที่ตรงกับตัวกรองที่เลือก</td>';
            tbody.appendChild(emptyTr);
            return;
        }}

        matched.slice(0, dashExplorerLimit).forEach(function(r, idx) {{
            var tr = document.createElement('tr');
            var badgeColor = colorByParent[r.parent_state] || '#64748b';
            var actionHtml = r.safe_id
                ? '<button type="button" class="btn-jump-map" data-safe-id="' + r.safe_id + '" onclick="window.jumpFromDashToMapPin(this.getAttribute(&quot;data-safe-id&quot;))">ดูพิกัด</button>'
                : '<span style="font-size:11px;opacity:0.65;">ไม่มีพิกัด</span>';
            tr.innerHTML =
                '<td class="num" style="opacity:0.7;">' + (idx + 1) + '</td>' +
                '<td><strong>' + r.site_id + '</strong>' +
                    '<div class="mobile-sub-breakdown">' + r.prod + ' · ' + r.comm_type + ' (' + r.comm_item + ') · IP: ' + r.ip + '</div>' +
                '</td>' +
                '<td><span style="font-weight:600;">' + r.cmd + '</span></td>' +
                '<td>' + r.loc + '</td>' +
                '<td>' + r.pea + '</td>' +
                '<td>' + r.norm_eq + '</td>' +
                '<td class="hide-on-mobile">' + r.prod + '</td>' +
                '<td class="hide-on-mobile">' + r.comm_type + '</td>' +
                '<td class="hide-on-mobile">' + r.comm_item + '</td>' +
                '<td class="hide-on-mobile">' + r.comm_ref + '</td>' +
                '<td class="hide-on-mobile" style="font-family:monospace;">' + r.ip + '</td>' +
                '<td class="hide-on-mobile" style="font-family:monospace;">' + r.dnp + '</td>' +
                '<td><span class="status-pill-sm" style="background:' + badgeColor + ';">' + r.state_full + '</span></td>' +
                '<td>' + actionHtml + '</td>';
            tbody.appendChild(tr);
        }});
    }};

    window.switchMainView = function(viewName) {{
        var dashEl = document.getElementById('scadaDashboardView');
        var btnMap = document.getElementById('navBtnMap');
        var btnDash = document.getElementById('navBtnDash');
        if (!dashEl) return;

        if (viewName === 'dashboard') {{
            if (typeof closeFloatingSurfaces === 'function') closeFloatingSurfaces();
            document.body.classList.add('dashboard-mode-active');
            dashEl.classList.add('show');
            if (btnDash) btnDash.classList.add('is-active');
            if (btnMap) btnMap.classList.remove('is-active');
            window.renderScadaDashboard();
        }} else {{
            document.body.classList.remove('dashboard-mode-active');
            dashEl.classList.remove('show');
            if (btnMap) btnMap.classList.add('is-active');
            if (btnDash) btnDash.classList.remove('is-active');
            setTimeout(function() {{
                for (var key in window) {{
                    if (key.startsWith('map_') && window[key] && typeof window[key].invalidateSize === 'function') {{
                        window[key].invalidateSize();
                    }}
                }}
            }}, 120);
        }}
    }};

    window.jumpFromDashToMapPin = function(safeId) {{
        var m = stationBySafeId[safeId];
        window.switchMainView('map');
        if (!m) return;
        setTimeout(function() {{
            var foundMap = false;
            for (var key in window) {{
                if (key.startsWith('map_') && window[key] && typeof window[key].project === 'function') {{
                    foundMap = true;
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
                    break;
                }}
            }}
            if (!foundMap) {{
                window.location.href = 'scada_detailed_map.html?site=' + encodeURIComponent(m.id);
            }}
        }}, 180);
    }};

    // ส่งออกไฟล์ Excel ของหน้า Dashboard ทุกตารางขนาดมาตรฐาน A4
    window.exportDashboardExcel = async function() {{
        var workbook = new ExcelJS.Workbook();
        var a4Setup = {{
            paperSize: 9, // A4 Standard
            orientation: 'landscape',
            fitToPage: true,
            fitToWidth: 1,
            fitToHeight: 0,
            horizontalCentered: true,
            printTitlesRow: '1:2',
            margins: {{ left: 0.25, right: 0.25, top: 0.4, bottom: 0.4, header: 0.2, footer: 0.2 }}
        }};

        function addFormattedA4Sheet(sheetName, titleText, headers, dataRows, colWidths) {{
            var ws = workbook.addWorksheet(sheetName, {{ pageSetup: a4Setup }});
            ws.mergeCells(1, 1, 1, headers.length);
            var tCell = ws.getCell(1, 1);
            tCell.value = titleText;
            tCell.font = {{ name: 'TH SarabunPSK', size: 18, bold: true }};
            tCell.alignment = {{ vertical: 'middle', horizontal: 'center' }};
            ws.getRow(1).height = 28;

            var hRow = ws.addRow(headers);
            hRow.height = 28;
            hRow.eachCell(function(c) {{
                c.font = {{ name: 'TH SarabunPSK', size: 15, bold: true }};
                c.fill = {{ type: 'pattern', pattern: 'solid', fgColor: {{ argb: 'FFF1F3F4' }} }};
                c.alignment = {{ vertical: 'middle', horizontal: 'center', wrapText: true }};
                c.border = {{ top: {{style:'thin'}}, left: {{style:'thin'}}, bottom: {{style:'thin'}}, right: {{style:'thin'}} }};
            }});

            dataRows.forEach(function(rVals) {{
                var dRow = ws.addRow(rVals);
                dRow.eachCell(function(c, colNum) {{
                    c.font = {{ name: 'TH SarabunPSK', size: 15 }};
                    c.alignment = {{ vertical: 'middle', horizontal: colNum === 1 ? 'left' : 'center', wrapText: true }};
                    c.border = {{ top: {{style:'thin'}}, left: {{style:'thin'}}, bottom: {{style:'thin'}}, right: {{style:'thin'}} }};
                }});
            }});

            if (colWidths) {{
                colWidths.forEach(function(w, i) {{ ws.getColumn(i + 1).width = w; }});
            }}
            return ws;
        }}

        addFormattedA4Sheet(
            '1.แยกตามชนิดอุปกรณ์',
            'รายงานความพร้อมใช้งานอุปกรณ์ในระบบ SCADA (%AVA) - แยกตามชนิดอุปกรณ์',
            ['ชนิดอุปกรณ์', 'ทั้งหมด', 'Online', 'ไม่ออนไลน์รวม', 'Telemetry Failure', 'Offline', 'Initializing', 'Connecting', '%AVA'],
            cachedSummaryExports.eq,
            [30, 14, 14, 16, 16, 14, 14, 14, 16]
        );

        addFormattedA4Sheet(
            '3.แยกตามผลิตภัณฑ์ FRTU',
            'รายงานความพร้อมใช้งานอุปกรณ์ในระบบ SCADA (%AVA) - แยกตามประเภทผลิตภัณฑ์ของ FRTU',
            ['ประเภทผลิตภัณฑ์ (FRTU Model)', 'ทั้งหมด', 'Online', 'ไม่ออนไลน์รวม', 'สถานะย่อย', '%AVA'],
            cachedSummaryExports.prod,
            [28, 14, 14, 16, 38, 16]
        );

        addFormattedA4Sheet(
            '4.แยกตามสื่อสาร-แม่ข่าย',
            'รายงานความพร้อมใช้งานอุปกรณ์ในระบบ SCADA (%AVA) - แยกตามอุปกรณ์สื่อสารและ WRL Master',
            ['อุปกรณ์สื่อสาร / WRL Master', 'ทั้งหมด', 'Online', 'ไม่ออนไลน์รวม', '%AVA'],
            cachedSummaryExports.comm,
            [38, 16, 16, 18, 18]
        );

        addFormattedA4Sheet(
            '5.แยกตามการไฟฟ้า',
            'รายงานความพร้อมใช้งานอุปกรณ์ในระบบ SCADA (%AVA) - แยกตามสังกัดการไฟฟ้า (กฟจ. / กฟส.)',
            ['การไฟฟ้า', 'ทั้งหมด', 'Online', 'ไม่ออนไลน์รวม', 'Telemetry Failure', 'Offline', 'Initializing', 'Connecting', '%AVA'],
            cachedSummaryExports.pea,
            [30, 14, 14, 16, 16, 14, 14, 14, 16]
        );

        var deviceRows = cachedDashboardRows.map(function(r, i) {{
            return [i + 1, r.site_id, r.cmd, r.loc, r.pea, r.norm_eq, r.prod, r.comm_type, r.comm_item, r.comm_ref, r.ip, r.dnp, r.state_full];
        }});
        addFormattedA4Sheet(
            '6.รายการอุปกรณ์ทั้งหมด',
            'ตารางข้อมูลอุปกรณ์และการสื่อสารในระบบ SCADA',
            ['ลำดับ', 'Site ID', 'รหัสสั่งการ', 'สถานที่', 'การไฟฟ้า', 'ชนิดอุปกรณ์', 'ประเภทผลิตภัณฑ์', 'Commu.', 'Item', 'WRL Master', 'IP', 'DNP', 'State SCADA'],
            deviceRows,
            [8, 16, 16, 22, 20, 22, 16, 14, 14, 16, 16, 10, 14]
        );

        var buffer = await workbook.xlsx.writeBuffer();
        var blob = new Blob([buffer], {{ type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }});
        var link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = 'SCADA_Dashboard_AVA_Summary_A4.xlsx';
        document.body.appendChild(link); link.click(); document.body.removeChild(link);
    }};

    (function initDashboardBindings() {{
        var navBtnMap = document.getElementById('navBtnMap');
        var navBtnDash = document.getElementById('navBtnDash');
        if (navBtnMap) {{
            navBtnMap.addEventListener('click', function(e) {{
                e.preventDefault();
                e.stopPropagation();
                window.switchMainView('map');
            }});
        }}
        if (navBtnDash) {{
            navBtnDash.addEventListener('click', function(e) {{
                e.preventDefault();
                e.stopPropagation();
                window.switchMainView('dashboard');
            }});
        }}

        try {{
            window.renderScadaDashboard();
        }} catch (err) {{
            console.error('Initial dashboard render error:', err);
        }}

        var urlParams = new URLSearchParams(window.location.search);
        if (window.DEFAULT_START_DASHBOARD === true || urlParams.get('view') === 'dashboard' || window.location.pathname.toLowerCase().includes('dashboard')) {{
            window.switchMainView('dashboard');
        }}
    }})();
    </script>
    """
    m.get_root().html.add_child(folium.Element(custom_ui_html))
    return m.get_root().render()

def background_task():
    try:
        print("กำลังดึงข้อมูลและสร้างแผนที่/แดชบอร์ดเบื้องหลัง...")
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
        print(f"อัปเดตแผนที่และแดชบอร์ดเสร็จสมบูรณ์! (เวอร์ชัน {new_version})")
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการรันเบื้องหลัง: {e}")
        if not os.path.exists(CACHE_HTML_FILE):
            error_html = f"""
            <div style='display:flex;flex-direction:column;justify-content:center;align-items:center;height:100vh;background:#f0f2f5;color:#1c1e21;font-family:sans-serif;'>
                <h2 style='color:#d93025;'>เกิดข้อผิดพลาดในการโหลดข้อมูลระบบ SCADA</h2>
                <p style='color:#5f6368;max-width:80%;text-align:center;'>{html.escape(str(e))}</p>
                <p style='color:#1a73e8;font-size:14px;margin-top:20px;cursor:pointer;font-weight:600;' onclick='location.reload()'>↻ รีเฟรชหน้าเว็บใหม่อีกครั้ง</p>
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
            content = f.read()
        if request.args.get('view') == 'dashboard':
            content = content.replace(
                'var dashDemoMode = false;',
                'window.DEFAULT_START_DASHBOARD = true;\nvar dashDemoMode = false;',
                1
            )
        return content
    except Exception:
        trigger_update_if_needed()
        return "<style>body{background:#f0f2f5;}</style>", 503

@app.route('/api/version')
def api_version():
    trigger_update_if_needed()
    meta = get_meta()
    return jsonify({"version": meta['version']})

@app.route('/')
@app.route('/dashboard')
def index():
    trigger_update_if_needed()
    default_view = 'dashboard' if request.path.endswith('/dashboard') or request.args.get('view') == 'dashboard' else 'map'
    html_wrapper = f"""
    <!DOCTYPE html>
    <html lang="th">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>รายงานความพร้อมใช้งานอุปกรณ์ในระบบ SCADA (%AVA)</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Prompt:wght@300;400;500;600&display=swap" rel="stylesheet">
        <style>
            body, html {{ margin: 0; padding: 0; height: 100%; width: 100%; overflow: hidden; background-color: #f0f2f5; font-family: 'Prompt', 'Inter', sans-serif; }}
            .map-layer {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: none; }}
            .layer-active {{ z-index: 2; opacity: 1; transition: opacity 0.45s ease-in-out; }}
            .layer-hidden {{ z-index: 1; opacity: 0; pointer-events: none; }}
            #loader {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: #1c1e21; z-index: 0; text-align: center; transition: opacity 0.35s; background: #ffffff; padding: 24px 32px; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); border: 1px solid #dadce0; }}
            #loader h2 {{ margin: 0 0 8px 0; font-size: 18px; font-weight: 600; color: #1a73e8; }}
            #loader p {{ margin: 0; font-size: 13px; color: #5f6368; }}
        </style>
    </head>
    <body>
        <div id="loader"><h2 id="loading-text">กำลังเตรียมข้อมูลระบบ SCADA...</h2><p>กำลังเชื่อมต่อฐานข้อมูลแผนที่และแดชบอร์ด (%AVA)</p></div>
        <iframe id="layer1" class="map-layer layer-hidden" src="about:blank"></iframe>
        <iframe id="layer2" class="map-layer layer-hidden" src="about:blank"></iframe>

        <script>
            var currentVersion = 0;
            var pendingVersion = 0;
            var activeLayer = 1;
            var isSwapping = false;
            var initialDefaultView = "{default_view}";

            var dotCount = 0;
            setInterval(() => {{
                var loaderText = document.getElementById('loading-text');
                if (loaderText && currentVersion === 0) {{
                    dotCount = (dotCount + 1) % 4;
                    loaderText.innerText = "กำลังเตรียมข้อมูลระบบ SCADA" + ".".repeat(dotCount);
                }}
            }}, 500);

            window.addEventListener('resize', () => {{
                document.querySelectorAll('iframe').forEach(ifr => {{
                    var map = getMapInstance(ifr);
                    if (map) setTimeout(() => map.invalidateSize(), 200);
                }});
            }});
            window.addEventListener('orientationchange', () => {{
                document.querySelectorAll('iframe').forEach(ifr => {{
                    var map = getMapInstance(ifr);
                    if (map) setTimeout(() => map.invalidateSize(), 300);
                }});
            }});

            function getMapInstance(iframe) {{
                try {{
                    var win = iframe.contentWindow;
                    for (var key in win) {{ if (key.startsWith('map_')) return win[key]; }}
                }} catch(e) {{}}
                return null;
            }}

            function isUserBusyInIframe(iframe) {{
                try {{
                    var doc = iframe.contentWindow.document;
                    if (!doc) return false;
                    if (doc.body && doc.body.classList.contains('dashboard-mode-active')) return true;
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
                }} catch(e) {{}}
                return false;
            }}

            function checkUpdate() {{
                fetch('/api/version?t=' + new Date().getTime(), {{ cache: 'no-store' }})
                    .then(res => res.json())
                    .then(data => {{
                        if (data.version > 0 && data.version !== currentVersion) {{
                            pendingVersion = data.version;
                            tryApplyPendingSwap();
                        }}
                    }}).catch(e => console.log(e));
            }}

            function tryApplyPendingSwap() {{
                if (!pendingVersion || pendingVersion === currentVersion || isSwapping) return;
                var activeIframe = document.getElementById('layer' + activeLayer);
                if (currentVersion > 0 && isUserBusyInIframe(activeIframe)) {{
                    return;
                }}
                currentVersion = pendingVersion;
                swapMap();
            }}

            function swapMap() {{
                isSwapping = true;
                var nextLayer = activeLayer === 1 ? 2 : 1;
                var activeIframe = document.getElementById('layer' + activeLayer);
                var nextIframe = document.getElementById('layer' + nextLayer);

                var viewParam = '';
                try {{
                    var activeDoc = activeIframe.contentWindow.document;
                    if (activeDoc && activeDoc.body && activeDoc.body.classList.contains('dashboard-mode-active')) {{
                        viewParam = '&view=dashboard';
                    }} else if (currentVersion === pendingVersion && initialDefaultView === 'dashboard' && !activeDoc.body.innerHTML) {{
                        viewParam = '&view=dashboard';
                    }}
                }} catch(e) {{
                    if (initialDefaultView === 'dashboard') viewParam = '&view=dashboard';
                }}

                nextIframe.src = '/map-data?v=' + currentVersion + viewParam + '&t=' + new Date().getTime();

                nextIframe.onload = function() {{
                    var checkReady = setInterval(function() {{
                        var newMap = getMapInstance(nextIframe);
                        var oldMap = getMapInstance(activeIframe);

                        var hasErrorPage = false;
                        try {{ hasErrorPage = nextIframe.contentWindow.document.body.innerHTML.includes('เกิดข้อผิดพลาด'); }} catch(e) {{}}

                        if (newMap || hasErrorPage) {{
                            clearInterval(checkReady);
                            if (oldMap && newMap) {{
                                newMap.setView(oldMap.getCenter(), oldMap.getZoom(), {{animate: false}});
                            }}

                            nextIframe.className = 'map-layer layer-active';
                            activeIframe.className = 'map-layer layer-hidden';
                            activeLayer = nextLayer;
                            isSwapping = false;

                            var loader = document.getElementById('loader');
                            if (loader) {{
                                loader.style.opacity = '0';
                                setTimeout(() => loader.style.display = 'none', 400);
                            }}

                            setTimeout(function() {{ activeIframe.src = 'about:blank'; }}, 1200);
                        }}
                    }}, 150);
                    setTimeout(() => {{ clearInterval(checkReady); isSwapping = false; }}, 10000);
                }};
            }}

            checkUpdate();

            var initialPoll = setInterval(() => {{
                if (currentVersion === 0) checkUpdate();
                else clearInterval(initialPoll);
            }}, 1500);

            setInterval(() => {{
                checkUpdate();
                tryApplyPendingSwap();
            }}, 15000);

            document.addEventListener('keydown', function(e) {{
                if (e.key === 'Escape' || e.keyCode === 27) {{
                    var ifr = document.getElementById('layer' + activeLayer);
                    if (ifr && ifr.contentWindow) {{
                        ifr.contentWindow.document.dispatchEvent(new KeyboardEvent('keydown', {{'key': 'Escape', 'keyCode': 27}}));
                    }}
                }}
            }});
        </script>
    </body>
    </html>
    """
    return html_wrapper

# สั่งเตรียมข้อมูลแผนที่และแดชบอร์ดเบื้องหลังทันทีเมื่อ Server เริ่มทำงาน (เช่น บน Render / Gunicorn)
trigger_update_if_needed()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)