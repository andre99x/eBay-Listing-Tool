from typing import List, Optional

from pydantic import BaseModel, Field


class CSVPreviewSample(BaseModel):
    lpn: str
    asin: Optional[str] = None
    item_name: Optional[str] = None


class CSVPreviewResult(BaseModel):
    columns: List[str]
    detected_map: dict
    use_header: bool
    sample_rows: List[CSVPreviewSample]


class CSVIngestResult(BaseModel):
    imported: int
    skipped: List[str] = Field(default_factory=list)


class JobCreateRequest(BaseModel):
    lpn: Optional[str] = None
    asin: Optional[str] = None
    ean: Optional[str] = None
    query: Optional[str] = None
    item_name: Optional[str] = None
    condition_id: str
    condition_name: str


class ListingRequest(BaseModel):
    lpn: str
    fulfillment_policy_id: Optional[str] = None


class ListingPreviewResponse(BaseModel):
    lpn: str
    category_id: Optional[str] = None
    title: str
    description: str
    price: str
    condition: str
    condition_id: Optional[str] = None
    condition_name: Optional[str] = None
    brand: str
    ean: str
    product_type: str
    gpsr: str
    article_attributes: dict
    images: List[str]
    promotion: str
    shipping_policy: str
    payment_policy: str
    return_policy: str
    account_label: str
    missing_required_aspects: List[str] = Field(default_factory=list)
    market_suggestions: List[dict] = Field(default_factory=list)
    avg_price_suggestion: Optional[str] = None
    ready: bool


class DashboardStats(BaseModel):
    active_account: str
    active_user: Optional[str] = None
    listings_created: int
    total_jobs: int
    last_import_count: Optional[int] = None
    listed_history: int
    unprocessed_items: int
    ebay_linked: bool = False


class PoliciesResponse(BaseModel):
    fulfillment_policies: List[dict]
    payment_policy_id: Optional[str] = None
    return_policy_id: Optional[str] = None


class ListingJobOut(BaseModel):
    id: int
    lpn: str
    asin: Optional[str] = None
    ean: Optional[str] = None
    query: Optional[str] = None
    item_name: Optional[str] = None
    condition_id: Optional[str] = None
    condition_name: Optional[str] = None
    status: str


class ListedItemOut(BaseModel):
    id: int
    lpn: str
    asin: Optional[str] = None
    item_name: Optional[str] = None
    category_id: Optional[str] = None
    condition_id: Optional[str] = None
    condition_name: Optional[str] = None
    ebay_listing_id: Optional[str] = None
    price: Optional[str] = None
    listed_at: str


class LpnLookupResponse(BaseModel):
    lpn: str
    asin: Optional[str] = None
    item_name: Optional[str] = None
    status: str
    location: str
    listed_at: Optional[str] = None


class AuthRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    token: str
    email: str


class MeResponse(BaseModel):
    email: str
    ebay_linked: bool
    account_name: Optional[str] = None


class EbayAuthStart(BaseModel):
    auth_url: str


class EbayAuthStatus(BaseModel):
    ebay_linked: bool
    account_name: Optional[str] = None
    expires_at: Optional[str] = None


class SuggestionRequest(BaseModel):
    asin: Optional[str] = None
    ean: Optional[str] = None
    query: Optional[str] = None


class SuggestionResponse(BaseModel):
    keepa_used: bool
    ebay_used: bool
    keepa_data: Optional[dict] = None
    ebay_data: Optional[dict] = None
