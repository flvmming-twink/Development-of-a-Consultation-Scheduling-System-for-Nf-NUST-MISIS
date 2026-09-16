"""Create a custom-format PostgreSQL dump; never print connection secrets."""
import os
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy.engine import make_url

load_dotenv(Path(__file__).resolve().parents[1] / '.env')
if len(sys.argv) != 2:
    raise SystemExit('Использование: python scripts/backup.py backups/filename.dump')
output = Path(sys.argv[1]).resolve()
if output.exists():
    raise SystemExit('Файл уже существует. Выберите новое имя резервной копии.')
url = make_url(os.environ['DATABASE_URL'])
env = dict(os.environ, PGHOST=url.host or 'localhost', PGPORT=str(url.port or 5432), PGUSER=url.username or '', PGPASSWORD=url.password or '', PGDATABASE=url.database or '')
output.parent.mkdir(parents=True, exist_ok=True)
try:
    subprocess.run(['pg_dump','--format=custom','--file',str(output)],env=env,check=True)
except FileNotFoundError:
    raise SystemExit('pg_dump не найден. Добавьте папку bin PostgreSQL в PATH.')
print('Резервная копия создана:', output)
