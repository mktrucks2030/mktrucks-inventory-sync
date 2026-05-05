#!/usr/bin/env python3
import os, base64, tempfile, re, requests, pandas as pd
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

GMAIL_CLIENT_ID     = os.environ["GMAIL_CLIENT_ID"]
GMAIL_CLIENT_SECRET = os.environ["GMAIL_CLIENT_SECRET"]
GMAIL_REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]
MATRIXIFY_API_KEY   = os.environ["MATRIXIFY_API_KEY"]
SHOPIFY_STORE       = os.environ["SHOPIFY_STORE"]
RC_EMAIL            = os.environ["RC_EMAIL"]
RC_PASSWORD         = os.environ["RC_PASSWORD"]

MATRIXIFY_API = f"https://app.matrixify.app/api/v1/stores/{SHOPIFY_STORE}"

def get_gmail_service():
    creds = Credentials(token=None, refresh_token=GMAIL_REFRESH_TOKEN,
        client_id=GMAIL_CLIENT_ID, client_secret=GMAIL_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://mail.google.com/"])
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)

def get_email_body(service, msg_id):
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    parts = msg.get("payload", {}).get("parts", [])
    body = ""
    # Check parts for HTML/text
    for part in parts:
        if part.get("mimeType") in ("text/html", "text/plain"):
            data = part.get("body", {}).get("data", "")
            if data:
                body += base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    # If no parts, check body directly
    if not body:
        data = msg.get("payload", {}).get("body", {}).get("data", "")
        if data:
            body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    return body

def download_wheelpros(service):
    """Busca emails de WheelPros por subject y descarga los archivos via links."""
    print("  Searching WheelPros emails by subject...")
    query = 'subject:"WHEEL PROS TECH DATA IS READY" newer_than:2d'
    msgs = service.users().messages().list(userId="me", q=query, maxResults=5).execute().get("messages", [])
    
    if not msgs:
        query = 'subject:"WHEEL PROS" subject:"DATA IS READY" newer_than:7d'
        msgs = service.users().messages().list(userId="me", q=query, maxResults=5).execute().get("messages", [])

    if not msgs:
        print("  No WheelPros emails found")
        return []

    print(f"  Found {len(msgs)} WheelPros email(s)")
    
    all_files = []
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

    for msg in msgs[:1]:  # Process most recent email only
        body = get_email_body(service, msg["id"])
        # Extract all download URLs
        urls = re.findall(r'https://backend\.api\.data\.wheelpros\.com/[^\s"\'<>]+', body)
        # Also check href links
        hrefs = re.findall(r'href=["\']([^"\']*wheelpros\.com[^"\']*)["\']', body)
        urls = list(set(urls + hrefs))
        print(f"  Found {len(urls)} download URLs")

        for url in urls:
            # Clean URL (remove HTML entities)
            url = url.replace("&amp;", "&")
            try:
                r = session.get(url, timeout=60)
                if r.status_code == 200 and len(r.content) > 1000:
                    # Detect filename from URL or content
                    fn_match = re.search(r'files=([^&]+)', url)
                    filename = fn_match.group(1) if fn_match else "wp_data.csv"
                    ext = ".xlsx" if r.content[:4] == b"PK\x03\x04" else ".csv"
                    if not filename.lower().endswith(ext):
                        filename = filename + ext
                    print(f"  Downloaded: {filename} ({len(r.content)} bytes)")
                    all_files.append((filename, r.content))
            except Exception as e:
                print(f"  Error downloading {url[:80]}: {e}")

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
            print(f"  Downloaded: {len(r.content)} bytes")
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
    wheel_inv = access_inv = tire_inv = wheel_tech = access_tech = tire_tech = lights_tech = None
    for filename, data in attachments:
        fn = filename.lower()
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(filename)[1], delete=False) as f:
            f.write(data); tmp = f.name
        try:
            if "wheelinv" in fn or ("wheel" in fn and "inv" in fn and "price" in fn): wheel_inv = pd.read_csv(tmp, low_memory=False)
            elif "accessori" in fn and "inv" in fn: access_inv = pd.read_csv(tmp, low_memory=False)
            elif "tire" in fn and "inv" in fn: tire_inv = pd.read_csv(tmp, low_memory=False)
            elif "wheel_tech" in fn or ("wheel" in fn and "tech" in fn): wheel_tech = pd.read_csv(tmp, low_memory=False)
            elif "accessory_tech" in fn or ("access" in fn and "tech" in fn): access_tech = pd.read_csv(tmp, low_memory=False)
            elif "tire_tech" in fn or ("tire" in fn and "tech" in fn): tire_tech = pd.read_csv(tmp, low_memory=False)
            elif "lighting" in fn: lights_tech = pd.read_csv(tmp, low_memory=False)
        finally: os.unlink(tmp)
    w_imgs = index_tech(wheel_tech, "sku", ["image_url","image_url1","image_url2","image_url3","image_url4"])
    a_imgs = index_tech(access_tech, "sku", ["image_url","image_url1","image_url2","image_url3","image_url4"])
    t_imgs = index_tech(tire_tech, "sku", ["image_url"])
    l_imgs = index_tech(lights_tech, "SKU", [f"ImageLink{i}" for i in range(1,16)])
    rows = []
    def add_rows(inv_df, tech_idx):
        if inv_df is None: return
        for _, row in inv_df[inv_df["TotalQOH"] > 0].iterrows():
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
    add_rows(wheel_inv, w_imgs); add_rows(access_inv, a_imgs); add_rows(tire_inv, t_imgs)
    for sku, imgs in l_imgs.items():
        for pos, img in enumerate(imgs, 1):
            rows.append({"Handle": sku.lower(), "Command": "MERGE", "Image Src": img, "Image Position": pos, "Image Alt Text": ""})
    df = pd.DataFrame(rows)
    print(f"  WP: {df['Handle'].nunique()} products, {len(df)} images")
    return df

def process_roughcountry(attachments):
    rows = []
    for filename, data in attachments:
        fn = filename.lower()
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(filename)[1], delete=False) as f:
            f.write(data); tmp = f.name
        try:
            rc = pd.read_excel(tmp, sheet_name="General") if fn.endswith(".xlsx") else pd.read_csv(tmp, low_memory=False)
            for _, row in rc[rc["availability"].astype(str) != "Out Of Stock"].iterrows():
                sku = str(row["sku"]).strip()
                images = [str(row.get(f"image_{i}","")).strip() for i in range(1,7) if str(row.get(f"image_{i}","")).strip() not in ("","nan")]
                if not images: continue
                for pos, url in enumerate(images, 1):
                    rows.append({"Handle": sku.lower(), "Command": "MERGE", "Image Src": url,
                                 "Image Position": pos, "Image Alt Text": str(row.get("title",""))[:255]})
        finally: os.unlink(tmp)
    df = pd.DataFrame(rows)
    print(f"  RC: {df['Handle'].nunique()} products, {len(df)} images")
    return df

def upload_to_matrixify(csv_path, label):
    print(f"  Uploading {label}...")
    with open(csv_path, "rb") as f:
        resp = requests.post(f"{MATRIXIFY_API}/imports",
            headers={"Authorization": f"Bearer {MATRIXIFY_API_KEY}"},
            files={"file": (os.path.basename(csv_path), f, "text/csv")},
            data={"reimport": "true"})
    resp.raise_for_status()
    job = resp.json()
    print(f"  Job ID: {job.get('id')}")
    return job.get("id")

def main():
    print("Starting MK Trucks Inventory Sync...")
    service = get_gmail_service()

    print("\nDownloading WheelPros from email links...")
    wp_atts = download_wheelpros(service)
    if wp_atts:
        wp_df = process_wheelpros(wp_atts)
        if not wp_df.empty:
            wp_df.to_csv("/tmp/wp.csv", index=False)
            upload_to_matrixify("/tmp/wp.csv", "WheelPros")
    else:
        print("  No WheelPros data found")

    print("\nDownloading RoughCountry from portal...")
    rc_atts = download_roughcountry()
    if rc_atts:
        rc_df = process_roughcountry(rc_atts)
        if not rc_df.empty:
            rc_df.to_csv("/tmp/rc.csv", index=False)
            upload_to_matrixify("/tmp/rc.csv", "RoughCountry")
    else:
        print("  Could not download RC file")

    print("\nSync complete!")

if __name__ == "__main__":
    main()
