from fastapi import FastAPI

app = FastAPI(
    title="GPU AI Scheduler",
    version="0.1.0",
)


@app.get("/health")
def health():
    return {"status": "ok"}