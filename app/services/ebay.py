import base64
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, TypedDict

try:  # pragma: no cover - optional runtime dependency
    import requests  # type: ignore
except ImportError:  # pragma: no cover - surface friendly guidance when missing
    requests = None

DEFAULT_MARKETPLACE = "EBAY_DE"


class TokenExchangeResult(TypedDict, total=False):
    access_token: str
    refresh_token: Optional[str]
    expires_at: Optional[str]
    account_name: Optional[str]


mock_ebay_catalog = {
    "B000TESTASIN": {
        "title": "eBay Backup Coffee Maker",
        "price": "51.99",
        "condition": "Used",
        "description": "Fallback description from eBay catalog.",
        "images": ["https://example.com/images/coffee_alt.jpg"],
        "brand": "eBayBrand",
        "ean": "978020137962",
        "product_type": "Coffee Maker",
        "gpsr": "GPSR-konform",
        "article_attributes": {
            "Spannung": "220V",
            "Farbe": "Grau",
        },
    }
}

_category_cache: Dict[str, str] = {}


def _require_requests():
    if requests is None:
        raise RuntimeError("Die Bibliothek 'requests' fehlt. Bitte `pip install -r requirements.txt` ausführen.")


def fetch_listing_data(asin: str) -> Dict[str, str]:
    return mock_ebay_catalog.get(
        asin,
        {
            "title": "N/A",
            "price": "N/A",
            "condition": "N/A",
            "description": "N/A",
            "images": [],
            "brand": "N/A",
            "ean": "N/A",
            "product_type": "N/A",
            "gpsr": "N/A",
            "article_attributes": {},
        },
    )


def search_listing(
    ean: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 5,
    access_token: Optional[str] = None,
    app_id: Optional[str] = None,
) -> Dict[str, object]:
    """Return primary guess + market suggestions."""

    _require_requests()
    app_id = app_id or os.getenv("EBAY_APP_ID")
    oauth_token = access_token or os.getenv("EBAY_OAUTH_TOKEN")
    suggestions: List[dict] = []
    primary: Dict[str, str] = {}

    search_query = ean or query
    if oauth_token and search_query:
        try:
            resp = requests.get(
                "https://api.ebay.com/buy/browse/v1/item_summary/search",
                params={"q": search_query, "limit": limit},
                headers={"Authorization": f"Bearer {oauth_token}"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("itemSummaries") or []
            for item in items[:limit]:
                image_field = item.get("image") or []
                images: list[str] = []
                if isinstance(image_field, list):
                    images = [img.get("imageUrl") for img in image_field if isinstance(img, dict) and img.get("imageUrl")]
                elif isinstance(image_field, dict) and image_field.get("imageUrl"):
                    images = [image_field.get("imageUrl")]
                suggestion = {
                    "title": item.get("title") or "N/A",
                    "price": (item.get("price") or {}).get("value", "N/A"),
                    "condition": item.get("condition") or "N/A",
                    "categoryPath": item.get("categoryPath") or "N/A",
                    "categoryId": (item.get("categoryId") or "N/A"),
                    "images": images,
                }
                suggestions.append(suggestion)
            if suggestions:
                primary = suggestions[0]
        except Exception:
            suggestions = []
            primary = {}

    if not suggestions and app_id and search_query:
        try:
            resp = requests.get(
                "https://svcs.ebay.com/services/search/FindingService/v1",
                params={
                    "OPERATION-NAME": "findItemsByKeywords",
                    "SERVICE-VERSION": "1.0.0",
                    "SECURITY-APPNAME": app_id,
                    "RESPONSE-DATA-FORMAT": "JSON",
                    "keywords": search_query,
                    "paginationInput.entriesPerPage": limit,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            items = (
                data.get("findItemsByKeywordsResponse", [{}])[0]
                .get("searchResult", [{}])[0]
                .get("item", [])
            )
            for item in items[:limit]:
                gallery = item.get("galleryURL") or []
                gallery_list = gallery if isinstance(gallery, list) else [gallery]
                suggestion = {
                    "title": (item.get("title") or ["N/A"])[0],
                    "price": (item.get("sellingStatus", [{}])[0]
                        .get("currentPrice", [{}])[0]
                        .get("__value__", "N/A")),
                    "condition": (item.get("condition", [{}])[0].get("conditionDisplayName") or ["N/A"])[0],
                    "categoryPath": (item.get("primaryCategory", [{}])[0].get("categoryName", ["N/A"])[0]),
                    "categoryId": (item.get("primaryCategory", [{}])[0].get("categoryId", ["N/A"])[0]),
                    "images": gallery_list,
                }
                suggestions.append(suggestion)
            if suggestions:
                primary = suggestions[0]
        except Exception:
            suggestions = []
            primary = {}

    if not primary:
        fallback = fetch_listing_data(query or ean or "")
        primary = fallback
        suggestions = suggestions or [fallback]

    # average price suggestion
    prices = []
    for s in suggestions:
        try:
            prices.append(float(str(s.get("price", "0")).replace(",", ".")))
        except Exception:
            continue
    avg_price = f"{sum(prices)/len(prices):.2f}" if prices else None

    primary.update({"market_suggestions": suggestions, "avg_price": avg_price})
    return primary


def fetch_account_name(access_token: Optional[str]) -> Optional[str]:
    """Resolve the eBay Kontoname via Identity API if possible."""

    if not requests or not access_token:
        return None
    try:
        resp = requests.get(
            "https://api.ebay.com/commerce/identity/v1/user/",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=12,
        )
        resp.raise_for_status()
        data = resp.json() or {}
        return data.get("username") or data.get("userId") or data.get("userName")
    except Exception:
        return None


def exchange_code_for_token(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> Optional[TokenExchangeResult]:
    """Exchange the OAuth code for tokens and derive the account name.

    Returns None if requests is missing or credentials are placeholders.
    """

    if not requests:
        return None
    if not client_secret or client_secret.startswith("DEIN_") or client_id.startswith("DEIN_"):
        return None

    try:
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        resp = requests.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {basic}",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=18,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        access_token: Optional[str] = payload.get("access_token")
        refresh_token: Optional[str] = payload.get("refresh_token")
        expires_in = payload.get("expires_in") or 0
        expires_at = None
        if isinstance(expires_in, (int, float)):
            expires_at = (datetime.utcnow() + timedelta(seconds=int(expires_in))).isoformat()

        account_name = fetch_account_name(access_token) if access_token else None

        return TokenExchangeResult(
            access_token=access_token or "",
            refresh_token=refresh_token,
            expires_at=expires_at,
            account_name=account_name,
        )
    except Exception:
        return None


def account_profile(account_name: str | None = None) -> Dict[str, str]:
    return {
        "account_label": account_name or "Sandbox Händlerkonto",
        "shipping_policy": "Standardversand (2-3 Werktage)",
        "payment_policy": "PayPal, Kreditkarte",
        "return_policy": "30 Tage Rückgabe, Käufer trägt Rückversand",
    }


def get_default_category_tree_id(marketplace_id: str = DEFAULT_MARKETPLACE) -> str:
    if marketplace_id in _category_cache:
        return _category_cache[marketplace_id]

    if requests:
        try:
            resp = requests.get(
                "https://api.ebay.com/commerce/taxonomy/v1/get_default_category_tree_id",
                params={"marketplace_id": marketplace_id},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json() or {}
            category_tree_id = data.get("categoryTreeId")
            if category_tree_id:
                _category_cache[marketplace_id] = category_tree_id
                return category_tree_id
        except Exception:
            pass

    _category_cache[marketplace_id] = "0"
    return "0"


def get_category_suggestions(category_tree_id: str, q: str, limit: int = 5) -> List[dict]:
    if not requests:
        return []
    try:
        resp = requests.get(
            f"https://api.ebay.com/commerce/taxonomy/v1/category_tree/{category_tree_id}/get_category_suggestions",
            params={"q": q, "limit": limit},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json() or {}
        suggestions = data.get("categorySuggestions") or []
        shaped = []
        for entry in suggestions[:limit]:
            cat = entry.get("category") or {}
            shaped.append(
                {
                    "categoryId": cat.get("categoryId"),
                    "categoryName": cat.get("categoryName"),
                    "categoryPath": " > ".join((cat.get("categoryPath") or [])),
                }
            )
        return shaped
    except Exception:
        return []


def get_item_aspects_for_category(category_tree_id: str, category_id: str) -> List[dict]:
    if not requests:
        return []
    try:
        resp = requests.get(
            f"https://api.ebay.com/commerce/taxonomy/v1/category_tree/{category_tree_id}/get_item_aspects_for_category",
            params={"category_id": category_id},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json() or {}
        return data.get("aspects") or []
    except Exception:
        return []


def get_payment_policies(access_token: str, marketplace_id: str = DEFAULT_MARKETPLACE) -> List[dict]:
    _require_requests()
    try:
        resp = requests.get(
            "https://api.ebay.com/sell/account/v1/payment_policy",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"marketplace_id": marketplace_id},
            timeout=12,
        )
        resp.raise_for_status()
        data = resp.json() or {}
        return data.get("paymentPolicies") or []
    except Exception:
        return []


def get_return_policies(access_token: str, marketplace_id: str = DEFAULT_MARKETPLACE) -> List[dict]:
    _require_requests()
    try:
        resp = requests.get(
            "https://api.ebay.com/sell/account/v1/return_policy",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"marketplace_id": marketplace_id},
            timeout=12,
        )
        resp.raise_for_status()
        data = resp.json() or {}
        return data.get("returnPolicies") or []
    except Exception:
        return []


def get_fulfillment_policies(access_token: str, marketplace_id: str = DEFAULT_MARKETPLACE) -> List[dict]:
    _require_requests()
    try:
        resp = requests.get(
            "https://api.ebay.com/sell/account/v1/fulfillment_policy",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"marketplace_id": marketplace_id},
            timeout=12,
        )
        resp.raise_for_status()
        data = resp.json() or {}
        return data.get("fulfillmentPolicies") or []
    except Exception:
        return []


def create_or_replace_inventory_item(sku: str, payload: dict, access_token: str) -> dict:
    _require_requests()
    resp = requests.put(
        f"https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload),
        timeout=18,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def create_offer(payload: dict, access_token: str) -> Optional[str]:
    _require_requests()
    resp = requests.post(
        "https://api.ebay.com/sell/inventory/v1/offer",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload),
        timeout=18,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    return data.get("offerId")


def publish_offer(offer_id: str, access_token: str) -> Optional[str]:
    _require_requests()
    resp = requests.post(
        f"https://api.ebay.com/sell/inventory/v1/offer/{offer_id}/publish",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=18,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    listing_id = data.get("listingId") or (data.get("listing") or {}).get("listingId")
    return listing_id
