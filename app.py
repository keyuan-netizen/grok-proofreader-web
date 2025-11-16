from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
import uvicorn
import shutil
from pathlib import Path
import zipfile
import os
import asyncio
import uuid
import logging
from typing import List, Dict
from dotenv import load_dotenv

from proofreader import extract_text, call_grok, save_reports, save_single_report

load_dotenv()

app = FastAPI(title="Grok Proofreader")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger("proofreader.queue")

TEMP_ROOT = Path("/tmp/proofreader")
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
JOBS: Dict[str, Dict] = {}

ROLES = {
    "legal": "You are a senior paralegal editor. Ensure legal accuracy, eliminate ambiguity, flag risky language, and maintain formal contract structure. Use precise terminology.",
    "academic": "You are a peer-review editor for top-tier academic journals. Fix grammar, clarity, logic, and citation style (APA/MLA). Use formal, precise language.",
    "business": "You are a corporate communications director. Ensure clarity, brevity, professionalism, and brand voice. Eliminate jargon unless essential.",
    "creative": "You are a bestselling novelist’s editor. Improve flow, rhythm, imagery, dialogue, and emotional impact. Suggest vivid alternatives.",
    "mentor": "You are a kind, patient writing coach. Correct gently, praise strengths, and explain every change in simple terms."
}

DEFAULT_ROLE = "academic"

@app.get("/")
async def home(request: Request):
    context = {"request": request, "roles": ROLES.keys(), "default_role": DEFAULT_ROLE}
    return templates.TemplateResponse("index.html", context)

@app.post("/proofread")
async def proofread(
    role: str = Form(DEFAULT_ROLE),
    files: list[UploadFile] = File(...)
):
    if role not in ROLES:
        raise HTTPException(400, "Invalid role")
    
    api_key = os.getenv("GROK_API_KEY")
    if not api_key:
        raise HTTPException(500, "API key not configured")

    job_id = uuid.uuid4().hex
    job_dir = TEMP_ROOT / job_id
    upload_dir = job_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    docx_paths = []
    for file in files:
        if not file.filename.lower().endswith(".docx"):
            continue
        path = upload_dir / file.filename
        with open(path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        docx_paths.append(path)

    if not docx_paths:
        raise HTTPException(400, "No .docx files uploaded")

    file_entries = [
        {
            "id": idx,
            "name": path.name,
            "status": "queued",
            "download_url": None,
            "report_path": None
        }
        for idx, path in enumerate(docx_paths)
    ]

    JOBS[job_id] = {
        "status": "queued",
        "files": file_entries,
        "zip_path": None,
        "error": None,
        "role": role
    }
    logger.info("Queued job %s with %d file(s)", job_id, len(docx_paths))

    asyncio.create_task(asyncio.to_thread(process_job, job_id, docx_paths, api_key, role))

    return {
        "job_id": job_id,
        "status": JOBS[job_id]["status"],
        "files": serialize_job_files(JOBS[job_id])
    }

@app.get("/queue/{job_id}")
async def queue_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "job_id": job_id,
        "status": job["status"],
        "files": serialize_job_files(job),
        "download_ready": bool(job.get("zip_path")),
        "error": job.get("error")
    }

@app.get("/queue/{job_id}/download")
async def download_results(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "complete" or not job.get("zip_path"):
        raise HTTPException(400, "Job is still processing")
    return FileResponse(
        job["zip_path"],
        media_type="application/zip",
        filename=f"{job_id}_proofread_results.zip"
    )

@app.get("/queue/{job_id}/files/{file_id}")
async def download_single_file(job_id: str, file_id: int):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    try:
        file_entry = next(file for file in job["files"] if file["id"] == file_id)
    except StopIteration:
        raise HTTPException(404, "File not found")

    report_path = file_entry.get("report_path")
    if file_entry.get("status") != "complete" or not report_path:
        raise HTTPException(400, "File is not ready for download")

    path = Path(report_path)
    if not path.exists():
        raise HTTPException(404, "Report file missing")

    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=path.name
    )

@app.delete("/queue/{job_id}")
async def delete_job(job_id: str):
    cleaned = cleanup_job(job_id)
    if not cleaned:
        raise HTTPException(404, "Job not found")
    return {"status": "deleted"}

@app.post("/queue/{job_id}/cleanup")
async def cleanup_job_post(job_id: str):
    cleanup_job(job_id)
    return {"status": "deleted"}

def serialize_job_files(job: Dict) -> List[Dict]:
    return [
        {
            "id": file_info["id"],
            "name": file_info["name"],
            "status": file_info["status"],
            "download_url": file_info.get("download_url")
        }
        for file_info in job["files"]
    ]

def process_job(job_id: str, docx_paths: List[Path], api_key: str, role: str):
    job = JOBS.get(job_id)
    if not job:
        return

    temp_dir = TEMP_ROOT / job_id
    output_dir = temp_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    job["status"] = "processing"
    for idx, path in enumerate(docx_paths):
        file_entry = job["files"][idx]
        file_entry["status"] = "processing"
        file_entry["download_url"] = None
        file_entry["report_path"] = None
        logger.info("Job %s: processing %s", job_id, path.name)
        try:
            text = extract_text(path)
            structured = call_grok(text, api_key, ROLES[role])
            result_payload = {
                "filename": path.name,
                "char_count": len(text),
                "api_result": {"data": structured}
            }
            results.append(result_payload)
            report_path = save_single_report(result_payload, output_dir)
            file_entry["status"] = "complete"
            file_entry["report_path"] = str(report_path)
            file_entry["download_url"] = f"/queue/{job_id}/files/{file_entry['id']}"
        except Exception as e:
            logger.exception("Job %s failed on %s", job_id, path.name)
            file_entry["status"] = "error"
            job["error"] = str(e)
            fallback = {"summary": f"Processing failed: {e}", "corrections": []}
            results.append({
                "filename": path.name,
                "char_count": 0,
                "api_result": {"data": fallback}
            })

    save_reports(results, output_dir)
    zip_path = temp_dir / "proofread_results.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for file in output_dir.iterdir():
            zf.write(file, file.name)

    job["zip_path"] = str(zip_path)
    job["status"] = "complete" if not job.get("error") else "failed"
    logger.info("Job %s finished with status %s", job_id, job["status"])

def cleanup_job(job_id: str) -> bool:
    job_present = JOBS.pop(job_id, None) is not None
    job_dir = TEMP_ROOT / job_id
    dir_exists = job_dir.exists()
    if dir_exists:
        shutil.rmtree(job_dir, ignore_errors=True)
    logger.info("Cleaned up job %s", job_id)
    return job_present or dir_exists

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
