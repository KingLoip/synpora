import uvicorn
from backend.app.main import app
from backend.app.saas_api import install
app = install(app)
if __name__ == "__main__":
    uvicorn.run(app,host="0.0.0.0",port=int(__import__("os").getenv("PORT","8000")))
