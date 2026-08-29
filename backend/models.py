from sqlalchemy import Column, Integer, String, Boolean, Text, ForeignKey, JSON, DateTime
from database import Base

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

    id = Column(Integer, primary_key=True, index=True)
    product_ean = Column(String, unique=True, index=True)
    factory_id = Column(String, index=True)
    account_name = Column(String, index=True, default="JV")
    category_id = Column(String, index=True)
    status = Column(String, default="Not Mapped") # Not Mapped, Need Attributes, Ready, Error
    
    product_name = Column(String, nullable=True)
    price = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)

class ProductAttribute(Base):
    __tablename__ = "product_attributes"

    id = Column(Integer, primary_key=True, index=True)
    product_ean = Column(String, index=True)
    attribute_name = Column(String)
    attribute_value = Column(String)
    # Можно сделать уникальным (product_ean, attribute_name)

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
