#!/usr/bin/env python3
import os, base64, tempfile, re, requests, pandas as pd, zipfile, io, hashlib, time
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

GMAIL_CLIENT_ID     = os.environ["GMAIL_CLIENT_ID"]
GMAIL_CLIENT_SECRET = os.environ["GMAIL_CLIENT_SECRET"]
GMAIL_REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]
MATRIXIFY_API_KEY   = os.environ["MATRIXIFY_API_KEY"]
GITHUB_TOKEN        = os.environ["GITHUB_TOKEN"]
RC_EMAIL            = os.environ["RC_EMAIL"]
RC_PASSWORD         = os.environ["RC_PASSWORD"]

GITHUB_REPO   = "mktrucks2030/mktrucks-inventory-sync"
MATRIXIFY_API = "https://app.matrixify.app/api/v1"

def get_gmail_service():
    creds = Credentials(token=None, refresh_token=GMAIL_REFRESH_TOKEN,
        client_id=GMAIL_CLIENT_ID, client_secret=GMAIL_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://mail.google.com/"])
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)

def get_email_body(service, msg_id):
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    body = ""
    def extract_parts(parts):
        nonlocal body
        for part in parts:
            if part.get("mimeType") in ("text/html", "text/plain"):
                data = part.get("body", {}).get("data", "")
                if data:
                    body += base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
            if part.get("parts"):
                extract_parts(part["parts"])
    payload = msg.get("payload", {})
    if payload.get("parts"):
        extract_parts(payload["parts"])
    else:
        data = payload.get("body", {}).get("data", "")
        if data:
            body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    return body

def download_wheelpros(service):
    print("  Searching WheelPros inventory emails...")
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    all_files = []
    query = 'subject:"WHEEL PROS INVENTORY FEED IS READY" newer_than:2d'
    msgs = service.users().messages().list(userId="me", q=query, maxResults=5).execute().get("messages", [])
    print(f"  Found {len(msgs)} inventory email(s)")
    for msg in msgs[:1]:
        body = get_email_body(service, msg["id"])
        urls = re.findall(r'https://backend\.api\.data\.wheelpros\.com/[^\s"\'<>]+', body)
        hrefs = re.findall(r'href=["\']([^"\']*wheelpros\.com[^"\']*)["\']', body)
        all_urls = list(set([u.replace("&amp;", "&") for u in urls + hrefs]))
        print(f"  Found {len(all_urls)} download URL(s)")
        for url in all_urls:
            try:
                r = session.get(url, timeout=180)
                if r.status_code != 200 or len(r.content) < 1000:
                    continue
                if r.content[:4] == b"PK\x03\x04":
                    print(f"  Downloaded ZIP: {len(r.content):,} bytes")
                    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                        for name in zf.namelist():
                            data = zf.read(name)
                            print(f"    Extracted: {name} ({len(data):,} bytes)")
                            all_files.append((name, data))
                else:
                    fn_match = re.search(r'files=([^&]+)', url)
                    filename = fn_match.group(1).split(",")[0] if fn_match else "wp_data.csv"
                    if not filename.lower().endswith(".csv"):
                        filename += ".csv"
                    all_files.append((filename, r.content))
            except Exception as e:
                print(f"  Error: {e}")
    return all_files

def download_roughcountry():
    print("  Logging in to RoughCountry...")
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    login_page = session.get("https://www.roughcountry.com/account/login")
    csrf_token = ""
    match = re.search(r'name=["\']csrf[_-]?token["\'][^>]*value=["\']([^"\']+)["\']', login_page.text, re.I)
    if not match:
        match = re.search(r'value=["\']([^"\']+)["\'][^>]*name=["\']csrf[_-]?token["\']', login_page.text, re.I)
    if match:
        csrf_token = match.group(1)
    resp = session.post("https://www.roughcountry.com/account/login",
                        data={"email": RC_EMAIL, "password": RC_PASSWORD, "csrf_token": csrf_token},
                        allow_redirects=True)
    print(f"  Login: {resp.status_code} {resp.url}")
    downloads_page = session.get("https://www.roughcountry.com/account/downloads")
    print(f"  Downloads page: {downloads_page.status_code}")
    links = re.findall(r'href=["\']([^"\']*(?:jobber|inventory|download)[^"\']*)["\']', downloads_page.text, re.I)
    if not links:
        links = re.findall(r'href=["\']([^"\']*\.(?:xlsx|csv)[^"\']*)["\']', downloads_page.text, re.I)
    print(f"  Links found: {links[:3]}")
    for link in links[:5]:
        url = link if link.startswith("http") else f"https://www.roughcountry.com{link}"
        r = session.get(url)
        if r.status_code == 200 and len(r.content) > 1000:
            ext = ".xlsx" if r.content[:4] == b"PK\x03\x04" else ".csv"
            print(f"  Downloaded: {len(r.content):,} bytes")
            return [(f"roughcountry{ext}", r.content)]
    for url in ["https://www.roughcountry.com/account/downloads/jobber",
                "https://www.roughcountry.com/media/downloads/jobber_pc1.xlsx"]:
        r = session.get(url)
        if r.status_code == 200 and len(r.content) > 1000:
            ext = ".xlsx" if r.content[:4] == b"PK\x03\x04" else ".csv"
            print(f"  Downloaded from {url}")
            return [(f"roughcountry{ext}", r.content)]
    return []

def index_tech(df, sku_col, img_cols):
    if df is None: return {}
    result = {}
    for _, row in df.iterrows():
        sku = str(row[sku_col]).strip()
        imgs = [str(row[c]).strip() for c in img_cols if c in row and pd.notna(row[c]) and str(row[c]).strip() not in ("","nan")]
        result[sku] = imgs
    return result

def process_wheelpros(attachments):
    wheel_inv = access_inv = tire_inv = None
    wheel_tech = access_tech = tire_tech = lights_tech = None
    for filename, data in attachments:
        fn = filename.lower()
        fn_base = fn.split("/")[-1].split("\\")[-1]
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(data); tmp = f.name
        try:
            df_tmp = pd.read_csv(tmp, low_memory=False)
            print(f"  File: {fn_base} | cols: {list(df_tmp.columns)[:4]}")
            if "wheelinvpricedata" in fn_base: wheel_inv = df_tmp; print(f"  → Wheel inv: {len(wheel_inv)}")
            elif "tireinvpricedata" in fn_base: tire_inv = df_tmp; print(f"  → Tire inv: {len(tire_inv)}")
            elif "accessoriesinvpricedata" in fn_base: access_inv = df_tmp; print(f"  → Access inv: {len(access_inv)}")
            elif "wheel_techguide" in fn_base: wheel_tech = df_tmp
            elif "accessory_techguide" in fn_base: access_tech = df_tmp
            elif "tire_techguide" in fn_base: tire_tech = df_tmp
            elif "lighting_techguide" in fn_base: lights_tech = df_tmp
            else: print(f"  → Unrecognized: {fn_base}")
        except Exception as e:
            print(f"  Error: {e}")
        finally:
            os.unlink(tmp)
    w_imgs = index_tech(wheel_tech,  "sku", ["image_url","image_url1","image_url2","image_url3","image_url4"])
    a_imgs = index_tech(access_tech, "sku", ["image_url","image_url1","image_url2","image_url3","image_url4"])
    t_imgs = index_tech(tire_tech,   "sku", ["image_url"])
    l_imgs = index_tech(lights_tech, "SKU", [f"ImageLink{i}" for i in range(1,16)])
    rows = []
    def add_rows(inv_df, tech_idx, label):
        if inv_df is None: print(f"  No {label} — skipping"); return
        active = inv_df[inv_df["TotalQOH"] > 0]
        print(f"  {label}: {len(active)} active SKUs")
        for _, row in active.iterrows():
            sku = str(row["PartNumber"]).strip()
            images = []
            inv_img = str(row.get("ImageURL","")).strip()
            if inv_img not in ("","nan"): images.append(inv_img)
            for img in tech_idx.get(sku, []):
                if img not in images: images.append(img)
            if not images: return
            for pos, url in enumerate(images, 1):
                rows.append({"Handle": sku.lower(), "Command": "MERGE", "Image Src": url,
                             "Image Position": pos, "Image Alt Text": str(row.get("PartDescription",""))[:255]})
    add_rows(wheel_inv, w_imgs, "Wheels")
    add_rows(access_inv, a_imgs, "Accessories")
    add_rows(tire_inv, t_imgs, "Tires")
    for sku, imgs in l_imgs.items():
        for pos, img in enumerate(imgs, 1):
            rows.append({"Handle": sku.lower(), "Command": "MERGE", "Image Src": img, "Image Position": pos, "Image Alt Text": ""})
    if not rows: print("  ⚠️ No WP rows"); return pd.DataFrame()
    df = pd.DataFrame(rows)
    print(f"  WP TOTAL: {df['Handle'].nunique():,} products, {len(df):,} images")
    return df

def process_roughcountry(attachments):
    rows = []
    for filename, data in attachments:
        fn = filename.lower()
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(filename)[1], delete=False) as f:
            f.write(data); tmp = f.name
        try:
            rc = pd.read_excel(tmp, sheet_name="General") if fn.endswith(".xlsx") else pd.read_csv(tmp, low_memory=False)
            print(f"  RC loaded: {len(rc)} rows")
            active = rc[rc["availability"].astype(str) != "Out Of Stock"]
            for _, row in active.iterrows():
                sku = str(row["sku"]).strip()
                images = [str(row.get(f"image_{i}","")).strip() for i in range(1,7)
                          if str(row.get(f"image_{i}","")).strip() not in ("","nan")]
                if not images: continue
                for pos, url in enumerate(images, 1):
                    rows.append({"Handle": sku.lower(), "Command": "MERGE", "Image Src": url,
                                 "Image Position": pos, "Image Alt Text": str(row.get("title",""))[:255]})
        finally:
            os.unlink(tmp)
    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows)
    print(f"  RC TOTAL: {df['Handle'].nunique():,} products, {len(df):,} images")
    return df

def upload_csv_to_github(csv_path, filename):
    """Sube CSV a GitHub y retorna URL publica raw."""
    print(f"  Uploading {filename} to GitHub...")
    with open(csv_path, "rb") as f:
        content = f.read()
    content_b64 = base64.b64encode(content).decode()
    headers = {"Authorization": f"token {GITHUB_TOKEN}",
               "Accept": "application/vnd.github.v3+json"}
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/temp/{filename}"
    # Check if file exists to get SHA
    get_resp = requests.get(url, headers=headers)
    sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None
    payload = {"message": f"sync: update {filename}", "content": content_b64}
    if sha:
        payload["sha"] = sha
    put_resp = requests.put(url, headers=headers, json=payload)
    print(f"  GitHub upload: {put_resp.status_code}")
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/temp/{filename}"
    return raw_url

def upload_to_matrixify(raw_url, label):
    """Importa CSV a Shopify via Matrixify desde URL."""
    print(f"  Triggering Matrixify import for {label}...")
    headers = {"Authorization": f"Bearer {MATRIXIFY_API_KEY}",
               "Content-Type": "application/json"}
    resp = requests.post(f"{MATRIXIFY_API}/imports",
        headers=headers,
        json={"import": {"remote_url": raw_url}})
    print(f"  Matrixify response: {resp.status_code}")
    if resp.status_code not in (200, 201):
        print(f"  Error: {resp.text[:300]}")
        return None
    job = resp.json()
    job_id = job.get("id")
    print(f"  ✅ {label} import started — Job ID: {job_id}")
    return job_id

def main():
    print("🚀 Starting MK Trucks Inventory Sync...")
    service = get_gmail_service()

    print("\n📦 Downloading WheelPros from email...")
    wp_atts = download_wheelpros(service)
    if wp_atts:
        wp_df = process_wheelpros(wp_atts)
        if not wp_df.empty:
            wp_df.to_csv("/tmp/Products.csv", index=False)
            raw_url = upload_csv_to_github("/tmp/Products.csv", "wp_products.csv")
            upload_to_matrixify(raw_url, "WheelPros")
        else:
            print("  ⚠️ WP DataFrame empty")
    else:
        print("  No WheelPros data found")

    print("\n📦 Downloading RoughCountry from portal...")
    rc_atts = download_roughcountry()
    if rc_atts:
        rc_df = process_roughcountry(rc_atts)
        if not rc_df.empty:
            rc_df.to_csv("/tmp/Products_RC.csv", index=False)
            raw_url = upload_csv_to_github("/tmp/Products_RC.csv", "rc_products.csv")
            upload_to_matrixify(raw_url, "RoughCountry")
        else:
            print("  ⚠️ RC DataFrame empty")
    else:
        print("  Could not download RC file")

    print("\n✅ Sync complete!")

if __name__ == "__main__":
    main()
