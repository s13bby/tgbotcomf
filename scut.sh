#!/bin/bash

cd ~/ComfyUI && source venv/bin/activate
screen -dmS comfy_bot python bot.py
screen -dmS comfy_main python main.py
echo -e "Бот и ComfyUI запущены в отдельных сессиях screen.\n"
echo -e "Посмотреть, что там происходит, можно будет командами screen -r comfy_bot и screen -r comfy_main\n"
echo -e "Чтобы выйти: ctrl+A, потом D"