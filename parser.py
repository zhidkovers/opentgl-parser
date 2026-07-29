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
    print(f"  Загрузка страницы: {url}")

    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.encoding = "utf-8"
        response.raise_for_status()
        print(f"  Страница загружена, размер: {len(response.text)} байт, "
              f"статус: {response.status_code}")
    except requests.RequestException as e:
        print(f"  ОШИБКА при загрузке страницы {url}: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    table = soup.find("table", class_="od_table")
    if not table:
        print(
            f"  ОШИБКА: таблица с классом 'od_table' не найдена на странице {page_number}",
            file=sys.stderr,
        )
        return []

    tbody = table.find("tbody")
    if not tbody:
        print(
            f"  ОШИБКА: тело таблицы (tbody) не найдено на странице {page_number}",
            file=sys.stderr,
        )
        return []

    all_rows = tbody.find_all("tr")
    print(f"  Найдено строк в таблице: {len(all_rows)}")

    results = []

    for row_idx, row in enumerate(all_rows, start=1):
        cells = row.find_all("td")
        if len(cells) < 3:
            print(f"  Строка {row_idx}: пропущена (меньше 3 столбцов)")
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
                print(f"  Строка {row_idx}: дубликат CSV пропущен: {filename}")
                continue

            results.append({"url": absolute_url, "filename": filename})
            print(f"  Строка {row_idx}: найден CSV -> {filename}")

    print(f"  Итого CSV на странице {page_number}: {len(results)}")
    return results


def download_csv(url: str) -> str | None:
    """
    Скачивает CSV-файл по указанному URL.
    Возвращает содержимое в виде строки или None при ошибке.
    Пытается декодировать как UTF-8, затем как CP1251.
    """
    print(f"    Скачивание: {url}")
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        print(f"    Скачан, размер: {len(response.content)} байт, "
              f"статус: {response.status_code}, "
              f"content-type: {response.headers.get('Content-Type', 'не указан')}")
    except requests.RequestException as e:
        print(f"    ОШИБКА при скачивании {url}: {e}", file=sys.stderr)
        return None

    content = response.content

    # Пробуем наиболее вероятные кодировки для российских сайтов
    for encoding in ("utf-8", "cp1251", "koi8-r"):
        try:
            decoded = content.decode(encoding)
            print(f"    Кодировка: {encoding}")
            return decoded
        except (UnicodeDecodeError, UnicodeError):
            print(f"    Кодировка {encoding} не подошла, пробую дальше...")

    # Если ничего не подошло — декодируем с заменой нечитаемых символов
    print(f"    Кодировка не определена, декодирую utf-8 с заменой ошибок")
    return content.decode("utf-8", errors="replace")


def _detect_delimiter(text: str) -> str:
    """
    Определяет наиболее вероятный разделитель CSV.
    Приоритет: ';' -> ',' -> '\t' -> '|'.
    Для каждого разделителя парсит текст и оценивает:
      - среднее количество полей на строку (без учёта пустых строк)
      - количество строк, где число полей совпадает с максимальным
    """
    lines = text.strip().splitlines()
    if not lines:
        print("    Текст пуст, разделитель по умолчанию: ';'")
        return ";"

    delimiters = [";", ",", "\t", "|"]
    best_delim = ";"
    best_score = -1

    for delim in delimiters:
        total_cols = 0
        non_empty = 0
        for line in lines:
            if not line.strip():
                continue
            reader = csv.reader(io.StringIO(line), delimiter=delim)
            try:
                row = next(reader)
                total_cols += len(row)
                non_empty += 1
            except StopIteration:
                continue

        if non_empty == 0:
            print(f"    Разделитель '{delim}': нет непустых строк — пропущен")
            continue

        avg_cols = total_cols / non_empty
        consistent = sum(
            1 for line in lines
            if line.strip()
            and len(next(csv.reader(io.StringIO(line), delimiter=delim))) >= avg_cols
        )

        score = avg_cols * consistent
        print(f"    Разделитель '{delim}': ср. колонок={avg_cols:.2f}, "
              f"согласовано={consistent}, оценка={score:.2f}")

        if score > best_score:
            best_score = score
            best_delim = delim

    print(f"    Выбран разделитель: '{best_delim}' (оценка {best_score:.2f})")
    return best_delim


def parse_csv_content(text: str) -> list[list[str]]:
    """
    Принимает строку с CSV-данными и возвращает список строк,
    каждая из которых — список значений ячеек.
    Разделитель определяется автоматически с приоритетом ';'.
    """
    delimiter = _detect_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [row for row in reader]
    col_count = len(rows[0]) if rows else 0
    print(f"    Распарсено строк: {len(rows)}, колонок: {col_count}")
    if len(rows) > 1:
        print(f"    Первая строка (заголовок): {rows[0]}")
        print(f"    Вторая строка (образец):   {rows[1]}")
    return rows


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
    print(f"    Очистка листа '{worksheet.title}'...")
    worksheet.clear()

    if not data:
        print(f"    Данных нет, лист оставлен пустым")
        return

    rows = len(data)
    cols = len(data[0]) if data else 0
    print(f"    Запись {rows} строк x {cols} колонок в лист '{worksheet.title}'...")

    # Пакетная запись: преобразуем список строк в список списков
    worksheet.update(values=data, range_name="A1")
    print(f"    Запись завершена")


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
    print("=" * 60)
    print("ЭТАП 1: Сбор CSV-ссылок со страниц каталога")
    print("=" * 60)
    all_csv_links: list[dict] = []
    for page in range(1, TOTAL_PAGES + 1):
        print(f"\n--- Страница {page} из {TOTAL_PAGES} ---")
        links = parse_csv_links_from_page(page)
        print(f"--- Итого на странице {page}: {len(links)} CSV ---")
        all_csv_links.extend(links)

    if not all_csv_links:
        print("\nНе найдено ни одной CSV-ссылки. Завершение работы.", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"ВСЕГО собрано CSV-файлов: {len(all_csv_links)}")
    for idx, link in enumerate(all_csv_links, start=1):
        print(f"  {idx}. {link['filename']} -> {link['url']}")
    print("=" * 60)

    # Шаг 2: подключение к Google Таблице
    print(f"\n{'=' * 60}")
    print("ЭТАП 2: Подключение к Google Sheets")
    print("=" * 60)
    print(f"ID таблицы: {spreadsheet_id}")
    print(f"Сервисный аккаунт: загружен из GOOGLE_SERVICE_ACCOUNT_JSON")
    try:
        sheet = get_google_sheet(spreadsheet_id, credentials_json)
        print(f"Подключение успешно: таблица '{sheet.title}'")
        print(f"Существующие листы: {[ws.title for ws in sheet.worksheets()]}")
    except Exception as e:
        print(f"ОШИБКА подключения к Google Sheets: {e}", file=sys.stderr)
        sys.exit(1)

    # Шаг 3: обработка каждого CSV-файла
    print(f"\n{'=' * 60}")
    print("ЭТАП 3: Скачивание и загрузка CSV в Google Sheets")
    print("=" * 60)
    processed = 0
    skipped = 0
    for csv_info in all_csv_links:
        filename = csv_info["filename"]
        url = csv_info["url"]

        print(f"\n--- {filename} ---")

        csv_text = download_csv(url)
        if csv_text is None:
            print(f"  ПРОПУСК {filename}: не удалось скачать")
            skipped += 1
            continue

        data = parse_csv_content(csv_text)

        # Создаём новый лист или получаем существующий
        try:
            worksheet = sheet.worksheet(filename)
            print(f"  Лист '{filename}' уже существует, будет перезаписан")
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sheet.add_worksheet(title=filename, rows=1, cols=1)
            print(f"  Создан новый лист '{filename}'")

        update_sheet(worksheet, data)
        processed += 1
        print(f"  +++ Лист '{filename}' успешно обновлён +++")

    # Итог
    print(f"\n{'=' * 60}")
    print("ИТОГ")
    print("=" * 60)
    print(f"  Успешно обработано: {processed}")
    print(f"  Пропущено (ошибки): {skipped}")
    if processed > 0:
        print(f"  Итоговые листы: {[ws.title for ws in sheet.worksheets()]}")
    print("Скрипт успешно завершён.")


if __name__ == "__main__":
    main()
