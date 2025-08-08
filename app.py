import asyncio
import json
import threading
from flask import Flask, Response, abort, request
import requests
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

API_TOKEN = "твой_токен_бота"
ADMIN_ID = 123456789  # твой Telegram ID

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

app = Flask(__name__)

class Form(StatesGroup):
    waiting_for_file = State()
    waiting_for_filename = State()

# Загружаем или создаём файл базы
def load_files_db():
    try:
        with open("files.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_files_db(data):
    with open("files.json", "w") as f:
        json.dump(data, f)

# Telegram бот: команда старт - только для админа
@dp.message(Command("start"))
async def start_handler(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Ты не админ.")
        return
    await message.answer("Привет! Отправь имя ссылки (например: myfile) — это часть URL, по которой будет скачиваться файл.")
    await state.set_state(Form.waiting_for_filename)

# Принимаем имя ссылки
@dp.message(Form.waiting_for_filename)
async def filename_handler(message: Message, state: FSMContext):
    filename = message.text.strip()
    if "/" in filename or "\\" in filename or not filename.isalnum():
        await message.answer("Имя ссылки должно быть буквенно-цифровым и без слэшей.")
        return
    await state.update_data(filename=filename)
    await message.answer(f"Отлично! Теперь отправь файл, который будет доступен по ссылке /{filename}")
    await state.set_state(Form.waiting_for_file)

# Принимаем файл
@dp.message(Form.waiting_for_file, F.document)
async def file_handler(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Ты не админ.")
        return

    data = await state.get_data()
    filename = data.get("filename")
    if not filename:
        await message.answer("Ошибка: сначала отправь имя ссылки командой /start")
        return

    file_id = message.document.file_id
    files_db = load_files_db()
    files_db[filename] = file_id
    save_files_db(files_db)

    await message.answer(f"Файл сохранён! Теперь его можно скачать по ссылке:\nhttps://твой_домен/{filename}")
    await state.clear()

# Flask сервер — отдаём файл по имени
@app.route("/<filename>")
def download_file(filename):
    files_db = load_files_db()
    file_id = files_db.get(filename)
    if not file_id:
        return abort(404)

    # Получаем путь к файлу Telegram
    r = requests.get(f"https://api.telegram.org/bot{API_TOKEN}/getFile?file_id={file_id}")
    if not r.ok:
        return abort(500)

    file_path = r.json()["result"]["file_path"]

    # Скачиваем файл потоково
    file_response = requests.get(f"https://api.telegram.org/file/bot{API_TOKEN}/{file_path}", stream=True)
    if not file_response.ok:
        return abort(500)

    headers = {
        "Content-Disposition": f"attachment; filename={filename}",
        "Content-Type": file_response.headers.get('content-type'),
        "Content-Length": file_response.headers.get('content-length'),
    }
    return Response(file_response.iter_content(chunk_size=8192),
                    headers=headers)

# Опционально: можно показать простую страницу на корне
@app.route("/")
def index():
    return "Файлы доступны по ссылкам: /имя_файла"

# Функция для запуска бота
async def start_bot():
    await dp.start_polling(bot)

# Запускаем Flask и бота параллельно
def main():
    # Запускаем Flask в отдельном потоке
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=5000)).start()

    # Запускаем бота в asyncio
    asyncio.run(start_bot())

if __name__ == "__main__":
    main()