# OpenTGL Parser

Парсер открытых данных г. Тольятти (https://tgl.ru/opendata/setlist/).

Скрипт собирает CSV-файлы со страниц 1–3 каталога открытых данных, парсит их через pandas (автоопределение разделителя и кодировки) и загружает в Google Таблицу. Каждому CSV-файлу соответствует отдельный лист, имя листа совпадает с именем файла (без расширения `.csv`).

## Установка и запуск локально

```bash
pip install requests beautifulsoup4 gspread google-auth pandas
python parser.py
```

Перед запуском необходимо задать переменные окружения:

| Переменная | Описание |
|---|---|
| `SPREADSHEET_ID` | ID целевой Google Таблицы |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | JSON-ключ сервисного аккаунта (одной строкой) |

---

## Настройка Google Cloud Console и сервисного аккаунта

### 1. Создание проекта

1. Перейдите в [Google Cloud Console](https://console.cloud.google.com/).
2. Нажмите на выпадающий список в верхней панели (рядом с надписью «Google Cloud Platform») → **New Project**.
3. Укажите название проекта (например, `tgl-opendata-parser`) и нажмите **Create**.

### 2. Включение API

1. Выберите созданный проект.
2. Перейдите в **APIs & Services** → **Library** (https://console.cloud.google.com/apis/library).
3. Найдите и включите **Google Sheets API**.
4. Найдите и включите **Google Drive API**.

### 3. Создание сервисного аккаунта

1. Перейдите в **APIs & Services** → **Credentials** (https://console.cloud.google.com/apis/credentials).
2. Нажмите **Create Credentials** → **Service Account**.
3. Укажите имя (например, `tgl-parser-sa`), нажмите **Create and Continue**.
4. Назначьте роль **Editor** (Редактор), нажмите **Continue**, затем **Done**.

### 4. Генерация JSON-ключа

1. На странице **Credentials** найдите созданный сервисный аккаунт и нажмите на его email.
2. Перейдите на вкладку **Keys**.
3. Нажмите **Add Key** → **Create New Key** → выберите формат **JSON** → **Create**.
4. Браузер скачает JSON-файл. Это ваш приватный ключ. Храните его в безопасном месте.

### 5. Предоставление доступа к Google Таблице

1. Откройте вашу Google Таблицу в браузере.
2. Нажмите **Настройки доступа** (кнопка **Share** в правом верхнем углу).
3. В поле ввода вставьте email сервисного аккаунта (выглядит как `имя@проект.iam.gserviceaccount.com`).
4. Выберите роль **Editor** (Редактор) и снимите галочку «Notify people», чтобы не отправлять уведомление.
5. Нажмите **Share**.

ID таблицы можно извлечь из её URL:
```
https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit#gid=0
```

---

## Добавление секретов в GitHub репозиторий

1. Откройте ваш репозиторий на GitHub.
2. Перейдите в **Settings** → **Secrets and variables** → **Actions**.
3. Нажмите **New repository secret**.
4. Добавьте два секрета:

| Имя секрета | Значение |
|---|---|
| `SPREADSHEET_ID` | ID вашей Google Таблицы |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Содержимое JSON-файла сервисного аккаунта (целиком, одной строкой) |

5. Нажмите **Add secret** для каждого.

---

## Расписание GitHub Actions

Воркфлоу запускается автоматически по расписанию:

- Каждые 2 дня в **06:00** и **15:00** по Самарскому времени (SAMT, UTC+4).
- В UTC это **02:00** и **11:00** соответственно.
- Крон-выражение: `0 2,11 */2 * *`

Также предусмотрен ручной запуск через вкладку **Actions** → **Обновление данных из открытых источников Тольятти** → **Run workflow**.

## Структура проекта

```
.
├── .github/workflows/
│   └── update_data.yml   # Конфигурация GitHub Actions
├── parser.py             # Основной скрипт парсера
└── README.md             # Данный файл
```
