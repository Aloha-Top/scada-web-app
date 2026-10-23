import sys
import io
import sqlite3
import ssl
import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
import glob
import urllib.request
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
            out_doc = (
                '<!DOCTYPE html>\n<html lang="th">\n<head>\n'
                '    <meta http-equiv="content-type" content="text/html; charset=UTF-8" />\n'
                '    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />\n'
                '    <title>รายงานความพร้อมใช้งานอุปกรณ์ในระบบ SCADA NE2 (%AVA)</title>\n'
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
            return out_doc
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


# ตั้งค่า Encoding และ Working Directory สำหรับการรันผ่านไฟล์ .bat บน Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()

# --- 1. ตั้งค่าโปรเจกต์พื้นฐาน (Configuration) ---
# หมายเหตุ: ลบโค้ดส่วน Git Upload ออกจาก Python ทั้งหมดแล้ว เพื่อย้ายไปจัดการในไฟล์ Batch (.bat) แยกต่างหาก
DB_PATH = os.path.join(BASE_DIR, 'scada_real.db')
DASHBOARD_OUTPUT_FILE = os.path.join(BASE_DIR, 'scada_dashboard.html')
DETAILED_MAP_OUTPUT_FILE = os.path.join(BASE_DIR, 'scada_detailed_map.html')
OUTPUT_FILE = DETAILED_MAP_OUTPUT_FILE

# โครงสร้าง Dictionary สำหรับจัดกลุ่มอุปกรณ์สื่อสารหลัก (รองรับการเพิ่มอุปกรณ์ชนิดอื่นๆ ได้ง่ายในอนาคต)
COMM_DEVICE_GROUPS = {
    "Cellular Router": [
        "CELLULAR ROUTER",
        "VSPP/CELLULAR ROUTER",
        "SPP/CELLULAR ROUTER",
        "ROUTER",
        "CELLULAR",
        "SIM",
        "3G",
        "4G",
        "5G",
        "LTE",
    ],
    "DAU": [
        "DAU",
        "DAU/J120",
        "J120",
    ],
    "Fiber Optic / RMU": [
        "CUS/FIBER OPTIC",
        "RMU/FIBER OPTIC",
        "FIBER OPTIC",
        "FIBER",
        "OFC",
        "OPTIC",
        "ONU",
        "OLT",
        "RMU",
        "CUS",
    ],
}


def classify_comm_device_group(commu_val, item_val=""):
    """จำแนกกลุ่มอุปกรณ์สื่อสารหลัก (เช่น 'Cellular Router', 'DAU') จาก Dictionary โครงสร้าง COMM_DEVICE_GROUPS"""
    raw_c = str(commu_val or "").strip()
    raw_i = str(item_val or "").strip()
    if raw_c.lower() in ("nan", "none", "nat", "-", "ไม่มีค่า", "ไม่ระบุ commu."):
        raw_c = ""
    if raw_i.lower() in ("nan", "none", "nat", "-", "ไม่มีค่า", "ไม่ระบุ item"):
        raw_i = ""
    combined = f"{raw_c} {raw_i}".strip()
    if not combined:
        return "ไม่ระบุ"

    upper_c = raw_c.upper()
    upper_all = combined.upper()

    # 1. ตรวจสอบจากคอลัมน์ Commu. เป็นลำดับแรก
    for group_name, keywords in COMM_DEVICE_GROUPS.items():
        for kw in keywords:
            if kw.upper() in upper_c:
                return group_name

    # 2. หากไม่พบใน Commu. ให้ตรวจสอบจากข้อความรวม (Commu. + Item)
    for group_name, keywords in COMM_DEVICE_GROUPS.items():
        for kw in keywords:
            if kw.upper() in upper_all:
                return group_name

    return "อุปกรณ์สื่อสารอื่นๆ"


# ตั้งค่า Google Sheets สำหรับดึงข้อมูลลงฐานข้อมูล SQLite (อัปเดตอัตโนมัติจาก Google Sheets / Google Form)
SPREADSHEET_ID = '10QuVWnj2BCPpNqrXpBM8sbARmKGTksQ1fxUYx2Xaa8Q'
SHEET1_GID = '639012274'
DATA_CHECK_GID = '2024067511'
UPDATE_STATE_GID = '0'
RTU_SUMMARY_GID = '747048278'
PEA_GIS_GID = '532577781'
USER_REPORT_GID = '1800640563'
USER_REPORT_SHEET_NAME = 'User_Report'
USER_REPORT_SHEET_URL = f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit?gid={USER_REPORT_GID}#gid={USER_REPORT_GID}'
USER_REPORT_GAS_WEBHOOK_URL = os.environ.get('SCADA_USER_REPORT_WEBHOOK_URL', 'https://script.google.com/macros/s/AKfycbxamC17XRskueymfpmR-PeRiF8c30kfznzqsoeAW5Y9a_1mAS4eeVVmBuvRy_3fN_k/exec')
FORM_SOURCE_ID = '1Qp-T7x891juPqp8co-dv7bV1pnPeeHFVQtE8JZpAqvg'
RTU_SUMMARY_SOURCE_ID = '1Yf2w0GJi7VJex12WBTD5F3UtWgsz1c6T9VSdV4jglgE'

# คอลัมน์จริงใน ชีต1 (21 คอลัมน์: A ถึง U) ตามโครงสร้างชีตจริง
SHEET1_RAW_COLS = [
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
    'DNP',
    'FRTU Model',
    'WRL Master',
    'ซ้ำ',
    'เช็ครหัส',
]

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
    'กลุ่มอุปกรณ์สื่อสารหลัก',
    'ประเภทย่อยอุปกรณ์สื่อสาร (Item)',
    'แม่ข่ายสื่อสาร (WRL Master / Base)',
]

# เรียงลำดับ URL โดยใช้ GViz CSV/JSON ก่อนเพื่อให้ได้ข้อมูลสดทันทีโดยไม่ติด Google CDN Cache นาน และมี /export?format=csv เป็นตัวสำรอง
SHEET1_URLS = [
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=1&gid={SHEET1_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={SHEET1_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:json&headers=1&gid={SHEET1_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=0&gid={SHEET1_GID}',
]

DATA_CHECK_URLS = [
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=1&gid={DATA_CHECK_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={DATA_CHECK_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet=Data_Check',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:json&headers=1&gid={DATA_CHECK_GID}',
    f'https://docs.google.com/spreadsheets/d/{FORM_SOURCE_ID}/gviz/tq?tqx=out:csv&sheet=Form3_Responses',
    f'https://docs.google.com/spreadsheets/d/{FORM_SOURCE_ID}/export?format=csv',
]

UPDATE_STATE_URLS = [
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=1&gid={UPDATE_STATE_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={UPDATE_STATE_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet=Update%20State',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:json&headers=1&gid={UPDATE_STATE_GID}',
]

RTU_SUMMARY_URLS = [
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=1&gid={RTU_SUMMARY_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={RTU_SUMMARY_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet=RTU%20Summary',
    f'https://docs.google.com/spreadsheets/d/{RTU_SUMMARY_SOURCE_ID}/gviz/tq?tqx=out:csv&sheet=%E0%B8%AA%E0%B8%A3%E0%B8%B8%E0%B8%9B%20RTU',
    f'https://docs.google.com/spreadsheets/d/{RTU_SUMMARY_SOURCE_ID}/export?format=csv',
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

# ==============================================================================
# --- โมดูล Google Gemini API (Free Tier), Session State, Fallback & Chunking ---
# ==============================================================================
import threading
import time
from datetime import datetime, timezone, timedelta

try:
    from google import genai
    from google.genai import types as genai_types
    HAS_GOOGLE_GENAI_SDK = True
except ImportError:
    genai = None
    genai_types = None
    HAS_GOOGLE_GENAI_SDK = False

# อ่านค่า API Key จาก Environment Variables เท่านั้น (หมายเหตุ: การอัปโหลดไฟล์ขึ้น Git ย้ายไปจัดการในไฟล์ .bat แยกต่างหาก)
DEFAULT_GEMINI_API_KEY = (
    os.environ.get("GEMINI_API_KEY")
    or os.environ.get("GOOGLE_API_KEY")
)

# ==============================================================================
# --- AI Prompt Integration: แยก System Prompt เป็น 2 ส่วนหลักตามแผนก (ผบอ. / ผอส.) ---
# ==============================================================================

# [System Prompt 1 สำหรับแผนก ผบอ.]
SYSTEM_PROMPT_PBO = """Act as a Senior Control System & Electrical Engineer สังกัด แผนก ผบอ. ของการไฟฟ้าส่วนภูมิภาค (PEA) ควบคู่กับบทบาท 'ผู้ชำนาญการระดับสูงด้านการใช้ภาษาไทยเชิงวิชาการ'
ขอบเขตความรับผิดชอบของคุณคือ: ตู้คอนโทรล, อุปกรณ์ภายในตู้, หม้อแปลงแรงดัน (VT) ที่เชื่อมกับระบบ 22kV เพื่อจ่ายไฟให้ตู้, อุปกรณ์สวิตช์เกียร์ (LBS, Recloser), ชุดควบคุมโทรมาตร (FRTU), บอร์ดคอนโทรลภายใน, คอนเวอร์เตอร์ (Converter), และระบบแบตเตอรี่
หน้าที่ของคุณคือการวิเคราะห์ข้อมูลดิบจากรายงานการซ่อมบำรุง และเรียบเรียงเป็น 'รายงานเชิงวิศวกรรม' ที่มีความเป็นมืออาชีพ สละสลวย และเนื้อหากระชับ
โครงสร้างการเขียน (บังคับ):
ให้ร้อยเรียงเรื่องราวเป็นประโยคอธิบายยาวแบบต่อเนื่อง (Flowing Narrative) 1 ย่อหน้า โดยต้องครอบคลุม 4 องค์ประกอบหลักตามลำดับนี้เสมอ:
1. สาเหตุ: ปัญหาเกิดจากอุปกรณ์ใดในความรับผิดชอบของ ผบอ. ชำรุดหรือขัดข้อง
2. การดำเนินการ: ได้แก้ไขเบื้องต้นหรือซ่อมแซมเปลี่ยนอุปกรณ์ฮาร์ดแวร์อย่างไร
3. ผลลัพธ์: สถานะปัจจุบันของตู้คอนโทรลและอุปกรณ์สวิตช์เกียร์
4. การดำเนินการต่อไป: ระบุอย่างชัดเจนว่า แผนก ผบอ. จะดำเนินการอะไรต่อไปในเชิงฮาร์ดแวร์/ระบบไฟ
5. รูปแบบประโยคหลักที่ต้องยึดถือ (ปรับคำเชื่อมให้สละสลวยได้):
6. 'เนื่องจาก... (สาเหตุ) จึงได้ดำเนินการ... (การดำเนินการ) ส่งผลให้... (ผลลัพธ์) ทั้งนี้ ในระยะต่อไปทางแผนก ผบอ. จะดำเนินการ... (การดำเนินการต่อไป)'
7. ข้อกำหนดสำคัญระดับสูง (Rules & Constraints):
8. ความสมบูรณ์แบบทางภาษา (Linguistic Excellence): ตัดคำฟุ่มเฟือย คำวกวน หรือการใช้คำซ้ำซ้อนทางความหมายออกอย่างเด็ดขาด (เช่น หลีกเลี่ยงการใช้คำว่า 'ทำการ' หรือ 'ดำเนินการ' ซ้ำซ้อนกันหลายรอบ) ประโยคต้องกระชับ อ่านลื่นไหล แต่ยังคงรายละเอียดทางเทคนิคไว้อย่างครบถ้วน
9. Technical Terminology: ใช้คำศัพท์เฉพาะทางให้ถูกต้อง (เช่น FRTU, Recloser, LBS, VT 22kV, Control Board, Converter)
10. Professional Formatting: ห้ามใช้สัญลักษณ์ (เช่น ✅, ❌, •, -) หรือการขึ้นบรรทัดใหม่แบบหัวข้อย่อยเด็ดขาด
11. Handling Missing Data: หากข้อมูลไม่ครบ ให้ใช้ตรรกะทางวิศวกรรมประเมินจาก 'สถานะสุดท้าย' และกำหนดแผนการดำเนินงานของ ผบอ. ตามมาตรฐาน ห้ามแต่งเติมชื่ออุปกรณ์ที่ไม่มีในบริบท"""

# [System Prompt 2 สำหรับแผนก ผอส.]
SYSTEM_PROMPT_POS = """Act as a Senior SCADA & Communication Engineer สังกัด แผนก ผอส. ของการไฟฟ้าส่วนภูมิภาค (PEA) ควบคู่กับบทบาท 'ผู้ชำนาญการระดับสูงด้านการใช้ภาษาไทยเชิงวิชาการ'
ขอบเขตความรับผิดชอบของคุณคือ: ระบบสื่อสารข้อมูล, อุปกรณ์สื่อสาร (Modem, Router), สายสัญญาณสื่อสาร, แผงสัญญาณเสาอากาศ, ซิมการ์ด, สัญญาณเครือข่าย, และการเชื่อมต่อโปรโตคอลระหว่างตู้ FRTU กับศูนย์ควบคุม SCADA
หน้าที่ของคุณคือการวิเคราะห์ข้อมูลดิบจากรายงานการซ่อมบำรุง และเรียบเรียงเป็น 'รายงานเชิงวิศวกรรม' ที่มีความเป็นมืออาชีพ สละสลวย และเนื้อหากระชับ
โครงสร้างการเขียน (บังคับ):
ให้ร้อยเรียงเรื่องราวเป็นประโยคอธิบายยาวแบบต่อเนื่อง (Flowing Narrative) 1 ย่อหน้า โดยต้องครอบคลุม 4 องค์ประกอบหลักตามลำดับนี้เสมอ:
1. สาเหตุ: ปัญหาเกิดจากอุปกรณ์สื่อสารใดในความรับผิดชอบของ ผอส. ขัดข้อง
2. การดำเนินการ: ได้แก้ไขเบื้องต้นอย่างไร
3. ผลลัพธ์: สถานะการเชื่อมต่อสื่อสารปัจจุบัน
4. การดำเนินการต่อไป: ระบุอย่างชัดเจนว่า แผนก ผอส. จะดำเนินการอะไรต่อไปในเชิงระบบสื่อสาร
5. รูปแบบประโยคหลักที่ต้องยึดถือ (ปรับคำเชื่อมให้สละสลวยได้):
6. 'เนื่องจาก... (สาเหตุ) จึงได้ดำเนินการ... (การดำเนินการ) ส่งผลให้... (ผลลัพธ์) ทั้งนี้ ในระยะต่อไปทางแผนก ผอส. จะดำเนินการ... (การดำเนินการต่อไป)'
7. ข้อกำหนดสำคัญระดับสูง (Rules & Constraints):
8. ความสมบูรณ์แบบทางภาษา (Linguistic Excellence): ตัดคำฟุ่มเฟือย คำวกวน หรือการใช้คำซ้ำซ้อนทางความหมายออกอย่างเด็ดขาด (เช่น หลีกเลี่ยงการใช้คำว่า 'ทำการ' หรือ 'ดำเนินการ' ซ้ำซ้อนกันหลายรอบ) ประโยคต้องกระชับ อ่านลื่นไหล แต่ยังคงรายละเอียดทางเทคนิคไว้อย่างครบถ้วน
9. Technical Terminology: ใช้คำศัพท์เฉพาะทางด้านสื่อสารให้ถูกต้อง (เช่น Communication Loss, Antenna, Signal Cable, Modem, Protocol)
10. Professional Formatting: ห้ามใช้สัญลักษณ์ (เช่น ✅, ❌, •, -) หรือการขึ้นบรรทัดใหม่แบบหัวข้อย่อยเด็ดขาด
11. Handling Missing Data: หากข้อมูลไม่ครบ ให้ใช้ตรรกะทางวิศวกรรมประเมินจาก 'สถานะสุดท้าย' และกำหนดแผนการดำเนินงานของ ผอส. ตามมาตรฐาน ห้ามแต่งเติมชื่ออุปกรณ์ที่ไม่มีในบริบท"""

# รวม System Prompt มาตรฐานสำหรับอ้างอิงและตั้งค่าเริ่มต้น
ENGINEERING_SYSTEM_PROMPT = (
    "[System Prompt 1 สำหรับแผนก ผบอ.]\n"
    + SYSTEM_PROMPT_PBO
    + "\n\n[System Prompt 2 สำหรับแผนก ผอส.]\n"
    + SYSTEM_PROMPT_POS
)


def resolve_department_for_row(resp_str="", detail_str=""):
    """จำแนกแผนกผู้รับผิดชอบหลัก ('PBO' สำหรับ ผบอ. หรือ 'POS' สำหรับ ผอส.) จากคอลัมน์ผู้รับผิดชอบและรายละเอียดอาการ"""
    resp = str(resp_str or "").strip()
    detail = str(detail_str or "").strip().upper()
    has_pbo = "ผบอ" in resp
    has_pos = "ผอส" in resp

    if has_pbo and not has_pos:
        return "PBO"
    if has_pos and not has_pbo:
        return "POS"

    pbo_kw = [
        "FRTU", "RECLOSER", "LBS", "VT", "22KV", "CONTROL BOARD", "CONVERTER",
        "BATTERY", "แบต", "ตู้คอนโทรล", "หม้อแปลง", "ฟิวส์", "FUSE", "NO AC",
        "ไม่มีไฟ", "บอร์ด", "การ์ด", "AVR", "BREAKER", "SWITCHGEAR"
    ]
    pos_kw = [
        "MODEM", "ROUTER", "CELLULAR", "DAU", "SIM", "ซิม", "ANTENNA", "เสาอากาศ",
        "สายสัญญาณ", "SIGNAL", "PROTOCOL", "DNP", "COMMUNICATION", "สื่อสาร",
        "สัญญาณ", "เครือข่าย", "PORT LAN", "WRL"
    ]
    pbo_score = sum(1 for k in pbo_kw if k in detail)
    pos_score = sum(1 for k in pos_kw if k in detail)

    if has_pbo and has_pos:
        return "PBO" if pbo_score >= pos_score else "POS"
    if pbo_score > pos_score:
        return "PBO"
    return "POS"


def get_system_prompt_for_department(dept_code="POS", custom_prompt=None):
    """คืนค่า System Prompt ตามแผนกที่รับผิดชอบ (ผบอ. ใช้ SYSTEM_PROMPT_PBO / ผอส. ใช้ SYSTEM_PROMPT_POS)"""
    if custom_prompt and str(custom_prompt).strip():
        cp = str(custom_prompt).strip()
        if "[System Prompt 1 สำหรับแผนก ผบอ.]" in cp and "[System Prompt 2 สำหรับแผนก ผอส.]" in cp:
            parts = cp.split("[System Prompt 2 สำหรับแผนก ผอส.]")
            if str(dept_code).upper() == "PBO":
                return parts[0].replace("[System Prompt 1 สำหรับแผนก ผบอ.]", "").strip()
            return parts[1].strip()
        return cp
    return SYSTEM_PROMPT_PBO if str(dept_code).upper() == "PBO" else SYSTEM_PROMPT_POS

# กำหนดรายการโมเดล AI โดยให้ความสำคัญกับโมเดลที่ "ฉลาดที่สุด" เป็นอันดับแรก (Pro / วิเคราะห์ขั้นสูง -> โมเดลที่ฉลาดรองลงมาตามลำดับ)
GEMINI_FREE_TIER_MODELS = {
    "gemini-3.5-pro-exp": {
        "label": "gemini-3.5-pro-exp (โมเดลหลักอันดับ 1 - ตระกูล Pro วิเคราะห์ขั้นสูง ฉลาดที่สุด)",
        "role": "โมเดลหลักอันดับ 1 (Smartest Pro)",
        "rpd_limit": 50,
        "rpm_limit": 5,
        "tpm_limit": 250000,
        "max_chunk_chars": 4800,
        "max_chunk_rows": 25,
        "simulated_disabled": False,
        "sdk_candidates": [
            "gemini-3.5-pro-exp",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
        ],
    },
    "gemini-3.8-flash": {
        "label": "gemini-3.8-flash (โมเดลสำรองอันดับ 1 - เน้นวิเคราะห์เชิงลึก ฉลาดรองลงมา)",
        "role": "โมเดลสำรองอันดับ 1 (Fallback 1)",
        "rpd_limit": 100,
        "rpm_limit": 10,
        "tpm_limit": 250000,
        "max_chunk_chars": 4500,
        "max_chunk_rows": 25,
        "sdk_candidates": [
            "gemini-3.8-flash",
            "gemini-2.5-flash",
            "gemini-3-flash-preview",
            "gemini-2.0-flash",
        ],
    },
    "gemini-3.5-flash-lite": {
        "label": "gemini-3.5-flash-lite (โมเดลสำรองอันดับ 2 - ความเร็วสูงและโควต้า 500 RPD)",
        "role": "โมเดลสำรองอันดับ 2 (Fallback 2)",
        "rpd_limit": 500,
        "rpm_limit": 15,
        "tpm_limit": 250000,
        "max_chunk_chars": 3800,
        "max_chunk_rows": 20,
        "sdk_candidates": [
            "gemini-3.5-flash-lite",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash-lite",
            "gemini-2.5-flash",
        ],
    },
    "gemini-3.1-flash-lite": {
        "label": "gemini-3.1-flash-lite (โมเดลสำรองอันดับ 3 - ความเสถียรสูง โควต้า 500 RPD)",
        "role": "โมเดลสำรองอันดับ 3 (Fallback 3)",
        "rpd_limit": 500,
        "rpm_limit": 15,
        "tpm_limit": 250000,
        "max_chunk_chars": 3600,
        "max_chunk_rows": 18,
        "sdk_candidates": [
            "gemini-3.1-flash-lite",
            "gemini-3.1-flash-lite-preview",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
        ],
    },
}

# ลำดับการสลับโมเดลอัตโนมัติ (Model Fallback Order): เรียงจากโมเดลที่ฉลาดที่สุดไปยังโมเดลที่ฉลาดรองลงมาเรื่อยๆ
GEMINI_FALLBACK_ORDER = [
    "gemini-3.5-pro-exp",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]

ROLLING_WINDOW_COOLDOWN_SECONDS = 60  # ล็อกปุ่มกดสำหรับโมเดลที่ติด 429 เป็นเวลา 60 วินาที (Rolling Window 60s)

_GEMINI_STATE_LOCK = threading.Lock()


def get_next_utc_midnight_timestamp(now_ts=None):
    """คำนวณ Timestamp ของเวลา 00:00 UTC ถัดไป (ตรงกับเวลา 07:00 น. ตามเวลาประเทศไทย UTC+7)"""
    now_dt = datetime.fromtimestamp(now_ts or time.time(), tz=timezone.utc)
    next_midnight_utc = (now_dt + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return next_midnight_utc.timestamp()


def get_seconds_until_rpd_reset(now_ts=None):
    """คืนค่าจำนวนวินาทีคงเหลือก่อนถึง 00:00 UTC (07:00 น. เวลาไทย)"""
    now_val = now_ts or time.time()
    return max(1, int(get_next_utc_midnight_timestamp(now_val) - now_val))


def format_thai_countdown_hms(seconds):
    """แปลงจำนวนวินาทีเป็นข้อความ ชม. นาที วินาที สำหรับแสดงบน Tooltip"""
    sec = max(0, int(seconds))
    hrs = sec // 3600
    mins = (sec % 3600) // 60
    rem_s = sec % 60
    if hrs > 0:
        return f"{hrs} ชม. {mins} นาที {rem_s} วินาที"
    if mins > 0:
        return f"{mins} นาที {rem_s} วินาที"
    return f"{rem_s} วินาที"


def _create_initial_session_state():
    now_ts = time.time()
    utc_day_str = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    models_state = {}
    for m_name, cfg in GEMINI_FREE_TIER_MODELS.items():
        is_sim_dis = bool(cfg.get("simulated_disabled", False))
        models_state[m_name] = {
            "rpd_used": cfg["rpd_limit"] if is_sim_dis else 0,
            "rpd_limit": cfg["rpd_limit"],
            "cooldown_until": 0.0,      # Timestamp ปลดล็อก 60s Rolling Window (Rate Limit 429 / TPM)
            "rpd_locked_until": get_next_utc_midnight_timestamp(now_ts) if is_sim_dis else 0.0,
            "last_error": cfg.get("simulated_reason", "") if is_sim_dis else "",
            "last_error_type": "RPD_EXHAUSTED" if is_sim_dis else "",
            "resolved_sdk_model": cfg["sdk_candidates"][0],
        }
    return {
        "utc_day": utc_day_str,
        # ตั้งค่าเริ่มต้น (Default) ให้เลือกโมเดลที่ฉลาดที่สุดเป็นอันดับแรก (gemini-3.5-pro-exp)
        "selected_model": "gemini-3.5-pro-exp",
        "auto_fallback_enabled": True,
        "api_key_configured": bool(DEFAULT_GEMINI_API_KEY),
        "models": models_state,
        "last_used_model": "",
        "fallback_history": [],
    }


# โครงสร้าง session_state หลังบ้านสำหรับบันทึกสถานะ Rate Limit (60s Rolling Window) และ RPD ของแต่ละโมเดล
session_state = _create_initial_session_state()


def sync_and_get_session_state(now_ts=None):
    """ตรวจสอบและรีเซ็ตโควต้ารายวันอัตโนมัติเมื่อข้ามวัน UTC (07:00 น. เวลาไทย) พร้อมปลดล็อก Rolling Window ที่ครบ 60 วินาที"""
    now_val = now_ts or time.time()
    utc_day_str = datetime.fromtimestamp(now_val, tz=timezone.utc).strftime("%Y-%m-%d")

    with _GEMINI_STATE_LOCK:
        if session_state.get("utc_day") != utc_day_str:
            session_state["utc_day"] = utc_day_str
            for m_name, m_st in session_state["models"].items():
                m_st["rpd_used"] = 0
                m_st["rpd_locked_until"] = 0.0
                if m_st.get("last_error_type") == "RPD_EXHAUSTED":
                    m_st["last_error_type"] = ""
                    m_st["last_error"] = ""

        models_snapshot = {}
        for m_name, m_st in session_state["models"].items():
            if m_st["cooldown_until"] > 0 and now_val >= m_st["cooldown_until"]:
                m_st["cooldown_until"] = 0.0
                if m_st.get("last_error_type") in ("RATE_LIMIT_429", "TOKEN_LIMIT"):
                    m_st["last_error_type"] = ""
                    m_st["last_error"] = ""

            if m_st["rpd_locked_until"] > 0 and now_val >= m_st["rpd_locked_until"]:
                m_st["rpd_locked_until"] = 0.0
                m_st["rpd_used"] = 0
                if m_st.get("last_error_type") == "RPD_EXHAUSTED":
                    m_st["last_error_type"] = ""
                    m_st["last_error"] = ""

            cd_rem = max(0, int(round(m_st["cooldown_until"] - now_val)))
            rpd_rem = max(0, int(round(m_st["rpd_locked_until"] - now_val)))
            is_rpd_full = (m_st["rpd_used"] >= m_st["rpd_limit"]) or (rpd_rem > 0)
            if is_rpd_full and rpd_rem == 0:
                m_st["rpd_locked_until"] = get_next_utc_midnight_timestamp(now_val)
                rpd_rem = max(1, int(round(m_st["rpd_locked_until"] - now_val)))

            is_blocked = (cd_rem > 0) or is_rpd_full
            tooltip_text = ""
            if is_rpd_full:
                tooltip_text = (
                    f"⚠️ ติดโควต้ารายวัน (RPD) โมเดล {m_name} ({m_st['rpd_used']}/{m_st['rpd_limit']} RPD) "
                    f"จะรีเซ็ตเวลา 07:00 น. (อีก {format_thai_countdown_hms(rpd_rem)})"
                )
            elif cd_rem > 0:
                reason_label = "Token Limit (TPM)" if m_st.get("last_error_type") == "TOKEN_LIMIT" else "Rate Limit (429)"
                tooltip_text = f"⚠️ ติด {reason_label} โมเดล {m_name} ปลดล็อกใน {cd_rem} วินาที"

            models_snapshot[m_name] = {
                "model": m_name,
                "label": GEMINI_FREE_TIER_MODELS[m_name]["label"],
                "role": GEMINI_FREE_TIER_MODELS[m_name]["role"],
                "rpd_used": m_st["rpd_used"],
                "rpd_limit": m_st["rpd_limit"],
                "rpd_remaining": max(0, m_st["rpd_limit"] - m_st["rpd_used"]),
                "cooldown_until": m_st["cooldown_until"],
                "cooldown_remaining_sec": cd_rem,
                "rpd_locked_until": m_st["rpd_locked_until"],
                "rpd_reset_remaining_sec": rpd_rem,
                "is_blocked": is_blocked,
                "block_type": "RPD" if is_rpd_full else ("COOLDOWN_60S" if cd_rem > 0 else "READY"),
                "tooltip": tooltip_text,
                "last_error": m_st.get("last_error", ""),
                "resolved_sdk_model": m_st.get("resolved_sdk_model", m_name),
            }

        return {
            "server_time": now_val,
            "next_rpd_reset_utc_ts": get_next_utc_midnight_timestamp(now_val),
            "rpd_reset_seconds": get_seconds_until_rpd_reset(now_val),
            "utc_day": session_state["utc_day"],
            "has_google_genai_sdk": HAS_GOOGLE_GENAI_SDK,
            "fallback_order": GEMINI_FALLBACK_ORDER,
            "models": models_snapshot,
            "last_used_model": session_state.get("last_used_model", ""),
        }


def mark_model_rate_limited(model_name, error_msg="", is_rpd=False, is_tpm=False):
    """บันทึกสถานะลง session_state เมื่อโมเดลติด Error 429 (ล็อก 60 วินาที) หรือติด RPD (ล็อกถึง 07:00 น. เวลาไทย)"""
    now_val = time.time()
    with _GEMINI_STATE_LOCK:
        if model_name not in session_state["models"]:
            return
        m_st = session_state["models"][model_name]
        m_st["last_error"] = str(error_msg or "")[:300]
        if is_rpd or m_st["rpd_used"] >= m_st["rpd_limit"]:
            m_st["rpd_used"] = m_st["rpd_limit"]
            m_st["rpd_locked_until"] = get_next_utc_midnight_timestamp(now_val)
            m_st["last_error_type"] = "RPD_EXHAUSTED"
        else:
            m_st["cooldown_until"] = now_val + ROLLING_WINDOW_COOLDOWN_SECONDS
            m_st["last_error_type"] = "TOKEN_LIMIT" if is_tpm else "RATE_LIMIT_429"


def reset_model_limit_state(model_name=None):
    """รีเซ็ตสถานะล็อกของโมเดลใน session_state"""
    with _GEMINI_STATE_LOCK:
        targets = [model_name] if model_name in session_state["models"] else list(session_state["models"].keys())
        for m in targets:
            session_state["models"][m]["cooldown_until"] = 0.0
            session_state["models"][m]["rpd_locked_until"] = 0.0
            session_state["models"][m]["rpd_used"] = 0
            session_state["models"][m]["last_error"] = ""
            session_state["models"][m]["last_error_type"] = ""


def increment_model_rpd_usage(model_name, resolved_sdk_name=None):
    """เพิ่มตัวนับการใช้งานรายวัน (RPD) ของโมเดลหลังเรียกใช้งานสำเร็จ"""
    now_val = time.time()
    with _GEMINI_STATE_LOCK:
        if model_name in session_state["models"]:
            m_st = session_state["models"][model_name]
            m_st["rpd_used"] = int(m_st.get("rpd_used", 0)) + 1
            if resolved_sdk_name:
                m_st["resolved_sdk_model"] = resolved_sdk_name
            if m_st["rpd_used"] >= m_st["rpd_limit"]:
                m_st["rpd_locked_until"] = get_next_utc_midnight_timestamp(now_val)
                m_st["last_error_type"] = "RPD_EXHAUSTED"
            session_state["last_used_model"] = model_name


def _classify_gemini_exception(exc_str):
    """จำแนกประเภทข้อผิดพลาดจากข้อความ Exception ของ Gemini API"""
    low = str(exc_str or "").lower()
    is_404 = any(k in low for k in ["404", "not_found", "not found", "is not found for api version", "not supported"])
    is_429 = any(k in low for k in ["429", "resource_exhausted", "too many requests", "rate limit", "quota"])
    is_rpd = any(k in low for k in ["perday", "per_day", "requests per day", "rpd", "daily limit", "daily quota"])
    is_tpm = any(k in low for k in ["token", "tpm", "input_token", "context length", "too large", "payload size"])
    is_auth = any(k in low for k in ["api_key_invalid", "invalid api key", "401", "403", "permission_denied", "unauthenticated"])
    return {
        "is_404": is_404,
        "is_429": is_429,
        "is_rpd": is_rpd,
        "is_tpm": is_tpm,
        "is_auth": is_auth,
    }


def _call_single_gemini_candidate(sdk_model_id, prompt_text, api_key, temperature=0.2, system_instruction=None):
    """เชื่อมต่อ Gemini API โดยใช้ Google GenAI SDK เวอร์ชันล่าสุด (google-genai) พร้อม System Instruction และ REST API สำรอง"""
    clean_key = str(api_key or DEFAULT_GEMINI_API_KEY).strip()
    if not clean_key:
        raise RuntimeError("ไม่พบ Gemini API Key กรุณาระบุ API Key ของตนเองในหน้าต่างตั้งค่า หรือตั้งค่าใน Environment Variable")

    sys_inst = str(system_instruction or ENGINEERING_SYSTEM_PROMPT).strip()

    # 1. เรียกผ่าน Google GenAI SDK ล่าสุด (google-genai) หากติดตั้งไว้ในระบบ
    if HAS_GOOGLE_GENAI_SDK and genai is not None:
        client = genai.Client(api_key=clean_key)
        cfg = (
            genai_types.GenerateContentConfig(
                temperature=temperature,
                system_instruction=sys_inst,
            )
            if genai_types is not None
            else {"temperature": temperature, "system_instruction": sys_inst}
        )
        resp = client.models.generate_content(
            model=sdk_model_id,
            contents=prompt_text,
            config=cfg,
        )
        out_text = getattr(resp, "text", None)
        if out_text:
            return str(out_text).strip()

    # 2. Fallback เรียกผ่าน Google Generative Language REST API v1beta โดยตรง
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{sdk_model_id}:generateContent?key={clean_key}"
    payload = {
        "systemInstruction": {"parts": [{"text": sys_inst}]},
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {"temperature": temperature},
    }
    req_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=req_bytes,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    opener = _build_url_opener()
    try:
        with opener.open(req, timeout=45) as resp:
            raw_body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw_body)
            candidates = data.get("candidates") or []
            if candidates:
                parts = (candidates[0].get("content") or {}).get("parts") or []
                texts = [str(p.get("text", "")) for p in parts if p.get("text")]
                if texts:
                    return "\n".join(texts).strip()
            raise RuntimeError(f"Empty response from Gemini model {sdk_model_id}")
    except urllib.error.HTTPError as http_err:
        err_body = ""
        try:
            err_body = http_err.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"HTTP {http_err.code}: {err_body or http_err.reason}")


# ตั้งค่า Default preferred_model ให้เริ่มจากโมเดลที่ฉลาดที่สุด (gemini-3.5-pro-exp) และ Fallback ไปยังโมเดลที่ฉลาดรองลงมาตามลำดับ
def call_gemini_with_fallback(prompt_text, preferred_model="gemini-3.5-pro-exp", api_key=None, system_instruction=None):
    """
    เรียกใช้งาน Gemini API พร้อมระบบ:
    - เริ่มต้นจากโมเดลที่ฉลาดที่สุด (gemini-3.5-pro-exp) หรือโมเดลที่ผู้ใช้เลือก
    - หากโมเดลที่ฉลาดที่สุดไม่พร้อมใช้งาน ติด Rate Limit (429) หรือโควต้าเต็ม จะเรียงลำดับสลับ (Fallback)
      ไปยังโมเดลที่ฉลาดรองลงมาเรื่อยๆ ตาม GEMINI_FALLBACK_ORDER:
      gemini-3.5-pro-exp -> gemini-3.8-flash -> gemini-3.5-flash-lite -> gemini-3.1-flash-lite
    """
    state_snap = sync_and_get_session_state()
    models_info = state_snap["models"]

    if preferred_model == "auto_fallback" or preferred_model not in GEMINI_FREE_TIER_MODELS:
        candidate_logical_models = list(GEMINI_FALLBACK_ORDER)
    else:
        # เริ่มจากโมเดลที่เลือก หากไม่พร้อมใช้งานให้สลับไปยังโมเดลที่ฉลาดรองลงมาตามลำดับใน GEMINI_FALLBACK_ORDER
        candidate_logical_models = [preferred_model] + [
            m for m in GEMINI_FALLBACK_ORDER if m != preferred_model
        ]

    fallback_trace = []
    last_err_msg = ""

    for logical_model in candidate_logical_models:
        m_status = models_info.get(logical_model, {})
        if m_status.get("is_blocked"):
            fallback_trace.append({
                "model": logical_model,
                "status": "skipped_blocked",
                "reason": m_status.get("tooltip", "Blocked"),
            })
            continue

        sdk_candidates = GEMINI_FREE_TIER_MODELS[logical_model]["sdk_candidates"]

        for sdk_id in sdk_candidates:
            try:
                res_text = _call_single_gemini_candidate(sdk_id, prompt_text, api_key, system_instruction=system_instruction)
                increment_model_rpd_usage(logical_model, resolved_sdk_name=sdk_id)
                fallback_trace.append({
                    "model": logical_model,
                    "sdk_model": sdk_id,
                    "status": "success",
                })
                return {
                    "text": res_text,
                    "used_model": logical_model,
                    "resolved_sdk_model": sdk_id,
                    "fallback_trace": fallback_trace,
                }
            except Exception as exc:
                err_str = str(exc)
                last_err_msg = err_str
                err_info = _classify_gemini_exception(err_str)

                if err_info["is_404"]:
                    continue

                if err_info["is_429"] or err_info["is_rpd"]:
                    mark_model_rate_limited(
                        logical_model,
                        error_msg=err_str,
                        is_rpd=err_info["is_rpd"],
                        is_tpm=err_info["is_tpm"],
                    )
                    fallback_trace.append({
                        "model": logical_model,
                        "sdk_model": sdk_id,
                        "status": "rate_limited_429",
                        "reason": err_str[:200],
                    })
                    break

                if err_info["is_tpm"]:
                    raise RuntimeError(f"TOKEN_LIMIT_EXCEEDED: {err_str}")

                if err_info["is_auth"]:
                    raise RuntimeError(f"API_KEY_ERROR: {err_str}")

                fallback_trace.append({
                    "model": logical_model,
                    "sdk_model": sdk_id,
                    "status": "error",
                    "reason": err_str[:200],
                })
                break

    raise RuntimeError(last_err_msg or "ทุกโมเดลในรายการ Fallback ติดขีดจำกัดการใช้งานชั่วคราว")


def chunk_inspection_rows(rows, max_chars=3500, max_rows=18):
    """แบ่งข้อมูลรายการตรวจสอบจาก Google Sheet ออกเป็นส่วนย่อย (Chunks) อัตโนมัติเพื่อป้องกันปัญหา Token Limit (TPM)"""
    if not rows:
        return []
    chunks = []
    current_chunk = []
    current_chars = 0

    for r in rows:
        row_repr = f"{r.get('site','')} | {r.get('cmd','')} | {r.get('loc','')} | {r.get('detail','')} | {r.get('resp','')} | {r.get('status','')}"
        row_len = len(row_repr)
        if current_chunk and (len(current_chunk) >= max_rows or (current_chars + row_len) > max_chars):
            chunks.append(current_chunk)
            current_chunk = [r]
            current_chars = row_len
        else:
            current_chunk.append(r)
            current_chars += row_len

    if current_chunk:
        chunks.append(current_chunk)
    return chunks


def _normalize_engineering_clause_text(text):
    """ทำความสะอาดข้อความ แก้คำผิด/ช่องว่างซ้ำ และตัดสัญลักษณ์ไฮไลท์เดิมออก"""
    s = str(text or "").strip()
    s = re.sub(r"^[•\-\s✅❌☑☒✔✘❗️⚠️]+", "", s)
    s = re.sub(r"[✅❌☑☒✔✘❗️⚠️]+", " ", s)
    # แก้ไขคำผิดหรือรูปแบบคำที่พบบ่อยในงาน SCADA
    typo_fixes = [
        (r"\bNo\s*AC\b", "ไม่มีไฟเลี้ยง AC"),
        (r"\bNO\s*AC\b", "ไม่มีไฟเลี้ยง AC"),
        (r"อุปกรณ์สื่อสาร\s+ปกติ", "อุปกรณ์สื่อสารทำงานปกติ"),
        (r"ระบบสื่อสาร\s*ONLINE", "ระบบสื่อสารสามารถเชื่อมต่อ ONLINE ได้ตามปกติ"),
        (r"ทดแทนตัวที่ชำรุด\s*DAU\s*ที่ชำรุด", "ทดแทนชุดเดิมที่ชำรุด"),
        (r"DAU/Cellular\s*Router\s*ชำรุด", "อุปกรณ์ DAU และ Cellular Router ชำรุด"),
        (r"ชํารุด", "ชำรุด"),
        (r"สือสาร", "สื่อสาร"),
        (r"เเบตเตอรี่", "แบตเตอรี่"),
    ]
    for pat, repl in typo_fixes:
        s = re.sub(pat, repl, s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" ,;|-")
    return s


def _deterministic_categorize_row_detail(raw_detail, resp_str="", status_str=""):
    """
    ฟังก์ชันเรียบเรียงประโยครายงานเชิงวิศวกรรม (Flowing Narrative 1 ย่อหน้า 4 องค์ประกอบ) แยกตามแผนก ผบอ. และ ผอส.:
    'เนื่องจาก... (สาเหตุ) จึงได้ดำเนินการ... (การดำเนินการ) ส่งผลให้... (ผลลัพธ์) ทั้งนี้ ในระยะต่อไปทางแผนก ผบอ./ผอส. จะดำเนินการ... (การดำเนินการต่อไป)'
    """
    txt = str(raw_detail or "").strip()
    st_clean = str(status_str or "").strip()
    resp_clean = str(resp_str or "").strip()
    dept = resolve_department_for_row(resp_clean, txt)
    dept_label = "ผบอ." if dept == "PBO" else "ผอส."

    if txt.startswith("เนื่องจาก") and "ทั้งนี้ ในระยะต่อไปทางแผนก" in txt:
        return re.sub(r"[✅❌•\-]+", " ", txt).replace("\n", " ").strip()

    if not txt or txt == "-":
        if "ONLINE" in st_clean.upper():
            if dept == "PBO":
                return (
                    "เนื่องจากตรวจพบการแจ้งเตือนสถานะการทำงานของตู้คอนโทรลและชุดควบคุมโทรมาตร (FRTU) จากระบบตรวจวัด "
                    "จึงได้เข้าตรวจสอบระบบไฟเลี้ยง หม้อแปลงแรงดัน (VT 22kV) และอุปกรณ์สวิตช์เกียร์ในความรับผิดชอบของแผนก ผบอ. "
                    "ส่งผลให้ปัจจุบันตู้คอนโทรลและอุปกรณ์สวิตช์เกียร์สามารถทำงานและเชื่อมต่อระบบได้ตามปกติ "
                    "ทั้งนี้ ในระยะต่อไปทางแผนก ผบอ. จะติดตามเฝ้าระวังเสถียรภาพของระบบจ่ายไฟและฮาร์ดแวร์ภายในตู้คอนโทรลตามแผนบำรุงรักษา"
                )
            return (
                "เนื่องจากตรวจพบสภาวะการสูญเสียการสื่อสารข้อมูล (Communication Loss) ระหว่างตู้ FRTU กับศูนย์ควบคุม SCADA "
                "จึงได้เข้าตรวจสอบอุปกรณ์สื่อสาร (Modem/Router) ซิมการ์ด และระดับสัญญาณเครือข่ายในความรับผิดชอบของแผนก ผอส. "
                "ส่งผลให้ปัจจุบันการเชื่อมต่อโปรโตคอลและระบบสื่อสารข้อมูลกลับมาออนไลน์ได้ตามปกติ "
                "ทั้งนี้ ในระยะต่อไปทางแผนก ผอส. จะเฝ้าระวังคุณภาพสัญญาณเครือข่ายและเสถียรภาพของการเชื่อมต่อสื่อสารอย่างต่อเนื่อง"
            )
        if dept == "PBO":
            return (
                f"เนื่องจากตรวจพบความผิดปกติของอุปกรณ์ภายในตู้คอนโทรลและระบบจ่ายไฟทำให้มีสถานะ {st_clean or 'Offline'} "
                f"จึงได้เข้าตรวจสอบเบื้องต้นในส่วนของชุดควบคุมโทรมาตร (FRTU) คอนเวอร์เตอร์ และระบบแบตเตอรี่ "
                f"ส่งผลให้ปัจจุบันตู้คอนโทรลและอุปกรณ์สวิตช์เกียร์ยังคงมีสถานะ {st_clean or 'Offline'} "
                f"ทั้งนี้ ในระยะต่อไปทางแผนก ผบอ. จะเข้าตรวจสอบเชิงลึกและซ่อมแซมหรือเปลี่ยนอุปกรณ์ฮาร์ดแวร์ที่ชำรุดเพื่อให้ระบบกลับมาใช้งานได้ตามปกติ"
            )
        return (
            f"เนื่องจากตรวจพบปัญหาการเชื่อมต่อระบบสื่อสารข้อมูลขัดข้องทำให้มีสถานะ {st_clean or 'Offline'} "
            f"จึงได้เข้าตรวจสอบเบื้องต้นในส่วนของอุปกรณ์สื่อสาร (Modem/Router) สายสัญญาณ และแผงสัญญาณเสาอากาศ "
            f"ส่งผลให้ปัจจุบันสถานะการเชื่อมต่อสื่อสารยังคงเป็น {st_clean or 'Offline'} "
            f"ทั้งนี้ ในระยะต่อไปทางแผนก ผอส. จะเข้าตรวจสอบแก้ไขระบบสื่อสารและปรับปรุงการเชื่อมต่อโปรโตคอลกับศูนย์ควบคุม SCADA ให้กลับมาออนไลน์โดยเร็ว"
        )

    raw_tokens = [
        t.strip()
        for t in re.split(r"[\n\r;|]+|,\s*|\s+(?=เปลี่ยน|ดำเนินการ|ตรวจสอบ|รีเซ็ต|ตั้งค่า|ระบบสื่อสาร\s*ONLINE|อุปกรณ์สื่อสาร\s*ปกติ|No\s*AC)", txt)
        if t and t.strip()
    ]

    causes, actions, results = [], [], []
    cause_kw = [
        "ชำรุด", "เสีย", "พัง", "แตก", "ขาด", "เสื่อม", "ค้าง", "หลวม", "ขัดข้อง",
        "ไม่มีไฟ", "NO AC", "OFFLINE", "ALARM", "FAULT", "ไม่ทำงาน", "กระพริบ",
        "ต่ำ", "รั่ว", "ไหม้", "มีปัญหา", "OPEN", "TRIP", "ไม่จ่าย", "ดับ", "ผิดปกติ"
    ]
    action_kw = [
        "เปลี่ยน", "แก้ไข", "รีเซ็ต", "RESET", "RESTART", "ทำความสะอาด", "ขัน",
        "ปรับ", "ตั้งค่า", "จูน", "ตรวจสอบ", "วัด", "เช็ค", "ทดลอง", "ย้ำสาย",
        "ซ่อม", "ตัดต่อ", "ดำเนินการ", "สลับ", "ทริม", "ปลด", "สับ"
    ]
    result_kw = [
        "ใช้งานได้", "ทำงานปกติ", "ปกติ", "ONLINE", "สำเร็จ", "เรียบร้อย",
        "ยังไม่ออนไลน์", "รอ ", "ส่งต่อ", "ใช้ได้แล้ว", "CONNECTING"
    ]

    def _add_unique_clause(target_list, clause):
        c_norm = _normalize_engineering_clause_text(clause)
        if not c_norm:
            return
        c_key = re.sub(r"\s+", "", c_norm.lower())
        for idx_existing, existing in enumerate(target_list):
            ex_key = re.sub(r"\s+", "", existing.lower())
            if c_key == ex_key or c_key in ex_key or ("ไม่มีไฟเลี้ยงac" in c_key and "ไม่มีไฟac" in ex_key):
                return
            if ex_key in c_key:
                target_list[idx_existing] = c_norm
                return
        target_list.append(c_norm)

    for tk in raw_tokens:
        clean_tk = _normalize_engineering_clause_text(tk)
        if not clean_tk:
            continue
        u = clean_tk.upper()

        if "อุปกรณ์สื่อสารทำงานปกติ" in clean_tk and any(k in u for k in ["ไม่มีไฟ", "OPEN", "ชำรุด"]):
            parts_split = clean_tk.split("อุปกรณ์สื่อสารทำงานปกติ")
            if parts_split[0].strip():
                _add_unique_clause(causes, parts_split[0].strip())
            _add_unique_clause(results, "อุปกรณ์สื่อสารทำงานปกติ")
            if len(parts_split) > 1 and parts_split[1].strip():
                rem_part = parts_split[1].strip()
                if any(k in rem_part.upper() for k in result_kw):
                    _add_unique_clause(results, rem_part)
                else:
                    _add_unique_clause(causes, rem_part)
            continue

        if any(u.startswith(k) for k in ["เปลี่ยน", "ดำเนินการ", "จึงดำเนินการ", "ได้ดำเนินการ", "รีเซ็ต", "ตั้งค่า", "ซ่อม", "ย้ำสาย", "ทำความสะอาด", "ปรับตั้ง"]):
            if "ชำรุด" in clean_tk and "ทดแทน" in clean_tk:
                sub_parts = re.split(r"(?=Port\s*LAN|อุปกรณ์\s*DAU|เนื่องจาก)", clean_tk, flags=re.IGNORECASE)
                _add_unique_clause(actions, sub_parts[0])
                for sp in sub_parts[1:]:
                    _add_unique_clause(causes, sp)
            else:
                _add_unique_clause(actions, clean_tk)
        elif any(k in u for k in cause_kw) and not u.startswith("ระบบสื่อสารสามารถเชื่อมต่อ"):
            _add_unique_clause(causes, clean_tk)
        elif any(k in u for k in action_kw):
            _add_unique_clause(actions, clean_tk)
        elif any(k in u for k in result_kw):
            _add_unique_clause(results, clean_tk)
        else:
            if not causes:
                _add_unique_clause(causes, clean_tk)
            else:
                _add_unique_clause(actions, clean_tk)

    if not causes:
        if dept == "PBO":
            causes.append("ชุดควบคุมโทรมาตร (FRTU) และอุปกรณ์ภายในตู้คอนโทรลเกิดความผิดปกติ")
        else:
            causes.append("ระบบสื่อสารข้อมูลเกิดสภาวะสูญเสียการเชื่อมต่อ (Communication Loss) กับศูนย์ควบคุม SCADA")

    if not actions:
        if dept == "PBO":
            actions.append("เข้าตรวจสอบสภาพฮาร์ดแวร์ภายในตู้คอนโทรล ระบบแบตเตอรี่ และแหล่งจ่ายไฟแรงดัน 22kV (VT)")
        else:
            actions.append("เข้าตรวจสอบอุปกรณ์สื่อสาร (Modem/Router) สายสัญญาณสื่อสาร แผงสัญญาณเสาอากาศ และซิมการ์ดเบื้องต้น")

    if not results:
        if "ONLINE" in st_clean.upper():
            if dept == "PBO":
                results.append("ปัจจุบันตู้คอนโทรลและอุปกรณ์สวิตช์เกียร์กลับมาทำงานและออนไลน์ได้ตามปกติ")
            else:
                results.append("ปัจจุบันการเชื่อมต่อโปรโตคอลและระบบสื่อสารข้อมูลกลับมาออนไลน์ได้ตามปกติ")
        else:
            if dept == "PBO":
                results.append(f"สถานะปัจจุบันของตู้คอนโทรลและอุปกรณ์สวิตช์เกียร์ยังคงเป็น {st_clean or 'Offline'}")
            else:
                results.append(f"สถานะการเชื่อมต่อสื่อสารปัจจุบันยังคงเป็น {st_clean or 'Offline'}")

    if "ONLINE" in st_clean.upper() and ("ผบอ" not in resp_clean and "ผอส" not in resp_clean):
        if dept == "PBO":
            next_step_str = "ติดตามเฝ้าระวังเสถียรภาพของระบบไฟเลี้ยงและอุปกรณ์ฮาร์ดแวร์ภายในตู้คอนโทรลอย่างต่อเนื่อง"
        else:
            next_step_str = "ติดตามเฝ้าระวังคุณภาพสัญญาณเครือข่ายและเสถียรภาพของการเชื่อมต่อโปรโตคอลกับศูนย์ควบคุม SCADA อย่างต่อเนื่อง"
    else:
        if dept == "PBO":
            next_step_str = "เข้าตรวจสอบเชิงลึกเพื่อซ่อมแซมหรือเปลี่ยนอุปกรณ์ฮาร์ดแวร์ภายในตู้คอนโทรลและระบบไฟเลี้ยงที่ชำรุดให้กลับมาทำงานได้ตามมาตรฐาน"
        else:
            next_step_str = "เข้าตรวจสอบแก้ไขอุปกรณ์สื่อสาร สายสัญญาณ และตั้งค่าพารามิเตอร์โปรโตคอลเชื่อมต่อกับตู้ FRTU เพื่อให้ระบบกลับมาออนไลน์ได้ตามปกติ"

    cause_str = " และ".join(causes)
    cause_str = re.sub(r"^(เนื่องจาก|สาเหตุเกิดจาก|เพราะ)\s*", "", cause_str).strip()

    action_str = " และ".join(actions)
    action_str = re.sub(r"^(จึงได้ดำเนินการ|จึงดำเนินการ|ได้ดำเนินการ|ดำเนินการ|ทำการ)\s*", "", action_str).strip()

    result_str = " และ".join(results)
    result_str = re.sub(r"^(ส่งผลให้|ผลลัพธ์คือ|ทำให้)\s*", "", result_str).strip()

    narrative = (
        f"เนื่องจาก{cause_str} จึงได้{action_str} ส่งผลให้{result_str} "
        f"ทั้งนี้ ในระยะต่อไปทางแผนก {dept_label} จะดำเนินการ{next_step_str}"
    )
    narrative = re.sub(r"[✅❌•\-]+", " ", narrative)
    narrative = re.sub(r"\s+", " ", narrative).strip()
    return narrative


def _build_deterministic_executive_summary(rows):
    """สร้างตารางและบทสรุปแยกหมวดหมู่จากข้อมูลจริง"""
    total = len(rows)
    online_cnt = sum(1 for r in rows if "ONLINE" in str(r.get("status", "")).upper())
    connecting_cnt = sum(1 for r in rows if "CONNECTING" in str(r.get("status", "")).upper())
    offline_cnt = sum(1 for r in rows if "OFFLINE" in str(r.get("status", "")).upper())
    other_cnt = max(0, total - online_cnt - connecting_cnt - offline_cnt)

    bo_cnt = sum(1 for r in rows if "ผบอ" in str(r.get("resp", "")))
    po_cnt = sum(1 for r in rows if "ผอส" in str(r.get("resp", "")))
    done_cnt = sum(1 for r in rows if str(r.get("resp", "-")).strip() in ("-", "", "ไม่มีค่า"))

    comm_issue_cnt = 0
    frtu_issue_cnt = 0
    power_issue_cnt = 0
    for r in rows:
        d_up = str(r.get("detail", "")).upper()
        if any(k in d_up for k in ["สื่อสาร", "MODEM", "SIM", "WRL", "OPTIC", "สัญญาณ", "ROUTER", "DAU", "LAN"]):
            comm_issue_cnt += 1
        if any(k in d_up for k in ["FRTU", "RTU", "การ์ด", "CONTROL", "BOARD", "โมดูล", "AVR", "RECLOSER"]):
            frtu_issue_cnt += 1
        if any(k in d_up for k in ["แบต", "BATTERY", "ฟิวส์", "FUSE", "AC", "DC", "ไฟเลี้ยง", "VT"]):
            power_issue_cnt += 1

    categories = [
        {
            "category": "1. หมวดสถานะอุปกรณ์หลังการเข้าตรวจสอบ (Post-Inspection Status)",
            "items": [
                f"อุปกรณ์กลับมา Online ปกติ: {online_cnt} ชุด ({(online_cnt/total*100) if total else 0:.1f}%)",
                f"สถานะ Connecting (รอจูนสัญญาณ/รอตรวจสอบร่วม): {connecting_cnt} ชุด ({(connecting_cnt/total*100) if total else 0:.1f}%)",
                f"สถานะ Offline (ยังไม่ออนไลน์): {offline_cnt} ชุด ({(offline_cnt/total*100) if total else 0:.1f}%)",
                f"สถานะอื่นๆ / Initializing / Telemetry: {other_cnt} ชุด",
            ],
        },
        {
            "category": "2. หมวดสาเหตุและอาการชำรุดที่ตรวจพบ (Fault Categorization)",
            "items": [
                f"ระบบสื่อสารและโครงข่าย (DAU / Cellular Router / Modem / SIM / WRL / สายสัญญาณ): พบเกี่ยวข้อง {comm_issue_cnt} รายการ",
                f"อุปกรณ์ชุดควบคุมหลัก (FRTU / Recloser / AVR / Control Board): พบเกี่ยวข้อง {frtu_issue_cnt} รายการ",
                f"ระบบแหล่งจ่ายไฟและหม้อแปลงแรงดัน (VT 22kV / Battery / Converter / Fuse): พบเกี่ยวข้อง {power_issue_cnt} รายการ",
            ],
        },
        {
            "category": "3. หมวดหน่วยงานผู้รับผิดชอบดำเนินการต่อ (Action Assignment)",
            "items": [
                f"รอ แผนก ผบอ. เข้าดำเนินการแก้ไข (ตู้คอนโทรล/FRTU/VT 22kV/สวิตช์เกียร์): {bo_cnt} รายการ",
                f"รอ แผนก ผอส. เข้าดำเนินการแก้ไข (ระบบสื่อสาร/Modem/Router/เสาอากาศ): {po_cnt} รายการ",
                f"ดำเนินการแก้ไขเสร็จสิ้น/ไม่ต้องส่งต่อ: {done_cnt} รายการ",
            ],
        },
    ]

    summary_md = (
        f"เนื่องจากผลการตรวจวิเคราะห์ข้อมูลรายงานการซ่อมบำรุงอุปกรณ์ในระบบ SCADA จำนวนทั้งสิ้น {total} รายการ "
        f"พบสาเหตุความผิดปกติแบ่งเป็นปัญหาด้านระบบสื่อสารข้อมูลและโปรโตคอลในความรับผิดชอบของแผนก ผอส. จำนวน {comm_issue_cnt} รายการ "
        f"ปัญหาด้านชุดควบคุมโทรมาตร (FRTU) บอร์ดคอนโทรล และอุปกรณ์สวิตช์เกียร์จำนวน {frtu_issue_cnt} รายการ "
        f"ตลอดจนปัญหาด้านหม้อแปลงแรงดัน (VT 22kV) คอนเวอร์เตอร์ และระบบแบตเตอรี่ในความรับผิดชอบของแผนก ผบอ. จำนวน {power_issue_cnt} รายการ "
        f"จึงได้เข้าตรวจสอบแก้ไขเบื้องต้น เปลี่ยนอุปกรณ์ฮาร์ดแวร์และอุปกรณ์สื่อสารที่ชำรุด พร้อมปรับตั้งค่าพารามิเตอร์ในระบบใหม่ "
        f"ส่งผลให้ปัจจุบันตู้คอนโทรลและระบบสื่อสารสามารถกลับมาเชื่อมต่อออนไลน์ได้ตามปกติจำนวน {online_cnt} ชุด คิดเป็นร้อยละ {(online_cnt/total*100) if total else 0:.1f} "
        f"อยู่ในสถานะกำลังเชื่อมต่อ (Connecting) จำนวน {connecting_cnt} ชุด และยังคงมีสถานะออฟไลน์ (Offline) จำนวน {offline_cnt} ชุด "
        f"ทั้งนี้ ในระยะต่อไปทางแผนก ผบอ. จะเข้าดำเนินการซ่อมแซมเปลี่ยนฮาร์ดแวร์ตู้คอนโทรลและระบบไฟเลี้ยงจำนวน {bo_cnt} รายการ "
        f"ควบคู่กับทางแผนก ผอส. ที่จะเข้าดำเนินการแก้ไขระบบสื่อสารและสายสัญญาณอีกจำนวน {po_cnt} รายการเพื่อให้ระบบกลับมาพร้อมใช้งานอย่างสมบูรณ์"
    )
    return {
        "summary_markdown": summary_md,
        "categories": categories,
        "metrics": {
            "total": total,
            "online": online_cnt,
            "connecting": connecting_cnt,
            "offline": offline_cnt,
            "other": other_cnt,
            "wait_bo": bo_cnt,
            "wait_po": po_cnt,
            "completed": done_cnt,
            "comm_issues": comm_issue_cnt,
            "frtu_issues": frtu_issue_cnt,
            "power_issues": power_issue_cnt,
        },
    }


def summarize_inspection_report_with_gemini(rows, preferred_model="gemini-3.5-pro-exp", api_key=None, custom_prompt=None):
    """
    ฟังก์ชันหลักสำหรับสรุปและเรียบเรียงรายงานการตรวจเช็คอุปกรณ์ด้วย Google Gemini API:
    แยกระบบ System Prompt เป็น 2 ส่วนหลักตามแผนก:
    - แผนก ผบอ. ใช้ SYSTEM_PROMPT_PBO (Senior Control System & Electrical Engineer)
    - แผนก ผอส. ใช้ SYSTEM_PROMPT_POS (Senior SCADA & Communication Engineer)
    """
    if not rows:
        raise ValueError("ไม่พบรายการข้อมูลสำหรับสรุปรายงาน")

    base_exec_info = _build_deterministic_executive_summary(rows)
    rewritten_rows = [dict(r) for r in rows]
    for r in rewritten_rows:
        r["dept_code"] = resolve_department_for_row(r.get("resp", ""), r.get("detail", ""))
        r["ai_detail"] = _deterministic_categorize_row_detail(
            r.get("detail", ""), r.get("resp", ""), r.get("status", "")
        )

    model_cfg = GEMINI_FREE_TIER_MODELS.get(
        preferred_model, GEMINI_FREE_TIER_MODELS["gemini-3.5-pro-exp"]
    )
    max_chars = model_cfg.get("max_chunk_chars", 4500)
    max_rows = model_cfg.get("max_chunk_rows", 22)

    pbo_rows = [r for r in rewritten_rows if r.get("dept_code") == "PBO"]
    pos_rows = [r for r in rewritten_rows if r.get("dept_code") != "PBO"]

    dept_batches = []
    if pbo_rows:
        for ch in chunk_inspection_rows(pbo_rows, max_chars=max_chars, max_rows=max_rows):
            dept_batches.append(("PBO", ch))
    if pos_rows:
        for ch in chunk_inspection_rows(pos_rows, max_chars=max_chars, max_rows=max_rows):
            dept_batches.append(("POS", ch))

    chunk_summaries = []
    used_models_log = []
    fallback_traces = []
    ai_warning = ""

    for batch_idx, (dept_code, chunk) in enumerate(dept_batches):
        dept_name = "ผบอ." if dept_code == "PBO" else "ผอส."
        active_sys_prompt = get_system_prompt_for_department(dept_code, custom_prompt=custom_prompt)

        lines_payload = []
        for item_i, r in enumerate(chunk):
            lines_payload.append(
                f"[{item_i}] Site: {r.get('site','-')} | รหัสสั่งการ: {r.get('cmd','-')} | "
                f"สถานที่: {r.get('loc','-')} | รายละเอียดเดิม: {r.get('detail','-')} | "
                f"ผู้รับผิดชอบต่อ: {r.get('resp','-')} | สถานะสุดท้าย: {r.get('status','-')}"
            )
        chunk_text = "\n".join(lines_payload)

        chunk_prompt = (
            "โปรดวิเคราะห์ข้อมูลดิบจากรายงานการซ่อมบำรุงในแต่ละรายการต่อไปนี้ และเรียบเรียงเป็น 'รายงานเชิงวิศวกรรม' "
            f"ตามโครงสร้างการเขียนบังคับของแผนก {dept_name} (Flowing Narrative 1 ย่อหน้า ครอบคลุม 4 องค์ประกอบตามลำดับ: "
            f"'เนื่องจาก... (สาเหตุ) จึงได้ดำเนินการ... (การดำเนินการ) ส่งผลให้... (ผลลัพธ์) ทั้งนี้ ในระยะต่อไปทางแผนก {dept_name} จะดำเนินการ... (การดำเนินการต่อไป)') "
            "ห้ามใช้สัญลักษณ์พิเศษ (เช่น ✅, ❌, •, -) หรือขึ้นบรรทัดใหม่แบบหัวข้อย่อยเด็ดขาด\n"
            "ตอบกลับเป็น JSON เท่านั้น ตามโครงสร้างนี้:\n"
            "{\n"
            f'  "rows": [ {{"idx": 0, "clean_detail": "เนื่องจาก... จึงได้ดำเนินการ... ส่งผลให้... ทั้งนี้ ในระยะต่อไปทางแผนก {dept_name} จะดำเนินการ..."}} ],\n'
            f'  "chunk_summary": "สรุปภาพรวมเชิงวิศวกรรมของแผนก {dept_name} แบบร้อยเรียงเรื่องราว 1 ย่อหน้า"\n'
            "}\n\n"
            f"ข้อมูลชุดที่ {batch_idx + 1}/{len(dept_batches)} (แผนก {dept_name}):\n{chunk_text}"
        )

        try:
            res = call_gemini_with_fallback(
                chunk_prompt,
                preferred_model=preferred_model,
                api_key=api_key,
                system_instruction=active_sys_prompt,
            )
        except RuntimeError as err_rt:
            if "TOKEN_LIMIT_EXCEEDED" in str(err_rt) and len(chunk) > 1:
                mid = max(1, len(chunk) // 2)
                sub_chunks = [chunk[:mid], chunk[mid:]]
                try:
                    for sc in sub_chunks:
                        sc_text = "\n".join(
                            f"[{idx_s}] Site: {x.get('site','-')} | รายละเอียด: {x.get('detail','-')} | ผู้รับผิดชอบ: {x.get('resp','-')} | สถานะสุดท้าย: {x.get('status','-')}"
                            for idx_s, x in enumerate(sc)
                        )
                        sc_prompt = (
                            f"โปรดเรียบเรียงรายงานเชิงวิศวกรรมของแผนก {dept_name} ต่อไปนี้เป็น JSON:\n"
                            f'{{"rows": [{{"idx": 0, "clean_detail": "เนื่องจาก... จึงได้ดำเนินการ... ส่งผลให้... ทั้งนี้ ในระยะต่อไปทางแผนก {dept_name} จะดำเนินการ..."}}], "chunk_summary": "..."}}\n\n'
                            + sc_text
                        )
                        res = call_gemini_with_fallback(
                            sc_prompt, preferred_model=preferred_model, api_key=api_key, system_instruction=active_sys_prompt
                        )
                        used_models_log.append(res["used_model"])
                    continue
                except Exception as sub_err:
                    ai_warning = str(sub_err)
                    break
            else:
                ai_warning = str(err_rt)
                break

        used_models_log.append(res["used_model"])
        fallback_traces.extend(res.get("fallback_trace", []))
        raw_reply = res.get("text", "")

        json_match = re.search(r"\{.*\}", raw_reply, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(0))
                for r_item in parsed.get("rows", []):
                    r_idx = r_item.get("idx")
                    c_det = str(r_item.get("clean_detail") or "").strip()
                    if isinstance(r_idx, int) and 0 <= r_idx < len(chunk) and c_det:
                        c_det = re.sub(r"[✅❌•\-]+", " ", c_det)
                        c_det = re.sub(r"\s+", " ", c_det).strip()
                        chunk[r_idx]["ai_detail"] = c_det
                if parsed.get("chunk_summary"):
                    chunk_summaries.append(str(parsed["chunk_summary"]).strip())
            except Exception:
                chunk_summaries.append(raw_reply[:400])
        elif raw_reply:
            chunk_summaries.append(raw_reply[:400])

    final_summary_md = base_exec_info["summary_markdown"]
    if chunk_summaries and not ai_warning:
        primary_dept = "PBO" if len(pbo_rows) > len(pos_rows) else "POS"
        synth_sys_prompt = get_system_prompt_for_department(primary_dept, custom_prompt=custom_prompt)
        synth_prompt = (
            "โปรดสังเคราะห์ข้อมูลสรุปย่อยต่อไปนี้เป็น 'รายงานเชิงวิศวกรรม' สำหรับผู้บริหาร 1 ย่อหน้าแบบต่อเนื่อง (Flowing Narrative) "
            "โดยครอบคลุม 4 องค์ประกอบหลักตามลำดับ (สาเหตุ, การดำเนินการ, ผลลัพธ์, และการดำเนินการต่อไปของแผนก ผบอ. และ แผนก ผอส.) "
            "ห้ามใช้สัญลักษณ์ (เช่น ✅, ❌, •, -) หรือการขึ้นบรรทัดใหม่แบบหัวข้อย่อยเด็ดขาด:\n\n"
            f"สถิติภาพรวม: ทั้งหมด {base_exec_info['metrics']['total']} รายการ, "
            f"Online {base_exec_info['metrics']['online']} ชุด, "
            f"Connecting {base_exec_info['metrics']['connecting']} ชุด, "
            f"Offline {base_exec_info['metrics']['offline']} ชุด, "
            f"รอ ผบอ. {base_exec_info['metrics']['wait_bo']} ชุด, "
            f"รอ ผอส. {base_exec_info['metrics']['wait_po']} ชุด\n\n"
            "ข้อมูลสรุปรายส่วน:\n" + "\n".join(chunk_summaries)
        )
        try:
            synth_res = call_gemini_with_fallback(
                synth_prompt, preferred_model=preferred_model, api_key=api_key, system_instruction=synth_sys_prompt
            )
            if synth_res.get("text"):
                clean_synth = re.sub(r"[✅❌•]+", " ", synth_res["text"].strip())
                final_summary_md = re.sub(r"\s+", " ", clean_synth).strip()
            used_models_log.append(synth_res["used_model"])
            fallback_traces.extend(synth_res.get("fallback_trace", []))
        except Exception as synth_err:
            ai_warning = str(synth_err)

    for r in rewritten_rows:
        if r.get("ai_detail"):
            r["detail"] = r["ai_detail"]

    return {
        "success": len(used_models_log) > 0,
        "warning": ai_warning,
        "used_model": used_models_log[-1] if used_models_log else preferred_model,
        "chunk_count": len(dept_batches),
        "rows": rewritten_rows,
        "summary_markdown": final_summary_md,
        "categories": base_exec_info["categories"],
        "metrics": base_exec_info["metrics"],
        "fallback_trace": fallback_traces,
        "session_state": sync_and_get_session_state(),
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

def _is_meaningful_cell(val):
    """ตรวจสอบว่าค่าในเซลล์เป็นข้อมูลจริง (ไม่ใช่ค่าว่าง หรือ Error จากสูตรใน Google Sheets เช่น #N/A, #REF!)"""
    s = _s(val)
    if not s:
        return False
    if s.upper() in ("#N/A", "#REF!", "#VALUE!", "#NAME?", "#ERROR!", "N/A", "NAN", "NONE", "NAT"):
        return False
    return True


def _normalize_date_display_ddmmyyyy(val):
    """แปลงวันที่รูปแบบ YYYY-MM-DD 00:00:00 หรือ Date(...) ให้เป็น dd/MM/yyyy ตามรูปแบบในชีต"""
    s = _s(val)
    if not s:
        return ""
    m_iso = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$', s)
    if m_iso:
        y, m, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        if y > 2400:
            y -= 543
        return f"{d:02d}/{m:02d}/{y:04d}"
    return s


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

    # กรณีอ่านแบบ header=None แถวแรก (หรือแถวที่ 0..4) จะเป็นชื่อคอลัมน์จริงของ ชีต1 (21 คอลัมน์)
    for idx in range(min(5, len(df_raw))):
        row_vals = [_s(v) for v in df_raw.iloc[idx].values]
        row_lower = [v.lower() for v in row_vals]
        if any('site id' in v for v in row_lower) and not any('ne2-' in v for v in row_lower):
            ref_cols = SHEET1_RAW_COLS if len(row_vals) <= len(SHEET1_RAW_COLS) else SHEET1_CANONICAL_COLS
            new_cols = []
            for col_i, val in enumerate(row_vals):
                if val and not val.lower().startswith('unnamed'):
                    new_cols.append(val)
                elif col_i < len(ref_cols):
                    new_cols.append(ref_cols[col_i])
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

        ref_cols = SHEET1_RAW_COLS if len(cand_headers) <= len(SHEET1_RAW_COLS) else SHEET1_CANONICAL_COLS
        new_cols = [
            ref_cols[col_i] if col_i < len(ref_cols) else c_str
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
    if not s or not _is_meaningful_cell(s):
        return ""
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except Exception:
        pass
    return re.sub(r'\.0$', '', s)

def parse_rtu_alias(alias_str):
    """
    แยกค่า WRL Master และ ประเภทผลิตภัณฑ์ (FRTU Model) จากคอลัมน์ Alias ในชีต RTU Summary (gid=747048278)
    ตามสูตรในชีตจริง:
      - FRTU Model (Col R): =IFERROR(INDEX(SPLIT(Alias, "_"), 1, 3), "")
      - WRL Master (Col S): =IFERROR(INDEX(SPLIT(Alias, "_"), 1, 2), "")
    """
    s = _s(alias_str)
    if not s or not _is_meaningful_cell(s) or '_' not in s:
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
    return "", ""

def _append_cache_buster(url):
    """เพิ่มพารามิเตอร์ป้องกัน HTTP Cache ค้างเมื่อดึงข้อมูลจาก Google Sheets"""
    ts = int(time.time())
    sep = '&' if '?' in url else '?'
    return f"{url}{sep}_cb={ts}"

def fetch_remote_dataframe(urls, validator_fn, normalizer_fn=None, sheet_name=None):
    opener = _build_url_opener()
    for url in urls:
        try:
            req_url = _append_cache_buster(url)
            req = urllib.request.Request(
                req_url,
                headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Cache-Control': 'no-cache, no-store, must-revalidate',
                    'Pragma': 'no-cache',
                },
            )
            with opener.open(req, timeout=20) as resp:
                raw_bytes = resp.read()
            if b'setResponse(' in raw_bytes[:300] or 'tqx=out:json' in url:
                df_raw = parse_gviz_json_bytes(raw_bytes)
                df_cand = normalizer_fn(df_raw) if normalizer_fn else df_raw
                if validator_fn(df_cand):
                    return df_cand
            else:
                # ลองอ่านแบบมี Header แถวแรกก่อน เพื่อรักษาชื่อคอลัมน์ตรงตามชีตจริง
                try:
                    df_std = pd.read_csv(io.BytesIO(raw_bytes), dtype=str)
                    df_std_norm = normalizer_fn(df_std) if normalizer_fn else df_std
                    if validator_fn(df_std_norm):
                        return df_std_norm
                except Exception:
                    pass
                df_raw = pd.read_csv(io.BytesIO(raw_bytes), header=None, dtype=str)
                df_cand = normalizer_fn(df_raw) if normalizer_fn else df_raw
                if validator_fn(df_cand):
                    return df_cand
        except Exception:
            continue

    if sheet_name:
        # Fallback 1: ตรวจสอบผ่าน gspread Service Account หากมีไฟล์ JSON ในโปรเจกต์
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
                            df_cand = normalizer_fn(df_cand) if normalizer_fn else df_cand
                            if validator_fn(df_cand):
                                return df_cand
                except Exception:
                    continue
        except Exception:
            pass

        # Fallback 2: หากมีไฟล์ Excel FRTU_NE2_2026.xlsx ที่ดาวน์โหลดไว้ในโฟลเดอร์โปรเจกต์
        for cand_xlsx in [
            os.path.join(BASE_DIR, 'FRTU_NE2_2026.xlsx'),
            os.path.join(BASE_DIR, 'downloads', 'FRTU_NE2_2026.xlsx'),
        ]:
            if os.path.exists(cand_xlsx):
                try:
                    df_xl = pd.read_excel(cand_xlsx, sheet_name=sheet_name, dtype=str)
                    df_xl = normalizer_fn(df_xl) if normalizer_fn else df_xl
                    if validator_fn(df_xl):
                        return df_xl
                except Exception:
                    continue
    return None


def merge_sheet1_remote_with_base(df_remote, df_base):
    """
    ผสานข้อมูลจาก ชีต1 ล่าสุดบน Google Sheets (df_remote) เข้ากับฐานข้อมูลพิกัดพื้นฐาน (df_base):
    - ยึดข้อมูลจาก df_remote (ชีต1 ล่าสุด) เป็นหลักทุกคอลัมน์ และเพิ่มแถวอุปกรณ์ใหม่ทั้งหมดที่มีใน ชีต1
    - ใช้ df_base เป็นแหล่งสำรองเฉพาะเซลล์ที่ใน ชีต1 ว่างเปล่าหรือติด #N/A (เช่น พิกัด LAT, LONG ของบางสถานี)
    """
    if df_remote is None or len(df_remote) == 0:
        return df_base
    if df_base is None or len(df_base) == 0:
        return df_remote

    base_site_col = get_actual_col_name(df_base.columns, ["site id"]) or 'Site ID'
    base_cmd_col = get_actual_col_name(df_base.columns, ["รหัสสั่งการ"]) or 'รหัสสั่งการ'

    base_by_sid = {}
    base_by_cmd = {}
    for br in df_base.to_dict('records'):
        bsid = _s(br.get(base_site_col)).upper()
        bcmd = _s(br.get(base_cmd_col)).upper()
        if bsid:
            base_by_sid[bsid] = br
        if bcmd:
            base_by_cmd[bcmd] = br

    rem_cols = list(df_remote.columns)
    rem_col_map = {
        'รหัสสั่งการ': get_actual_col_name(rem_cols, ["รหัสสั่งการ"]),
        'Site ID': get_actual_col_name(rem_cols, ["site id"]),
        'สถานที่': get_actual_col_name(rem_cols, ["สถานที่"]),
        'State SCADA': get_actual_col_name(rem_cols, ["state scada"], exclude=["หลัง"]),
        'State SCADA\n (หลังตรวจสอบ)': get_actual_col_name(rem_cols, ["state scada", "หลัง"]),
        'ชนิดอุปกรณ์': get_actual_col_name(rem_cols, ["ชนิดอุปกรณ์"]),
        'รายละเอียดการแก้ไขของ ผอส.ฉ.2': get_actual_col_name(rem_cols, ["รายละเอียด", "การแก้ไข"], exclude=["เข้า"]),
        'รายละเอียดการเข้าแก้ไข (กรณีอุปกรณ์สื่อสารชำรุด)': get_actual_col_name(rem_cols, ["รายละเอียด", "การเข้าแก้ไข"]),
        'LAT, LONG': get_actual_col_name(rem_cols, ["lat", "long"]),
        'รอ ผอส. เข้าแก้ไข (ยังไม่ออนไลน์)': get_actual_col_name(rem_cols, ["รอ ผอส"]),
        'รอ ผบอ. เข้าแก้ไข (ยังไม่ออนไลน์)': get_actual_col_name(rem_cols, ["รอ ผบอ"]),
        'การไฟฟ้า': get_actual_col_name(rem_cols, ["การไฟฟ้า"]),
        'วันที่เข้าตรวจสอบ': get_actual_col_name(rem_cols, ["วันที่เข้าตรวจสอบ"]),
        'สถานะการเข้าแก้ไขและออนไลน์ โดย ผบอ.': get_actual_col_name(rem_cols, ["โดย ผบอ"]),
        'IP': get_actual_col_name(rem_cols, ["ip"], exclude=["site", "script", "description"]),
        'Commu.': get_actual_col_name(rem_cols, ["commu"]),
        'Item': get_actual_col_name(rem_cols, ["item"], exclude=["ประเภทย่อย"]),
        'DNP': get_actual_col_name(rem_cols, ["dnp"]),
        'FRTU Model': get_actual_col_name(rem_cols, ["frtu model"]) or get_actual_col_name(rem_cols, ["ชนิดผลิตภัณฑ์ของ frtu"]),
        'WRL Master': get_actual_col_name(rem_cols, ["wrl master"]) or get_actual_col_name(rem_cols, ["แม่ข่ายสื่อสาร"]),
        'ซ้ำ': get_actual_col_name(rem_cols, ["ซ้ำ"]),
        'เช็ครหัส': get_actual_col_name(rem_cols, ["เช็ครหัส"]),
    }

    merged_records = []
    seen_sids = set()

    for rr in df_remote.to_dict('records'):
        sid_col = rem_col_map['Site ID']
        cmd_col = rem_col_map['รหัสสั่งการ']
        sid = _s(rr.get(sid_col) if sid_col else '')
        if not _is_meaningful_cell(sid):
            continue
        sid_u = sid.upper()
        seen_sids.add(sid_u)

        cmd_val = _s(rr.get(cmd_col) if cmd_col else '')
        cmd_u = cmd_val.upper()
        base_row = base_by_sid.get(sid_u) or (base_by_cmd.get(cmd_u) if cmd_u else None) or {}

        new_row = {c: _s(base_row.get(c)) for c in SHEET1_CANONICAL_COLS}
        new_row['Site ID'] = sid

        for canon_col, actual_rem_col in rem_col_map.items():
            if canon_col == 'Site ID' or not actual_rem_col:
                continue
            rem_val = _s(rr.get(actual_rem_col))
            if canon_col == 'สถานะการเข้าแก้ไขและออนไลน์ โดย ผบอ.':
                rem_val = _normalize_date_display_ddmmyyyy(rem_val)
            elif canon_col == 'DNP':
                rem_val = clean_dnp_str(rem_val)

            if _is_meaningful_cell(rem_val):
                # กรณีการไฟฟ้าเป็น Unknown ในชีต แต่ในฐานข้อมูลเดิมมีการไฟฟ้าที่ระบุชัดเจน ให้ใช้ค่าที่ระบุชัดเจน
                if canon_col == 'การไฟฟ้า' and rem_val.upper() == 'UNKNOWN' and _is_meaningful_cell(new_row.get(canon_col)) and new_row.get(canon_col).upper() != 'UNKNOWN':
                    continue
                new_row[canon_col] = rem_val
            elif canon_col in (
                'รายละเอียดการแก้ไขของ ผอส.ฉ.2',
                'รายละเอียดการเข้าแก้ไข (กรณีอุปกรณ์สื่อสารชำรุด)',
                'รอ ผอส. เข้าแก้ไข (ยังไม่ออนไลน์)',
                'รอ ผบอ. เข้าแก้ไข (ยังไม่ออนไลน์)',
                'วันที่เข้าตรวจสอบ',
                'สถานะการเข้าแก้ไขและออนไลน์ โดย ผบอ.',
            ):
                # คอลัมน์สถานะการตรวจสอบต้องสะท้อนค่าปัจจุบันจาก ชีต1 (หากเคลียร์เป็นค่าว่าง ต้องว่างตามชีต)
                new_row[canon_col] = ""

        if not _is_meaningful_cell(new_row.get('ซ้ำ')):
            new_row['ซ้ำ'] = 'ปกติ'
        if not _is_meaningful_cell(new_row.get('เช็ครหัส')):
            new_row['เช็ครหัส'] = 'ตรงกัน'

        new_row['ชนิดผลิตภัณฑ์ของ FRTU'] = _s(new_row.get('FRTU Model'))
        new_row['ชนิดของอุปกรณ์สื่อสาร'] = _s(new_row.get('Commu.'))
        new_row['ประเภทย่อยอุปกรณ์สื่อสาร (Item)'] = _s(new_row.get('Item'))
        new_row['กลุ่มอุปกรณ์สื่อสารหลัก'] = classify_comm_device_group(new_row.get('ชนิดของอุปกรณ์สื่อสาร'), new_row.get('ประเภทย่อยอุปกรณ์สื่อสาร (Item)'))
        new_row['แม่ข่ายสื่อสาร (WRL Master / Base)'] = _s(new_row.get('WRL Master'))

        merged_records.append(new_row)

    return pd.DataFrame(merged_records, columns=SHEET1_CANONICAL_COLS)


def enrich_sheet1_with_update_and_check(df_s1, df_up, df_chk, df_rtu=None):
    """ผสานข้อมูลทุกชีตโดยใช้ Site ID เป็นเกณฑ์หลักตามสูตรใน Google Sheets จริง:
    - Update State (gid=0): State SCADA, รหัสสั่งการ, ชนิดอุปกรณ์, การไฟฟ้า, สถานที่ (รวมถึงเพิ่ม Site ID ใหม่ใน Update State อัตโนมัติ)
    - RTU Summary (gid=747048278): IP, DNP (Address), Commu., Item, ประเภทผลิตภัณฑ์ (FRTU Model), เชื่อมกับ WRL Master
    - Data_Check (gid=2024067511): ข้อมูลตรวจสอบ/แก้ไขหน้างานจาก Google Form (เฉพาะรายการล่าสุด Latest=1 ของแต่ละ Site ID)
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
    item_col = get_actual_col_name(cols, ["item"], exclude=["ประเภทย่อย"]) or 'Item'
    frtu_model_col = get_actual_col_name(cols, ["frtu model"]) or 'FRTU Model'
    wrl_master_col = get_actual_col_name(cols, ["wrl master"]) or 'WRL Master'

    frtu_prod_col = 'ชนิดผลิตภัณฑ์ของ FRTU'
    comm_type_col = 'ชนิดของอุปกรณ์สื่อสาร'
    comm_group_col = 'กลุ่มอุปกรณ์สื่อสารหลัก'
    comm_item_col = 'ประเภทย่อยอุปกรณ์สื่อสาร (Item)'
    comm_ref_col = 'แม่ข่ายสื่อสาร (WRL Master / Base)'

    for extra_c in [ip_col, dnp_col, commu_col, item_col, frtu_model_col, wrl_master_col, frtu_prod_col, comm_type_col, comm_group_col, comm_item_col, comm_ref_col]:
        if extra_c not in cols:
            cols.append(extra_c)

    re_online_comm = re.compile(r'✅|ไม่มีอุปกรณ์สื่อสารชำรุด')
    re_strip_marks = re.compile(r'[✅❌]')
    re_good_clause = re.compile(r'✅[^❌]*')

    up_map = {}
    up_ordered_sids = []
    if df_up is not None and len(df_up) > 0:
        up_site_col = get_actual_col_name(df_up.columns, ["site id"]) or df_up.columns[0]
        for r in df_up.to_dict('records'):
            sid = _s(r.get(up_site_col)).upper()
            if _is_meaningful_cell(sid):
                up_map[sid] = r
                if sid not in up_ordered_sids:
                    up_ordered_sids.append(sid)

    rtu_map = {}
    if df_rtu is not None and len(df_rtu) > 0:
        rtu_site_col = (
            get_actual_col_name(df_rtu.columns, ["name"], exclude=["dau"])
            or get_actual_col_name(df_rtu.columns, ["site id"])
            or df_rtu.columns[0]
        )
        for r in df_rtu.to_dict('records'):
            sid = _s(r.get(rtu_site_col)).upper()
            if _is_meaningful_cell(sid):
                rtu_map[sid] = r

    chk_map = {}
    if df_chk is not None and len(df_chk) > 0:
        chk_cols = list(df_chk.columns)
        chk_site_col = get_actual_col_name(chk_cols, ["site id"]) or (chk_cols[2] if len(chk_cols) > 2 else chk_cols[0])
        chk_latest_col = get_actual_col_name(chk_cols, ["latest"])
        sid_idx = chk_cols.index(chk_site_col)
        latest_idx = chk_cols.index(chk_latest_col) if chk_latest_col in chk_cols else (14 if len(chk_cols) > 14 else -1)

        for row_vals in df_chk.itertuples(index=False, name=None):
            sid = _s(row_vals[sid_idx] if sid_idx < len(row_vals) else '').upper()
            if not _is_meaningful_cell(sid):
                continue
            # หากมีคอลัมน์ Latest ให้ยึดแถวที่ Latest == 1 เป็นหลัก (หรือแถวล่าสุดตามลำดับ)
            is_latest = True
            if 0 <= latest_idx < len(row_vals):
                lv = _s(row_vals[latest_idx])
                if lv in ('0', '0.0', 'false', 'False'):
                    is_latest = False
            if is_latest or sid not in chk_map:
                chk_map[sid] = row_vals

    records = df_s1.to_dict('records')
    existing_sids = {_s(r.get(site_col)).upper() for r in records if _is_meaningful_cell(r.get(site_col))}

    # เพิ่มอุปกรณ์ใหม่ที่มีใน Update State (gid=0) แต่ยังไม่มีในตารางหลักโดยอัตโนมัติ
    for new_sid in up_ordered_sids:
        if new_sid not in existing_sids:
            ur = up_map[new_sid]
            new_r = {c: '' for c in cols}
            new_r[site_col] = _s(ur.get('Site ID')) or new_sid
            new_r[cmd_col] = _s(ur.get('รหัสสั่งการ')).replace('-OLTC', '')
            new_r[type_col] = _s(ur.get('ชนิดอุปกรณ์'))
            new_r[state_col] = _s(ur.get('State SCADA'))
            new_r[pea_col] = _s(ur.get('การไฟฟ้า'))
            new_r[loc_col] = _s(ur.get('สถานที่'))
            new_r['ซ้ำ'] = 'ปกติ'
            new_r['เช็ครหัส'] = 'ตรงกัน'
            records.append(new_r)
            existing_sids.add(new_sid)

    today_ddmmyyyy = datetime.now(timezone(timedelta(hours=7))).strftime("%d/%m/%Y")

    for row in records:
        sid = _s(row.get(site_col)).upper()
        if not sid:
            continue

        # 1. อัปเดตจาก Update State (gid=0) โดยยึด Site ID เป็นเกณฑ์หลักตามสูตร VLOOKUP ในชีต
        if sid in up_map:
            ur = up_map[sid]
            u_state = _s(ur.get('State SCADA'))
            if _is_meaningful_cell(u_state):
                row[state_col] = u_state

            u_cmd = _s(ur.get('รหัสสั่งการ')).replace('-OLTC', '')
            if _is_meaningful_cell(u_cmd):
                row[cmd_col] = u_cmd

            u_type = _s(ur.get('ชนิดอุปกรณ์'))
            if _is_meaningful_cell(u_type):
                row[type_col] = u_type

            u_pea = _s(ur.get('การไฟฟ้า'))
            cur_pea = _s(row.get(pea_col))
            if _is_meaningful_cell(u_pea):
                if u_pea.upper() != 'UNKNOWN' or not _is_meaningful_cell(cur_pea):
                    row[pea_col] = u_pea

            u_loc = _s(ur.get('สถานที่'))
            cur_loc = _s(row.get(loc_col))
            if (not _is_meaningful_cell(cur_loc) or cur_loc == '-') and _is_meaningful_cell(u_loc) and u_loc != '-':
                row[loc_col] = u_loc

        # 2. อัปเดตจาก RTU Summary (gid=747048278) โดยยึด Site ID เป็นเกณฑ์หลักตามสูตร VLOOKUP ในชีต
        if sid in rtu_map:
            rr = rtu_map[sid]
            r_ip = _s(rr.get('IP'))
            r_commu = _s(rr.get('Commu.'))
            r_item = _s(rr.get('Item'))
            r_dnp = clean_dnp_str(rr.get('Address') if 'Address' in rr else rr.get('DNP'))
            r_wrl, r_model = parse_rtu_alias(rr.get('Alias'))

            if _is_meaningful_cell(r_ip):
                row[ip_col] = r_ip
            if _is_meaningful_cell(r_commu):
                row[commu_col] = r_commu
            if _is_meaningful_cell(r_item):
                row[item_col] = r_item
            if _is_meaningful_cell(r_dnp):
                row[dnp_col] = r_dnp
            row[frtu_model_col] = r_model if _is_meaningful_cell(r_model) else _s(row.get(frtu_model_col))
            row[wrl_master_col] = r_wrl if _is_meaningful_cell(r_wrl) else _s(row.get(wrl_master_col))

        # ทำความสะอาดค่า DNP และซิงค์คอลัมน์มาตรฐานสำหรับแสดงผลบนหมุดแผนที่และ Dashboard
        row[dnp_col] = clean_dnp_str(row.get(dnp_col))
        row[frtu_prod_col] = _s(row.get(frtu_model_col)) or _s(row.get(frtu_prod_col))
        row[comm_type_col] = _s(row.get(commu_col)) or _s(row.get(comm_type_col))
        row[comm_item_col] = _s(row.get(item_col)) or _s(row.get(comm_item_col))
        row[comm_group_col] = classify_comm_device_group(row.get(comm_type_col), row.get(comm_item_col))
        row[comm_ref_col] = _s(row.get(wrl_master_col)) or _s(row.get(comm_ref_col))

        # 3. อัปเดตจาก Data_Check (gid=2024067511) โดยยึด Site ID เป็นเกณฑ์หลักตามสูตรใน ชีต1
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

            # อัปเดตทับค่าเดิมเสมอ (รวมถึงกรณีที่ผลตรวจใหม่เป็นปกติและค่าความผิดปกติกลายเป็นค่าว่าง '')
            row[col_g_name] = val_g
            row[col_h_name] = val_h
            row[col_j_name] = val_j
            row[col_k_name] = val_k
            if raw_d:
                row[date_col] = _normalize_date_display_ddmmyyyy(raw_d)

        # 4. คำนวณคอลัมน์ N (สถานะการเข้าแก้ไขและออนไลน์ โดย ผบอ.) และ คอลัมน์ E (State SCADA หลังตรวจสอบ) ตามสูตรชีตจริง
        d_val = _s(row.get(state_col))
        j_val = _s(row.get(col_j_name))
        k_val = _s(row.get(col_k_name))
        n_val = _normalize_date_display_ddmmyyyy(row.get(col_n_name))
        if n_val in ('0', '0.0', 'false', 'False'):
            n_val = ''
        if not n_val and d_val == 'Online' and k_val != '':
            n_val = today_ddmmyyyy
        row[col_n_name] = n_val

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
            'กลุ่มอุปกรณ์สื่อสารหลัก': classify_comm_device_group(commu, item),
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
    """โหลดข้อมูลอุปกรณ์ในระบบและผสานข้อมูลจาก ชีต1 (gid=639012274), Update State (gid=0), RTU Summary (gid=747048278) และ Data_Check (gid=2024067511) โดยใช้ Site ID เป็นเกณฑ์หลัก"""
    df_base = build_gis_base_dataframe()
    if prefer_local:
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scada_stations'")
            if cur.fetchone():
                df_local = pd.read_sql("SELECT * FROM scada_stations", conn)
                if _is_valid_sheet1_df(df_local) and len(df_local) >= len(df_base):
                    df_base = merge_sheet1_remote_with_base(df_local, df_base)
        except Exception:
            pass
    else:
        print("กำลังดึงข้อมูลอุปกรณ์และพิกัด (ชีต1) จาก Google Sheets เข้าสู่ฐานข้อมูล...")
        df_remote = fetch_remote_dataframe(
            SHEET1_URLS,
            _is_valid_sheet1_df,
            normalizer_fn=normalize_sheet1_dataframe,
            sheet_name='ชีต1',
        )
        if df_remote is not None:
            print(f"[OK] ดึงข้อมูล ชีต1 จาก Google Sheets สำเร็จ! พบ {len(df_remote)} อุปกรณ์")
            df_base = merge_sheet1_remote_with_base(df_remote, df_base)
        else:
            print(f"[OK] ใช้โครงสร้างพิกัดและข้อมูลอุปกรณ์ฐาน (GIS Master + RTU Summary): {len(df_base)} อุปกรณ์")

    df_enriched = enrich_sheet1_with_update_and_check(df_base, df_update, df_check, df_rtu=df_rtu)
    try:
        df_enriched.to_sql('scada_stations', conn, if_exists='replace', index=False)
    except Exception:
        pass
    return df_enriched

status_hash_map = {}


def compute_overall_summary(dash_all_data_list):
    """
    [BUG FIX 1.1] คำนวณค่าสรุปภาพรวมของทั้งระบบ (overall_summary) แยกออกจากข้อมูลที่ถูกกรอง (filtered_data) อย่างชัดเจน
    เพื่อให้การ์ดสรุปด้านบนสุด (Summary Cards) แสดงยอดรวมทั้งหมดของทั้งระบบเสมอ ไม่ว่าจะคลิกกรองยี่ห้อ/รุ่น FRTU ใดก็ตาม
    """
    counts = {
        "total": len(dash_all_data_list),
        "Online": 0,
        "Telemetry Failure": 0,
        "Offline": 0,
        "Initializing": 0,
        "Connecting": 0,
        "other": 0,
    }
    for arr in dash_all_data_list:
        # arr[5] คือ State SCADA (สถานะหลักของระบบ), arr[0] คือ active_status
        raw_scada_state = arr[5] if len(arr) > 5 and arr[5] else (arr[0] if len(arr) > 0 else "")
        parent_state, _, _ = get_status_config(raw_scada_state)
        if parent_state in counts:
            counts[parent_state] += 1
        else:
            counts["other"] += 1

    offline_total = (
        counts["Telemetry Failure"]
        + counts["Offline"]
        + counts["Initializing"]
        + counts["Connecting"]
        + counts["other"]
    )
    denom = counts["Online"] + offline_total
    ava_pct = round((counts["Online"] / denom) * 100, 2) if denom > 0 else 0.0

    return {
        "total": counts["total"],
        "Online": counts["Online"],
        "Connecting": counts["Connecting"],
        "Offline": counts["Offline"],
        "Initializing": counts["Initializing"],
        "Telemetry Failure": counts["Telemetry Failure"],
        "other": counts["other"],
        "offline_total": offline_total,
        "ava_pct": ava_pct,
    }


_CLIENT_SIDE_HTML_SHELL_CACHE = None


def build_scada_map_and_data(prefer_local=False, write_files=False, embed_initial_data=True):
    """
    ฟังก์ชันหลักสำหรับดึงข้อมูลจาก Google Sheets / SQLite, คำนวณค่า overall_summary,
    สร้างแผนที่ Folium พร้อม UI Dashboard และคืนค่า Dictionary สำหรับเก็บใน In-Memory Cache ของ app.py
    """
    global status_hash_map
    status_hash_map = {}
    # --- 3. ดึงข้อมูล ---
    print("กำลังเชื่อมต่อฐานข้อมูลและซิงค์ข้อมูลล่าสุดจาก Google Sheets (ใช้ Site ID เป็นเกณฑ์หลัก)...")
    _offline_mode = bool(prefer_local or '--offline' in sys.argv or '--use-cache' in sys.argv)
    import tempfile, shutil
    _use_temp_db = False
    _work_db_path = DB_PATH
    try:
        _test_c = sqlite3.connect(DB_PATH)
        _test_c.execute("CREATE TABLE IF NOT EXISTS _ping (id INT)")
        _test_c.close()
    except Exception:
        _use_temp_db = True
        _work_db_path = os.path.join(tempfile.gettempdir(), "scada_real_work.db")
        if os.path.exists(DB_PATH):
            try:
                shutil.copy2(DB_PATH, _work_db_path)
            except Exception:
                pass

    conn = sqlite3.connect(_work_db_path)
    df_update = load_update_state_df(conn, prefer_local=_offline_mode)
    df_rtu = load_rtu_summary_df(conn, prefer_local=_offline_mode)
    df_check = load_data_check_df(conn, prefer_local=_offline_mode)
    df = load_sheet1_df(conn, df_update=df_update, df_check=df_check, df_rtu=df_rtu, prefer_local=_offline_mode)
    conn.close()
    if _use_temp_db and os.path.exists(_work_db_path):
        try:
            shutil.copy2(_work_db_path, DB_PATH)
        except Exception:
            pass

    print(f"กำลังประมวลผลข้อมูลอุปกรณ์ในระบบทั้งหมด {len(df)} แถว...")

    # --- 4. [CLIENT-SIDE RENDERING] ยกเลิกการสร้างออบเจกต์แผนที่ Folium และ Marker บนฝั่ง Server ---
    # ฝั่ง Backend จะประมวลผลเฉพาะข้อมูลดิบ (Raw JSON Data) เพื่อลดภาระ CPU/RAM บน Render Server
    # และส่งให้ฝั่ง Frontend (Browser JavaScript) เป็นผู้สร้างแผนที่ Leaflet, MarkerCluster และตารางทั้งหมด

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

    search_data, export_data_list = [], []
    thai_months_short = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]

    for node_idx, node in enumerate(processed_nodes):
        row, lat, lon, active_status, active_status_display = node['row'], node['lat'], node['lon'], node['active_status'], node['active_status_display']
        raw_parent, icon_name, h_color = node['raw_parent'], node['icon_name'], hex_color_map.get(node['color'], '#575757')
        get_hash(active_status)
        get_hash(raw_parent)

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

        eq_val_s = export_row_arr[2 + 4] if len(export_row_arr) > 6 else ''
        prod_val_s = export_row_arr[2 + 5] if len(export_row_arr) > 7 else ''
        comm_val_s = export_row_arr[2 + 6] if len(export_row_arr) > 8 else ''
        item_val_s = export_row_arr[2 + 7] if len(export_row_arr) > 9 else ''
        ref_val_s = export_row_arr[2 + 8] if len(export_row_arr) > 10 else ''
        ip_val_s = export_row_arr[2 + 9] if len(export_row_arr) > 11 else ''
        dnp_val_s = export_row_arr[2 + 10] if len(export_row_arr) > 12 else ''
        pea_val_s = export_row_arr[2 + 16] if len(export_row_arr) > 18 else ''
        extra_search_str = f"{ip_val_s} {dnp_val_s} {prod_val_s} {comm_val_s} {item_val_s} {ref_val_s} {pea_val_s} {eq_val_s}".strip()

        search_data.append({
            "id": site_id,
            "code": cmd_code,
            "name": location_name,
            "pea": pea_val_s,
            "eq": eq_val_s,
            "prod": prod_val_s,
            "comm": comm_val_s,
            "item": item_val_s,
            "wrl": ref_val_s,
            "ip": ip_val_s,
            "dnp": dnp_val_s,
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

    overall_summary_dict = compute_overall_summary(dash_all_data_list)
    _data_hash_str = hashlib.md5(
        json.dumps([overall_summary_dict, dash_all_data_list, report_data_list], ensure_ascii=False, sort_keys=True).encode('utf-8')
    ).hexdigest()[:12]
    initial_version = int(_data_hash_str, 16) if embed_initial_data else 0

    # ในโหมด Web Server (embed_initial_data=False) จะส่งเฉพาะโครงสร้างเริ่มต้นที่ว่างเปล่าใน HTML Shell
    # แล้วให้ Client-Side JavaScript ดึง JSON ข้อมูลดิบผ่าน `/api/map_data` มาประมวลผลและวาดแผนที่บน Browser เอง 100%
    overall_summary_json = json.dumps(overall_summary_dict if embed_initial_data else {}, ensure_ascii=False, separators=(',', ':'))
    dash_all_json = json.dumps(dash_all_data_list if embed_initial_data else [], ensure_ascii=False, separators=(',', ':'))
    comm_device_groups_json = json.dumps(COMM_DEVICE_GROUPS, ensure_ascii=False, separators=(',', ':'))
    search_json = json.dumps(search_data if embed_initial_data else [], ensure_ascii=False, separators=(',', ':'))
    export_json = json.dumps(export_data_list if embed_initial_data else [], ensure_ascii=False, separators=(',', ':'))
    status_hash_json = json.dumps(status_hash_map if embed_initial_data else {}, ensure_ascii=False, separators=(',', ':'))
    report_json_data = json.dumps(report_data_list if embed_initial_data else [], ensure_ascii=False, separators=(',', ':'))
    base_layer_names_json = json.dumps({
        "terrain": "tile_layer_terrain",
        "street": "tile_layer_street",
        "satellite": "tile_layer_satellite",
        "hybrid": "tile_layer_hybrid",
        "esri": "tile_layer_esri",
    }, ensure_ascii=False, separators=(',', ':'))

    # --- 8. UI CSS & JavaScript (Client-Side Rendering Shell) ---
    global _CLIENT_SIDE_HTML_SHELL_CACHE
    if (not embed_initial_data) and (_CLIENT_SIDE_HTML_SHELL_CACHE is not None) and (not write_files):
        return {
            "version": int(_data_hash_str, 16),
            "last_updated": time.time(),
            "overall_summary": overall_summary_dict,
            "dash_all_data": dash_all_data_list,
            "search_data": search_data,
            "export_data": export_data_list,
            "report_data": report_data_list,
            "status_hash_map": dict(status_hash_map),
            "comm_device_groups": COMM_DEVICE_GROUPS,
            "display_fields": [name for name, _ in resolved_display_cols],
            "html": _CLIENT_SIDE_HTML_SHELL_CACHE,
        }

    custom_ui_html = f"""
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&family=Prompt:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <script src="https://cdnjs.cloudflare.com/ajax/libs/exceljs/4.3.0/exceljs.min.js"></script>

        <style>
        :root {{
            --bg-canvas: #f4f6f9;
            --bg-surface: #ffffff;
            --bg-subtle: #f8fafc;
            --bg-hover: #f1f5f9;
            --bg-active: #eff6ff;
            --border-hairline: #cbd5e1;
            --border-subtle: #e2e8f0;
            --text-primary: #0f172a;
            --text-secondary: #475569;
            --text-muted: #64748b;
            --accent-primary: #2563eb;
            --accent-hover: #1d4ed8;
            --accent-soft: rgba(37, 99, 235, 0.10);
            --tree-line-color: #cbd5e1;
            --tree-line-active: #60a5fa;
            --shadow-card: 0 1px 3px rgba(15, 23, 42, 0.05), 0 1px 2px rgba(15, 23, 42, 0.03);
            --shadow-float: 0 12px 32px -4px rgba(15, 23, 42, 0.14), 0 4px 12px -2px rgba(15, 23, 42, 0.07);
            --seg-bg: #e2e8f0;
            --seg-pill: #ffffff;
            --radius-card: 14px;
            --radius-ctrl: 10px;
            --transition-smooth: 0.2s cubic-bezier(0.22, 1, 0.36, 1);
        }}

        body.theme-dark {{
            --bg-canvas: #0b0f17;
            --bg-surface: #151c28;
            --bg-subtle: #111722;
            --bg-hover: #1e293b;
            --bg-active: rgba(59, 130, 246, 0.18);
            --border-hairline: #334155;
            --border-subtle: #1e293b;
            --text-primary: #f1f5f9;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --accent-primary: #3b82f6;
            --accent-hover: #60a5fa;
            --accent-soft: rgba(59, 130, 246, 0.20);
            --tree-line-color: #334155;
            --tree-line-active: #60a5fa;
            --shadow-card: 0 2px 6px rgba(0, 0, 0, 0.38);
            --shadow-float: 0 16px 36px rgba(0, 0, 0, 0.62), 0 4px 12px rgba(0, 0, 0, 0.4);
            --seg-bg: #1e293b;
            --seg-pill: #334155;
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

        /* CHANGED: ปรับปุ่ม "รีเซ็ตค่า" และ "ยกเลิก" เป็นสไตล์ Ghost/Outline สีเทา และจัดวางปุ่ม "สรุปและ Export รายงาน" (Primary Blue) ให้โดดเด่นที่สุดอยู่ด้านล่างสุด */
        .report-btn-group {{ display: flex; flex-direction: column; gap: 10px; margin-top: 14px; }}
        .report-btn-secondary-row {{ display: flex; gap: 8px; width: 100%; }}
        .report-btn {{ flex: 1; min-height: 40px; padding: 8px 14px; border-radius: 10px; border: none; font-size: 12.5px; font-weight: 500; cursor: pointer; display: flex; justify-content: center; align-items: center; gap: 6px; transition: all 0.16s ease; }}
        .report-btn-cancel {{ background: transparent; color: var(--text-secondary); border: 1px solid var(--border-hairline); flex: 1; }}
        .report-btn-cancel:hover {{ background: var(--bg-hover); color: var(--text-primary); border-color: #9aa0a6; }}
        .report-btn-dl {{ background: var(--accent-primary); color: #ffffff; font-weight: 600; min-height: 44px; font-size: 13.5px; width: 100%; box-shadow: 0 2px 8px rgba(26, 115, 232, 0.26); }}
        .report-btn-dl:hover {{ background: var(--accent-hover); box-shadow: 0 4px 12px rgba(26, 115, 232, 0.34); }}
        .report-note {{ font-size: 11px; color: var(--text-secondary); margin: 8px 0 0; line-height: 1.45; text-align: center; }}

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
            gap: 7px;
            background: #ffffff;
            border: 1.5px solid #94a3b8;
            border-radius: 9px;
            padding: 6px 12px;
            max-width: 100%;
            min-width: 0;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
            transition: all 0.18s cubic-bezier(0.2, 0.8, 0.2, 1);
        }}
        body.theme-dark .dash-ctrl-group {{
            background: #2b3038;
            border-color: #4b5563;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
        }}
        .dash-ctrl-group:hover,
        .dash-ctrl-group:focus-within {{
            border-color: #2563eb;
            background: #f8fafc;
            box-shadow: 0 2px 6px rgba(37, 99, 235, 0.14);
        }}
        body.theme-dark .dash-ctrl-group:hover,
        body.theme-dark .dash-ctrl-group:focus-within {{
            border-color: #60a5fa;
            background: #323842;
            box-shadow: 0 2px 8px rgba(96, 165, 250, 0.2);
        }}
        /* สถานะสีเน้น (Highlight) เมื่อตัวกรองกำลังถูกเลือกใช้งาน */
        .dash-ctrl-group.is-filter-active {{
            background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%) !important;
            border-color: #1d4ed8 !important;
            box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.18), 0 2px 6px rgba(29, 78, 216, 0.15) !important;
        }}
        body.theme-dark .dash-ctrl-group.is-filter-active {{
            background: linear-gradient(135deg, rgba(37, 99, 235, 0.34) 0%, rgba(29, 78, 216, 0.48) 100%) !important;
            border-color: #60a5fa !important;
            box-shadow: 0 0 0 3px rgba(96, 165, 250, 0.24), 0 2px 8px rgba(0, 0, 0, 0.35) !important;
        }}
        .dash-ctrl-group label {{
            font-size: 12px;
            color: #334155;
            font-weight: 600;
            white-space: nowrap;
            flex-shrink: 0;
            letter-spacing: 0.1px;
        }}
        body.theme-dark .dash-ctrl-group label {{
            color: #cbd5e1;
        }}
        .dash-ctrl-group.is-filter-active label {{
            color: #1e40af !important;
            font-weight: 700 !important;
        }}
        body.theme-dark .dash-ctrl-group.is-filter-active label {{
            color: #bfdbfe !important;
        }}
        .dash-select {{
            background: transparent;
            border: none;
            color: #0f172a;
            font-size: 12.5px;
            font-weight: 700;
            cursor: pointer;
            min-width: 0;
            max-width: 100%;
            text-overflow: ellipsis;
            padding-right: 4px;
        }}
        body.theme-dark .dash-select {{
            color: #f8fafc;
        }}
        .dash-ctrl-group.is-filter-active .dash-select,
        .dash-ctrl-group.is-filter-active .hier-brand-trigger {{
            color: #1d4ed8 !important;
            font-weight: 700 !important;
        }}
        body.theme-dark .dash-ctrl-group.is-filter-active .dash-select,
        body.theme-dark .dash-ctrl-group.is-filter-active .hier-brand-trigger {{
            color: #ffffff !important;
        }}
        .dash-select option {{
            background: var(--bg-surface);
            color: var(--text-primary);
            font-weight: 500;
        }}
        .dash-static-label {{
            color: #0f172a;
            font-size: 12.5px;
            font-weight: 700;
            padding: 1px 2px;
            white-space: nowrap;
        }}
        body.theme-dark .dash-static-label {{
            color: #f8fafc;
        }}
        /* Hierarchical Brand Filter Popover */
        .hier-brand-filter {{
            position: relative;
            display: inline-flex;
            align-items: center;
        }}
        .hier-brand-trigger {{
            background: transparent;
            border: none;
            color: #0f172a;
            font-size: 12.5px;
            font-weight: 700;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 0 2px;
            white-space: nowrap;
        }}
        body.theme-dark .hier-brand-trigger {{
            color: #f8fafc;
        }}
        .hier-brand-trigger:hover {{
            color: var(--accent-primary);
        }}
        .hier-brand-menu {{
            display: none;
            position: absolute;
            top: calc(100% + 8px);
            left: 0;
            width: 330px;
            max-width: calc(100vw - 32px);
            max-height: 420px;
            overflow-y: auto;
            background: var(--bg-surface);
            border: 1px solid var(--border-hairline);
            border-radius: 12px;
            box-shadow: var(--shadow-float);
            padding: 8px;
            z-index: 100050;
            flex-direction: column;
            gap: 4px;
        }}
        .hier-brand-menu.align-right {{
            left: auto;
            right: 0;
        }}
        .hier-brand-menu.show {{
            display: flex;
        }}
        .hier-brand-search {{
            width: 100%;
            padding: 6px 10px;
            font-size: 12px;
            border: 1px solid var(--border-hairline);
            border-radius: 8px;
            background: var(--bg-subtle);
            color: var(--text-primary);
            margin-bottom: 4px;
        }}
        .hier-brand-search:focus {{
            border-color: var(--accent-primary);
            background: var(--bg-surface);
        }}
        .hier-brand-all-btn {{
            width: 100%;
            text-align: left;
            padding: 7px 10px;
            border-radius: 8px;
            border: 1px solid transparent;
            background: transparent;
            color: var(--text-primary);
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .hier-brand-all-btn:hover {{
            background: var(--bg-hover);
        }}
        .hier-brand-all-btn.is-active {{
            background: var(--bg-active);
            color: var(--accent-primary);
            border-color: rgba(26,115,232,0.3);
        }}
        .hier-brand-group {{
            border: 1px solid var(--border-subtle);
            border-radius: 8px;
            overflow: hidden;
            background: var(--bg-subtle);
        }}
        .hier-brand-group.is-expanded {{
            border-color: var(--border-hairline);
            background: var(--bg-surface);
        }}
        .hier-brand-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 7px 10px;
            cursor: pointer;
            user-select: none;
            gap: 6px;
            transition: background-color 0.12s;
        }}
        .hier-brand-header:hover {{
            background: var(--bg-hover);
        }}
        .hier-brand-header.is-active {{
            background: var(--bg-active);
            color: var(--accent-primary);
        }}
        .hier-brand-header-left {{
            display: flex;
            align-items: center;
            gap: 7px;
            min-width: 0;
            flex: 1;
        }}
        .hier-brand-arrow {{
            font-size: 10px;
            color: var(--text-secondary);
            transition: transform 0.18s ease;
            display: inline-flex;
            width: 14px;
            justify-content: center;
        }}
        .hier-brand-group.is-expanded .hier-brand-arrow {{
            transform: rotate(90deg);
            color: var(--accent-primary);
        }}
        .hier-brand-title {{
            font-size: 12px;
            font-weight: 600;
            color: var(--text-primary);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .hier-brand-count {{
            font-size: 11px;
            color: var(--text-secondary);
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            padding: 1px 7px;
            border-radius: 999px;
            font-variant-numeric: tabular-nums;
        }}
        .hier-brand-sublist {{
            display: none;
            flex-direction: column;
            padding: 4px 6px 6px 22px;
            border-top: 1px solid var(--border-subtle);
            background: var(--bg-surface);
            gap: 2px;
        }}
        .hier-brand-group.is-expanded .hier-brand-sublist {{
            display: flex;
        }}
        .hier-brand-subitem {{
            width: 100%;
            text-align: left;
            padding: 5px 8px;
            border-radius: 6px;
            border: none;
            background: transparent;
            color: var(--text-primary);
            font-size: 11.5px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
        }}
        .hier-brand-subitem:hover {{
            background: var(--bg-hover);
        }}
        .hier-brand-subitem.is-active {{
            background: var(--bg-active);
            color: var(--accent-primary);
            font-weight: 600;
        }}
        .hier-brand-subitem-SelectGroup {{
            font-weight: 600;
            color: var(--accent-primary);
            border-bottom: 1px dashed var(--border-subtle);
            margin-bottom: 2px;
            padding-bottom: 6px;
        }}
        .prod-group-row {{
            background: var(--bg-subtle);
        }}
        .prod-sub-row td:first-child {{
            padding-left: 28px !important;
        }}
        .prod-expand-btn {{
            border: 1px solid var(--border-hairline);
            background: var(--bg-surface);
            color: var(--text-secondary);
            border-radius: 5px;
            width: 20px;
            height: 20px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            cursor: pointer;
            margin-right: 6px;
            transition: transform 0.15s, background-color 0.15s;
        }}
        .prod-expand-btn:hover {{
            background: var(--bg-hover);
            color: var(--text-primary);
        }}
        .prod-expand-btn.is-open {{
            transform: rotate(90deg);
            color: var(--accent-primary);
            border-color: var(--accent-primary);
        }}
        .dash-action-btn {{
            border: 1.5px solid #94a3b8;
            background: #ffffff;
            color: #1e293b;
            border-radius: 9px;
            padding: 6px 13px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
            transition: all 0.16s ease;
            white-space: nowrap;
        }}
        body.theme-dark .dash-action-btn {{
            background: #2b3038;
            border-color: #4b5563;
            color: #f1f5f9;
        }}
        .dash-action-btn:hover {{
            background: #eff6ff;
            border-color: #2563eb;
            color: #1d4ed8;
            transform: translateY(-1px);
            box-shadow: 0 2px 6px rgba(37, 99, 235, 0.14);
        }}
        body.theme-dark .dash-action-btn:hover {{
            background: #374151;
            border-color: #60a5fa;
            color: #93c5fd;
        }}
        .dash-action-btn.is-active {{
            background: #dbeafe !important;
            border-color: #1d4ed8 !important;
            color: #1e40af !important;
            font-weight: 700 !important;
        }}
        body.theme-dark .dash-action-btn.is-active {{
            background: rgba(37, 99, 235, 0.35) !important;
            border-color: #60a5fa !important;
            color: #ffffff !important;
        }}
        /* ปุ่มล้างตัวกรอง (Clear Filter Button) สีสันและ Contrast ชัดเจน */
        .dash-action-btn.btn-clear-filter {{
            background: #fff1f2;
            border: 1.5px solid #fb7185;
            color: #be123c;
            font-weight: 700;
        }}
        body.theme-dark .dash-action-btn.btn-clear-filter {{
            background: rgba(225, 29, 72, 0.18);
            border-color: #fb7185;
            color: #fda4af;
        }}
        .dash-action-btn.btn-clear-filter:hover {{
            background: #ffe4e6;
            border-color: #e11d48;
            color: #9f1239;
            box-shadow: 0 2px 8px rgba(225, 29, 72, 0.22);
        }}
        body.theme-dark .dash-action-btn.btn-clear-filter:hover {{
            background: rgba(225, 29, 72, 0.32);
            border-color: #f43f5e;
            color: #ffffff;
        }}
        /* เมื่อมีตัวกรองกำลังถูกใช้งาน ให้ปุ่มล้างตัวกรองสว่างเด่นชัดเป็นพิเศษ */
        .dash-action-btn.btn-clear-filter.has-active-filters {{
            background: linear-gradient(135deg, #e11d48 0%, #be123c 100%) !important;
            border-color: #9f1239 !important;
            color: #ffffff !important;
            box-shadow: 0 2px 8px rgba(225, 29, 72, 0.35) !important;
        }}
        .dash-action-btn.btn-clear-filter.has-active-filters:hover {{
            background: linear-gradient(135deg, #be123c 0%, #9f1239 100%) !important;
            transform: translateY(-1px);
        }}
        .dash-action-btn.btn-primary {{
            background: var(--accent-primary);
            border-color: var(--accent-primary);
            color: #ffffff;
            font-weight: 600;
        }}
        .dash-action-btn.btn-primary:hover {{
            background: var(--accent-hover);
            border-color: var(--accent-hover);
            color: #ffffff;
        }}

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
            gap: 8px;
            padding: 10px 14px;
            background: linear-gradient(90deg, #eff6ff 0%, #dbeafe 100%);
            border: 1.5px solid #60a5fa;
            border-radius: 10px;
            font-size: 12.5px;
            color: #1e40af;
            box-shadow: 0 2px 6px rgba(37, 99, 235, 0.1);
        }}
        body.theme-dark .explorer-active-filters {{
            background: linear-gradient(90deg, rgba(30, 64, 175, 0.35) 0%, rgba(29, 78, 216, 0.25) 100%);
            border-color: #60a5fa;
            color: #bfdbfe;
        }}
        .filter-chip-badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: #1d4ed8;
            border: 1px solid #1e40af;
            color: #ffffff;
            padding: 3px 10px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 600;
            box-shadow: 0 1px 3px rgba(29, 78, 216, 0.25);
        }}
        body.theme-dark .filter-chip-badge {{
            background: #2563eb;
            border-color: #60a5fa;
            color: #ffffff;
        }}
        .filter-chip-badge button {{
            border: none;
            background: rgba(255, 255, 255, 0.22);
            color: #ffffff;
            cursor: pointer;
            font-size: 13px;
            font-weight: 700;
            line-height: 1;
            width: 18px;
            height: 18px;
            border-radius: 50%;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 0;
            transition: background-color 0.15s;
        }}
        .filter-chip-badge button:hover {{
            background: #ef4444;
            color: #ffffff;
        }}
        .filter-chip-clear-all {{
            margin-left: auto;
            border: 1px solid #fb7185;
            background: #fff1f2;
            color: #be123c;
            border-radius: 999px;
            padding: 3px 10px;
            font-size: 11.5px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .filter-chip-clear-all:hover {{
            background: #e11d48;
            border-color: #be123c;
            color: #ffffff;
        }}
        body.theme-dark .filter-chip-clear-all {{
            background: rgba(225, 29, 72, 0.25);
            border-color: #fb7185;
            color: #fecdd3;
        }}
        .explorer-toolbar {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: space-between; }}
        .explorer-search-input {{
            flex: 1 1 240px;
            min-width: 0;
            background: #ffffff;
            border: 1.5px solid #94a3b8;
            border-radius: 9px;
            padding: 7px 12px;
            color: #0f172a;
            font-size: 12.5px;
            font-weight: 500;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
            transition: all 0.18s ease;
        }}
        body.theme-dark .explorer-search-input {{
            background: #2b3038;
            border-color: #4b5563;
            color: #f8fafc;
        }}
        .explorer-search-input::placeholder {{
            color: #64748b;
            font-weight: 400;
        }}
        body.theme-dark .explorer-search-input::placeholder {{
            color: #94a3b8;
        }}
        /* === NEW: Minimalist Top Bar, Plain Reference Text, Filtered Summary Badges, Cascading Disabled State & User Report Modal === */
        .dash-ref-plain-text {{
            font-size: 13.5px;
            font-weight: 500;
            color: var(--text-secondary);
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            padding: 0 !important;
            margin: 0;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            user-select: text;
            cursor: default;
        }}
        .dash-ref-plain-text strong {{
            color: var(--text-primary);
            font-weight: 700;
        }}
        .dash-action-btn.btn-report-issue {{
            background: #fff7ed;
            border: 1.5px solid #f97316;
            color: #c2410c;
            font-weight: 600;
        }}
        body.theme-dark .dash-action-btn.btn-report-issue {{
            background: rgba(249, 115, 22, 0.16);
            border-color: #fb923c;
            color: #fdba74;
        }}
        .dash-action-btn.btn-report-issue:hover {{
            background: #ea580c;
            border-color: #c2410c;
            color: #ffffff;
            box-shadow: 0 2px 8px rgba(234, 88, 12, 0.25);
        }}

        /* Filtered Summary Badge Bar in Section 6 */
        .filtered-summary-strip {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 8px 12px;
            padding: 10px 14px;
            border-radius: 10px;
            background: var(--bg-subtle);
            border: 1px solid var(--border-hairline);
            margin-bottom: 2px;
            transition: all 0.2s ease;
        }}
        .filtered-summary-strip.is-filtered-mode {{
            background: linear-gradient(90deg, rgba(26, 115, 232, 0.06) 0%, rgba(26, 115, 232, 0.02) 100%);
            border-color: rgba(26, 115, 232, 0.38);
        }}
        body.theme-dark .filtered-summary-strip.is-filtered-mode {{
            background: linear-gradient(90deg, rgba(69, 153, 255, 0.14) 0%, rgba(69, 153, 255, 0.05) 100%);
            border-color: rgba(96, 165, 250, 0.45);
        }}
        .filtered-summary-lead {{
            display: inline-flex;
            align-items: center;
            gap: 7px;
            font-size: 12px;
            font-weight: 700;
            color: var(--text-primary);
            white-space: nowrap;
        }}
        .filtered-summary-tag {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 3px 9px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 700;
            background: #e8f0fe;
            color: #1967d2;
            border: 1px solid rgba(25, 103, 210, 0.25);
        }}
        body.theme-dark .filtered-summary-tag {{
            background: rgba(69, 153, 255, 0.2);
            color: #93c5fd;
            border-color: rgba(147, 197, 253, 0.3);
        }}
        .filtered-summary-badges {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 6px;
        }}
        .fs-badge {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 3px 10px;
            border-radius: 999px;
            font-size: 11.5px;
            font-weight: 600;
            font-variant-numeric: tabular-nums;
            border: 1px solid transparent;
            white-space: nowrap;
        }}
        .fs-badge-dot {{
            width: 7px;
            height: 7px;
            border-radius: 50%;
            display: inline-block;
            flex-shrink: 0;
        }}
        .fs-badge.fs-total {{
            background: var(--bg-surface);
            color: var(--text-primary);
            border-color: var(--border-hairline);
        }}
        .fs-badge.fs-online {{
            background: #e6f4ea;
            color: #137333;
            border-color: rgba(19, 115, 51, 0.22);
        }}
        .fs-badge.fs-offline-all {{
            background: #fce8e6;
            color: #c5221f;
            border-color: rgba(197, 34, 31, 0.22);
        }}
        .fs-badge.fs-connecting {{
            background: #fef0e6;
            color: #c25100;
            border-color: rgba(194, 81, 0, 0.22);
        }}
        .fs-badge.fs-offline {{
            background: #fce8e6;
            color: #c5221f;
            border-color: rgba(197, 34, 31, 0.22);
        }}
        .fs-badge.fs-init {{
            background: #ecfccb;
            color: #3f6212;
            border-color: rgba(63, 98, 18, 0.22);
        }}
        .fs-badge.fs-tele {{
            background: #fef3c7;
            color: #b45309;
            border-color: rgba(180, 83, 9, 0.22);
        }}
        body.theme-dark .fs-badge.fs-online {{ background: rgba(22, 163, 74, 0.2); color: #4ade80; }}
        body.theme-dark .fs-badge.fs-offline-all {{ background: rgba(220, 38, 38, 0.2); color: #f87171; }}
        body.theme-dark .fs-badge.fs-connecting {{ background: rgba(234, 88, 12, 0.2); color: #fb923c; }}
        body.theme-dark .fs-badge.fs-offline {{ background: rgba(220, 38, 38, 0.2); color: #f87171; }}
        body.theme-dark .fs-badge.fs-init {{ background: rgba(101, 163, 13, 0.2); color: #a3e635; }}
        body.theme-dark .fs-badge.fs-tele {{ background: rgba(217, 119, 6, 0.2); color: #fbbf24; }}

        /* Cascading Cross-Filter Disabled State (Opacity 0.4 & Unclickable) */
        .is-zero-match,
        tr.clickable-row.is-zero-match,
        .hier-brand-group.is-zero-match,
        .hier-brand-subitem.is-zero-match,
        .casc-menu-item.is-zero-match,
        .dash-select option:disabled {{
            opacity: 0.4 !important;
            pointer-events: none !important;
            cursor: not-allowed !important;
            user-select: none !important;
        }}
        .hier-brand-group.is-zero-match > .hier-brand-header {{
            pointer-events: none !important;
            cursor: not-allowed !important;
        }}

        /* Custom Cascading Dropdown Popover for Section 6 Filters */
        .casc-filter-wrap {{
            position: relative;
            display: inline-flex;
            align-items: center;
        }}
        .casc-filter-trigger {{
            background: transparent;
            border: none;
            color: #0f172a;
            font-size: 12.5px;
            font-weight: 700;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 0 2px;
            white-space: nowrap;
            max-width: 220px;
        }}
        body.theme-dark .casc-filter-trigger {{
            color: #f8fafc;
        }}
        .casc-filter-trigger span.casc-label-txt {{
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        .dash-ctrl-group.is-filter-active .casc-filter-trigger {{
            color: #1d4ed8 !important;
        }}
        body.theme-dark .dash-ctrl-group.is-filter-active .casc-filter-trigger {{
            color: #ffffff !important;
        }}
        .casc-filter-menu {{
            display: none;
            position: absolute;
            top: calc(100% + 8px);
            left: 0;
            width: 300px;
            max-width: calc(100vw - 32px);
            max-height: 360px;
            overflow-y: auto;
            background: var(--bg-surface);
            border: 1px solid var(--border-hairline);
            border-radius: 12px;
            box-shadow: var(--shadow-float);
            padding: 8px;
            z-index: 100055;
            flex-direction: column;
            gap: 3px;
        }}
        .casc-filter-menu.align-right {{
            left: auto;
            right: 0;
        }}
        .casc-filter-menu.show {{
            display: flex;
        }}
        .casc-menu-item {{
            width: 100%;
            text-align: left;
            padding: 6px 10px;
            border-radius: 7px;
            border: 1px solid transparent;
            background: transparent;
            color: var(--text-primary);
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            transition: background-color 0.12s;
        }}
        .casc-menu-item:hover:not(.is-zero-match) {{
            background: var(--bg-hover);
        }}
        .casc-menu-item.is-active {{
            background: var(--bg-active);
            color: var(--accent-primary);
            font-weight: 700;
            border-color: rgba(26, 115, 232, 0.3);
        }}

        /* User Report Incorrect Data Modal */
        .ur-modal-box {{
            width: min(96vw, 760px) !important;
            max-height: calc(100dvh - 24px);
        }}
        .ur-step-badge {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 22px;
            height: 22px;
            border-radius: 50%;
            background: var(--accent-primary);
            color: #ffffff;
            font-size: 11.5px;
            font-weight: 700;
            margin-right: 6px;
            flex-shrink: 0;
        }}
        .ur-device-info-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
            gap: 6px;
            margin-top: 8px;
            padding: 10px;
            border-radius: 10px;
            background: var(--bg-surface);
            border: 1px solid var(--border-hairline);
            max-height: 195px;
            overflow-y: auto;
        }}
        .ur-info-cell {{
            padding: 5px 8px;
            border-radius: 6px;
            background: var(--bg-subtle);
            border: 1px solid var(--border-subtle);
            display: flex;
            flex-direction: column;
            gap: 1px;
            min-width: 0;
        }}
        .ur-info-cell span {{
            font-size: 10.5px;
            color: var(--text-secondary);
        }}
        .ur-info-cell strong {{
            font-size: 11.5px;
            color: var(--text-primary);
            word-break: break-word;
        }}
        .ur-fields-checklist {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
            gap: 7px;
            max-height: 240px;
            overflow-y: auto;
            padding: 4px 2px;
        }}
        .ur-field-check-card {{
            display: flex;
            flex-direction: column;
            gap: 6px;
            padding: 8px 10px;
            border-radius: 9px;
            background: var(--bg-surface);
            border: 1px solid var(--border-hairline);
            transition: all 0.14s ease;
        }}
        .ur-field-check-card.is-checked {{
            border-color: #ea580c;
            background: #fff7ed;
            box-shadow: 0 1px 4px rgba(234, 88, 12, 0.14);
        }}
        body.theme-dark .ur-field-check-card.is-checked {{
            border-color: #fb923c;
            background: rgba(234, 88, 12, 0.15);
        }}
        .ur-field-check-top {{
            display: flex;
            align-items: flex-start;
            gap: 8px;
            cursor: pointer;
        }}
        .ur-field-check-top input[type="checkbox"] {{
            width: 16px;
            height: 16px;
            margin-top: 2px;
            accent-color: #ea580c;
            cursor: pointer;
            flex-shrink: 0;
        }}
        .ur-field-name {{
            font-size: 12px;
            font-weight: 600;
            color: var(--text-primary);
        }}
        .ur-field-curval {{
            font-size: 11px;
            color: var(--text-secondary);
            word-break: break-word;
        }}
        .ur-field-inline-input {{
            display: none;
            width: 100%;
            padding: 6px 9px;
            font-size: 12px;
            border-radius: 7px;
            border: 1px solid #fb923c;
            background: var(--bg-surface);
            color: var(--text-primary);
        }}
        .ur-field-check-card.is-checked .ur-field-inline-input {{
            display: block;
        }}
        .ur-text-input {{
            width: 100%;
            padding: 9px 12px;
            border-radius: 9px;
            border: 1.5px solid var(--border-hairline);
            background: var(--bg-surface);
            color: var(--text-primary);
            font-size: 13px;
            transition: border-color 0.15s;
        }}
        .ur-text-input:focus {{
            border-color: var(--accent-primary);
            box-shadow: 0 0 0 3px rgba(26, 115, 232, 0.15);
        }}
        .ur-status-banner {{
            display: none;
            padding: 10px 12px;
            border-radius: 9px;
            font-size: 12px;
            font-weight: 500;
            margin-top: 8px;
            line-height: 1.45;
        }}
        .ur-status-banner.show {{
            display: block;
        }}
        .ur-status-banner.is-success {{
            background: #e6f4ea;
            color: #137333;
            border: 1px solid rgba(19, 115, 51, 0.3);
        }}
        .ur-status-banner.is-error {{
            background: #fce8e6;
            color: #c5221f;
            border: 1px solid rgba(197, 34, 31, 0.3);
        }}
        .ur-status-banner.is-warn {{
            background: #fef7e0;
            color: #b06000;
            border: 1px solid rgba(176, 96, 0, 0.3);
        }}


        /* === UPGRADED BESPOKE DESIGN: MULTI-SELECT, TREE GUIDE LINES, USER REPORT & FUZZY SEARCH HIGHLIGHT === */
        mark.search-hl {{
            background: rgba(250, 204, 21, 0.42);
            color: inherit;
            padding: 0 2px;
            border-radius: 3px;
            font-weight: 700;
            box-decoration-break: clone;
            -webkit-box-decoration-break: clone;
        }}
        body.theme-dark mark.search-hl {{
            background: rgba(234, 179, 8, 0.38);
            color: #fef08a;
        }}

        /* Fix Dropdown Squishing Bug & Standardize Multi-Select Popover Layout */
        .casc-filter-menu,
        .hier-brand-menu {{
            width: 356px !important;
            max-width: calc(100vw - 24px) !important;
            max-height: 440px !important;
            overflow: hidden !important;
            padding: 0 !important;
            border-radius: 14px !important;
            border: 1px solid var(--border-hairline) !important;
            box-shadow: var(--shadow-float) !important;
            background: var(--bg-surface) !important;
            z-index: 100060 !important;
            transition: opacity 0.16s ease, transform 0.16s ease;
        }}
        .ms-menu-header {{
            flex-shrink: 0 !important;
            padding: 10px 12px 8px !important;
            background: var(--bg-subtle);
            border-bottom: 1px solid var(--border-subtle);
            display: flex;
            flex-direction: column;
            gap: 7px;
        }}
        .ms-menu-toolbar {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            font-size: 11.5px;
            color: var(--text-secondary);
        }}
        .ms-menu-status-txt {{
            font-weight: 600;
            color: var(--text-primary);
            font-size: 11.5px;
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }}
        .ms-menu-actions {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}
        .ms-menu-action-btn {{
            border: 1px solid var(--border-hairline);
            background: var(--bg-surface);
            color: var(--text-secondary);
            border-radius: 6px;
            padding: 3px 8px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.14s ease;
            white-space: nowrap;
        }}
        .ms-menu-action-btn:hover {{
            background: var(--bg-hover);
            color: var(--text-primary);
            border-color: var(--accent-primary);
        }}
        .ms-menu-action-btn.is-clear-btn {{
            color: #dc2626;
            border-color: rgba(220, 38, 38, 0.28);
            background: rgba(254, 242, 242, 0.7);
        }}
        body.theme-dark .ms-menu-action-btn.is-clear-btn {{
            color: #f87171;
            border-color: rgba(248, 113, 113, 0.3);
            background: rgba(220, 38, 38, 0.14);
        }}
        .ms-menu-action-btn.is-clear-btn:hover {{
            background: #dc2626;
            color: #ffffff;
            border-color: #dc2626;
        }}
        .ms-menu-scroll-list {{
            flex: 1 1 auto !important;
            max-height: 332px !important;
            overflow-y: auto !important;
            overflow-x: hidden !important;
            padding: 8px 10px 10px !important;
            display: flex !important;
            flex-direction: column !important;
            gap: 4px !important;
        }}

        /* Prevent any item or group from ever being squished by Flexbox */
        .casc-menu-item,
        .hier-brand-group,
        .hier-brand-header,
        .hier-brand-subitem,
        .tree-node-group,
        .tree-parent-row,
        .tree-child-row {{
            flex-shrink: 0 !important;
            min-height: 36px !important;
            height: auto !important;
            overflow: visible !important;
        }}

        /* Multi-Select Trigger Pill Badge */
        .ms-trigger-badge {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 19px;
            height: 19px;
            padding: 0 6px;
            border-radius: 999px;
            background: var(--accent-primary);
            color: #ffffff !important;
            font-size: 11px;
            font-weight: 700;
            line-height: 1;
            font-variant-numeric: tabular-nums;
            box-shadow: 0 1px 2px rgba(37, 99, 235, 0.25);
        }}
        .ms-trigger-clear-inline {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 17px;
            height: 17px;
            border-radius: 50%;
            background: rgba(15, 23, 42, 0.08);
            color: var(--text-secondary);
            font-size: 12px;
            font-weight: 700;
            line-height: 1;
            margin-left: 2px;
            transition: all 0.14s ease;
        }}
        body.theme-dark .ms-trigger-clear-inline {{
            background: rgba(255, 255, 255, 0.15);
            color: #e2e8f0;
        }}
        .ms-trigger-clear-inline:hover {{
            background: #dc2626 !important;
            color: #ffffff !important;
        }}

        /* Multi-Select Option Row with Checkbox */
        .ms-check-row {{
            width: 100%;
            display: flex !important;
            align-items: center !important;
            justify-content: space-between !important;
            gap: 10px !important;
            padding: 7px 10px !important;
            border-radius: 8px !important;
            border: 1px solid transparent !important;
            background: transparent;
            color: var(--text-primary);
            font-size: 12.5px;
            font-weight: 500;
            line-height: 1.38 !important;
            cursor: pointer;
            text-align: left;
            transition: background-color 0.12s ease, border-color 0.12s ease;
            user-select: none;
            flex-shrink: 0 !important;
            min-height: 36px !important;
        }}
        .ms-check-row:hover:not(.is-zero-match) {{
            background: var(--bg-hover);
        }}
        .ms-check-row.is-active {{
            background: var(--bg-active);
            border-color: rgba(37, 99, 235, 0.28) !important;
            color: var(--accent-primary);
            font-weight: 600;
        }}
        .ms-check-left {{
            display: flex;
            align-items: flex-start;
            gap: 9px;
            min-width: 0;
            flex: 1;
        }}
        .ms-checkbox-input {{
            width: 16px !important;
            height: 16px !important;
            margin: 2px 0 0 0 !important;
            accent-color: var(--accent-primary) !important;
            border-radius: 4px;
            cursor: pointer;
            flex-shrink: 0 !important;
        }}
        .ms-label-text {{
            word-break: break-word;
            overflow-wrap: anywhere;
            line-height: 1.38;
            color: var(--text-primary);
        }}
        .ms-check-row.is-active .ms-label-text {{
            color: var(--accent-primary);
            font-weight: 600;
        }}

        /* === TREE VIEW HIERARCHY WITH VISUAL GUIDE LINES (เส้นโยงหัวข้อหลัก-ย่อย) === */
        .tree-node-group {{
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            background: var(--bg-subtle);
            margin-bottom: 4px;
            transition: border-color 0.15s ease, background-color 0.15s ease;
        }}
        .tree-node-group.is-expanded {{
            background: var(--bg-surface);
            border-color: var(--border-hairline);
            box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
        }}
        .tree-node-group.has-checked-children {{
            border-color: rgba(37, 99, 235, 0.42);
        }}
        .tree-parent-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            padding: 8px 10px;
            border-radius: 9px;
            cursor: pointer;
            user-select: none;
            transition: background-color 0.12s ease;
        }}
        .tree-parent-row:hover {{
            background: var(--bg-hover);
        }}
        .tree-parent-row.is-active {{
            background: var(--bg-active);
        }}
        .tree-parent-left {{
            display: flex;
            align-items: center;
            gap: 7px;
            min-width: 0;
            flex: 1;
        }}
        .tree-expand-btn {{
            width: 22px;
            height: 22px;
            border-radius: 6px;
            border: 1px solid var(--border-subtle);
            background: var(--bg-surface);
            color: var(--text-secondary);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            cursor: pointer;
            flex-shrink: 0;
            padding: 0;
            transition: transform 0.18s ease, color 0.15s ease, border-color 0.15s ease;
        }}
        .tree-expand-btn:hover {{
            border-color: var(--accent-primary);
            color: var(--accent-primary);
        }}
        .tree-node-group.is-expanded .tree-expand-btn {{
            transform: rotate(90deg);
            color: var(--accent-primary);
            border-color: rgba(37, 99, 235, 0.35);
            background: var(--bg-active);
        }}
        .tree-parent-title-wrap {{
            display: flex;
            flex-direction: column;
            min-width: 0;
            flex: 1;
        }}
        .tree-parent-title {{
            font-size: 12.5px;
            font-weight: 700;
            color: var(--text-primary);
            line-height: 1.35;
            word-break: break-word;
        }}
        .tree-parent-meta {{
            font-size: 10.5px;
            color: var(--text-secondary);
            line-height: 1.25;
        }}
        /* Tree Children Container with Vertical Guide Line (เส้นโยงแนวตั้ง) */
        .tree-children-list {{
            display: none;
            flex-direction: column;
            position: relative;
            margin: 2px 8px 8px 20px;
            padding: 4px 0 2px 15px;
            border-left: 1.5px solid var(--tree-line-color);
            gap: 3px;
        }}
        .tree-node-group.has-checked-children .tree-children-list {{
            border-left-color: var(--tree-line-active);
        }}
        .tree-node-group.is-expanded .tree-children-list {{
            display: flex !important;
        }}
        /* Tree Child Item with Horizontal Connector Branch (เส้นโยงแนวนอนเชื่อมหัวข้อย่อย) */
        .tree-child-row {{
            position: relative;
            display: flex !important;
            align-items: center !important;
            justify-content: space-between !important;
            gap: 8px !important;
            padding: 6px 9px !important;
            border-radius: 7px !important;
            border: 1px solid transparent !important;
            background: transparent;
            color: var(--text-primary);
            font-size: 12px;
            line-height: 1.38 !important;
            cursor: pointer;
            user-select: none;
            transition: background-color 0.12s ease, border-color 0.12s ease;
        }}
        .tree-child-row::before {{
            content: "";
            position: absolute;
            left: -15px;
            top: 50%;
            width: 12px;
            height: 1.5px;
            background: var(--tree-line-color);
            pointer-events: none;
        }}
        .tree-node-group.has-checked-children .tree-child-row::before {{
            background: var(--tree-line-active);
        }}
        .tree-child-row:hover:not(.is-zero-match) {{
            background: var(--bg-hover);
        }}
        .tree-child-row.is-active {{
            background: var(--bg-active);
            border-color: rgba(37, 99, 235, 0.28) !important;
            color: var(--accent-primary);
            font-weight: 600;
        }}

        /* === REQUIREMENT 4: CLEAN & INTUITIVE USER REPORT MODAL === */
        .ur-top-settings-btn {{
            position: absolute;
            top: 16px;
            right: 54px;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            border: 1px solid var(--border-hairline);
            background: var(--bg-subtle);
            color: var(--text-secondary);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.16s ease;
            z-index: 5;
        }}
        .ur-top-settings-btn:hover,
        .ur-top-settings-btn.is-active {{
            background: var(--bg-active);
            color: var(--accent-primary);
            border-color: var(--accent-primary);
            transform: rotate(25deg);
        }}
        .ur-top-settings-btn svg {{
            width: 16px;
            height: 16px;
            stroke: currentColor;
            fill: none;
            stroke-width: 2;
        }}
        .ur-settings-popover {{
            display: none;
            margin: 0 0 12px;
            padding: 12px 14px;
            border-radius: 12px;
            background: var(--bg-subtle);
            border: 1px solid var(--accent-primary);
            box-shadow: var(--shadow-card);
            animation: geminiSlideDown 0.18s ease;
        }}
        .ur-settings-popover.show {{
            display: block;
        }}

        /* Step 1: Unified Smart Search Combobox + Selected Device Summary Card */
        .ur-smart-combobox {{
            position: relative;
            width: 100%;
        }}
        .ur-smart-input-wrap {{
            position: relative;
            display: flex;
            align-items: center;
            background: var(--bg-surface);
            border: 1.5px solid var(--border-hairline);
            border-radius: 11px;
            padding: 0 12px;
            min-height: 44px;
            transition: all 0.16s ease;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }}
        .ur-smart-input-wrap:focus-within {{
            border-color: var(--accent-primary);
            box-shadow: 0 0 0 3px var(--accent-soft);
        }}
        .ur-smart-search-icon {{
            width: 18px;
            height: 18px;
            color: var(--text-secondary);
            flex-shrink: 0;
            margin-right: 8px;
        }}
        .ur-smart-input {{
            flex: 1;
            border: none !important;
            background: transparent !important;
            color: var(--text-primary) !important;
            font-size: 13px !important;
            font-weight: 500;
            padding: 8px 0;
            min-width: 0;
        }}
        .ur-smart-clear-btn {{
            display: none;
            border: none;
            background: var(--bg-subtle);
            color: var(--text-secondary);
            width: 22px;
            height: 22px;
            border-radius: 50%;
            font-size: 14px;
            line-height: 1;
            cursor: pointer;
            align-items: center;
            justify-content: center;
            margin-left: 6px;
            flex-shrink: 0;
        }}
        .ur-smart-clear-btn:hover {{
            background: var(--bg-hover);
            color: var(--text-primary);
        }}
        .ur-smart-dropdown {{
            display: none;
            position: absolute;
            top: calc(100% + 6px);
            left: 0;
            right: 0;
            max-height: 280px;
            overflow-y: auto;
            background: var(--bg-surface);
            border: 1px solid var(--border-hairline);
            border-radius: 12px;
            box-shadow: var(--shadow-float);
            z-index: 100080;
            padding: 6px;
        }}
        .ur-smart-dropdown.show {{
            display: block;
        }}
        .ur-smart-option {{
            width: 100%;
            display: flex;
            flex-direction: column;
            gap: 4px;
            padding: 9px 12px;
            border-radius: 8px;
            border: 1px solid transparent;
            background: transparent;
            text-align: left;
            cursor: pointer;
            transition: background-color 0.12s ease;
            color: var(--text-primary);
        }}
        .ur-smart-option:hover,
        .ur-smart-option.is-selected {{
            background: var(--bg-active);
            border-color: rgba(37, 99, 235, 0.25);
        }}
        .ur-smart-opt-top {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .ur-smart-opt-id {{
            font-size: 13px;
            font-weight: 700;
            color: var(--text-primary);
        }}
        .ur-smart-opt-cmd {{
            font-size: 11px;
            font-weight: 600;
            padding: 1px 7px;
            border-radius: 5px;
            background: var(--bg-subtle);
            border: 1px solid var(--border-subtle);
            color: var(--accent-primary);
            font-family: 'JetBrains Mono', monospace;
        }}
        .ur-smart-opt-sub {{
            font-size: 11.5px;
            color: var(--text-secondary);
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            align-items: center;
        }}

        /* Selected Device Summary Card in Step 1 */
        .ur-selected-device-card {{
            display: none;
            flex-direction: column;
            gap: 10px;
            padding: 12px 14px;
            border-radius: 12px;
            background: linear-gradient(135deg, var(--bg-active) 0%, var(--bg-surface) 100%);
            border: 1.5px solid rgba(37, 99, 235, 0.38);
            box-shadow: 0 2px 8px rgba(37, 99, 235, 0.08);
            animation: geminiSlideDown 0.18s ease;
        }}
        .ur-selected-device-card.show {{
            display: flex;
        }}
        .ur-sdc-header {{
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 10px;
            flex-wrap: wrap;
        }}
        .ur-sdc-title-group {{
            display: flex;
            flex-direction: column;
            gap: 3px;
            min-width: 0;
        }}
        .ur-sdc-site {{
            font-size: 15px;
            font-weight: 700;
            color: var(--accent-primary);
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .ur-sdc-loc {{
            font-size: 12.5px;
            font-weight: 500;
            color: var(--text-primary);
        }}
        .ur-sdc-change-btn {{
            border: 1px solid var(--border-hairline);
            background: var(--bg-surface);
            color: var(--text-primary);
            border-radius: 8px;
            padding: 5px 11px;
            font-size: 11.5px;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 5px;
            transition: all 0.15s ease;
            white-space: nowrap;
        }}
        .ur-sdc-change-btn:hover {{
            border-color: var(--accent-primary);
            color: var(--accent-primary);
            background: var(--bg-hover);
        }}
        .ur-sdc-meta-pills {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }}
        .ur-sdc-pill {{
            font-size: 11px;
            padding: 3px 9px;
            border-radius: 6px;
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            color: var(--text-secondary);
        }}
        .ur-sdc-pill strong {{
            color: var(--text-primary);
            font-weight: 600;
        }}

        /* Step 2: Field Sub-Cards with Smooth Expandable Input */
        .ur-field-check-card {{
            display: flex;
            flex-direction: column;
            gap: 0 !important;
            padding: 10px 12px !important;
            border-radius: 10px !important;
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle) !important;
            transition: border-color 0.18s ease, background-color 0.18s ease, box-shadow 0.18s ease !important;
        }}
        .ur-field-check-card:hover {{
            border-color: var(--border-hairline) !important;
        }}
        .ur-field-check-card.is-checked {{
            border-color: #ea580c !important;
            background: #fff7ed !important;
            box-shadow: 0 2px 8px rgba(234, 88, 12, 0.12) !important;
        }}
        body.theme-dark .ur-field-check-card.is-checked {{
            border-color: #fb923c !important;
            background: rgba(234, 88, 12, 0.14) !important;
        }}
        .ur-field-input-collapse {{
            max-height: 0;
            opacity: 0;
            overflow: hidden;
            margin-top: 0;
            transition: max-height 0.22s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.18s ease, margin-top 0.18s ease;
        }}
        .ur-field-check-card.is-checked .ur-field-input-collapse {{
            max-height: 68px;
            opacity: 1;
            margin-top: 8px;
        }}
        .ur-field-check-card .ur-field-inline-input {{
            display: block !important;
            width: 100%;
            padding: 7px 10px;
            font-size: 12px;
            border-radius: 8px;
            border: 1.5px solid #fb923c;
            background: var(--bg-surface);
            color: var(--text-primary);
            transition: border-color 0.15s ease, box-shadow 0.15s ease;
        }}
        .ur-field-check-card .ur-field-inline-input:focus {{
            border-color: #ea580c;
            box-shadow: 0 0 0 3px rgba(234, 88, 12, 0.15);
        }}

        /* === BESPOKE HUMAN-CRAFTED UI/UX, ANTI-SQUISH DROPDOWNS, MAP TREE LINES & SUCCESS STATE === */
        body {{
            font-feature-settings: "cv02", "cv03", "cv04", "cv11";
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
            text-rendering: optimizeLegibility;
        }}
        .explorer-toolbar {{
            position: relative;
            z-index: 45;
        }}
        /* Fix Dropdown Squishing: Use block layout inside scroll containers so items never flex-shrink or overlap */
        .ms-menu-scroll-list {{
            display: block !important;
            flex: 1 1 auto !important;
            min-height: 0 !important;
            max-height: 336px !important;
            overflow-y: auto !important;
            overflow-x: hidden !important;
            padding: 8px 10px 12px !important;
            -webkit-overflow-scrolling: touch;
        }}
        .ms-menu-scroll-list > * + * {{
            margin-top: 5px !important;
        }}
        .tree-node-group {{
            display: block !important;
            height: auto !important;
            min-height: 40px !important;
            overflow: visible !important;
            margin-bottom: 6px !important;
        }}
        .tree-node-group.is-expanded .tree-children-list {{
            display: block !important;
            height: auto !important;
            overflow: visible !important;
            margin: 4px 8px 8px 21px !important;
            padding: 4px 0 2px 16px !important;
            border-left: 1.5px solid var(--tree-line-color) !important;
        }}
        .tree-node-group.has-checked-children .tree-children-list {{
            border-left-color: var(--tree-line-active) !important;
        }}
        .tree-child-row {{
            min-height: 36px !important;
            height: auto !important;
            margin-bottom: 3px !important;
            padding: 6px 10px !important;
            line-height: 1.42 !important;
            overflow: visible !important;
        }}
        .tree-child-row::before {{
            content: "";
            position: absolute;
            left: -16px;
            top: 50%;
            width: 13px;
            height: 1.5px;
            background: var(--tree-line-color);
            pointer-events: none;
        }}
        .ms-check-row,
        .ur-smart-option,
        .worker-dropdown-option,
        .g-search-item {{
            flex-shrink: 0 !important;
            min-height: 38px !important;
            height: auto !important;
            line-height: 1.42 !important;
            overflow: visible !important;
            margin-bottom: 3px;
        }}
        /* Map Filter (#customFilterWrapper) Tree Guide Lines */
        .custom-filter-wrapper .leaflet-control-layers-group {{
            display: block !important;
            height: auto !important;
            overflow: visible !important;
            margin: 0 10px 8px !important;
            padding-bottom: 6px !important;
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            background: var(--bg-subtle);
        }}
        .custom-filter-wrapper .leaflet-control-layers-group-label {{
            padding: 4px 8px !important;
            border-bottom: 1px solid var(--border-subtle);
            margin-bottom: 4px !important;
        }}
        .custom-filter-wrapper .leaflet-control-layers-group label:not(.leaflet-control-layers-group-label) {{
            position: relative !important;
            margin-left: 24px !important;
            margin-right: 8px !important;
            width: calc(100% - 32px) !important;
            padding: 7px 10px 7px 14px !important;
            border-left: 1.5px solid var(--tree-line-color) !important;
            border-radius: 0 8px 8px 0 !important;
            min-height: 36px !important;
            height: auto !important;
        }}
        .custom-filter-wrapper .leaflet-control-layers-group label:not(.leaflet-control-layers-group-label)::before {{
            content: "";
            position: absolute;
            left: 0;
            top: 17px;
            width: 10px;
            height: 1.5px;
            background: var(--tree-line-color);
            pointer-events: none;
        }}

        /* User Report Dedicated Success State View */
        .ur-success-state-view {{
            display: none;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            padding: 36px 24px 28px;
            background: var(--bg-surface);
            border-radius: 14px;
            border: 1px solid var(--border-subtle);
            margin: 8px 0 4px;
            animation: geminiSlideDown 0.24s cubic-bezier(0.22, 1, 0.36, 1);
        }}
        .ur-success-state-view.show {{
            display: flex !important;
        }}
        .ur-success-icon-circle {{
            width: 68px;
            height: 68px;
            border-radius: 50%;
            background: rgba(22, 163, 74, 0.12);
            border: 2px solid rgba(22, 163, 74, 0.28);
            color: #16a34a;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 16px;
            box-shadow: 0 8px 24px -4px rgba(22, 163, 74, 0.2);
        }}
        body.theme-dark .ur-success-icon-circle {{
            background: rgba(34, 197, 94, 0.18);
            border-color: rgba(74, 222, 128, 0.35);
            color: #4ade80;
        }}
        .ur-success-icon-circle svg {{
            width: 34px;
            height: 34px;
            stroke: currentColor;
            fill: none;
            stroke-width: 2.6;
            stroke-linecap: round;
            stroke-linejoin: round;
        }}
        .ur-success-title {{
            font-size: 18px;
            font-weight: 700;
            color: var(--text-primary);
            margin: 0 0 8px;
            line-height: 1.35;
        }}
        .ur-success-desc {{
            font-size: 13px;
            color: var(--text-secondary);
            margin: 0 0 16px;
            max-width: 460px;
            line-height: 1.55;
        }}
        .ur-success-summary-chip {{
            display: inline-flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: center;
            gap: 8px;
            padding: 8px 14px;
            border-radius: 10px;
            background: var(--bg-subtle);
            border: 1px solid var(--border-hairline);
            font-size: 12px;
            color: var(--text-primary);
            margin-bottom: 22px;
            max-width: 100%;
        }}
        .ur-success-btn-row {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            justify-content: center;
            width: 100%;
            max-width: 420px;
        }}

        /* Responsive Mobile Bottom-Sheet for Multi-Select Filter Popovers */
        @media (max-width: 768px) {{
            .casc-filter-menu.show,
            .hier-brand-menu.show {{
                position: fixed !important;
                top: auto !important;
                bottom: max(12px, env(safe-area-inset-bottom, 12px)) !important;
                left: 10px !important;
                right: 10px !important;
                width: calc(100vw - 20px) !important;
                max-width: calc(100vw - 20px) !important;
                max-height: 72dvh !important;
                z-index: 100099 !important;
                box-shadow: 0 -4px 32px rgba(0, 0, 0, 0.28), 0 12px 32px rgba(0, 0, 0, 0.32) !important;
            }}
            .ms-menu-scroll-list {{
                max-height: calc(72dvh - 96px) !important;
            }}
            .ur-fields-checklist {{
                grid-template-columns: 1fr !important;
            }}
        }}

        .explorer-search-input:focus,
        .explorer-search-input.is-filter-active {{
            border-color: #1d4ed8 !important;
            background: #eff6ff !important;
            box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.18) !important;
            font-weight: 600;
        }}
        body.theme-dark .explorer-search-input:focus,
        body.theme-dark .explorer-search-input.is-filter-active {{
            border-color: #60a5fa !important;
            background: rgba(37, 99, 235, 0.28) !important;
            color: #ffffff !important;
        }}

        /* === RESPONSIVE MULTI-DEVICE BREAKPOINTS (Apple / Google Maps World-Class Layout: Phone, Tablet, Laptop, PC) === */
        /* 1. Laptop & Medium Desktop (1025px - 1366px) */
        @media (max-width: 1366px) {{
            .g-search-container {{
                width: clamp(240px, 27vw, 340px);
            }}
            .dash-kpi-grid {{
                grid-template-columns: 1.25fr repeat(5, 1fr);
                gap: 8px;
            }}
            .kpi-main-val {{ font-size: 23px; }}
            .dash-kpi-ava .kpi-main-val {{ font-size: 25px; }}
        }}

        /* 2. Tablet Landscape & Small Laptop (769px - 1180px) */
        @media (max-width: 1180px) {{
            .g-search-container {{
                width: clamp(210px, calc(100vw - 440px), 320px);
            }}
            .dash-kpi-grid {{
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 10px;
            }}
            .dash-grid-2col, .dash-grid-equal {{
                grid-template-columns: 1fr;
            }}
            .dash-sticky-inner {{
                padding-right: 290px;
            }}
            body.dashboard-mode-active .top-nav-switcher {{
                position: fixed;
                top: 12px;
                right: 16px;
            }}
        }}

        /* Tablet Portrait (769px - 900px): ซ่อน badge %AVA บนปุ่มสลับหน้าเพื่อประหยัดพื้นที่แถบด้านบน */
        @media (min-width: 769px) and (max-width: 900px) {{
            .nav-ava-pill {{ display: none !important; }}
            .g-search-container {{
                width: calc(100vw - 360px);
            }}
            .dash-sticky-inner {{
                padding-right: 220px;
            }}
        }}

        /* 3. Mobile Phone & Phablet (<= 768px): ใช้เฉพาะ Icon บนแถบสลับหน้า ไม่วางทับปุ่มพิกัดปัจจุบันและประเภทแผนที่ด้านล่าง */
        @media (max-width: 768px) {{
            .g-zoom-container {{ display: none !important; }}

            /* แถบค้นหาด้านซ้ายบน: ขยายเต็มความกว้างอัตโนมัติเมื่อแตะพิมพ์ค้นหา (Google Maps / Apple Maps Style) */
            .g-search-container {{
                top: max(10px, env(safe-area-inset-top, 10px)) !important;
                left: 10px !important;
                width: calc(100vw - 238px) !important;
                transition: width 0.22s cubic-bezier(0.2, 0.8, 0.2, 1) !important;
            }}
            .g-search-container:focus-within {{
                width: calc(100vw - 20px) !important;
                z-index: 100020 !important;
            }}
            .g-search-box {{
                height: 40px !important;
                padding: 0 10px !important;
            }}
            input#searchInput.g-search-input {{
                font-size: 12.5px !important;
                margin-left: 6px !important;
            }}

            /* ปุ่มรายงานและปุ่มตัวกรองมุมขวาบน */
            .top-action-btn {{
                top: max(10px, env(safe-area-inset-top, 10px)) !important;
                width: 40px !important;
                height: 40px !important;
            }}
            .standalone-filter-btn {{ right: 10px !important; }}
            .standalone-report-btn {{ right: 54px !important; }}

            /* แถบสลับหน้า แผนที่/แดชบอร์ด + โหมดมืด/สว่าง บนมือถือ: ใช้เฉพาะ Icon (Icon-Only) และวางด้านบน ไม่ให้ทับปุ่มด้านล่าง */
            body:not(.dashboard-mode-active) .top-nav-switcher {{
                position: fixed !important;
                top: max(10px, env(safe-area-inset-top, 10px)) !important;
                bottom: auto !important;
                left: auto !important;
                right: 98px !important;
                height: 40px !important;
                padding: 3px 4px !important;
                gap: 4px !important;
                z-index: 100008 !important;
            }}
            body.dashboard-mode-active .top-nav-switcher {{
                position: fixed !important;
                top: max(10px, env(safe-area-inset-top, 10px)) !important;
                bottom: auto !important;
                left: auto !important;
                right: 10px !important;
                height: 40px !important;
                padding: 3px 4px !important;
                gap: 4px !important;
                z-index: 100010 !important;
            }}

            /* ซ่อนข้อความในปุ่มสลับหน้าบนมือถือ แสดงเฉพาะไอคอนคมชัดสไตล์ Apple / Google */
            .top-nav-switcher .nav-switch-btn span,
            .top-nav-switcher .nav-ava-pill {{
                display: none !important;
            }}
            .segmented-control {{
                padding: 2px !important;
                gap: 2px !important;
            }}
            .nav-switch-btn {{
                width: 34px !important;
                height: 30px !important;
                padding: 0 !important;
                justify-content: center !important;
                border-radius: 999px !important;
            }}
            .nav-switch-btn svg {{
                width: 16px !important;
                height: 16px !important;
            }}
            .apple-theme-switch {{
                width: 46px !important;
                height: 26px !important;
                padding: 2px !important;
            }}
            .apple-switch-track-icon {{
                width: 20px !important;
                height: 20px !important;
            }}
            .apple-switch-track-icon svg {{
                width: 11.5px !important;
                height: 11.5px !important;
            }}
            .apple-switch-thumb {{
                width: 20px !important;
                height: 20px !important;
            }}
            body.theme-dark .apple-switch-thumb {{
                transform: translateX(20px) !important;
            }}
            .apple-switch-thumb svg {{
                width: 11.5px !important;
                height: 11.5px !important;
            }}

            /* พื้นที่ด้านล่างแผนที่โล่ง 100%: ปุ่มเลือกประเภทแผนที่ (ซ้ายล่าง) และปุ่มพิกัดปัจจุบัน (ขวาล่าง) กดได้ทันทีไม่โดนทับ */
            .g-layer-container {{
                bottom: calc(18px + env(safe-area-inset-bottom, 0px)) !important;
                left: 10px !important;
                z-index: 100002 !important;
            }}
            .g-layer-main-btn {{
                width: 48px !important;
                height: 48px !important;
                border-radius: 12px !important;
            }}
            .g-layer-panel {{
                height: 68px !important;
                margin-left: 8px !important;
                gap: 6px !important;
            }}
            .g-layer-panel.show {{
                max-width: calc(100vw - 128px) !important;
                padding: 0 10px !important;
                overflow-x: auto !important;
                -webkit-overflow-scrolling: touch !important;
            }}
            .g-layer-item {{
                flex: 0 0 auto !important;
                padding: 3px !important;
            }}
            .g-layer-thumb {{
                width: 36px !important;
                height: 36px !important;
            }}
            .custom-right-controls {{
                margin-right: 10px !important;
                margin-bottom: calc(18px + env(safe-area-inset-bottom, 0px)) !important;
                z-index: 100002 !important;
            }}
            .g-locate-btn {{
                width: 44px !important;
                height: 44px !important;
                border-radius: 12px !important;
            }}

            /* Header หน้าแดชบอร์ดบนมือถือ: เว้นที่ด้านขวาให้ปุ่มสลับหน้าแบบ Icon-Only อยู่บรรทัดเดียวกันอย่างสวยงาม */
            body.dashboard-mode-active .dash-sticky-topbar {{
                padding: max(10px, env(safe-area-inset-top, 10px)) 142px 10px 12px !important;
                margin-bottom: 10px !important;
                min-height: 58px !important;
                display: flex !important;
                align-items: center !important;
            }}
            .dash-sticky-inner {{
                padding-right: 0 !important;
                width: 100% !important;
            }}
            .dash-sticky-title {{
                font-size: 14.5px !important;
                line-height: 1.3 !important;
            }}
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

            .custom-filter-wrapper {{ top: calc(max(10px, env(safe-area-inset-top, 10px)) + 46px) !important; right: 10px !important; left: 10px !important; width: auto !important; max-height: calc(100dvh - 110px) !important; }}
            .custom-info-panel {{ top: auto !important; bottom: 0 !important; left: 0 !important; width: 100vw !important; max-width: 100vw !important; max-height: 60vh !important; border-radius: 18px 18px 0 0 !important; padding-bottom: env(safe-area-inset-bottom, 0px); z-index: 100015 !important; }}
            .popup-body {{ max-height: calc(60vh - 120px - env(safe-area-inset-bottom, 0px)) !important; }}
            .date-flex-container {{ flex-direction: column; }}
            .adv-grid-row {{ grid-template-columns: 1fr; }}
            .report-modal {{ padding: 16px; width: calc(100vw - 16px); }}
        }}

        /* 4. จอโทรศัพท์ขนาดเล็กมาก (<= 390px เช่น iPhone SE / Mini) */
        @media (max-width: 390px) {{
            .g-search-container {{
                width: calc(100vw - 226px) !important;
            }}
            .top-action-btn {{
                width: 38px !important;
                height: 38px !important;
            }}
            .standalone-report-btn {{ right: 50px !important; }}
            body:not(.dashboard-mode-active) .top-nav-switcher {{
                right: 92px !important;
                height: 38px !important;
            }}
            .nav-switch-btn {{
                width: 31px !important;
                height: 28px !important;
            }}
            .apple-theme-switch {{
                width: 42px !important;
                height: 24px !important;
            }}
            .apple-switch-thumb {{
                width: 18px !important;
                height: 18px !important;
            }}
            body.theme-dark .apple-switch-thumb {{
                transform: translateX(18px) !important;
            }}
        }}

        /* CHANGED: Refactor สไตล์ Gemini AI Section, ปุ่มตั้งค่าพร้อม Active State และหน้าต่าง Pop-up กรอก API Key */
        .gemini-ai-section {{
            margin: 0 0 12px;
            background: var(--bg-subtle);
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            overflow: visible;
            transition: border-color 0.2s ease, background-color 0.2s ease;
        }}
        .gemini-ai-section.is-open {{
            background: var(--bg-surface);
            border-color: var(--border-hairline);
            box-shadow: 0 2px 10px rgba(0,0,0,0.04);
        }}
        .gemini-collapse-trigger {{
            width: 100%;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            padding: 11px 14px;
            background: transparent;
            border: 0;
            border-radius: 12px;
            color: var(--text-primary);
            cursor: pointer;
            text-align: left;
            transition: background-color 0.15s ease;
        }}
        .gemini-collapse-trigger:hover {{
            background: var(--bg-hover);
        }}
        .gemini-trigger-left {{
            display: flex;
            align-items: center;
            gap: 9px;
            min-width: 0;
            flex-wrap: wrap;
        }}
        .gemini-sparkle-icon {{
            width: 26px;
            height: 26px;
            border-radius: 7px;
            background: var(--bg-surface);
            border: 1px solid var(--border-hairline);
            color: var(--text-secondary);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }}
        .gemini-title-badge {{
            font-size: 12.5px;
            font-weight: 600;
            color: var(--text-primary);
        }}
        .gemini-tier-pill {{
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 2px 8px;
            border-radius: 999px;
            background: var(--bg-surface);
            border: 1px solid var(--border-hairline);
            font-size: 10.5px;
            font-weight: 500;
            color: var(--text-secondary);
        }}
        .gemini-trigger-right {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 11.5px;
            font-weight: 500;
            color: var(--text-secondary);
            white-space: nowrap;
            flex-shrink: 0;
        }}
        .gemini-chevron-icon {{
            width: 18px;
            height: 18px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: var(--text-secondary);
            transition: transform 0.2s ease;
        }}
        .gemini-ai-section.is-open .gemini-chevron-icon {{
            transform: rotate(180deg);
            color: var(--text-primary);
        }}
        .gemini-collapsible-body {{
            display: none;
            padding: 4px 14px 14px;
            border-top: 1px solid var(--border-subtle);
            animation: geminiSlideDown 0.18s ease;
        }}
        .gemini-collapsible-body.show {{
            display: block;
        }}
        @keyframes geminiSlideDown {{
            from {{ opacity: 0; transform: translateY(-3px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}

        .gemini-grid-row {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 10px;
            margin-top: 10px;
            margin-bottom: 10px;
        }}
        .gemini-field {{
            display: flex;
            flex-direction: column;
            gap: 5px;
            min-width: 0;
            position: relative;
        }}
        .gemini-field label {{
            font-size: 11.5px;
            color: var(--text-secondary);
            font-weight: 500;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 6px;
        }}

        /* ปุ่มเครื่องมือ AI ("ตั้งค่า Prompt", "แก้ไขประโยครายแถว", "ระบุ API Key ของตนเอง") พร้อมสถานะ Active State ("กำลังถูกเลือกใช้งานอยู่") */
        .ai-tool-toggle-btn {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            border-radius: 8px;
            background: var(--bg-surface);
            border: 1px solid var(--border-hairline);
            padding: 5px 11px;
            font-size: 11.5px;
            font-weight: 500;
            color: var(--text-primary);
            cursor: pointer;
            transition: all 0.16s ease;
            position: relative;
        }}
        .ai-tool-toggle-btn:hover {{
            background: var(--bg-hover);
            border-color: var(--accent-primary);
        }}
        .ai-tool-toggle-btn.is-active {{
            background: var(--accent-primary) !important;
            color: #ffffff !important;
            border-color: var(--accent-primary) !important;
            font-weight: 600 !important;
            box-shadow: 0 2px 8px rgba(26, 115, 232, 0.28);
        }}
        .ai-tool-toggle-btn .btn-active-dot {{
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: #86efac;
            display: none;
            box-shadow: 0 0 0 2px rgba(255,255,255,0.28);
        }}
        .ai-tool-toggle-btn.is-active .btn-active-dot {{
            display: inline-block;
        }}
        .ai-tool-toggle-btn .btn-active-pill {{
            display: none;
            font-size: 9.5px;
            font-weight: 600;
            padding: 1px 6px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.22);
            color: #ffffff;
            letter-spacing: 0.1px;
        }}
        .ai-tool-toggle-btn.is-active .btn-active-pill {{
            display: inline-flex;
            align-items: center;
        }}

        /* หน้าต่าง Pop-up / Dropdown เล็กๆ สำหรับกรอก API Key ของตนเอง (ซ่อนไว้เป็นค่าเริ่มต้น) */
        .api-key-popover-anchor {{
            position: relative;
            display: inline-flex;
        }}
        .gemini-apikey-popover {{
            display: none;
            position: absolute;
            top: calc(100% + 8px);
            right: 0;
            width: 340px;
            max-width: calc(100vw - 44px);
            background: var(--bg-surface);
            border: 1px solid var(--accent-primary);
            border-radius: 12px;
            padding: 12px 14px;
            box-shadow: 0 12px 32px rgba(0,0,0,0.18), 0 2px 8px rgba(0,0,0,0.08);
            z-index: 10000025;
            animation: geminiSlideDown 0.16s ease;
        }}
        .gemini-apikey-popover.show {{
            display: block;
        }}
        .gemini-apikey-popover-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 8px;
        }}
        .gemini-apikey-popover-title {{
            font-size: 12px;
            font-weight: 600;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .gemini-apikey-status-badge {{
            font-size: 10px;
            font-weight: 600;
            padding: 2px 7px;
            border-radius: 999px;
            background: var(--bg-subtle);
            border: 1px solid var(--border-hairline);
            color: var(--text-secondary);
        }}
        .gemini-apikey-status-badge.has-custom-key {{
            background: #e6f4ea;
            border-color: rgba(19, 115, 51, 0.3);
            color: #137333;
        }}
        body.theme-dark .gemini-apikey-status-badge.has-custom-key {{
            background: rgba(22, 163, 74, 0.22);
            color: #4ade80;
        }}
        .gemini-key-input-wrap {{
            position: relative;
            display: flex;
            align-items: center;
            background: var(--bg-subtle);
            border: 1px solid var(--border-hairline);
            border-radius: 9px;
            transition: border-color 0.15s ease, background-color 0.15s ease;
            min-height: 38px;
            padding: 0 6px 0 10px;
        }}
        .gemini-key-input-wrap:focus-within {{
            background: var(--bg-surface);
            border-color: var(--accent-primary);
        }}
        .gemini-input {{
            width: 100%;
            background: transparent;
            border: none;
            color: var(--text-primary);
            padding: 6px 4px;
            font-size: 12px;
            min-height: 34px;
            letter-spacing: 0.3px;
        }}
        .gemini-key-toggle-btn {{
            border: none;
            background: transparent;
            color: var(--text-secondary);
            width: 28px;
            height: 28px;
            border-radius: 6px;
            padding: 0;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            transition: background-color 0.15s ease, color 0.15s ease;
            flex-shrink: 0;
        }}
        .gemini-key-toggle-btn:hover {{
            color: var(--text-primary);
            background: var(--bg-hover);
        }}
        .gemini-apikey-popover-footer {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-top: 8px;
            font-size: 10.5px;
            color: var(--text-secondary);
        }}

        /* Model Dropdown Menu สไตล์ Minimalist & Clean */
        .model-selector-custom {{
            position: relative;
            width: 100%;
        }}
        .model-selector-trigger {{
            width: 100%;
            min-height: 42px;
            padding: 6px 12px;
            background: var(--bg-subtle);
            border: 1px solid var(--border-hairline);
            border-radius: 9px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            cursor: pointer;
            text-align: left;
            transition: border-color 0.15s ease, background-color 0.15s ease;
        }}
        .model-selector-trigger:hover,
        .model-selector-custom.is-open .model-selector-trigger {{
            background: var(--bg-surface);
            border-color: var(--accent-primary);
        }}
        .model-trigger-main {{
            display: flex;
            align-items: center;
            gap: 8px;
            min-width: 0;
            flex: 1;
        }}
        .model-trigger-icon {{
            width: 24px;
            height: 24px;
            border-radius: 6px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: var(--text-secondary);
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            flex-shrink: 0;
        }}
        .model-trigger-icon svg {{
            width: 14px;
            height: 14px;
            stroke: currentColor;
            fill: none;
            stroke-width: 1.8;
        }}
        .model-trigger-text {{
            display: flex;
            flex-direction: column;
            min-width: 0;
        }}
        .model-trigger-name {{
            font-size: 12.5px;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.25;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .model-trigger-desc {{
            font-size: 10.5px;
            color: var(--text-secondary);
            line-height: 1.2;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .model-trigger-badge {{
            font-size: 10.5px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 999px;
            background: #e6f4ea;
            color: #137333;
            white-space: nowrap;
            flex-shrink: 0;
        }}
        .model-trigger-badge.is-warn {{
            background: #fef0e6;
            color: #c25100;
        }}
        .model-trigger-badge.is-full {{
            background: #fce8e6;
            color: #c5221f;
        }}
        body.theme-dark .model-trigger-badge {{
            background: rgba(22,163,74,0.2);
            color: #4ade80;
        }}

        /* Floating Model Selector Popover Menu */
        .model-selector-menu {{
            display: none;
            position: absolute;
            top: calc(100% + 5px);
            left: 0;
            width: 100%;
            min-width: 320px;
            background: var(--bg-surface);
            border: 1px solid var(--border-hairline);
            border-radius: 11px;
            box-shadow: 0 10px 28px rgba(0,0,0,0.14), 0 2px 6px rgba(0,0,0,0.06);
            padding: 5px;
            z-index: 10000020;
            flex-direction: column;
            gap: 2px;
            animation: geminiSlideDown 0.15s ease;
        }}
        .model-selector-custom.is-open .model-selector-menu {{
            display: flex;
        }}
        .model-option-card {{
            position: relative;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            padding: 8px 10px;
            border-radius: 8px;
            border: 1px solid transparent;
            background: transparent;
            cursor: pointer;
            text-align: left;
            transition: background-color 0.14s ease;
        }}
        .model-option-card:hover:not(.is-disabled) {{
            background: var(--bg-hover);
        }}
        .model-option-card.is-selected {{
            background: var(--bg-subtle);
        }}
        .model-option-card.is-disabled {{
            opacity: 0.55;
            cursor: not-allowed !important;
        }}
        .model-opt-left {{
            display: flex;
            align-items: center;
            gap: 9px;
            min-width: 0;
            flex: 1;
        }}
        .model-opt-icon {{
            width: 26px;
            height: 26px;
            border-radius: 6px;
            background: transparent;
            border: 1px solid var(--border-hairline);
            color: var(--text-secondary);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }}
        .model-opt-icon svg {{
            width: 14px;
            height: 14px;
            stroke: currentColor;
            fill: none;
            stroke-width: 1.8;
        }}
        .model-option-card.is-selected .model-opt-icon {{
            color: var(--text-primary);
            border-color: var(--text-secondary);
            background: var(--bg-surface);
        }}
        .model-opt-info {{
            display: flex;
            flex-direction: column;
            gap: 1px;
            min-width: 0;
        }}
        .model-opt-title-row {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .model-opt-title {{
            font-size: 12.5px;
            font-weight: 700;
            color: var(--text-primary);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .model-opt-sub {{
            font-size: 11px;
            color: var(--text-secondary);
            line-height: 1.3;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .model-opt-right {{
            display: flex;
            align-items: center;
            justify-content: flex-end;
            gap: 8px;
            flex-shrink: 0;
            margin-left: auto;
        }}
        .model-opt-quota {{
            font-size: 10.5px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 999px;
            background: #e6f4ea;
            color: #137333;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
        }}
        .model-opt-quota.is-warn {{
            background: #fef0e6;
            color: #c25100;
        }}
        .model-opt-quota.is-full {{
            background: #fce8e6;
            color: #c5221f;
        }}
        body.theme-dark .model-opt-quota {{
            background: rgba(22,163,74,0.2);
            color: #4ade80;
        }}
        body.theme-dark .model-opt-quota.is-full {{
            background: rgba(220,38,38,0.22);
            color: #f87171;
        }}
        .model-opt-check {{
            width: 16px;
            height: 16px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: var(--accent-primary);
            flex-shrink: 0;
        }}
        .model-opt-check svg {{
            width: 15px;
            height: 15px;
            stroke: currentColor;
            fill: none;
            stroke-width: 2.3;
        }}

        /* Hover Tooltip on Disabled Model Option Card */
        .model-opt-tooltip {{
            visibility: hidden;
            opacity: 0;
            position: absolute;
            bottom: calc(100% + 6px);
            left: 12px;
            right: 12px;
            background: #1e293b;
            color: #f8fafc;
            padding: 7px 10px;
            border-radius: 8px;
            font-size: 11px;
            font-weight: 500;
            line-height: 1.4;
            box-shadow: 0 8px 20px rgba(0,0,0,0.28);
            border: 1px solid rgba(255,255,255,0.14);
            z-index: 10000030;
            pointer-events: none;
            transform: translateY(4px);
            transition: opacity 0.16s ease, transform 0.16s ease, visibility 0.16s;
        }}
        .model-option-card.is-disabled:hover .model-opt-tooltip {{
            visibility: visible;
            opacity: 1;
            transform: translateY(0);
        }}

        /* Engineering Prompt Editor Box inside Gemini Section */
        .gemini-prompt-box {{
            display: none;
            margin-top: 10px;
            padding: 10px 12px;
            border-radius: 10px;
            background: var(--bg-subtle);
            border: 1px solid var(--accent-primary);
        }}
        .gemini-prompt-box.show {{
            display: block;
        }}
        .gemini-prompt-textarea {{
            width: 100%;
            min-height: 110px;
            border: 1px solid var(--border-hairline);
            border-radius: 8px;
            padding: 8px 10px;
            font-size: 11.5px;
            color: var(--text-primary);
            background: var(--bg-surface);
            line-height: 1.5;
            resize: vertical;
            margin-top: 6px;
        }}
        .gemini-prompt-textarea:focus {{
            border-color: var(--accent-primary);
        }}

        /* Hover Tooltip Wrapper สำหรับปุ่มสรุปและ Export รายงาน */
        .report-btn-tooltip-wrap {{
            position: relative;
            width: 100%;
            display: flex;
        }}
        .report-btn-tooltip-wrap .report-btn {{
            width: 100%;
        }}
        .report-btn.is-disabled-limit,
        .report-btn:disabled.is-disabled-limit {{
            background: #9aa0a6 !important;
            color: #f1f3f4 !important;
            border: 1px solid #80868b !important;
            cursor: not-allowed !important;
            opacity: 0.78 !important;
            box-shadow: none !important;
            pointer-events: none !important;
        }}
        .gemini-hover-tooltip {{
            visibility: hidden;
            opacity: 0;
            position: absolute;
            bottom: calc(100% + 10px);
            right: 0;
            min-width: 280px;
            max-width: 360px;
            background: #202124;
            color: #ffffff;
            text-align: left;
            padding: 10px 12px;
            border-radius: 10px;
            font-size: 11.5px;
            font-weight: 500;
            line-height: 1.45;
            box-shadow: 0 6px 20px rgba(0,0,0,0.35);
            border: 1px solid rgba(255,255,255,0.16);
            z-index: 10000010;
            pointer-events: none;
            transform: translateY(4px);
            transition: opacity 0.16s ease, transform 0.16s ease, visibility 0.16s;
        }}
        .gemini-hover-tooltip::after {{
            content: "";
            position: absolute;
            top: 100%;
            right: 28px;
            border-width: 6px;
            border-style: solid;
            border-color: #202124 transparent transparent transparent;
        }}
        .report-btn-tooltip-wrap:hover .gemini-hover-tooltip.has-tooltip {{
            visibility: visible !important;
            opacity: 1 !important;
            transform: translateY(0) !important;
        }}

        /* Sentence Manager & Editor Panel before Export */
        .sentence-manager-box {{
            display: none;
            margin: 0 0 12px;
            padding: 12px 14px;
            border-radius: 12px;
            background: var(--bg-surface);
            border: 1px solid var(--accent-primary);
            box-shadow: var(--shadow-card);
        }}
        .sentence-manager-box.show {{
            display: block;
        }}
        .sentence-mgr-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            flex-wrap: wrap;
            margin-bottom: 10px;
            padding-bottom: 8px;
            border-bottom: 1px solid var(--border-subtle);
        }}
        .sentence-mgr-title {{
            font-size: 13px;
            font-weight: 600;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .sentence-mgr-list {{
            max-height: 260px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 8px;
            padding-right: 4px;
        }}
        .sentence-mgr-item {{
            background: var(--bg-subtle);
            border: 1px solid var(--border-subtle);
            border-radius: 8px;
            padding: 8px 10px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}
        .sentence-mgr-item-top {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            font-size: 11.5px;
            font-weight: 600;
            color: var(--accent-primary);
        }}
        .sentence-mgr-orig {{
            font-size: 11px;
            color: var(--text-secondary);
            background: var(--bg-surface);
            padding: 4px 8px;
            border-radius: 6px;
            border: 1px dashed var(--border-hairline);
        }}
        .sentence-mgr-textarea {{
            width: 100%;
            min-height: 54px;
            border: 1px solid var(--border-hairline);
            border-radius: 6px;
            padding: 6px 8px;
            font-size: 12px;
            color: var(--text-primary);
            background: var(--bg-surface);
            resize: vertical;
            line-height: 1.45;
        }}
        .sentence-mgr-textarea:focus {{
            border-color: var(--accent-primary);
        }}

        /* AI Executive Summary Preview Box in Modal */
        .ai-summary-preview-box {{
            display: none;
            margin: 0 0 12px;
            padding: 12px 14px;
            border-radius: 10px;
            background: var(--bg-surface);
            border: 1px solid var(--accent-primary);
            max-height: 220px;
            overflow-y: auto;
            font-size: 12px;
            line-height: 1.55;
            color: var(--text-primary);
            white-space: pre-wrap;
        }}
        .ai-summary-preview-box.show {{
            display: block;
        }}
        .ai-summary-preview-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 6px;
            padding-bottom: 6px;
            border-bottom: 1px solid var(--border-subtle);
            font-weight: 600;
            color: var(--accent-primary);
        }}
    </style>

        <!-- แถบสลับหน้า แผนที่ (Maps) <-> แดชบอร์ด (%AVA) แบบ Segmented Control + สวิตช์โหมดมืด/สว่างสไตล์ Apple -->
        <div class="top-nav-switcher" id="topNavSwitcher">
            <div class="segmented-control" role="tablist" aria-label="สลับมุมมองหลัก">
                <button type="button" class="nav-switch-btn is-active" id="navBtnMap" title="แผนที่ (Map View)" aria-label="แผนที่" onclick="window.switchMainView('map'); return false;">
                    <svg viewBox="0 0 24 24"><polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/><line x1="8" y1="2" x2="8" y2="18"/><line x1="16" y1="6" x2="16" y2="22"/></svg>
                    <span>แผนที่</span>
                </button>
                <button type="button" class="nav-switch-btn" id="navBtnDash" title="แดชบอร์ด (%AVA Dashboard)" aria-label="แดชบอร์ด %AVA" onclick="window.switchMainView('dashboard'); return false;">
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

        <!-- หน้า Dashboard ภาพรวมระบบ SCADA NE2 (%AVA) -->
        <div id="scadaDashboardView" class="scada-dashboard-view" aria-label="SCADA Availability Dashboard">
            <div class="dash-sticky-topbar">
                <div class="dash-sticky-inner">
                    <h1 class="dash-sticky-title">รายงานความพร้อมใช้งานอุปกรณ์ในระบบ SCADA NE2 (%AVA)</h1>
                </div>
            </div>

            <div class="dash-container">
                <div class="dash-toolbar-card">
                    <div class="dash-controls-bar">
                        <p class="dash-ref-plain-text">สถานะอ้างอิง: <strong id="dashStateBasis">State SCADA</strong></p>
                    </div>
                    <div class="dash-controls-bar">
                        <button type="button" class="dash-action-btn btn-report-issue" id="openUserReportModalBtn" onclick="window.openUserReportModal()">
                            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>
                            Report ข้อมูลไม่ถูกต้อง
                        </button>
                        <button type="button" class="dash-action-btn btn-primary" onclick="window.openDashExportModal()">
                            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg>
                            Export ข้อมูล (.xlsx)
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
                                        <option value="comm_group" selected>กลุ่มสื่อสารหลัก (Cellular Router / DAU)</option>
                                        <option value="commu_item">Commu. &bull; Item</option>
                                        <option value="type">ชนิดสื่อสารย่อย (Commu.)</option>
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
                            <button type="button" id="showAllExplorerRowsBtn" class="dash-action-btn" onclick="window.showAllExplorerRows()">แสดงครบทุกแถว</button>
                            <button type="button" id="expResetFilterBtn" class="dash-action-btn btn-clear-filter" onclick="window.resetDashExplorerFilters()" title="ล้างตัวกรองทั้งหมดให้กลับเป็นค่าเริ่มต้น">
                                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M3 6h18M6 12h12m-9 6h6"/></svg>
                                <span class="btn-clear-filter-text">ล้างตัวกรอง</span>
                            </button>
                            <button type="button" class="dash-action-btn" onclick="document.getElementById('scadaDashboardView').scrollTo({{top: 0, behavior: 'smooth'}})">↑ บนสุด</button>
                        </div>
                    </div>

                    <!-- ค่าสรุปผลจากการกรอง (Filtered Summary Badges) แสดงเฉพาะตรงหัวข้อที่ 6 -->
                    <div id="dashFilteredSummaryBar" class="filtered-summary-strip">
                        <div class="filtered-summary-lead">
                            <span class="filtered-summary-tag" id="filteredSummaryModeTag">ค่าตามเงื่อนไข Filter</span>
                            <span id="filteredSummaryAvaBadge"></span>
                        </div>
                        <div class="filtered-summary-badges" id="filteredSummaryBadgesWrap"></div>
                    </div>

                    <div id="dashActiveFilterBar" class="explorer-active-filters" style="display:none;"></div>

                    <div class="explorer-toolbar">
                        <input type="search" id="dashExplorerSearch" class="explorer-search-input" placeholder="ค้นหา Site ID, รหัสสั่งการ, สถานที่, การไฟฟ้า, IP, DNP, ผลิตภัณฑ์, Commu., Item, WRL Master..." oninput="window.onExplorerSearchInput()">

                        <div class="dash-ctrl-group casc-filter-wrap" id="expPeaFilterGroup">
                            <label>การไฟฟ้า:</label>
                            <button type="button" class="casc-filter-trigger" id="expPeaTrigger" onclick="window.toggleCascFilterMenu('pea', event)">
                                <span class="casc-label-txt" id="expPeaTriggerLabel">ทุกการไฟฟ้า</span>
                                <span style="font-size:10px;color:var(--text-secondary);">▾</span>
                            </button>
                            <div class="casc-filter-menu" id="expPeaCascMenu" onclick="event.stopPropagation()"></div>
                            <select id="dashExplorerPea" class="dash-select" style="display:none;" onchange="window.onExplorerPeaDropdownChange(this.value)">
                                <option value="all">ทุกการไฟฟ้า</option>
                            </select>
                        </div>

                        <div class="dash-ctrl-group casc-filter-wrap" id="expStatusFilterGroup">
                            <label>State SCADA:</label>
                            <button type="button" class="casc-filter-trigger" id="expStatusTrigger" onclick="window.toggleCascFilterMenu('status', event)">
                                <span class="casc-label-txt" id="expStatusTriggerLabel">ทุกสถานะ</span>
                                <span style="font-size:10px;color:var(--text-secondary);">▾</span>
                            </button>
                            <div class="casc-filter-menu" id="expStatusCascMenu" onclick="event.stopPropagation()"></div>
                            <select id="dashExplorerStatus" class="dash-select" style="display:none;" onchange="window.onExplorerDropdownChange()">
                                <option value="all">ทุกสถานะ</option>
                                <option value="ALL_OFFLINE">ไม่ออนไลน์ทั้งหมด</option>
                                <option value="Online">Online</option>
                                <option value="Connecting">Connecting</option>
                                <option value="Offline">Offline</option>
                                <option value="Initializing">Initializing</option>
                                <option value="Telemetry Failure">Telemetry Failure</option>
                            </select>
                        </div>

                        <div class="dash-ctrl-group casc-filter-wrap" id="expEqFilterGroup">
                            <label>ชนิดอุปกรณ์:</label>
                            <button type="button" class="casc-filter-trigger" id="expEqTrigger" onclick="window.toggleCascFilterMenu('eq', event)">
                                <span class="casc-label-txt" id="expEqTriggerLabel">ทุกชนิดอุปกรณ์</span>
                                <span style="font-size:10px;color:var(--text-secondary);">▾</span>
                            </button>
                            <div class="casc-filter-menu" id="expEqCascMenu" onclick="event.stopPropagation()"></div>
                            <select id="dashExplorerEq" class="dash-select" style="display:none;" onchange="window.onExplorerDropdownChange()">
                                <option value="all">ทุกชนิดอุปกรณ์</option>
                            </select>
                        </div>

                        <div class="dash-ctrl-group hier-brand-filter" id="expHierBrandFilterWrap">
                            <label>ผลิตภัณฑ์ (ยี่ห้อ):</label>
                            <button type="button" class="hier-brand-trigger" id="expHierBrandTrigger" onclick="window.toggleHierBrandMenu('exp', event)">
                                <span id="expHierBrandLabel">ทุกผลิตภัณฑ์</span>
                                <span style="font-size:10px;color:var(--text-secondary);">▾</span>
                            </button>
                            <div class="hier-brand-menu align-right" id="expHierBrandMenu" onclick="event.stopPropagation()"></div>
                            <select id="dashExplorerProd" class="dash-select" style="display:none;" onchange="window.onExplorerDropdownChange()">
                                <option value="all">ทุกผลิตภัณฑ์</option>
                            </select>
                        </div>

                        <div class="dash-ctrl-group casc-filter-wrap" id="expCommFilterGroup">
                            <label>Commu./Item:</label>
                            <button type="button" class="casc-filter-trigger" id="expCommTrigger" onclick="window.toggleCascFilterMenu('comm', event)">
                                <span class="casc-label-txt" id="expCommTriggerLabel">ทุกประเภทสื่อสาร</span>
                                <span style="font-size:10px;color:var(--text-secondary);">▾</span>
                            </button>
                            <div class="casc-filter-menu align-right" id="expCommCascMenu" onclick="event.stopPropagation()"></div>
                            <select id="dashExplorerComm" class="dash-select" style="display:none;" onchange="window.onExplorerDropdownChange()">
                                <option value="all">ทุกประเภทสื่อสาร</option>
                            </select>
                        </div>

                        <div class="dash-ctrl-group casc-filter-wrap" id="expWrlFilterGroup">
                            <label>WRL Master:</label>
                            <button type="button" class="casc-filter-trigger" id="expWrlTrigger" onclick="window.toggleCascFilterMenu('wrl', event)">
                                <span class="casc-label-txt" id="expWrlTriggerLabel">ทุกแม่ข่าย</span>
                                <span style="font-size:10px;color:var(--text-secondary);">▾</span>
                            </button>
                            <div class="casc-filter-menu align-right" id="expWrlCascMenu" onclick="event.stopPropagation()"></div>
                            <select id="dashExplorerWrl" class="dash-select" style="display:none;" onchange="window.onExplorerDropdownChange()">
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
                                    <th>แผนที่ / แจ้งแก้ไข</th>
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

                <!-- CHANGED: ส่วนตั้งค่า AI Model Selector (ฉลาดที่สุดเป็นอันดับแรก) และปุ่มตั้งค่าพร้อม Pop-up API Key ที่ซ่อนค่าเริ่มต้น -->
                <section class="gemini-ai-section" id="geminiAiSectionCard" aria-labelledby="geminiAiHeading">
                    <button type="button" class="gemini-collapse-trigger" id="geminiCollapseBtn" onclick="toggleGeminiAiPanel()" aria-expanded="false">
                        <div class="gemini-trigger-left">
                            <span class="gemini-sparkle-icon">
                                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3l1.9 5.8a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-1.9a2 2 0 0 0 1.3-1.3L12 3z"/></svg>
                            </span>
                            <span class="gemini-title-badge" id="geminiAiHeading">
                                สรุปและเรียบเรียงรายงานด้วย Gemini AI
                            </span>
                            <span class="gemini-tier-pill" id="geminiCollapsedSummaryPill">Gemini 3.5 Pro · โมเดลหลัก (ฉลาดที่สุด)</span>
                        </div>
                        <div class="gemini-trigger-right">
                            <span id="geminiCollapseLabel">ตั้งค่า AI</span>
                            <span class="gemini-chevron-icon">
                                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
                            </span>
                        </div>
                    </button>

                    <div class="gemini-collapsible-body" id="geminiCollapsibleBody">
                        <div class="gemini-grid-row">
                            <!-- Model Dropdown Menu: เริ่มต้นที่โมเดลที่ฉลาดที่สุด (gemini-3.5-pro-exp) พร้อม Auto Fallback ไปยังโมเดลที่ฉลาดรองลงมา -->
                            <div class="gemini-field">
                                <label>
                                    <span>โมเดล AI (เรียงลำดับจากฉลาดที่สุด &rarr; ฉลาดรองลงมา)</span>
                                    <span id="geminiSelectedQuotaText" style="color:var(--text-secondary);font-weight:500;">พร้อมใช้งาน</span>
                                </label>
                                <input type="hidden" id="geminiModelSelect" value="gemini-3.5-pro-exp">
                                <div class="model-selector-custom" id="geminiCustomModelSelector">
                                    <button type="button" class="model-selector-trigger" id="geminiCustomModelTrigger" onclick="toggleCustomModelMenu(event)" aria-haspopup="listbox" aria-expanded="false">
                                        <div class="model-trigger-main">
                                            <span class="model-trigger-icon" id="geminiTriggerIcon">
                                                <svg viewBox="0 0 24 24"><path d="M12 3l1.9 5.8a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-1.9a2 2 0 0 0 1.3-1.3L12 3z"/></svg>
                                            </span>
                                            <div class="model-trigger-text">
                                                <span class="model-trigger-name" id="geminiTriggerName">Gemini 3.5 Pro (Exp)</span>
                                                <span class="model-trigger-desc" id="geminiTriggerDesc">อันดับ 1 · โมเดลวิเคราะห์ขั้นสูงที่ฉลาดที่สุด (สลับอัตโนมัติเมื่อโควต้าเต็ม)</span>
                                            </div>
                                        </div>
                                        <span class="model-trigger-badge" id="geminiTriggerBadge">ว่าง · 50 RPD</span>
                                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="var(--text-secondary)" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
                                    </button>
                                    <div class="model-selector-menu" id="geminiCustomModelMenu" role="listbox" aria-label="รายการโมเดล Gemini AI"></div>
                                </div>
                            </div>
                        </div>

                        <!-- แถบปุ่มตั้งค่าพร้อมสถานะ Active State ("กำลังเลือกใช้งาน") และ Pop-up ช่องกรอก API Key ที่ซ่อนเป็นค่าเริ่มต้น -->
                        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-top:6px;">
                            <label style="display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-primary);cursor:pointer;">
                                <input type="checkbox" id="enableGeminiAiCheckbox" checked style="width:15px;height:15px;accent-color:var(--accent-primary);cursor:pointer;">
                                <span>เรียบเรียงแบบวิศวกรรม <strong>เนื่องจาก... (สาเหตุ) จึงได้ดำเนินการ... (การดำเนินการ) ส่งผลให้... (ผลลัพธ์)</strong></span>
                            </label>
                            <div style="display:inline-flex;align-items:center;gap:6px;flex-wrap:wrap;">
                                <button type="button" class="ai-tool-toggle-btn" id="openPromptSettingBtn" aria-pressed="false" onclick="toggleEngineeringPromptBox()" title="ตั้งค่า System Prompt สำหรับเขียนรายงานแบบวิศวกรรม">
                                    <span class="btn-active-dot"></span>
                                    <span>ตั้งค่า Prompt</span>
                                    <span class="btn-active-pill">กำลังเลือกใช้งาน</span>
                                </button>
                                <button type="button" class="ai-tool-toggle-btn" id="openSentenceMgrBtn" aria-pressed="false" onclick="toggleSentenceManagerBox()" title="เปิดหน้าต่างแก้ไขประโยครายแถวก่อน Export">
                                    <span class="btn-active-dot"></span>
                                    <span>แก้ไขประโยครายแถว</span>
                                    <span class="btn-active-pill">กำลังเลือกใช้งาน</span>
                                </button>

                                <!-- ปุ่มระบุ API Key ของตนเอง พร้อมหน้าต่าง Pop-up/Dropdown เล็กๆ (ซ่อนช่องกรอก API Key ไว้เป็นค่าเริ่มต้น และห้ามแสดง Default Key เด็ดขาด) -->
                                <div class="api-key-popover-anchor" id="geminiApiKeyPopoverAnchor">
                                    <button type="button" class="ai-tool-toggle-btn" id="openCustomApiKeyBtn" aria-pressed="false" onclick="toggleApiKeyPopover(event)" title="ระบุ Gemini API Key ของตนเอง (ซ่อนช่องกรอกเป็นค่าเริ่มต้น)">
                                        <span class="btn-active-dot"></span>
                                        <span id="openCustomApiKeyBtnText">ระบุ API Key ของตนเอง</span>
                                        <span class="btn-active-pill">กำลังเลือกใช้งาน</span>
                                    </button>
                                    <div id="geminiApiKeyPopover" class="gemini-apikey-popover" role="dialog" aria-label="หน้าต่างกรอก API Key ของตนเอง">
                                        <div class="gemini-apikey-popover-header">
                                            <span class="gemini-apikey-popover-title">
                                                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="var(--accent-primary)" stroke-width="2"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>
                                                ระบุ API Key ของตนเอง
                                            </span>
                                            <span id="geminiKeyStatusBadge" class="gemini-apikey-status-badge">ยังไม่ได้ระบุ</span>
                                        </div>
                                        <div class="gemini-key-input-wrap">
                                            <input type="password" id="geminiApiKeyInput" class="gemini-input" value="" placeholder="วาง Gemini API Key ของคุณที่นี่..." autocomplete="off" oninput="saveUserGeminiApiKey(this.value)">
                                            <button type="button" class="gemini-key-toggle-btn" id="geminiKeyToggleBtn" onclick="toggleGeminiKeyVisibility()" title="แสดง/ซ่อน API Key ที่คุณกรอก" aria-label="แสดงหรือซ่อน API Key">
                                                <svg id="geminiKeyEyeIcon" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                                                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                                                    <circle cx="12" cy="12" r="3"></circle>
                                                </svg>
                                            </button>
                                        </div>
                                        <div class="gemini-apikey-popover-footer">
                                            <span>เว้นว่างได้หากต้องการใช้โควต้าระบบหลังบ้าน</span>
                                            <div style="display:inline-flex;gap:5px;">
                                                <button type="button" class="report-mini-btn" onclick="clearUserGeminiApiKey()">ล้างค่า</button>
                                                <button type="button" class="report-mini-btn" style="background:var(--accent-primary);color:#fff;border-color:var(--accent-primary);" onclick="closeApiKeyPopover()">ตกลง</button>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                <button type="button" class="report-mini-btn" id="previewAiSummaryBtn" onclick="runGeminiPreviewOnly()" style="color:var(--accent-primary);border-color:rgba(26,115,232,0.35);font-weight:500;">
                                    พรีวิวสรุป AI
                                </button>
                            </div>
                        </div>

                        <!-- กล่องดูและปรับแต่ง System Prompt โครงสร้างวิศวกรรม 3 ส่วน -->
                        <div id="engineeringPromptBox" class="gemini-prompt-box">
                            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;">
                                <span style="font-size:11.5px;font-weight:600;color:var(--accent-primary);">
                                    System Prompt มาตรฐานวิศวกรรม (เนื่องจากสาเหตุ &rarr; จึงได้ดำเนินการ &rarr; ส่งผลให้ผลลัพธ์)
                                </span>
                                <div style="display:inline-flex;gap:6px;">
                                    <button type="button" class="report-mini-btn" onclick="resetEngineeringPromptDefault()">คืนค่าเริ่มต้น</button>
                                    <button type="button" class="report-mini-btn" onclick="toggleEngineeringPromptBox(false)">ปิด</button>
                                </div>
                            </div>
                            <textarea id="geminiCustomPromptInput" class="gemini-prompt-textarea"></textarea>
                        </div>
                    </div>
                </section>

                <!-- หน้าต่างจัดการและแก้ไขประโยครายแถวก่อนกดปุ่ม Export -->
                <div id="sentenceManagerBox" class="sentence-manager-box">
                    <div class="sentence-mgr-header">
                        <span class="sentence-mgr-title">
                            จัดการประโยครายละเอียดการตรวจสอบ (แก้ไขก่อนกด Export)
                        </span>
                        <div style="display:inline-flex;gap:6px;flex-wrap:wrap;">
                            <button type="button" class="report-mini-btn" onclick="applySmartCategorizeToAllRows()" title="จัดรูปแบบประโยควิศวกรรมทุกแถวอัตโนมัติ">จัดรูปแบบทุกแถว</button>
                            <button type="button" class="report-mini-btn" onclick="resetCustomRowSentences()" title="คืนค่าข้อความต้นฉบับ">คืนค่าต้นฉบับ</button>
                            <button type="button" class="report-mini-btn" onclick="toggleSentenceManagerBox(false)">ปิด</button>
                        </div>
                    </div>
                    <div id="sentenceManagerList" class="sentence-mgr-list"></div>
                </div>

                <!-- กล่องพรีวิวผลสรุปภาพรวมจาก Gemini API ก่อนกดดาวน์โหลด -->
                <div id="aiSummaryPreviewBox" class="ai-summary-preview-box">
                    <div class="ai-summary-preview-header">
                        <span id="aiSummaryPreviewTitle">ตัวอย่างรายงานสรุปภาพรวมแยกหมวดหมู่ (Gemini AI)</span>
                        <button type="button" class="report-mini-btn" onclick="document.getElementById('aiSummaryPreviewBox').classList.remove('show')">ซ่อนพรีวิว</button>
                    </div>
                    <div id="aiSummaryPreviewContent"></div>
                </div>

                <!-- ปุ่ม "รีเซ็ตค่า" และ "ยกเลิก" เป็น Ghost/Outline สีเทา ส่วนปุ่ม "สรุปและ Export รายงาน" โดดเด่นที่สุดอยู่ด้านล่างสุด (Primary Blue) -->
                <div class="report-btn-group">
                    <div class="report-btn-secondary-row">
                        <button class="report-btn report-btn-cancel" type="button" onclick="resetAllReportFilters()">รีเซ็ตค่า</button>
                        <button class="report-btn report-btn-cancel" type="button" onclick="closeReportModal()">ยกเลิก</button>
                    </div>
                    <div id="execReportBtnWrap" class="report-btn-tooltip-wrap">
                        <button id="execReportBtn" class="report-btn report-btn-dl" type="button" onclick="generateExcelReport()">
                            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="white" stroke-width="2"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg> สรุปและ Export รายงาน (.xlsx)
                        </button>
                        <div id="execReportBtnTooltip" class="gemini-hover-tooltip" role="tooltip"></div>
                    </div>
                </div>
                            <p class="report-note">* ไฟล์รายงานตั้งค่าหน้ากระดาษขนาด A4 แนวนอน (Fit to 1 Page Wide) พร้อมพิมพ์ทันที</p>
            </div>
            <div id="reportWorkerDropdown" class="report-worker-dropdown" role="listbox" aria-label="ผลการค้นหาผู้ปฏิบัติงาน"></div>
        </div>

        <!-- หน้าต่าง Export ข้อมูลของหน้า Dashboard (เลือกเนื้อหาและรายละเอียดที่ต้องการส่งออก) -->
        <div id="dashExportModalOverlay" class="report-modal-overlay">
            <div class="report-modal" role="dialog" aria-modal="true" aria-labelledby="dashExportModalTitle">
                <button class="report-close-btn" type="button" onclick="window.closeDashExportModal()" aria-label="ปิดหน้าต่าง">×</button>
                <div class="report-modal-top">
                    <div class="report-modal-icon"><svg viewBox="0 0 24 24"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg></div>
                    <div>
                        <div class="report-eyebrow-row">
                            <p class="report-eyebrow">CUSTOM DATA EXPORT (.XLSX)</p>
                            <span class="report-source-badge" id="dashExportScopeBadge">Dashboard Data</span>
                        </div>
                        <h3 id="dashExportModalTitle">Export ข้อมูลความพร้อมใช้งานระบบ SCADA</h3>
                        <p class="report-subtitle">เลือกหัวข้อตารางสรุป ขอบเขตข้อมูล และคอลัมน์รายละเอียดที่ต้องการส่งออกเป็นไฟล์ Excel (.xlsx)</p>
                    </div>
                </div>

                <section class="report-section">
                    <div class="report-section-heading">
                        <div class="report-section-title-wrap">
                            <span class="report-section-title">1. เลือกตารางข้อมูลที่ต้องการส่งออก (Sheets)</span>
                            <span class="report-section-help">เลือกได้หลายตารางในไฟล์เดียวกัน</span>
                        </div>
                        <div class="report-footer-btns">
                            <button type="button" class="report-mini-btn" onclick="window.setAllDashExportSheets(true)">เลือกทั้งหมด</button>
                            <button type="button" class="report-mini-btn" onclick="window.setAllDashExportSheets(false)">ล้างที่เลือก</button>
                        </div>
                    </div>
                    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:8px;">
                        <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-primary);cursor:pointer;padding:6px 8px;background:var(--bg-surface);border:1px solid var(--border-hairline);border-radius:8px;">
                            <input type="checkbox" class="dash-exp-sheet-cb" value="eq" checked onchange="window.updateDashExportPreview()" style="width:15px;height:15px;accent-color:var(--accent-primary);">
                            <span>1. %AVA แยกตามชนิดอุปกรณ์</span>
                        </label>
                        <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-primary);cursor:pointer;padding:6px 8px;background:var(--bg-surface);border:1px solid var(--border-hairline);border-radius:8px;">
                            <input type="checkbox" class="dash-exp-sheet-cb" value="resp" checked onchange="window.updateDashExportPreview()" style="width:15px;height:15px;accent-color:var(--accent-primary);">
                            <span>2. สถานะการติดตามหน้างาน (ไม่ออนไลน์)</span>
                        </label>
                        <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-primary);cursor:pointer;padding:6px 8px;background:var(--bg-surface);border:1px solid var(--border-hairline);border-radius:8px;">
                            <input type="checkbox" class="dash-exp-sheet-cb" value="prod" checked onchange="window.updateDashExportPreview()" style="width:15px;height:15px;accent-color:var(--accent-primary);">
                            <span>3. แยกตามประเภทผลิตภัณฑ์ FRTU</span>
                        </label>
                        <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-primary);cursor:pointer;padding:6px 8px;background:var(--bg-surface);border:1px solid var(--border-hairline);border-radius:8px;">
                            <input type="checkbox" class="dash-exp-sheet-cb" value="comm" checked onchange="window.updateDashExportPreview()" style="width:15px;height:15px;accent-color:var(--accent-primary);">
                            <span>4. แยกตามอุปกรณ์สื่อสารและแม่ข่าย</span>
                        </label>
                        <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-primary);cursor:pointer;padding:6px 8px;background:var(--bg-surface);border:1px solid var(--border-hairline);border-radius:8px;">
                            <input type="checkbox" class="dash-exp-sheet-cb" value="pea" checked onchange="window.updateDashExportPreview()" style="width:15px;height:15px;accent-color:var(--accent-primary);">
                            <span>5. %AVA แยกตามสังกัดการไฟฟ้า</span>
                        </label>
                        <label style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-primary);cursor:pointer;padding:6px 8px;background:var(--bg-surface);border:1px solid var(--border-hairline);border-radius:8px;">
                            <input type="checkbox" class="dash-exp-sheet-cb" id="dashExpDevicesSheetCb" value="devices" checked onchange="window.updateDashExportPreview()" style="width:15px;height:15px;accent-color:var(--accent-primary);">
                            <span>6. ตารางรายการอุปกรณ์และการสื่อสาร</span>
                        </label>
                    </div>
                </section>

                <section class="report-section" id="dashExportDeviceOptionsSection">
                    <div class="report-section-heading">
                        <div class="report-section-title-wrap">
                            <span class="report-section-title">2. เลือกรายละเอียดตารางรายการอุปกรณ์ (หัวข้อที่ 6)</span>
                            <span class="report-section-help">ปรับเลือกขอบเขตและคอลัมน์ได้ตามต้องการ</span>
                        </div>
                        <div class="report-footer-btns">
                            <button type="button" class="report-mini-btn" onclick="window.setAllDashExportCols(true)">เลือกทุกคอลัมน์</button>
                            <button type="button" class="report-mini-btn" onclick="window.setDefaultDashExportCols()">คอลัมน์หลัก</button>
                        </div>
                    </div>
                    <div class="adv-grid-row" style="margin-bottom:10px;">
                        <div class="adv-field">
                            <label for="dashExportRowScope">ขอบเขตรายการอุปกรณ์ที่จะส่งออก</label>
                            <select id="dashExportRowScope" class="adv-select" onchange="window.updateDashExportPreview()">
                                <option value="filtered" selected>ตามตัวกรองที่เลือกในตารางข้อ 6 ปัจจุบัน</option>
                                <option value="all_dashboard">อุปกรณ์ทั้งหมดตามขอบเขต Dashboard ด้านบน</option>
                            </select>
                        </div>
                        <div class="adv-field">
                            <label for="dashExportPageFormat">รูปแบบการจัดหน้ากระดาษ Excel</label>
                            <select id="dashExportPageFormat" class="adv-select" onchange="window.updateDashExportPreview()">
                                <option value="a4_landscape" selected>จัดหน้ามาตรฐาน A4 แนวนอน (Fit to 1 Page Wide)</option>
                                <option value="standard">ตารางข้อมูลมาตรฐาน (ไม่จำกัดความกว้างหน้า)</option>
                            </select>
                        </div>
                    </div>
                    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(165px,1fr));gap:6px;">
                        <label style="display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-primary);cursor:pointer;">
                            <input type="checkbox" class="dash-exp-col-cb" value="seq" checked onchange="window.updateDashExportPreview()" style="accent-color:var(--accent-primary);"> ลำดับ
                        </label>
                        <label style="display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-primary);cursor:pointer;">
                            <input type="checkbox" class="dash-exp-col-cb" value="site_id" checked onchange="window.updateDashExportPreview()" style="accent-color:var(--accent-primary);"> Site ID
                        </label>
                        <label style="display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-primary);cursor:pointer;">
                            <input type="checkbox" class="dash-exp-col-cb" value="cmd" checked onchange="window.updateDashExportPreview()" style="accent-color:var(--accent-primary);"> รหัสสั่งการ
                        </label>
                        <label style="display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-primary);cursor:pointer;">
                            <input type="checkbox" class="dash-exp-col-cb" value="loc" checked onchange="window.updateDashExportPreview()" style="accent-color:var(--accent-primary);"> สถานที่
                        </label>
                        <label style="display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-primary);cursor:pointer;">
                            <input type="checkbox" class="dash-exp-col-cb" value="pea" checked onchange="window.updateDashExportPreview()" style="accent-color:var(--accent-primary);"> การไฟฟ้า
                        </label>
                        <label style="display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-primary);cursor:pointer;">
                            <input type="checkbox" class="dash-exp-col-cb" value="norm_eq" checked onchange="window.updateDashExportPreview()" style="accent-color:var(--accent-primary);"> ชนิดอุปกรณ์
                        </label>
                        <label style="display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-primary);cursor:pointer;">
                            <input type="checkbox" class="dash-exp-col-cb" value="prod" checked onchange="window.updateDashExportPreview()" style="accent-color:var(--accent-primary);"> ประเภทผลิตภัณฑ์
                        </label>
                        <label style="display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-primary);cursor:pointer;">
                            <input type="checkbox" class="dash-exp-col-cb" value="comm_type" checked onchange="window.updateDashExportPreview()" style="accent-color:var(--accent-primary);"> Commu.
                        </label>
                        <label style="display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-primary);cursor:pointer;">
                            <input type="checkbox" class="dash-exp-col-cb" value="comm_item" checked onchange="window.updateDashExportPreview()" style="accent-color:var(--accent-primary);"> Item
                        </label>
                        <label style="display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-primary);cursor:pointer;">
                            <input type="checkbox" class="dash-exp-col-cb" value="comm_ref" checked onchange="window.updateDashExportPreview()" style="accent-color:var(--accent-primary);"> WRL Master
                        </label>
                        <label style="display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-primary);cursor:pointer;">
                            <input type="checkbox" class="dash-exp-col-cb" value="ip" checked onchange="window.updateDashExportPreview()" style="accent-color:var(--accent-primary);"> IP Address
                        </label>
                        <label style="display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-primary);cursor:pointer;">
                            <input type="checkbox" class="dash-exp-col-cb" value="dnp" checked onchange="window.updateDashExportPreview()" style="accent-color:var(--accent-primary);"> DNP Address
                        </label>
                        <label style="display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-primary);cursor:pointer;">
                            <input type="checkbox" class="dash-exp-col-cb" value="state_full" checked onchange="window.updateDashExportPreview()" style="accent-color:var(--accent-primary);"> State SCADA
                        </label>
                        <label style="display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-primary);cursor:pointer;">
                            <input type="checkbox" class="dash-exp-col-cb" value="after_full" onchange="window.updateDashExportPreview()" style="accent-color:var(--accent-primary);"> สถานะหลังตรวจสอบ
                        </label>
                    </div>
                </section>

                <div class="report-export-summary">
                    <div class="report-export-summary-left">
                        <svg viewBox="0 0 24 24" width="18" height="18" stroke="var(--accent-primary)" fill="none" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                        <div>
                            <div class="report-export-summary-text" id="dashExportSummaryTitle">สรุปรายละเอียดข้อมูลที่จะ Export</div>
                            <div class="report-export-subtext" id="dashExportSummarySub">เลือก 6 ตาราง · 13 คอลัมน์อุปกรณ์</div>
                        </div>
                    </div>
                    <div class="report-preview-count" id="dashExportPreviewBadge">6 Sheets</div>
                </div>

                <div class="report-btn-group">
                    <div class="report-btn-secondary-row">
                        <button class="report-btn report-btn-cancel" type="button" onclick="window.setAllDashExportSheets(true); window.setAllDashExportCols(true);">เลือกครบทั้งหมด</button>
                        <button class="report-btn report-btn-cancel" type="button" onclick="window.closeDashExportModal()">ยกเลิก</button>
                    </div>
                    <button id="execDashExportBtn" class="report-btn report-btn-dl" type="button" onclick="window.exportDashboardExcel()">
                        <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="white" stroke-width="2"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg> Export ข้อมูล (.xlsx)
                    </button>
                </div>
            </div>
        </div>

        <!-- หน้าต่าง Popup/Modal แจ้งข้อมูลไม่ถูกต้อง (Report Incorrect Data) -->
        <div id="userReportModalOverlay" class="report-modal-overlay">
            <div class="report-modal ur-modal-box" role="dialog" aria-modal="true" aria-labelledby="userReportModalTitle">
                <button class="report-close-btn" type="button" onclick="window.closeUserReportModal()" aria-label="ปิดหน้าต่าง">×</button>
                <div class="report-modal-top" style="padding-right: 44px;">
                    <div class="report-modal-icon" style="background:#fff7ed;">
                        <svg viewBox="0 0 24 24" style="stroke:#ea580c;"><path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>
                    </div>
                    <div style="flex:1;min-width:0;">
                        <div class="report-eyebrow-row">
                            <p class="report-eyebrow" style="color:#ea580c;">REPORT INCORRECT DATA</p>
                        </div>
                        <h3 id="userReportModalTitle">แจ้งข้อมูลอุปกรณ์ไม่ถูกต้อง (ระบบ SCADA NE2)</h3>
                        <p class="report-subtitle">ค้นหาอุปกรณ์ที่ต้องการแจ้งแก้ไข ติ๊กเลือกฟิลด์ที่ไม่ถูกต้อง แล้วระบุข้อมูลที่ถูกต้องเพื่อส่งแจ้งแก้ไขเข้าระบบ</p>
                    </div>
                </div>

                <!-- ส่วนฟอร์มแจ้งข้อมูลไม่ถูกต้อง (ซ่อนอัตโนมัติเมื่อส่งข้อมูลสำเร็จ) -->
                <div id="urFormBodyWrap">
                    <!-- ขั้นตอนที่ 1: ช่องค้นหาอัจฉริยะช่องเดียว + การ์ดสรุปอุปกรณ์ที่เลือก -->
                    <section class="report-section">
                        <div class="report-section-heading">
                            <div class="report-section-title-wrap">
                                <span class="report-section-title"><span class="ur-step-badge">1</span>เลือกอุปกรณ์ที่ต้องการแจ้งแก้ไข</span>
                                <span class="report-section-help">พิมพ์ค้นหาสลับคำได้ เช่น สถานที่ ตามด้วย IP, Site ID, รหัสสั่งการ หรือการไฟฟ้า (เรียงลำดับ A–Z, ก–ฮ)</span>
                            </div>
                            <span class="report-worker-count" id="urMatchedDeviceCount">1,571 อุปกรณ์</span>
                        </div>

                        <!-- ช่องค้นหาอัจฉริยะช่องเดียว (ซ่อนอัตโนมัติเมื่อเลือกอุปกรณ์แล้ว และแสดงการ์ดสรุปแทน) -->
                        <div id="urSmartSearchContainer" class="ur-smart-combobox">
                            <div class="ur-smart-input-wrap">
                                <svg class="ur-smart-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                                <input type="search" id="urDeviceSearchInput" class="ur-smart-input" placeholder="พิมพ์ค้นหาอุปกรณ์อัจฉริยะ (เช่น อุบล 10.120, NE2-UBN, รหัสสั่งการ, สถานที่)..." autocomplete="off" onfocus="window.openUrSmartDropdown()" oninput="window.filterUserReportDeviceOptions()">
                                <button type="button" id="urSmartClearBtn" class="ur-smart-clear-btn" onclick="window.clearUrSmartSearchInput(event)" title="ล้างคำค้นหา">×</button>
                            </div>
                            <div id="urSmartSearchDropdown" class="ur-smart-dropdown" role="listbox"></div>
                            <select id="urDeviceSelect" style="display:none;" onchange="window.onUserReportDeviceSelected(this.value)">
                                <option value="">-- กรุณาเลือกอุปกรณ์ที่ต้องการแจ้งข้อมูลไม่ถูกต้อง --</option>
                            </select>
                        </div>

                        <!-- การ์ดสรุปข้อมูลอุปกรณ์เมื่อคลิกเลือกแล้ว พร้อมปุ่มเปลี่ยนอุปกรณ์ -->
                        <div id="urSelectedDeviceCard" class="ur-selected-device-card">
                            <div class="ur-sdc-header">
                                <div class="ur-sdc-title-group">
                                    <div class="ur-sdc-site">
                                        <span id="urCardSiteId">-</span>
                                        <span id="urCardCmdBadge" class="ur-smart-opt-cmd">-</span>
                                        <span id="urCardStateBadge" class="status-pill-sm" style="background:#16a34a;">-</span>
                                    </div>
                                    <div class="ur-sdc-loc" id="urCardLocation">-</div>
                                </div>
                                <div style="display:inline-flex;gap:6px;align-items:center;">
                                    <button type="button" class="report-mini-btn" id="urToggleFullFieldsBtn" onclick="window.toggleUrFullFieldsView()">ดูข้อมูลครบทุกฟิลด์ ▾</button>
                                    <button type="button" class="ur-sdc-change-btn" onclick="window.changeUserReportDevice()">
                                        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 2v6h-6M3 12a9 9 0 0 1 15-6.7L21 8M3 22v-6h6M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>
                                        เปลี่ยนอุปกรณ์
                                    </button>
                                </div>
                            </div>
                            <div class="ur-sdc-meta-pills" id="urCardMetaPills"></div>
                            <div id="urDeviceFullDataBox" style="display:none;margin-top:4px;">
                                <div style="font-size:11px;font-weight:600;color:var(--accent-primary);margin-bottom:4px;" id="urSelectedDeviceHeader">ข้อมูลปัจจุบันของอุปกรณ์ในระบบทั้งหมด:</div>
                                <div id="urDeviceInfoGrid" class="ur-device-info-grid"></div>
                            </div>
                        </div>
                    </section>

                    <!-- ขั้นตอนที่ 2: การ์ดย่อยเลือกฟิลด์ที่ไม่ถูกต้อง กางช่องกรอกค่าที่ถูกต้องออกมาอย่างนุ่มนวล -->
                    <section class="report-section">
                        <div class="report-section-heading">
                            <div class="report-section-title-wrap">
                                <span class="report-section-title"><span class="ur-step-badge">2</span>ติ๊กเลือกฟิลด์ที่ไม่ถูกต้อง และระบุค่าที่ถูกต้อง</span>
                                <span class="report-section-help">เมื่อติ๊กเลือกการ์ดฟิลด์ใด ช่องกรอกค่าที่ถูกต้องจะกางออกมาทันที</span>
                            </div>
                            <span class="report-worker-count" id="urCheckedFieldsBadge">เลือกแล้ว 0 ฟิลด์</span>
                        </div>
                        <div id="urFieldsChecklist" class="ur-fields-checklist">
                            <div style="grid-column:1/-1;text-align:center;padding:18px;color:var(--text-secondary);font-size:12.5px;">
                                กรุณาค้นหาและเลือกอุปกรณ์ในขั้นตอนที่ 1 ก่อน เพื่อแสดงรายการฟิลด์ทั้งหมด
                            </div>
                        </div>
                    </section>

                    <!-- ขั้นตอนที่ 3: สรุปข้อมูลที่แก้ไข & หมายเหตุผู้แจ้ง -->
                    <section class="report-section">
                        <div class="report-section-heading">
                            <div class="report-section-title-wrap">
                                <span class="report-section-title"><span class="ur-step-badge">3</span>สรุปข้อมูลที่ถูกต้อง (แก้ไขเป็น) & หมายเหตุผู้แจ้ง</span>
                                <span class="report-section-help">ระบบรวบรวมค่าที่กรอกจากข้อ 2 ให้อัตโนมัติ หรือพิมพ์เพิ่มเติมได้</span>
                            </div>
                        </div>
                        <div style="display:flex;flex-direction:column;gap:8px;">
                            <input type="text" id="urCorrectedValueInput" class="ur-text-input" placeholder="สรุปข้อมูลที่ถูกต้อง (แก้ไขเป็น)... เช่น รหัสสั่งการ แก้ไขเป็น: UBB05R-01">
                            <input type="text" id="urReporterNoteInput" class="ur-text-input" style="font-size:12px;padding:7px 12px;" placeholder="ชื่อผู้แจ้ง / สังกัด / หมายเหตุเพิ่มเติม (ถ้ามี)">
                        </div>
                        <div id="urStatusBanner" class="ur-status-banner"></div>
                    </section>

                    <!-- ปุ่มส่งข้อมูลแจ้งแก้ไข -->
                    <div class="report-btn-group">
                        <div class="report-btn-secondary-row">
                            <button class="report-btn report-btn-cancel" type="button" onclick="window.resetUserReportForm()">ล้างฟอร์ม</button>
                            <button class="report-btn report-btn-cancel" type="button" onclick="window.closeUserReportModal()">ปิดหน้าต่าง</button>
                        </div>
                        <button id="urSubmitBtn" class="report-btn report-btn-dl" type="button" onclick="window.submitUserReportToGoogleSheet()" style="background:#ea580c;">
                            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="white" stroke-width="2"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>
                            ส่งข้อมูลแจ้งแก้ไข
                        </button>
                    </div>
                </div>

                <!-- หน้าต่างยืนยันเมื่อส่งข้อมูลสำเร็จ (Success State) -->
                <div id="urSuccessStateView" class="ur-success-state-view" role="status" aria-live="polite">
                    <div class="ur-success-icon-circle">
                        <svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg>
                    </div>
                    <h4 class="ur-success-title">ส่งข้อมูลสำเร็จ ขอบคุณสำหรับการแจ้งปัญหา</h4>
                    <p class="ur-success-desc">ระบบได้รับข้อมูลแจ้งแก้ไขของคุณเรียบร้อยแล้ว ข้อมูลดังกล่าวจะถูกนำไปตรวจสอบและอัปเดตในฐานข้อมูลระบบ SCADA ต่อไป</p>
                    <div id="urSuccessSummaryChip" class="ur-success-summary-chip"></div>
                    <div class="ur-success-btn-row">
                        <button type="button" class="report-btn report-btn-cancel" onclick="window.resetUserReportForm()">แจ้งแก้ไขรายการอื่นเพิ่มเติม</button>
                        <button type="button" class="report-btn report-btn-dl" style="background:#16a34a;box-shadow:0 2px 8px rgba(22,163,74,0.28);" onclick="window.closeUserReportModal()">ปิดหน้าต่าง</button>
                    </div>
                </div>
            </div>
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
        var statusHashMap = window.statusHashMap = {status_hash_json};
        var expDataCompact = window.expDataCompact = {export_json};
        var sData = window.sData = {search_json};
        var reportRawData = window.reportRawData = {report_json_data};
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
                        '<div class="popup-tech-chip full-span"><span>อุปกรณ์สื่อสาร (กลุ่มหลัก: ' + (typeof classifyMainCommGroup === "function" ? classifyMainCommGroup(commuVal, itemVal) : commuVal) + ')</span><strong>' + commuVal + ' &bull; ' + itemVal + '</strong></div>' +
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
            if (typeof window.closeDashExportModal === 'function') window.closeDashExportModal();
            if (typeof window.closeUserReportModal === 'function') window.closeUserReportModal();
            if (typeof window.closeAllCascFilterMenus === 'function') window.closeAllCascFilterMenus();
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

        window.openReportModal = function(expandAi) {{
            var fw = document.getElementById('customFilterWrapper'); if (fw) fw.classList.remove('show');
            var alertEl = document.getElementById('customAlertOverlay'); if (alertEl) alertEl.classList.remove('show');
            var srEl = document.getElementById('searchResults'); if (srEl) srEl.style.display = 'none';
            var glEl = document.getElementById('gLayerPanel'); if (glEl) glEl.classList.remove('show');
            closeReportWorkerDropdown();
            hideCustomPanel();
            var sd = document.getElementById('reportStartDate');
            var ed = document.getElementById('reportEndDate');
            if (sd) {{ sd.value = ''; sd.type = 'text'; }}
            if (ed) {{ ed.value = ''; ed.type = 'text'; }}
            var drawer = document.getElementById('advFilterDrawer');
            var container = document.getElementById('advFilterContainer');
            var expandBtn = document.getElementById('advExpandBtn');
            if (drawer) drawer.classList.remove('show');
            if (container) container.classList.remove('is-open');
            if (expandBtn) expandBtn.setAttribute('aria-expanded', 'false');
            var workerColSel = document.getElementById('advWorkerColToggle');
            if (workerColSel) workerColSel.value = 'hide';
            toggleGeminiAiPanel(expandAi !== false);
            if (reportModal) reportModal.classList.add('show');
            initializeReportWorkerFilter();
            updateGeminiUiAndButtonState();
            fetch('/api/gemini/status').then(function(r){{return r.json();}}).then(function(s){{syncBackendStatusToLocal(s);}}).catch(function(){{}});
        }};

        if (reportBtn) {{
            reportBtn.addEventListener('click', function(e) {{
                e.preventDefault(); e.stopPropagation();
                window.openReportModal(true);
            }});
        }}

        function closeReportModal() {{ if (reportModal) reportModal.classList.remove('show'); closeReportWorkerDropdown(); if (typeof closeApiKeyPopover === 'function') closeApiKeyPopover(); }}

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
            if (typeof renderSentenceManagerList === "function") renderSentenceManagerList();
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

            var rawQuery = String(input.value || '').trim();
            var matchedList = [];
            reportWorkerNames.forEach(function(name) {{
                var sc = rawQuery
                    ? (typeof window.scoreFieldsAgainstSmartQuery === 'function'
                        ? window.scoreFieldsAgainstSmartQuery([name], rawQuery)
                        : (String(name).toLowerCase().indexOf(rawQuery.toLowerCase()) >= 0 ? 100 : 0))
                    : 1;
                if (sc > 0) matchedList.push({{ name: name, score: sc }});
            }});

            // เรียงลำดับจากน้อยไปมาก (Ascending Order: ก-ฮ, A-Z)
            matchedList.sort(function(a, b) {{
                if (typeof window.compareAscendingNatural === 'function') {{
                    return window.compareAscendingNatural(a.name, b.name);
                }}
                return String(a.name || '').localeCompare(String(b.name || ''), 'th', {{ numeric: true, sensitivity: 'base' }});
            }});
            var matches = matchedList.slice(0, 30).map(function(m) {{ return m.name; }});

            dropdown.innerHTML = '';

            var header = document.createElement('div');
            header.className = 'worker-dropdown-header';
            var headerTitle = document.createElement('span');
            headerTitle.textContent = rawQuery ? ('ผลการค้นหา "' + rawQuery + '" (' + matches.length + ' คน · เรียง ก–ฮ)') : ('รายชื่อผู้ปฏิบัติงานทั้งหมด (' + reportWorkerNames.length + ' คน)');
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

            if (!matches.length) {{
                var empty = document.createElement('div');
                empty.style.padding = '14px';
                empty.style.textAlign = 'center';
                empty.style.fontSize = '12px';
                empty.style.color = 'var(--text-secondary)';
                empty.textContent = rawQuery ? ('ไม่พบรายชื่อที่ตรงกับ "' + rawQuery + '"') : 'ไม่พบรายชื่อผู้ปฏิบัติงานในข้อมูล';
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
                    if (rawQuery && typeof window.highlightSearchText === 'function') {{
                        nameSpan.innerHTML = window.highlightSearchText(name, rawQuery);
                    }} else {{
                        nameSpan.textContent = name;
                    }}
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

        // ============================================================================
        // === ระบบบริหารจัดการ Google Gemini API, Dual Department Engineering Prompts (ผบอ. / ผอส.) & Smartest-First Model Fallback ===
        var SYSTEM_PROMPT_PBO_JS =
            "Act as a Senior Control System & Electrical Engineer สังกัด แผนก ผบอ. ของการไฟฟ้าส่วนภูมิภาค (PEA) ควบคู่กับบทบาท 'ผู้ชำนาญการระดับสูงด้านการใช้ภาษาไทยเชิงวิชาการ'\\n" +
            "ขอบเขตความรับผิดชอบของคุณคือ: ตู้คอนโทรล, อุปกรณ์ภายในตู้, หม้อแปลงแรงดัน (VT) ที่เชื่อมกับระบบ 22kV เพื่อจ่ายไฟให้ตู้, อุปกรณ์สวิตช์เกียร์ (LBS, Recloser), ชุดควบคุมโทรมาตร (FRTU), บอร์ดคอนโทรลภายใน, คอนเวอร์เตอร์ (Converter), และระบบแบตเตอรี่\\n" +
            "หน้าที่ของคุณคือการวิเคราะห์ข้อมูลดิบจากรายงานการซ่อมบำรุง และเรียบเรียงเป็น 'รายงานเชิงวิศวกรรม' ที่มีความเป็นมืออาชีพ สละสลวย และเนื้อหากระชับ\\n" +
            "โครงสร้างการเขียน (บังคับ):\\n" +
            "ให้ร้อยเรียงเรื่องราวเป็นประโยคอธิบายยาวแบบต่อเนื่อง (Flowing Narrative) 1 ย่อหน้า โดยต้องครอบคลุม 4 องค์ประกอบหลักตามลำดับนี้เสมอ:\\n" +
            "1. สาเหตุ: ปัญหาเกิดจากอุปกรณ์ใดในความรับผิดชอบของ ผบอ. ชำรุดหรือขัดข้อง\\n" +
            "2. การดำเนินการ: ได้แก้ไขเบื้องต้นหรือซ่อมแซมเปลี่ยนอุปกรณ์ฮาร์ดแวร์อย่างไร\\n" +
            "3. ผลลัพธ์: สถานะปัจจุบันของตู้คอนโทรลและอุปกรณ์สวิตช์เกียร์\\n" +
            "4. การดำเนินการต่อไป: ระบุอย่างชัดเจนว่า แผนก ผบอ. จะดำเนินการอะไรต่อไปในเชิงฮาร์ดแวร์/ระบบไฟ\\n" +
            "5. รูปแบบประโยคหลักที่ต้องยึดถือ (ปรับคำเชื่อมให้สละสลวยได้):\\n" +
            "6. 'เนื่องจาก... (สาเหตุ) จึงได้ดำเนินการ... (การดำเนินการ) ส่งผลให้... (ผลลัพธ์) ทั้งนี้ ในระยะต่อไปทางแผนก ผบอ. จะดำเนินการ... (การดำเนินการต่อไป)'\\n" +
            "7. ข้อกำหนดสำคัญระดับสูง (Rules & Constraints):\\n" +
            "8. ความสมบูรณ์แบบทางภาษา (Linguistic Excellence): ตัดคำฟุ่มเฟือย คำวกวน หรือการใช้คำซ้ำซ้อนทางความหมายออกอย่างเด็ดขาด (เช่น หลีกเลี่ยงการใช้คำว่า 'ทำการ' หรือ 'ดำเนินการ' ซ้ำซ้อนกันหลายรอบ) ประโยคต้องกระชับ อ่านลื่นไหล แต่ยังคงรายละเอียดทางเทคนิคไว้อย่างครบถ้วน\\n" +
            "9. Technical Terminology: ใช้คำศัพท์เฉพาะทางให้ถูกต้อง (เช่น FRTU, Recloser, LBS, VT 22kV, Control Board, Converter)\\n" +
            "10. Professional Formatting: ห้ามใช้สัญลักษณ์ (เช่น ✅, ❌, •, -) หรือการขึ้นบรรทัดใหม่แบบหัวข้อย่อยเด็ดขาด\\n" +
            "11. Handling Missing Data: หากข้อมูลไม่ครบ ให้ใช้ตรรกะทางวิศวกรรมประเมินจาก 'สถานะสุดท้าย' และกำหนดแผนการดำเนินงานของ ผบอ. ตามมาตรฐาน ห้ามแต่งเติมชื่ออุปกรณ์ที่ไม่มีในบริบท";

        var SYSTEM_PROMPT_POS_JS =
            "Act as a Senior SCADA & Communication Engineer สังกัด แผนก ผอส. ของการไฟฟ้าส่วนภูมิภาค (PEA) ควบคู่กับบทบาท 'ผู้ชำนาญการระดับสูงด้านการใช้ภาษาไทยเชิงวิชาการ'\\n" +
            "ขอบเขตความรับผิดชอบของคุณคือ: ระบบสื่อสารข้อมูล, อุปกรณ์สื่อสาร (Modem, Router), สายสัญญาณสื่อสาร, แผงสัญญาณเสาอากาศ, ซิมการ์ด, สัญญาณเครือข่าย, และการเชื่อมต่อโปรโตคอลระหว่างตู้ FRTU กับศูนย์ควบคุม SCADA\\n" +
            "หน้าที่ของคุณคือการวิเคราะห์ข้อมูลดิบจากรายงานการซ่อมบำรุง และเรียบเรียงเป็น 'รายงานเชิงวิศวกรรม' ที่มีความเป็นมืออาชีพ สละสลวย และเนื้อหากระชับ\\n" +
            "โครงสร้างการเขียน (บังคับ):\\n" +
            "ให้ร้อยเรียงเรื่องราวเป็นประโยคอธิบายยาวแบบต่อเนื่อง (Flowing Narrative) 1 ย่อหน้า โดยต้องครอบคลุม 4 องค์ประกอบหลักตามลำดับนี้เสมอ:\\n" +
            "1. สาเหตุ: ปัญหาเกิดจากอุปกรณ์สื่อสารใดในความรับผิดชอบของ ผอส. ขัดข้อง\\n" +
            "2. การดำเนินการ: ได้แก้ไขเบื้องต้นอย่างไร\\n" +
            "3. ผลลัพธ์: สถานะการเชื่อมต่อสื่อสารปัจจุบัน\\n" +
            "4. การดำเนินการต่อไป: ระบุอย่างชัดเจนว่า แผนก ผอส. จะดำเนินการอะไรต่อไปในเชิงระบบสื่อสาร\\n" +
            "5. รูปแบบประโยคหลักที่ต้องยึดถือ (ปรับคำเชื่อมให้สละสลวยได้):\\n" +
            "6. 'เนื่องจาก... (สาเหตุ) จึงได้ดำเนินการ... (การดำเนินการ) ส่งผลให้... (ผลลัพธ์) ทั้งนี้ ในระยะต่อไปทางแผนก ผอส. จะดำเนินการ... (การดำเนินการต่อไป)'\\n" +
            "7. ข้อกำหนดสำคัญระดับสูง (Rules & Constraints):\\n" +
            "8. ความสมบูรณ์แบบทางภาษา (Linguistic Excellence): ตัดคำฟุ่มเฟือย คำวกวน หรือการใช้คำซ้ำซ้อนทางความหมายออกอย่างเด็ดขาด (เช่น หลีกเลี่ยงการใช้คำว่า 'ทำการ' หรือ 'ดำเนินการ' ซ้ำซ้อนกันหลายรอบ) ประโยคต้องกระชับ อ่านลื่นไหล แต่ยังคงรายละเอียดทางเทคนิคไว้อย่างครบถ้วน\\n" +
            "9. Technical Terminology: ใช้คำศัพท์เฉพาะทางด้านสื่อสารให้ถูกต้อง (เช่น Communication Loss, Antenna, Signal Cable, Modem, Protocol)\\n" +
            "10. Professional Formatting: ห้ามใช้สัญลักษณ์ (เช่น ✅, ❌, •, -) หรือการขึ้นบรรทัดใหม่แบบหัวข้อย่อยเด็ดขาด\\n" +
            "11. Handling Missing Data: หากข้อมูลไม่ครบ ให้ใช้ตรรกะทางวิศวกรรมประเมินจาก 'สถานะสุดท้าย' และกำหนดแผนการดำเนินงานของ ผอส. ตามมาตรฐาน ห้ามแต่งเติมชื่ออุปกรณ์ที่ไม่มีในบริบท";

        var DEFAULT_ENGINEERING_PROMPT_JS =
            "[System Prompt 1 สำหรับแผนก ผบอ.]\\n" + SYSTEM_PROMPT_PBO_JS +
            "\\n\\n[System Prompt 2 สำหรับแผนก ผอส.]\\n" + SYSTEM_PROMPT_POS_JS;

        function resolveDepartmentForRowJS(respStr, detailStr) {{
            var resp = String(respStr || "").trim();
            var detail = String(detailStr || "").toUpperCase();
            var hasPbo = resp.indexOf("ผบอ") !== -1;
            var hasPos = resp.indexOf("ผอส") !== -1;
            if (hasPbo && !hasPos) return "PBO";
            if (hasPos && !hasPbo) return "POS";
            var pboKw = ["FRTU", "RECLOSER", "LBS", "VT", "22KV", "CONTROL BOARD", "CONVERTER", "BATTERY", "แบต", "ตู้คอนโทรล", "หม้อแปลง", "ฟิวส์", "FUSE", "NO AC", "ไม่มีไฟ", "บอร์ด", "การ์ด", "AVR"];
            var posKw = ["MODEM", "ROUTER", "CELLULAR", "DAU", "SIM", "ซิม", "ANTENNA", "เสาอากาศ", "สายสัญญาณ", "SIGNAL", "PROTOCOL", "DNP", "COMMUNICATION", "สื่อสาร", "สัญญาณ", "เครือข่าย", "PORT LAN", "WRL"];
            var pboScore = 0, posScore = 0;
            pboKw.forEach(function(k) {{ if (detail.indexOf(k) !== -1) pboScore++; }});
            posKw.forEach(function(k) {{ if (detail.indexOf(k) !== -1) posScore++; }});
            if (hasPbo && hasPos) return pboScore >= posScore ? "PBO" : "POS";
            return pboScore > posScore ? "PBO" : "POS";
        }}

        function getDepartmentSystemPromptJS(deptCode, customPromptText) {{
            var cp = String(customPromptText || "").trim();
            if (cp && cp !== DEFAULT_ENGINEERING_PROMPT_JS.trim()) {{
                if (cp.indexOf("[System Prompt 1 สำหรับแผนก ผบอ.]") !== -1 && cp.indexOf("[System Prompt 2 สำหรับแผนก ผอส.]") !== -1) {{
                    var parts = cp.split("[System Prompt 2 สำหรับแผนก ผอส.]");
                    return deptCode === "PBO"
                        ? parts[0].replace("[System Prompt 1 สำหรับแผนก ผบอ.]", "").trim()
                        : parts[1].trim();
                }}
                return cp;
            }}
            return deptCode === "PBO" ? SYSTEM_PROMPT_PBO_JS : SYSTEM_PROMPT_POS_JS;
        }}

        // ไม่เก็บ API Key แบบ Hardcode ในฝั่ง Frontend เพื่อความปลอดภัยของระบบ
        var INTERNAL_FALLBACK_GEMINI_KEY = "";

        var MODEL_MONO_ICONS = {{
            sparkle: '<svg viewBox="0 0 24 24"><path d="M12 3l1.9 5.8a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-1.9a2 2 0 0 0 1.3-1.3L12 3z"/></svg>',
            bolt: '<svg viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
            routing: '<svg viewBox="0 0 24 24"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg>',
            layers: '<svg viewBox="0 0 24 24"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>',
            cpu: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/></svg>',
            check: '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>'
        }};

        // โครงสร้างข้อมูลโมเดล AI โดยให้ความสำคัญกับโมเดลที่ "ฉลาดที่สุด" เป็นอันดับแรก (Pro -> Flash วิเคราะห์เชิงลึก -> Flash Lite)
        var GEMINI_MODELS_CONFIG = {{
            "gemini-3.5-pro-exp": {{
                id: "gemini-3.5-pro-exp",
                iconSvg: MODEL_MONO_ICONS.sparkle,
                title: "Gemini 3.5 Pro (Exp)",
                label: "gemini-3.5-pro-exp",
                role: "อันดับ 1 · ฉลาดที่สุด",
                desc: "โมเดลตระกูล Pro วิเคราะห์ขั้นสูงและเขียนรายงานวิศวกรรมได้ดีที่สุด (ค่าเริ่มต้น)",
                rpd_limit: 50,
                max_chunk_chars: 4800,
                max_chunk_rows: 25,
                simulated_disabled: false,
                sdk_candidates: ["gemini-3.5-pro-exp", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"]
            }},
            "gemini-3.8-flash": {{
                id: "gemini-3.8-flash",
                iconSvg: MODEL_MONO_ICONS.cpu,
                title: "Gemini 3.8 Flash",
                label: "gemini-3.8-flash",
                role: "Fallback 1 · ฉลาดรองลงมา",
                desc: "โมเดลสำรองอันดับ 1 เน้นวิเคราะห์เชิงลึกและเรียบเรียงภาษาทางการ",
                rpd_limit: 100,
                max_chunk_chars: 4500,
                max_chunk_rows: 25,
                sdk_candidates: ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-3-flash-preview", "gemini-2.0-flash"]
            }},
            "gemini-3.5-flash-lite": {{
                id: "gemini-3.5-flash-lite",
                iconSvg: MODEL_MONO_ICONS.bolt,
                title: "Gemini 3.5 Flash Lite",
                label: "gemini-3.5-flash-lite",
                role: "Fallback 2 · ความเร็วสูง",
                desc: "โมเดลสำรองอันดับ 2 ประมวลผลเร็วและรองรับโควต้าสูง 500 RPD",
                rpd_limit: 500,
                max_chunk_chars: 3800,
                max_chunk_rows: 20,
                sdk_candidates: ["gemini-3.5-flash-lite", "gemini-2.5-flash-lite", "gemini-2.0-flash-lite", "gemini-2.5-flash"]
            }},
            "gemini-3.1-flash-lite": {{
                id: "gemini-3.1-flash-lite",
                iconSvg: MODEL_MONO_ICONS.layers,
                title: "Gemini 3.1 Flash Lite",
                label: "gemini-3.1-flash-lite",
                role: "Fallback 3 · เสถียรสูง",
                desc: "โมเดลสำรองอันดับ 3 ความเสถียรสูงและรองรับงานปริมาณมาก",
                rpd_limit: 500,
                max_chunk_chars: 3600,
                max_chunk_rows: 18,
                sdk_candidates: ["gemini-3.1-flash-lite", "gemini-3.1-flash-lite-preview", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
            }},
            "auto_fallback": {{
                id: "auto_fallback",
                iconSvg: MODEL_MONO_ICONS.routing,
                title: "Auto Fallback (Smartest Routing)",
                label: "auto_fallback",
                role: "Smart Routing",
                desc: "เริ่มจากโมเดลที่ฉลาดที่สุด (Pro) และสลับไปโมเดลที่ฉลาดรองลงมาอัตโนมัติ",
                rpd_limit: 1150,
                max_chunk_chars: 4800,
                max_chunk_rows: 25,
                sdk_candidates: []
            }}
        }};

        // ลำดับการสลับโมเดลอัตโนมัติ (Fallback Order): เรียงจากโมเดลที่ฉลาดที่สุดไปยังโมเดลที่ฉลาดรองลงมาเรื่อยๆ
        var GEMINI_FALLBACK_LIST = [
            "gemini-3.5-pro-exp",
            "gemini-3.8-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite"
        ];
        var GEMINI_SELECTOR_ORDER = [
            "gemini-3.5-pro-exp",
            "auto_fallback",
            "gemini-3.8-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite"
        ];
        var autoFallbackSmartRoutingEnabled = true;
        var cachedAiSummaryResult = null;
        var cachedAiFilterSignature = "";
        var customRowSentences = Object.create(null);

        function syncAiToolButtonsActiveState() {{
            var promptBox = document.getElementById("engineeringPromptBox");
            var promptBtn = document.getElementById("openPromptSettingBtn");
            var promptTa = document.getElementById("geminiCustomPromptInput");
            var isPromptOpen = Boolean(promptBox && promptBox.classList.contains("show"));
            var isCustomPromptModified = Boolean(
                promptTa && promptTa.value.trim() && promptTa.value.trim() !== DEFAULT_ENGINEERING_PROMPT_JS.trim()
            );
            var promptActive = isPromptOpen || isCustomPromptModified;
            if (promptBtn) {{
                promptBtn.classList.toggle("is-active", promptActive);
                promptBtn.setAttribute("aria-pressed", promptActive ? "true" : "false");
            }}

            var smBox = document.getElementById("sentenceManagerBox");
            var smBtn = document.getElementById("openSentenceMgrBtn");
            var isSmOpen = Boolean(smBox && smBox.classList.contains("show"));
            var hasEditedRows = Object.keys(customRowSentences || {{}}).length > 0;
            var smActive = isSmOpen || hasEditedRows;
            if (smBtn) {{
                smBtn.classList.toggle("is-active", smActive);
                smBtn.setAttribute("aria-pressed", smActive ? "true" : "false");
            }}

            var keyPopover = document.getElementById("geminiApiKeyPopover");
            var keyBtn = document.getElementById("openCustomApiKeyBtn");
            var keyBtnText = document.getElementById("openCustomApiKeyBtnText");
            var keyBadge = document.getElementById("geminiKeyStatusBadge");
            var keyInp = document.getElementById("geminiApiKeyInput");
            var userKeyVal = (keyInp && keyInp.value) ? keyInp.value.trim() : "";
            var isKeyPopoverOpen = Boolean(keyPopover && keyPopover.classList.contains("show"));
            var hasUserKey = Boolean(userKeyVal && userKeyVal !== INTERNAL_FALLBACK_GEMINI_KEY);
            var keyActive = isKeyPopoverOpen || hasUserKey;

            if (keyBtn) {{
                keyBtn.classList.toggle("is-active", keyActive);
                keyBtn.setAttribute("aria-pressed", keyActive ? "true" : "false");
            }}
            if (keyBtnText) {{
                keyBtnText.textContent = "ระบุ API Key ของตนเอง";
            }}
            if (keyBadge) {{
                keyBadge.textContent = hasUserKey ? "ใช้คีย์ของตนเองอยู่" : "ยังไม่ได้ระบุ";
                keyBadge.classList.toggle("has-custom-key", hasUserKey);
            }}
        }}

        function toggleGeminiAiPanel(forceState) {{
            var sec = document.getElementById("geminiAiSectionCard");
            var body = document.getElementById("geminiCollapsibleBody");
            var btn = document.getElementById("geminiCollapseBtn");
            var lbl = document.getElementById("geminiCollapseLabel");
            if (!sec || !body) return;
            var shouldOpen = typeof forceState === "boolean" ? forceState : !body.classList.contains("show");
            body.classList.toggle("show", shouldOpen);
            sec.classList.toggle("is-open", shouldOpen);
            if (btn) btn.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
            if (lbl) lbl.textContent = shouldOpen ? "พับเก็บ" : "ตั้งค่า AI";
            if (!shouldOpen) {{
                closeApiKeyPopover();
            }}
            syncAiToolButtonsActiveState();
        }}

        function toggleEngineeringPromptBox(forceState) {{
            var box = document.getElementById("engineeringPromptBox");
            var ta = document.getElementById("geminiCustomPromptInput");
            if (!box) return;
            if (ta && !ta.value.trim()) {{
                ta.value = DEFAULT_ENGINEERING_PROMPT_JS;
            }}
            var shouldShow = typeof forceState === "boolean" ? forceState : !box.classList.contains("show");
            box.classList.toggle("show", shouldShow);
            syncAiToolButtonsActiveState();
        }}

        function resetEngineeringPromptDefault() {{
            var ta = document.getElementById("geminiCustomPromptInput");
            if (ta) ta.value = DEFAULT_ENGINEERING_PROMPT_JS;
            syncAiToolButtonsActiveState();
        }}

        function getActiveEngineeringPrompt() {{
            var ta = document.getElementById("geminiCustomPromptInput");
            return (ta && ta.value.trim()) ? ta.value.trim() : DEFAULT_ENGINEERING_PROMPT_JS;
        }}

        function toggleApiKeyPopover(e) {{
            if (e) {{
                e.preventDefault();
                e.stopPropagation();
            }}
            var pop = document.getElementById("geminiApiKeyPopover");
            if (!pop) return;
            var isShowing = pop.classList.toggle("show");
            syncAiToolButtonsActiveState();
            if (isShowing) {{
                var inp = document.getElementById("geminiApiKeyInput");
                if (inp) setTimeout(function() {{ inp.focus(); }}, 40);
            }}
        }}

        function closeApiKeyPopover() {{
            var pop = document.getElementById("geminiApiKeyPopover");
            if (pop) pop.classList.remove("show");
            syncAiToolButtonsActiveState();
        }}

        function clearUserGeminiApiKey() {{
            var inp = document.getElementById("geminiApiKeyInput");
            if (inp) inp.value = "";
            try {{
                localStorage.removeItem("scada_user_custom_gemini_api_key");
                localStorage.removeItem("scada_user_gemini_api_key");
            }} catch (e) {{}}
            syncAiToolButtonsActiveState();
        }}

        function saveUserGeminiApiKey(val) {{
            var clean = String(val || "").trim();
            // ห้ามบันทึกหรือแสดง Default API Key ในช่องกรอกของผู้ใช้เด็ดขาด
            if (clean === INTERNAL_FALLBACK_GEMINI_KEY) {{
                clean = "";
                var inp = document.getElementById("geminiApiKeyInput");
                if (inp) inp.value = "";
            }}
            try {{
                if (clean) {{
                    localStorage.setItem("scada_user_custom_gemini_api_key", clean);
                }} else {{
                    localStorage.removeItem("scada_user_custom_gemini_api_key");
                }}
                localStorage.removeItem("scada_user_gemini_api_key");
            }} catch (e) {{}}
            syncAiToolButtonsActiveState();
        }}

        // โหลดเฉพาะ API Key ที่ผู้ใช้ระบุเองเท่านั้น ห้ามนำ Default API Key มาใส่ในหน้า UI เด็ดขาด
        function loadSavedUserGeminiApiKey() {{
            var inp = document.getElementById("geminiApiKeyInput");
            if (!inp) return;
            try {{
                localStorage.removeItem("scada_user_gemini_api_key");
                var saved = localStorage.getItem("scada_user_custom_gemini_api_key") || "";
                if (saved && saved !== INTERNAL_FALLBACK_GEMINI_KEY) {{
                    inp.value = saved;
                }} else {{
                    inp.value = "";
                }}
            }} catch (e) {{
                inp.value = "";
            }}
            syncAiToolButtonsActiveState();
        }}

        function toggleCustomModelMenu(e) {{
            if (e) {{ e.preventDefault(); e.stopPropagation(); }}
            var wrap = document.getElementById("geminiCustomModelSelector");
            var trig = document.getElementById("geminiCustomModelTrigger");
            if (!wrap) return;
            var isOpen = wrap.classList.toggle("is-open");
            if (trig) trig.setAttribute("aria-expanded", isOpen ? "true" : "false");
        }}

        function selectCustomGeminiModel(modelId) {{
            var st = loadLocalGeminiSessionState();
            var nowMs = Date.now();
            if (modelId !== "auto_fallback") {{
                var blk = evaluateModelBlockInfo(modelId, st, nowMs);
                if (blk.isBlocked) return;
            }}
            var hiddenInput = document.getElementById("geminiModelSelect");
            if (hiddenInput) hiddenInput.value = modelId;
            var wrap = document.getElementById("geminiCustomModelSelector");
            var trig = document.getElementById("geminiCustomModelTrigger");
            if (wrap) wrap.classList.remove("is-open");
            if (trig) trig.setAttribute("aria-expanded", "false");
            onGeminiModelChange();
        }}

        document.addEventListener("click", function(e) {{
            var wrap = document.getElementById("geminiCustomModelSelector");
            if (wrap && wrap.classList.contains("is-open") && !wrap.contains(e.target)) {{
                wrap.classList.remove("is-open");
                var trig = document.getElementById("geminiCustomModelTrigger");
                if (trig) trig.setAttribute("aria-expanded", "false");
            }}
            var keyAnchor = document.getElementById("geminiApiKeyPopoverAnchor");
            var keyPop = document.getElementById("geminiApiKeyPopover");
            if (keyPop && keyPop.classList.contains("show") && keyAnchor && !keyAnchor.contains(e.target)) {{
                closeApiKeyPopover();
            }}
        }});

        function getRowSentenceKey(row) {{
            return String(row.seq !== undefined ? row.seq : "") + "_" + String(row.site || "") + "_" + String(row.iso_date || "");
        }}

        function getEffectiveRowSentence(row) {{
            var k = getRowSentenceKey(row);
            if (customRowSentences[k] !== undefined) return customRowSentences[k];
            if (row.ai_detail) return row.ai_detail;
            return categorizeRowDetailJS(row.detail, row.resp, row.status);
        }}

        function renderSentenceManagerList() {{
            var box = document.getElementById("sentenceManagerBox");
            var listEl = document.getElementById("sentenceManagerList");
            if (!box || !listEl || !box.classList.contains("show")) return;

            var filteredResult = getFilteredReportResult();
            var rows = filteredResult.rows || [];
            listEl.innerHTML = "";

            if (rows.length === 0) {{
                listEl.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-secondary);font-size:12px;">ไม่พบรายการตามตัวกรองที่เลือก</div>';
                return;
            }}

            var maxShow = Math.min(rows.length, 100);
            rows.slice(0, maxShow).forEach(function(r, idx) {{
                var rKey = getRowSentenceKey(r);
                var currentVal = getEffectiveRowSentence(r);
                var itemDiv = document.createElement("div");
                itemDiv.className = "sentence-mgr-item";

                var topDiv = document.createElement("div");
                topDiv.className = "sentence-mgr-item-top";
                topDiv.innerHTML = '<span>#' + (idx + 1) + ' · ' + (r.site || '-') + ' (' + (r.loc || '-') + ')</span>' +
                                   '<span style="color:var(--text-secondary);font-weight:500;">สถานะ: ' + (r.status || '-') + '</span>';

                var origDiv = document.createElement("div");
                origDiv.className = "sentence-mgr-orig";
                origDiv.textContent = "ต้นฉบับ: " + (r.detail || "-");

                var ta = document.createElement("textarea");
                ta.className = "sentence-mgr-textarea";
                ta.value = currentVal;
                ta.setAttribute("data-row-key", rKey);
                ta.addEventListener("input", function() {{
                    customRowSentences[this.getAttribute("data-row-key")] = this.value;
                    syncAiToolButtonsActiveState();
                }});

                itemDiv.appendChild(topDiv);
                itemDiv.appendChild(origDiv);
                itemDiv.appendChild(ta);
                listEl.appendChild(itemDiv);
            }});
        }}

        function toggleSentenceManagerBox(forceState) {{
            var box = document.getElementById("sentenceManagerBox");
            if (!box) return;
            var isShowing = typeof forceState === "boolean" ? forceState : !box.classList.contains("show");
            box.classList.toggle("show", isShowing);
            if (isShowing) renderSentenceManagerList();
            syncAiToolButtonsActiveState();
        }}

        function applySmartCategorizeToAllRows() {{
            var filteredResult = getFilteredReportResult();
            (filteredResult.rows || []).forEach(function(r) {{
                var k = getRowSentenceKey(r);
                customRowSentences[k] = categorizeRowDetailJS(r.detail, r.resp, r.status);
            }});
            renderSentenceManagerList();
            syncAiToolButtonsActiveState();
        }}

        function resetCustomRowSentences() {{
            customRowSentences = Object.create(null);
            renderSentenceManagerList();
            syncAiToolButtonsActiveState();
        }}

        function getNextUtcMidnightMs() {{
            var now = new Date();
            return Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1, 0, 0, 0, 0);
        }}

        function formatCountdownThaiHMS(totalSec) {{
            var sec = Math.max(0, Math.floor(totalSec));
            var h = Math.floor(sec / 3600);
            var m = Math.floor((sec % 3600) / 60);
            var s = sec % 60;
            if (h > 0) return h + " ชม. " + m + " นาที " + s + " วินาที";
            if (m > 0) return m + " นาที " + s + " วินาที";
            return s + " วินาที";
        }}

        function loadLocalGeminiSessionState() {{
            var nowMs = Date.now();
            var utcDay = new Date(nowMs).toISOString().slice(0, 10);
            var raw = null;
            try {{
                raw = JSON.parse(localStorage.getItem("scada_gemini_session_state_v2") || "null");
            }} catch (e) {{ raw = null; }}

            if (!raw || raw.utc_day !== utcDay || !raw.models) {{
                raw = {{ utc_day: utcDay, models: {{}}, last_used_model: "" }};
            }}
            Object.keys(GEMINI_MODELS_CONFIG).forEach(function(m) {{
                if (m === "auto_fallback") return;
                var cfg = GEMINI_MODELS_CONFIG[m];
                if (!raw.models[m]) {{
                    raw.models[m] = {{
                        rpd_used: cfg.simulated_disabled ? cfg.rpd_limit : 0,
                        rpd_limit: cfg.rpd_limit,
                        cooldown_until_ms: 0,
                        rpd_locked_until_ms: cfg.simulated_disabled ? getNextUtcMidnightMs() : 0,
                        last_error_type: cfg.simulated_disabled ? "UNSUPPORTED_OR_FULL" : "",
                        last_error: cfg.simulated_reason || ""
                    }};
                }}
                var st = raw.models[m];
                st.rpd_limit = cfg.rpd_limit;
                if (cfg.simulated_disabled) {{
                    st.rpd_used = cfg.rpd_limit;
                    st.rpd_locked_until_ms = getNextUtcMidnightMs();
                    st.last_error_type = "UNSUPPORTED_OR_FULL";
                    st.last_error = cfg.simulated_reason;
                }} else {{
                    if (st.cooldown_until_ms && nowMs >= st.cooldown_until_ms) {{
                        st.cooldown_until_ms = 0;
                        if (st.last_error_type === "RATE_LIMIT_429" || st.last_error_type === "TOKEN_LIMIT") {{
                            st.last_error_type = "";
                        }}
                    }}
                    if (st.rpd_locked_until_ms && nowMs >= st.rpd_locked_until_ms) {{
                        st.rpd_locked_until_ms = 0;
                        st.rpd_used = 0;
                        if (st.last_error_type === "RPD_EXHAUSTED") st.last_error_type = "";
                    }}
                }}
            }});
            try {{
                localStorage.setItem("scada_gemini_session_state_v2", JSON.stringify(raw));
            }} catch (e) {{}}
            return raw;
        }}

        function saveLocalGeminiSessionState(st) {{
            try {{
                localStorage.setItem("scada_gemini_session_state_v2", JSON.stringify(st));
            }} catch (e) {{}}
        }}

        function markLocalModelLimit(modelName, limitType, errMsg) {{
            var st = loadLocalGeminiSessionState();
            if (!st.models[modelName]) return;
            var m = st.models[modelName];
            m.last_error = String(errMsg || "").slice(0, 200);
            if (limitType === "RPD_EXHAUSTED" || m.rpd_used >= m.rpd_limit) {{
                m.rpd_used = m.rpd_limit;
                m.rpd_locked_until_ms = getNextUtcMidnightMs();
                m.last_error_type = "RPD_EXHAUSTED";
            }} else {{
                m.cooldown_until_ms = Date.now() + 60000;
                m.last_error_type = limitType === "TOKEN_LIMIT" ? "TOKEN_LIMIT" : "RATE_LIMIT_429";
            }}
            saveLocalGeminiSessionState(st);
            updateGeminiUiAndButtonState();
        }}

        function incrementLocalModelRpd(modelName) {{
            var st = loadLocalGeminiSessionState();
            if (!st.models[modelName]) return;
            var m = st.models[modelName];
            m.rpd_used = (m.rpd_used || 0) + 1;
            st.last_used_model = modelName;
            if (m.rpd_used >= m.rpd_limit) {{
                m.rpd_locked_until_ms = getNextUtcMidnightMs();
                m.last_error_type = "RPD_EXHAUSTED";
            }}
            saveLocalGeminiSessionState(st);
            updateGeminiUiAndButtonState();
        }}

        function syncBackendStatusToLocal(serverState) {{
            if (!serverState || !serverState.models) return;
            var st = loadLocalGeminiSessionState();
            var nowMs = Date.now();
            GEMINI_FALLBACK_LIST.forEach(function(mName) {{
                var sm = serverState.models[mName];
                if (!sm || !st.models[mName]) return;
                var lm = st.models[mName];
                lm.rpd_used = Math.max(lm.rpd_used || 0, sm.rpd_used || 0);
                if (sm.cooldown_remaining_sec > 0) {{
                    var srvCdMs = nowMs + (sm.cooldown_remaining_sec * 1000);
                    if (srvCdMs > (lm.cooldown_until_ms || 0)) lm.cooldown_until_ms = srvCdMs;
                }}
                if (sm.block_type === "RPD" || sm.rpd_reset_remaining_sec > 0) {{
                    lm.rpd_locked_until_ms = getNextUtcMidnightMs();
                    lm.last_error_type = "RPD_EXHAUSTED";
                }}
            }});
            saveLocalGeminiSessionState(st);
            updateGeminiUiAndButtonState();
        }}

        function evaluateModelBlockInfo(modelName, st, nowMs) {{
            var cfg = GEMINI_MODELS_CONFIG[modelName];
            if (cfg && cfg.simulated_disabled) {{
                return {{
                    isBlocked: true,
                    type: "UNSUPPORTED_OR_FULL",
                    remainSec: 0,
                    shortReason: "เต็ม · " + cfg.rpd_limit + "/" + cfg.rpd_limit + " RPD",
                    tooltip: "ไม่สามารถเลือกโมเดลนี้ได้: " + cfg.simulated_reason
                }};
            }}
            var m = (st.models && st.models[modelName]) ? st.models[modelName] : null;
            if (!m) return {{ isBlocked: false, type: "READY", remainSec: 0, shortReason: "ว่าง", tooltip: "" }};

            var cdRemSec = m.cooldown_until_ms > nowMs ? Math.ceil((m.cooldown_until_ms - nowMs) / 1000) : 0;
            var isRpdFull = (m.rpd_used >= m.rpd_limit) || (m.rpd_locked_until_ms > nowMs);
            var rpdRemSec = isRpdFull ? Math.max(1, Math.ceil((getNextUtcMidnightMs() - nowMs) / 1000)) : 0;

            if (isRpdFull) {{
                return {{
                    isBlocked: true,
                    type: "RPD",
                    remainSec: rpdRemSec,
                    shortReason: "เต็ม · " + m.rpd_used + "/" + m.rpd_limit + " RPD",
                    tooltip: "โควต้ารายวันเต็ม (" + m.rpd_used + "/" + m.rpd_limit + " RPD) รีเซ็ตเวลา 07:00 น. (อีก " + formatCountdownThaiHMS(rpdRemSec) + ")"
                }};
            }}
            if (cdRemSec > 0) {{
                var reasonTxt = m.last_error_type === "TOKEN_LIMIT" ? "Token Limit (TPM)" : "Rate Limit (429)";
                return {{
                    isBlocked: true,
                    type: "COOLDOWN_60S",
                    remainSec: cdRemSec,
                    shortReason: "เต็มชั่วคราว · " + cdRemSec + "s",
                    tooltip: "ติด " + reasonTxt + " โมเดล " + modelName + " ปลดล็อกใน " + cdRemSec + " วินาที"
                }};
            }}
            return {{
                isBlocked: false,
                type: "READY",
                remainSec: 0,
                shortReason: "ว่าง · " + (m.rpd_limit - m.rpd_used) + "/" + m.rpd_limit + " RPD",
                tooltip: "โมเดล " + modelName + " พร้อมใช้งาน (ใช้ไป " + m.rpd_used + "/" + m.rpd_limit + " RPD)"
            }};
        }}

        // คืนค่าโมเดลที่ฉลาดที่สุดที่พร้อมใช้งานในขณะนั้นตามลำดับ GEMINI_FALLBACK_LIST
        function getNextAvailableFallbackModel(st, nowMs) {{
            for (var i = 0; i < GEMINI_FALLBACK_LIST.length; i++) {{
                var candidate = GEMINI_FALLBACK_LIST[i];
                var info = evaluateModelBlockInfo(candidate, st, nowMs);
                if (!info.isBlocked) return candidate;
            }}
            return "";
        }}

        function renderCustomModelSelectorUI(st, nowMs, selectedMode, activeRoutedModel) {{
            var menuEl = document.getElementById("geminiCustomModelMenu");
            var trigIcon = document.getElementById("geminiTriggerIcon");
            var trigName = document.getElementById("geminiTriggerName");
            var trigDesc = document.getElementById("geminiTriggerDesc");
            var trigBadge = document.getElementById("geminiTriggerBadge");
            var summaryPill = document.getElementById("geminiCollapsedSummaryPill");

            var effectiveModelId = (selectedMode === "auto_fallback" && activeRoutedModel) ? activeRoutedModel : selectedMode;
            var selCfg = GEMINI_MODELS_CONFIG[selectedMode] || GEMINI_MODELS_CONFIG["gemini-3.5-pro-exp"];
            var activeCfg = GEMINI_MODELS_CONFIG[effectiveModelId] || selCfg;

            if (trigIcon) trigIcon.innerHTML = selCfg.iconSvg || MODEL_MONO_ICONS.sparkle;
            if (trigName) {{
                trigName.textContent = (selectedMode === "auto_fallback" && activeRoutedModel)
                    ? ("Auto Fallback · " + activeCfg.title)
                    : (selCfg.title || selectedMode);
            }}
            if (trigDesc) {{
                trigDesc.textContent = (selectedMode === "auto_fallback" && activeRoutedModel)
                    ? ("เลือกโมเดลที่ฉลาดที่สุดที่ว่างอัตโนมัติ: " + activeCfg.title)
                    : (selCfg.desc || "");
            }}
            if (summaryPill) {{
                summaryPill.textContent = activeCfg.title + (selectedMode === "auto_fallback" ? " · Auto Fallback" : (" · " + activeCfg.role));
            }}

            if (trigBadge) {{
                trigBadge.className = "model-trigger-badge";
                if (selectedMode === "auto_fallback") {{
                    if (activeRoutedModel) {{
                        var rtSt = st.models[activeRoutedModel] || {{ rpd_used: 0, rpd_limit: activeCfg.rpd_limit }};
                        trigBadge.textContent = "ว่าง · " + (rtSt.rpd_limit - rtSt.rpd_used) + " RPD";
                    }} else {{
                        trigBadge.textContent = "เต็มทุกโมเดล";
                        trigBadge.classList.add("is-full");
                    }}
                }} else {{
                    var selBlk = evaluateModelBlockInfo(selectedMode, st, nowMs);
                    var mSt = st.models[selectedMode] || {{ rpd_used: 0, rpd_limit: selCfg.rpd_limit }};
                    if (selBlk.isBlocked) {{
                        trigBadge.textContent = selBlk.type === "COOLDOWN_60S" ? ("เต็ม · " + selBlk.remainSec + "s") : "เต็ม · RPD";
                        trigBadge.classList.add("is-full");
                    }} else {{
                        trigBadge.textContent = "ว่าง · " + (mSt.rpd_limit - mSt.rpd_used) + " RPD";
                    }}
                }}
            }}

            if (!menuEl) return;
            menuEl.innerHTML = "";

            GEMINI_SELECTOR_ORDER.forEach(function(mId) {{
                var cfg = GEMINI_MODELS_CONFIG[mId];
                if (!cfg) return;
                var blkInfo = (mId === "auto_fallback")
                    ? {{ isBlocked: false, type: "READY", remainSec: 0, tooltip: "" }}
                    : evaluateModelBlockInfo(mId, st, nowMs);

                var isChecked = (mId === selectedMode) || (selectedMode === "auto_fallback" && mId === activeRoutedModel);

                var card = document.createElement("div");
                card.className = "model-option-card" + (isChecked ? " is-selected" : "") + (blkInfo.isBlocked ? " is-disabled" : "");
                card.setAttribute("role", "option");
                card.setAttribute("aria-selected", isChecked ? "true" : "false");
                card.setAttribute("aria-disabled", blkInfo.isBlocked ? "true" : "false");

                var quotaText = "Auto Routing";
                var quotaCls = "model-opt-quota";
                if (mId === "auto_fallback") {{
                    if (activeRoutedModel) {{
                        quotaText = "ว่าง · Auto";
                    }} else {{
                        quotaText = "เต็มทุกโมเดล";
                        quotaCls += " is-full";
                    }}
                }} else {{
                    var mObj = st.models[mId] || {{ rpd_used: 0, rpd_limit: cfg.rpd_limit }};
                    if (blkInfo.type === "UNSUPPORTED_OR_FULL" || blkInfo.type === "RPD") {{
                        quotaText = "เต็ม · " + mObj.rpd_used + "/" + mObj.rpd_limit + " RPD";
                        quotaCls += " is-full";
                    }} else if (blkInfo.type === "COOLDOWN_60S") {{
                        quotaText = "เต็ม · " + blkInfo.remainSec + "s";
                        quotaCls += " is-full";
                    }} else {{
                        quotaText = "ว่าง · " + (mObj.rpd_limit - mObj.rpd_used) + "/" + mObj.rpd_limit + " RPD";
                    }}
                }}

                var tooltipHtml = blkInfo.isBlocked
                    ? '<div class="model-opt-tooltip">' + blkInfo.tooltip + '</div>'
                    : '';

                var checkHtml = isChecked
                    ? '<span class="model-opt-check" title="เลือกใช้งาน">' + MODEL_MONO_ICONS.check + '</span>'
                    : '<span class="model-opt-check" style="opacity:0;"></span>';

                card.innerHTML =
                    tooltipHtml +
                    '<div class="model-opt-left">' +
                        '<span class="model-opt-icon">' + (cfg.iconSvg || MODEL_MONO_ICONS.sparkle) + '</span>' +
                        '<div class="model-opt-info">' +
                            '<div class="model-opt-title-row">' +
                                '<span class="model-opt-title">' + cfg.title + '</span>' +
                            '</div>' +
                            '<span class="model-opt-sub">' + cfg.desc + '</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="model-opt-right">' +
                        '<span class="' + quotaCls + '">' + quotaText + '</span>' +
                        checkHtml +
                    '</div>';

                if (!blkInfo.isBlocked) {{
                    card.addEventListener("click", function(ev) {{
                        ev.preventDefault();
                        ev.stopPropagation();
                        selectCustomGeminiModel(mId);
                    }});
                }}
                menuEl.appendChild(card);
            }});
        }}

        function updateGeminiUiAndButtonState() {{
            var promptTa = document.getElementById("geminiCustomPromptInput");
            if (promptTa && !promptTa.value) promptTa.value = DEFAULT_ENGINEERING_PROMPT_JS;
            syncAiToolButtonsActiveState();

            var st = loadLocalGeminiSessionState();
            var nowMs = Date.now();
            var selEl = document.getElementById("geminiModelSelect");
            var selectedMode = selEl ? selEl.value : "gemini-3.5-pro-exp";
            var quotaTxt = document.getElementById("geminiSelectedQuotaText");

            var nextAvailableModel = getNextAvailableFallbackModel(st, nowMs);
            var autoSwitchedNotice = "";

            // หากโมเดลที่ฉลาดที่สุดที่เลือกไว้ไม่พร้อมใช้งานหรือโควต้าเต็ม ให้สลับไปยังโมเดลที่ฉลาดรองลงมาอัตโนมัติ
            if (selectedMode !== "auto_fallback" && autoFallbackSmartRoutingEnabled) {{
                var currentCheck = evaluateModelBlockInfo(selectedMode, st, nowMs);
                if (currentCheck.isBlocked && nextAvailableModel && nextAvailableModel !== selectedMode) {{
                    var prevModelTitle = (GEMINI_MODELS_CONFIG[selectedMode] || {{}}).title || selectedMode;
                    selectedMode = nextAvailableModel;
                    if (selEl) selEl.value = nextAvailableModel;
                    autoSwitchedNotice = "สลับจาก " + prevModelTitle + " ไปยัง " + GEMINI_MODELS_CONFIG[nextAvailableModel].title + " อัตโนมัติ";
                }}
            }}

            renderCustomModelSelectorUI(st, nowMs, selectedMode, nextAvailableModel);

            var buttonBlocked = false;
            var activeTooltip = "";

            if (selectedMode === "auto_fallback") {{
                var blockedInfos = [];
                GEMINI_FALLBACK_LIST.forEach(function(mName) {{
                    var inf = evaluateModelBlockInfo(mName, st, nowMs);
                    if (inf.isBlocked) blockedInfos.push(inf);
                }});
                if (nextAvailableModel) {{
                    buttonBlocked = false;
                    var routedTitle = (GEMINI_MODELS_CONFIG[nextAvailableModel] || {{}}).title || nextAvailableModel;
                    if (quotaTxt) quotaTxt.textContent = "ใช้งาน: " + routedTitle;
                    if (blockedInfos.length > 0) {{
                        activeTooltip = "Auto Fallback เลือกใช้ " + routedTitle + " อัตโนมัติ (" + blockedInfos[0].tooltip + ")";
                    }}
                }} else {{
                    buttonBlocked = true;
                    var rpdAll = blockedInfos.every(function(b) {{ return b.type === "RPD"; }});
                    if (rpdAll && blockedInfos.length > 0) {{
                        activeTooltip = "ติดโควต้ารายวัน (RPD) ครบทุกโมเดล รีเซ็ตเวลา 07:00 น. (อีก " + formatCountdownThaiHMS(blockedInfos[0].remainSec) + ")";
                    }} else {{
                        var minCd = blockedInfos.filter(function(b) {{ return b.type === "COOLDOWN_60S"; }}).sort(function(a,b) {{ return a.remainSec - b.remainSec; }})[0];
                        activeTooltip = minCd
                            ? ("ติด Rate Limit (429) ทุกโมเดลสำรอง ปลดล็อกใน " + minCd.remainSec + " วินาที")
                            : (blockedInfos[0] ? blockedInfos[0].tooltip : "โมเดลติดลิมิตการใช้งาน");
                    }}
                    if (quotaTxt) quotaTxt.textContent = "โควต้าเต็มทุกโมเดล";
                }}
            }} else {{
                var selInfo = evaluateModelBlockInfo(selectedMode, st, nowMs);
                buttonBlocked = selInfo.isBlocked;
                activeTooltip = selInfo.isBlocked ? selInfo.tooltip : autoSwitchedNotice;
                if (quotaTxt) {{
                    var mData = st.models[selectedMode] || {{ rpd_used: 0, rpd_limit: 50 }};
                    quotaTxt.textContent = selInfo.isBlocked
                        ? selInfo.shortReason
                        : (autoSwitchedNotice ? ("สลับอัตโนมัติ · " + (GEMINI_MODELS_CONFIG[selectedMode] || {{}}).title) : ("ว่าง " + (mData.rpd_limit - mData.rpd_used) + "/" + mData.rpd_limit + " RPD"));
                }}
            }}

            var execBtn = document.getElementById("execReportBtn");
            var previewBtn = document.getElementById("previewAiSummaryBtn");
            var tooltipEl = document.getElementById("execReportBtnTooltip");
            var wrapEl = document.getElementById("execReportBtnWrap");

            if (execBtn && !execBtn.dataset.isGenerating) {{
                execBtn.disabled = buttonBlocked;
                execBtn.classList.toggle("is-disabled-limit", buttonBlocked);
            }}
            if (previewBtn && !previewBtn.dataset.isGenerating) {{
                previewBtn.disabled = buttonBlocked;
            }}
            if (tooltipEl) {{
                if (buttonBlocked || activeTooltip) {{
                    tooltipEl.textContent = activeTooltip;
                    tooltipEl.classList.add("has-tooltip");
                    if (wrapEl) wrapEl.setAttribute("title", activeTooltip);
                }} else {{
                    tooltipEl.textContent = "พร้อมสรุปเนื้อหาแบบวิศวกรรมด้วย Gemini AI และส่งออกรายงาน Excel ขนาด A4";
                    tooltipEl.classList.remove("has-tooltip");
                    if (wrapEl) wrapEl.removeAttribute("title");
                }}
            }}
        }}

        function onGeminiModelChange() {{
            updateGeminiUiAndButtonState();
        }}

        function toggleGeminiKeyVisibility() {{
            var inp = document.getElementById("geminiApiKeyInput");
            var icon = document.getElementById("geminiKeyEyeIcon");
            var btn = document.getElementById("geminiKeyToggleBtn");
            if (!inp) return;
            var isHidden = (inp.type === "password");
            inp.type = isHidden ? "text" : "password";
            if (icon) {{
                icon.innerHTML = isHidden
                    ? '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line>'
                    : '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle>';
            }}
            if (btn) {{
                btn.setAttribute("title", isHidden ? "ซ่อน API Key" : "แสดง API Key");
            }}
        }}

        // โหลดเฉพาะคีย์ที่ผู้ใช้เคยตั้งค่าเองเมื่อเริ่มต้น (โดยไม่แสดง Default Key)
        loadSavedUserGeminiApiKey();

        setInterval(function() {{
            var modalEl = document.getElementById("reportModalOverlay");
            if (modalEl && modalEl.classList.contains("show")) {{
                updateGeminiUiAndButtonState();
            }}
        }}, 1000);

        function normalizeEngineeringClauseJS(text) {{
            var s = String(text || "").trim();
            s = s.replace(/^[•\\-\\s✅❌☑☒✔✘❗️⚠️]+/, "").replace(/[✅❌☑☒✔✘❗️⚠️]+/g, " ");
            s = s.replace(/\\bNo\\s*AC\\b/gi, "ไม่มีไฟเลี้ยง AC");
            s = s.replace(/อุปกรณ์สื่อสาร\\s+ปกติ/g, "อุปกรณ์สื่อสารทำงานปกติ");
            s = s.replace(/ระบบสื่อสาร\\s*ONLINE/gi, "ระบบสื่อสารสามารถเชื่อมต่อ ONLINE ได้ตามปกติ");
            s = s.replace(/ทดแทนตัวที่ชำรุด\\s*DAU\\s*ที่ชำรุด/gi, "ทดแทนชุดเดิมที่ชำรุด");
            s = s.replace(/DAU\\/Cellular\\s*Router\\s*ชำรุด/gi, "อุปกรณ์ DAU และ Cellular Router ชำรุด");
            s = s.replace(/ชํารุด/g, "ชำรุด").replace(/สือสาร/g, "สื่อสาร").replace(/เเบตเตอรี่/g, "แบตเตอรี่");
            s = s.replace(/\\s+/g, " ").replace(/^[ ,;|\\-]+|[ ,;|\\-]+$/g, "").trim();
            return s;
        }}

        function categorizeRowDetailJS(rawDetail, respStr, statusStr) {{
            var txt = String(rawDetail || "").trim();
            var stClean = String(statusStr || "").trim();
            var respClean = String(respStr || "").trim();
            var dept = resolveDepartmentForRowJS(respClean, txt);
            var deptLabel = dept === "PBO" ? "ผบอ." : "ผอส.";

            if (txt.startsWith("เนื่องจาก") && txt.indexOf("ทั้งนี้ ในระยะต่อไปทางแผนก") !== -1) {{
                return txt.replace(/[✅❌•\\-]+/g, " ").replace(/\\s+/g, " ").trim();
            }}

            if (!txt || txt === "-") {{
                if (/ONLINE/i.test(stClean)) {{
                    if (dept === "PBO") {{
                        return "เนื่องจากตรวจพบการแจ้งเตือนสถานะการทำงานของตู้คอนโทรลและชุดควบคุมโทรมาตร (FRTU) จากระบบตรวจวัด จึงได้เข้าตรวจสอบระบบไฟเลี้ยง หม้อแปลงแรงดัน (VT 22kV) และอุปกรณ์สวิตช์เกียร์ในความรับผิดชอบของแผนก ผบอ. ส่งผลให้ปัจจุบันตู้คอนโทรลและอุปกรณ์สวิตช์เกียร์สามารถทำงานและเชื่อมต่อระบบได้ตามปกติ ทั้งนี้ ในระยะต่อไปทางแผนก ผบอ. จะติดตามเฝ้าระวังเสถียรภาพของระบบจ่ายไฟและฮาร์ดแวร์ภายในตู้คอนโทรลตามแผนบำรุงรักษา";
                    }}
                    return "เนื่องจากตรวจพบสภาวะการสูญเสียการสื่อสารข้อมูล (Communication Loss) ระหว่างตู้ FRTU กับศูนย์ควบคุม SCADA จึงได้เข้าตรวจสอบอุปกรณ์สื่อสาร (Modem/Router) ซิมการ์ด และระดับสัญญาณเครือข่ายในความรับผิดชอบของแผนก ผอส. ส่งผลให้ปัจจุบันการเชื่อมต่อโปรโตคอลและระบบสื่อสารข้อมูลกลับมาออนไลน์ได้ตามปกติ ทั้งนี้ ในระยะต่อไปทางแผนก ผอส. จะเฝ้าระวังคุณภาพสัญญาณเครือข่ายและเสถียรภาพของการเชื่อมต่อสื่อสารอย่างต่อเนื่อง";
                }}
                if (dept === "PBO") {{
                    return "เนื่องจากตรวจพบความผิดปกติของอุปกรณ์ภายในตู้คอนโทรลและระบบจ่ายไฟทำให้มีสถานะ " + (stClean || "Offline") + " จึงได้เข้าตรวจสอบเบื้องต้นในส่วนของชุดควบคุมโทรมาตร (FRTU) คอนเวอร์เตอร์ และระบบแบตเตอรี่ ส่งผลให้ปัจจุบันตู้คอนโทรลและอุปกรณ์สวิตช์เกียร์ยังคงมีสถานะ " + (stClean || "Offline") + " ทั้งนี้ ในระยะต่อไปทางแผนก ผบอ. จะเข้าตรวจสอบเชิงลึกและซ่อมแซมหรือเปลี่ยนอุปกรณ์ฮาร์ดแวร์ที่ชำรุดเพื่อให้ระบบกลับมาใช้งานได้ตามปกติ";
                }}
                return "เนื่องจากตรวจพบปัญหาการเชื่อมต่อระบบสื่อสารข้อมูลขัดข้องทำให้มีสถานะ " + (stClean || "Offline") + " จึงได้เข้าตรวจสอบเบื้องต้นในส่วนของอุปกรณ์สื่อสาร (Modem/Router) สายสัญญาณ และแผงสัญญาณเสาอากาศ ส่งผลให้ปัจจุบันสถานะการเชื่อมต่อสื่อสารยังคงเป็น " + (stClean || "Offline") + " ทั้งนี้ ในระยะต่อไปทางแผนก ผอส. จะเข้าตรวจสอบแก้ไขระบบสื่อสารและปรับปรุงการเชื่อมต่อโปรโตคอลกับศูนย์ควบคุม SCADA ให้กลับมาออนไลน์โดยเร็ว";
            }}

            var rawTokens = txt.split(/[\\n\\r;|]+|,\\s*|\\s+(?=เปลี่ยน|ดำเนินการ|ตรวจสอบ|รีเซ็ต|ตั้งค่า|ระบบสื่อสาร\\s*ONLINE|อุปกรณ์สื่อสาร\\s*ปกติ|No\\s*AC)/i)
                .map(function(s) {{ return s.trim(); }})
                .filter(Boolean);

            var causes = [], actions = [], results = [];
            var causeRe = /ชำรุด|เสีย|พัง|แตก|ขาด|เสื่อม|ค้าง|หลวม|ขัดข้อง|ไม่มีไฟ|NO AC|OFFLINE|ALARM|FAULT|ไม่ทำงาน|กระพริบ|ต่ำ|รั่ว|ไหม้|มีปัญหา|OPEN|TRIP|ไม่จ่าย|ดับ|ผิดปกติ/i;
            var actionRe = /เปลี่ยน|แก้ไข|รีเซ็ต|RESET|RESTART|ทำความสะอาด|ขัน|ปรับ|ตั้งค่า|จูน|ตรวจสอบ|วัด|เช็ค|ทดลอง|ย้ำสาย|ซ่อม|ตัดต่อ|ดำเนินการ|สลับ|ทริม|ปลด|สับ/i;
            var resultRe = /ใช้งานได้|ทำงานปกติ|ปกติ|ONLINE|สำเร็จ|เรียบร้อย|ยังไม่ออนไลน์|รอ |ส่งต่อ|ใช้ได้แล้ว|CONNECTING/i;

            function addUniqueClause(targetArr, clause) {{
                var cNorm = normalizeEngineeringClauseJS(clause);
                if (!cNorm) return;
                var cKey = cNorm.toLowerCase().replace(/\\s+/g, "");
                for (var i = 0; i < targetArr.length; i++) {{
                    var exKey = targetArr[i].toLowerCase().replace(/\\s+/g, "");
                    if (cKey === exKey || exKey.includes(cKey) || (cKey.includes("ไม่มีไฟเลี้ยงac") && exKey.includes("ไม่มีไฟac"))) return;
                    if (cKey.includes(exKey)) {{
                        targetArr[i] = cNorm;
                        return;
                    }}
                }}
                targetArr.push(cNorm);
            }}

            rawTokens.forEach(function(tk) {{
                var cleanTk = normalizeEngineeringClauseJS(tk);
                if (!cleanTk) return;

                if (cleanTk.includes("อุปกรณ์สื่อสารทำงานปกติ") && /ไม่มีไฟ|OPEN|ชำรุด/i.test(cleanTk)) {{
                    var pSplit = cleanTk.split("อุปกรณ์สื่อสารทำงานปกติ");
                    if (pSplit[0] && pSplit[0].trim()) addUniqueClause(causes, pSplit[0].trim());
                    addUniqueClause(results, "อุปกรณ์สื่อสารทำงานปกติ");
                    if (pSplit[1] && pSplit[1].trim()) {{
                        if (resultRe.test(pSplit[1])) addUniqueClause(results, pSplit[1].trim());
                        else addUniqueClause(causes, pSplit[1].trim());
                    }}
                    return;
                }}

                if (/^(เปลี่ยน|ดำเนินการ|จึงดำเนินการ|ได้ดำเนินการ|รีเซ็ต|ตั้งค่า|ซ่อม|ย้ำสาย|ทำความสะอาด|ปรับตั้ง)/i.test(cleanTk)) {{
                    if (cleanTk.includes("ชำรุด") && cleanTk.includes("ทดแทน")) {{
                        var subParts = cleanTk.split(/(?=Port\\s*LAN|อุปกรณ์\\s*DAU|เนื่องจาก)/i);
                        addUniqueClause(actions, subParts[0]);
                        for (var sIdx = 1; sIdx < subParts.length; sIdx++) {{
                            addUniqueClause(causes, subParts[sIdx]);
                        }}
                    }} else {{
                        addUniqueClause(actions, cleanTk);
                    }}
                }} else if (causeRe.test(cleanTk) && !cleanTk.startsWith("ระบบสื่อสารสามารถเชื่อมต่อ")) {{
                    addUniqueClause(causes, cleanTk);
                }} else if (actionRe.test(cleanTk)) {{
                    addUniqueClause(actions, cleanTk);
                }} else if (resultRe.test(cleanTk)) {{
                    addUniqueClause(results, cleanTk);
                }} else if (causes.length === 0) {{
                    addUniqueClause(causes, cleanTk);
                }} else {{
                    addUniqueClause(actions, cleanTk);
                }}
            }});

            if (causes.length === 0) {{
                causes.push(dept === "PBO"
                    ? "ชุดควบคุมโทรมาตร (FRTU) และอุปกรณ์ภายในตู้คอนโทรลเกิดความผิดปกติ"
                    : "ระบบสื่อสารข้อมูลเกิดสภาวะสูญเสียการเชื่อมต่อ (Communication Loss) กับศูนย์ควบคุม SCADA");
            }}
            if (actions.length === 0) {{
                actions.push(dept === "PBO"
                    ? "เข้าตรวจสอบสภาพฮาร์ดแวร์ภายในตู้คอนโทรล ระบบแบตเตอรี่ และแหล่งจ่ายไฟแรงดัน 22kV (VT)"
                    : "เข้าตรวจสอบอุปกรณ์สื่อสาร (Modem/Router) สายสัญญาณสื่อสาร แผงสัญญาณเสาอากาศ และซิมการ์ดเบื้องต้น");
            }}
            if (results.length === 0) {{
                if (/ONLINE/i.test(stClean)) {{
                    results.push(dept === "PBO"
                        ? "ปัจจุบันตู้คอนโทรลและอุปกรณ์สวิตช์เกียร์กลับมาทำงานและออนไลน์ได้ตามปกติ"
                        : "ปัจจุบันการเชื่อมต่อโปรโตคอลและระบบสื่อสารข้อมูลกลับมาออนไลน์ได้ตามปกติ");
                }} else {{
                    results.push(dept === "PBO"
                        ? ("สถานะปัจจุบันของตู้คอนโทรลและอุปกรณ์สวิตช์เกียร์ยังคงเป็น " + (stClean || "Offline"))
                        : ("สถานะการเชื่อมต่อสื่อสารปัจจุบันยังคงเป็น " + (stClean || "Offline")));
                }}
            }}

            var nextStepStr = "";
            if (/ONLINE/i.test(stClean) && respClean.indexOf("ผบอ") === -1 && respClean.indexOf("ผอส") === -1) {{
                nextStepStr = dept === "PBO"
                    ? "ติดตามเฝ้าระวังเสถียรภาพของระบบไฟเลี้ยงและอุปกรณ์ฮาร์ดแวร์ภายในตู้คอนโทรลอย่างต่อเนื่อง"
                    : "ติดตามเฝ้าระวังคุณภาพสัญญาณเครือข่ายและเสถียรภาพของการเชื่อมต่อโปรโตคอลกับศูนย์ควบคุม SCADA อย่างต่อเนื่อง";
            }} else {{
                nextStepStr = dept === "PBO"
                    ? "เข้าตรวจสอบเชิงลึกเพื่อซ่อมแซมหรือเปลี่ยนอุปกรณ์ฮาร์ดแวร์ภายในตู้คอนโทรลและระบบไฟเลี้ยงที่ชำรุดให้กลับมาทำงานได้ตามมาตรฐาน"
                    : "เข้าตรวจสอบแก้ไขอุปกรณ์สื่อสาร สายสัญญาณ และตั้งค่าพารามิเตอร์โปรโตคอลเชื่อมต่อกับตู้ FRTU เพื่อให้ระบบกลับมาออนไลน์ได้ตามปกติ";
            }}

            var causeStr = causes.join(" และ").replace(/^(เนื่องจาก|สาเหตุเกิดจาก|เพราะ)\\s*/, "").trim();
            var actionStr = actions.join(" และ").replace(/^(จึงได้ดำเนินการ|จึงดำเนินการ|ได้ดำเนินการ|ดำเนินการ|ทำการ)\\s*/, "").trim();
            var resultStr = results.join(" และ").replace(/^(ส่งผลให้|ผลลัพธ์คือ|ทำให้)\\s*/, "").trim();

            var out = "เนื่องจาก" + causeStr + " จึงได้" + actionStr + " ส่งผลให้" + resultStr + " ทั้งนี้ ในระยะต่อไปทางแผนก " + deptLabel + " จะดำเนินการ" + nextStepStr;
            return out.replace(/[✅❌•\\-]+/g, " ").replace(/\\s+/g, " ").trim();
        }}

        function buildDeterministicSummaryJS(rows) {{
            var total = rows.length;
            var onlineCnt = 0, connCnt = 0, offCnt = 0, boCnt = 0, poCnt = 0, doneCnt = 0;
            var commIssues = 0, frtuIssues = 0, powerIssues = 0;

            rows.forEach(function(r) {{
                var st = String(r.status || "").toUpperCase();
                if (st.includes("ONLINE")) onlineCnt++;
                else if (st.includes("CONNECTING")) connCnt++;
                else if (st.includes("OFFLINE")) offCnt++;

                var resp = String(r.resp || "-").trim();
                if (resp.includes("ผบอ")) boCnt++;
                if (resp.includes("ผอส")) poCnt++;
                if (resp === "-" || resp === "" || resp === "ไม่มีค่า") doneCnt++;

                var dUp = String(r.detail || "").toUpperCase();
                if (/สื่อสาร|MODEM|SIM|WRL|OPTIC|สัญญาณ|ROUTER|DAU|LAN/.test(dUp)) commIssues++;
                if (/FRTU|RTU|การ์ด|CONTROL|BOARD|โมดูล|AVR|RECLOSER/.test(dUp)) frtuIssues++;
                if (/แบต|BATTERY|ฟิวส์|FUSE|AC|DC|ไฟเลี้ยง|VT/.test(dUp)) powerIssues++;
            }});
            var otherCnt = Math.max(0, total - onlineCnt - connCnt - offCnt);

            var md =
                "รายงานสรุปเชิงวิศวกรรมผลการตรวจสอบและแก้ไขอุปกรณ์ในระบบ SCADA (รวมทั้งสิ้น " + total + " รายการ)\\n\\n" +
                "จากการเข้าตรวจสอบและวิเคราะห์สาเหตุความผิดปกติของอุปกรณ์ภาคสนามจำนวน " + total + " รายการ พบว่าเกิดจากสาเหตุหลัก 3 ด้าน ได้แก่ " +
                "ปัญหาด้านระบบสื่อสารและโครงข่ายจำนวน " + commIssues + " รายการ ปัญหาด้านชุดควบคุมหลัก (FRTU/AVR/Recloser) จำนวน " + frtuIssues + " รายการ " +
                "และปัญหาด้านระบบแหล่งจ่ายไฟหรือหม้อแปลงแรงดัน (AC/DC/VT/Battery) จำนวน " + powerIssues + " รายการ (สาเหตุ) " +
                "จึงได้ดำเนินการเข้าตรวจสอบแก้ไข เปลี่ยนอุปกรณ์ที่ชำรุด ตั้งค่าระบบใหม่ และประสานงานส่งต่อหน่วยงานที่เกี่ยวข้องตามขอบเขตความรับผิดชอบ (การดำเนินการ) " +
                "ส่งผลให้อุปกรณ์สามารถกลับมาเชื่อมต่อออนไลน์และทำงานได้ตามปกติจำนวน " + onlineCnt + " ชุด คิดเป็นร้อยละ " + (total ? (onlineCnt/total*100).toFixed(1) : "0.0") + " " +
                "อยู่ในสถานะกำลังเชื่อมต่อ (Connecting) จำนวน " + connCnt + " ชุด และยังคงสถานะออฟไลน์ (Offline) เพื่อรอการเข้าแก้ไขต่อเนื่องโดย ผบอ.กบษ.ฉ.2 จำนวน " + boCnt + " รายการ " +
                "และรอ ผอส.กสฟ.ฉ.2 จำนวน " + poCnt + " รายการ (ผลลัพธ์)";

            return {{
                summary_markdown: md,
                metrics: {{
                    total: total,
                    online: onlineCnt,
                    connecting: connCnt,
                    offline: offCnt,
                    other: otherCnt,
                    wait_bo: boCnt,
                    wait_po: poCnt,
                    completed: doneCnt,
                    comm_issues: commIssues,
                    frtu_issues: frtuIssues,
                    power_issues: powerIssues
                }},
                categories: [
                    {{
                        category: "1. หมวดสถานะอุปกรณ์หลังการเข้าตรวจสอบ (Post-Inspection Status)",
                        items: [
                            "อุปกรณ์กลับมา Online ปกติ: " + onlineCnt + " ชุด",
                            "สถานะ Connecting: " + connCnt + " ชุด",
                            "สถานะ Offline: " + offCnt + " ชุด",
                            "สถานะอื่นๆ / Initializing: " + otherCnt + " ชุด"
                        ]
                    }},
                    {{
                        category: "2. หมวดสาเหตุและอาการชำรุดที่ตรวจพบ (Fault Categorization)",
                        items: [
                            "ระบบสื่อสารและโครงข่าย (DAU / Cellular Router / Modem / SIM / WRL / สายสัญญาณ): " + commIssues + " รายการ",
                            "อุปกรณ์ชุดควบคุมหลัก (FRTU / Recloser / AVR / การ์ดควบคุม): " + frtuIssues + " รายการ",
                            "ระบบแหล่งจ่ายไฟและหม้อแปลงแรงดัน (VT / Battery / Fuse / AC-DC): " + powerIssues + " รายการ"
                        ]
                    }},
                    {{
                        category: "3. หมวดหน่วยงานผู้รับผิดชอบดำเนินการต่อ (Action Assignment)",
                        items: [
                            "รอ ผบอ.กบษ.ฉ.2 เข้าดำเนินการแก้ไข: " + boCnt + " รายการ",
                            "รอ ผอส.กสฟ.ฉ.2 เข้าดำเนินการแก้ไข: " + poCnt + " รายการ",
                            "ดำเนินการแก้ไขเสร็จสิ้น (-): " + doneCnt + " รายการ"
                        ]
                    }}
                ]
            }};
        }}

        async function callDirectGeminiRestFromBrowser(promptText, preferredModel, apiKey, sysPromptOverride) {{
            var effectiveKey = String(apiKey || INTERNAL_FALLBACK_GEMINI_KEY).trim();
            if (!effectiveKey) {{
                throw new Error("กรุณาระบุ Gemini API Key ของตนเองในหน้าต่างตั้งค่า AI ก่อนเรียกใช้งาน");
            }}
            var st = loadLocalGeminiSessionState();
            var nowMs = Date.now();
            // เรียงลำดับจากโมเดลที่ฉลาดที่สุดไปยังโมเดลที่ฉลาดรองลงมาเรื่อยๆ
            var order = (preferredModel === "auto_fallback" || !GEMINI_MODELS_CONFIG[preferredModel])
                ? GEMINI_FALLBACK_LIST.slice()
                : [preferredModel].concat(GEMINI_FALLBACK_LIST.filter(function(m) {{ return m !== preferredModel; }}));

            var sysPrompt = sysPromptOverride || getActiveEngineeringPrompt();
            var lastErr = "";
            for (var i = 0; i < order.length; i++) {{
                var logicalModel = order[i];
                var blk = evaluateModelBlockInfo(logicalModel, st, nowMs);
                if (blk.isBlocked) {{
                    continue;
                }}
                var candidates = GEMINI_MODELS_CONFIG[logicalModel].sdk_candidates || [];
                for (var c = 0; c < candidates.length; c++) {{
                    var sdkId = candidates[c];
                    var url = "https://generativelanguage.googleapis.com/v1beta/models/" + encodeURIComponent(sdkId) + ":generateContent?key=" + encodeURIComponent(effectiveKey);
                    try {{
                        var resp = await fetch(url, {{
                            method: "POST",
                            headers: {{ "Content-Type": "application/json" }},
                            body: JSON.stringify({{
                                systemInstruction: {{ parts: [{{ text: sysPrompt }}] }},
                                contents: [{{ parts: [{{ text: promptText }}] }}],
                                generationConfig: {{ temperature: 0.2 }}
                            }})
                        }});
                        if (resp.status === 404) continue;
                        if (resp.status === 429) {{
                            var errBody429 = await resp.text();
                            var isRpd = /perday|per_day|daily|rpd/i.test(errBody429);
                            markLocalModelLimit(logicalModel, isRpd ? "RPD_EXHAUSTED" : "RATE_LIMIT_429", errBody429);
                            lastErr = "HTTP 429 Rate Limit on " + logicalModel;
                            break;
                        }}
                        if (!resp.ok) {{
                            var errBody = await resp.text();
                            if (/token|tpm|too large|payload/i.test(errBody)) {{
                                throw new Error("TOKEN_LIMIT_EXCEEDED: " + errBody);
                            }}
                            lastErr = "HTTP " + resp.status + ": " + errBody;
                            break;
                        }}
                        var data = await resp.json();
                        var textOut = ((((data.candidates || [])[0] || {{}}).content || {{}}).parts || []).map(function(p) {{ return p.text || ""; }}).join("\\n").trim();
                        if (textOut) {{
                            incrementLocalModelRpd(logicalModel);
                            return {{ text: textOut, used_model: logicalModel, sdk_model: sdkId }};
                        }}
                    }} catch (err) {{
                        if (String(err.message || "").includes("TOKEN_LIMIT_EXCEEDED")) throw err;
                        lastErr = String(err.message || err);
                    }}
                }}
            }}
            throw new Error(lastErr || "ไม่สามารถเชื่อมต่อ Gemini API ได้ในขณะนี้");
        }}

        async function summarizeInspectionRowsWithGeminiJS(filteredRows) {{
            var selEl = document.getElementById("geminiModelSelect");
            var preferredModel = selEl ? selEl.value : "gemini-3.5-pro-exp";
            var keyEl = document.getElementById("geminiApiKeyInput");
            var userTypedKey = (keyEl && keyEl.value.trim()) ? keyEl.value.trim() : "";
            var customPrompt = getActiveEngineeringPrompt();

            var baseExec = buildDeterministicSummaryJS(filteredRows);
            var workingRows = filteredRows.map(function(r) {{
                var copy = Object.assign({{}}, r);
                copy.ai_detail = categorizeRowDetailJS(r.detail, r.resp, r.status);
                return copy;
            }});

            // 1. เรียกผ่าน Flask Backend API (/api/gemini/summarize) ก่อน (หากผู้ใช้ไม่ได้กรอกคีย์ จะส่งค่าว่างให้ Backend ใช้คีย์ภายใน)
            try {{
                var resp = await fetch("/api/gemini/summarize", {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify({{
                        rows: workingRows,
                        model: preferredModel,
                        api_key: userTypedKey,
                        custom_prompt: customPrompt
                    }})
                }});
                if (resp.status === 200 || resp.status === 429) {{
                    var srvData = await resp.json();
                    if (srvData.session_state) syncBackendStatusToLocal(srvData.session_state);
                    if (srvData.rows && srvData.rows.length === workingRows.length) {{
                        return srvData;
                    }}
                }}
            }} catch (srvErr) {{
                // กรณีรันไฟล์ HTML แบบ Standalone บน Windows (.bat) จะประมวลผลผ่าน Browser REST API โดยตรง
            }}

            // 2. Fallback ทำ Auto Chunking + เรียก Gemini API โดยตรง
            var maxRowsPerChunk = 20;
            var chunks = [];
            for (var i = 0; i < workingRows.length; i += maxRowsPerChunk) {{
                chunks.push(workingRows.slice(i, i + maxRowsPerChunk));
            }}

            var chunkSummaries = [];
            var usedModelName = preferredModel === "auto_fallback" ? "gemini-3.5-pro-exp" : preferredModel;
            var warningMsg = "";

            for (var cIdx = 0; cIdx < chunks.length; cIdx++) {{
                var ch = chunks[cIdx];
                var pboCountInChunk = ch.filter(function(r) {{ return resolveDepartmentForRowJS(r.resp, r.detail) === "PBO"; }}).length;
                var chunkDeptCode = (pboCountInChunk >= Math.ceil(ch.length / 2)) ? "PBO" : "POS";
                var chunkDeptLabel = chunkDeptCode === "PBO" ? "ผบอ." : "ผอส.";
                var deptSysPrompt = getDepartmentSystemPromptJS(chunkDeptCode, customPrompt);
                var linesStr = ch.map(function(r, idx) {{
                    return "[" + idx + "] Site: " + r.site + " | สถานที่: " + r.loc + " | รายละเอียดเดิม: " + r.detail + " | ผู้รับผิดชอบ: " + r.resp + " | สถานะสุดท้าย: " + r.status;
                }}).join("\\n");

                var prompt =
                    "โปรดวิเคราะห์ข้อมูลดิบจากรายงานการซ่อมบำรุงในแต่ละรายการต่อไปนี้ และเรียบเรียงเป็น 'รายงานเชิงวิศวกรรม' แบบต่อเนื่อง 1 ย่อหน้า (Flowing Narrative) ตามโครงสร้างบังคับ:\\n" +
                    "'เนื่องจาก... (สาเหตุ) จึงได้ดำเนินการ... (การดำเนินการ) ส่งผลให้... (ผลลัพธ์) ทั้งนี้ ในระยะต่อไปทางแผนก " + chunkDeptLabel + " จะดำเนินการ... (การดำเนินการต่อไป)'\\n" +
                    "ห้ามใช้สัญลักษณ์ (เช่น ✅, ❌, •, -) หรือการขึ้นบรรทัดใหม่แบบหัวข้อย่อยเด็ดขาด\\n" +
                    'ตอบเป็น JSON เท่านั้น: {{"rows":[{{"idx":0,"clean_detail":"เนื่องจาก... จึงได้ดำเนินการ... ส่งผลให้... ทั้งนี้ ในระยะต่อไปทางแผนก ' + chunkDeptLabel + ' จะดำเนินการ..."}}],"chunk_summary":"..."}}\\n\\n' +
                    linesStr;
                try {{
                    var aiRes = await callDirectGeminiRestFromBrowser(prompt, preferredModel, userTypedKey, deptSysPrompt);
                    usedModelName = aiRes.used_model;
                    var mJson = aiRes.text.match(/\\{{[\\s\\S]*\\}}/);
                    if (mJson) {{
                        var parsed = JSON.parse(mJson[0]);
                        (parsed.rows || []).forEach(function(item) {{
                            if (typeof item.idx === "number" && ch[item.idx] && item.clean_detail) {{
                                ch[item.idx].ai_detail = String(item.clean_detail).trim();
                                ch[item.idx].detail = ch[item.idx].ai_detail;
                            }}
                        }});
                        if (parsed.chunk_summary) chunkSummaries.push(parsed.chunk_summary);
                    }}
                }} catch (err) {{
                    warningMsg = String(err.message || err);
                    break;
                }}
            }}

            var finalSummaryMd = baseExec.summary_markdown;
            if (chunkSummaries.length > 0 && !warningMsg) {{
                try {{
                    var synthPrompt =
                        "โปรดเขียนรายงานสรุปเชิงวิศวกรรมสำหรับผู้บริหาร (Engineering Executive Report) ที่มีความเป็นมืออาชีพ ถูกหลักภาษา " +
                        "โดยร้อยเรียงเรื่องราวให้ครบถ้วนทั้ง สาเหตุ การดำเนินการ และผลลัพธ์ จากข้อมูลสรุปย่อยต่อไปนี้:\\n- " +
                        chunkSummaries.join("\\n- ");
                    var synthRes = await callDirectGeminiRestFromBrowser(synthPrompt, preferredModel, userTypedKey);
                    if (synthRes.text) finalSummaryMd = synthRes.text;
                    usedModelName = synthRes.used_model;
                }} catch (e) {{}}
            }}

            workingRows.forEach(function(r) {{
                if (r.ai_detail) r.detail = r.ai_detail;
            }});

            return {{
                success: !warningMsg,
                warning: warningMsg,
                used_model: usedModelName,
                chunk_count: chunks.length,
                rows: workingRows,
                summary_markdown: finalSummaryMd,
                categories: baseExec.categories,
                metrics: baseExec.metrics
            }};
        }}

        async function runGeminiPreviewOnly() {{
            var previewBtn = document.getElementById("previewAiSummaryBtn");
            var previewBox = document.getElementById("aiSummaryPreviewBox");
            var previewContent = document.getElementById("aiSummaryPreviewContent");
            var previewTitle = document.getElementById("aiSummaryPreviewTitle");

            var filteredResult = getFilteredReportResult();
            if (!filteredResult.rows || filteredResult.rows.length === 0) {{
                showCustomAlert("ไม่พบข้อมูลรายงานที่ตรงกับตัวกรองที่เลือก");
                return;
            }}

            if (previewBtn) {{
                previewBtn.dataset.isGenerating = "1";
                previewBtn.disabled = true;
                previewBtn.textContent = "⏳ กำลังสรุปด้วย Gemini AI...";
            }}
            if (previewBox && previewContent) {{
                previewBox.classList.add("show");
                previewContent.textContent = "กำลังวิเคราะห์เนื้อหาและเรียบเรียงรายงานแบบวิศวกรรมผ่าน Google Gemini API...";
            }}

            try {{
                var aiRes = await summarizeInspectionRowsWithGeminiJS(filteredResult.rows);
                cachedAiSummaryResult = aiRes;
                if (aiRes && aiRes.rows) {{
                    aiRes.rows.forEach(function(ar) {{
                        var k = getRowSentenceKey(ar);
                        if (ar.ai_detail || ar.detail) {{
                            customRowSentences[k] = ar.ai_detail || ar.detail;
                        }}
                    }});
                    var smBox = document.getElementById("sentenceManagerBox");
                    if (smBox && smBox.classList.contains("show")) {{
                        renderSentenceManagerList();
                    }}
                }}
                if (previewTitle) {{
                    previewTitle.textContent = "✨ รายงานสรุปเชิงวิศวกรรม (โมเดล: " + (aiRes.used_model || "gemini-3.5-pro-exp") + " · " + (aiRes.chunk_count || 1) + " Chunks)";
                }}
                if (previewContent) {{
                    var note = aiRes.warning ? ("⚠️ หมายเหตุ: " + aiRes.warning + "\\n\\n") : "";
                    previewContent.textContent = note + (aiRes.summary_markdown || "");
                }}
            }} catch (err) {{
                if (previewContent) previewContent.textContent = "เกิดข้อผิดพลาด: " + String(err.message || err);
            }} finally {{
                if (previewBtn) {{
                    delete previewBtn.dataset.isGenerating;
                    previewBtn.textContent = "พรีวิวสรุป AI";
                }}
                updateGeminiUiAndButtonState();
            }}
        }}

        async function generateExcelReport() {{
            var btn = document.getElementById('execReportBtn');
            var st = loadLocalGeminiSessionState();
            var selEl = document.getElementById('geminiModelSelect');
            var selectedMode = selEl ? selEl.value : 'gemini-3.5-pro-exp';

            if (selectedMode !== 'auto_fallback') {{
                var blkInfo = evaluateModelBlockInfo(selectedMode, st, Date.now());
                if (blkInfo.isBlocked) {{
                    updateGeminiUiAndButtonState();
                    return;
                }}
            }}

            btn.dataset.isGenerating = "1";
            btn.disabled = true;
            btn.innerHTML = '⏳ กำลังสรุปรายงานวิศวกรรมด้วย Gemini AI และสร้างไฟล์ A4...';

            var params = getCurrentFilterParams();
            var startInput = params.startInput;
            var endInput = params.endInput;
            var selectedWorkers = params.selectedWorkers;
            var includeWorkerCol = params.includeWorkerCol;

            var dateValidationMessage = validateReportDateRange(startInput, endInput);
            if (dateValidationMessage) {{
                showCustomAlert(dateValidationMessage);
                delete btn.dataset.isGenerating;
                btn.innerHTML = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="white" stroke-width="2"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg> สรุปและ Export รายงาน (.xlsx)';
                updateGeminiUiAndButtonState();
                return;
            }}

            var filteredResult = getFilteredReportResult();
            var filteredData = filteredResult.rows;

            if (filteredData.length === 0) {{
                showCustomAlert("ไม่พบข้อมูลรายงานที่ตรงกับช่วงวันที่และตัวกรองที่เลือก");
                delete btn.dataset.isGenerating;
                btn.innerHTML = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="white" stroke-width="2"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg> สรุปและ Export รายงาน (.xlsx)';
                updateGeminiUiAndButtonState();
                return;
            }}

            var useAiEl = document.getElementById("enableGeminiAiCheckbox");
            var useGeminiAi = useAiEl ? useAiEl.checked : true;
            var aiSummaryData = null;

            if (useGeminiAi) {{
                try {{
                    aiSummaryData = await summarizeInspectionRowsWithGeminiJS(filteredData);
                    if (aiSummaryData && aiSummaryData.rows && aiSummaryData.rows.length === filteredData.length) {{
                        filteredData = aiSummaryData.rows;
                    }}
                    var previewBox = document.getElementById("aiSummaryPreviewBox");
                    var previewContent = document.getElementById("aiSummaryPreviewContent");
                    var previewTitle = document.getElementById("aiSummaryPreviewTitle");
                    if (previewBox && previewContent && aiSummaryData) {{
                        previewBox.classList.add("show");
                        if (previewTitle) {{
                            previewTitle.textContent = "✨ รายงานสรุปเชิงวิศวกรรม (โมเดล: " + (aiSummaryData.used_model || "gemini-3.5-pro-exp") + " · " + (aiSummaryData.chunk_count || 1) + " Chunks)";
                        }}
                        previewContent.textContent = aiSummaryData.summary_markdown || "";
                    }}
                }} catch (aiErr) {{
                    aiSummaryData = buildDeterministicSummaryJS(filteredData);
                }}
            }} else {{
                aiSummaryData = buildDeterministicSummaryJS(filteredData);
            }}

            var workbook = new ExcelJS.Workbook();

            // Sheet ที่ 1: ตารางรายการตรวจสอบและแก้ไขอุปกรณ์ (เรียบเรียงแบบวิศวกรรม)
            var ws = workbook.addWorksheet('Report', {{
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

            var lastColLetter = includeWorkerCol ? 'H' : 'G';
            ws.mergeCells('A1:' + lastColLetter + '1');
            var titleCell = ws.getCell('A1');
            titleCell.value = "รายละเอียดการตรวจสอบ/แก้ไขอุปกรณ์ FDCU OFFLine (รายงานเชิงวิศวกรรมด้วย Gemini AI)";
            titleCell.font = {{ name: 'TH SarabunPSK', size: 20, bold: true }};
            titleCell.alignment = {{ vertical: 'middle', horizontal: 'center' }};
            ws.getRow(1).height = 30;

            var headers = includeWorkerCol
                ? ['ลำดับ', 'รหัสสั่งการ\\n(ข้อมูล GIS)', 'Site ID', 'สถานที่\\n(ข้อมูล GIS)', 'รายละเอียดการตรวจสอบ/แก้ไข\\n(สาเหตุ -> การดำเนินการ -> ผลลัพธ์)', 'ผู้รับผิดชอบและดำเนินการแก้ไขต่อไป', 'ผู้ปฏิบัติงาน (ผอส.ฉ.2)', 'สถานะหลังดำเนินการ']
                : ['ลำดับ', 'รหัสสั่งการ\\n(ข้อมูล GIS)', 'Site ID', 'สถานที่\\n(ข้อมูล GIS)', 'รายละเอียดการตรวจสอบ/แก้ไข\\n(สาเหตุ -> การดำเนินการ -> ผลลัพธ์)', 'ผู้รับผิดชอบและดำเนินการแก้ไขต่อไป', 'สถานะหลังดำเนินการ'];

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
                var rKey = getRowSentenceKey(row);
                var cleanDetailText = (customRowSentences[rKey] !== undefined)
                    ? customRowSentences[rKey]
                    : (row.ai_detail || categorizeRowDetailJS(row.detail, row.resp, row.status) || '-');
                var rowValues = includeWorkerCol
                    ? [ i+1, row.cmd, row.site, row.loc, cleanDetailText, row.resp, splitOperatorNames(row.workers).join('\\n') || '-', row.status ]
                    : [ i+1, row.cmd, row.site, row.loc, cleanDetailText, row.resp, row.status ];

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
                ws.getColumn(5).width = 58;
                ws.getColumn(6).width = 28;
                ws.getColumn(7).width = 30;
                ws.getColumn(8).width = 20;
            }} else {{
                ws.getColumn(1).width = 8;
                ws.getColumn(2).width = 20;
                ws.getColumn(3).width = 20;
                ws.getColumn(4).width = 28;
                ws.getColumn(5).width = 68;
                ws.getColumn(6).width = 32;
                ws.getColumn(7).width = 22;
            }}

            // Sheet ที่ 2: รายงานสรุปภาพรวมเชิงวิศวกรรมโดย Google Gemini API (ขนาด A4)
            var wsAi = workbook.addWorksheet('สรุปภาพรวม AI (A4)', {{
                pageSetup: {{
                    paperSize: 9,
                    orientation: 'landscape',
                    fitToPage: true,
                    fitToWidth: 1,
                    fitToHeight: 0,
                    horizontalCentered: true,
                    margins: {{ left: 0.3, right: 0.3, top: 0.4, bottom: 0.4, header: 0.2, footer: 0.2 }}
                }}
            }});

            wsAi.mergeCells('A1:D1');
            var aiTitle = wsAi.getCell('A1');
            aiTitle.value = "รายงานสรุปเชิงวิศวกรรมผลการตรวจเช็คและแก้ไขอุปกรณ์ SCADA (Gemini AI Engineering Report)";
            aiTitle.font = {{ name: 'TH SarabunPSK', size: 20, bold: true, color: {{ argb: 'FF1A73E8' }} }};
            aiTitle.alignment = {{ vertical: 'middle', horizontal: 'center' }};
            wsAi.getRow(1).height = 32;

            wsAi.mergeCells('A2:D2');
            var aiMeta = wsAi.getCell('A2');
            var usedModelStr = (aiSummaryData && aiSummaryData.used_model) ? aiSummaryData.used_model : selectedMode;
            var chunkCntStr = (aiSummaryData && aiSummaryData.chunk_count) ? aiSummaryData.chunk_count : 1;
            aiMeta.value = "โมเดลที่ใช้ประมวลผล: " + usedModelStr + " | จำนวน Chunks อัตโนมัติ: " + chunkCntStr + " ชุด | จำนวนอุปกรณ์ที่วิเคราะห์: " + filteredData.length + " รายการ";
            aiMeta.font = {{ name: 'TH SarabunPSK', size: 15, italic: true }};
            aiMeta.alignment = {{ vertical: 'middle', horizontal: 'center' }};
            wsAi.getRow(2).height = 24;

            var catHeaderRow = wsAi.addRow(['ลำดับหมวด', 'หมวดหมู่การวิเคราะห์เชิงวิศวกรรม (Category)', 'รายละเอียดและสถิติที่ตรวจพบ', 'หมายเหตุ / การติดตามผล']);
            catHeaderRow.height = 30;
            catHeaderRow.eachCell(function(c) {{
                c.font = {{ name: 'TH SarabunPSK', size: 16, bold: true }};
                c.fill = {{ type: 'pattern', pattern: 'solid', fgColor: {{ argb: 'FFE8F0FE' }} }};
                c.alignment = {{ vertical: 'middle', horizontal: 'center', wrapText: true }};
                c.border = {{ top: {{style:'thin'}}, left: {{style:'thin'}}, bottom: {{style:'thin'}}, right: {{style:'thin'}} }};
            }});

            var cats = (aiSummaryData && aiSummaryData.categories) ? aiSummaryData.categories : [];
            cats.forEach(function(catObj, cIdx) {{
                var itemsJoined = (catObj.items || []).map(function(it) {{ return "• " + it; }}).join("\\n");
                var rRow = wsAi.addRow([
                    cIdx + 1,
                    catObj.category || "-",
                    itemsJoined || "-",
                    cIdx === 0 ? "อ้างอิงสถานะหลังตรวจสอบ" : (cIdx === 1 ? "จำแนกจากบันทึกการเข้าแก้ไข" : "แยกตามหน่วยงานรับผิดชอบ")
                ]);
                rRow.eachCell(function(c, colN) {{
                    c.font = {{ name: 'TH SarabunPSK', size: 16, bold: colN === 2 }};
                    c.alignment = {{ vertical: 'top', horizontal: colN === 1 ? 'center' : 'left', wrapText: true }};
                    c.border = {{ top: {{style:'thin'}}, left: {{style:'thin'}}, bottom: {{style:'thin'}}, right: {{style:'thin'}} }};
                }});
            }});

            wsAi.addRow([]);
            var synthTitleRow = wsAi.addRow(['บทสรุปเชิงวิศวกรรมสำหรับผู้บริหาร (Engineering Narrative Summary: สาเหตุ -> การดำเนินการ -> ผลลัพธ์)', '', '', '']);
            var synthRowNum = synthTitleRow.number;
            wsAi.mergeCells('A' + synthRowNum + ':D' + synthRowNum);
            var stCell = wsAi.getCell('A' + synthRowNum);
            stCell.font = {{ name: 'TH SarabunPSK', size: 17, bold: true }};
            stCell.fill = {{ type: 'pattern', pattern: 'solid', fgColor: {{ argb: 'FFF1F3F4' }} }};
            stCell.alignment = {{ vertical: 'middle', horizontal: 'left' }};
            stCell.border = {{ top: {{style:'thin'}}, left: {{style:'thin'}}, bottom: {{style:'thin'}}, right: {{style:'thin'}} }};
            synthTitleRow.height = 28;

            var mdBody = (aiSummaryData && aiSummaryData.summary_markdown) ? aiSummaryData.summary_markdown : "-";
            var synthBodyRow = wsAi.addRow([mdBody, '', '', '']);
            var bodyRowNum = synthBodyRow.number;
            wsAi.mergeCells('A' + bodyRowNum + ':D' + bodyRowNum);
            var sbCell = wsAi.getCell('A' + bodyRowNum);
            sbCell.font = {{ name: 'TH SarabunPSK', size: 16 }};
            sbCell.alignment = {{ vertical: 'top', horizontal: 'left', wrapText: true }};
            sbCell.border = {{ top: {{style:'thin'}}, left: {{style:'thin'}}, bottom: {{style:'thin'}}, right: {{style:'thin'}} }};
            synthBodyRow.height = Math.max(140, Math.min(360, mdBody.split('\\n').length * 22));

            wsAi.getColumn(1).width = 12;
            wsAi.getColumn(2).width = 36;
            wsAi.getColumn(3).width = 64;
            wsAi.getColumn(4).width = 28;

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
            link.download = 'SCADA_Inspection_Report_AI_A4_' + safeFilenameDate + workerFileSuffix + dedupeSuffix + '.xlsx';

            document.body.appendChild(link); link.click(); document.body.removeChild(link);

            delete btn.dataset.isGenerating;
            btn.innerHTML = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="white" stroke-width="2"><path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/></svg> สรุปและ Export รายงาน (.xlsx)';
            updateGeminiUiAndButtonState();
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
                    exportDiv.innerHTML = '<button id="exportCsvBtn" class="g-export-btn" type="button"><svg xmlns="http://www.w3.org/2000/svg" height="20" viewBox="0 -960 960 960" width="20"><path d="M480-320 280-520l56-58 104 104v-326h80v326l104-104 56 58-200 200ZM240-160q-33 0-56.5-23.5T160-240v-120h80v120h480v-120h80v120q0 33-23.5 56.5T720-160H240Z"/></svg>Export ข้อมูลตามตัวกรอง (.xlsx)</button>';
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

        // --- ระบบค้นหาอัจฉริยะที่ยืดหยุ่น (Maps-like Flexible, Multi-Token & Fuzzy Search Engine) ---
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
            return String(value || '').toLocaleLowerCase('th-TH').normalize('NFC').replace(/\\s+/g, '').replace(/[._\\-\\/():,\\[\\]]/g, '');
        }}

        function normalizeToneInsensitive(value) {{
            return normalizeSearchText(value).replace(/[่-๋็์]/g, '');
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
            if (query.length < 3 || !target) return false;
            var minLen = Math.max(2, query.length - 1);
            var maxLen = Math.min(target.length, query.length + 1);
            for (var len = minLen; len <= maxLen; len++) {{
                for (var start = 0; start + len <= target.length; start++) {{
                    if (isOneEditAway(query, target.slice(start, start + len))) return true;
                }}
            }}
            return false;
        }}

        function isSubsequenceMatch(query, target) {{
            if (query.length < 3 || !target || query.length > target.length) return false;
            var qi = 0;
            for (var ti = 0; ti < target.length && qi < query.length; ti++) {{
                if (target.charAt(ti) === query.charAt(qi)) qi++;
            }}
            return qi === query.length;
        }}

        // แยกคำค้นหา (Multi-Token) เพื่อให้พิมพ์สลับตำแหน่งคำได้อย่างอิสระ เช่น "อุบล 10.120" หรือ "10.120 อุบล"
        window.tokenizeSmartQuery = function(rawQuery) {{
            var s = String(rawQuery || '').trim();
            if (!s) return [];
            return s.split(/\\s+/).map(function(tok) {{
                return {{
                    raw: tok,
                    low: tok.toLocaleLowerCase('th-TH').normalize('NFC'),
                    norm: normalizeSearchText(tok),
                    tone: normalizeToneInsensitive(tok)
                }};
            }}).filter(function(t) {{ return t.norm.length > 0 || t.low.length > 0; }});
        }};

        // คำนวณคะแนนความตรงของรายการต่อคำค้นหาแบบ Maps-like (รองรับพิมพ์สลับตำแหน่งคำ + ทนทานต่อการพิมพ์ผิด)
        window.scoreFieldsAgainstSmartQuery = function(fieldsArray, rawQuery) {{
            var tokens = window.tokenizeSmartQuery(rawQuery);
            if (tokens.length === 0) return 1;

            var normFields = [];
            var toneFields = [];
            for (var f = 0; f < fieldsArray.length; f++) {{
                var fv = String(fieldsArray[f] || '');
                normFields.push(normalizeSearchText(fv));
                toneFields.push(normalizeToneInsensitive(fv));
            }}
            var joinedNorm = normFields.join(' ');
            var joinedTone = toneFields.join(' ');

            var totalScore = 0;
            for (var t = 0; t < tokens.length; t++) {{
                var tk = tokens[t];
                var qNorm = tk.norm;
                var qTone = tk.tone;
                if (!qNorm) continue;

                var bestTokenScore = 0;
                for (var idx = 0; idx < normFields.length; idx++) {{
                    var nf = normFields[idx];
                    var tf = toneFields[idx];
                    if (!nf) continue;
                    // ถ่วงน้ำหนักให้ฟิลด์แรกๆ (เช่น Site ID, รหัสสั่งการ, สถานที่) มีความสำคัญสูงกว่า
                    var fieldWeight = idx === 0 ? 1.25 : (idx === 1 ? 1.2 : (idx === 2 ? 1.15 : 1.0));
                    var s = 0;
                    if (nf === qNorm) {{
                        s = 130 * fieldWeight;
                    }} else if (nf.indexOf(qNorm) === 0) {{
                        s = 105 * fieldWeight;
                    }} else if (nf.indexOf(qNorm) >= 0) {{
                        s = 85 * fieldWeight;
                    }} else if (qTone.length >= 2 && tf.indexOf(qTone) >= 0) {{
                        s = 70 * fieldWeight;
                    }} else if (qNorm.length >= 3 && hasNearMatch(qNorm, nf)) {{
                        s = 48 * fieldWeight;
                    }} else if (qTone.length >= 3 && hasNearMatch(qTone, tf)) {{
                        s = 42 * fieldWeight;
                    }} else if (qNorm.length >= 4 && isSubsequenceMatch(qNorm, nf)) {{
                        s = 26 * fieldWeight;
                    }}
                    if (s > bestTokenScore) bestTokenScore = s;
                }}

                if (bestTokenScore === 0) {{
                    if (joinedNorm.indexOf(qNorm) >= 0) bestTokenScore = 60;
                    else if (qTone.length >= 2 && joinedTone.indexOf(qTone) >= 0) bestTokenScore = 50;
                }}

                // ทุกคำที่เว้นวรรคต้องตรงอย่างน้อย 1 ฟิลด์ (AND ระหว่างคำค้นหาในช่องเดียวกัน)
                if (bestTokenScore <= 0) return 0;
                totalScore += bestTokenScore;
            }}

            // โบนัสพิเศษกรณีข้อความเต็มตรงกันตามลำดับ
            var fullNorm = normalizeSearchText(rawQuery);
            if (fullNorm && normFields[0] === fullNorm) totalScore += 60;
            else if (fullNorm && joinedNorm.indexOf(fullNorm) >= 0) totalScore += 25;

            return totalScore;
        }};

        function escapeHtmlSafe(str) {{
            return String(str === undefined || str === null ? '' : str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        }}

        // ไฮไลต์สีตรงส่วนที่ตรงกับคำค้นหา (รองรับการข้ามอักขระพิเศษ . _ - และวรรณยุกต์)
        window.highlightSearchText = function(text, rawQuery) {{
            var rawStr = String(text === undefined || text === null ? '' : text);
            if (!rawStr) return '';
            var tokens = window.tokenizeSmartQuery(rawQuery);
            if (!tokens.length) return escapeHtmlSafe(rawStr);

            var n = rawStr.length;
            var highlightMask = new Array(n).fill(false);
            var lowerStr = rawStr.toLocaleLowerCase('th-TH');

            // สร้าง mapping จากสตริงที่ตัดช่องว่าง/อักขระพิเศษ/วรรณยุกต์ กลับไปยัง index ของสตริงต้นฉบับ
            var strippedChars = [];
            var strippedToneChars = [];
            var origIndices = [];
            for (var i = 0; i < n; i++) {{
                var ch = lowerStr.charAt(i);
                if (/[\\s._\\-\\/():,\\[\\]]/.test(ch)) continue;
                strippedChars.push(ch);
                strippedToneChars.push(ch.replace(/[่-๋็์]/g, ''));
                origIndices.push(i);
            }}
            var strippedJoined = strippedChars.join('');
            var strippedToneJoined = strippedToneChars.join('');

            tokens.forEach(function(tk) {{
                // 1. Direct case-insensitive substring match
                if (tk.low) {{
                    var pos = 0;
                    while (pos < lowerStr.length) {{
                        var idx = lowerStr.indexOf(tk.low, pos);
                        if (idx === -1) break;
                        for (var k = idx; k < idx + tk.low.length; k++) highlightMask[k] = true;
                        pos = idx + Math.max(1, tk.low.length);
                    }}
                }}
                // 2. Punctuation/space-insensitive match
                if (tk.norm && tk.norm.length >= 2 && strippedJoined) {{
                    var sPos = 0;
                    while (sPos < strippedJoined.length) {{
                        var sIdx = strippedJoined.indexOf(tk.norm, sPos);
                        if (sIdx === -1) break;
                        var startOrig = origIndices[sIdx];
                        var endOrig = origIndices[Math.min(origIndices.length - 1, sIdx + tk.norm.length - 1)];
                        for (var m = startOrig; m <= endOrig; m++) highlightMask[m] = true;
                        sPos = sIdx + Math.max(1, tk.norm.length);
                    }}
                }}
                // 3. Tone-insensitive match
                if (tk.tone && tk.tone.length >= 2 && strippedToneJoined) {{
                    var tPos = 0;
                    while (tPos < strippedToneJoined.length) {{
                        var tIdx = strippedToneJoined.indexOf(tk.tone, tPos);
                        if (tIdx === -1) break;
                        var stOrig = origIndices[tIdx];
                        var enOrig = origIndices[Math.min(origIndices.length - 1, tIdx + tk.tone.length - 1)];
                        for (var p = stOrig; p <= enOrig; p++) highlightMask[p] = true;
                        tPos = tIdx + Math.max(1, tk.tone.length);
                    }}
                }}
            }});

            var out = '';
            var inMark = false;
            for (var cIdx = 0; cIdx < n; cIdx++) {{
                if (highlightMask[cIdx] && !inMark) {{
                    out += '<mark class="search-hl">';
                    inMark = true;
                }} else if (!highlightMask[cIdx] && inMark) {{
                    out += '</mark>';
                    inMark = false;
                }}
                out += escapeHtmlSafe(rawStr.charAt(cIdx));
            }}
            if (inMark) out += '</mark>';
            return out;
        }};

        // ฟังก์ชันเปรียบเทียบเรียงลำดับจากน้อยไปมาก (Ascending Order: A-Z, ก-ฮ, และรหัสตัวเลข 0-9 แบบ Natural Sort)
        window.compareAscendingNatural = function(a, b) {{
            var sa = String(a === undefined || a === null ? '' : a).trim();
            var sb = String(b === undefined || b === null ? '' : b).trim();
            return sa.localeCompare(sb, 'th', {{ numeric: true, sensitivity: 'base' }});
        }};

        function triggerSearch() {{
            var rawVal = inp.value.trim();
            clr.style.display = rawVal.length > 0 ? 'block' : 'none';
            res.innerHTML = '';
            if (rawVal.length < 1) {{ res.style.display = 'none'; return; }}

            var directMatches = [];
            var fuzzyMatches = [];
            for (var i = 0; i < sData.length; i++) {{
                var item = sData[i];
                var rowObj = expData[item.idx] || {{}};
                var ipVal = item.ip || (rowObj["IP"] !== 'ไม่มีค่า' ? rowObj["IP"] : '') || '';
                var dnpVal = item.dnp || (rowObj["DNP"] !== 'ไม่มีค่า' ? rowObj["DNP"] : '') || '';
                var peaVal = item.pea || (rowObj["การไฟฟ้า"] !== 'ไม่มีค่า' ? rowObj["การไฟฟ้า"] : '') || '';
                var prodVal = item.prod || (rowObj["ประเภทผลิตภัณฑ์"] !== 'ไม่มีค่า' ? rowObj["ประเภทผลิตภัณฑ์"] : '') || '';
                var commVal = item.comm || (rowObj["อุปกรณ์สื่อสาร (Commu.)"] !== 'ไม่มีค่า' ? rowObj["อุปกรณ์สื่อสาร (Commu.)"] : '') || '';
                var itemSubVal = item.item || (rowObj["ประเภทย่อยสื่อสาร (Item)"] !== 'ไม่มีค่า' ? rowObj["ประเภทย่อยสื่อสาร (Item)"] : '') || '';
                var wrlVal = item.wrl || (rowObj["เชื่อมกับ WRL Master"] !== 'ไม่มีค่า' ? rowObj["เชื่อมกับ WRL Master"] : '') || '';
                var eqVal = item.eq || (rowObj["ชนิดอุปกรณ์"] !== 'ไม่มีค่า' ? rowObj["ชนิดอุปกรณ์"] : '') || '';

                var fields = [
                    item.id,
                    item.code,
                    item.name,
                    ipVal,
                    dnpVal,
                    peaVal,
                    eqVal,
                    prodVal,
                    commVal,
                    itemSubVal,
                    wrlVal,
                    item.status
                ];
                var score = window.scoreFieldsAgainstSmartQuery(fields, rawVal);
                if (score > 0) {{
                    var matchObj = {{
                        item: item,
                        score: score,
                        ip: ipVal,
                        dnp: dnpVal,
                        pea: peaVal,
                        prod: prodVal,
                        comm: commVal
                    }};
                    if (score >= 50) directMatches.push(matchObj);
                    else fuzzyMatches.push(matchObj);
                }}
            }}

            var scoredMatches = directMatches.length > 0 ? directMatches : fuzzyMatches;

            // เรียงลำดับผลลัพธ์ที่ตรงเงื่อนไขจากน้อยไปมาก (Ascending Order: A-Z, ก-ฮ, รหัสอุปกรณ์)
            scoredMatches.sort(function(a, b) {{
                var cmpId = window.compareAscendingNatural(a.item.id, b.item.id);
                if (cmpId !== 0) return cmpId;
                var cmpCode = window.compareAscendingNatural(a.item.code, b.item.code);
                if (cmpCode !== 0) return cmpCode;
                return window.compareAscendingNatural(a.item.name, b.item.name);
            }});

            var topMatches = scoredMatches.slice(0, 30);
            if (!topMatches.length) {{ res.style.display = 'none'; return; }}
            res.style.display = 'block';
            var frag = document.createDocumentFragment();
            topMatches.forEach(function(entry) {{
                var m = entry.item;
                var div = document.createElement('div');
                div.className = 'g-search-item';
                var hlId = window.highlightSearchText(m.id, rawVal);
                var hlCode = m.code ? window.highlightSearchText(m.code, rawVal) : '';
                var hlName = window.highlightSearchText(m.name ? m.name : 'ไม่มีชื่อสถานที่', rawVal);
                var metaParts = [];
                if (entry.pea) metaParts.push('<span>กฟภ.: ' + window.highlightSearchText(entry.pea, rawVal) + '</span>');
                if (entry.ip) metaParts.push('<span style="font-family:monospace;">IP: ' + window.highlightSearchText(entry.ip, rawVal) + '</span>');
                if (entry.dnp) metaParts.push('<span style="font-family:monospace;">DNP: ' + window.highlightSearchText(entry.dnp, rawVal) + '</span>');
                if (entry.prod) metaParts.push('<span>' + window.highlightSearchText(entry.prod, rawVal) + '</span>');

                div.innerHTML = `<div style="display:flex;flex-direction:column;width:100%;gap:5px;">
                  <div style="display:flex;justify-content:space-between;align-items:flex-start;width:100%;gap:8px;">
                    <span style="font-size:14px;font-weight:600;line-height:1.2;">${{hlId}}</span>
                    ${{hlCode ? `<span style="color:var(--accent-primary);font-size:11px;font-weight:600;background:var(--bg-active);padding:2px 6px;border-radius:4px;flex-shrink:0;">${{hlCode}}</span>` : ''}}
                  </div>
                  <div style="display:flex;flex-wrap:wrap;align-items:center;gap:6px;width:100%;">
                    <span style="background-color:${{m.color}};color:#fff;padding:2px 7px;border-radius:999px;font-size:10px;font-weight:500;">${{m.status}}</span>
                    <span style="color:var(--text-secondary);font-size:11.5px;flex:1;min-width:120px;">${{hlName}}</span>
                  </div>
                  ${{metaParts.length ? `<div style="display:flex;flex-wrap:wrap;gap:8px;font-size:10.5px;color:var(--text-muted);">${{metaParts.join(' · ')}}</div>` : ''}}
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
            var dashExpModal = document.getElementById('dashExportModalOverlay');
            if (dashExpModal && dashExpModal.classList.contains('show') && target === dashExpModal) window.closeDashExportModal();
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

        if (typeof DISPLAY_FIELD_NAMES === 'undefined') {{
            window.DISPLAY_FIELD_NAMES = [
                "รหัสสั่งการ", "สถานที่", "State SCADA", "State SCADA (หลังตรวจสอบ)",
                "ชนิดอุปกรณ์", "ประเภทผลิตภัณฑ์", "อุปกรณ์สื่อสาร (Commu.)",
                "ประเภทย่อยสื่อสาร (Item)", "เชื่อมกับ WRL Master", "IP", "DNP",
                "รายละเอียดการแก้ไขข้อขัดข้อง", "รายละเอียดการเข้าแก้ไข", "LAT/LONG",
                "รอ ผอส. เข้าแก้ไข", "รอ ผบอ. เข้าแก้ไข", "การไฟฟ้า", "วันที่เข้าตรวจสอบ"
            ];
        }}
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

        // === [REQUIREMENT 2 & 3] ระบบ Multi-Select Filter (OR ในหมวดเดียวกัน / AND ข้ามหมวดหมู่) + Tree View เส้นโยงหัวข้อ ===
        var explorerFilterState = {{
            pea: 'all',
            status: 'all',
            eq: 'all',
            prod: 'all',
            comm: 'all',
            wrl: 'all',
            resp: ''
        }};

        // โครงสร้าง Set สำหรับเก็บตัวเลือกแบบ Multi-Select ของแต่ละหมวดหมู่
        var explorerFilterSets = {{
            pea: new Set(),
            status: new Set(),
            eq: new Set(),
            prod: new Set(),
            comm: new Set(),
            wrl: new Set(),
            resp: new Set()
        }};

        function syncLegacyExplorerStateFromSets() {{
            ['pea', 'status', 'eq', 'prod', 'comm', 'wrl'].forEach(function(dim) {{
                var st = explorerFilterSets[dim];
                if (!st || st.size === 0) {{
                    explorerFilterState[dim] = 'all';
                }} else {{
                    explorerFilterState[dim] = Array.from(st).join('||');
                }}
            }});
            var respSet = explorerFilterSets.resp;
            explorerFilterState.resp = (!respSet || respSet.size === 0) ? '' : Array.from(respSet).join('||');
        }}

        function getFilterSet(dimKey) {{
            if (!explorerFilterSets[dimKey]) explorerFilterSets[dimKey] = new Set();
            return explorerFilterSets[dimKey];
        }}

        function isDimFiltered(dimKey) {{
            var s = explorerFilterSets[dimKey];
            return Boolean(s && s.size > 0);
        }}

        // ฝัง (Hardcode) URL ของ Webhook ลงในโค้ด JavaScript โดยตรงสำหรับส่งข้อมูลแจ้งแก้ไขอุปกรณ์
        const WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbxamC17XRskueymfpmR-PeRiF8c30kfznzqsoeAW5Y9a_1mAS4eeVVmBuvRy_3fN_k/exec";

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
            if (u.includes('UNIT SUBSTATION') || u === 'CUS') return 'Unit Substation';
            if (u.includes('RING MAIN UNIT') || u === 'RMU') return 'Ring Main Unit';
            if (u.includes('SWITCHING CAPACITOR') || u.includes('CAPACITOR BANK') || u === 'SCB') return 'Switching Capacitor Bank';
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

        var MAIN_BRAND_DEFINITIONS = [
            {{ group: 'FXD', keywords: ['FXD'] }},
            {{ group: 'Shinsung', keywords: ['SHINSUNG', 'SHINSING'] }},
            {{ group: 'CYG', keywords: ['CYG'] }},
            {{ group: 'Precise', keywords: ['PRECISE'] }},
            {{ group: 'Timpano', keywords: ['TIMPANO'] }},
            {{ group: 'Form / Cooper', keywords: ['FORM', 'COOPER'] }},
            {{ group: 'Nulec', keywords: ['NULEC'] }},
            {{ group: 'G&W', keywords: ['G&W', 'G & W'] }},
            {{ group: 'FDCU', keywords: ['FDCU'] }},
            {{ group: 'ENTEC', keywords: ['ENTEC'] }},
            {{ group: 'ENOVA', keywords: ['ENOVA'] }},
            {{ group: 'Jin Kwang', keywords: ['JIN KWANG', 'JINKWANG'] }},
            {{ group: 'Robin', keywords: ['ROBIN'] }},
            {{ group: 'CCS', keywords: ['CCS'] }},
            {{ group: 'LBS115', keywords: ['LBS115', 'LBS 115'] }},
            {{ group: 'LRR ทั่วไป', keywords: ['LRR'] }}
        ];

        var BRAND_DISPLAY_ORDER = [
            'FXD', 'Shinsung', 'CYG', 'Precise', 'Timpano', 'Form / Cooper',
            'Nulec', 'G&W', 'FDCU', 'ENTEC', 'ENOVA', 'Jin Kwang',
            'Robin', 'CCS', 'LBS115', 'LRR ทั่วไป', 'ยี่ห้ออื่นๆ', 'ไม่ระบุ'
        ];

        function classifyMainBrandGroup(subBrandStr) {{
            var raw = String(subBrandStr || '').trim();
            if (!raw || raw === 'ไม่มีค่า' || raw === '-' || raw === 'ไม่ระบุประเภทผลิตภัณฑ์' || raw === 'ไม่ระบุ') {{
                return 'ไม่ระบุ';
            }}
            var upper = raw.toUpperCase();
            for (var i = 0; i < MAIN_BRAND_DEFINITIONS.length; i++) {{
                var def = MAIN_BRAND_DEFINITIONS[i];
                for (var j = 0; j < def.keywords.length; j++) {{
                    if (upper.indexOf(def.keywords[j]) !== -1) {{
                        return def.group;
                    }}
                }}
            }}
            return 'ยี่ห้ออื่นๆ';
        }}

        var COMM_DEVICE_GROUPS_CONFIG = {comm_device_groups_json};
        var expandedCommGroupsInTable = Object.create(null);
        var expandedCommGroupsInMenu = Object.create(null);

        function classifyMainCommGroup(commTypeStr, commItemStr) {{
            var rawC = String(commTypeStr || '').trim();
            var rawI = String(commItemStr || '').trim();
            if (rawC === 'ไม่มีค่า' || rawC === '-' || rawC === 'ไม่ระบุ Commu.') rawC = '';
            if (rawI === 'ไม่มีค่า' || rawI === '-' || rawI === 'ไม่ระบุ Item') rawI = '';
            var combined = (rawC + ' ' + rawI).trim();
            if (!combined) return 'ไม่ระบุ';

            var upperC = rawC.toUpperCase();
            var upperAll = combined.toUpperCase();
            var groupNames = Object.keys(COMM_DEVICE_GROUPS_CONFIG);

            for (var i = 0; i < groupNames.length; i++) {{
                var gName = groupNames[i];
                var kws = COMM_DEVICE_GROUPS_CONFIG[gName] || [];
                for (var j = 0; j < kws.length; j++) {{
                    if (upperC.indexOf(String(kws[j]).toUpperCase()) !== -1) return gName;
                }}
            }}
            for (var k = 0; k < groupNames.length; k++) {{
                var gName2 = groupNames[k];
                var kws2 = COMM_DEVICE_GROUPS_CONFIG[gName2] || [];
                for (var m = 0; m < kws2.length; m++) {{
                    if (upperAll.indexOf(String(kws2[m]).toUpperCase()) !== -1) return gName2;
                }}
            }}
            return 'อุปกรณ์สื่อสารอื่นๆ';
        }}

        function matchesSingleCommToken(row, token) {{
            if (!token || token === 'all') return true;
            if (token.indexOf('COMM_GROUP:') === 0) {{
                return row.comm_group === token.substring(11);
            }}
            return (
                row.comm_group === token ||
                row.comm_combo === token ||
                row.comm_type === token ||
                row.comm_item === token
            );
        }}

        function matchesCommSelection(row, selectedSetOrVal) {{
            if (!selectedSetOrVal || selectedSetOrVal === 'all') return true;
            if (selectedSetOrVal instanceof Set) {{
                if (selectedSetOrVal.size === 0) return true;
                var arr = Array.from(selectedSetOrVal);
                for (var i = 0; i < arr.length; i++) {{
                    if (matchesSingleCommToken(row, arr[i])) return true;
                }}
                return false;
            }}
            return matchesSingleCommToken(row, String(selectedSetOrVal));
        }}

        var expandedBrandGroupsInMenu = Object.create(null);
        var expandedBrandGroupsInTable = Object.create(null);
        var brandSearchQueryByMode = {{ top: '', exp: '' }};
        var cascFilterSearchQuery = {{ pea: '', status: '', eq: '', comm: '', wrl: '' }};

        function matchesSingleBrandToken(row, token) {{
            if (!token || token === 'all') return true;
            if (token.indexOf('GROUP:') === 0) {{
                return row.prod_group === token.substring(6);
            }}
            return row.prod === token || row.prod_group === token;
        }}

        function matchesBrandSelection(row, selectedSetOrVal) {{
            if (!selectedSetOrVal || selectedSetOrVal === 'all') return true;
            if (selectedSetOrVal instanceof Set) {{
                if (selectedSetOrVal.size === 0) return true;
                var arr = Array.from(selectedSetOrVal);
                for (var i = 0; i < arr.length; i++) {{
                    if (matchesSingleBrandToken(row, arr[i])) return true;
                }}
                return false;
            }}
            return matchesSingleBrandToken(row, String(selectedSetOrVal));
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
                prod_group: classifyMainBrandGroup(finalProd),
                ip: ip || '-',
                dnp: dnp || '-',
                comm_group: classifyMainCommGroup(finalCommType, finalCommItem),
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
                var hasA = totA > 0 ? 1 : 0;
                var hasB = totB > 0 ? 1 : 0;
                if (hasA !== hasB) return hasB - hasA;

                var onA = da.Online || 0, onB = db.Online || 0;
                var offA = (da.OfflineSum !== undefined) ? da.OfflineSum : (totA - onA);
                var offB = (db.OfflineSum !== undefined) ? db.OfflineSum : (totB - onB);
                var avaA = totA > 0 ? (onA / totA) * 100 : 0;
                var avaB = totB > 0 ? (onB / totB) * 100 : 0;

                if (mode === 'ava_desc') return (avaB - avaA) || (totB - totA) || String(a).localeCompare(String(b), 'th');
                if (mode === 'ava_asc') return (avaA - avaB) || (offB - offA) || String(a).localeCompare(String(b), 'th');
                if (mode === 'total_desc') return (totB - totA) || (avaB - avaA) || String(a).localeCompare(String(b), 'th');
                if (mode === 'total_asc') return (totA - totB) || (avaB - avaA) || String(a).localeCompare(String(b), 'th');
                if (mode === 'online_desc') return (onB - onA) || (totB - totA) || String(a).localeCompare(String(b), 'th');
                if (mode === 'online_asc') return (onA - onB) || (totA - totB) || String(a).localeCompare(String(b), 'th');
                if (mode === 'offline_desc') return (offB - offA) || (totB - totA) || String(a).localeCompare(String(b), 'th');
                if (mode === 'offline_asc') return (offA - offB) || (totA - totB) || String(a).localeCompare(String(b), 'th');
                if (mode === 'name_asc') return String(a).localeCompare(String(b), 'th');
                if (mode === 'name_desc') return String(b).localeCompare(String(a), 'th');
                return (avaB - avaA) || (totB - totA);
            }});
        }}

        function classifyRowRespBucketKey(r) {{
            if (r.parent_state === 'Online') return '';
            var af = r.after_full || '';
            if (af.includes('รอ ผบอ. และ ผอส.')) return 'รอ ผบอ. และ ผอส. เข้าแก้ไข';
            if (af.includes('รอ ผบอ.')) return 'รอ ผบอ. เข้าแก้ไข';
            if (af.includes('รอ ผอส.')) return 'รอ ผอส. เข้าแก้ไข';
            if (af.includes('ผบอ. เคยแก้ไข')) return 'ผบอ. เคยแก้ไขแล้วกลับมา Offline';
            if (af.includes('ระบบสื่อสารเคยแก้ไข')) return 'ระบบสื่อสารเคยแก้ไขแล้ว กลับมา Offline';
            return 'รอตรวจสอบสาเหตุ';
        }}

        function matchesStatusSet(r, statusSet) {{
            if (!statusSet || statusSet.size === 0) return true;
            if (statusSet.has('ALL_OFFLINE') && r.parent_state !== 'Online') return true;
            return statusSet.has(r.parent_state);
        }}

        // ลอจิกการกรอง: รายการในหมวดเดียวกันคิดแบบ OR (รวมกัน) ส่วนการกรองข้ามหมวดหมู่คิดแบบ AND (ตัดกัน)
        function rowMatchesActiveFilters(r, ignoreDim) {{
            var ig = ignoreDim || '';

            if (ig !== 'pea' && explorerFilterSets.pea.size > 0) {{
                if (!explorerFilterSets.pea.has(r.pea)) return false;
            }}
            if (ig !== 'status' && explorerFilterSets.status.size > 0) {{
                if (!matchesStatusSet(r, explorerFilterSets.status)) return false;
            }}
            if (ig !== 'eq' && explorerFilterSets.eq.size > 0) {{
                if (!explorerFilterSets.eq.has(r.norm_eq)) return false;
            }}
            if (ig !== 'prod' && explorerFilterSets.prod.size > 0) {{
                if (!matchesBrandSelection(r, explorerFilterSets.prod)) return false;
            }}
            if (ig !== 'comm' && explorerFilterSets.comm.size > 0) {{
                if (!matchesCommSelection(r, explorerFilterSets.comm)) return false;
            }}
            if (ig !== 'wrl' && explorerFilterSets.wrl.size > 0) {{
                if (!explorerFilterSets.wrl.has(r.comm_ref)) return false;
            }}
            if (ig !== 'resp' && explorerFilterSets.resp.size > 0) {{
                var rowRespKey = classifyRowRespBucketKey(r);
                if (!explorerFilterSets.resp.has(rowRespKey)) return false;
            }}
            if (ig !== 'search') {{
                var rawQ = String((document.getElementById('dashExplorerSearch') || {{}}).value || '').trim();
                if (rawQ) {{
                    var score = window.scoreFieldsAgainstSmartQuery([
                        r.site_id, r.cmd, r.loc, r.pea, r.norm_eq, r.prod, r.ip, r.dnp, r.comm_type, r.comm_item, r.comm_ref, r.state_full
                    ], rawQ);
                    if (score <= 0) return false;
                }}
            }}
            return true;
        }}

        function populateDashboardDropdownsOnce() {{
            var expPeaSelect = document.getElementById('dashExplorerPea');
            if (!expPeaSelect || expPeaSelect.dataset.initialized === '1') return;

            var peaSet = new Set();
            for (var i = 0; i < dashAllRecords.length; i++) {{
                var p = dashAllRecords[i]["การไฟฟ้า"];
                if (p && p !== 'ไม่มีค่า') peaSet.add(p);
            }}
            var sortedPeas = Array.from(peaSet).sort(function(a, b) {{ return a.localeCompare(b, 'th'); }});
            sortedPeas.forEach(function(p) {{
                var opt2 = document.createElement('option');
                opt2.value = p;
                opt2.textContent = p;
                expPeaSelect.appendChild(opt2);
            }});
            expPeaSelect.dataset.initialized = '1';
        }}

        window.onTopPeaFilterChange = function(val) {{
            var st = explorerFilterSets.pea;
            if (!val || val === 'all') {{
                st.clear();
            }} else {{
                if (st.has(val)) st.delete(val);
                else st.add(val);
            }}
            syncLegacyExplorerStateFromSets();
            dashExplorerLimit = 250;
            window.renderScadaDashboard();
        }};

        window.onExplorerPeaDropdownChange = function(val) {{
            window.onTopPeaFilterChange(val);
        }};

        window.onExplorerSearchInput = function() {{
            dashExplorerLimit = 250;
            window.renderScadaDashboard();
        }};

        // === ระบบ Multi-Select Popover Menus และ Tree View เส้นโยงหัวข้อหลัก-ย่อย ===
        window.closeAllCascFilterMenus = function() {{
            ['expPeaCascMenu', 'expStatusCascMenu', 'expEqCascMenu', 'expCommCascMenu', 'expWrlCascMenu', 'expHierBrandMenu'].forEach(function(id) {{
                var el = document.getElementById(id);
                if (el) el.classList.remove('show');
            }});
        }};

        window.toggleCascFilterMenu = function(dimKey, ev) {{
            if (ev) {{
                ev.preventDefault();
                ev.stopPropagation();
            }}
            var menuIdMap = {{
                pea: 'expPeaCascMenu',
                status: 'expStatusCascMenu',
                eq: 'expEqCascMenu',
                comm: 'expCommCascMenu',
                wrl: 'expWrlCascMenu'
            }};
            var targetId = menuIdMap[dimKey];
            var targetEl = document.getElementById(targetId);
            if (!targetEl) return;
            var willShow = !targetEl.classList.contains('show');
            window.closeAllCascFilterMenus();
            if (willShow) {{
                targetEl.classList.add('show');
                renderCascadingFilterMenus(cachedDashboardRows);
                var sInp = targetEl.querySelector('.hier-brand-search');
                if (sInp) setTimeout(function() {{ sInp.focus(); }}, 30);
            }}
        }};

        // สลับสถานะการติ๊กเลือกตัวกรองแบบ Multi-Select (โดยไม่ปิดเมนู เพื่อให้ติ๊กเลือกหลายรายการต่อเนื่องได้ทันที)
        window.toggleMultiSelectOption = function(dimKey, val, ev) {{
            if (ev) {{
                ev.preventDefault();
                ev.stopPropagation();
            }}
            var st = getFilterSet(dimKey);
            if (!val || val === 'all') {{
                st.clear();
            }} else {{
                if (st.has(val)) st.delete(val);
                else st.add(val);
            }}
            syncLegacyExplorerStateFromSets();
            dashExplorerLimit = 250;
            window.renderScadaDashboard();
        }};

        window.selectAllInFilterDimension = function(dimKey, allValuesArray, ev) {{
            if (ev) {{
                ev.preventDefault();
                ev.stopPropagation();
            }}
            var st = getFilterSet(dimKey);
            (allValuesArray || []).forEach(function(v) {{
                if (v && v !== 'all') st.add(v);
            }});
            syncLegacyExplorerStateFromSets();
            dashExplorerLimit = 250;
            window.renderScadaDashboard();
        }};

        window.clearFilterDimension = function(dimKey, ev) {{
            if (ev) {{
                ev.preventDefault();
                ev.stopPropagation();
            }}
            var st = getFilterSet(dimKey);
            st.clear();
            syncLegacyExplorerStateFromSets();
            dashExplorerLimit = 250;
            window.renderScadaDashboard();
        }};

        // คงฟังก์ชันเดิมไว้เพื่อความเข้ากันได้ย้อนหลัง
        window.selectCascFilterOption = function(dimKey, val, ev) {{
            window.toggleMultiSelectOption(dimKey, val, ev);
        }};

        function updateTriggerLabelWithBadge(labelEl, dimKey, defaultLabel, selectedSet, labelLookupMap) {{
            if (!labelEl) return;
            var count = selectedSet ? selectedSet.size : 0;
            if (count === 0) {{
                labelEl.innerHTML = '<span>' + escapeHtmlSafe(defaultLabel) + '</span>';
                return;
            }}
            var arr = Array.from(selectedSet);
            var firstTxt = (labelLookupMap && labelLookupMap[arr[0]]) ? labelLookupMap[arr[0]] : arr[0];
            if (firstTxt.indexOf('GROUP:') === 0) firstTxt = firstTxt.substring(6);
            if (firstTxt.indexOf('COMM_GROUP:') === 0) firstTxt = firstTxt.substring(11);
            var summaryTxt = count === 1 ? firstTxt : (firstTxt + ' +' + (count - 1));
            labelEl.innerHTML =
                '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:135px;display:inline-block;vertical-align:middle;">' + escapeHtmlSafe(summaryTxt) + '</span> ' +
                '<span class="ms-trigger-badge" title="เลือกไว้ ' + count + ' รายการ">' + count + '</span>' +
                '<span class="ms-trigger-clear-inline" title="ล้างตัวกรองหมวดนี้" onclick="window.clearFilterDimension(&quot;' + dimKey + '&quot;, event)">×</span>';
        }}

        function renderSingleCascMenu(dimKey, menuId, labelId, selectId, defaultLabel, allOptionsList, countByValMap, totalAvailCount) {{
            var menuEl = document.getElementById(menuId);
            var labelEl = document.getElementById(labelId);
            var selEl = document.getElementById(selectId);
            var selectedSet = getFilterSet(dimKey);

            var labelLookup = Object.create(null);
            allOptionsList.forEach(function(o) {{ labelLookup[o.value] = o.label; }});
            updateTriggerLabelWithBadge(labelEl, dimKey, defaultLabel, selectedSet, labelLookup);

            if (selEl) {{
                selEl.innerHTML = '';
                var optAll = document.createElement('option');
                optAll.value = 'all';
                optAll.textContent = defaultLabel + ' (' + totalAvailCount.toLocaleString() + ')';
                selEl.appendChild(optAll);
                allOptionsList.forEach(function(o) {{
                    var c = countByValMap[o.value] || 0;
                    var opt = document.createElement('option');
                    opt.value = o.value;
                    opt.textContent = o.label + ' (' + c.toLocaleString() + ')';
                    if (c === 0 && !selectedSet.has(o.value)) {{
                        opt.disabled = true;
                        opt.style.opacity = '0.4';
                    }}
                    selEl.appendChild(opt);
                }});
            }}

            if (!menuEl) return;
            var rawQ = String(cascFilterSearchQuery[dimKey] || '').trim();
            var prevScrollTop = 0;
            var prevScrollEl = menuEl.querySelector('.ms-menu-scroll-list');
            if (prevScrollEl) prevScrollTop = prevScrollEl.scrollTop;

            menuEl.innerHTML = '';

            var headerWrap = document.createElement('div');
            headerWrap.className = 'ms-menu-header';

            if (allOptionsList.length > 4) {{
                var searchInp = document.createElement('input');
                searchInp.type = 'search';
                searchInp.className = 'hier-brand-search';
                searchInp.style.marginBottom = '0';
                searchInp.placeholder = 'พิมพ์ค้นหาตัวเลือก (เรียงลำดับ A–Z, ก–ฮ)...';
                searchInp.value = cascFilterSearchQuery[dimKey] || '';
                searchInp.onclick = function(e) {{ e.stopPropagation(); }};
                searchInp.oninput = function(e) {{
                    cascFilterSearchQuery[dimKey] = e.target.value;
                    renderSingleCascMenu(dimKey, menuId, labelId, selectId, defaultLabel, allOptionsList, countByValMap, totalAvailCount);
                    var nextInp = menuEl.querySelector('.hier-brand-search');
                    if (nextInp) {{
                        nextInp.focus();
                        var ln = nextInp.value.length;
                        nextInp.setSelectionRange(ln, ln);
                    }}
                }};
                headerWrap.appendChild(searchInp);
            }}

            var toolbarDiv = document.createElement('div');
            toolbarDiv.className = 'ms-menu-toolbar';
            var statusTxt = document.createElement('span');
            statusTxt.className = 'ms-menu-status-txt';
            statusTxt.innerHTML = selectedSet.size > 0
                ? ('เลือกไว้ <span class="ms-trigger-badge">' + selectedSet.size + '</span> รายการ')
                : ('ทั้งหมด ' + totalAvailCount.toLocaleString() + ' ชุด');

            var actionsDiv = document.createElement('div');
            actionsDiv.className = 'ms-menu-actions';

            var availVals = allOptionsList.filter(function(o) {{ return (countByValMap[o.value] || 0) > 0; }}).map(function(o) {{ return o.value; }});
            var selAllBtn = document.createElement('button');
            selAllBtn.type = 'button';
            selAllBtn.className = 'ms-menu-action-btn';
            selAllBtn.textContent = 'เลือกทั้งหมด';
            selAllBtn.onclick = function(e) {{ window.selectAllInFilterDimension(dimKey, availVals, e); }};

            var clrBtn = document.createElement('button');
            clrBtn.type = 'button';
            clrBtn.className = 'ms-menu-action-btn is-clear-btn';
            clrBtn.textContent = 'ล้างค่า';
            clrBtn.onclick = function(e) {{ window.clearFilterDimension(dimKey, e); }};

            actionsDiv.appendChild(selAllBtn);
            actionsDiv.appendChild(clrBtn);
            toolbarDiv.appendChild(statusTxt);
            toolbarDiv.appendChild(actionsDiv);
            headerWrap.appendChild(toolbarDiv);
            menuEl.appendChild(headerWrap);

            var scrollList = document.createElement('div');
            scrollList.className = 'ms-menu-scroll-list';

            var directOpts = [];
            var fuzzyOpts = [];
            allOptionsList.forEach(function(o) {{
                var sc = rawQ ? window.scoreFieldsAgainstSmartQuery([o.label, o.value], rawQ) : 100;
                if (sc >= 50) directOpts.push({{ opt: o, score: sc }});
                else if (sc > 0) fuzzyOpts.push({{ opt: o, score: sc }});
            }});
            var scoredOpts = directOpts.length > 0 ? directOpts : fuzzyOpts;

            // เรียงลำดับจากน้อยไปมาก (Ascending Order: A-Z, ก-ฮ, 0-9)
            scoredOpts.sort(function(a, b) {{
                var oa = a.opt, ob = b.opt;
                if (!rawQ) {{
                    var cA = countByValMap[oa.value] || 0;
                    var cB = countByValMap[ob.value] || 0;
                    var selA = selectedSet.has(oa.value) ? 1 : 0;
                    var selB = selectedSet.has(ob.value) ? 1 : 0;
                    var hasA = (cA > 0 || selA) ? 1 : 0;
                    var hasB = (cB > 0 || selB) ? 1 : 0;
                    if (hasA !== hasB) return hasB - hasA;
                    if (oa.preserveOrder && ob.preserveOrder) return oa.orderIdx - ob.orderIdx;
                }}
                return window.compareAscendingNatural(oa.label, ob.label);
            }});

            if (scoredOpts.length === 0) {{
                var emptyDiv = document.createElement('div');
                emptyDiv.style.padding = '16px 10px';
                emptyDiv.style.textAlign = 'center';
                emptyDiv.style.fontSize = '12px';
                emptyDiv.style.color = 'var(--text-secondary)';
                emptyDiv.textContent = 'ไม่พบตัวเลือกที่ตรงกับคำค้นหา';
                scrollList.appendChild(emptyDiv);
            }}

            scoredOpts.forEach(function(entry) {{
                var o = entry.opt;
                var cnt = countByValMap[o.value] || 0;
                var isChecked = selectedSet.has(o.value);
                var isZero = (cnt === 0 && !isChecked);

                var rowDiv = document.createElement('div');
                rowDiv.className = 'ms-check-row' + (isChecked ? ' is-active' : '') + (isZero ? ' is-zero-match' : '');
                rowDiv.setAttribute('role', 'option');
                rowDiv.setAttribute('aria-selected', isChecked ? 'true' : 'false');

                var leftWrap = document.createElement('div');
                leftWrap.className = 'ms-check-left';

                var cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'ms-checkbox-input';
                cb.checked = isChecked;
                cb.disabled = isZero;
                cb.onclick = function(ev) {{
                    ev.stopPropagation();
                    if (!isZero) window.toggleMultiSelectOption(dimKey, o.value, ev);
                }};

                var lblSpan = document.createElement('span');
                lblSpan.className = 'ms-label-text';
                lblSpan.innerHTML = rawQ ? window.highlightSearchText(o.label, rawQ) : escapeHtmlSafe(o.label);

                leftWrap.appendChild(cb);
                leftWrap.appendChild(lblSpan);

                var cntSpan = document.createElement('span');
                cntSpan.className = 'hier-brand-count';
                cntSpan.textContent = cnt.toLocaleString() + ' ชุด';

                rowDiv.appendChild(leftWrap);
                rowDiv.appendChild(cntSpan);

                if (!isZero) {{
                    rowDiv.onclick = function(ev) {{
                        window.toggleMultiSelectOption(dimKey, o.value, ev);
                    }};
                }}
                scrollList.appendChild(rowDiv);
            }});

            menuEl.appendChild(scrollList);
            if (prevScrollTop > 0) scrollList.scrollTop = prevScrollTop;
        }}

        // === ฟังก์ชันจำแนก "กลุ่มหลัก -> รายการย่อย" สำหรับสร้าง Tree View พร้อมเส้นโยง (Tree Guide Lines) ในตัวกรองทุกหมวดหมู่ ===
        function classifyDimensionGroupAndSub(dimKey, r) {{
            if (dimKey === 'pea') {{
                var peaVal = r.pea || 'ไม่ระบุการไฟฟ้า';
                var peaGrp = 'ไม่ระบุการไฟฟ้า / อื่นๆ';
                if (peaVal.indexOf('กฟจ.') === 0) peaGrp = 'การไฟฟ้าส่วนภูมิภาคจังหวัด (กฟจ.)';
                else if (peaVal.indexOf('กฟส.') === 0) peaGrp = 'การไฟฟ้าส่วนภูมิภาคสาขา/อำเภอ (กฟส.)';
                else if (peaVal.indexOf('กฟย.') === 0) peaGrp = 'การไฟฟ้าส่วนภูมิภาคสาขาย่อย (กฟย.)';
                return {{ grp: peaGrp, sub: peaVal }};
            }}
            if (dimKey === 'status') {{
                var stVal = r.parent_state || 'สถานะอื่นๆ';
                var stGrp = (stVal === 'Online') ? 'สถานะปกติ (Online)' : 'ไม่ออนไลน์ทั้งหมด (Non-Online)';
                return {{ grp: stGrp, sub: stVal }};
            }}
            if (dimKey === 'eq') {{
                var eqVal = r.norm_eq || 'ไม่ระบุชนิดอุปกรณ์';
                var eqGrp = 'อุปกรณ์ระบบจำหน่าย 22–33 kV';
                if (eqVal.indexOf('115') !== -1) eqGrp = 'อุปกรณ์ระบบสายส่ง 115 kV';
                else if (eqVal.indexOf('ไม่ระบุ') !== -1) eqGrp = 'ไม่ระบุชนิดอุปกรณ์';
                return {{ grp: eqGrp, sub: eqVal }};
            }}
            if (dimKey === 'wrl') {{
                var wrlVal = r.comm_ref || 'ไม่ระบุ WRL Master';
                var wrlGrp = 'แม่ข่ายสื่อสารอื่นๆ / ไม่ระบุ';
                if (/CELLUL/i.test(wrlVal)) wrlGrp = 'โครงข่าย Cellular (AIS / DTAC)';
                else if (/Substation/i.test(wrlVal)) wrlGrp = 'สถานีไฟฟ้า (Substation)';
                else if (/Office/i.test(wrlVal)) wrlGrp = 'สำนักงานการไฟฟ้า (PEA Office)';
                return {{ grp: wrlGrp, sub: wrlVal }};
            }}
            if (dimKey === 'prod') {{
                return {{
                    grp: r.prod_group || classifyMainBrandGroup(r.prod),
                    sub: r.prod || 'ไม่ระบุประเภทผลิตภัณฑ์'
                }};
            }}
            return {{
                grp: r.comm_group || classifyMainCommGroup(r.comm_type, r.comm_item),
                sub: r.comm_combo || 'ไม่ระบุ Commu. • ไม่ระบุ Item'
            }};
        }}

        function buildHierarchyForDimension(dimKey, allRowsSource) {{
            var hierarchy = Object.create(null);
            var matchedRows = (allRowsSource || []).filter(function(r) {{
                return rowMatchesActiveFilters(r, dimKey);
            }});
            var matchedGroupCounts = Object.create(null);
            var matchedSubCounts = Object.create(null);

            matchedRows.forEach(function(r) {{
                var gs = classifyDimensionGroupAndSub(dimKey, r);
                matchedGroupCounts[gs.grp] = (matchedGroupCounts[gs.grp] || 0) + 1;
                matchedSubCounts[gs.sub] = (matchedSubCounts[gs.sub] || 0) + 1;
            }});

            (allRowsSource || []).forEach(function(r) {{
                var gs = classifyDimensionGroupAndSub(dimKey, r);
                if (!hierarchy[gs.grp]) {{
                    hierarchy[gs.grp] = {{ total: matchedGroupCounts[gs.grp] || 0, subs: Object.create(null) }};
                }}
                if (hierarchy[gs.grp].subs[gs.sub] === undefined) {{
                    hierarchy[gs.grp].subs[gs.sub] = matchedSubCounts[gs.sub] || 0;
                }}
            }});

            // รับประกันว่าหมวดสถานะมีตัวเลือกมาตรฐานครบเสมอ
            if (dimKey === 'status') {{
                var stdStatusGroups = {{
                    'สถานะปกติ (Online)': ['Online'],
                    'ไม่ออนไลน์ทั้งหมด (Non-Online)': ['Connecting', 'Offline', 'Initializing', 'Telemetry Failure']
                }};
                Object.keys(stdStatusGroups).forEach(function(gName) {{
                    if (!hierarchy[gName]) hierarchy[gName] = {{ total: matchedGroupCounts[gName] || 0, subs: Object.create(null) }};
                    stdStatusGroups[gName].forEach(function(sName) {{
                        if (hierarchy[gName].subs[sName] === undefined) {{
                            hierarchy[gName].subs[sName] = matchedSubCounts[sName] || 0;
                        }}
                    }});
                }});
            }}

            var orderedGroups = Object.keys(hierarchy).sort(function(a, b) {{
                var totA = hierarchy[a].total > 0 ? 1 : 0;
                var totB = hierarchy[b].total > 0 ? 1 : 0;
                if (totA !== totB) return totB - totA;
                if (dimKey === 'status') {{
                    if (a.indexOf('Online') !== -1 && b.indexOf('Online') === -1) return -1;
                    if (a.indexOf('Online') === -1 && b.indexOf('Online') !== -1) return 1;
                }}
                if (dimKey === 'prod') {{
                    var idxA = BRAND_DISPLAY_ORDER.indexOf(a);
                    var idxB = BRAND_DISPLAY_ORDER.indexOf(b);
                    if (idxA === -1) idxA = 999;
                    if (idxB === -1) idxB = 999;
                    if (idxA !== idxB) return idxA - idxB;
                }}
                return window.compareAscendingNatural(a, b);
            }});

            return {{
                hierarchy: hierarchy,
                orderedGroups: orderedGroups,
                totalAvailable: matchedRows.length
            }};
        }}

        function normalizeTreeDimensionsSelection(dimKey, hierarchy) {{
            var st = getFilterSet(dimKey);
            if (dimKey === 'status' && st.has('ALL_OFFLINE')) {{
                st.delete('ALL_OFFLINE');
                var nonOnlineGrp = hierarchy['ไม่ออนไลน์ทั้งหมด (Non-Online)'];
                if (nonOnlineGrp && nonOnlineGrp.subs) {{
                    Object.keys(nonOnlineGrp.subs).forEach(function(sub) {{ st.add(sub); }});
                }} else {{
                    ['Connecting', 'Offline', 'Initializing', 'Telemetry Failure'].forEach(function(sub) {{ st.add(sub); }});
                }}
            }}
            var prefix = dimKey === 'prod' ? 'GROUP:' : (dimKey === 'comm' ? 'COMM_GROUP:' : 'TREE_GROUP:');
            Array.from(st).forEach(function(token) {{
                if (token.indexOf(prefix) === 0) {{
                    var grp = token.substring(prefix.length);
                    st.delete(token);
                    if (hierarchy[grp] && hierarchy[grp].subs) {{
                        Object.keys(hierarchy[grp].subs).forEach(function(sub) {{
                            st.add(sub);
                        }});
                    }}
                }}
            }});
        }}

        window.toggleTreeParentGroupCheckbox = function(dimKey, grpName, allRowsSource, ev) {{
            if (ev) {{
                ev.stopPropagation();
            }}
            var info = buildHierarchyForDimension(dimKey, allRowsSource || cachedDashboardRows);
            normalizeTreeDimensionsSelection(dimKey, info.hierarchy);
            var gData = info.hierarchy[grpName];
            if (!gData) return;

            var st = getFilterSet(dimKey);
            var subNames = Object.keys(gData.subs);
            var availSubs = subNames.filter(function(s) {{ return (gData.subs[s] || 0) > 0 || st.has(s); }});
            var targetSubs = availSubs.length > 0 ? availSubs : subNames;

            var allChecked = targetSubs.length > 0 && targetSubs.every(function(s) {{ return st.has(s); }});
            if (allChecked) {{
                subNames.forEach(function(s) {{ st.delete(s); }});
            }} else {{
                targetSubs.forEach(function(s) {{ st.add(s); }});
            }}
            syncLegacyExplorerStateFromSets();
            dashExplorerLimit = 250;
            window.renderScadaDashboard();
        }};

        window.toggleTreeGroupExpandState = function(dimKey, grpName, ev) {{
            if (ev) {{
                ev.preventDefault();
                ev.stopPropagation();
            }}
            var store = dimKey === 'prod' ? expandedBrandGroupsInMenu : expandedCommGroupsInMenu;
            var key = dimKey + ':' + grpName;
            store[key] = !Boolean(store[key]);
            if (dimKey === 'prod') {{
                renderHierBrandFilterMenus(cachedDashboardRows);
            }} else {{
                renderCascadingFilterMenus(cachedDashboardRows);
            }}
        }};

        function updateTreeFilterTriggerLabel(dimKey, labelId, defaultLabel, hierarchyInfo) {{
            var labelEl = document.getElementById(labelId);
            if (!labelEl) return;
            var st = getFilterSet(dimKey);
            normalizeTreeDimensionsSelection(dimKey, hierarchyInfo.hierarchy);

            if (st.size === 0) {{
                labelEl.innerHTML = '<span>' + escapeHtmlSafe(defaultLabel) + '</span>';
                return;
            }}

            var summaryLabels = [];
            hierarchyInfo.orderedGroups.forEach(function(grp) {{
                var subs = Object.keys(hierarchyInfo.hierarchy[grp].subs || {{}});
                if (!subs.length) return;
                var checkedSubs = subs.filter(function(s) {{ return st.has(s); }});
                if (checkedSubs.length === subs.length && subs.length > 0) {{
                    summaryLabels.push('[กลุ่มหลัก] ' + grp);
                }} else if (checkedSubs.length > 0) {{
                    checkedSubs.forEach(function(cs) {{ summaryLabels.push(cs); }});
                }}
            }});
            var firstTxt = summaryLabels[0] || Array.from(st)[0] || defaultLabel;
            var totalSelectedCount = st.size;
            var displayTxt = totalSelectedCount === 1 ? firstTxt : (firstTxt + ' +' + (totalSelectedCount - 1));

            labelEl.innerHTML =
                '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:140px;display:inline-block;vertical-align:middle;">' + escapeHtmlSafe(displayTxt) + '</span> ' +
                '<span class="ms-trigger-badge" title="เลือกไว้ ' + totalSelectedCount + ' รายการย่อย">' + totalSelectedCount + '</span>' +
                '<span class="ms-trigger-clear-inline" title="ล้างตัวกรองหมวดนี้" onclick="window.clearFilterDimension(&quot;' + dimKey + '&quot;, event)">×</span>';
        }}

        function renderTreeFilterPopoverMenu(dimKey, containerId, labelId, defaultLabel, rowsSource) {{
            var menuEl = document.getElementById(containerId);
            var info = buildHierarchyForDimension(dimKey, rowsSource);
            var hierarchy = info.hierarchy;
            var orderedGroups = info.orderedGroups;
            var st = getFilterSet(dimKey);
            normalizeTreeDimensionsSelection(dimKey, hierarchy);
            updateTreeFilterTriggerLabel(dimKey, labelId, defaultLabel, info);

            if (!menuEl) return;
            var rawQ = dimKey === 'prod'
                ? String(brandSearchQueryByMode.exp || '').trim()
                : String(cascFilterSearchQuery[dimKey] || '').trim();

            var prevScrollTop = 0;
            var prevScrollEl = menuEl.querySelector('.ms-menu-scroll-list');
            if (prevScrollEl) prevScrollTop = prevScrollEl.scrollTop;

            menuEl.innerHTML = '';

            var placeholderMap = {{
                pea: 'ค้นหาการไฟฟ้า กฟจ./กฟส. (เรียง ก–ฮ)...',
                status: 'ค้นหาสถานะ State SCADA (เรียง A–Z)...',
                eq: 'ค้นหาชนิดอุปกรณ์ (เรียง A–Z, ก–ฮ)...',
                prod: 'ค้นหากลุ่มยี่ห้อหลัก หรือรุ่นย่อย (เรียง A–Z)...',
                comm: 'ค้นหากลุ่มสื่อสารหลัก (DAU / Router) หรือรุ่นย่อย...',
                wrl: 'ค้นหาแม่ข่าย WRL Master (เรียง A–Z)...'
            }};

            // ส่วนหัวเมนู (Sticky Header: Search + Action Bar)
            var headerWrap = document.createElement('div');
            headerWrap.className = 'ms-menu-header';

            var searchInp = document.createElement('input');
            searchInp.type = 'search';
            searchInp.className = 'hier-brand-search';
            searchInp.style.marginBottom = '0';
            searchInp.placeholder = placeholderMap[dimKey] || 'พิมพ์ค้นหาตัวเลือก (เรียงลำดับ A–Z, ก–ฮ)...';
            searchInp.value = rawQ;
            searchInp.onclick = function(e) {{ e.stopPropagation(); }};
            searchInp.oninput = function(e) {{
                if (dimKey === 'prod') brandSearchQueryByMode.exp = e.target.value;
                else cascFilterSearchQuery[dimKey] = e.target.value;
                renderTreeFilterPopoverMenu(dimKey, containerId, labelId, defaultLabel, rowsSource);
                var newInp = menuEl.querySelector('.hier-brand-search');
                if (newInp) {{
                    newInp.focus();
                    var len = newInp.value.length;
                    newInp.setSelectionRange(len, len);
                }}
            }};
            headerWrap.appendChild(searchInp);

            var toolbarDiv = document.createElement('div');
            toolbarDiv.className = 'ms-menu-toolbar';
            var statusTxt = document.createElement('span');
            statusTxt.className = 'ms-menu-status-txt';
            statusTxt.innerHTML = st.size > 0
                ? ('เลือกไว้ <span class="ms-trigger-badge">' + st.size + '</span> รายการย่อย')
                : ('ทั้งหมด ' + info.totalAvailable.toLocaleString() + ' ชุด');

            var actionsDiv = document.createElement('div');
            actionsDiv.className = 'ms-menu-actions';

            var allAvailSubKeys = [];
            orderedGroups.forEach(function(g) {{
                Object.keys(hierarchy[g].subs || {{}}).forEach(function(s) {{
                    if ((hierarchy[g].subs[s] || 0) > 0) allAvailSubKeys.push(s);
                }});
            }});

            var selAllBtn = document.createElement('button');
            selAllBtn.type = 'button';
            selAllBtn.className = 'ms-menu-action-btn';
            selAllBtn.textContent = 'เลือกทั้งหมด';
            selAllBtn.onclick = function(e) {{ window.selectAllInFilterDimension(dimKey, allAvailSubKeys, e); }};

            var clrBtn = document.createElement('button');
            clrBtn.type = 'button';
            clrBtn.className = 'ms-menu-action-btn is-clear-btn';
            clrBtn.textContent = 'ล้างค่า';
            clrBtn.onclick = function(e) {{ window.clearFilterDimension(dimKey, e); }};

            actionsDiv.appendChild(selAllBtn);
            actionsDiv.appendChild(clrBtn);
            toolbarDiv.appendChild(statusTxt);
            toolbarDiv.appendChild(actionsDiv);
            headerWrap.appendChild(toolbarDiv);
            menuEl.appendChild(headerWrap);

            // ส่วนรายการแบบ Tree View ที่ใช้ Block Layout ไม่บีบความสูง และเรียงลำดับ Ascending Order
            var scrollList = document.createElement('div');
            scrollList.className = 'ms-menu-scroll-list';

            var expStore = dimKey === 'prod' ? expandedBrandGroupsInMenu : expandedCommGroupsInMenu;

            // เมื่อค้นหา ให้เรียงกลุ่มหลักตามตัวอักษร A-Z, ก-ฮ (Ascending Order)
            var displayGroups = orderedGroups.slice();
            if (rawQ) {{
                displayGroups.sort(function(a, b) {{
                    return window.compareAscendingNatural(a, b);
                }});
            }}

            var renderedGroupCount = 0;
            displayGroups.forEach(function(grp, grpIdx) {{
                var gData = hierarchy[grp];
                var allSubsInGroup = Object.keys(gData.subs);

                // เรียงรายการย่อยจากน้อยไปมาก (Ascending Order: A-Z, ก-ฮ, 0-9)
                allSubsInGroup.sort(function(a, b) {{
                    if (!rawQ) {{
                        var cA = gData.subs[a] || 0;
                        var cB = gData.subs[b] || 0;
                        var hasA = (cA > 0 || st.has(a)) ? 1 : 0;
                        var hasB = (cB > 0 || st.has(b)) ? 1 : 0;
                        if (hasA !== hasB) return hasB - hasA;
                    }}
                    return window.compareAscendingNatural(a, b);
                }});

                var groupMatchesSearch = rawQ ? (window.scoreFieldsAgainstSmartQuery([grp], rawQ) >= 50) : true;
                var directSubMatches = [];
                var fuzzySubMatches = [];
                if (rawQ) {{
                    allSubsInGroup.forEach(function(sName) {{
                        var sc = window.scoreFieldsAgainstSmartQuery([sName, grp], rawQ);
                        if (groupMatchesSearch || sc >= 50) directSubMatches.push(sName);
                        else if (sc > 0) fuzzySubMatches.push(sName);
                    }});
                }}
                var filteredSubs = !rawQ
                    ? allSubsInGroup
                    : (directSubMatches.length > 0 ? directSubMatches : fuzzySubMatches);

                if (rawQ && filteredSubs.length === 0 && !groupMatchesSearch) {{
                    return;
                }}

                // บังคับเรียงลำดับผลลัพธ์ย่อยที่ค้นหาได้จากน้อยไปมาก (Ascending Order: A-Z, ก-ฮ, 0-9)
                if (rawQ) {{
                    filteredSubs.sort(function(a, b) {{
                        return window.compareAscendingNatural(a, b);
                    }});
                }}

                renderedGroupCount++;
                var checkedSubsCount = allSubsInGroup.filter(function(s) {{ return st.has(s); }}).length;
                var availSubs = allSubsInGroup.filter(function(s) {{ return (gData.subs[s] || 0) > 0 || st.has(s); }});
                var totalTargetSubs = availSubs.length > 0 ? availSubs.length : allSubsInGroup.length;
                var isAllGroupChecked = (checkedSubsCount > 0 && checkedSubsCount >= totalTargetSubs);
                var isIndeterminateGroup = (checkedSubsCount > 0 && checkedSubsCount < totalTargetSubs);
                var isZeroGroup = (gData.total === 0 && checkedSubsCount === 0);

                var expKey = dimKey + ':' + grp;
                if (expStore[expKey] === undefined) {{
                    // กางกลุ่มที่มีการเลือกไว้ หรือกางกลุ่มแรกๆ เพื่อให้เห็นเส้นโยง Tree View ชัดเจนทันที
                    expStore[expKey] = (checkedSubsCount > 0) || (!isZeroGroup && (orderedGroups.length <= 5 || grpIdx < 2));
                }}
                var isExpanded = Boolean(rawQ) || Boolean(expStore[expKey]);

                var grpWrap = document.createElement('div');
                grpWrap.className = 'tree-node-group' +
                    (isExpanded ? ' is-expanded' : '') +
                    (checkedSubsCount > 0 ? ' has-checked-children' : '') +
                    (isZeroGroup ? ' is-zero-match' : '');

                var parentRow = document.createElement('div');
                parentRow.className = 'tree-parent-row' + (checkedSubsCount > 0 ? ' is-active' : '');

                var leftDiv = document.createElement('div');
                leftDiv.className = 'tree-parent-left';

                var expandBtn = document.createElement('button');
                expandBtn.type = 'button';
                expandBtn.className = 'tree-expand-btn';
                expandBtn.innerHTML = '&#9654;';
                expandBtn.title = isExpanded ? 'ยุบรายการย่อย' : 'กางดูรายการย่อย';
                expandBtn.onclick = function(ev) {{
                    window.toggleTreeGroupExpandState(dimKey, grp, ev);
                }};

                var parentCb = document.createElement('input');
                parentCb.type = 'checkbox';
                parentCb.className = 'ms-checkbox-input';
                parentCb.checked = isAllGroupChecked;
                parentCb.indeterminate = isIndeterminateGroup;
                parentCb.disabled = isZeroGroup;
                parentCb.title = 'ติ๊กเพื่อเลือกหรือยกเลิกรายการย่อยทั้งหมดในกลุ่ม ' + grp;
                parentCb.onclick = function(ev) {{
                    ev.stopPropagation();
                    if (!isZeroGroup) window.toggleTreeParentGroupCheckbox(dimKey, grp, rowsSource, ev);
                }};

                var titleWrap = document.createElement('div');
                titleWrap.className = 'tree-parent-title-wrap';
                var grpPrefix = '[กลุ่มหลัก] ';
                var titleHtml = rawQ
                    ? (escapeHtmlSafe(grpPrefix) + window.highlightSearchText(grp, rawQ))
                    : escapeHtmlSafe(grpPrefix + grp);
                var subStatusTxt = checkedSubsCount > 0
                    ? ('เลือกแล้ว ' + checkedSubsCount + '/' + allSubsInGroup.length + ' รายการย่อย')
                    : (allSubsInGroup.length + ' รายการย่อย');
                titleWrap.innerHTML =
                    '<span class="tree-parent-title">' + titleHtml + '</span>' +
                    '<span class="tree-parent-meta">' + subStatusTxt + '</span>';

                leftDiv.appendChild(expandBtn);
                leftDiv.appendChild(parentCb);
                leftDiv.appendChild(titleWrap);

                var countBadge = document.createElement('span');
                countBadge.className = 'hier-brand-count';
                countBadge.textContent = gData.total.toLocaleString() + ' ชุด';

                parentRow.appendChild(leftDiv);
                parentRow.appendChild(countBadge);

                parentRow.onclick = function(ev) {{
                    if (ev.target === parentCb) return;
                    window.toggleTreeGroupExpandState(dimKey, grp, ev);
                }};

                grpWrap.appendChild(parentRow);

                // รายการย่อยพร้อมเส้นโยง (Tree Guide Lines)
                var childrenList = document.createElement('div');
                childrenList.className = 'tree-children-list';

                filteredSubs.forEach(function(subName) {{
                    var subCnt = gData.subs[subName] || 0;
                    var isSubChecked = st.has(subName);
                    var isZeroSub = (subCnt === 0 && !isSubChecked);

                    var childRow = document.createElement('div');
                    childRow.className = 'tree-child-row' + (isSubChecked ? ' is-active' : '') + (isZeroSub ? ' is-zero-match' : '');

                    var childLeft = document.createElement('div');
                    childLeft.className = 'ms-check-left';

                    var childCb = document.createElement('input');
                    childCb.type = 'checkbox';
                    childCb.className = 'ms-checkbox-input';
                    childCb.checked = isSubChecked;
                    childCb.disabled = isZeroSub;
                    childCb.onclick = function(ev) {{
                        ev.stopPropagation();
                        if (!isZeroSub) window.toggleMultiSelectOption(dimKey, subName, ev);
                    }};

                    var childLabel = document.createElement('span');
                    childLabel.className = 'ms-label-text';
                    childLabel.innerHTML = rawQ ? window.highlightSearchText(subName, rawQ) : escapeHtmlSafe(subName);

                    childLeft.appendChild(childCb);
                    childLeft.appendChild(childLabel);

                    var childCntBadge = document.createElement('span');
                    childCntBadge.className = 'hier-brand-count';
                    childCntBadge.textContent = subCnt.toLocaleString() + ' ชุด';

                    childRow.appendChild(childLeft);
                    childRow.appendChild(childCntBadge);

                    if (!isZeroSub) {{
                        childRow.onclick = function(ev) {{
                            window.toggleMultiSelectOption(dimKey, subName, ev);
                        }};
                    }}
                    childrenList.appendChild(childRow);
                }});

                grpWrap.appendChild(childrenList);
                scrollList.appendChild(grpWrap);
            }});

            if (renderedGroupCount === 0) {{
                var emptyDiv = document.createElement('div');
                emptyDiv.style.padding = '16px 10px';
                emptyDiv.style.textAlign = 'center';
                emptyDiv.style.fontSize = '12px';
                emptyDiv.style.color = 'var(--text-secondary)';
                emptyDiv.textContent = 'ไม่พบตัวเลือกที่ตรงกับคำค้นหา "' + rawQ + '"';
                scrollList.appendChild(emptyDiv);
            }}

            menuEl.appendChild(scrollList);
            if (prevScrollTop > 0) scrollList.scrollTop = prevScrollTop;
        }}

        function renderCascadingFilterMenus(allRows) {{
            var rows = allRows || [];
            // อัปเกรดตัวกรองทุกหมวดหมู่เป็น Multi-Select แบบ Tree View พร้อมเส้นโยง (Tree Guide Lines) และ Faceted Count เรียลไทม์
            renderTreeFilterPopoverMenu('pea', 'expPeaCascMenu', 'expPeaTriggerLabel', 'ทุกการไฟฟ้า', rows);
            renderTreeFilterPopoverMenu('status', 'expStatusCascMenu', 'expStatusTriggerLabel', 'ทุกสถานะ', rows);
            renderTreeFilterPopoverMenu('eq', 'expEqCascMenu', 'expEqTriggerLabel', 'ทุกชนิดอุปกรณ์', rows);
            renderTreeFilterPopoverMenu('comm', 'expCommCascMenu', 'expCommTriggerLabel', 'ทุกประเภทสื่อสาร', rows);
            renderTreeFilterPopoverMenu('wrl', 'expWrlCascMenu', 'expWrlTriggerLabel', 'ทุกแม่ข่าย', rows);
            renderHierBrandFilterMenus(rows);
        }}

        function syncExplorerFilterDropdowns(rows) {{
            renderCascadingFilterMenus(rows);
        }}

        window.toggleHierBrandMenu = function(mode, ev) {{
            if (ev) {{
                ev.preventDefault();
                ev.stopPropagation();
            }}
            var expMenu = document.getElementById('expHierBrandMenu');
            if (!expMenu) return;
            var willShow = !expMenu.classList.contains('show');
            window.closeAllCascFilterMenus();
            if (willShow) {{
                expMenu.classList.add('show');
                renderHierBrandFilterMenus(cachedDashboardRows);
                var sInp = expMenu.querySelector('.hier-brand-search');
                if (sInp) setTimeout(function() {{ sInp.focus(); }}, 30);
            }}
        }};

        window.toggleBrandGroupInMenu = function(mode, grpName, ev) {{
            window.toggleTreeGroupExpandState('prod', grpName, ev);
        }};

        window.selectHierBrandOption = function(val, ev) {{
            if (val && val.indexOf('GROUP:') === 0) {{
                window.toggleTreeParentGroupCheckbox('prod', val.substring(6), cachedDashboardRows, ev);
            }} else {{
                window.toggleMultiSelectOption('prod', val, ev);
            }}
        }};

        function renderHierBrandFilterMenus(rowsSource) {{
            var allScopeRows = cachedDashboardRows && cachedDashboardRows.length ? cachedDashboardRows : rowsSource;
            renderTreeFilterPopoverMenu('prod', 'expHierBrandMenu', 'expHierBrandLabel', 'ทุกผลิตภัณฑ์', allScopeRows);
        }}

        document.addEventListener('click', function(e) {{
            var expWrap = document.getElementById('expHierBrandFilterWrap');
            var expMenu = document.getElementById('expHierBrandMenu');
            if (expMenu && expMenu.classList.contains('show') && expWrap && !expWrap.contains(e.target)) {{
                expMenu.classList.remove('show');
            }}
            var cascWraps = [
                ['expPeaFilterGroup', 'expPeaCascMenu'],
                ['expStatusFilterGroup', 'expStatusCascMenu'],
                ['expEqFilterGroup', 'expEqCascMenu'],
                ['expCommFilterGroup', 'expCommCascMenu'],
                ['expWrlFilterGroup', 'expWrlCascMenu']
            ];
            cascWraps.forEach(function(pair) {{
                var wEl = document.getElementById(pair[0]);
                var mEl = document.getElementById(pair[1]);
                if (mEl && mEl.classList.contains('show') && wEl && !wEl.contains(e.target)) {{
                    mEl.classList.remove('show');
                }}
            }});
        }});

        var cachedDashboardRows = [];
        var cachedSummaryExports = {{ eq: [], resp: [], prod: [], comm: [], pea: [] }};

        function computeSummaryMetricsFromRows(targetRows) {{
            var c = {{
                total: targetRows.length,
                Online: 0,
                'Telemetry Failure': 0,
                Offline: 0,
                Initializing: 0,
                Connecting: 0,
                other: 0
            }};
            targetRows.forEach(function(r) {{
                if (c[r.parent_state] !== undefined) c[r.parent_state]++;
                else c.other++;
            }});
            var offTot = c['Telemetry Failure'] + c.Offline + c.Initializing + c.Connecting + c.other;
            var dn = c.Online + offTot;
            var ava = dn > 0 ? (c.Online / dn) * 100 : 0;
            return {{
                counts: c,
                offlineTotal: offTot,
                denom: dn,
                avaPct: ava,
                tier: getAvaTierInfo(ava)
            }};
        }}

        function renderFilteredSummaryInSection6(matchedRows, allRowsCount) {{
            var stripEl = document.getElementById('dashFilteredSummaryBar');
            var tagEl = document.getElementById('filteredSummaryModeTag');
            var avaEl = document.getElementById('filteredSummaryAvaBadge');
            var badgesWrap = document.getElementById('filteredSummaryBadgesWrap');
            if (!stripEl || !badgesWrap) return;

            var fm = computeSummaryMetricsFromRows(matchedRows || []);
            var c = fm.counts;
            var offTot = fm.offlineTotal;
            var dn = fm.denom;
            var ava = fm.avaPct;
            var tier = fm.tier;

            var isFiltered = (matchedRows.length !== allRowsCount) ||
                isDimFiltered('pea') ||
                isDimFiltered('status') ||
                isDimFiltered('eq') ||
                isDimFiltered('prod') ||
                isDimFiltered('comm') ||
                isDimFiltered('wrl') ||
                isDimFiltered('resp') ||
                Boolean(String((document.getElementById('dashExplorerSearch') || {{}}).value || '').trim());

            stripEl.classList.toggle('is-filtered-mode', isFiltered);
            if (tagEl) {{
                tagEl.textContent = isFiltered ? 'ค่าตามเงื่อนไข Filter (กำลังกรองข้อมูล)' : 'ค่าตามเงื่อนไข Filter (แสดงทั้งหมด)';
            }}
            if (avaEl) {{
                avaEl.innerHTML = '<span class="ava-badge ' + tier.cls + '" style="font-size:12px;padding:3px 10px;">%AVA: ' + ava.toFixed(2) + '% (' + tier.gradeText + ')</span>';
            }}

            var onPct = dn > 0 ? ((c.Online / dn) * 100).toFixed(1) : '0.0';
            var offPct = dn > 0 ? ((offTot / dn) * 100).toFixed(1) : '0.0';

            badgesWrap.innerHTML =
                '<span class="fs-badge fs-total">ทั้งหมด: <strong>' + c.total.toLocaleString() + '</strong> ชุด</span>' +
                '<span class="fs-badge fs-online"><span class="fs-badge-dot" style="background:#16a34a;"></span>Online: <strong>' + c.Online.toLocaleString() + '</strong> (' + onPct + '%)</span>' +
                '<span class="fs-badge fs-offline-all"><span class="fs-badge-dot" style="background:#dc2626;"></span>ไม่ออนไลน์รวม: <strong>' + offTot.toLocaleString() + '</strong> (' + offPct + '%)</span>' +
                '<span class="fs-badge fs-connecting"><span class="fs-badge-dot" style="background:#ea580c;"></span>Connecting: <strong>' + c.Connecting.toLocaleString() + '</strong></span>' +
                '<span class="fs-badge fs-offline"><span class="fs-badge-dot" style="background:#dc2626;"></span>Offline: <strong>' + c.Offline.toLocaleString() + '</strong></span>' +
                '<span class="fs-badge fs-init"><span class="fs-badge-dot" style="background:#65a30d;"></span>Init: <strong>' + c.Initializing.toLocaleString() + '</strong></span>' +
                '<span class="fs-badge fs-tele"><span class="fs-badge-dot" style="background:#d97706;"></span>Telemetry: <strong>' + c['Telemetry Failure'].toLocaleString() + '</strong></span>';
        }}

        window.renderScadaDashboard = function() {{
            var dashScrollEl = document.getElementById('scadaDashboardView');
            var savedScrollTop = dashScrollEl ? dashScrollEl.scrollTop : 0;

            populateDashboardDropdownsOnce();

            var allSystemRows = [];
            for (var i = 0; i < dashAllRecords.length; i++) {{
                allSystemRows.push(getEffectiveRowData(i));
            }}
            cachedDashboardRows = allSystemRows;

            // การ์ดสรุปผลด้านบนสุด (Top Summary Cards) แสดงค่าภาพรวมทั้งหมดของระบบเสมอ
            var overall_summary = computeSummaryMetricsFromRows(allSystemRows);
            var overallCounts = overall_summary.counts;
            var overallOfflineTotal = overall_summary.offlineTotal;
            var overallDenom = overall_summary.denom;
            var overallAvaPct = overall_summary.avaPct;
            var overallTier = overall_summary.tier;

            // ข้อมูลที่ผ่านตัวกรองทุกเงื่อนไข (OR ในหมวดเดียวกัน / AND ข้ามหมวดหมู่)
            var fullyFilteredRows = allSystemRows.filter(function(r) {{
                return rowMatchesActiveFilters(r, '');
            }});
            var filteredSummary = computeSummaryMetricsFromRows(fullyFilteredRows);
            var counts = filteredSummary.counts;
            var offlineTotal = filteredSummary.offlineTotal;
            var avaPct = filteredSummary.avaPct;

            syncExplorerFilterDropdowns(allSystemRows);

            var navBadge = document.getElementById('navAvaBadge');
            if (navBadge) {{
                navBadge.textContent = overallAvaPct.toFixed(2) + '%';
                navBadge.className = 'nav-ava-pill ' + overallTier.cls;
            }}

            var kpiAvaValEl = document.getElementById('kpiAvaVal');
            var kpiAvaStatusPillEl = document.getElementById('kpiAvaStatusPill');
            if (kpiAvaValEl) {{
                kpiAvaValEl.textContent = overallAvaPct.toFixed(2) + '%';
                kpiAvaValEl.style.color = overallTier.textColor;
            }}
            if (kpiAvaStatusPillEl) {{
                kpiAvaStatusPillEl.textContent = overallTier.gradeText;
                kpiAvaStatusPillEl.className = 'ava-badge ' + overallTier.cls;
            }}

            document.getElementById('kpiTotalCount').textContent = 'ทั้งหมด ' + overallCounts.total.toLocaleString() + ' ชุด';
            document.getElementById('kpiOfflineTotalText').textContent = 'ไม่ออนไลน์ ' + overallOfflineTotal.toLocaleString() + ' ชุด (' + (overallDenom > 0 ? ((overallOfflineTotal / overallDenom) * 100).toFixed(2) : '0.00') + '%)';
            document.getElementById('dashFormulaRatioText').innerHTML =
                'Online <strong>' + overallCounts.Online.toLocaleString() + '</strong> | ' +
                'ไม่ออนไลน์ <strong>' + overallOfflineTotal.toLocaleString() + '</strong> ' +
                '(&bull; <strong>' + overallTier.gradeText + '</strong> &bull; ค่าภาพรวมทั้งระบบ)';

            var stackedBar = document.getElementById('kpiStackedBar');
            if (stackedBar && overallDenom > 0) {{
                stackedBar.innerHTML =
                    '<div style="width:' + ((overallCounts.Online / overallDenom) * 100) + '%;background:#16a34a;" title="Online"></div>' +
                    '<div style="width:' + ((overallCounts.Connecting / overallDenom) * 100) + '%;background:#ea580c;" title="Connecting"></div>' +
                    '<div style="width:' + ((overallCounts.Offline / overallDenom) * 100) + '%;background:#dc2626;" title="Offline"></div>' +
                    '<div style="width:' + ((overallCounts.Initializing / overallDenom) * 100) + '%;background:#65a30d;" title="Initializing"></div>' +
                    '<div style="width:' + ((overallCounts['Telemetry Failure'] / overallDenom) * 100) + '%;background:#d97706;" title="Telemetry Failure"></div>';
            }}

            function setKpiBox(cntId, pctId, shareId, barId, val) {{
                var pctAll = overallDenom > 0 ? (val / overallDenom) * 100 : 0;
                var pctOff = overallOfflineTotal > 0 ? (val / overallOfflineTotal) * 100 : 0;
                document.getElementById(cntId).textContent = val.toLocaleString();
                document.getElementById(pctId).textContent = pctAll.toFixed(2) + '% ของทั้งหมด';
                if (shareId) document.getElementById(shareId).textContent = pctOff.toFixed(1) + '% ที่ไม่ออนไลน์';
                document.getElementById(barId).style.width = Math.min(100, pctAll).toFixed(1) + '%';
            }}
            setKpiBox('kpiOnlineCount', 'kpiOnlinePct', null, 'kpiOnlineBar', overallCounts.Online);
            setKpiBox('kpiConnectingCount', 'kpiConnectingPct', 'kpiConnectingShareOffline', 'kpiConnectingBar', overallCounts.Connecting);
            setKpiBox('kpiOfflineCount', 'kpiOfflinePct', 'kpiOfflineShareOffline', 'kpiOfflineBar', overallCounts.Offline);
            setKpiBox('kpiInitCount', 'kpiInitPct', 'kpiInitShareOffline', 'kpiInitBar', overallCounts.Initializing);
            setKpiBox('kpiTeleCount', 'kpiTelePct', 'kpiTeleShareOffline', 'kpiTeleBar', overallCounts['Telemetry Failure']);

            // 1. ตารางแยกตามชนิดอุปกรณ์ (Multi-Select + Faceted Count)
            var eqMap = {{}};
            allSystemRows.forEach(function(r) {{
                var k = r.norm_eq;
                if (!eqMap[k]) eqMap[k] = {{ total: 0, Online: 0, 'Telemetry Failure': 0, Offline: 0, Initializing: 0, Connecting: 0 }};
            }});
            var rowsForEqTable = allSystemRows.filter(function(r) {{ return rowMatchesActiveFilters(r, 'eq'); }});
            rowsForEqTable.forEach(function(r) {{
                var k = r.norm_eq;
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
                var isSelected = explorerFilterSets.eq.has(k);
                var isZero = (d.total === 0 && !isSelected);
                var offSum = d['Telemetry Failure'] + d.Offline + d.Initializing + d.Connecting;
                var eqAva = d.total > 0 ? (d.Online / d.total) * 100 : 0;
                if (d.total > 0) {{
                    cachedSummaryExports.eq.push([k, d.total, d.Online, offSum, d['Telemetry Failure'], d.Offline, d.Initializing, d.Connecting, eqAva.toFixed(2) + '%']);
                }}
                var tr = document.createElement('tr');
                tr.className = 'clickable-row' + (isSelected ? ' is-active-filter' : '') + (isZero ? ' is-zero-match' : '');
                if (!isZero) {{
                    tr.onclick = function() {{ window.selectSummaryItem('eq', k); }};
                }}
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
                    '<td>รวมตามตัวกรอง</td>' +
                    '<td class="num">' + counts.total.toLocaleString() + '</td>' +
                    '<td class="num" style="color:#16a34a;">' + counts.Online.toLocaleString() + '</td>' +
                    '<td class="num" style="color:#dc2626;">' + offlineTotal.toLocaleString() + '</td>' +
                    '<td class="num hide-on-mobile">' + counts['Telemetry Failure'].toLocaleString() + '</td>' +
                    '<td class="num hide-on-mobile">' + counts.Offline.toLocaleString() + '</td>' +
                    '<td class="num hide-on-mobile">' + counts.Initializing.toLocaleString() + '</td>' +
                    '<td class="num hide-on-mobile">' + counts.Connecting.toLocaleString() + '</td>' +
                    '<td class="num">' + formatAvaBadge(avaPct) + '</td>' +
                '</tr>';

            // 2. ตารางสถานะการติดตามหน้างานของอุปกรณ์ที่ไม่ออนไลน์ (Multi-Select + Faceted Count)
            var respBuckets = [
                {{ key: 'รอ ผบอ. เข้าแก้ไข', label: 'รอ ผบอ.กบษ.ฉ.2 เข้าแก้ไข (FRTU/อุปกรณ์หลัก)', count: 0, color: '#ea580c' }},
                {{ key: 'รอ ผอส. เข้าแก้ไข', label: 'รอ ผอส.กสฟ.ฉ.2 เข้าแก้ไข (ระบบสื่อสาร)', count: 0, color: '#7c3aed' }},
                {{ key: 'รอ ผบอ. และ ผอส. เข้าแก้ไข', label: 'รอทั้ง ผบอ. และ ผอส. เข้าแก้ไขร่วมกัน', count: 0, color: '#dc2626' }},
                {{ key: 'ผบอ. เคยแก้ไขแล้วกลับมา Offline', label: 'ผบอ. เคยเข้าแก้ไขแล้ว กลับมาไม่ออนไลน์', count: 0, color: '#0284c7' }},
                {{ key: 'ระบบสื่อสารเคยแก้ไขแล้ว กลับมา Offline', label: 'ระบบสื่อสารเคยแก้ไขแล้ว กลับมาไม่ออนไลน์', count: 0, color: '#9333ea' }},
                {{ key: 'รอตรวจสอบสาเหตุ', label: 'ไม่ออนไลน์ (อยู่ระหว่างรอเข้าตรวจสอบสาเหตุ)', count: 0, color: '#64748b' }}
            ];
            var rowsForRespTable = allSystemRows.filter(function(r) {{ return rowMatchesActiveFilters(r, 'resp'); }});
            var respOfflineDenom = 0;
            rowsForRespTable.forEach(function(r) {{
                if (r.parent_state === 'Online') return;
                respOfflineDenom++;
                var bKey = classifyRowRespBucketKey(r);
                for (var bIdx = 0; bIdx < respBuckets.length; bIdx++) {{
                    if (respBuckets[bIdx].key === bKey) {{
                        respBuckets[bIdx].count++;
                        break;
                    }}
                }}
            }});
            var respSortMode = (document.getElementById('dashRespSort') || {{}}).value || 'count_desc';
            respBuckets.sort(function(a, b) {{
                var hasA = a.count > 0 ? 1 : 0;
                var hasB = b.count > 0 ? 1 : 0;
                if (hasA !== hasB) return hasB - hasA;
                return respSortMode === 'count_asc' ? (a.count - b.count) : (b.count - a.count);
            }});

            var respTbody = document.getElementById('dashRespTbody');
            respTbody.innerHTML = '';
            cachedSummaryExports.resp = [];
            respBuckets.forEach(function(b) {{
                var isSelected = explorerFilterSets.resp.has(b.key);
                var isZero = (b.count === 0 && !isSelected);
                var share = respOfflineDenom > 0 ? (b.count / respOfflineDenom) * 100 : 0;
                cachedSummaryExports.resp.push([b.label, b.count, share.toFixed(1) + '%']);
                var tr = document.createElement('tr');
                tr.className = 'clickable-row' + (isSelected ? ' is-active-filter' : '') + (isZero ? ' is-zero-match' : '');
                if (!isZero) {{
                    tr.onclick = function() {{ window.selectSummaryItem('resp', b.key); }};
                }}
                tr.innerHTML =
                    '<td><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + b.color + ';margin-right:7px;"></span>' + b.label + '</td>' +
                    '<td class="num"><strong>' + b.count.toLocaleString() + '</strong></td>' +
                    '<td class="num">' + share.toFixed(1) + '%</td>' +
                    '<td class="num"><span class="btn-jump-map">' + (isSelected ? '✓ เลือกอยู่' : 'กรองข้อมูล') + '</span></td>';
                respTbody.appendChild(tr);
            }});

            // 3. ตารางแยกตามประเภทผลิตภัณฑ์ FRTU (Multi-Select + Faceted Count)
            var prodGroupMap = {{}};
            allSystemRows.forEach(function(r) {{
                var gKey = r.prod_group || classifyMainBrandGroup(r.prod);
                var sKey = r.prod;
                if (!prodGroupMap[gKey]) {{
                    prodGroupMap[gKey] = {{
                        total: 0, Online: 0, OfflineSum: 0, Connecting: 0, Offline: 0, Init: 0, Tele: 0,
                        subs: {{}}
                    }};
                }}
                if (!prodGroupMap[gKey].subs[sKey]) {{
                    prodGroupMap[gKey].subs[sKey] = {{ total: 0, Online: 0, OfflineSum: 0, Connecting: 0, Offline: 0, Init: 0, Tele: 0 }};
                }}
            }});
            var rowsForProdTable = allSystemRows.filter(function(r) {{ return rowMatchesActiveFilters(r, 'prod'); }});
            rowsForProdTable.forEach(function(r) {{
                var gKey = r.prod_group || classifyMainBrandGroup(r.prod);
                var sKey = r.prod;
                var gObj = prodGroupMap[gKey];
                var sObj = gObj.subs[sKey];
                gObj.total++;
                sObj.total++;
                if (r.parent_state === 'Online') {{
                    gObj.Online++;
                    sObj.Online++;
                }} else {{
                    gObj.OfflineSum++;
                    sObj.OfflineSum++;
                    if (r.parent_state === 'Connecting') {{ gObj.Connecting++; sObj.Connecting++; }}
                    else if (r.parent_state === 'Offline') {{ gObj.Offline++; sObj.Offline++; }}
                    else if (r.parent_state === 'Initializing') {{ gObj.Init++; sObj.Init++; }}
                    else if (r.parent_state === 'Telemetry Failure') {{ gObj.Tele++; sObj.Tele++; }}
                }}
            }});

            var prodSortMode = (document.getElementById('dashProdSort') || {{}}).value || 'ava_desc';
            var sortedProdGroupKeys = sortSummaryKeys(Object.keys(prodGroupMap), prodGroupMap, prodSortMode);
            var prodTbody = document.getElementById('dashProductTbody');
            prodTbody.innerHTML = '';
            cachedSummaryExports.prod = [];

            window.toggleProdTableGroupExpand = function(gKey, ev) {{
                if (ev) {{
                    ev.preventDefault();
                    ev.stopPropagation();
                }}
                expandedBrandGroupsInTable[gKey] = !Boolean(expandedBrandGroupsInTable[gKey]);
                window.renderScadaDashboard();
            }};

            sortedProdGroupKeys.forEach(function(gKey) {{
                var gData = prodGroupMap[gKey];
                var subKeys = sortSummaryKeys(Object.keys(gData.subs), gData.subs, prodSortMode);
                var checkedSubsInGrp = subKeys.filter(function(sk) {{ return explorerFilterSets.prod.has(sk); }});
                var hasSubSelected = checkedSubsInGrp.length > 0;
                var isZeroGroup = (gData.total === 0 && !hasSubSelected);
                var gAva = gData.total > 0 ? (gData.Online / gData.total) * 100 : 0;
                var gSubBreak = 'Conn ' + gData.Connecting + ' · Off ' + gData.Offline + ' · Init ' + gData.Init + ' · Tele ' + gData.Tele;
                var isExpanded = !isZeroGroup && Boolean(expandedBrandGroupsInTable[gKey]);

                if (gData.total > 0) {{
                    cachedSummaryExports.prod.push(['[ยี่ห้อหลัก] ' + gKey + ' (' + subKeys.length + ' รุ่นย่อย)', gData.total, gData.Online, gData.OfflineSum, gSubBreak, gAva.toFixed(2) + '%']);
                }}

                var trGroup = document.createElement('tr');
                trGroup.className = 'clickable-row prod-group-row' + (hasSubSelected ? ' is-active-filter' : '') + (isZeroGroup ? ' is-zero-match' : '');
                if (!isZeroGroup) {{
                    trGroup.onclick = function(ev) {{
                        expandedBrandGroupsInTable[gKey] = true;
                        window.toggleTreeParentGroupCheckbox('prod', gKey, allSystemRows, ev);
                    }};
                }}
                trGroup.innerHTML =
                    '<td>' +
                        '<button type="button" class="prod-expand-btn' + (isExpanded ? ' is-open' : '') + '" title="คลิกเพื่อขยาย/ซ่อนยี่ห้อย่อย" ' + (isZeroGroup ? 'disabled' : 'onclick="window.toggleProdTableGroupExpand(&quot;' + gKey.replace(/"/g, '&quot;') + '&quot;, event)"') + '>&#9654;</button>' +
                        '<strong>' + gKey + '</strong> <span style="font-size:11px;color:var(--text-secondary);">(' + subKeys.length + ' ยี่ห้อย่อย' + (hasSubSelected ? (' · เลือก ' + checkedSubsInGrp.length) : '') + ')</span>' +
                        '<div class="mobile-sub-breakdown">' + gSubBreak + '</div>' +
                    '</td>' +
                    '<td class="num"><strong>' + gData.total.toLocaleString() + '</strong></td>' +
                    '<td class="num" style="color:#16a34a;font-weight:600;">' + gData.Online.toLocaleString() + '</td>' +
                    '<td class="num" style="color:#dc2626;font-weight:600;">' + gData.OfflineSum.toLocaleString() + '</td>' +
                    '<td class="num hide-on-mobile" style="font-size:11px;color:var(--text-secondary);">' + gSubBreak + '</td>' +
                    '<td class="num">' + formatAvaBadge(gAva) + '</td>';
                prodTbody.appendChild(trGroup);

                subKeys.forEach(function(sKey) {{
                    var sd = gData.subs[sKey];
                    var isSubSelected = explorerFilterSets.prod.has(sKey);
                    var isZeroSub = (sd.total === 0 && !isSubSelected);
                    var sAva = sd.total > 0 ? (sd.Online / sd.total) * 100 : 0;
                    var sSubBreak = 'Conn ' + sd.Connecting + ' · Off ' + sd.Offline + ' · Init ' + sd.Init + ' · Tele ' + sd.Tele;
                    if (sd.total > 0) {{
                        cachedSummaryExports.prod.push(['   ↳ ' + sKey, sd.total, sd.Online, sd.OfflineSum, sSubBreak, sAva.toFixed(2) + '%']);
                    }}
                    if (!isExpanded) return;
                    var trSub = document.createElement('tr');
                    trSub.className = 'clickable-row prod-sub-row' + (isSubSelected ? ' is-active-filter' : '') + (isZeroSub ? ' is-zero-match' : '');
                    if (!isZeroSub) {{
                        trSub.onclick = function(ev) {{
                            ev.stopPropagation();
                            window.selectSummaryItem('prod', sKey);
                        }};
                    }}
                    trSub.innerHTML =
                        '<td><span style="color:var(--text-secondary);margin-right:4px;">↳</span>' + sKey + '<div class="mobile-sub-breakdown">' + sSubBreak + '</div></td>' +
                        '<td class="num">' + sd.total.toLocaleString() + '</td>' +
                        '<td class="num" style="color:#16a34a;font-weight:600;">' + sd.Online.toLocaleString() + '</td>' +
                        '<td class="num" style="color:#dc2626;font-weight:600;">' + sd.OfflineSum.toLocaleString() + '</td>' +
                        '<td class="num hide-on-mobile" style="font-size:11px;color:var(--text-secondary);">' + sSubBreak + '</td>' +
                        '<td class="num">' + formatAvaBadge(sAva) + '</td>';
                    prodTbody.appendChild(trSub);
                }});
            }});

            // 4. ตารางแยกตามอุปกรณ์สื่อสารและแม่ข่าย (Multi-Select + Faceted Count)
            var commMode = (document.getElementById('dashCommGroupMode') || {{}}).value || 'comm_group';
            var commSortMode = (document.getElementById('dashCommSort') || {{}}).value || 'ava_desc';
            var commTbody = document.getElementById('dashCommTbody');
            commTbody.innerHTML = '';
            cachedSummaryExports.comm = [];

            window.toggleCommTableGroupExpand = function(gKey, ev) {{
                if (ev) {{
                    ev.preventDefault();
                    ev.stopPropagation();
                }}
                expandedCommGroupsInTable[gKey] = !Boolean(expandedCommGroupsInTable[gKey]);
                window.renderScadaDashboard();
            }};

            if (commMode === 'comm_group') {{
                var commGroupMap = {{}};
                allSystemRows.forEach(function(r) {{
                    var gKey = r.comm_group || classifyMainCommGroup(r.comm_type, r.comm_item);
                    var sKey = r.comm_combo;
                    if (!commGroupMap[gKey]) {{
                        commGroupMap[gKey] = {{ total: 0, Online: 0, OfflineSum: 0, subs: {{}} }};
                    }}
                    if (!commGroupMap[gKey].subs[sKey]) {{
                        commGroupMap[gKey].subs[sKey] = {{ label: sKey, comm_type: r.comm_type, comm_item: r.comm_item, total: 0, Online: 0, OfflineSum: 0 }};
                    }}
                }});
                var rowsForCommTable = allSystemRows.filter(function(r) {{ return rowMatchesActiveFilters(r, 'comm'); }});
                rowsForCommTable.forEach(function(r) {{
                    var gKey = r.comm_group || classifyMainCommGroup(r.comm_type, r.comm_item);
                    var sKey = r.comm_combo;
                    var gObj = commGroupMap[gKey];
                    var sObj = gObj.subs[sKey];
                    gObj.total++;
                    sObj.total++;
                    if (r.parent_state === 'Online') {{
                        gObj.Online++;
                        sObj.Online++;
                    }} else {{
                        gObj.OfflineSum++;
                        sObj.OfflineSum++;
                    }}
                }});

                var sortedCommGroupKeys = sortSummaryKeys(Object.keys(commGroupMap), commGroupMap, commSortMode);
                sortedCommGroupKeys.forEach(function(gKey) {{
                    var gData = commGroupMap[gKey];
                    var subKeys = sortSummaryKeys(Object.keys(gData.subs), gData.subs, commSortMode);
                    var checkedSubsInGrp = subKeys.filter(function(sk) {{ return explorerFilterSets.comm.has(sk); }});
                    var hasSubSelected = checkedSubsInGrp.length > 0;
                    var isZeroGroup = (gData.total === 0 && !hasSubSelected);
                    var gAva = gData.total > 0 ? (gData.Online / gData.total) * 100 : 0;
                    var isExpanded = !isZeroGroup && Boolean(expandedCommGroupsInTable[gKey]);

                    if (gData.total > 0) {{
                        cachedSummaryExports.comm.push(['[กลุ่มหลัก] ' + gKey + ' (' + subKeys.length + ' รูปแบบย่อย)', gData.total, gData.Online, gData.OfflineSum, gAva.toFixed(2) + '%']);
                    }}

                    var trGroup = document.createElement('tr');
                    trGroup.className = 'clickable-row prod-group-row' + (hasSubSelected ? ' is-active-filter' : '') + (isZeroGroup ? ' is-zero-match' : '');
                    if (!isZeroGroup) {{
                        trGroup.onclick = function(ev) {{
                            expandedCommGroupsInTable[gKey] = true;
                            window.toggleTreeParentGroupCheckbox('comm', gKey, allSystemRows, ev);
                        }};
                    }}
                    trGroup.innerHTML =
                        '<td>' +
                            '<button type="button" class="prod-expand-btn' + (isExpanded ? ' is-open' : '') + '" title="คลิกเพื่อขยาย/ซ่อนรูปแบบย่อย" ' + (isZeroGroup ? 'disabled' : 'onclick="window.toggleCommTableGroupExpand(&quot;' + gKey.replace(/"/g, '&quot;') + '&quot;, event)"') + '>&#9654;</button>' +
                            '<strong>' + gKey + '</strong> <span style="font-size:11px;color:var(--text-secondary);">(' + subKeys.length + ' รูปแบบย่อย' + (hasSubSelected ? (' · เลือก ' + checkedSubsInGrp.length) : '') + ')</span>' +
                        '</td>' +
                        '<td class="num"><strong>' + gData.total.toLocaleString() + '</strong></td>' +
                        '<td class="num" style="color:#16a34a;font-weight:600;">' + gData.Online.toLocaleString() + '</td>' +
                        '<td class="num" style="color:#dc2626;font-weight:600;">' + gData.OfflineSum.toLocaleString() + '</td>' +
                        '<td class="num">' + formatAvaBadge(gAva) + '</td>';
                    commTbody.appendChild(trGroup);

                    subKeys.forEach(function(sKey) {{
                        var sd = gData.subs[sKey];
                        var isSubSelected = explorerFilterSets.comm.has(sKey);
                        var isZeroSub = (sd.total === 0 && !isSubSelected);
                        var sAva = sd.total > 0 ? (sd.Online / sd.total) * 100 : 0;
                        if (sd.total > 0) {{
                            cachedSummaryExports.comm.push(['   ↳ ' + sKey, sd.total, sd.Online, sd.OfflineSum, sAva.toFixed(2) + '%']);
                        }}
                        if (!isExpanded) return;
                        var trSub = document.createElement('tr');
                        trSub.className = 'clickable-row prod-sub-row' + (isSubSelected ? ' is-active-filter' : '') + (isZeroSub ? ' is-zero-match' : '');
                        if (!isZeroSub) {{
                            trSub.onclick = function(ev) {{
                                ev.stopPropagation();
                                window.selectSummaryItem('comm', sKey);
                            }};
                        }}
                        trSub.innerHTML =
                            '<td><span style="color:var(--text-secondary);margin-right:4px;">↳</span>' + sKey +
                                '<div style="font-size:10.5px;color:var(--text-secondary);padding-left:14px;">Commu.: ' + sd.comm_type + ' | Item: ' + sd.comm_item + '</div>' +
                            '</td>' +
                            '<td class="num">' + sd.total.toLocaleString() + '</td>' +
                            '<td class="num" style="color:#16a34a;font-weight:600;">' + sd.Online.toLocaleString() + '</td>' +
                            '<td class="num" style="color:#dc2626;font-weight:600;">' + sd.OfflineSum.toLocaleString() + '</td>' +
                            '<td class="num">' + formatAvaBadge(sAva) + '</td>';
                        commTbody.appendChild(trSub);
                    }});
                }});
            }} else {{
                var commMap = {{}};
                var targetIgnoreDim = (commMode === 'master') ? 'wrl' : 'comm';
                allSystemRows.forEach(function(r) {{
                    var k = '';
                    var subText = '';
                    var filterTargetType = 'comm';
                    if (commMode === 'commu_item') {{
                        k = r.comm_combo;
                        subText = 'กลุ่มหลัก: ' + r.comm_group + ' | Commu.: ' + r.comm_type + ' | Item: ' + r.comm_item;
                    }} else if (commMode === 'type') {{
                        k = r.comm_type;
                        subText = 'กลุ่มหลัก: ' + r.comm_group + ' (ชนิดสื่อสารย่อย Commu.)';
                    }} else if (commMode === 'item') {{
                        k = r.comm_item;
                        subText = 'กลุ่มหลัก: ' + r.comm_group + ' (ประเภทย่อย Item)';
                    }} else {{
                        k = r.comm_ref;
                        subText = 'แม่ข่ายสื่อสาร (WRL Master)';
                        filterTargetType = 'wrl';
                    }}
                    if (!commMap[k]) commMap[k] = {{ label: k, sub: subText, fType: filterTargetType, total: 0, Online: 0, OfflineSum: 0 }};
                }});
                var rowsForCommFlat = allSystemRows.filter(function(r) {{ return rowMatchesActiveFilters(r, targetIgnoreDim); }});
                rowsForCommFlat.forEach(function(r) {{
                    var k = commMode === 'commu_item' ? r.comm_combo : (commMode === 'type' ? r.comm_type : (commMode === 'item' ? r.comm_item : r.comm_ref));
                    commMap[k].total++;
                    if (r.parent_state === 'Online') commMap[k].Online++;
                    else commMap[k].OfflineSum++;
                }});
                var sortedCommKeys = sortSummaryKeys(Object.keys(commMap), commMap, commSortMode);
                sortedCommKeys.forEach(function(k) {{
                    var d = commMap[k];
                    var isSelected = (d.fType === 'wrl' ? explorerFilterSets.wrl.has(k) : explorerFilterSets.comm.has(k));
                    var isZero = (d.total === 0 && !isSelected);
                    var cAva = d.total > 0 ? (d.Online / d.total) * 100 : 0;
                    if (d.total > 0) {{
                        cachedSummaryExports.comm.push([d.label, d.total, d.Online, d.OfflineSum, cAva.toFixed(2) + '%']);
                    }}
                    var tr = document.createElement('tr');
                    tr.className = 'clickable-row' + (isSelected ? ' is-active-filter' : '') + (isZero ? ' is-zero-match' : '');
                    if (!isZero) {{
                        tr.onclick = function() {{ window.selectSummaryItem(d.fType, k); }};
                    }}
                    tr.innerHTML =
                        '<td><strong>' + d.label + '</strong><div style="font-size:10.5px;color:var(--text-secondary);">' + d.sub + '</div></td>' +
                        '<td class="num">' + d.total.toLocaleString() + '</td>' +
                        '<td class="num" style="color:#16a34a;font-weight:600;">' + d.Online.toLocaleString() + '</td>' +
                        '<td class="num" style="color:#dc2626;font-weight:600;">' + d.OfflineSum.toLocaleString() + '</td>' +
                        '<td class="num">' + formatAvaBadge(cAva) + '</td>';
                    commTbody.appendChild(tr);
                }});
            }}

            // 5. ตารางแยกตามการไฟฟ้า (กฟจ. / กฟส.) (Multi-Select + Faceted Count)
            var peaMap = {{}};
            allSystemRows.forEach(function(r) {{
                var k = r.pea;
                if (!peaMap[k]) peaMap[k] = {{ total: 0, Online: 0, 'Telemetry Failure': 0, Offline: 0, Initializing: 0, Connecting: 0 }};
            }});
            var rowsForPeaTable = allSystemRows.filter(function(r) {{ return rowMatchesActiveFilters(r, 'pea'); }});
            rowsForPeaTable.forEach(function(r) {{
                var k = r.pea;
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
                var isPeaSelected = explorerFilterSets.pea.has(k);
                var isZero = (d.total === 0 && !isPeaSelected);
                var offSum = d['Telemetry Failure'] + d.Offline + d.Initializing + d.Connecting;
                var pAva = d.total > 0 ? (d.Online / d.total) * 100 : 0;
                if (d.total > 0) {{
                    cachedSummaryExports.pea.push([k, d.total, d.Online, offSum, d['Telemetry Failure'], d.Offline, d.Initializing, d.Connecting, pAva.toFixed(2) + '%']);
                }}
                var tr = document.createElement('tr');
                tr.className = 'clickable-row' + (isPeaSelected ? ' is-active-filter' : '') + (isZero ? ' is-zero-match' : '');
                if (!isZero) {{
                    tr.onclick = function() {{
                        window.onTopPeaFilterChange(k);
                    }};
                }}
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

            if (dashScrollEl) {{
                dashScrollEl.scrollTop = savedScrollTop;
            }}
        }};

        window.selectSummaryItem = function(filterType, value) {{
            window.toggleMultiSelectOption(filterType, value);
        }};

        window.onExplorerDropdownChange = function() {{
            dashExplorerLimit = 250;
            window.renderScadaDashboard();
        }};

        window.clearSingleExplorerFilter = function(filterKey, specificVal) {{
            if (filterKey === 'search') {{
                var s = document.getElementById('dashExplorerSearch');
                if (s) s.value = '';
            }} else if (explorerFilterSets[filterKey]) {{
                if (specificVal !== undefined && specificVal !== null && specificVal !== '') {{
                    explorerFilterSets[filterKey].delete(specificVal);
                }} else {{
                    explorerFilterSets[filterKey].clear();
                }}
            }}
            syncLegacyExplorerStateFromSets();
            window.renderScadaDashboard();
        }};

        window.resetDashExplorerFilters = function() {{
            Object.keys(explorerFilterSets).forEach(function(k) {{
                explorerFilterSets[k].clear();
            }});
            syncLegacyExplorerStateFromSets();
            dashExplorerLimit = 250;

            var s = document.getElementById('dashExplorerSearch');
            if (s) s.value = '';
            brandSearchQueryByMode = {{ top: '', exp: '' }};
            cascFilterSearchQuery = {{ pea: '', status: '', eq: '', comm: '', wrl: '' }};

            window.closeAllCascFilterMenus();
            expandedBrandGroupsInMenu = Object.create(null);
            expandedCommGroupsInMenu = Object.create(null);
            expandedBrandGroupsInTable = Object.create(null);
            expandedCommGroupsInTable = Object.create(null);

            window.renderScadaDashboard();
        }};

        window.showAllExplorerRows = function() {{
            dashExplorerLimit = (dashExplorerLimit > 250) ? 250 : 99999;
            window.renderDashExplorerTable();
        }};

        function syncFilterControlHighlights(activeChipCount, q) {{
            function toggleCtrlGroupActive(selectOrGroupId, isActive) {{
                var el = document.getElementById(selectOrGroupId);
                if (!el) return;
                var grp = el.classList.contains('dash-ctrl-group') ? el : el.closest('.dash-ctrl-group');
                if (grp) grp.classList.toggle('is-filter-active', Boolean(isActive));
            }}

            toggleCtrlGroupActive('expPeaFilterGroup', isDimFiltered('pea'));
            toggleCtrlGroupActive('expHierBrandFilterWrap', isDimFiltered('prod'));
            toggleCtrlGroupActive('expStatusFilterGroup', isDimFiltered('status'));
            toggleCtrlGroupActive('expEqFilterGroup', isDimFiltered('eq'));
            toggleCtrlGroupActive('expCommFilterGroup', isDimFiltered('comm'));
            toggleCtrlGroupActive('expWrlFilterGroup', isDimFiltered('wrl'));

            var searchInp = document.getElementById('dashExplorerSearch');
            if (searchInp) {{
                searchInp.classList.toggle('is-filter-active', Boolean(q));
            }}

            var kpiMap = {{
                'Online': 'kpiCardOnline',
                'Connecting': 'kpiCardConnecting',
                'Offline': 'kpiCardOffline',
                'Initializing': 'kpiCardInitializing',
                'Telemetry Failure': 'kpiCardTelemetry'
            }};
            Object.keys(kpiMap).forEach(function(stKey) {{
                var cardEl = document.getElementById(kpiMap[stKey]);
                if (cardEl) {{
                    cardEl.classList.toggle('is-selected', explorerFilterSets.status.has(stKey));
                }}
            }});

            var hasActive = activeChipCount > 0;
            var expResetBtn = document.getElementById('expResetFilterBtn');
            if (expResetBtn) {{
                expResetBtn.classList.toggle('has-active-filters', hasActive);
                var txtSpan = expResetBtn.querySelector('.btn-clear-filter-text');
                if (txtSpan) {{
                    txtSpan.textContent = hasActive ? ('ล้างตัวกรอง (' + activeChipCount + ')') : 'ล้างตัวกรอง';
                }}
            }}

            var showAllBtn = document.getElementById('showAllExplorerRowsBtn');
            if (showAllBtn) {{
                var isShowingAll = dashExplorerLimit > 250;
                showAllBtn.classList.toggle('is-active', isShowingAll);
                showAllBtn.textContent = isShowingAll ? 'แสดง 250 แถวแรก' : 'แสดงครบทุกแถว';
            }}
        }}

        function renderActiveFilterChips(q) {{
            var bar = document.getElementById('dashActiveFilterBar');
            var chips = [];

            var dimTitleMap = {{
                pea: 'การไฟฟ้า',
                status: 'สถานะ',
                eq: 'ชนิดอุปกรณ์',
                prod: 'ผลิตภัณฑ์',
                comm: 'สื่อสาร',
                wrl: 'WRL Master',
                resp: 'การติดตาม'
            }};

            ['pea', 'status', 'eq', 'prod', 'comm', 'wrl', 'resp'].forEach(function(dim) {{
                var st = explorerFilterSets[dim];
                if (!st || st.size === 0) return;
                Array.from(st).forEach(function(val) {{
                    var disp = val;
                    if (val === 'ALL_OFFLINE') disp = 'ไม่ออนไลน์ทั้งหมด';
                    else if (val.indexOf('GROUP:') === 0) disp = '[กลุ่มหลัก] ' + val.substring(6);
                    else if (val.indexOf('COMM_GROUP:') === 0) disp = '[กลุ่มหลัก] ' + val.substring(11);
                    chips.push({{
                        key: dim,
                        val: val,
                        label: dimTitleMap[dim] + ': ' + disp
                    }});
                }});
            }});

            if (q) {{
                chips.push({{ key: 'search', val: '', label: 'ค้นหา: "' + q + '"' }});
            }}

            syncFilterControlHighlights(chips.length, q);

            if (!bar) return;
            if (chips.length === 0) {{
                bar.style.display = 'none';
                bar.innerHTML = '';
                return;
            }}

            bar.style.display = 'flex';
            var html = '<strong>ตัวกรองที่เลือก (' + chips.length + '):</strong>';
            chips.forEach(function(c) {{
                var safeK = escapeHtmlSafe(c.key);
                var safeV = escapeHtmlSafe(c.val || '');
                html += '<span class="filter-chip-badge">' + escapeHtmlSafe(c.label) +
                        '<button type="button" data-k="' + safeK + '" data-v="' + safeV + '" onclick="window.clearSingleExplorerFilter(this.getAttribute(&quot;data-k&quot;), this.getAttribute(&quot;data-v&quot;))" title="ลบตัวกรองนี้">×</button></span>';
            }});
            html += '<button type="button" class="filter-chip-clear-all" onclick="window.resetDashExplorerFilters()" title="ล้างตัวกรองทั้งหมด">ล้างตัวกรองทั้งหมด ×</button>';
            bar.innerHTML = html;
        }}

        window.renderDashExplorerTable = function() {{
            var rawQ = String((document.getElementById('dashExplorerSearch') || {{}}).value || '').trim();
            renderActiveFilterChips(rawQ);

            var matched = cachedDashboardRows.filter(function(r) {{
                return rowMatchesActiveFilters(r, '');
            }});

            // เมื่อพิมพ์ค้นหา ให้กรองรายการที่ตรงเงื่อนไขและเรียงลำดับจากน้อยไปมาก (Ascending Order: A-Z, ก-ฮ, รหัสอุปกรณ์)
            if (rawQ) {{
                var directMatched = [];
                var fuzzyMatched = [];
                matched.forEach(function(r) {{
                    var sc = window.scoreFieldsAgainstSmartQuery([
                        r.site_id, r.cmd, r.loc, r.pea, r.norm_eq, r.prod, r.ip, r.dnp, r.comm_type, r.comm_item, r.comm_ref, r.state_full
                    ], rawQ);
                    if (sc >= 50) directMatched.push(r);
                    else if (sc > 0) fuzzyMatched.push(r);
                }});
                matched = directMatched.length > 0 ? directMatched : fuzzyMatched;
                matched.sort(function(a, b) {{
                    var c1 = window.compareAscendingNatural(a.site_id, b.site_id);
                    if (c1 !== 0) return c1;
                    var c2 = window.compareAscendingNatural(a.cmd, b.cmd);
                    if (c2 !== 0) return c2;
                    return window.compareAscendingNatural(a.loc, b.loc);
                }});
            }}

            window.cachedExplorerMatchedRows = matched;
            renderFilteredSummaryInSection6(matched, cachedDashboardRows.length);

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
                var mapBtnHtml = r.safe_id
                    ? '<button type="button" class="btn-jump-map" data-safe-id="' + r.safe_id + '" onclick="window.jumpFromDashToMapPin(this.getAttribute(&quot;data-safe-id&quot;))">ดูพิกัด</button>'
                    : '<span style="font-size:11px;opacity:0.65;">ไม่มีพิกัด</span>';
                var reportIssueBtnHtml = '<button type="button" class="btn-jump-map" style="margin-left:4px;color:#c2410c;border-color:#fdba74;" data-row-idx="' + r.idx + '" onclick="window.openUserReportModal(Number(this.getAttribute(&quot;data-row-idx&quot;)))" title="แจ้งข้อมูลไม่ถูกต้องของอุปกรณ์นี้">แจ้งแก้ไข</button>';

                var hlSite = rawQ ? window.highlightSearchText(r.site_id, rawQ) : escapeHtmlSafe(r.site_id);
                var hlCmd = rawQ ? window.highlightSearchText(r.cmd, rawQ) : escapeHtmlSafe(r.cmd);
                var hlLoc = rawQ ? window.highlightSearchText(r.loc, rawQ) : escapeHtmlSafe(r.loc);
                var hlPea = rawQ ? window.highlightSearchText(r.pea, rawQ) : escapeHtmlSafe(r.pea);
                var hlEq = rawQ ? window.highlightSearchText(r.norm_eq, rawQ) : escapeHtmlSafe(r.norm_eq);
                var hlProd = rawQ ? window.highlightSearchText(r.prod, rawQ) : escapeHtmlSafe(r.prod);
                var hlCommType = rawQ ? window.highlightSearchText(r.comm_type, rawQ) : escapeHtmlSafe(r.comm_type);
                var hlCommItem = rawQ ? window.highlightSearchText(r.comm_item, rawQ) : escapeHtmlSafe(r.comm_item);
                var hlCommRef = rawQ ? window.highlightSearchText(r.comm_ref, rawQ) : escapeHtmlSafe(r.comm_ref);
                var hlIp = rawQ ? window.highlightSearchText(r.ip, rawQ) : escapeHtmlSafe(r.ip);
                var hlDnp = rawQ ? window.highlightSearchText(r.dnp, rawQ) : escapeHtmlSafe(r.dnp);

                tr.innerHTML =
                    '<td class="num" style="opacity:0.7;">' + (idx + 1) + '</td>' +
                    '<td><strong>' + hlSite + '</strong>' +
                        '<div class="mobile-sub-breakdown">' + hlProd + ' · ' + hlCommType + ' (' + hlCommItem + ') · IP: ' + hlIp + '</div>' +
                    '</td>' +
                    '<td><span style="font-weight:600;">' + hlCmd + '</span></td>' +
                    '<td>' + hlLoc + '</td>' +
                    '<td>' + hlPea + '</td>' +
                    '<td>' + hlEq + '</td>' +
                    '<td class="hide-on-mobile">' + hlProd + '</td>' +
                    '<td class="hide-on-mobile">' + hlCommType + '</td>' +
                    '<td class="hide-on-mobile">' + hlCommItem + '</td>' +
                    '<td class="hide-on-mobile">' + hlCommRef + '</td>' +
                    '<td class="hide-on-mobile" style="font-family:monospace;">' + hlIp + '</td>' +
                    '<td class="hide-on-mobile" style="font-family:monospace;">' + hlDnp + '</td>' +
                    '<td><span class="status-pill-sm" style="background:' + badgeColor + ';">' + escapeHtmlSafe(r.state_full) + '</span></td>' +
                    '<td style="white-space:nowrap;">' + mapBtnHtml + reportIssueBtnHtml + '</td>';
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

        // ============================================================================
        // === [REQUIREMENT 4] ยกระดับหน้าต่าง "แจ้งข้อมูลอุปกรณ์ไม่ถูกต้อง" (Smart Search + Summary Card + Smooth Field Cards) ===
        // ============================================================================
        var USER_REPORT_FIELD_LIST = [
            "Site ID",
            "รหัสสั่งการ",
            "สถานที่",
            "การไฟฟ้า",
            "ชนิดอุปกรณ์",
            "ประเภทผลิตภัณฑ์",
            "อุปกรณ์สื่อสาร (Commu.)",
            "ประเภทย่อยสื่อสาร (Item)",
            "เชื่อมกับ WRL Master",
            "IP",
            "DNP",
            "LAT/LONG",
            "State SCADA",
            "State SCADA (หลังตรวจสอบ)",
            "รายละเอียดการแก้ไขข้อขัดข้อง",
            "รายละเอียดการเข้าแก้ไข",
            "รอ ผอส. เข้าแก้ไข",
            "รอ ผบอ. เข้าแก้ไข",
            "วันที่เข้าตรวจสอบ"
        ];

        var urCurrentSelectedIdx = -1;
        var urPerFieldCorrections = Object.create(null);

        function showUrFormView() {{
            var formWrap = document.getElementById('urFormBodyWrap');
            var successView = document.getElementById('urSuccessStateView');
            if (formWrap) formWrap.style.display = 'block';
            if (successView) successView.classList.remove('show');
        }}

        function showUrSuccessStateView(siteId, cmdCode, incorrectFields) {{
            var formWrap = document.getElementById('urFormBodyWrap');
            var successView = document.getElementById('urSuccessStateView');
            var chipEl = document.getElementById('urSuccessSummaryChip');
            if (formWrap) formWrap.style.display = 'none';
            if (chipEl) {{
                chipEl.innerHTML =
                    '<span>อุปกรณ์: <strong>' + escapeHtmlSafe(siteId) + '</strong> (' + escapeHtmlSafe(cmdCode) + ')</span>' +
                    '<span>•</span>' +
                    '<span>ฟิลด์ที่แจ้งแก้ไข: <strong>' + escapeHtmlSafe((incorrectFields || []).join(', ')) + '</strong></span>';
            }}
            if (successView) successView.classList.add('show');
        }}

        window.openUrSmartDropdown = function() {{
            var dd = document.getElementById('urSmartSearchDropdown');
            if (dd) dd.classList.add('show');
            window.filterUserReportDeviceOptions();
        }};

        window.clearUrSmartSearchInput = function(ev) {{
            if (ev) {{
                ev.preventDefault();
                ev.stopPropagation();
            }}
            var inp = document.getElementById('urDeviceSearchInput');
            if (inp) {{
                inp.value = '';
                inp.focus();
            }}
            window.filterUserReportDeviceOptions();
        }};

        window.changeUserReportDevice = function() {{
            var searchContainer = document.getElementById('urSmartSearchContainer');
            var selectedCard = document.getElementById('urSelectedDeviceCard');
            var searchInp = document.getElementById('urDeviceSearchInput');
            if (selectedCard) selectedCard.classList.remove('show');
            if (searchContainer) searchContainer.style.display = 'block';
            if (searchInp) {{
                searchInp.focus();
                searchInp.select();
            }}
            window.openUrSmartDropdown();
        }};

        window.toggleUrFullFieldsView = function() {{
            var box = document.getElementById('urDeviceFullDataBox');
            var btn = document.getElementById('urToggleFullFieldsBtn');
            if (!box) return;
            var isHidden = (box.style.display === 'none' || !box.style.display);
            box.style.display = isHidden ? 'block' : 'none';
            if (btn) btn.textContent = isHidden ? 'ซ่อนข้อมูลทุกฟิลด์ ▴' : 'ดูข้อมูลครบทุกฟิลด์ ▾';
        }};

        window.openUserReportModal = function(preselectRowIdx) {{
            var modal = document.getElementById('userReportModalOverlay');
            if (!modal) return;
            showUrFormView();

            var searchInp = document.getElementById('urDeviceSearchInput');
            if (searchInp) searchInp.value = '';
            window.filterUserReportDeviceOptions();

            if (typeof preselectRowIdx === 'number' && !isNaN(preselectRowIdx) && preselectRowIdx >= 0) {{
                var sel = document.getElementById('urDeviceSelect');
                if (sel) sel.value = String(preselectRowIdx);
                window.onUserReportDeviceSelected(String(preselectRowIdx));
            }} else if (urCurrentSelectedIdx < 0) {{
                window.resetUserReportForm();
            }}
            modal.classList.add('show');
        }};

        window.closeUserReportModal = function() {{
            var modal = document.getElementById('userReportModalOverlay');
            if (modal) modal.classList.remove('show');
            var dd = document.getElementById('urSmartSearchDropdown');
            if (dd) dd.classList.remove('show');
        }};

        window.filterUserReportDeviceOptions = function() {{
            var searchInp = document.getElementById('urDeviceSearchInput');
            var clearBtn = document.getElementById('urSmartClearBtn');
            var dd = document.getElementById('urSmartSearchDropdown');
            var sel = document.getElementById('urDeviceSelect');
            var cntBadge = document.getElementById('urMatchedDeviceCount');
            if (!sel) return;

            var rawQ = String((searchInp && searchInp.value) || '').trim();
            if (clearBtn) clearBtn.style.display = rawQ.length > 0 ? 'inline-flex' : 'none';

            var prevVal = sel.value;
            var directList = [];
            var fuzzyList = [];

            for (var i = 0; i < dashAllRecords.length; i++) {{
                var rec = dashAllRecords[i];
                var sid = rec["Site ID"] || '-';
                var cmd = (rec["รหัสสั่งการ"] && rec["รหัสสั่งการ"] !== 'ไม่มีค่า') ? rec["รหัสสั่งการ"] : '-';
                var loc = (rec["สถานที่"] && rec["สถานที่"] !== 'ไม่มีค่า') ? rec["สถานที่"] : '-';
                var pea = (rec["การไฟฟ้า"] && rec["การไฟฟ้า"] !== 'ไม่มีค่า') ? rec["การไฟฟ้า"] : '-';
                var eq = (rec["ชนิดอุปกรณ์"] && rec["ชนิดอุปกรณ์"] !== 'ไม่มีค่า') ? rec["ชนิดอุปกรณ์"] : '-';
                var prod = (rec["ประเภทผลิตภัณฑ์"] && rec["ประเภทผลิตภัณฑ์"] !== 'ไม่มีค่า') ? rec["ประเภทผลิตภัณฑ์"] : '';
                var comm = (rec["อุปกรณ์สื่อสาร (Commu.)"] && rec["อุปกรณ์สื่อสาร (Commu.)"] !== 'ไม่มีค่า') ? rec["อุปกรณ์สื่อสาร (Commu.)"] : '';
                var ip = (rec["IP"] && rec["IP"] !== 'ไม่มีค่า') ? rec["IP"] : '';
                var dnp = (rec["DNP"] && rec["DNP"] !== 'ไม่มีค่า') ? rec["DNP"] : '';

                var sc = rawQ
                    ? window.scoreFieldsAgainstSmartQuery([sid, cmd, loc, pea, ip, dnp, eq, prod, comm], rawQ)
                    : 100;
                if (sc > 0) {{
                    var itemObj = {{
                        idx: i,
                        score: sc,
                        sid: sid,
                        cmd: cmd,
                        loc: loc,
                        pea: pea,
                        eq: eq,
                        ip: ip,
                        dnp: dnp
                    }};
                    if (sc >= 50) directList.push(itemObj);
                    else fuzzyList.push(itemObj);
                }}
            }}

            var scoredList = directList.length > 0 ? directList : fuzzyList;

            // เรียงลำดับผลลัพธ์ที่ตรงเงื่อนไขจากน้อยไปมาก (Ascending Order: A-Z, ก-ฮ, รหัสอุปกรณ์)
            scoredList.sort(function(a, b) {{
                var c1 = window.compareAscendingNatural(a.sid, b.sid);
                if (c1 !== 0) return c1;
                var c2 = window.compareAscendingNatural(a.cmd, b.cmd);
                if (c2 !== 0) return c2;
                return window.compareAscendingNatural(a.loc, b.loc);
            }});

            if (cntBadge) {{
                cntBadge.textContent = scoredList.length.toLocaleString() + ' อุปกรณ์';
            }}

            sel.innerHTML = '';
            var placeholderOpt = document.createElement('option');
            placeholderOpt.value = '';
            placeholderOpt.textContent = '-- กรุณาเลือกอุปกรณ์ที่ต้องการแจ้งข้อมูลไม่ถูกต้อง --';
            sel.appendChild(placeholderOpt);
            scoredList.slice(0, 300).forEach(function(item) {{
                var opt = document.createElement('option');
                opt.value = String(item.idx);
                opt.textContent = item.sid + ' | ' + item.cmd + ' | ' + item.loc + ' (' + item.pea + ')';
                sel.appendChild(opt);
            }});
            if (prevVal) sel.value = prevVal;

            if (!dd) return;
            dd.innerHTML = '';
            if (scoredList.length === 0) {{
                dd.innerHTML = '<div style="padding:14px;text-align:center;font-size:12px;color:var(--text-secondary);">ไม่พบอุปกรณ์ที่ตรงกับคำค้นหา "' + escapeHtmlSafe(rawQ) + '"</div>';
                return;
            }}

            var maxDropdownItems = Math.min(scoredList.length, 50);
            for (var k = 0; k < maxDropdownItems; k++) {{
                (function(item) {{
                    var btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'ur-smart-option' + (Number(prevVal) === item.idx ? ' is-selected' : '');

                    var hlSid = rawQ ? window.highlightSearchText(item.sid, rawQ) : escapeHtmlSafe(item.sid);
                    var hlCmd = rawQ ? window.highlightSearchText(item.cmd, rawQ) : escapeHtmlSafe(item.cmd);
                    var hlLoc = rawQ ? window.highlightSearchText(item.loc, rawQ) : escapeHtmlSafe(item.loc);
                    var hlPea = rawQ ? window.highlightSearchText(item.pea, rawQ) : escapeHtmlSafe(item.pea);
                    var hlEq = rawQ ? window.highlightSearchText(item.eq, rawQ) : escapeHtmlSafe(item.eq);
                    var extraMeta = [];
                    if (item.ip) extraMeta.push('IP: ' + (rawQ ? window.highlightSearchText(item.ip, rawQ) : escapeHtmlSafe(item.ip)));
                    if (item.dnp) extraMeta.push('DNP: ' + (rawQ ? window.highlightSearchText(item.dnp, rawQ) : escapeHtmlSafe(item.dnp)));

                    btn.innerHTML =
                        '<div class="ur-smart-opt-top">' +
                            '<span class="ur-smart-opt-id">' + hlSid + '</span>' +
                            '<span class="ur-smart-opt-cmd">' + hlCmd + '</span>' +
                        '</div>' +
                        '<div class="ur-smart-opt-sub">' +
                            '<span>📍 ' + hlLoc + '</span>' +
                            '<span>• ' + hlPea + '</span>' +
                            '<span>• ' + hlEq + '</span>' +
                            (extraMeta.length ? ('<span style="font-family:monospace;">• ' + extraMeta.join(' / ') + '</span>') : '') +
                        '</div>';

                    btn.onclick = function(ev) {{
                        ev.preventDefault();
                        ev.stopPropagation();
                        sel.value = String(item.idx);
                        dd.classList.remove('show');
                        window.onUserReportDeviceSelected(String(item.idx));
                    }};
                    dd.appendChild(btn);
                }})(scoredList[k]);
            }}
        }};

        document.addEventListener('click', function(e) {{
            var scWrap = document.getElementById('urSmartSearchContainer');
            var dd = document.getElementById('urSmartSearchDropdown');
            if (dd && dd.classList.contains('show') && scWrap && !scWrap.contains(e.target)) {{
                dd.classList.remove('show');
            }}
        }});

        function syncUrCombinedCorrectedInput() {{
            var checkedCbs = Array.from(document.querySelectorAll('.ur-field-cb:checked'));
            var badge = document.getElementById('urCheckedFieldsBadge');
            if (badge) {{
                badge.textContent = 'เลือกแล้ว ' + checkedCbs.length + ' ฟิลด์';
            }}
            var parts = [];
            checkedCbs.forEach(function(cb) {{
                var fName = cb.value;
                var inlineVal = String(urPerFieldCorrections[fName] || '').trim();
                if (inlineVal) {{
                    parts.push(fName + ' แก้ไขเป็น: ' + inlineVal);
                }}
            }});
            var mainInput = document.getElementById('urCorrectedValueInput');
            if (mainInput && parts.length > 0) {{
                mainInput.value = parts.join(' | ');
            }}
        }}

        window.onUserReportDeviceSelected = function(idxStr) {{
            var searchContainer = document.getElementById('urSmartSearchContainer');
            var selectedCard = document.getElementById('urSelectedDeviceCard');
            var infoBox = document.getElementById('urDeviceFullDataBox');
            var infoGrid = document.getElementById('urDeviceInfoGrid');
            var checklistEl = document.getElementById('urFieldsChecklist');
            var headerEl = document.getElementById('urSelectedDeviceHeader');
            var bannerEl = document.getElementById('urStatusBanner');
            var dd = document.getElementById('urSmartSearchDropdown');
            if (dd) dd.classList.remove('show');
            if (bannerEl) bannerEl.className = 'ur-status-banner';

            urPerFieldCorrections = Object.create(null);

            if (idxStr === '' || idxStr === null || idxStr === undefined) {{
                urCurrentSelectedIdx = -1;
                if (searchContainer) searchContainer.style.display = 'block';
                if (selectedCard) selectedCard.classList.remove('show');
                if (infoBox) infoBox.style.display = 'none';
                if (checklistEl) {{
                    checklistEl.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:18px;color:var(--text-secondary);font-size:12.5px;">กรุณาค้นหาและเลือกอุปกรณ์ในขั้นตอนที่ 1 ก่อน เพื่อแสดงรายการฟิลด์ทั้งหมด</div>';
                }}
                var badge = document.getElementById('urCheckedFieldsBadge');
                if (badge) badge.textContent = 'เลือกแล้ว 0 ฟิลด์';
                return;
            }}

            var idx = Number(idxStr);
            urCurrentSelectedIdx = idx;
            var rec = dashAllRecords[idx] || {{}};

            // ซ่อนช่องค้นหาและแสดงการ์ดสรุปข้อมูลอุปกรณ์เมื่อเลือกแล้ว
            if (searchContainer) searchContainer.style.display = 'none';
            if (selectedCard) selectedCard.classList.add('show');

            var sidVal = rec["Site ID"] || '-';
            var cmdVal = (rec["รหัสสั่งการ"] && rec["รหัสสั่งการ"] !== 'ไม่มีค่า') ? rec["รหัสสั่งการ"] : '-';
            var locVal = (rec["สถานที่"] && rec["สถานที่"] !== 'ไม่มีค่า') ? rec["สถานที่"] : '-';
            var peaVal = (rec["การไฟฟ้า"] && rec["การไฟฟ้า"] !== 'ไม่มีค่า') ? rec["การไฟฟ้า"] : '-';
            var eqVal = (rec["ชนิดอุปกรณ์"] && rec["ชนิดอุปกรณ์"] !== 'ไม่มีค่า') ? rec["ชนิดอุปกรณ์"] : '-';
            var prodVal = (rec["ประเภทผลิตภัณฑ์"] && rec["ประเภทผลิตภัณฑ์"] !== 'ไม่มีค่า') ? rec["ประเภทผลิตภัณฑ์"] : '-';
            var commVal = (rec["อุปกรณ์สื่อสาร (Commu.)"] && rec["อุปกรณ์สื่อสาร (Commu.)"] !== 'ไม่มีค่า') ? rec["อุปกรณ์สื่อสาร (Commu.)"] : '-';
            var itemVal = (rec["ประเภทย่อยสื่อสาร (Item)"] && rec["ประเภทย่อยสื่อสาร (Item)"] !== 'ไม่มีค่า') ? rec["ประเภทย่อยสื่อสาร (Item)"] : '-';
            var ipVal = (rec["IP"] && rec["IP"] !== 'ไม่มีค่า') ? rec["IP"] : '-';
            var dnpVal = (rec["DNP"] && rec["DNP"] !== 'ไม่มีค่า') ? rec["DNP"] : '-';
            var stVal = (rec["State SCADA"] && rec["State SCADA"] !== 'ไม่มีค่า') ? rec["State SCADA"] : (rec["_layerName"] || '-');

            var cardSiteEl = document.getElementById('urCardSiteId');
            var cardCmdEl = document.getElementById('urCardCmdBadge');
            var cardStateEl = document.getElementById('urCardStateBadge');
            var cardLocEl = document.getElementById('urCardLocation');
            var cardPillsEl = document.getElementById('urCardMetaPills');

            if (cardSiteEl) cardSiteEl.textContent = sidVal;
            if (cardCmdEl) cardCmdEl.textContent = 'รหัสสั่งการ: ' + cmdVal;
            if (cardStateEl) {{
                cardStateEl.textContent = stVal;
                var pSt = classifyRawParentState(stVal);
                var stColorMap = {{ 'Online': '#16a34a', 'Connecting': '#ea580c', 'Offline': '#dc2626', 'Initializing': '#65a30d', 'Telemetry Failure': '#d97706' }};
                cardStateEl.style.background = stColorMap[pSt] || '#64748b';
            }}
            if (cardLocEl) cardLocEl.textContent = 'สถานที่: ' + locVal + ' (' + peaVal + ')';
            if (cardPillsEl) {{
                cardPillsEl.innerHTML =
                    '<span class="ur-sdc-pill">ชนิดอุปกรณ์: <strong>' + escapeHtmlSafe(eqVal) + '</strong></span>' +
                    '<span class="ur-sdc-pill">ผลิตภัณฑ์: <strong>' + escapeHtmlSafe(prodVal) + '</strong></span>' +
                    '<span class="ur-sdc-pill">สื่อสาร: <strong>' + escapeHtmlSafe(commVal + ' • ' + itemVal) + '</strong></span>' +
                    '<span class="ur-sdc-pill">IP / DNP: <strong style="font-family:monospace;">' + escapeHtmlSafe(ipVal + ' / ' + dnpVal) + '</strong></span>';
            }}

            if (headerEl) {{
                headerEl.textContent = 'ข้อมูลปัจจุบันของอุปกรณ์ครบทุกฟิลด์: ' + sidVal + ' (' + cmdVal + ')';
            }}

            if (infoGrid) {{
                infoGrid.innerHTML = '';
                USER_REPORT_FIELD_LIST.forEach(function(fName) {{
                    var curVal = (rec[fName] && rec[fName] !== 'ไม่มีค่า') ? rec[fName] : '-';
                    var cell = document.createElement('div');
                    cell.className = 'ur-info-cell';
                    cell.innerHTML = '<span>' + escapeHtmlSafe(fName) + '</span><strong>' + escapeHtmlSafe(curVal) + '</strong>';
                    infoGrid.appendChild(cell);
                }});
            }}

            if (checklistEl) {{
                checklistEl.innerHTML = '';
                USER_REPORT_FIELD_LIST.forEach(function(fName) {{
                    var curVal = (rec[fName] && rec[fName] !== 'ไม่มีค่า') ? rec[fName] : '-';
                    var card = document.createElement('div');
                    card.className = 'ur-field-check-card';

                    var topLabel = document.createElement('label');
                    topLabel.className = 'ur-field-check-top';

                    var cb = document.createElement('input');
                    cb.type = 'checkbox';
                    cb.className = 'ur-field-cb';
                    cb.value = fName;

                    var txtWrap = document.createElement('div');
                    txtWrap.style.flex = '1';
                    txtWrap.style.minWidth = '0';
                    txtWrap.innerHTML =
                        '<div class="ur-field-name">' + escapeHtmlSafe(fName) + '</div>' +
                        '<div class="ur-field-curval">ค่าปัจจุบัน: <strong>' + escapeHtmlSafe(curVal) + '</strong></div>';

                    var collapseWrap = document.createElement('div');
                    collapseWrap.className = 'ur-field-input-collapse';

                    var inlineInp = document.createElement('input');
                    inlineInp.type = 'text';
                    inlineInp.className = 'ur-field-inline-input';
                    inlineInp.placeholder = 'กรอกค่าที่ถูกต้องของ "' + fName + '" ที่นี่...';

                    cb.addEventListener('change', function() {{
                        card.classList.toggle('is-checked', cb.checked);
                        syncUrCombinedCorrectedInput();
                        if (cb.checked) {{
                            setTimeout(function() {{ inlineInp.focus(); }}, 60);
                        }}
                    }});

                    inlineInp.addEventListener('input', function() {{
                        urPerFieldCorrections[fName] = inlineInp.value;
                        syncUrCombinedCorrectedInput();
                    }});

                    collapseWrap.appendChild(inlineInp);
                    topLabel.appendChild(cb);
                    topLabel.appendChild(txtWrap);
                    card.appendChild(topLabel);
                    card.appendChild(collapseWrap);
                    checklistEl.appendChild(card);
                }});
            }}
            syncUrCombinedCorrectedInput();
        }};

        window.resetUserReportForm = function() {{
            showUrFormView();
            urCurrentSelectedIdx = -1;
            urPerFieldCorrections = Object.create(null);
            var sInp = document.getElementById('urDeviceSearchInput');
            if (sInp) sInp.value = '';
            var sel = document.getElementById('urDeviceSelect');
            if (sel) sel.value = '';
            var corrInp = document.getElementById('urCorrectedValueInput');
            if (corrInp) corrInp.value = '';
            var noteInp = document.getElementById('urReporterNoteInput');
            if (noteInp) noteInp.value = '';
            var bannerEl = document.getElementById('urStatusBanner');
            if (bannerEl) bannerEl.className = 'ur-status-banner';
            var fullBox = document.getElementById('urDeviceFullDataBox');
            var toggleBtn = document.getElementById('urToggleFullFieldsBtn');
            if (fullBox) fullBox.style.display = 'none';
            if (toggleBtn) toggleBtn.textContent = 'ดูข้อมูลครบทุกฟิลด์ ▾';
            window.filterUserReportDeviceOptions();
            window.onUserReportDeviceSelected('');
        }};

        function setUrBanner(type, htmlMsg) {{
            var bannerEl = document.getElementById('urStatusBanner');
            if (!bannerEl) return;
            bannerEl.className = 'ur-status-banner show is-' + type;
            bannerEl.innerHTML = htmlMsg;
        }}

        window.submitUserReportToGoogleSheet = async function() {{
            if (urCurrentSelectedIdx < 0 || !dashAllRecords[urCurrentSelectedIdx]) {{
                setUrBanner('error', '⚠️ กรุณาเลือกอุปกรณ์ที่ต้องการแจ้งข้อมูลไม่ถูกต้องในขั้นตอนที่ 1 ก่อนครับ');
                return;
            }}
            var checkedCbs = Array.from(document.querySelectorAll('.ur-field-cb:checked'));
            if (checkedCbs.length === 0) {{
                setUrBanner('error', '⚠️ กรุณาติ๊กเลือกฟิลด์ที่ไม่ถูกต้องอย่างน้อย 1 ฟิลด์ในขั้นตอนที่ 2 ครับ');
                return;
            }}
            syncUrCombinedCorrectedInput();
            var corrInp = document.getElementById('urCorrectedValueInput');
            var correctedValueText = String((corrInp && corrInp.value) || '').trim();
            if (!correctedValueText) {{
                setUrBanner('error', '⚠️ กรุณากรอก "ค่าที่ถูกต้อง" ในการ์ดฟิลด์ที่เลือก หรือในขั้นตอนที่ 3 ก่อนกดส่งข้อมูลครับ');
                var firstInline = document.querySelector('.ur-field-check-card.is-checked .ur-field-inline-input');
                if (firstInline) firstInline.focus();
                else if (corrInp) corrInp.focus();
                return;
            }}

            var rec = dashAllRecords[urCurrentSelectedIdx];
            var incorrectFields = checkedCbs.map(function(cb) {{ return cb.value; }});
            var currentValuesObj = {{}};
            var perFieldDetailParts = [];
            incorrectFields.forEach(function(fName) {{
                var cVal = (rec[fName] && rec[fName] !== 'ไม่มีค่า') ? rec[fName] : '-';
                currentValuesObj[fName] = cVal;
                var newVal = String(urPerFieldCorrections[fName] || '').trim();
                if (newVal) {{
                    perFieldDetailParts.push(fName + ': [' + cVal + '] -> [' + newVal + ']');
                }} else {{
                    perFieldDetailParts.push(fName + ': [' + cVal + ']');
                }}
            }});

            var snapshotObj = {{}};
            USER_REPORT_FIELD_LIST.forEach(function(fName) {{
                snapshotObj[fName] = (rec[fName] && rec[fName] !== 'ไม่มีค่า') ? rec[fName] : '-';
            }});

            var noteInp = document.getElementById('urReporterNoteInput');
            var reporterNote = String((noteInp && noteInp.value) || '').trim();

            var payload = {{
                timestamp: new Date().toISOString(),
                webhook_url: WEBHOOK_URL,
                site_id: rec["Site ID"] || '-',
                cmd: (rec["รหัสสั่งการ"] && rec["รหัสสั่งการ"] !== 'ไม่มีค่า') ? rec["รหัสสั่งการ"] : '-',
                location: (rec["สถานที่"] && rec["สถานที่"] !== 'ไม่มีค่า') ? rec["สถานที่"] : '-',
                pea: (rec["การไฟฟ้า"] && rec["การไฟฟ้า"] !== 'ไม่มีค่า') ? rec["การไฟฟ้า"] : '-',
                device_type: (rec["ชนิดอุปกรณ์"] && rec["ชนิดอุปกรณ์"] !== 'ไม่มีค่า') ? rec["ชนิดอุปกรณ์"] : '-',
                incorrect_fields: incorrectFields,
                current_values: currentValuesObj,
                corrected_value: correctedValueText,
                per_field_detail: perFieldDetailParts.join(' ; '),
                reporter_note: reporterNote,
                device_snapshot: snapshotObj
            }};

            try {{
                var localQueue = JSON.parse(localStorage.getItem('scada_user_reports_history') || '[]');
                localQueue.unshift(payload);
                localStorage.setItem('scada_user_reports_history', JSON.stringify(localQueue.slice(0, 100)));
            }} catch (e) {{}}

            var submitBtn = document.getElementById('urSubmitBtn');
            if (submitBtn) {{
                submitBtn.disabled = true;
                submitBtn.innerHTML = '⏳ กำลังส่งข้อมูลแจ้งแก้ไข...';
            }}

            var sentOk = false;
            var errDetail = '';

            if (WEBHOOK_URL) {{
                try {{
                    var resp = await fetch(WEBHOOK_URL, {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'text/plain;charset=utf-8' }},
                        body: JSON.stringify(payload)
                    }});
                    if (resp.ok || resp.type === 'opaque') {{
                        sentOk = true;
                    }} else {{
                        errDetail = 'HTTP ' + resp.status;
                    }}
                }} catch (fetchErr) {{
                    try {{
                        await fetch(WEBHOOK_URL, {{
                            method: 'POST',
                            mode: 'no-cors',
                            headers: {{ 'Content-Type': 'text/plain;charset=utf-8' }},
                            body: JSON.stringify(payload)
                        }});
                        sentOk = true;
                    }} catch (noCorsErr) {{
                        errDetail = String(noCorsErr.message || noCorsErr);
                    }}
                }}
            }}

            if (!sentOk && window.location.protocol !== 'file:') {{
                try {{
                    var bkResp = await fetch('/api/user_report', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(payload)
                    }});
                    if (bkResp.ok) sentOk = true;
                }} catch (bkErr) {{}}
            }}

            if (submitBtn) {{
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="white" stroke-width="2"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg> ส่งข้อมูลแจ้งแก้ไข';
            }}

            if (sentOk) {{
                showUrSuccessStateView(payload.site_id, payload.cmd, incorrectFields);
            }} else {{
                setUrBanner(
                    'error',
                    '❌ ไม่สามารถส่งข้อมูลได้ในขณะนี้ (' + escapeHtmlSafe(errDetail || 'Network Error') + ') กรุณาตรวจสอบการเชื่อมต่ออินเทอร์เน็ตแล้วลองใหม่อีกครั้ง'
                );
            }}
        }};

        // ระบบเปิดหน้าต่างตั้งค่า Export ข้อมูลของหน้า Dashboard (เลือกตารางและคอลัมน์ได้ตามต้องการ)
        var DASH_EXPORT_COL_DEFS = [
            {{ key: 'seq', label: 'ลำดับ', width: 8 }},
            {{ key: 'site_id', label: 'Site ID', width: 16 }},
            {{ key: 'cmd', label: 'รหัสสั่งการ', width: 16 }},
            {{ key: 'loc', label: 'สถานที่', width: 24 }},
            {{ key: 'pea', label: 'การไฟฟ้า', width: 20 }},
            {{ key: 'norm_eq', label: 'ชนิดอุปกรณ์', width: 22 }},
            {{ key: 'prod', label: 'ประเภทผลิตภัณฑ์', width: 18 }},
            {{ key: 'comm_type', label: 'Commu.', width: 14 }},
            {{ key: 'comm_item', label: 'Item', width: 14 }},
            {{ key: 'comm_ref', label: 'WRL Master', width: 16 }},
            {{ key: 'ip', label: 'IP', width: 16 }},
            {{ key: 'dnp', label: 'DNP', width: 10 }},
            {{ key: 'state_full', label: 'State SCADA', width: 16 }},
            {{ key: 'after_full', label: 'สถานะหลังตรวจสอบ', width: 26 }}
        ];

        window.openDashExportModal = function() {{
            var modal = document.getElementById('dashExportModalOverlay');
            if (!modal) return;
            window.updateDashExportPreview();
            modal.classList.add('show');
        }};

        window.closeDashExportModal = function() {{
            var modal = document.getElementById('dashExportModalOverlay');
            if (modal) modal.classList.remove('show');
        }};

        window.setAllDashExportSheets = function(checked) {{
            document.querySelectorAll('.dash-exp-sheet-cb').forEach(function(cb) {{
                cb.checked = Boolean(checked);
            }});
            window.updateDashExportPreview();
        }};

        window.setAllDashExportCols = function(checked) {{
            document.querySelectorAll('.dash-exp-col-cb').forEach(function(cb) {{
                cb.checked = Boolean(checked);
            }});
            window.updateDashExportPreview();
        }};

        window.setDefaultDashExportCols = function() {{
            var mainKeys = ['seq', 'site_id', 'cmd', 'loc', 'pea', 'norm_eq', 'state_full'];
            document.querySelectorAll('.dash-exp-col-cb').forEach(function(cb) {{
                cb.checked = mainKeys.indexOf(cb.value) >= 0;
            }});
            window.updateDashExportPreview();
        }};

        window.updateDashExportPreview = function() {{
            var selectedSheets = Array.from(document.querySelectorAll('.dash-exp-sheet-cb:checked')).map(function(cb) {{ return cb.value; }});
            var selectedCols = Array.from(document.querySelectorAll('.dash-exp-col-cb:checked')).map(function(cb) {{ return cb.value; }});
            var devSection = document.getElementById('dashExportDeviceOptionsSection');
            var hasDevices = selectedSheets.indexOf('devices') >= 0;
            if (devSection) {{
                devSection.style.opacity = hasDevices ? '1' : '0.5';
            }}

            var scopeSel = document.getElementById('dashExportRowScope');
            var scopeMode = scopeSel ? scopeSel.value : 'filtered';
            var targetDeviceRows = (scopeMode === 'filtered' && window.cachedExplorerMatchedRows) ? window.cachedExplorerMatchedRows : cachedDashboardRows;
            var rowCount = targetDeviceRows ? targetDeviceRows.length : 0;

            var subEl = document.getElementById('dashExportSummarySub');
            var badgeEl = document.getElementById('dashExportPreviewBadge');
            var scopeBadge = document.getElementById('dashExportScopeBadge');
            if (scopeBadge) {{
                scopeBadge.textContent = 'อุปกรณ์ ' + rowCount.toLocaleString() + ' ชุด';
            }}
            if (subEl) {{
                subEl.textContent = 'เลือก ' + selectedSheets.length + ' ตาราง' + (hasDevices ? (' · ตารางอุปกรณ์ ' + rowCount.toLocaleString() + ' ชุด (' + selectedCols.length + ' คอลัมน์)') : '');
            }}
            if (badgeEl) {{
                badgeEl.textContent = selectedSheets.length + ' Sheets';
                badgeEl.classList.toggle('is-invalid', selectedSheets.length === 0 || (hasDevices && selectedCols.length === 0));
            }}
        }};

        // ส่งออกไฟล์ Excel ของหน้า Dashboard ตามเนื้อหาและรายละเอียดที่ผู้ใช้เลือก
        window.exportDashboardExcel = async function() {{
            var selectedSheets = Array.from(document.querySelectorAll('.dash-exp-sheet-cb:checked')).map(function(cb) {{ return cb.value; }});
            var selectedColKeys = Array.from(document.querySelectorAll('.dash-exp-col-cb:checked')).map(function(cb) {{ return cb.value; }});

            // กรณีเรียกใช้งานโดยตรงโดยยังไม่ได้เปิด Modal ให้ส่งออกครบทุกตารางเป็นค่าเริ่มต้น
            if (selectedSheets.length === 0 && !document.getElementById('dashExportModalOverlay').classList.contains('show')) {{
                selectedSheets = ['eq', 'resp', 'prod', 'comm', 'pea', 'devices'];
            }}
            if (selectedSheets.length === 0) {{
                showCustomAlert('กรุณาเลือกตารางข้อมูลที่ต้องการส่งออกอย่างน้อย 1 ตาราง');
                return;
            }}
            if (selectedSheets.indexOf('devices') >= 0 && selectedColKeys.length === 0) {{
                showCustomAlert('กรุณาเลือกคอลัมน์ในตารางรายการอุปกรณ์อย่างน้อย 1 คอลัมน์');
                return;
            }}

            var pageFmtEl = document.getElementById('dashExportPageFormat');
            var isA4Format = !pageFmtEl || pageFmtEl.value === 'a4_landscape';
            var scopeSel = document.getElementById('dashExportRowScope');
            var scopeMode = scopeSel ? scopeSel.value : 'filtered';

            var workbook = new ExcelJS.Workbook();
            var sheetPageSetup = isA4Format ? {{
                paperSize: 9, // A4 Standard
                orientation: 'landscape',
                fitToPage: true,
                fitToWidth: 1,
                fitToHeight: 0,
                horizontalCentered: true,
                printTitlesRow: '1:2',
                margins: {{ left: 0.25, right: 0.25, top: 0.4, bottom: 0.4, header: 0.2, footer: 0.2 }}
            }} : {{
                orientation: 'landscape',
                printTitlesRow: '1:2'
            }};

            function addFormattedSheet(sheetName, titleText, headers, dataRows, colWidths) {{
                var ws = workbook.addWorksheet(sheetName, {{ pageSetup: sheetPageSetup }});
                ws.mergeCells(1, 1, 1, Math.max(1, headers.length));
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

            if (selectedSheets.indexOf('eq') >= 0) {{
                addFormattedSheet(
                    '1.แยกตามชนิดอุปกรณ์',
                    'รายงานความพร้อมใช้งานอุปกรณ์ในระบบ SCADA (%AVA) - แยกตามชนิดอุปกรณ์',
                    ['ชนิดอุปกรณ์', 'ทั้งหมด', 'Online', 'ไม่ออนไลน์รวม', 'Telemetry Failure', 'Offline', 'Initializing', 'Connecting', '%AVA'],
                    cachedSummaryExports.eq,
                    [30, 14, 14, 16, 16, 14, 14, 14, 16]
                );
            }}

            if (selectedSheets.indexOf('resp') >= 0) {{
                addFormattedSheet(
                    '2.สถานะติดตามหน้างาน',
                    'สถานะการติดตามหน้างานของอุปกรณ์ที่ไม่ออนไลน์',
                    ['สถานะการติดตาม / ผู้รับผิดชอบ', 'จำนวน (ชุด)', 'สัดส่วนของที่ไม่ออนไลน์'],
                    cachedSummaryExports.resp,
                    [46, 18, 22]
                );
            }}

            if (selectedSheets.indexOf('prod') >= 0) {{
                addFormattedSheet(
                    '3.แยกตามผลิตภัณฑ์ FRTU',
                    'รายงานความพร้อมใช้งานอุปกรณ์ในระบบ SCADA (%AVA) - แยกตามประเภทผลิตภัณฑ์ของ FRTU',
                    ['ประเภทผลิตภัณฑ์ (FRTU Model)', 'ทั้งหมด', 'Online', 'ไม่ออนไลน์รวม', 'สถานะย่อย', '%AVA'],
                    cachedSummaryExports.prod,
                    [28, 14, 14, 16, 38, 16]
                );
            }}

            if (selectedSheets.indexOf('comm') >= 0) {{
                addFormattedSheet(
                    '4.แยกตามสื่อสาร-แม่ข่าย',
                    'รายงานความพร้อมใช้งานอุปกรณ์ในระบบ SCADA (%AVA) - แยกตามอุปกรณ์สื่อสารและ WRL Master',
                    ['อุปกรณ์สื่อสาร / WRL Master', 'ทั้งหมด', 'Online', 'ไม่ออนไลน์รวม', '%AVA'],
                    cachedSummaryExports.comm,
                    [38, 16, 16, 18, 18]
                );
            }}

            if (selectedSheets.indexOf('pea') >= 0) {{
                addFormattedSheet(
                    '5.แยกตามการไฟฟ้า',
                    'รายงานความพร้อมใช้งานอุปกรณ์ในระบบ SCADA (%AVA) - แยกตามสังกัดการไฟฟ้า (กฟจ. / กฟส.)',
                    ['การไฟฟ้า', 'ทั้งหมด', 'Online', 'ไม่ออนไลน์รวม', 'Telemetry Failure', 'Offline', 'Initializing', 'Connecting', '%AVA'],
                    cachedSummaryExports.pea,
                    [30, 14, 14, 16, 16, 14, 14, 14, 16]
                );
            }}

            if (selectedSheets.indexOf('devices') >= 0) {{
                var activeColDefs = DASH_EXPORT_COL_DEFS.filter(function(def) {{
                    return selectedColKeys.indexOf(def.key) >= 0;
                }});
                var sourceRows = (scopeMode === 'filtered' && window.cachedExplorerMatchedRows) ? window.cachedExplorerMatchedRows : cachedDashboardRows;
                var deviceHeaders = activeColDefs.map(function(def) {{ return def.label; }});
                var deviceWidths = activeColDefs.map(function(def) {{ return def.width; }});
                var deviceRows = (sourceRows || []).map(function(r, i) {{
                    return activeColDefs.map(function(def) {{
                        if (def.key === 'seq') return i + 1;
                        return r[def.key] || '-';
                    }});
                }});
                addFormattedSheet(
                    '6.รายการอุปกรณ์',
                    'ตารางข้อมูลอุปกรณ์และการสื่อสารในระบบ SCADA (' + deviceRows.length.toLocaleString() + ' ชุด)',
                    deviceHeaders,
                    deviceRows,
                    deviceWidths
                );
            }}

            var buffer = await workbook.xlsx.writeBuffer();
            var blob = new Blob([buffer], {{ type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }});
            var link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = 'SCADA_Dashboard_Export.xlsx';
            document.body.appendChild(link); link.click(); document.body.removeChild(link);
            window.closeDashExportModal();
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
            if (window.DEFAULT_OPEN_AI_MODAL === true || urlParams.get('ai') === '1' || urlParams.get('open_ai') === '1' || window.location.pathname.toLowerCase().includes('ai-report')) {{
                setTimeout(function() {{
                    if (typeof window.openReportModal === 'function') window.openReportModal(true);
                }}, 350);
            }}
        }})();

        // ============================================================================
        // === [FEATURE 4] ระบบ Seamless Map & Dashboard Update ด้วย Fetch API (/api/map_data) ===
        // ดึงข้อมูล JSON ล่าสุดจาก Memory Cache ของ Backend มาอัปเดตทั้ง Dashboard และหมุดบนแผนที่โดยไม่รีเฟรชหน้าเว็บ
        window.currentScadaDataVersion = {initial_version};
        window.serverOverallSummary = {overall_summary_json};

        window.updateLeafletMapMarkersSeamless = function(payload) {{
            try {{
                // อัปเดตสีและสถานะของหมุด (DOM Pin / Cluster) และข้อมูล Popup ในหน่วยความจำโดยไม่เสียตำแหน่งซูมหรือหน้าต่างที่เปิดอยู่
                if (!payload || !Array.isArray(payload.search_data)) return;
                payload.search_data.forEach(function(item) {{
                    var pinEls = document.querySelectorAll('.pin-site-' + item.safe_id);
                    pinEls.forEach(function(pinEl) {{
                        var shape = pinEl.querySelector('.pin-shape');
                        if (shape && item.color) shape.style.backgroundColor = item.color;
                        var iconEl = pinEl.querySelector('.map-pin-icon');
                        if (iconEl && item.icon) iconEl.className = 'fa fa-' + item.icon + ' map-pin-icon';
                    }});
                }});
                // หากเปิดหน้าต่างรายละเอียดของสถานีใดค้างไว้ ให้อัปเดตเนื้อหาให้เป็นข้อมูลล่าสุดทันที
                var infoPanel = document.getElementById('customInfoPanel');
                if (infoPanel && infoPanel.style.display === 'flex' && window.currentSelectedSafeId) {{
                    var newPopupHtml = buildStationPopupHtml(window.currentSelectedSafeId);
                    if (newPopupHtml) {{
                        document.getElementById('customInfoContent').innerHTML = newPopupHtml;
                    }}
                }}
            }} catch (err) {{
                console.warn('Seamless map marker update error:', err);
            }}
        }};

        window.applySeamlessMapData = function(payload) {{
            if (!payload || !payload.dash_all_data) return;
            window.currentScadaDataVersion = payload.version || window.currentScadaDataVersion;
            if (payload.overall_summary) {{
                window.serverOverallSummary = payload.overall_summary;
            }}
            if (payload.status_hash_map) {{
                statusHashMap = payload.status_hash_map;
            }}
            if (Array.isArray(payload.export_data)) {{
                expDataCompact = payload.export_data;
                expData = expDataCompact.map(function(arr) {{
                    var obj = {{ "_layerName": arr[0] || "", "Site ID": arr[1] || "" }};
                    for (var i = 0; i < DISPLAY_FIELD_NAMES.length; i++) {{
                        var v = arr[i + 2];
                        obj[DISPLAY_FIELD_NAMES[i]] = (v !== undefined && v !== "") ? v : "ไม่มีค่า";
                    }}
                    return obj;
                }});
            }}
            if (Array.isArray(payload.search_data)) {{
                sData = payload.search_data;
                stationBySafeId = Object.create(null);
                sData.forEach(function(item) {{
                    stationBySafeId[item.safe_id] = item;
                }});
            }}
            if (Array.isArray(payload.report_data)) {{
                reportRawData = payload.report_data;
            }}
            if (Array.isArray(payload.dash_all_data)) {{
                dashAllCompact = payload.dash_all_data;
                dashAllRecords = dashAllCompact.map(function(arr) {{
                    var obj = {{ "_layerName": arr[0] || "", "Site ID": arr[1] || "", "_safeId": arr[2] || "" }};
                    for (var i = 0; i < DISPLAY_FIELD_NAMES.length; i++) {{
                        var v = arr[i + 3];
                        obj[DISPLAY_FIELD_NAMES[i]] = (v !== undefined && v !== "") ? v : "ไม่มีค่า";
                    }}
                    return obj;
                }});
            }}
            window.renderScadaDashboard();
            window.updateLeafletMapMarkersSeamless(payload);
            if (typeof updateReportPreview === 'function') {{
                updateReportPreview();
            }}
        }};

        window.fetchSeamlessMapData = function(forceUpdate) {{
            // เรียก API /api/map_data เพื่อดึงข้อมูลล่าสุดจาก In-memory Cache ของเซิร์ฟเวอร์แบบไม่รีเฟรชหน้า
            if (window.location.protocol === 'file:') return;
            fetch('/api/map_data?t=' + Date.now(), {{ cache: 'no-store' }})
                .then(function(res) {{
                    if (!res.ok) throw new Error('HTTP ' + res.status);
                    return res.json();
                }})
                .then(function(data) {{
                    if (!data || !Array.isArray(data.dash_all_data)) return;
                    try {{
                        sessionStorage.setItem('scada_raw_cache_v1', JSON.stringify(data));
                    }} catch (e) {{}}
                    var needsInitialHydration = (!sData || sData.length === 0 || !dashAllCompact || dashAllCompact.length === 0);
                    if (forceUpdate || needsInitialHydration || data.version !== window.currentScadaDataVersion) {{
                        window.applySeamlessMapData(data);
                    }}
                }})
                .catch(function(err) {{
                    console.warn('fetchSeamlessMapData warning:', err);
                }});
        }};

        // ============================================================================
        // === [CLIENT-SIDE RENDERING ENGINE] สร้างแผนที่, MarkerCluster และตัวกรองบน Browser ===
        // ============================================================================
        var RAW_PARENT_META_JS = {{
            "Telemetry Failure": ["#ffc107", "Telemetry Failure"],
            "Offline": ["#d33d2a", "Offline"],
            "Online": ["#008000", "Online"],
            "Initializing": ["#82c91e", "Initializing"],
            "Connecting": ["#f3943b", "Connecting"],
            "สถานะอื่นๆ": ["#575757", "สถานะอื่นๆ"]
        }};

        function getClientStatusWeight(sUpper) {{
            var s = String(sUpper || "").toUpperCase();
            if (["ONLINE", "OFFLINE", "INITIALIZING", "CONNECTING", "TELEMETRY FAILURE"].indexOf(s) !== -1) return 1;
            if (s.indexOf("รอ ผบอ. เข้าแก้ไข") !== -1 && s.indexOf("ผอส") === -1) return 2;
            if (s.indexOf("รอ ผบอ. และ ผอส.") !== -1 || s.indexOf("และ ผอส") !== -1) return 3;
            if (s.indexOf("รอ ผอส.") !== -1) return 4;
            if (s.indexOf("ผบอ. เคยแก้ไข") !== -1) return 5;
            if (s.indexOf("ระบบสื่อสาร") !== -1 && s.indexOf("เคยแก้ไข") !== -1) return 6;
            return 10;
        }}

        window.clientMcGroups = Object.create(null);

        window.rebuildLeafletMapClientSide = function() {{
            var map = window.map_scada_client;
            if (!map || !Array.isArray(sData)) return;

            // ล้าง Cluster เดิมออกจากแผนที่ก่อนสร้างใหม่จากข้อมูล JSON
            Object.keys(window.clientMcGroups).forEach(function(stKey) {{
                var oldMc = window.clientMcGroups[stKey];
                if (oldMc && map.hasLayer(oldMc)) {{
                    map.removeLayer(oldMc);
                }}
            }});
            window.clientMcGroups = Object.create(null);

            var statusCounts = Object.create(null);
            var parentCounts = {{
                "Telemetry Failure": 0,
                "Offline": 0,
                "Online": 0,
                "Initializing": 0,
                "Connecting": 0,
                "สถานะอื่นๆ": 0
            }};
            var statusMetaByStatus = Object.create(null);

            sData.forEach(function(item) {{
                var st = item.status || "Unknown";
                var rp = item.raw_parent || classifyRawParentState(st);
                statusCounts[st] = (statusCounts[st] || 0) + 1;
                if (parentCounts[rp] === undefined) parentCounts[rp] = 0;
                parentCounts[rp]++;
                if (!statusMetaByStatus[st]) {{
                    statusMetaByStatus[st] = {{
                        raw_parent: rp,
                        color: item.color || "#575757",
                        icon: item.icon || "info-circle"
                    }};
                }}
            }});

            var markersByStatus = Object.create(null);
            sData.forEach(function(item) {{
                var st = item.status || "Unknown";
                var rp = item.raw_parent || classifyRawParentState(st);
                var safeSt = "s_" + (statusHashMap[st] || "0");
                var safePr = "p_" + (statusHashMap[rp] || "0");
                if (!markersByStatus[st]) markersByStatus[st] = [];

                var pinHtml =
                    '<div class="map-pin-inner ' + safeSt + ' ' + safePr + ' pin-site-' + item.safe_id + '">' +
                        '<div class="pin-shape" style="background-color:' + item.color + ';"></div>' +
                        '<i class="fa fa-' + item.icon + ' map-pin-icon"></i>' +
                    '</div>';
                var iconObj = L.divIcon({{
                    html: pinHtml,
                    iconSize: [30, 42],
                    iconAnchor: [15, 42],
                    popupAnchor: [0, -42],
                    className: "empty"
                }});
                var popupStub = '<div class="custom-popup-card" data-safe-id="' + item.safe_id + '"></div>';
                var tipHtml = '<div>' + escapeHtmlSafe(item.id + ' (' + (item.name || '') + ')') + '</div>';

                var mk = L.marker([item.lat, item.lon], {{ icon: iconObj }})
                    .bindPopup(popupStub, {{ maxWidth: "100%", autoPan: false }})
                    .bindTooltip(tipHtml, {{ sticky: true }});
                markersByStatus[st].push(mk);
            }});

            var statusGroupsByParent = Object.create(null);
            Object.keys(RAW_PARENT_META_JS).forEach(function(pk) {{
                statusGroupsByParent[pk] = [];
            }});

            Object.keys(markersByStatus).forEach(function(st) {{
                var meta = statusMetaByStatus[st];
                var rp = meta.raw_parent;
                var safeSt = "s_" + (statusHashMap[st] || "0");
                var safePr = "p_" + (statusHashMap[rp] || "0");
                var hColor = meta.color;
                var iconName = meta.icon;

                var mc = L.markerClusterGroup({{
                    disableClusteringAtZoom: 17,
                    maxClusterRadius: 50,
                    chunkedLoading: true,
                    chunkInterval: 100,
                    chunkDelay: 15,
                    animate: false,
                    animateAddingMarkers: false,
                    showCoverageOnHover: false,
                    removeOutsideVisibleBounds: true,
                    iconCreateFunction: function(c) {{
                        var count = c.getChildCount();
                        return new L.DivIcon({{
                            html: '<div class="map-cluster-inner ' + safeSt + ' ' + safePr + '" style="background-color:' + hColor + ';"><i class="fa fa-' + iconName + ' map-cluster-icon"></i><span class="map-cluster-count">' + count + '</span></div>',
                            className: 'custom-cluster-marker',
                            iconSize: new L.Point(44, 44),
                            iconAnchor: new L.Point(22, 22)
                        }});
                    }}
                }});
                mc.addLayers(markersByStatus[st]);
                mc.addTo(map);
                window.clientMcGroups[st] = mc;
                if (!statusGroupsByParent[rp]) statusGroupsByParent[rp] = [];
                statusGroupsByParent[rp].push({{
                    status: st,
                    mc: mc,
                    count: statusCounts[st] || 0,
                    color: hColor,
                    icon: iconName
                }});
            }});

            // สร้าง UI ตัวกรองสถานะบนแผนที่ (#customFilterWrapper) บนฝั่ง Client-Side
            var filterForm = document.getElementById("clientLayersForm");
            var overlaysContainer = document.getElementById("clientLayersOverlays");
            if (filterForm && overlaysContainer) {{
                overlaysContainer.innerHTML = "";
                Object.keys(RAW_PARENT_META_JS).forEach(function(parentKey) {{
                    var pCount = parentCounts[parentKey] || 0;
                    var items = statusGroupsByParent[parentKey] || [];
                    if (pCount <= 0 || items.length === 0) return;

                    items.sort(function(a, b) {{
                        var wA = getClientStatusWeight(a.status);
                        var wB = getClientStatusWeight(b.status);
                        if (wA !== wB) return wA - wB;
                        if (a.status.length !== b.status.length) return a.status.length - b.status.length;
                        return a.status.localeCompare(b.status, "th");
                    }});

                    var cHex = RAW_PARENT_META_JS[parentKey][0];
                    var pLabel = RAW_PARENT_META_JS[parentKey][1];

                    var groupDiv = document.createElement("div");
                    groupDiv.className = "leaflet-control-layers-group";

                    var groupLabel = document.createElement("label");
                    groupLabel.className = "leaflet-control-layers-group-label";
                    groupLabel.title = "คลิกเพื่อเลือกหรือยกเลิกสถานะย่อยทั้งหมดในกลุ่มนี้";
                    groupLabel.innerHTML =
                        '<input type="checkbox" class="leaflet-control-layers-group-selector" checked> ' +
                        '<span class="leaflet-control-layers-group-name">' +
                            '<span style="display:flex; justify-content:space-between; align-items:center; width:100%; padding: 10px 16px; border-bottom: 1px solid #444746;">' +
                                '<span style="display:flex; align-items:center;">' +
                                    '<span style="color:' + cHex + '; font-size:16px; margin-right:8px; line-height:1;">●</span>' +
                                    '<span class="parent-text" data-parent="' + escapeHtmlSafe(parentKey) + '" style="font-size:14px; font-weight:600; color:#e3e3e3;">' + escapeHtmlSafe(pLabel) + '</span>' +
                                '</span>' +
                                '<span style="color:#9aa0a6; font-size:12px;">(' + pCount + ')</span>' +
                            '</span>' +
                        '</span>';
                    groupDiv.appendChild(groupLabel);

                    items.forEach(function(it) {{
                        var itemLbl = document.createElement("label");
                        var cb = document.createElement("input");
                        cb.type = "checkbox";
                        cb.className = "leaflet-control-layers-selector";
                        cb.checked = true;
                        cb.setAttribute("data-status-key", it.status);
                        cb.addEventListener("change", function() {{
                            var targetMc = window.clientMcGroups[it.status];
                            if (!targetMc) return;
                            if (this.checked) {{
                                if (!map.hasLayer(targetMc)) map.addLayer(targetMc);
                            }} else {{
                                if (map.hasLayer(targetMc)) map.removeLayer(targetMc);
                            }}
                        }});

                        var spanWrap = document.createElement("span");
                        spanWrap.innerHTML =
                            ' <span style="display:flex; justify-content:space-between; align-items:flex-start; width:100%;">' +
                                '<span style="display:flex; align-items:flex-start; flex:1;">' +
                                    '<i class="fa fa-' + it.icon + '" style="color:' + it.color + '; width:16px; text-align:center; margin-right:12px; margin-top:3px; flex-shrink:0;"></i>' +
                                    '<span class="status-text" data-status="' + escapeHtmlSafe(it.status) + '" style="font-size:13.5px; color:#e3e3e3; line-height:1.4; word-break:keep-all; overflow-wrap:break-word; text-wrap:balance;">' + escapeHtmlSafe(it.status) + '</span>' +
                                '</span>' +
                                '<span style="color:#9aa0a6; font-size:12px; margin-left:8px; flex-shrink:0;">(' + it.count + ')</span>' +
                            '</span>';

                        itemLbl.appendChild(cb);
                        itemLbl.appendChild(spanWrap);
                        groupDiv.appendChild(itemLbl);
                    }});

                    overlaysContainer.appendChild(groupDiv);
                }});

                if (typeof window.bindClientFilterEvents === "function") {{
                    window.bindClientFilterEvents();
                }}
            }}
        }};

        // ผูก Event Hover และ Group Checkbox สำหรับตัวกรองแผนที่ที่สร้างขึ้นแบบไดนามิกบนฝั่ง Client
        window.bindClientFilterEvents = function() {{
            var filterWrapper = document.getElementById("customFilterWrapper");
            if (!filterWrapper) return;

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

            filterWrapper.querySelectorAll('.leaflet-control-layers-selector').forEach(function(cb) {{
                cb.addEventListener('change', function() {{ syncFilterGroupCheckboxes(); reapplyHighlight(); }});
            }});

            filterWrapper.querySelectorAll('label:not(.leaflet-control-layers-group-label)').forEach(function(lbl) {{
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
                lbl.addEventListener('click', function(e) {{
                    e.preventDefault(); e.stopPropagation();
                    var parentGroup = this.closest('.leaflet-control-layers-group');
                    if (!parentGroup) return;
                    var checkboxes = Array.from(parentGroup.querySelectorAll('input.leaflet-control-layers-selector[type="checkbox"]'))
                        .filter(function(cb) {{ return !cb.closest('.leaflet-control-layers-group-label'); }});
                    if (!checkboxes.length) return;
                    var allChecked = checkboxes.every(function(cb) {{ return cb.checked; }});
                    var shouldCheck = !allChecked;
                    checkboxes.forEach(function(cb) {{ if (cb.checked !== shouldCheck) cb.click(); }});
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

            syncFilterGroupCheckboxes();
        }};

        // อัปเกรด applySeamlessMapData ให้สร้างแผนที่ครั้งแรกเท่านั้น ส่วนการอัปเดตรอบถัดไปให้ซิงค์เฉพาะจุดที่เปลี่ยนแบบไร้รอยต่อ (Zero-Flicker Seamless Update)
        var _origApplySeamlessMapData = window.applySeamlessMapData;
        window.applySeamlessMapData = function(payload) {{
            var isFirstHydration = (!sData || sData.length === 0 || !window.clientMcGroups || Object.keys(window.clientMcGroups).length === 0);
            var oldStatusById = Object.create(null);
            if (!isFirstHydration && Array.isArray(sData)) {{
                sData.forEach(function(it) {{
                    oldStatusById[it.safe_id] = it.status;
                }});
            }}
            _origApplySeamlessMapData(payload);
            if (isFirstHydration) {{
                window.rebuildLeafletMapClientSide();
            }} else if (payload && Array.isArray(payload.search_data)) {{
                var hasStatusShift = payload.search_data.some(function(it) {{
                    return oldStatusById[it.safe_id] !== it.status;
                }});
                if (hasStatusShift && (!document.getElementById('customInfoPanel') || document.getElementById('customInfoPanel').style.display !== 'flex')) {{
                    window.rebuildLeafletMapClientSide();
                }}
            }}
        }};

        // รองรับการดึงข้อมูลสดจาก Google Sheets โดยตรงผ่าน GViz JSONP กรณีเปิดแบบไฟล์ HTML Standalone (file://)
        window.syncLiveGoogleSheetDirectJSONP = function() {{
            var sheetId = "{SPREADSHEET_ID}";
            var cbName = "_scadaGvizCb_" + Date.now();
            window[cbName] = function(resp) {{
                try {{
                    if (!resp || !resp.table || !Array.isArray(resp.table.rows)) return;
                    var rows = resp.table.rows;
                    if (rows.length < 100) return;
                    var upBySid = Object.create(null);
                    rows.forEach(function(r) {{
                        var c = r.c || [];
                        var cmd = c[0] && (c[0].f || c[0].v) ? String(c[0].f || c[0].v).trim() : "";
                        var sid = c[1] && (c[1].f || c[1].v) ? String(c[1].f || c[1].v).trim().toUpperCase() : "";
                        var eq = c[2] && (c[2].f || c[2].v) ? String(c[2].f || c[2].v).trim() : "";
                        var st = c[3] && (c[3].f || c[3].v) ? String(c[3].f || c[3].v).trim() : "";
                        var pea = c[4] && (c[4].f || c[4].v) ? String(c[4].f || c[4].v).trim() : "";
                        var loc = c[5] && (c[5].f || c[5].v) ? String(c[5].f || c[5].v).trim() : "";
                        if (sid && sid !== "SITE ID") {{
                            upBySid[sid] = {{ cmd: cmd.replace(/-OLTC$/i, ""), eq: eq, st: st, pea: pea, loc: loc }};
                        }}
                    }});
                    var changed = false;
                    dashAllRecords.forEach(function(rec) {{
                        var sid = String(rec["Site ID"] || "").trim().toUpperCase();
                        var u = upBySid[sid];
                        if (!u) return;
                        if (u.st && rec["State SCADA"] !== u.st) {{
                            rec["State SCADA"] = u.st;
                            changed = true;
                        }}
                        if (u.pea && u.pea !== "Unknown" && rec["การไฟฟ้า"] !== u.pea) {{
                            rec["การไฟฟ้า"] = u.pea;
                            changed = true;
                        }}
                        if (u.loc && rec["สถานที่"] !== u.loc) {{
                            rec["สถานที่"] = u.loc;
                            changed = true;
                        }}
                    }});
                    if (changed) {{
                        window.renderScadaDashboard();
                    }}
                }} catch (err) {{
                    console.warn("Direct Google Sheet JSONP sync warning:", err);
                }} finally {{
                    try {{ delete window[cbName]; }} catch (e) {{}}
                }}
            }};
            var sc = document.createElement("script");
            sc.src = "https://docs.google.com/spreadsheets/d/" + sheetId + "/gviz/tq?gid={UPDATE_STATE_GID}&headers=1&tqx=out:json;responseHandler:" + cbName + "&_cb=" + Date.now();
            sc.async = true;
            document.body.appendChild(sc);
        }};

        // หากเปิดหน้าเว็บแบบ Client-Side Shell หรือมีข้อมูลฝังมาแล้ว ให้ตรวจสอบข้อมูลล่าสุดจาก `/api/map_data` เสมอ
        if (!dashAllCompact || dashAllCompact.length === 0) {{
            try {{
                var cachedRawStr = sessionStorage.getItem('scada_raw_cache_v1');
                if (cachedRawStr) {{
                    var cachedPayload = JSON.parse(cachedRawStr);
                    if (cachedPayload && Array.isArray(cachedPayload.dash_all_data) && cachedPayload.dash_all_data.length > 0) {{
                        window.applySeamlessMapData(cachedPayload);
                    }}
                }}
            }} catch (e) {{}}
            window.fetchSeamlessMapData(true);
        }} else {{
            setTimeout(function() {{
                window.rebuildLeafletMapClientSide();
                if (window.location.protocol !== 'file:') {{
                    window.fetchSeamlessMapData(false);
                }} else {{
                    window.syncLiveGoogleSheetDirectJSONP();
                }}
            }}, 50);
        }}

        // ตั้งเวลาดึงข้อมูลอัปเดตอัตโนมัติผ่าน Fetch API ทุกๆ 60 วินาที (Seamless Background Polling)
        setInterval(function() {{
            window.fetchSeamlessMapData(false);
        }}, 60000);
        </script>
    """

    rendered_html = (
        '<!DOCTYPE html>\n<html lang="th">\n<head>\n'
        '    <meta http-equiv="content-type" content="text/html; charset=UTF-8" />\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />\n'
        '    <title>รายงานความพร้อมใช้งานอุปกรณ์ในระบบ SCADA NE2 (%AVA)</title>\n'
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
        '    <style>#map_scada_client { position: relative; width: 100.0%; height: 100.0%; left: 0.0%; top: 0.0%; } .leaflet-container { font-size: 1rem; } html, body { width: 100%; height: 100%; margin: 0; padding: 0; } #map { position:absolute; top:0; bottom:0; right:0; left:0; }</style>\n'
        '    <script>L_NO_TOUCH = false; L_DISABLE_3D = false;</script>\n'
        '    <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.1.0/leaflet.markercluster.js"></script>\n'
        '    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.1.0/MarkerCluster.css"/>\n'
        '    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.1.0/MarkerCluster.Default.css"/>\n'
        '</head>\n<body>\n'
        '    <!-- โครงสร้างฟอร์มตัวกรองเริ่มต้นสำหรับให้ Client-Side JS ย้ายเข้าไปใน #customFilterWrapper -->\n'
        '    <div class="leaflet-control-layers" style="display:none;">\n'
        '        <form id="clientLayersForm" class="leaflet-control-layers-list">\n'
        '            <div id="clientLayersOverlays" class="leaflet-control-layers-overlays"></div>\n'
        '        </form>\n'
        '    </div>\n'
        '    <div class="folium-map" id="map_scada_client"></div>\n'
        '    <script>\n'
        '    var map_scada_client = L.map("map_scada_client", { center: [15.2282, 104.8563], crs: L.CRS.EPSG3857, zoom: 8, zoomControl: false, preferCanvas: true });\n'
        '    var tile_layer_terrain = L.tileLayer("https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}", {"minZoom": 0, "maxZoom": 22, "maxNativeZoom": 22, "noWrap": false, "attribution": "Google", "subdomains": "abc", "detectRetina": false, "tms": false, "opacity": 1}).addTo(map_scada_client);\n'
        '    var tile_layer_street = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {"minZoom": 0, "maxZoom": 22, "maxNativeZoom": 22, "noWrap": false, "attribution": "OpenStreetMap", "subdomains": "abc", "detectRetina": false, "tms": false, "opacity": 1});\n'
        '    var tile_layer_satellite = L.tileLayer("https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}", {"minZoom": 0, "maxZoom": 22, "maxNativeZoom": 22, "noWrap": false, "attribution": "Google", "subdomains": "abc", "detectRetina": false, "tms": false, "opacity": 1});\n'
        '    var tile_layer_hybrid = L.tileLayer("https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}", {"minZoom": 0, "maxZoom": 22, "maxNativeZoom": 22, "noWrap": false, "attribution": "Google", "subdomains": "abc", "detectRetina": false, "tms": false, "opacity": 1});\n'
        '    var tile_layer_esri = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {"minZoom": 0, "maxZoom": 22, "maxNativeZoom": 22, "noWrap": false, "attribution": "Esri", "subdomains": "abc", "detectRetina": false, "tms": false, "opacity": 1});\n'
        '    </script>\n'
        + custom_ui_html + '\n</body>\n</html>'
    )

    if write_files:
        try:
            with open(DETAILED_MAP_OUTPUT_FILE, 'w', encoding='utf-8') as _f_out_map:
                _f_out_map.write(rendered_html)
            _dash_html = rendered_html.replace(
                'var dashDemoMode = false;',
                'window.DEFAULT_START_DASHBOARD = true;\nvar dashDemoMode = false;',
                1
            )
            with open(DASHBOARD_OUTPUT_FILE, 'w', encoding='utf-8') as _f_out_dash:
                _f_out_dash.write(_dash_html)
            print(f"[OK] บันทึกไฟล์ผลลัพธ์ทั้ง 3 ไฟล์ไว้ในตำแหน่งเดิมเรียบร้อยแล้ว:")
            print(f"  1) ไฟล์แดชบอร์ด : {DASHBOARD_OUTPUT_FILE}")
            print(f"  2) ไฟล์ฐานข้อมูล : {DB_PATH}")
            print(f"  3) ไฟล์แผนที่ละเอียด: {DETAILED_MAP_OUTPUT_FILE}")
        except Exception as _e:
            print(f"[ERROR] ไม่สามารถสร้างไฟล์ {DASHBOARD_OUTPUT_FILE} และ {DETAILED_MAP_OUTPUT_FILE} ได้: {_e}")

    if not embed_initial_data:
        _CLIENT_SIDE_HTML_SHELL_CACHE = rendered_html

    return {
        "version": int(_data_hash_str, 16),
        "last_updated": time.time(),
        "overall_summary": overall_summary_dict,
        "dash_all_data": dash_all_data_list,
        "search_data": search_data,
        "export_data": export_data_list,
        "report_data": report_data_list,
        "status_hash_map": dict(status_hash_map),
        "comm_device_groups": COMM_DEVICE_GROUPS,
        "display_fields": [name for name, _ in resolved_display_cols],
        "html": rendered_html,
    }


def get_client_side_shell_html(view_mode="map", open_ai=False, embed_data_payload=None):
    """
    คืนค่า HTML Shell น้ำหนักเบาสำหรับทำ Client-Side Rendering
    โดยฝั่ง Browser จะดึง JSON ข้อมูลดิบจาก `/api/map_data` มาประมวลผลและสร้างแผนที่/ตารางเอง
    """
    res = build_scada_map_and_data(prefer_local=True, write_files=False, embed_initial_data=bool(embed_data_payload))
    html_content = res["html"]
    if view_mode == "dashboard":
        html_content = html_content.replace(
            "var dashDemoMode = false;",
            "window.DEFAULT_START_DASHBOARD = true;\nvar dashDemoMode = false;",
            1,
        )
    if open_ai:
        html_content = html_content.replace(
            "var dashDemoMode = false;",
            "window.DEFAULT_OPEN_AI_MODAL = true;\nvar dashDemoMode = false;",
            1,
        )
    return html_content


if __name__ == '__main__':
    build_scada_map_and_data(
        prefer_local=('--offline' in sys.argv or '--use-cache' in sys.argv),
        write_files=True,
        embed_initial_data=('--no-embed-data' not in sys.argv),
    )