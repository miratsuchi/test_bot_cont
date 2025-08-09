import asyncio
import json
import threading
import os
from flask import Flask, Response, abort
import requests
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()  # Загружаем переменные из .env

API_TOKEN = os.getenv("API_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "")  # строка с ID через запятую
DOMAIN = os.getenv("DOMAIN", "https://example.com")

# Преобразуем ADMIN_IDS в множество чисел
try:
    ADMIN_IDS = set(int(x.strip()) for x in ADMIN_IDS.split(",") if x.strip())
except Exception:
    ADMIN_IDS = set()

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

app = Flask(__name__)

class Form(StatesGroup):
    waiting_for_file = State()

def load_files_db():
    try:
        with open("files.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_files_db(data):
    with open("files.json", "w") as f:
        json.dump(data, f)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

@dp.message(Command("start"))
async def start_handler(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Ты не админ.")
        return
    await message.answer(
        f"Привет! Отправь файл — он станет доступен для скачивания по ссылке:\n{DOMAIN}/"
    )
    await state.set_state(Form.waiting_for_file)

@dp.message(Form.waiting_for_file, F.document)
async def file_handler(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Ты не админ.")
        return

    file_id = message.document.file_id
    files_db = {"current": file_id}  # Всегда сохраняем как "current"
    save_files_db(files_db)

    await message.answer(f"Файл сохранён! Теперь его можно скачать по ссылке:\n{DOMAIN}/")
    await state.clear()

@app.route("/")
def download_file():
    files_db = load_files_db()
    file_id = files_db.get("current")
    if not file_id:
        return Response("", content_type="text/html")

    r = requests.get(f"https://api.telegram.org/bot{API_TOKEN}/getFile?file_id={file_id}")
    if not r.ok:
        return abort(500)

    file_path = r.json()["result"]["file_path"]
    file_response = requests.get(f"https://api.telegram.org/file/bot{API_TOKEN}/{file_path}", stream=True)
    if not file_response.ok:
        return abort(500)

    filename = "file"

    return Response(
        file_response.iter_content(chunk_size=8192),
        content_type=file_response.headers.get('content-type'),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": file_response.headers.get('content-length'),
        }
    )

async def start_bot():
    await dp.start_polling(bot)

def main():
    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=8080, use_reloader=False),
        daemon=True,
    )
    flask_thread.start()

    asyncio.run(start_bot())

if __name__ == "__main__":
    main()