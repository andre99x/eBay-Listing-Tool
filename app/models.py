from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey
from sqlalchemy.orm import relationship

from .db import Base


class ListingJob(Base):
    __tablename__ = "listing_jobs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    lpn = Column(String, index=True, nullable=False)
    asin = Column(String, index=True, nullable=True)
    ean = Column(String, index=True, nullable=True)
    query = Column(String, nullable=True)
    item_name = Column(String, nullable=True)
    condition_id = Column(String, nullable=True)
    condition_name = Column(String, nullable=True)
    image_paths = Column(Text, nullable=True)
    status = Column(String, default="pending", nullable=False)


class ListingPreview(Base):
    __tablename__ = "listing_previews"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    lpn = Column(String, index=True, nullable=False)
    category_id = Column(String, nullable=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    price = Column(String, nullable=False)
    condition = Column(String, nullable=False)
    condition_id = Column(String, nullable=True)
    condition_name = Column(String, nullable=True)
    brand = Column(String, nullable=False)
    ean = Column(String, nullable=False)
    product_type = Column(String, nullable=False)
    gpsr = Column(String, nullable=False)
    article_attributes = Column(Text, nullable=False)
    images = Column(Text, nullable=False)
    promotion = Column(String, nullable=False)
    shipping_policy = Column(String, nullable=False)
    payment_policy = Column(String, nullable=False)
    return_policy = Column(String, nullable=False)
    account_label = Column(String, nullable=False)
    missing_required_aspects = Column(Text, nullable=True)
    market_suggestions = Column(Text, nullable=True)
    avg_price_suggestion = Column(String, nullable=True)
    ready = Column(Boolean, default=False)


class ListingHistory(Base):
    __tablename__ = "listing_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    lpn = Column(String, index=True, nullable=False)
    asin = Column(String, index=True, nullable=True)
    item_name = Column(String, nullable=True)
    category_id = Column(String, nullable=True)
    condition_id = Column(String, nullable=True)
    condition_name = Column(String, nullable=True)
    ebay_listing_id = Column(String, nullable=True)
    price = Column(String, nullable=True)
    listed_at = Column(String, nullable=False)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(String, nullable=False)

    sessions = relationship("UserSession", back_populates="user")
    ebay_auth = relationship("EbayAuth", back_populates="user", uselist=False)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(String, nullable=False)

    user = relationship("User", back_populates="sessions")


class EbayAuth(Base):
    __tablename__ = "ebay_auth"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    access_token = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    expires_at = Column(String, nullable=True)
    account_name = Column(String, nullable=True)
    payment_policy_id = Column(String, nullable=True)
    return_policy_id = Column(String, nullable=True)
    fulfillment_policies = Column(Text, nullable=True)

    user = relationship("User", back_populates="ebay_auth")


class CategoryCache(Base):
    __tablename__ = "category_cache"

    id = Column(Integer, primary_key=True, index=True)
    marketplace_id = Column(String, unique=True, nullable=False)
    category_tree_id = Column(String, nullable=False)
