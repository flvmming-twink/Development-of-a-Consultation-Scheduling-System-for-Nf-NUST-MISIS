# Система записи на очные консультации

Начните с [START_HERE.md](START_HERE.md): там находятся тестовые аккаунты,
запуск на Windows, Docker, подключение pgAdmin и восстановление администратора.

Краткая архитектура: браузер → Flask/Jinja → сервисы проверки прав и правил записи
→ SQLAlchemy/psycopg → PostgreSQL. Три роли работают в одном приложении.

Документы: [архитектура](docs/ARCHITECTURE.md), [развёртывание](docs/DEPLOYMENT.md),
[проверки](docs/VERIFICATION.md). Word с двумя ТЗ и календарным планом находится в `docs/`.

Для запуска тестов создайте отдельную БД с именем, заканчивающимся на `_test`.
Тесты очищают только указанную тестовую БД — никогда не используйте рабочую.

```powershell
py -m pip install -r requirements-dev.txt
$env:TEST_DATABASE_URL="postgresql+psycopg://postgres:your_password@localhost:5432/consultations_test"
py -m pytest -q
```

На Linux: `TEST_DATABASE_URL='postgresql+psycopg://...' python -m pytest -q`.
При публикации репозитория в GitHub приложенный workflow повторяет тесты с PostgreSQL 16.
Команда `alembic revision --autogenerate -m "description"` создаёт следующую миграцию;
просмотрите её до `alembic upgrade head`. Исходную `0001_schema.sql` не переписывайте.
