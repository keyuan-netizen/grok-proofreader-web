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
from dotenv import load_dotenv

from proofreader import extract_text, call_grok, save_reports

load_dotenv()

app = FastAPI(title="Grok Proofreader")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

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

    temp_dir = Path("/tmp/proofreader")
    temp_dir.mkdir(exist_ok=True)
    upload_dir = temp_dir / "uploads"
    upload_dir.mkdir(exist_ok=True)

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

    results = []
    for path in docx_paths:
        try:
            text = extract_text(path)
            structured = call_grok(text, api_key, ROLES[role])
            results.append({
                "filename": path.name,
                "char_count": len(text),
                "api_result": {"data": structured}
            })
        except Exception as e:
            results.append({
                "filename": path.name,
                "api_result": {"error": str(e)}
            })

    output_dir = temp_dir / "output"
    save_reports(results, output_dir)

    zip_path = temp_dir / "proofread_results.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for file in output_dir.iterdir():
            zf.write(file, file.name)

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename="proofread_results.zip"
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
