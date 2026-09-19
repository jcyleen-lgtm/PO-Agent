-- Migration 001: Sales append mode + PO table setup
-- Run this on existing databases before deploying the new app.py

-- 1. Add index for faster dedup checks on sales
CREATE INDEX IF NOT EXISTS idx_sales_dedup
ON sales(sku, invoice_date, quantity, amount);

-- 2. Ensure purchase_orders table exists (already in schema.sql, safe to re-run)
CREATE TABLE IF NOT EXISTS purchase_orders (
    id              BIGSERIAL PRIMARY KEY,
    sku             VARCHAR(50) NOT NULL REFERENCES products(sku),
    qty_ordered     INTEGER NOT NULL,
    qty_received    INTEGER DEFAULT 0,
    supplier        VARCHAR(255),
    status          VARCHAR(20) DEFAULT 'pending',
    order_date      DATE,
    expected_date   DATE,
    received_date   DATE,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_po_sku ON purchase_orders(sku);
CREATE INDEX IF NOT EXISTS idx_po_status ON purchase_orders(status);
