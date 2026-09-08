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
-- MARGIN CALCULATOR TABLES
-- ============================================================

-- 5. MASTER COGS — HPP + ongkir per SKU
CREATE TABLE master_cogs (
    sku             VARCHAR(50) PRIMARY KEY REFERENCES products(sku),
    hpp             NUMERIC(15,2) NOT NULL,         -- harga beli per unit (CBP)
    shipping_fee    NUMERIC(15,2) DEFAULT 0,        -- ongkir / SF
    cogs            NUMERIC(15,2) GENERATED ALWAYS AS (hpp + shipping_fee) STORED,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 6. MARKETPLACE FEES — configurable fee structure per platform
CREATE TABLE marketplace_fees (
    marketplace     VARCHAR(50) PRIMARY KEY,        -- 'shopee', 'tiktok', 'shopee_food', etc.
    admin_fee_pct   NUMERIC(5,4) NOT NULL,          -- e.g. 0.2100 = 21%
    ads_pct         NUMERIC(5,4) NOT NULL,          -- e.g. 0.1100 = 11%
    packing_pct     NUMERIC(5,4) NOT NULL,          -- e.g. 0.0500 = 5%
    packing_min     NUMERIC(10,2) DEFAULT 2500,
    packing_max     NUMERIC(10,2) DEFAULT 7500,
    bpp             NUMERIC(10,2) DEFAULT 1250,     -- biaya proses pesanan (fixed)
    bpl             NUMERIC(10,2) DEFAULT 0,        -- biaya proses logistik (TikTok)
    label           VARCHAR(100),                   -- display name
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 7. SELLING PRICES — harga jual per SKU per marketplace
CREATE TABLE selling_prices (
    sku             VARCHAR(50) NOT NULL REFERENCES products(sku),
    marketplace     VARCHAR(50) NOT NULL REFERENCES marketplace_fees(marketplace),
    selling_price   NUMERIC(15,2) NOT NULL,
    min_qty         INTEGER DEFAULT 1,
    discount_pct    NUMERIC(5,4) DEFAULT 0,         -- campaign discount
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (sku, marketplace)
);

-- 8. MARGIN RESULTS — calculated, not imported
CREATE TABLE margin_results (
    sku                 VARCHAR(50) NOT NULL,
    marketplace         VARCHAR(50) NOT NULL,
    selling_price       NUMERIC(15,2),
    min_qty             INTEGER DEFAULT 1,
    final_selling_price NUMERIC(15,2),              -- SP * min_qty * (1 - discount)
    cogs                NUMERIC(15,2),
    final_cogs          NUMERIC(15,2),              -- cogs * min_qty
    admin_fee           NUMERIC(15,2),
    bpp                 NUMERIC(10,2),
    bpl                 NUMERIC(10,2) DEFAULT 0,
    gpbo                NUMERIC(15,2),
    gpbo_pct            NUMERIC(8,6),
    ads                 NUMERIC(15,2),
    packing             NUMERIC(15,2),
    gpao                NUMERIC(15,2),
    gpao_pct            NUMERIC(8,6),
    calculated_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (sku, marketplace)
);

-- ============================================================
-- ANALYTICS RESULT TABLES
-- ============================================================

-- 9. ABC CLASSIFICATION — dual-criteria results
CREATE TABLE abc_results (
    id              BIGSERIAL PRIMARY KEY,
    sku             VARCHAR(50) NOT NULL REFERENCES products(sku),
    revenue_rank    CHAR(1) NOT NULL,               -- A, B, C
    margin_rank     CHAR(1) NOT NULL,               -- A, B, C
    abc_class       CHAR(2) NOT NULL,               -- AA, AB, ..., CC
    total_revenue   NUMERIC(15,2),
    total_margin    NUMERIC(15,2),
    avg_margin_pct  NUMERIC(8,6),
    period_start    DATE,
    period_end      DATE,
    calculated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_abc_sku ON abc_results(sku);
CREATE INDEX idx_abc_class ON abc_results(abc_class);
CREATE INDEX idx_abc_calculated ON abc_results(calculated_at);

-- 10. FORECAST RESULTS — Holt's DES per SKU
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

-- 11. RESTOCK RECOMMENDATIONS
CREATE TABLE restock_recommendations (
    id                  BIGSERIAL PRIMARY KEY,
    sku                 VARCHAR(50) NOT NULL REFERENCES products(sku),
    abc_class           CHAR(2),
    current_stock       INTEGER,
    incoming_po         INTEGER DEFAULT 0,
    forecasted_demand   INTEGER,                    -- from Holt DES
    safety_stock        INTEGER,                    -- ABC-aware multiplier
    reorder_qty         INTEGER,
    priority            VARCHAR(20),                -- urgent, normal, low, skip
    supplier            VARCHAR(255),
    coverage_weeks      NUMERIC(6,2),               -- current stock / weekly demand
    calculated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_restock_priority ON restock_recommendations(priority);

-- ============================================================
-- SEED DATA — marketplace fees
-- ============================================================

INSERT INTO marketplace_fees (marketplace, admin_fee_pct, ads_pct, packing_pct, packing_min, packing_max, bpp, bpl, label)
VALUES
    ('shopee',      0.2100, 0.1100, 0.0500, 2500, 7500, 1250,    0, 'Shopee'),
    ('tiktok',      0.2100, 0.1100, 0.0500, 2500, 7500, 1250, 1500, 'TikTok'),
    ('shopee_cmp',  0.2100, 0.1100, 0.0500, 2500, 7500, 1250,    0, 'Shopee Campaign'),
    ('tiktok_cmp',  0.2100, 0.1100, 0.0500, 2500, 7500, 1250, 1500, 'TikTok Campaign')
ON CONFLICT DO NOTHING;

-- ============================================================
-- FUNCTION: recalculate margin for a SKU+marketplace
-- ============================================================

CREATE OR REPLACE FUNCTION calc_margin(p_sku VARCHAR, p_marketplace VARCHAR)
RETURNS VOID AS $$
DECLARE
    v_sp        NUMERIC;
    v_min_qty   INTEGER;
    v_discount  NUMERIC;
    v_fsp       NUMERIC;    -- final selling price
    v_cogs      NUMERIC;
    v_fcogs     NUMERIC;    -- final cogs (cogs * qty)
    v_admin_pct NUMERIC;
    v_ads_pct   NUMERIC;
    v_pack_pct  NUMERIC;
    v_pack_min  NUMERIC;
    v_pack_max  NUMERIC;
    v_bpp       NUMERIC;
    v_bpl       NUMERIC;
    v_admin     NUMERIC;
    v_gpbo      NUMERIC;
    v_ads       NUMERIC;
    v_packing   NUMERIC;
    v_gpao      NUMERIC;
BEGIN
    -- Get selling price
    SELECT selling_price, min_qty, discount_pct
    INTO v_sp, v_min_qty, v_discount
    FROM selling_prices
    WHERE sku = p_sku AND marketplace = p_marketplace;

    IF NOT FOUND THEN RETURN; END IF;

    -- Get COGS
    SELECT cogs INTO v_cogs
    FROM master_cogs
    WHERE sku = p_sku;

    IF NOT FOUND THEN RETURN; END IF;

    -- Get marketplace fees
    SELECT admin_fee_pct, ads_pct, packing_pct, packing_min, packing_max, bpp, bpl
    INTO v_admin_pct, v_ads_pct, v_pack_pct, v_pack_min, v_pack_max, v_bpp, v_bpl
    FROM marketplace_fees
    WHERE marketplace = p_marketplace;

    IF NOT FOUND THEN RETURN; END IF;

    -- Calculate
    v_fsp   := v_sp * v_min_qty * (1 - v_discount);
    v_fcogs := v_cogs * v_min_qty;
    v_admin := v_fsp * v_admin_pct;
    v_gpbo  := v_fsp - v_fcogs - v_admin - v_bpp - v_bpl;
    v_ads   := v_fsp * v_ads_pct;
    v_packing := GREATEST(v_pack_min, LEAST(v_fsp * v_pack_pct, v_pack_max));
    v_gpao  := v_gpbo - v_ads - v_packing;

    -- Upsert result
    INSERT INTO margin_results (
        sku, marketplace, selling_price, min_qty, final_selling_price,
        cogs, final_cogs, admin_fee, bpp, bpl,
        gpbo, gpbo_pct, ads, packing, gpao, gpao_pct, calculated_at
    ) VALUES (
        p_sku, p_marketplace, v_sp, v_min_qty, v_fsp,
        v_cogs, v_fcogs, v_admin, v_bpp, v_bpl,
        v_gpbo, CASE WHEN v_fsp > 0 THEN v_gpbo / v_fsp ELSE 0 END,
        v_ads, v_packing,
        v_gpao, CASE WHEN v_fsp > 0 THEN v_gpao / v_fsp ELSE 0 END,
        NOW()
    )
    ON CONFLICT (sku, marketplace) DO UPDATE SET
        selling_price       = EXCLUDED.selling_price,
        min_qty             = EXCLUDED.min_qty,
        final_selling_price = EXCLUDED.final_selling_price,
        cogs                = EXCLUDED.cogs,
        final_cogs          = EXCLUDED.final_cogs,
        admin_fee           = EXCLUDED.admin_fee,
        bpp                 = EXCLUDED.bpp,
        bpl                 = EXCLUDED.bpl,
        gpbo                = EXCLUDED.gpbo,
        gpbo_pct            = EXCLUDED.gpbo_pct,
        ads                 = EXCLUDED.ads,
        packing             = EXCLUDED.packing,
        gpao                = EXCLUDED.gpao,
        gpao_pct            = EXCLUDED.gpao_pct,
        calculated_at       = NOW();
END;
$$ LANGUAGE plpgsql;

-- Batch recalculate all margins for a marketplace
CREATE OR REPLACE FUNCTION calc_margin_all(p_marketplace VARCHAR)
RETURNS INTEGER AS $$
DECLARE
    v_count INTEGER := 0;
    v_sku   VARCHAR;
BEGIN
    FOR v_sku IN
        SELECT sp.sku FROM selling_prices sp
        JOIN master_cogs mc ON mc.sku = sp.sku
        WHERE sp.marketplace = p_marketplace
    LOOP
        PERFORM calc_margin(v_sku, p_marketplace);
        v_count := v_count + 1;
    END LOOP;
    RETURN v_count;
END;
$$ LANGUAGE plpgsql;

-- Recalculate ALL margins across ALL marketplaces
CREATE OR REPLACE FUNCTION calc_margin_global()
RETURNS INTEGER AS $$
DECLARE
    v_count INTEGER := 0;
    v_mp    VARCHAR;
BEGIN
    FOR v_mp IN SELECT marketplace FROM marketplace_fees
    LOOP
        v_count := v_count + calc_margin_all(v_mp);
    END LOOP;
    RETURN v_count;
END;
$$ LANGUAGE plpgsql;
