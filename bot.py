import asyncio
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime
from PIL import Image
import subprocess
import requests
import shutil
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# === КОНФИГУРАЦИЯ ===
TELEGRAM_TOKEN = "7829006328:AAFOyk-CHv5Eg0POH2qnIuOP0elGGyE-c_A"
COMFYUI_URL = "http://127.0.0.1:8188"
COMFYUI_OUTPUT_DIR = "/home/ivan/comfy/ComfyUI/output"  # Единая переменная для пути к output
COMFYUI_INPUT_DIR = "/home/ivan/comfy/ComfyUI/input"    # Единая переменная для пути к input
ADMIN_IDS = [1145483994, 498845556, 111111111, 222222222, 333333333]

# Состояния разговора
WAITING_PROMPT, WAITING_CONFIRM = range(2)

# === ИНИЦИАЛИЗАЦИЯ БД ===
def init_db():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

def get_user_balance(user_id):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else 0

def add_user(user_id, username):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
    conn.commit()
    conn.close()

def update_balance(user_id, amount):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

def deduct_balance(user_id, amount):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

# === ОБРАБОТКА ИЗОБРАЖЕНИЯ ===
def resize_image(image_path, max_size=600):
    with Image.open(image_path) as img:
        img = img.convert('RGBA')
        width, height = img.size
        if width > height:
            new_width = min(width, max_size)
            new_height = int(height * new_width / width)
        else:
            new_height = min(height, max_size)
            new_width = int(width * new_height / height)
        resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        return new_width, new_height, resized

# === МОДИФИКАЦИЯ WORKFLOW ===
def modify_workflow(workflow_str, prompt, image_filename, width, height, video_prefix):
    wf = json.loads(workflow_str)
    
    # Prompt
    if '93' in wf and 'inputs' in wf['93']:
        wf['93']['inputs']['text'] = prompt

    # Размеры (округляем до 16)
    height = (height // 16) * 16
    width = (width // 16) * 16

    if '183' in wf and 'inputs' in wf['183']:
        wf['183']['inputs']['value'] = height
    if '184' in wf and 'inputs' in wf['184']:
        wf['184']['inputs']['value'] = width

    # Изображение
    if '193' in wf and 'inputs' in wf['193']:
        wf['193']['inputs']['image'] = image_filename

    # Уникальный префикс для видео (нода 214 - FIENAL)
    if '214' in wf and 'inputs' in wf['214']:
        wf['214']['inputs']['filename_prefix'] = video_prefix

    return json.dumps(wf)

# === ЗАПУСК WORKFLOW ===
async def run_comfyui_workflow(workflow_json_str, image_path, video_prefix):
    # 1. Копируем изображение в input директорию
    os.makedirs(COMFYUI_INPUT_DIR, exist_ok=True)
    image_filename = f"user_image_{uuid.uuid4().hex[:8]}.png"
    input_path = os.path.join(COMFYUI_INPUT_DIR, image_filename)
    shutil.copy(image_path, input_path)

    # 2. Отправляем workflow в ComfyUI
    workflow_dict = json.loads(workflow_json_str)
    payload = {"prompt": workflow_dict}  # ВАЖНО: без пробела в ключе "prompt"
    
    resp = requests.post(f"{COMFYUI_URL}/prompt", json=payload)
    if resp.status_code != 200:
        raise Exception(f"ComfyUI error {resp.status_code}: {resp.text[:500]}")
    
    prompt_id = resp.json()['prompt_id']
    print(f"Запущен prompt_id: {prompt_id}")

    # 3. Ждём завершения и ищем видео по уникальному префиксу
    max_wait = 2600  # 10 минут
    for i in range(max_wait):
        await asyncio.sleep(2)
        try:
            history_resp = requests.get(f"{COMFYUI_URL}/history/{prompt_id}")
            history = history_resp.json()
            
            if prompt_id in history:
                # Ищем файл в output директории по уникальному префиксу
                os.makedirs(COMFYUI_OUTPUT_DIR, exist_ok=True)
                for filename in sorted(os.listdir(COMFYUI_OUTPUT_DIR), reverse=True):
                    if filename.startswith(video_prefix) and filename.endswith(('.mp4', '.webm')):
                        video_path = os.path.join(COMFYUI_OUTPUT_DIR, filename)
                        if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
                            print(f"Видео найдено: {video_path}")
                            # Удаляем исходное изображение из input
                            if os.path.exists(input_path):
                                os.unlink(input_path)
                            return video_path
                # Если файл ещё не записан полностью — ждём
                if i < max_wait - 1:
                    continue
                else:
                    raise Exception(f"Видео с префиксом '{video_prefix}' не найдено после {max_wait*2} секунд")
        except Exception as e:
            if i == max_wait - 1:
                raise Exception(f"Ошибка при ожидании результата: {str(e)}")
    
    raise Exception("Таймаут генерации")

# === ОЧИСТКА МЕТАДАННЫХ БЕЗ ОШИБКИ КРОСС-ДЕВАЙС ===
def clean_metadata(video_path):
    # Используем временную директорию на том же диске, что и output
    temp_dir = os.path.dirname(video_path)
    temp_video = tempfile.NamedTemporaryFile(suffix='.mp4', dir=temp_dir, delete=False)
    temp_path = temp_video.name
    temp_video.close()
    
    try:
        subprocess.run([
            'ffmpeg', '-y', '-i', video_path,
            '-map_metadata', '-1',
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-pix_fmt', 'yuv420p',
            temp_path
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Безопасная замена: копируем + удаляем (работает между файловыми системами)
        shutil.copy2(temp_path, video_path)
        os.unlink(temp_path)
        return video_path
    except Exception as e:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise Exception(f"Ошибка очистки метаданных: {str(e)}")

# === ХЕНДЛЕРЫ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username)
    balance = get_user_balance(user.id)
    await update.message.reply_text(
        f"Привет, {user.first_name}! Добро пожаловать в бот для генерации видео.\n"
        f"Ваш баланс: {balance} токенов.\n"
        "Отправьте фото для генерации."
    )
    return ConversationHandler.END

async def handle_admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("У вас нет прав администратора.")
        return
    try:
        tokens = int(context.args[0])
        target_id = int(context.args[1])
        update_balance(target_id, tokens)
        await update.message.reply_text(f"Добавлено {tokens} токенов пользователю {target_id}.")
    except (IndexError, ValueError):
        await update.message.reply_text("Использование: /add <токены> <user_id>")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file = await update.message.photo[-1].get_file()
    temp_image = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    await photo_file.download_to_drive(temp_image.name)

    width, height, resized_img = resize_image(temp_image.name)
    resized_path = temp_image.name.replace('.png', '_resized.png')
    resized_img.save(resized_path)
    os.unlink(temp_image.name)

    context.user_data['image_path'] = resized_path
    context.user_data['width'] = width
    context.user_data['height'] = height

    await update.message.reply_text(
        f"Картинка получена и масштабирована до {width}x{height}.\n"
        "Введите промпт на английском:"
    )
    return WAITING_PROMPT

async def handle_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = update.message.text.strip()
    context.user_data['prompt'] = prompt
    
    # Генерируем уникальный префикс для видео
    video_prefix = f"video_{uuid.uuid4().hex[:12]}"
    context.user_data['video_prefix'] = video_prefix
    
    with open('main.json', 'r', encoding='utf-8') as f:
        workflow = f.read()

    image_filename = os.path.basename(context.user_data['image_path'])
    modified_workflow = modify_workflow(
        workflow, 
        prompt, 
        image_filename,
        context.user_data['width'], 
        context.user_data['height'],
        video_prefix
    )

    cost = 20
    balance = get_user_balance(update.effective_user.id)
    await update.message.reply_text(
        f"Стоимость генерации: {cost} токенов.\n"
        f"Ваш баланс: {balance}\n"
        "Начать генерацию? (да/нет)"
    )
    return WAITING_CONFIRM

async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.lower() not in ['да', 'yes', 'y']:
        await update.message.reply_text("Операция отменена.")
        if 'image_path' in context.user_data and os.path.exists(context.user_data['image_path']):
            os.unlink(context.user_data['image_path'])
        context.user_data.clear()
        return ConversationHandler.END

    user_id = update.effective_user.id
    cost = 20
    balance = get_user_balance(user_id)
    if balance < cost:
        await update.message.reply_text("Недостаточно токенов.")
        return ConversationHandler.END

    deduct_balance(user_id, cost)
    await update.message.reply_text("🚀 Начинаю генерацию...")

    try:
        image_path = context.user_data['image_path']
        prompt = context.user_data['prompt']
        video_prefix = context.user_data['video_prefix']

        with open('main.json', 'r', encoding='utf-8') as f:
            workflow_base = f.read()

        # Копируем изображение в input директорию
        os.makedirs(COMFYUI_INPUT_DIR, exist_ok=True)
        image_filename = f"user_image_{uuid.uuid4().hex[:8]}.png"
        input_path = os.path.join(COMFYUI_INPUT_DIR, image_filename)
        shutil.copy(image_path, input_path)

        workflow_json = modify_workflow(
            workflow_base, 
            prompt, 
            image_filename,
            context.user_data['width'], 
            context.user_data['height'],
            video_prefix
        )

        # Запускаем генерацию
        video_path = await run_comfyui_workflow(workflow_json, image_path, video_prefix)
        
        # Очищаем метаданные
        video_path = clean_metadata(video_path)
        
        # Отправляем видео
        with open(video_path, 'rb') as video_file:
            await update.message.reply_video(video_file, supports_streaming=True)
        
        await update.message.reply_text("✅ Генерация завершена!")
        
        # Удаляем файлы
        if os.path.exists(image_path):
            os.unlink(image_path)
        if os.path.exists(video_path):
            os.unlink(video_path)
        if os.path.exists(input_path):
            os.unlink(input_path)
            
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка генерации: {str(e)}")
        update_balance(user_id, cost)  # Возврат токенов
    
    # Показываем баланс
    new_balance = get_user_balance(user_id)
    await update.message.reply_text(f"Ваш баланс: {new_balance} токенов.")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    if 'image_path' in context.user_data and os.path.exists(context.user_data['image_path']):
        os.unlink(context.user_data['image_path'])
    context.user_data.clear()
    return ConversationHandler.END

# === ЗАПУСК БОТА ===
def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.PHOTO, handle_photo)],
        states={
            WAITING_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_prompt)],
            WAITING_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_confirm)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", handle_admin_add))
    app.add_handler(conv_handler)

    print(f"Бот запущен. Output dir: {COMFYUI_OUTPUT_DIR}")
    app.run_polling()

if __name__ == '__main__':
    main()