from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class DomainError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message, self.status_code = message, status_code


def envelope(data=None, message="success", code=200):
    return {"code": code, "message": message, "data": data}


async def domain_error_handler(request: Request, exc: DomainError):
    return JSONResponse(status_code=exc.status_code, content=envelope(None, exc.message, exc.status_code))


async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content=envelope(None, "invalid request", 422))
