import os
import asyncio
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from schemas import SensorEvent, OccupancySync, EnvTelemetry



# ! Disable the /docs , /redoc, and openapi.json routes in production
app = FastAPI(docs_url=None,redoc_url=None,openapi_url=None,title = "SmartStudy Backend")

# Add this CORS configuration (to prevent CORS errors)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows requests from any domain (like 127.0.0.1)
    allow_credentials=True,
    allow_methods=["*"],  # Allows GET, POST, etc.
    allow_headers=["*"],  # Allows all custom headers (including X-API-Key)
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
@app.post("/api/sensor/event")
async def handle_sensor_event(event_data: SensorEvent, db=Depends(get_db), api_key: str = Depends(verify_api_key)):
    cur = db.cursor()
    try:
        # 1. Get the current capacity and the latest student count
        cur.execute("""
            SELECT a.capacity, COALESCE(s.student_count, 0) as current_count
            FROM study_areas a
            LEFT JOIN LATERAL (
                SELECT student_count FROM occupancy_snapshots 
                WHERE study_area_id = a.id 
                ORDER BY recorded_at DESC LIMIT 1
            ) s ON TRUE
            WHERE a.slug = %s;
        """, (event_data.area_id,))
        
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Study area not found")
            
        capacity = row["capacity"]
        current_count = row["current_count"]
        
        # 2. Calculate the new count safely
        if event_data.event == "ENTER":
            new_count = current_count + 1 if current_count < capacity else capacity
        elif event_data.event == "EXIT":
            new_count = current_count - 1 if current_count > 0 else 0
        else:
            new_count = current_count
            
        # 3. Save the new count using Chamika's custom function
        cur.execute("SELECT record_occupancy(%s, %s);", (event_data.area_id, new_count))
        
        # 4. Update the heartbeat since the sensor just communicated
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

    # 1. Execute the heartbeat check exactly when the user loads the dashboard
    cur.execute("""
        UPDATE study_areas
        SET wifi_available = FALSE, power_available = FALSE
        WHERE last_sync < NOW() - INTERVAL '2 minutes'
        AND (wifi_available = TRUE OR power_available = TRUE);
    """)
    db.commit()
    
    cur.execute("SELECT * FROM v_current_occupancy;")
    rows = cur.fetchall()
    
    # Format exactly as Nisal's frontend expects
    dashboard_data = {}
    for row in rows:
        # Recreating Thareen's basic score logic
        availability_ratio = row["seats_available"] / row["capacity"]
        score = (0.40 * availability_ratio) + (0.15 * int(row["wifi_available"])) + (0.10 * int(row["power_available"]))
        # Normalize score to 100
        overall_score = min(int(score * 100), 100) 

        dashboard_data[row["slug"]] = {
            "capacity": row["capacity"],
            "students": row["student_count"],
            "wifi": row["wifi_available"],
            "power": row["power_available"],
            "aqi": row["aqi"],
            "temp": float(row["temp"]),
            "feelsLike": float(row["feels_like"]),
            "humidity": float(row["humidity"]),
            "noise": float(row["noise"]),
            "overall": overall_score
        }
    return dashboard_data

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
async def sync_environment(data: EnvTelemetry, db=Depends(get_db), api_key: str = Depends(verify_api_key)):
    cur = db.cursor()
    try:
        # 1. Update the study_areas table heartbeat and statuses
        cur.execute("""
            UPDATE study_areas 
            SET last_sync = NOW(), wifi_available = %s, power_available = %s 
            WHERE slug = %s
        """, (data.wifi, data.power, data.area_id))
        
        # 2. Insert the environmental data into occupancy_snapshots
        # We inherit the last known student count so we don't accidentally set it to 0
        cur.execute("""
            INSERT INTO occupancy_snapshots (study_area_id, student_count, aqi, temp, feels_like, humidity, noise)
            SELECT id, 
                   COALESCE((SELECT student_count FROM occupancy_snapshots WHERE study_area_id = study_areas.id ORDER BY recorded_at DESC LIMIT 1), 0), 
                   %s, %s, %s, %s, %s
            FROM study_areas WHERE slug = %s
        """, (data.aqi, data.temp, data.feels_like, data.humidity, data.noise, data.area_id))
        
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
        
    return {"status": "env_updated"}