-- Test schema = the tables the app uses, as they exist in production (docs/database.md, 2026-11-03).
-- Applied by tests/conftest.py to the TEST database only (never production).
DROP TABLE IF EXISTS hero_skus, food_skus, sales_excluded, forecast_results, master_cogs,
                     purchase_orders, stock_snapshots, sales, products CASCADE;
DROP SEQUENCE IF EXISTS sales_id_seq CASCADE;

CREATE TABLE products (
    sku          VARCHAR(255) PRIMARY KEY,
    name         VARCHAR(255) NOT NULL,
    grp          VARCHAR(255),
    category     SMALLINT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    product_type VARCHAR(10) DEFAULT 'import'
);
CREATE INDEX idx_products_type ON products (product_type);

CREATE SEQUENCE sales_id_seq;
CREATE TABLE sales (
    id            BIGINT PRIMARY KEY DEFAULT nextval('sales_id_seq'),
    sku           VARCHAR(255) NOT NULL REFERENCES products(sku),
    invoice_date  DATE NOT NULL,
    quantity      INTEGER NOT NULL,
    unit          VARCHAR(20),
    amount        NUMERIC(15,2) NOT NULL,
    cogs_accurate NUMERIC(15,2),
    gpao_accurate NUMERIC(15,2),
    gpao_pct_acc  NUMERIC(8,4),
    week_label    VARCHAR(30),
    month_label   VARCHAR(20),
    quarter_label VARCHAR(30),
    year_val      SMALLINT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_sales_sku ON sales (sku);
CREATE INDEX idx_sales_date ON sales (invoice_date);
CREATE INDEX idx_sales_sku_date ON sales (sku, invoice_date);
CREATE INDEX idx_sales_dedup ON sales (sku, invoice_date, quantity, amount);

CREATE TABLE stock_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    sku           VARCHAR(50) NOT NULL REFERENCES products(sku),
    quantity      INTEGER NOT NULL,
    warehouse     VARCHAR(100),
    snapshot_date DATE NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_stock_sku ON stock_snapshots (sku);
CREATE INDEX idx_stock_date ON stock_snapshots (snapshot_date);

CREATE TABLE purchase_orders (
    id            BIGSERIAL PRIMARY KEY,
    sku           VARCHAR(50) NOT NULL REFERENCES products(sku),
    qty_ordered   INTEGER NOT NULL,
    qty_received  INTEGER DEFAULT 0,
    supplier      VARCHAR(255),
    status        VARCHAR(20) DEFAULT 'pending',
    order_date    DATE,
    expected_date DATE,
    received_date DATE,
    notes         TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_po_sku ON purchase_orders (sku);
CREATE INDEX idx_po_status ON purchase_orders (status);

CREATE TABLE master_cogs (
    sku          VARCHAR(50) PRIMARY KEY REFERENCES products(sku),
    hpp          NUMERIC(15,2) NOT NULL,
    shipping_fee NUMERIC(15,2) DEFAULT 0,
    cogs         NUMERIC(15,2),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE forecast_results (
    id              BIGSERIAL PRIMARY KEY,
    sku             VARCHAR(50) NOT NULL REFERENCES products(sku),
    alpha           NUMERIC(5,4),
    beta            NUMERIC(5,4),
    mape            NUMERIC(8,4),
    forecast_data   JSONB,
    training_months INTEGER,
    calculated_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_forecast_sku ON forecast_results (sku);
-- food_skus, sales_excluded, hero_skus and the extra purchase_orders columns are created by the
-- app's runtime DDL (ensure_*), exactly as on a database that has never had them.
