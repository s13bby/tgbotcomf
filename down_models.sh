#!/bin/bash
curl -L "https://huggingface.co/NSFW-API/NSFW-Wan-UMT5-XXL/resolve/main/nsfw_wan_umt5-xxl_fp8_scaled.safetensors" \
  --output "models/clip/nsfw_wan_umt5-xxl_fp8_scaled.safetensors"

curl -f -L -H "Authorization: Bearer YOUR_API_KEY_FOR_CIVITAI" \
  "https://civitai.com/api/download/models/2467166?type=Model&format=GGUF&size=full&fp=fp16" \
  --output "models/unet/DasiwaWAN22I2V14BTastysinV8_q5High.gguf"

curl -f -L -H "Authorization: Bearer YOUR_API_KEY_FOR_CIVITAI" \
  "https://civitai.com/api/download/models/2467350?type=Model&format=GGUF&size=full&fp=fp16" \
  --output "models/unet/DasiwaWAN22I2V14BTastysinV8_q5Low.gguf"

