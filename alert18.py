import asyncio
import json
import os
import requests
import urllib.request
from datetime import datetime, timezone
from math import radians, cos, sin, asin, sqrt, atan2, degrees
from shapely.geometry import Point, LineString
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.spinner import Spinner
from rich.align import Align
from rich.live import Live
import psutil
import socket
import subprocess
import gc
import sys
import calendar
import os
from zoneinfo import ZoneInfo


EST = ZoneInfo("America/New_York")
BLACKLISTED_HEX = {"c06032", "abcd12", "123abc"}  # use a set for efficient lookups


REFERENCE_LAT = 43.666426
REFERENCE_LON = -79.422638
AIRCRAFT_JSON_PATH = "/run/dump1090-fa/aircraft.json"
# ALERT_JSON_FILE = "latest_flight_alert.json"
ALERT_JSON_FILE = "/usr/share/skyaware/html/flight_card.html"
last_alert_write_time = None
DISTANCE_ALERT_KM = 9
BULLSEYE_ALERT_KM = 1.4
MIN_ALERT_INTERVAL = 60
SCAN_INTERVAL_SECONDS = 1
adsbdb_invalid_hexes = set()
latest_alert = None
current_temperature_c = None

# constants (adjust if needed)
REFRESH_DURATION_MS = 13000        # keep refresh=True for this many ms after cycle start
MIN_ALERT_INTERVAL = 60           # seconds between refresh cycles
last_png_mtime = 0
refresh_ready = False
within_schedule= False

# persistent state for refresh cycles (lives for the duration of main_loop())
last_refresh_start = None         # datetime when last refresh cycle started, or None
last_card_launch_time = None      # same as last_refresh_start (optional), used to ensure card3 launched once




spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
spinner_index = 0

def get_mtime(path):
    try:
        return os.path.getmtime(path)
    except FileNotFoundError:
        return 0

from datetime import datetime, time
from zoneinfo import ZoneInfo

EST = ZoneInfo("America/New_York")

def is_within_schedule(now: datetime) -> bool:
    """
    Determine if 'now' (converted to EST) is within the publishing schedule:
    - Disabled in December, January, February
    - Weekdays (Mon-Fri): 16:00 to 22:00
    - Weekends (Sat-Sun): 09:00 to 23:00
    """
    # Convert naive datetime (assumed UTC) or aware datetime to EST
    if now.tzinfo is None:
        now_est = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(EST)
    else:
        now_est = now.astimezone(EST)

    month = now_est.month
    # Disable for Dec(12), Jan(1), Feb(2)
    if month in (12, 1, 2):
        return False

    weekday = now_est.weekday()  # Monday=0 ... Sunday=6
    now_time = now_est.time()

    if 0 <= weekday <= 4:  # Weekday
        # Between 16:00 and 22:00
        if time(16, 0) <= now_time <= time(22, 0):
            return True
    else:  # Weekend (Sat=5, Sun=6)
        # Between 09:00 and 23:00
        if time(9, 0) <= now_time <= time(23, 0):
            return True

    return False



def get_uptime():
    try:
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.readline().split()[0])
        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        return f"{hours}h {minutes}m"
    except:
        return "n/a"

def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "n/a"



def get_wifi_strength():
    try:
        result = subprocess.run(['/sbin/iw', 'dev', 'wlan0', 'link'], capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("signal:"):
                # Example line: "signal: -64 dBm"
                signal_dbm = line.split()[1]  # get the number part: "-64"
                return f"{signal_dbm} dBm"
        return "n/a"
    except Exception:
        return "n/a"

def render_header(spinner_frame, aircraft_list, current_temperature_c, within_schedule):
    # First line text
    line1 = Text()
    line1.append(f"{spinner_frame} Tracking: {len(aircraft_list)} aircraft", style="bold cyan")

    # Add schedule status indicator
    schedule_status = "✅ Within Schedule" if within_schedule else "❌ Outside Schedule"
    line1.append(f"  | Schedule: {schedule_status}", style="bold green" if within_schedule else "bold red")

    if current_temperature_c is not None:
        line1.append(f"  🌡️ {current_temperature_c}°C", style="bold magenta")

    try:
        cpu_temp_c, mem_usage, cpu_usage = get_system_info()

        if cpu_temp_c is not None:
            line1.append(f"  🔥 CPU: {cpu_temp_c:5.1f}°C", style="bold red")
        else:
            line1.append("  🔥 CPU: N/A", style="bold red")

        line1.append(f"  🧠 {cpu_usage} CPU", style="bold yellow")
        line1.append(f"  📈 {mem_usage} RAM", style="bold green")

        disk = psutil.disk_usage('/')
        disk_usage = f"{disk.percent}%"
        line1.append(f"  💽 {disk_usage} Disk", style="bold blue")

    except Exception:
        line1.append(" ⚠️ Stats error", style="bold red")

    # Second line text
    wifi_strength = get_wifi_strength()
    ip_address = get_ip_address()
    uptime = get_uptime()

    line2 = Text()
    line2.append(f"📶 WiFi: {wifi_strength}   ", style="bold cyan")
    line2.append(f"🌐 IP: {ip_address}   ", style="bold cyan")
    line2.append(f"⏱️ Uptime: {uptime}", style="bold cyan")

    # Create a Group to combine lines with a blank line in between, each centered
    group = Group(
        Align.center(line1),
        Text("\n"),  # blank line for spacing
        Align.center(line2)
    )

    header_panel = Panel(group, expand=True)
    return header_panel


def render_dashboard(aircraft_list, latest_alert, spinner_frame, within_schedule, temperature_c=None):

    # ─── Header ─────────────────────────────────────────────
    header_panel = render_header(spinner_frame, aircraft_list, temperature_c, within_schedule)

    # ─── Aircraft Table ─────────────────────────────────────
    table = Table(expand=True)
    table.add_column("HEX")
    table.add_column("FLIGHT")
    table.add_column("DIST(KM)", justify="right", width=10)
    table.add_column("CLOSEST(KM)", justify="right", width=10)
    table.add_column("ALT(FT)", justify="right")
    table.add_column("HDG", justify="right")
    table.add_column("SPD", justify="right")
    table.add_column("FROM", justify="center")
    table.add_column("TO", justify="center")
    table.add_column("ETA", justify="center")
    table.add_column("ICONS")

    for ac in sorted(aircraft_list.values(), key=lambda x: x["distance"]):
        fa = ac.get("flightaware", {})
        origin = fa.get("origin_iata", "n/a")
        dest = fa.get("destination_iata", "n/a")
        eta = fa.get("eta_minutes", "n/a")
        if eta != "n/a" and eta is not None:
            eta = f"{eta} min"
        else:
            eta = "n/a"
        icons = ""
        if ac.get("is_closing"):
            icons += "🎯 "
        if ac.get("alerted"):
            icons += "🚨"

        table.add_row(
            ac["hex"],
            ac["flight"],
            f"{ac['distance']:.2f}",
            f"{ac['bullseye_km']:.2f}" if ac["bullseye_km"] is not None else "n/a",
            str(ac["altitude"]),
            str(ac["heading"]),
            str(ac["speed"]),
            origin,
            dest,
            eta,
            icons
        )

    # Build alert panel safely
    if latest_alert and isinstance(latest_alert, dict) and latest_alert.get('flight'):
        flight = latest_alert.get('flight', 'n/a')
        aircraft_info = latest_alert.get('aircraft_info', {})
        flight_info = latest_alert.get('flight_info', {})

        alert_text = Text()
        alert_text.append(f"Flight: {flight}\n", style="bold yellow")
        alert_text.append(f"Aircraft: {aircraft_info.get('manufacturer', 'n/a')} "
                          f"({aircraft_info.get('icao_type', 'n/a')})\n")
        alert_text.append(f"Route: {flight_info.get('origin_iata', 'n/a')} "
                          f"({flight_info.get('origin', 'n/a')}) → "
                          f"{flight_info.get('destination_iata', 'n/a')} "
                          f"({flight_info.get('destination', 'n/a')})\n")

        eta_minutes = latest_alert.get("eta_minutes", "n/a")
        if eta_minutes != "n/a" and eta_minutes is not None:
            alert_text.append(f"ETA: {eta_minutes} minutes\n")
        else:
            alert_text.append("ETA: n/a\n")

        alert_text.append(f"Speed: {latest_alert.get('speed', 'n/a')} knots\n")
        alert_text.append(f"Altitude: {latest_alert.get('altitude', 'n/a')} ft\n")

        heading_val = latest_alert.get('heading')
        heading_str = f"{int(heading_val)}°" if heading_val not in (None, 'n/a') else "n/a"

        alert_text.append(f"Heading: {heading_str}\n")
        
        #adding refresh variable
        refresh_status = latest_alert.get("refresh", False)
        alert_text.append(f"Refresh: {'On' if refresh_status else 'Off'}\n", style="bold cyan")


        bullseye_km = latest_alert.get('bullseye_km')
        if bullseye_km is not None:
            alert_text.append(f"Closest Point: {bullseye_km:.2f} km\n")

        temp_c = latest_alert.get("temperature_c")
        if temp_c is not None:
            alert_text.append(f"Outside Temperature: {temp_c:.1f}°C\n")

        percent_complete = latest_alert.get("percent_complete", 0) or 0
        bar_width = 30
        filled = int(bar_width * percent_complete / 100)
        bar = "[" + "█" * filled + " " * (bar_width - filled) + f"] {percent_complete:.0f}%"
        alert_text.append(f"Progress: {bar}\n", style="bold green")

        alert_panel = Panel(alert_text, title="🛬 Most Recent Alert", border_style="green")
    else:
        alert_panel = Panel(Text("No recent alerts."), title="🛬 Most Recent Alert", border_style="dim")

    # ─── Combine All ────────────────────────────────────────
    layout = Group(
        Align.center(header_panel),
        table,
        alert_panel
    )

    return layout


import psutil

def get_system_info():
    # Get temperature
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp_c = int(f.read()) / 1000
    except:
        temp_c = None

    # Get memory usage
    mem = psutil.virtual_memory()
    mem_usage = f"{mem.percent}%"

    # Get CPU usage
    cpu_usage = f"{psutil.cpu_percent(interval=None)}%"

    return temp_c, mem_usage, cpu_usage


def save_flight_alert_to_json(alert_data):
    try:
        with open(ALERT_JSON_FILE, "w") as f:
            json.dump(alert_data, f, indent=2)
        print(f"✅ Saved latest flight alert to {ALERT_JSON_FILE}")
    except Exception as e:
        print(f"⚠️ Failed to save flight alert JSON: {e}")

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def closest_approach_distance(lat1, lon1, heading_deg, ref_lat, ref_lon):
    R = 6371
    heading_rad = radians(heading_deg)
    lat1_rad = radians(lat1)
    lon1_rad = radians(lon1)
    distance_ahead = 100
    proj_lat = degrees(asin(sin(lat1_rad)*cos(distance_ahead/R) +
                            cos(lat1_rad)*sin(distance_ahead/R)*cos(heading_rad)))
    proj_lon = degrees(lon1_rad + atan2(
        sin(heading_rad)*sin(distance_ahead/R)*cos(lat1_rad),
        cos(distance_ahead/R) - sin(lat1_rad)*sin(radians(proj_lat))))
    line = LineString([(lat1, lon1), (proj_lat, proj_lon)])
    return Point(ref_lat, ref_lon).distance(line) * 111


def get_temperature(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m"
        resp = requests.get(url, timeout=5)
        return resp.json().get("current", {}).get("temperature_2m")
    except:
        return None


def lookup_adsbdb_info(hexcode):
    hexcode = hexcode.lower()

    # Skip known invalid hexes
    if hexcode in adsbdb_invalid_hexes:
        return None


    url = f"https://api.adsbdb.com/v0/aircraft/{hexcode}"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json().get("response", {}).get("aircraft", {})
            info = {
                "type": data.get("type", "n/a"),
                "icao_type": data.get("icao_type", "n/a"),
                "manufacturer": data.get("manufacturer", "n/a"),
                "registration": data.get("registration", "n/a"),
                "operator": data.get("registered_owner", "n/a"),
                "country": data.get("registered_owner_country_name", "n/a"),
            }
            return info

        elif response.status_code == 404:
            print(f"❌ ADSBdb API 404: {hexcode} not found.")
        else:
            print(f"❌ ADSBdb API error for {hexcode}: {response.status_code}")
    except Exception as e:
        print(f"⚠️ ADSBdb API request failed for {hexcode}: {e}")
    return None


async def scrape_flightaware(flight_number):
    if not flight_number or flight_number == "n/a":
        return None
    url = f'https://www.flightaware.com/live/flight/{flight_number}'
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/115.0.0.0 Safari/537.36'
        )
    }
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')
    except Exception as e:
        print(f"❌ Error scraping FlightAware {flight_number}: {e}")
        return None

    idx1 = html.find('trackpollBootstrap = ')
    if idx1 == -1:
        return None

    json_block = html[idx1 + 21:]
    idx2 = json_block.find(';</script>')
    if idx2 == -1:
        return None

    json_text = json_block[:idx2].strip()

    try:
        data = json.loads(json_text)
        flight_key = list(data["flights"].keys())[0]
        flight = data["flights"][flight_key]

        def parse_time(ts):
            if ts:
                return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            return "n/a"

        flightaware_data = {
            "flight": flight.get("friendlyIdent", flight_number),
            "origin": flight.get("origin", {}).get("friendlyLocation", "n/a"),
            "origin_iata": flight.get("origin", {}).get("iata", "n/a"),
            "destination": flight.get("destination", {}).get("friendlyLocation", "n/a"),
            "destination_iata": flight.get("destination", {}).get("iata", "n/a"),
            "departure_time_estimated": parse_time(flight.get("gateDepartureTimes", {}).get("estimated")),
            "departure_time_actual": parse_time(flight.get("gateDepartureTimes", {}).get("actual")),
            "takeoff_time_estimated": parse_time(flight.get("takeoffTimes", {}).get("estimated")),
            "takeoff_time_actual": parse_time(flight.get("takeoffTimes", {}).get("actual")),
            "landing_time_estimated": parse_time(flight.get("landingTimes", {}).get("estimated")),
            "landing_time_actual": parse_time(flight.get("landingTimes", {}).get("actual")),
            "arrival_time_estimated": parse_time(flight.get("gateArrivalTimes", {}).get("estimated")),
            "arrival_time_actual": parse_time(flight.get("gateArrivalTimes", {}).get("actual")),
            "distance_elapsed_nm": flight.get("distance", {}).get("elapsed", "n/a"),
            "distance_remaining_nm": flight.get("distance", {}).get("remaining", "n/a"),
        }
        del html
        del json_text
        return flightaware_data

    except Exception as e:
        print(f"❌ FlightAware JSON parse error for {flight_number}: {e}")
        del html
        del json_text
        return None

def safe_float(value, default=0.0):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def clear_console():
    os.system('cls' if os.name == 'nt' else 'clear')

from datetime import datetime, timedelta

# Store seen aircraft hexes and their data with timestamps

async def main_loop():
    global spinner_index, last_alert_write_time, latest_alert, current_temperature_c
    global last_refresh_start, last_card_launch_time, within_schedule

    aircraft_list = {}
    last_alerted_flight = None  # Track last alerted flight number
    png_path = "/usr/share/skyaware/html/flight_card.png"

    refresh_ready = False
    refresh_ready_time = None

    with Live(render_dashboard(aircraft_list, latest_alert, spinner_frames[spinner_index], within_schedule), refresh_per_second=1, screen=True) as live:
        while True:
            try:
                with open(AIRCRAFT_JSON_PATH) as f:
                    data = json.load(f)
            except Exception:
                await asyncio.sleep(SCAN_INTERVAL_SECONDS)
                continue

            now = datetime.utcnow()
            seen_this_loop = set()

            within_schedule = is_within_schedule(now)
            # Remove forced override; respect schedule as is

            for ac in data.get("aircraft", []):
                lat, lon, hexcode, flight = ac.get("lat"), ac.get("lon"), ac.get("hex"), ac.get("flight")
                if lat is None or lon is None or flight is None or not hexcode:
                    continue

                hexcode = hexcode.lower()
                seen_this_loop.add(hexcode)

                if hexcode not in aircraft_list:
                    adsb_info = lookup_adsbdb_info(hexcode) or {}
                else:
                    adsb_info = aircraft_list[hexcode].get("adsb", {})

                distance_km = haversine(REFERENCE_LAT, REFERENCE_LON, lat, lon)

                try:
                    heading = float(ac.get("track"))
                    bullseye_km = closest_approach_distance(lat, lon, heading, REFERENCE_LAT, REFERENCE_LON)
                except (TypeError, ValueError):
                    heading = None
                    bullseye_km = None

                is_closing = (
                    bullseye_km is not None
                    and bullseye_km <= BULLSEYE_ALERT_KM
                    and hexcode not in BLACKLISTED_HEX
                )

                prev_flightaware = aircraft_list.get(hexcode, {}).get("flightaware", {})

                aircraft_list[hexcode] = {
                    "hex": hexcode,
                    "flight": ac.get("flight", "").strip(),
                    "lat": lat,
                    "lon": lon,
                    "distance": distance_km,
                    "altitude": ac.get("alt_baro", "n/a"),
                    "speed": ac.get("gs", "n/a"),
                    "heading": ac.get("track", "n/a"),
                    "adsb": adsb_info,
                    "bullseye_km": bullseye_km,
                    "is_closing": is_closing,
                    "alerted": aircraft_list.get(hexcode, {}).get("alerted", False),
                    "last_seen": now,
                    "flightaware": prev_flightaware,
                }

            # Prune old aircraft
            cutoff = now - timedelta(minutes=15)
            for hexcode in list(aircraft_list.keys()):
                if hexcode not in seen_this_loop and aircraft_list[hexcode]["last_seen"] < cutoff:
                    del aircraft_list[hexcode]
                    gc.collect()

            json_path = ALERT_JSON_FILE

            matching_aircraft = [
                ac for ac in aircraft_list.values()
                if ac.get("flight")
                and ac["distance"] is not None and ac["distance"] <= DISTANCE_ALERT_KM
                and ac["is_closing"]
            ]
            matching_aircraft.sort(key=lambda x: x["distance"])

            if matching_aircraft:
                ac = matching_aircraft[0]
                flight = ac["flight"]
                hexcode = ac["hex"]

                # Decide if starting a refresh cycle
                start_refresh_cycle = False
                if last_refresh_start is None:
                    start_refresh_cycle = True
                else:
                    seconds_since_last_refresh_start = (now - last_refresh_start).total_seconds()
                    if seconds_since_last_refresh_start >= MIN_ALERT_INTERVAL:
                        start_refresh_cycle = True

                if start_refresh_cycle:
                    last_refresh_start = now
                    refresh_ready = False
                    refresh_ready_time = None
                    last_png_mtime = get_mtime(png_path)

                    try:
                        fa_info = await scrape_flightaware(flight) if flight else {}
                    except Exception:
                        fa_info = {}
                    gc.collect()
                    aircraft_list[hexcode]["flightaware"] = fa_info

                    temperature_c = get_temperature(ac["lat"], ac["lon"])
                    current_temperature_c = temperature_c

                    eta_minutes = None
                    try:
                        landing_time_str = fa_info.get("landing_time_estimated")
                        if landing_time_str:
                            landing_time = datetime.strptime(landing_time_str, "%Y-%m-%d %H:%M:%S UTC")
                            eta_minutes = int((landing_time - now).total_seconds() / 60)
                    except Exception:
                        pass
                    fa_info["eta_minutes"] = eta_minutes

                    percent_complete = None
                    try:
                        actual_takeoff_str = fa_info.get("takeoff_time_actual")
                        estimated_arrival_str = fa_info.get("arrival_time_estimated")
                        if actual_takeoff_str and estimated_arrival_str:
                            actual_takeoff = datetime.strptime(actual_takeoff_str, "%Y-%m-%d %H:%M:%S UTC")
                            estimated_arrival = datetime.strptime(estimated_arrival_str, "%Y-%m-%d %H:%M:%S UTC")
                            total_duration = (estimated_arrival - actual_takeoff).total_seconds()
                            elapsed = (now - actual_takeoff).total_seconds()
                            if total_duration > 0:
                                percent_complete = max(0, min(100, (elapsed / total_duration) * 100))
                    except Exception:
                        percent_complete = None
                    fa_info["percent_complete"] = percent_complete

                    # Launch card5.py only if within_schedule is True
                    subprocess.Popen([sys.executable, 'card5.py'],
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    last_card_launch_time = last_refresh_start

                else:
                    fa_info = aircraft_list.get(hexcode, {}).get("flightaware", {}) or {}
                    temperature_c = get_temperature(ac["lat"], ac["lon"])
                    current_temperature_c = temperature_c

                    eta_minutes = None
                    try:
                        landing_time_str = fa_info.get("landing_time_estimated")
                        if landing_time_str:
                            landing_time = datetime.strptime(landing_time_str, "%Y-%m-%d %H:%M:%S UTC")
                            eta_minutes = int((landing_time - now).total_seconds() / 60)
                    except Exception:
                        pass
                    fa_info["eta_minutes"] = eta_minutes

                    percent_complete = None
                    try:
                        actual_takeoff_str = fa_info.get("takeoff_time_actual")
                        estimated_arrival_str = fa_info.get("arrival_time_estimated")
                        if actual_takeoff_str and estimated_arrival_str:
                            actual_takeoff = datetime.strptime(actual_takeoff_str, "%Y-%m-%d %H:%M:%S UTC")
                            estimated_arrival = datetime.strptime(estimated_arrival_str, "%Y-%m-%d %H:%M:%S UTC")
                            total_duration = (estimated_arrival - actual_takeoff).total_seconds()
                            elapsed = (now - actual_takeoff).total_seconds()
                            if total_duration > 0:
                                percent_complete = max(0, min(100, (elapsed / total_duration) * 100))
                    except Exception:
                        percent_complete = None
                    fa_info["percent_complete"] = percent_complete

            else:
                ac = None
                fa_info = {}
                flight = None
                temperature_c = current_temperature_c  # maintain previous temperature or None

            # Determine refresh_flag only if within_schedule and aircraft present
            refresh_flag = False
            if within_schedule and ac:
                if flight != last_alerted_flight:
                    current_png_mtime = get_mtime(png_path)
                    if not refresh_ready and current_png_mtime > last_png_mtime:
                        refresh_ready = True
                        refresh_ready_time = now
                    if refresh_ready and refresh_ready_time is not None:
                        elapsed_ms = (now - refresh_ready_time).total_seconds() * 1000.0
                        if elapsed_ms <= REFRESH_DURATION_MS:
                            refresh_flag = True
                            last_alerted_flight = flight
                        else:
                            refresh_ready = False
                            refresh_ready_time = None
                else:
                    refresh_flag = False
            else:
                last_alerted_flight = None

            latest_alert = {
                "display": within_schedule,
                "refresh": refresh_flag,
                "refreshInterval": 10000,
                "png_url": "http://192.168.0.105:8080/flight_card.png" if within_schedule and ac else "",
                "flight": flight or "",
                "aircraft_info": ac["adsb"] if ac else {},
                "flight_info": fa_info,
                "eta_minutes": fa_info.get("eta_minutes") if fa_info else None,
                "speed": ac.get("speed") if ac else None,
                "heading": ac.get("heading") if ac else None,
                "altitude": ac.get("altitude") if ac else None,
                "bullseye_km": ac.get("bullseye_km") if ac else None,
                "flight_progress": 100 - (fa_info.get("distance_remaining_nm", 0) /
                                          max((fa_info.get("distance_elapsed_nm", 1) +
                                               fa_info.get("distance_remaining_nm", 0)), 1)) * 100
                                    if fa_info.get("distance_remaining_nm") and fa_info.get("distance_elapsed_nm") else 0,
                "departure_time_actual": fa_info.get("departure_time_actual"),
                "arrival_time_estimated": fa_info.get("arrival_time_estimated"),
                "percent_complete": fa_info.get("percent_complete"),
                "temperature_c": temperature_c,
            }

            with open(json_path, 'w') as f:
                json.dump(latest_alert, f)

            if ac:
                aircraft_list[ac["hex"]]["alerted"] = True

            spinner_index = (spinner_index + 1) % len(spinner_frames)
            spinner_frame = spinner_frames[spinner_index]
            live.update(render_dashboard(aircraft_list, latest_alert, spinner_frame, within_schedule, current_temperature_c))
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\nExiting...")




