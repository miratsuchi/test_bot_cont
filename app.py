# app.py
import os
import asyncio
import json
import threading
from flask import Flask, Response, abort
import requests
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramConflictError

# ====== Конфигурация (через переменные окружения) ======
API_TOKEN = os.getenv("API_TOKEN", "PUT_YOUR_TOKEN_HERE")
# ADMIN_IDS: "123,456,789"
ADMINS = [int(x) for x in os.getenv("ADMIN_IDS", "53962232").split(",") if x.strip()]

# Домены/файлы хранятся в файлах
FILES_DB = "files.json"
DOMAIN_DB = "domain.json"

# Фреймворки
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
app = Flask(__name__)

# FSM
class Form(StatesGroup):
    waiting_for_file = State()

# ===== helpers =====
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_current_file_meta():
    # returns dict like {"file_id": "...", "file_name": "..."} or None
    data = load_json(FILES_DB, {})
    return data.get("current")

def save_current_file_meta(file_id, file_name):
    save_json(FILES_DB, {"current": {"file_id": file_id, "file_name": file_name}})

def get_domain():
    # priority: saved domain -> RENDER_EXTERNAL_URL -> env DOMAIN -> fallback localhost
    d = load_json(DOMAIN_DB, {}).get("domain")
    if d:
        return d
    # Render provides RENDER_EXTERNAL_URL env var
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if render_url:
        return render_url.rstrip("/")  # e.g. https://myapp.onrender.com
    env_domain = os.getenv("DOMAIN")
    if env_domain:
        return env_domain.rstrip("/")
    # fallback
    port = os.getenv("PORT", "8080")
    return f"http://localhost:{port}"

def set_domain(domain):
    save_json(DOMAIN_DB, {"domain": domain.rstrip("/")})

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

# ===== Telegram handlers =====
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("У тебя нет прав.")
        return
    await message.answer("Привет! Отправь файл — он станет доступен по корневой ссылке.")
    await state.set_state(Form.waiting_for_file)

@dp.message(Form.waiting_for_file, F.document)
async def handle_file(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("У тебя нет прав.")
        return

    # получаем file_id и оригинальное имя
    file_id = message.document.file_id
    file_name = message.document.file_name or "file"

    save_current_file_meta(file_id, file_name)

    link = f"{get_domain()}/"
    await message.answer(f"Файл сохранён! Скачать можно по: {link}")
    await state.clear()

@dp.message(Command("setdomain"))
async def cmd_setdomain(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("У тебя нет прав.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /setdomain https://my-domain.com")
        return

    domain = parts[1].strip()
    # простая валидация: добавить http(s) если нет
    if not domain.startswith("http://") and not domain.startswith("https://"):
        domain = "https://" + domain
    set_domain(domain)
    await message.answer(f"Домен установлен: {domain}/")

@dp.message(Command("getlink"))
async def cmd_getlink(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("У тебя нет прав.")
        return
    await message.answer(get_domain() + "/")

# ===== Flask endpoints =====
@app.route("/", methods=["GET"])
def root_download():
    meta = get_current_file_meta()
    if not meta:
        # пустая белая страница (ничего не показываем)
        return Response("", content_type="text/html")

    file_id = meta.get("file_id")
    file_name = meta.get("file_name") or "file"

    # Получаем путь к файлу в Telegram
    r = requests.get(f"https://api.telegram.org/bot{API_TOKEN}/getFile?file_id={file_id}", timeout=15)
    if not r.ok:
        return abort(502)

    try:
        file_path = r.json()["result"]["file_path"]
    except Exception:
        return abort(502)

    # Скачиваем файл потоково из Telegram
    file_resp = requests.get(f"https://api.telegram.org/file/bot{API_TOKEN}/{file_path}", stream=True, timeout=30)
    if not file_resp.ok:
        return abort(502)

    headers = {
        "Content-Disposition": f'attachment; filename="{file_name}"',
    }
    # content_type передаём отдельно, Response принимает итератор
    return Response(file_resp.iter_content(chunk_size=8192),
                    headers=headers,
                    content_type=file_resp.headers.get("content-type", "application/octet-stream"))

# ===== запуск (Flask в потоке, aiogram polling) =====
async def start_bot_polling():
    # Delete webhook if previously set (helps avoid TelegramConflictError)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        # не критично - продолжим
        pass

    # Start polling (this will retry on conflict too)
    # If TelegramConflictError still appears, убедись, что запущен только 1 экземпляр
    try:
        await dp.start_polling(bot)
    except TelegramConflictError as e:
        # логируем и пробуем снова (Render выводит в логи)
        print("TelegramConflictError:", e)
        # можно ждать и повторять, но лучше убедиться, что есть только один экземпляр
        raise

def run_flask():
    # порт берём из окружения (Render предоставляет PORT)
    port = int(os.getenv("PORT", "8080"))
    # отключаем reloader — иначе запустится два процесса
    app.run(host="0.0.0.0", port=port, use_reloader=False)

def main():
    # Запускаем Flask в отдельном потоке
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

    # Запускаем polling бота
    asyncio.run(start_bot_polling())

if __name__ == "__main__":
    main()