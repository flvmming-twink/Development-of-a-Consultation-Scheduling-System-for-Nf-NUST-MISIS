"""Generate a local .env without exposing passwords in terminal output."""
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
destination = root / '.env'
if destination.exists():
    print('.env уже существует; файл сохранён без изменений.')
else:
    db_password = secrets.token_urlsafe(24)
    text = (root / '.env.example').read_text(encoding='utf-8')
    text = text.replace('consult_app:CHANGE_ME@','consult_app:' + db_password + '@')
    text = text.replace('POSTGRES_PASSWORD=CHANGE_ME','POSTGRES_PASSWORD=' + db_password)
    text = text.replace('SECRET_KEY=CHANGE_ME','SECRET_KEY=' + secrets.token_urlsafe(48))
    text = text.replace('PGADMIN_DEFAULT_PASSWORD=CHANGE_ME','PGADMIN_DEFAULT_PASSWORD=' + secrets.token_urlsafe(24))
    destination.write_text(text,encoding='utf-8')
    try:
        destination.chmod(0o600)
    except OSError:
        pass
    print('Создан .env. Пароли PostgreSQL и pgAdmin находятся только в этом файле.')
