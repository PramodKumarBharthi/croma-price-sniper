import os
import sys
import time
import random
from typing import List, Dict, Any
from dotenv import load_dotenv
from curl_cffi import requests

from scraper import fetch_croma_product
from notifier import send_price_alert, send_tracking_started_alert

# Load environment variables
load_dotenv()

GAS_WEBHOOK_URL = os.environ.get("GAS_WEBHOOK_URL")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "online_tracker_2026")


def sync_tracker():
    """
    Main loop to sync Google Sheet items with live Croma prices and dispatch alerts.
    """
    if not GAS_WEBHOOK_URL:
        print("[Error] GAS_WEBHOOK_URL is not set in environment or .env file", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("Starting Online Price Sniper - Google Sheet Sync")
    print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Webhook URL: {GAS_WEBHOOK_URL}")
    print(f"Notification Topic: {NTFY_TOPIC}")
    print("=" * 60)

    session = requests.Session()

    # 1. Fetch product rows from Google Apps Script
    print("\n[1/3] Fetching tracked products from Google Sheet...")
    try:
        get_resp = session.get(GAS_WEBHOOK_URL, allow_redirects=True, timeout=20)
        if get_resp.status_code != 200:
            print(f"[Error] Failed to fetch sheet data. HTTP {get_resp.status_code}: {get_resp.text}", file=sys.stderr)
            return False

        items: List[Dict[str, Any]] = get_resp.json()
        print(f"✓ Retrieved {len(items)} row(s) from Google Sheet.")
    except Exception as e:
        print(f"[Error] Exception fetching from GAS_WEBHOOK_URL: {e}", file=sys.stderr)
        return False

    if not items:
        print("No products found in Google Sheet to track.")
        return True

    updates: List[Dict[str, Any]] = []

    # 2. Iterate through each product and scrape live data
    print("\n[2/3] Processing products and checking prices...")
    for idx, item in enumerate(items):
        row_num = item.get("row")
        url = item.get("url")
        pincode = str(item.get("pincode") or "500083")
        current_name = item.get("name") or ""
        
        try:
            initial_price = float(item.get("initialPrice") or 0)
        except (ValueError, TypeError):
            initial_price = 0.0

        try:
            lowest_price = float(item.get("lowestPrice") or 0)
        except (ValueError, TypeError):
            lowest_price = 0.0

        print(f"\n--- [Row {row_num}] Processing Product ---")
        print(f"URL: {url}")
        print(f"Current Name: '{current_name}' | Pincode: {pincode}")
        print(f"Stored Baseline: Rs. {initial_price:,.0f} | Stored Lowest: Rs. {lowest_price:,.0f}")

        if not url:
            print(f"[Skip] Row {row_num} has no URL.")
            continue

        try:
            scraped = fetch_croma_product(url, pincode=pincode, session=session)
            live_price = float(scraped["live_price"])
            scraped_name = scraped.get("name") or ""
            product_name = current_name if current_name else scraped_name
            source = scraped.get("source", "unknown")

            print(f"✓ Scraped Title: {product_name}")
            print(f"✓ Live Price: Rs. {live_price:,.0f} (Extracted via {source})")

            # Check if this is the first time tracking this item
            if initial_price == 0:
                print(f"[Onboarding] Initial tracking baseline set to Rs. {live_price:,.0f}")
                new_initial_price = live_price
                new_lowest_price = live_price

                # Send one-time onboarding notification
                send_tracking_started_alert(
                    product_name=product_name,
                    live_price=live_price,
                    url=url,
                    pincode=pincode,
                )
            else:
                new_initial_price = initial_price
                reference_price = min(initial_price, lowest_price) if lowest_price > 0 else initial_price

                if live_price < reference_price:
                    print(f"🚨 PRICE DROP DETECTED! Rs. {live_price:,.0f} < Reference Rs. {reference_price:,.0f}")
                    send_price_alert(
                        product_name=product_name,
                        live_price=live_price,
                        reference_price=reference_price,
                        url=url,
                        pincode=pincode,
                    )
                    new_lowest_price = live_price
                else:
                    print(f"ℹ No price drop. Current: Rs. {live_price:,.0f} >= Reference: Rs. {reference_price:,.0f}")
                    new_lowest_price = min(lowest_price, live_price) if lowest_price > 0 else live_price

            update_entry = {
                "row": row_num,
                "name": product_name,
                "initialPrice": new_initial_price,
                "currentPrice": live_price,
                "lowestPrice": new_lowest_price,
            }
            updates.append(update_entry)

        except Exception as e:
            print(f"[Error] Failed to scrape row {row_num} ({url}): {e}", file=sys.stderr)

        # Politeness delay between product requests
        if idx < len(items) - 1:
            delay = round(random.uniform(1.5, 3.5), 2)
            print(f"Waiting {delay}s before next product...")
            time.sleep(delay)

    # 3. Batch post updates back to Google Sheet
    if updates:
        print(f"\n[3/3] Sending batch update of {len(updates)} record(s) to Google Sheet...")
        try:
            post_resp = session.post(
                GAS_WEBHOOK_URL,
                json=updates,
                headers={"Content-Type": "application/json"},
                allow_redirects=True,
                timeout=30,
            )
            print(f"GAS Webhook Response: HTTP {post_resp.status_code}")
            print(f"Response Body: {post_resp.text}")
            if post_resp.status_code == 200:
                print("✓ Successfully synchronized updates with Google Sheet!")
                return True
            else:
                print(f"[Error] Failed to update Google Sheet: {post_resp.text}", file=sys.stderr)
                return False
        except Exception as e:
            print(f"[Error] Exception sending batch update to GAS: {e}", file=sys.stderr)
            return False
    else:
        print("\nNo updates to send back to Google Sheet.")
        return True


if __name__ == "__main__":
    success = sync_tracker()
    sys.exit(0 if success else 1)
