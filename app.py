import uvicorn

from backend.main import app


if __name__ == "__main__":
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("backend/alembic.ini"), "head")
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
