import asyncio
import time
import tracemalloc
import sys
import os

from sqlalchemy.orm import Session
from database import SessionLocal
import models
from routers.csv_workflow import export_categories

# Provide a mock export request
class MockRequest:
    def __init__(self, eans=None):
        self.product_eans = eans

async def run_benchmark(factory_id: str, eans_list: list = None):
    db = SessionLocal()
    req = MockRequest(eans=eans_list) if eans_list else None
    
    print(f"--- Benchmark for Factory: {factory_id}, Items: {len(eans_list) if eans_list else 'ALL'} ---")
    
    tracemalloc.start()
    start_time = time.time()
    
    # We call the router function directly. It returns a StreamingResponse.
    response = await export_categories(factory_id, req, db)
    
    # Actually stream the content to simulate real generation
    content_len = 0
    if hasattr(response, "body_iterator"):
        async for chunk in response.body_iterator:
            content_len += len(chunk)
    else:
        # standard Response
        content_len = len(response.body)
        
    end_time = time.time()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    
    duration = end_time - start_time
    print(f"Total Latency: {duration:.4f} seconds")
    print(f"Peak Memory: {peak / 1024 / 1024:.2f} MB")
    print(f"Generated CSV Bytes: {content_len} bytes")
    print("-" * 50)
    db.close()

async def main():
    db = SessionLocal()
    # Let's find a factory with enough products, or just pick the first one with many products
    mappings = db.query(models.ProductMapping).all()
    if not mappings:
        print("No mappings found in database.")
        db.close()
        return
        
    factory_counts = {}
    for m in mappings:
        factory_counts[m.factory_id] = factory_counts.get(m.factory_id, 0) + 1
        
    best_factory = max(factory_counts, key=factory_counts.get)
    max_count = factory_counts[best_factory]
    print(f"Selected Factory {best_factory} with {max_count} items for benchmark.")
    
    all_eans_in_factory = [m.product_ean for m in db.query(models.ProductMapping).filter(models.ProductMapping.factory_id == best_factory).all()]
    
    db.close()
    
    sizes = [100, 500, max_count]
    for size in sizes:
        if size <= max_count:
            subset = all_eans_in_factory[:size]
            await run_benchmark(best_factory, subset)

if __name__ == "__main__":
    asyncio.run(main())
