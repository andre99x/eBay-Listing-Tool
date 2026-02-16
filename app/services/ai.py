from typing import Dict, List


def build_ai_listing_text(title: str, description: str, keywords: List[str]) -> Dict[str, str]:
    keyword_blob = ", ".join(keywords) if keywords else "eBay Listing Tool"
    ai_title = f"{title} | SEO-optimiert für Google"
    ai_description = (
        f"{description}\n\n"
        "Optimiert für Suchmaschinen mit Keywords: "
        f"{keyword_blob}.\n"
        "Automatisch erstellt durch die integrierte KI, bereit für den 1-Klick-Upload."
    )
    return {"title": ai_title, "description": ai_description}
