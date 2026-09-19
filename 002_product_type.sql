-- Migration 002: Add product_type + cleanup cancelled POs
-- Run in Supabase SQL Editor

-- 1. Add product_type column (food / import)
ALTER TABLE products ADD COLUMN IF NOT EXISTS product_type VARCHAR(10) DEFAULT 'import';
CREATE INDEX IF NOT EXISTS idx_products_type ON products(product_type);

-- 2. Delete cancelled POs
DELETE FROM purchase_orders WHERE status = 'cancelled';
