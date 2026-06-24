-- Queries de profiling executadas em 2026-06-16
-- Fonte: Data Mart PostgreSQL (id=43), schemas gold.tiktok_brand_daily e gold.ml_gestao_diaria

-- ============================================================
-- TIKTOK: gold.tiktok_brand_daily
-- ============================================================

-- Cobertura, nulos e gaps de visitors por brand
SELECT
    brand,
    COUNT(*)                                                              AS dias,
    MIN(date)                                                             AS data_min,
    MAX(date)                                                             AS data_max,
    ROUND(AVG(gmv)::numeric, 2)                                          AS gmv_medio_dia,
    ROUND(SUM(gmv)::numeric, 2)                                          AS gmv_total,
    SUM(orders)                                                           AS total_pedidos,
    COUNT(*) FILTER (WHERE gmv IS NULL)                                   AS gmv_nulos,
    COUNT(*) FILTER (WHERE orders IS NULL)                                AS orders_nulos,
    COUNT(*) FILTER (WHERE visitors IS NULL OR visitors = 0)             AS sem_visitors
FROM gold.tiktok_brand_daily
GROUP BY brand
ORDER BY gmv_total DESC;

-- Verificar duplicatas em (brand, date)
SELECT brand, date, COUNT(*) AS ocorrencias
FROM gold.tiktok_brand_daily
GROUP BY brand, date
HAVING COUNT(*) > 1;

-- GMV mensal TikTok (últimos 6 meses)
SELECT
    DATE_TRUNC('month', date)::date AS mes,
    brand,
    ROUND(SUM(gmv)::numeric, 0)     AS gmv_mes
FROM gold.tiktok_brand_daily
WHERE date >= CURRENT_DATE - INTERVAL '6 months'
GROUP BY DATE_TRUNC('month', date), brand
ORDER BY mes DESC, gmv_mes DESC;

-- ============================================================
-- ML: gold.ml_gestao_diaria
-- ============================================================

-- Cobertura, nulos e qualidade por brand
SELECT
    brand,
    COUNT(*)                                           AS dias,
    MIN(ref_date)                                      AS data_min,
    MAX(ref_date)                                      AS data_max,
    ROUND(AVG(gmv)::numeric, 2)                        AS gmv_medio_dia,
    ROUND(SUM(gmv)::numeric, 2)                        AS gmv_total,
    SUM(paid_orders)                                   AS total_pedidos,
    COUNT(*) FILTER (WHERE gmv IS NULL)                AS gmv_nulos,
    COUNT(*) FILTER (WHERE paid_orders IS NULL)        AS orders_nulos,
    COUNT(*) FILTER (WHERE ad_spend IS NULL)           AS ad_spend_nulos
FROM gold.ml_gestao_diaria
WHERE brand IN ('barbours', 'kokeshi', 'lescent')
GROUP BY brand
ORDER BY gmv_total DESC;

-- Verificar duplicatas em (brand, ref_date)
SELECT brand, ref_date, COUNT(*) AS ocorrencias
FROM gold.ml_gestao_diaria
GROUP BY brand, ref_date
HAVING COUNT(*) > 1;

-- GMV mensal ML (últimos 6 meses)
SELECT
    DATE_TRUNC('month', ref_date)::date AS mes,
    brand,
    ROUND(SUM(gmv)::numeric, 0)         AS gmv_mes,
    SUM(paid_orders)                    AS pedidos_mes
FROM gold.ml_gestao_diaria
WHERE brand IN ('barbours', 'kokeshi', 'lescent')
  AND ref_date >= CURRENT_DATE - INTERVAL '6 months'
GROUP BY DATE_TRUNC('month', ref_date), brand
ORDER BY mes DESC, gmv_mes DESC;

-- Alerta lescent jun/2026 — investigar zero
SELECT brand, ref_date, gmv, paid_orders
FROM gold.ml_gestao_diaria
WHERE brand = 'lescent'
  AND ref_date >= '2026-06-01'
ORDER BY ref_date;
