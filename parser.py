#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Парсер открытых данных г. Тольятти (https://tgl.ru/opendata/setlist/).

Скрипт собирает со страниц 1-3 ссылки на CSV-файлы, скачивает и парсит их
с помощью pandas (автоопределение разделителя и кодировки), затем загружает
данные в Google Таблицу. Каждому CSV соответствует отдельный лист.
"""

import os
import sys
import json
import io

import requests
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# Загрузка креденшелов из .env
# from dotenv import load_dotenv

# load_dotenv()

import gspread
from google.oauth2.service_account import Credentials


# Константы
BASE_URL = "https://tgl.ru"
SETLIST_URL = urljoin(BASE_URL, "/opendata/setlist/")
TOTAL_PAGES = 3
REQUEST_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 10  # таймаут скачивания данных, секунд

# Пороговые значения для валидации данных
MIN_ROWS_FOR_VALID_CSV = 1
MIN_COLS_FOR_VALID_CSV = 1


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
        print(
            f"  Страница загружена, размер: {len(response.text)} байт, "
            f"статус: {response.status_code}"
        )
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


def _decode_sniff(raw: bytes) -> str:
    """
    Декодирует байты в подходящей кодировке для анализа первых строк.
    Возвращает текст, даже если идеальной кодировки нет (с заменой ошибок).
    """
    for encoding in ("utf-8", "cp1251"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _detect_delimiter(raw: bytes) -> str | None:
    """
    Определяет разделитель CSV на основе первых строк файла.

    Приоритеты:
      1. Если первая строка содержит слово «маршрут» (в любом регистре и
         любом числе: маршрут, маршрута, маршруты) — это транспортные
         данные, у которых разделитель ВСЕГДА ';'.
      2. Иначе — эвристика по количеству символов в первой непустой строке:
         если ';' больше или равно ',' — берём ';', иначе ','.
      3. Если разделитель не удалось определить — возвращаем None
         (pandas попробует автоматически).
    """
    text = _decode_sniff(raw)

    first_line = None
    for line in text.splitlines():
        if line.strip():
            first_line = line
            break

    if first_line is None:
        return None

    # Транспортные данные: слово «маршрут» в первой строке -> всегда ';'
    if "маршрут" in first_line.lower():
        print(
            f"    Обнаружены транспортные данные (первая строка содержит "
            f"'маршрут') -> разделитель принудительно ';'"
        )
        return ";"

    # Эвристика: сравниваем количество разделителей в первой строке
    semicolons = first_line.count(";")
    commas = first_line.count(",")

    if semicolons > 0 and semicolons >= commas:
        print(f"    Эвристика: ';'={semicolons}, ','={commas} -> разделитель ';'")
        return ";"
    if commas > 0:
        print(f"    Эвристика: ';'={semicolons}, ','={commas} -> разделитель ','")
        return ","

    print(f"    Эвристика: разделитель не определён, оставляю автоопределение")
    return None


def parse_csv_with_pandas(url: str) -> pd.DataFrame | None:
    """
    Скачивает и парсит CSV-файл через pandas.

    Параметры:
      url — ссылка на файл данных (CSV/JSON на сайте)
      таймаут скачивания — DOWNLOAD_TIMEOUT (10 секунд)

    Сначала файл скачивается через requests (с таймаутом 10 секунд),
    затем передаётся в pandas, который автоматически определяет
    разделитель (sep=None, engine='python') и кодировку.

    Исключение: транспортные данные (первая строка содержит «Маршруты»)
    всегда читаются с принудительным разделителем ';'.

    Возвращает DataFrame или None при ошибке.
    """
    print(f"    Скачивание данных: {url}")
    try:
        response = requests.get(url, timeout=DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        print(
            f"    Скачано, размер: {len(response.content)} байт, "
            f"статус: {response.status_code}"
        )
    except requests.Timeout:
        print(
            f"    ОШИБКА: таймаут {DOWNLOAD_TIMEOUT} сек при скачивании {url}",
            file=sys.stderr,
        )
        return None
    except requests.RequestException as e:
        print(f"    ОШИБКА при скачивании {url}: {e}", file=sys.stderr)
        return None

    raw = response.content

    # Определяем разделитель (для транспортных данных — всегда ';')
    delimiter = _detect_delimiter(raw)

    content = io.BytesIO(raw)
    encodings_to_try = ["utf-8", "cp1251", "koi8-r"]
    last_error = None

    for encoding in encodings_to_try:
        try:
            sep_desc = repr(delimiter) if delimiter else "auto"
            print(f"    Попытка чтения: encoding={encoding}, sep={sep_desc}")

            content.seek(0)  # сброс указателя перед повторным чтением

            df = pd.read_csv(
                content,
                sep=delimiter,  # None — pandas определит сам
                engine="python",
                encoding=encoding,
                keep_default_na=False,
                dtype=str,
                on_bad_lines="warn",
            )

            df = df.fillna("")

            print(f"    Успешно: {len(df)} строк, {len(df.columns)} колонок")
            print(f"    Заголовки: {df.columns.tolist()}")
            if len(df) > 0:
                print(f"    Образец первой строки данных: {df.iloc[0].tolist()}")

            return df

        except Exception as e:
            last_error = e
            print(f"    Кодировка {encoding} не подошла: {e}")
            continue

    print(
        f"    ОШИБКА: CSV не читается ни в одной кодировке. "
        f"Последняя ошибка: {last_error}",
        file=sys.stderr,
    )
    return None


def dataframe_to_sheet_data(df: pd.DataFrame) -> list[list[str]]:
    """
    Преобразует DataFrame в список списков для Google Sheets.
    Первая строка — заголовки столбцов, остальные — данные.
    """
    headers = df.columns.tolist()
    data = df.values.tolist()
    return [headers] + data


def save_json(df: pd.DataFrame, filename: str):
    """
    Сохраняет DataFrame в JSON-файл (массив объектов).
    Формат: [{"колонка": "значение", ...}, ...]
    Файл создаётся в директории data/.
    """
    records = df.to_dict(orient="records")
    os.makedirs("data", exist_ok=True)
    path = os.path.join("data", f"{filename}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=4)
    print(f"    JSON сохранён: {path} ({len(records)} записей)")


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
    print(
        f"    Запись {rows} строк x {cols} колонок "
        f"в лист '{worksheet.title}'..."
    )

    # Пакетная запись: за один вызов отправляем все данные
    worksheet.update(values=data, range_name="A1")
    print(f"    Запись завершена")


def main():
    """
    Основной рабочий процесс:
      1. Сбор CSV-ссылок со страниц 1-3.
      2. Подключение к Google Таблице.
      3. Скачивание и парсинг каждого CSV через pandas,
         затем запись в соответствующий лист.
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
    print("Сервисный аккаунт: загружен из GOOGLE_SERVICE_ACCOUNT_JSON")
    try:
        sheet = get_google_sheet(spreadsheet_id, credentials_json)
        print(f"Подключение успешно: таблица '{sheet.title}'")
        print(f"Существующие листы: {[ws.title for ws in sheet.worksheets()]}")
    except Exception as e:
        print(f"ОШИБКА подключения к Google Sheets: {e}", file=sys.stderr)
        sys.exit(1)

    # Шаг 3: обработка каждого CSV-файла
    print(f"\n{'=' * 60}")
    print("ЭТАП 3: Скачивание, конвертация в JSON и загрузка в Google Sheets")
    print("=" * 60)
    processed = 0
    skipped = 0
    for csv_info in all_csv_links:
        filename = csv_info["filename"]
        url = csv_info["url"]

        print(f"\n--- {filename} ---")

        df = parse_csv_with_pandas(url)
        if df is None:
            print(f"  ПРОПУСК {filename}: не удалось прочитать CSV")
            skipped += 1
            continue

        # Сохранение в JSON (массив объектов)
        save_json(df, filename)

        # Конвертация в список списков для Google Sheets
        sheet_data = dataframe_to_sheet_data(df)

        # Создаём новый лист или получаем существующий
        try:
            worksheet = sheet.worksheet(filename)
            print(f"  Лист '{filename}' уже существует, будет перезаписан")
        except gspread.exceptions.WorksheetNotFound:
            worksheet = sheet.add_worksheet(title=filename, rows=1, cols=1)
            print(f"  Создан новый лист '{filename}'")

        update_sheet(worksheet, sheet_data)
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
