import os

# Секрет JWT обязателен и без значения по умолчанию, а `pulse.api.main` создаёт
# приложение уже при импорте. Выставляем тестовый до сбора тестов.
os.environ["PULSE_API_JWT_SECRET"] = "test-only-secret-not-for-production-use"
