import os
import sys
import json
import logging

logging.basicConfig(level=logging.INFO)

from database import SessionLocal
import models
from services.otto_client import OttoMarketClient

db = SessionLocal()
pm = db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == "4071489823714").first()
if not pm:
    print("Mapping not found")
    sys.exit(1)

acc_name = pm.account_name.value if hasattr(pm.account_name, "value") else str(pm.account_name)
settings = db.query(models.AccountSettings).filter(models.AccountSettings.account_name == acc_name).first()

client = OttoMarketClient(
    client_id=settings.otto_client_id,
    client_secret=settings.otto_client_secret
)

print("--- Quantities API Test ---")
qty_payload = [{"sku": "4071489823714", "quantity": 20}]
res_qty = client.update_quantities(qty_payload)
print("Quantities Response:", json.dumps(res_qty, indent=2))

print("\n--- Quantities API Test Object ---")
res_qty2 = client.update_quantities({"quantities": [{"sku": "4071489823714", "quantity": 20}]})
print("Quantities Response Object:", json.dumps(res_qty2, indent=2))

print("\n--- Delivery API Test ---")
delivery_payload = [{
    "sku": "4071489823714",
    "shippingProfileId": "786c6468-3baf-52e0-88b5-13757eb7f873",
    "processingTime": 2
}]
res_deliv = client.update_product_delivery_information(delivery_payload)
print("Delivery Response:", json.dumps(res_deliv, indent=2))
