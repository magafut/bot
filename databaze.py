import sqlite3
import logging
from datetime import datetime, timedelta

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_db():
    return sqlite3.connect('rides.db')

def init_db():
    """Инициализация базы данных с поддержкой миграций"""
    conn = get_db()
    cursor = conn.cursor()

    # Создаем таблицу пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            phone TEXT,
            accepted_terms BOOLEAN DEFAULT 0,
            accepted_at TIMESTAMP
        )
    ''')

    # Создаем таблицу поездок с улучшенной структурой
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER,
            driver_username TEXT,
            from_location TEXT,
            to_location TEXT,
            date TEXT,
            time TEXT,
            seats INTEGER,
            is_active BOOLEAN DEFAULT 1,
            last_check TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (driver_id) REFERENCES users(user_id)
        )
    ''')

    # Создаем таблицу для истории поисков пассажиров
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS passenger_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            passenger_id INTEGER,
            from_location TEXT,
            to_location TEXT,
            search_date TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (passenger_id) REFERENCES users(user_id)
        )
    ''')

    # Выполняем миграции для существующих таблиц
    migrate_database(cursor)

    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

def migrate_database(cursor):
    """Выполняет миграции для обновления структуры базы данных"""

    # Проверяем существование столбцов в таблице users
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]

    # Добавляем новые столбцы, если их нет
    if 'accepted_terms' not in columns:
        try:
            cursor.execute('ALTER TABLE users ADD COLUMN accepted_terms BOOLEAN DEFAULT 0')
            logger.info("Добавлен столбец accepted_terms в таблицу users")
        except Exception as e:
            logger.error(f"Ошибка при добавлении столбца accepted_terms: {e}")

    if 'accepted_at' not in columns:
        try:
            cursor.execute('ALTER TABLE users ADD COLUMN accepted_at TIMESTAMP')
            logger.info("Добавлен столбец accepted_at в таблицу users")
        except Exception as e:
            logger.error(f"Ошибка при добавлении столбца accepted_at: {e}")

    # Проверяем существование столбцов в таблице rides
    cursor.execute("PRAGMA table_info(rides)")
    columns = [column[1] for column in cursor.fetchall()]

    # Добавляем новые столбцы, если их нет
    if 'is_active' not in columns:
        try:
            cursor.execute('ALTER TABLE rides ADD COLUMN is_active BOOLEAN DEFAULT 1')
            logger.info("Добавлен столбец is_active в таблицу rides")
        except Exception as e:
            logger.error(f"Ошибка при добавлении столбца is_active: {e}")

    if 'last_check' not in columns:
        try:
            cursor.execute('ALTER TABLE rides ADD COLUMN last_check TIMESTAMP')
            logger.info("Добавлен столбец last_check в таблицу rides")
        except Exception as e:
            logger.error(f"Ошибка при добавлении столбца last_check: {e}")

    if 'created_at' not in columns:
        try:
            cursor.execute('ALTER TABLE rides ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
            logger.info("Добавлен столбец created_at в таблицу rides")
        except Exception as e:
            logger.error(f"Ошибка при добавлении столбца created_at: {e}")

    # Для существующих записей устанавливаем is_active = 1 и last_check = текущее время
    cursor.execute('''
        UPDATE rides
        SET is_active = 1,
            last_check = datetime('now')
        WHERE is_active IS NULL OR last_check IS NULL
    ''')

    # Для существующих пользователей устанавливаем accepted_terms = 1 (если они уже пользовались ботом)
    cursor.execute('''
        UPDATE users
        SET accepted_terms = 1,
            accepted_at = datetime('now')
        WHERE accepted_terms IS NULL AND user_id IN (SELECT DISTINCT driver_id FROM rides)
    ''')


def add_user(user_id, username, phone):
    """Добавление пользователя (старая версия для обратной совместимости)"""
    return add_user_with_terms(user_id, username, phone, False)


def add_user_with_terms(user_id, username, phone, accepted_terms=False):
    """Добавление пользователя с указанием принятия соглашения"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
            INSERT OR REPLACE INTO users (user_id, username, phone, accepted_terms, accepted_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, phone, 1 if accepted_terms else 0, current_time if accepted_terms else None))
        conn.commit()
        logger.info(f"Пользователь добавлен: {user_id}, {username}, accepted_terms: {accepted_terms}")
    except Exception as e:
        logger.error(f"Ошибка при добавлении пользователя: {e}")
        raise
    finally:
        conn.close()


def update_user_terms(user_id, accepted_terms=True):
    """Обновление статуса принятия соглашения пользователем"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
            UPDATE users
            SET accepted_terms = ?, accepted_at = ?
            WHERE user_id = ?
        ''', (1 if accepted_terms else 0, current_time if accepted_terms else None, user_id))
        conn.commit()
        logger.info(f"Статус соглашения пользователя {user_id} обновлен: accepted_terms={accepted_terms}")
    except Exception as e:
        logger.error(f"Ошибка при обновлении статуса соглашения: {e}")
        raise
    finally:
        conn.close()


def add_ride(driver_id, from_location, to_location, date, time, seats):
    conn = get_db()
    cursor = conn.cursor()

    try:
        # Получаем username водителя
        cursor.execute('SELECT username FROM users WHERE user_id = ?', (driver_id,))
        user = cursor.fetchone()
        driver_username = user[0] if user else f"user_{driver_id}"

        # Текущее время для last_check
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute('''
            INSERT INTO rides (driver_id, driver_username, from_location, to_location,
                             date, time, seats, is_active, last_check)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        ''', (driver_id, driver_username, from_location, to_location,
              date, time, seats, current_time))

        ride_id = cursor.lastrowid
        conn.commit()
        logger.info(f"Поездка добавлена: {from_location} -> {to_location} на {date}, ID: {ride_id}")
        return ride_id
    except Exception as e:
        logger.error(f"Ошибка при добавлении поездки: {e}")
        raise
    finally:
        conn.close()


def get_user(user_id):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        user = cursor.fetchone()
        return user
    except Exception as e:
        logger.error(f"Ошибка при получении пользователя {user_id}: {e}")
        return None
    finally:
        conn.close()


def get_user_rides(user_id):
    """Получение активных поездок пользователя"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT * FROM rides
            WHERE driver_id = ? AND is_active = 1
            ORDER BY date, time
        ''', (user_id,))
        rides = cursor.fetchall()
        return rides
    except Exception as e:
        logger.error(f"Ошибка при получении поездок пользователя {user_id}: {e}")
        return []
    finally:
        conn.close()


def get_all_active_rides():
    """Получение всех активных поездок для проверки актуальности"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT * FROM rides
            WHERE is_active = 1
            ORDER BY last_check ASC
        ''')
        rides = cursor.fetchall()
        return rides
    except Exception as e:
        logger.error(f"Ошибка при получении активных поездок: {e}")
        return []
    finally:
        conn.close()


def update_ride_status(ride_id, is_active):
    """Обновление статуса поездки"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
            UPDATE rides
            SET is_active = ?, last_check = ?
            WHERE id = ?
        ''', (1 if is_active else 0, current_time, ride_id))
        conn.commit()
        logger.info(f"Статус поездки {ride_id} обновлен на is_active={is_active}")
    except Exception as e:
        logger.error(f"Ошибка при обновлении статуса поездки {ride_id}: {e}")
        raise
    finally:
        conn.close()


def update_last_check(ride_id):
    """Обновление времени последней проверки"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
            UPDATE rides
            SET last_check = ?
            WHERE id = ?
        ''', (current_time, ride_id))
        conn.commit()
        logger.info(f"Время последней проверки для поездки {ride_id} обновлено")
    except Exception as e:
        logger.error(f"Ошибка при обновлении времени проверки поездки {ride_id}: {e}")
        raise
    finally:
        conn.close()


def search_rides(from_location, to_location, date):
    """Поиск активных поездок"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Проверяем и обрабатываем входные параметры
        if not from_location or not to_location or not date:
            logger.warning(f"Пустые параметры поиска: from={from_location}, to={to_location}, date={date}")
            return []

        # Ищем только активные поездки
        cursor.execute('''
            SELECT
                id,
                driver_id,
                driver_username,
                from_location,
                to_location,
                date,
                time,
                seats
            FROM rides
            WHERE from_location = ?
              AND to_location = ?
              AND date = ?
              AND is_active = 1
              AND seats > 0
            ORDER BY time
        ''', (from_location, to_location, date))

        results = cursor.fetchall()
        logger.info(f"Найдено {len(results)} активных поездок для {from_location} -> {to_location} на {date}")
        return results
    except Exception as e:
        logger.error(f"Ошибка при поиске поездок: {e}")
        return []
    finally:
        conn.close()


def get_driver_contact(ride_id):
    """Получение контактов водителя по ID поездки"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT users.username, users.phone
            FROM rides
            JOIN users ON rides.driver_id = users.user_id
            WHERE rides.id = ?
        ''', (ride_id,))
        contact = cursor.fetchone()
        return contact
    except Exception as e:
        logger.error(f"Ошибка при получении контактов для поездки {ride_id}: {e}")
        return None
    finally:
        conn.close()


def add_passenger_search(passenger_id, from_location, to_location, search_date):
    """Добавление истории поиска пассажира"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO passenger_searches (passenger_id, from_location, to_location, search_date)
            VALUES (?, ?, ?, ?)
        ''', (passenger_id, from_location, to_location, search_date))
        conn.commit()
        logger.info(f"Поиск пассажира добавлен: {passenger_id}, {from_location} -> {to_location} на {search_date}")
    except Exception as e:
        logger.error(f"Ошибка при добавлении поиска пассажира: {e}")
        raise
    finally:
        conn.close()


def get_passenger_searches(passenger_id):
    """Получение истории поисков пассажира"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT * FROM passenger_searches
            WHERE passenger_id = ?
            ORDER BY created_at DESC
            LIMIT 10
        ''', (passenger_id,))
        searches = cursor.fetchall()
        return searches
    except Exception as e:
        logger.error(f"Ошибка при получении поисков пассажира {passenger_id}: {e}")
        return []
    finally:
        conn.close()


def get_relevant_rides_for_passenger(passenger_id, limit_searches=5):
    """Получение актуальных поездок на основе истории поисков пассажира"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Получаем последние поиски пассажира
        cursor.execute('''
            SELECT DISTINCT from_location, to_location, search_date
            FROM passenger_searches
            WHERE passenger_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        ''', (passenger_id, limit_searches))

        searches = cursor.fetchall()

        if not searches:
            return []

        # Собираем все релевантные поездки
        all_rides = []
        current_date = datetime.now().strftime("%Y-%m-%d")

        for search in searches:
            from_location, to_location, search_date = search

            # Ищем активные поездки для этого поиска (только будущие даты)
            cursor.execute('''
                SELECT
                    id,
                    driver_id,
                    driver_username,
                    from_location,
                    to_location,
                    date,
                    time,
                    seats
                FROM rides
                WHERE from_location = ?
                  AND to_location = ?
                  AND date >= ?
                  AND date = ?
                  AND is_active = 1
                  AND seats > 0
                ORDER BY date, time
            ''', (from_location, to_location, current_date, search_date))

            rides = cursor.fetchall()
            for ride in rides:
                all_rides.append({
                    'search': (from_location, to_location, search_date),
                    'ride': ride
                })

        # Убираем дубликаты (если одна поездка подходит под несколько поисков)
        unique_rides = {}
        for item in all_rides:
            ride_id = item['ride'][0]
            if ride_id not in unique_rides:
                unique_rides[ride_id] = item

        # Преобразуем обратно в список и сортируем по дате
        result = sorted(unique_rides.values(),
                       key=lambda x: (x['ride'][5], x['ride'][6]))  # Сортировка по date, time

        return result

    except Exception as e:
        logger.error(f"Ошибка при получении актуальных поездок для пассажира {passenger_id}: {e}")
        return []
    finally:
        conn.close()


def delete_old_inactive_rides():
    """Удаление старых неактивных поездок"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Удаляем поездки, которые неактивны и были созданы более 7 дней назад
        cursor.execute('''
            DELETE FROM rides
            WHERE is_active = 0
            AND created_at < datetime('now', '-7 days')
        ''')
        deleted_count = cursor.rowcount
        conn.commit()
        logger.info(f"Удалено {deleted_count} старых неактивных поездок")
        return deleted_count
    except Exception as e:
        logger.error(f"Ошибка при удалении старых поездок: {e}")
        return 0
    finally:
        conn.close()


def cleanup_expired_rides():
    """Очистка просроченных поездок"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        current_date = datetime.now().strftime("%Y-%m-%d")

        # Помечаем как неактивные поездки, дата которых уже прошла
        cursor.execute('''
            UPDATE rides
            SET is_active = 0, last_check = datetime('now')
            WHERE date < ? AND is_active = 1
        ''', (current_date,))

        expired_count = cursor.rowcount
        conn.commit()

        if expired_count > 0:
            logger.info(f"Помечено как неактивных {expired_count} просроченных поездок")

        return expired_count
    except Exception as e:
        logger.error(f"Ошибка при очистке просроченных поездок: {e}")
        return 0
    finally:
        conn.close()

# Добавьте эти функции в конец файла database.py:

def get_all_users():
    """Получение всех пользователей из базы данных"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT user_id, username, phone, accepted_terms, accepted_at
            FROM users
            ORDER BY user_id DESC
        ''')
        users = cursor.fetchall()
        return users
    except Exception as e:
        logger.error(f"Ошибка при получении всех пользователей: {e}")
        return []
    finally:
        conn.close()


def get_ride_by_id(ride_id):
    """Получение поездки по ID"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT *
            FROM rides
            WHERE id = ?
        ''', (ride_id,))
        ride = cursor.fetchone()
        return ride
    except Exception as e:
        logger.error(f"Ошибка при получении поездки {ride_id}: {e}")
        return None
    finally:
        conn.close()


def delete_ride(ride_id):
    """Удаление поездки по ID"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM rides WHERE id = ?', (ride_id,))
        conn.commit()
        logger.info(f"Поездка {ride_id} удалена администратором")
        return True
    except Exception as e:
        logger.error(f"Ошибка при удалении поездки {ride_id}: {e}")
        return False
    finally:
        conn.close()


if __name__ == '__main__':
    init_db()
    print("База данных успешно инициализирована и обновлена")
