import os, json

CSV_FILE = "jobs.csv"
JSON_FILE = "desc.json"

latest_csv_batch = []
latest_ts = None

if os.path.exists(CSV_FILE):
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()[1:]  # Skip header
        if lines:
            # Grab the universal timestamp from the very last entry
            latest_ts = lines[-1].strip().split(",")[0]

            for line in lines:
                parts = line.strip().split(",")
                # SAFETY GATE: Ignore lines that don't have all 4 columns
                if len(parts) < 4:
                    continue

                if parts[0] == latest_ts:
                    latest_csv_batch.append({
                        "time": parts[0],
                        "title": parts[1],
                        "description": parts[2],
                        "link": parts[3]
                    })

# Sync logic: Only update if the batch ID is different
json_ts = None
if os.path.exists(JSON_FILE):
    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if data: json_ts = data[0].get("time")
    except:
        pass

if latest_ts and latest_ts != json_ts:
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(latest_csv_batch, f, indent=4)
    print(f"Desc: Staged batch {latest_ts} ({len(latest_csv_batch)} jobs)")
else:
    print("Desc: No new run detected or timestamp matches.")