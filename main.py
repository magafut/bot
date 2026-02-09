import logging
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from config import TOKEN, REQUIRED_CHANNEL, ADMIN_IDS
from database import (
    init_db, add_user, add_ride, get_user, get_user_rides,
    search_rides, get_driver_contact, add_passenger_search,
    get_passenger_searches, update_ride_status,
    cleanup_expired_rides, delete_old_inactive_rides, get_db,
    get_relevant_rides_for_passenger, add_user_with_terms, update_user_terms,
    get_all_active_rides, get_all_users, get_ride_by_id, delete_ride
)
from datetime import datetime
import re

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def get_role_selection_keyboard(chat_type: str = "private"):
    """Клавиатура для выбора роли - только в личных чатах"""
    if chat_type != "private":
        return None
    keyboard = [
        [KeyboardButton("🚗 Я водитель"), KeyboardButton("👤 Я пассажир")],
        [KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


def get_driver_keyboard(chat_type: str = "private"):
    """Клавиатура для водителей - только в личных чатах"""
    if chat_type != "private":
        return None
    keyboard = [
        [KeyboardButton("🚗 Создать поездку"), KeyboardButton("📋 Мои поездки")],
        [KeyboardButton("📞 Регистрация"), KeyboardButton("🔄 Сменить роль")],
        [KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


def get_passenger_keyboard(chat_type: str = "private"):
    """Клавиатура для пассажиров - только в личных чатах"""
    if chat_type != "private":
        return None
    keyboard = [
        [KeyboardButton("🔍 Найти поездку"), KeyboardButton("📋 Мои поиски")],
        [KeyboardButton("🚗 Актуальные поездки"), KeyboardButton("📞 Регистрация")],
        [KeyboardButton("🔄 Сменить роль"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


def get_registration_keyboard(chat_type: str = "private"):
    """Создает клавиатуру для регистрации - только в личных чатах"""
    if chat_type != "private":
        return None
    keyboard = [
        [KeyboardButton("📱 Поделиться номером", request_contact=True)],
        [KeyboardButton("🔙 Назад")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)


def get_cancel_keyboard(chat_type: str = "private"):
    """Создает клавиатуру с кнопкой отмены - только в личных чатах"""
    if chat_type != "private":
        return None
    keyboard = [
        [KeyboardButton("❌ Отмена")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)


def get_chat_type(update: Update) -> str:
    """Определяет тип чата"""
    if update.message:
        return update.message.chat.type
    elif update.callback_query:
        return update.callback_query.message.chat.type
    return "private"


async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверяет, подписан ли пользователь на обязательный канал."""
    try:
        chat_member = await context.bot.get_chat_member(
            chat_id=REQUIRED_CHANNEL,
            user_id=user_id
        )
        subscribed_statuses = ['member', 'administrator', 'creator']
        return chat_member.status in subscribed_statuses
    except Exception as e:
        logger.error(f"Ошибка при проверке подписки: {e}")
        return True


def format_date_for_display(date_str: str) -> str:
    """Преобразует дату из формата YYYY-MM-DD в DD.MM.YYYY для отображения"""
    try:
        # Пытаемся распарсить разные форматы
        if '.' in date_str:
            # Уже в формате DD.MM.YYYY
            parts = date_str.split('.')
            if len(parts) == 3:
                day, month, year = parts
                return f"{int(day):02d}.{int(month):02d}.{year}"

        # Пробуем формат YYYY-MM-DD
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            return date_obj.strftime('%d.%m.%Y')
        except ValueError:
            pass

        # Возвращаем как есть, если не удалось распарсить
        return date_str
    except Exception as e:
        logger.error(f"Ошибка при форматировании даты {date_str}: {e}")
        return date_str


def parse_date_input(date_str: str) -> tuple:
    """Парсит дату из формата DD.MM.YYYY в YYYY-MM-DD"""
    try:
        # Убираем пробелы
        date_str = date_str.strip()

        # Проверяем формат DD.MM.YYYY
        if re.match(r'^\d{1,2}\.\d{1,2}\.\d{4}$', date_str):
            day, month, year = map(int, date_str.split('.'))

            # Проверяем валидность даты
            datetime(year, month, day)

            # Форматируем в YYYY-MM-DD для хранения в БД
            return f"{year:04d}-{month:02d}-{day:02d}", True, ""
        else:
            return "", False, "❌ Неверный формат даты. Используйте: ДД.ММ.ГГГГ (например: 31.12.2024)"

    except ValueError as e:
        if "day is out of range" in str(e):
            return "", False, "❌ Неверное число дня. Проверьте правильность даты."
        elif "month must be in 1..12" in str(e):
            return "", False, "❌ Неверный номер месяца. Месяц должен быть от 1 до 12."
        else:
            return "", False, f"❌ Ошибка при обработке даты: {str(e)}"
    except Exception as e:
        logger.error(f"Ошибка при парсинге даты {date_str}: {e}")
        return "", False, "❌ Произошла ошибка при обработке даты."


async def show_terms_acceptance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать пользовательское соглашение для принятия"""
    query = update.callback_query
    chat_type = get_chat_type(update)

    if query:
        await query.answer()

    terms_text = "📜 Пожалуйста, ознакомьтесь с пользовательским соглашением:"

    keyboard = [
        [InlineKeyboardButton("📄 Открыть полный текст", url="https://docs.google.com/document/d/1FTKKfsDyG66IGQMgWDJQKgBI5nWf8TgXy5Z2aPti5U0/edit?usp=sharing")],
        [InlineKeyboardButton("✅ Принимаю условия соглашения", callback_data="accept_terms")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text(
            terms_text,
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            terms_text,
            reply_markup=reply_markup
        )


# АДМИН-ПАНЕЛЬ ФУНКЦИИ
def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором"""
    return user_id in ADMIN_IDS


def get_admin_keyboard():
    """Создает клавиатуру для админ-панели"""
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 Все пользователи", callback_data="admin_users")],
        [InlineKeyboardButton("🚗 Все активные поездки", callback_data="admin_active_rides")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🗑️ Очистка БД", callback_data="admin_cleanup")],
        [InlineKeyboardButton("🔙 Выход", callback_data="admin_exit")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отображает админ-панель"""
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return

    await update.message.reply_text(
        "👑 АДМИН-ПАНЕЛЬ\n\n"
        "Выберите действие:",
        reply_markup=get_admin_keyboard()
    )


async def show_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает статистику бота"""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    cursor = conn.cursor()

    try:
        # Получаем статистику
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM users WHERE phone IS NOT NULL")
        registered_users = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM users WHERE accepted_terms = 1")
        accepted_terms = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM rides WHERE is_active = 1")
        active_rides = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM rides")
        total_rides = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM passenger_searches")
        total_searches = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT passenger_id) FROM passenger_searches")
        unique_searchers = cursor.fetchone()[0]

        # Получаем последние 5 регистраций
        cursor.execute('''
            SELECT user_id, username, phone, accepted_terms, accepted_at
            FROM users
            ORDER BY user_id DESC
            LIMIT 5
        ''')
        recent_users = cursor.fetchall()

        stats_text = f"""
📊 СТАТИСТИКА БОТА:

👥 ПОЛЬЗОВАТЕЛИ:
• Всего пользователей: {total_users}
• Зарегистрировано: {registered_users}
• Приняли соглашение: {accepted_terms}

🚗 ПОЕЗДКИ:
• Активных поездок: {active_rides}
• Всего поездок: {total_rides}

🔍 ПОИСКИ:
• Всего поисков: {total_searches}
• Уникальных искателей: {unique_searchers}

📈 ПОСЛЕДНИЕ РЕГИСТРАЦИИ:
"""

        for user in recent_users:
            user_id, username, phone, accepted_terms_flag, accepted_at = user
            status = "✅" if accepted_terms_flag else "❌"
            phone_status = "📱" if phone else "❌"
            username = username or "Нет имени"
            stats_text += f"• {status} {phone_status} ID: {user_id}, Имя: {username}\n"

        await query.edit_message_text(
            stats_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Обновить", callback_data="admin_stats")],
                [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]
            ])
        )

    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}")
        await query.edit_message_text(
            "❌ Ошибка при получении статистики.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]
            ])
        )
    finally:
        conn.close()


async def show_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает всех пользователей"""
    query = update.callback_query
    await query.answer()

    try:
        users = get_all_users()

        if not users:
            await query.edit_message_text(
                "❌ В базе данных нет пользователей.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]
                ])
            )
            return

        users_text = "👥 ВСЕ ПОЛЬЗОВАТЕЛИ:\n\n"

        for user in users[:50]:  # Ограничиваем 50 пользователями
            user_id, username, phone, accepted_terms, accepted_at = user
            status = "✅" if accepted_terms else "❌"
            phone_status = phone if phone else "Нет телефона"
            username = username or "Нет имени"
            users_text += f"{status} ID: {user_id}\nИмя: {username}\nТел: {phone_status}\n"

            if accepted_at:
                users_text += f"Принял: {accepted_at[:10]}\n"
            users_text += "─" * 30 + "\n"

        if len(users) > 50:
            users_text += f"\n... и ещё {len(users) - 50} пользователей"

        keyboard = [
            [InlineKeyboardButton("🔄 Обновить", callback_data="admin_users")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]
        ]

        await query.edit_message_text(
            users_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Ошибка при получении пользователей: {e}")
        await query.edit_message_text(
            "❌ Ошибка при получении списка пользователей.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]
            ])
        )


async def show_active_rides(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает все активные поездки"""
    query = update.callback_query
    await query.answer()

    try:
        rides = get_all_active_rides()

        if not rides:
            await query.edit_message_text(
                "🚗 Активных поездок нет.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]
                ])
            )
            return

        rides_text = "🚗 ВСЕ АКТИВНЫЕ ПОЕЗДКИ:\n\n"

        for ride in rides[:30]:  # Ограничиваем 30 поездками
            ride_id, driver_id, driver_username, from_loc, to_loc, date, time, seats, is_active, last_check, created_at = ride

            # Форматируем дату
            date_display = format_date_for_display(date)

            rides_text += f"📍 Поездка #{ride_id}\n"
            rides_text += f"Маршрут: {from_loc} → {to_loc}\n"
            rides_text += f"Дата: {date_display} в {time}\n"
            rides_text += f"Мест: {seats}\n"
            rides_text += f"Водитель: {driver_username} (ID: {driver_id})\n"
            rides_text += f"Создана: {created_at[:16] if created_at else 'N/A'}\n"
            rides_text += "─" * 40 + "\n"

        if len(rides) > 30:
            rides_text += f"\n... и ещё {len(rides) - 30} поездок"

        # Создаем клавиатуру с кнопками управления
        keyboard = []

        # Добавляем кнопки удаления для первых 5 поездок
        for ride in rides[:5]:
            ride_id = ride[0]
            from_loc = ride[3][:15]
            to_loc = ride[4][:15]
            keyboard.append([
                InlineKeyboardButton(
                    f"🗑️ Удалить #{ride_id}",
                    callback_data=f"admin_delete_ride_{ride_id}"
                )
            ])

        keyboard.extend([
            [InlineKeyboardButton("🔄 Обновить", callback_data="admin_active_rides")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]
        ])

        await query.edit_message_text(
            rides_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Ошибка при получении поездок: {e}")
        await query.edit_message_text(
            "❌ Ошибка при получении списка поездок.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]
            ])
        )


async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Начинает процесс рассылки"""
    query = update.callback_query
    await query.answer()

    context.user_data['broadcast_step'] = 'waiting_message'

    await query.edit_message_text(
        "📢 РАССЫЛКА СООБЩЕНИЙ\n\n"
        "Пожалуйста, введите сообщение для рассылки всем пользователям.\n"
        "Вы можете использовать HTML-разметку.\n\n"
        "Для отмены введите /cancel",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Отмена", callback_data="admin_back")]
        ])
    )


async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает ввод сообщения для рассылки"""
    if 'broadcast_step' not in context.user_data:
        return

    message_text = update.message.text

    # Сохраняем сообщение и переходим к подтверждению
    context.user_data['broadcast_message'] = message_text
    context.user_data['broadcast_step'] = 'confirm'

    # Получаем количество пользователей
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    user_count = cursor.fetchone()[0]
    conn.close()

    await update.message.reply_text(
        f"📢 ПОДТВЕРЖДЕНИЕ РАССЫЛКИ\n\n"
        f"Сообщение будет отправлено {user_count} пользователям:\n\n"
        f"---\n{message_text[:500]}\n---\n\n"
        f"Подтвердите отправку:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Отправить", callback_data="admin_confirm_broadcast")],
            [InlineKeyboardButton("❌ Отмена", callback_data="admin_back")]
        ])
    )


async def confirm_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Подтверждает и отправляет рассылку"""
    query = update.callback_query
    await query.answer()

    if 'broadcast_message' not in context.user_data:
        await query.edit_message_text(
            "❌ Сообщение для рассылки не найдено.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]
            ])
        )
        return

    message_text = context.user_data['broadcast_message']

    await query.edit_message_text(
        "⏳ Начинаю рассылку... Это может занять некоторое время."
    )

    # Получаем всех пользователей
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()

    total_users = len(users)
    successful = 0
    failed = 0

    # Отправляем сообщение каждому пользователю
    for i, (user_id,) in enumerate(users, 1):
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📢 СООБЩЕНИЕ ОТ АДМИНИСТРАЦИИ:\n\n{message_text}",
                parse_mode='HTML'
            )
            successful += 1

            # Обновляем прогресс каждые 10 отправок
            if i % 10 == 0:
                await query.edit_message_text(
                    f"⏳ Рассылка... Отправлено {i}/{total_users}"
                )

        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения пользователю {user_id}: {e}")
            failed += 1

    # Очищаем данные рассылки
    if 'broadcast_step' in context.user_data:
        del context.user_data['broadcast_step']
    if 'broadcast_message' in context.user_data:
        del context.user_data['broadcast_message']

    await query.edit_message_text(
        f"✅ РАССЫЛКА ЗАВЕРШЕНА\n\n"
        f"• Всего пользователей: {total_users}\n"
        f"• Успешно отправлено: {successful}\n"
        f"• Не удалось отправить: {failed}\n\n"
        f"Сообщение было отправлено всем пользователям бота.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 В админ-панель", callback_data="admin_back")]
        ])
    )


async def perform_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выполняет очистку базы данных"""
    query = update.callback_query
    await query.answer()

    try:
        await query.edit_message_text("⏳ Выполняю очистку базы данных...")

        # Очищаем просроченные поездки
        expired_count = cleanup_expired_rides()

        # Удаляем старые неактивные поездки
        deleted_count = delete_old_inactive_rides()

        result_text = f"✅ ОЧИСТКА ЗАВЕРШЕНА\n\n"
        result_text += f"• Просроченных поездок удалено: {expired_count}\n"
        result_text += f"• Старых неактивных поездок удалено: {deleted_count}\n"

        await query.edit_message_text(
            result_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Ещё раз", callback_data="admin_cleanup")],
                [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]
            ])
        )

    except Exception as e:
        logger.error(f"Ошибка при очистке БД: {e}")
        await query.edit_message_text(
            "❌ Ошибка при выполнении очистки базы данных.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="admin_back")]
            ])
        )


async def delete_ride_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удаляет поездку из админ-панели"""
    query = update.callback_query
    await query.answer()

    try:
        ride_id = int(query.data.split("_")[3])

        # Получаем информацию о поездке перед удалением
        ride_info = get_ride_by_id(ride_id)

        if not ride_info:
            await query.answer("❌ Поездка не найдена", show_alert=True)
            return

        # Удаляем поездку
        delete_ride(ride_id)

        await query.answer(f"✅ Поездка #{ride_id} удалена", show_alert=True)

        # Обновляем список поездок
        await show_active_rides(update, context)

    except Exception as e:
        logger.error(f"Ошибка при удалении поездки: {e}")
        await query.answer("❌ Ошибка при удалении поездки", show_alert=True)


async def handle_admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик callback-запросов для админ-панели"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "admin_stats":
        await show_admin_stats(update, context)

    elif data == "admin_users":
        await show_all_users(update, context)

    elif data == "admin_active_rides":
        await show_active_rides(update, context)

    elif data == "admin_broadcast":
        await start_broadcast(update, context)

    elif data == "admin_cleanup":
        await perform_cleanup(update, context)

    elif data == "admin_confirm_broadcast":
        await confirm_broadcast(update, context)

    elif data == "admin_back":
        await query.edit_message_text(
            "👑 АДМИН-ПАНЕЛЬ\n\nВыберите действие:",
            reply_markup=get_admin_keyboard()
        )

    elif data == "admin_exit":
        await query.delete_message()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Админ-панель закрыта."
        )

    elif data.startswith("admin_delete_ride_"):
        await delete_ride_admin(update, context)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start."""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        # В группах игнорируем или отправляем короткое сообщение
        await update.message.reply_text(
            "🤖 Я работаю только в личных сообщениях. Напишите мне в ЛС: @yuldar02bot"
        )
        return

    user = update.effective_user
    chat_type = get_chat_type(update)
    is_subscribed = await check_subscription(user.id, context)

    welcome_text = (
        f"Привет, {user.first_name}! 👋\n"
        "Я бот для поиска попутчиков.\n\n"
        "Пожалуйста, выберите вашу роль:"
    )

    if not is_subscribed:
        keyboard = [
            [InlineKeyboardButton("📢 Подписаться на канал",
                                url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")],
            [InlineKeyboardButton("✅ Я подписался",
                                callback_data="check_subscription")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        welcome_text = (
            f"Привет, {user.first_name}! 👋\n"
            "Я бот для поиска попутчиков.\n\n"
            "Для использования бота необходимо подписаться на наш канал!\n\n"
            "Пожалуйста, подпишитесь на канал ниже, затем нажмите 'Я подписался'"
        )

        await update.message.reply_text(
            welcome_text,
            reply_markup=reply_markup
        )
    else:
        # Очищаем предыдущую роль при новом старте
        if 'role' in context.user_data:
            del context.user_data['role']

        # Проверяем, принимал ли пользователь соглашение
        user_data = get_user(user.id)
        if user_data:
            # Пользователь зарегистрирован, проверяем accepted_terms
            if len(user_data) > 3:  # Проверяем, есть ли поле accepted_terms
                accepted_terms = user_data[3]
                if not accepted_terms:
                    # Показываем соглашение для принятия
                    await show_terms_acceptance(update, context)
                    return
        else:
            # Пользователь не зарегистрирован, показываем соглашение
            await show_terms_acceptance(update, context)
            return

        await update.message.reply_text(
            welcome_text,
            reply_markup=get_role_selection_keyboard(chat_type)
        )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатий на inline-кнопки для проверки подписки."""
    query = update.callback_query
    await query.answer()

    if query.data == "check_subscription":
        user_id = query.from_user.id
        chat_type = query.message.chat.type
        is_subscribed = await check_subscription(user_id, context)

        if is_subscribed:
            # Проверяем, принимал ли пользователь соглашение
            user_data = get_user(user_id)
            if user_data:
                # Пользователь зарегистрирован, проверяем accepted_terms
                if len(user_data) > 3:  # Проверяем, есть ли поле accepted_terms
                    accepted_terms = user_data[3]
                    if not accepted_terms:
                        # Показываем соглашение для принятия
                        await show_terms_acceptance(update, context)
                        return
            else:
                # Пользователь не зарегистрирован, показываем соглашение
                await show_terms_acceptance(update, context)
                return

            # Удаляем старое сообщение с inline-кнопками
            await query.delete_message()
            # Отправляем новое сообщение с обычной клавиатурой
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Отлично! Вы подписаны на канал!\n\nТеперь вы можете пользоваться всеми функциями бота.\nВыберите вашу роль:",
                reply_markup=get_role_selection_keyboard(chat_type)
            )
        else:
            keyboard = [
                [InlineKeyboardButton("📢 Подписаться на канал",
                                    url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")],
                [InlineKeyboardButton("🔄 Проверить подписку",
                                    callback_data="check_subscription")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                "Вы еще не подписались на канал!\n\n"
                "Пожалуйста, подпишитесь на канал и нажмите 'Проверить подписку'",
                reply_markup=reply_markup
            )

    elif query.data == "accept_terms":
        # Обновляем базу данных - пользователь принял соглашение
        user_id = query.from_user.id
        chat_type = query.message.chat.type

        # Проверяем, есть ли пользователь в базе
        user_data = get_user(user_id)
        if not user_data:
            # Если пользователя нет, регистрируем его с accepted_terms = 1
            username = query.from_user.username or query.from_user.first_name
            # Используем новую функцию add_user_with_terms
            add_user_with_terms(user_id, username, None, True)
        else:
            # Обновляем существующего пользователя
            update_user_terms(user_id, True)

        # Удаляем старое сообщение с inline-кнопками
        await query.delete_message()
        # Отправляем новое сообщение с обычной клавиатурой
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="✅ Вы автоматически соглашаетесь с пользовательским соглашением и политикой конфиденциальности.\n\nТеперь вы можете пользоваться всеми функциями бота.\nВыберите вашу роль:",
            reply_markup=get_role_selection_keyboard(chat_type)
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений (кнопок меню)."""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        # В группах игнорируем сообщения
        return

    # Проверяем, не является ли это сообщением для рассылки
    if 'broadcast_step' in context.user_data:
        await handle_broadcast_message(update, context)
        return

    user_id = update.effective_user.id
    message_text = update.message.text
    chat_type = get_chat_type(update)

    # Проверяем, принимал ли пользователь соглашение
    user_data = get_user(user_id)
    if user_data and len(user_data) > 3:
        accepted_terms = user_data[3]
        if not accepted_terms and message_text not in ["/start", "/help", "/terms", "/admin"]:
            # Пользователь не принял соглашение
            await update.message.reply_text(
                "Для использования бота необходимо принять пользовательское соглашение!\n\n"
                "Пожалуйста, сначала запустите /start и примите условия соглашения."
            )
            return
    elif user_data and message_text not in ["/start", "/help", "/terms", "/admin"]:
        # Старая запись пользователя без поле accepted_terms
        # Показываем соглашение
        await show_terms_acceptance(update, context)
        return
    elif not user_data and message_text not in ["/start", "/help", "/terms", "/admin"]:
        # Пользователь не зарегистрирован и не принимал соглашение
        await update.message.reply_text(
            "Для использования бота необходимо принять пользовательское соглашение!\n\n"
            "Пожалуйста, сначала запустите /start и примите условия соглашения."
        )
        return

    # Проверяем подписку для большинства команд
    if message_text in ["🚗 Я водитель", "👤 Я пассажир", "🚗 Создать поездку",
                       "🔍 Найти поездку", "📋 Мои поездки", "📋 Мои поиски",
                       "🚗 Актуальные поездки", "📞 Регистрация", "🔄 Сменить роль"]:
        is_subscribed = await check_subscription(user_id, context)
        if not is_subscribed:
            keyboard = [
                [InlineKeyboardButton("📢 Подписаться на канал",
                                    url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")],
                [InlineKeyboardButton("✅ Я подписался",
                                    callback_data="check_subscription")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                "Для использования этой функции необходимо подписаться на канал!\n\n"
                "Пожалуйста, подпишитесь на канал ниже, затем нажмите 'Я подписался'",
                reply_markup=reply_markup
            )
            return

    # Обработка выбора роли
    if message_text == "🚗 Я водитель":
        # Проверяем регистрацию для водителя
        user_data = get_user(user_id)
        if not user_data or not user_data[2]:  # user_data[2] - телефон
            await update.message.reply_text(
                "❌ Для использования роли водителя необходимо зарегистрироваться!\n\n"
                "Пожалуйста, сначала зарегистрируйтесь через кнопку '📞 Регистрация', "
                "а затем выберите роль водителя снова.",
                reply_markup=get_registration_keyboard(chat_type)
            )
            return

        context.user_data['role'] = 'driver'
        await update.message.reply_text(
            "✅ Вы выбрали роль Водителя\n\n"
            "Теперь вы можете:\n"
            "• 🚗 Создать поездку - предложить другим поехать с вами\n"
            "• 📋 Мои поездки - просмотреть ваши созданные поездки\n"
            "• 📞 Регистрация - зарегистрироваться в системе\n"
            "• 🔄 Сменить роль - переключиться на роль пассажира\n\n"
            "Выберите действие:",
            reply_markup=get_driver_keyboard(chat_type)
        )

    elif message_text == "👤 Я пассажир":
        context.user_data['role'] = 'passenger'
        await update.message.reply_text(
            "✅ Вы выбрали роль Пассажира\n\n"
            "Теперь вы можете:\n"
            "• 🔍 Найти поездку - найти попутчиков\n"
            "• 📋 Мои поиски - история ваших поисков\n"
            "• 🚗 Актуальные поездки - поездки по вашим поискам\n"
            "• 📞 Регистрация - зарегистрироваться в системе\n"
            "• 🔄 Сменить роль - переключиться на роль водителя\n\n"
            "Выберите действие:",
            reply_markup=get_passenger_keyboard(chat_type)
        )

    elif message_text == "🔄 Сменить роль":
        # Очищаем текущую роль
        if 'role' in context.user_data:
            del context.user_data['role']
        await update.message.reply_text(
            "Смена роли\n\n"
            "Пожалуйста, выберите новую роль:",
            reply_markup=get_role_selection_keyboard(chat_type)
        )

    # Обработка команд водителя
    elif message_text == "🚗 Создать поездку":
        if context.user_data.get('role') == 'driver':
            # Дополнительная проверка регистрации
            user_data = get_user(user_id)
            if not user_data or not user_data[2]:  # user_data[2] - телефон
                await update.message.reply_text(
                    "❌ Для создания поездки необходимо зарегистрироваться!\n\n"
                    "Пожалуйста, сначала зарегистрируйтесь через кнопку '📞 Регистрация'.",
                    reply_markup=get_driver_keyboard(chat_type)
                )
                return
            await start_create_ride(update, context)
        else:
            await update.message.reply_text(
                "Эта функция доступна только для водителей.",
                reply_markup=get_role_selection_keyboard(chat_type)
            )

    elif message_text == "📋 Мои поездки":
        if context.user_data.get('role') == 'driver':
            # Дополнительная проверка регистрации
            user_data = get_user(user_id)
            if not user_data or not user_data[2]:  # user_data[2] - телефон
                await update.message.reply_text(
                    "❌ Для просмотра ваших поездок необходимо зарегистрироваться!\n\n"
                    "Пожалуйста, сначала зарегистрируйтесь через кнопку '📞 Регистрация'.",
                    reply_markup=get_driver_keyboard(chat_type)
                )
                return
            await my_rides(update, context)
        else:
            await update.message.reply_text(
                "Эта функция доступна только для водителей.",
                reply_markup=get_role_selection_keyboard(chat_type)
            )

    # Обработка команд пассажира
    elif message_text == "🔍 Найти поездку":
        if context.user_data.get('role') == 'passenger':
            # Дополнительная проверка регистрации для сохранения поисков
            user_data = get_user(user_id)
            if not user_data or not user_data[2]:  # user_data[2] - телефон
                await update.message.reply_text(
                    "ℹ️ Поиск работает без регистрации, но для сохранения истории поисков и получения контактов водителей необходима регистрация.\n\n"
                    "Хотите зарегистрироваться сейчас?",
                    reply_markup=get_registration_keyboard(chat_type)
                )
                # Устанавливаем флаг, что пользователь хочет зарегистрироваться после поиска
                context.user_data['register_after_search'] = True
                return
            await start_search_ride(update, context)
        else:
            await update.message.reply_text(
                "Эта функция доступна только для пассажиров.",
                reply_markup=get_role_selection_keyboard(chat_type)
            )

    elif message_text == "📋 Мои поиски":
        if context.user_data.get('role') == 'passenger':
            # Дополнительная проверка регистрации
            user_data = get_user(user_id)
            if not user_data or not user_data[2]:  # user_data[2] - телефон
                await update.message.reply_text(
                    "❌ Для просмотра истории поисков необходимо зарегистрироваться!\n\n"
                    "Пожалуйста, сначала зарегистрируйтесь через кнопку '📞 Регистрация'.",
                    reply_markup=get_passenger_keyboard(chat_type)
                )
                return
            await my_searches(update, context)
        else:
            await update.message.reply_text(
                "Эта функция доступна только для пассажиров.",
                reply_markup=get_role_selection_keyboard(chat_type)
            )

    elif message_text == "🚗 Актуальные поездки":
        if context.user_data.get('role') == 'passenger':
            # Дополнительная проверка регистрации
            user_data = get_user(user_id)
            if not user_data or not user_data[2]:  # user_data[2] - телефон
                await update.message.reply_text(
                    "❌ Для просмотра актуальных поездок необходимо зарегистрироваться!\n\n"
                    "Пожалуйста, сначала зарегистрируйтесь через кнопку '📞 Регистрация'.",
                    reply_markup=get_passenger_keyboard(chat_type)
                )
                return
            await relevant_rides(update, context)
        else:
            await update.message.reply_text(
                "Эта функция доступна только для пассажиров.",
                reply_markup=get_role_selection_keyboard(chat_type)
            )

    # Общие команды
    elif message_text == "📞 Регистрация":
        await start_registration(update, context)

    elif message_text == "❓ Помощь":
        await help_command(update, context)

    elif message_text == "❌ Отмена":
        await cancel_command(update, context)

    elif message_text == "🔙 Назад":
        await back_to_main(update, context)

    else:
        # Обработка ввода данных
        if 'create_ride_step' in context.user_data:
            # Проверяем регистрацию при создании поездки
            user_data = get_user(user_id)
            if not user_data or not user_data[2]:
                await update.message.reply_text(
                    "❌ Для создания поездки необходимо зарегистрироваться!\n\n"
                    "Пожалуйста, сначала зарегистрируйтесь через кнопку '📞 Регистрация'.",
                    reply_markup=get_driver_keyboard(chat_type)
                )
                # Очищаем шаг создания поездки
                if 'create_ride_step' in context.user_data:
                    del context.user_data['create_ride_step']
                return
            await handle_create_ride_step(update, context)
        elif 'search_ride_step' in context.user_data:
            await handle_search_ride_step(update, context)
        elif 'registration_step' in context.user_data:
            await handle_registration_step(update, context)
        else:
            # Проверяем, выбрана ли роль
            role = context.user_data.get('role')
            if role == 'driver':
                await update.message.reply_text(
                    "Используйте меню водителя:",
                    reply_markup=get_driver_keyboard(chat_type)
                )
            elif role == 'passenger':
                await update.message.reply_text(
                    "Используйте меню пассажира:",
                    reply_markup=get_passenger_keyboard(chat_type)
                )
            else:
                await update.message.reply_text(
                    "Пожалуйста, сначала выберите вашу роль:",
                    reply_markup=get_role_selection_keyboard(chat_type)
                )


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик получения контакта от пользователя."""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        return

    user_id = update.effective_user.id
    chat_type = get_chat_type(update)

    if update.message.contact:
        phone = update.message.contact.phone_number
        username = update.effective_user.username or update.effective_user.first_name

        try:
            # Используем новую функцию с поддержкой accepted_terms
            user_data = get_user(user_id)
            if user_data:
                # Пользователь уже есть, обновляем телефон и accepted_terms если нужно
                if len(user_data) > 3:
                    accepted_terms = user_data[3]
                    add_user_with_terms(user_id, username, phone, accepted_terms)
                else:
                    add_user_with_terms(user_id, username, phone, True)
            else:
                add_user_with_terms(user_id, username, phone, True)

            # Проверяем, хочет ли пользователь продолжить поиск после регистрации
            if 'register_after_search' in context.user_data and context.user_data['register_after_search']:
                del context.user_data['register_after_search']
                # Возвращаем к поиску
                await update.message.reply_text(
                    "✅ Регистрация успешна!\n\n"
                    "Теперь вы можете использовать все функции пассажира.\n"
                    "Попробуйте снова:",
                    reply_markup=get_passenger_keyboard(chat_type)
                )
                return

            # Определяем текущую роль пользователя для возврата в нужное меню
            role = context.user_data.get('role')
            if role == 'driver':
                keyboard = get_driver_keyboard(chat_type)
                role_text = "водителя"
            elif role == 'passenger':
                keyboard = get_passenger_keyboard(chat_type)
                role_text = "пассажира"
            else:
                keyboard = get_role_selection_keyboard(chat_type)
                role_text = "пользователя"

            await update.message.reply_text(
                f"✅ Регистрация успешна!\n"
                f"👤 Имя: {update.effective_user.first_name}\n"
                f"📱 Телефон: {phone}\n"
                f"👤 Роль: {role_text}\n\n"
                "Теперь вы можете пользоваться всеми функциями бота.",
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"Ошибка при регистрации: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при регистрации. Попробуйте позже.",
                reply_markup=get_role_selection_keyboard(chat_type)
            )


async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Начало процесса регистрации."""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        await update.message.reply_text(
            "🤖 Я работаю только в личных сообщениях. Напишите мне в ЛС: @yuldar02bot"
        )
        return

    user_id = update.effective_user.id
    chat_type = get_chat_type(update)
    user = get_user(user_id)

    if user:
        await update.message.reply_text(
            f"✅ Вы уже зарегистрированы!\n"
            f"👤 Имя: {user[1]}\n"
            f"📱 Телефон: {user[2]}\n\n"
            "Если нужно изменить данные, отправьте новый номер телефона в формате 89123456789.",
            reply_markup=get_registration_keyboard(chat_type)
        )
    else:
        await update.message.reply_text(
            "📝 Регистрация\n\n"
            "Для регистрации нажмите кнопку ниже, чтобы поделиться номером телефона.\n"
            "Или отправьте номер вручную в формате 89123456789:",
            reply_markup=get_registration_keyboard(chat_type)
        )


async def handle_registration_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка ручного ввода номера телефона."""
    message_text = update.message.text
    chat_type = get_chat_type(update)

    # Проверка формата номера телефона
    phone_pattern = r'^(\+7|7|8)?[\s\-]?\(?[489][0-9]{2}\)?[\s\-]?[0-9]{3}[\s\-]?[0-9]{2}[\s\-]?[0-9]{2}$'

    if re.match(phone_pattern, message_text):
        user_id = update.effective_user.id
        username = update.effective_user.username or update.effective_user.first_name

        # Нормализация номера телефона
        phone = re.sub(r'[\s\-\(\)]', '', message_text)
        if phone.startswith('8'):
            phone = '7' + phone[1:]
        elif phone.startswith('+7'):
            phone = phone[1:]

        try:
            # Используем новую функцию с поддержкой accepted_terms
            user_data = get_user(user_id)
            if user_data and len(user_data) > 3:
                accepted_terms = user_data[3]
                add_user_with_terms(user_id, username, phone, accepted_terms)
            else:
                add_user_with_terms(user_id, username, phone, True)

            # Проверяем, хочет ли пользователь продолжить поиск после регистрации
            if 'register_after_search' in context.user_data and context.user_data['register_after_search']:
                del context.user_data['register_after_search']
                # Возвращаем к поиску
                await update.message.reply_text(
                    "✅ Регистрация успешна!\n\n"
                    "Теперь вы можете использовать все функции пассажира.\n"
                    "Попробуйте снова:",
                    reply_markup=get_passenger_keyboard(chat_type)
                )
                return

            # Определяем текущую роль пользователя
            role = context.user_data.get('role')
            if role == 'driver':
                keyboard = get_driver_keyboard(chat_type)
                role_text = "водителя"
            elif role == 'passenger':
                keyboard = get_passenger_keyboard(chat_type)
                role_text = "пассажира"
            else:
                keyboard = get_role_selection_keyboard(chat_type)
                role_text = "пользователя"

            await update.message.reply_text(
                f"✅ Регистрация успешна!\n"
                f"👤 Имя: {update.effective_user.first_name}\n"
                f"📱 Телефон: {phone}\n"
                f"👤 Роль: {role_text}\n\n"
                "Теперь вы можете пользоваться всеми функциями бота.",
                reply_markup=keyboard
            )

            # Очищаем шаг регистрации
            if 'registration_step' in context.user_data:
                del context.user_data['registration_step']

        except Exception as e:
            logger.error(f"Ошибка при регистрации: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при регистрации. Попробуйте позже.",
                reply_markup=get_role_selection_keyboard(chat_type)
            )
    else:
        await update.message.reply_text(
            "❌ Неверный формат номера телефона.\n"
            "Пожалуйста, отправьте номер в формате 89123456789 или нажмите кнопку 'Поделиться номером'.",
            reply_markup=get_registration_keyboard(chat_type)
        )


async def start_create_ride(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Начало процесса создания поездки."""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        return

    user_id = update.effective_user.id
    chat_type = get_chat_type(update)
    user = get_user(user_id)

    if not user:
        await update.message.reply_text(
            "❌ Сначала зарегистрируйтесь!\n"
            "Нажмите кнопку '📞 Регистрация'",
            reply_markup=get_driver_keyboard(chat_type)
        )
        return

    context.user_data['create_ride_step'] = 'from'
    await update.message.reply_text(
        "🚗 Создание поездки\n\n"
        "Шаг 1/5: Откуда выезжаете?\n"
        "Например: Москва",
        reply_markup=get_cancel_keyboard(chat_type)
    )


async def handle_create_ride_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка шагов создания поездки."""
    step = context.user_data.get('create_ride_step')
    message_text = update.message.text
    chat_type = get_chat_type(update)

    if step == 'from':
        context.user_data['from_location'] = message_text
        context.user_data['create_ride_step'] = 'to'
        await update.message.reply_text(
            "Шаг 2/5: Куда едете?\n"
            "Например: Санкт-Петербург",
            reply_markup=get_cancel_keyboard(chat_type)
        )

    elif step == 'to':
        context.user_data['to_location'] = message_text
        context.user_data['create_ride_step'] = 'date'
        await update.message.reply_text(
            "Шаг 3/5: Дата поездки?\n"
            "Формат: ДД.ММ.ГГГГ\n"
            "Например: 31.12.2024",
            reply_markup=get_cancel_keyboard(chat_type)
        )

    elif step == 'date':
        # Парсим дату в формате ДД.ММ.ГГГГ
        parsed_date, is_valid, error_message = parse_date_input(message_text)

        if is_valid:
            context.user_data['date'] = parsed_date  # Сохраняем в формате YYYY-MM-DD для БД
            context.user_data['create_ride_step'] = 'time'
            await update.message.reply_text(
                "Шаг 4/5: Время выезда?\n"
                "Формат: ЧЧ:ММ\n"
                "Например: 14:30",
                reply_markup=get_cancel_keyboard(chat_type)
            )
        else:
            await update.message.reply_text(
                f"{error_message}\n"
                "Попробуйте снова:",
                reply_markup=get_cancel_keyboard(chat_type)
            )

    elif step == 'time':
        try:
            # Проверяем формат времени
            datetime.strptime(message_text, '%H:%M')
            context.user_data['time'] = message_text
            context.user_data['create_ride_step'] = 'seats'
            await update.message.reply_text(
                "Шаг 5/5: Сколько свободных мест?\n"
                "Введите число от 1 до 10:",
                reply_markup=get_cancel_keyboard(chat_type)
            )
        except ValueError:
            await update.message.reply_text(
                "❌ Неверный формат времени. Используйте: ЧЧ:ММ (например: 14:30)\n"
                "Попробуйте снова:",
                reply_markup=get_cancel_keyboard(chat_type)
            )

    elif step == 'seats':
        try:
            seats = int(message_text)
            if seats < 1 or seats > 10:
                raise ValueError

            # Создаем поездку
            user_id = update.effective_user.id
            username = update.effective_user.username or update.effective_user.first_name

            add_ride(
                user_id,
                context.user_data['from_location'],
                context.user_data['to_location'],
                context.user_data['date'],  # В формате YYYY-MM-DD
                context.user_data['time'],
                seats
            )

            # Форматируем дату для отображения пользователю
            display_date = format_date_for_display(context.user_data['date'])

            await update.message.reply_text(
                f"✅ Поездка создана!\n\n"
                f"📍 Откуда: {context.user_data['from_location']}\n"
                f"📍 Куда: {context.user_data['to_location']}\n"
                f"📅 Дата: {display_date}\n"
                f"🕒 Время: {context.user_data['time']}\n"
                f"👥 Свободных мест: {seats}\n\n"
                f"👤 Водитель: {username}",
                reply_markup=get_driver_keyboard(chat_type)
            )

            # Очищаем данные
            for key in ['create_ride_step', 'from_location', 'to_location', 'date', 'time']:
                if key in context.user_data:
                    del context.user_data[key]

        except ValueError:
            await update.message.reply_text(
                "❌ Количество мест должно быть числом от 1 до 10\n"
                "Попробуйте снова:",
                reply_markup=get_cancel_keyboard(chat_type)
            )


async def start_search_ride(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Начало процесса поиска поездки."""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        return

    chat_type = get_chat_type(update)
    context.user_data['search_ride_step'] = 'from'
    await update.message.reply_text(
        "🔍 Поиск поездки\n\n"
        "Шаг 1/3: Откуда ищете поездку?\n"
        "Например: Москва",
        reply_markup=get_cancel_keyboard(chat_type)
    )


async def handle_search_ride_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка шагов поиска поездки."""
    step = context.user_data.get('search_ride_step')
    message_text = update.message.text
    chat_type = get_chat_type(update)

    if step == 'from':
        context.user_data['search_from'] = message_text
        context.user_data['search_ride_step'] = 'to'
        await update.message.reply_text(
            "Шаг 2/3: Куда нужно доехать?\n"
            "Например: Санкт-Петербург",
            reply_markup=get_cancel_keyboard(chat_type)
        )

    elif step == 'to':
        context.user_data['search_to'] = message_text
        context.user_data['search_ride_step'] = 'date'
        await update.message.reply_text(
            "Шаг 3/3: На какую дату?\n"
            "Формат: ДД.ММ.ГГГГ\n"
            "Например: 31.12.2024",
            reply_markup=get_cancel_keyboard(chat_type)
        )

    elif step == 'date':
        # Парсим дату в формате ДД.ММ.ГГГГ
        parsed_date, is_valid, error_message = parse_date_input(message_text)

        if is_valid:
            from_location = context.user_data['search_from']
            to_location = context.user_data['search_to']
            date = parsed_date  # В формате YYYY-MM-ДД для поиска в БД

            # Ищем поездки
            rides = search_rides(from_location, to_location, date)

            # Сохраняем поиск в историю (только если пользователь зарегистрирован)
            user_data = get_user(update.effective_user.id)
            if user_data and user_data[2]:  # Проверяем наличие телефона
                try:
                    add_passenger_search(update.effective_user.id, from_location, to_location, date)
                except Exception as e:
                    logger.error(f"Ошибка при сохранении поиска: {e}")

            # Очищаем данные
            for key in ['search_ride_step', 'search_from', 'search_to']:
                if key in context.user_data:
                    del context.user_data[key]

            if not rides:
                # Форматируем дату для отображения
                display_date = format_date_for_display(date)

                await update.message.reply_text(
                    f"🔍 По вашему запросу ничего не найдено.\n"
                    f"📍 Маршрут: {from_location} → {to_location}\n"
                    f"📅 Дата: {display_date}\n\n"
                    "Попробуйте изменить параметры поиска.",
                    reply_markup=get_passenger_keyboard(chat_type)
                )
                return

            # Форматируем дату для отображения
            display_date = format_date_for_display(date)

            # Формируем ответ с inline-кнопками
            response = f"🎯 Найдено поездок: {len(rides)}\n\n"
            response += f"📍 Маршрут: {from_location} → {to_location}\n"
            response += f"📅 Дата: {display_date}\n\n"

            keyboard = []

            for ride in rides:
                ride_id, driver_id, driver_username, from_loc, to_loc, ride_date, ride_time, seats = ride

                # Форматируем дату поездки для отображения
                ride_date_display = format_date_for_display(ride_date)

                response += (
                    f"🚗 Поездка #{ride_id}\n"
                    f"  📍 {from_loc} → {to_loc}\n"
                    f"  📅 {ride_date_display} в {ride_time}\n"
                    f"  👥 Свободных мест: {seats}\n"
                    f"  👤 Водитель: {driver_username}\n\n"
                )

                # Проверяем, зарегистрирован ли пользователь для получения контактов
                user_data = get_user(update.effective_user.id)
                if user_data and user_data[2]:  # Если есть телефон
                    keyboard.append([
                        InlineKeyboardButton(f"📞 Контакты водителя #{ride_id}", callback_data=f"contact_{ride_id}")
                    ])
                else:
                    keyboard.append([
                        InlineKeyboardButton(f"📞 Зарегистрируйтесь для контактов #{ride_id}", callback_data="register_for_contacts")
                    ])

            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                response,
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                f"{error_message}\n"
                "Попробуйте снова:",
                reply_markup=get_cancel_keyboard(chat_type)
            )


async def my_rides(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать мои поездки (для водителей)."""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        return

    user_id = update.effective_user.id
    chat_type = get_chat_type(update)

    user = get_user(user_id)
    if not user:
        await update.message.reply_text(
            "❌ Сначала зарегистрируйтесь!\n"
            "Нажмите кнопку '📞 Регистрация'",
            reply_markup=get_driver_keyboard(chat_type)
        )
        return

    rides = get_user_rides(user_id)

    if not rides:
        await update.message.reply_text(
            "🚗 Мои поездки\n\n"
            "У вас пока нет активных поездок.\n"
            "Создайте новую поездку, нажав '🚗 Создать поездку'",
            reply_markup=get_driver_keyboard(chat_type)
        )
        return

    response = "🚗 Ваши активные поездки:\n\n"

    for ride in rides:
        # ride: (id, driver_id, driver_username, from_location, to_location, date, time, seats, is_active, last_check, created_at)
        ride_id = ride[0]
        from_loc = ride[3]
        to_loc = ride[4]
        date = ride[5]  # В формате YYYY-MM-DD из БД
        time = ride[6]
        seats = ride[7]

        # Форматируем дату для отображения
        date_display = format_date_for_display(date)

        response += (
            f"📍 Маршрут: {from_loc} → {to_loc}\n"
            f"📅 Дата: {date_display}\n"
            f"🕒 Время: {time}\n"
            f"👥 Свободных мест: {seats}\n"
            f"🆔 ID поездки: {ride_id}\n"
            "────────────────────\n"
        )

    # Добавляем кнопки для управления поездками
    keyboard = []
    for ride in rides:
        ride_id = ride[0]
        from_loc = ride[3]
        to_loc = ride[4]
        keyboard.append([
            InlineKeyboardButton(
                f"❌ Завершить #{ride_id}: {from_loc}→{to_loc}",
                callback_data=f"end_ride_{ride_id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_driver")])

    await update.message.reply_text(
        response,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def my_searches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать историю поисков (для пассажиров)."""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        return

    user_id = update.effective_user.id
    chat_type = get_chat_type(update)

    searches = get_passenger_searches(user_id)

    if not searches:
        await update.message.reply_text(
            "🔍 Мои поиски\n\n"
            "У вас пока нет сохраненных поисков.\n"
            "Начните поиск поездки, нажав '🔍 Найти поездку'",
            reply_markup=get_passenger_keyboard(chat_type)
        )
        return

    response = "🔍 История ваших поисков:\n\n"

    for search in searches:
        # search: (id, passenger_id, from_location, to_location, search_date, created_at)
        search_id = search[0]
        from_loc = search[2]
        to_loc = search[3]
        date = search[4]  # В формате YYYY-MM-DD из БД
        created_at = search[5]

        # Форматируем дату поиска для отображения
        date_display = format_date_for_display(date)

        # Форматируем дату создания
        created_time = created_at.split(' ')[1][:5] if ' ' in created_at else created_at[:5]

        response += (
            f"📍 Маршрут: {from_loc} → {to_loc}\n"
            f"📅 Дата поездки: {date_display}\n"
            f"🕒 Время поиска: {created_time}\n"
            f"🆔 ID поиска: {search_id}\n"
            "────────────────────\n"
        )

    # Добавляем кнопки для быстрого повторного поиска
    keyboard = []
    for search in searches[-3:]:  # Последние 3 поиска
        from_loc = search[2]
        to_loc = search[3]
        date = search[4]  # В формате YYYY-MM-DD
        date_display = format_date_for_display(date)
        keyboard.append([
            InlineKeyboardButton(
                f"🔍 Повторить: {from_loc}→{to_loc} ({date_display})",
                callback_data=f"repeat_search_{search[0]}"
            )
        ])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_passenger")])

    await update.message.reply_text(
        response,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def relevant_rides(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать актуальные поездки на основе истории поисков"""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        return

    user_id = update.effective_user.id
    chat_type = get_chat_type(update)

    # Получаем актуальные поездки
    relevant_rides = get_relevant_rides_for_passenger(user_id)

    if not relevant_rides:
        await update.message.reply_text(
            "🚗 Актуальные поездки\n\n"
            "Пока нет поездок по вашим поискам.\n"
            "Сначала найдите поездки с помощью кнопки '🔍 Найти поездку', "
            "и они появятся здесь!",
            reply_markup=get_passenger_keyboard(chat_type)
        )
        return

    response = "🚗 Актуальные поездки по вашим поискам:\n\n"

    # Счетчик для нумерации
    count = 1

    # Словарь для группировки поездок по маршруту
    rides_by_route = {}

    for item in relevant_rides:
        search_from, search_to, search_date = item['search']
        ride = item['ride']

        ride_id = ride[0]
        driver_username = ride[2]
        from_loc = ride[3]
        to_loc = ride[4]
        date = ride[5]  # YYYY-MM-DD
        time = ride[6]
        seats = ride[7]

        # Форматируем даты
        search_date_display = format_date_for_display(search_date)
        ride_date_display = format_date_for_display(date)

        route_key = f"{search_from}→{search_to}"
        if route_key not in rides_by_route:
            rides_by_route[route_key] = []

        rides_by_route[route_key].append({
            'ride_id': ride_id,
            'driver_username': driver_username,
            'from_loc': from_loc,
            'to_loc': to_loc,
            'date': ride_date_display,
            'time': time,
            'seats': seats,
            'search_date': search_date_display
        })

    # Формируем ответ с группировкой по маршрутам
    for route, rides in rides_by_route.items():
        search_from, search_to = route.split('→')

        # Используем дату из первого поиска для этого маршрута
        first_search_date = rides[0]['search_date']

        response += f"📍 Маршрут: {search_from} → {search_to}\n"
        response += f"📅 Искали на дату: {first_search_date}\n\n"

        for ride_info in rides:
            response += (
                f"  🚗 Поездка #{ride_info['ride_id']}\n"
                f"    📅 {ride_info['date']} в {ride_info['time']}\n"
                f"    👥 Свободных мест: {ride_info['seats']}\n"
                f"    👤 Водитель: {ride_info['driver_username']}\n"
                "    ──────────────────\n"
            )

        response += "\n"

    # Добавляем inline-кнопки для каждой поездки
    keyboard = []
    for item in relevant_rides:
        ride = item['ride']
        ride_id = ride[0]
        from_loc = ride[3]
        to_loc = ride[4]
        date_display = format_date_for_display(ride[5])

        # Проверяем, зарегистрирован ли пользователь для получения контактов
        user_data = get_user(user_id)
        if user_data and user_data[2]:  # Если есть телефон
            # Ограничиваем текст кнопки
            button_text = f"📞 Контакты #{ride_id}: {from_loc[:5]}→{to_loc[:5]}"
            if len(button_text) > 40:
                button_text = f"📞 #{ride_id}: {from_loc[:3]}→{to_loc[:3]}"

            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"contact_{ride_id}")])
        else:
            keyboard.append([InlineKeyboardButton(f"📞 Зарегистрируйтесь для контактов #{ride_id}", callback_data="register_for_contacts")])

    # Добавляем кнопки управления
    keyboard.append([
        InlineKeyboardButton("🔄 Обновить", callback_data="refresh_relevant_rides"),
        InlineKeyboardButton("🔙 Назад", callback_data="back_to_passenger")
    ])

    await update.message.reply_text(
        response,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def terms_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда для показа пользовательского соглашения"""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        await update.message.reply_text(
            "🤖 Я работаю только в личных сообщениях. Напишите мне в ЛС: @yuldar02bot"
        )
        return

    terms_text = """
📜 ПОЛЬЗОВАТЕЛЬСКОЕ СОГЛАШЕНИЕ

Полный текст пользовательского соглашения и политики конфиденциальности доступен по ссылке:
https://docs.google.com/document/d/1FTKKfsDyG66IGQMgWDJQKgBI5nWf8TgXy5Z2aPti5U0/edit?usp=sharing

Используя бота @yuldar02bot, вы автоматически соглашаетесь со всеми условиями соглашения.
    """
    await update.message.reply_text(terms_text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /help."""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        await update.message.reply_text(
            "🤖 Я работаю только в личных сообщениях. Напишите мне в ЛС: @yuldar02bot"
        )
        return

    chat_type = get_chat_type(update)

    help_text = f"""
📚 Помощь по боту:

👥 Роли пользователей:
• 🚗 Водитель - создает поездки и предлагает места
• 👤 Пассажир - ищет и бронирует поездки

🚗 Функции для водителей (требуется регистрация):
• Создать поездку - предложить поездку другим
• Мои поездки - просмотр созданных поездок

👤 Функции для пассажиров:
• Найти поездку - поиск подходящих поездок (регистрация не обязательна)
• Мои поиски - история ваших поисков (требуется регистрация)
• Актуальные поездки - поездки по вашим поискам (требуется регистрация)

📱 Регистрация:
• Обязательна для водителей
• Обязательна для доступа к контактам водителей
• Обязательна для сохранения истории поисков

📅 Формат даты:
Используйте формат: ДД.ММ.ГГГГ (например: 31.12.2024)

📢 Обязательный канал: {REQUIRED_CHANNEL[1:]}
Для использования бота необходимо быть подписанным на канал!

📜 Пользовательское соглашение:
• /terms - показать основные положения соглашения

Админ-панель:
• /admin - панель администратора (только для админов)

Команды в группах:
В групповых чатах кнопки не отображаются. Используйте текстовые команды:
• /start - начать работу с ботом
• /help - помощь
• /role - выбрать роль
• /register - регистрация
• /terms - пользовательское соглашение
    """

    await update.message.reply_text(
        help_text,
        reply_markup=get_role_selection_keyboard(chat_type)
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды отмены."""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        return

    chat_type = get_chat_type(update)

    # Очищаем данные пользователя
    for key in ['create_ride_step', 'search_ride_step', 'registration_step',
                'from_location', 'to_location', 'date', 'time',
                'search_from', 'search_to', 'register_after_search',
                'broadcast_step', 'broadcast_message']:
        if key in context.user_data:
            del context.user_data[key]

    # Возвращаем в соответствующее меню
    role = context.user_data.get('role')
    if role == 'driver':
        await update.message.reply_text(
            "❌ Действие отменено.\n"
            "Выберите действие:",
            reply_markup=get_driver_keyboard(chat_type)
        )
    elif role == 'passenger':
        await update.message.reply_text(
            "❌ Действие отменено.\n"
            "Выберите действие:",
            reply_markup=get_passenger_keyboard(chat_type)
        )
    else:
        await update.message.reply_text(
            "❌ Действие отменено.\n"
            "Выберите роль:",
            reply_markup=get_role_selection_keyboard(chat_type)
        )


async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Возврат в главное меню."""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        return

    chat_type = get_chat_type(update)

    # Очищаем данные пользователя
    for key in ['create_ride_step', 'search_ride_step', 'registration_step', 'register_after_search',
                'broadcast_step', 'broadcast_message']:
        if key in context.user_data:
            del context.user_data[key]

    role = context.user_data.get('role')
    if role == 'driver':
        await update.message.reply_text(
            "Главное меню водителя:",
            reply_markup=get_driver_keyboard(chat_type)
        )
    elif role == 'passenger':
        await update.message.reply_text(
            "Главное меню пассажира:",
            reply_markup=get_passenger_keyboard(chat_type)
        )
    else:
        await update.message.reply_text(
            "Выберите вашу роль:",
            reply_markup=get_role_selection_keyboard(chat_type)
        )


async def handle_contact_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик callback для получения контактов."""
    query = update.callback_query
    await query.answer()

    if query.data.startswith("contact_"):
        try:
            ride_id = int(query.data.split("_")[1])
            contact_info = get_driver_contact(ride_id)

            if not contact_info:
                await query.edit_message_text(f"❌ Поездка #{ride_id} не найдена.")
                return

            username, phone = contact_info

            # Определяем тип чата для правильного возврата
            chat_type = query.message.chat.type
            if chat_type == "private":
                # Определяем роль пользователя для возврата в нужное меню
                role = context.user_data.get('role')
                if role == 'passenger':
                    # Отправляем сообщение с контактами и кнопкой возврата
                    await query.message.reply_text(
                        f"📞 Контакты водителя для поездки #{ride_id}:\n\n"
                        f"👤 Username: @{username if username else 'не указан'}\n"
                        f"📱 Телефон: {phone}\n\n"
                        "Пожалуйста, будьте вежливы и осторожны при общении."
                    )
                    # Отправляем отдельное сообщение с кнопкой возврата
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Вернуться к поиску", callback_data="back_to_passenger_search")]
                    ])
                    await query.message.reply_text(
                        "Чтобы вернуться к результатам поиска, нажмите кнопку ниже:",
                        reply_markup=keyboard
                    )
                elif role == 'driver':
                    await query.message.reply_text(
                        f"📞 Контакты водителя для поездка #{ride_id}:\n\n"
                        f"👤 Username: @{username if username else 'не указан'}\n"
                        f"📱 Телефон: {phone}\n\n"
                        "Пожалуйста, будьте вежливы и осторожны при общении.",
                        reply_markup=get_driver_keyboard(chat_type)
                    )
                else:
                    await query.message.reply_text(
                        f"📞 Контакты водителя для поездки #{ride_id}:\n\n"
                        f"👤 Username: @{username if username else 'не указан'}\n"
                        f"📱 Телефон: {phone}",
                        reply_markup=get_role_selection_keyboard(chat_type)
                    )
            else:
                # В групповом чате просто показываем контакты
                await query.message.reply_text(
                    f"📞 Контакты водителя для поездки #{ride_id}:\n\n"
                    f"👤 Username: @{username if username else 'не указан'}\n"
                    f"📱 Телефон: {phone}"
                )

        except (ValueError, IndexError):
            await query.answer("❌ Ошибка при получении контактов", show_alert=True)

    elif query.data == "register_for_contacts":
        chat_type = query.message.chat.type
        if chat_type == "private":
            await query.message.reply_text(
                "📝 Для получения контактов водителей необходимо зарегистрироваться.\n\n"
                "Пожалуйста, зарегистрируйтесь, чтобы получить доступ к контактам водителей:",
                reply_markup=get_registration_keyboard(chat_type)
            )
        else:
            await query.answer("Эта функция доступна только в личных сообщениях", show_alert=True)


async def handle_callback_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка дополнительных callback-действий."""
    query = update.callback_query
    await query.answer()

    data = query.data
    chat_type = query.message.chat.type

    if data.startswith("end_ride_"):
        try:
            ride_id = int(data.split("_")[2])
            update_ride_status(ride_id, False)

            await query.edit_message_text(
                f"✅ Поездка #{ride_id} успешно завершена!\n"
                "Она больше не будет отображаться в поиске."
            )

            # Возвращаем в меню водителя только если это личный чат
            if chat_type == "private":
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="Выберите действие:",
                    reply_markup=get_driver_keyboard(chat_type)
                )

        except Exception as e:
            logger.error(f"Ошибка при завершении поездки: {e}")
            await query.answer("❌ Ошибка при завершении поездки", show_alert=True)

    elif data.startswith("repeat_search_"):
        try:
            search_id = int(data.split("_")[2])
            # Получаем детали поиска из БД
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT from_location, to_location, search_date
                FROM passenger_searches
                WHERE id = ? AND passenger_id = ?
            ''', (search_id, query.from_user.id))
            search_details = cursor.fetchone()
            conn.close()

            if search_details:
                from_location, to_location, date = search_details

                # Форматируем дату для отображения
                display_date = format_date_for_display(date)

                # Ищем поездки снова
                rides = search_rides(from_location, to_location, date)

                if not rides:
                    await query.edit_message_text(
                        f"🔍 По вашему запросу ничего не найдено.\n"
                        f"📍 Маршрут: {from_location} → {to_location}\n"
                        f"📅 Дата: {display_date}"
                    )
                    return

                # Формируем ответ
                response = f"🎯 Найдено поездок: {len(rides)}\n\n"
                response += f"📍 Маршрут: {from_location} → {to_location}\n"
                response += f"📅 Дата: {display_date}\n\n"

                keyboard = []
                for ride in rides:
                    ride_id, driver_id, driver_username, from_loc, to_loc, ride_date, ride_time, seats = ride

                    # Форматируем дату поездки для отображения
                    ride_date_display = format_date_for_display(ride_date)

                    response += (
                        f"🚗 Поездка #{ride_id}\n"
                        f"  📍 {from_loc} → {to_loc}\n"
                        f"  📅 {ride_date_display} в {ride_time}\n"
                        f"  👥 Свободных мест: {seats}\n"
                        f"  👤 Водитель: {driver_username}\n\n"
                    )

                    # Проверяем, зарегистрирован ли пользователь для получения контактов
                    user_data = get_user(query.from_user.id)
                    if user_data and user_data[2]:  # Если есть телефон
                        keyboard.append([
                            InlineKeyboardButton(f"📞 Контакты водителя #{ride_id}", callback_data=f"contact_{ride_id}")
                        ])
                    else:
                        keyboard.append([
                            InlineKeyboardButton(f"📞 Зарегистрируйтесь для контактов #{ride_id}", callback_data="register_for_contacts")
                        ])

                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(response, reply_markup=reply_markup)

        except Exception as e:
            logger.error(f"Ошибка при повторном поиске: {e}")
            await query.answer("❌ Ошибка при выполнении поиска", show_alert=True)

    elif data == "refresh_relevant_rides":
        # Получаем обновленные данные
        relevant_rides_list = get_relevant_rides_for_passenger(query.from_user.id)

        if not relevant_rides_list:
            await query.edit_message_text(
                "🚗 Актуальные поездки\n\n"
                "Пока нет поездок по вашим поискам.\n"
                "Сначала найдите поездки с помощью кнопки '🔍 Найти поездку'!"
            )
            return

        # Формируем ответ
        response = "🚗 Актуальные поездки по вашим поискам:\n\n"

        rides_by_route = {}
        for item in relevant_rides_list:
            search_from, search_to, search_date = item['search']
            ride = item['ride']

            ride_id = ride[0]
            driver_username = ride[2]
            from_loc = ride[3]
            to_loc = ride[4]
            date = ride[5]
            time = ride[6]
            seats = ride[7]

            search_date_display = format_date_for_display(search_date)
            ride_date_display = format_date_for_display(date)

            route_key = f"{search_from}→{search_to}"
            if route_key not in rides_by_route:
                rides_by_route[route_key] = []

            rides_by_route[route_key].append({
                'ride_id': ride_id,
                'driver_username': driver_username,
                'from_loc': from_loc,
                'to_loc': to_loc,
                'date': ride_date_display,
                'time': time,
                'seats': seats,
                'search_date': search_date_display
            })

        for route, rides in rides_by_route.items():
            search_from, search_to = route.split('→')
            first_search_date = rides[0]['search_date']

            response += f"📍 Маршрут: {search_from} → {search_to}\n"
            response += f"📅 Искали на дату: {first_search_date}\n\n"

            for ride_info in rides:
                response += (
                    f"  🚗 Поездка #{ride_info['ride_id']}\n"
                    f"    📅 {ride_info['date']} в {ride_info['time']}\n"
                    f"    👥 Свободных мест: {ride_info['seats']}\n"
                    f"    👤 Водитель: {ride_info['driver_username']}\n"
                    "    ──────────────────\n"
                )
            response += "\n"

        # Создаем клавиатуру
        keyboard = []
        for item in relevant_rides_list[:5]:  # Ограничиваем 5 кнопками
            ride = item['ride']
            ride_id = ride[0]
            from_loc = ride[3]
            to_loc = ride[4]

            # Проверяем, зарегистрирован ли пользователь для получения контактов
            user_data = get_user(query.from_user.id)
            if user_data and user_data[2]:  # Если есть телефон
                button_text = f"📞 #{ride_id}: {from_loc[:5]}→{to_loc[:5]}"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=f"contact_{ride_id}")])
            else:
                keyboard.append([InlineKeyboardButton(f"📞 Зарегистрируйтесь для контактов #{ride_id}", callback_data="register_for_contacts")])

        keyboard.append([
            InlineKeyboardButton("🔄 Обновить", callback_data="refresh_relevant_rides"),
        ])

        if chat_type == "private":
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_passenger_search")])

        await query.edit_message_text(
            response,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "back_to_driver":
        if chat_type == "private":
            # Удаляем старое сообщение с inline-кнопками
            try:
                await query.delete_message()
            except:
                pass
            # Отправляем новое сообщение с меню водителя
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Главное меню водителя:",
                reply_markup=get_driver_keyboard(chat_type)
            )
        else:
            await query.answer("Эта функция доступна только в личных сообщениях", show_alert=True)

    elif data == "back_to_passenger":
        if chat_type == "private":
            # Удаляем старое сообщение с inline-кнопками
            try:
                await query.delete_message()
            except:
                pass
            # Отправляем новое сообщение с меню пассажира
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Главное меню пассажира:",
                reply_markup=get_passenger_keyboard(chat_type)
            )
        else:
            await query.answer("Эта функция доступна только в личных сообщениях", show_alert=True)

    elif data == "back_to_passenger_search":
        if chat_type == "private":
            # Удаляем старое сообщение
            try:
                await query.delete_message()
            except:
                pass
            # Отправляем сообщение с меню пассажира
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Выберите действие:",
                reply_markup=get_passenger_keyboard(chat_type)
            )
        else:
            await query.answer("Эта функция доступна только в личных сообщениях", show_alert=True)


async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /register для групп."""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        await update.message.reply_text(
            "🤖 Я работаю только в личных сообщениях. Напишите мне в ЛС: @yuldar02bot"
        )
        return
    await start_registration(update, context)


async def role_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /role для групп."""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        await update.message.reply_text(
            "🤖 Я работаю только в личных сообщениях. Напишите мне в ЛС: @yuldar02bot"
        )
        return

    chat_type = get_chat_type(update)
    # Очищаем текущую роль
    if 'role' in context.user_data:
        del context.user_data['role']
    await update.message.reply_text(
        "Смена роли\n\n"
        "Пожалуйста, выберите новую роль:\n"
        "1. 🚗 Водитель\n"
        "2. 👤 Пассажир\n\n"
        "Отправьте '1' или '2'",
        reply_markup=get_role_selection_keyboard(chat_type)
    )


async def scheduled_cleanup(context: ContextTypes.DEFAULT_TYPE):
    """Регулярная очистка базы данных от просроченных поездок"""
    try:
        # Очищаем просроченные поездки
        expired_count = cleanup_expired_rides()

        # Удаляем старые неактивные поездки
        deleted_count = delete_old_inactive_rides()

        if expired_count > 0 or deleted_count > 0:
            logger.info(f"Планировщик: удалено {expired_count} просроченных и {deleted_count} старых поездок")
    except Exception as e:
        logger.error(f"Ошибка при плановой очистке БД: {e}")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать статистику бота (только для админов)."""
    # Проверяем, что сообщение в личном чате
    if update.message.chat.type != "private":
        await update.message.reply_text(
            "🤖 Я работаю только в личных сообщениях. Напишите мне в ЛС: @yuldar02bot"
        )
        return

    user_id = update.effective_user.id

    # Проверяем, является ли пользователь админом
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к этой команде.")
        return

    conn = get_db()
    cursor = conn.cursor()

    try:
        # Получаем статистику
        cursor.execute("SELECT COUNT(*) FROM users")
        users_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM rides WHERE is_active = 1")
        active_rides_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM passenger_searches")
        searches_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT driver_id) FROM rides WHERE is_active = 1")
        active_drivers_count = cursor.fetchone()[0]

        await update.message.reply_text(
            f"📊 Статистика бота:\n\n"
            f"👥 Всего пользователей: {users_count}\n"
            f"🚗 Активных поездок: {active_rides_count}\n"
            f"🔍 Всего поисков: {searches_count}\n"
            f"👨‍✈️ Активных водителей: {active_drivers_count}\n\n"
            f"🔄 Последняя очистка: {datetime.now().strftime('%H:%M')}"
        )
    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}")
        await update.message.reply_text("❌ Ошибка при получении статистики")
    finally:
        conn.close()


def setup_jobs(application):
    """Настройка планировщика задач"""
    # Очистка каждые 6 часов
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(scheduled_cleanup, interval=21600, first=10)


def main() -> None:
    """Запуск бота."""
    # Инициализация БД
    init_db()

    # Инициализация периодической очистки
    cleanup_expired_rides()
    delete_old_inactive_rides()

    # Создание приложения
    application = Application.builder().token(TOKEN).build()

    # Регистрация обработчиков команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("role", role_command))
    application.add_handler(CommandHandler("register", register_command))
    application.add_handler(CommandHandler("terms", terms_command))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("cleanup", lambda u, c: scheduled_cleanup(c)))  # Команда для ручной очистки
    application.add_handler(CommandHandler("stats", stats_command))

    # Регистрация обработчиков callback-кнопок
    application.add_handler(CallbackQueryHandler(handle_contact_callback, pattern="^contact_"))
    application.add_handler(CallbackQueryHandler(handle_contact_callback, pattern="^register_for_contacts$"))
    application.add_handler(CallbackQueryHandler(handle_callback_actions, pattern="^(end_ride_|repeat_search_|refresh_relevant_rides|back_to_)"))
    application.add_handler(CallbackQueryHandler(button_callback, pattern="^(check_subscription|accept_terms)$"))

    # Обработчики для админ-панели
    application.add_handler(CallbackQueryHandler(handle_admin_callbacks, pattern="^admin_"))

    # Регистрация обработчика контактов
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))

    # Регистрация обработчика текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Настройка планировщика задач
    setup_jobs(application)

    # Запуск бота
    print("🤖 Бот запущен...")
    print(f"📢 Обязательный канал: {REQUIRED_CHANNEL}")
    print("📜 Обязательное пользовательское соглашение активировано")
    print("👑 Админ-панель активирована")
    print("✅ После проверки подписки бот просит принять пользовательское соглашение")
    print("👥 Бот теперь поддерживает роли: Водитель и Пассажир")
    print("📱 Регистрация теперь обязательна для водителей и для получения контактов")
    print("🔍 Поиск работает без регистрации, но история поисков сохраняется только для зарегистрированных пользователей")
    print("💬 Бот работает ТОЛЬКО в личных сообщениях")
    print("🗑️ Планировщик очистки БД активирован")
    print("📅 Формат даты изменен на ДД.ММ.ГГГГ (например: 31.12.2024)")
    print("✅ Inline-кнопки 'Завершить' и 'Повторить' теперь работают!")
    print("🚗 Добавлена кнопка 'Актуальные поездки' для пассажиров")
    print("👑 Админ-панель доступна по команде /admin")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
