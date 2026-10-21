from fastapi import FastAPI, HTTPException
from pydantic import BaseModel , Field
from typing import Literal

app = FastAPI()

# Temporary mock database
mock_db = {
"centra": {
        "name": "Sentra Court",
        "capacity": 50,
        "wifi": True,
        "power": True,
        "aqi": 42,
        "temp": 24.0,
        "feelsLike": 26.0,
        "humidity": 55.0,
        "noise": 45.0,
        "students": 18,
        "overall": 85
    },
    "apaya":{
        "name" : "Apaya",
        "capacity": 60,
        "wifi": True,
        "power": True,
        "aqi": 115,
        "temp": 33.0,
        "feelsLike": 38.0,
        "humidity": 72.0,
        "noise": 68.0,

        "students": 42,
        
        "overall": 52
    }
}

class SensorEvent(BaseModel):
    # Using Field alias to support "area id" with a space as defined in the spec
    area_id : str = Field(... , alias = "area id")
    event : Literal["ENTER" , "EXIT"]

# for open area
class OccupancySync(BaseModel):
    area_id: str  # "apaya" or "centra"
    student_count: int

# for environment data
class EnvTelemetry(BaseModel):
    area_id: str
    aqi: int
    temp: float
    feels_like: float
    humidity: float
    noise: float
