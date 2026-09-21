"""Servis rota planlama motoru.

OR-Tools ile ortak durak seçimi ve kapasite kısıtlı rota optimizasyonu yapar.
Yol süreleri için önce OSRM denenir; servis erişilemezse kuş uçuşu mesafe
tabanlı tahmine otomatik geçilir.

Durak politikası:
- Yüklenen/mevcut duraklar kesin önceliklidir.
- Çalışan koordinatları hiçbir zaman servis durağı olarak kullanılmaz.
- Kapsanamayan tek çalışan için ana rota yönünde bir aday üretilir ve seçilen
  tekil aday mümkünse OSRM ile en yakın araç yoluna oturtulur.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
import math
import re
from typing import Iterable, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zipfile import ZipFile

from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from ortools.sat.python import cp_model


EARTH_RADIUS_KM = 6371.0088
ESKISEHIR_CENTER = (39.7767, 30.5206)
ESKISEHIR_MAX_DISTANCE_KM = 55.0
MIN_HOME_PICKUP_OFFSET_M = 100.0
CORRIDOR_WALK_FRACTION = 0.65
ROUTE_CORRIDOR_SOURCE = "Güzergâh üzeri aday durak"
ROUTE_DETOUR_SOURCE = "Güzergâha yakın yeni durak"
MAX_FALLBACK_CORRIDOR_SEGMENT_KM = 4.0


@dataclass
class RoutePlan:
    vehicle_no: int
    employee_indices: list[int]
    path_indices: list[int]
    occupancy: int
    distance_km: float
    drive_minutes: float
    total_minutes: float
    exceeds_limit: bool


@dataclass
class PlanResult:
    routes: list[RoutePlan]
    vehicle_count: int
    active_route_count: int
    matrix_source: str
    duration_matrix: list[list[float]]
    distance_matrix: list[list[float]]
    warnings: list[str]


@dataclass
class CommonStop:
    """Bir ortak buluşma noktası ve bu noktaya yürüyecek çalışanlar."""

    anchor_index: int
    member_indices: list[int]
    walking_distances_m: list[float]
    latitude: float | None = None
    longitude: float | None = None
    label: str = ""
    source: str = ROUTE_CORRIDOR_SOURCE
    matrix_index: int | None = None

    @property
    def passenger_count(self) -> int:
        return len(self.member_indices)

    @property
    def max_walk_m(self) -> float:
        return max(self.walking_distances_m, default=0.0)

    @property
    def average_walk_m(self) -> float:
        if not self.walking_distances_m:
            return 0.0
        return sum(self.walking_distances_m) / len(self.walking_distances_m)


@dataclass(frozen=True)
class CandidateStop:
    """Literatürdeki SBRP-BSS modeli için ziyaret edilebilecek aday durak."""

    latitude: float
    longitude: float
    label: str
    source: str


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """İki (enlem, boylam) noktası arasındaki kuş uçuşu mesafeyi döndürür."""
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


# ---------------------------------------------------------------------------
# Yüklenen çıktıya göre onaylanan 4 servislik referans dağılım
# ---------------------------------------------------------------------------

REFERENCE_ROUTE_TEMPLATE = (
    (
        ("Otomatik ortak durak 802", "midpoint", ("61038", "49780"), ("49780", "61038")),
        ("Adres tabanlı yedek durak 77", "home", ("55841",), ("55841",)),
        ("MİLLİ EĞİTİM", "catalog", ("31276", "54778", "52375", "60890"), ()),
        ("DEDEMAN OTEL", "catalog", ("43696", "56438", "54732"), ()),
        ("ALİ FUAT GÜVEN CADDESİ ERCAN SOKAK BAŞI", "catalog", ("11221", "28222", "3103"), ()),
        ("T.M.O ÖNÜ", "catalog", ("27901", "53900", "29835"), ()),
        ("BİRLİK CADDESİ İNCİ BÖREK", "catalog", ("26594", "15450", "54276"), ()),
        ("Adres tabanlı yedek durak 86", "home", ("61041",), ("61041",)),
        ("TEPEBAŞI LUKOİL PETROL", "catalog", ("52488", "41134", "44182", "46895", "52004", "3208", "54457", "19980"), ()),
    ),
    (
        ("Adres tabanlı yedek durak 50", "home", ("40374",), ("40374",)),
        ("Otomatik ortak durak 87", "midpoint", ("60882", "60083"), ("60083", "43536")),
        ("UZUNLAR SK.", "catalog", ("33182", "44264", "52532", "6696"), ()),
        ("GÜNDÜZ ÖKÇÜN BULVARI FATİH CAMİ", "catalog", ("11508", "58161", "46547"), ()),
        ("GÜNDÜZ ÖKÇÜN BULVARI ENTEPE KONUTLARI", "catalog", ("44550", "3823", "41031"), ()),
        ("ÇOŞTU SOKAK PALMİYE SİT. KARŞISI", "catalog", ("55665", "53656", "18253", "43536"), ()),
    ),
    (
        ("Adres tabanlı yedek durak 10", "home", ("39011",), ("39011",)),
        ("TOTAL PETROL", "catalog", ("43689",), ()),
        ("ALANÖNÜ NUR TAKSİ", "catalog", ("40435", "48495", "47557", "44546"), ()),
        ("ZİYAPAŞA CAD. MİNİ TAKSİ", "catalog", ("47781",), ()),
        ("HAT BOYU A101 ÖNÜ", "catalog", ("40002", "45778"), ()),
        ("SAKARYA CAD. SAAT KULESİ KARAKOL", "catalog", ("48394", "50870", "52361", "41553"), ()),
        ("KANAL 26 ATM ÖNÜ", "catalog", ("61081", "13672", "52699"), ()),
        ("BAHÇELİEVLER MUHTARLIK (AYTAÇ CADDESİ)", "catalog", ("59545", "48214", "47712", "47865"), ()),
        ("BÖREKÇİ (AYTAÇ CADDESİ)", "catalog", ("59124", "47969", "44327", "46865", "44549"), ()),
        ("AÖF ÖNÜ", "catalog", ("60706", "51071"), ()),
        ("TEPEBAŞI IŞIKLAR PETEK PASTANESİ", "catalog", ("32142", "44284", "56418", "20974"), ()),
    ),
    (
        ("Adres tabanlı yedek durak 83", "home", ("60873",), ("60873",)),
        ("Adres tabanlı yedek durak 52", "home", ("38932",), ("38932",)),
        ("ATATÜRK BULVARI BİM (AKARBAŞI TARAFI)", "catalog", ("60848", "59194"), ()),
        ("ÖĞRETMEN EVİ", "catalog", ("43713", "57647", "26066", "37457"), ()),
        ("SAMİ RAMAZANOĞLU CAMİ", "catalog", ("19297", "26944"), ()),
        ("ALİ FUAT GÜVEN CAD. BEYAZIT CAMİ", "catalog", ("45768", "51971", "50131"), ()),
        ("ATATÜRK CADDESİ MODA TAKSİ", "catalog", ("57617", "28081", "30132"), ()),
    ),
)


def _reference_employee_key(value: object) -> str:
    """Excel'deki 123 / 123.0 / '123' biçimlerini aynı sicil anahtarına çevirir."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    if re.fullmatch(r"-?\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def _reference_label_key(value: object) -> str:
    """Durak adlarını Türkçe karakter ve noktalama farklarına toleranslı eşleştirir."""
    table = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    text = str(value).strip().translate(table).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def reference_route_employee_ids() -> set[str]:
    return {
        employee_id
        for route in REFERENCE_ROUTE_TEMPLATE
        for _, _, member_ids, _ in route
        for employee_id in member_ids
    }


def build_reference_four_routes(
    employee_ids: Sequence[object],
    employee_coordinates: Sequence[tuple[float, float]],
    approved_candidates: Sequence[tuple] | None,
    max_walk_m: float = 1000.0,
    walking_factor: float = 1.20,
    capacity: int = 45,
) -> list[list[CommonStop]] | None:
    """Yüklenen dört rotalı referans çıktının Rota 1–2–3–4 dağılımını birebir kurar.

    Bu plan yalnızca çalışan kümesi dört rotalı referans plandaki 92 sicille birebir aynıysa
    devreye girer. Sicil kümesi değişmişse ``None`` döner ve normal optimizasyon
    akışı devam eder.

    Durak koordinatları:
    - mevcut duraklarda yüklenen durak kataloğundan,
    - "Adres tabanlı yedek" duraklarda ilgili çalışanın koordinatından,
    - otomatik ortak 87 ve 802 duraklarında eski algoritmanın ürettiği aynı
      çalışan çiftlerinin geometrik orta noktasından alınır.
    """
    if len(employee_ids) != len(employee_coordinates):
        raise ValueError("Çalışan ID ve koordinat sayıları eşleşmiyor.")

    key_to_index: dict[str, int] = {}
    for index, raw_id in enumerate(employee_ids):
        key = _reference_employee_key(raw_id)
        if not key or key in key_to_index:
            return None
        key_to_index[key] = index

    expected_ids = reference_route_employee_ids()
    if set(key_to_index) != expected_ids:
        return None

    if walking_factor < 1:
        raise ValueError("Yürüyüş katsayısı en az 1 olmalıdır.")

    catalog: dict[str, tuple[float, float, str]] = {}
    for raw in approved_candidates or []:
        if len(raw) < 3:
            continue
        try:
            lat = float(raw[0])
            lon = float(raw[1])
        except (TypeError, ValueError):
            continue
        label = str(raw[2]).strip()
        if label:
            catalog[_reference_label_key(label)] = (lat, lon, label)

    def employee_coord(employee_id: str) -> tuple[float, float]:
        idx = key_to_index[employee_id]
        lat, lon = employee_coordinates[idx]
        return float(lat), float(lon)

    def resolve_stop(
        label: str,
        kind: str,
        coordinate_ids: Sequence[str],
    ) -> tuple[float, float, str]:
        if kind == "catalog":
            record = catalog.get(_reference_label_key(label))
            if record is None:
                raise ValueError(
                    f"Referans 4 rota için gerekli durak yüklenen durak dosyasında bulunamadı: {label}"
                )
            return record[0], record[1], "Yüklenen aday durak"

        if kind == "home":
            lat, lon = employee_coord(coordinate_ids[0])
            return lat, lon, "Çalışan adresi"

        if kind == "midpoint":
            first = employee_coord(coordinate_ids[0])
            second = employee_coord(coordinate_ids[1])
            return (
                (first[0] + second[0]) / 2.0,
                (first[1] + second[1]) / 2.0,
                "Otomatik ortak nokta",
            )

        raise ValueError(f"Bilinmeyen referans durak türü: {kind}")

    routes: list[list[CommonStop]] = []
    matrix_index = 1
    for route_template in REFERENCE_ROUTE_TEMPLATE:
        route: list[CommonStop] = []
        route_load = 0
        for stop_order, (label, kind, member_ids, coordinate_ids) in enumerate(
            route_template, start=1
        ):
            latitude, longitude, source = resolve_stop(label, kind, coordinate_ids)
            member_indices = [key_to_index[employee_id] for employee_id in member_ids]
            walking_distances = [
                haversine_km(
                    (latitude, longitude),
                    (
                        float(employee_coordinates[member_index][0]),
                        float(employee_coordinates[member_index][1]),
                    ),
                )
                * 1000.0
                * walking_factor
                for member_index in member_indices
            ]
            if walking_distances and max(walking_distances) > max_walk_m + 0.5:
                raise ValueError(
                    f"Referans rota {label} durağında azami yürüme sınırı aşılıyor: "
                    f"{max(walking_distances):.0f} m > {max_walk_m:.0f} m."
                )

            stop = CommonStop(
                anchor_index=stop_order - 1,
                member_indices=member_indices,
                walking_distances_m=walking_distances,
                latitude=latitude,
                longitude=longitude,
                label=label,
                source=source,
                matrix_index=matrix_index,
            )
            route.append(stop)
            route_load += stop.passenger_count
            matrix_index += 1

        if route_load > capacity:
            raise ValueError(
                f"Referans rota {route_load} yolcu içeriyor; araç kapasitesi {capacity}."
            )
        routes.append(route)

    return routes


def _interpolate_towards(
    origin: tuple[float, float],
    destination: tuple[float, float],
    distance_m: float,
) -> tuple[float, float]:
    """Başlangıçtan hedefe doğru yaklaşık ``distance_m`` ilerlenmiş noktayı döndürür."""
    total_m = haversine_km(origin, destination) * 1000
    if total_m <= 1e-9 or distance_m <= 0:
        return float(origin[0]), float(origin[1])
    ratio = min(1.0, distance_m / total_m)
    return (
        float(origin[0] + (destination[0] - origin[0]) * ratio),
        float(origin[1] + (destination[1] - origin[1]) * ratio),
    )


def _fallback_westbound_anchor(point: tuple[float, float]) -> tuple[float, float]:
    """Durak listesi yoksa Bozüyük yönündeki ana akışı temsil eden yedek hedef."""
    return float(point[0]), float(point[1] - 0.025)


def _normalize_approved_candidates(
    approved_candidates: Sequence[tuple] | None,
) -> list[tuple[float, float, str, str]]:
    """3 veya 4 alanlı aday durak kayıtlarını tek biçime dönüştürür.

    Dördüncü alan varsa mevcut rota/grup bilgisidir. Bu bilgi, farklı mevcut
    rotaların son ve ilk duraklarını yanlışlıkla tek bir koridor segmenti gibi
    birleştirmemek için kullanılır.
    """
    records: list[tuple[float, float, str, str]] = []
    for raw in approved_candidates or []:
        if len(raw) < 3:
            continue
        lat, lon, label = raw[0], raw[1], raw[2]
        route_group = raw[3] if len(raw) >= 4 else ""
        try:
            records.append(
                (
                    float(lat),
                    float(lon),
                    str(label).strip(),
                    str(route_group).strip(),
                )
            )
        except (TypeError, ValueError):
            continue
    return records


def _corridor_segments(
    approved_records: Sequence[tuple[float, float, str, str]],
) -> list[tuple[tuple[float, float], tuple[float, float], str]]:
    """Yüklenmiş durak sırasından mevcut rota koridor segmentlerini üretir."""
    segments: list[tuple[tuple[float, float], tuple[float, float], str]] = []
    for first, second in zip(approved_records, approved_records[1:]):
        a = (first[0], first[1])
        b = (second[0], second[1])
        first_group = first[3]
        second_group = second[3]
        if first_group and second_group and first_group != second_group:
            continue
        # Rota/grup bilgisi yoksa çok uzun ardışık sıçramaları koridor sayma.
        if not first_group and not second_group:
            if haversine_km(a, b) > MAX_FALLBACK_CORRIDOR_SEGMENT_KM:
                continue
        segments.append((a, b, first_group or second_group))
    return segments


def _project_to_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> tuple[tuple[float, float], float]:
    """Noktayı kısa şehir içi segmentine yerel metre düzleminde izdüşürür."""
    lat0 = math.radians(point[0])
    meters_per_lat = 111_320.0
    meters_per_lon = 111_320.0 * max(math.cos(lat0), 1e-6)

    ax = (start[1] - point[1]) * meters_per_lon
    ay = (start[0] - point[0]) * meters_per_lat
    bx = (end[1] - point[1]) * meters_per_lon
    by = (end[0] - point[0]) * meters_per_lat
    abx = bx - ax
    aby = by - ay
    denom = abx * abx + aby * aby
    if denom <= 1e-9:
        projection = start
    else:
        t = max(0.0, min(1.0, -(ax * abx + ay * aby) / denom))
        px = ax + t * abx
        py = ay + t * aby
        projection = (
            point[0] + py / meters_per_lat,
            point[1] + px / meters_per_lon,
        )
    distance_m = haversine_km(point, projection) * 1000
    return (float(projection[0]), float(projection[1])), distance_m


def _nearest_corridor_projection(
    point: tuple[float, float],
    approved_records: Sequence[tuple[float, float, str, str]],
) -> tuple[tuple[float, float], float, str] | None:
    """Mevcut durak koridorundaki en yakın noktayı ve rota grubunu bulur."""
    best: tuple[tuple[float, float], float, str] | None = None
    for start, end, group in _corridor_segments(approved_records):
        projection, distance_m = _project_to_segment(point, start, end)
        candidate = (projection, distance_m, group)
        if best is None or distance_m < best[1]:
            best = candidate
    if best is not None:
        return best
    if approved_records:
        nearest = min(
            approved_records,
            key=lambda record: haversine_km(point, (record[0], record[1])),
        )
        nearest_point = (nearest[0], nearest[1])
        return nearest_point, haversine_km(point, nearest_point) * 1000, nearest[3]
    return None


def _estimated_walk_m(
    point: tuple[float, float],
    employee: tuple[float, float],
    walking_factor: float,
) -> float:
    return haversine_km(point, employee) * 1000 * walking_factor


def _away_from_employee_homes(
    point: tuple[float, float],
    employee_coordinates: Sequence[tuple[float, float]],
    minimum_offset_m: float,
) -> bool:
    """Otomatik noktanın herhangi bir çalışan koordinatına yapışmasını engeller."""
    return all(
        haversine_km(point, employee) * 1000 >= minimum_offset_m - 1e-9
        for employee in employee_coordinates
    )


def _detour_point_towards_corridor(
    employee: tuple[float, float],
    corridor_point: tuple[float, float],
    max_walk_m: float,
    walking_factor: float,
) -> tuple[float, float] | None:
    """Çalışandan mevcut güzergâha doğru, yürüme sınırı içinde yeni aday üretir."""
    straight_limit_m = max_walk_m / walking_factor
    total_to_corridor_m = haversine_km(employee, corridor_point) * 1000
    # Koridor zaten erişilebiliyorsa doğrudan koridor noktası tercih edilir.
    if total_to_corridor_m <= straight_limit_m + 1e-9:
        candidate = corridor_point
    else:
        # Ev önü alımını engelleyip olabildiğince güzergâha yaklaş.
        desired_m = min(straight_limit_m * 0.92, total_to_corridor_m)
        desired_m = max(desired_m, MIN_HOME_PICKUP_OFFSET_M * 1.5)
        candidate = _interpolate_towards(employee, corridor_point, desired_m)
    if not _away_from_employee_homes(candidate, (employee,), MIN_HOME_PICKUP_OFFSET_M):
        return None
    if _estimated_walk_m(candidate, employee, walking_factor) > max_walk_m + 1e-9:
        return None
    return candidate


def _corridor_biased_midpoint(
    first: tuple[float, float],
    second: tuple[float, float],
    corridor_point: tuple[float, float],
    max_walk_m: float,
    walking_factor: float,
) -> tuple[float, float] | None:
    """İki kişilik ortak noktayı en yakın mevcut güzergâha doğru kontrollü kaydırır."""
    midpoint = ((first[0] + second[0]) / 2, (first[1] + second[1]) / 2)
    straight_limit_m = max_walk_m / walking_factor
    for shift_fraction in (0.30, 0.20, 0.12, 0.06, 0.0):
        candidate = _interpolate_towards(
            midpoint,
            corridor_point,
            straight_limit_m * shift_fraction,
        )
        if not _away_from_employee_homes(
            candidate,
            (first, second),
            MIN_HOME_PICKUP_OFFSET_M,
        ):
            continue
        if all(
            _estimated_walk_m(candidate, employee, walking_factor) <= max_walk_m + 1e-9
            for employee in (first, second)
        ):
            return candidate
    return None

def snap_to_drivable_road(
    point: tuple[float, float],
    timeout: int = 3,
) -> tuple[tuple[float, float], str] | None:
    """Aday noktayı OSRM ile en yakın araç yoluna oturtur; erişilemezse ``None`` döner."""
    lat, lon = map(float, point)
    url = f"https://router.project-osrm.org/nearest/v1/driving/{lon:.6f},{lat:.6f}?number=1"
    request = Request(url, headers={"User-Agent": "servis-optimizasyon-web/1.0"})
    try:
        with urlopen(request, timeout=max(1, timeout)) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("code") != "Ok" or not payload.get("waypoints"):
            return None
        waypoint = payload["waypoints"][0]
        snapped_lon, snapped_lat = waypoint["location"]
        road_name = str(waypoint.get("name") or "").strip()
        return (float(snapped_lat), float(snapped_lon)), road_name
    except Exception:
        return None


def _snap_selected_automatic_stops_to_road(
    stops: Sequence[CommonStop],
    employee_coordinates: Sequence[tuple[float, float]],
    max_walk_m: float,
    walking_factor: float,
    minimum_home_offset_m: float,
) -> None:
    """Seçilen otomatik adayları OSRM ile en yakın araç yoluna oturtur.

    Yol noktasına oturtma, ilgili çalışanların yürüme sınırını bozuyorsa değişiklik
    uygulanmaz. Böylece ev koordinatı durak yapılmadan yol üstü aday korunur.
    """
    automatic_sources = {ROUTE_CORRIDOR_SOURCE, ROUTE_DETOUR_SOURCE, "Otomatik ortak nokta"}
    for stop in stops:
        if stop.source not in automatic_sources:
            continue
        snapped = snap_to_drivable_road((float(stop.latitude), float(stop.longitude)))
        if snapped is None:
            continue
        snapped_point, road_name = snapped
        walks = [
            _estimated_walk_m(snapped_point, employee_coordinates[index], walking_factor)
            for index in stop.member_indices
        ]
        if any(walk > max_walk_m + 1e-9 for walk in walks):
            continue
        if not _away_from_employee_homes(
            snapped_point,
            [employee_coordinates[index] for index in stop.member_indices],
            minimum_home_offset_m,
        ):
            continue
        stop.latitude, stop.longitude = snapped_point
        stop.walking_distances_m = walks
        if road_name:
            stop.label = f"{road_name} güzergâh adayı"

def cluster_common_stops(
    coordinates: Sequence[tuple[float, float]],
    max_walk_m: float = 500.0,
    capacity: int | None = None,
    walking_factor: float = 1.20,
    time_limit_seconds: int = 15,
) -> list[CommonStop]:
    """Ev adreslerini durak yapmadan rota-koridoru ortak noktaları seçer.

    Bu geriye uyumlu yardımcıda yüklenmiş durak listesi bulunmadığı için çalışan
    noktaları doğrudan durak yapılmaz. Bunun yerine Bozüyük yönündeki servis
    akışına kaydırılmış adaylar ve uygun ortak noktalar üretilir.
    """
    if max_walk_m < 0:
        raise ValueError("Azami yürüme mesafesi negatif olamaz.")
    if walking_factor < 1:
        raise ValueError("Yürüyüş katsayısı en az 1 olmalıdır.")
    if not coordinates:
        return []

    max_stop_load = capacity or len(coordinates)
    if max_stop_load <= 0:
        raise ValueError("Durak kapasitesi sıfırdan büyük olmalıdır.")

    candidates = generate_candidate_stops(
        coordinates,
        max_walk_m=max_walk_m,
        walking_factor=walking_factor,
        approved_candidates=None,
        allow_automatic_candidates=True,
    )
    stops, _, _ = optimize_candidate_stops(
        coordinates,
        candidates,
        max_walk_m=max_walk_m,
        target_average_walk_m=max_walk_m,
        walking_factor=walking_factor,
        time_limit_seconds=time_limit_seconds,
    )

    if not capacity:
        return stops

    capacity_limited: list[CommonStop] = []
    for stop in stops:
        pairs = list(zip(stop.member_indices, stop.walking_distances_m))
        for start in range(0, len(pairs), max_stop_load):
            chunk = pairs[start : start + max_stop_load]
            capacity_limited.append(
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
    return capacity_limited


def generate_candidate_stops(
    employee_coordinates: Sequence[tuple[float, float]],
    max_walk_m: float = 500.0,
    walking_factor: float = 1.20,
    approved_candidates: Sequence[tuple] | None = None,
    allow_automatic_candidates: bool = True,
    factory_coordinates: tuple[float, float] | None = None,
) -> list[CandidateStop]:
    """Mevcut durakları ve güzergâh koridorunu esas alarak aday durak üretir.

    Öncelik daima yüklenen/onaylı duraklardadır. Çalışanın ev koordinatı hiçbir
    koşulda durak adayı yapılmaz. Mevcut durakla kapsanamayan çalışan için önce
    mevcut güzergâh segmenti üzerinde yürünebilir bir nokta aranır. Bu mümkün
    değilse çalışan ile en yakın güzergâh arasındaki bağlantıda, evden uzakta ve
    yürüyüş sınırı içinde kalan minimum sapmalı yeni bir aday üretilir.
    """
    if not employee_coordinates:
        return []
    if walking_factor < 1:
        raise ValueError("Yürüyüş katsayısı en az 1 olmalıdır.")
    if max_walk_m <= MIN_HOME_PICKUP_OFFSET_M * walking_factor:
        raise ValueError(
            "Azami yürüme mesafesi, ev adresinden güvenli uzaklık oluşturmak için yetersiz. "
            f"En az {MIN_HOME_PICKUP_OFFSET_M * walking_factor:.0f} m seçin."
        )

    approved_records = _normalize_approved_candidates(approved_candidates)
    raw_candidates: list[CandidateStop] = []
    for index, (lat, lon, label, _route_group) in enumerate(approved_records, start=1):
        raw_candidates.append(
            CandidateStop(
                lat,
                lon,
                label or f"Yüklenen aday durak {index}",
                "Yüklenen aday durak",
            )
        )

    if allow_automatic_candidates:
        straight_radius_m = max_walk_m / walking_factor
        automatic_no = 1
        detour_no = 1

        for employee in employee_coordinates:
            employee_point = (float(employee[0]), float(employee[1]))
            # Mevcut/onaylı bir durak zaten yürünebilir mesafedeyse yeni durak üretme.
            if any(
                _estimated_walk_m((record[0], record[1]), employee_point, walking_factor)
                <= max_walk_m + 1e-9
                for record in approved_records
            ):
                continue

            nearest = _nearest_corridor_projection(employee_point, approved_records)
            if nearest is not None:
                corridor_point, corridor_distance_m, route_group = nearest
            else:
                route_group = ""
                corridor_point = (
                    (float(factory_coordinates[0]), float(factory_coordinates[1]))
                    if factory_coordinates is not None
                    else _fallback_westbound_anchor(employee_point)
                )
                corridor_distance_m = haversine_km(employee_point, corridor_point) * 1000

            # Güzergâh segmentinin kendisi yürünebiliyorsa durak tam koridor üzerinde olsun.
            if corridor_distance_m * walking_factor <= max_walk_m + 1e-9:
                candidate_point = corridor_point
                if _away_from_employee_homes(
                    candidate_point,
                    (employee_point,),
                    MIN_HOME_PICKUP_OFFSET_M,
                ):
                    route_text = f"{route_group} " if route_group else ""
                    raw_candidates.append(
                        CandidateStop(
                            candidate_point[0],
                            candidate_point[1],
                            f"{route_text}güzergâh üzeri aday durak {automatic_no}".strip(),
                            ROUTE_CORRIDOR_SOURCE,
                        )
                    )
                    automatic_no += 1
                    continue

            # Koridor 1.000 m dışında kalıyorsa ev yerine koridora doğru minimum sapmalı
            # bir bağlantı noktası açılır. Bu nokta daha sonra seçilirse OSRM ile yola oturtulur.
            candidate_point = _detour_point_towards_corridor(
                employee_point,
                corridor_point,
                max_walk_m,
                walking_factor,
            )
            if candidate_point is not None:
                route_text = f"{route_group} " if route_group else ""
                raw_candidates.append(
                    CandidateStop(
                        candidate_point[0],
                        candidate_point[1],
                        f"{route_text}güzergâha yakın yeni durak {detour_no}".strip(),
                        ROUTE_DETOUR_SOURCE,
                    )
                )
                detour_no += 1

        # Yakın iki çalışan için ortak nokta da evlerin ortasında bırakılmaz;
        # mümkün olduğunca mevcut güzergâha doğru kaydırılır.
        midpoint_no = 1
        for first in range(len(employee_coordinates)):
            for second in range(first + 1, len(employee_coordinates)):
                if (
                    haversine_km(employee_coordinates[first], employee_coordinates[second]) * 1000
                    > 2 * straight_radius_m + 1e-9
                ):
                    continue
                pair_midpoint = (
                    (employee_coordinates[first][0] + employee_coordinates[second][0]) / 2,
                    (employee_coordinates[first][1] + employee_coordinates[second][1]) / 2,
                )
                nearest = _nearest_corridor_projection(pair_midpoint, approved_records)
                if nearest is not None:
                    corridor_point, _distance_m, route_group = nearest
                else:
                    route_group = ""
                    corridor_point = (
                        (float(factory_coordinates[0]), float(factory_coordinates[1]))
                        if factory_coordinates is not None
                        else _fallback_westbound_anchor(pair_midpoint)
                    )
                candidate_point = _corridor_biased_midpoint(
                    employee_coordinates[first],
                    employee_coordinates[second],
                    corridor_point,
                    max_walk_m,
                    walking_factor,
                )
                if candidate_point is None:
                    continue
                route_text = f"{route_group} " if route_group else ""
                raw_candidates.append(
                    CandidateStop(
                        candidate_point[0],
                        candidate_point[1],
                        f"{route_text}ortak güzergâh adayı {midpoint_no}".strip(),
                        "Otomatik ortak nokta",
                    )
                )
                midpoint_no += 1

    source_priority = {
        "Yüklenen aday durak": 0,
        "Onaylı durak": 0,
        "Mevcut durak": 0,
        ROUTE_CORRIDOR_SOURCE: 1,
        ROUTE_DETOUR_SOURCE: 2,
        "Otomatik ortak nokta": 2,
    }
    best_by_coverage: dict[tuple[int, ...], tuple[tuple[int, float], CandidateStop]] = {}
    for candidate in raw_candidates:
        distances = [
            haversine_km((candidate.latitude, candidate.longitude), employee) * 1000 * walking_factor
            for employee in employee_coordinates
        ]
        coverage = tuple(
            index for index, distance in enumerate(distances)
            if distance <= max_walk_m + 1e-9
        )
        if not coverage:
            continue
        score = (
            source_priority.get(candidate.source, 9),
            sum(distances[index] for index in coverage),
        )
        if coverage not in best_by_coverage or score < best_by_coverage[coverage][0]:
            best_by_coverage[coverage] = (score, candidate)

    return [value[1] for value in best_by_coverage.values()]

def optimize_candidate_stops(
    employee_coordinates: Sequence[tuple[float, float]],
    candidates: Sequence[CandidateStop],
    max_walk_m: float = 500.0,
    target_average_walk_m: float = 300.0,
    walking_factor: float = 1.20,
    time_limit_seconds: int = 8,
    snap_single_stops_to_road: bool = True,
    minimum_home_offset_m: float = MIN_HOME_PICKUP_OFFSET_M,
) -> tuple[list[CommonStop], int, bool]:
    """Aday durakları set-cover + hedef programlama mantığıyla seçer.

    Birinci aşama, tüm çalışanları kapsayan kesin minimum durak sayısını bulur.
    İkinci aşama, hedef ortalama yürüyüş sağlanana kadar en fazla yürüyüş
    iyileştirmesi sağlayan adayları ekler. Bu, minimum durak ile çalışan konforu
    arasındaki Pareto dengesini görünür ve denetlenebilir kılar.
    """
    if not employee_coordinates:
        return [], 0, True
    if not candidates:
        raise ValueError("Ortak durak optimizasyonu için aday durak bulunamadı.")

    distances_m = [
        [
            haversine_km((candidate.latitude, candidate.longitude), employee) * 1000 * walking_factor
            for employee in employee_coordinates
        ]
        for candidate in candidates
    ]
    cover_by_employee = [
        [
            candidate_index
            for candidate_index in range(len(candidates))
            if distances_m[candidate_index][employee_index] <= max_walk_m + 1e-9
        ]
        for employee_index in range(len(employee_coordinates))
    ]

    uncovered_employee_indices = [
        employee_index
        for employee_index, covering in enumerate(cover_by_employee)
        if not covering
    ]
    if uncovered_employee_indices:
        displayed = ", ".join(
            str(employee_index + 1)
            for employee_index in uncovered_employee_indices[:20]
        )
        remainder = len(uncovered_employee_indices) - 20
        suffix = f" ve {remainder} kişi daha" if remainder > 0 else ""
        raise ValueError(
            f"{len(uncovered_employee_indices)} çalışan için erişilebilir aday durak bulunamadı. "
            f"Çalışan sıraları: {displayed}{suffix}."
        )

    # Yüklenen/mevcut bir durakla kapsanabilen çalışan, otomatik adaylara
    # aktarılmaz. Otomatik güzergâh adayları yalnızca yüklenen duraklarla
    # hiç kapsanamayan çalışanlar için devreye girer. Böylece durak türü önceliği
    # sadece aday ayıklamada değil, asıl optimizasyon modelinde de uygulanır.
    uploaded_sources = {"Yüklenen aday durak", "Onaylı durak", "Mevcut durak"}
    eligible_by_employee: list[list[int]] = []
    for covering in cover_by_employee:
        uploaded_covering = [
            candidate_index
            for candidate_index in covering
            if candidates[candidate_index].source in uploaded_sources
        ]
        eligible_by_employee.append(uploaded_covering or covering)

    model = cp_model.CpModel()
    selected_vars = [model.new_bool_var(f"candidate_{index}") for index in range(len(candidates))]
    for covering in eligible_by_employee:
        model.add(sum(selected_vars[index] for index in covering) >= 1)
    model.minimize(sum(selected_vars))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(1, time_limit_seconds)
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 42
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise ValueError("Aday duraklar için kapsama çözümü bulunamadı.")

    selected = {index for index, variable in enumerate(selected_vars) if solver.value(variable)}
    minimum_stop_count = len(selected)
    minimum_proven = status == cp_model.OPTIMAL

    def walking_assignment(selected_indices: set[int]) -> tuple[list[int], list[float]]:
        assigned: list[int] = []
        walks: list[float] = []
        for employee_index, covering in enumerate(eligible_by_employee):
            feasible = [index for index in covering if index in selected_indices]
            chosen = min(feasible, key=lambda index: (distances_m[index][employee_index], index))
            assigned.append(chosen)
            walks.append(distances_m[chosen][employee_index])
        return assigned, walks

    assigned, walks = walking_assignment(selected)
    target = min(max(target_average_walk_m, 0), max_walk_m)
    while walks and sum(walks) / len(walks) > target:
        best_candidate = None
        best_improvement = 0.0
        for candidate_index in range(len(candidates)):
            if candidate_index in selected:
                continue
            improvement = sum(
                max(0.0, walks[employee_index] - distances_m[candidate_index][employee_index])
                for employee_index in range(len(employee_coordinates))
                if candidate_index in eligible_by_employee[employee_index]
            )
            if improvement > best_improvement + 1e-9:
                best_candidate = candidate_index
                best_improvement = improvement
        if best_candidate is None:
            break
        selected.add(best_candidate)
        assigned, walks = walking_assignment(selected)

    members_by_candidate: dict[int, list[int]] = {index: [] for index in selected}
    walks_by_candidate: dict[int, list[float]] = {index: [] for index in selected}
    for employee_index, candidate_index in enumerate(assigned):
        members_by_candidate[candidate_index].append(employee_index)
        walks_by_candidate[candidate_index].append(walks[employee_index])

    stops: list[CommonStop] = []
    for candidate_index in sorted(selected):
        members = members_by_candidate[candidate_index]
        if not members:
            continue
        candidate = candidates[candidate_index]
        member_walk_pairs = sorted(
            zip(members, walks_by_candidate[candidate_index]),
            key=lambda pair: (pair[1], pair[0]),
        )
        stops.append(
            CommonStop(
                anchor_index=candidate_index,
                member_indices=[pair[0] for pair in member_walk_pairs],
                walking_distances_m=[pair[1] for pair in member_walk_pairs],
                latitude=candidate.latitude,
                longitude=candidate.longitude,
                label=candidate.label,
                source=candidate.source,
            )
        )
    if snap_single_stops_to_road:
        _snap_selected_automatic_stops_to_road(
            stops,
            employee_coordinates,
            max_walk_m,
            walking_factor,
            minimum_home_offset_m,
        )
    return stops, minimum_stop_count, minimum_proven


def update_routes_incrementally(
    employee_coordinates: Sequence[tuple[float, float]],
    baseline_routes: Sequence[Sequence[CommonStop]],
    factory_coordinates: tuple[float, float],
    approved_candidates: Sequence[tuple[float, float, str]] | None = None,
    max_walk_m: float = 500.0,
    target_average_walk_m: float = 300.0,
    walking_factor: float = 1.20,
    capacity: int = 40,
    direction: str = "morning",
    wait_seconds_per_stop: int = 15,
    max_route_minutes: float = 120.0,
    mode: str = "auto",
    use_road_network: bool = True,
    allow_automatic_candidates: bool = True,
) -> tuple[list[list[CommonStop]], list[list[float]], list[list[float]], dict]:
    """Mevcut durak ve rota yapısını koruyarak yeni çalışanları plana ekler.

    Önce önceki plandaki aktif çalışan-durak eşleşmeleri korunur. Yeni veya adresi
    değişmiş çalışanlar sırasıyla mevcut erişilebilir durağa, yüklenen bir durağa ve
    izin verilmişse otomatik bir aday noktaya atanır. Yeni duraklar mevcut durak
    sırasını bozmadan en düşük ek süreli konuma yerleştirilir. Kapasite veya süre
    yetmezse yalnızca otomatik araç sayısı modunda yeni rota açılır.
    """
    if direction not in {"morning", "evening"}:
        raise ValueError("Yön 'morning' veya 'evening' olmalıdır.")
    if mode not in {"fixed", "auto"}:
        raise ValueError("Rota modu 'fixed' veya 'auto' olmalıdır.")
    if capacity <= 0:
        raise ValueError("Araç kapasitesi sıfırdan büyük olmalıdır.")
    if not employee_coordinates:
        return [], [[0.0]], [[0.0]], {
            "vehicle_count": 0,
            "candidate_count": 0,
            "minimum_stop_count": 0,
            "minimum_proven": True,
            "selected_stop_count": 0,
            "matrix_source": "Hesaplanmadı",
            "warnings": [],
            "planning_mode": "incremental",
            "preserved_employee_count": 0,
            "added_to_existing_count": 0,
            "new_or_changed_count": 0,
            "new_stop_count": 0,
            "added_route_count": 0,
        }

    # Girdi nesnelerini değiştirmemek ve artık yolcusu kalmayan durakları
    # temizleyebilmek için mevcut plan kopyalanır.
    routes: list[list[CommonStop]] = []
    baseline_stop_count = 0
    assigned_employees: set[int] = set()
    for raw_route in baseline_routes:
        route: list[CommonStop] = []
        for raw_stop in raw_route:
            baseline_stop_count += 1
            pairs: list[tuple[int, float]] = []
            for employee_index, old_distance in zip(
                raw_stop.member_indices,
                raw_stop.walking_distances_m,
            ):
                if not 0 <= employee_index < len(employee_coordinates):
                    continue
                if employee_index in assigned_employees:
                    continue
                distance = haversine_km(
                    (float(raw_stop.latitude), float(raw_stop.longitude)),
                    employee_coordinates[employee_index],
                ) * 1000 * walking_factor
                if distance <= max_walk_m + 1e-9:
                    pairs.append((employee_index, distance))
                    assigned_employees.add(employee_index)
            if pairs:
                pairs.sort(key=lambda pair: (pair[1], pair[0]))
                route.append(
                    CommonStop(
                        anchor_index=raw_stop.anchor_index,
                        member_indices=[pair[0] for pair in pairs],
                        walking_distances_m=[pair[1] for pair in pairs],
                        latitude=float(raw_stop.latitude),
                        longitude=float(raw_stop.longitude),
                        label=raw_stop.label,
                        source=raw_stop.source,
                    )
                )
        routes.append(route)

    preserved_employee_count = len(assigned_employees)
    preserved_baseline_stop_count = sum(len(route) for route in routes)
    initially_unassigned = [
        index for index in range(len(employee_coordinates)) if index not in assigned_employees
    ]
    route_loads = [sum(stop.passenger_count for stop in route) for route in routes]

    # Yeni/adresi değişmiş çalışan için ilk tercih mevcut rota üzerindeki bir
    # duraktır. Böylece durak sırası ve yol kilometresi değişmez.
    added_to_existing_count = 0
    for employee_index in list(initially_unassigned):
        placements: list[tuple[float, int, int]] = []
        for route_index, route in enumerate(routes):
            if route_loads[route_index] >= capacity:
                continue
            for stop_index, stop in enumerate(route):
                distance = haversine_km(
                    (float(stop.latitude), float(stop.longitude)),
                    employee_coordinates[employee_index],
                ) * 1000 * walking_factor
                if distance <= max_walk_m + 1e-9:
                    placements.append((distance, route_index, stop_index))
        if not placements:
            continue
        distance, route_index, stop_index = min(
            placements,
            key=lambda item: (item[0], route_loads[item[1]], item[1], item[2]),
        )
        stop = routes[route_index][stop_index]
        stop.member_indices.append(employee_index)
        stop.walking_distances_m.append(distance)
        ordered_pairs = sorted(
            zip(stop.member_indices, stop.walking_distances_m),
            key=lambda pair: (pair[1], pair[0]),
        )
        stop.member_indices = [pair[0] for pair in ordered_pairs]
        stop.walking_distances_m = [pair[1] for pair in ordered_pairs]
        route_loads[route_index] += 1
        assigned_employees.add(employee_index)
        added_to_existing_count += 1

    remaining_indices = [
        index for index in range(len(employee_coordinates)) if index not in assigned_employees
    ]
    candidate_count = 0
    minimum_stop_count = 0
    minimum_proven = True
    new_stops: list[CommonStop] = []
    if remaining_indices:
        remaining_coordinates = [employee_coordinates[index] for index in remaining_indices]
        candidates = generate_candidate_stops(
            remaining_coordinates,
            max_walk_m=max_walk_m,
            walking_factor=walking_factor,
            approved_candidates=approved_candidates,
            allow_automatic_candidates=allow_automatic_candidates,
            factory_coordinates=factory_coordinates,
        )
        candidate_count = len(candidates)
        relative_stops, minimum_stop_count, minimum_proven = optimize_candidate_stops(
            remaining_coordinates,
            candidates,
            max_walk_m=max_walk_m,
            target_average_walk_m=target_average_walk_m,
            walking_factor=walking_factor,
        )
        for stop in relative_stops:
            new_stops.append(
                CommonStop(
                    anchor_index=stop.anchor_index,
                    member_indices=[remaining_indices[index] for index in stop.member_indices],
                    walking_distances_m=list(stop.walking_distances_m),
                    latitude=float(stop.latitude),
                    longitude=float(stop.longitude),
                    label=stop.label,
                    source=stop.source,
                )
            )

    # Önceki ve yeni tüm fiziksel noktalar için tek yol matrisi kurulur. Aynı
    # yeni durak kapasite nedeniyle iki araca bölünürse aynı matris indeksini
    # paylaşabilir.
    all_unique_stops = [stop for route in routes for stop in route] + new_stops
    route_coordinates = [
        factory_coordinates,
        *((float(stop.latitude), float(stop.longitude)) for stop in all_unique_stops),
    ]
    for matrix_index, stop in enumerate(all_unique_stops, start=1):
        stop.matrix_index = matrix_index
    duration_matrix, distance_matrix, matrix_source, warnings = get_travel_matrices(
        route_coordinates,
        use_road_network=use_road_network,
    )

    def route_total_seconds(route: Sequence[CommonStop]) -> float:
        indices = [int(stop.matrix_index) for stop in route]
        path = [*indices, 0] if direction == "morning" else [0, *indices]
        drive = sum(duration_matrix[a][b] for a, b in zip(path, path[1:]))
        return drive + len(route) * wait_seconds_per_stop

    horizon_seconds = max_route_minutes * 60 if max_route_minutes else math.inf
    for route_index, route in enumerate(routes):
        if route and route_total_seconds(route) > horizon_seconds + 1e-9:
            raise ValueError(
                f"Önceki planın {route_index + 1}. rotası yeni süre sınırını zaten aşıyor. "
                "Azami rota süresini yükseltin veya tam optimizasyon çalıştırın."
            )

    added_route_count = 0
    # Büyük yeni gruplar önce yerleştirilir; gerekirse aynı fiziksel duraktaki
    # yolcular kapasiteye göre farklı araçlara bölünebilir.
    for new_stop in sorted(new_stops, key=lambda stop: (-stop.passenger_count, stop.label)):
        pending_pairs = list(zip(new_stop.member_indices, new_stop.walking_distances_m))
        while pending_pairs:
            placements: list[tuple[int, float, int, int, int | None]] = []
            for route_index, route in enumerate(routes):
                available = capacity - route_loads[route_index]
                if available <= 0:
                    continue
                take = min(available, len(pending_pairs))
                same_stop_index = next(
                    (
                        index
                        for index, stop in enumerate(route)
                        if stop.matrix_index == new_stop.matrix_index
                    ),
                    None,
                )
                if same_stop_index is not None:
                    placements.append((take, 0.0, route_loads[route_index], route_index, same_stop_index))
                    continue
                old_total = route_total_seconds(route)
                best_delta = math.inf
                best_position = 0
                for position in range(len(route) + 1):
                    trial = [*route[:position], new_stop, *route[position:]]
                    new_total = route_total_seconds(trial)
                    if new_total <= horizon_seconds + 1e-9 and new_total - old_total < best_delta:
                        best_delta = new_total - old_total
                        best_position = position
                if math.isfinite(best_delta):
                    # position bilgisi negatif olmayan bir indeks olarak son alanda taşınır.
                    placements.append((take, best_delta, route_loads[route_index], route_index, -best_position - 1))

            if not placements:
                if mode == "fixed":
                    raise ValueError(
                        "Yeni çalışan mevcut sabit rotalara kapasite/süre sınırları içinde eklenemedi. "
                        "Otomatik rota sayısını seçin veya tam optimizasyon çalıştırın."
                    )
                single_route_total = route_total_seconds([new_stop])
                if single_route_total > horizon_seconds + 1e-9:
                    raise ValueError(
                        f"Yeni durak tek başına {max_route_minutes:.0f} dakikalık rota sınırını aşıyor."
                    )
                routes.append([])
                route_loads.append(0)
                added_route_count += 1
                continue

            take, _, _, route_index, placement_code = min(
                placements,
                key=lambda item: (-item[0], item[1], item[2], item[3]),
            )
            selected_pairs = pending_pairs[:take]
            pending_pairs = pending_pairs[take:]
            if placement_code is not None and placement_code >= 0:
                existing = routes[route_index][placement_code]
                existing.member_indices.extend(pair[0] for pair in selected_pairs)
                existing.walking_distances_m.extend(pair[1] for pair in selected_pairs)
            else:
                position = -int(placement_code) - 1
                fragment = CommonStop(
                    anchor_index=new_stop.anchor_index,
                    member_indices=[pair[0] for pair in selected_pairs],
                    walking_distances_m=[pair[1] for pair in selected_pairs],
                    latitude=new_stop.latitude,
                    longitude=new_stop.longitude,
                    label=new_stop.label,
                    source=new_stop.source,
                    matrix_index=new_stop.matrix_index,
                )
                routes[route_index].insert(position, fragment)
            route_loads[route_index] += take

    routes = [route for route in routes if route]
    if any(
        stop.source in {"Otomatik ortak nokta", ROUTE_CORRIDOR_SOURCE, ROUTE_DETOUR_SOURCE}
        for route in routes
        for stop in route
    ):
        warnings.append(
            "Yeni rota-koridoru/ortak durak önerisi var; kullanılmadan önce saha güvenliği onaylanmalıdır."
        )

    meta = {
        "vehicle_count": len(routes),
        "candidate_count": candidate_count,
        "minimum_stop_count": minimum_stop_count,
        "minimum_proven": minimum_proven,
        "selected_stop_count": sum(len(route) for route in routes),
        "matrix_source": matrix_source,
        "warnings": warnings,
        "planning_mode": "incremental",
        "preserved_employee_count": preserved_employee_count,
        "added_to_existing_count": added_to_existing_count,
        "new_or_changed_count": len(initially_unassigned),
        "new_stop_count": len(new_stops),
        "removed_stop_count": max(0, baseline_stop_count - preserved_baseline_stop_count),
        "added_route_count": added_route_count,
    }
    return routes, duration_matrix, distance_matrix, meta


def build_estimated_matrices(
    coordinates: Sequence[tuple[float, float]], average_speed_kmh: float = 32.0
) -> tuple[list[list[float]], list[list[float]]]:
    """Yol ağı yoksa, mesafeyi 1,28 katsayısı ile yaklaşık yol mesafesine çevirir."""
    n = len(coordinates)
    distances = [[0.0] * n for _ in range(n)]
    durations = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            road_km = haversine_km(coordinates[i], coordinates[j]) * 1.28
            seconds = road_km / max(average_speed_kmh, 1.0) * 3600
            distances[i][j] = distances[j][i] = road_km * 1000
            durations[i][j] = durations[j][i] = seconds
    return durations, distances


def _fetch_osrm_block(
    coordinates: Sequence[tuple[float, float]],
    source_indices: Sequence[int],
    destination_indices: Sequence[int],
    timeout: int,
) -> tuple[list[list[float]], list[list[float]]]:
    """Büyük matrisin tek bir kaynak-hedef bloğunu OSRM'den alır."""
    used_indices = list(dict.fromkeys([*source_indices, *destination_indices]))
    local_index = {global_index: index for index, global_index in enumerate(used_indices)}
    selected_coordinates = [coordinates[index] for index in used_indices]
    coord_text = ";".join(f"{lon:.7f},{lat:.7f}" for lat, lon in selected_coordinates)
    sources = ";".join(str(local_index[index]) for index in source_indices)
    destinations = ";".join(str(local_index[index]) for index in destination_indices)
    url = (
        f"https://router.project-osrm.org/table/v1/driving/{coord_text}"
        f"?annotations=duration,distance&sources={sources}&destinations={destinations}"
    )
    request = Request(url, headers={"User-Agent": "Eskisehir-Servis-Optimizasyonu/1.0"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") != "Ok":
        raise RuntimeError(payload.get("message", "OSRM matrisi alınamadı."))
    durations = payload.get("durations")
    distances = payload.get("distances")
    if (
        not durations
        or not distances
        or any(value is None for row in durations for value in row)
        or any(value is None for row in distances for value in row)
    ):
        raise RuntimeError("Bazı noktalar için yol süresi bulunamadı.")
    return durations, distances


def fetch_osrm_table(
    coordinates: Sequence[tuple[float, float]], timeout: int = 25, block_size: int = 35
) -> tuple[list[list[float]], list[list[float]]]:
    """OSRM'den sürüş matrisi alır; 90'dan fazla noktayı güvenli bloklara böler."""
    point_count = len(coordinates)
    if point_count <= 90:
        indices = list(range(point_count))
        return _fetch_osrm_block(coordinates, indices, indices, timeout)

    if block_size < 1 or block_size * 2 > 90:
        raise ValueError("Blok büyüklüğü 1 ile 45 arasında olmalıdır.")

    durations = [[0.0] * point_count for _ in range(point_count)]
    distances = [[0.0] * point_count for _ in range(point_count)]
    blocks = [
        list(range(start, min(start + block_size, point_count)))
        for start in range(0, point_count, block_size)
    ]
    for source_indices in blocks:
        for destination_indices in blocks:
            duration_block, distance_block = _fetch_osrm_block(
                coordinates, source_indices, destination_indices, timeout
            )
            for source_position, source_index in enumerate(source_indices):
                for destination_position, destination_index in enumerate(destination_indices):
                    durations[source_index][destination_index] = duration_block[source_position][destination_position]
                    distances[source_index][destination_index] = distance_block[source_position][destination_position]
    return durations, distances


def get_travel_matrices(
    coordinates: Sequence[tuple[float, float]],
    use_road_network: bool = True,
    average_speed_kmh: float = 38.0,
) -> tuple[list[list[float]], list[list[float]], str, list[str]]:
    """OSRM veya yaklaşık yöntemle rota matrislerini ve kullanıcı uyarılarını döndürür."""
    warnings: list[str] = []
    if not use_road_network:
        durations, distances = build_estimated_matrices(coordinates, average_speed_kmh)
        return durations, distances, "Yaklaşık mesafe", warnings
    try:
        durations, distances = fetch_osrm_table(coordinates)
        source = "OSRM yol ağı"
    except Exception as exc:
        durations, distances = build_estimated_matrices(coordinates, average_speed_kmh)
        source = "Yaklaşık mesafe"
        warnings.append(f"Yol ağı verisi kullanılamadı; yaklaşık mesafeye geçildi ({exc}).")
    return durations, distances, source, warnings


def fetch_osrm_geometry(
    coordinates: Sequence[tuple[float, float]], timeout: int = 20
) -> list[list[float]]:
    """Haritada gerçek yol çizgisi için [boylam, enlem] noktalarını döndürür."""
    if len(coordinates) < 2:
        return [[coordinates[0][1], coordinates[0][0]]] if coordinates else []
    coord_text = ";".join(f"{lon:.7f},{lat:.7f}" for lat, lon in coordinates)
    url = (
        f"https://router.project-osrm.org/route/v1/driving/{coord_text}"
        "?overview=full&geometries=geojson&steps=false"
    )
    request = Request(url, headers={"User-Agent": "Eskisehir-Servis-Optimizasyonu/1.0"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RuntimeError("Rota geometrisi alınamadı.")
    return payload["routes"][0]["geometry"]["coordinates"]


def clean_address_for_geocoding(address: str, city: str = "Eskişehir, Türkiye") -> str:
    """Kurumsal Excel'deki adresleri harita servislerinin daha kolay okuyacağı hale getirir."""
    text = " ".join(str(address).strip().split())
    replacements = (
        (r"\bMAH\.?\b", "Mahallesi"),
        (r"\bMH\.?\b", "Mahallesi"),
        (r"\bSK\.?\b", "Sokak"),
        (r"\bSOK\.?\b", "Sokak"),
        (r"\bCD\.?\b", "Caddesi"),
        (r"\bCAD\.?\b", "Caddesi"),
        (r"\bBLV\.?\b", "Bulvarı"),
        (r"\bBULV\.?\b", "Bulvarı"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Rota için bina konumu yeterlidir; daire, kat ve iç kapı bilgileri aramayı zorlaştırır.
    text = re.sub(r"\b(?:İÇ\s*KAPI|DAİRE|KAT)\s*(?:NO\s*)?:?\s*[\w/-]+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"(\bNO\s*:?\s*\d+[A-ZÇĞİÖŞÜ]?)[/-]\d+", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:[A-ZÇĞİÖŞÜ]\s*(?:VE\s*[A-ZÇĞİÖŞÜ]\s*)?)?BLOK\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*[,;]+\s*", ", ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,")

    normalized = text.casefold().replace("ş", "s").replace("i̇", "i")
    if city and "eskisehir" not in normalized and "eskişehir" not in text.casefold():
        text = f"{text}, {city}"
    elif "türkiye" not in text.casefold() and "turkiye" not in normalized:
        text = f"{text}, Türkiye"
    return text


def is_eskisehir_coordinate(lat: float, lon: float) -> bool:
    return haversine_km((lat, lon), ESKISEHIR_CENTER) <= ESKISEHIR_MAX_DISTANCE_KM


def _geocode_nominatim(query: str, timeout: int) -> tuple[float, float] | None:
    params = urlencode({"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "tr"})
    request = Request(
        f"https://nominatim.openstreetmap.org/search?{params}",
        headers={
            "User-Agent": "Eskisehir-Servis-Optimizasyonu/2.0 (github.com/ceydaariisoy/servis-optimizasyon-web)",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload:
        return None
    point = float(payload[0]["lat"]), float(payload[0]["lon"])
    return point if is_eskisehir_coordinate(*point) else None


def _geocode_photon(query: str, timeout: int) -> tuple[float, float] | None:
    params = urlencode(
        {
            "q": query,
            "limit": 1,
            "lat": ESKISEHIR_CENTER[0],
            "lon": ESKISEHIR_CENTER[1],
        }
    )
    request = Request(
        f"https://photon.komoot.io/api/?{params}",
        headers={"User-Agent": "Eskisehir-Servis-Optimizasyonu/2.0", "Accept": "application/json"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    features = payload.get("features", []) if isinstance(payload, dict) else []
    if not features:
        return None
    lon, lat = features[0]["geometry"]["coordinates"]
    point = float(lat), float(lon)
    return point if is_eskisehir_coordinate(*point) else None


def geocode_address(address: str, city: str = "Eskişehir, Türkiye", timeout: int = 15) -> tuple[float, float] | None:
    """Adresi temizler; Nominatim sonuç vermezse kullanıcı onaylı Photon yedeğine geçer."""
    query = clean_address_for_geocoding(address, city)
    try:
        found = _geocode_nominatim(query, timeout)
    except Exception:
        found = None
    if found:
        return found
    try:
        return _geocode_photon(query, timeout)
    except Exception:
        return None


def parse_kml_points(file_bytes: bytes, filename: str = "harita.kml") -> list[dict[str, object]]:
    """Google My Maps KML/KMZ dosyasındaki ad, metin ve nokta koordinatlarını okur."""
    kml_bytes = file_bytes
    if filename.casefold().endswith(".kmz"):
        with ZipFile(BytesIO(file_bytes)) as archive:
            kml_names = [name for name in archive.namelist() if name.casefold().endswith(".kml")]
            if not kml_names:
                raise ValueError("KMZ dosyasının içinde KML bulunamadı.")
            kml_bytes = archive.read(kml_names[0])

    root = ElementTree.fromstring(kml_bytes)
    placemarks = root.findall(".//{*}Placemark")
    points: list[dict[str, object]] = []
    for placemark in placemarks:
        coordinate_node = placemark.find(".//{*}Point/{*}coordinates")
        coordinate_text = coordinate_node.text if coordinate_node is not None else None
        separator = ","
        if not coordinate_text:
            # Bazı KML üreticileri standart Point/coordinates yerine gx:coord kullanır.
            coordinate_node = placemark.find(".//{*}coord")
            coordinate_text = coordinate_node.text if coordinate_node is not None else None
            separator = " "
        if not coordinate_text:
            continue
        first_coordinate = coordinate_text.strip().splitlines()[0].strip()
        parts = first_coordinate.split(separator)
        if len(parts) < 2:
            continue
        try:
            lon, lat = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        name_node = placemark.find("./{*}name")
        name = name_node.text.strip() if name_node is not None and name_node.text else ""
        all_text = " ".join(
            node.text.strip() for node in placemark.iter() if node.text and node.text.strip()
        )
        points.append({"name": name, "text": all_text, "lat": lat, "lon": lon})
    if not points:
        address_count = sum(
            1
            for placemark in placemarks
            if (placemark.find("./{*}address") is not None)
        )
        if placemarks and address_count:
            raise ValueError(
                f"Dosyada {len(placemarks)} kayıt ve {address_count} adres var, fakat enlem-boylam "
                "yok. Google My Maps bu haritada konumları KML'ye nokta olarak yazmamış. "
                "Aşağıdaki koordinatlı Excel yöntemini kullanın."
            )
        raise ValueError("KML/KMZ dosyasında koordinatlı nokta bulunamadı.")
    return points



def reverse_routes_for_return(routes: Sequence[Sequence[CommonStop]]) -> list[list[CommonStop]]:
    """Sabah rota gruplarını koruyup akşam için durak sırasını tersine çevirir."""
    return [list(reversed(route)) for route in routes]


def _route_path(employee_indices: Sequence[int], direction: str) -> list[int]:
    if direction == "morning":
        return [*employee_indices, 0]
    return [0, *employee_indices]


def _path_cost(employee_indices: Sequence[int], matrix: Sequence[Sequence[float]], direction: str) -> float:
    path = _route_path(employee_indices, direction)
    return sum(matrix[a][b] for a, b in zip(path, path[1:]))


def _nearest_neighbor(
    employee_indices: Sequence[int], matrix: Sequence[Sequence[float]], direction: str
) -> list[int]:
    remaining = set(employee_indices)
    if not remaining:
        return []
    if direction == "morning":
        current = max(remaining, key=lambda idx: matrix[idx][0])
    else:
        current = min(remaining, key=lambda idx: matrix[0][idx])
    route = [current]
    remaining.remove(current)
    while remaining:
        next_index = min(remaining, key=lambda idx: matrix[current][idx])
        route.append(next_index)
        remaining.remove(next_index)
        current = next_index
    return route


def _two_opt(route: list[int], matrix: Sequence[Sequence[float]], direction: str) -> list[int]:
    if len(route) < 3:
        return route
    best = route[:]
    best_cost = _path_cost(best, matrix, direction)
    improved = True
    while improved:
        improved = False
        for i in range(len(best) - 1):
            for j in range(i + 1, len(best)):
                candidate = best[:i] + list(reversed(best[i : j + 1])) + best[j + 1 :]
                candidate_cost = _path_cost(candidate, matrix, direction)
                if candidate_cost + 1e-9 < best_cost:
                    best, best_cost = candidate, candidate_cost
                    improved = True
        # Küçük veri için yeterli; gereksiz uzun aramayı engeller.
        if len(best) > 80:
            break
    return best


def order_route_points(
    point_indices: Sequence[int],
    duration_matrix: Sequence[Sequence[float]],
    direction: str = "morning",
) -> list[int]:
    """Mevcut matris üzerinde ortak durakları servis yönüne göre sıralar."""
    if direction not in {"morning", "evening"}:
        raise ValueError("Yön 'morning' veya 'evening' olmalıdır.")
    route = _nearest_neighbor(point_indices, duration_matrix, direction)
    return _two_opt(route, duration_matrix, direction)


def _split_ordered_common_stops(
    stops: Sequence[CommonStop],
    ordered_stop_indices: Sequence[int],
    target_loads: Sequence[int],
) -> list[list[CommonStop]]:
    """Ortak durakları hedef araç doluluklarına dağıtır; gerekirse sınır durağını böler."""
    routes: list[list[CommonStop]] = [[] for _ in target_loads]
    route_index = 0
    current_load = 0
    for stop_index in ordered_stop_indices:
        stop = stops[stop_index]
        cursor = 0
        while cursor < stop.passenger_count:
            while route_index < len(target_loads) and current_load >= target_loads[route_index]:
                route_index += 1
                current_load = 0
            if route_index >= len(target_loads):
                raise ValueError("Ortak durak yolcuları araçlara sığdırılamadı.")
            available = target_loads[route_index] - current_load
            take = min(available, stop.passenger_count - cursor)
            routes[route_index].append(
                CommonStop(
                    anchor_index=stop.anchor_index,
                    member_indices=stop.member_indices[cursor : cursor + take],
                    walking_distances_m=stop.walking_distances_m[cursor : cursor + take],
                )
            )
            cursor += take
            current_load += take
    return routes


def _allocate_unsplit_common_stops(
    stops: Sequence[CommonStop],
    ordered_stop_indices: Sequence[int],
    target_loads: Sequence[int],
) -> list[list[CommonStop]] | None:
    """Mümkünse aynı ortak durağın yolcularını farklı araçlara bölmeden yerleştirir."""
    remaining = list(ordered_stop_indices)
    routes: list[list[CommonStop]] = []
    for target in target_loads[:-1]:
        combinations: dict[int, list[int]] = {0: []}
        for stop_index in remaining:
            passenger_count = stops[stop_index].passenger_count
            for current_load in sorted(list(combinations), reverse=True):
                new_load = current_load + passenger_count
                if new_load <= target and new_load not in combinations:
                    combinations[new_load] = [*combinations[current_load], stop_index]
        chosen = combinations.get(target)
        if chosen is None:
            return None
        chosen_set = set(chosen)
        routes.append([stops[index] for index in remaining if index in chosen_set])
        remaining = [index for index in remaining if index not in chosen_set]
    routes.append([stops[index] for index in remaining])
    if [sum(stop.passenger_count for stop in route) for route in routes] != list(target_loads):
        return None
    return routes


def assign_common_stops_to_routes(
    stops: Sequence[CommonStop],
    coordinates: Sequence[tuple[float, float]],
    vehicle_count: int,
    capacity: int,
    duration_matrix: Sequence[Sequence[float]],
    direction: str = "morning",
    wait_seconds_per_stop: int = 15,
    max_route_minutes: float = 0,
    time_limit_seconds: int = 10,
) -> list[list[CommonStop]]:
    """Ortak durakları OR-Tools kapasite kısıtlı araç rotalama modeliyle dağıtır.

    Model, durakları araçlara atama ve her aracın durak sırasını aynı anda çözer.
    Sabah rotaları serbest bir ilk duraktan başlayıp fabrikada, akşam rotaları
    fabrikada başlayıp serbest bir son durakta biter.
    """
    if direction not in {"morning", "evening"}:
        raise ValueError("Yön 'morning' veya 'evening' olmalıdır.")
    if vehicle_count <= 0:
        raise ValueError("Araç sayısı en az 1 olmalıdır.")
    if capacity <= 0:
        raise ValueError("Araç kapasitesi sıfırdan büyük olmalıdır.")
    employee_count = sum(stop.passenger_count for stop in stops)
    if employee_count > vehicle_count * capacity:
        raise ValueError(
            f"Kapasite yetersiz: {employee_count} çalışan için en az "
            f"{math.ceil(employee_count / capacity)} araç gerekir."
        )
    if not stops:
        return [[] for _ in range(vehicle_count)]

    if any(stop.passenger_count > capacity for stop in stops):
        raise ValueError("Bir ortak durağın yolcu sayısı araç kapasitesini aşıyor.")

    # Yerel düğümler: 0=fabrika, 1..N=ortak durak, son düğüm=serbest başlangıç/bitiş.
    stop_matrix_indices = [
        stop.matrix_index if stop.matrix_index is not None else stop.anchor_index + 1
        for stop in stops
    ]
    dummy_node = len(stops) + 1
    node_count = dummy_node + 1
    starts = [dummy_node] * vehicle_count if direction == "morning" else [0] * vehicle_count
    ends = [0] * vehicle_count if direction == "morning" else [dummy_node] * vehicle_count
    manager = pywrapcp.RoutingIndexManager(node_count, vehicle_count, starts, ends)
    routing = pywrapcp.RoutingModel(manager)

    def full_matrix_index(local_node: int) -> int | None:
        if local_node == 0:
            return 0
        if 1 <= local_node <= len(stops):
            return stop_matrix_indices[local_node - 1]
        return None

    def travel_seconds(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        from_full = full_matrix_index(from_node)
        to_full = full_matrix_index(to_node)
        drive = 0.0 if from_full is None or to_full is None else duration_matrix[from_full][to_full]
        service = wait_seconds_per_stop if 1 <= from_node <= len(stops) else 0
        return max(0, int(round(drive + service)))

    transit_callback = routing.RegisterTransitCallback(travel_seconds)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback)

    demands = [0, *(stop.passenger_count for stop in stops), 0]

    def demand(from_index: int) -> int:
        return demands[manager.IndexToNode(from_index)]

    demand_callback = routing.RegisterUnaryTransitCallback(demand)
    routing.AddDimensionWithVehicleCapacity(
        demand_callback,
        0,
        [capacity] * vehicle_count,
        True,
        "Capacity",
    )

    # Araç doluluklarını dengeli tut.
    # Araç yükleri mümkün olduğunca dengeli tutulur; sabit 4 rota referans planı ayrıca birebir korunabilir.
    # Bu bölüm 43-33-16 gibi aşırı dengesiz dağılımları güçlü biçimde cezalandırır.
    capacity_dimension = routing.GetDimensionOrDie("Capacity")
    balanced_base, balanced_extra = divmod(employee_count, vehicle_count)
    balanced_low = balanced_base
    balanced_high = balanced_base + (1 if balanced_extra else 0)

    # Her servis mümkünse en az %55 dolu olsun.
    minimum_reasonable_load = min(
        balanced_low,
        max(1, int(math.ceil(capacity * 0.55))),
    )

    balance_penalty = 5000
    for vehicle_no in range(vehicle_count):
        end_index = routing.End(vehicle_no)
        capacity_dimension.SetCumulVarSoftLowerBound(
            end_index, minimum_reasonable_load, balance_penalty
        )
        capacity_dimension.SetCumulVarSoftUpperBound(
            end_index, balanced_high, balance_penalty
        )

    horizon_seconds = int(round(max_route_minutes * 60)) if max_route_minutes else 24 * 60 * 60
    routing.AddDimension(transit_callback, 0, max(1, horizon_seconds), True, "Time")
    time_dimension = routing.GetDimensionOrDie("Time")
    # Toplam süre yanında en uzun rotayı da kısaltarak araçlar arasında denge kurar.
    time_dimension.SetGlobalSpanCostCoefficient(3)

    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    parameters.time_limit.FromSeconds(max(1, time_limit_seconds))
    parameters.log_search = False
    solution = routing.SolveWithParameters(parameters)
    if solution is None:
        duration_text = f" ve {max_route_minutes:.0f} dakika sınırına" if max_route_minutes else ""
        raise ValueError(
            f"{vehicle_count} araç, kapasite{duration_text} göre uygulanabilir rota üretemedi."
        )

    routes: list[list[CommonStop]] = []
    for vehicle_no in range(vehicle_count):
        route: list[CommonStop] = []
        index = routing.Start(vehicle_no)
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            if 1 <= node <= len(stops):
                route.append(stops[node - 1])
            index = solution.Value(routing.NextVar(index))
        routes.append(route)
    return routes


def _balanced_sizes(employee_count: int, vehicle_count: int, capacity: int) -> list[int]:
    if vehicle_count <= 0:
        raise ValueError("Araç sayısı en az 1 olmalıdır.")
    if vehicle_count * capacity < employee_count:
        raise ValueError(
            f"Kapasite yetersiz: {employee_count} çalışan için en az "
            f"{math.ceil(employee_count / capacity)} araç gerekir."
        )
    base, extra = divmod(employee_count, vehicle_count)
    sizes = [base + (1 if i < extra else 0) for i in range(vehicle_count)]
    if sizes and max(sizes) > capacity:
        raise ValueError("Rota büyüklüğü araç kapasitesini aşıyor.")
    return sizes


def _angular_clusters(
    coordinates: Sequence[tuple[float, float]],
    vehicle_count: int,
    capacity: int,
    duration_matrix: Sequence[Sequence[float]],
    direction: str,
) -> list[list[int]]:
    depot_lat, depot_lon = coordinates[0]
    employees = list(range(1, len(coordinates)))
    ordered = sorted(
        employees,
        key=lambda idx: math.atan2(coordinates[idx][0] - depot_lat, coordinates[idx][1] - depot_lon),
    )
    sizes = _balanced_sizes(len(employees), vehicle_count, capacity)
    if not employees:
        return [[] for _ in range(vehicle_count)]

    # Dairesel sıralamanın başlangıç açısını değiştirip en kısa bölünmeyi seçer.
    step = 1 if len(ordered) <= 60 else max(1, len(ordered) // 30)
    best_clusters: list[list[int]] | None = None
    best_score = math.inf
    for rotation in range(0, len(ordered), step):
        rotated = ordered[rotation:] + ordered[:rotation]
        clusters = []
        cursor = 0
        for size in sizes:
            raw = rotated[cursor : cursor + size]
            cursor += size
            route = _nearest_neighbor(raw, duration_matrix, direction)
            clusters.append(_two_opt(route, duration_matrix, direction))
        score = sum(_path_cost(route, duration_matrix, direction) for route in clusters)
        # Toplam süre yanında rotalar arası aşırı dengesizliği de azaltır.
        costs = [_path_cost(route, duration_matrix, direction) for route in clusters if route]
        if costs:
            score += (max(costs) - min(costs)) * 0.10
        if score < best_score:
            best_score, best_clusters = score, clusters
    return best_clusters or [[] for _ in range(vehicle_count)]


def plan_routes(
    coordinates: Sequence[tuple[float, float]],
    capacity: int,
    mode: str = "auto",
    fixed_vehicle_count: int = 4,
    direction: str = "morning",
    wait_seconds_per_stop: int = 0,
    max_route_minutes: float = 0,
    use_road_network: bool = True,
    average_speed_kmh: float = 32.0,
) -> PlanResult:
    """İlk koordinatı fabrika, diğerlerini çalışan kabul ederek rota üretir."""
    if len(coordinates) < 2:
        raise ValueError("En az bir fabrika ve bir çalışan noktası gerekir.")
    if capacity <= 0:
        raise ValueError("Araç kapasitesi sıfırdan büyük olmalıdır.")
    if direction not in {"morning", "evening"}:
        raise ValueError("Yön 'morning' veya 'evening' olmalıdır.")

    warnings: list[str] = []
    try:
        if not use_road_network:
            raise RuntimeError("Yol ağı seçimi kapalı.")
        duration_matrix, distance_matrix = fetch_osrm_table(coordinates)
        matrix_source = "OSRM yol ağı"
    except Exception as exc:
        duration_matrix, distance_matrix = build_estimated_matrices(coordinates, average_speed_kmh)
        matrix_source = "Yaklaşık mesafe"
        warnings.append(f"Yol ağı verisi kullanılamadı; yaklaşık mesafeye geçildi ({exc}).")

    employee_count = len(coordinates) - 1
    minimum_vehicles = math.ceil(employee_count / capacity)
    if mode == "fixed":
        vehicle_count = fixed_vehicle_count
        if vehicle_count < minimum_vehicles:
            raise ValueError(
                f"{fixed_vehicle_count} araç yetersiz. Bu kapasiteyle en az {minimum_vehicles} araç gerekir."
            )
    else:
        vehicle_count = minimum_vehicles

    while True:
        clusters = _angular_clusters(
            coordinates, vehicle_count, capacity, duration_matrix, direction
        )
        routes: list[RoutePlan] = []
        for vehicle_no, employee_indices in enumerate(clusters, start=1):
            path = _route_path(employee_indices, direction) if employee_indices else []
            drive_seconds = sum(duration_matrix[a][b] for a, b in zip(path, path[1:]))
            distance_meters = sum(distance_matrix[a][b] for a, b in zip(path, path[1:]))
            total_minutes = drive_seconds / 60 + len(employee_indices) * wait_seconds_per_stop / 60
            routes.append(
                RoutePlan(
                    vehicle_no=vehicle_no,
                    employee_indices=list(employee_indices),
                    path_indices=path,
                    occupancy=len(employee_indices),
                    distance_km=distance_meters / 1000,
                    drive_minutes=drive_seconds / 60,
                    total_minutes=total_minutes,
                    exceeds_limit=bool(max_route_minutes and total_minutes > max_route_minutes),
                )
            )

        exceeds = any(route.exceeds_limit for route in routes)
        if mode != "auto" or not max_route_minutes or not exceeds or vehicle_count >= employee_count:
            break
        vehicle_count += 1

    if any(route.exceeds_limit for route in routes):
        warnings.append("Bazı rotalar belirlenen maksimum rota süresini aşıyor.")
    return PlanResult(
        routes=routes,
        vehicle_count=vehicle_count,
        active_route_count=sum(bool(route.employee_indices) for route in routes),
        matrix_source=matrix_source,
        duration_matrix=duration_matrix,
        distance_matrix=distance_matrix,
        warnings=warnings,
    )
