from fastapi import FastAPI, HTTPException
from pydantic import BaseModel , Field
from typing import Literal

app = FastAPI()

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
    wifi: bool     # Add this
    power: bool    # Add this
