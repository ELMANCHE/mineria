import os
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine

# Configuración
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")

st.set_page_config(
    page_title="Análisis de Minimarket Andino",
    page_icon="🛒",
    layout="wide",
)

COLOR_VENTA = "#2E7D6B"
COLOR_DEVOLUCION = "#D1495B"
COLOR_SERIE = ["#2E7D6B", "#E9A23B", "#3D5A80", "#D1495B", "#8D6A9F", "#6C757D"]
CLIENTE_ANONIMO = "C0000"


def soles(valor: float) -> str:
    """Formatea un monto en soles: S/ 1,234.56 (negativos como -S/ 1,234.56)."""
    signo = "-" if valor < 0 else ""
    return f"{signo}S/ {abs(valor):,.2f}"


def estilo_figura(fig, titulo: str, alto: int = 360):
    fig.update_layout(
        title=dict(text=titulo, font=dict(size=16)),
        height=alto,
        margin=dict(l=10, r=10, t=50, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        colorway=COLOR_SERIE,
    )
    return fig


# Acceso a datos
st.title("Análisis de Minimarket Andino")
st.caption("Tablero de ventas · modelo estrella en NeonDB (Arequipa y Lima)")

if not DATABASE_URL:
    st.error("No se encontró DATABASE_URL en el archivo .env")
    st.stop()


@st.cache_resource
def get_engine():
    return create_engine(DATABASE_URL, pool_pre_ping=True)


@st.cache_data(ttl=300, show_spinner="Cargando datos desde la base…")
def cargar_ventas() -> pd.DataFrame:
    """Lee la vista consolidada y normaliza tipos."""
    with get_engine().connect() as conn:
        df = pd.read_sql("SELECT * FROM vw_ventas", conn)
    df["fecha_venta"] = pd.to_datetime(df["fecha_venta"])
    for col in ["monto_total", "precio_unitario"]:
        df[col] = pd.to_numeric(df[col]).astype(float)
    df["cantidad"] = df["cantidad"].astype(int)
    return df


@st.cache_data(ttl=300)
def cargar_conteos_dimensiones() -> pd.Series:
    query = """
    SELECT
        (SELECT COUNT(*) FROM dim_cliente)  AS clientes,
        (SELECT COUNT(*) FROM dim_producto) AS productos,
        (SELECT COUNT(*) FROM dim_tienda)   AS tiendas
    """
    with get_engine().connect() as conn:
        return pd.read_sql(query, conn).iloc[0]


try:
    ventas = cargar_ventas()
    dims = cargar_conteos_dimensiones()
except Exception as exc:  # noqa: BLE001
    st.error("No se pudo leer la base de datos. Verifica DATABASE_URL y que el ETL se haya ejecutado.")
    st.exception(exc)
    st.stop()

if ventas.empty:
    st.warning("La base está vacía. Ejecuta primero `python scripts/etl_neon.py`.")
    st.stop()

# Filtros (sidebar)
st.sidebar.header("Filtros")

fmin = ventas["fecha_venta"].min().date()
fmax = ventas["fecha_venta"].max().date()
rango = st.sidebar.date_input(
    "Rango de fechas", value=(fmin, fmax), min_value=fmin, max_value=fmax
)
if isinstance(rango, (tuple, list)):
    f_ini = rango[0]
    f_fin = rango[1] if len(rango) > 1 else rango[0]
else:
    f_ini = f_fin = rango

regiones = sorted(ventas["region"].unique())
sel_regiones = st.sidebar.multiselect("Región", regiones, default=regiones)

tiendas_disp = sorted(ventas.loc[ventas["region"].isin(sel_regiones), "nombre_tienda"].unique())
sel_tiendas = st.sidebar.multiselect("Tienda", tiendas_disp, default=tiendas_disp)

canales = sorted(ventas["canal"].unique())
sel_canales = st.sidebar.multiselect("Canal", canales, default=canales)

categorias = sorted(ventas["categoria"].unique())
sel_categorias = st.sidebar.multiselect("Categoría de producto", categorias, default=categorias)

tipos = sorted(ventas["tipo_transaccion"].unique())
sel_tipos = st.sidebar.multiselect("Tipo de transacción", tipos, default=tipos)

segmentos = sorted(ventas["segmento"].unique())
sel_segmentos = st.sidebar.multiselect("Segmento de cliente", segmentos, default=segmentos)

# Cliente: etiqueta "ID · Nombre" porque hay nombres repetidos
clientes_df = (
    ventas[["id_cliente", "cliente"]]
    .drop_duplicates()
    .assign(etiqueta=lambda d: d["id_cliente"] + " · " + d["cliente"])
    .sort_values("id_cliente")
)
etiquetas = clientes_df["etiqueta"].tolist()
sel_etiquetas = st.sidebar.multiselect(
    "Cliente (vacío = todos)", etiquetas, placeholder="Buscar por código o nombre"
)
incluir_anonimas = st.sidebar.checkbox("Incluir ventas anónimas (C0000)", value=True)

if st.sidebar.button("🔄 Actualizar datos", width="stretch"):
    st.cache_data.clear()
    st.rerun()

# Aplicar filtros
mask = (
    (ventas["fecha_venta"].dt.date >= f_ini)
    & (ventas["fecha_venta"].dt.date <= f_fin)
    & ventas["region"].isin(sel_regiones)
    & ventas["nombre_tienda"].isin(sel_tiendas)
    & ventas["canal"].isin(sel_canales)
    & ventas["categoria"].isin(sel_categorias)
    & ventas["tipo_transaccion"].isin(sel_tipos)
    & ventas["segmento"].isin(sel_segmentos)
)
if sel_etiquetas:
    ids_sel = clientes_df.loc[clientes_df["etiqueta"].isin(sel_etiquetas), "id_cliente"]
    mask &= ventas["id_cliente"].isin(ids_sel)
if not incluir_anonimas:
    mask &= ventas["id_cliente"] != CLIENTE_ANONIMO

df = ventas[mask].copy()

if df.empty:
    st.warning("No hay datos con los filtros seleccionados. Amplía el rango o quita algún filtro.")
    st.stop()

df["monto_abs"] = df["monto_total"].abs()
solo_ventas = df[df["tipo_transaccion"] == "Venta"]
solo_dev = df[df["tipo_transaccion"] == "Devolucion"]

# KPIs
monto_ventas = solo_ventas["monto_total"].sum()
monto_dev = solo_dev["monto_total"].sum()  # negativo
monto_neto = df["monto_total"].sum()
n_ventas = len(solo_ventas)
n_dev = len(solo_dev)
ticket = monto_ventas / n_ventas if n_ventas else 0.0
tasa_dev = abs(monto_dev) / monto_ventas * 100 if monto_ventas else 0.0

st.subheader("Indicadores clave")
k1, k2, k3, k4 = st.columns(4)
k1.metric("Ventas brutas", soles(monto_ventas), help="Suma de transacciones tipo Venta")
k2.metric("Devoluciones", soles(monto_dev), help="Suma de transacciones tipo Devolución")
k3.metric("Ventas netas", soles(monto_neto), help="Ventas brutas + devoluciones")
k4.metric("Tasa de devolución", f"{tasa_dev:.1f}%", help="Monto devuelto / monto vendido")

k5, k6, k7, k8 = st.columns(4)
k5.metric("Transacciones", f"{len(df):,}")
k6.metric("N.º de ventas", f"{n_ventas:,}")
k7.metric("N.º de devoluciones", f"{n_dev:,}")
k8.metric("Ticket promedio", soles(ticket))

st.divider()

# Pestañas
tab_resumen, tab_prod, tab_cli, tab_datos, tab_modelo = st.tabs(
    ["Resumen", "Productos", "Clientes", "Datos", "Modelo"]
)

# Resumen 
with tab_resumen:
    granularidad = st.radio(
        "Agrupar evolución por", ["Día", "Semana", "Mes"], horizontal=True, index=1
    )
    freq = {"Día": "D", "Semana": "W-MON", "Mes": "MS"}[granularidad]

    evol = (
        df.set_index("fecha_venta")
        .groupby([pd.Grouper(freq=freq), "tipo_transaccion"])["monto_total"]
        .sum()
        .reset_index()
    )
    fig = px.bar(
        evol,
        x="fecha_venta",
        y="monto_total",
        color="tipo_transaccion",
        barmode="relative",
        color_discrete_map={"Venta": COLOR_VENTA, "Devolucion": COLOR_DEVOLUCION},
        labels={"fecha_venta": "Fecha", "monto_total": "Monto (S/)", "tipo_transaccion": "Tipo"},
    )
    estilo_figura(fig, f"Evolución de ventas y devoluciones ({granularidad.lower()})")
    st.plotly_chart(fig, width="stretch")

    col_a, col_b = st.columns(2)

    por_tienda = (
        df.groupby(["nombre_tienda", "region"], as_index=False)["monto_total"].sum()
        .sort_values("monto_total")
    )
    fig = px.bar(
        por_tienda,
        x="monto_total",
        y="nombre_tienda",
        color="region",
        orientation="h",
        text_auto=",.0f",
        labels={"monto_total": "Ventas netas (S/)", "nombre_tienda": "", "region": "Región"},
    )
    col_a.plotly_chart(estilo_figura(fig, "Ventas netas por tienda"), width="stretch")

    por_canal = df.groupby("canal", as_index=False)["monto_total"].sum()
    fig = px.pie(por_canal, names="canal", values="monto_total", hole=0.5)
    fig.update_traces(textinfo="percent+label")
    col_b.plotly_chart(estilo_figura(fig, "Participación por canal (ventas netas)"), width="stretch")

    col_c, col_d = st.columns(2)

    por_region = df.groupby(["region", "tipo_transaccion"], as_index=False)["monto_total"].sum()
    fig = px.bar(
        por_region,
        x="region",
        y="monto_total",
        color="tipo_transaccion",
        barmode="group",
        color_discrete_map={"Venta": COLOR_VENTA, "Devolucion": COLOR_DEVOLUCION},
        labels={"region": "Región", "monto_total": "Monto (S/)", "tipo_transaccion": "Tipo"},
    )
    col_c.plotly_chart(estilo_figura(fig, "Lima vs Arequipa"), width="stretch")

    por_origen = df.groupby("origen", as_index=False).agg(
        transacciones=("id_venta", "count"), monto=("monto_total", "sum")
    )
    por_origen["origen"] = por_origen["origen"].str.title()
    fig = px.bar(
        por_origen,
        x="origen",
        y="transacciones",
        text="transacciones",
        labels={"origen": "Sistema de origen", "transacciones": "Transacciones"},
    )
    col_d.plotly_chart(estilo_figura(fig, "Transacciones por sistema de origen"), width="stretch")

# Productos 
with tab_prod:
    top_n = st.slider("Cantidad de productos a mostrar", 3, 15, 10, key="top_prod")

    prod = (
        df.groupby(["nombre_producto", "categoria"], as_index=False)
        .agg(unidades=("cantidad", "sum"), monto=("monto_total", "sum"), transacciones=("id_venta", "count"))
    )

    c1, c2 = st.columns(2)
    top_monto = prod.nlargest(top_n, "monto").sort_values("monto")
    fig = px.bar(
        top_monto, x="monto", y="nombre_producto", color="categoria", orientation="h",
        text_auto=",.0f",
        labels={"monto": "Ventas netas (S/)", "nombre_producto": "", "categoria": "Categoría"},
    )
    c1.plotly_chart(estilo_figura(fig, f"Top {top_n} productos por monto"), width="stretch")

    top_uni = prod.nlargest(top_n, "unidades").sort_values("unidades")
    fig = px.bar(
        top_uni, x="unidades", y="nombre_producto", color="categoria", orientation="h",
        text_auto=True,
        labels={"unidades": "Unidades netas", "nombre_producto": "", "categoria": "Categoría"},
    )
    c2.plotly_chart(estilo_figura(fig, f"Top {top_n} productos por unidades"), width="stretch")

    c3, c4 = st.columns(2)
    por_cat = df.groupby("categoria", as_index=False)["monto_total"].sum()
    fig = px.pie(por_cat, names="categoria", values="monto_total", hole=0.5)
    fig.update_traces(textinfo="percent+label")
    c3.plotly_chart(estilo_figura(fig, "Ventas netas por categoría"), width="stretch")

    dev_prod = (
        solo_dev.groupby("nombre_producto", as_index=False)["monto_abs"].sum()
        .nlargest(top_n, "monto_abs")
        .sort_values("monto_abs")
    )
    if dev_prod.empty:
        c4.info("No hay devoluciones en la selección actual.")
    else:
        fig = px.bar(
            dev_prod, x="monto_abs", y="nombre_producto", orientation="h", text_auto=",.0f",
            color_discrete_sequence=[COLOR_DEVOLUCION],
            labels={"monto_abs": "Monto devuelto (S/)", "nombre_producto": ""},
        )
        c4.plotly_chart(estilo_figura(fig, "Productos más devueltos"), width="stretch")

    st.markdown("**Detalle por producto**")
    st.dataframe(
        prod.sort_values("monto", ascending=False).rename(
            columns={
                "nombre_producto": "Producto",
                "categoria": "Categoría",
                "unidades": "Unidades netas",
                "monto": "Ventas netas (S/)",
                "transacciones": "Transacciones",
            }
        ),
        width="stretch",
        hide_index=True,
        column_config={"Ventas netas (S/)": st.column_config.NumberColumn(format="S/ %.2f")},
    )

# Clientes 
with tab_cli:
    con_cliente = df[df["id_cliente"] != CLIENTE_ANONIMO]
    anon = df[df["id_cliente"] == CLIENTE_ANONIMO]

    m1, m2, m3 = st.columns(3)
    m1.metric("Clientes distintos", f"{con_cliente['id_cliente'].nunique():,}")
    m2.metric("Ventas anónimas (transacciones)", f"{len(anon):,}")
    m3.metric("Ventas anónimas (monto)", soles(anon["monto_total"].sum()))

    top_c = st.slider("Cantidad de clientes a mostrar", 5, 20, 10, key="top_cli")

    cli = (
        con_cliente.groupby(["id_cliente", "cliente", "segmento", "ciudad"], as_index=False)
        .agg(monto=("monto_total", "sum"), transacciones=("id_venta", "count"))
    )

    c1, c2 = st.columns(2)
    top_cli = cli.nlargest(top_c, "monto").sort_values("monto")
    top_cli["etiqueta"] = top_cli["id_cliente"] + " · " + top_cli["cliente"]
    fig = px.bar(
        top_cli, x="monto", y="etiqueta", color="segmento", orientation="h", text_auto=",.0f",
        labels={"monto": "Ventas netas (S/)", "etiqueta": "", "segmento": "Segmento"},
    )
    c1.plotly_chart(estilo_figura(fig, f"Top {top_c} clientes por monto", 420), width="stretch")

    por_seg = df.groupby("segmento", as_index=False)["monto_total"].sum()
    fig = px.pie(por_seg, names="segmento", values="monto_total", hole=0.5)
    fig.update_traces(textinfo="percent+label")
    c2.plotly_chart(estilo_figura(fig, "Ventas netas por segmento", 420), width="stretch")

    por_ciudad = (
        con_cliente.groupby("ciudad", as_index=False)["monto_total"].sum().sort_values("monto_total")
    )
    fig = px.bar(
        por_ciudad, x="monto_total", y="ciudad", orientation="h", text_auto=",.0f",
        color_discrete_sequence=[COLOR_SERIE[2]],
        labels={"monto_total": "Ventas netas (S/)", "ciudad": "Ciudad del cliente"},
    )
    st.plotly_chart(estilo_figura(fig, "Ventas netas por ciudad de residencia del cliente"), width="stretch")

# Datos
with tab_datos:
    st.caption(f"{len(df):,} transacciones con los filtros actuales.")
    columnas = [
        "id_venta", "fecha_venta", "tipo_transaccion", "nombre_tienda", "region", "canal",
        "id_cliente", "cliente", "segmento", "id_producto", "nombre_producto", "categoria",
        "cantidad", "precio_unitario", "monto_total", "origen",
    ]
    tabla = df.sort_values(["fecha_venta", "id_venta"], ascending=[False, True])[columnas]
    st.dataframe(
        tabla,
        width="stretch",
        hide_index=True,
        column_config={
            "fecha_venta": st.column_config.DateColumn("Fecha", format="YYYY-MM-DD"),
            "precio_unitario": st.column_config.NumberColumn("Precio unit.", format="S/ %.2f"),
            "monto_total": st.column_config.NumberColumn("Monto total", format="S/ %.2f"),
        },
    )
    st.download_button(
        "Descargar CSV filtrado",
        data=tabla.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"ventas_filtradas_{date.today():%Y%m%d}.csv",
        mime="text/csv",
    )

# Modelo
with tab_modelo:
    st.markdown("Tamaño del modelo estrella cargado en la base (sin filtros).")
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Clientes", int(dims["clientes"]))
    d2.metric("Productos", int(dims["productos"]))
    d3.metric("Tiendas", int(dims["tiendas"]))
    d4.metric("Transacciones (fact_venta)", f"{len(ventas):,}")
    st.caption(
        f"Rango de fechas en la base: {fmin:%d/%m/%Y} – {fmax:%d/%m/%Y}. "
        "Los datos se cachean 5 minutos; usa «Actualizar datos» para recargar."
    )