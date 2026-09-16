# Развертывание на Render

Проект подготовлен для Render Blueprint через `render.yaml` в корне репозитория.
Blueprint создает Docker web service `misis-consultations` и PostgreSQL
`misis-consultations-db`.

После подключения репозитория в Render выберите New > Blueprint и этот репозиторий.
Render использует `render.yaml`. На бесплатном тарифе pre-deploy commands недоступны,
поэтому контейнер при старте сам выполнит `alembic upgrade head` и команду
`flask --app wsgi seed-demo-if-empty`, а затем `flask --app wsgi reconcile-demo-accounts`.

Публичный адрес будет вида:

```text
https://misis-consultations.onrender.com
```

Тестовые входы после первого успешного деплоя:

| Роль | Логин | Пароль |
|---|---|---|
| Студент | `2300431` | `Student` |
| Преподаватель | `kurenkov.ee` | `Kurenkov` |
| Администратор | `lxrdx` | значение `DEMO_ADMIN_PASSWORD` в Render |
| Администратор | `flvmming` | значение `DEMO_ADMIN_PASSWORD` в Render |

Пароль администратора задается переменной `DEMO_ADMIN_PASSWORD` в Render. В
Blueprint она помечена как `sync: false`, поэтому значение вводится в панели Render
и не публикуется в репозитории. Команда `reconcile-demo-accounts` активирует
администраторов `lxrdx` и `flvmming`, не меняя пароль уже существующих админов, а
логин `kurenkov.ee` оставляет преподавательским.
