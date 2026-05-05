#!/usr/bin/env python3
"""
MK Trucks Inventory Sync
Descarga attachments de Gmail (WheelPros + RoughCountry),
genera CSVs de Matrixify y los importa a Shopify via Matrixify API.
"""

import os
import base64
import json
import re
import tempfile
import time
import requests
import pandas as pd
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── Credenciales desde GitHub Secrets ──────────────────────────────────────
GMAIL_CLIENT_ID     = os.environ["GMAIL_CLIENT_ID"]
GMAIL_CLIENT_SECRET = os.environ["GMAIL_CLIENT_SECRET"]
GMAIL_REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]
MATRIXIFY_API_KEY   = os.environ["MATRIXIFY_API_KEY"]
SHOPIFY_STORE       = os.environ["SHOPIFY_STORE"]

# ── Config ──────────────────────────────────────────────────────────────────
WP_SENDERS   = ["noreply@wheelpros.com", "data@wheelpros.com", "edi@wheelpros.com"]
RC_SENDERS   = ["noreply@roughcountry.com", "data@roughcountry.com", "jobber@roughcountry.com"]

MATRIXIFY_API = f"https://app.matrixify.app/api/v1/stores/{SHOPIFY_STORE}"


def get_gmail_service():
    creds = Credentials(
        token=None,
        refresh_token=GMAIL_REFRESH_TOKEN,
        client_id=GMAIL_CLIENT_ID,
        client_secret=GMAIL_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://mail.google.com/"]
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


def search_emails(service, query, max_results=10):
    result = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    return result.get("messages", [])


def get_attachments(service, msg_id):
    msg = service.users().messages().get(userId="me", id=msg_id).execute()
    attachments = []
    parts = msg.get("payload", {}).get("parts", [])
    for part in parts:
        if part.get("filename") and part.get("body", {}).get("attachmentId"):
            att_id = part["body"]["attachmentId"]
            att = service.users().messages().attachments().get(
                userId="me", messageId=msg_id, attachmentId=att_id
            ).execute()
            data = base64.urlsafe_b64decode(att["data"])
            attachments.append((part["filename"], data))
    return attachments


def find_latest_attachments(service, senders, file_extensions=(".csv", ".xlsx")):
    sender_query = " OR ".join([f"from:{s}" for s in senders])
    query = f"({sender_query}) has:attachment newer_than:7d"
    messages = search_emails(service, query)
    for msg in messages:
        attachments = get_attachments(service, msg["id"])
        matched = [(fn, data) for fn, data in attachments
                   if any(fn.lower().endswith(ext) for ext in file_extensions)]
        if matched:
            print(f"  ✅ Encontrado: {[fn for fn, _ in matched]}")
            return matched
    return []


def process_wheelpros(attachments):
    wheel_inv = access_inv = tire_inv = None
    wheel_tech = access_tech = tire_tech = lights_tech = None

    for filename, data in attachments:
        fn = filename.lower()
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(filename)[1], delete=False) as f:
            f.write(data)
            tmp = f.name
        try:
            if "wheelinv" in fn or ("wheel" in fn and "inv" in fn and "price" in fn):
                wheel_inv = pd.read_csv(tmp, low_memory=False)
            elif "accessori" in fn and "inv" in fn:
                access_inv = pd.read_csv(tmp, low_memory=False)
            elif "tire" in fn and "inv" in fn:
                tire_inv = pd.read_csv(tmp, low_memory=False)
            elif "wheel_tech" in fn or ("wheel" in fn and "tech" in fn):
                wheel_tech = pd.read_csv(tmp, low_memory=False)
            elif "accessory_tech" in fn or ("access" in fn and "tech" in fn):
                access_tech = pd.read_csv(tmp, low_memory=False)
            elif "tire_tech" in fn or ("tire" in fn and "tech" in fn):
                tire_tech = pd.read_csv(tmp, low_memory=False)
            elif "lighting" in fn:
                lights_tech = pd.read_csv(tmp, low_memory=False)
        finally:
            os.unlink(tmp)

    rows = []

    def index_tech(df, sku_col, img_cols):
        if df is None:
            return {}
        result = {}
        for _, row in df.iterrows():
            sku = str(row[sku_col]).strip()
            imgs = [str(row[c]).strip() for c in img_cols
                    if c in row and pd.notna(row[c]) and str(row[c]).strip() not in ("", "nan")]
            result[sku] = imgs
        return result

    w_imgs = index_tech(wheel_tech,  "sku", ["image_url","image_url1","image_url2","image_url3","image_url4"])
    a_imgs = index_tech(access_tech, "sku", ["image_url","image_url1","image_url2","image_url3","image_url4"])
    t_imgs = index_tech(tire_tech,   "sku", ["image_url"])
    l_imgs = index_tech(lights_tech, "SKU", [f"ImageLink{i}" for i in range(1, 16)])

    def add_rows(inv_df, tech_idx):
        if inv_df is None:
            return
        active = inv_df[inv_df["TotalQOH"] > 0]
        for _, row in active.iterrows():
            sku = str(row["PartNumber"]).strip()
            handle = sku.lower().replace(" ", "-")
            desc = str(row.get("PartDescription", "")).strip()[:255]
            images = []
            inv_img = str(row.get("ImageURL", "")).strip()
            if inv_img not in ("", "nan"):
                images.append(inv_img)
            for img in tech_idx.get(sku, []):
                if img not in images:
                    images.append(img)
            if not images:
                return
            for pos, url in enumerate(images, 1):
                rows.append({"Handle": handle, "Command": "MERGE",
                             "Image Src": url, "Image Position": pos,
                             "Image Alt Text": desc})

    add_rows(wheel_inv, w_imgs)
    add_rows(access_inv, a_imgs)
    add_rows(tire_inv, t_imgs)

    for sku, imgs in l_imgs.items():
        for pos, img in enumerate(imgs, 1):
            rows.append({"Handle": sku.lower(), "Command": "MERGE",
                         "Image Src": img, "Image Position": pos, "Image Alt Text": ""})

    df = pd.DataFrame(rows)
    print(f"  WP: {df['Handle'].nunique()} productos, {len(df)} imágenes")
    return df


def process_roughcountry(attachments):
    rows = []
    for filename, data in attachments:
        fn = filename.lower()
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(filename)[1], delete=False) as f:
            f.write(data)
            tmp = f.name
        try:
            if fn.endswith(".xlsx"):
                rc = pd.read_excel(tmp, sheet_name="General")
            elif fn.endswith(".csv"):
                rc = pd.read_csv(tmp, low_memory=False)
            else:
                continue
            rc_active = rc[rc["availability"].astype(str) != "Out Of Stock"]
            for _, row in rc_active.iterrows():
                sku = str(row["sku"]).strip()
                handle = sku.lower().replace(" ", "-")
                title = str(row.get("title", "")).strip()[:255]
                images = []
                for i in range(1, 7):
                    img = str(row.get(f"image_{i}", "")).strip()
                    if img not in ("", "nan"):
                        images.append(img)
                if not images:
                    continue
                for pos, url in enumerate(images, 1):
                    rows.append({"Handle": handle, "Command": "MERGE",
                                 "Image Src": url, "Image Position": pos,
                                 "Image Alt Text": title})
        finally:
            os.unlink(tmp)

    df = pd.DataFrame(rows)
    print(f"  RC: {df['Handle'].nunique()} productos, {len(df)} imágenes")
    return df


def upload_to_matrixify(csv_path, label):
    print(f"  Subiendo {label} a Matrixify...")
    with open(csv_path, "rb") as f:
        resp = requests.post(
            f"{MATRIXIFY_API}/imports",
            headers={"Authorization": f"Bearer {MATRIXIFY_API_KEY}"},
            files={"file": (os.path.basename(csv_path), f, "text/csv")},
            data={"reimport": "true"}
        )
    resp.raise_for_status()
    job = resp.json()
    print(f"  ✅ {label} importando — Job ID: {job.get('id')}")
    return job.get("id")


def main():
    print("🚀 Iniciando MK Trucks Inventory Sync...")
    print("\n📧 Conectando a Gmail...")
    service = get_gmail_service()

    print("\n📦 Buscando archivos WheelPros...")
    wp_attachments = find_latest_attachments(service, WP_SENDERS)
    if wp_attachments:
        wp_df = process_wheelpros(wp_attachments)
        if not wp_df.empty:
            wp_csv = "/tmp/WheelPros_Images_Matrixify.csv"
            wp_df.to_csv(wp_csv, index=False)
            upload_to_matrixify(wp_csv, "WheelPros")
    else:
        print("  ⚠️ No se encontraron emails de WheelPros en los últimos 7 días")

    print("\n📦 Buscando archivos RoughCountry...")
    rc_attachments = find_latest_attachments(service, RC_SENDERS, (".xlsx", ".csv"))
    if rc_attachments:
        rc_df = process_roughcountry(rc_attachments)
        if not rc_df.empty:
            rc_csv = "/tmp/RoughCountry_Images_Matrixify.csv"
            rc_df.to_csv(rc_csv, index=False)
            upload_to_matrixify(rc_csv, "RoughCountry")
    else:
        print("  ⚠️ No se encontraron emails de RoughCountry en los últimos 7 días")

    print("\n✅ Sync completado!")


if __name__ == "__main__":
    main()
