import os

TOKEN = os.getenv('BOT_TOKEN')
REQUIRED_CHANNEL = '@yuldar02'  # или ID канала (например: -1001234567890)
ADMIN_IDS = [5117701931]  # Замените на ваши user_id

if TOKEN is None:
    raise ValueError(
        "Токен бота не найден!"
    )
