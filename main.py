import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from schemas import SensorEvent, OccupancySync, EnvTelemetry , CapacityUpdate



# ! Disable the /docs , /redoc, and openapi.json routes in production
app = FastAPI(docs_url=None,redoc_url=None,openapi_url=None,title = "SmartStudy Backend")

# Add this CORS configuration (to prevent CORS errors and secure the API)
# these will not block sensors posting to backend
app.add_middleware(
    CORSMiddleware,
    # Allow only your specific frontend URLs instead of "*"
    allow_origins=[
        "https://vps4618.github.io",  # Your GitHub Pages domain
        "http://localhost:3000",      # Local frontend testing
        "http://127.0.0.1:3000",
        "http://localhost:5500",      # VS Code Live Server
        "http://127.0.0.1:5500"
    ],
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
)

# Load DB URL
DATABASE_URL = os.getenv("DATABASE_URL")
# Define a strong secret key (Do not share this publicly)
SECRET_API_KEY = os.getenv("API")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)


# ! Dependency function to validate incoming requests
def verify_api_key(api_key: str = Security(api_key_header)):
    # Failsafe to prevent bypassing security if the environment variable goes missing
    if not SECRET_API_KEY:
        raise HTTPException(status_code=500, detail="Server configuration error")
        
    if api_key != SECRET_API_KEY:
        raise HTTPException(status_code=403, detail="Access forbidden: Invalid API Key")
    return api_key

def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()

# ---------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------   
# info page
@app.get("/")
def root():
    return {"status" : "SmartStudy API online"}

# gathering posted data and update db
# endpoint updated to Fix the Door Event Race Condition
@app.post("/api/sensor/event")
async def handle_sensor_event(event_data: SensorEvent, db=Depends(get_db), api_key: str = Depends(verify_api_key)):
    cur = db.cursor()
    try:
        # Atomic read-modify-write in ONE SQL statement to prevent race conditions
        cur.execute("""
            INSERT INTO occupancy_snapshots (
                study_area_id, student_count, aqi, temp, feels_like, humidity, noise,
                custom_aqi_percent, custom_aqi_label, eco2, tvoc, ens_aqi, dust_ug_m3
            )
            SELECT 
                a.id,
                CASE 
                    WHEN %s = 'ENTER' THEN LEAST(COALESCE(s.student_count, 0) + 1, a.capacity)
                    WHEN %s = 'EXIT'  THEN GREATEST(COALESCE(s.student_count, 0) - 1, 0)
                    ELSE COALESCE(s.student_count, 0)
                END,
                s.aqi, s.temp, s.feels_like, s.humidity, s.noise,
                s.custom_aqi_percent, s.custom_aqi_label, s.eco2, s.tvoc, s.ens_aqi, s.dust_ug_m3
            FROM study_areas a
            LEFT JOIN LATERAL (
                SELECT * FROM occupancy_snapshots 
                WHERE study_area_id = a.id 
                ORDER BY recorded_at DESC LIMIT 1
            ) s ON TRUE
            WHERE a.slug = %s
            RETURNING student_count;
        """, (event_data.event, event_data.event, event_data.area_id))
        
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Study area not found")
            
        new_count = row["student_count"]
        
        # Update the heartbeat since the sensor just communicated
        cur.execute("UPDATE study_areas SET last_sync = NOW() WHERE slug = %s;", (event_data.area_id,))
        
        db.commit()
        
        return {
            "area_id": event_data.area_id,
            "new_occupancy": new_count,
            "status": "success"
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/study-areas")
def get_study_areas(db=Depends(get_db)):
    cur = db.cursor()
    
    try:
        # 1. Execute the heartbeat check exactly when the user loads the dashboard
        cur.execute("""
            UPDATE study_areas
            SET wifi_available = FALSE, power_available = FALSE, wifi_quality = 'None'
            WHERE last_sync < NOW() - INTERVAL '2 minutes'
            AND (wifi_available = TRUE OR power_available = TRUE OR wifi_quality IS DISTINCT FROM 'None');
        """)
        db.commit()
        
        cur.execute("SELECT * FROM v_current_occupancy;")
        rows = cur.fetchall()
        
        # Format exactly as Nisal's frontend expects
        dashboard_data = {}
        for row in rows:
            # Recreating Thareen's basic score logic (assuming wifi_quality is a string)
            availability_ratio = row["seats_available"] / row["capacity"]
            wifi_score = 0.25 if row["wifi_quality"] in ["Excellent", "Good", "Fair", "Very Good"] else 0.0
            
            score = (0.60 * availability_ratio) + wifi_score + (0.15 * int(row["power_available"]))
            overall_score = min(int(score * 100), 100) 

            dashboard_data[row["slug"]] = {
                "capacity": row["capacity"],
                "students": row["student_count"],
                "wifi": row["wifi_quality"], # Updated
                "power": row["power_available"],
                
                # New Environmental Fields
                "custom_aqi_percent": float(row["custom_aqi_percent"] or 0),
                "custom_aqi_label": row["custom_aqi_label"] or "Unknown",
                "eco2": float(row["eco2"] or 0),
                "tvoc": float(row["tvoc"] or 0),
                "ens_aqi": int(row["ens_aqi"] or 0),
                "dust_ug_m3": float(row["dust_ug_m3"] or 0),
                
                # Original Environmental Fields
                "temp": float(row["temp"]),
                "feelsLike": float(row["feels_like"]),
                "humidity": float(row["humidity"]),
                "noise": float(row["noise"]),
                "overall": overall_score,
                "level": row["level"] # <-- ADD THIS LINE
            }
        return dashboard_data
        
    except Exception as e:
        # Rollback the transaction to prevent database locks on failed queries
        db.rollback()
        # Surface the exact Python/PostgreSQL error to the browser
        raise HTTPException(status_code=500, detail=f"Database Error: {str(e)}")

@app.post("/api/occupancy/sync")
async def sync_occupancy(data: OccupancySync, db=Depends(get_db), api_key: str = Depends(verify_api_key)):
    cur = db.cursor()
    try:
        # Call Chamika's custom Postgres function
        cur.execute("SELECT record_occupancy(%s, %s);", (data.area_id, data.student_count))
        
        # Also update the heartbeat since the camera sent data
        cur.execute("UPDATE study_areas SET last_sync = NOW() WHERE slug = %s", (data.area_id,))
        
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    
    return {"status": "updated", "count": data.student_count}


@app.post("/api/env/sync")
async def sync_environment(
    data: EnvTelemetry,
    db=Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    cur = db.cursor()

    try:
        # 1. Convert Wi-Fi quality into a Boolean status.
        # The original dashboard still expects wifi_available
        # to be TRUE or FALSE.

        valid_wifi_levels = {
            "Excellent",
            "Very Good",
            "Good",
            "Fair",
            "Weak",
            "Very Weak"
        }

        wifi_quality = data.wifi.strip()

        if wifi_quality not in valid_wifi_levels:
            raise HTTPException(
                status_code=422,
                detail="Invalid Wi-Fi quality value"
            )

        wifi_available = wifi_quality != "Very Weak"

        # 2. Update study area status and heartbeat.

        cur.execute(
            """
            UPDATE study_areas
            SET
                last_sync = NOW(),
                wifi_available = %s,
                wifi_quality = %s,
                power_available = %s
            WHERE slug = %s
            RETURNING id;
            """,
            (
                wifi_available,
                wifi_quality,
                data.power,
                data.area_id
            )
        )

        area = cur.fetchone()

        if not area:
            raise HTTPException(
                status_code=404,
                detail="Study area not found"
            )

        # 3. Save environmental measurements.
        # Preserve the last known student count.
        # Keep the existing aqi and noise columns compatible
        # with the current dashboard.

        cur.execute(
            """
            INSERT INTO occupancy_snapshots (
                study_area_id,
                student_count,
                aqi,
                temp,
                feels_like,
                humidity,
                noise,
                custom_aqi_percent,
                custom_aqi_label,
                eco2,
                tvoc,
                ens_aqi,
                dust_ug_m3
            )
            VALUES (
                %s,
                COALESCE(
                    (
                        SELECT student_count
                        FROM occupancy_snapshots
                        WHERE study_area_id = %s
                        ORDER BY recorded_at DESC
                        LIMIT 1
                    ),
                    0
                ),
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            );
            """,
            (
                area["id"],
                area["id"],
                data.custom_aqi_percent,
                data.temp,
                data.feels_like,
                data.humidity,
                data.noise_db,
                data.custom_aqi_percent,
                data.custom_aqi_label,
                data.eco2,
                data.tvoc,
                data.ens_aqi,
                data.dust_ug_m3
            )
        )

        # 4. Commit both database updates.

        db.commit()

        return {
            "status": "env_updated",
            "area_id": data.area_id,
            "custom_aqi_percent": data.custom_aqi_percent,
            "custom_aqi_label": data.custom_aqi_label,
            "wifi_quality": wifi_quality
        }

    except HTTPException:
        db.rollback()
        raise

    except Exception as e:
        db.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Environment sync failed: {str(e)}"
        )

    finally:
        cur.close()


@app.get("/api/recommendations")
def get_recommend_times(area: str = None, top: int = 3, db=Depends(get_db)):
    cur = db.cursor()
    try:
        # Calls the SQL function: recommend_study_times(p_area_name, p_day_of_week, p_top)
        cur.execute("SELECT * FROM recommend_study_times(%s, NULL, %s);", (area, top))
        rows = cur.fetchall()
        
        recommendations = []
        for row in rows:
            recommendations.append({
                "area_name": row["area_name"],
                "time_window": row["time_window"],
                "typical_count": float(row["typical_count"]),
                "level": row["level"]
            })
            
        return {"recommendations": recommendations}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/api/trends")
def get_historical_trends(area: str = None, db=Depends(get_db)):
    cur = db.cursor()
    try:
        # Group the last 14 days of data into 4 shifts and calculate average capacity %
        cur.execute("""
            WITH shifts AS (
                SELECT 
                    CASE 
                        WHEN EXTRACT(HOUR FROM o.recorded_at AT TIME ZONE 'Asia/Colombo') >= 8 AND EXTRACT(HOUR FROM o.recorded_at AT TIME ZONE 'Asia/Colombo') < 10 THEN 'shift_1'
                        WHEN EXTRACT(HOUR FROM o.recorded_at AT TIME ZONE 'Asia/Colombo') >= 10 AND EXTRACT(HOUR FROM o.recorded_at AT TIME ZONE 'Asia/Colombo') < 13 THEN 'shift_2'
                        WHEN EXTRACT(HOUR FROM o.recorded_at AT TIME ZONE 'Asia/Colombo') >= 13 AND EXTRACT(HOUR FROM o.recorded_at AT TIME ZONE 'Asia/Colombo') < 16 THEN 'shift_3'
                        WHEN EXTRACT(HOUR FROM o.recorded_at AT TIME ZONE 'Asia/Colombo') >= 16 AND EXTRACT(HOUR FROM o.recorded_at AT TIME ZONE 'Asia/Colombo') < 19 THEN 'shift_4'
                    END AS shift,
                    (o.student_count::NUMERIC / a.capacity) * 100 AS pct
                FROM occupancy_snapshots o
                JOIN study_areas a ON o.study_area_id = a.id
                WHERE o.recorded_at >= NOW() - INTERVAL '14 days'
                  AND (%s IS NULL OR a.slug = %s)
            )
            SELECT shift, ROUND(AVG(pct), 1) as avg_pct
            FROM shifts
            WHERE shift IS NOT NULL
            GROUP BY shift;
        """, (area, area))
        
        rows = cur.fetchall()
        
        # Default empty structure if no data exists yet
        trends = {"shift_1": 0.0, "shift_2": 0.0, "shift_3": 0.0, "shift_4": 0.0}
        
        for row in rows:
            trends[row["shift"]] = float(row["avg_pct"])
            
        return trends
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/capacity")
async def update_capacity(
    data: CapacityUpdate, 
    db=Depends(get_db), 
    api_key: str = Depends(verify_api_key) # This secures the endpoint!
):
    cur = db.cursor()
    try:
        cur.execute(
            "UPDATE study_areas SET capacity = %s WHERE slug = %s RETURNING id;",
            (data.capacity, data.area_id)
        )
        
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Study area not found")
            
        db.commit()
        return {
            "status": "success", 
            "message": f"{data.area_id} capacity updated to {data.capacity}"
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
