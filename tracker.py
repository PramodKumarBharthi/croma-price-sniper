import os
import sys
import time
import random
import re
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
    Main loop to sync Google Sheet items with live Croma prices,
    evaluate bank discount offers, compare Net Effective Prices, and dispatch alerts.
    """
    if not GAS_WEBHOOK_URL:
        print("[Error] GAS_WEBHOOK_URL is not set in environment or .env file", file=sys.stderr)
        sys.exit(1)

    print("=" * 65)
    print("Starting Online Price Sniper - Net Effective Price Tracker")
    print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Webhook URL: {GAS_WEBHOOK_URL}")
    print(f"Notification Topic: {NTFY_TOPIC}")
    preferred_banks = os.environ.get("PREFERRED_BANKS", "")
    if preferred_banks:
        print(f"Preferred Banks: {preferred_banks}")
    print("=" * 65)

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

    # 2. Iterate through each product, scrape live data & bank offers
    print("\n[2/3] Processing products, checking bank offers & net prices...")
    for idx, item in enumerate(items):
        row_num = item.get("row")
        url = item.get("url")
        pincode = str(item.get("pincode") or "500083")
        current_name = item.get("name") or ""

        # Strip any existing '[Bank Offer: ...]' tag from the stored name
        clean_base_name = re.sub(r'\s*\[Bank Offer:.*?\]', '', current_name).strip()

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
        print(f"Current Name: '{clean_base_name}' | Pincode: {pincode}")
        print(f"Stored Baseline: Rs. {initial_price:,.0f} | Stored Lowest Net: Rs. {lowest_price:,.0f}")

        if not url:
            print(f"[Skip] Row {row_num} has no URL.")
            continue

        try:
            scraped = fetch_croma_product(url, pincode=pincode, session=session)
            live_price = float(scraped["live_price"])
            scraped_name = scraped.get("name") or ""
            source = scraped.get("source", "unknown")

            bank_discount = float(scraped.get("bank_discount") or 0.0)
            net_price = float(scraped.get("net_effective_price") or (live_price - bank_discount))
            bank_offer_summary = scraped.get("bank_offer_summary") or ""

            product_base_name = clean_base_name if clean_base_name else scraped_name

            # Format name with bank offer tag for Column A visibility
            if bank_discount > 0:
                sheet_display_name = f"{product_base_name} [Bank Offer: -Rs.{bank_discount:,.0f} | Net: Rs.{net_price:,.0f}]"
            else:
                sheet_display_name = product_base_name

            print(f"✓ Scraped Title: {product_base_name}")
            print(f"✓ Sticker Price: Rs. {live_price:,.0f} (via {source})")
            if bank_discount > 0:
                print(f"✓ Best Bank Offer: -Rs. {bank_discount:,.0f} ({bank_offer_summary})")
                print(f"✓ Net Effective Price: Rs. {net_price:,.0f}")
            else:
                print("✓ Bank Offers: No active instant bank discount detected.")

            # Onboarding run vs subsequent monitoring runs
            if initial_price == 0:
                print(f"[Onboarding] Initial tracking baseline: Rs. {live_price:,.0f} | Net: Rs. {net_price:,.0f}")
                new_initial_price = live_price
                new_lowest_price = net_price

                # Send one-time onboarding alert
                send_tracking_started_alert(
                    product_name=product_base_name,
                    live_price=live_price,
                    url=url,
                    pincode=pincode,
                    bank_discount=bank_discount,
                    net_effective_price=net_price,
                    bank_offer_summary=bank_offer_summary,
                )
            else:
                new_initial_price = initial_price
                reference_price = min(initial_price, lowest_price) if lowest_price > 0 else initial_price

                if net_price < reference_price:
                    drop_amount = reference_price - net_price
                    print(f"🚨 NET PRICE DROP DETECTED! Net Rs. {net_price:,.0f} < Reference Rs. {reference_price:,.0f} (-Rs. {drop_amount:,.0f})")
                    send_price_alert(
                        product_name=product_base_name,
                        live_price=live_price,
                        reference_price=reference_price,
                        url=url,
                        pincode=pincode,
                        bank_discount=bank_discount,
                        net_effective_price=net_price,
                        bank_offer_summary=bank_offer_summary,
                    )
                    new_lowest_price = net_price
                else:
                    print(f"ℹ No price drop. Net Rs. {net_price:,.0f} >= Reference Rs. {reference_price:,.0f}")
                    new_lowest_price = min(lowest_price, net_price) if lowest_price > 0 else net_price

            update_entry = {
                "row": row_num,
                "name": sheet_display_name,
                "initialPrice": new_initial_price,
                "currentPrice": live_price,
                "lowestPrice": new_lowest_price,
                "bankDiscount": bank_discount,
                "netEffectivePrice": net_price,
                "bankOffer": bank_offer_summary,
            }
            updates.append(update_entry)

        except Exception as e:
            print(f"[Error] Failed to process row {row_num} ({url}): {e}", file=sys.stderr)

        # Politeness delay between products
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
