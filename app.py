from flask import Flask, jsonify, request
import pandas as pd
import folium
import json
import hashlib
import html
import time
import threading
import os
from folium.plugins import GroupedLayerControl, MarkerCluster

app = Flask(__name__)

# --- ระบบ Cache แบบ File-Based (ย้ายไปไว้ที่ /tmp/ เพื่อความเสถียรบน Render) ---
CACHE_HTML_FILE = '/tmp/scada_cache.html'
CACHE_META_FILE = '/tmp/scada_meta.json'
LOCK_FILE = '/tmp/updating.lock'
CACHE_DURATION = 300 # อัปเดตทุก 5 นาที

def get_meta():
    """อ่านเวอร์ชันล่าสุดจากไฟล์ เพื่อให้พนักงานทุกคนเข้าใจตรงกัน"""
    try:
        with open(CACHE_META_FILE, 'r') as f:
            return json.load(f)
    except:
        return {'version': 0, 'last_update': 0}

def get_status_config(status_text):
    status_upper = str(status_text).upper()
    if 'TELEMETRY' in status_upper: return "Telemetry Failure", "gold", "wrench"
    elif 'CONNECTING' in status_upper:
        raw_parent = "Connecting"
        if 'ผบอ.' in status_upper and 'ผอส.' in status_upper: return raw_parent, "orange", "user"
        elif 'ระบบสื่อสาร' in status_upper: return raw_parent, "purple", "wrench"
        elif 'ผบอ.' in status_upper: return raw_parent, "orange", "check"
        elif 'ผอส.' in status_upper: return raw_parent, "purple", "check" 
        else: return raw_parent, "orange", "wrench"
    elif 'OFFLINE' in status_upper:
        raw_parent = "Offline"
        if 'ผบอ.' in status_upper and 'ผอส.' in status_upper: return raw_parent, "red", "user" 
        elif 'ผบอ.' in status_upper or 'ผอส.' in status_upper: return raw_parent, "red", "check"
        else: return raw_parent, "red", "times"
    elif 'ONLINE' in status_upper: return "Online", "green", "check"
    elif 'INITIALIZING' in status_upper:
        raw_parent = "Initializing"
        if 'ผบอ.' in status_upper or 'ผอส.' in status_upper: return raw_parent, "lightgreen", "check" 
        else: return raw_parent, "lightgreen", "wrench"
    else: return "สถานะอื่นๆ", "gray", "info-circle"

def generate_map():
    """ฟังก์ชันหลักสำหรับดึง Google Sheets และวาดแผนที่"""
    print("กำลังดึงข้อมูลใหม่จาก Google Sheets...")
    sheet_id = "10QuVWnj2BCPpNqrXpBM8sbARmKGTksQ1fxUYx2Xaa8Q"
    csv_export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid=0"
    
    try:
        df = pd.read_csv(csv_export_url)
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการดึงข้อมูล: {e}")
        raise e

    status_hash_map = {}
    def get_hash(text):
        if text not in status_hash_map: status_hash_map[text] = hashlib.md5(text.encode('utf-8')).hexdigest()[:8]
        return status_hash_map[text]

    def get_actual_col_name(df_columns, keywords, exclude=None):
        for col in df_columns:
            if all(kw.lower() in col.lower() for kw in keywords):
                if exclude and any(ex.lower() in col.lower() for ex in exclude): continue
                return col
        return None

    m = folium.Map(location=[15.2282, 104.8563], zoom_start=8, zoom_control=False, tiles=None, prefer_canvas=True, max_zoom=22)

    folium.TileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', attr='Google', name='ภาพดาวเทียมล้วน (Google Satellite)', overlay=False, control=True, max_zoom=22, show=True).add_to(m)
    folium.TileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', attr='Google', name='ภาพดาวเทียม + ถนน (Google Hybrid)', overlay=False, control=True, max_zoom=22, show=False).add_to(m)
    folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr='Esri', name='ภาพดาวเทียม (Esri World Imagery)', overlay=False, control=True, max_zoom=22, show=False).add_to(m)
    folium.TileLayer('https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}', attr='Google', name='แผนที่ภูมิประเทศ (Google Terrain)', overlay=False, control=True, max_zoom=22, show=False).add_to(m)
    folium.TileLayer('OpenStreetMap', name='แผนที่ถนน (Street Map)', overlay=False, control=True, max_zoom=22, show=False).add_to(m)

    raw_parent_keys = {"Telemetry Failure": ("#ffc107", "Telemetry Failure"), "Offline": ("#d33d2a", "Offline"), "Online": ("#72b026", "Online"), "Initializing": ("#82c91e", "Initializing"), "Connecting": ("#f3943b", "Connecting"), "สถานะอื่นๆ": ("#575757", "สถานะอื่นๆ")}
    hex_color_map = {'red': '#d33d2a', 'darkred': '#8b0000', 'orange': '#f3943b', 'green': '#72b026', 'lightgreen': '#82c91e', 'blue': '#38aadd', 'darkblue': '#0067a3', 'purple': '#9b59b6', 'black': '#333333', 'gray': '#575757', 'lightgray': '#a3a3a3', 'beige': '#f5c07f', 'gold': '#ffc107'}
    display_fields = [("รหัสสั่งการ", ["รหัสสั่งการ"], []), ("สถานที่", ["สถานที่"], []), ("State SCADA", ["state scada"], ["หลัง"]), ("State SCADA (หลังตรวจสอบ)", ["state scada", "หลัง"], []), ("ชนิดอุปกรณ์", ["ชนิดอุปกรณ์"], []), ("รายละเอียดการแก้ไขข้อขัดข้อง", ["รายละเอียด", "การแก้ไข"], ["เข้า"]), ("รายละเอียดการเข้าแก้ไข", ["รายละเอียด", "การเข้าแก้ไข"], []), ("LAT/LONG", ["lat", "long"], []), ("รอ ผอส. เข้าแก้ไข", ["รอ ผอส"], []), ("รอ ผบอ. เข้าแก้ไข", ["รอ ผบอ"], []), ("การไฟฟ้า", ["การไฟฟ้า"], []), ("วันที่เข้าตรวจสอบ", ["วันที่เข้าตรวจสอบ"], [])]

    processed_nodes, status_counts, parent_counts = [], {}, {k: 0 for k in raw_parent_keys.keys()}
    seen_coords = {}

    google_svg = '<svg viewBox="0 0 24 24" width="15" height="15" fill="white" style="margin-right:6px;"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg>'
    apple_svg = '<svg viewBox="0 0 384 512" style="width: 13px; height: auto; margin-right: 6px; margin-bottom: 2px;" fill="white"><path d="M318.7 268.7c-.2-36.7 16.4-64.4 50-84.8-18.8-26.9-47.2-41.7-84.7-44.6-35.5-2.8-74.3 20.7-88.5 20.7-15 0-49.4-19.7-76.4-19.7C63.3 141.2 4 184.8 4 273.5q0 39.3 14.4 81.2c12.8 36.7 59 126.7 107.2 125.2 25.2-.6 43-17.9 75.8-17.9 31.8 0 48.3 17.9 76.4 17.3 48.6-.7 90.4-84.3 103-119.3-34.6-18.6-53.6-45.9-52.7-81.5zM266.4 87.8C282.5 67 292.7 39.2 292.7 11.5c0-3.3-.2-5.5-.6-7.8-33.1 1.4-62.7 20-80.8 42.1-15 18.5-26.4 46.1-24.9 71.6 3.3.4 5.5.6 7.8.6 30.5-.3 57.7-18.7 71.6-40.2z"/></svg>'

    for index, row in df.iterrows():
        lat_col = get_actual_col_name(df.columns, ["lat", "long"])
        lat_lng_str = str(row[lat_col]) if lat_col and pd.notna(row[lat_col]) else ''
        if not lat_lng_str or ',' not in lat_lng_str: continue
        try: lat, lon = float(lat_lng_str.split(',')[0].strip()), float(lat_lng_str.split(',')[1].strip())
        except ValueError: continue

        coord_key = (round(lat, 5), round(lon, 5)) 
        if coord_key in seen_coords:
            seen_coords[coord_key] += 1
            offsets = [(0.00005, 0.00005), (-0.00005, -0.00005), (0.00005, -0.00005), (-0.00005, 0.00005), (0.00008, 0.0), (0.0, 0.00008), (-0.00008, 0.0), (0.0, -0.00008)]
            lat += offsets[(seen_coords[coord_key] - 1) % len(offsets)][0]; lon += offsets[(seen_coords[coord_key] - 1) % len(offsets)][1]
        else: seen_coords[coord_key] = 0

        status_col = get_actual_col_name(df.columns, ["state scada"], exclude=["หลัง"])
        status_check_col = get_actual_col_name(df.columns, ["state scada", "หลัง"])
        base_status = str(row[status_col]).strip() if status_col and pd.notna(row[status_col]) else 'Unknown'
        check_status = str(row[status_check_col]).strip() if status_check_col and pd.notna(row[status_check_col]) else ''
        
        active_status = check_status if check_status and check_status.lower() not in ['nan', 'ไม่มีค่า', 'none'] else base_status
        raw_parent, color, icon_name = get_status_config(active_status)
        active_status_display = active_status.replace("Connecting ระบบสื่อสารเคยแก้ไขแล้ว กลับมา Offline", "Connecting ระบบสื่อสารเคยแก้ไขแล้ว<br>กลับมา Offline")

        status_counts[active_status] = status_counts.get(active_status, 0) + 1
        parent_counts[raw_parent] += 1
        processed_nodes.append({'row': row, 'lat': lat, 'lon': lon, 'active_status': active_status, 'active_status_display': active_status_display, 'raw_parent': raw_parent, 'color': color, 'icon_name': icon_name})

    grouped_layers = {f"<span style='display:flex; justify-content:space-between; align-items:center; width:100%; padding: 10px 16px; border-bottom: 1px solid #444746;'><span style='display:flex; align-items:center;'><span style='color:{raw_parent_keys[k][0]}; font-size:16px; margin-right:8px; line-height:1;'>●</span><span class='parent-text' data-parent='{k}' style='font-size:14px; font-weight:600; color:#e3e3e3;'>{raw_parent_keys[k][1]}</span></span><span style='color:#9aa0a6; font-size:12px;'>({parent_counts[k]})</span></span>": [] for k in raw_parent_keys.keys() if parent_counts[k] > 0}
    mc_groups, search_data, export_data_list = {}, [], []

    for node in processed_nodes:
        row, lat, lon, active_status, active_status_display = node['row'], node['lat'], node['lon'], node['active_status'], node['active_status_display']
        raw_parent, icon_name, h_color = node['raw_parent'], node['icon_name'], hex_color_map.get(node['color'], '#575757')
        
        p_html = f"<span style='display:flex; justify-content:space-between; align-items:center; width:100%; padding: 10px 16px; border-bottom: 1px solid #444746;'><span style='display:flex; align-items:center;'><span style='color:{raw_parent_keys[raw_parent][0]}; font-size:16px; margin-right:8px; line-height:1;'>●</span><span class='parent-text' data-parent='{raw_parent}' style='font-size:14px; font-weight:600; color:#e3e3e3;'>{raw_parent_keys[raw_parent][1]}</span></span><span style='color:#9aa0a6; font-size:12px;'>({parent_counts[raw_parent]})</span></span>"
        safe_status, safe_parent, safe_active_status_attr = "s_" + get_hash(active_status), "p_" + get_hash(raw_parent), html.escape(active_status)

        if active_status not in mc_groups:
            layer_name = f"<span style='display:flex; justify-content:space-between; align-items:flex-start; width:100%;'><span style='display:flex; align-items:flex-start; flex:1;'><i class='fa fa-{icon_name}' style='color:{h_color}; width:16px; text-align:center; margin-right:12px; margin-top:3px; flex-shrink:0;'></i><span class='status-text' data-status='{safe_active_status_attr}' style='font-size:13.5px; color:#e3e3e3; line-height:1.4; word-break:keep-all; overflow-wrap:break-word; text-wrap:balance;'>{active_status_display}</span></span><span style='color:#9aa0a6; font-size:12px; margin-left:8px; flex-shrink:0;'>({status_counts[active_status]})</span></span>"
            custom_cluster_js = f"function(c) {{ var count = c.getChildCount(); return new L.DivIcon({{ html: '<div class=\"map-cluster-inner {safe_status} {safe_parent}\" style=\"background-color: {h_color}; color: white; border-radius: 50%; width: 44px; height: 44px; display: flex; flex-direction: column; justify-content: center; align-items: center; font-family: Prompt, sans-serif; font-weight: 600; border: 2px solid white; box-shadow: 0 2px 5px rgba(0,0,0,0.4); text-shadow: 1px 1px 2px rgba(0,0,0,0.7); transition: all 0.3s ease;\"><i class=\"fa fa-{icon_name}\" style=\"font-size: 12px; margin-bottom: 2px;\"></i><span style=\"font-size: 13px; line-height: 1;\">' + count + '</span></div>', className: 'custom-cluster-marker', iconSize: new L.Point(44, 44), iconAnchor: new L.Point(22, 22) }}); }}"
            mc = MarkerCluster(name=layer_name, show=True, icon_create_function=custom_cluster_js, control=False, options={'disableClusteringAtZoom': 17, 'maxClusterRadius': 35, 'chunkedLoading': True})
            m.add_child(mc); mc_groups[active_status] = mc
            # [แก้ไข] เก็บ active_status คู่กับ mc ไว้เพื่อใช้เรียงลำดับได้อย่างปลอดภัย
            grouped_layers[p_html].append((active_status, mc))
        
        target_group, table_rows = mc_groups[active_status], ""
        
        site_id_col = get_actual_col_name(df.columns, ["site id"])
        site_id = str(row[site_id_col]) if site_id_col else 'Unknown'
        safe_site_id = "id_" + get_hash(site_id + str(lat))

        cmd_code_col = get_actual_col_name(df.columns, ["รหัสสั่งการ"]) or get_actual_col_name(df.columns, ["รหัสอุปกรณ์"])
        cmd_code = str(row[cmd_code_col]) if cmd_code_col and pd.notna(row[cmd_code_col]) else ''
        loc_col = get_actual_col_name(df.columns, ["สถานที่"])
        location_name = str(row[loc_col]) if loc_col and pd.notna(row[loc_col]) else ''

        export_row = {"_layerName": active_status, "Site ID": site_id} 
        
        for display_name, keywords, excludes in display_fields:
            actual_col = get_actual_col_name(df.columns, keywords, exclude=excludes) or (get_actual_col_name(df.columns, ["รหัสอุปกรณ์"]) if "รหัสสั่งการ" in keywords else None)
            val = row[actual_col] if actual_col and pd.notna(row[actual_col]) else 'ไม่มีค่า'
            if str(val).strip() == '' or str(val).lower() == 'nan' or str(val) == 'None': val = 'ไม่มีค่า'
            
            if "วันที่" in display_name and val != 'ไม่มีค่า':
                try:
                    dt = pd.to_datetime(val)
                    thai_months = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
                    val = f"{dt.day} {thai_months[dt.month]} {str(dt.year)[-2:]}"
                except: pass
                
            val_str = str(val)
            display_val = val_str.replace("Connecting ระบบสื่อสารเคยแก้ไขแล้ว กลับมา Offline", "Connecting ระบบสื่อสารเคยแก้ไขแล้ว<br>กลับมา Offline")
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

    # =========================================================================
    # [แก้ไข] ระบบจัดเรียงลำดับชั้นของ Layer อย่างปลอดภัย 100%
    # =========================================================================
    active_grouped_layers = {}
    for p_html, items_list in grouped_layers.items():
        if len(items_list) > 0:
            # Sort จากข้อความสถานะ (x[0]) เรียงจากสั้นไปยาว เช่น "Connecting" โดนดึงขึ้นมาอยู่บนสุด
            items_list.sort(key=lambda x: (len(x[0]), x[0]))
            # แยกร่างเอาเฉพาะตัว Layer (x[1]) กลับมาส่งให้ระบบ
            active_grouped_layers[p_html] = [x[1] for x in items_list]

    folium.LayerControl(position='topleft', collapsed=True).add_to(m)
    GroupedLayerControl(groups=active_grouped_layers, exclusive_groups=False, collapsed=True).add_to(m)

    search_json = json.dumps(search_data, ensure_ascii=False)
    export_json = json.dumps(export_data_list, ensure_ascii=False)
    status_hash_json = json.dumps(status_hash_map, ensure_ascii=False)

    custom_ui_html = f"""
    <link href="https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;500;600&display=swap" rel="stylesheet">
    <style>
    * {{ font-family: 'Prompt', sans-serif; outline: none !important; -webkit-tap-highlight-color: transparent !important; box-sizing: border-box; }}
    .leaflet-top {{ z-index: 999 !important; }}
    .leaflet-bottom {{ z-index: 998 !important; }}
    .leaflet-top.leaflet-left .leaflet-control-layers, .leaflet-top.leaflet-right .leaflet-control-layers {{ display: none !important; }}
    .leaflet-popup {{ display: none !important; opacity: 0 !important; pointer-events: none !important; }}
    .selected-pin-glow {{ transform: scale(1.4) !important; z-index: 100000 !important; }}
    .selected-pin-glow .pin-shape {{ box-shadow: 0 0 0 3px #ffffff, 0 0 20px 8px rgba(66, 133, 244, 0.8) !important; border-color: #4285F4 !important; }}
    .selected-pin-glow i {{ text-shadow: 0 0 5px rgba(255,255,255,0.8); }}
    body.filter-hover-active .selected-pin-glow {{ opacity: 1 !important; filter: none !important; }}

    .custom-info-panel {{ display: none; position: fixed; top: 74px; left: 16px; width: 360px; max-width: calc(100vw - 32px); max-height: calc(100dvh - 88px); background: #fff; border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,0.25); z-index: 99999; flex-direction: column; overflow: hidden; animation: slideDownFade 0.2s ease-out; }}
    @media (max-width: 768px) {{ .custom-info-panel {{ top: auto !important; bottom: 20px !important; left: 16px; width: calc(100vw - 32px); max-height: 48vh !important; animation: slideUpFade 0.3s ease-out; }} .popup-body {{ max-height: calc(48vh - 125px) !important; }} }}
    @keyframes slideDownFade {{ from {{ opacity: 0; transform: translateY(-15px); }} to {{ opacity: 1; transform: translateY(0); }} }}
    @keyframes slideUpFade {{ from {{ opacity: 0; transform: translateY(20px); }} to {{ opacity: 1; transform: translateY(0); }} }}

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

    body.filter-hover-active .map-pin-inner, body.filter-hover-active .map-cluster-inner {{ opacity: 0.2; filter: grayscale(100%); }}
    body.filter-hover-active .map-pin-inner.highlight-active, body.filter-hover-active .map-cluster-inner.highlight-active {{ opacity: 1 !important; filter: none !important; transform: scale(1.25); }}
    body.filter-hover-active .map-pin-inner.highlight-active .pin-shape, body.filter-hover-active .map-cluster-inner.highlight-active {{ box-shadow: 0 0 12px 6px rgba(255, 255, 255, 0.9), 0 0 5px rgba(0,0,0,0.5) !important; }}

    .standalone-filter-btn {{ position: fixed; right: 16px; top: 16px; width: 48px; height: 48px; background-color: #282a2d; border-radius: 50%; box-shadow: 0 2px 6px rgba(0,0,0,0.3); display: flex; align-items: center; justify-content: center; cursor: pointer; z-index: 100000; transition: transform 0.1s, background-color 0.2s; border: 1px solid #444746; }}
    .standalone-filter-btn:hover {{ background-color: #3c4043; }}
    .standalone-filter-btn:active {{ transform: scale(0.92); }}
    .standalone-filter-btn svg {{ fill: none; stroke: #e3e3e3; stroke-width: 2.2; width: 22px; height: 22px; }}

    .custom-filter-wrapper {{ display: none; flex-direction: column; position: fixed; top: 72px; right: 16px; width: 340px; max-width: calc(100vw - 32px); max-height: calc(100dvh - 90px) !important; background-color: #282a2d; border-radius: 16px; box-shadow: 0 8px 24px rgba(0,0,0,0.5); border: 1px solid #444746; overflow: hidden; z-index: 99999; }}
    .custom-filter-wrapper.show {{ display: flex; }}
    .custom-filter-wrapper form {{ display: flex !important; flex-direction: column !important; margin: 0 !important; padding: 0 !important; height: 100% !important; }}
    .custom-filter-wrapper .leaflet-control-layers-list {{ flex: 1 1 auto !important; overflow-y: auto !important; overflow-x: hidden !important; padding: 0 0 12px 0 !important; margin: 0 !important; overscroll-behavior: contain !important; }}
    .custom-filter-wrapper .leaflet-control-layers-list::-webkit-scrollbar {{ width: 6px; }}
    .custom-filter-wrapper .leaflet-control-layers-list::-webkit-scrollbar-thumb {{ background: #5f6368; border-radius: 10px; }}
    .custom-filter-wrapper .leaflet-control-layers-separator {{ display: none !important; }}
    .custom-filter-wrapper .leaflet-control-layers-group {{ display: block !important; width: 100%; margin-bottom: 8px; }}
    .custom-filter-wrapper .leaflet-control-layers-group-label {{ display: block !important; width: 100%; cursor: pointer; }}
    .custom-filter-wrapper .leaflet-control-layers-group-name {{ display: block !important; width: 100%; transition: background-color 0.2s; }}
    .custom-filter-wrapper .leaflet-control-layers-group-name:hover {{ background-color: #35363a !important; }}
    .custom-filter-wrapper label {{ display: flex !important; flex-direction: row !important; align-items: flex-start !important; width: 100% !important; padding: 6px 16px !important; margin: 0 !important; cursor: pointer; transition: background-color 0.15s; }}
    .custom-filter-wrapper label:hover {{ background-color: #35363a !important; }}
    .custom-filter-wrapper label > span {{ display: flex !important; flex-direction: row !important; align-items: flex-start !important; width: 100% !important; white-space: normal !important; }}
    .custom-filter-wrapper input[type="checkbox"] {{ flex-shrink: 0 !important; -webkit-appearance: none !important; -moz-appearance: none !important; appearance: none !important; width: 18px !important; height: 18px !important; border: 2px solid #8e918f !important; border-radius: 3px !important; margin: 2px 14px 0 0 !important; position: relative !important; cursor: pointer; outline: none; transition: all 0.2s ease; background-color: transparent !important; }}
    .custom-filter-wrapper input[type="checkbox"]:checked {{ background-color: #8ab4f8 !important; border-color: #8ab4f8 !important; }}
    .custom-filter-wrapper input[type="checkbox"]:checked::after {{ content: ''; position: absolute; top: 1px; left: 5px; width: 4px; height: 8px; border: solid #202124; border-width: 0 2px 2px 0; transform: rotate(45deg); }}

    .g-export-container {{ flex: 0 0 auto !important; padding: 16px !important; background-color: #282a2d !important; border-top: 1px solid #444746; display: flex; justify-content: center; }}
    .g-export-btn {{ background-color: #3c4043 !important; color: #e3e3e3 !important; border: none !important; border-radius: 24px !important; height: 48px !important; width: 100% !important; font-size: 14px !important; font-weight: 500 !important; cursor: pointer !important; display: flex !important; align-items: center !important; justify-content: center !important; gap: 10px !important; transition: background-color 0.2s !important; outline: none !important; font-family: 'Prompt', sans-serif !important; }}
    .g-export-btn:hover {{ background-color: #4a4d51 !important; }}
    .g-export-btn:active {{ transform: scale(0.97) !important; }}
    .g-export-btn svg {{ width: 20px !important; height: 20px !important; fill: currentColor !important; flex-shrink: 0 !important; }}

    .g-search-container {{ position: fixed; z-index: 100005; font-family: 'Prompt', sans-serif; top: 16px; left: 16px; width: 380px; margin: 0; }}
    @media (max-width: 768px) {{ .g-search-container {{ width: calc(100vw - 88px); max-width: 400px; }} }}
    .g-search-box {{ background: #282a2d; border-radius: 24px; box-shadow: 0 2px 6px rgba(0,0,0,0.3); display: flex; align-items: center; padding: 0 14px; height: 48px; border: 1px solid #444746; }}
    .g-search-box:hover, .g-search-box.focus {{ border-color: #8ab4f8; }}
    .g-search-icon {{ display: flex; align-items: center; justify-content: center; width: 24px; height: 24px; color: #9aa0a6; }}
    .g-search-input {{ flex: 1; border: none; outline: none; background: transparent; font-size: 14px; color: #e8eaed; margin-left: 10px; width: 100%; font-family: 'Prompt', sans-serif; }}
    .g-search-input::placeholder {{ color: #9aa0a6; font-weight: 400; }}
    .g-search-clear {{ display: none; color: #9aa0a6; font-size: 22px; cursor: pointer; padding: 0 8px; line-height: 1; }}
    .g-search-results {{ position: absolute; top: 54px; left: 0; width: 100%; background: #282a2d; border-radius: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.4); display: none; overflow: hidden; padding: 8px 0; max-height: 320px; overflow-y: auto; border: 1px solid #444746; }}

    .g-search-item {{ padding: 12px 16px; display: flex; flex-direction: column; cursor: pointer; border-bottom: 1px solid #35363a; transition: background-color 0.2s, border-left 0.2s; border-left: 3px solid transparent; gap: 6px; }}
    .g-search-item:last-child {{ border-bottom: none; }}
    .g-search-item:hover {{ background: #3c4043; border-left-color: #8ab4f8; }}

    @media (max-width: 768px) {{ .g-zoom-container {{ display: none !important; }} }}
    .custom-right-controls {{ display: flex; flex-direction: column; gap: 12px; align-items: center; margin-right: 16px; margin-bottom: 24px; z-index: 500 !important; position: relative; }}
    .g-zoom-container {{ width: 40px; background-color: #fff; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.3); display: flex; flex-direction: column; overflow: hidden; }}
    .g-zoom-btn {{ width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; font-size: 22px; color: #5f6368; background: transparent; cursor: pointer; user-select: none; transition: background-color 0.2s; font-weight: 400; margin: 0; border: none; }}
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

    .g-layer-container {{ position: absolute; bottom: 24px; left: 16px; z-index: 9999; display: flex; align-items: flex-end; }}
    .g-layer-main-btn {{ width: 50px; height: 50px; border-radius: 12px; border: 2px solid #202124; box-shadow: 0 2px 6px rgba(0,0,0,0.4); background-size: cover; background-position: center; cursor: pointer; position: relative; overflow: hidden; }}
    .g-layer-label {{ position: absolute; bottom: 0; left: 0; right: 0; background: rgba(0,0,0,0.7); color: #fff; font-size: 10px; text-align: center; padding: 3px 0; font-weight: 600; }}
    .g-layer-panel {{ background: #282a2d; border-radius: 12px; display: flex; gap: 12px; padding: 0; max-width: 0; overflow: hidden; opacity: 0; transition: all 0.3s ease; height: 75px; align-items: center; margin-left: 10px; border: 1px solid #444746; }}
    .g-layer-container:hover .g-layer-panel {{ max-width: 400px; padding: 0 16px; opacity: 1; }}
    .g-layer-item {{ display: flex; flex-direction: column; align-items: center; cursor: pointer; gap: 4px; }}
    .g-layer-thumb {{ width: 40px; height: 40px; border-radius: 8px; border: 2px solid transparent; background-size: cover; background-position: center; }}
    .g-layer-item.active .g-layer-thumb {{ border-color: #8ab4f8; }}
    .g-layer-name {{ font-size: 11px; color: #e8eaed; font-weight: 500; }}
    </style>

    <div id="customInfoPanel" class="custom-info-panel">
        <div class="panel-close-btn" id="closeInfoPanelBtn"><svg viewBox="0 0 24 24" width="18" height="18" fill="white"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg></div>
        <div id="customInfoContent" style="display:flex; flex-direction:column; height:100%; width:100%;"></div>
    </div>

    <div id="standaloneFilterBtn" class="standalone-filter-btn"><svg viewBox="0 0 24 24"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"></polygon></svg></div>
    <div class="g-search-container">
        <div class="g-search-box" id="searchBox">
            <div class="g-search-icon"><svg focusable="false" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" style="width:20px; height:20px;"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"></path></svg></div>
            <input type="text" id="searchInput" class="g-search-input" placeholder="ค้นหา Site ID, รหัสสั่งการ, หรือ สถานที่..." autocomplete="off">
            <div class="g-search-clear" id="searchClear">×</div>
        </div>
        <div id="searchResults" class="g-search-results"></div>
    </div>
    <div class="g-layer-container" id="gLayerContainer">
        <div class="g-layer-main-btn" id="gLayerMainBtn"><div class="g-layer-label" id="gLayerMainLabel">...</div></div>
        <div class="g-layer-panel" id="gLayerPanel"></div>
    </div>

    <script>
    var statusHashMap = {status_hash_json};
    var currentHoveredSelector = null;
    window.currentSelectedSafeId = null;

    function hideCustomPanel() {{
        var panel = document.getElementById('customInfoPanel');
        if (panel) panel.style.display = 'none';
        clearHighlight();
        for (var key in window) {{ if (key.startsWith('map_')) {{ var map = window[key]; if (map && typeof map.closePopup === 'function') {{ map.closePopup(); }} }} }}
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

    function clearHighlight() {{ document.querySelectorAll('.selected-pin-glow').forEach(function(el) {{ el.classList.remove('selected-pin-glow'); }}); window.currentSelectedSafeId = null; }}

    function reapplyHighlight() {{
        if (currentHoveredSelector && document.body.classList.contains('filter-hover-active')) {{
            requestAnimationFrame(function() {{ document.querySelectorAll(currentHoveredSelector).forEach(function(el) {{ el.classList.add('highlight-active'); if(el.parentElement) el.parentElement.style.zIndex = 9999; }}); }});
        }}
        if(window.currentSelectedSafeId) {{ highlightPin(window.currentSelectedSafeId); }}
    }}

    setTimeout(function() {{
        var filterBtn = document.getElementById('standaloneFilterBtn');
        var layerControl = document.querySelector('.leaflet-top.leaflet-right .leaflet-control-layers'); 
        var formElem = layerControl ? layerControl.querySelector('form') : null;
        var globalMap = null;
        for (var key in window) {{ if (key.startsWith('map_')) {{ globalMap = window[key]; break; }} }}

        if (filterBtn && formElem) {{
            var filterWrapper = document.createElement('div');
            filterWrapper.id = 'customFilterWrapper';
            filterWrapper.className = 'custom-filter-wrapper';
            document.body.appendChild(filterWrapper); filterWrapper.appendChild(formElem);
            
            filterBtn.addEventListener('click', function(e) {{ e.preventDefault(); e.stopPropagation(); filterWrapper.classList.toggle('show'); }});
            document.addEventListener('click', function(e) {{ if (filterWrapper.classList.contains('show')) {{ if (!filterWrapper.contains(e.target) && !filterBtn.contains(e.target)) {{ filterWrapper.classList.remove('show'); }} }} }});
            var stopWheel = function(e) {{ e.stopPropagation(); }};
            filterWrapper.addEventListener('wheel', stopWheel, {{passive: false}});
            filterWrapper.addEventListener('mouseenter', function () {{ if(globalMap) {{ globalMap.scrollWheelZoom.disable(); }} }});
            filterWrapper.addEventListener('mouseleave', function () {{ if(globalMap) {{ globalMap.scrollWheelZoom.enable(); }} }});
            
            document.querySelectorAll('.leaflet-control-layers-selector').forEach(function(cb) {{ cb.addEventListener('change', reapplyHighlight); }});

            if(!document.getElementById('exportCsvBtn')) {{
                var exportDiv = document.createElement('div'); exportDiv.className = 'g-export-container';
                exportDiv.innerHTML = '<button id="exportCsvBtn" class="g-export-btn" type="button"><svg xmlns="http://www.w3.org/2000/svg" height="24" viewBox="0 -960 960 960" width="24"><path d="M480-320 280-520l56-58 104 104v-326h80v326l104-104 56 58-200 200ZM240-160q-33 0-56.5-23.5T160-240v-120h80v120h480v-120h80v120q0 33-23.5 56.5T720-160H240Z"/></svg>ส่งออกข้อมูล (CSV)</button>';
                formElem.appendChild(exportDiv);

                document.getElementById('exportCsvBtn').onclick = function(e) {{
                    e.preventDefault(); e.stopPropagation();
                    var activeStatuses = [];
                    document.querySelectorAll('.leaflet-control-layers-selector:checked').forEach(function(inp) {{
                        var lbl = inp.nextElementSibling || inp.parentElement;
                        var statusNode = lbl.querySelector('.status-text');
                        activeStatuses.push(statusNode ? statusNode.getAttribute('data-status') : lbl.textContent.replace(/●/g,'').trim());
                    }});
                    var filtered = expData.filter(function(d) {{ return activeStatuses.includes(d._layerName.trim()); }});
                    if(filtered.length === 0) {{ alert('ไม่พบข้อมูล (กรุณาติ๊กเลือกอย่างน้อย 1 สถานะ)'); return; }}
                    var headers = Object.keys(filtered[0]).filter(function(k) {{ return k !== '_layerName'; }});
                    var csv = headers.map(function(h) {{ return '"' + h + '"'; }}).join(',') + '\\r\\n';
                    filtered.forEach(function(row) {{ csv += headers.map(function(h) {{ return '"' + (row[h] ? row[h].toString().replace(/"/g, '""') : '') + '"'; }}).join(',') + '\\r\\n'; }});
                    var blob = new Blob(["\\uFEFF" + csv], {{ type: 'text/csv;charset=utf-8;' }});
                    var link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = "SCADA_Export_Data.csv";
                    document.body.appendChild(link); link.click(); document.body.removeChild(link);
                }};
            }}
        }}
    }}, 1000);

    setTimeout(function() {{
        document.querySelectorAll('.custom-filter-wrapper label').forEach(function(lbl) {{
            lbl.addEventListener('mouseenter', function() {{
                var statusNode = this.querySelector('.status-text');
                var targetStr = statusNode ? statusNode.getAttribute('data-status') : this.textContent.replace(/●/g,'').trim();
                currentHoveredSelector = ".s_" + statusHashMap[targetStr];
                if (statusHashMap[targetStr]) {{ document.body.classList.add('filter-hover-active'); document.querySelectorAll(currentHoveredSelector).forEach(function(el) {{ el.classList.add('highlight-active'); if(el.parentElement) el.parentElement.style.zIndex = 9999; }}); }}
            }});
            lbl.addEventListener('mouseleave', function() {{
                currentHoveredSelector = null; document.body.classList.remove('filter-hover-active'); document.querySelectorAll('.highlight-active').forEach(function(el) {{ el.classList.remove('highlight-active'); if(el.parentElement) el.parentElement.style.zIndex = ''; }});
            }});
        }});

        document.querySelectorAll('.leaflet-control-layers-group-label').forEach(function(lbl) {{
            lbl.addEventListener('mouseenter', function() {{
                var parentNode = this.querySelector('.parent-text');
                if(parentNode) {{
                    var parentStr = parentNode.getAttribute('data-parent');
                    currentHoveredSelector = ".p_" + statusHashMap[parentStr];
                    if(statusHashMap[parentStr]) {{ document.body.classList.add('filter-hover-active'); document.querySelectorAll(currentHoveredSelector).forEach(function(el) {{ el.classList.add('highlight-active'); if(el.parentElement) el.parentElement.style.zIndex = 9999; }}); }}
                }}
            }});
            lbl.addEventListener('mouseleave', function() {{
                currentHoveredSelector = null; document.body.classList.remove('filter-hover-active'); document.querySelectorAll('.highlight-active').forEach(function(el) {{ el.classList.remove('highlight-active'); if(el.parentElement) el.parentElement.style.zIndex = ''; }});
            }});
        }});
    }}, 1500);

    setTimeout(function() {{
        var globalMap = null;
        for (var key in window) {{ if (key.startsWith('map_')) {{ globalMap = window[key]; break; }} }}
        if (globalMap) {{
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
        {{ id: "hybrid", name: "ดาวเทียม+ถนน", keyword: "Google Hybrid", thumb: "https://mt1.google.com/vt/lyrs=y&x=130&y=119&z=8" }},
        {{ id: "satellite", name: "ดาวเทียม", keyword: "Google Satellite", thumb: "https://mt1.google.com/vt/lyrs=s&x=130&y=119&z=8" }},
        {{ id: "esri", name: "Esri", keyword: "Esri World Imagery", thumb: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/8/119/130" }},
        {{ id: "terrain", name: "ภูมิประเทศ", keyword: "Google Terrain", thumb: "https://mt1.google.com/vt/lyrs=p&x=130&y=119&z=8" }},
        {{ id: "street", name: "แผนที่ถนน", keyword: "Street Map", thumb: "https://mt1.google.com/vt/lyrs=m&x=130&y=119&z=8" }}
    ];
    var currentMapIndex = 1; 
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
        currentMapIndex = idx; var radio = getRadioByKeyword(mapConfigs[idx].keyword); if (radio && !radio.checked) radio.click();
        panel.querySelectorAll('.g-layer-item').forEach(function(item, i) {{ if(i === idx) item.classList.add('active'); else item.classList.remove('active'); }});
        var nextIdx = (idx + 1) % mapConfigs.length;
        document.getElementById('gLayerMainBtn').style.backgroundImage = 'url(' + mapConfigs[nextIdx].thumb + ')';
        document.getElementById('gLayerMainLabel').innerText = mapConfigs[nextIdx].name;
    }}
    document.getElementById('gLayerMainBtn').onclick = function() {{ switchMapLayer((currentMapIndex + 1) % mapConfigs.length); }};
    setTimeout(function() {{ document.getElementById('gLayerMainBtn').style.backgroundImage = 'url(' + mapConfigs[2].thumb + ')'; document.getElementById('gLayerMainLabel').innerText = mapConfigs[2].name; }}, 100);

    var expData = {export_json};
    setTimeout(function() {{
        document.querySelectorAll('.leaflet-control-layers-group-name').forEach(function(header) {{
            header.title = 'คลิกเพื่อ เลือก/ยกเลิก ทั้งหมดในกลุ่มนี้';
            header.addEventListener('click', function(e) {{
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
        }});
    }}, 1400);

    var sData = {search_json};
    var box = document.getElementById('searchBox');
    var inp = document.getElementById('searchInput');
    var res = document.getElementById('searchResults');
    var clr = document.getElementById('searchClear');

    function handleSearchFocus() {{ box.classList.add('focus'); hideCustomPanel(); triggerSearch(); }}

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

    /* ========================================================
       ระบบ World-Class Auto-Update (ทำงานร่วมกับ Backend) 
       ======================================================== */
    var currentDataVersion = null;

    function checkServerForUpdate() {{
        // บังคับไม่ให้แคชคำตอบ
        fetch('/api/version?t=' + new Date().getTime(), {{ cache: 'no-store' }})
            .then(response => response.json())
            .then(data => {{
                if (currentDataVersion === null) {{
                    currentDataVersion = data.version; 
                }} else if (data.version > currentDataVersion) {{
                    console.log("พบข้อมูลใหม่จาก SCADA! กำลังอัปเดตหน้าจอ...");
                    performSeamlessReload();
                }}
            }}).catch(e => console.log("Check update error:", e));
    }}

    function performSeamlessReload() {{
        var map = null;
        for (var key in window) {{ if (key.startsWith('map_')) {{ map = window[key]; break; }} }}
        if (map) {{
            var center = map.getCenter();
            sessionStorage.setItem('scada_saved_lat', center.lat);
            sessionStorage.setItem('scada_saved_lng', center.lng);
            sessionStorage.setItem('scada_saved_zoom', map.getZoom());
        }}
        window.location.href = window.location.pathname + '?v=' + new Date().getTime();
    }}

    setTimeout(function() {{
        var map = null;
        for (var key in window) {{ if (key.startsWith('map_')) {{ map = window[key]; break; }} }}
        if (map) {{
            // ดีดตัวกลับมาที่ตำแหน่งเดิม
            var sLat = sessionStorage.getItem('scada_saved_lat');
            var sLng = sessionStorage.getItem('scada_saved_lng');
            var sZoom = sessionStorage.getItem('scada_saved_zoom');
            
            if (sLat && sLng && sZoom) {{
                map.setView([parseFloat(sLat), parseFloat(sLng)], parseInt(sZoom), {{animate: false}});
                sessionStorage.removeItem('scada_saved_lat');
                sessionStorage.removeItem('scada_saved_lng');
                sessionStorage.removeItem('scada_saved_zoom');
            }}
        }}
        
        // เช็คเวอร์ชันทุกๆ 30 วินาที
        setInterval(checkServerForUpdate, 30000);
        checkServerForUpdate();
    }}, 800);
    </script>
    """
    m.get_root().html.add_child(folium.Element(custom_ui_html))
    return m.get_root().render()

def background_task():
    """พนักงานหลังร้าน: แอบดึงข้อมูลและบันทึกลงไฟล์ให้ทุกคนอ่าน"""
    try:
        print("กำลังดึงข้อมูลและสร้างแผนที่เบื้องหลัง...")
        new_html = generate_map()
        
        # บันทึกไฟล์แผนที่ HTML
        with open(CACHE_HTML_FILE, 'w', encoding='utf-8') as f:
            f.write(new_html)
            
        # บันทึกตัวเลขเวอร์ชัน (บวก 1 จากของเดิม)
        meta = get_meta()
        new_version = meta['version'] + 1
        with open(CACHE_META_FILE, 'w') as f:
            json.dump({'version': new_version, 'last_update': time.time()}, f)
            
        print(f"อัปเดตแผนที่เสร็จสมบูรณ์! (เวอร์ชัน {new_version})")
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการรันเบื้องหลัง: {e}")
    finally:
        # ปลดล็อกให้รอบถัดไปทำงานได้
        if os.path.exists(LOCK_FILE):
            try: os.remove(LOCK_FILE)
            except: pass

def trigger_update_if_needed():
    """เช็คเวลาและสั่งให้พนักงานหลังร้านไปทำงานถ้าถึงเวลา"""
    meta = get_meta()
    if time.time() - meta['last_update'] > CACHE_DURATION:
        # เช็คว่ามีคนกำลังดึงข้อมูลอยู่ไหม (ถ้าค้างเกิน 5 นาทีให้ลบทิ้งทลายกำแพง)
        if os.path.exists(LOCK_FILE) and (time.time() - os.path.getmtime(LOCK_FILE) > 300):
            try: os.remove(LOCK_FILE)
            except: pass
            
        if not os.path.exists(LOCK_FILE):
            try:
                open(LOCK_FILE, 'w').close()
                threading.Thread(target=background_task).start()
            except:
                pass

@app.route('/api/version')
def api_version():
    """ช่องทางสำหรับให้บอทจากหน้าเว็บแวะมาถามเวอร์ชัน"""
    trigger_update_if_needed()
    meta = get_meta()
    return jsonify({"version": meta['version']})

@app.route('/')
def index():
    """ส่งแผนที่หน้าจอหลักให้คนที่กดเข้าเว็บ"""
    trigger_update_if_needed()
    
    try:
        with open(CACHE_HTML_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    except:
        return "<h2 style='text-align:center; margin-top:20%; font-family:sans-serif;'>กำลังเตรียมข้อมูล SCADA ครั้งแรก...<br>ระบบจะโหลดหน้าเว็บอัตโนมัติในไม่ช้า</h2><script>setTimeout(()=>window.location.reload(), 5000);</script>", 503

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)