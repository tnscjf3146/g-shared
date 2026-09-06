from fastapi import FastAPI

from app.api import scheduler

app = FastAPI(
    title="GPU AI Scheduler",
    version="0.1.0",
)

app.include_router(scheduler.router)


@app.get("/health")
def health():
    return {"status": "ok"}
