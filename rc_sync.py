#!/usr/bin/env python3
import os, base64, requests, pandas as pd

GITHUB_TOKEN      = os.environ["GITHUB_TOKEN"]
MATRIXIFY_API_KEY = os.environ["MATRIXIFY_API_KEY"]
GITHUB_REPO       = "mktrucks2030/mktrucks-inventory-sync"

def generate_rc_inventory_csv():
    print("📦 Reading jobber_pc1.xlsx...")
    rc = pd.read_excel("jobber_pc1.xlsx", sheet_name="General")
    print(f"  Loaded {len(rc)} rows")

    # Filter out Out of Stock
    rc_active = rc[rc["availability"].astype(str) != "Out Of Stock"].copy()
    print(f"  Active products: {len(rc_active)}")

    rows = []
    for _, row in rc_active.iterrows():
        sku = str(row["sku"]).strip()
        availability = str(row["availability"]).strip()
        in_stock = 1 if availability == "In Stock" or "Please allow" in availability else 0
        qty = int(row.get("NV_Stock", 0) or 0) + int(row.get("TN_Stock", 0) or 0)
        if qty == 0 and in_stock:
            qty = 1  # At least 1 if available

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

def trigger_matrixify(raw_url, label):
    print(f"  Scheduling Matrixify import for {label}...")
    headers = {"Authorization": f"Bearer {MATRIXIFY_API_KEY}",
               "Content-Type": "application/json"}
    resp = requests.post(
        "https://mcp.matrixify.app/mcp/import/create_from_url",
        headers=headers,
        json={"remote": {"uri": raw_url}}
    )
    print(f"  Response: {resp.status_code}")
    if resp.status_code in (200, 201):
        print(f"  ✅ {label} import triggered!")
    else:
        print(f"  ⚠️ Could not trigger Matrixify — CSV is ready at: {raw_url}")
        print(f"  Import manually at: https://app.matrixify.app")

def main():
    print("🚀 RC Inventory Sync starting...")
    df = generate_rc_inventory_csv()
    if df.empty:
        print("  ⚠️ No data generated")
        return
    raw_url = upload_to_github("/tmp/rc_inventory.csv", "rc_inventory.csv")
    trigger_matrixify(raw_url, "RoughCountry Inventory")
    print("\n✅ Done!")

if __name__ == "__main__":
    main()
