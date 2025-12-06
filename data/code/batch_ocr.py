import os
import subprocess
from pathlib import Path

# 目录配置
BASE_DIR = Path(__file__).resolve().parent.parent   # poker_tracker/
ORIGIN_DIR = BASE_DIR / "origin_data"
OUTPUT_DIR = BASE_DIR / "ocr_data"
SCRIPT = BASE_DIR / "code" / "chatgpt_ocr.py"

# 支持的图片后缀
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".JPG", ".PNG"}

def main():
    print(f"🔍 扫描目录: {ORIGIN_DIR}")

    if not ORIGIN_DIR.exists():
        print("❌ origin_data 目录不存在，请确认路径是否正确")
        return

    OUTPUT_DIR.mkdir(exist_ok=True)

    # 找到所有图片文件
    images = sorted([p for p in ORIGIN_DIR.iterdir() if p.suffix in IMAGE_EXTS])

    if not images:
        print("⚠️ origin_data 下没有找到任何图片文件")
        return

    print(f"📸 一共找到 {len(images)} 张图片，开始批量处理...\n")

    for img in images:
        out_file = OUTPUT_DIR / (img.stem + ".json")
        cmd = [
            "python3",
            str(SCRIPT),
            "--image", str(img),
            "--output", str(out_file)
        ]

        print(f"➡️ 正在处理: {img.name}")
        print(f"   将写入: {out_file.name}")

        try:
            subprocess.run(cmd, check=True)
            print(f"   ✅ 完成\n")
        except subprocess.CalledProcessError as e:
            print(f"   ❌ 处理失败: {img.name}")
            print(f"   错误信息: {e}\n")

    print("🎉 全部处理完毕！")
    print(f"📂 结果目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()