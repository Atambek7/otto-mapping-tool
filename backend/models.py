import enum
import sqlalchemy as sa
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, Text, ForeignKey, JSON, DateTime, UniqueConstraint, Enum, func
from sqlalchemy.orm import relationship
from database import Base

class AccountType(str, enum.Enum):
    JV = "JV"
    XL = "XL"

class PlanStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class QueueItemStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"

class ExecutorType(str, enum.Enum):
    OTTO_API = "otto_api"
    BATCH_FILE = "batch_file"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_admin = Column(Boolean, default=False)
    openai_api_key = Column(String, nullable=True)

class Draft(Base):
    """Легаси-черновики (для обратной совместимости, если нужно)"""
    __tablename__ = "drafts"

    id = Column(Integer, primary_key=True, index=True)
    factory_name = Column(String, index=True)
    product_ean = Column(String, index=True)
    data_json = Column(Text)
    is_ok = Column(Boolean, default=False)

class ProductMapping(Base):
    __tablename__ = "product_mappings"
    __table_args__ = (
        UniqueConstraint("product_ean", "account_name", name="uq_product_ean_account"),
    )

    id = Column(Integer, primary_key=True, index=True)
    product_ean = Column(String, index=True)
    factory_id = Column(String, index=True)
    account_name = Column(Enum(AccountType, name="accounttype", create_type=False, values_callable=lambda x: [e.value for e in x]), nullable=False, index=True)
    category_id = Column(String, index=True)
    status = Column(String, default="Not Mapped") # Not Mapped, Need Attributes, Ready, Error
    ai_attributes = Column(JSON, default=list, nullable=True)
    missing_attributes = Column(JSON, default=list, nullable=True)
    last_error = Column(Text, nullable=True)
    
    product_name = Column(String, nullable=True)
    has_ai_title = Column(Boolean, default=False, nullable=True)
    price = Column(String, nullable=True)
    price_af = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True, index=True)

class ProductAttribute(Base):
    __tablename__ = "product_attributes"
    __table_args__ = (
        UniqueConstraint("product_ean", "attribute_name", "account_name", name="uq_product_attr_account"),
    )

    id = Column(Integer, primary_key=True, index=True)
    product_ean = Column(String, index=True)
    account_name = Column(Enum(AccountType, name="accounttype", create_type=False, values_callable=lambda x: [e.value for e in x]), nullable=False, default=AccountType.JV, index=True)
    attribute_name = Column(String)
    attribute_value = Column(String)

class CategoryCache(Base):
    __tablename__ = "categories"

    id = Column(String, primary_key=True, index=True)
    name = Column(String)
    path = Column(String)

class CategoryAttributeCache(Base):
    __tablename__ = "category_attributes"

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(String, index=True)
    name = Column(String)
    type = Column(String) # text, number, select, multiselect, boolean
    required = Column(Boolean, default=False)
    options = Column(JSON, nullable=True) # для select/multiselect

class AccountSettings(Base):
    __tablename__ = "account_settings"

    id = Column(Integer, primary_key=True, index=True)
    account_name = Column(String, unique=True, index=True) # "JV" or "XL"
    openai_api_key = Column(String, nullable=True)
    otto_client_id = Column(String, nullable=True)
    otto_client_secret = Column(String, nullable=True)
    otto_partner_id = Column(String, nullable=True)
    otto_brand_id = Column(String, nullable=True)
    otto_app_id = Column(String, nullable=True)

class PublishPlan(Base):
    __tablename__ = "publish_plans"

    id = Column(Integer, primary_key=True, index=True)
    factory_id = Column(String, nullable=False, index=True)
    factory_name = Column(String, nullable=True)
    account_name = Column(Enum(AccountType, name="accounttype", create_type=False, values_callable=lambda x: [e.value for e in x]), nullable=False)
    total_products = Column(Integer, nullable=False)
    total_days = Column(Integer, nullable=False)
    current_day = Column(Integer, default=1, nullable=False)
    daily_time = Column(String, default="08:00", nullable=False)
    status = Column(Enum(PlanStatus, name="planstatus", create_type=False, values_callable=lambda x: [e.value for e in x]), default=PlanStatus.ACTIVE, nullable=False)
    executor_type = Column(Enum(ExecutorType, name="executortype", create_type=False, values_callable=lambda x: [e.value for e in x]), default=ExecutorType.OTTO_API, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    items = relationship("PublishQueueItem", back_populates="plan", cascade="all, delete-orphan")

class PublishQueueItem(Base):
    __tablename__ = "publish_queue_items"
    __table_args__ = (
        UniqueConstraint("plan_id", "product_ean", name="uq_plan_product_ean"),
    )

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("publish_plans.id", ondelete="CASCADE"), nullable=False)
    product_ean = Column(String, nullable=False)
    account_name = Column(Enum(AccountType, name="accounttype", create_type=False, values_callable=lambda x: [e.value for e in x]), nullable=False)
    category_id = Column(String, nullable=False)
    scheduled_day = Column(Integer, nullable=False)
    scheduled_date = Column(DateTime(timezone=True), nullable=False, index=True)
    status = Column(Enum(QueueItemStatus, name="queueitemstatus", create_type=False, values_callable=lambda x: [e.value for e in x]), default=QueueItemStatus.PENDING, nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)
    missing_attributes = Column(JSON, default=list, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    otto_task_href = Column(String, nullable=True)
    otto_task_failed_href = Column(String, nullable=True)
    poll_attempts = Column(Integer, default=0, nullable=False)

    plan = relationship("PublishPlan", back_populates="items")


class OrderStat(Base):
    """
    Stores daily aggregated order statistics for weekday analysis.
    One row per (account_name, order_date). Populated by sync_order_stats Celery task.
    """
    __tablename__ = "order_stats"
    __table_args__ = (
        UniqueConstraint("account_name", "order_date", name="uq_order_stats_account_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    account_name = Column(
        Enum(AccountType, name="accounttype", create_type=False,
             values_callable=lambda x: [e.value for e in x]),
        nullable=False
    )
    order_date = Column(sa.Date, nullable=False)
    # 0=Monday .. 6=Sunday (Python isoweekday() - 1)
    weekday = Column(sa.SmallInteger, nullable=False)
    orders_count = Column(Integer, default=0, nullable=False)
    items_count = Column(Integer, default=0, nullable=False)
    revenue = Column(sa.Numeric(12, 2), default=0, nullable=False)
    synced_at = Column(DateTime(timezone=True), nullable=False)


class OttoOrder(Base):
    """
    Detailed Otto Market order for drilldown and product activity view.
    """
    __tablename__ = "otto_orders"
    __table_args__ = (
        UniqueConstraint("account_name", "sales_order_id", name="uq_otto_orders_account_sales_order"),
    )

    id = Column(Integer, primary_key=True, index=True)
    account_name = Column(
        Enum(AccountType, name="accounttype", create_type=False,
             values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True
    )
    sales_order_id = Column(String, nullable=False, index=True)
    order_number = Column(String, nullable=False, index=True)
    order_date = Column(DateTime(timezone=True), nullable=False, index=True)
    order_calendar_date = Column(sa.Date, nullable=False, index=True)
    weekday = Column(sa.SmallInteger, nullable=False, index=True)
    total_revenue = Column(sa.Numeric(12, 2), default=0, nullable=False)
    items_count = Column(Integer, default=0, nullable=False)
    synced_at = Column(DateTime(timezone=True), nullable=False)

    items = relationship("OttoOrderItem", back_populates="order", cascade="all, delete-orphan")


class OttoOrderItem(Base):
    """
    Individual position item inside an Otto Market order.
    """
    __tablename__ = "otto_order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "position_item_id", name="uq_otto_order_items_order_pos"),
    )

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("otto_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    position_item_id = Column(String, nullable=False, index=True)
    sku = Column(String, nullable=True, index=True)
    ean = Column(String, nullable=True, index=True)
    article_number = Column(String, nullable=True)
    product_title = Column(String, nullable=True)
    price = Column(sa.Numeric(12, 2), default=0, nullable=False)
    currency = Column(String, default="EUR", nullable=False)
    fulfillment_status = Column(String, nullable=True)
    shop_url = Column(String, nullable=True)

    order = relationship("OttoOrder", back_populates="items")


class AvailabilitySyncLog(Base):
    """
    Log of periodic availability (stock quantity) and price synchronization with Aftercool/Otto.
    """
    __tablename__ = "availability_sync_log"

    id = Column(Integer, primary_key=True, index=True)
    product_ean = Column(String, index=True, nullable=False)
    account_name = Column(String, index=True, nullable=False)
    factory_id = Column(String, index=True, nullable=True)
    old_stock = Column(Integer, nullable=True)
    new_stock = Column(Integer, nullable=True)
    old_price = Column(String, nullable=True)
    new_price = Column(String, nullable=True)
    sync_result = Column(String, nullable=False)  # "ok", "failed", "skipped", "unchanged"
    error_message = Column(Text, nullable=True)
    synced_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class SchemaDriftLog(Base):
    """
    Log of category schema drift audits against OTTO Categories API.
    """
    __tablename__ = "schema_drift_log"

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(String, index=True, nullable=False)
    category_name = Column(String, nullable=True)
    has_drift = Column(Boolean, default=False, nullable=False)
    diff_details = Column(JSON, nullable=True)
    checked_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)



