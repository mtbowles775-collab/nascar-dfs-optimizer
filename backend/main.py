# ============================================================
# NASCAR DFS OPTIMIZER — FastAPI Backend
# main.py — Entry point
# ============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from routers import drivers, tracks, races, qualifying, practice, results, simulate, lineups, ownership, salaries, admin, track_types
from database import engine, Base

# Create all tables (safety net — schema.sql already did this)
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="NASCAR DFS Optimizer API",
    description="Simulation, projections, odds, and lineup optimization for NASCAR DFS",
    version="1.0.0"
)

# Allow requests from your React frontend (Vercel) and local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "https://*.vercel.app",
        "https://yourdomain.com",  # replace with your actual domain later
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register all routers
app.include_router(drivers.router,    prefix="/api/drivers",    tags=["Drivers"])
app.include_router(tracks.router,     prefix="/api/tracks",     tags=["Tracks"])
app.include_router(races.router,      prefix="/api/races",      tags=["Races"])
app.include_router(qualifying.router, prefix="/api/qualifying",  tags=["Qualifying"])
app.include_router(practice.router,   prefix="/api/practice",   tags=["Practice"])
app.include_router(results.router,    prefix="/api/results",    tags=["Results"])
app.include_router(simulate.router,   prefix="/api/simulate",   tags=["Simulation"])
app.include_router(lineups.router,    prefix="/api/lineups",    tags=["Lineups"])
app.include_router(ownership.router,  prefix="/api/ownership",  tags=["Ownership"])
app.include_router(salaries.router,   prefix="/api/salaries",   tags=["Salaries"])
app.include_router(admin.router,      prefix="/api/admin",      tags=["Admin"])
app.include_router(track_types.router, prefix="/api/track-types", tags=["Track Types"])

@app.get("/")
def root():
    return {"status": "ok", "message": "NASCAR DFS Optimizer API is running"}

@app.get("/health")
def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
