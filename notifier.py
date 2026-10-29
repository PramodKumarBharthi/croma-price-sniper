import os
import sys
from dotenv import load_dotenv
from curl_cffi import requests

# Load environment variables from .env file if present
load_dotenv()

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "online_tracker_2026")


def send_price_alert(
    product_name: str,
    live_price: float,
    reference_price: float,
    url: str,
    pincode: str = "500083",
    bank_discount: float = 0.0,
    net_effective_price: float = 0.0,
    bank_offer_summary: str = "",
) -> bool:
    """
    Sends an urgent price drop push notification via ntfy.sh with Net Effective Price details.
    """
    effective_price = net_effective_price if net_effective_price > 0 else (live_price - bank_discount)
    drop_amt = reference_price - effective_price
    endpoint = f"https://ntfy.sh/{NTFY_TOPIC}"

    headers = {
        "Title": f"NET PRICE DROP: Rs. {effective_price:,.0f} (-Rs. {drop_amt:,.0f})",
        "Priority": "urgent",
        "Tags": "rotating_light,money_with_wings",
        "Click": url,
        "Actions": f"view, Buy Now on Croma, {url}, clear=true",
    }

    if bank_discount > 0:
        offer_str = f"-Rs. {bank_discount:,.0f} ({bank_offer_summary})"
    else:
        offer_str = "No active instant bank discount"

    body = (
        f"Product: {product_name}\n"
        f"Sticker Price: Rs. {live_price:,.0f}\n"
        f"Bank Offer: {offer_str}\n"
        f"Net Effective Price: Rs. {effective_price:,.0f} (Was Rs. {reference_price:,.0f})\n"
        f"Total Savings: Rs. {drop_amt:,.0f}\n"
        f"Pincode: {pincode}\n"
        f"Link: {url}"
    )

    try:
        response = requests.post(
            endpoint,
            data=body.encode("utf-8"),
            headers=headers,
            timeout=15,
        )
        if response.status_code == 200:
            print(f"[Notifier] Net price alert sent successfully to {endpoint} (HTTP 200)")
            return True
        else:
            print(
                f"[Notifier Error] HTTP {response.status_code}: {response.text}",
                file=sys.stderr,
            )
            return False
    except Exception as e:
        print(f"[Notifier Error] Failed to send price alert: {e}", file=sys.stderr)
        return False


def send_tracking_started_alert(
    product_name: str,
    live_price: float,
    url: str,
    pincode: str = "500083",
    bank_discount: float = 0.0,
    net_effective_price: float = 0.0,
    bank_offer_summary: str = "",
) -> bool:
    """
    Sends a one-time onboarding notification when a product is newly tracked.
    """
    effective_price = net_effective_price if net_effective_price > 0 else (live_price - bank_discount)
    endpoint = f"https://ntfy.sh/{NTFY_TOPIC}"

    if bank_discount > 0:
        title = f"TRACKING STARTED: Net Rs. {effective_price:,.0f}"
        offer_str = f"-Rs. {bank_discount:,.0f} ({bank_offer_summary})"
    else:
        title = f"TRACKING STARTED: Rs. {live_price:,.0f}"
        offer_str = "No active instant bank discount"

    headers = {
        "Title": title,
        "Priority": "default",
        "Tags": "white_check_mark,eyes",
        "Click": url,
        "Actions": f"view, View on Croma, {url}, clear=true",
    }

    body = (
        f"Tracking Started!\n"
        f"Product: {product_name}\n"
        f"Sticker Price: Rs. {live_price:,.0f}\n"
        f"Bank Offer: {offer_str}\n"
        f"Net Effective Price: Rs. {effective_price:,.0f}\n"
        f"Pincode: {pincode}\n"
        f"Link: {url}\n"
        f"Status: Actively monitoring 24/7 for price drops."
    )

    try:
        response = requests.post(
            endpoint,
            data=body.encode("utf-8"),
            headers=headers,
            timeout=15,
        )
        if response.status_code == 200:
            print(f"[Notifier] Tracking started alert sent to {endpoint} (HTTP 200)")
            return True
        else:
            print(
                f"[Notifier Error] HTTP {response.status_code}: {response.text}",
                file=sys.stderr,
            )
            return False
    except Exception as e:
        print(f"[Notifier Error] Failed to send onboarding alert: {e}", file=sys.stderr)
        return False
