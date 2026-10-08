FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY SYNPORA_1_0_1_DEPLOY_READY.zip /tmp/synpora-package.zip
RUN python - <<'PY'
import zipfile
from pathlib import Path
z=zipfile.ZipFile("/tmp/synpora-package.zip")
z.extractall("/tmp/pkg")
root=Path("/tmp/pkg/synpora_deploy/synpora_v71_90_work")
for name in ("backend","frontend"):
    src=root/name
    dst=Path("/app")/name
    import shutil
    shutil.copytree(src,dst)
PY
COPY synpora_enhancement.py /tmp/synpora_enhancement.py
RUN python /tmp/synpora_enhancement.py
RUN pip install --no-cache-dir -r backend/requirements.txt
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["uvicorn","backend.app.main:app","--host","0.0.0.0","--port","8000"]