"use client";

import { useState, useRef } from "react";
import { fetchWithAuth } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Search, PackageOpen } from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { cn } from "@/lib/utils";
import { ProductDrawer } from "@/components/ProductDrawer";

export default function ProductsPage() {
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [products, setProducts] = useState<any[]>([]);
  const [activeProduct, setActiveProduct] = useState<any | null>(null);
  const [hasSearched, setHasSearched] = useState(false);
  
  const parentRef = useRef<HTMLDivElement>(null);

  const handleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!search.trim()) return;
    
    setLoading(true);
    setHasSearched(true);
    try {
      // Use global search by q or EAN
      // If it looks like EAN (digits), try by-ean first? 
      // User said: /api/products?account=&dataset=&factory_id=&q=&limit=0&offset=0&include_row=0
      // We will use q=search
      const res = await fetchWithAuth(`/aftercool/products?account=JV&dataset=lister&q=${encodeURIComponent(search)}&limit=100&include_row=1`);
      if (res.ok) {
        const data = await res.json();
        setProducts(data?.items || data || []);
      } else {
        setProducts([]);
      }
    } catch (e) {
      console.error(e);
      setProducts([]);
    } finally {
      setLoading(false);
    }
  };

  const rowVirtualizer = useVirtualizer({
    count: products.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 64,
    overscan: 10,
  });

  return (
    <div className="flex flex-col h-full space-y-4">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Products Search</h1>
        <p className="text-sm text-slate-500">Search for products globally across all factories.</p>
      </div>

      <form onSubmit={handleSearch} className="flex gap-2">
        <div className="relative flex-1 max-w-xl">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name or EAN..."
            className="pl-9 h-11 bg-white"
          />
        </div>
        <Button type="submit" disabled={loading} className="h-11 px-8 bg-indigo-600 hover:bg-indigo-700">
          {loading ? "Searching..." : "Search"}
        </Button>
      </form>

      {hasSearched && products.length === 0 && !loading && (
        <div className="py-20 flex flex-col items-center justify-center text-slate-400 border border-dashed border-slate-200 rounded-2xl bg-white">
          <PackageOpen className="w-12 h-12 opacity-20 mb-3" />
          <p>ТОВАРЫ НЕ НАЙДЕНЫ</p>
        </div>
      )}

      {products.length > 0 && (
        <div ref={parentRef} className="flex-1 overflow-auto bg-white border border-slate-200 rounded-xl shadow-sm relative">
          
          <div className="sticky top-0 bg-slate-50 border-b border-slate-200 z-10 shadow-sm flex items-center text-sm font-semibold text-slate-500 py-3 px-4">
            <div className="w-16 text-xs">IMG</div>
            <div className="flex-1">Product Name</div>
            <div className="w-48">EAN</div>
            <div className="w-40">Factory ID</div>
          </div>

          <div style={{ height: `${rowVirtualizer.getTotalSize()}px`, position: "relative" }}>
            {rowVirtualizer.getVirtualItems().map((virtualRow) => {
              const p = products[virtualRow.index];
              const row = p.row || {};
              const img = row.GalleryURL || row.PictureURL || "";
              
              return (
                <div
                  key={virtualRow.key}
                  className="absolute top-0 left-0 w-full flex items-center border-b border-slate-100 hover:bg-slate-50 cursor-pointer px-4 text-sm"
                  style={{
                    height: `${virtualRow.size}px`,
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                  onClick={() => setActiveProduct(p)}
                >
                  <div className="w-16">
                    <div className="w-10 h-10 rounded border border-slate-200 bg-white flex items-center justify-center p-0.5 overflow-hidden">
                      {img ? <img src={img} className="w-full h-full object-contain" /> : "-"}
                    </div>
                  </div>
                  <div className="flex-1 truncate font-medium text-slate-700">{row.Artikelbeschreibung || p.name || ""}</div>
                  <div className="w-48 font-mono text-xs text-slate-500">{p.ean || row.EAN || ""}</div>
                  <div className="w-40 font-mono text-xs text-slate-400">{p.factory_id}</div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {activeProduct && (
        <ProductDrawer 
          product={activeProduct} 
          factoryId={activeProduct.factory_id} 
          onClose={() => setActiveProduct(null)} 
          onSave={() => {}}
        />
      )}
    </div>
  );
}
