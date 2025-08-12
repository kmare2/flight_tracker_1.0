import asyncio
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Config
CARD_JSON_FILE = "/usr/share/skyaware/html/flight_card.html"
DB_FILE = "flights_stats.db"
REGISTRATION_EXPIRY_SECONDS = 300  # 5 minutes

OUTPUT_DIR = "/usr/share/skyaware/html/top10"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "index.html")

# Tracking last update per registration
last_update_per_reg = {}

def utcnow():
    return datetime.now(timezone.utc)

def parse_time(ts: str):
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    except Exception:
        return None

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS flight_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_utc TEXT NOT NULL,
            registration TEXT NOT NULL,
            manufacturer TEXT,
            model TEXT,
            origin_iata TEXT,
            origin_name TEXT,
            destination_iata TEXT,
            destination_name TEXT,
            operator TEXT,
            flight_number TEXT,
            speed REAL,
            altitude INTEGER
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_registration_time ON flight_events (registration, timestamp_utc)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON flight_events (timestamp_utc)")
        conn.commit()

def insert_flight_event(data):
    ts = utcnow()
    registration = data.get('aircraft_info', {}).get('registration')
    if not registration:
        return False

    # Skip if updated within 5 minutes for this registration
    last_ts = last_update_per_reg.get(registration)
    if last_ts and (ts - last_ts).total_seconds() < REGISTRATION_EXPIRY_SECONDS:
        return False
    last_update_per_reg[registration] = ts

    aircraft_info = data.get('aircraft_info', {})
    flight_info = data.get('flight_info', {})
    speed = data.get('speed')
    altitude = data.get('altitude')

    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO flight_events (
                timestamp_utc, registration, manufacturer, model, origin_iata, origin_name,
                destination_iata, destination_name, operator, flight_number, speed, altitude
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ts.isoformat(),
            registration,
            aircraft_info.get('manufacturer'),
            aircraft_info.get('type'),
            flight_info.get('origin_iata'),
            flight_info.get('origin'),
            flight_info.get('destination_iata'),
            flight_info.get('destination'),
            aircraft_info.get('operator'),
            data.get('flight'),
            speed,
            altitude
        ))
        conn.commit()
    return True

class JsonFileHandler(FileSystemEventHandler):
    def __init__(self, file_path, loop):
        self.file_path = file_path
        self.loop = loop

    def on_modified(self, event):
        if event.src_path == self.file_path:
            # Schedule the synchronous function to run in the event loop thread
            self.loop.call_soon_threadsafe(process_json_file_and_update_html)


def process_json_file_and_update_html():
    try:
        with open(CARD_JSON_FILE, 'r') as f:
            text = f.read()
        if not text.strip():
            return
        data = json.loads(text)
    except Exception as e:
        print(f"Error reading/parsing JSON: {e}")
        return

    inserted = insert_flight_event(data)
    reg = data.get('aircraft_info', {}).get('registration', 'Unknown')
    if inserted:
        print(f"Inserted/updated flight for reg: {reg}")
    else:
        print(f"Skipped insert for reg: {reg} (recent update)")

    save_dashboard_html()



def query_top_10(period: str):
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    if period == 'today':
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == 'week':
        start = now - timedelta(days=7)
    else:
        start = None  # all time

    def q(col, extra_cols=None):
        extra = ""
        if extra_cols:
            extra = ", " + ", ".join(extra_cols)
        base = f"SELECT {col}, COUNT(*) as cnt{extra} FROM flight_events"
        if start:
            base += f" WHERE timestamp_utc >= '{start.isoformat()}'"
        base += f" GROUP BY {col}"
        if extra_cols:
            group_cols = ", ".join([col] + extra_cols)
            base = f"SELECT {group_cols}, COUNT(*) as cnt FROM flight_events"
            if start:
                base += f" WHERE timestamp_utc >= '{start.isoformat()}'"
            base += f" GROUP BY {group_cols}"
        base += " ORDER BY cnt DESC LIMIT 10"
        return base

    c.execute(q('manufacturer'))
    manufacturers = c.fetchall()

    c.execute(q('model'))
    models = c.fetchall()

    c.execute(q('origin_iata', ['origin_name']))
    origins = c.fetchall()

    c.execute(q('destination_iata', ['destination_name']))
    destinations = c.fetchall()

    c.execute(q('operator'))
    operators = c.fetchall()

    c.execute(q('flight_number'))
    flight_numbers = c.fetchall()

    speed_query = "SELECT registration, MAX(speed) as max_speed FROM flight_events"
    if start:
        speed_query += f" WHERE timestamp_utc >= '{start.isoformat()}'"
    speed_query += " GROUP BY registration ORDER BY max_speed DESC LIMIT 3"
    c.execute(speed_query)
    fastest_flights = c.fetchall()

    conn.close()
    return {
        "manufacturers": manufacturers,
        "models": models,
        "origins": origins,
        "destinations": destinations,
        "operators": operators,
        "flight_numbers": flight_numbers,
        "fastest_flights": fastest_flights,
    }

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Flight Stats Dashboard</title>
<style>
  body {{
    font-family: Arial, sans-serif;
    margin: 1rem;
    background: #f0f2f5;
    color: #333;
  }}
  h1 {{
    text-align: center;
  }}
  .tabs {{
    display: flex;
    justify-content: center;
    margin-bottom: 1rem;
  }}
  .tab-btn {{
    background: #e0e0e0;
    border: none;
    padding: 0.6rem 1.2rem;
    margin: 0 0.3rem;
    font-weight: bold;
    cursor: pointer;
    border-radius: 4px 4px 0 0;
  }}
  .tab-btn.active {{
    background: #007bff;
    color: white;
  }}
  .tab-content {{
    background: white;
    padding: 1rem;
    border-radius: 0 8px 8px 8px;
    box-shadow: 0 4px 10px rgb(0 0 0 / 0.1);
    max-width: 960px;
    margin: 0 auto;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 0.5rem;
  }}
  th, td {{
    border-bottom: 1px solid #ddd;
    padding: 0.5rem 0.7rem;
    text-align: left;
  }}
  th {{
    background: #007bff;
    color: white;
  }}
  caption {{
    font-weight: bold;
    margin-top: 1rem;
  }}
  .fastest {{
    background-color: #d1e7dd;
  }}
</style>
</head>
<body>

<h1>📊 Flight Stats Dashboard</h1>

<div class="tabs">
  <button class="tab-btn active" data-tab="today">Today</button>
  <button class="tab-btn" data-tab="week">This Week</button>
  <button class="tab-btn" data-tab="all">All Time</button>
</div>

<div id="today" class="tab-content">
  {content_today}
</div>

<div id="week" class="tab-content" style="display:none;">
  {content_week}
</div>

<div id="all" class="tab-content" style="display:none;">
  {content_all}
</div>

<script>
  const tabs = document.querySelectorAll('.tab-btn');
  const contents = document.querySelectorAll('.tab-content');

  tabs.forEach(tab => {{
    tab.addEventListener('click', () => {{
      tabs.forEach(t => t.classList.remove('active'));
      contents.forEach(c => c.style.display = 'none');
      tab.classList.add('active');
      document.getElementById(tab.dataset.tab).style.display = 'block';
    }});
  }});
</script>

</body>
</html>
"""

def render_table(rows, cols):
    html = "<table><thead><tr>"
    for c in cols:
        html += f"<th>{c.replace('_', ' ').title()}</th>"
    html += "</tr></thead><tbody>"
    for row in rows:
        html += "<tr>"
        for c in cols:
            val = row[c]
            if val is None:
                val = "-"
            html += f"<td>{val}</td>"
        html += "</tr>"
    html += "</tbody></table>"
    return html

def render_top10_content(data):
    html = ""

    html += "<h2>🏭 Top 10 Manufacturers</h2>"
    html += render_table(data["manufacturers"], ["manufacturer", "cnt"])

    html += "<h2>✈️ Top 10 Models</h2>"
    html += render_table(data["models"], ["model", "cnt"])

    html += "<h2>🌍 Top 10 Origins</h2>"
    html += render_table(data["origins"], ["origin_iata", "origin_name", "cnt"])

    html += "<h2>🏁 Top 10 Destinations</h2>"
    html += render_table(data["destinations"], ["destination_iata", "destination_name", "cnt"])

    html += "<h2>🧑‍✈️ Top 10 Operators</h2>"
    html += render_table(data["operators"], ["operator", "cnt"])

    html += "<h2>🎫 Top 10 Flight Numbers</h2>"
    html += render_table(data["flight_numbers"], ["flight_number", "cnt"])

    html += '<h2 style="margin-top:2rem;">🚀 Top 3 Fastest Flights</h2>'
    html += "<table><thead><tr><th>Registration</th><th>Max Speed (knots)</th></tr></thead><tbody>"
    for row in data["fastest_flights"]:
        reg = row["registration"] or "-"
        speed = f"{row['max_speed']:.1f}" if row["max_speed"] else "-"
        html += f"<tr class='fastest'><td>{reg}</td><td>{speed}</td></tr>"
    html += "</tbody></table>"

    return html

def save_dashboard_html():
    data_today = query_top_10("today")
    data_week = query_top_10("week")
    data_all = query_top_10("all")

    content_today = render_top10_content(data_today)
    content_week = render_top10_content(data_week)
    content_all = render_top10_content(data_all)

    html = HTML_TEMPLATE.format(
        content_today=content_today,
        content_week=content_week,
        content_all=content_all,
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard HTML updated: {OUTPUT_FILE}")

async def main():
    init_db()
    loop = asyncio.get_event_loop()
    event_handler = JsonFileHandler(CARD_JSON_FILE, loop)
    observer = Observer()
    observer.schedule(event_handler, path=os.path.dirname(CARD_JSON_FILE) or '.', recursive=False)
    observer.start()

    # Call WITHOUT await here since function is sync
    process_json_file_and_update_html()

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("Stopping...")
        observer.stop()
        observer.join()


if __name__ == "__main__":
    asyncio.run(main())

