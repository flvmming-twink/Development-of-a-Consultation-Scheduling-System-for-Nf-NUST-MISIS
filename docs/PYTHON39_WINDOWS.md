# Переход с Python 3.9 на Windows

Начиная с обновления 21 сентября 2026 года Pillow 12.3 требует Python 3.10+.
Установите Python 3.11 или 3.12 с официального сайта Python и создайте новое
окружение. Старый Python 3.9 текущим набором зависимостей не поддерживается.
Docker-вариант использует Python 3.11 внутри контейнера и не зависит от локального Python.
Для слабого компьютера можно использовать обычную установку без Docker.

1. Установите PostgreSQL и pgAdmin отдельно либо подключитесь к PostgreSQL на сервере
   в локальной сети. Создайте БД и роль по START_HERE.md.
2. Откройте терминал в корне проекта и выполните:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts/configure.py
```

3. В `.env` укажите действующий DATABASE_URL. Затем:

```powershell
.venv\Scripts\alembic upgrade head
.venv\Scripts\python -m flask --app wsgi seed-demo
.venv\Scripts\waitress-serve --listen=127.0.0.1:8000 wsgi:app
```

Если команда `py` отсутствует, используйте путь к установленному `python.exe`.
Используйте короткий путь к окружению, например `C:\Projects\misis-venv`,
если Windows не загружает DLL из длинного пути. При обновлении сохраняйте
существующий `.env` и БД; демонстрационное заполнение на рабочей БД не запускайте.
Старое окружение не удаляйте до проверки нового запуска.
Для экспорта БД нужен `pg_dump` не старше сервера PostgreSQL. На Windows укажите
путь к нему в переменной `PG_DUMP_PATH`, если утилита не находится в PATH.
