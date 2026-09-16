# Если на компьютере остаётся Python 3.9

Проект не требует синтаксиса Python новее 3.9. Набор основных зависимостей
разрешается для Python 3.9; функциональная проверка выполнена на Python 3.12.
Docker-вариант использует Python 3.11 внутри контейнера и не зависит от локального Python.
Для слабого компьютера можно использовать обычную установку без Docker.

1. Установите PostgreSQL и pgAdmin отдельно либо подключитесь к PostgreSQL на сервере
   в локальной сети. Создайте БД и роль по START_HERE.md.
2. Откройте терминал в корне проекта и выполните:

```powershell
py -3.9 -m venv .venv
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
Если старый pip не находит совместимый пакет, обновите только pip в окружении,
а не сам Python: `.venv\Scripts\python -m pip install "pip<26"`.
Для сервера организации следует использовать поддерживаемую версию Python;
совместимость старого Python предусмотрена для локального знакомства с проектом.
