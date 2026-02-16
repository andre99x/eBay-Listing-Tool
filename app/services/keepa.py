import os
from typing import Dict, Optional

try:  # pragma: no cover - optional runtime dependency
    import requests  # type: ignore
except ImportError:  # pragma: no cover - surface friendly guidance when missing
    requests = None

mock_keepa_catalog = {
    "B000TESTASIN": {
        "title": "Mock Keepa Coffee Maker",
        "price": "49.99",
        "condition": "Used - Good",
        "description": "A durable coffee maker pulled from Keepa data.",
        "images": [
            "https://example.com/images/coffee_front.jpg",
            "https://example.com/images/coffee_side.jpg",
        ],
        "brand": "MockBrand",
        "ean": "4006381333931",
        "product_type": "Coffee Maker",
        "gpsr": "GPSR-konform",
        "article_attributes": {
            "Leistung": "1200W",
            "Farbe": "Schwarz",
        },
    }
}


def _coerce_price(raw_price: Optional[int]) -> Optional[str]:
    if raw_price is None:
        return None
    return f"{raw_price / 100:.2f}"


def _shape_keepa_product(product: dict) -> Dict[str, str]:
    images = []
    image_csv = product.get("imagesCSV")
    if image_csv:
        images = [f"https://images-na.ssl-images-amazon.com/images/I/{key}.jpg" for key in image_csv.split(",")[:5]]

    ean_list = product.get("eanList") or []
    ean = ean_list[0] if ean_list else product.get("ean") or product.get("upc")

    price = _coerce_price((product.get("buyBoxPriceHistory") or [None])[-1])
    if price is None and product.get("buyBoxCurrent"):
        price = _coerce_price(product.get("buyBoxCurrent"))

    attributes = {}
    if product.get("binding"):
        attributes["Produktart"] = product.get("binding")
    if product.get("brand"):
        attributes["Marke"] = product.get("brand")

    return {
        "title": product.get("title") or "N/A",
        "price": price or "N/A",
        "condition": (product.get("condition") or "").title() or "N/A",
        "description": product.get("description") or product.get("title") or "N/A",
        "images": images,
        "brand": product.get("brand") or "N/A",
        "ean": ean or "N/A",
        "product_type": product.get("productGroup") or product.get("binding") or "N/A",
        "gpsr": "GPSR-konform",
        "article_attributes": attributes,
    }


def fetch_by_asin(asin: str) -> Optional[Dict[str, str]]:
    if requests is None:
        raise RuntimeError(
            "Die Bibliothek 'requests' ist nicht installiert. Bitte `pip install -r requirements.txt` ausführen."
        )

    api_key = os.getenv("KEEPA_API_KEY")
    if api_key:
        try:
            resp = requests.get(
                "https://api.keepa.com/product",
                params={"key": api_key, "domain": 2, "asin": asin},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            products = data.get("products") or []
            if products:
                shaped = _shape_keepa_product(products[0])
                return shaped
        except Exception:
            # fall back to mock catalog if real call fails
            pass

    return mock_keepa_catalog.get(asin)


def fetch_by_ean_or_title(*_args, **_kwargs):  # pragma: no cover - guard rail
    """Keepa darf nur mit ASIN verwendet werden."""

    raise RuntimeError("Keepa darf ausschließlich mit ASIN abgefragt werden.")
