from fastapi import FastAPI, HTTPException
from pydantic import BaseModel , Field
from typing import Literal, Optional

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

    # Temperature and humidity
    temp: float
    feels_like: float
    humidity: float

    # Custom air-quality score
    custom_aqi_percent: float
    custom_aqi_label: str

    # Individual air-quality measurements
    eco2: float
    tvoc: float
    ens_aqi: int
    dust_ug_m3: float
    noise_db: float

    # Connectivity
    wifi: str
    power: bool


# for admin capacity updates
class CapacityUpdate(BaseModel):
    area_id: str
    capacity: int
    