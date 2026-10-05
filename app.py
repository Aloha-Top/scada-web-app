from flask import Flask, jsonify, request, render_template_string
import pandas as pd
import folium
import json
import hashlib
import html
import time
import threading
import os
import re
from folium.plugins import GroupedLayerControl, MarkerCluster

app = Flask(__name__)

# --- ระบบ Cache แบบ File-Based (เสถียรบน Render) ---
CACHE_HTML_FILE = '/tmp/scada_cache.html'
CACHE_META_FILE = '/tmp/scada_meta.json'
LOCK_FILE = '/tmp/updating.lock'
CACHE_DURATION = 300 # อัปเดตข้อมูลอัตโนมัติทุกๆ 5 นาที

def get_meta():
    try:
        with open(CACHE_META_FILE, 'r') as f:
            return json.load(f)
    except:
        return {'version': 0, 'last_update': 0}

def get_status_config(status_text):
    status_upper = str(status_text).strip().upper()
    
    if status_upper.startswith('TELEMETRY'):
        return "Telemetry Failure", "gold", "wrench"
    elif status_upper.startswith('CONNECTING'):
        raw_parent = "Connecting"
        if 'ระบบสื่อสาร' in status_upper: color = "purple"
        elif 'ผอส.' in status_upper and 'ผบอ.' not in status_upper: color = "purple" 
        else: color = "orange"
        if 'เคยแก้ไข' in status_upper: icon = "history"
        elif 'ผบอ.' in status_upper and 'ผอส.' in status_upper: icon = "user"
        elif 'ผบอ.' in status_upper or 'ผอส.' in status_upper: icon = "check"
        else: icon = "wrench"
        return raw_parent, color, icon
    elif status_upper.startswith('OFFLINE'):
        raw_parent = "Offline"
        color = "red"
        if 'เคยแก้ไข' in status_upper: icon = "history"
        elif 'ผบอ.' in status_upper and 'ผอส.' in status_upper: icon = "user"
        elif 'ผบอ.' in status_upper or 'ผอส.' in status_upper: icon = "check"
        else: icon = "times"
        return raw_parent, color, icon
    elif status_upper.startswith('ONLINE'):
        raw_parent = "Online"
        color = "green"
        icon = "history" if 'เคยแก้ไข' in status_upper else "check"
        return raw_parent, color, icon
    elif status_upper.startswith('INITIALIZING'):
        raw_parent = "Initializing"
        color = "lightgreen"
        if 'เคยแก้ไข' in status_upper: icon = "history"
        elif 'ผบอ.' in status_upper or 'ผอส.' in status_upper: icon = "check"
        else: icon = "wrench"
        return raw_parent, color, icon
    else:
        return "สถานะอื่นๆ", "gray", "info-circle"

def get_actual_col_name(df_columns, keywords, exclude=None):
    for col in df_columns:
        if all(kw.lower() in str(col).lower() for kw in keywords):
            if exclude and any(ex.lower() in str(col).lower() for ex in exclude): continue
            return col
    return None

def parse_thai_date(date_str):
    if not date_str or pd.isna(date_str): return ""
    date_str = str(date_str).strip()
    if re.match(r'^\d{4}-\d{2}-\d{2}', date_str): return date_str[:10]
        
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
        if year < 100: year += 2000
        elif year > 2500: year -= 543
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    
    try: 
        dt = pd.to_datetime(date_str, dayfirst=True)
        if pd.notna(dt): return dt.strftime("%Y-%m-%d")
    except: pass
    return ""

def format_date_ddmmyyyy(iso_date):
    if not iso_date: return "ไม่ระบุวันที่"
    try:
        parts = iso_date.split('-')
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    except: return "ไม่ระบุวันที่"

def generate_map():
    print("กำลังดึงข้อมูลใหม่จาก Google Sheets...")
    sheet_id = "10QuVWnj2BCPpNqrXpBM8sbARmKGTksQ1fxUYx2Xaa8Q"
    csv_export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid=0"
    
    try: 
        df = pd.read_csv(csv_export_url)
    except Exception as e: 
        raise RuntimeError(f"ไม่สามารถเชื่อมต่อหรือดึงข้อมูลจาก Google Sheets ได้: {e}")

    status_hash_map = {}
    def get_hash(text):
        if text not in status_hash_map: status_hash_map[text] = hashlib.md5(text.encode('utf-8')).hexdigest()[:8]
        return status_hash_map[text]

    m = folium.Map(location=[15.2282, 104.8563], zoom_start=8, zoom_control=False, tiles=None, prefer_canvas=True, max_zoom=22)

    folium.TileLayer('https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}', attr='Google', name='แผนที่ภูมิประเทศ (Google Terrain)', overlay=False, control=True, max_zoom=22, show=True).add_to(m)
    folium.TileLayer('OpenStreetMap', name='แผนที่ถนน (Street Map)', overlay=False, control=True, max_zoom=22, show=False).add_to(m)
    folium.TileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', attr='Google', name='ภาพดาวเทียมล้วน (Google Satellite)', overlay=False, control=True, max_zoom=22, show=False).add_to(m)
    folium.TileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', attr='Google', name='ภาพดาวเทียม + ถนน (Google Hybrid)', overlay=False, control=True, max_zoom=22, show=False).add_to(m)
    folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr='Esri', name='ภาพดาวเทียม (Esri World Imagery)', overlay=False, control=True, max_zoom=22, show=False).add_to(m)

    raw_parent_keys = {"Telemetry Failure": ("#ffc107", "Telemetry Failure"), "Offline": ("#d33d2a", "Offline"), "Online": ("#72b026", "Online"), "Initializing": ("#82c91e", "Initializing"), "Connecting": ("#f3943b", "Connecting"), "สถานะอื่นๆ": ("#575757", "สถานะอื่นๆ")}
    hex_color_map = {'red': '#d33d2a', 'darkred': '#8b0000', 'orange': '#f3943b', 'green': '#72b026', 'lightgreen': '#82c91e', 'blue': '#38aadd', 'darkblue': '#0067a3', 'purple': '#9b59b6', 'black': '#333333', 'gray': '#575757', 'lightgray': '#a3a3a3', 'beige': '#f5c07f', 'gold': '#ffc107'}
    display_fields = [("รหัสสั่งการ", ["รหัสสั่งการ"], []), ("สถานที่", ["สถานที่"], []), ("State SCADA", ["state scada"], ["หลัง"]), ("State SCADA (หลังตรวจสอบ)", ["state scada", "หลัง"], []), ("ชนิดอุปกรณ์", ["ชนิดอุปกรณ์"], []), ("รายละเอียดการแก้ไขข้อขัดข้อง", ["รายละเอียด", "การแก้ไข"], ["เข้า"]), ("รายละเอียดการเข้าแก้ไข", ["รายละเอียด", "การเข้าแก้ไข"], []), ("LAT/LONG", ["lat", "long"], []), ("รอ ผอส. เข้าแก้ไข", ["รอ ผอส"], []), ("รอ ผบอ. เข้าแก้ไข", ["รอ ผบอ"], []), ("การไฟฟ้า", ["การไฟฟ้า"], []), ("วันที่เข้าตรวจสอบ", ["วันที่เข้าตรวจสอบ"], [])]

    processed_nodes, status_counts, parent_counts = [], {}, {k: 0 for k in raw_parent_keys.keys()}
    seen_coords = {}

    report_data_list = []
    
    cmd_col_report = get_actual_col_name(df.columns, ["รหัสสั่งการ"]) or get_actual_col_name(df.columns, ["รหัสอุปกรณ์"]) or get_actual_col_name(df.columns, ["Equipment_ID"])
    site_col_report = get_actual_col_name(df.columns, ["site id"])
    loc_col_report = get_actual_col_name(df.columns, ["สถานที่"])
    date_col_report = get_actual_col_name(df.columns, ["วันที่เข้าตรวจสอบ"])

    state_scada_col = get_actual_col_name(df.columns, ["state scada"], exclude=["หลัง"])
    state_after_col = get_actual_col_name(df.columns, ["state scada", "หลัง"])
    wait_po_col = get_actual_col_name(df.columns, ["รอ ผอส"])
    wait_bo_col = get_actual_col_name(df.columns, ["รอ ผบอ"])

    col_H = df.columns[7] if len(df.columns) > 7 else None
    col_J = df.columns[9] if len(df.columns) > 9 else None
    col_K = df.columns[10] if len(df.columns) > 10 else None
    target_detail_cols = [c for c in [col_H, col_J, col_K] if c is not None]

    google_svg = '<svg viewBox="0 0 24 24" width="15" height="15" fill="white" style="margin-right:6px;"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg>'
    apple_svg = '<svg viewBox="0 0 384 512" style="width: 13px; height: auto; margin-right: 6px; margin-bottom: 2px;" fill="white"><path d="M318.7 268.7c-.2-36.7 16.4-64.4 50-84.8-18.8-26.9-47.2-41.7-84.7-44.6-35.5-2.8-74.3 20.7-88.5 20.7-15 0-49.4-19.7-76.4-19.7C63.3 141.2 4 184.8 4 273.5q0 39.3 14.4 81.2c12.8 36.7 59 126.7 107.2 125.2 25.2-.6 43-17.9 75.8-17.9 31.8 0 48.3 17.9 76.4 17.3 48.6-.7 90.4-84.3 103-119.3-34.6-18.6-53.6-45.9-52.7-81.5zM266.4 87.8C282.5 67 292.7 39.2 292.7 11.5c0-3.3-.2-5.5-.6-7.8-33.1 1.4-62.7 20-80.8 42.1-15 18.5-26.4 46.1-24.9 71.6 3.3.4 5.5.6 7.8.6 30.5-.3 57.7-18.7 71.6-40.2z"/></svg>'

    for index, row in df.iterrows():
        lat_col = get_actual_col_name(df.columns, ["lat", "long"])
        lat_lng_str = str(row.get(lat_col, '')) if lat_col else ''
        if not lat_lng_str or ',' not in lat_lng_str: continue
        try: lat, lon = float(lat_lng_str.split(',')[0].strip()), float(lat_lng_str.split(',')[1].strip())
        except ValueError: continue

        coord_key = (round(lat, 5), round(lon, 5)) 
        if coord_key in seen_coords:
            seen_coords[coord_key] += 1
            offsets = [(0.00005, 0.00005), (-0.00005, -0.00005), (0.00005, -0.00005), (-0.00005, 0.00005), (0.00008, 0.0), (0.0, 0.00008), (-0.00008, 0.0), (0.0, -0.00008)]
            lat += offsets[(seen_coords[coord_key] - 1) % len(offsets)][0]; lon += offsets[(seen_coords[coord_key] - 1) % len(offsets)][1]
        else: seen_coords[coord_key] = 0

        base_status = str(row.get(state_scada_col, '')).strip() if state_scada_col else 'Unknown'
        check_status = str(row.get(state_after_col, '')).strip() if state_after_col else ''
        
        active_status = check_status if check_status and check_status.lower() not in ['nan', 'ไม่มีค่า', 'none'] else base_status
        raw_parent, color, icon_name = get_status_config(active_status)
        active_status_display = active_status

        status_counts[active_status] = status_counts.get(active_status, 0) + 1
        parent_counts[raw_parent] += 1
        processed_nodes.append({'row': row, 'lat': lat, 'lon': lon, 'active_status': active_status, 'active_status_display': active_status_display, 'raw_parent': raw_parent, 'color': color, 'icon_name': icon_name})

        # --- Report Data Extraction ---
        raw_date_val = str(row.get(date_col_report, '')).strip() if date_col_report else ""
        
        if raw_date_val and raw_date_val.lower() not in ['nan', 'none', '-']:
            
            details = []
            for d_col in target_detail_cols:
                val = str(row.get(d_col, '')).strip()
                if val and val.lower() not in ['nan', 'none', '-', '', 'ไม่มีค่า']:
                    clean_val = re.sub(r'[\☑\☒\☐\✔\✘\✓\❌\✅\✖\⚠️\u26A0\uFE0F\❗️\❓\‼️\⁉️]', '', val).strip()
                    clean_val = re.sub(r'\d{2}\.\d{4,},\s*\d{3}\.\d{4,}', '', clean_val).strip()
                    clean_val = re.sub(r'\s+', ' ', clean_val)
                    if clean_val and clean_val not in details:
                        details.append(clean_val)
            
            detail_str = "\n".join(details) if details else "-"
            
            resp_parts = []
            if check_status and check_status.lower() not in ['nan', 'none']:
                if "ผบอ" in check_status: resp_parts.append("ผบอ.กบษ.ฉ.2")
                if "ผอส" in check_status: resp_parts.append("ผอส.กสฟ.ฉ.2")
                    
            val_bo = str(row.get(wait_bo_col, '')).strip() if wait_bo_col else ""
            has_bo = val_bo and val_bo.lower() not in ['nan', 'none', '-', '', 'ไม่มีค่า', 'false', '0']
            
            val_po = str(row.get(wait_po_col, '')).strip() if wait_po_col else ""
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
                if r not in final_resp: final_resp.append(r)
            responsible = " และ ".join(final_resp) if final_resp else "-"
            
            final_status = base_status if base_status.lower() not in ['nan', 'none'] else "-"
            iso_date = parse_thai_date(raw_date_val)
            
            report_data_list.append({
                "iso_date": iso_date,
                "cmd": str(row.get(cmd_col_report, '-')) if cmd_col_report else "-",
                "site": str(row.get(site_col_report, '-')) if site_col_report else "-",
                "loc": str(row.get(loc_col_report, '-')) if loc_col_report else "-",
                "detail": detail_str,
                "resp": responsible,
                "status": final_status
            })

    grouped_layers = {f"<span style='display:flex; justify-content:space-between; align-items:center; width:100%; padding: 10px 16px; border-bottom: 1px solid #444746;'><span style='display:flex; align-items:center;'><span style='color:{raw_parent_keys[k][0]}; font-size:16px; margin-right:8px; line-height:1;'>●</span><span class='parent-text' data-parent='{k}' style='font-size:14px; font-weight:600; color:#e3e3e3;'>{raw_parent_keys[k][1]}</span></span><span style='color:#9aa0a6; font-size:12px;'>({parent_counts[k]})</span></span>": [] for k in raw_parent_keys.keys() if parent_counts[k] > 0}
    mc_groups, search_data, export_data_list = {}, [], []

    for node in processed_nodes:
        row, lat, lon, active_status, active_status_display = node['row'], node['lat'], node['lon'], node['active_status'], node['active_status_display']
        raw_parent, icon_name, h_color = node['raw_parent'], node['icon_name'], hex_color_map.get(node['color'], '#575757')
        
        c_hex, label = raw_parent_keys[raw_parent]
        p_html = f"<span style='display:flex; justify-content:space-between; align-items:center; width:100%; padding: 10px 16px; border-bottom: 1px solid #444746;'><span style='display:flex; align-items:center;'><span style='color:{c_hex}; font-size:16px; margin-right:8px; line-height:1;'>●</span><span class='parent-text' data-parent='{raw_parent}' style='font-size:14px; font-weight:600; color:#e3e3e3;'>{label}</span></span><span style='color:#9aa0a6; font-size:12px;'>({parent_counts[raw_parent]})</span></span>"
        safe_status, safe_parent, safe_active_status_attr = "s_" + get_hash(active_status), "p_" + get_hash(raw_parent), html.escape(active_status)

        if active_status not in mc_groups:
            layer_name = f"<span style='display:flex; justify-content:space-between; align-items:flex-start; width:100%;'><span style='display:flex; align-items:flex-start; flex:1;'><i class='fa fa-{icon_name}' style='color:{h_color}; width:16px; text-align:center; margin-right:12px; margin-top:3px; flex-shrink:0;'></i><span class='status-text' data-status='{safe_active_status_attr}' style='font-size:13.5px; color:#e3e3e3; line-height:1.4; word-break:keep-all; overflow-wrap:break-word; text-wrap:balance;'>{active_status_display}</span></span><span style='color:#9aa0a6; font-size:12px; margin-left:8px; flex-shrink:0;'>({status_counts[active_status]})</span></span>"
            custom_cluster_js = f"function(c) {{ var count = c.getChildCount(); return new L.DivIcon({{ html: '<div class=\"map-cluster-inner {safe_status} {safe_parent}\" style=\"background-color: {h_color}; color: white; border-radius: 50%; width: 44px; height: 44px; display: flex; flex-direction: column; justify-content: center; align-items: center; font-family: Prompt, sans-serif; font-weight: 600; border: 2px solid white; box-shadow: 0 2px 5px rgba(0,0,0,0.4); text-shadow: 1px 1px 2px rgba(0,0,0,0.7); transition: all 0.3s ease;\"><i class=\"fa fa-{icon_name}\" style=\"font-size: 12px; margin-bottom: 2px;\"></i><span style=\"font-size: 13px; line-height: 1;\">' + count + '</span></div>', className: 'custom-cluster-marker', iconSize: new L.Point(44, 44), iconAnchor: new L.Point(22, 22) }}); }}"
            mc = MarkerCluster(name=layer_name, show=True, icon_create_function=custom_cluster_js, control=False, options={'disableClusteringAtZoom': 17, 'maxClusterRadius': 35, 'chunkedLoading': True})
            mc_groups[active_status] = mc
            grouped_layers[p_html].append((active_status, mc))
        
        target_group, table_rows = mc_groups[active_status], ""
        
        site_id_col = get_actual_col_name(df.columns, ["site id"])
        site_id = str(row.get(site_id_col, '')) if site_id_col else 'Unknown'
        safe_site_id = "id_" + get_hash(site_id + str(lat))

        cmd_code_col = get_actual_col_name(df.columns, ["รหัสสั่งการ"]) or get_actual_col_name(df.columns, ["รหัสอุปกรณ์"])
        cmd_code = str(row.get(cmd_code_col, '')) if cmd_code_col else ''
        loc_col = get_actual_col_name(df.columns, ["สถานที่"])
        location_name = str(row.get(loc_col, '')) if loc_col else ''

        export_row = {"_layerName": active_status, "Site ID": site_id} 
        
        for display_name, keywords, excludes in display_fields:
            actual_col = get_actual_col_name(df.columns, keywords, exclude=excludes) or (get_actual_col_name(df.columns, ["รหัสอุปกรณ์"]) if "รหัสสั่งการ" in keywords else None)
            val = row.get(actual_col, 'ไม่มีค่า') if actual_col else 'ไม่มีค่า'
            if str(val).strip() == '' or str(val).lower() == 'nan' or str(val) == 'none': val = 'ไม่มีค่า'
            
            if "วันที่" in display_name and val != 'ไม่มีค่า':
                try:
                    dt = pd.to_datetime(val)
                    thai_months = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
                    val = f"{dt.day} {thai_months[dt.month]} {str(dt.year)[-2:]}"
                except: pass
                
            val_str = str(val)
            display_val = val_str 
            table_rows += f"<tr><td>{display_name}</td><td>{display_val}</td></tr>"
            export_row[display_name] = val_str

        export_data_list.append(export_row)
        
        popup_html = f"""
        <div class="custom-popup-card" data-safe-id="{safe_site_id}">
            <div class="popup-header" style="background-color: {h_color};">
                <div class="popup-icon"><i class="fa fa-{icon_name}"></i></div>
                <div class="popup-title"><h3>{site_id}</h3><span>{active_status_display}</span></div>
            </div>
            <div class="popup-actions">
                <a href="https://www.google.com/maps/dir/?api=1&destination={lat},{lon}" target="_blank" class="btn-map btn-google">{google_svg} Google Maps</a>
                <a href="http://maps.apple.com/?daddr={lat},{lon}" target="_blank" class="btn-map btn-apple">{apple_svg} Apple Maps</a>
            </div>
            <div class="popup-body"><table class="popup-table">{table_rows}</table></div>
        </div>
        """
        search_data.append({"id": site_id, "code": cmd_code, "name": location_name, "lat": lat, "lon": lon, "popup": popup_html, "safe_id": safe_site_id, "status": active_status_display, "color": h_color})

        pin_html = f"""<div class="map-pin-inner {safe_status} {safe_parent} pin-site-{safe_site_id}" style="position: relative; width: 30px; height: 42px; display: flex; justify-content: center; transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);"><div class="pin-shape" style="position: absolute; top: 0; left: 0; width: 30px; height: 30px; background-color: {h_color}; border: 2px solid white; border-radius: 50% 50% 50% 0; transform: rotate(-45deg); box-shadow: 2px 2px 6px rgba(0,0,0,0.4); transition: all 0.3s ease;"></div><i class="fa fa-{icon_name}" style="position: relative; color: white; font-size: 14px; margin-top: 6px; z-index: 1; transition: all 0.3s ease;"></i></div>"""
        folium.Marker(location=[lat, lon], popup=folium.Popup(popup_html, autoPan=False), tooltip=f"{site_id} ({location_name})", icon=folium.DivIcon(html=pin_html, icon_size=(30, 42), icon_anchor=(15, 42), popup_anchor=(0, -42))).add_to(target_group)

    active_grouped_layers = {}
    for p_html, items_list in grouped_layers.items():
        if len(items_list) > 0:
            def custom_sort(x):
                s = x[0].upper()
                w = 100
                if s in ["ONLINE", "OFFLINE", "INITIALIZING", "CONNECTING", "TELEMETRY FAILURE"]: w = 1
                elif "รอ ผบอ. เข้าแก้ไข" in s and "ผอส" not in s: w = 2
                elif "รอ ผบอ. และ ผอส." in s or "และ ผอส" in s: w = 3
                elif "รอ ผอส." in s: w = 4
                elif "ผบอ. เคยแก้ไข" in s: w = 5
                elif "ระบบสื่อสาร" in s and "เคยแก้ไข" in s: w = 6
                else: w = 10
                return (w, len(s), s)
            items_list.sort(key=custom_sort)
            sorted_mcs = []
            for active_status, mc in items_list:
                m.add_child(mc)
                sorted_mcs.append(mc)
            active_grouped_layers[p_html] = sorted_mcs

    folium.LayerControl(position='topleft', collapsed=True).add_to(m)
    GroupedLayerControl(groups=active_grouped_layers, exclusive_groups=False, collapsed=True).add_to(m)

    search_json = json.dumps(search_data, ensure_ascii=False)
    export_json = json.dumps(export_data_list, ensure_ascii=False)
    status_hash_json = json.dumps(status_hash_map, ensure_ascii=False)
    report_json_data = json.dumps(report_data_list, ensure_ascii=False)

    # --- ส่วน UI ฉบับแก้ไขสมบูรณ์แล้ว ---
    custom_ui_html = f"""
    <link href="https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;500;600&display=swap" rel="stylesheet">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/exceljs/4.3.0/exceljs.min.js"></script>

    <style>
    * {{ font-family: 'Prompt', sans-serif; outline: none !important; -webkit-tap-highlight-color: transparent !important; box-sizing: border-box; }}
    .leaflet-top {{ z-index: 999 !important; }}
    .leaflet-bottom {{ z-index: 998 !important; }}
    .leaflet-top.leaflet-left .leaflet-control-layers {{ display: none !important; }}
    .leaflet-top.leaflet-right .leaflet-control-layers {{ display: none !important; }}
    .leaflet-popup {{ display: none !important; opacity: 0 !important; pointer-events: none !important; }}

    .selected-pin-glow {{ transform: scale(1.4) !important; z-index: 100000 !important; }}
    .selected-pin-glow .pin-shape {{ box-shadow: 0 0 0 3px #ffffff, 0 0 20px 8px rgba(66, 133, 244, 0.8) !important; border-color: #4285F4 !important; }}
    .selected-pin-glow i {{ text-shadow: 0 0 5px rgba(255,255,255,0.8); }}

    body.filter-hover-active .map-pin-inner, body.filter-hover-active .map-cluster-inner {{ opacity: 0.2; filter: grayscale(100%); }}
    body.filter-hover-active .map-pin-inner.highlight-active, body.filter-hover-active .map-cluster-inner.highlight-active {{ opacity: 1 !important; filter: none !important; transform: scale(1.25); }}
    body.filter-hover-active .map-pin-inner.highlight-active .pin-shape, body.filter-hover-active .map-cluster-inner.highlight-active {{ box-shadow: 0 0 12px 6px rgba(255, 255, 255, 0.9), 0 0 5px rgba(0,0,0,0.5) !important; }}
    body.filter-hover-active .selected-pin-glow {{ opacity: 1 !important; filter: none !important; }}

    .custom-filter-wrapper .leaflet-control-layers-list,
    .g-search-results,
    .popup-body {{ scrollbar-width: thin; scrollbar-color: rgba(154, 160, 166, 0.3) transparent; }}
    .custom-filter-wrapper .leaflet-control-layers-list::-webkit-scrollbar,
    .g-search-results::-webkit-scrollbar,
    .popup-body::-webkit-scrollbar {{ width: 6px; }}
    .custom-filter-wrapper .leaflet-control-layers-list::-webkit-scrollbar-track,
    .g-search-results::-webkit-scrollbar-track,
    .popup-body::-webkit-scrollbar-track {{ background: transparent; }}
    .custom-filter-wrapper .leaflet-control-layers-list::-webkit-scrollbar-thumb,
    .g-search-results::-webkit-scrollbar-thumb,
    .popup-body::-webkit-scrollbar-thumb {{ background-color: rgba(154, 160, 166, 0); border-radius: 10px; }}
    .custom-filter-wrapper .leaflet-control-layers-list:hover::-webkit-scrollbar-thumb,
    .g-search-results:hover::-webkit-scrollbar-thumb,
    .popup-body:hover::-webkit-scrollbar-thumb {{ background-color: rgba(154, 160, 166, 0.4); }}
    .custom-filter-wrapper .leaflet-control-layers-list::-webkit-scrollbar-thumb:hover,
    .g-search-results::-webkit-scrollbar-thumb:hover,
    .popup-body::-webkit-scrollbar-thumb:hover {{ background-color: rgba(138, 180, 248, 0.8); }}

    .g-search-container {{ position: fixed; z-index: 100005; font-family: 'Prompt', sans-serif; top: max(20px, env(safe-area-inset-top, 20px)); left: 16px; width: 380px; margin: 0; pointer-events: none; }}
    .g-search-box {{ pointer-events: auto; background: #282a2d; border-radius: 24px; box-shadow: 0 2px 6px rgba(0,0,0,0.3); display: flex; align-items: center; padding: 0 14px; height: 48px; border: 1px solid #444746; }}

    .top-action-btn {{ position: fixed; top: max(20px, env(safe-area-inset-top, 20px)); width: 48px; height: 48px; background-color: #282a2d; border-radius: 50%; box-shadow: 0 4px 12px rgba(0,0,0,0.3); display: flex; align-items: center; justify-content: center; cursor: pointer; z-index: 100000; transition: all 0.2s; border: 1px solid #444746; pointer-events: auto; }}
    .top-action-btn:hover {{ background-color: #3c4043; transform: translateY(-2px); }}
    .top-action-btn:active {{ transform: scale(0.92); }}
    .standalone-filter-btn {{ right: 16px; }}
    .standalone-report-btn {{ right: 74px; background-color: #1a73e8; border-color: #1a73e8; }}
    .standalone-report-btn:hover {{ background-color: #1557b0; border-color: #1557b0; }}
    .top-action-btn svg {{ fill: none; stroke: #e3e3e3; stroke-width: 2.2; width: 22px; height: 22px; pointer-events: none; }}
    .standalone-report-btn svg {{ stroke: #ffffff; width: 20px; height: 20px; }}

    .custom-filter-wrapper .leaflet-control-layers-base {{ display: none !important; }}

    .custom-filter-wrapper {{ display: none; flex-direction: column; position: fixed; top: calc(max(20px, env(safe-area-inset-top, 20px)) + 60px); right: 16px; width: 340px; max-height: calc(100dvh - 100px) !important; background-color: #282a2d; border-radius: 16px; box-shadow: 0 8px 24px rgba(0,0,0,0.5); border: 1px solid #444746; overflow: hidden; z-index: 999998 !important; pointer-events: auto; padding-top: 8px; }}
    .custom-filter-wrapper.show {{ display: flex !important; }}

    .custom-alert-overlay {{ display: none; position: fixed; top: 0; left: 0; width: 100vw; height: 100dvh; background: rgba(0,0,0,0.4); backdrop-filter: blur(2px); z-index: 9999999; justify-content: center; align-items: center; opacity: 0; transition: opacity 0.3s ease; pointer-events: auto; }}
    .custom-alert-overlay.show {{ display: flex; opacity: 1; }}
    .custom-alert-box {{ background: #282a2d; width: 90%; max-width: 320px; border-radius: 16px; padding: 24px 20px 20px 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); border: 1px solid #444746; text-align: center; transform: scale(0.9); transition: transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275); }}
    .custom-alert-overlay.show .custom-alert-box {{ transform: scale(1); }}
    .custom-alert-icon {{ width: 50px; height: 50px; background: rgba(211, 61, 42, 0.15); border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 16px auto; border: 2px solid rgba(211, 61, 42, 0.4); }}
    .custom-alert-icon svg {{ width: 24px; height: 24px; fill: #d33d2a; }}
    .custom-alert-title {{ color: #e3e3e3; font-size: 16px; font-weight: 600; margin-bottom: 8px; }}
    .custom-alert-msg {{ color: #9aa0a6; font-size: 13.5px; margin-bottom: 24px; line-height: 1.4; }}
    .custom-alert-btn {{ background: #1a73e8; color: white; border: none; padding: 10px 24px; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: background 0.2s; width: 100%; }}
    .custom-alert-btn:hover {{ background: #1557b0; }}

    .report-modal-overlay {{ display: none; position: fixed; top: 0; left: 0; width: 100vw; height: 100dvh; background: rgba(0,0,0,0.6); backdrop-filter: blur(4px); z-index: 999999; justify-content: center; align-items: center; opacity: 0; transition: opacity 0.3s ease; pointer-events: auto; }}
    .report-modal-overlay.show {{ display: flex; opacity: 1; }}
    .report-modal {{ background: #282a2d; width: 90%; max-width: 400px; border-radius: 16px; padding: 24px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); border: 1px solid #444746; transform: translateY(20px); transition: transform 0.3s ease; }}
    .report-modal-overlay.show .report-modal {{ transform: translateY(0); }}
    .report-modal h3 {{ color: #e3e3e3; margin: 0 0 20px 0; font-size: 18px; font-weight: 600; text-align: center; }}
    
    .date-input-group {{ display: flex; flex-direction: column; gap: 4px; flex: 1; }}
    .date-input-group label {{ color: #babbbe; font-size: 13px; font-weight: 400; }}
    .date-input {{ width: 100%; padding: 10px 14px; border-radius: 8px; border: none !important; background: transparent !important; background-color: transparent !important; color: #e3e3e3 !important; font-size: 14px; outline: none !important; font-family: 'Prompt', sans-serif; color-scheme: dark; transition: 0.2s; -webkit-appearance: none; box-shadow: inset 0 0 0 1px #5f6368; }}
    .date-input::placeholder {{ color: #9aa0a6; font-weight: 400; opacity: 1; }}
    .date-input:focus {{ box-shadow: inset 0 0 0 2px #8ab4f8 !important; }}
    .date-flex-container {{ display: flex; gap: 12px; margin-bottom: 8px; }}
    .report-note {{ font-size: 11.5px; color: #9aa0a6; margin-bottom: 20px; text-align: center; }}
    
    .report-btn-group {{ display: flex; gap: 12px; }}
    .report-btn {{ flex: 1; padding: 12px; border-radius: 8px; border: none; font-size: 15px; font-weight: 600; cursor: pointer; transition: 0.2s; display: flex; justify-content: center; align-items: center; gap: 8px; }}
    .report-btn-cancel {{ background: transparent; color: #8ab4f8; border: 1px solid #8ab4f8; }}
    .report-btn-cancel:hover {{ background: rgba(138, 180, 248, 0.1); }}
    .report-btn-dl {{ background: #1a73e8; color: white; }}
    .report-btn-dl:hover {{ background: #1557b0; }}
    .report-btn-dl:disabled {{ background: #5f6368; cursor: not-allowed; }}

    .custom-info-panel {{ display: none; position: fixed; top: 74px; left: 16px; width: 360px; max-width: calc(100vw - 32px); max-height: calc(100dvh - 88px); background: #fff; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.25); z-index: 99999; flex-direction: column; overflow: hidden; animation: slideDownFade 0.2s ease-out; pointer-events: auto; }}

    @media (max-width: 768px) {{ 
        .g-search-container {{ width: calc(100vw - 146px) !important; max-width: none !important; }} 
        .custom-filter-wrapper {{ top: calc(max(20px, env(safe-area-inset-top, 20px)) + 60px) !important; right: 16px !important; left: 16px !important; width: auto !important; max-height: calc(100dvh - 110px) !important; }}
        .custom-info-panel {{ 
            top: auto !important; bottom: 0 !important; left: 0 !important; 
            width: 100vw !important; max-width: 100vw !important; 
            max-height: 55vh !important; 
            border-radius: 20px 20px 0 0 !important; 
            animation: slideUpFade 0.3s ease-out; 
            border-bottom: none !important;
            padding-bottom: env(safe-area-inset-bottom, 0px);
        }} 
        .popup-body {{ max-height: calc(55vh - 125px - env(safe-area-inset-bottom, 0px)) !important; }} 
        .date-flex-container {{ flex-direction: column; }}
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
    
    .g-search-input {{ flex: 1; border: none !important; outline: none !important; background: transparent !important; background-color: transparent !important; font-size: 14px; color: #e8eaed !important; margin-left: 10px; width: 100%; font-family: 'Prompt', sans-serif; pointer-events: auto; box-shadow: none !important; -webkit-appearance: none; }}

    .g-layer-container {{ position: absolute; bottom: calc(24px + env(safe-area-inset-bottom, 0px)); left: 16px; z-index: 9999; display: flex; align-items: flex-end; font-family: 'Prompt', sans-serif; pointer-events: none; }}
    .g-layer-main-btn {{ pointer-events: auto; width: 56px; height: 56px; border-radius: 12px; border: 2px solid rgba(255,255,255,0.8); box-shadow: 0 4px 12px rgba(0,0,0,0.3); background-size: cover; background-position: center; cursor: pointer; position: relative; overflow: hidden; transition: all 0.2s ease; }}
    .g-layer-main-btn:hover {{ transform: scale(1.05); box-shadow: 0 6px 16px rgba(0,0,0,0.4); }}
    .g-layer-label {{ position: absolute; bottom: 0; left: 0; right: 0; background: rgba(0,0,0,0.6); color: #fff; font-size: 10px; text-align: center; padding: 4px 0; font-weight: 500; backdrop-filter: blur(2px); }}
    .g-layer-panel {{ pointer-events: auto; background: rgba(40, 42, 45, 0.95); border-radius: 16px; display: flex; gap: 12px; padding: 0; max-width: 0; overflow: hidden; opacity: 0; transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1); height: 80px; align-items: center; margin-left: 12px; border: 1px solid rgba(255,255,255,0.1); box-shadow: 0 8px 24px rgba(0,0,0,0.4); backdrop-filter: blur(8px); }}
    .g-layer-container:hover .g-layer-panel {{ max-width: 500px; padding: 0 20px; opacity: 1; }}
    .g-layer-item {{ display: flex; flex-direction: column; align-items: center; cursor: pointer; gap: 6px; padding: 4px; border-radius: 10px; transition: background-color 0.2s; }}
    .g-layer-item:hover {{ background-color: rgba(255,255,255,0.1); }}
    .g-layer-thumb {{ width: 44px; height: 44px; border-radius: 10px; border: 2px solid transparent; background-size: cover; background-position: center; box-shadow: 0 2px 6px rgba(0,0,0,0.2); transition: all 0.2s; }}
    .g-layer-item.active .g-layer-thumb {{ border-color: #8ab4f8; box-shadow: 0 0 0 2px rgba(138,180,248,0.4); transform: scale(1.05); }}
    .g-layer-name {{ font-size: 11px; color: #e8eaed; font-weight: 500; white-space: nowrap; }}
    </style>

    <!-- ปุ่ม Filter และ Report -->
    <div id="standaloneReportBtn" class="top-action-btn standalone-report-btn" title="ดาวน์โหลดรายงาน Excel">
        <svg viewBox="0 0 24 24"><path d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/></svg>
    </div>
    <div id="standaloneFilterBtn" class="top-action-btn standalone-filter-btn" title="ตัวกรองสถานะ"><svg viewBox="0 0 24 24"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"></polygon></svg></div>
    <div id="customFilterWrapper" class="custom-filter-wrapper"></div>

    <div id="customInfoPanel" class="custom-info-panel">
        <div class="panel-close-btn" id="closeInfoPanelBtn"><svg viewBox="0 0 24 24" width="18" height="18" fill="white"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg></div>
        <div id="customInfoContent" style="display:flex; flex-direction:column; height:100%; width:100%;"></div>
    </div>

    <!-- Modal แจ้งเตือนแบบ Custom -->
    <div id="customAlertOverlay" class="custom-alert-overlay">
        <div class="custom-alert-box">
            <div class="custom-alert-icon">
                <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
            </div>
            <div class="custom-alert-title">ไม่พบข้อมูล</div>
            <div class="custom-alert-msg" id="customAlertMsg">ไม่พบข้อมูลในช่วงวันที่ที่คุณเลือกครับ</div>
            <button class="custom-alert-btn" onclick="document.getElementById('customAlertOverlay').classList.remove('show');">ตกลง</button>
        </div>
    </div>

    <!-- Modal สำหรับเลือก Report Range (แก้ไขให้แสดง Placeholder บนมือถือ) -->
    <div id="reportModalOverlay" class="report-modal-overlay">
        <div class="report-modal">
            <h3>ดาวน์โหลดรายงาน</h3>
            <div class="date-flex-container">
                <div class="date-input-group">
                    <label>จากวันที่ :</label>
                    <input type="text" id="reportStartDate" class="date-input" placeholder="วว/ดด/ปปปป (ค.ศ.)" onfocus="(this.type='date')" onblur="if(!this.value) this.type='text'">
                </div>
                <div class="date-input-group">
                    <label>ถึงวันที่ :</label>
                    <input type="text" id="reportEndDate" class="date-input" placeholder="วว/ดด/ปปปป (ค.ศ.)" onfocus="(this.type='date')" onblur="if(!this.value) this.type='text'">
                </div>
            </div>
            <div class="report-note">*หากไม่ระบุวันที่ ระบบจะดาวน์โหลดข้อมูลทั้งหมด</div>
            <div class="report-btn-group">
                <button class="report-btn report-btn-cancel" onclick="closeReportModal()">ยกเลิก</button>
                <button id="execReportBtn" class="report-btn report-btn-dl" onclick="generateExcelReport()">
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="white"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg> โหลด Excel
                </button>
            </div>
        </div>
    </div>

    <div class="g-search-container">
        <div class="g-search-box" id="searchBox">
            <div class="g-search-icon"><svg focusable="false" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" style="width:20px; height:20px;"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"></path></svg></div>
            <input type="text" id="searchInput" class="g-search-input" placeholder="ค้นหา Site ID, รหัสสั่งการ, หรือ สถานที่..." autocomplete="off">
            <div class="g-search-clear" id="searchClear">×</div>
        </div>
        <div id="searchResults" class="g-search-results"></div>
    </div>

    <!-- กู้คืนปุ่มสลับแผนที่ที่ทำหายไป ทำให้ JS ทั้งหมดกลับมาทำงานได้ 100% -->
    <div class="g-layer-container" id="gLayerContainer">
        <div class="g-layer-main-btn" id="gLayerMainBtn"><div class="g-layer-label" id="gLayerMainLabel">...</div></div>
        <div class="g-layer-panel" id="gLayerPanel"></div>
    </div>

    <script>
    var statusHashMap = {status_hash_json};
    var sData = {search_json};
    var reportRawData = {report_json_data};
    var currentHoveredSelector = null;
    window.currentSelectedSafeId = null;

    function showCustomAlert(msg) {{
        document.getElementById('customAlertMsg').innerText = msg;
        document.getElementById('customAlertOverlay').classList.add('show');
    }}

    var reportModal = document.getElementById('reportModalOverlay');
    var reportBtn = document.getElementById('standaloneReportBtn');

    if (reportBtn) {{
        reportBtn.addEventListener('click', function(e) {{
            e.preventDefault(); e.stopPropagation();
            var sd = document.getElementById('reportStartDate');
            var ed = document.getElementById('reportEndDate');
            sd.value = ''; sd.type = 'text';
            ed.value = ''; ed.type = 'text';
            reportModal.classList.add('show');
        }});
    }}

    function closeReportModal() {{ reportModal.classList.remove('show'); }}

    async function generateExcelReport() {{
        var btn = document.getElementById('execReportBtn');
        btn.disabled = true; btn.innerHTML = 'กำลังสร้าง...';
        
        var startInput = document.getElementById('reportStartDate').value;
        var endInput = document.getElementById('reportEndDate').value;
        
        var filteredData = reportRawData.filter(d => {{
            if (!startInput && !endInput) return true; 
            if (!d.iso_date || d.iso_date === "") return false; 
            
            var parts = d.iso_date.split('-');
            var dDate = new Date(parts[0], parts[1] - 1, parts[2]); 
            
            var sDate = startInput ? new Date(startInput) : new Date(1970, 0, 1);
            var eDate = endInput ? new Date(endInput) : new Date(2100, 0, 1);
            
            sDate.setHours(0,0,0,0);
            eDate.setHours(23,59,59,999);

            return dDate >= sDate && dDate <= eDate;
        }});
        
        if (filteredData.length === 0) {{ 
            showCustomAlert("ไม่พบข้อมูลรายงานในช่วงวันที่ ที่คุณเลือกครับ"); 
            btn.disabled = false; 
            btn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="white"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg> โหลด Excel'; 
            return; 
        }}

        filteredData.sort(function(a, b) {{
            var sA = a.status.toUpperCase(); var sB = b.status.toUpperCase();
            if (sA.includes("ONLINE") && !sB.includes("ONLINE")) return -1;
            if (!sA.includes("ONLINE") && sB.includes("ONLINE")) return 1;
            return sA.localeCompare(sB);
        }});

        var workbook = new ExcelJS.Workbook();
        var ws = workbook.addWorksheet('Report');

        ws.mergeCells('A1:G1');
        var titleCell = ws.getCell('A1');
        titleCell.value = "รายละเอียดการตรวจสอบ/แก้ไขอุปกรณ์ FDCU OFFLine";
        titleCell.font = {{ name: 'TH SarabunPSK', size: 20, bold: true }};
        titleCell.alignment = {{ vertical: 'middle', horizontal: 'center' }};

        var headers = ['ลำดับ', 'รหัสสั่งการ\\n(ข้อมูล GIS)', 'Site ID', 'สถานที่\\n(ข้อมูล GIS)', 'รายละเอียดการตรวจสอบ/แก้ไข', 'ผู้รับผิดชอบและดำเนินการแก้ไขต่อไป', 'สถานะหลังดำเนินการ'];
        var headerRow = ws.addRow(headers);
        headerRow.eachCell((cell) => {{
            cell.font = {{ name: 'TH SarabunPSK', size: 16, bold: true }};
            cell.alignment = {{ vertical: 'middle', horizontal: 'center', wrapText: true }};
            cell.border = {{ top: {{style:'thin'}}, left: {{style:'thin'}}, bottom: {{style:'thin'}}, right: {{style:'thin'}} }};
        }});

        var startMergeRow = 3; 
        var currentStatus = filteredData[0].status;

        filteredData.forEach((row, i) => {{
            var dataRow = ws.addRow([ i+1, row.cmd, row.site, row.loc, row.detail, row.resp, row.status ]);
            dataRow.eachCell((cell, colNumber) => {{
                cell.font = {{ name: 'TH SarabunPSK', size: 16 }};
                cell.border = {{ top: {{style:'thin'}}, left: {{style:'thin'}}, bottom: {{style:'thin'}}, right: {{style:'thin'}} }};
                if (colNumber === 5) {{ cell.alignment = {{ vertical: 'top', horizontal: 'left', wrapText: true }}; }} 
                else {{ cell.alignment = {{ vertical: 'middle', horizontal: 'center', wrapText: true }}; }}
            }});

            var currentRow = i + 3;
            var nextStatus = (i < filteredData.length - 1) ? filteredData[i+1].status : null;
            
            if (nextStatus !== currentStatus || i === filteredData.length - 1) {{
                if (currentRow > startMergeRow) {{
                    ws.mergeCells(`G${{startMergeRow}}:G${{currentRow}}`);
                }}
                startMergeRow = currentRow + 1;
                currentStatus = nextStatus;
            }}
        }});

        ws.getColumn(1).width = 6;
        ws.getColumn(2).width = 18;
        ws.getColumn(3).width = 18;
        ws.getColumn(4).width = 25;
        ws.getColumn(5).width = 60;
        ws.getColumn(6).width = 30;
        ws.getColumn(7).width = 20;

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
        link.download = 'SCADA_Report_' + safeFilenameDate + '.xlsx';
        
        document.body.appendChild(link); link.click(); document.body.removeChild(link);

        btn.disabled = false; btn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="white"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg> โหลด Excel';
        closeReportModal();
    }}

    var filterBtn = document.getElementById('standaloneFilterBtn');
    var filterWrapper = document.getElementById('customFilterWrapper');

    if (filterBtn && filterWrapper) {{
        filterBtn.addEventListener('click', function(e) {{ 
            e.preventDefault(); 
            e.stopPropagation(); 
            filterWrapper.classList.toggle('show'); 
        }});
        
        document.addEventListener('click', function(e) {{ 
            if (filterWrapper.classList.contains('show')) {{ 
                if (!filterWrapper.contains(e.target) && !filterBtn.contains(e.target)) {{ 
                    filterWrapper.classList.remove('show'); 
                }} 
            }} 
        }});
        
        L.DomEvent.disableClickPropagation(filterBtn);
        L.DomEvent.disableClickPropagation(filterWrapper);
        filterWrapper.addEventListener('wheel', function(e) {{ e.stopPropagation(); }}, {{passive: false}});
        filterWrapper.addEventListener('touchmove', function(e) {{ e.stopPropagation(); }}, {{passive: false}});
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
                attempts++;
                if (attempts > 15) clearInterval(tryHighlight);
            }}, 100);
        }}
    }}

    function clearHighlight() {{ 
        document.querySelectorAll('.selected-pin-glow').forEach(function(el) {{ el.classList.remove('selected-pin-glow'); }}); 
        window.currentSelectedSafeId = null; 
        document.body.classList.remove('filter-hover-active');
    }}

    function reapplyHighlight() {{
        if (currentHoveredSelector && document.body.classList.contains('filter-hover-active')) {{
            requestAnimationFrame(function() {{ document.querySelectorAll(currentHoveredSelector).forEach(function(el) {{ el.classList.add('highlight-active'); if(el.parentElement) el.parentElement.style.zIndex = 9999; }}); }});
        }}
        if(window.currentSelectedSafeId) {{ highlightPin(window.currentSelectedSafeId); }}
    }}

    var checkMapReady = setInterval(function() {{
        var targetForm = null;
        document.querySelectorAll('.leaflet-control-layers form').forEach(function(f) {{
            if (f.querySelector('.leaflet-control-layers-group')) {{
                targetForm = f;
            }}
        }});
        
        var globalMap = null;
        for (var key in window) {{ if (key.startsWith('map_')) {{ globalMap = window[key]; break; }} }}

        if (targetForm && globalMap && filterWrapper) {{
            clearInterval(checkMapReady); 
            
            filterWrapper.addEventListener('mouseenter', function () {{ globalMap.scrollWheelZoom.disable(); }});
            filterWrapper.addEventListener('mouseleave', function () {{ globalMap.scrollWheelZoom.enable(); }});
            
            filterWrapper.appendChild(targetForm);
            
            var controlList = filterWrapper.querySelector('.leaflet-control-layers-overlays');
            if (controlList) {{
                var groups = Array.from(controlList.querySelectorAll('.leaflet-control-layers-group'));
                groups.forEach(function(group) {{
                    var itemLabels = Array.from(group.querySelectorAll('label:not(.leaflet-control-layers-group-label)'));
                    
                    itemLabels.sort(function(a, b) {{
                        var snA = a.querySelector('.status-text');
                        var snB = b.querySelector('.status-text');
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
                        var weightA = getWeight(sA);
                        var weightB = getWeight(sB);
                        if (weightA !== weightB) return weightA - weightB;
                        if (sA.length !== sB.length) return sA.length - sB.length;
                        return sA.localeCompare(sB);
                    }});
                    itemLabels.forEach(function(label) {{ group.appendChild(label); }});
                }});
            }}

            filterWrapper.querySelectorAll('.leaflet-control-layers-selector').forEach(function(cb) {{ 
                cb.addEventListener('change', reapplyHighlight); 
            }});

            filterWrapper.querySelectorAll('label').forEach(function(lbl) {{
                lbl.addEventListener('mouseenter', function() {{
                    var statusNode = this.querySelector('.status-text');
                    var targetStr = statusNode ? statusNode.getAttribute('data-status') : this.textContent.replace(/●/g,'').trim();
                    currentHoveredSelector = ".s_" + statusHashMap[targetStr];
                    if (statusHashMap[targetStr]) {{ document.body.classList.add('filter-hover-active'); document.querySelectorAll(currentHoveredSelector).forEach(function(el) {{ el.classList.add('highlight-active'); if(el.parentElement) el.parentElement.style.zIndex = 9999; }}); }}
                }});
                lbl.addEventListener('mouseleave', function() {{
                    currentHoveredSelector = null; 
                    if (!window.currentSelectedSafeId) {{ document.body.classList.remove('filter-hover-active'); }}
                    document.querySelectorAll('.highlight-active').forEach(function(el) {{ el.classList.remove('highlight-active'); if(el.parentElement) el.parentElement.style.zIndex = ''; }});
                }});
            }});

            filterWrapper.querySelectorAll('.leaflet-control-layers-group-label').forEach(function(lbl) {{
                lbl.title = 'คลิกเพื่อ เลือก/ยกเลิก ทั้งหมดในกลุ่มนี้';
                lbl.addEventListener('click', function(e) {{
                    e.preventDefault(); e.stopPropagation();
                    var parentGroup = this.closest('.leaflet-control-layers-group') || this.parentElement.parentElement;
                    if (!parentGroup) return;
                    var checkboxes = parentGroup.querySelectorAll('.leaflet-control-layers-selector');
                    if (checkboxes.length === 0) return;
                    var allChecked = true;
                    checkboxes.forEach(function(cb) {{ if (!cb.checked) allChecked = false; }});
                    checkboxes.forEach(function(cb) {{ if (cb.checked !== !allChecked) {{ cb.click(); }} }});
                    reapplyHighlight(); 
                }});
                lbl.addEventListener('mouseenter', function() {{
                    var parentNode = this.querySelector('.parent-text');
                    if(parentNode) {{
                        var parentStr = parentNode.getAttribute('data-parent');
                        currentHoveredSelector = ".p_" + statusHashMap[parentStr];
                        if(statusHashMap[parentStr]) {{ document.body.classList.add('filter-hover-active'); document.querySelectorAll(currentHoveredSelector).forEach(function(el) {{ el.classList.add('highlight-active'); if(el.parentElement) el.parentElement.style.zIndex = 9999; }}); }}
                    }}
                }});
                lbl.addEventListener('mouseleave', function() {{
                    currentHoveredSelector = null; 
                    if (!window.currentSelectedSafeId) {{ document.body.classList.remove('filter-hover-active'); }}
                    document.querySelectorAll('.highlight-active').forEach(function(el) {{ el.classList.remove('highlight-active'); if(el.parentElement) el.parentElement.style.zIndex = ''; }});
                }});
            }});
        }}
    }}, 100); 

    setTimeout(function() {{
        var globalMap = null;
        for (var key in window) {{ if (key.startsWith('map_')) {{ globalMap = window[key]; break; }} }}
        if (globalMap) {{

            globalMap.on('click dragstart popupopen', function() {{
                var fw = document.getElementById('customFilterWrapper');
                if (fw) fw.classList.remove('show');
            }});

            globalMap.on('popupopen', function(e) {{
                var content = e.popup.getContent();
                var htmlStr = typeof content === 'string' ? content : content.innerHTML;
                document.getElementById('customInfoContent').innerHTML = htmlStr;
                document.getElementById('customInfoPanel').style.display = 'flex';
                var tempDiv = document.createElement('div'); tempDiv.innerHTML = htmlStr;
                var card = tempDiv.querySelector('.custom-popup-card');
                if(card) {{ highlightPin(card.getAttribute('data-safe-id')); }}

                var targetZoom = 18;
                var isMobile = window.innerWidth <= 768;
                var offsetX = isMobile ? 0 : -180;
                var offsetY = isMobile ? 120 : 0; 
                var targetLatlng = e.popup.getLatLng();
                var targetPoint = globalMap.project(targetLatlng, targetZoom);
                targetPoint.x += offsetX; targetPoint.y += offsetY;
                var newCenter = globalMap.unproject(targetPoint, targetZoom);
                globalMap.flyTo(newCenter, targetZoom, {{animate: true, duration: 0.8}});
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

            if(document.getElementById('customZoomIn')) document.getElementById('customZoomIn').onclick = function(e) {{ e.stopPropagation(); globalMap.zoomIn(); }};
            if(document.getElementById('customZoomOut')) document.getElementById('customZoomOut').onclick = function(e) {{ e.stopPropagation(); globalMap.zoomOut(); }};

            var myLocMarker = null; var userLatLng = null; var pendingFlyToLoc = false;
            globalMap.locate({{watch: true, setView: false, enableHighAccuracy: true}});
            
            globalMap.on('locationfound', function(e) {{
                userLatLng = e.latlng;
                if (!myLocMarker) {{
                    myLocMarker = L.marker(e.latlng, {{ icon: L.divIcon({{ className: '', html: '<div class="my-location-container"><div class="my-location-cone"></div><div class="my-location-dot"></div></div>', iconSize: [60,60], iconAnchor: [30,30] }}), zIndexOffset: 9999 }}).addTo(globalMap);
                }} else {{ myLocMarker.setLatLng(e.latlng); }}
                if (pendingFlyToLoc) {{ globalMap.flyTo(userLatLng, 16); pendingFlyToLoc = false; }}
            }});
            
            if(document.getElementById('locTargetBtn')) {{
                document.getElementById('locTargetBtn').onclick = function(e) {{
                    e.preventDefault(); e.stopPropagation();
                    if (userLatLng) {{ globalMap.flyTo(userLatLng, 16); }} else {{ pendingFlyToLoc = true; }}
                }};
            }}
        }}
    }}, 1200);

    document.getElementById('closeInfoPanelBtn').onclick = function(e) {{ e.stopPropagation(); hideCustomPanel(); }};

    var mapConfigs = [
        {{ id: "terrain", name: "ภูมิประเทศ", keyword: "Google Terrain", thumb: "https://mt1.google.com/vt/lyrs=p&x=130&y=119&z=8" }},
        {{ id: "street", name: "แผนที่ถนน", keyword: "Street Map", thumb: "https://mt1.google.com/vt/lyrs=m&x=130&y=119&z=8" }},
        {{ id: "satellite", name: "ดาวเทียม", keyword: "Google Satellite", thumb: "https://mt1.google.com/vt/lyrs=s&x=130&y=119&z=8" }},
        {{ id: "hybrid", name: "ดาวเทียม+ถนน", keyword: "Google Hybrid", thumb: "https://mt1.google.com/vt/lyrs=y&x=130&y=119&z=8" }},
        {{ id: "esri", name: "Esri", keyword: "Esri World Imagery", thumb: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/8/119/130" }}
    ];

    var currentMapIndex = 0; 
    var panel = document.getElementById('gLayerPanel');
    mapConfigs.forEach(function(conf, idx) {{
        var div = document.createElement('div'); div.className = 'g-layer-item';
        div.innerHTML = '<div class="g-layer-thumb" style="background-image: url(' + conf.thumb + ')"></div><div class="g-layer-name">' + conf.name + '</div>';
        div.onclick = function(e) {{ e.stopPropagation(); switchMapLayer(idx); }};
        panel.appendChild(div);
    }});

    function getRadioByKeyword(kw) {{
        var labels = document.querySelectorAll('.leaflet-control-layers-base label');
        for(var i=0; i<labels.length; i++) {{ if(labels[i].innerHTML.includes(kw)) return labels[i].querySelector('input[type="radio"]'); }}
        return null;
    }}

    function switchMapLayer(idx) {{
        currentMapIndex = idx; 
        var radio = getRadioByKeyword(mapConfigs[idx].keyword); 
        if (radio && !radio.checked) {{
            radio.checked = true;
            radio.dispatchEvent(new Event('change', {{ bubbles: true }}));
            radio.dispatchEvent(new MouseEvent('click', {{ bubbles: true }}));
        }}
        
        panel.querySelectorAll('.g-layer-item').forEach(function(item, i) {{ 
            if(i === idx) item.classList.add('active'); 
            else item.classList.remove('active'); 
        }});
        
        document.getElementById('gLayerMainBtn').style.backgroundImage = 'url(' + mapConfigs[idx].thumb + ')';
        document.getElementById('gLayerMainLabel').innerText = mapConfigs[idx].name;
    }}

    document.getElementById('gLayerMainBtn').onclick = function(e) {{ 
        e.stopPropagation(); 
        switchMapLayer((currentMapIndex + 1) % mapConfigs.length); 
    }};

    setTimeout(function() {{ switchMapLayer(0); }}, 800);

    var box = document.getElementById('searchBox');
    var inp = document.getElementById('searchInput');
    var res = document.getElementById('searchResults');
    var clr = document.getElementById('searchClear');

    function handleSearchFocus() {{ 
        box.classList.add('focus'); 
        hideCustomPanel(); 
        var fw = document.getElementById('customFilterWrapper');
        if (fw) fw.classList.remove('show');
        triggerSearch(); 
    }}

    function triggerSearch() {{
        var val = inp.value.toLowerCase().trim();
        clr.style.display = val.length > 0 ? 'block' : 'none';
        res.innerHTML = '';
        if (val.length < 1) {{ res.style.display = 'none'; return; }}
        
        var matches = sData.filter(function(i) {{ return i.id.toLowerCase().includes(val) || i.code.toLowerCase().includes(val) || i.name.toLowerCase().includes(val); }}).slice(0, 8);
        
        if (matches.length > 0) {{
            res.style.display = 'block';
            matches.forEach(function(m) {{
                var div = document.createElement('div'); div.className = 'g-search-item';
                div.innerHTML = `<div style="display: flex; flex-direction: column; width: 100%; gap: 6px;">
                                    <div style="display: flex; justify-content: space-between; align-items: flex-start; width: 100%; gap: 8px;">
                                        <span style="color: #e3e3e3; font-size: 14.5px; font-weight: 600; line-height: 1.2;">${{m.id}}</span>
                                        ${{m.code ? `<span style="color: #8ab4f8; font-size: 11px; font-weight: 500; background: rgba(138, 180, 248, 0.1); padding: 2px 6px; border-radius: 4px; border: 1px solid rgba(138, 180, 248, 0.2); flex-shrink: 0; margin-top: 1px;">${{m.code}}</span>` : ''}}
                                    </div>
                                    <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; width: 100%;">
                                        <span style="background-color: ${{m.color}}; color: #fff; padding: 3px 8px; border-radius: 12px; font-size: 10px; font-weight: 500; line-height: 1.2; box-shadow: 0 1px 3px rgba(0,0,0,0.3);">${{m.status}}</span>
                                        <span style="color: #babbbe; font-size: 11.5px; display: flex; align-items: flex-start; gap: 4px; line-height: 1.3; flex: 1; min-width: 120px;">
                                            <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" style="flex-shrink: 0; margin-top: 1px;"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg>
                                            <span style="word-break: break-word;">${{m.name ? m.name : 'ไม่มีชื่อสถานที่'}}</span>
                                        </span>
                                    </div>
                                 </div>`;
                                 
                div.onclick = function() {{
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
                            map.flyTo(newCenter, targetZoom, {{animate: true, duration: 0.8}});
                            
                            document.getElementById('customInfoContent').innerHTML = m.popup;
                            document.getElementById('customInfoPanel').style.display = 'flex';
                            highlightPin(m.safe_id);
                        }} 
                    }}
                    res.style.display = 'none'; 
                }};
                res.appendChild(div);
            }});
        }} else {{ res.style.display = 'none'; }}
    }}

    inp.addEventListener('keyup', triggerSearch);
    inp.addEventListener('focus', handleSearchFocus); 
    inp.addEventListener('click', handleSearchFocus); 
    inp.addEventListener('blur', function() {{ box.classList.remove('focus'); setTimeout(function(){{ res.style.display = 'none'; }}, 200); }});
    clr.addEventListener('click', function() {{ inp.value = ''; res.innerHTML = ''; res.style.display = 'none'; this.style.display = 'none'; hideCustomPanel(); inp.focus(); }});
    </script>
    """
    m.get_root().html.add_child(folium.Element(custom_ui_html))
    return m.get_root().render()

def background_task():
    try:
        print("กำลังดึงข้อมูลและสร้างแผนที่เบื้องหลัง...")
        new_html = generate_map()
        tmp_html = CACHE_HTML_FILE + '.tmp'
        with open(tmp_html, 'w', encoding='utf-8') as f: f.write(new_html)
        os.replace(tmp_html, CACHE_HTML_FILE)
        new_version = int(time.time())
        tmp_meta = CACHE_META_FILE + '.tmp'
        with open(tmp_meta, 'w') as f: json.dump({'version': new_version, 'last_update': time.time()}, f)
        os.replace(tmp_meta, CACHE_META_FILE)
        print(f"อัปเดตแผนที่เสร็จสมบูรณ์! (เวอร์ชัน {new_version})")
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการรันเบื้องหลัง: {e}")
        # --- UI โชว์ Error อัตโนมัติ (แก้แอปค้างหน้าโหลด) ---
        error_html = f"""
        <div style='display:flex;flex-direction:column;justify-content:center;align-items:center;height:100vh;background:#282a2d;color:white;font-family:sans-serif;'>
            <h2 style='color:#d33d2a;'>เกิดข้อผิดพลาดในการโหลดข้อมูลแผนที่</h2>
            <p style='color:#9aa0a6;max-width:80%;text-align:center;'>{str(e)}</p>
            <p style='color:#8ab4f8;font-size:14px;margin-top:20px;cursor:pointer;' onclick='location.reload()'>↻ รีเฟรชหน้าเว็บใหม่อีกครั้ง</p>
        </div>
        """
        tmp_html = CACHE_HTML_FILE + '.tmp'
        with open(tmp_html, 'w', encoding='utf-8') as f: f.write(error_html)
        os.replace(tmp_html, CACHE_HTML_FILE)
        new_version = int(time.time())
        tmp_meta = CACHE_META_FILE + '.tmp'
        with open(tmp_meta, 'w') as f: json.dump({'version': new_version, 'last_update': time.time()}, f)
        os.replace(tmp_meta, CACHE_META_FILE)
    finally:
        if os.path.exists(LOCK_FILE):
            try: os.remove(LOCK_FILE)
            except: pass

def trigger_update_if_needed():
    meta = get_meta()
    if time.time() - meta['last_update'] > CACHE_DURATION:
        if os.path.exists(LOCK_FILE) and (time.time() - os.path.getmtime(LOCK_FILE) > 300):
            try: os.remove(LOCK_FILE)
            except: pass
        if not os.path.exists(LOCK_FILE):
            try:
                open(LOCK_FILE, 'w').close()
                threading.Thread(target=background_task).start()
            except: pass

@app.route('/map-data')
def map_data():
    try:
        with open(CACHE_HTML_FILE, 'r', encoding='utf-8') as f: return f.read()
    except:
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
            .layer-active { z-index: 2; opacity: 1; transition: opacity 0.8s ease-in-out; }
            .layer-hidden { z-index: 1; opacity: 0; pointer-events: none; }
            #loader { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: white; z-index: 0; text-align: center; transition: opacity 0.5s; }
        </style>
    </head>
    <body>
        <div id="loader"><h2 id="loading-text">กำลังเตรียมข้อมูล SCADA...</h2><p>รอการเชื่อมต่อแผนที่ครั้งแรก</p></div>
        <iframe id="layer1" class="map-layer layer-hidden" src="about:blank"></iframe>
        <iframe id="layer2" class="map-layer layer-hidden" src="about:blank"></iframe>

        <script>
            var currentVersion = 0; 
            var activeLayer = 1;

            var dotCount = 0;
            setInterval(() => {
                var loaderText = document.getElementById('loading-text');
                if(loaderText && currentVersion === 0) {
                    dotCount = (dotCount + 1) % 4;
                    loaderText.innerText = "กำลังเตรียมข้อมูล SCADA" + ".".repeat(dotCount);
                }
            }, 500);

            window.addEventListener('resize', () => {
                document.querySelectorAll('iframe').forEach(ifr => {
                    var map = getMapInstance(ifr);
                    if(map) setTimeout(() => map.invalidateSize(), 200);
                });
            });
            window.addEventListener('orientationchange', () => {
                document.querySelectorAll('iframe').forEach(ifr => {
                    var map = getMapInstance(ifr);
                    if(map) setTimeout(() => map.invalidateSize(), 300);
                });
            });

            function getMapInstance(iframe) {
                try {
                    var win = iframe.contentWindow;
                    for (var key in win) { if (key.startsWith('map_')) return win[key]; }
                } catch(e) {}
                return null;
            }

            function checkUpdate() {
                fetch('/api/version?t=' + new Date().getTime(), { cache: 'no-store' })
                    .then(res => res.json())
                    .then(data => {
                        if (data.version > 0 && data.version !== currentVersion) {
                            currentVersion = data.version;
                            swapMap();
                        }
                    }).catch(e => console.log(e));
            }

            function swapMap() {
                var nextLayer = activeLayer === 1 ? 2 : 1;
                var activeIframe = document.getElementById('layer' + activeLayer);
                var nextIframe = document.getElementById('layer' + nextLayer);

                nextIframe.src = '/map-data?v=' + currentVersion + '&t=' + new Date().getTime();

                nextIframe.onload = function() {
                    var checkReady = setInterval(function() {
                        var newMap = getMapInstance(nextIframe);
                        var oldMap = getMapInstance(activeIframe);
                        
                        // ถ้าระบบส่ง Error Page มาให้ (ไม่มีตัวแปร Map) ให้โชว์เลย จะได้ไม่ค้าง
                        var hasErrorPage = false;
                        try {
                            hasErrorPage = nextIframe.contentWindow.document.body.innerHTML.includes('เกิดข้อผิดพลาด');
                        } catch(e) {}

                        if (newMap || hasErrorPage) {
                            clearInterval(checkReady);
                            if (oldMap && newMap) { newMap.setView(oldMap.getCenter(), oldMap.getZoom(), {animate: false}); }
                            
                            nextIframe.className = 'map-layer layer-active';
                            activeIframe.className = 'map-layer layer-hidden';
                            activeLayer = nextLayer;
                            
                            var loader = document.getElementById('loader');
                            if (loader) {
                                loader.style.opacity = '0';
                                setTimeout(() => loader.style.display = 'none', 800);
                            }

                            setTimeout(function() { activeIframe.src = 'about:blank'; }, 1500);
                        }
                    }, 200);
                    setTimeout(() => clearInterval(checkReady), 8000);
                };
            }

            var initialPoll = setInterval(() => {
                if(currentVersion === 0) checkUpdate();
                else clearInterval(initialPoll);
            }, 3000);

            setInterval(checkUpdate, 15000);
        </script>
    </body>
    </html>
    """
    return html_wrapper

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)