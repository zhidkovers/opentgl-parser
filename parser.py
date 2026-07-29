#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Парсер открытых данных г. Тольятти (https://tgl.ru/opendata/setlist/).

Скрипт собирает со страниц 1-3 ссылки на CSV-файлы, скачивает их содержимое
и загружает данные в Google Таблицу. Каждому CSV соответствует отдельный лист.
"""

import os
import sys
import json
import csv
import io

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

import gspread
from google.oauth2.service_account import Credentials


# Константы
BASE_URL = "https://tgl.ru"
SETLIST_URL = urljoin(BASE_URL, "/opendata/setlist/")
TOTAL_PAGES = 3
REQUEST_TIMEOUT = 30


def parse_csv_links_from_page(page_number: int) -> list[dict]:
    """
    Парсит страницу с указанным номером и возвращает список словарей
    с ключами 'url' (абсолютная ссылка на CSV) и 'filename' (имя файла без .csv).
    """
    url = f"{SETLIST_URL}?page={page_number}"

    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.encoding = "utf-8"
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Ошибка при загрузке страницы {url}: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    table = soup.find("table", class_="od_table")
    if not table:
        print(
            f"Таблица с классом 'od_table' не найдена на странице {page_number}",
            file=sys.stderr,
        )
        return []

    tbody = table.find("tbody")
    if not tbody:
        print(f"Тело таблицы (tbody) не найдено на странице {page_number}", file=sys.stderr)
        return []

    results = []

    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        # Третий столбец (индекс 2) — «Гиперссылка на набор»
        link_cell = cells[2]

        for a_tag in link_cell.find_all("a", href=True):
            href = a_tag["href"]
            if ".csv" not in href.lower():
                continue

            absolute_url = urljoin(BASE_URL, href)

            # Извлекаем имя файла из URL (часть после последнего /)
            filename = absolute_url.rstrip("/").split("/")[-1]
            if filename.endswith(".csv"):
                filename = filename[:-4]
            else:
                filename = filename.split("?")[0]

            # Пропускаем дубликаты (если вдруг на одной странице ссылка
            # встречается несколько раз)
            if any(item["url"] == absolute_url for item in results):
                continue

            results.append({"url": absolute_url, "filename": filename})

    return results


def download_csv(url: str) -> str | None:
    """
    Скачивает CSV-файл по указанному URL.
    Возвращает содержимое в виде строки или None при ошибке.
    Пытается декодировать как UTF-8, затем как CP1251.
    """
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Ошибка при скачивании {url}: {e}", file=sys.stderr)
        return None

    content = response.content

    # Пробуем наиболее вероятные кодировки для российских сайтов
    for encoding in ("utf-8", "cp1251", "koi8-r"):
        try:
            return content.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue

    # Если ничего не подошло — декодируем с заменой нечитаемых символов
    return content.decode("utf-8", errors="replace")


def parse_csv_content(text: str) -> list[list[str]]:
    """
    Принимает строку с CSV-данными и возвращает список строк,
    каждая из которых — список значений ячеек.
    """
    reader = csv.reader(io.StringIO(text))
    return [row for row in reader]


def get_google_sheet(spreadsheet_id: str, credentials_json: str):
    """
    Авторизуется в Google Sheets API через сервисный аккаунт
    и возвращает объект таблицы для работы.
    """
    creds_dict = json.loads(credentials_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(spreadsheet_id)


def update_sheet(worksheet, data: list[list[str]]):
    """
    Очищает лист и записывает новые данные пакетным методом update(),
    чтобы минимизировать количество API-вызовов.
    """
    worksheet.clear()

    if not data:
        return

    # Пакетная запись: преобразуем список строк в список списков
    worksheet.update(values=data, range_name="A1")


def main():
    """
    Основной рабочий процесс:
      1. Сбор CSV-ссылок со страниц 1-3.
      2. Подключение к Google Таблице.
      3. Скачивание каждого CSV и запись в соответствующий лист.
    """

    # Чтение переменных окружения
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    credentials_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not spreadsheet_id:
        print("Ошибка: не задана переменная окружения SPREADSHEET_ID", file=sys.stderr)
        sys.exit(1)

    if not credentials_json:
        print(
            "Ошибка: не задана переменная окружения GOOGLE_SERVICE_ACCOUNT_JSON",
            file=sys.stderr,
        )
        sys.exit(1)

    # Шаг 1: сбор ссылок на CSV-файлы
    all_csv_links: list[dict] = []
    for page in range(1, TOTAL_PAGES + 1):
        print(f"Парсинг страницы {page} из {TOTAL_PAGES}...")
        links = parse_csv_links_from_page(page)
        print(f"  Найдено {len(links)} CSV-ссылок")
        all_csv_links.extend(links)

    if not all_csv_links:
        print("Не найдено ни одной CSV-ссылки. Завершение работы.", file=sys.stderr)
        sys.exit(1)

    print(f"Всего найдено CSV-файлов: {len(all_csv_links)}")

    # Шаг 2: подключение к Google Таблице
    print("Подключение к Google Sheets...")
    try:
        sheet = get_google_sheet(spreadsheet_id, credentials_json)
    except Exception as e:
        print(f"Ошибка подключения к Google Sheets: {e}", file=sys.stderr)
        sys.exit(1)

    # Шаг 3: обработка каждого CSV-файла
    for csv_info in all_csv_links:
        filename = csv_info["filename"]
        url = csv_info["url"]

        print(f"Обработка: {filename}")

        csv_text = download_csv(url)
        if csv_text is None:
            print(f"  Пропускаю {filename}: не удалось скачать")
            continue

        data = parse_csv_content(csv_text)
        print(f"  Скачано строк: {len(data)}")

        # Создаём новый лист или получаем существующий
        try:
            worksheet = sheet.worksheet(filename)
            print(f"  Лист '{filename}' существует, перезаписываю")
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sheet.add_worksheet(title=filename, rows=1, cols=1)
            print(f"  Создан новый лист '{filename}'")

        update_sheet(worksheet, data)
        print(f"  Данные записаны в лист '{filename}'")

    print("Скрипт успешно завершён.")


if __name__ == "__main__":
    main()
