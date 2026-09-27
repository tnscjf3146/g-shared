import math
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import scheduler
from app.scheduler.runtime_model import LoadedRuntimeModel, models_directory


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runtime 모델을 시작할 때 한 번 읽어 둔다.

    읽지 못해도 앱은 뜬다. 실패한 이유를 들고 있다가 Runtime 예측을 요청받을 때
    그대로 돌려준다 -- 모델과 무관한 나머지 엔드포인트까지 같이 죽일 이유가 없고,
    가짜 예측으로 대신할 이유는 더더욱 없다.
    """
    app.state.runtime_model = LoadedRuntimeModel.load_from(models_directory())
    yield


app = FastAPI(
    title="GPU AI Scheduler",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(scheduler.router)


@app.exception_handler(RequestValidationError)
async def _validation_failed(request: Request, error: RequestValidationError):
    """검증 실패를 422로 돌려준다. 값이 무한대나 NaN이어도.

    기본 처리기는 오류 상세에 문제가 된 입력값을 그대로 담아 돌려주는데,
    그 값이 ``Infinity`` 나 ``NaN`` 이면 JSON으로 직렬화할 수 없어 응답을 만들다
    터진다. 결과는 500 -- 값을 제대로 거절해 놓고도 서버 잘못처럼 보인다.

    표준 JSON에는 두 리터럴이 없지만 파이썬의 ``json.loads`` 는 받아들이므로
    그렇게 만든 본문은 실제로 여기까지 도달한다. 그래서 직렬화하기 전에 문자열로
    바꿔 둔다 -- 무엇이 왔는지는 그대로 보이면서 응답은 만들어진다.
    """
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": _serialisable(error.errors())},
    )


def _serialisable(value):
    """JSON이 표현할 수 없는 float만 문자열로 바꾼다. 나머지는 그대로."""
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, dict):
        return {key: _serialisable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialisable(item) for item in value]
    if isinstance(value, (str, int, bool, type(None))):
        return value
    # 예외 객체 등 그대로는 직렬화되지 않는 것들. 사람이 읽을 수 있으면 충분하다.
    return str(value)


@app.get("/health")
def health():
    return {"status": "ok"}
