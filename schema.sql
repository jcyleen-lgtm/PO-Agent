-- ============================================================
-- PO Agent — PostgreSQL Schema
-- Target: Supabase (PostgreSQL 15+)
-- ============================================================

-- 1. PRODUCTS — master produk (deduplicated)
CREATE TABLE products (
    sku         VARCHAR(50) PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    grp         VARCHAR(255),           -- product group (e.g. "# Kuali Besi")
    category    SMALLINT,               -- 1=Best Seller, 2=Fast Moving, 3=Moderate, 4=Slow Moving, 5=Deadweight, 6=New, 9=Discontinue
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 2. SALES — transaction-level sales data (87k+ rows)
CREATE TABLE sales (
    id              BIGSERIAL PRIMARY KEY,
    sku             VARCHAR(50) NOT NULL REFERENCES products(sku),
    invoice_date    DATE NOT NULL,
    quantity        INTEGER NOT NULL,
    unit            VARCHAR(20),
    amount          NUMERIC(15,2) NOT NULL,        -- revenue (harga jual x qty)
    cogs_accurate   NUMERIC(15,2),                 -- COGS from Accurate (as-is, may be 0 or negative)
    gpao_accurate   NUMERIC(15,2),                 -- GPAO from Accurate (as-is)
    gpao_pct_acc    NUMERIC(8,4),                  -- GPAO % from Accurate
    week_label      VARCHAR(30),
    month_label     VARCHAR(20),
    quarter_label   VARCHAR(10),
    year_val        SMALLINT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_sales_sku ON sales(sku);
CREATE INDEX idx_sales_date ON sales(invoice_date);
CREATE INDEX idx_sales_month ON sales(month_label);
CREATE INDEX idx_sales_sku_date ON sales(sku, invoice_date);

-- 3. STOCK SNAPSHOTS — current inventory per upload
CREATE TABLE stock_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    sku             VARCHAR(50) NOT NULL REFERENCES products(sku),
    quantity        INTEGER NOT NULL,
    warehouse       VARCHAR(100),
    snapshot_date   DATE NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_stock_sku ON stock_snapshots(sku);
CREATE INDEX idx_stock_date ON stock_snapshots(snapshot_date);

-- 4. PURCHASE ORDERS — PO berjalan
CREATE TABLE purchase_orders (
    id              BIGSERIAL PRIMARY KEY,
    sku             VARCHAR(50) NOT NULL REFERENCES products(sku),
    qty_ordered     INTEGER NOT NULL,
    qty_received    INTEGER DEFAULT 0,
    supplier        VARCHAR(255),
    status          VARCHAR(20) DEFAULT 'pending',  -- pending, partial, received, cancelled
    order_date      DATE,
    expected_date   DATE,
    received_date   DATE,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_po_sku ON purchase_orders(sku);
CREATE INDEX idx_po_status ON purchase_orders(status);

-- ============================================================
-- ANALYTICS RESULT TABLES
-- ============================================================

-- 5. FORECAST RESULTS — Holt's DES per SKU
CREATE TABLE forecast_results (
    id              BIGSERIAL PRIMARY KEY,
    sku             VARCHAR(50) NOT NULL REFERENCES products(sku),
    alpha           NUMERIC(5,4),
    beta            NUMERIC(5,4),
    mape            NUMERIC(8,4),
    forecast_data   JSONB,                          -- [{period, forecasted_qty}, ...]
    training_months INTEGER,                        -- how many months used
    calculated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_forecast_sku ON forecast_results(sku);


