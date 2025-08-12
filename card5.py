import json
import io
import math
from datetime import datetime, timezone
from html import escape
from PIL import Image, ImageDraw, ImageFont
import cairosvg
from svgpathtools import parse_path
from rapidfuzz import process, fuzz
import pandas as pd

WIDTH = 384
HEIGHT = 184

CARD_JSON_FILE = "/usr/share/skyaware/html/flight_card.html"
PNG_PATH = "/usr/share/skyaware/html/flight_card.png"

SHAPE_DF = pd.read_csv('shape_data.csv', delimiter='\t')

PALETTE = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "yellow": (255, 255, 0),
    "transparent": (0, 0, 0, 0)
}

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
        print(f"✅ Exact designator match found: '{designator}'")
    else:
        print(f"ℹ️ No exact match for designator '{designator}'. Trying fuzzy match using input type: '{type}'")
        designator_list = df['description'].tolist()
        best_match, score, idx = process.extractOne(type, designator_list, scorer=fuzz.token_set_ratio)

        if score >= 65:
            row = df.iloc[[idx]]
            match_type = "fuzzy"
            print(f"🔍 Fuzzy match result: input type '{type}' ≈ designator '{best_match}' (score: {score})")
        else:
            print(f"No close match found for designator '{designator}' or type '{type}'")
            return None, "no_match"

    # Proceed to extract and decode shape data
    shape_data_str = row.iloc[0]['shape_data']

    try:
        shape_data = json.loads(shape_data_str)
    except json.JSONDecodeError as e:
        print(f"❌ Error decoding shape_data JSON for designator '{row.iloc[0]['designator']}': {e}")
        return

    path_data = shape_data.get('path')
    accent_data = shape_data.get('accent', None)

    if not path_data:
        print(f"❌ No path data found in shape_data for '{row.iloc[0]['designator']}'")
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
    viewBox = f"{xmin} {ymin} {width} {height}"

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
    return (*nearest, 255)

def draw_header(draw, img, width, flight_number, flight_info, aircraft_info, latest_alert):
    header_height = 52
    bg_color = (0,0,0)
    text_color = (255,255,255)
    logo_size = 52
    padding_right = 1
    info_start_x = logo_size + 5

    draw.rectangle([0,0,width,header_height], fill=bg_color)
    try:
        font_large = ImageFont.truetype("./Anton-Regular.ttf", 50)
        font_small = ImageFont.truetype("./Roboto-Medium.ttf", 12)
    except:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    manufacturer = aircraft_info.get("manufacturer", "")
    aircraft_type = aircraft_info.get("type", "")  # Note: use aircraft_info here, not flight_info

    if manufacturer and aircraft_type:
        aircraft_full = f"{manufacturer} {aircraft_type}"
    elif manufacturer:
        aircraft_full = manufacturer
    elif aircraft_type:
        aircraft_full = aircraft_type
    else:
        aircraft_full = "Unknown"






    speed = latest_alert.get("speed", "N/A")
    altitude_ft = latest_alert.get("altitude", "N/A")

    operator_code = flight_number[:3].upper()
    logo_filename = f"{operator_code}.png"
    logo_path = f"./operator_logos/airline-logos/radarbox_logos/{logo_filename}"

    try:
        logo_img = Image.open(logo_path).convert("RGBA")
        pixels = logo_img.load()
        for y in range(logo_img.height):
            for x in range(logo_img.width):
                pixels[x,y] = nearest_palette_color(pixels[x,y])
        logo_img.thumbnail((logo_size,logo_size), Image.Resampling.LANCZOS)
        logo_y = (header_height - logo_img.height)//2
        img.paste(logo_img, (0, logo_y), logo_img)
    except Exception:
        logo_y = (header_height - logo_size)//2
        draw.rectangle([0, logo_y, logo_size, logo_y + logo_size], fill=(255,255,255))

    bbox = font_large.getbbox(flight_number)
    text_w = bbox[2] - bbox[0]
    text_y = 0 - 10
    text_x = width - text_w - padding_right
    draw.text((text_x, text_y), flight_number, font=font_large, fill=text_color)

    line_height = font_small.getbbox("Ag")[3]
    total_text_height = line_height * 3
    start_y = (header_height - total_text_height)//2

    labels = [aircraft_full, f"{speed} kt", f"{altitude_ft} ft"]
    for i, label in enumerate(labels):
        y = start_y + i * line_height
        draw.text((info_start_x, y), label, font=font_small, fill=text_color)
    return img

def draw_middle_section(draw, width, height, flight_info):
    section_top = 52
    section_height = 100
    bg_color = (255,255,255)
    text_color_iata = (255,0,0)
    text_color_full = (255,0,0)
    center_x = 192
    center_y = 110
    radius = 70

    draw.rectangle([0, section_top, width, section_top + section_height], fill=bg_color)

    try:
        font_iata = ImageFont.truetype("./Anton-Regular.ttf", 85)
    except:
        font_iata = ImageFont.load_default()

    try:
        font_full = ImageFont.truetype("./Roboto_Condensed-SemiBold.ttf", 18)
    except:
        font_full = ImageFont.load_default()

    origin_iata = flight_info.get("origin_iata", "N/A") or "N/A"
    destination_iata = flight_info.get("destination_iata", "N/A") or "N/A"
    origin_raw = flight_info.get("origin", "Unknown") or "Unknown"
    origin_full = origin_raw.split(",")[0]

    destination_raw = flight_info.get("destination", "Unknown") or "Unknown"
    destination_full = destination_raw.split(",")[0]

    origin_bbox = font_iata.getbbox(origin_iata)
    origin_width = origin_bbox[2] - origin_bbox[0]
    origin_height = origin_bbox[3] - origin_bbox[1]
    origin_x = 1
    origin_y = section_top - 16

    draw.text((origin_x, origin_y), origin_iata, font=font_iata, fill=text_color_iata)

    dest_bbox = font_iata.getbbox(destination_iata)
    dest_width = dest_bbox[2] - dest_bbox[0]
    dest_height = dest_bbox[3] - dest_bbox[1]
    dest_x = width - dest_width - 1
    dest_y = section_top - 16

    draw.text((dest_x, dest_y), destination_iata, font=font_iata, fill=text_color_iata)

    origin_full_bbox = font_full.getbbox(origin_full)
    origin_full_width = origin_full_bbox[2] - origin_full_bbox[0]
    origin_full_x = origin_x + (origin_width // 2) - (origin_full_width // 2)
    origin_full_y = origin_y + origin_height + 30

    draw.text((origin_full_x, origin_full_y), origin_full, font=font_full, fill=text_color_full)

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

    font = ImageFont.load_default()
    for i in range(60):
        angle = math.radians(i * 6)
        tick_len = radius * 0.15 if i % 15 == 0 else radius * 0.07
        x_start = center_x + radius * math.sin(angle)
        y_start = center_y - radius * math.cos(angle)
        x_end = center_x + (radius - tick_len) * math.sin(angle)
        y_end = center_y - (radius - tick_len) * math.cos(angle)
        draw.line([(x_start, y_start), (x_end, y_end)], fill="black", width=1)
        if i % 15 == 0:
            label = ['N', 'E', 'S', 'W'][i // 15]
            label_x = center_x + (radius - tick_len - 15) * math.sin(angle)
            label_y = center_y - (radius - tick_len - 15) * math.cos(angle)
            text_bbox = draw.textbbox((label_x, label_y), label, font=font)
            text_w = text_bbox[2] - text_bbox[0]
            text_h = text_bbox[3] - text_bbox[1]
            draw.text((label_x - text_w / 2, label_y - text_h / 2), label, fill="black", font=font)

def draw_bottom_bar(draw, img, latest_alert):
    bar_height = 20
    width, height = img.size
    bar_y = height - bar_height

    departure_str = latest_alert.get("departure_time_actual")
    arrival_str = latest_alert.get("arrival_time_estimated")
    progress_percent = latest_alert.get("percent_complete", 0) or 0
    temperature_c = latest_alert.get("temperature_c")

    draw.rectangle([(0, bar_y), (width, height)], fill="black")

    if departure_str and arrival_str and departure_str.lower() != "n/a" and arrival_str.lower() != "n/a":
        fmt = "%Y-%m-%d %H:%M:%S %Z"
        try:
            departure_time = datetime.strptime(departure_str, fmt).replace(tzinfo=timezone.utc)
            arrival_time = datetime.strptime(arrival_str, fmt).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)

            total_secs = (arrival_time - departure_time).total_seconds()
            elapsed_secs = (now - departure_time).total_seconds()
            progress = max(0.0, min(1.0, elapsed_secs / total_secs)) if total_secs > 0 else 0
        except Exception:
            progress = progress_percent / 100
    else:
        progress = progress_percent / 100

    red_width = int(width * progress)
    draw.rectangle([(0, bar_y), (red_width, height)], fill="red")

    # Draw remaining time text
    if departure_str and arrival_str:
        try:
            now = datetime.now(timezone.utc)
            arrival_time = datetime.strptime(arrival_str, "%Y-%m-%d %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            remaining = arrival_time - now
            if remaining.total_seconds() < 0:
                text = "Arrived"
            else:
                hrs, rem = divmod(int(remaining.total_seconds()), 3600)
                mins = rem // 60
                text = f"{hrs}h {mins:02}m"
        except Exception:
            text = ""
    else:
        text = ""

    try:
        font = ImageFont.truetype("./Anton-Regular.ttf", 16)
    except:
        font = ImageFont.load_default()

    bbox = font.getbbox(text)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    text_x = (width - text_width) // 2
    text_y = bar_y + (bar_height - text_height) // 2
    draw.text((text_x, text_y - 4), text, font=font, fill="white")

    # Draw temperature
    if temperature_c is not None:
        temp_str = f"{int(temperature_c)}°C"
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
        temp_y = text_y - 4
        draw.text((temp_x, temp_y), temp_str, font=font, fill="yellow")

def draw_aircraft_background(img, icao_type, type, heading):
    if not icao_type or icao_type == "N/A":
        return "no_match"
    try:
        aircraft_img, match_type = render_shape(icao_type, type, rotation=heading or 0, use_accent=False)
        if aircraft_img is None:
            return "no_match"
        aircraft_img.thumbnail((100, 100), Image.Resampling.LANCZOS)

        img_width, img_height = img.size
        ac_width, ac_height = aircraft_img.size
        paste_x = (img_width - ac_width) // 2
        paste_y = (img_height - ac_height) // 2 + 18
        img.paste(aircraft_img, (paste_x, paste_y), aircraft_img)
        return match_type
    except Exception:
        return "no_match"



def process_flight_data(latest_alert):



    img = Image.new("RGBA", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(img)

    flight_number = latest_alert.get("flight", "N/A")
    flight_info = latest_alert.get("flight_info", {})
    aircraft_info = latest_alert.get("aircraft_info", {})

    # Header (pass aircraft_info and latest_alert for needed top-level keys)
    draw_header(draw, img, WIDTH, flight_number, flight_info, aircraft_info, latest_alert)

    # Middle section
    draw_middle_section(draw, WIDTH, HEIGHT, flight_info)

    # Aircraft background
    icao_type = aircraft_info.get("icao_type")
    ac_type = aircraft_info.get("type")
    heading = latest_alert.get("heading", 0)
    draw_aircraft_background(img, icao_type, ac_type, heading)

    # Bottom progress and temperature bar
    draw_bottom_bar(draw, img, latest_alert)

    img = img.convert("RGB")
    img.save(PNG_PATH,format="PNG",
        optimize=True,
        compress_level=6,  # Reasonable compression without risk of artifacts
        interlace=False)

  




    print(f"Flight card PNG saved to: {PNG_PATH}")

if __name__ == "__main__":
    try:
        with open(CARD_JSON_FILE, "r") as f:
            latest_alert = json.load(f)
    except Exception as e:
        print(f"Failed to read alert JSON: {e}")
        latest_alert = None

    process_flight_data(latest_alert)

