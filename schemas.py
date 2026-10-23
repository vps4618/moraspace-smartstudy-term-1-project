from fastapi import FastAPI, HTTPException
from pydantic import BaseModel , Field
from typing import Literal , Optional

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
    #aqi: int REMOVED              
    custom_aqi_percent: float    # Add this
    custom_aqi_label: str   # Add this
    eco2: float        # Add this
    tvoc: float        # Add this
    ens_aqi: int       # Add this
    dust_ug_m3: float  # Add this
    temp: float
    feels_like: float
    humidity: float
    noise_db: float  #changed
    wifi: str     # Add this changed
    power: bool    # Add this
