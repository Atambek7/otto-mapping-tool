import time
import os
import sys
import sqlite3

sys.path.insert(0, "/app")
import models
import database
from tasks import export_factory_db_task

def run_test():
    db = database.SessionLocal()
    eans = [m.product_ean for m in db.query(models.ProductMapping).filter(models.ProductMapping.factory_id == '504201').limit(30).all()]
    print(f"Testing async export for {len(eans)} products...")
    
    task = export_factory_db_task.delay('504201', 'JV', '504201', eans)
    start = time.time()
    
    while not task.ready():
        time.sleep(0.4)
        info = task.info if isinstance(task.info, dict) else {}
        cur = info.get("current", 0)
        tot = info.get("total", 0)
        pct = info.get("percent", 0)
        eta = info.get("eta_seconds", 0)
        msg = info.get("message", "")
        print(f"Progress: {cur}/{tot} ({pct}%) | ETA: {eta}s | {msg}")
    
    elapsed = round(time.time() - start, 2)
    print(f"Completed in {elapsed}s!")
    res = task.result
    print("Task Result:", res)
    
    if isinstance(res, dict) and res.get("task_id"):
        db_path = f"/app/static/exports/{res['task_id']}.db"
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("SELECT count(*), count(pic_main) FROM products")
            row = c.fetchone()
            print(f"Verification DB: {row[0]} products, {row[1]} with images.")
            assert row[0] == len(eans), f"Expected {len(eans)} products, got {row[0]}"
            print("TEST PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    run_test()
