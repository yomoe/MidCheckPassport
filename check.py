import json
import logging
import os
import sys

import colorlog
import requests
from environs import Env
from fake_useragent import UserAgent

exe_file = sys.executable
exe_parent = os.path.dirname(exe_file)
exe_path = os.path.join(exe_parent, 'check.exe')
txt_path = os.path.join(exe_parent, 'check.txt')
env_path = os.path.join(exe_parent, '.env')
bat_path_create = os.path.join(exe_parent, 'create_task.bat')
bat_path_delete = os.path.join(exe_parent, 'delete_task.bat')

handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter('%(log_color)s%(levelname)-8s%(reset)s %(message)s'))

logger = colorlog.getLogger()
logger.addHandler(handler)
logger.setLevel(logging.INFO)

ua = UserAgent()
HEADERS = {'user-agent': ua.chrome}

env = Env()
env.read_env(env_path)

# Файл для хранения последнего значения процента
LAST_PERCENT_FILE = txt_path

# Ваш токен и ID чата в Telegram
API_TOKEN: str = env.str('BOT_TOKEN')
CHAT_ID: int = env.int('CHAT_ID')

MID_ENDPOINT: str = env.str('MID_ENDPOINT')
ID_REQUEST: str = env.str('ID_REQUEST')


def create_scheduled_task_bats(exe_path, create_bat_path, delete_bat_path, env_path):
    """Создает BAT-файлы для создания и удаления запланированных задач в Windows.

    Args:
        exe_path (str): Путь до исполняемого файла.
        create_bat_path (str): Путь для создания BAT-файла с задачами.
        delete_bat_path (str): Путь для создания BAT-файла с удалением задач.
        env_path (str): Путь до файла с переменными окружения.
    """
    if not os.path.exists(env_path):
        logger.critical('!!! НЕ НАЙДЕН ФАЙЛ .env !!!')
        logger.critical('1. переименуйте .env.dict в .env')
        logger.critical('2. откройте .env при помощи блокнота')
        logger.critical('3. внесите свои данные')
        logger.critical('4. перезапустите программу')
        input('Нажмите Enter, чтобы продолжить...')
        sys.exit(1)  # прерывание выполнения программы с кодом 1

    tasks = {
        'create': {
            'path': create_bat_path,
            'content': f""":: Создание задачи для запуска утром в 5:00
schtasks /create /tn "MidCheckPassMorningTask" /tr "{exe_path}" /sc daily /st 09:00

:: Создание задачи для запуска вечером в 17:00
schtasks /create /tn "MidCheckPassEveningTask" /tr "{exe_path}" /sc daily /st 21:00

pause"""
        },
        'delete': {
            'path': delete_bat_path,
            'content': """:: Удаление задач
schtasks /delete /tn "MidCheckPassMorningTask" /f
schtasks /delete /tn "MidCheckPassEveningTask" /f

pause"""
        }
    }

    for task_type, task_info in tasks.items():
        if os.path.exists(task_info['path']):
            logger.info(f'Файл {task_info["path"]} существует. Пропускаем...')
            continue
        with open(task_info['path'], "w") as f:
            f.write(task_info['content'])
            logger.info(f'Файл {task_info["path"]} был создан')


def send_telegram_message(message):
    """Отправляет сообщение в Telegram.

    Args:
        message (str): Сообщение для отправки.
    """
    url = f'https://api.telegram.org/bot{API_TOKEN}/sendMessage?chat_id={CHAT_ID}&text={message}&parse_mode=HTML'
    try:
        requests.get(url, headers=HEADERS)
        logger.info(f'Отправили сообщение в чат: {CHAT_ID}')
    except requests.exceptions.ConnectionError:
        logger.error('Ошибка соединения с сервером Telegram')
    except requests.exceptions.RequestException as e:
        logger.error(f'Ошибка: {e}')


def check_status():
    """Проверяет статус заявления и отправляет уведомления в Telegram при изменениях."""
    # Получение данных с сайта
    url = MID_ENDPOINT + ID_REQUEST
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, json.JSONDecodeError) as e:
        logging.error(f'Ошибка получения данных: {e}')
        return

    # Извлечение данных из ответа
    uid = data['uid']
    reception_date = data['receptionDate']
    passport_status = data['passportStatus']['name']
    internal_status = data['internalStatus']['name']
    percent = data['internalStatus']['percent']

    # Чтение последнего значения процента из файла
    try:
        with open(LAST_PERCENT_FILE, "r") as f:
            last_percent_str = f.read().strip()
            if not last_percent_str:
                logging.info('Файл пустой, устанавливаем значение -1')
                last_percent = -1
            else:
                last_percent = int(last_percent_str)
    except (FileNotFoundError, ValueError):
        logging.info(f'Файл {txt_path} не найден, создаем новый файл')
        last_percent = -1

    # Сравнение процентов и отправка сообщения в Telegram
    if percent != last_percent:
        logger.warning(f'Процент готовности изменился теперь он составляет: {percent}%')
        message = (
            f'Заявление №<a href="https://info.midpass.ru/?id={uid}">{uid}</a> от даты <i>{reception_date}</i> имеет '
            f'статус <code>{passport_status}</code> (внутренний статус <code>{internal_status}</code>) процент '
            f'готовности <code>{percent}%</code>'
        )
        send_telegram_message(message)

        # Обновление последнего значения процента в файле
        with open(LAST_PERCENT_FILE, "w") as f:
            f.write(str(percent))
        logger.info(f'Сохраняем изменения в файле {LAST_PERCENT_FILE}')
    else:
        logger.info(f'Процент готовности не изменился: {percent}%')
    logger.info('Процесс проверки статуса заявления завершен')


if __name__ == "__main__":
    create_scheduled_task_bats(exe_path, bat_path_create, bat_path_delete, env_path)
    check_status()
