import os
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import gspread
from gspread.exceptions import APIError
from google.oauth2 import service_account
from process_data import process_pdfs, list_new_pdfs_from_drive, process_pdfs_from_drive

REQUIRED_COLUMNS = {
    "fecha",
    "identificativo de ticket",
    "ubicación",
    "item",
    "categoría",
    "precio",
    "url",
}

OPTIONAL_COLUMNS = {
    "peso",
    "precio_por_kg",
}

NUMERIC_COLUMN_PRECISION = {
    "precio": 2,
    "peso": 3,
    "precio_por_kg": 2,
}

# Configuración de la página de Streamlit
st.set_page_config(
    page_title="Mercadona Data Analysis",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Ruta del logo
logo_path = "images/logo.png"  # Cambia esto a la ubicación de tu archivo de logo


@st.cache_resource
def get_gsheets_client():
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
    )
    return gspread.authorize(creds)


@st.cache_data(ttl=300)
def load_data() -> pd.DataFrame:
    try:
        gc = get_gsheets_client()
        sh = gc.open_by_key(st.secrets["google"]["spreadsheet_id"])
        ws = sh.sheet1
        records = ws.get_all_records()
        return pd.DataFrame(records)
    except PermissionError as exc:
        raise RuntimeError(
            "Google Sheets sin permisos. Comparte la hoja con el email del service account "
            "(Editor) y verifica que spreadsheet_id sea correcto."
        ) from exc
    except APIError as exc:
        raise RuntimeError(f"Error de API de Google Sheets: {exc}") from exc


def _normalize_decimal(value):
    s = str(value).strip().replace("€", "").replace(" ", "")
    if not s or s.lower() == "nan":
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    return s


def _normalize_optional_numeric_column(series: pd.Series, column_name: str) -> pd.Series:
    raw_values = series.astype(str)
    parsed_values = pd.to_numeric(series.map(_normalize_decimal), errors="coerce")
    has_decimal_marker = raw_values.str.contains(r"[,.]", regex=True)

    if column_name == "peso":
        needs_rescale = parsed_values.notna() & (~has_decimal_marker) & (parsed_values >= 10)
        parsed_values.loc[needs_rescale] = parsed_values.loc[needs_rescale] / 1000
    elif column_name == "precio_por_kg":
        needs_rescale = parsed_values.notna() & (~has_decimal_marker) & (parsed_values >= 100)
        parsed_values.loc[needs_rescale] = parsed_values.loc[needs_rescale] / 100

    return parsed_values


def normalize_and_validate_data(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza columnas y valida el esquema mínimo esperado."""
    if df.empty:
        return df

    df = df.copy()
    df.columns = [str(col).strip().lower() for col in df.columns]

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise RuntimeError(
            "Faltan columnas en Google Sheets: "
            + ", ".join(sorted(missing))
            + ". Columnas detectadas: "
            + ", ".join(df.columns)
        )

    for column in OPTIONAL_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA

    df["fecha"] = pd.to_datetime(df["fecha"], format="%d/%m/%Y %H:%M", dayfirst=True, errors="coerce")

    raw_price = df["precio"].astype(str)

    parsed_price = pd.to_numeric(df["precio"].map(_normalize_decimal), errors="coerce")

    # Si la hoja convirtió 1.85 en 185 (locale), corregimos de céntimos a euros.
    has_decimal_marker = raw_price.str.contains(r"[,.]", regex=True)
    if (
        parsed_price.notna().any()
        and not has_decimal_marker.any()
        and (parsed_price >= 100).any()
    ):
        parsed_price = parsed_price / 100

    df["precio"] = parsed_price
    for column in OPTIONAL_COLUMNS:
        df[column] = _normalize_optional_numeric_column(
            df[column],
            column,
        )
    df = df.dropna(subset=["fecha", "precio"])
    return df


def format_decimal_for_sheet(value, precision: int) -> str:
    if pd.isna(value):
        return ""
    return f"{float(value):.{precision}f}".replace(".", ",")


def build_ticket_display(ticket_rows: pd.DataFrame) -> pd.DataFrame:
    display = ticket_rows.copy()
    display["precio"] = display["precio"].map(
        lambda x: f"€{x:.2f}" if pd.notna(x) else ""
    )

    columns = ["item", "categoría"]
    if "peso" in display.columns and display["peso"].notna().any():
        display["peso"] = display["peso"].map(
            lambda x: f"{x:.3f} kg" if pd.notna(x) else ""
        )
        columns.append("peso")
    if "precio_por_kg" in display.columns and display["precio_por_kg"].notna().any():
        display["precio_por_kg"] = display["precio_por_kg"].map(
            lambda x: f"€{x:.2f}/kg" if pd.notna(x) else ""
        )
        columns.append("precio_por_kg")

    columns.extend(["precio", "url"])
    return display[columns].sort_values("categoría")


def append_to_sheet(df_new: pd.DataFrame):
    try:
        gc = get_gsheets_client()
        sh = gc.open_by_key(st.secrets["google"]["spreadsheet_id"])
        ws = sh.sheet1
        df_to_write = df_new.copy()
        if "fecha" in df_to_write.columns:
            df_to_write["fecha"] = pd.to_datetime(
                df_to_write["fecha"],
                errors="coerce"
            ).dt.strftime("%d/%m/%Y %H:%M")
        for column, precision in NUMERIC_COLUMN_PRECISION.items():
            if column in df_to_write.columns:
                df_to_write[column] = df_to_write[column].map(
                    lambda x, current_precision=precision: format_decimal_for_sheet(x, current_precision)
                )

        existing_headers = [header.strip().lower() for header in ws.row_values(1) if header.strip()]
        if not existing_headers:
            header_order = df_to_write.columns.tolist()
            ws.append_row(header_order, value_input_option="RAW")
        else:
            missing_headers = [column for column in df_to_write.columns if column not in existing_headers]
            header_order = existing_headers + missing_headers
            if missing_headers:
                ws.update("1:1", [header_order], value_input_option="RAW")

        df_to_write = df_to_write.reindex(columns=header_order, fill_value="")
        ws.append_rows(df_to_write.values.tolist(), value_input_option="RAW")
    except PermissionError as exc:
        raise RuntimeError(
            "No se puede escribir en Google Sheets por permisos. Comparte la hoja con el "
            "service account como Editor."
        ) from exc
    except APIError as exc:
        raise RuntimeError(f"Error de API al escribir en Sheets: {exc}") from exc


def get_existing_ticket_ids(df_existing: pd.DataFrame) -> set:
    """Devuelve identificativos de ticket ya presentes en Sheets."""
    if df_existing is None or df_existing.empty:
        return set()

    temp = df_existing.copy()
    temp.columns = [str(c).strip().lower() for c in temp.columns]
    if "identificativo de ticket" not in temp.columns:
        return set()

    return {
        str(v).strip().lower()
        for v in temp["identificativo de ticket"].dropna().tolist()
        if str(v).strip()
    }


def get_new_tickets_only(df_new: pd.DataFrame, existing_ticket_ids: set) -> pd.DataFrame:
    """Filtra y deja solo tickets cuyo identificativo no existe aún en Sheets."""
    if df_new is None or df_new.empty:
        return pd.DataFrame(columns=df_new.columns if df_new is not None else [])

    normalized_new = normalize_and_validate_data(df_new)
    if normalized_new.empty:
        return normalized_new

    ids = normalized_new["identificativo de ticket"].astype(str).str.strip().str.lower()
    return normalized_new.loc[~ids.isin(existing_ticket_ids)]


filtered_data_by_month = pd.DataFrame()
filtered_data_by_categories = pd.DataFrame()

# Mostrar el logo como banner en la parte superior
if os.path.exists(logo_path):
    st.image(logo_path, width=600)  # Ajusta el ancho del logo según tu preferencia
else:
    st.warning(f"Logo no encontrado en {logo_path}")

# --- Importar tickets nuevos desde Gmail/Drive ---
if st.button("🔄 Importar tickets nuevos desde Gmail/Drive"):
    try:
        # list_new_pdfs_from_drive trabaja con IDs de archivo de Drive, no IDs de ticket.
        new_files = list_new_pdfs_from_drive(set())
        if new_files:
            with st.spinner(f"Procesando {len(new_files)} ticket(s)..."):
                df_new = process_pdfs_from_drive(new_files)
            if not df_new.empty:
                try:
                    df_existing = load_data()
                except Exception:
                    df_existing = pd.DataFrame(columns=df_new.columns)

                existing_ticket_ids = get_existing_ticket_ids(df_existing)
                df_to_append = get_new_tickets_only(df_new, existing_ticket_ids)
                if not df_to_append.empty:
                    append_to_sheet(df_to_append)
                    st.cache_data.clear()
                    st.success(
                        f"Importación completada: {df_to_append['identificativo de ticket'].nunique()} ticket(s) nuevo(s), "
                        f"{len(df_to_append)} fila(s) añadida(s)."
                    )
                else:
                    st.info("Los tickets ya estaban importados. No se añadieron duplicados.")
            else:
                st.warning("No se pudieron extraer datos de los PDFs descargados.")
        else:
            st.info("No hay tickets nuevos en Drive.")
    except Exception as e:
        st.error(f"Error al importar desde Drive: {e}")

st.divider()

# --- Subir archivos PDF manualmente ---
uploaded_files = st.file_uploader("O sube tus archivos PDF manualmente", type="pdf", accept_multiple_files=True)

if uploaded_files:
    st.success(f"Has subido {len(uploaded_files)} archivo(s) PDF.")
    if st.button("Procesar PDFs"):
        try:
            df_new = process_pdfs(uploaded_files)
            if df_new is not None and not df_new.empty:
                try:
                    df_existing = load_data()
                except Exception:
                    df_existing = pd.DataFrame(columns=df_new.columns)

                existing_ticket_ids = get_existing_ticket_ids(df_existing)
                df_to_append = get_new_tickets_only(df_new, existing_ticket_ids)
                if not df_to_append.empty:
                    append_to_sheet(df_to_append)
                    st.cache_data.clear()
                    st.success(
                        f"PDFs procesados: {df_to_append['identificativo de ticket'].nunique()} ticket(s) nuevo(s), "
                        f"{len(df_to_append)} fila(s) guardada(s) en Google Sheets."
                    )
                else:
                    st.info("Estos datos ya existían en Google Sheets. No se añadieron duplicados.")
            else:
                st.warning("No se encontraron datos válidos en los PDFs subidos.")
        except Exception as e:
            st.error(f"Error al procesar los archivos PDF: {e}")

# Barra lateral
with st.sidebar:
    st.title('🛒 Mercadona Data Analysis')

    selected_view = st.radio("Vista", ["📊 Dashboard", "🔍 Búsqueda de productos", "🧾 Consulta de tickets"])

    if selected_view == "📊 Dashboard":
        # Filtro por meses
        try:
            data = normalize_and_validate_data(load_data())
            if not data.empty:
                data.set_index("fecha", inplace=True)

                month_start_dates = data.index.to_period("M").to_timestamp().drop_duplicates().sort_values()
                selected_month_start = st.selectbox("Selecciona el mes", month_start_dates.strftime('%Y-%m'), index=0)
                selected_month_start = pd.Timestamp(selected_month_start)
                filtered_data_by_month = data[data.index.to_period("M").start_time == selected_month_start]

                # Filtro por categoría
                selected_category = st.selectbox("Selecciona la categoría", data["categoría"].unique())
                filtered_data_by_categories = data[data["categoría"] == selected_category]
            else:
                st.warning("No hay datos aún. Importa o sube tickets PDF.")
        except Exception as e:
            st.error(f"Error al cargar datos desde Google Sheets: {e}")

    st.subheader("Sobre la Aplicación")
    st.write('''
        - Esta aplicación pretende analizar los patrones de gasto en diferentes categorías y a lo largo del tiempo.
        - Beta Testing de [Izan](https://www.tiktok.com/@quarto.es/video/7402546595943730464), en desarrollo. ¡Se aceptan sugerencias!
    ''')

# Visualizaciones principales
if selected_view == "📊 Dashboard":
    try:
        data = normalize_and_validate_data(load_data())
        if not data.empty:
            data.set_index("fecha", inplace=True)

            if filtered_data_by_month.empty:
                filtered_data_by_month = data
            if filtered_data_by_categories.empty:
                filtered_data_by_categories = data

            # Métricas relevantes
            total_spent = data["precio"].sum()
            total_purchases = data["identificativo de ticket"].nunique()
            avg_spent_per_purchase = data.groupby("identificativo de ticket")["precio"].sum().mean()
            category_with_highest_spent = data.groupby("categoría")["precio"].sum().idxmax()
            total_items_sold = data['item'].nunique()
            avg_spent_per_month = data["precio"].resample('M').sum().mean()
            total_tickets_per_month = data.groupby(data.index.to_period('M')).size().mean()

            # Crear columnas para las métricas
            col1, col2, col3 = st.columns(3)

            # Mostrar las métricas en las columnas
            with col1:
                st.metric(label="Gasto Total", value=f"€{total_spent:.2f}")
                st.metric(label="Gasto Promedio por Compra", value=f"€{avg_spent_per_purchase:.2f}")
                st.metric(label="Número Total de Compras", value=total_purchases)
                st.metric(label="Items Vendidos", value=total_items_sold)

            with col2:
                st.metric(label="Categoría con Mayor Gasto", value=category_with_highest_spent)
                st.metric(label="Gasto Promedio Mensual", value=f"€{avg_spent_per_month:.2f}")
                st.metric(label="Tickets por Mes", value=f"{total_tickets_per_month:.2f}")

            with col3:
                st.metric(label="Total Gastado en el Mes Seleccionado", value=f"€{filtered_data_by_month['precio'].sum():.2f}")
                st.metric(label="Número de Compras en el Mes Seleccionado", value=filtered_data_by_month['identificativo de ticket'].nunique())
                st.metric(label="Categoría con Mayor Gasto en el Mes Seleccionado", value=filtered_data_by_month.groupby("categoría")["precio"].sum().idxmax())

            # Crear una sola fila con los gráficos principales
            col1, col2, col3 = st.columns(3)

            with col1:
                # Distribución del Gasto por Categoría
                total_price_per_category = data.groupby("categoría")["precio"].sum().reset_index()
                fig_pie = px.pie(total_price_per_category, values='precio', names='categoría', title='Distribución del Gasto por Categoría', labels={'precio': 'Gasto total (€)', 'categoría': 'Categoría'})
                st.plotly_chart(fig_pie)

            with col2:
                # Gasto Total por Mes
                monthly_expense = data["precio"].resample('M').sum().reset_index()
                fig_bar = px.bar(monthly_expense, x='fecha', y='precio', labels={'fecha': 'Mes', 'precio': 'Gasto (€)'})
                st.plotly_chart(fig_bar)

            with col3:
                # Precio Medio por Categoría
                avg_price_per_category = data.groupby("categoría")["precio"].mean().reset_index().sort_values(by="precio", ascending=False)
                fig_bar_avg = px.bar(avg_price_per_category, x='categoría', y='precio', labels={'precio': 'Precio Medio (€)'})
                st.plotly_chart(fig_bar_avg)

            # Análisis del Gasto en el Tiempo y Top 10 Items
            col1, col2 = st.columns(2)

            with col1:
                # Análisis del Gasto en el Tiempo
                daily_expense = data["precio"].resample('D').sum().reset_index()
                fig_line = px.line(daily_expense, x='fecha', y='precio', labels={'fecha': 'Fecha', 'precio': 'Gasto (€)'})
                st.plotly_chart(fig_line)

            with col2:
                # Top 25 Items con Mayor Gasto
                top_items = data.groupby('item')['precio'].sum().nlargest(25).reset_index()
                fig_top_items = px.bar(top_items, x='item', y='precio', labels={'item': 'Item', 'precio': 'Gasto (€)'})
                st.plotly_chart(fig_top_items)

            # Datos Filtrados
            col1, col2 = st.columns(2)

            with col1:
                st.subheader("Datos Filtrados por Categorías")
                st.dataframe(filtered_data_by_categories)

            with col2:
                st.subheader("Datos Filtrados por Mes")
                st.dataframe(filtered_data_by_month)

            # Heatmap del gasto por día y hora
            st.subheader("Heatmap del Gasto por Día y Hora")
            heatmap_source = data.copy()
            heatmap_source["day_of_week"] = heatmap_source.index.dayofweek
            heatmap_source["hour_of_day"] = heatmap_source.index.hour

            day_labels = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
            hour_labels = [f"{hour:02d}:00" for hour in range(24)]

            heatmap_data = (
                heatmap_source.pivot_table(
                    values="precio",
                    index="hour_of_day",
                    columns="day_of_week",
                    aggfunc="sum",
                    fill_value=0,
                )
                .reindex(index=range(24), columns=range(7), fill_value=0)
            )

            fig_heatmap = go.Figure(
                data=go.Heatmap(
                    z=heatmap_data.values,
                    x=day_labels,
                    y=hour_labels,
                    colorscale="Viridis",
                    hovertemplate="Día: %{x}<br>Hora: %{y}<br>Gasto: €%{z:.2f}<extra></extra>",
                )
            )
            fig_heatmap.update_layout(
                xaxis_title="Día de la Semana",
                yaxis_title="Hora del Día",
                yaxis=dict(autorange="reversed"),
            )
            st.plotly_chart(fig_heatmap, use_container_width=True)

        else:
            st.warning("No hay datos aún. Importa o sube tickets PDF para ver el análisis.")
    except Exception as e:
        st.error(f"Error al cargar datos desde Google Sheets: {e}")

elif selected_view == "🔍 Búsqueda de productos":
    st.header("🔍 Búsqueda de productos")
    try:
        data = normalize_and_validate_data(load_data())
        if data.empty:
            st.warning("No hay datos aún. Importa o sube tickets PDF.")
        else:
            search_query = st.text_input(
                "Busca un producto por nombre",
                placeholder="Ej: LECHE, YOGUR, ATÚN…",
            )

            if search_query.strip():
                matching_items = sorted(
                    data.loc[
                        data["item"].str.contains(search_query.strip(), case=False, na=False),
                        "item",
                    ].unique()
                )

                if not matching_items:
                    st.info(f"No se encontraron productos que coincidan con «{search_query}».")
                else:
                    selected_item = st.selectbox("Selecciona un producto", matching_items)

                    item_data = data[data["item"] == selected_item].sort_values("fecha")

                    # Métricas del producto
                    times_bought = item_data["identificativo de ticket"].nunique()
                    avg_price = item_data["precio"].mean()
                    min_price = item_data["precio"].min()
                    max_price = item_data["precio"].max()

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Veces comprado", times_bought)
                    with col2:
                        st.metric("Precio medio", f"€{avg_price:.2f}")
                    with col3:
                        st.metric("Rango de precio", f"€{min_price:.2f} – €{max_price:.2f}")

                    # Gráfico de evolución del precio
                    fig_price = px.scatter(
                        item_data,
                        x="fecha",
                        y="precio",
                        hover_data={"identificativo de ticket": True, "ubicación": True},
                        labels={"fecha": "Fecha", "precio": "Precio (€)"},
                        title=f"Evolución del precio: {selected_item}",
                    )
                    fig_price.update_traces(mode="lines+markers")
                    st.plotly_chart(fig_price, use_container_width=True)

                    # Seleccionar ticket para ver detalle completo
                    ticket_options = (
                        item_data[["fecha", "identificativo de ticket"]]
                        .drop_duplicates("identificativo de ticket")
                        .sort_values("fecha", ascending=False)
                    )
                    ticket_labels = {
                        row["identificativo de ticket"]: (
                            f"{row['fecha'].strftime('%d/%m/%Y %H:%M')}"
                            f" — {row['identificativo de ticket']}"
                        )
                        for _, row in ticket_options.iterrows()
                    }

                    selected_ticket_id = st.selectbox(
                        "Ver ticket completo de…",
                        options=list(ticket_labels.keys()),
                        format_func=lambda tid: ticket_labels[tid],
                    )

                    if selected_ticket_id:
                        st.subheader(f"🧾 Ticket {selected_ticket_id}")
                        ticket_rows = data[
                            data["identificativo de ticket"] == selected_ticket_id
                        ].copy()
                        ticket_display = build_ticket_display(ticket_rows)
                        ticket_total = ticket_rows["precio"].sum()

                        col_t1, col_t2 = st.columns([3, 1])
                        with col_t1:
                            st.dataframe(ticket_display, use_container_width=True, hide_index=True)
                        with col_t2:
                            st.metric("Total del ticket", f"€{ticket_total:.2f}")
                            st.metric(
                                "Fecha",
                                ticket_rows["fecha"].iloc[0].strftime("%d/%m/%Y"),
                            )
                            st.write(f"**Tienda:** {ticket_rows['ubicación'].iloc[0]}")
            else:
                st.info("Escribe el nombre de un producto para buscar su historial de compras.")
    except Exception as e:
        st.error(f"Error al cargar datos: {e}")

elif selected_view == "🧾 Consulta de tickets":
    st.header("🧾 Consulta de tickets")
    try:
        data = normalize_and_validate_data(load_data())
        if data.empty:
            st.warning("No hay datos aún. Importa o sube tickets PDF.")
        else:
            # --- Filtros ---
            col_f1, col_f2, col_f3 = st.columns([2, 2, 3])

            all_dates = data["fecha"].dt.date
            min_date = all_dates.min()
            max_date = all_dates.max()

            with col_f1:
                date_from = st.date_input("Desde", value=min_date, min_value=min_date, max_value=max_date)
            with col_f2:
                date_to = st.date_input("Hasta", value=max_date, min_value=min_date, max_value=max_date)
            with col_f3:
                store_query = st.text_input("Filtrar por tienda", placeholder="Ej: LINARES, MADRID…")

            # Aplicar filtros
            mask = (data["fecha"].dt.date >= date_from) & (data["fecha"].dt.date <= date_to)
            filtered = data[mask]
            if store_query.strip():
                filtered = filtered[filtered["ubicación"].str.contains(store_query.strip(), case=False, na=False)]

            if filtered.empty:
                st.info("No se encontraron tickets con los filtros aplicados.")
            else:
                # Resumen de tickets encontrados
                ticket_summary = (
                    filtered.groupby("identificativo de ticket")
                    .agg(
                        fecha=("fecha", "first"),
                        ubicación=("ubicación", "first"),
                        items=("item", "count"),
                        total=("precio", "sum"),
                    )
                    .sort_values("fecha", ascending=False)
                    .reset_index()
                )
                ticket_summary["fecha_str"] = ticket_summary["fecha"].dt.strftime("%d/%m/%Y %H:%M")
                ticket_summary["total_fmt"] = ticket_summary["total"].map(lambda x: f"€{x:.2f}")

                st.caption(f"{len(ticket_summary)} ticket(s) encontrado(s)")

                # Tabla de selección
                st.dataframe(
                    ticket_summary[["fecha_str", "identificativo de ticket", "ubicación", "items", "total_fmt"]].rename(columns={
                        "fecha_str": "Fecha",
                        "identificativo de ticket": "Ticket ID",
                        "ubicación": "Tienda",
                        "items": "Artículos",
                        "total_fmt": "Total",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )

                st.divider()

                # Selector de ticket para ver el detalle
                ticket_labels = {
                    row["identificativo de ticket"]: (
                        f"{row['fecha_str']} — {row['identificativo de ticket']} — {row['ubicación']}"
                    )
                    for _, row in ticket_summary.iterrows()
                }
                selected_ticket_id = st.selectbox(
                    "Ver detalle del ticket",
                    options=list(ticket_labels.keys()),
                    format_func=lambda tid: ticket_labels[tid],
                )

                if selected_ticket_id:
                    ticket_rows = filtered[filtered["identificativo de ticket"] == selected_ticket_id].copy()
                    ticket_display = build_ticket_display(ticket_rows)
                    ticket_total = ticket_rows["precio"].sum()

                    st.subheader(f"🧾 {ticket_labels[selected_ticket_id]}")
                    col_t1, col_t2 = st.columns([3, 1])
                    with col_t1:
                        st.dataframe(ticket_display, use_container_width=True, hide_index=True)
                    with col_t2:
                        st.metric("Total", f"€{ticket_total:.2f}")
                        st.metric("Artículos", len(ticket_rows))
                        st.write(f"**Tienda:** {ticket_rows['ubicación'].iloc[0]}")
    except Exception as e:
        st.error(f"Error al cargar datos: {e}")