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

# เรียงลำดับ URL โดยให้ความสำคัญกับ /export?format=csv เป็นอันดับแรก เพื่อป้องกันปัญหา GViz ตัดค่าคอลัมน์ที่มีชนิดข้อมูลผสม (Mixed Types)
SHEET1_URLS = [
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={SHEET1_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=1&gid={SHEET1_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:json&headers=1&gid={SHEET1_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=0&gid={SHEET1_GID}',
]

DATA_CHECK_URLS = [
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={DATA_CHECK_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=1&gid={DATA_CHECK_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet=Data_Check',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:json&headers=1&gid={DATA_CHECK_GID}',
    f'https://docs.google.com/spreadsheets/d/{FORM_SOURCE_ID}/gviz/tq?tqx=out:csv&sheet=Form3_Responses',
    f'https://docs.google.com/spreadsheets/d/{FORM_SOURCE_ID}/export?format=csv',
]

UPDATE_STATE_URLS = [
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={UPDATE_STATE_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=1&gid={UPDATE_STATE_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet=Update%20State',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:json&headers=1&gid={UPDATE_STATE_GID}',
]

RTU_SUMMARY_URLS = [
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid={RTU_SUMMARY_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&headers=1&gid={RTU_SUMMARY_GID}',
    f'https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet=RTU%20Summary',
    f'https://docs.google.com/spreadsheets/d/{RTU_SUMMARY_SOURCE_ID}/export?format=csv',
    f'https://docs.google.com/spreadsheets/d/{RTU_SUMMARY_SOURCE_ID}/gviz/tq?tqx=out:csv&sheet=%E0%B8%AA%E0%B8%A3%E0%B8%B8%E0%B8%9B%20RTU',
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
            if _is_meaningful_cell(u_loc):
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

    initial_version = int(time.time()) if embed_initial_data else 0
    overall_summary_dict = compute_overall_summary(dash_all_data_list)

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
            "version": int(time.time()),
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

        // อัปเกรด applySeamlessMapData ให้รองรับการสร้างแผนที่และตารางครั้งแรกบน Client-Side ทันทีที่ได้รับ JSON
        var _origApplySeamlessMapData = window.applySeamlessMapData;
        window.applySeamlessMapData = function(payload) {{
            var isFirstHydration = (!sData || sData.length === 0);
            _origApplySeamlessMapData(payload);
            if (isFirstHydration || (payload && Array.isArray(payload.search_data))) {{
                window.rebuildLeafletMapClientSide();
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
        "version": int(time.time()),
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