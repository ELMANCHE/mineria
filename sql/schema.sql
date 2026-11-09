-- Modelo estrella: dim_tienda, dim_cliente, dim_producto, fact_venta
-- Dimensión de tiendas/sucursales
CREATE TABLE IF NOT EXISTS dim_tienda (
    tienda_sk      SERIAL PRIMARY KEY,
    id_tienda      VARCHAR(20)  NOT NULL UNIQUE,
    nombre_tienda  VARCHAR(100) NOT NULL,
    region         VARCHAR(50)  NOT NULL
);

-- Dimensión de clientes
CREATE TABLE IF NOT EXISTS dim_cliente (
    cliente_sk     SERIAL PRIMARY KEY,
    id_cliente     VARCHAR(20)  NOT NULL UNIQUE,
    nombre         VARCHAR(150) NOT NULL,
    email          VARCHAR(150),
    ciudad         VARCHAR(100) NOT NULL,
    fecha_registro DATE         NOT NULL,
    segmento       VARCHAR(50)  NOT NULL
);

-- Dimensión de productos
CREATE TABLE IF NOT EXISTS dim_producto (
    producto_sk    SERIAL PRIMARY KEY,
    id_producto    VARCHAR(20)  NOT NULL UNIQUE,
    nombre_producto VARCHAR(150) NOT NULL,
    categoria      VARCHAR(100) NOT NULL,
    costo          NUMERIC(12,2) NOT NULL,
    precio_lista   NUMERIC(12,2) NOT NULL
);

-- Tabla de hechos: ventas
CREATE TABLE IF NOT EXISTS fact_venta (
    venta_sk         SERIAL PRIMARY KEY,
    id_venta         VARCHAR(20)   NOT NULL UNIQUE,
    fecha_venta      DATE          NOT NULL,
    cliente_sk       INTEGER       NOT NULL REFERENCES dim_cliente(cliente_sk),
    producto_sk      INTEGER       NOT NULL REFERENCES dim_producto(producto_sk),
    tienda_sk        INTEGER       NOT NULL REFERENCES dim_tienda(tienda_sk),
    cantidad         INTEGER       NOT NULL CHECK (cantidad <> 0),
    precio_unitario  NUMERIC(12,2) NOT NULL CHECK (precio_unitario >= 0),
    monto_total      NUMERIC(14,2) NOT NULL,
    canal            VARCHAR(20)   NOT NULL,
    moneda           VARCHAR(3)    NOT NULL DEFAULT 'PEN' CHECK (moneda = 'PEN'),
    origen           VARCHAR(20)   NOT NULL,
    tipo_transaccion VARCHAR(20)   NOT NULL DEFAULT 'Venta' CHECK (tipo_transaccion IN ('Venta', 'Devolucion'))
);

-- Índices para acelerar consultas desde Streamlit
CREATE INDEX IF NOT EXISTS idx_fact_venta_fecha     ON fact_venta(fecha_venta);
CREATE INDEX IF NOT EXISTS idx_fact_venta_cliente   ON fact_venta(cliente_sk);
CREATE INDEX IF NOT EXISTS idx_fact_venta_producto  ON fact_venta(producto_sk);
CREATE INDEX IF NOT EXISTS idx_fact_venta_tienda    ON fact_venta(tienda_sk);
CREATE INDEX IF NOT EXISTS idx_fact_venta_origen    ON fact_venta(origen);
CREATE INDEX IF NOT EXISTS idx_fact_venta_canal     ON fact_venta(canal);
CREATE INDEX IF NOT EXISTS idx_fact_venta_tipo      ON fact_venta(tipo_transaccion);

-- Vista consolidada para consultas analíticas
CREATE OR REPLACE VIEW vw_ventas AS
SELECT
    f.venta_sk,
    f.id_venta,
    f.fecha_venta,
    f.cantidad,
    f.precio_unitario,
    f.monto_total,
    f.canal,
    f.moneda,
    f.origen,
    f.tipo_transaccion,
    c.id_cliente,
    c.nombre        AS cliente,
    c.ciudad,
    c.segmento,
    p.id_producto,
    p.nombre_producto,
    p.categoria,
    t.id_tienda,
    t.nombre_tienda,
    t.region
FROM fact_venta f
JOIN dim_cliente  c ON f.cliente_sk  = c.cliente_sk
JOIN dim_producto p ON f.producto_sk = p.producto_sk
JOIN dim_tienda   t ON f.tienda_sk   = t.tienda_sk;
