import json
import re
import sys
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup
from curl_cffi import requests


def extract_sku(url: str) -> str:
    """
    Extracts the product SKU/ID appearing after '/p/' in a Croma URL.
    e.g. 'https://www.croma.com/apple-ipad-pro-.../p/324220' -> '324220'
    """
    match = re.search(r'/p/([a-zA-Z0-9]+)', url)
    if match:
        return match.group(1)
    # Fallback splitting
    if '/p/' in url:
        return url.split('/p/')[1].split('/')[0].split('?')[0]
    return ""


def clean_and_parse_json(text: str) -> Optional[Any]:
    """
    Robust JSON parser that handles unescaped control characters and extra trailing braces.
    """
    if not text:
        return None
    cleaned = text.strip()
    try:
        return json.loads(cleaned, strict=False)
    except Exception:
        pass

    # Replace newlines/tabs inside string literals
    cleaned_controls = re.sub(r'[\r\n\t]+', ' ', cleaned)
    try:
        return json.loads(cleaned_controls, strict=False)
    except Exception:
        pass

    # Handle extra trailing characters or brackets by finding outermost balanced curly braces
    m = re.search(r'(\{.*\})', cleaned, re.DOTALL)
    if m:
        candidate = m.group(1)
        depth = 0
        end_idx = 0
        for i, char in enumerate(candidate):
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    end_idx = i + 1
                    break
        if end_idx > 0:
            balanced = candidate[:end_idx]
            balanced_clean = re.sub(r'[\r\n\t]+', ' ', balanced)
            try:
                return json.loads(balanced_clean, strict=False)
            except Exception:
                pass
    return None


def _clean_price(val: Any) -> Optional[float]:
    """
    Converts various price formats (str, int, float, formatted with commas/currency) into a float.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        # Remove currency symbols, commas, spaces
        cleaned = re.sub(r'[^\d.]', '', val)
        if cleaned:
            try:
                return float(cleaned)
            except ValueError:
                return None
    return None


def fetch_croma_product(
    url: str,
    pincode: str = "500083",
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """
    Fetches product name and live price from Croma with a 3-tier fallback:
      Tier 1: Croma Internal APIs
      Tier 2: Page JSON-LD & Next.js state
      Tier 3: Page DOM selectors & Regex fallback
    Returns:
      {"sku": str, "name": str, "live_price": float, "url": str, "source": str}
    """
    sku = extract_sku(url)
    http = session or requests.Session()

    default_headers = {
        "Referer": "https://www.croma.com/",
        "Origin": "https://www.croma.com",
        "omp-pincode": pincode,
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    }

    # ==========================================
    # Tier 1: Croma Internal APIs
    # ==========================================
    if sku:
        api_endpoints = [
            f"https://api.croma.com/product/allchannels/v1/sku/{sku}",
            f"https://api.croma.com/searchservices/v1/search?currentPage=0&query={sku}:relevance&fields=FULL",
        ]
        for api_url in api_endpoints:
            try:
                resp = http.get(
                    api_url,
                    headers=default_headers,
                    impersonate="chrome",
                    timeout=10,
                )
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/json"):
                    data = resp.json()
                    name = data.get("name")
                    price_val = None
                    if "pdpPriceData" in data:
                        price_val = data["pdpPriceData"].get("sellingPrice", {}).get("value")
                    elif "price" in data:
                        price_val = data["price"].get("value") if isinstance(data["price"], dict) else data.get("price")
                    elif "products" in data and isinstance(data["products"], list) and len(data["products"]) > 0:
                        prod = data["products"][0]
                        name = name or prod.get("name")
                        price_val = prod.get("price", {}).get("value") if isinstance(prod.get("price"), dict) else prod.get("price")

                    cleaned_p = _clean_price(price_val)
                    if cleaned_p and cleaned_p > 0 and name:
                        return {
                            "sku": sku,
                            "name": name.strip(),
                            "live_price": cleaned_p,
                            "url": url,
                            "source": "tier1_api",
                        }
            except Exception as e:
                # Silently proceed to next endpoint/tier
                pass

    # ==========================================
    # Fetch Product Page HTML for Tier 2 and Tier 3
    # ==========================================
    page_resp = http.get(
        url,
        headers=default_headers,
        impersonate="chrome",
        timeout=15,
    )
    if page_resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch Croma page HTTP {page_resp.status_code} for {url}")

    html_text = page_resp.text
    soup = BeautifulSoup(html_text, "html.parser")

    # ==========================================
    # Tier 2: JSON-LD & Next.js State
    # ==========================================
    # 1. JSON-LD scripts
    json_ld_scripts = soup.find_all("script", type="application/ld+json")
    for script_elem in json_ld_scripts:
        script_content = script_elem.string or script_elem.text
        if not script_content:
            continue
        data = clean_and_parse_json(script_content)
        if isinstance(data, dict) and data.get("@type") == "Product":
            prod_name = data.get("name")
            offers = data.get("offers")
            offer_price = None
            if isinstance(offers, dict):
                offer_price = offers.get("price")
            elif isinstance(offers, list) and len(offers) > 0:
                offer_price = offers[0].get("price")

            price = _clean_price(offer_price)
            if price and price > 0 and prod_name:
                return {
                    "sku": sku,
                    "name": prod_name.strip(),
                    "live_price": price,
                    "url": url,
                    "source": "tier2_jsonld",
                }

    # 2. Next.js state script (__NEXT_DATA__)
    next_data_script = soup.find("script", id="__NEXT_DATA__")
    if next_data_script:
        data = clean_and_parse_json(next_data_script.string or next_data_script.text)
        if isinstance(data, dict):
            props = data.get("props", {}).get("pageProps", {})
            prod_data = props.get("product") or props.get("productDetails") or {}
            prod_name = prod_data.get("name")
            prod_price = prod_data.get("price", {}).get("value") if isinstance(prod_data.get("price"), dict) else prod_data.get("price")
            price = _clean_price(prod_price)
            if price and price > 0 and prod_name:
                return {
                    "sku": sku,
                    "name": prod_name.strip(),
                    "live_price": price,
                    "url": url,
                    "source": "tier2_next_data",
                }

    # ==========================================
    # Tier 3: DOM Selectors & Regex Fallback
    # ==========================================
    extracted_name = None
    extracted_price = None

    # DOM selectors for price
    price_selectors = [
        "#pdp-product-price",
        "[data-testid='new-price']",
        ".amount",
        ".pdp-price",
        ".new-price",
    ]
    for sel in price_selectors:
        elem = soup.select_one(sel)
        if elem:
            cleaned_p = _clean_price(elem.get_text(strip=True))
            if cleaned_p and cleaned_p > 0:
                extracted_price = cleaned_p
                break

    # DOM selectors for name
    name_selectors = [
        "h1.pdp-title",
        "h1.product-title",
        "h1",
        ".product-title",
    ]
    for sel in name_selectors:
        elem = soup.select_one(sel)
        if elem and elem.get_text(strip=True):
            extracted_name = elem.get_text(strip=True)
            break

    # Regex fallback for price
    if not extracted_price:
        # Check sellingPrice JSON snippet in page script
        m_selling = re.search(r'\"sellingPrice\":\s*\{\"value\":\s*\"([0-9\.]+)\"', html_text)
        if m_selling:
            extracted_price = _clean_price(m_selling.group(1))

        if not extracted_price:
            m_price = re.search(r'\"price\":\s*\"([0-9\.]+)\"', html_text)
            if m_price:
                extracted_price = _clean_price(m_price.group(1))

        if not extracted_price:
            # Match ₹ 1,36,990 or Rs. 1,36,990
            m_inr = re.search(r'(?:₹|Rs\.?)\s*([\d,]+(?:\.\d{1,2})?)', html_text)
            if m_inr:
                extracted_price = _clean_price(m_inr.group(1))

    # Regex fallback for name
    if not extracted_name:
        m_name = re.search(r'\"name\":\s*\"([^\"]+)\"', html_text)
        if m_name:
            extracted_name = m_name.group(1).encode().decode('unicode_escape', 'ignore').strip()
        elif soup.title and soup.title.string:
            title = soup.title.string.strip()
            # Clean title (e.g., 'Buy Apple iPad... Online at Best Price in India - Croma')
            extracted_name = re.sub(r'\s*\|\s*Croma.*$', '', title, flags=re.I)
            extracted_name = re.sub(r'Online at Best Price.*$', '', extracted_name, flags=re.I).strip()

    if extracted_price and extracted_price > 0:
        return {
            "sku": sku,
            "name": extracted_name or f"Croma Product {sku}",
            "live_price": extracted_price,
            "url": url,
            "source": "tier3_dom_regex",
        }

    raise ValueError(f"Could not extract price or name for Croma URL: {url}")
