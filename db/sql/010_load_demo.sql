-- Загрузка демо-выгрузки из CSV. Запускать из каталога с CSV (db/apply.sh делает это сам).
\set ON_ERROR_STOP on

BEGIN;
\copy erp.export_info     FROM 'export_info.csv'     WITH (FORMAT csv, HEADER true)
\copy erp.warehouses      FROM 'warehouses.csv'      WITH (FORMAT csv, HEADER true)
\copy erp.products        FROM 'products.csv'        WITH (FORMAT csv, HEADER true)
\copy erp.managers        FROM 'managers.csv'        WITH (FORMAT csv, HEADER true)
\copy erp.clients         FROM 'clients.csv'         WITH (FORMAT csv, HEADER true)
\copy erp.sales_documents FROM 'sales_documents.csv' WITH (FORMAT csv, HEADER true)
\copy erp.sales_lines     FROM 'sales_lines.csv'     WITH (FORMAT csv, HEADER true)
\copy erp.stock_balances  FROM 'stock_balances.csv'  WITH (FORMAT csv, HEADER true)
\copy erp.receivables     FROM 'receivables.csv'     WITH (FORMAT csv, HEADER true)
\copy erp.sales_plans     FROM 'sales_plans.csv'     WITH (FORMAT csv, HEADER true)

-- id загружены явно — сдвигаем identity-последовательности
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['warehouses', 'products', 'managers', 'clients', 'sales_documents',
                             'sales_lines', 'stock_balances', 'receivables', 'sales_plans'] LOOP
        EXECUTE format('SELECT setval(pg_get_serial_sequence(%L, ''id''), (SELECT max(id) FROM erp.%I))',
                       'erp.' || t, t);
    END LOOP;
END
$$;
COMMIT;

ANALYZE;
