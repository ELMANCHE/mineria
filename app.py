import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine

# Configuración
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")

st.set_page_config(page_title="Análisis de Minimarket Andino", layout="wide")

st.title("Análisis de Minimarket Andino")

# Sidebar reservada para los filtros del equipo
st.sidebar.header("Filtros")
st.sidebar.info("Aquí se agregarán los filtros de fecha, tienda, canal y cliente.")

if not DATABASE_URL:
    st.error("No se encontró DATABASE_URL en el archivo .env")
    st.stop()

engine = create_engine(DATABASE_URL)


@st.cache_data(ttl=300)
def get_totales():
    query = """
    SELECT
        COUNT(*) AS total_transacciones,
        SUM(CASE WHEN tipo_transaccion = 'Venta' THEN 1 ELSE 0 END) AS total_ventas,
        SUM(CASE WHEN tipo_transaccion = 'Devolucion' THEN 1 ELSE 0 END) AS total_devoluciones,
        SUM(monto_total) AS monto_total,
        SUM(CASE WHEN tipo_transaccion = 'Venta' THEN monto_total ELSE 0 END) AS monto_ventas,
        SUM(CASE WHEN tipo_transaccion = 'Devolucion' THEN monto_total ELSE 0 END) AS monto_devoluciones
    FROM fact_venta
    """
    with engine.connect() as conn:
        return pd.read_sql(query, conn).iloc[0]


@st.cache_data(ttl=300)
def get_conteos_dimensiones():
    query = """
    SELECT
        (SELECT COUNT(*) FROM dim_cliente) AS clientes,
        (SELECT COUNT(*) FROM dim_producto) AS productos,
        (SELECT COUNT(*) FROM dim_tienda) AS tiendas
    """
    with engine.connect() as conn:
        return pd.read_sql(query, conn).iloc[0]


totales = get_totales()
dims = get_conteos_dimensiones()

# Totales de ventas
st.subheader("Totales de ventas")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Transacciones", int(totales["total_transacciones"]))
c2.metric("Ventas", int(totales["total_ventas"]))
c3.metric("Devoluciones", int(totales["total_devoluciones"]))
c4.metric("Monto total (S/)", f"S/ {totales['monto_total']:,.2f}")

c5, c6 = st.columns(2)
c5.metric("Monto ventas (S/)", f"S/ {totales['monto_ventas']:,.2f}")
c6.metric("Monto devoluciones (S/)", f"S/ {totales['monto_devoluciones']:,.2f}")

# Totales del modelo
st.subheader("Totales del modelo")
d1, d2, d3 = st.columns(3)
d1.metric("Clientes", int(dims["clientes"]))
d2.metric("Productos", int(dims["productos"]))
d3.metric("Tiendas", int(dims["tiendas"]))
