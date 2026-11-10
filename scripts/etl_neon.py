#etl
import json
import os
import re
from datetime import date
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Configuración
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SQL_FILE = BASE_DIR / "sql" / "schema.sql"
INVALID_EMAILS_FILE = BASE_DIR / "reports" / "clientes_email_invalido.csv"
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
EMAIL_VALID_THRESHOLD = 95

load_dotenv(BASE_DIR / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no está definido en .env")

engine = create_engine(DATABASE_URL)

# Tiendas oficiales
TIENDAS = [
    ("T001", "Arequipa Cayma", "Arequipa"),
    ("T002", "Arequipa Cercado", "Arequipa"),
    ("T003", "Arequipa Yanahuara", "Arequipa"),
    ("T004", "Lima Norte", "Lima"),
    ("T005", "Lima Centro", "Lima"),
    ("T006", "Lima Sur", "Lima"),
]

# Utilidades de limpieza
def clean_text(series: pd.Series) -> pd.Series:
    """Quita espacios y convierte a Title Case."""
    return series.astype(str).str.strip().str.title()


def parse_price(series: pd.Series) -> pd.Series:
    """Limpia precios: quita 'S/', comas decimales y caracteres no numéricos."""
    s = series.astype(str).str.strip()
    s = s.str.replace("S/", "", regex=False)
    s = s.str.replace(",", ".")
    s = s.str.replace(r"[^0-9.\-]", "", regex=True)
    return pd.to_numeric(s, errors="coerce")


def parse_fecha_arequipa(value) -> date:
    """Arequipa viene en formato YYYY-MM-DD."""
    try:
        return pd.to_datetime(str(value).strip(), format="%Y-%m-%d").date()
    except Exception:
        return pd.NaT


def parse_fecha_lima(value) -> date:
    """Lima viene mezclado: DD/MM/YYYY y YYYY-MM-DD."""
    s = str(value).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return pd.to_datetime(s, format=fmt).date()
        except Exception:
            continue
    return pd.NaT


def fix_future_date(d):
    """Cambia el año a 2026 si la fecha es futura."""
    if pd.isna(d):
        return d
    if d.year > 2026:
        return date(2026, d.month, d.day)
    return d


def normalize_email(value):
    email = str(value).strip()
    if not email:
        return None
    domain = "correo.pe"
    if "@" not in email and email.lower().endswith(domain):
        local = email[:-len(domain)].strip()
        if local:
            email = f"{local}@{domain}"
    return email


def clean_email(series: pd.Series) -> pd.Series:
    """Email: strip, corrige @ faltante antes de correo.pe."""
    return series.apply(lambda x: normalize_email(x) if pd.notna(x) else None)


def is_valid_email(email) -> bool:
    return isinstance(email, str) and bool(EMAIL_PATTERN.fullmatch(email))


def register_invalid_emails(dim_cliente: pd.DataFrame):
    """Registra emails inválidos para corrección en la fuente."""
    invalid_mask = ~dim_cliente["email"].map(is_valid_email)
    invalid = dim_cliente.loc[invalid_mask, ["id_cliente", "nombre", "email"]].copy()
    invalid["motivo"] = invalid["email"].apply(
        lambda x: "vacio" if pd.isna(x) else "formato_invalido"
    )

    INVALID_EMAILS_FILE.parent.mkdir(parents=True, exist_ok=True)
    invalid.to_csv(INVALID_EMAILS_FILE, index=False)

    total = len(dim_cliente)
    validos = int((~invalid_mask).sum())
    porcentaje = validos / total * 100 if total else 0
    estado = "OK" if porcentaje >= EMAIL_VALID_THRESHOLD else "ALERTA"
    print(f"  Email válido: {validos}/{total} ({porcentaje:.1f}%) - {estado}")
    print(f"    Inválidos registrados en {INVALID_EMAILS_FILE.relative_to(BASE_DIR)}")


# Extracción
def extract():
    print("Extrayendo datos...")

    df_areq = pd.read_csv(DATA_DIR / "ventas_arequipa.csv", sep=";")
    df_lima = pd.read_csv(DATA_DIR / "ventas_lima.csv", sep=",")
    # Eliminar duplicados exactos de Lima
    df_lima = df_lima.drop_duplicates()

    with open(DATA_DIR / "clientes.json", encoding="utf-8") as f:
        df_clientes = pd.DataFrame(json.load(f))

    df_productos = pd.read_excel(DATA_DIR / "productos.xlsx")

    print(f"  Arequipa: {len(df_areq)} filas")
    print(f"  Lima (sin duplicados): {len(df_lima)} filas")
    print(f"  Clientes: {len(df_clientes)} filas")
    print(f"  Productos: {len(df_productos)} filas")

    return df_areq, df_lima, df_clientes, df_productos


# Transformación de ventas
def transform_ventas(df_areq: pd.DataFrame, df_lima: pd.DataFrame) -> pd.DataFrame:
    print("Transformando ventas...")

    # --- Arequipa ---
    areq = df_areq.rename(
        columns={
            "nro_venta": "id_venta",
            "cod_cliente": "id_cliente",
            "cod_producto": "id_producto",
            "cant": "cantidad",
            "precio": "precio_unitario",
            "sucursal": "tienda",
        }
    )
    areq["canal"] = "Tienda"
    areq["origen"] = "arequipa"

    # --- Lima ---
    lima = df_lima.rename(columns={"fecha": "fecha_venta"})
    lima["origen"] = "lima"

    # Unir
    ventas = pd.concat([areq, lima], ignore_index=True)

    # Columnas de texto
    ventas["id_venta"] = ventas["id_venta"].astype(str).str.strip()
    ventas["id_cliente"] = ventas["id_cliente"].fillna("C0000").astype(str).str.strip()
    ventas.loc[ventas["id_cliente"] == "", "id_cliente"] = "C0000"
    ventas["id_producto"] = ventas["id_producto"].astype(str).str.strip()
    ventas["tienda"] = clean_text(ventas["tienda"])
    ventas["canal"] = ventas["canal"].fillna("Tienda").astype(str).str.strip().str.title()

    # Fechas
    # Arequipa usa YYYY-MM-DD, Lima mixta. Detectamos por origen.
    mask_areq = ventas["origen"] == "arequipa"
    ventas.loc[mask_areq, "fecha_venta"] = ventas.loc[mask_areq, "fecha_venta"].apply(parse_fecha_arequipa)
    ventas.loc[~mask_areq, "fecha_venta"] = ventas.loc[~mask_areq, "fecha_venta"].apply(parse_fecha_lima)
    ventas["fecha_venta"] = ventas["fecha_venta"].apply(fix_future_date)

    # Eliminar filas con fecha inválida
    ventas = ventas[ventas["fecha_venta"].notna()].copy()

    # Cantidad y precio
    ventas["cantidad"] = pd.to_numeric(ventas["cantidad"], errors="coerce").astype("Int64")
    ventas = ventas[ventas["cantidad"].notna()].copy()
    # Descartar cantidades estrictamente 0
    ventas = ventas[ventas["cantidad"] != 0].copy()
    ventas["cantidad"] = ventas["cantidad"].astype(int)

    ventas["precio_unitario"] = parse_price(ventas["precio_unitario"])
    ventas = ventas[ventas["precio_unitario"].notna()].copy()
    ventas["precio_unitario"] = ventas["precio_unitario"].round(2)

    # Moneda y tipo de transacción
    ventas["moneda"] = "PEN"
    ventas["tipo_transaccion"] = ventas["cantidad"].apply(lambda x: "Devolucion" if x < 0 else "Venta")

    # Monto total
    ventas["monto_total"] = (ventas["cantidad"] * ventas["precio_unitario"]).round(2)

    # Validar unicidad de id_venta
    if not ventas["id_venta"].is_unique:
        dup = ventas[ventas["id_venta"].duplicated(keep=False)]["id_venta"].unique()
        raise ValueError(f"id_venta duplicados: {dup}")

    # Mapear tienda a id_tienda
    tienda_map = {nombre: id_t for id_t, nombre, _ in TIENDAS}
    ventas["id_tienda"] = ventas["tienda"].map(tienda_map)
    missing_tiendas = ventas.loc[ventas["id_tienda"].isna(), "tienda"].unique()
    if len(missing_tiendas):
        raise ValueError(f"Tiendas no reconocidas: {missing_tiendas}")

    # Seleccionar columnas finales
    cols = [
        "id_venta",
        "fecha_venta",
        "id_cliente",
        "id_producto",
        "id_tienda",
        "cantidad",
        "precio_unitario",
        "monto_total",
        "canal",
        "moneda",
        "origen",
        "tipo_transaccion",
    ]
    ventas = ventas[cols].copy()

    print(f"  Ventas transformadas: {len(ventas)} filas")
    print(f"    Devoluciones: {(ventas['tipo_transaccion'] == 'Devolucion').sum()}")
    print(f"    Origen lima: {(ventas['origen'] == 'lima').sum()}, arequipa: {(ventas['origen'] == 'arequipa').sum()}")

    return ventas


# Transformación de dimensiones
def transform_dimensiones(df_clientes: pd.DataFrame, df_productos: pd.DataFrame):
    print("Transformando dimensiones...")

    # --- dim_tienda ---
    dim_tienda = pd.DataFrame(TIENDAS, columns=["id_tienda", "nombre_tienda", "region"])

    # --- dim_cliente ---
    dim_cliente = df_clientes.copy()
    dim_cliente["id_cliente"] = dim_cliente["id_cliente"].astype(str).str.strip()
    dim_cliente["nombre"] = dim_cliente["nombre"].astype(str).str.strip()
    dim_cliente["email"] = clean_email(dim_cliente["email"])
    register_invalid_emails(dim_cliente)
    dim_cliente["ciudad"] = clean_text(dim_cliente["ciudad"])
    dim_cliente["segmento"] = clean_text(dim_cliente["segmento"])
    dim_cliente["fecha_registro"] = pd.to_datetime(
        dim_cliente["fecha_registro"], format="%Y-%m-%d"
    ).dt.date

    # Cliente genérico para ventas sin cliente
    c0000 = pd.DataFrame(
        [
            {
                "id_cliente": "C0000",
                "nombre": "Venta Anónima",
                "email": None,
                "ciudad": "No Especificada",
                "fecha_registro": date(1900, 1, 1),
                "segmento": "Sin Segmento",
            }
        ]
    )
    dim_cliente = pd.concat([dim_cliente, c0000], ignore_index=True)
    dim_cliente = dim_cliente.drop_duplicates(subset=["id_cliente"])

    # --- dim_producto ---
    dim_producto = df_productos.copy()
    dim_producto["id_producto"] = dim_producto["id_producto"].astype(str).str.strip()
    dim_producto["nombre_producto"] = dim_producto["nombre_producto"].astype(str).str.strip()
    dim_producto["categoria"] = clean_text(dim_producto["categoria"])
    dim_producto["costo"] = pd.to_numeric(dim_producto["costo"], errors="coerce").round(2)
    dim_producto["precio_lista"] = pd.to_numeric(dim_producto["precio_lista"], errors="coerce").round(2)

    # Producto no catalogado
    p999 = pd.DataFrame(
        [
            {
                "id_producto": "P999",
                "nombre_producto": "Producto No Catalogado",
                "categoria": "Sin Categoría",
                "costo": 0.00,
                "precio_lista": 0.00,
            }
        ]
    )
    dim_producto = pd.concat([dim_producto, p999], ignore_index=True)
    dim_producto = dim_producto.drop_duplicates(subset=["id_producto"])

    print(f"  dim_tienda: {len(dim_tienda)} filas")
    print(f"  dim_cliente: {len(dim_cliente)} filas")
    print(f"  dim_producto: {len(dim_producto)} filas")

    return dim_tienda, dim_cliente, dim_producto


# Carga a NeonDB
def load_schema():
    print("Creando esquema en NeonDB...")
    with open(SQL_FILE, encoding="utf-8") as f:
        schema_sql = f.read()
    with engine.begin() as conn:
        conn.exec_driver_sql(schema_sql)
    print("  Esquema creado.")


def truncate_tables():
    print("Limpiando tablas (TRUNCATE)...")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "TRUNCATE TABLE fact_venta, dim_cliente, dim_producto, dim_tienda RESTART IDENTITY CASCADE"
        )


def load_dimensions(dim_tienda, dim_cliente, dim_producto):
    print("Cargando dimensiones...")
    dim_tienda.to_sql("dim_tienda", engine, if_exists="append", index=False)
    dim_cliente.to_sql("dim_cliente", engine, if_exists="append", index=False)
    dim_producto.to_sql("dim_producto", engine, if_exists="append", index=False)
    print("  Dimensiones cargadas.")


def get_sk_mappings():
    print("Obteniendo mapeos de surrogate keys...")
    with engine.connect() as conn:
        tienda_sk = pd.read_sql("SELECT tienda_sk, id_tienda FROM dim_tienda", conn)
        cliente_sk = pd.read_sql("SELECT cliente_sk, id_cliente FROM dim_cliente", conn)
        producto_sk = pd.read_sql("SELECT producto_sk, id_producto FROM dim_producto", conn)
    return tienda_sk, cliente_sk, producto_sk


def load_facts(ventas: pd.DataFrame):
    print("Cargando hechos...")

    tienda_sk, cliente_sk, producto_sk = get_sk_mappings()

    tienda_map = dict(zip(tienda_sk["id_tienda"], tienda_sk["tienda_sk"]))
    cliente_map = dict(zip(cliente_sk["id_cliente"], cliente_sk["cliente_sk"]))
    producto_map = dict(zip(producto_sk["id_producto"], producto_sk["producto_sk"]))

    ventas = ventas.copy()
    ventas["tienda_sk"] = ventas["id_tienda"].map(tienda_map)
    ventas["cliente_sk"] = ventas["id_cliente"].map(cliente_map)
    ventas["producto_sk"] = ventas["id_producto"].map(producto_map)

    # Validar que no haya FKs nulas
    for col, name in [("tienda_sk", "tienda"), ("cliente_sk", "cliente"), ("producto_sk", "producto")]:
        if ventas[col].isna().any():
            missing = ventas.loc[ventas[col].isna(), f"id_{name}"].unique()
            raise ValueError(f"{name.capitalize()}s sin match en dimensión: {missing}")

    fact_cols = [
        "id_venta",
        "fecha_venta",
        "cliente_sk",
        "producto_sk",
        "tienda_sk",
        "cantidad",
        "precio_unitario",
        "monto_total",
        "canal",
        "moneda",
        "origen",
        "tipo_transaccion",
    ]
    fact = ventas[fact_cols].copy()
    fact["fecha_venta"] = pd.to_datetime(fact["fecha_venta"]).dt.date
    fact["precio_unitario"] = fact["precio_unitario"].round(2)
    fact["monto_total"] = fact["monto_total"].round(2)

    fact.to_sql("fact_venta", engine, if_exists="append", index=False)
    print(f"  Hechos cargados: {len(fact)} filas")


# Verificación
def verify():
    print("Verificando datos en NeonDB...")
    with engine.connect() as conn:
        for table in ["dim_tienda", "dim_cliente", "dim_producto", "fact_venta"]:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            print(f"  {table}: {count} filas")

        # Integridad referencial
        orphans = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM fact_venta f
                LEFT JOIN dim_cliente c ON f.cliente_sk = c.cliente_sk
                LEFT JOIN dim_producto p ON f.producto_sk = p.producto_sk
                LEFT JOIN dim_tienda t ON f.tienda_sk = t.tienda_sk
                WHERE c.cliente_sk IS NULL OR p.producto_sk IS NULL OR t.tienda_sk IS NULL
                """
            )
        ).scalar()
        print(f"  Ventas huérfanas (sin match en dimensiones): {orphans}")

        # Resumen de la vista
        resumen = conn.execute(
            text(
                """
                SELECT origen, tipo_transaccion, COUNT(*) as ventas, SUM(monto_total) as total
                FROM fact_venta
                GROUP BY origen, tipo_transaccion
                ORDER BY origen, tipo_transaccion
                """
            )
        ).fetchall()
        print("  Resumen por origen y tipo:")
        for row in resumen:
            print(f"    {row.origen:10s} {row.tipo_transaccion:12s} ventas={row.ventas:4d}  total=S/ {row.total:,.2f}")


# Main
def main():
    df_areq, df_lima, df_clientes, df_productos = extract()
    ventas = transform_ventas(df_areq, df_lima)
    dim_tienda, dim_cliente, dim_producto = transform_dimensiones(df_clientes, df_productos)

    load_schema()
    truncate_tables()
    load_dimensions(dim_tienda, dim_cliente, dim_producto)
    load_facts(ventas)
    verify()

    print("\nETL completado exitosamente.")


if __name__ == "__main__":
    main()
