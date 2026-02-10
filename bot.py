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
import glob
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler

RULES_CALLBACK = "show_rules"
BACK_CALLBACK = "back_to_main"

COMFYUI_WORKFLOW = "video_generate.json"
TELEGRAM_TOKEN = "TELEGRAM_TOKEN"
COMFYUI_URL = "http://127.0.0.1:8188"
COMFYUI_OUTPUT_DIR = "/home/ivan/ComfyUI/output" # <-- сменить на относительный путь
COMFYUI_INPUT_DIR = "/home/ivan/ComfyUI/input"   # <-- сменить на относительный путь
COMFYUI_COMMON_PROMT = "woman take off her clothes, full nudity, full naked, pyssy, boobs, nipples. camera zooms out"
COMFYUI_MAX_SIZE = 580
ADMIN_IDS = [1145483994, 498845556, 111111111, 222222222, 333333333]

WAITING_CONFIRM = 0

def init_db():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS admin_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER NOT NULL,
        admin_username TEXT,
        target_user_id INTEGER NOT NULL,
        tokens_added INTEGER NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

def log_admin_action(admin_id, admin_username, target_user_id, tokens):
    """Записывает действие админа в файл"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = (
        f"[{timestamp}] Admin {admin_id} (@{admin_username}) "
        f"added {tokens} tokens to user {target_user_id}\n"
    )
    with open('admin_actions.log', 'a', encoding='utf-8') as f:
        f.write(log_entry)

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

def resize_image(image_path, max_size=COMFYUI_MAX_SIZE):
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

def find_video_output_node(workflow_dict):
    for node_id, node in workflow_dict.items():
        inputs = node.get("inputs", {})
        if isinstance(inputs, dict) and "filename_prefix" in inputs:
            return node_id
    raise ValueError("No video output node with 'filename_prefix' found in workflow!")

def modify_workflow(workflow_str, prompt, image_filename, width, height, video_prefix):
    wf_raw = json.loads(workflow_str)
    wf = {}
    for key, value in wf_raw.items():
        clean_key = key.strip()
        if isinstance(value, dict) and 'inputs' in value:
            inputs_raw = value['inputs']
            if isinstance(inputs_raw, dict):
                clean_inputs = {}
                for in_key, in_val in inputs_raw.items():
                    clean_inputs[in_key.strip()] = in_val
                value = {k: v for k, v in value.items() if k != 'inputs'}
                value['inputs'] = clean_inputs
        wf[clean_key] = value

    if '93' in wf and 'inputs' in wf['93']:
        wf['93']['inputs']['text'] = f"{COMFYUI_COMMON_PROMT}"

    height = (height // 16) * 16
    width = (width // 16) * 16

    if '183' in wf and 'inputs' in wf['183']:
        wf['183']['inputs']['value'] = height
    if '184' in wf and 'inputs' in wf['184']:
        wf['184']['inputs']['value'] = width

    if '193' in wf and 'inputs' in wf['193']:
        wf['193']['inputs']['image'] = image_filename

    if '214' in wf and 'inputs' in wf['214']:
        wf['214']['inputs']['filename_prefix'] = video_prefix

    return json.dumps(wf)

def find_video_by_prefix(output_dir, prefix, extensions=('.mp4', '.webm', '.mkv')):
    for ext in extensions:
        pattern = os.path.join(output_dir, '**', f"{prefix}*{ext}")
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return max(matches, key=os.path.getmtime)
    return None

async def run_comfyui_workflow(workflow_json_str, input_image_path, video_prefix):
    
    workflow_dict = json.loads(workflow_json_str)
    resp = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow_dict})
    if resp.status_code != 200:
        raise Exception(f"ComfyUI API error {resp.status_code}: {resp.text[:500]}")
    
    prompt_id = resp.json()['prompt_id']
    print(f"✅ Workflow started | prompt_id: {prompt_id} | prefix: {video_prefix}")

    max_wait = 750
    for i in range(max_wait):
        await asyncio.sleep(2)
        try:
            history_resp = requests.get(f"{COMFYUI_URL}/history/{prompt_id}")
            history = history_resp.json()
            
            if prompt_id not in history:
                continue

            exec_info = history[prompt_id].get("exec_info", {})
            if "node_errors" in exec_info and exec_info["node_errors"]:
                errors = list(exec_info["node_errors"].keys())
                raise Exception(f"ComfyUI node execution failed at nodes: {errors}")

            if history[prompt_id].get("status", {}).get("completed", False):
                break
        except Exception as e:
            if i == max_wait - 1:
                raise Exception(f"Error checking ComfyUI history: {str(e)}")
    else:
        raise Exception(f"Workflow timeout after 25 minutes (prompt_id: {prompt_id})")

    for _ in range(30):
        await asyncio.sleep(2)
        video_path = find_video_by_prefix(COMFYUI_OUTPUT_DIR, video_prefix)
        if video_path and os.path.getsize(video_path) > 1024:
            last_size = -1
            stable = 0
            for _ in range(5):
                size = os.path.getsize(video_path)
                if size == last_size:
                    stable += 1
                    if stable >= 2:
                        break
                else:
                    stable = 0
                last_size = size
                await asyncio.sleep(1)
            print(f"🎬 Video found: {video_path}")
            return video_path

    recent = []
    for root, _, files in os.walk(COMFYUI_OUTPUT_DIR):
        for f in sorted(files, key=lambda x: os.path.getmtime(os.path.join(root, x)), reverse=True)[:5]:
            recent.append(os.path.relpath(os.path.join(root, f), COMFYUI_OUTPUT_DIR))
    raise Exception(
        f"Video with prefix '{video_prefix}' not found in output directory.\n"
        f"Recent files:\n" + "\n".join(f"  - {f}" for f in recent)
    )

def clean_metadata(video_path):
    temp_dir = os.path.dirname(video_path)
    with tempfile.NamedTemporaryFile(suffix='.mp4', dir=temp_dir, delete=False) as tmp:
        temp_path = tmp.name

    try:
        subprocess.run([
            'ffmpeg', '-y', '-i', video_path,
            '-map_metadata', '-1',
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-pix_fmt', 'yuv420p',
            temp_path
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        shutil.copy2(temp_path, video_path)
        os.unlink(temp_path)
        return video_path
    except Exception as e:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise Exception(f"Metadata cleanup failed: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username)
    balance = get_user_balance(user.id)
    keyboard = [[InlineKeyboardButton("ПРАВИЛА", callback_data=RULES_CALLBACK)]]
    await update.message.reply_text(
        f"👋🏻 Привет, Творец!\n\n"
        "Рада видеть тебя в SecretRoom\n\n"
        "✅ Любая фантазия о которой ты мечтал оживёт в этом боте\n\n"
        "Жми «🔮 Оживить фото» и наслаждайся\n\n"
        "Стоимость: 20 токен за запрос.\n\n"
        f"Ваш баланс: {balance} токенов.",
        reply_markup=InlineKeyboardMarkup(keyboard),
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
        log_admin_action(
            update.effective_user.id,
            update.effective_user.username or 'unknown',
            target_id,
            tokens
        )
        await update.message.reply_text(f"Добавлено {tokens} токенов пользователю {target_id}.")
    except (IndexError, ValueError):
        await update.message.reply_text("Использование: /add <токены> <user_id>")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file = await update.message.photo[-1].get_file()
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
        temp_image = tmp.name
    await photo_file.download_to_drive(temp_image)

    width, height, resized_img = resize_image(temp_image)
    resized_path = temp_image.replace('.png', '_resized.png')
    resized_img.save(resized_path, format='PNG')
    os.unlink(temp_image)

    context.user_data['image_path'] = resized_path
    context.user_data['width'] = width
    context.user_data['height'] = height

    video_prefix = f"video_{uuid.uuid4().hex[:12]}"
    context.user_data['video_prefix'] = video_prefix

    cost = 20
    balance = get_user_balance(update.effective_user.id)
    await update.message.reply_text(
        f"Стоимость генерации: {cost} токенов.\n"
        f"Ваш баланс: {balance}\n"
        "Начать генерацию? (да/нет)"
    )
    return WAITING_CONFIRM

async def handle_photo_anywhere(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await handle_photo(update, context)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == RULES_CALLBACK:
        keyboard = [[InlineKeyboardButton("← Назад", callback_data=BACK_CALLBACK)]]
        await query.message.reply_text(
            "🖼 Творец, отправь фото ниже!:\n\n"
            "Выбери фото человека или из аниме🙈:\n\n"
            "Для лучшего результата следуй этим правилам:\n\n"
            "✅ Фотография в полный рост\n"
            "✅ Девушка смотрит прямо в камеру\n"
            "✅ Хорошее освещение\n"
            "❌ Нет солнцезащитных очков\n"
            "❌ Не закрывать лицо волосами",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await query.message.delete()
    
    elif query.data == BACK_CALLBACK:
        context.user_data.clear()
        
        user = update.effective_user
        add_user(user.id, user.username)
        balance = get_user_balance(user.id)
        keyboard = [[InlineKeyboardButton("ПРАВИЛА", callback_data=RULES_CALLBACK)]]
        await query.message.reply_text(
            f"👋🏻 Привет, Творец!\n\n"
            "Рада видеть тебя в SecretRoom\n\n"
            "✅ Любая фантазия о которой ты мечтал оживёт в этом боте\n\n"
            "Жми «🔮 Оживить фото» и наслаждайся\n\n"
            "Стоимость: 20 токен за запрос.\n\n"
            f"Ваш баланс: {balance} токенов.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await query.message.delete()

async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text not in ['да', 'yes', 'y']:
        await update.message.reply_text("Операция отменена.")
        image_path = context.user_data.get('image_path')
        if image_path and os.path.exists(image_path):
            os.unlink(image_path)
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
        user_id = update.effective_user.id
        image_path = context.user_data['image_path']
        video_prefix = context.user_data['video_prefix']

        os.makedirs(COMFYUI_INPUT_DIR, exist_ok=True)
        image_filename = f"user_image_{uuid.uuid4().hex[:12]}.png"
        input_image_path = os.path.join(COMFYUI_INPUT_DIR, image_filename)
        shutil.copy(image_path, input_image_path)

        with open(f'{COMFYUI_WORKFLOW}', 'r', encoding='utf-8') as f:
            workflow_base = f.read()

        modified_workflow = modify_workflow(
            workflow_base,
            "",
            image_filename,
            context.user_data['width'],
            context.user_data['height'],
            video_prefix
        )

        video_path = await run_comfyui_workflow(modified_workflow, input_image_path, video_prefix)
        video_path = clean_metadata(video_path)

        with open(video_path, 'rb') as video_file:
            await update.message.reply_video(video_file, supports_streaming=True)

        await update.message.reply_text("✅ Генерация завершена!")

        for p in [image_path, input_image_path, video_path]:
            if os.path.exists(p):
                os.unlink(p)

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка генерации: {str(e)}")
        update_balance(user_id, cost) 

    new_balance = get_user_balance(user_id)
    await update.message.reply_text(f"Ваш баланс: {new_balance} токенов.")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    image_path = context.user_data.get('image_path')
    if image_path and os.path.exists(image_path):
        os.unlink(image_path)
    context.user_data.clear()
    return ConversationHandler.END

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()


    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_photo)],
        states={
            WAITING_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_confirm)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", handle_admin_add))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(handle_callback))
    print(f"Бот запущен. Output dir: {COMFYUI_OUTPUT_DIR}")
    app.run_polling()

if __name__ == '__main__':
    main()
