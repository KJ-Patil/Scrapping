import os
import csv
import io
from pathlib import Path
from fastapi import FastAPI, Request, Query, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="ReLU Scraping Challenge")

BASE_DIR = Path(__file__).resolve().parent
CHALLENGE_DIR = BASE_DIR.parent

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase_client = None

if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client
        supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    except ImportError:
        pass


def read_csv_file(filepath: Path) -> list[dict]:
    rows = []
    with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        clean_fieldnames = [name.lstrip("﻿") for name in reader.fieldnames]
        reader.fieldnames = clean_fieldnames
        for row in reader:
            rows.append(row)
    return rows


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/disney")
async def get_disney_data(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str = Query("", max_length=200),
):
    if supabase_client:
        query = supabase_client.table("disney_cruises").select("*")
        if search:
            query = query.ilike("title", f"%{search}%")
        response = query.range((page - 1) * per_page, page * per_page - 1).execute()
        count_resp = supabase_client.table("disney_cruises").select("*", count="exact")
        if search:
            count_resp = count_resp.ilike("title", f"%{search}%")
        count_resp = count_resp.execute()
        return {"data": response.data, "total": count_resp.count, "page": page, "per_page": per_page}

    csv_path = CHALLENGE_DIR / "obj1_disney" / "disney_cruise_results.csv"
    all_rows = read_csv_file(csv_path)
    if search:
        search_lower = search.lower()
        all_rows = [r for r in all_rows if any(search_lower in str(v).lower() for v in r.values())]
    total = len(all_rows)
    start = (page - 1) * per_page
    end = start + per_page
    return {"data": all_rows[start:end], "total": total, "page": page, "per_page": per_page}


@app.get("/api/ingredients")
async def get_ingredients_data(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str = Query("", max_length=200),
):
    if supabase_client:
        query = supabase_client.table("ingredients_companies").select("*")
        if search:
            query = query.ilike("company_name", f"%{search}%")
        response = query.range((page - 1) * per_page, page * per_page - 1).execute()
        count_resp = supabase_client.table("ingredients_companies").select("*", count="exact")
        if search:
            count_resp = count_resp.ilike("company_name", f"%{search}%")
        count_resp = count_resp.execute()
        return {"data": response.data, "total": count_resp.count, "page": page, "per_page": per_page}

    csv_path = CHALLENGE_DIR / "obj2_ingredients" / "ingredients_network_results.csv"
    all_rows = read_csv_file(csv_path)
    if search:
        search_lower = search.lower()
        all_rows = [r for r in all_rows if any(search_lower in str(v).lower() for v in r.values())]
    total = len(all_rows)
    start = (page - 1) * per_page
    end = start + per_page
    return {"data": all_rows[start:end], "total": total, "page": page, "per_page": per_page}


@app.get("/api/disney/stats")
async def get_disney_stats():
    csv_path = CHALLENGE_DIR / "obj1_disney" / "disney_cruise_results.csv"
    all_rows = read_csv_file(csv_path)

    total = len(all_rows)
    pacific = sum(1 for r in all_rows if "PACIFIC" in r.get("Destination", "").upper())
    holiday = sum(
        1 for r in all_rows
        if any(tag in r.get("Tags", "").lower() for tag in ["holiday", "merrytime", "halloween"])
    )
    more_than_2_dates = sum(
        1 for r in all_rows
        if r.get("Number of Dates", "0").isdigit() and int(r.get("Number of Dates", "0")) > 2
    )
    miami = sum(1 for r in all_rows if "miami" in r.get("Departing From", "").lower())
    london = sum(1 for r in all_rows if "london" in r.get("Departing From", "").lower())

    return {
        "total_cruises": total,
        "pacific_cruises": pacific,
        "holiday_cruises": holiday,
        "more_than_2_dates": more_than_2_dates,
        "miami_departures": miami,
        "london_departures": london,
    }


@app.get("/api/ingredients/stats")
async def get_ingredients_stats():
    csv_path = CHALLENGE_DIR / "obj2_ingredients" / "ingredients_network_results.csv"
    all_rows = read_csv_file(csv_path)

    total_ingredients = sum(
        1 for r in all_rows
        if "ingredient" in r.get("Primary Business Activity", "").lower()
    )
    finished_products = sum(
        1 for r in all_rows
        if "finished" in r.get("Primary Business Activity", "").lower()
    )
    herbs_spices = sum(
        1 for r in all_rows
        if any(k in r.get("Categories", "").lower() for k in ["herb", "spice"])
    )
    delivery_formats = sum(
        1 for r in all_rows
        if any(k in r.get("Categories", "").lower() for k in [
            "capsule", "tablet", "powder", "liquid", "softgel", "granul", "pellet", "sachet",
        ])
    )
    cognitive_mental = sum(
        1 for r in all_rows
        if any(k in r.get("Categories", "").lower() for k in ["cognitive", "mental health"])
    )

    return {
        "total_ingredients": total_ingredients,
        "finished_products": finished_products,
        "herbs_spices": herbs_spices,
        "delivery_formats": delivery_formats,
        "cognitive_mental": cognitive_mental,
    }


@app.post("/api/upload-csv")
async def upload_csv(file: UploadFile = File(...)):
    if not file.filename.endswith(".csv"):
        return {"error": "Only CSV files are allowed"}

    contents = await file.read()
    decoded = contents.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(decoded))
    clean_fieldnames = [name.lstrip("﻿") for name in reader.fieldnames]
    reader.fieldnames = clean_fieldnames

    rows = []
    for row in reader:
        rows.append(row)

    return {
        "columns": clean_fieldnames,
        "data": rows,
        "total": len(rows),
        "filename": file.filename,
    }
