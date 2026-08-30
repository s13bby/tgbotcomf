#!/bin/bash

cd ~/ComfyUI && source venv/bin/activate
screen -dmS comfy_main python main.py
echo -e "ComfyUI запущен отдельной сессии screen.\n"
echo -e "Посмотреть, что там происходит, можно воспользоваться командой screen -r comfy_main\n"
echo -e "Чтобы выйти: ctrl+A, потом D\n"