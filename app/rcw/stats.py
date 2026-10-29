"""Numbers for the Stats page (brief: matches per week, top routes, typical days included, median time-to-gone,
how often Hobart routes appear). Matches only by default; everything=True covers every stored listing."""
from . import db


def collect(c, everything: bool = False) -> dict:
    scope = "TRUE" if everything else "matched"
    seen = "first_seen" if everything else "COALESCE(matched_at, first_seen)"
    weeks = c.execute(f"""
        SELECT date_trunc('week', {seen} AT TIME ZONE 'Australia/Hobart')::date AS week, count(*) AS n,
               count(*) FILTER (WHERE boosted) AS starred
        FROM relocations WHERE {scope} AND dup_of IS NULL
        GROUP BY 1 ORDER BY 1 DESC LIMIT 26""").fetchall()
    routes = c.execute(f"""
        SELECT COALESCE(origin, '****') || ' → ' || COALESCE(destination, '****') AS route, count(*) AS n,
               round(avg(days_included)::numeric, 1) AS avg_days
        FROM relocations WHERE {scope} AND dup_of IS NULL
        GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 12""").fetchall()
    days = c.execute(f"""
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY days_included) AS median,
               min(days_included) AS min, max(days_included) AS max, count(days_included) AS n,
               count(*) FILTER (WHERE days_included BETWEEN 1 AND 3) AS d1_3,
               count(*) FILTER (WHERE days_included BETWEEN 4 AND 5) AS d4_5,
               count(*) FILTER (WHERE days_included BETWEEN 6 AND 7) AS d6_7,
               count(*) FILTER (WHERE days_included BETWEEN 8 AND 10) AS d8_10,
               count(*) FILTER (WHERE days_included > 10) AS d11
        FROM relocations WHERE {scope} AND dup_of IS NULL""").fetchone()
    gone = c.execute(f"""
        SELECT vehicle_type, count(*) AS n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM last_seen - first_seen)) AS median_s
        FROM relocations WHERE {scope} AND status = 'gone'
        GROUP BY ROLLUP (vehicle_type) ORDER BY vehicle_type NULLS FIRST""").fetchall()
    hobart = c.execute(f"""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE origin = 'Hobart' OR destination = 'Hobart') AS any,
               count(*) FILTER (WHERE origin = 'Hobart') AS from_hobart,
               count(*) FILTER (WHERE destination = 'Hobart') AS to_hobart,
               max(first_seen) FILTER (WHERE origin = 'Hobart' OR destination = 'Hobart') AS last_seen
        FROM relocations WHERE {scope} AND dup_of IS NULL""").fetchone()
    hobart_routes = c.execute(f"""
        SELECT origin || ' → ' || destination AS route, count(*) AS n FROM relocations
        WHERE {scope} AND dup_of IS NULL AND (origin = 'Hobart' OR destination = 'Hobart')
        GROUP BY 1 ORDER BY 2 DESC LIMIT 8""").fetchall()
    polls = c.execute("""
        SELECT source, count(*) FILTER (WHERE ok) AS good, count(*) FILTER (WHERE NOT ok AND skipped IS NULL) AS bad,
               count(*) FILTER (WHERE skipped IS NOT NULL) AS skipped, round(avg(listings_found)) AS avg_found
        FROM polls WHERE started_at > now() - interval '7 days' GROUP BY source ORDER BY source""").fetchall()
    recent = c.execute("SELECT * FROM polls ORDER BY started_at DESC LIMIT 12").fetchall()
    totals = c.execute("""
        SELECT count(*) AS stored, count(*) FILTER (WHERE status = 'open') AS open,
               count(*) FILTER (WHERE matched) AS matched, min(first_seen) AS since FROM relocations""").fetchone()
    top = max([w["n"] for w in weeks] + [1])
    return {"weeks": weeks, "week_max": top, "routes": routes, "route_max": max([r["n"] for r in routes] + [1]),
            "days": days, "gone": gone, "hobart": hobart, "hobart_routes": hobart_routes, "polls": polls,
            "recent": recent, "totals": totals, "db_mb": db.db_size_mb(c)}
