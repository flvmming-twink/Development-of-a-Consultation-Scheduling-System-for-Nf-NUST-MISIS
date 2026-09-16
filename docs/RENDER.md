# Развертывание на Render

Проект подготовлен для Render Blueprint через `render.yaml` в корне репозитория.
Blueprint создает Docker web service `misis-consultations` и PostgreSQL
`misis-consultations-db`.

После подключения репозитория в Render выберите New > Blueprint и репозиторий
`misis-consultations`. Render использует `render.yaml`, выполнит миграции
`alembic upgrade head` перед запуском и один раз заполнит демонстрационные данные.

Публичный адрес будет вида:

```text
https://misis-consultations.onrender.com
```

Тестовые входы после первого успешного деплоя:

| Роль | Логин | Пароль |
|---|---|---|
| Студент | `2300431` | `Student` |
| Преподаватель | `kurenkov.ee` | `Kurenkov` |

Пароль администратора задается переменной `DEMO_ADMIN_PASSWORD` в Render. В
Blueprint она генерируется автоматически, поэтому для публичного стенда пароль
администратора не публикуется в репозитории.
