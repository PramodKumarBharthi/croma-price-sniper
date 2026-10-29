import json
import os
import re
import sys
from typing import Optional, Dict, Any, List
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
        cleaned = re.sub(r'[^\d.]', '', val)
        if cleaned:
            try:
                return float(cleaned)
            except ValueError:
                return None
    return None


def extract_bank_offers(
    html: str,
    sku: str,
    live_price: float,
    session: Optional[requests.Session] = None,
    pincode: str = "500083",
) -> Dict[str, Any]:
    """
    Extracts and evaluates instant bank discounts/cashback offers from Croma HTML and state.
    Returns:
      {
        "bank_discount": float,
        "net_effective_price": float,
        "bank_offer_summary": str,
        "all_bank_offers": list[str]
      }
    """
    raw_offer_candidates: List[str] = []

    # 1. Parse window.__INITIAL_DATA__
    start = html.find('window.__INITIAL_DATA__')
    if start != -1:
        eq_pos = html.find('=', start)
        sub = html[eq_pos + 1:].strip()
        depth = 0
        end_idx = 0
        for i, c in enumerate(sub):
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end_idx = i + 1
                    break
        json_str = sub[:end_idx]
        json_str = re.sub(r':\s*undefined\b', ': null', json_str)
        try:
            state_data = json.loads(json_str, strict=False)
            pdp_data = state_data.get('pdpReducer', {}).get('pdpData', {})
            offer_keys = [
                'productMessage',
                'productMessage1',
                'productMessage2',
                'productMessage3',
                'potentialPromotions',
                'offerDetails',
                'storeoffer',
                'bankOffers',
                'promotions',
            ]
            for key in offer_keys:
                val = pdp_data.get(key)
                if isinstance(val, str) and val.strip():
                    raw_offer_candidates.append(val.strip())
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, str) and item.strip():
                            raw_offer_candidates.append(item.strip())
                        elif isinstance(item, dict):
                            for sub_k in ['message', 'title', 'description', 'offer', 'offerText', 'promoMessage']:
                                if item.get(sub_k):
                                    raw_offer_candidates.append(str(item[sub_k]).strip())
        except Exception:
            pass

    # 2. Extract from DOM offer containers
    soup = BeautifulSoup(html, 'html.parser')
    offer_selectors = [
        '[class*="offer"]',
        '[class*="bank"]',
        '[id*="offer"]',
        '[data-testid*="offer"]',
        '.bank-offers',
        '.offer-section',
    ]
    for sel in offer_selectors:
        for elem in soup.select(sel):
            txt = elem.get_text(separator=' ', strip=True)
            if txt and 10 <= len(txt) <= 250:
                raw_offer_candidates.append(txt)

    # 3. Deduplicate candidates
    unique_candidates: List[str] = []
    seen = set()
    for cand in raw_offer_candidates:
        normalized = ' '.join(cand.split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique_candidates.append(normalized)

    # 4. Filter & evaluate offers
    parsed_offers: List[Dict[str, Any]] = []
    for cand in unique_candidates:
        # Strict negative filters: ignore EMIs, Neucoins, Exchange bonuses, Trade-ins
        if re.search(
            r'\b(?:exchange|trade-in|neucoins?|starting\s*at|/month|/mo\b|no-cost\s*emi|ncemi|interest\s*savings)\b',
            cand,
            re.I,
        ):
            continue

        # Must look like an instant bank discount or cashback
        if not re.search(
            r'\b(?:instant\s*discount|cashback|bank\s*discount|instant\s*cashback|card\s*discount)\b',
            cand,
            re.I,
        ):
            continue

        discount_val = None

        # Check percentage discount with optional cap: e.g. "10% Instant Discount up to ₹3,000"
        m_pct = re.search(
            r'(\d+(?:\.\d+)?)\s*%\s*(?:instant\s*discount|cashback|off)(?:.*?\bup\s*to\s*(?:₹|Rs\.?)\s*([\d,]+))?',
            cand,
            re.I,
        )
        if m_pct:
            pct = float(m_pct.group(1)) / 100.0
            calc_amt = round(live_price * pct)
            if m_pct.group(2):
                cap = float(re.sub(r'[^\d]', '', m_pct.group(2)))
                discount_val = min(calc_amt, cap)
            else:
                discount_val = calc_amt

        # Check flat discount pattern 1: "Instant Discount of Rs. 4000" / "Instant Discount up to ₹4000"
        if not discount_val:
            m_flat1 = re.search(
                r'(?:instant\s*discount|cashback)[^\d]*(?:of|up\s*to)?\s*(?:₹|Rs\.?)\s*([\d,]+)',
                cand,
                re.I,
            )
            if m_flat1:
                discount_val = float(re.sub(r'[^\d]', '', m_flat1.group(1)))

        # Check flat discount pattern 2: "Flat ₹5000 Cashback" / "₹4,000 Instant Discount"
        if not discount_val:
            m_flat2 = re.search(
                r'(?:flat\s*)?(?:₹|Rs\.?)\s*([\d,]+)\s*(?:instant\s*discount|cashback)',
                cand,
                re.I,
            )
            if m_flat2:
                discount_val = float(re.sub(r'[^\d]', '', m_flat2.group(1)))

        # Sanity check: discount must be positive and less than the total live price
        if discount_val and 0 < discount_val < live_price:
            parsed_offers.append({
                "summary": cand,
                "discount": discount_val,
            })

    # Prioritize PREFERRED_BANKS if configured
    preferred_env = os.environ.get("PREFERRED_BANKS", "").strip()
    selected_offer = None

    if preferred_env and parsed_offers:
        pref_list = [b.strip().lower() for b in preferred_env.split(",") if b.strip()]
        matched_pref = [
            o for o in parsed_offers
            if any(pb in o["summary"].lower() for pb in pref_list)
        ]
        if matched_pref:
            matched_pref.sort(key=lambda x: x["discount"], reverse=True)
            selected_offer = matched_pref[0]

    # Otherwise, pick highest discount across all valid offers
    if not selected_offer and parsed_offers:
        parsed_offers.sort(key=lambda x: x["discount"], reverse=True)
        selected_offer = parsed_offers[0]

    if selected_offer:
        best_discount = selected_offer["discount"]
        best_summary = selected_offer["summary"]
    else:
        best_discount = 0.0
        best_summary = ""

    net_effective_price = round(live_price - best_discount, 2)
    all_summaries = [o["summary"] for o in parsed_offers]

    return {
        "bank_discount": best_discount,
        "net_effective_price": net_effective_price,
        "bank_offer_summary": best_summary,
        "all_bank_offers": all_summaries,
    }


def fetch_croma_product(
    url: str,
    pincode: str = "500083",
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """
    Fetches product name, live price, and bank discount offers from Croma.
    Returns:
      {
        "sku": str,
        "name": str,
        "live_price": float,
        "url": str,
        "source": str,
        "bank_discount": float,
        "net_effective_price": float,
        "bank_offer_summary": str,
        "all_bank_offers": list[str]
      }
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

    # Fetch Product Page HTML (Used for Price extraction and Bank Offers)
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

    extracted_name = None
    extracted_price = None
    source = "unknown"

    # ==========================================
    # Tier 2: JSON-LD & Next.js State
    # ==========================================
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
                extracted_name = prod_name.strip()
                extracted_price = price
                source = "tier2_jsonld"
                break

    # Next.js state (__NEXT_DATA__)
    if not extracted_price:
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
                    extracted_name = prod_name.strip()
                    extracted_price = price
                    source = "tier2_next_data"

    # ==========================================
    # Tier 3: DOM Selectors & Regex Fallback
    # ==========================================
    if not extracted_price:
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
                    source = "tier3_dom"
                    break

    if not extracted_name:
        name_selectors = ["h1.pdp-title", "h1.product-title", "h1", ".product-title"]
        for sel in name_selectors:
            elem = soup.select_one(sel)
            if elem and elem.get_text(strip=True):
                extracted_name = elem.get_text(strip=True)
                break

    if not extracted_price:
        m_selling = re.search(r'\"sellingPrice\":\s*\{\"value\":\s*\"([0-9\.]+)\"', html_text)
        if m_selling:
            extracted_price = _clean_price(m_selling.group(1))
            source = "tier3_regex_selling_price"

    if not extracted_price:
        m_price = re.search(r'\"price\":\s*\"([0-9\.]+)\"', html_text)
        if m_price:
            extracted_price = _clean_price(m_price.group(1))
            source = "tier3_regex_price"

    if not extracted_name:
        m_name = re.search(r'\"name\":\s*\"([^\"]+)\"', html_text)
        if m_name:
            extracted_name = m_name.group(1).encode().decode('unicode_escape', 'ignore').strip()
        elif soup.title and soup.title.string:
            title = soup.title.string.strip()
            extracted_name = re.sub(r'\s*\|\s*Croma.*$', '', title, flags=re.I)
            extracted_name = re.sub(r'Online at Best Price.*$', '', extracted_name, flags=re.I).strip()

    if not extracted_price or extracted_price <= 0:
        raise ValueError(f"Could not extract price or name for Croma URL: {url}")

    final_name = extracted_name or f"Croma Product {sku}"

    # Extract Bank Offers and calculate Net Effective Price
    offers_data = extract_bank_offers(
        html=html_text,
        sku=sku,
        live_price=extracted_price,
        session=http,
        pincode=pincode,
    )

    return {
        "sku": sku,
        "name": final_name,
        "live_price": extracted_price,
        "url": url,
        "source": source,
        "bank_discount": offers_data["bank_discount"],
        "net_effective_price": offers_data["net_effective_price"],
        "bank_offer_summary": offers_data["bank_offer_summary"],
        "all_bank_offers": offers_data["all_bank_offers"],
    }
