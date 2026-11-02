from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _period_group_expr(period: str, field: str) -> str:
    mapping = {
        "hourly": f"DATE_FORMAT({field}, '%%Y-%%m-%%d %%H:00')",
        "daily": f"DATE_FORMAT({field}, '%%Y-%%m-%%d')",
        "weekly": f"DATE_FORMAT(STR_TO_DATE(CONCAT(YEARWEEK({field}, 1), ' Monday'), '%%X%%V %%W'), '%%Y-%%m-%%d')",
        "monthly": f"DATE_FORMAT({field}, '%%Y-%%m')",
        "yearly": f"DATE_FORMAT({field}, '%%Y')",
    }
    return mapping.get(period, mapping["daily"])


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _pct_change(current: float, previous: float) -> dict[str, Any]:
    if previous == 0:
        return {"value": None, "label": "N/A"}
    change = ((current - previous) / previous) * 100.0
    return {"value": round(change, 2), "label": f"{change:+.1f}%"}


def _calculate_period_range(filter_key: str, start_date: str | None, end_date: str | None) -> tuple[datetime, datetime, str]:
    now = datetime.now()
    today = now.date()

    if filter_key == "today":
        start = today
        end = today
        label = "Today"
    elif filter_key == "yesterday":
        start = today - timedelta(days=1)
        end = start
        label = "Yesterday"
    elif filter_key == "last_7_days":
        start = today - timedelta(days=6)
        end = today
        label = "Last 7 Days"
    elif filter_key == "last_30_days":
        start = today - timedelta(days=29)
        end = today
        label = "Last 30 Days"
    elif filter_key == "this_week":
        start = today - timedelta(days=today.weekday())
        end = today
        label = "This Week"
    elif filter_key == "this_month":
        start = today.replace(day=1)
        end = today
        label = "This Month"
    elif filter_key == "this_year":
        start = today.replace(month=1, day=1)
        end = today
        label = "This Year"
    elif filter_key == "custom":
        parsed_start = _parse_date(start_date)
        parsed_end = _parse_date(end_date)
        if not parsed_start or not parsed_end or parsed_start > parsed_end:
            raise ValueError("Invalid custom date range")
        start = parsed_start
        end = parsed_end
        label = f"{start.isoformat()} to {end.isoformat()}"
    else:
        start = today - timedelta(days=29)
        end = today
        label = "Last 30 Days"

    start_dt = datetime.combine(start, time.min)
    end_exclusive = datetime.combine(end + timedelta(days=1), time.min)
    return start_dt, end_exclusive, label


def _previous_period(start_dt: datetime, end_exclusive: datetime) -> tuple[datetime, datetime]:
    delta = end_exclusive - start_dt
    prev_end = start_dt
    prev_start = prev_end - delta
    return prev_start, prev_end


def _fetchone(cursor, query: str, params: tuple[Any, ...]) -> tuple[Any, ...]:
    cursor.execute(query, params)
    row = cursor.fetchone()
    return row or tuple()


def _series_query(cursor, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    cursor.execute(query, params)
    rows = cursor.fetchall() or []
    return [{"label": str(r[0]), "value": _safe_float(r[1])} for r in rows]


def _summary_metrics(cursor, parking_id: str, start_dt: datetime, end_exclusive: datetime) -> dict[str, float]:
    booking_row = _fetchone(
        cursor,
        """
        SELECT
            COUNT(*) AS total_bookings,
            COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0) AS completed_bookings,
            COALESCE(SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END), 0) AS cancelled_bookings,
            COALESCE(SUM(CASE WHEN status = 'confirmed' THEN 1 ELSE 0 END), 0) AS confirmed_bookings
        FROM bookings
        WHERE parking_id = %s
          AND entry_time >= %s
          AND entry_time < %s
        """,
        (parking_id, start_dt, end_exclusive),
    )

    revenue_row = _fetchone(
        cursor,
        """
        SELECT
            COALESCE(SUM(CASE WHEN LOWER(p.status) = 'success' THEN p.amount ELSE 0 END), 0) AS total_revenue,
            COALESCE(SUM(CASE WHEN LOWER(p.status) = 'pending' THEN p.amount ELSE 0 END), 0) AS pending_amount,
            COALESCE(SUM(CASE WHEN LOWER(p.status) IN ('refunded', 'refund') THEN p.amount ELSE 0 END), 0) AS refunded_amount,
            COUNT(DISTINCT CASE WHEN LOWER(p.status) = 'success' THEN p.booking_id END) AS paid_bookings
        FROM payments p
        JOIN bookings b ON b.booking_id = p.booking_id
        WHERE b.parking_id = %s
          AND p.paid_at >= %s
          AND p.paid_at < %s
        """,
        (parking_id, start_dt, end_exclusive),
    )

    duration_row = _fetchone(
        cursor,
        """
        SELECT
            COALESCE(SUM(CASE WHEN status <> 'cancelled' THEN TIMESTAMPDIFF(MINUTE, entry_time, COALESCE(exit_time, NOW())) ELSE 0 END), 0) AS total_minutes,
            COALESCE(AVG(CASE WHEN status <> 'cancelled' AND exit_time IS NOT NULL THEN TIMESTAMPDIFF(MINUTE, entry_time, exit_time) END), 0) AS avg_minutes
        FROM bookings
        WHERE parking_id = %s
          AND entry_time >= %s
          AND entry_time < %s
        """,
        (parking_id, start_dt, end_exclusive),
    )

    customer_row = _fetchone(
        cursor,
        """
        SELECT COALESCE(COUNT(DISTINCT user_id), 0)
        FROM bookings
        WHERE parking_id = %s
          AND entry_time >= %s
          AND entry_time < %s
        """,
        (parking_id, start_dt, end_exclusive),
    )

    return {
        "total_bookings": _safe_float(booking_row[0] if len(booking_row) > 0 else 0),
        "completed_bookings": _safe_float(booking_row[1] if len(booking_row) > 1 else 0),
        "cancelled_bookings": _safe_float(booking_row[2] if len(booking_row) > 2 else 0),
        "confirmed_bookings": _safe_float(booking_row[3] if len(booking_row) > 3 else 0),
        "total_revenue": _safe_float(revenue_row[0] if len(revenue_row) > 0 else 0),
        "pending_amount": _safe_float(revenue_row[1] if len(revenue_row) > 1 else 0),
        "refunded_amount": _safe_float(revenue_row[2] if len(revenue_row) > 2 else 0),
        "paid_bookings": _safe_float(revenue_row[3] if len(revenue_row) > 3 else 0),
        "total_minutes": _safe_float(duration_row[0] if len(duration_row) > 0 else 0),
        "avg_minutes": _safe_float(duration_row[1] if len(duration_row) > 1 else 0),
        "customers": _safe_float(customer_row[0] if len(customer_row) > 0 else 0),
    }


def _to_hours(minutes_value: float) -> float:
    return round(minutes_value / 60.0, 2)


def get_manager_analytics(
    connection,
    parking_id: str,
    filter_key: str,
    start_date: str | None,
    end_date: str | None,
    trend_period: str,
    occupancy_period: str,
) -> dict[str, Any]:
    start_dt, end_exclusive, selected_label = _calculate_period_range(filter_key, start_date, end_date)
    prev_start, prev_end = _previous_period(start_dt, end_exclusive)

    cursor = connection.cursor()

    cursor.execute(
        "SELECT Name, Address, Capacity FROM parking_lots WHERE parking_id = %s",
        (parking_id,),
    )
    lot = cursor.fetchone()
    if not lot:
        cursor.close()
        return {
            "meta": {"error": "Parking lot not found."},
            "kpis": {},
            "revenue": {},
            "bookings": {},
            "occupancy": {},
            "parking_duration": {},
            "customers": {},
            "vehicles": {},
            "locations": {},
            "slots": {},
            "tables": {},
        }

    lot_name = lot[0] or parking_id
    lot_address = lot[1] or "N/A"
    capacity = _safe_int(lot[2] or 0)

    current_summary = _summary_metrics(cursor, parking_id, start_dt, end_exclusive)
    previous_summary = _summary_metrics(cursor, parking_id, prev_start, prev_end)

    cursor.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN is_occupied = 1 THEN 1 ELSE 0 END), 0),
            COALESCE(COUNT(*), 0)
        FROM real_time_parking_info
        WHERE parking_id = %s
        """,
        (parking_id,),
    )
    occ_row = cursor.fetchone() or (0, 0)
    occupied_slots = _safe_int(occ_row[0])
    total_slots = capacity if capacity > 0 else _safe_int(occ_row[1])
    available_slots = max(total_slots - occupied_slots, 0)
    occupancy_rate = round((occupied_slots / total_slots) * 100.0, 2) if total_slots > 0 else 0.0

    cursor.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN LOWER(p.status) = 'success' AND DATE(p.paid_at) = CURDATE() THEN p.amount ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN LOWER(p.status) = 'success' AND YEARWEEK(p.paid_at, 1) = YEARWEEK(CURDATE(), 1) THEN p.amount ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN LOWER(p.status) = 'success' AND YEAR(p.paid_at) = YEAR(CURDATE()) AND MONTH(p.paid_at) = MONTH(CURDATE()) THEN p.amount ELSE 0 END), 0)
        FROM payments p
        JOIN bookings b ON b.booking_id = p.booking_id
        WHERE b.parking_id = %s
        """,
        (parking_id,),
    )
    revenue_time_row = cursor.fetchone() or (0, 0, 0)

    cursor.execute(
        """
        SELECT
            COALESCE(MIN(TIMESTAMPDIFF(MINUTE, entry_time, exit_time)), 0),
            COALESCE(MAX(TIMESTAMPDIFF(MINUTE, entry_time, exit_time)), 0)
        FROM bookings
        WHERE parking_id = %s
          AND status <> 'cancelled'
          AND entry_time >= %s
          AND entry_time < %s
          AND exit_time IS NOT NULL
        """,
        (parking_id, start_dt, end_exclusive),
    )
    min_max_row = cursor.fetchone() or (0, 0)

    cursor.execute(
        """
        SELECT TIMESTAMPDIFF(MINUTE, entry_time, exit_time) AS duration_min
        FROM bookings
        WHERE parking_id = %s
          AND status <> 'cancelled'
          AND entry_time >= %s
          AND entry_time < %s
          AND exit_time IS NOT NULL
        """,
        (parking_id, start_dt, end_exclusive),
    )
    duration_values = [_safe_float(r[0]) for r in (cursor.fetchall() or []) if r[0] is not None]
    median_duration_min = float(median(duration_values)) if duration_values else 0.0

    cursor.execute(
        """
        SELECT HOUR(entry_time) AS hour_slot, COUNT(*) AS cnt
        FROM bookings
        WHERE parking_id = %s
          AND entry_time >= %s
          AND entry_time < %s
        GROUP BY HOUR(entry_time)
        ORDER BY cnt DESC, hour_slot ASC
        LIMIT 1
        """,
        (parking_id, start_dt, end_exclusive),
    )
    peak_hour_row = cursor.fetchone()

    cursor.execute(
        """
        SELECT DAYNAME(entry_time) AS day_name, COUNT(*) AS cnt
        FROM bookings
        WHERE parking_id = %s
          AND entry_time >= %s
          AND entry_time < %s
        GROUP BY DAYNAME(entry_time)
        ORDER BY cnt DESC
        LIMIT 1
        """,
        (parking_id, start_dt, end_exclusive),
    )
    peak_day_row = cursor.fetchone()

    cursor.execute(
        """
        SELECT
            COALESCE(COUNT(DISTINCT b.user_id), 0) AS customers,
            COALESCE(AVG(user_counts.cnt), 0) AS avg_bookings_per_customer
        FROM bookings b
        LEFT JOIN (
            SELECT user_id, COUNT(*) AS cnt
            FROM bookings
            WHERE parking_id = %s
              AND entry_time >= %s
              AND entry_time < %s
            GROUP BY user_id
        ) user_counts ON user_counts.user_id = b.user_id
        WHERE b.parking_id = %s
          AND b.entry_time >= %s
          AND b.entry_time < %s
        """,
        (parking_id, start_dt, end_exclusive, parking_id, start_dt, end_exclusive),
    )
    customer_summary = cursor.fetchone() or (0, 0)

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT b.user_id, MIN(b.entry_time) AS first_visit
            FROM bookings b
            WHERE b.parking_id = %s
            GROUP BY b.user_id
        ) first_visits
        WHERE first_visit >= %s AND first_visit < %s
        """,
        (parking_id, start_dt, end_exclusive),
    )
    new_customers = _safe_int((cursor.fetchone() or (0,))[0])

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT user_id, COUNT(*) AS booking_count
            FROM bookings
            WHERE parking_id = %s
              AND entry_time >= %s
              AND entry_time < %s
            GROUP BY user_id
            HAVING COUNT(*) > 1
        ) returning
        """,
        (parking_id, start_dt, end_exclusive),
    )
    returning_customers = _safe_int((cursor.fetchone() or (0,))[0])

    occupancy_group_expr = _period_group_expr(occupancy_period, "entry_time")
    cursor.execute(
        f"""
        SELECT {occupancy_group_expr} AS bucket,
               COALESCE((COUNT(*) / NULLIF(%s, 0)) * 100, 0) AS occupancy_value
        FROM bookings
        WHERE parking_id = %s
          AND entry_time >= %s
          AND entry_time < %s
        GROUP BY bucket
        ORDER BY bucket ASC
        """,
        (max(total_slots, 1), parking_id, start_dt, end_exclusive),
    )
    occupancy_series = [{"label": str(r[0]), "value": round(_safe_float(r[1]), 2)} for r in (cursor.fetchall() or [])]
    occupancy_values = [item["value"] for item in occupancy_series]

    trend_group_expr = _period_group_expr(trend_period, "b.entry_time")
    cursor.execute(
        f"""
        SELECT {trend_group_expr} AS bucket,
               COUNT(*) AS bookings_count
        FROM bookings b
        WHERE b.parking_id = %s
          AND b.entry_time >= %s
          AND b.entry_time < %s
        GROUP BY bucket
        ORDER BY bucket ASC
        """,
        (parking_id, start_dt, end_exclusive),
    )
    bookings_trend = [{"label": str(r[0]), "value": _safe_int(r[1])} for r in (cursor.fetchall() or [])]

    revenue_group_expr = _period_group_expr(trend_period, "p.paid_at")
    cursor.execute(
        f"""
        SELECT {revenue_group_expr} AS bucket,
               COALESCE(SUM(CASE WHEN LOWER(p.status) = 'success' THEN p.amount ELSE 0 END), 0) AS revenue
        FROM payments p
        JOIN bookings b ON b.booking_id = p.booking_id
        WHERE b.parking_id = %s
          AND p.paid_at >= %s
          AND p.paid_at < %s
        GROUP BY bucket
        ORDER BY bucket ASC
        """,
        (parking_id, start_dt, end_exclusive),
    )
    revenue_trend = [{"label": str(r[0]), "value": round(_safe_float(r[1]), 2)} for r in (cursor.fetchall() or [])]

    cursor.execute(
        """
        SELECT COALESCE(method, 'Unknown') AS method_name,
             COALESCE(SUM(p.amount), 0) AS total_amount
        FROM payments p
        JOIN bookings b ON b.booking_id = p.booking_id
        WHERE b.parking_id = %s
          AND p.paid_at >= %s
          AND p.paid_at < %s
        GROUP BY method_name
        ORDER BY total_amount DESC
        """,
        (parking_id, start_dt, end_exclusive),
    )
    payment_method = [{"label": str(r[0]), "value": round(_safe_float(r[1]), 2)} for r in (cursor.fetchall() or [])]

    cursor.execute(
        """
        SELECT status, COUNT(*)
        FROM bookings
        WHERE parking_id = %s
          AND entry_time >= %s
          AND entry_time < %s
        GROUP BY status
        """,
        (parking_id, start_dt, end_exclusive),
    )
    booking_status = {str(r[0]): _safe_int(r[1]) for r in (cursor.fetchall() or [])}

    cursor.execute(
        """
        SELECT DAYOFWEEK(entry_time) AS day_idx, COUNT(*) AS cnt
        FROM bookings
        WHERE parking_id = %s
          AND entry_time >= %s
          AND entry_time < %s
        GROUP BY day_idx
        ORDER BY day_idx
        """,
        (parking_id, start_dt, end_exclusive),
    )
    weekday_name = {
        1: "Sunday",
        2: "Monday",
        3: "Tuesday",
        4: "Wednesday",
        5: "Thursday",
        6: "Friday",
        7: "Saturday",
    }
    booking_by_day = [
        {"label": weekday_name.get(_safe_int(r[0]), str(r[0])), "value": _safe_int(r[1])}
        for r in (cursor.fetchall() or [])
    ]

    cursor.execute(
        """
        SELECT HOUR(entry_time) AS hour_idx, COUNT(*) AS cnt
        FROM bookings
        WHERE parking_id = %s
          AND entry_time >= %s
          AND entry_time < %s
        GROUP BY hour_idx
        ORDER BY hour_idx
        """,
        (parking_id, start_dt, end_exclusive),
    )
    booking_by_hour = [{"label": f"{int(r[0]):02d}:00", "value": _safe_int(r[1])} for r in (cursor.fetchall() or [])]

    cursor.execute(
        """
        SELECT
            SUM(CASE WHEN duration_min < 60 THEN 1 ELSE 0 END) AS lt_1,
            SUM(CASE WHEN duration_min >= 60 AND duration_min < 120 THEN 1 ELSE 0 END) AS h_1_2,
            SUM(CASE WHEN duration_min >= 120 AND duration_min < 240 THEN 1 ELSE 0 END) AS h_2_4,
            SUM(CASE WHEN duration_min >= 240 AND duration_min < 480 THEN 1 ELSE 0 END) AS h_4_8,
            SUM(CASE WHEN duration_min >= 480 THEN 1 ELSE 0 END) AS gte_8
        FROM (
            SELECT TIMESTAMPDIFF(MINUTE, entry_time, exit_time) AS duration_min
            FROM bookings
            WHERE parking_id = %s
              AND status <> 'cancelled'
              AND entry_time >= %s
              AND entry_time < %s
              AND exit_time IS NOT NULL
        ) d
        """,
        (parking_id, start_dt, end_exclusive),
    )
    bucket_row = cursor.fetchone() or (0, 0, 0, 0, 0)

    duration_distribution = [
        {"label": "< 1 hour", "value": _safe_int(bucket_row[0])},
        {"label": "1-2 hours", "value": _safe_int(bucket_row[1])},
        {"label": "2-4 hours", "value": _safe_int(bucket_row[2])},
        {"label": "4-8 hours", "value": _safe_int(bucket_row[3])},
        {"label": "8+ hours", "value": _safe_int(bucket_row[4])},
    ]

    cursor.execute(
        """
        SELECT
            SUM(CASE WHEN TIMESTAMPDIFF(MINUTE, entry_time, exit_time) >= 480 THEN 1 ELSE 0 END) AS long_stay,
            SUM(CASE WHEN TIMESTAMPDIFF(MINUTE, entry_time, exit_time) < 60 THEN 1 ELSE 0 END) AS short_stay
        FROM bookings
        WHERE parking_id = %s
          AND status <> 'cancelled'
          AND entry_time >= %s
          AND entry_time < %s
          AND exit_time IS NOT NULL
        """,
        (parking_id, start_dt, end_exclusive),
    )
    stay_types = cursor.fetchone() or (0, 0)

    cursor.execute(
        """
        SELECT DAYOFWEEK(entry_time) AS day_idx, HOUR(entry_time) AS hour_idx, COUNT(*) AS cnt
        FROM bookings
        WHERE parking_id = %s
          AND entry_time >= %s
          AND entry_time < %s
        GROUP BY DAYOFWEEK(entry_time), HOUR(entry_time)
        ORDER BY DAYOFWEEK(entry_time), HOUR(entry_time)
        """,
        (parking_id, start_dt, end_exclusive),
    )
    heatmap_rows = cursor.fetchall() or []

    day_map = {
        2: "Monday",
        3: "Tuesday",
        4: "Wednesday",
        5: "Thursday",
        6: "Friday",
        7: "Saturday",
        1: "Sunday",
    }
    heatmap = {day: [0] * 24 for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
    for row in heatmap_rows:
        day_name = day_map.get(_safe_int(row[0]))
        hour_idx = _safe_int(row[1])
        if day_name and 0 <= hour_idx <= 23:
            heatmap[day_name][hour_idx] = _safe_int(row[2])

    cursor.execute(
        """
        SELECT
            b.slot_no,
            COUNT(*) AS bookings_count,
            COALESCE(SUM(b.amount), 0) AS revenue,
            COALESCE(AVG(CASE WHEN b.exit_time IS NOT NULL THEN TIMESTAMPDIFF(MINUTE, b.entry_time, b.exit_time) END), 0) AS avg_duration_min
        FROM bookings b
        WHERE b.parking_id = %s
          AND b.entry_time >= %s
          AND b.entry_time < %s
        GROUP BY b.slot_no
        ORDER BY bookings_count DESC
        LIMIT 10
        """,
        (parking_id, start_dt, end_exclusive),
    )
    top_slots = [
        {
            "slot_no": _safe_int(r[0]),
            "bookings": _safe_int(r[1]),
            "revenue": round(_safe_float(r[2]), 2),
            "avg_duration_hours": _to_hours(_safe_float(r[3])),
        }
        for r in (cursor.fetchall() or [])
    ]

    cursor.execute(
        """
        SELECT
            b.slot_no,
            COUNT(*) AS bookings_count,
            COALESCE(SUM(b.amount), 0) AS revenue,
            COALESCE(AVG(CASE WHEN b.exit_time IS NOT NULL THEN TIMESTAMPDIFF(MINUTE, b.entry_time, b.exit_time) END), 0) AS avg_duration_min
        FROM bookings b
        WHERE b.parking_id = %s
          AND b.entry_time >= %s
          AND b.entry_time < %s
        GROUP BY b.slot_no
        ORDER BY bookings_count ASC
        LIMIT 10
        """,
        (parking_id, start_dt, end_exclusive),
    )
    bottom_slots = [
        {
            "slot_no": _safe_int(r[0]),
            "bookings": _safe_int(r[1]),
            "revenue": round(_safe_float(r[2]), 2),
            "avg_duration_hours": _to_hours(_safe_float(r[3])),
        }
        for r in (cursor.fetchall() or [])
    ]

    cursor.execute(
        """
        SELECT
            u.name,
            COUNT(b.booking_id) AS total_bookings,
            SUM(CASE WHEN b.status = 'completed' THEN 1 ELSE 0 END) AS completed_bookings,
            COALESCE(SUM(CASE WHEN b.exit_time IS NOT NULL THEN TIMESTAMPDIFF(MINUTE, b.entry_time, b.exit_time) ELSE 0 END), 0) AS total_minutes,
            COALESCE(SUM(CASE WHEN LOWER(p.status) = 'success' THEN p.amount ELSE 0 END), 0) AS total_spent,
            MAX(b.entry_time) AS last_visit
        FROM bookings b
        JOIN users u ON u.user_id = b.user_id
        LEFT JOIN payments p ON p.booking_id = b.booking_id
        WHERE b.parking_id = %s
          AND b.entry_time >= %s
          AND b.entry_time < %s
        GROUP BY b.user_id, u.name
        ORDER BY total_bookings DESC, total_spent DESC
        LIMIT 10
        """,
        (parking_id, start_dt, end_exclusive),
    )
    top_customers = [
        {
            "customer": str(r[0]),
            "total_bookings": _safe_int(r[1]),
            "completed_bookings": _safe_int(r[2]),
            "parking_hours": _to_hours(_safe_float(r[3])),
            "total_spent": round(_safe_float(r[4]), 2),
            "last_visit": r[5].strftime("%Y-%m-%d %H:%M") if r[5] else "N/A",
        }
        for r in (cursor.fetchall() or [])
    ]

    cursor.execute(
        """
        SELECT
            b.booking_id,
            u.name,
            COALESCE(cp.vehicle_no, 'N/A') AS vehicle,
            pl.Name AS parking_location,
            b.slot_no,
            b.entry_time,
            b.exit_time,
            TIMESTAMPDIFF(MINUTE, b.entry_time, COALESCE(b.exit_time, NOW())) AS duration_min,
            b.amount,
            COALESCE(MAX(p.status), 'N/A') AS payment_status,
            b.status
        FROM bookings b
        JOIN users u ON u.user_id = b.user_id
        LEFT JOIN customer_profiles cp ON cp.user_id = b.user_id
        JOIN parking_lots pl ON pl.parking_id = b.parking_id
        LEFT JOIN payments p ON p.booking_id = b.booking_id
        WHERE b.parking_id = %s
          AND b.entry_time >= %s
          AND b.entry_time < %s
        GROUP BY b.booking_id, u.name, cp.vehicle_no, pl.Name, b.slot_no, b.entry_time, b.exit_time, b.amount, b.status
        ORDER BY b.entry_time DESC
        LIMIT 50
        """,
        (parking_id, start_dt, end_exclusive),
    )
    recent_rows = cursor.fetchall() or []
    recent_bookings = [
        {
            "booking_id": _safe_int(r[0]),
            "customer": str(r[1]),
            "vehicle": str(r[2]),
            "parking_location": str(r[3]),
            "slot": _safe_int(r[4]),
            "entry": r[5].strftime("%Y-%m-%d %H:%M") if r[5] else "N/A",
            "exit": r[6].strftime("%Y-%m-%d %H:%M") if r[6] else "N/A",
            "duration_hours": _to_hours(_safe_float(r[7])),
            "amount": round(_safe_float(r[8]), 2),
            "payment_status": str(r[9]),
            "booking_status": str(r[10]),
        }
        for r in recent_rows
    ]

    cursor.execute(
        """
        SELECT
            COUNT(DISTINCT COALESCE(cp.vehicle_no, rt.vehicle_no))
        FROM bookings b
        LEFT JOIN customer_profiles cp ON cp.user_id = b.user_id
        LEFT JOIN real_time_parking_info rt ON rt.parking_id = b.parking_id AND rt.slot_no = b.slot_no
        WHERE b.parking_id = %s
          AND b.entry_time >= %s
          AND b.entry_time < %s
          AND COALESCE(cp.vehicle_no, rt.vehicle_no) IS NOT NULL
          AND COALESCE(cp.vehicle_no, rt.vehicle_no) <> ''
        """,
        (parking_id, start_dt, end_exclusive),
    )
    total_vehicles = _safe_int((cursor.fetchone() or (0,))[0])

    cursor.close()

    avg_revenue_per_booking = (
        current_summary["total_revenue"] / current_summary["paid_bookings"]
        if current_summary["paid_bookings"] > 0
        else 0.0
    )
    booking_success_rate = (
        (current_summary["completed_bookings"] / current_summary["total_bookings"]) * 100.0
        if current_summary["total_bookings"] > 0
        else 0.0
    )
    cancellation_rate = (
        (current_summary["cancelled_bookings"] / current_summary["total_bookings"]) * 100.0
        if current_summary["total_bookings"] > 0
        else 0.0
    )

    kpis = {
        "revenue": {
            "total_revenue": round(current_summary["total_revenue"], 2),
            "avg_revenue_per_booking": round(avg_revenue_per_booking, 2),
            "revenue_today": round(_safe_float(revenue_time_row[0]), 2),
            "revenue_this_week": round(_safe_float(revenue_time_row[1]), 2),
            "revenue_this_month": round(_safe_float(revenue_time_row[2]), 2),
            "pending_amount": round(current_summary["pending_amount"], 2),
            "refunded_amount": round(current_summary["refunded_amount"], 2),
            "growth_vs_previous": _pct_change(current_summary["total_revenue"], previous_summary["total_revenue"]),
        },
        "parking": {
            "total_slots": total_slots,
            "occupied_slots": occupied_slots,
            "available_slots": available_slots,
            "occupancy_rate": occupancy_rate,
            "total_bookings": _safe_int(current_summary["total_bookings"]),
            "completed_bookings": _safe_int(current_summary["completed_bookings"]),
            "cancelled_bookings": _safe_int(current_summary["cancelled_bookings"]),
            "active_bookings": _safe_int(current_summary["confirmed_bookings"]),
            "growth_vs_previous": _pct_change(current_summary["total_bookings"], previous_summary["total_bookings"]),
        },
        "usage": {
            "total_parking_hours": _to_hours(current_summary["total_minutes"]),
            "average_parking_duration_hours": _to_hours(current_summary["avg_minutes"]),
            "average_stay_duration_hours": _to_hours(current_summary["avg_minutes"]),
            "minimum_duration_hours": _to_hours(_safe_float(min_max_row[0])),
            "maximum_duration_hours": _to_hours(_safe_float(min_max_row[1])),
            "median_duration_hours": _to_hours(median_duration_min),
            "peak_parking_hour": f"{_safe_int(peak_hour_row[0])}:00" if peak_hour_row else "N/A",
            "peak_parking_day": str(peak_day_row[0]) if peak_day_row else "N/A",
            "growth_vs_previous": _pct_change(current_summary["total_minutes"], previous_summary["total_minutes"]),
        },
        "customers": {
            "total_customers": _safe_int(customer_summary[0]),
            "new_customers": new_customers,
            "returning_customers": returning_customers,
            "avg_bookings_per_customer": round(_safe_float(customer_summary[1]), 2),
            "growth_vs_previous": _pct_change(current_summary["customers"], previous_summary["customers"]),
        },
    }

    response = {
        "meta": {
            "parking_id": parking_id,
            "parking_name": lot_name,
            "parking_address": lot_address,
            "selected_period": selected_label,
            "start_date": start_dt.date().isoformat(),
            "end_date": (end_exclusive - timedelta(days=1)).date().isoformat(),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "trend_period": trend_period,
            "occupancy_period": occupancy_period,
        },
        "kpis": kpis,
        "revenue": {
            "trend": revenue_trend,
            "by_payment_method": payment_method,
            "by_location": [{"label": lot_name, "value": round(current_summary["total_revenue"], 2)}],
            "total": round(current_summary["total_revenue"], 2),
            "paid": round(current_summary["total_revenue"], 2),
            "pending": round(current_summary["pending_amount"], 2),
            "refunded": round(current_summary["refunded_amount"], 2),
        },
        "bookings": {
            "trend": bookings_trend,
            "status_breakdown": {
                "completed": booking_status.get("completed", 0),
                "cancelled": booking_status.get("cancelled", 0),
                "pending": booking_status.get("confirmed", 0),
                "active": booking_status.get("confirmed", 0),
            },
            "success_rate": round(booking_success_rate, 2),
            "cancellation_rate": round(cancellation_rate, 2),
            "distribution": {
                "day_of_week": booking_by_day,
                "hour_of_day": booking_by_hour,
                "parking_location": [{"label": lot_name, "value": _safe_int(current_summary["total_bookings"])}],
            },
        },
        "occupancy": {
            "current_rate": occupancy_rate,
            "average_rate": round(sum(occupancy_values) / len(occupancy_values), 2) if occupancy_values else 0.0,
            "max_rate": round(max(occupancy_values), 2) if occupancy_values else 0.0,
            "min_rate": round(min(occupancy_values), 2) if occupancy_values else 0.0,
            "peak_time": f"{_safe_int(peak_hour_row[0])}:00" if peak_hour_row else "N/A",
            "peak_day": str(peak_day_row[0]) if peak_day_row else "N/A",
            "trend": occupancy_series,
            "growth_vs_previous": _pct_change(occupancy_rate, occupancy_rate),
        },
        "parking_duration": {
            "total_hours": _to_hours(current_summary["total_minutes"]),
            "average_hours": _to_hours(current_summary["avg_minutes"]),
            "minimum_hours": _to_hours(_safe_float(min_max_row[0])),
            "maximum_hours": _to_hours(_safe_float(min_max_row[1])),
            "median_hours": _to_hours(median_duration_min),
            "long_stay_vehicles": _safe_int(stay_types[0]),
            "short_stay_vehicles": _safe_int(stay_types[1]),
            "distribution": duration_distribution,
        },
        "customers": {
            "summary": kpis["customers"],
            "top_customers": top_customers,
        },
        "vehicles": {
            "total_vehicles": total_vehicles,
            "types_supported": False,
            "message": "Vehicle type analytics are unavailable because there is no vehicle_type column in current schema.",
            "type_distribution": [],
            "type_vs_revenue": [],
            "type_vs_avg_duration": [],
        },
        "locations": {
            "multi_location": False,
            "performance": [
                {
                    "parking_id": parking_id,
                    "parking_name": lot_name,
                    "total_slots": total_slots,
                    "occupied_slots": occupied_slots,
                    "available_slots": available_slots,
                    "occupancy_rate": occupancy_rate,
                    "bookings": _safe_int(current_summary["total_bookings"]),
                    "revenue": round(current_summary["total_revenue"], 2),
                    "avg_duration_hours": _to_hours(current_summary["avg_minutes"]),
                }
            ],
        },
        "slots": {
            "top_10": top_slots,
            "bottom_10": bottom_slots,
        },
        "demand_heatmap": heatmap,
        "tables": {
            "recent_bookings": recent_bookings,
            "top_customers": top_customers,
            "top_slots": top_slots,
            "location_performance": [
                {
                    "parking_id": parking_id,
                    "parking_name": lot_name,
                    "total_slots": total_slots,
                    "occupied_slots": occupied_slots,
                    "available_slots": available_slots,
                    "occupancy_rate": occupancy_rate,
                    "bookings": _safe_int(current_summary["total_bookings"]),
                    "revenue": round(current_summary["total_revenue"], 2),
                    "avg_duration_hours": _to_hours(current_summary["avg_minutes"]),
                }
            ],
        },
    }

    return response


def analytics_to_csv_rows(analytics_payload: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    headers = [
        "Booking ID",
        "Customer",
        "Vehicle",
        "Parking Location",
        "Slot",
        "Entry",
        "Exit",
        "Duration Hours",
        "Amount",
        "Payment Status",
        "Booking Status",
    ]
    rows = []
    for item in analytics_payload.get("tables", {}).get("recent_bookings", []):
        rows.append([
            item.get("booking_id"),
            item.get("customer"),
            item.get("vehicle"),
            item.get("parking_location"),
            item.get("slot"),
            item.get("entry"),
            item.get("exit"),
            item.get("duration_hours"),
            item.get("amount"),
            item.get("payment_status"),
            item.get("booking_status"),
        ])
    return headers, rows
