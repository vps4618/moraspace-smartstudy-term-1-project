import os
from fastapi import FastAPI , HTTPException , Security , Depends , WebSocket , WebSocketDisconnect , Query
from fastapi.security.api_key import APIKeyHeader
from schemas import SensorEvent,mock_db, OccupancySync , EnvTelemetry
from fastapi.middleware.cors import CORSMiddleware

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

# info page
@app.get("/")
def root():
    return {"status" : "SmartStudy API online"}

# gathering posted data and update db
# async await added for websocket
@app.post("/api/sensor/event")
async def handle_sensor_event(event_data : SensorEvent, api_key: str = Depends(verify_api_key)):
    # 1. Process the event and update mock_db
    area_id = event_data.area_id

    if area_id not in mock_db:
        raise HTTPException(status_code=404 , detail = "Study area not found")

    area = mock_db[area_id]

    if event_data.event == "ENTER":
        if area["students"] < area["capacity"]:
            area["students"] += 1
    elif event_data.event == "EXIT":
        if area["students"] > 0:
            area["students"] -= 1

    # 2. Format the payload that want the frontend to receive
    live_update = {
        "area_id": event_data.area_id,
        "new_occupancy": area["students"],
        "status": "success"
    }
    return live_update

# displaying study area details
@app.get("/api/study-areas")
def get_study_areas():
    return mock_db

# displaying recommendations
@app.get("/api/recommendations")
def get_recommendations():
    results = []
    for area_id, data in mock_db.items():
        availability_ratio = (data["capacity"] - data["current_occupancy"]) / data["capacity"]
        
        # Calculate base score (adjust with actual DB metrics later)
        score = (0.40 * availability_ratio) + (0.15 * int(data["wifi"])) + (0.10 * int(data["power"]))
        
        results.append({"area_id": area_id, "score": score, "current_occupancy": data["current_occupancy"]})
    
    # Sort descending by score
    return sorted(results, key=lambda x: x["score"], reverse=True)


# for open area
@app.post("/api/occupancy/sync")
async def sync_occupancy(data: OccupancySync, api_key: str = Depends(verify_api_key)):
    if data.area_id not in mock_db:
        raise HTTPException(status_code=404, detail="Area not found")
    mock_db[data.area_id]["students"] = data.student_count
    # If using PostgreSQL: execute "SELECT record_occupancy(%s, %s)" with (data.area_id, data.student_count)
    return {"status": "updated", "count": data.student_count}

# for taking environment data
@app.post("/api/env/sync")
async def sync_environment(data: EnvTelemetry, api_key: str = Depends(verify_api_key)):
    if data.area_id not in mock_db:
        raise HTTPException(status_code=404, detail="Area not found")
    # Update the dictionary / database with new environmental metrics
    mock_db[data.area_id].update({
        "aqi": data.aqi,
        "temp": data.temp,
        "feelsLike": data.feels_like,
        "humidity": data.humidity,
        "noise": data.noise
    })
    return {"status": "env_updated"}