-- =====================================================================
-- MoraSpace SmartStudy - Simple Editable Database (PostgreSQL)
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. TABLES (Only 2 tables: study_areas & occupancy_snapshots)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS study_areas (
    id               SERIAL PRIMARY KEY,
    slug             VARCHAR(50)  NOT NULL UNIQUE, -- Short ID used by frontend/backend ('apaya', 'centra')
    name             VARCHAR(120) NOT NULL UNIQUE, -- Display name ('Apaya Area', 'Centra Area')
    capacity         INTEGER      NOT NULL DEFAULT 50 CHECK (capacity > 0),
    wifi_available   BOOLEAN      NOT NULL DEFAULT TRUE,
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS occupancy_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    study_area_id    INTEGER     NOT NULL REFERENCES study_areas(id) ON DELETE CASCADE,
    student_count    INTEGER     NOT NULL CHECK (student_count >= 0),
    aqi              INTEGER     DEFAULT 50,
    temp             NUMERIC(4,1) DEFAULT 25.0,
    feels_like       NUMERIC(4,1) DEFAULT 26.0,
    humidity         NUMERIC(4,1) DEFAULT 60.0,
    noise            NUMERIC(4,1) DEFAULT 45.0,
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_snapshots_area_time 
ON occupancy_snapshots (study_area_id, recorded_at DESC); 

-- ---------------------------------------------------------------------
-- 
-- ---------------------------------------------------------------------

INSERT INTO study_areas (slug, name, capacity, wifi_available)
VALUES
   
    ('apaya',  'Apaya Area',  60, TRUE),
    ('centra', 'Centra Area', 50, FALSE)
ON CONFLICT (slug) DO UPDATE
SET name             = EXCLUDED.name,
    capacity         = EXCLUDED.capacity,
    wifi_available   = EXCLUDED.wifi_available;
    
-- ---------------------------------------------------------------------
-- 3. VIEW: CURRENT OCCUPANCY (Read by FastAPI /api/study-areas)
-- ---------------------------------------------------------------------

DROP VIEW IF EXISTS v_current_occupancy;
CREATE VIEW v_current_occupancy AS
SELECT--✅Your backend can use this view to retrieve occupancy information without repeating the same calculation in every API query.
    a.id AS study_area_id,
    a.name,
    a.slug,
    a.wifi_available,
    a.capacity,
    COALESCE(s.student_count, 0) AS student_count,
    -- Inside the main SELECT of v_current_occupancy, add:
    COALESCE(s.aqi, 50) AS aqi,
    COALESCE(s.temp, 25.0) AS temp,
    COALESCE(s.feels_like, 26.0) AS feels_like,
    COALESCE(s.humidity, 60.0) AS humidity,
    COALESCE(s.noise, 45.0) AS noise,
    s.recorded_at,

    ROUND((COALESCE(s.student_count, 0)::NUMERIC / a.capacity) * 100, 1) AS occupancy_percent,
    GREATEST(a.capacity - COALESCE(s.student_count, 0), 0) AS seats_available,
    -- ✏️ EDIT HERE: Change percentage thresholds for Quiet / Moderate / Busy / Full
    CASE
        WHEN (COALESCE(s.student_count, 0)::NUMERIC / a.capacity) * 100 < 40 THEN 'Quiet'
        WHEN (COALESCE(s.student_count, 0)::NUMERIC / a.capacity) * 100 < 70 THEN 'Moderate'
        WHEN (COALESCE(s.student_count, 0)::NUMERIC / a.capacity) * 100 < 90 THEN 'Busy'
        ELSE 'Full'
    END AS level
FROM study_areas a
LEFT JOIN LATERAL (--✅mrthinin thm apit latestm snap shot ek gnn puluwn keedenek innwd kiyl
    SELECT student_count, aqi, temp, feels_like, humidity, noise, recorded_at
    FROM occupancy_snapshots o
    WHERE o.study_area_id = a.id
    ORDER BY o.recorded_at DESC
    LIMIT 1
) s ON TRUE --✅methn me s kiynne subquerie ekk eken krnne apit latestm snapshot ek backend ekt denw

WHERE a.is_active = TRUE
ORDER BY a.id;

-- ---------------------------------------------------------------------
-- 4. FUNCTION 1: SAVE STUDENT COUNT (Called by FastAPI when data arrives)
--    Works with EITHER slug ('apaya') OR name ('Apaya Area')
-- ---------------------------------------------------------------------

CREATE OR REPLACE FUNCTION record_occupancy(
    p_area   TEXT,
    p_count  INTEGER
) RETURNS BIGINT AS $$
DECLARE
    v_area_id INTEGER;
    v_snap_id BIGINT;
BEGIN
    SELECT id INTO v_area_id
    FROM study_areas
    WHERE (LOWER(slug) = LOWER(p_area) OR LOWER(name) = LOWER(p_area))
      AND is_active = TRUE;

    IF v_area_id IS NULL THEN
        RAISE EXCEPTION 'Study area not found: %', p_area;
    END IF;

    INSERT INTO occupancy_snapshots (study_area_id, student_count)
    VALUES (v_area_id, GREATEST(p_count, 0))
    RETURNING id INTO v_snap_id;

    RETURN v_snap_id;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------
-- 5. FUNCTION 2: RECOMMEND BEST STUDY TIMES (Called by FastAPI)
--    Finds the quietest hours directly from occupancy_snapshots
-- ---------------------------------------------------------------------
--✅methn thm podi case ekk thiyenne api ek ek study area eke wed krnn hod welw pennanw nm apit backend eken request krl adl welwl arn frontend eke display krnn wenw
DROP FUNCTION IF EXISTS recommend_study_times(TEXT, INTEGER, INTEGER);
CREATE OR REPLACE FUNCTION recommend_study_times(
    p_area_name   TEXT    DEFAULT NULL, -- NULL = all areas
    p_day_of_week INTEGER DEFAULT NULL, -- Ignored if not enough data; uses overall quietest hour
    p_top         INTEGER DEFAULT 3, --❌    -- Number of best hours to return per area ✅methn apit puluwn hodm welwl thunk wge illa gnn
    p_open_hour   INTEGER DEFAULT 8,  -- First hour the study area is open (24h clock, Colombo time)
    p_close_hour  INTEGER DEFAULT 22  -- Hour the study area closes (last recommended window ends here)
) RETURNS TABLE (
    area_name       TEXT,
    time_window     TEXT,
    typical_count   NUMERIC,
    typical_percent NUMERIC,
    level           TEXT
) AS $$
DECLARE
    v_dow INTEGER := p_day_of_week;
BEGIN
    -- Ignore the weekday filter if there is no data for that weekday
    IF v_dow IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM occupancy_snapshots o
        WHERE o.recorded_at >= NOW() - INTERVAL '14 days'
          AND EXTRACT(DOW FROM o.recorded_at AT TIME ZONE 'Asia/Colombo')::INT = v_dow
    ) THEN
        v_dow := NULL;
    END IF;

    RETURN QUERY
    WITH hourly_stats AS (

        SELECT
            a.id AS area_id,
            a.name::TEXT AS nm,
            a.capacity AS cap,

            EXTRACT(
                HOUR FROM o.recorded_at AT TIME ZONE 'Asia/Colombo'
            )::INT AS hr,

            ROUND(AVG(o.student_count), 1) AS avg_cnt,

            ROUND(
                (AVG(o.student_count)::NUMERIC / a.capacity) * 100,
                1
            ) AS avg_pct

        FROM occupancy_snapshots o

        JOIN study_areas a
            ON a.id = o.study_area_id

        WHERE a.is_active = TRUE

          -- Select a specific study area when requested
          AND (
              p_area_name IS NULL
              OR LOWER(a.name) = LOWER(p_area_name)
              OR LOWER(a.slug) = LOWER(p_area_name)
          )

          -- Consider only the previous 14 days
          AND o.recorded_at >= NOW() - INTERVAL '14 days'

          -- Filter by weekday when requested
          AND (
              v_dow IS NULL
              OR EXTRACT(
                  DOW FROM o.recorded_at AT TIME ZONE 'Asia/Colombo'
              )::INT = v_dow
          )

          -- Only consider hours when the study area is open
          AND EXTRACT(
                  HOUR FROM o.recorded_at AT TIME ZONE 'Asia/Colombo'
              )::INT >= p_open_hour
          AND EXTRACT(
                  HOUR FROM o.recorded_at AT TIME ZONE 'Asia/Colombo'
              )::INT < p_close_hour

        GROUP BY
            a.id,
            a.name,
            a.capacity,
            EXTRACT(
                HOUR FROM o.recorded_at AT TIME ZONE 'Asia/Colombo'
            )::INT
    ),

    ranked AS (

        SELECT
            hs.area_id,
            hs.nm,
            hs.hr,
            hs.avg_cnt,
            hs.avg_pct,

            ROW_NUMBER() OVER (
                PARTITION BY hs.area_id
                ORDER BY hs.avg_pct ASC, hs.hr ASC
            ) AS rk

        FROM hourly_stats hs
    )

    SELECT
        r.nm,

        (
            to_char(make_timestamp(2000, 1, 1, r.hr, 0, 0), 'HH12:MI AM')
            || ' - ' ||
            to_char(
                make_timestamp(2000, 1, 1, r.hr, 0, 0) + INTERVAL '1 hour',
                'HH12:MI AM'
            )
        )::TEXT,

        r.avg_cnt,
        r.avg_pct,

        (CASE
            WHEN r.avg_pct < 40 THEN 'Quiet'
            WHEN r.avg_pct < 70 THEN 'Moderate'
            WHEN r.avg_pct < 90 THEN 'Busy'
            ELSE 'Full'
        END)::TEXT

    FROM ranked r
    WHERE r.rk <= p_top
    ORDER BY r.nm, r.rk;

END;
$$ LANGUAGE plpgsql STABLE;

COMMIT;


/* ✅mechn bn mn krl thiyenne hrid den user onn agaharuwd dwsk aw ub backend eken request ekk dunnm database eken pahugiy sathi dekk e agaharuwda dwse kohomd kattiy 
idl thiyenne kiyl blnw itpsse hodm welwl thunk reccomend krnw oni nm ek adu wedi krgnn puluwn mn ❌ lakunkin dl thiyen then default ek wens krl */
ALTER TABLE study_areas 
ADD COLUMN power_available BOOLEAN NOT NULL DEFAULT TRUE,
ADD COLUMN last_sync TIMESTAMPTZ NOT NULL DEFAULT NOW();

DROP VIEW IF EXISTS v_current_occupancy;
CREATE VIEW v_current_occupancy AS
SELECT
    a.id AS study_area_id,
    a.name,
    a.slug,
    a.wifi_available,
    a.power_available,   -- NEW
    a.last_sync,         -- NEW
    a.capacity,
    COALESCE(s.student_count, 0) AS student_count,
    COALESCE(s.aqi, 50) AS aqi,
    COALESCE(s.temp, 25.0) AS temp,
    COALESCE(s.feels_like, 26.0) AS feels_like,
    COALESCE(s.humidity, 60.0) AS humidity,
    COALESCE(s.noise, 45.0) AS noise,
    s.recorded_at,
    ROUND((COALESCE(s.student_count, 0)::NUMERIC / a.capacity) * 100, 1) AS occupancy_percent,
    GREATEST(a.capacity - COALESCE(s.student_count, 0), 0) AS seats_available,
    CASE
        WHEN (COALESCE(s.student_count, 0)::NUMERIC / a.capacity) * 100 < 40 THEN 'Quiet'
        WHEN (COALESCE(s.student_count, 0)::NUMERIC / a.capacity) * 100 < 70 THEN 'Moderate'
        WHEN (COALESCE(s.student_count, 0)::NUMERIC / a.capacity) * 100 < 90 THEN 'Busy'
        ELSE 'Full'
    END AS level
FROM study_areas a
LEFT JOIN LATERAL (
    SELECT student_count, aqi, temp, feels_like, humidity, noise, recorded_at
    FROM occupancy_snapshots o
    WHERE o.study_area_id = a.id
    ORDER BY o.recorded_at DESC
    LIMIT 1
) s ON TRUE
WHERE a.is_active = TRUE
ORDER BY a.id;


ALTER TABLE occupancy_snapshots
    ADD COLUMN IF NOT EXISTS custom_aqi_percent DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS custom_aqi_label TEXT,
    ADD COLUMN IF NOT EXISTS eco2 DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS tvoc DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS ens_aqi INTEGER,
    ADD COLUMN IF NOT EXISTS dust_ug_m3 DOUBLE PRECISION;

ALTER TABLE study_areas
    ADD COLUMN IF NOT EXISTS wifi_quality TEXT;



DROP VIEW IF EXISTS v_current_occupancy;
CREATE VIEW v_current_occupancy AS
SELECT
    a.id AS study_area_id,
    a.name,
    a.slug,
    a.wifi_available,
    a.power_available,   -- NEW
    a.last_sync,         -- NEW
    a.capacity,
    COALESCE(s.student_count, 0) AS student_count,
    COALESCE(s.aqi, 50) AS aqi,
        -- NEW environmental fields
    s.custom_aqi_percent,
    s.custom_aqi_label,
    s.eco2,
    s.tvoc,
    s.ens_aqi,
    s.dust_ug_m3,

    COALESCE(s.temp, 25.0) AS temp,
    COALESCE(s.feels_like, 26.0) AS feels_like,
    COALESCE(s.humidity, 60.0) AS humidity,
    COALESCE(s.noise, 45.0) AS noise,
    s.recorded_at,
    ROUND((COALESCE(s.student_count, 0)::NUMERIC / a.capacity) * 100, 1) AS occupancy_percent,
    GREATEST(a.capacity - COALESCE(s.student_count, 0), 0) AS seats_available,
    CASE
        WHEN (COALESCE(s.student_count, 0)::NUMERIC / a.capacity) * 100 < 40 THEN 'Quiet'
        WHEN (COALESCE(s.student_count, 0)::NUMERIC / a.capacity) * 100 < 70 THEN 'Moderate'
        WHEN (COALESCE(s.student_count, 0)::NUMERIC / a.capacity) * 100 < 90 THEN 'Busy'
        ELSE 'Full'
    END AS level
FROM study_areas a
LEFT JOIN LATERAL (
    SELECT
        student_count,
        aqi,
        custom_aqi_percent,
        custom_aqi_label,
        eco2,
        tvoc,
        ens_aqi,
        dust_ug_m3,
        temp,
        feels_like,
        humidity,
        noise,
        recorded_at
    FROM occupancy_snapshots o
    WHERE o.study_area_id = a.id
    ORDER BY o.recorded_at DESC
    LIMIT 1
) s ON TRUE
WHERE a.is_active = TRUE
ORDER BY a.id;


