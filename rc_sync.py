#!/usr/bin/env python3
import os, base64, requests, pandas as pd, glob

GITHUB_TOKEN      = os.environ["GITHUB_TOKEN"]
MATRIXIFY_API_KEY = os.environ["MATRIXIFY_API_KEY"]
GITHUB_REPO       = "mktrucks2030/mktrucks-inventory-sync"

def find_rc_file():
    """Find the jobber xlsx file regardless of exact name."""
    patterns = ["jobber*.xlsx", "jobber*.xlsm", "jobber_pc*.xlsx"]
    for pattern in patterns:
        files = glob.glob(pattern)
        if files:
            print(f"  Found RC file: {files[0]}")
            return files[0]
    return None

def generate_rc_inventory_csv():
    print("📦 Reading RC file...")
    filepath = find_rc_file()
    if not filepath:
        print("  ❌ No jobber file found!")
        return pd.DataFrame()

    rc = pd.read_excel(filepath, sheet_name="General")
    print(f"  Loaded {len(rc)} rows")

    rc_active = rc[rc["availability"].astype(str) != "Out Of Stock"].copy()
    print(f"  Active products: {len(rc_active)}")

    rows = []
    for _, row in rc_active.iterrows():
        sku = str(row["sku"]).strip()
        availability = str(row["availability"]).strip()
        qty = int(row.get("NV_Stock", 0) or 0) + int(row.get("TN_Stock", 0) or 0)
        if qty == 0 and availability != "Out Of Stock":
            qty = 1

        rows.append({
            "Handle": sku.lower().replace(" ", "-"),
            "Command": "MERGE",
            "Variant SKU": sku,
            "Variant Inventory Qty": qty,
            "Variant Inventory Policy": "deny",
        })

    df = pd.DataFrame(rows)
    df.to_csv("/tmp/rc_inventory.csv", index=False)
    print(f"  Generated {len(df)} rows")
    return df

def upload_to_github(csv_path, filename):
    print(f"  Uploading {filename} to GitHub...")
    with open(csv_path, "rb") as f:
        content = f.read()
    content_b64 = base64.b64encode(content).decode()
    headers = {"Authorization": f"token {GITHUB_TOKEN}",
               "Accept": "application/vnd.github.v3+json"}
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/temp/{filename}"
    get_resp = requests.get(url, headers=headers)
    sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None
    payload = {"message": f"sync: update {filename}", "content": content_b64}
    if sha:
        payload["sha"] = sha
    put_resp = requests.put(url, headers=headers, json=payload)
    print(f"  GitHub upload: {put_resp.status_code}")
    return f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/temp/{filename}"

def main():
    print("🚀 RC Inventory Sync starting...")
    df = generate_rc_inventory_csv()
    if df.empty:
        print("  ⚠️ No data generated")
        return
    raw_url = upload_to_github("/tmp/rc_inventory.csv", "rc_inventory.csv")
    print(f"  ✅ RC inventory CSV ready at: {raw_url}")
    print(f"  Import manually at: https://app.matrixify.app")
    print("\n✅ Done!")

if __name__ == "__main__":
    main()
