# Гайд

> Установка приспособлена только для linux дистрибутивов (arch-based, redhat-based, debian-based)
> 
> любые вопросы -> qaws3623@gmail.com

## Установка ComfyUI
git clone https://github.com/Comfy-Org/ComfyUI.git && cd ComfyUI

python3 -m venv venv && source venv/bin/activate

pip install torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cu130

pip install -r requirements.txt && pip install -r manager_requirements.txt

python main.py --enable-manager

## Установка бота + моделей
cd && git clone https://github.com/s13bby/tgbotcomf.git

cd tgbotcomf/ && tar -xf models_and_nodes.tar.xz && mv models/ custom_nodes/ bot.py bot.db down_models.sh video_generate.json bot-requirements.txt ~/ComfyUI/

chmod +x tgbotcomf/scut.sh && mv tgbotcomf/scut.sh ~/

bash ~/ComfyUI/down_models.sh 

source ComfyUI/venv/bin/activate && pip install -r bot-requirements.txt

## Установка screen
arch
> sudo pacman -S screen

deb
> sudo apt install screen

fedora
> sudo dnf install screen

## Запуск бота + comfyui
./scut.sh
