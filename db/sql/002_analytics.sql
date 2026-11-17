-- Схема analytics: семантический слой для text-to-SQL.
-- Агент видит ТОЛЬКО эти представления: в них нет ИИН, контактов и телефонов,
-- учтены только проведённые документы, возвраты уже со знаком минус.
-- «Сегодня» для отчётов — дата выгрузки (analytics.export_info.export_date), а не CURRENT_DATE.

CREATE SCHEMA IF NOT EXISTS analytics;
COMMENT ON SCHEMA analytics IS 'Представления для ИИ-агента (без ПДн). Белый список для text-to-SQL.';

CREATE VIEW analytics.export_info AS
SELECT export_date, source, company, period_start, period_end, note
FROM erp.export_info;
COMMENT ON VIEW analytics.export_info IS 'Дата выгрузки (считать её «сегодня»), период данных, пометка о демо-данных';

CREATE VIEW analytics.warehouses AS
SELECT id, code, name, city FROM erp.warehouses;
COMMENT ON VIEW analytics.warehouses IS 'Склады';

CREATE VIEW analytics.products AS
SELECT id, sku, name, category, brand, supplier, unit, box_qty,
       price_kzt, purchase_price_kzt, shelf_life_days, is_active
FROM erp.products;
COMMENT ON VIEW analytics.products IS 'Номенклатура: категория, бренд, поставщик, действующие цены (₸ с НДС)';

CREATE VIEW analytics.managers AS
SELECT id, code, full_name, position, city FROM erp.managers;
COMMENT ON VIEW analytics.managers IS 'Сотрудники отдела продаж (без контактов)';

CREATE VIEW analytics.clients AS
SELECT id, code, name, legal_form, bin, segment, city, manager_id, warehouse_id,
       payment_terms_days, credit_limit_kzt, base_discount_pct, created_at,
       is_active, shipment_blocked
FROM erp.clients;
COMMENT ON VIEW analytics.clients IS 'Клиенты (без ИИН, адресов и контактов). segment: Сеть, Опт, HoReCa, Розница';
COMMENT ON COLUMN analytics.clients.payment_terms_days IS 'Отсрочка, дней; 0 — предоплата';
COMMENT ON COLUMN analytics.clients.shipment_blocked IS 'Отгрузки заблокированы из-за просрочки';

CREATE VIEW analytics.sales AS
SELECT
    l.id                                  AS line_id,
    d.id                                  AS document_id,
    d.doc_number,
    d.doc_date,
    date_trunc('month', d.doc_date)::date AS doc_month,
    d.doc_type,
    c.id   AS client_id,   c.name AS client_name, c.segment, c.city AS client_city,
    m.id   AS manager_id,  m.full_name AS manager_name,
    w.id   AS warehouse_id, w.name AS warehouse_name,
    p.id   AS product_id,  p.sku, p.name AS product_name, p.category, p.brand,
    s.sign * l.quantity                          AS quantity,
    l.price_kzt,
    l.discount_pct,
    s.sign * l.amount_kzt                        AS amount_kzt,
    s.sign * (l.amount_kzt - l.vat_kzt)          AS amount_net_kzt,
    s.sign * l.vat_kzt                           AS vat_kzt,
    s.sign * l.cost_kzt                          AS cost_kzt,
    s.sign * (l.amount_kzt - l.cost_kzt)         AS margin_kzt
FROM erp.sales_lines l
JOIN erp.sales_documents d ON d.id = l.document_id
CROSS JOIN LATERAL (SELECT CASE d.doc_type WHEN 'Возврат' THEN -1 ELSE 1 END AS sign) s
JOIN erp.clients    c ON c.id = d.client_id
JOIN erp.managers   m ON m.id = d.manager_id
JOIN erp.warehouses w ON w.id = d.warehouse_id
JOIN erp.products   p ON p.id = l.product_id
WHERE d.is_posted;
COMMENT ON VIEW analytics.sales IS 'Продажи построчно: только проведённые документы, возвраты с минусом. SUM(amount_kzt) = чистая выручка с НДС';
COMMENT ON COLUMN analytics.sales.amount_kzt IS 'Выручка строки с НДС после скидки, ₸ (возврат отрицательный)';
COMMENT ON COLUMN analytics.sales.amount_net_kzt IS 'Выручка без НДС, ₸';
COMMENT ON COLUMN analytics.sales.margin_kzt IS 'Валовая маржа = выручка с НДС − себестоимость, ₸';

CREATE VIEW analytics.sales_documents AS
SELECT d.id, d.doc_number, d.doc_date, date_trunc('month', d.doc_date)::date AS doc_month,
       d.doc_type, d.client_id, c.name AS client_name, c.segment,
       d.manager_id, m.full_name AS manager_name, d.warehouse_id,
       CASE d.doc_type WHEN 'Возврат' THEN -d.amount_kzt ELSE d.amount_kzt END AS amount_kzt,
       d.base_document_id, d.comment
FROM erp.sales_documents d
JOIN erp.clients  c ON c.id = d.client_id
JOIN erp.managers m ON m.id = d.manager_id
WHERE d.is_posted;
COMMENT ON VIEW analytics.sales_documents IS 'Проведённые документы продаж (шапки); возвраты с минусом, comment — причина возврата';

CREATE VIEW analytics.stock AS
SELECT b.as_of_date, b.warehouse_id, w.name AS warehouse_name, w.city,
       b.product_id, p.sku, p.name AS product_name, p.category,
       b.lot_number, b.production_date, b.expiry_date,
       (b.expiry_date - b.as_of_date)        AS days_to_expiry,
       (b.expiry_date < b.as_of_date)        AS is_expired,
       b.quantity, b.reserved_qty,
       (b.quantity - b.reserved_qty)         AS available_qty,
       b.quantity * p.purchase_price_kzt     AS stock_value_kzt
FROM erp.stock_balances b
JOIN erp.warehouses w ON w.id = b.warehouse_id
JOIN erp.products   p ON p.id = b.product_id;
COMMENT ON VIEW analytics.stock IS 'Остатки по партиям на дату выгрузки; нет строки — нет остатка. available_qty = остаток − резерв';
COMMENT ON COLUMN analytics.stock.stock_value_kzt IS 'Стоимость остатка по закупочной цене, ₸';

CREATE VIEW analytics.receivables AS
SELECT r.as_of_date, r.client_id, c.name AS client_name, c.segment,
       c.manager_id, m.full_name AS manager_name,
       d.doc_number, r.doc_date, r.due_date,
       r.amount_kzt, r.paid_kzt, (r.amount_kzt - r.paid_kzt) AS balance_kzt,
       GREATEST(0, r.as_of_date - r.due_date) AS days_overdue,
       CASE
           WHEN r.as_of_date <= r.due_date      THEN 'не просрочено'
           WHEN r.as_of_date - r.due_date <= 15 THEN '1–15 дней'
           WHEN r.as_of_date - r.due_date <= 30 THEN '16–30 дней'
           WHEN r.as_of_date - r.due_date <= 45 THEN '31–45 дней'
           WHEN r.as_of_date - r.due_date <= 90 THEN '46–90 дней'
           ELSE 'более 90 дней'
       END AS aging_bucket
FROM erp.receivables r
JOIN erp.clients         c ON c.id = r.client_id
JOIN erp.managers        m ON m.id = c.manager_id
JOIN erp.sales_documents d ON d.id = r.document_id;
COMMENT ON VIEW analytics.receivables IS 'Дебиторка: открытые позиции на дату выгрузки, остаток долга и просрочка в днях';

CREATE VIEW analytics.plan_fact AS
WITH fact AS (
    SELECT manager_id, doc_month AS month, SUM(amount_kzt) AS fact_kzt
    FROM analytics.sales_documents
    GROUP BY manager_id, doc_month
)
SELECT p.manager_id, m.full_name AS manager_name, m.city, p.month,
       p.plan_amount_kzt AS plan_kzt,
       COALESCE(f.fact_kzt, 0) AS fact_kzt,
       ROUND(100 * COALESCE(f.fact_kzt, 0) / NULLIF(p.plan_amount_kzt, 0), 1) AS completion_pct
FROM erp.sales_plans p
JOIN erp.managers m ON m.id = p.manager_id
LEFT JOIN fact f ON f.manager_id = p.manager_id AND f.month = p.month;
COMMENT ON VIEW analytics.plan_fact IS 'План/факт по менеджерам за месяц (факт — чистая выручка с НДС). Ноябрь 2026 — неполный месяц';
