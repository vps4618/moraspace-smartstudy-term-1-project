from fastapi import FastAPI, HTTPException
from pydantic import BaseModel , Field
from typing import Literal

app = FastAPI()

# Temporary mock database
mock_db = {
"centra": {
        "name": "Sentra Court",
        "capacity": 50,
        "current_occupancy": 0,
        "wifi": True,
        "power": True
    },
    "apaya":{
        "name" : "Apaya",
        "capacity": 60,
        "current_occupancy": 0,
        "wifi": True,
        "power": True,
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
