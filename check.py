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
ID_REQUESTS: list = env.str('ID_REQUEST').split(',')


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
            logger.debug(f'Файл {task_info["path"]} существует. Пропускаем...')
            continue
        with open(task_info['path'], "w") as f:
            f.write(task_info['content'])
            logger.debug(f'Файл {task_info["path"]} был создан')


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


def get_last_percent_file(id_request):
    """Возвращает путь к файлу, в котором хранится последний процент для данного ID заявления."""
    return os.path.join(exe_parent, f'check_{id_request}.txt')


def check_status(id_request):
    """Проверяет статус заявления и отправляет уведомления в Telegram при изменениях."""
    # Получение данных с сайта
    logger.info(f'Проверяем статус заявления {id_request}')
    url = MID_ENDPOINT + id_request
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.HTTPError as errh:
        if response.status_code == 400:
            try:
                error_data = response.json()
                if 'code' in error_data and error_data['code'] == 'REQUEST_UID_NOT_VALID':
                    logger.error(f'Неверный код заявления: {id_request}. Пожалуйста, проверьте корректность кода.')
                    logger.info('------------------------------------------------------')
                    send_telegram_message(
                        f'❌ <b>Неверный код заявления</b>: <code>{id_request}</code>\n'
                        f'Пожалуйста, проверьте корректность кода.'
                    )
                    return
            except json.JSONDecodeError:
                pass
        logger.error(f"HTTP Error: {errh}")
    except (requests.RequestException, json.JSONDecodeError) as e:
        logger.error(f'Ошибка получения данных: {e}')
        logger.info('------------------------------------------------------')
        return

    # Извлечение данных из ответа
    uid = data['uid']
    reception_date = data['receptionDate']
    passport_status = data['passportStatus']['name']
    description = data['passportStatus']['description']
    internal_status = data['internalStatus']['name']
    percent = data['internalStatus']['percent']

    # Чтение последнего значения процента из файла
    last_percent_file = get_last_percent_file(id_request)
    # Чтение последнего значения процента из файла
    try:
        with open(last_percent_file, "r") as f:
            last_percent_str = f.read().strip()
            if not last_percent_str:
                logging.info('Файл пустой, устанавливаем значение -1')
                last_percent = -1
            else:
                last_percent = int(last_percent_str)
    except (FileNotFoundError, ValueError):
        logging.debug(f'Файл {last_percent_file} не найден, создаем новый файл')
        last_percent = -1

    # Сравнение процентов и отправка сообщения в Telegram
    if percent != last_percent:
        logger.warning(f'Процент готовности изменился теперь он составляет: {percent}%')
        message = (
            f'📑 <b>Заявление</b>: №<a href="https://info.midpass.ru/?id={uid}">{uid}</a>\n'
            f'📆 <b>Дата подачи</b>: {reception_date}\n'
            f'🔍 <b>Текущий статус</b>: {passport_status}\n'
            f'🔒 <b>Внутренний статус</b>: {internal_status}\n'
            f'🔋 <b>Готовность</b>: {percent}%'
        )
        if description:
            message = message + f'\n📝 <b>Описание</b>: {description}'
        send_telegram_message(message)

        # Обновление последнего значения процента в файле
        with open(last_percent_file, "w") as f:
            f.write(str(percent))
        logger.debug(f'Сохраняем изменения в файле {last_percent_file}')
    else:
        logger.info(f'Процент готовности не изменился: {percent}%')
    logger.info('------------------------------------------------------')


if __name__ == "__main__":
    logger.info('Процесс проверки статуса заявления запущен')
    logger.info('------------------------------------------------------')
    create_scheduled_task_bats(exe_path, bat_path_create, bat_path_delete, env_path)
    for id_request in ID_REQUESTS:
        check_status(id_request)
    logger.info('Процесс проверки статуса заявления завершен')
