# -*- coding: utf-8 -*-
import json
import os
import io
import time
import traceback
import math
from datetime import datetime, timezone
from html import escape
import pandas as pd
import cairosvg
from svgpathtools import parse_path
from rapidfuzz import process, fuzz
from PIL import Image, ImageDraw, ImageFont, ImageColor
from tabulate import tabulate
import hashlib

WIDTH = 384
HEIGHT = 184

JSON_PATH = "latest_flight_alert.json"
SHAPE_DF = pd.read_csv('shape_data.csv', delimiter='\t')


# Define the target color palette (you can tweak these RGB values as needed)
PALETTE = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "yellow": (255, 255, 0),
    "transparent": (0, 0, 0, 0)
}


# === SCHEDULE CONFIGURATION ===
PUBLISH_SCHEDULE = {
    'mon': [('16:00', '20:00')],
    'tue': [('16:00', '20:00')],
    'wed': [('16:00', '20:00')],
    'thu': [('16:00', '20:00')],
    'fri': [('16:00', '20:00')],
    'sat': [('08:00', '20:00')],
    'sun': [('08:00', '20:00')],
}
DISABLED_MONTHS = ['jan', 'feb']
DISPLAY_CONFIG_PATH = "display_config.json"
PNG_URL = "flight_card.png"  # Adjust if hosted remotely (e.g., a URL)
last_flight = None
last_data_hash = None


def get_path_bounds(path_str):
    path = parse_path(path_str)
    xmin, xmax, ymin, ymax = None, None, None, None
    for seg in path:
        box = seg.bbox()
        if xmin is None or box[0] < xmin:
            xmin = box[0]
        if xmax is None or box[1] > xmax:
            xmax = box[1]
        if ymin is None or box[2] < ymin:
            ymin = box[2]
        if ymax is None or box[3] > ymax:
            ymax = box[3]
    return xmin, xmax, ymin, ymax

def render_shape(designator, type, rotation=0, use_accent=False, base_color="black", df=SHAPE_DF):
    row = df[df['designator'] == designator]

    if not row.empty:
        match_type = "exact"
    else:
        designator_list = df['description'].tolist()
        best_match, score, idx = process.extractOne(type, designator_list, scorer=fuzz.token_set_ratio)

        if score >= 65:
            row = df.iloc[[idx]]
            match_type = "fuzzy"
        else:
      #      print(f"No close match found for designator '{designator}' or type '{type}'")
            return None, "no_match"

    # Proceed to extract and decode shape data
    shape_data_str = row.iloc[0]['shape_data']

    try:
        shape_data = json.loads(shape_data_str)
    except json.JSONDecodeError as e:
   #     print(f"❌ Error decoding shape_data JSON for designator '{row.iloc[0]['designator']}': {e}")
        return

    path_data = shape_data.get('path')
    accent_data = shape_data.get('accent', None)

    if not path_data:
   #     print(f"❌ No path data found in shape_data for '{row.iloc[0]['designator']}'")
        return

    try:
        xmin, xmax, ymin, ymax = get_path_bounds(path_data)
 #       print(f"✅ Path bounds: xmin={xmin}, xmax={xmax}, ymin={ymin}, ymax={ymax}")
    except Exception as e:
   #     print(f"❌ Error computing path bounds for '{row.iloc[0]['designator']}': {e}")
        return


    padding = 2
    xmin -= padding
    ymin -= padding
    xmax += padding
    ymax += padding

    width = xmax - xmin
    height = ymax - ymin
    viewBox = "{} {} {} {}".format(xmin, ymin, width, height)

    from html import escape
    path_escaped = escape(path_data)

    svg_content = f'''<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800" viewBox="{viewBox}">
        <path d="{path_escaped}" fill="{base_color}" transform="rotate({rotation} {(xmin + xmax)/2} {(ymin + ymax)/2})" />
    '''

    if use_accent and accent_data:
        accent_escaped = escape(accent_data)
        svg_content += f'''
        <path d="{accent_escaped}" fill="red" opacity="0.6"
            transform="rotate({rotation} {(xmin + xmax)/2} {(ymin + ymax)/2})" />
        '''

    svg_content += "</svg>"
    
    png_bytes = cairosvg.svg2png(bytestring=svg_content.encode('utf-8'))
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    return img, match_type


def nearest_palette_color(rgba):
    if rgba[3] == 0:
        return PALETTE["transparent"]
    
    r, g, b, _ = rgba
    min_dist = float("inf")
    nearest = None
    for name, color in PALETTE.items():
        if name == "transparent":
            continue
        cr, cg, cb = color
        dist = (r - cr)**2 + (g - cg)**2 + (b - cb)**2
        if dist < min_dist:
            min_dist = dist
            nearest = color
    return (*nearest, 255)  # Keep alpha 255 for visible pixels


def draw_header(draw, img, width, flight_number, flight_info):
    header_height = 52
    bg_color = (0, 0, 0)
    text_color = (255, 255, 255)
    logo_size = 52
    padding_right = 1
    info_start_x = logo_size + 5  # info text starts just right of logo with 5px gap

    # Background
    draw.rectangle([0, 0, width, header_height], fill=bg_color)

    # Load fonts
    try:
        font_large = ImageFont.truetype("./Anton-Regular.ttf", 50)
        font_small = ImageFont.truetype("./Roboto-Medium.ttf", 12)
    except Exception as e:
        print(f"Font load error: {e}")
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Get flight info details
    aircraft_type = flight_info.get("type", "Unknown")
    speed = flight_info.get("speed", "N/A")
    altitude_ft = flight_info.get("altitude_ft", "N/A")

    # Draw operator logo flush left (x=0)
    operator_code = flight_number[:3].upper()
    logo_filename = f"{operator_code}.png"
    logo_path = f"./operator_logos/airline-logos/radarbox_logos/{logo_filename}"

    try:
        logo_img = Image.open(logo_path).convert("RGBA")
        if logo_img is not None:
            pixels = logo_img.load()
            for y in range(logo_img.height):
                for x in range(logo_img.width):
                    rgba = pixels[x, y]
                    pixels[x, y] = nearest_palette_color(rgba)

            logo_img.thumbnail((logo_size, logo_size), Image.Resampling.LANCZOS)
            logo_y = (header_height - logo_img.height) // 2
            img.paste(logo_img, (0, logo_y), logo_img)
        else:
            raise FileNotFoundError(f"Logo image is None for {logo_filename}")
    except FileNotFoundError as fnf_error:
        print(f"Logo load error for {logo_filename}: {fnf_error}")
        logo_y = (header_height - logo_size) // 2
        # Draw a placeholder rectangle (white or any visible color)
        draw.rectangle([0, logo_y, logo_size, logo_y + logo_size], fill=(255, 255, 255))
    except Exception as e:
        print(f"Unexpected error loading logo {logo_filename}: {e}")
        logo_y = (header_height - logo_size) // 2
        draw.rectangle([0, logo_y, logo_size, logo_y + logo_size], fill=(255, 255, 255))

    # Flight number top-right with 1px padding
    bbox = font_large.getbbox(flight_number)
    text_w = bbox[2] - bbox[0]
    text_y = 0 - 10
    text_x = width - text_w - padding_right
    draw.text((text_x, text_y), flight_number, font=font_large, fill=text_color)

    # Vertically stacked info text right of logo
    # Calculate vertical centering for the block of three lines
    line_height = font_small.getbbox("Ag")[3]  # approximate line height
    total_text_height = line_height * 3
    start_y = (header_height - total_text_height) // 2

    labels = [aircraft_type, f"{speed} kt", f"{altitude_ft} ft"]
    for i, label in enumerate(labels):
        y = start_y + i * line_height
        draw.text((info_start_x, y), label, font=font_small, fill=text_color)

    return img

    
def draw_middle_section(draw, width, height, flight_info):
    section_top = 52
    section_height = 100
    bg_color = (255, 255, 255)  # white background
    text_color_iata = (255, 0, 0)  # red color for IATA
    text_color_full = (255, 0, 0)  # red color for full names
    center_x=192
    center_y=110
    radius=70
    heading_deg=45

    # Clear middle section background
    draw.rectangle([0, section_top, width, section_top + section_height], fill=bg_color)

    try:
        font_iata = ImageFont.truetype("./Anton-Regular.ttf", 85)
    except Exception:
        font_iata = ImageFont.load_default()

    try:
        font_full = ImageFont.truetype("./Roboto_Condensed-SemiBold.ttf", 18)
    except Exception:
        font_full = ImageFont.load_default()

    origin_iata = flight_info.get("origin_iata", "N/A")
    destination_iata = flight_info.get("destination_iata", "N/A")
    origin_raw = flight_info.get("origin") or "Unknown"
    origin_full = origin_raw.split(",")[0]

    destination_raw = flight_info.get("destination") or "Unknown"
    destination_full = destination_raw.split(",")[0]



    # Draw origin IATA left (1 px padding)
    origin_bbox = font_iata.getbbox(origin_iata)
    origin_width = origin_bbox[2] - origin_bbox[0]
    origin_height = origin_bbox[3] - origin_bbox[1]
    origin_x = 1
    origin_y = section_top - 16

    draw.text((origin_x, origin_y), origin_iata, font=font_iata, fill=text_color_iata)

    # Draw destination IATA right (1 px padding)
    dest_bbox = font_iata.getbbox(destination_iata)
    dest_width = dest_bbox[2] - dest_bbox[0]
    dest_height = dest_bbox[3] - dest_bbox[1]
    dest_x = width - dest_width - 1
    dest_y = section_top - 16

    draw.text((dest_x, dest_y), destination_iata, font=font_iata, fill=text_color_iata)

    # Draw origin full name centered below origin IATA
    origin_full_bbox = font_full.getbbox(origin_full)
    origin_full_width = origin_full_bbox[2] - origin_full_bbox[0]
    origin_full_x = origin_x + (origin_width // 2) - (origin_full_width // 2)
    origin_full_y = origin_y + origin_height +30

    draw.text((origin_full_x, origin_full_y), origin_full, font=font_full, fill=text_color_full)

    # Draw destination full name centered below destination IATA
    dest_full_bbox = font_full.getbbox(destination_full)
    dest_full_width = dest_full_bbox[2] - dest_full_bbox[0]
    dest_full_x = dest_x + (dest_width // 2) - (dest_full_width // 2)
    dest_full_y = dest_y + dest_height + 30

    draw.text((dest_full_x, dest_full_y), destination_full, font=font_full, fill=text_color_full)

    draw.ellipse(
        [(center_x - radius, center_y - radius), (center_x + radius, center_y + radius)],
        outline="black",
        width=2,
    )
    
 
    
    # Draw tick marks and labels
    font = ImageFont.load_default()
    directions = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
    for i in range(60):
        angle = math.radians(i * 6)  # 360 degrees / 60 ticks
        
        # Tick lengths
        if i % 15 == 0:
            tick_len = radius * 0.15  # long tick
        else:
            tick_len = radius * 0.07  # short tick
        
        x_start = center_x + radius * math.sin(angle)
        y_start = center_y - radius * math.cos(angle)
        x_end = center_x + (radius - tick_len) * math.sin(angle)
        y_end = center_y - (radius - tick_len) * math.cos(angle)
        
        draw.line([(x_start, y_start), (x_end, y_end)], fill="black", width=1)
        
        # Draw cardinal labels (N, E, S, W)
        if i % 15 == 0:
            label = ['N', 'E', 'S', 'W'][i // 15]
            label_x = center_x + (radius - tick_len - 15) * math.sin(angle)
            label_y = center_y - (radius - tick_len - 15) * math.cos(angle)
            text_bbox = draw.textbbox((label_x, label_y), label, font=font)
            text_w = text_bbox[2] - text_bbox[0]
            text_h = text_bbox[3] - text_bbox[1]
            draw.text((label_x - text_w/2, label_y - text_h/2), label, fill="black", font=font)
    



def draw_bottom_bar(draw, img, flight_info, timestamp_str, temperature_str):
    bar_height = 20
    width, height = img.size
    bar_y = height - bar_height

    fmt = "%Y-%m-%d %H:%M:%S %Z"
    now = datetime.strptime(timestamp_str, fmt).replace(tzinfo=timezone.utc)

    takeoff_str = flight_info.get("takeoff_time_actual")
    arrival_str = flight_info.get("arrival_time_estimated")

    if not takeoff_str or not arrival_str or takeoff_str.lower() == "n/a" or arrival_str.lower() == "n/a":
        return

    takeoff_time = datetime.strptime(takeoff_str, fmt).replace(tzinfo=timezone.utc)
    arrival_time = datetime.strptime(arrival_str, fmt).replace(tzinfo=timezone.utc)

    draw.rectangle([(0, bar_y), (width, height)], fill="black")

    total = (arrival_time - takeoff_time).total_seconds()
    elapsed = (now - takeoff_time).total_seconds()
    progress = max(0.0, min(1.0, elapsed / total)) if total > 0 else 0

    red_width = int(width * progress)
    draw.rectangle([(0, bar_y), (red_width, height)], fill="red")

    remaining = arrival_time - now
    if remaining.total_seconds() < 0:
        text = "Arrived"
    else:
        hrs, rem = divmod(int(remaining.total_seconds()), 3600)
        mins = rem // 60
        text = f"{hrs}h {mins:02}m"

    font_path = "./Anton-Regular.ttf"
    font_size = 16
    font = ImageFont.truetype(font_path, font_size)

    # Draw remaining time (centered)
    bbox = font.getbbox(text)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    text_x = (width - text_width) // 2
    text_y = bar_y + (bar_height - text_height) // 2
    draw.text((text_x, text_y - 4), text, font=font, fill="white")

    # 🔲 Draw temperature block on bottom-right
    if temperature_str:
        temp_str = str(temperature_str)
        temp_bbox = font.getbbox(temp_str)
        temp_width = temp_bbox[2] - temp_bbox[0]
        temp_height = temp_bbox[3] - temp_bbox[1]

        padding = 4
        square_size = temp_width + 2 * padding
        square_x0 = width - square_size
        square_y0 = bar_y
        square_x1 = width
        square_y1 = bar_y + bar_height

        draw.rectangle([(square_x0, square_y0), (square_x1, square_y1)], fill="black")
        temp_x = square_x0 + padding
        temp_y = bar_y + (bar_height - temp_height) // 2 - 5
        temp_text = f"{int(temperature_str)}°C"
        draw.text((temp_x, text_y-4), temp_text, font=font, fill="yellow")


def draw_aircraft_background(img, icao_type, type, heading):
    if not icao_type or icao_type == "N/A":
   #     print("No valid ICAO type; skipping shape render.")
        return "no_match"

    try:
        aircraft_img, match_type = render_shape(icao_type, type, rotation=heading or 0, use_accent=False)
        if aircraft_img is None:
            return "no_match"

        aircraft_img.thumbnail((100, 100), Image.Resampling.LANCZOS)

        # Calculate centered position
        img_width, img_height = img.size
        ac_width, ac_height = aircraft_img.size

        paste_x = (img_width - ac_width) // 2
        paste_y = (img_height - ac_height) // 2 +18

        # Paste centered
        img.paste(aircraft_img, (paste_x, paste_y), aircraft_img)

        return match_type
    except Exception as e:
   #     print(f"Error drawing aircraft background for {icao_type}: {e}")
        return "no_match"

### end draw


def is_time_in_range(start_str, end_str, now_time):
    start = datetime.strptime(start_str, '%H:%M').time()
    end = datetime.strptime(end_str, '%H:%M').time()
    return start <= now_time <= end

def should_publish():
    now = datetime.now()
    weekday = now.strftime('%a').lower()  # e.g. 'mon'
    month = now.strftime('%b').lower()    # e.g. 'jan'
    current_time = now.time()

    if month in DISABLED_MONTHS:
        return False

    for start_str, end_str in PUBLISH_SCHEDULE.get(weekday, []):
        if is_time_in_range(start_str, end_str, current_time):
            return True
    return False

def write_display_json(display: bool, refresh: bool, png_url: str = None):
    data = {
        "display": display,
        "refresh": refresh,
        "refreshInterval": 18000
    }
    if display:
        data["png_url"] = "http://192.168.0.105:8080/flight_card.png"

    with open("/usr/share/skyaware/html/flight_card.html", "w") as f:
        json.dump(data ,f)


def draw_card(data):
    output_path = "/usr/share/skyaware/html/flight_card.png"
    WIDTH, HEIGHT = 384, 184

    heading = data.get("heading")
    img = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    draw_header(draw, img, WIDTH, data["flight"], data["flight_info"])
    draw_middle_section(draw, WIDTH, HEIGHT, data["flight_info"])
    match_type = draw_aircraft_background(img, data["icao_type"], data["type"], heading)
    draw_bottom_bar(draw, img, data["flight_info"], data["timestamp"], data["temperature_c"])

    img.save(output_path)

    return output_path, match_type  # return info needed for display json & print table


def load_flight_data(json_path):
    try:
        with open(json_path, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading JSON '{json_path}': {e}")
        return {}
        
    flight = data.get("flight") or data.get("flight_info", {}).get("flight") or "N/A"
    icao_type = data.get("aircraft_info", {}).get("icao_type") or "N/A"
    operator = data.get("aircraft_info", {}).get("operator") or "N/A"
    manufacturer = data.get("aircraft_info", {}).get("manufacturer") or "N/A"
    type = data.get("aircraft_info", {}).get("type") or "N/A"
    timestamp = data.get("timestamp", "1970-01-01 00:00:00 UTC")
    flight_info = data.get("flight_info", {})
    aircraft_info = data.get("aircraft_info", {})
    altitude_ft = data.get("altitude_ft",0)
    heading = data.get("heading",0)
    speed = data.get("speed",0)
    temperature_c = data.get("temperature_c",0)
    

    return {
        "flight": flight,
        "icao_type": icao_type,
        "operator": operator,
        "manufacturer":manufacturer,
        "type":type,
        "timestamp": timestamp,
        "altitude_ft": altitude_ft,
        "heading": heading,
        "speed": speed,
        "temperature_c":temperature_c,
        "flight_info": {
            "takeoff_time_actual": flight_info.get("takeoff_time_actual", "N/A"),
            "arrival_time_estimated": flight_info.get("arrival_time_estimated", "N/A"),
            "origin_iata": flight_info.get("origin_iata") or "N/A",
            "origin": flight_info.get("origin", "N/A"),
            "destination_iata": flight_info.get("destination_iata") or  "N/A",
            "destination": flight_info.get("destination", "N/A"),
            "flight": flight,
            "icao_type": icao_type,
            "operator": operator,
            "manufacturer":manufacturer,
            "type":type,
            "timestamp": timestamp,
            "altitude_ft": altitude_ft,
            "heading": heading,
            "speed": speed,
            "temperature_c": temperature_c,
        },
        
    }




def print_match_table(matches, call_count=0):
    headers = ["Match", "Flight", "Operator", "Manu", "Type", "Heading", "Speed", "Alt", "ORG", "DEST", "ETA"]
    rows = []

    now = datetime.now(timezone.utc)

    for m in matches:
        match_text = "yes" if m.get("match_type") == "exact" else "fuzzy"
        eta_str = m.get("arrival_time_estimated", "N/A")

        # Default ETA display
        eta_display = "N/A"
        if eta_str != "N/A":
            try:
                # Parse the ETA string with timezone aware datetime
                fmt = "%Y-%m-%d %H:%M:%S %Z"
                eta_dt = datetime.strptime(eta_str, fmt).replace(tzinfo=timezone.utc)
                delta = eta_dt - now

                if delta.total_seconds() <= 0:
                    eta_display = "Arrived"
                else:
                    minutes, seconds = divmod(int(delta.total_seconds()), 60)
                    eta_display = f"{minutes}m {seconds}s"
            except Exception:
                eta_display = eta_str  # fallback in case parsing fails

        row = [
            match_text,
            m.get("flight", "N/A"),
            m.get("operator", "N/A"),
            m.get("manufacturer", "N/A"),
            m.get("type", "N/A"),
            m.get("heading", "N/A"),
            m.get("speed", "N/A"),
            m.get("altitude_ft", "N/A"),
            m.get("origin_iata", "N/A"),
            m.get("destination_iata", "N/A"),
            eta_display,
        ]
        rows.append(row)

    if call_count % 10 == 0:
        os.system('cls' if os.name == 'nt' else 'clear')
    print(tabulate(rows, headers=headers, tablefmt="pretty"))


def watch_and_run(json_path):
    last_hash = None
    last_flight = None
    last_draw_time = 0

    while True:
        # ✅ 1. Missing JSON
        if not os.path.exists(json_path):
            print("[🟡] JSON file missing")
            write_display_json(display=False, refresh=False)
            time.sleep(2)
            continue

        # ✅ 2. Not in publishing window
        if not should_publish():
            print("[🔴] Outside publishing window")
            write_display_json(display=False, refresh=False)
            time.sleep(2)
            continue

        try:
            
            with open(json_path, "r") as f:
                raw = f.read()
                current_hash = hashlib.md5(raw.encode()).hexdigest()

                data = load_flight_data(json_path)
            
        except Exception as e:
            print(f"[❌] Error reading JSON: {e}")
            time.sleep(2)
            continue

        flight = data.get("flight") or data.get("flight_info", {}).get("flight") or "N/A"

        # ✅ 3. New JSON with different flight
        if current_hash != last_hash and flight != last_flight:
            now = time.time()
            if now - last_draw_time >= 30:
                print(f"[✅] Drawing new flight: {flight}")
                draw_card(data)
                write_display_json(display=True, refresh=True)
                last_draw_time = now
                last_flight = flight
                last_hash = current_hash
            else:
                print(f"[⏱️] Skipping draw (rate-limited): {flight}")
        
        # ✅ 4. New JSON, same flight
        elif current_hash != last_hash and flight == last_flight:
            print(f"[🔁] Same flight, updated data: {flight}")
            write_display_json(display=True, refresh=False)
            last_hash = current_hash

        # ✅ 5. Same JSON, same flight
        try:
            ts = data.get("timestamp") or data.get("generated_at")
            if ts:
                if isinstance(ts, str):
                    if ts.endswith(" UTC"):
                        ts = ts[:-4]
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=timezone.utc)
                ts = dt.timestamp()
            age = time.time() - ts
            if age > 90:
                print(f"[⚠️] Stale data ({int(age)}s old)")
                write_display_json(display=True, refresh=False)
        except Exception as e:
            print(f"[⚠️] Timestamp check failed: {e}")

        time.sleep(1)

if __name__ == "__main__":
    watch_and_run(JSON_PATH)



