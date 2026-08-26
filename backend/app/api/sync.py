from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.post("/sync/{job_name}")
async def run_job(request: Request, job_name: str):
    try:
        return await request.app.state.runner.run(job_name, trigger="manual")
    except KeyError:
        raise HTTPException(404, f"unknown job {job_name!r}")


@router.post("/backup")
async def backup_now(request: Request):
    return await request.app.state.runner.run("db_backup", trigger="manual")
