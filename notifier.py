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
) -> bool:
    """
    Sends an urgent price drop push notification via ntfy.sh using curl_cffi.
    """
    drop_amt = reference_price - live_price
    endpoint = f"https://ntfy.sh/{NTFY_TOPIC}"

    headers = {
        "Title": f"PRICE DROP: Rs. {live_price:,.0f} (-Rs. {drop_amt:,.0f})",
        "Priority": "urgent",
        "Tags": "rotating_light,money_with_wings",
        "Click": url,
        "Actions": f"view, Buy Now on Croma, {url}, clear=true",
    }

    body = (
        f"Product: {product_name}\n"
        f"New Price: Rs. {live_price:,.0f}\n"
        f"Previous Price: Rs. {reference_price:,.0f}\n"
        f"Amount Saved: Rs. {drop_amt:,.0f}\n"
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
            print(f"[Notifier] Price alert sent successfully to {endpoint} (HTTP 200)")
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
) -> bool:
    """
    Sends a one-time onboarding notification when a product is newly tracked.
    """
    endpoint = f"https://ntfy.sh/{NTFY_TOPIC}"

    headers = {
        "Title": f"TRACKING STARTED: Rs. {live_price:,.0f}",
        "Priority": "default",
        "Tags": "white_check_mark,eyes",
        "Click": url,
        "Actions": f"view, View on Croma, {url}, clear=true",
    }

    body = (
        f"Tracking Started!\n"
        f"Product: {product_name}\n"
        f"Baseline Price: Rs. {live_price:,.0f}\n"
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
