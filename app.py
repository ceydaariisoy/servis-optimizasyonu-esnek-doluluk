from __future__ import annotations

from io import BytesIO
import hashlib
import math
import re

import pandas as pd
import pydeck as pdk
import streamlit as st

from core import (
    CommonStop,
    assign_common_stops_to_routes,
    fetch_osrm_geometry,
    generate_candidate_stops,
    get_travel_matrices,
    optimize_candidate_stops,
    reverse_routes_for_return,
    update_routes_incrementally,
)


APP_VERSION = "2026.09.21-route-shape-v10"
FIXED_TARGET_AVERAGE_WALK_M = 400
FIXED_WAIT_SECONDS_PER_STOP = 15
MORNING_FACTORY_ARRIVAL_SECONDS = 7 * 3600 + 55 * 60
EVENING_FACTORY_DEPARTURE_SECONDS = 17 * 3600 + 40 * 60


st.set_page_config(
    page_title="Servis Rota Optimizasyonu",
    page_icon="🚌",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root {
        --navy: #17324D;
        --blue: #285A84;
        --blue-soft: #EAF1F7;
        --ink: #1F2937;
        --muted: #667085;
        --line: #DCE3EA;
        --surface: #FFFFFF;
        --background: #F6F8FA;
        --success: #147D64;
    }

    .stApp {background: var(--background);}
    .block-container {
        max-width: 1480px;
        padding-top: 1.4rem;
        padding-bottom: 4rem;
    }
    #MainMenu, footer {visibility: hidden;}
    [data-testid="stToolbar"] {display: none;}
    [data-testid="stSidebar"] {
        background: #F1F4F7;
        border-right: 1px solid var(--line);
    }
    [data-testid="stSidebarContent"] {padding-top: 1.1rem;}
    [data-testid="stSidebar"] hr {margin: 0.9rem 0;}
    [data-testid="stSidebar"] .stCaption {color: #687587;}

    .app-hero {
        display: flex;
        align-items: center;
        gap: 1.15rem;
        padding: 1.35rem 1.5rem;
        margin-bottom: 1.6rem;
        background: linear-gradient(120deg, #FFFFFF 0%, #F3F7FA 100%);
        border: 1px solid var(--line);
        border-radius: 18px;
        box-shadow: 0 10px 30px rgba(23, 50, 77, 0.06);
    }
    .hero-icon {
        width: 54px;
        height: 54px;
        flex: 0 0 54px;
        display: grid;
        place-items: center;
        background: var(--navy);
        border-radius: 14px;
        color: #FFFFFF;
    }
    .hero-kicker {
        color: var(--blue);
        font-size: 0.72rem;
        font-weight: 750;
        letter-spacing: 0.11em;
        margin-bottom: 0.18rem;
    }
    .app-hero h1 {
        color: var(--ink);
        font-size: clamp(1.75rem, 3vw, 2.45rem);
        line-height: 1.15;
        letter-spacing: -0.025em;
        margin: 0;
    }
    .app-hero p {
        color: var(--muted);
        font-size: 0.98rem;
        line-height: 1.55;
        margin: 0.4rem 0 0;
    }

    .section-heading {
        display: flex;
        align-items: flex-start;
        gap: 0.85rem;
        margin: 1.75rem 0 0.85rem;
    }
    .step-badge {
        min-width: 38px;
        height: 30px;
        display: grid;
        place-items: center;
        color: var(--blue);
        background: var(--blue-soft);
        border-radius: 9px;
        font-size: 0.76rem;
        font-weight: 800;
        letter-spacing: 0.04em;
    }
    .section-heading h2 {
        color: var(--ink);
        font-size: 1.3rem;
        line-height: 1.25;
        margin: 0;
    }
    .section-heading p {
        color: var(--muted);
        font-size: 0.88rem;
        line-height: 1.45;
        margin: 0.18rem 0 0;
    }

    .sidebar-brand {
        padding: 0.35rem 0 0.7rem;
    }
    .sidebar-brand strong {
        display: block;
        color: var(--navy);
        font-size: 1.08rem;
    }
    .sidebar-brand span {
        color: var(--muted);
        font-size: 0.79rem;
    }
    .sidebar-note {
        color: #607083;
        background: #FFFFFF;
        border: 1px solid var(--line);
        border-radius: 10px;
        padding: 0.7rem 0.8rem;
        font-size: 0.78rem;
        line-height: 1.45;
        margin-top: 0.6rem;
    }

    div[data-testid="stMetric"] {
        min-height: 104px;
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 13px;
        padding: 0.9rem 1rem;
        box-shadow: 0 4px 14px rgba(23, 50, 77, 0.035);
    }
    div[data-testid="stMetric"] label {
        color: var(--muted);
        font-size: 0.82rem;
        font-weight: 600;
    }
    div[data-testid="stMetricValue"] {
        color: var(--navy);
        font-size: 1.75rem;
    }
    [data-testid="stFileUploader"] {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 13px;
        padding: 0.85rem 1rem 0.35rem;
    }
    [data-testid="stFileUploaderDropzone"] {
        background: #FAFBFC;
        border-color: #B9C7D4;
        border-radius: 10px;
    }
    [data-testid="stExpander"] {
        background: var(--surface);
        border-color: var(--line);
        border-radius: 11px;
    }
    [data-testid="stAlert"] {border-radius: 11px;}
    .stButton > button[kind="primary"],
    .stDownloadButton > button[kind="primary"] {
        min-height: 2.85rem;
        background: var(--navy);
        border-color: var(--navy);
        border-radius: 10px;
        font-weight: 700;
        box-shadow: 0 6px 16px rgba(23, 50, 77, 0.14);
    }
    .stButton > button[kind="primary"]:hover,
    .stDownloadButton > button[kind="primary"]:hover {
        background: #214767;
        border-color: #214767;
    }

    .route-card {
        background: var(--surface);
        border: 1px solid var(--line);
        border-left: 5px solid var(--blue);
        padding: 1rem 1.15rem;
        border-radius: 13px;
        margin: 0.85rem 0 0.55rem;
        box-shadow: 0 5px 16px rgba(23, 50, 77, 0.04);
    }
    .route-card-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        margin-bottom: 0.85rem;
    }
    .route-title {
        color: var(--navy);
        font-size: 1rem;
        font-weight: 800;
    }
    .route-occupancy {
        color: var(--blue);
        background: var(--blue-soft);
        border-radius: 999px;
        padding: 0.3rem 0.65rem;
        font-size: 0.76rem;
        font-weight: 750;
        white-space: nowrap;
    }
    .route-stat-grid {
        display: grid;
        grid-template-columns: repeat(6, minmax(80px, 1fr));
        gap: 0.75rem;
    }
    .route-stat strong {
        display: block;
        color: var(--ink);
        font-size: 0.92rem;
    }
    .route-stat span {
        color: var(--muted);
        font-size: 0.72rem;
    }
    .muted {color: var(--muted); font-size: 0.92rem;}

    @media (max-width: 900px) {
        .app-hero {padding: 1.1rem;}
        .hero-icon {width: 46px; height: 46px; flex-basis: 46px;}
        .route-stat-grid {grid-template-columns: repeat(2, 1fr);}
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="app-hero">
        <div class="hero-icon" aria-hidden="true">
            <svg width="29" height="29" viewBox="0 0 24 24" fill="none"
                 xmlns="http://www.w3.org/2000/svg">
                <path d="M4 17.5V8.8C4 6.7 5.7 5 7.8 5h8.4C18.3 5 20 6.7 20 8.8v8.7"
                      stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
                <path d="M4 14.5h16M7 9h3.2M13.8 9H17"
                      stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
                <circle cx="7" cy="18" r="1.7" fill="currentColor"/>
                <circle cx="17" cy="18" r="1.7" fill="currentColor"/>
            </svg>
        </div>
        <div>
            <div class="hero-kicker">OPERASYON PLANLAMA</div>
            <h1>Servis Rota Optimizasyonu</h1>
            <p>Çalışan ve durak verilerini yükleyin; kapasite, süre ve yürüme
               kısıtlarına göre uygulanabilir servis rotalarını oluşturun.</p>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


def section_header(step: str, title: str, description: str) -> None:
    st.markdown(
        f"""
        <div class="section-heading">
            <div class="step-badge">{step}</div>
            <div>
                <h2>{title}</h2>
                <p>{description}</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


FACTORY_ADDRESS = (
    "VitrA Karo Sanayi ve Ticaret A.Ş., 4 Eylül Mah. "
    "Osman Rusçuk Cd. No:13, Bozüyük, Bilecik"
)
# Kampüs merkezi için yaklaşık koordinat. Servis kapısı biliniyorsa arayüzden değiştirilebilir.
FACTORY_LAT = 39.903830
FACTORY_LON = 30.084850


ALIASES = {
    "id": ["calisan_id", "çalışan_id", "personel_sicil", "sicil", "id"],
    "name": ["ad_soyad", "ad soyad", "çalışanın_adı_soyad", "calisan_adi", "çalışan adı"],
    "type": ["tip", "lokasyon_tipi", "nokta_tipi"],
    "address": ["adres", "acik_adres", "açık_adres", "ikamet_adresi"],
    "lat": ["enlem", "latitude", "lat"],
    "lon": ["boylam", "longitude", "lon", "lng"],
    "active": ["aktif_mi", "aktif mi", "aktif"],
    "uses_service": ["servis_kullaniyor_mu", "servis kullanıyor mu", "servis_kullanimi", "servis"],
}


def normalize(value: object) -> str:
    table = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return str(value).strip().translate(table).casefold().replace(" ", "_")


def detect_column(columns, alias_key):
    normalized = {normalize(column): column for column in columns}
    for alias in ALIASES[alias_key]:
        match = normalized.get(normalize(alias))
        if match is not None:
            return match
    return None


def is_yes(value: object, default: bool = True) -> bool:
    if pd.isna(value) or str(value).strip() == "":
        return default
    return normalize(value) in {"evet", "e", "yes", "true", "1", "aktif", "kullaniyor"}


def is_factory(value: object) -> bool:
    text = normalize(value)
    return any(word in text for word in ("ofis", "fabrika", "depo", "is_yeri"))


def get_mapping(df: pd.DataFrame):
    columns = list(df.columns)
    none_label = "(Yok)"
    options = [none_label, *columns]

    def select(label, key, required=False):
        detected = detect_column(columns, key)
        default = options.index(detected) if detected in options else 0
        value = st.selectbox(label, options, index=default, key=f"map_{key}")
        if required and value == none_label:
            st.warning(f"'{label}' alanını seçmelisiniz.")
        return None if value == none_label else value

    with st.expander("Gelişmiş: Excel sütun eşleştirmesini kontrol et", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            id_col = select("Çalışan / sicil no", "id")
            name_col = select("Ad soyad", "name")
            type_col = select("Nokta tipi", "type")
        with c2:
            address_col = select("Adres", "address")
            lat_col = select("Enlem", "lat")
            lon_col = select("Boylam", "lon")
        with c3:
            active_col = select("Aktif mi?", "active")
            service_col = select("Servis kullanıyor mu?", "uses_service")
    return {
        "id": id_col,
        "name": name_col,
        "type": type_col,
        "address": address_col,
        "lat": lat_col,
        "lon": lon_col,
        "active": active_col,
        "uses_service": service_col,
    }


def standardize(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    df = df.dropna(how="all").copy()
    out = pd.DataFrame(index=df.index)
    id_values = (
        df[mapping["id"]]
        if mapping["id"]
        else pd.Series(range(len(df)), index=df.index)
    )
    out["Calisan_ID"] = id_values.fillna("").astype(str).str.strip()
    out["Ad_Soyad"] = df[mapping["name"]].fillna("").astype(str) if mapping["name"] else ""
    out["Tip"] = df[mapping["type"]].fillna("Çalışan").astype(str) if mapping["type"] else "Çalışan"
    out["Adres"] = df[mapping["address"]].fillna("").astype(str) if mapping["address"] else ""
    out["Enlem"] = pd.to_numeric(df[mapping["lat"]], errors="coerce") if mapping["lat"] else math.nan
    out["Boylam"] = pd.to_numeric(df[mapping["lon"]], errors="coerce") if mapping["lon"] else math.nan
    out["Aktif_mi"] = df[mapping["active"]].apply(is_yes) if mapping["active"] else True
    out["Servis_Kullaniyor_mu"] = (
        df[mapping["uses_service"]].apply(is_yes) if mapping["uses_service"] else True
    )
    return out


def read_approved_candidates(uploaded_file) -> tuple[list[tuple], dict]:
    """Durak Excel'ini okur; rota/grup bilgisini korur ve reddedilenleri dışlar."""
    if uploaded_file is None:
        return [], {"loaded": 0, "approved": 0, "pending": 0, "excluded": 0}
    frame = pd.read_excel(BytesIO(uploaded_file.getvalue())).dropna(how="all")
    lat_col = detect_column(frame.columns, "lat")
    lon_col = detect_column(frame.columns, "lon")
    if lat_col is None or lon_col is None:
        raise ValueError("Durak dosyasında `Enlem` ve `Boylam` sütunları bulunmalıdır.")
    normalized = {normalize(column): column for column in frame.columns}
    name_col = next(
        (
            normalized[key]
            for key in ("durak_adi", "durak_adı", "durak", "stop_name", "name")
            if key in normalized
        ),
        None,
    )
    route_col = next(
        (
            normalized[key]
            for key in ("mevcut_rota", "rota", "route", "hat", "servis_hatti")
            if key in normalized
        ),
        None,
    )
    status_col = normalized.get("saha_onayi") or normalized.get("onay_durumu")
    candidates: list[tuple] = []
    stats = {"loaded": 0, "approved": 0, "pending": 0, "excluded": 0}
    for row_no, (_, row) in enumerate(frame.iterrows(), start=1):
        lat = pd.to_numeric(row[lat_col], errors="coerce")
        lon = pd.to_numeric(row[lon_col], errors="coerce")
        if pd.isna(lat) or pd.isna(lon):
            continue
        status = normalize(row[status_col]) if status_col and not pd.isna(row[status_col]) else ""
        if status in {"uygun_degil", "reddedildi", "red", "hayir", "kullanilmayacak"}:
            stats["excluded"] += 1
            continue
        label = str(row[name_col]).strip() if name_col and not pd.isna(row[name_col]) else f"Yüklenen durak {row_no}"
        route_group = str(row[route_col]).strip() if route_col and not pd.isna(row[route_col]) else ""
        # Dördüncü alan mevcut rota bilgisidir. core.py bu bilgiyi yalnızca
        # güzergâh segmentlerini doğru kurmak için kullanır; durak adı değişmez.
        candidates.append((float(lat), float(lon), label, route_group))
        stats["loaded"] += 1
        if status in {"onaylandi", "onayli", "evet", "uygun"}:
            stats["approved"] += 1
        else:
            stats["pending"] += 1
    if not candidates and not stats["excluded"]:
        raise ValueError("Durak dosyasında geçerli koordinat bulunamadı.")
    return candidates, stats

def normalize_employee_id(value: object) -> str:
    """Excel'in 123, 123.0 ve metin biçimlerini aynı sicil olarak eşleştirir."""
    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    if re.fullmatch(r"-?\d+\.0+", text):
        return text.split(".", 1)[0]
    return text.casefold()


def route_number(value: object) -> int:
    match = re.search(r"\d+", str(value))
    if not match:
        raise ValueError(f"Önceki sonuç dosyasında rota numarası okunamadı: {value}")
    return int(match.group())


def read_previous_routes(uploaded_file, employees: pd.DataFrame) -> list[list[CommonStop]]:
    """Uygulamanın indirdiği sonuç Excel'ini artımlı plan için geri okur."""
    if uploaded_file is None:
        raise ValueError(
            "Mevcut planı koruma modu için daha önce indirdiğiniz "
            "`servis_rota_sonuclari.xlsx` dosyasını yükleyin."
        )
    raw = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else bytes(uploaded_file)
    try:
        stops = pd.read_excel(BytesIO(raw), sheet_name="Ortak_Duraklar").dropna(how="all")
        assignments = pd.read_excel(
            BytesIO(raw),
            sheet_name="Personel_Durak_Eslesmesi",
        ).dropna(how="all")
    except Exception as exc:
        raise ValueError(
            "Önceki plan dosyası, uygulamadan indirilen rota sonuç Excel'i olmalıdır."
        ) from exc

    required_stops = {"Rota", "Durak_Sirasi", "Durak_Adi", "Enlem", "Boylam"}
    required_assignments = {"Rota", "Durak_Sirasi", "Calisan_ID"}
    if not required_stops.issubset(stops.columns) or not required_assignments.issubset(assignments.columns):
        raise ValueError("Önceki rota sonuç dosyasının gerekli sayfa veya sütunları eksik.")

    current_keys = [normalize_employee_id(value) for value in employees["Calisan_ID"]]
    if any(not key for key in current_keys):
        raise ValueError("Mevcut rotayı korumak için her çalışanın sicil/ID bilgisi dolu olmalıdır.")
    if len(set(current_keys)) != len(current_keys):
        raise ValueError("Mevcut rotayı korumak için çalışan sicil/ID değerleri benzersiz olmalıdır.")
    employee_by_key = {key: index for index, key in enumerate(current_keys)}

    members_by_stop: dict[tuple[int, int], list[int]] = {}
    for _, row in assignments.iterrows():
        key = normalize_employee_id(row["Calisan_ID"])
        if key not in employee_by_key:
            continue
        stop_key = (route_number(row["Rota"]), int(row["Durak_Sirasi"]))
        members_by_stop.setdefault(stop_key, []).append(employee_by_key[key])

    route_map: dict[int, list[CommonStop]] = {}
    ordered_stops = stops.sort_values(["Rota", "Durak_Sirasi"], kind="stable")
    for _, row in ordered_stops.iterrows():
        lat = pd.to_numeric(row["Enlem"], errors="coerce")
        lon = pd.to_numeric(row["Boylam"], errors="coerce")
        if pd.isna(lat) or pd.isna(lon):
            continue
        number = route_number(row["Rota"])
        order = int(row["Durak_Sirasi"])
        members = list(dict.fromkeys(members_by_stop.get((number, order), [])))
        route_map.setdefault(number, []).append(
            CommonStop(
                anchor_index=order - 1,
                member_indices=members,
                walking_distances_m=[0.0] * len(members),
                latitude=float(lat),
                longitude=float(lon),
                label=str(row["Durak_Adi"]).strip(),
                source=(
                    str(row["Durak_Kaynagi"]).strip()
                    if "Durak_Kaynagi" in row and not pd.isna(row["Durak_Kaynagi"])
                    else "Önceki plan"
                ),
            )
        )
    if not route_map:
        raise ValueError("Önceki plan dosyasında geçerli rota durağı bulunamadı.")
    return [route_map[number] for number in sorted(route_map)]


def _format_clock(seconds_from_midnight: float) -> str:
    """Saniye cinsinden günü HH:MM biçimine yuvarlar."""
    rounded_minutes = int((float(seconds_from_midnight) + 30) // 60) % (24 * 60)
    return f"{rounded_minutes // 60:02d}:{rounded_minutes % 60:02d}"


def _route_stop_schedule_seconds(
    ordered_matrix_indices: list[int],
    duration_matrix: list[list[float]],
    direction: str,
    wait_seconds_per_stop: int,
) -> tuple[list[float], float]:
    """Durak saatlerini 07:55 fabrika varışı / 17:40 fabrika çıkışına göre hesaplar."""
    if not ordered_matrix_indices:
        anchor = (
            MORNING_FACTORY_ARRIVAL_SECONDS
            if direction == "morning"
            else EVENING_FACTORY_DEPARTURE_SECONDS
        )
        return [], float(anchor)

    if direction == "morning":
        cursor = float(MORNING_FACTORY_ARRIVAL_SECONDS)
        arrivals = [0.0] * len(ordered_matrix_indices)
        for position in range(len(ordered_matrix_indices) - 1, -1, -1):
            current_index = ordered_matrix_indices[position]
            next_index = 0 if position == len(ordered_matrix_indices) - 1 else ordered_matrix_indices[position + 1]
            cursor -= float(duration_matrix[current_index][next_index])
            cursor -= float(wait_seconds_per_stop)
            arrivals[position] = cursor
        return arrivals, float(MORNING_FACTORY_ARRIVAL_SECONDS)

    cursor = float(EVENING_FACTORY_DEPARTURE_SECONDS)
    arrivals: list[float] = []
    previous_index = 0
    for current_index in ordered_matrix_indices:
        cursor += float(duration_matrix[previous_index][current_index])
        arrivals.append(cursor)
        cursor += float(wait_seconds_per_stop)
        previous_index = current_index
    return arrivals, float(EVENING_FACTORY_DEPARTURE_SECONDS)


def materialize_shared_routes(
    allocated_routes: list[list[CommonStop]],
    duration_matrix: list[list[float]],
    distance_matrix: list[list[float]],
    direction: str,
    wait_seconds_per_stop: int,
) -> list[dict]:
    """Ortak durak nesnelerini arayüz ve Excel çıktısının kullandığı yapıya çevirir."""
    shared_routes = []
    for vehicle_no, allocated_stops in enumerate(allocated_routes, start=1):
        stops = []
        for stop in allocated_stops:
            members = list(stop.member_indices)
            walk_by_employee = {
                employee_index: distance
                for employee_index, distance in zip(stop.member_indices, stop.walking_distances_m)
            }
            stops.append(
                {
                    "anchor_matrix_index": stop.matrix_index,
                    "latitude": float(stop.latitude),
                    "longitude": float(stop.longitude),
                    "label": stop.label,
                    "source": stop.source,
                    "member_indices": members,
                    "walk_by_employee": walk_by_employee,
                    "passenger_count": len(members),
                    "max_walk_m": stop.max_walk_m,
                    "average_walk_m": stop.average_walk_m,
                }
            )

        ordered_matrix_indices = [stop["anchor_matrix_index"] for stop in stops]
        schedule_seconds, factory_anchor_seconds = _route_stop_schedule_seconds(
            ordered_matrix_indices,
            duration_matrix,
            direction,
            wait_seconds_per_stop,
        )
        for stop, scheduled_seconds in zip(stops, schedule_seconds):
            stop["scheduled_seconds"] = scheduled_seconds
            stop["scheduled_time"] = _format_clock(scheduled_seconds)
        path_indices = [*ordered_matrix_indices, 0] if direction == "morning" else [0, *ordered_matrix_indices]
        drive_seconds = sum(duration_matrix[a][b] for a, b in zip(path_indices, path_indices[1:]))
        distance_meters = sum(distance_matrix[a][b] for a, b in zip(path_indices, path_indices[1:]))
        all_walks = [
            distance
            for stop in stops
            for distance in stop["walk_by_employee"].values()
        ]
        shared_routes.append(
            {
                "vehicle_no": vehicle_no,
                "direction": direction,
                "factory_time": _format_clock(factory_anchor_seconds),
                "occupancy": sum(stop["passenger_count"] for stop in stops),
                "stops": stops,
                "path_indices": path_indices,
                "distance_km": distance_meters / 1000,
                "drive_minutes": drive_seconds / 60,
                "wait_minutes": len(stops) * wait_seconds_per_stop / 60,
                "total_minutes": drive_seconds / 60 + len(stops) * wait_seconds_per_stop / 60,
                "average_walk_m": sum(all_walks) / len(all_walks) if all_walks else 0,
                "max_walk_m": max(all_walks, default=0),
            }
        )
    return shared_routes


def _allocated_route_total_minutes(
    route: list[CommonStop],
    duration_matrix: list[list[float]],
    direction: str,
    wait_seconds_per_stop: int,
) -> float:
    """Aynı rota grubunun verilen yöndeki sürüş + durak bekleme süresini hesaplar."""
    if not route:
        return 0.0
    matrix_indices = [
        stop.matrix_index if stop.matrix_index is not None else stop.anchor_index + 1
        for stop in route
    ]
    path = [*matrix_indices, 0] if direction == "morning" else [0, *matrix_indices]
    drive_seconds = sum(
        float(duration_matrix[a][b])
        for a, b in zip(path, path[1:])
    )
    return drive_seconds / 60.0 + len(route) * wait_seconds_per_stop / 60.0


def _same_route_directional_times(
    morning_routes: list[list[CommonStop]],
    duration_matrix: list[list[float]],
    wait_seconds_per_stop: int,
) -> tuple[list[float], list[float]]:
    """Aynı çalışan/araç grubu için sabah ve ters akşam sürelerini birlikte hesaplar."""
    morning_times = []
    evening_times = []
    for route in morning_routes:
        if not route:
            continue
        morning_times.append(
            _allocated_route_total_minutes(
                route, duration_matrix, "morning", wait_seconds_per_stop
            )
        )
        evening_times.append(
            _allocated_route_total_minutes(
                list(reversed(route)), duration_matrix, "evening", wait_seconds_per_stop
            )
        )
    return morning_times, evening_times


def _split_routes_to_target_count(
    routes: list[list[CommonStop]],
    target_count: int,
    duration_matrix: list[list[float]],
    wait_seconds_per_stop: int,
    max_route_minutes: int,
) -> list[list[CommonStop]]:
    """Sabit servis sayısını rota sırasını bozmadan tamamlar.

    Yolcu sayıları eşitlenmez. Ancak sırf hedef rota sayısına ulaşmak için
    5-6 kişilik çok düşük doluluklu bir servis oluşturmak da mümkün olduğunca
    önlenir. Bu, eşitleme hedefi değil; operasyonel olarak anlamsız küçük
    rotaları cezalandıran yumuşak bir kontroldür.
    """
    result = [list(route) for route in routes if route]
    if len(result) > target_count:
        raise ValueError(
            f"{target_count} sabit servis seçildi ancak {len(result)} aktif rota oluştu."
        )

    total_passengers = sum(
        stop.passenger_count
        for route in result
        for stop in route
    )
    average_target_load = total_passengers / max(1, target_count)

    # Ortalama hedef doluluğun yaklaşık yarısının altına düşen rotalar
    # yalnızca daha iyi bir coğrafi/süre çözümü yoksa kabul edilir.
    practical_soft_min = max(1, int(math.floor(average_target_load * 0.50)))

    while len(result) < target_count:
        best = None

        for route_index, route in enumerate(result):
            if len(route) < 2:
                continue

            for cut in range(1, len(route)):
                first = route[:cut]
                second = route[cut:]

                morning_times, evening_times = _same_route_directional_times(
                    [first, second],
                    duration_matrix,
                    wait_seconds_per_stop,
                )
                directional_times = [*morning_times, *evening_times]
                worst_time = max(directional_times, default=0.0)

                if max_route_minutes and worst_time > max_route_minutes + 1e-9:
                    continue

                candidate_routes = [
                    *result[:route_index],
                    first,
                    second,
                    *result[route_index + 1 :],
                ]
                candidate_loads = [
                    sum(stop.passenger_count for stop in candidate_route)
                    for candidate_route in candidate_routes
                ]

                # Önce aşırı küçük servisleri cezalandır; bu sınır sağlandığında
                # karar yine rota süresi/coğrafi maliyete göre verilir.
                underload_penalty = sum(
                    max(0, practical_soft_min - load)
                    for load in candidate_loads
                )
                total_directional_time = sum(directional_times)

                score = (
                    underload_penalty,
                    total_directional_time,
                    worst_time,
                    route_index,
                    cut,
                )

                if best is None or score < best[0]:
                    best = (score, route_index, first, second)

        if best is None:
            raise ValueError(
                f"Mevcut rotalar {target_count} aktif servise, "
                f"{max_route_minutes} dk sınırı korunarak bölünemedi."
            )

        _, route_index, first, second = best
        result = [
            *result[:route_index],
            first,
            second,
            *result[route_index + 1 :],
        ]

    return result


def _complete_mixed_fleet_routes(
    routes: list[list[CommonStop]],
    vehicle_capacities: list[int],
    duration_matrix: list[list[float]],
    wait_seconds_per_stop: int,
    max_route_minutes: int,
) -> list[list[CommonStop]]:
    """Karma filoda boş aracı, coğrafi olarak ardışık bir rota parçasıyla devreye alır.

    Araç kapasiteleri korunur. Yolcu sayıları eşitlenmez; yalnızca aşırı küçük
    bir servis oluşmasını önlemek için araç kapasitesinin yaklaşık %35'i kadar
    yumuşak bir alt doluluk tercihi kullanılır.
    """
    result = [list(route) for route in routes]
    if len(result) != len(vehicle_capacities):
        raise ValueError("Karma filo rota sayısı ile araç kapasitesi listesi uyuşmuyor.")

    def load(route: list[CommonStop]) -> int:
        return sum(stop.passenger_count for stop in route)

    while any(not route for route in result):
        empty_index = next(i for i, route in enumerate(result) if not route)
        empty_capacity = int(vehicle_capacities[empty_index])
        best = None

        for source_index, source_route in enumerate(result):
            if source_index == empty_index or len(source_route) < 2:
                continue

            source_capacity = int(vehicle_capacities[source_index])

            for cut in range(1, len(source_route)):
                left = source_route[:cut]
                right = source_route[cut:]

                for source_piece, new_piece in ((left, right), (right, left)):
                    source_load = load(source_piece)
                    new_load = load(new_piece)

                    if source_load > source_capacity or new_load > empty_capacity:
                        continue

                    morning_times, evening_times = _same_route_directional_times(
                        [source_piece, new_piece],
                        duration_matrix,
                        wait_seconds_per_stop,
                    )
                    directional_times = [*morning_times, *evening_times]
                    worst_time = max(directional_times, default=0.0)

                    if max_route_minutes and worst_time > max_route_minutes + 1e-9:
                        continue

                    source_soft_min = max(1, int(math.floor(source_capacity * 0.50)))
                    new_soft_min = max(1, int(math.floor(empty_capacity * 0.50)))
                    underload_penalty = (
                        max(0, source_soft_min - source_load)
                        + max(0, new_soft_min - new_load)
                    )

                    score = (
                        underload_penalty,
                        sum(directional_times),
                        worst_time,
                        -min(source_load, new_load),
                        source_index,
                        cut,
                    )
                    if best is None or score < best[0]:
                        best = (
                            score,
                            source_index,
                            source_piece,
                            new_piece,
                        )

        if best is None:
            raise ValueError(
                "Karma filo için dört anlamlı rota oluşturulamadı. "
                "Küçük servis kapasitesini artırmayı veya rota süresi sınırını yükseltmeyi deneyin."
            )

        _, source_index, source_piece, new_piece = best
        result[source_index] = list(source_piece)
        result[empty_index] = list(new_piece)

    return result


def _direction_limit_violation_text(
    morning_times: list[float],
    evening_times: list[float],
    max_route_minutes: int,
) -> str:
    violations = []
    for route_no, (morning, evening) in enumerate(zip(morning_times, evening_times), start=1):
        if morning > max_route_minutes + 1e-9 or evening > max_route_minutes + 1e-9:
            violations.append(
                f"Rota {route_no}: sabah {morning:.1f} dk / akşam {evening:.1f} dk"
            )
    return "; ".join(violations)


def build_shared_routes(
    employees: pd.DataFrame,
    factory_coordinates: tuple[float, float],
    max_walk_m: int,
    target_average_walk_m: int,
    direction: str,
    capacity: int,
    mode: str,
    fixed_vehicle_count: int,
    wait_seconds_per_stop: int,
    max_route_minutes: int,
    use_road_network: bool,
    approved_candidates: list[tuple],
    allow_automatic_candidates: bool,
    vehicle_capacities: list[int] | None = None,
):
    """Ortak durakları ve servis rotalarını oluşturur."""
    employee_coordinates = list(
        zip(employees["Enlem"].astype(float), employees["Boylam"].astype(float))
    )

    mixed_fleet = bool(vehicle_capacities)
    if mixed_fleet:
        vehicle_capacities = [int(value) for value in vehicle_capacities]
        if len(vehicle_capacities) != 4:
            raise ValueError("Karma filo planında 2 büyük + 2 küçük olmak üzere 4 araç bulunmalıdır.")
        capacity = max(vehicle_capacities)
        fixed_vehicle_count = 4
        mode = "fixed"
        if sum(vehicle_capacities) < len(employees):
            raise ValueError(
                f"Seçilen karma filonun toplam kapasitesi {sum(vehicle_capacities)} kişi; "
                f"{len(employees)} çalışan için yetersiz."
            )

    candidates = generate_candidate_stops(
        employee_coordinates,
        max_walk_m=max_walk_m,
        walking_factor=1.20,
        approved_candidates=approved_candidates,
        allow_automatic_candidates=allow_automatic_candidates,
        factory_coordinates=factory_coordinates,
    )
    all_stops, minimum_stop_count, minimum_proven = optimize_candidate_stops(
        employee_coordinates,
        candidates,
        max_walk_m=max_walk_m,
        target_average_walk_m=target_average_walk_m,
        walking_factor=1.20,
    )

    # Karma filoda bir fiziksel durak küçük aracın kapasitesinden kalabalıksa,
    # aynı konum korunarak mantıksal yolcu gruplarına ayrılır. Böylece o nokta
    # gerektiğinde iki farklı servis tarafından paylaşılabilir.
    stop_chunk_capacity = min(vehicle_capacities) if mixed_fleet else capacity
    capacity_limited_stops: list[CommonStop] = []
    for stop in all_stops:
        pairs = list(zip(stop.member_indices, stop.walking_distances_m))
        if len(pairs) <= stop_chunk_capacity:
            capacity_limited_stops.append(stop)
            continue
        for chunk_start in range(0, len(pairs), stop_chunk_capacity):
            chunk = pairs[chunk_start : chunk_start + stop_chunk_capacity]
            capacity_limited_stops.append(
                CommonStop(
                    anchor_index=stop.anchor_index,
                    member_indices=[pair[0] for pair in chunk],
                    walking_distances_m=[pair[1] for pair in chunk],
                    latitude=stop.latitude,
                    longitude=stop.longitude,
                    label=stop.label,
                    source=stop.source,
                )
            )
    all_stops = capacity_limited_stops

    route_coordinates = [
        factory_coordinates,
        *((float(stop.latitude), float(stop.longitude)) for stop in all_stops),
    ]
    for matrix_index, stop in enumerate(all_stops, start=1):
        stop.matrix_index = matrix_index

    duration_matrix, distance_matrix, matrix_source, warnings = get_travel_matrices(
        route_coordinates,
        use_road_network=use_road_network,
    )

    minimum_vehicle_count = math.ceil(len(employees) / capacity)
    if mixed_fleet:
        vehicle_count = 4
        maximum_vehicle_count = 4
    else:
        vehicle_count = int(fixed_vehicle_count) if mode == "fixed" else minimum_vehicle_count
        if vehicle_count < minimum_vehicle_count:
            raise ValueError(
                f"{vehicle_count} araç yetersiz. Bu kapasiteyle en az {minimum_vehicle_count} araç gerekir."
            )
        maximum_vehicle_count = max(
            vehicle_count,
            min(len(all_stops), minimum_vehicle_count + 8),
        )

    planning_direction = "morning"
    last_error: Exception | None = None
    last_direction_violation = ""

    while vehicle_count <= maximum_vehicle_count:
        try:
            candidate_routes = assign_common_stops_to_routes(
                all_stops,
                route_coordinates,
                vehicle_count,
                capacity,
                duration_matrix,
                planning_direction,
                wait_seconds_per_stop=wait_seconds_per_stop,
                max_route_minutes=max_route_minutes,
                time_limit_seconds=30 if mixed_fleet else 15,
                require_all_vehicles_active=False,
                vehicle_capacities=vehicle_capacities if mixed_fleet else None,
            )

            if mixed_fleet:
                active_candidate_routes = _complete_mixed_fleet_routes(
                    list(candidate_routes),
                    list(vehicle_capacities),
                    duration_matrix,
                    wait_seconds_per_stop,
                    max_route_minutes,
                )
            else:
                active_candidate_routes = [route for route in candidate_routes if route]
                if not active_candidate_routes and len(employees):
                    raise ValueError("Optimizasyon aktif bir servis rotası üretemedi.")
                if mode == "fixed":
                    active_candidate_routes = _split_routes_to_target_count(
                        active_candidate_routes,
                        int(fixed_vehicle_count),
                        duration_matrix,
                        wait_seconds_per_stop,
                        max_route_minutes,
                    )

            morning_times, evening_times = _same_route_directional_times(
                active_candidate_routes,
                duration_matrix,
                wait_seconds_per_stop,
            )
            last_direction_violation = _direction_limit_violation_text(
                morning_times, evening_times, max_route_minutes
            )

            if max_route_minutes and last_direction_violation:
                if mode == "fixed":
                    fleet_text = "2 büyük + 2 küçük servis" if mixed_fleet else f"Sabit {fixed_vehicle_count} servis"
                    raise ValueError(
                        f"{fleet_text} ile {max_route_minutes} dk sınırı her iki yönde sağlanamıyor. "
                        f"{last_direction_violation}."
                    )
                vehicle_count += 1
                continue

            allocated_routes = active_candidate_routes
            vehicle_count = len(allocated_routes)
            break

        except ValueError as exc:
            last_error = exc
            if mode == "fixed":
                raise
            vehicle_count += 1
    else:
        detail = f" Son kontrol: {last_direction_violation}." if last_direction_violation else ""
        last_error_text = f" Teknik neden: {last_error}" if last_error else ""
        raise ValueError(
            f"Kapasite ve {max_route_minutes} dk sabah/akşam rota süresi sınırlarını "
            f"birlikte sağlayan çözüm bulunamadı.{detail}{last_error_text} "
            "Araç kapasitesini, rota süresini veya durak yapısını kontrol edin."
        ) from last_error

    morning_times, evening_times = _same_route_directional_times(
        allocated_routes, duration_matrix, wait_seconds_per_stop
    )

    output_routes = (
        reverse_routes_for_return(allocated_routes)
        if direction == "evening"
        else allocated_routes
    )
    shared_routes = materialize_shared_routes(
        output_routes,
        duration_matrix,
        distance_matrix,
        direction,
        wait_seconds_per_stop,
    )

    if mixed_fleet:
        route_capacities = list(vehicle_capacities)
        route_types = ["Büyük servis", "Büyük servis", "Küçük servis", "Küçük servis"]
    else:
        route_capacities = [int(capacity)] * len(shared_routes)
        route_types = ["Standart servis"] * len(shared_routes)

    for route, route_capacity, route_type in zip(shared_routes, route_capacities, route_types):
        route["vehicle_capacity"] = int(route_capacity)
        route["vehicle_type"] = route_type

    if any(
        stop.source in {"Otomatik ortak nokta", "Güzergâh üzeri aday durak", "Güzergâha yakın yeni durak"}
        for stop in all_stops
    ):
        warnings.append(
            "Otomatik/adres tabanlı duraklar matematiksel adaydır; kaldırım, yaya geçidi ve "
            "güvenli bekleme alanı sahada onaylanmalıdır."
        )
    warnings.append(
        "Geliş ve dönüş aynı servis rotasına sabitlenmiştir. Akşam seferinde çalışanlar "
        "başka bir rotaya aktarılmaz; durak sırası sabah rotasının tersidir."
    )

    meta = {
        "vehicle_count": len(allocated_routes),
        "candidate_count": len(candidates),
        "minimum_stop_count": minimum_stop_count,
        "minimum_proven": minimum_proven,
        "selected_stop_count": len(all_stops),
        "route_loads": [sum(stop.passenger_count for stop in route) for route in allocated_routes],
        "route_capacities": route_capacities,
        "route_types": route_types,
        "mixed_fleet": mixed_fleet,
        "load_spread": (
            max((sum(stop.passenger_count for stop in route) for route in allocated_routes), default=0)
            - min((sum(stop.passenger_count for stop in route) for route in allocated_routes), default=0)
        ),
        "matrix_source": matrix_source,
        "warnings": warnings,
        "planning_mode": "full",
        "same_route_morning_evening": True,
        "hard_route_limit_minutes": max_route_minutes,
        "max_morning_minutes": max(morning_times, default=0.0),
        "max_evening_minutes": max(evening_times, default=0.0),
    }
    return shared_routes, meta


def build_incremental_shared_routes(
    employees: pd.DataFrame,
    baseline_routes: list[list[CommonStop]],
    factory_coordinates: tuple[float, float],
    max_walk_m: int,
    target_average_walk_m: int,
    direction: str,
    capacity: int,
    mode: str,
    fixed_vehicle_count: int,
    wait_seconds_per_stop: int,
    max_route_minutes: int,
    use_road_network: bool,
    approved_candidates: list[tuple],
    allow_automatic_candidates: bool,
    vehicle_capacities: list[int] | None = None,
):
    """Mevcut planı günceller; rota grubu geliş/dönüşte aynı kalır."""
    if vehicle_capacities:
        raise ValueError(
            "2 Büyük + 2 Küçük servis planı için 'Tam optimizasyon' seçilmelidir."
        )
    employee_coordinates = list(
        zip(employees["Enlem"].astype(float), employees["Boylam"].astype(float))
    )
    planning_direction = "morning"
    allocated_routes, duration_matrix, distance_matrix, meta = update_routes_incrementally(
        employee_coordinates=employee_coordinates,
        baseline_routes=baseline_routes,
        factory_coordinates=factory_coordinates,
        approved_candidates=approved_candidates,
        max_walk_m=max_walk_m,
        target_average_walk_m=target_average_walk_m,
        walking_factor=1.20,
        capacity=capacity,
        direction=planning_direction,
        wait_seconds_per_stop=wait_seconds_per_stop,
        max_route_minutes=max_route_minutes,
        mode=mode,
        fixed_vehicle_count=fixed_vehicle_count,
        use_road_network=use_road_network,
        allow_automatic_candidates=allow_automatic_candidates,
    )

    allocated_routes = [route for route in allocated_routes if route]
    morning_times, evening_times = _same_route_directional_times(
        allocated_routes, duration_matrix, wait_seconds_per_stop
    )
    violation = _direction_limit_violation_text(
        morning_times, evening_times, max_route_minutes
    )
    if max_route_minutes and violation:
        raise ValueError(
            f"Mevcut plan korunurken {max_route_minutes} dk sınırı iki yönde birden sağlanamadı: "
            f"{violation}. Bu veri için 'Tam optimizasyon' çalıştırın."
        )

    output_routes = (
        reverse_routes_for_return(allocated_routes)
        if direction == "evening"
        else allocated_routes
    )
    shared_routes = materialize_shared_routes(
        output_routes,
        duration_matrix,
        distance_matrix,
        direction,
        wait_seconds_per_stop,
    )

    meta["vehicle_count"] = len(allocated_routes)
    meta["same_route_morning_evening"] = True
    meta["hard_route_limit_minutes"] = max_route_minutes
    meta["max_morning_minutes"] = max(morning_times, default=0.0)
    meta["max_evening_minutes"] = max(evening_times, default=0.0)
    meta.setdefault("warnings", []).append(
        "Geliş ve dönüş aynı servis rotasına sabitlenmiştir. Akşam seferinde çalışanlar "
        "başka bir rotaya aktarılmaz; durak sırası sabah rotasının tersidir."
    )
    return shared_routes, meta


def result_workbook(shared_routes, employees: pd.DataFrame, capacity: int) -> bytes:
    summary_rows = []
    stop_rows = []
    assignment_rows = []
    for route in shared_routes:
        summary_rows.append(
            {
                "Rota": f"Rota {route['vehicle_no']}",
                "Hedef_Fabrika_Saati": route.get("factory_time", ""),
                "Araç_Tipi": route.get("vehicle_type", "Standart servis"),
                "Yolcu": route["occupancy"],
                "Kapasite": route.get("vehicle_capacity", capacity),
                "Doluluk_Orani": (
                    route["occupancy"] / route.get("vehicle_capacity", capacity)
                    if route.get("vehicle_capacity", capacity)
                    else 0
                ),
                "Toplam_Durak_Sayisi": len(route["stops"]),
                "Coklu_Ortak_Durak": sum(stop["passenger_count"] > 1 for stop in route["stops"]),
                "Tekil_Durak": sum(stop["passenger_count"] == 1 for stop in route["stops"]),
                "Mesafe_km": round(route["distance_km"], 1),
                "Surus_Suresi_dk": round(route["drive_minutes"]),
                "Bekleme_Suresi_dk": round(route["wait_minutes"]),
                "Toplam_Sure_dk": round(route["total_minutes"]),
                "Ort_Yurume_m": round(route["average_walk_m"]),
                "En_Uzak_Yurume_m": round(route["max_walk_m"]),
            }
        )
        for order, stop in enumerate(route["stops"], start=1):
            stop_rows.append(
                {
                    "Rota": f"Rota {route['vehicle_no']}",
                    "Durak_Sirasi": order,
                    "Tahmini_Saat": stop.get("scheduled_time", ""),
                    "Durak_Adi": stop["label"],
                    "Durak_Kaynagi": stop["source"],
                    "Durak_Turu": "Ortak" if stop["passenger_count"] > 1 else "Tekil",
                    "Yolcu_Sayisi": stop["passenger_count"],
                    "Enlem": stop["latitude"],
                    "Boylam": stop["longitude"],
                    "En_Uzak_Yurume_m": round(stop["max_walk_m"]),
                }
            )
            for employee_index in stop["member_indices"]:
                row = employees.iloc[employee_index]
                assignment_rows.append(
                    {
                        "Rota": f"Rota {route['vehicle_no']}",
                        "Durak_Sirasi": order,
                        "Tahmini_Saat": stop.get("scheduled_time", ""),
                        "Calisan_ID": row["Calisan_ID"],
                        "Ad_Soyad": row["Ad_Soyad"],
                        "Ev_Adresi": row["Adres"],
                        "Durak_Adi": stop["label"],
                        "Durak_Kaynagi": stop["source"],
                        "Yurume_Mesafesi_m": round(stop["walk_by_employee"][employee_index]),
                        "Durak_Enlem": stop["latitude"],
                        "Durak_Boylam": stop["longitude"],
                    }
                )
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Rota_Ozeti", index=False)
        pd.DataFrame(stop_rows).to_excel(writer, sheet_name="Ortak_Duraklar", index=False)
        pd.DataFrame(assignment_rows).to_excel(writer, sheet_name="Personel_Durak_Eslesmesi", index=False)
        workbook = writer.book
        header = workbook.add_format({"bold": True, "font_color": "white", "bg_color": "#285A84"})
        percent = workbook.add_format({"num_format": "0%"})
        frames = (
            ("Rota_Ozeti", pd.DataFrame(summary_rows)),
            ("Ortak_Duraklar", pd.DataFrame(stop_rows)),
            ("Personel_Durak_Eslesmesi", pd.DataFrame(assignment_rows)),
        )
        for sheet_name, frame in frames:
            sheet = writer.sheets[sheet_name]
            for col, name in enumerate(frame.columns):
                sheet.write(0, col, name, header)
                width = min(max(len(name) + 2, *(len(str(v)) + 2 for v in frame[name].head(200))), 42)
                sheet.set_column(col, col, width)
            sheet.freeze_panes(1, 0)
            sheet.autofilter(0, 0, max(len(frame), 1), max(len(frame.columns) - 1, 0))
        writer.sheets["Rota_Ozeti"].set_column("F:F", 15, percent)
        writer.sheets["Rota_Ozeti"].set_column("E:N", 18)
    return output.getvalue()


with st.sidebar:
    st.markdown(
        """
        <div class="sidebar-brand">
            <strong>Planlama Ayarları</strong>
            <span>Optimizasyon kısıtlarını belirleyin</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Planlama modeli", expanded=True):
        planning_label = st.selectbox(
            "Planlama yaklaşımı",
            ["Tam optimizasyon", "Mevcut planı koruyarak güncelle"],
            help=(
                "Tam optimizasyon bütün planı yeniden kurar. Mevcut planı koruma modu "
                "önce yeni çalışanı mevcut durağa ekler ve yalnızca gerekirse yeni durak/rota açar."
            ),
        )
        mode_label = st.selectbox(
            "Rota sayısı",
            [
                "Otomatik (kapasite + süreye göre)",
                "Sabit 3 servis",
                "Sabit 4 servis",
                "2 Büyük + 2 Küçük servis",
            ],
        )
        stop_policy_label = st.selectbox(
            "Durak politikası",
            [
                "Yüklenen durakları kullan; gerekirse yeni aday öner",
                "Yalnızca yüklenen durakları kullan",
            ],
            help=(
                "Yeni aday seçeneği çalışan evini durak yapmaz. Önce yüklenen durakları, sonra mevcut "
                "güzergâh koridorunu kullanır; gerekirse güzergâha yakın yeni yol noktası önerir. "
                "Yeni noktalar saha onayı olmadan kesin durak değildir."
            ),
        )

    with st.expander("Kapasite ve süre kısıtları", expanded=True):
        mixed_fleet_selected = mode_label.startswith("2 Büyük")
        if mixed_fleet_selected:
            cap_col1, cap_col2 = st.columns(2)
            with cap_col1:
                big_capacity = st.number_input(
                    "Büyük servis kapasitesi",
                    min_value=1,
                    max_value=100,
                    value=45,
                    step=1,
                )
            with cap_col2:
                small_capacity = st.number_input(
                    "Küçük servis kapasitesi",
                    min_value=1,
                    max_value=100,
                    value=27,
                    step=1,
                )
            capacity = max(int(big_capacity), int(small_capacity))
            st.caption(
                f"Toplam filo kapasitesi: 2 × {int(big_capacity)} + 2 × {int(small_capacity)} = "
                f"{2 * int(big_capacity) + 2 * int(small_capacity)} kişi."
            )
        else:
            big_capacity = None
            small_capacity = None
            capacity = st.number_input(
                "Araç kapasitesi",
                min_value=1,
                max_value=100,
                value=45,
                step=1,
            )

        max_walk_m = st.slider(
            "Azami yürüme mesafesi",
            min_value=200,
            max_value=1200,
            value=1000,
            step=50,
            format="%d m",
            help="Yakın çalışanlar bu sınırı aşmayacak biçimde ortak bir durakta toplanır.",
        )
        target_average_walk_m = min(FIXED_TARGET_AVERAGE_WALK_M, int(max_walk_m))
        max_route_minutes = st.slider(
            "Azami rota süresi",
            min_value=20,
            max_value=100,
            value=70,
            step=5,
            format="%d dk",
            help=(
                "Sürüş + durak bekleme süresidir. Seçtiğiniz değer sabah ve akşam için "
                "kontrol edilir; otomatik mod gerekirse servis sayısını artırır."
            ),
        )
        wait_seconds_per_stop = FIXED_WAIT_SECONDS_PER_STOP

    with st.expander("Sefer ve yol hesabı", expanded=True):
        direction_label = st.selectbox(
            "Sefer yönü",
            [
                "Sabah (07.55 varış): çalışan → fabrika",
                "Akşam (17.40 çıkış): fabrika → çalışan",
            ],
        )
        st.info(
            "🔒 Aynı çalışanlar geliş ve dönüşte aynı servis rotasında kalır. "
            f"Sistem seçtiğiniz {max_route_minutes} dk sınırını hem sabah hem akşam için kontrol eder."
        )
        use_road_network = st.checkbox("Gerçek yol güzergâhını kullan", value=True)
        road_consent = False
        if use_road_network:
            road_consent = st.checkbox(
                "Koordinatların yol hesabı için paylaşılmasını onaylıyorum",
                help=(
                    "Yalnızca koordinatlar açık OSRM servisine gönderilir. "
                    "İsim, sicil ve açık adres paylaşılmaz."
                ),
            )

    st.markdown(
        """
        <div class="sidebar-note">
            <strong>Çalışma düzeni</strong><br>
            Sabah hedef fabrika varışı 07.55 · Akşam çıkış 17.40 · Durak bekleme süresi 15 sn ·
            Araç kapasitesi üst sınır olarak uygulanır; rota dolulukları eşitlenmez. Karma filo seçeneğinde 2 büyük ve 2 küçük araç birlikte optimize edilir; aşırı düşük doluluk ve fabrikadan gereksiz uzaklaşan zikzaklar yumuşak ceza ile azaltılır.
        </div>
        """,
        unsafe_allow_html=True,
    )

allow_automatic_candidates = not stop_policy_label.startswith("Yalnızca")
section_header(
    "01",
    "Veri yükleme",
    "Güncel çalışan listesini ve varsa onaylı durak listenizi sisteme ekleyin.",
)
upload_left, upload_right = st.columns(2, gap="large")
with upload_left:
    uploaded = st.file_uploader(
        "Çalışan listesi",
        type=["xlsx", "xls"],
        help="Dosyada çalışanların enlem ve boylam bilgilerinin bulunması gerekir.",
    )
with upload_right:
    approved_stop_file = st.file_uploader(
        "Mevcut / aday durak listesi",
        type=["xlsx", "xls"],
        help=(
            "Durak_Adi, Enlem ve Boylam sütunlarını içeren dosyadır. Saha_Onayi sütununda "
            "Uygun Değil/Reddedildi olan satırlar kullanılmaz; Bekliyor olanlar yalnızca adaydır."
        ),
    )
previous_plan_file = None
if planning_label.startswith("Mevcut"):
    previous_plan_file = st.file_uploader(
        "Önceki rota sonuç dosyası",
        type=["xlsx"],
        help=(
            "Bir önceki çalıştırmada `Rota sonuçlarını Excel olarak indir` düğmesiyle "
            "kaydettiğiniz servis_rota_sonuclari.xlsx dosyasıdır."
        ),
    )
    if previous_plan_file is None and st.session_state.get("last_plan_bytes") is None:
        st.info(
            "İlk kez kullanıyorsanız önce Tam optimizasyonu çalıştırıp sonucu indirin. "
            "Sonraki çalışan güncellemelerinde bu modu seçin."
        )

if uploaded is None:
    st.info("Başlamak için enlem ve boylam bilgileri dolu olan çalışan listenizi yükleyin.")
    st.stop()

raw_bytes = uploaded.getvalue()
approved_bytes = approved_stop_file.getvalue() if approved_stop_file is not None else b""
previous_bytes = previous_plan_file.getvalue() if previous_plan_file is not None else b""
file_key = hashlib.sha256(
    raw_bytes
    + approved_bytes
    + previous_bytes
    + planning_label.encode("utf-8")
    + stop_policy_label.encode("utf-8")
    + APP_VERSION.encode("utf-8")
).hexdigest()
if st.session_state.get("file_key") != file_key:
    try:
        st.session_state["raw_df"] = pd.read_excel(BytesIO(raw_bytes))
        approved_candidates, approved_stats = read_approved_candidates(approved_stop_file)
        st.session_state["approved_candidates"] = approved_candidates
        st.session_state["approved_candidate_stats"] = approved_stats
        st.session_state["file_key"] = file_key
        st.session_state.pop("result", None)
        st.session_state.pop("shared_routes", None)
    except Exception as exc:
        st.error(f"Excel dosyası okunamadı: {exc}")
        st.stop()

raw_df = st.session_state["raw_df"]
mapping = get_mapping(raw_df)
working = standardize(raw_df, mapping)

if mapping["lat"] is None or mapping["lon"] is None:
    st.error("Excel'de `Enlem` ve `Boylam` sütunları bulunamadı.")
    st.stop()

valid_people = working[working["Aktif_mi"] & working["Servis_Kullaniyor_mu"]].copy()
factory_mask = valid_people["Tip"].apply(is_factory)
factory_rows = valid_people[factory_mask]
employees = valid_people[~factory_mask].reset_index(drop=True)

section_header(
    "02",
    "Veri kontrolü",
    "Rota hesabına alınacak çalışanları, koordinat durumunu ve kapasite ihtiyacını doğrulayın.",
)
approved_count = len(st.session_state.get("approved_candidates", []))
c1, c2, c3, c4 = st.columns(4)
c1.metric("Aktif servis kullanıcısı", len(employees))
c2.metric("Eksik çalışan koordinatı", int(employees[["Enlem", "Boylam"]].isna().any(axis=1).sum()))
c3.metric("Kapasiteye göre minimum", math.ceil(len(employees) / int(capacity)) if len(employees) else 0)
c4.metric("Kullanılabilir durak", approved_count)
if approved_count:
    candidate_stats = st.session_state.get("approved_candidate_stats", {})
    st.caption(
        f"{approved_count} yüklenmiş durak bulundu: "
        f"{candidate_stats.get('approved', 0)} saha onaylı, "
        f"{candidate_stats.get('pending', approved_count)} onay bekliyor. "
        + (
            "Bunlara ek olarak gerektiğinde otomatik güzergâh adayları da değerlendirilecek."
            if allow_automatic_candidates
            else "Bu çalıştırmada bu listenin dışına yeni durak eklenmeyecek."
        )
    )

with st.expander("Çalışan verisini görüntüle", expanded=False):
    st.dataframe(working, width="stretch", hide_index=True)

st.markdown("##### Fabrika bilgisi")
if not factory_rows.empty and factory_rows[["Enlem", "Boylam"]].notna().all(axis=1).any():
    factory = factory_rows[factory_rows[["Enlem", "Boylam"]].notna().all(axis=1)].iloc[0]
    factory_lat = float(factory["Enlem"])
    factory_lon = float(factory["Boylam"])
    factory_address = factory["Adres"] or "Fabrika"
    st.success(f"Excel'den alındı: {factory_address} ({factory_lat:.5f}, {factory_lon:.5f})")
else:
    f1, f2, f3 = st.columns([2, 1, 1])
    factory_address = f1.text_input("Fabrika adresi", value=FACTORY_ADDRESS)
    factory_lat = f2.number_input("Fabrika enlem", value=FACTORY_LAT, format="%.6f")
    factory_lon = f3.number_input("Fabrika boylam", value=FACTORY_LON, format="%.6f")
    st.caption(
        "Koordinatlar VitrA Bozüyük kampüs merkezini yaklaşık gösterir. "
        "Servis araçlarının kullandığı kapının koordinatı biliniyorsa onunla değiştirin."
    )

missing_mask = employees[["Enlem", "Boylam"]].isna().any(axis=1)
if missing_mask.any():
    missing_rows = ", ".join(str(index + 2) for index in employees.index[missing_mask][:20])
    st.error(
        f"{int(missing_mask.sum())} çalışanın Enlem veya Boylam bilgisi eksik. "
        f"Excel satırlarını tamamlayıp dosyayı yeniden yükleyin: {missing_rows}"
    )
else:
    st.success(f"{len(employees)} personelin koordinatı hazır. Rota hesabına geçebilirsiniz.")

ready = not employees.empty and employees[["Enlem", "Boylam"]].notna().all(axis=1).all()
road_ready = not use_road_network or road_consent
incremental_mode = planning_label.startswith("Mevcut")
previous_plan_source = previous_plan_file or st.session_state.get("last_plan_bytes")
incremental_ready = not incremental_mode or (
    previous_plan_source is not None and mapping["id"] is not None
)
approved_ready = allow_automatic_candidates or bool(
    st.session_state.get("approved_candidates", [])
)
section_header(
    "03",
    "Rotaları oluştur",
    "Seçilen ayarlara göre ortak durakları, araç dağılımını ve güzergâh sırasını hesaplayın.",
)
if use_road_network and not road_consent:
    st.info("Gerçek yol güzergâhı için sol menüdeki koordinat paylaşım onayını işaretleyin.")
if incremental_mode and mapping["id"] is None:
    st.error("Mevcut planı korumak için çalışan Excel'inde benzersiz bir sicil/ID sütunu seçilmelidir.")
if not approved_ready:
    st.error("Yalnızca yüklenen duraklar modunda mevcut/adayı durak Excel'i yüklenmelidir.")
button_label = "Mevcut Planı Güncelle" if incremental_mode else "Rotaları Oluştur"
if st.button(
    button_label,
    type="primary",
    disabled=not ready or not road_ready or not incremental_ready or not approved_ready,
    use_container_width=True,
):
    with st.spinner("Ortak duraklar seçiliyor ve rotalar birlikte optimize ediliyor..."):
        try:
            mixed_fleet = mode_label.startswith("2 Büyük")
            mode = "fixed" if (mode_label.startswith("Sabit") or mixed_fleet) else "auto"
            if mixed_fleet:
                fixed_vehicle_count = 4
                vehicle_capacities = [
                    int(big_capacity),
                    int(big_capacity),
                    int(small_capacity),
                    int(small_capacity),
                ]
            else:
                fixed_match = re.search(r"\d+", mode_label)
                fixed_vehicle_count = int(fixed_match.group()) if fixed_match else 3
                vehicle_capacities = None

            direction = "morning" if direction_label.startswith("Sabah") else "evening"
            common_arguments = {
                "employees": employees,
                "factory_coordinates": (factory_lat, factory_lon),
                "max_walk_m": int(max_walk_m),
                "target_average_walk_m": min(int(target_average_walk_m), int(max_walk_m)),
                "direction": direction,
                "capacity": int(capacity),
                "mode": mode,
                "fixed_vehicle_count": fixed_vehicle_count,
                "wait_seconds_per_stop": int(wait_seconds_per_stop),
                "max_route_minutes": int(max_route_minutes),
                "use_road_network": bool(use_road_network),
                "approved_candidates": st.session_state.get("approved_candidates", []),
                "allow_automatic_candidates": allow_automatic_candidates,
                "vehicle_capacities": vehicle_capacities,
            }
            if incremental_mode:
                baseline_routes = read_previous_routes(previous_plan_source, employees)
                shared_routes, result_meta = build_incremental_shared_routes(
                    baseline_routes=baseline_routes,
                    **common_arguments,
                )
            else:
                shared_routes, result_meta = build_shared_routes(**common_arguments)
            st.session_state["result"] = result_meta
            st.session_state["shared_routes"] = shared_routes
            st.session_state["result_employees"] = employees.copy()
            st.session_state["result_factory"] = (factory_lat, factory_lon, factory_address)
            st.session_state["result_direction"] = direction_label
            st.session_state["result_capacity"] = int(capacity)
            st.session_state["result_vehicle_capacities"] = result_meta.get(
                "route_capacities",
                [int(capacity)] * int(result_meta.get("vehicle_count", fixed_vehicle_count)),
            )
            st.session_state["result_max_walk_m"] = int(max_walk_m)
            st.session_state["result_target_average_walk_m"] = min(
                int(target_average_walk_m), int(max_walk_m)
            )
            st.session_state["result_wait_seconds"] = int(wait_seconds_per_stop)
            st.session_state["result_max_route_minutes"] = int(max_route_minutes)
            st.session_state["result_mode"] = mode
            st.session_state["result_planning_mode"] = "incremental" if incremental_mode else "full"
            st.session_state["result_allow_automatic_candidates"] = allow_automatic_candidates
        except Exception as exc:
            st.session_state.pop("result", None)
            st.session_state.pop("shared_routes", None)
            st.error(str(exc))

result = st.session_state.get("result")
if result is None:
    if not ready:
        st.error("Optimizasyonu çalıştırmak için tüm aktif çalışanların enlem ve boylamı bulunmalı.")
    st.stop()

employees = st.session_state["result_employees"]
factory_lat, factory_lon, factory_address = st.session_state["result_factory"]
result_direction = st.session_state["result_direction"]
result_capacity = st.session_state["result_capacity"]
result_max_walk_m = st.session_state.get("result_max_walk_m", 500)
direction = "morning" if result_direction.startswith("Sabah") else "evening"
shared_routes = st.session_state.get("shared_routes")
if shared_routes is None:
    st.error("Rota sonucu bulunamadı. Optimizasyonu yeniden çalıştırın.")
    st.stop()
optimized_vehicle_count = int(result.get("vehicle_count", 0))
result_wait_seconds = st.session_state.get("result_wait_seconds", 15)
result_max_route_minutes = st.session_state.get("result_max_route_minutes", 70)
result_target_average_walk_m = st.session_state.get(
    "result_target_average_walk_m", FIXED_TARGET_AVERAGE_WALK_M
)

section_header(
    "04",
    "Optimizasyon sonucu",
    "Önerilen servis planının temel performans göstergelerini ve güzergâh detaylarını inceleyin.",
)
for warning in result.get("warnings", []):
    if "Yol ağı verisi kullanılamadı" in warning:
        st.warning("Gerçek yol verisine ulaşılamadı; rota yaklaşık mesafeyle hesaplandı.")
    elif "maksimum rota süresini aşıyor" in warning:
        st.warning("Bazı rotalar belirlenen azami rota süresini aşıyor.")

if result.get("same_route_morning_evening"):
    max_morning = float(result.get("max_morning_minutes", 0.0))
    max_evening = float(result.get("max_evening_minutes", 0.0))
    hard_limit = int(result.get("hard_route_limit_minutes", result_max_route_minutes))
    if max(max_morning, max_evening) <= hard_limit + 1e-9:
        st.success(
            f"Süre kontrolü tamam: en uzun sabah rotası {max_morning:.0f} dk, "
            f"en uzun akşam rotası {max_evening:.0f} dk — her ikisi de ≤ {hard_limit} dk."
        )

nonempty_routes = [route for route in shared_routes if route["occupancy"]]
# Ekrandaki servis sayısı meta verideki nominal araç sayısı değil, gerçekten kullanılan rota sayısıdır.
optimized_vehicle_count = len(nonempty_routes)
result["vehicle_count"] = optimized_vehicle_count
total_available_capacity = sum(
    int(route.get("vehicle_capacity", result_capacity))
    for route in nonempty_routes
)
avg_fill = (
    sum(route["occupancy"] for route in nonempty_routes) / total_available_capacity
    if nonempty_routes and total_available_capacity
    else 0
)
route_loads = [int(route["occupancy"]) for route in nonempty_routes]
load_spread = max(route_loads, default=0) - min(route_loads, default=0)
total_stop_count = sum(len(route["stops"]) for route in nonempty_routes)
automatic_stop_count = sum(
    stop.get("source") in {"Otomatik ortak nokta", "Güzergâh üzeri aday durak", "Güzergâha yakın yeni durak"}
    for route in nonempty_routes
    for stop in route["stops"]
)
multi_stop_count = sum(
    stop["passenger_count"] > 1
    for route in nonempty_routes
    for stop in route["stops"]
)
single_stop_count = total_stop_count - multi_stop_count
all_walks = [
    distance
    for route in nonempty_routes
    for stop in route["stops"]
    for distance in stop["walk_by_employee"].values()
]
average_walk = sum(all_walks) / len(all_walks) if all_walks else 0
maximum_walk = max(all_walks, default=0)
total_distance = sum(route["distance_km"] for route in nonempty_routes)
longest_route = max((route["total_minutes"] for route in nonempty_routes), default=0)
r1, r2, r3, r4 = st.columns(4)
r1.metric("Önerilen servis", optimized_vehicle_count)
r2.metric("Toplam çalışan", len(employees))
r3.metric("Toplam durak", total_stop_count)
r4.metric("Ortalama doluluk", f"%{avg_fill * 100:.0f}")
s1, s2, s3, s4 = st.columns(4)
s1.metric("Ortalama yürüme", f"{average_walk:.0f} m")
s2.metric("En uzun yürüme", f"{maximum_walk:.0f} m")
s3.metric("Toplam rota mesafesi", f"{total_distance:.1f} km")
s4.metric("En uzun rota", f"{longest_route:.0f} dk")
if len(route_loads) > 1:
    st.caption(
        "Yolcu dağılımı: "
        + " · ".join(f"Rota {i + 1}: {load} kişi" for i, load in enumerate(route_loads))
        + f" · En yüksek–en düşük fark: {load_spread} kişi. "
        + (
            "Karma filoda her rota kendi araç kapasitesine göre çözülür; eşit yolcu dağılımı hedeflenmez."
            if result.get("mixed_fleet")
            else "Araç kapasitesi üst sınırdır; rota doluluklarının birbirine eşit olması hedeflenmez."
        )
    )

with st.expander("Teknik optimizasyon ayrıntıları", expanded=False):
    t1, t2, t3, t4 = st.columns(4)
    if result.get("planning_mode") == "incremental":
        t1.metric("Eşleşmesi korunan", result.get("preserved_employee_count", 0))
        t2.metric("Mevcut durağa eklenen", result.get("added_to_existing_count", 0))
        t3.metric("Yeni durak", result.get("new_stop_count", 0))
        t4.metric("Yeni rota", result.get("added_route_count", 0))
    else:
        t1.metric("Değerlendirilen aday durak", result.get("candidate_count", 0))
        t2.metric(
            "Kanıtlanmış minimum" if result.get("minimum_proven") else "Bulunan en iyi alt plan",
            result.get("minimum_stop_count", 0),
        )
        t3.metric("Çoklu ortak durak", multi_stop_count)
        t4.metric("Tekil durak", single_stop_count)
if automatic_stop_count:
    st.caption(
        f"{automatic_stop_count} otomatik güzergâh durağı önerildi; "
        "kesinleştirilmeden önce saha uygunluğu kontrol edilmelidir."
    )
if result.get("planning_mode") == "incremental":
    st.success(
        f"Mevcut plan korundu: {result.get('preserved_employee_count', 0)} çalışan eski durağında kaldı; "
        f"{result.get('added_to_existing_count', 0)} yeni/adresi değişen çalışan mevcut durağa eklendi; "
        f"{result.get('new_stop_count', 0)} yeni durak ve {result.get('added_route_count', 0)} yeni rota açıldı."
    )
    st.caption(
        f"Artımlı güncelleme: önceki duraklar ve sıraları korunarak {result_max_walk_m} m yürüme, "
        f"{result_capacity} kişi kapasite, durak başına {result_wait_seconds} sn bekleme ve "
        f"{result_max_route_minutes} dk rota sınırı uygulandı. Yeni otomatik noktalar saha onayı gerektirir."
    )
else:
    st.caption(
        f"Hesaplama: {result_max_walk_m} m azami yürüyüş, "
        f"{result_target_average_walk_m} m sabit konfor hedefi, "
        f"durak başına {result_wait_seconds} sn bekleme ve "
        f"{result_max_route_minutes} dk azami rota süresi."
    )

palette = [
    [40, 90, 132], [213, 94, 0], [0, 140, 120], [163, 75, 148],
    [230, 159, 0], [86, 180, 233], [204, 121, 167], [0, 114, 178],
]
path_data = []
point_data = [
    {
        "lon": factory_lon,
        "lat": factory_lat,
        "label": "Fabrika",
        "stop_no": "F",
        "color": [196, 44, 44],
        "radius": 150,
    }
]

for route in nonempty_routes:
    color = palette[(route["vehicle_no"] - 1) % len(palette)]
    ordered_coordinates = []
    stop_by_matrix_index = {stop["anchor_matrix_index"]: stop for stop in route["stops"]}
    stop_number_by_index = {
        stop["anchor_matrix_index"]: order for order, stop in enumerate(route["stops"], start=1)
    }
    for matrix_index in route["path_indices"]:
        if matrix_index == 0:
            ordered_coordinates.append((factory_lat, factory_lon))
        else:
            stop = stop_by_matrix_index[matrix_index]
            ordered_coordinates.append((stop["latitude"], stop["longitude"]))
            stop_type = "Ortak" if stop["passenger_count"] > 1 else "Tekil"
            point_data.append(
                {
                    "lon": stop["longitude"], "lat": stop["latitude"],
                    "label": (
                        f"Rota {route['vehicle_no']} · {stop_type} Durak {stop_number_by_index[matrix_index]} · "
                        f"{stop['passenger_count']} yolcu · {stop['label']}"
                    ),
                    "stop_no": str(stop_number_by_index[matrix_index]),
                    "color": color,
                    "radius": 95 + stop["passenger_count"] * 10,
                }
            )
    try:
        geometry = fetch_osrm_geometry(ordered_coordinates) if result["matrix_source"].startswith("OSRM") else []
    except Exception:
        geometry = []
    if not geometry:
        geometry = [[lon, lat] for lat, lon in ordered_coordinates]
    path_data.append({"path": geometry, "color": color, "route": f"Rota {route['vehicle_no']}"})

layers = [
    pdk.Layer("PathLayer", path_data, get_path="path", get_color="color", get_width=6, width_min_pixels=3, pickable=True),
    pdk.Layer(
        "ScatterplotLayer", point_data, get_position="[lon, lat]", get_fill_color="color",
        get_radius="radius", radius_min_pixels=5, radius_max_pixels=13, pickable=True,
    ),
    pdk.Layer(
        "TextLayer",
        point_data,
        get_position="[lon, lat]",
        get_text="stop_no",
        get_color=[255, 255, 255],
        get_size=11,
        get_alignment_baseline="center",
        get_text_anchor="middle",
        pickable=False,
    ),
]
all_lats = [factory_lat, *employees["Enlem"].astype(float).tolist()]
all_lons = [factory_lon, *employees["Boylam"].astype(float).tolist()]
view_state = pdk.ViewState(
    latitude=(min(all_lats) + max(all_lats)) / 2,
    longitude=(min(all_lons) + max(all_lons)) / 2,
    zoom=9.8,
    pitch=0,
)
deck = pdk.Deck(
    layers=layers,
    initial_view_state=view_state,
    map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
    tooltip={"text": "{label}"},
)
st.markdown("#### Rota haritası")
st.caption("Rota çizgilerinin ve numaralı durakların ayrıntılarını görmek için harita üzerinde gezinin.")
st.pydeck_chart(deck, width="stretch")

st.markdown("#### Rota detayları")
st.caption("Sabah durak saatleri, fabrikaya 07:55 varış hedefinden OSRM segment süreleri ve 15 sn/durak bekleme ile geriye doğru hesaplanır.")
for route in nonempty_routes:
    route_capacity = int(route.get("vehicle_capacity", result_capacity))
    route_type = route.get("vehicle_type", "Standart servis")
    route_fill = route["occupancy"] / route_capacity if route_capacity else 0
    st.markdown(
        f"""
        <div class="route-card">
            <div class="route-card-head">
                <div class="route-title">Rota {route['vehicle_no']} · {route_type}</div>
                <div class="route-occupancy">
                    {route['occupancy']} / {route_capacity} yolcu · %{route_fill * 100:.0f} doluluk
                </div>
            </div>
            <div class="route-stat-grid">
                <div class="route-stat"><strong>{len(route['stops'])}</strong><span>Durak</span></div>
                <div class="route-stat"><strong>{route['distance_km']:.1f} km</strong><span>Mesafe</span></div>
                <div class="route-stat"><strong>{route['drive_minutes']:.0f} dk</strong><span>Sürüş</span></div>
                <div class="route-stat"><strong>{route['total_minutes']:.0f} dk</strong><span>Toplam süre</span></div>
                <div class="route-stat"><strong>{route.get('factory_time', '')}</strong><span>{'Fabrika varış' if result_direction.startswith('Sabah') else 'Fabrika çıkış'}</span></div>
                <div class="route-stat"><strong>{route['average_walk_m']:.0f} m</strong><span>Ort. yürüme</span></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    stop_rows = []
    if not result_direction.startswith("Sabah"):
        stop_rows.append(
            {
                "Durak": "Başlangıç",
                "Tahmini Saat": route.get("factory_time", "17:40"),
                "Durak Türü": "Fabrika",
                "Kaynak": "Sabit",
                "Konum": factory_address,
                "Yolcu": None,
                "En Uzak Yürüme": "-",
                "Bu Durağa Gelecekler": "-",
            }
        )
    for order, stop in enumerate(route["stops"], start=1):
        passenger_names = ", ".join(
            str(employees.iloc[index]["Ad_Soyad"] or employees.iloc[index]["Calisan_ID"])
            for index in stop["member_indices"]
        )
        stop_rows.append(
            {
                "Durak": str(order),
                "Tahmini Saat": stop.get("scheduled_time", ""),
                "Durak Türü": "Ortak" if stop["passenger_count"] > 1 else "Tekil",
                "Kaynak": stop["source"],
                "Konum": stop["label"],
                "Yolcu": stop["passenger_count"],
                "En Uzak Yürüme": f"{stop['max_walk_m']:.0f} m",
                "Bu Durağa Gelecekler": passenger_names,
            }
        )
    if result_direction.startswith("Sabah"):
        stop_rows.append(
            {
                "Durak": "Varış",
                "Tahmini Saat": route.get("factory_time", "07:55"),
                "Durak Türü": "Fabrika",
                "Kaynak": "Sabit",
                "Konum": factory_address,
                "Yolcu": None,
                "En Uzak Yürüme": "-",
                "Bu Durağa Gelecekler": "-",
            }
        )
    with st.expander(f"Rota {route['vehicle_no']} · Durak ve yolcu listesini görüntüle"):
        st.dataframe(pd.DataFrame(stop_rows), width="stretch", hide_index=True)

export_bytes = result_workbook(shared_routes, employees, result_capacity)
st.session_state["last_plan_bytes"] = export_bytes
st.markdown("#### Raporlama")
st.caption("Rota özetini, durak sıralamasını ve çalışan–durak eşleşmelerini tek Excel dosyasında indirin.")
st.download_button(
    "Rota Sonuçlarını Excel Olarak İndir",
    data=export_bytes,
    file_name="servis_rota_sonuclari.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
    use_container_width=True,
)
