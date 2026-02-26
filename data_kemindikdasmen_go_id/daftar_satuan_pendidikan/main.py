import sqlite3
import cloudscraper
import time
import threading
from fastapi import FastAPI, BackgroundTasks, HTTPException
from typing import Dict, Optional
import csv
import io
import os
from pydantic import BaseModel

class CSVScrapeRequest(BaseModel):
    file_url: str

app = FastAPI()
scraper = cloudscraper.create_scraper()

# Thread lock to prevent concurrent scraping runs
scrape_lock = threading.Lock()
scrape_state = {
    "is_running": False,
    "total": 0,
    "completed": 0,
    "failed": 0,
    "current_npsn": None
}

def init_db():
    """Initializes the SQLite database and table."""
    conn = sqlite3.connect("schools.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS school_details (
            npsn TEXT PRIMARY KEY,
            lat REAL,
            lon REAL,
            alamat TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def get_school_details(npsn: str) -> Optional[Dict]:
    """Fetches details for a specific NPSN."""
    detail_url = f"https://api.data.belajar.id/data-portal-backend/v1/master-data/satuan-pendidikan/details/{npsn}"
    try:
        time.sleep(0.5)  # Respectful delay
        response = scraper.get(detail_url, timeout=15)
        
        if response.status_code == 200:
            sp = response.json().get("satuanPendidikan", {})
            return {
                "npsn": sp.get("npsn"),
                "lat": sp.get("lintang"),
                "lon": sp.get("bujur"),
                "alamat": sp.get("alamatJalan")
            }
        return None
    except Exception as e:
        print(f"Error fetching NPSN {npsn}: {e}")
        return None

def save_to_db(data: Dict):
    """Saves the scraped data to SQLite."""
    conn = sqlite3.connect("schools.db")
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO school_details (npsn, lat, lon, alamat)
        VALUES (?, ?, ?, ?)
    ''', (data['npsn'], data['lat'], data['lon'], data['alamat']))
    conn.commit()
    conn.close()

def run_scraping_process(npsn_list: list):
    """The background task logic."""
    global scrape_state
    # Acquire the lock; if another process is running, this waits or skips
    with scrape_lock:
        scrape_state["is_running"] = True
        scrape_state["total"] = len(npsn_list)
        scrape_state["completed"] = 0
        scrape_state["failed"] = 0
        
        print(f"Starting background scrape for {len(npsn_list)} items...")
        init_db()
        
        for npsn in npsn_list:
            scrape_state["current_npsn"] = npsn
            details = get_school_details(npsn)
            if details:
                save_to_db(details)
                scrape_state["completed"] += 1
            else:
                scrape_state["failed"] += 1
        
        scrape_state["is_running"] = False
        scrape_state["current_npsn"] = None
        print("Scraping process finished.")

@app.post("/trigger-scrape")
async def trigger_scrape(npsn_list: list[str], background_tasks: BackgroundTasks):
    """
    Endpoint to start the scraping process.
    Returns 200 immediately and runs the logic in the background.
    """
    global scrape_state
    
    if scrape_state["is_running"]:
        return {"status": "busy", "message": "A scrape is already in progress. Please wait."}
    
    # Add the function to background tasks
    background_tasks.add_task(run_scraping_process, npsn_list)
    
    return {"status": "success", "message": "Scrape started in background."}

@app.post("/trigger-scrape-csv")
async def trigger_scrape_csv(request: CSVScrapeRequest, background_tasks: BackgroundTasks):
    """
    Endpoint to start the scraping process from a CSV URL.
    The CSV should have an 'NPSN' or 'npsn' column.
    """
    global scrape_state
    
    if scrape_state["is_running"]:
        return {"status": "busy", "message": "A scrape is already in progress. Please wait."}
    
    file_url = request.file_url
    
    if not file_url.startswith("http"):
        return {"status": "error", "message": "file_url must be a valid HTTP/HTTPS URL."}
        
    try:
        response = scraper.get(file_url, timeout=30)
        response.raise_for_status()
        decoded = response.content.decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(decoded))
        
        npsn_list = []
        for row in reader:
            # Handle case where column name might be 'NPSN', 'npsn', or have whitespace
            npsn = None
            for key, val in row.items():
                if key and key.strip().upper() == 'NPSN':
                    npsn = val
                    break
            
            if npsn and npsn.strip():
                npsn_list.append(npsn.strip())
                    
    except Exception as e:
        return {"status": "error", "message": f"Failed to parse CSV: {str(e)}"}
        
    if not npsn_list:
        return {"status": "error", "message": "No valid NPSN found in the specified CSV."}
    
    # Add the function to background tasks
    background_tasks.add_task(run_scraping_process, npsn_list)
    
    return {"status": "success", "message": f"Scrape started in background for {len(npsn_list)} NPSNs."}

@app.get("/status")
async def get_status():
    """Check if the scraper is currently active and return its progress."""
    return scrape_state

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
