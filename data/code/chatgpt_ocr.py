"""Poker screenshot OCR via OpenAI GPT vision.

Usage example:

    python3 code/chatgpt_ocr.py --image origin_data/demo.jpg --output ocr_data/demo.json

Requirements:
    pip3 install openai pandas pillow

Environment:
    export OPENAI_API_KEY="sk-..."  # or set in .env / system env
"""

import argparse
import base64
import json
import os
from pathlib import Path
from typing import List, Dict, Any

import pandas as pd
from PIL import Image
from openai import OpenAI


# ========== 配置区 ==========
# 你可以在这里统一改模型名称，例如："gpt-4.1-mini" / "gpt-4.1" / "gpt-4o-mini" / "gpt-4o"
OPENAI_VISION_MODEL = "gpt-4o"


def encode_image_to_base64(image_path: str) -> str:
    """读取图片并编码为 base64 字符串。"""
    with open(image_path, "rb") as f:
        img_bytes = f.read()
    return base64.b64encode(img_bytes).decode("utf-8")


def build_prompt() -> str:
    """构造给 GPT 的中文说明 Prompt。"""
    return (
        "你是一名德州扑克记账助手。\n"
        "用户提供的是一张牌局结算截图，其中包含一个表格：每一行包含玩家的昵称、手数、码量、盈亏。\n"
        "此外，该截图中也可以看出本次牌局中 \"一手筹码量\"（例如：200 一手）。\n"
        "请你认真观察截图中的内容，并严格按照以下 JSON 对象格式输出识别结果：\n\n"
        "{\n"
        "  \"per_hand_amount\": 一手筹码量(整数),\n"
        "  \"rows\": [\n"
        "    {\"name\": \"昵称\", \"hands\": 手数(整数), \"amount\": 码量(整数), \"profit\": 盈亏(整数)},\n"
        "    {\"name\": \"...\", \"hands\": ..., \"amount\": ..., \"profit\": ...}\n"
        "  ]\n"
        "}\n\n"
        "注意：\n"
        "1. 只输出上述 JSON 对象本身，不要输出任何额外文字。\n"
        "2. 盈亏字段 profit：赢为正数，输为负数，去掉前面的 + 号。\n"
        "3. 一手筹码量 per_hand_amount 必须是一个整数（例如 200），如果截图中存在多个类似数字，请结合表格和标题判断最合理的那个。\n"
        "4. 如果截图中存在无法辨认的某一行，可以跳过该行，不要胡乱猜测。\n"
        "5. 不要把截图中的表头（例如：昵称、手数、码量、盈亏）作为一行数据加入 rows。\n"
    )


def call_gpt_vision_on_image(image_path: str) -> Dict[str, Any]:
    """调用 OpenAI GPT 模型对截图做结构化识别，返回字典：{"per_hand_amount": int, "rows": [...]}。"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("请先在环境变量中设置 OPENAI_API_KEY")

    client = OpenAI(api_key=api_key)

    # 确认图片存在，并尝试打开（防止路径错误）
    image_path_obj = Path(image_path)
    if not image_path_obj.exists():
        raise FileNotFoundError(f"图片文件不存在：{image_path_obj}")

    # 简单验证图片能被打开
    Image.open(image_path_obj).load()

    b64_image = encode_image_to_base64(str(image_path_obj))

    prompt = build_prompt()

    # 使用 response_format 要求返回 JSON
    response = client.chat.completions.create(
        model=OPENAI_VISION_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64_image}",
                        },
                    },
                ],
            }
        ],
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content

    # 解析 JSON
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"模型返回的内容不是合法 JSON：{e}\n原始内容：{content}") from e

    if not isinstance(data, dict):
        raise ValueError(f"模型返回的 JSON 顶层必须是对象(dict)，当前类型为：{type(data)}，内容：{data}")

    # 提取 per_hand_amount
    def to_int(x):
        try:
            if isinstance(x, str):
                x = x.replace(",", "").strip()
            return int(x)
        except Exception:
            return None

    per_hand_amount = to_int(data.get("per_hand_amount"))

    # 提取 rows
    rows_raw = data.get("rows", [])
    if not isinstance(rows_raw, list):
        raise ValueError(f"JSON 中 rows 字段不是数组：{rows_raw}")

    cleaned_rows: List[Dict[str, Any]] = []
    for r in rows_raw:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name", "")).strip()
        if not name:
            continue

        hands = to_int(r.get("hands"))
        amount = to_int(r.get("amount"))
        profit = to_int(r.get("profit"))

        cleaned_rows.append(
            {
                "name": name,
                "hands": hands,
                "amount": amount,
                "profit": profit,
            }
        )

    return {
        "per_hand_amount": per_hand_amount,
        "rows": cleaned_rows,
    }


def validate_profit_balance(rows: List[Dict[str, Any]], tolerance: int = 0) -> Dict[str, Any]:
    """校验所有玩家盈亏是否平衡。

    参数:
        rows: 识别出的每行记录列表，每条记录应包含 "profit" 字段。
        tolerance: 容差，允许总盈亏在 [-tolerance, tolerance] 内视为平衡，默认 0。

    返回:
        一个包含校验信息的字典，例如：
        {
            "total_profit": -10,
            "num_rows": 9,
            "num_valid_profit_rows": 9,
            "is_balanced": False,
            "tolerance": 0
        }
    """
    total_profit = 0
    num_valid = 0

    for r in rows:
        if not isinstance(r, dict):
            continue
        p = r.get("profit")
        if isinstance(p, int):
            total_profit += p
            num_valid += 1

    is_balanced = abs(total_profit) <= tolerance

    return {
        "total_profit": total_profit,
        "num_rows": len(rows),
        "num_valid_profit_rows": num_valid,
        "is_balanced": is_balanced,
        "tolerance": tolerance,
    }


def result_to_json(result: Dict[str, Any], output_path: str) -> None:
    """将识别出的结果写入 JSON 文件，包含 per_hand_amount 和 rows。"""
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 为了方便阅读，这里使用缩进格式写入
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"已将识别结果写入 JSON：{out_path}")
    # 简单打印一份 DataFrame 预览 rows，方便在终端查看
    rows = result.get("rows") or []
    if rows:
        df = pd.DataFrame(rows, columns=["name", "hands", "amount", "profit"])
        print(df)
    else:
        print("rows 为空，没有可预览的行数据。")


def process_image_with_gpt(image_path: str, output_path: str) -> None:
    """主流程：图片 -> GPT Vision 识别 -> JSON，并附加盈亏平衡校验结果。"""
    print(f"使用 GPT Vision 识别图片：{image_path}")
    result = call_gpt_vision_on_image(image_path)
    rows = result.get("rows") or []

    # 计算盈亏是否平衡
    validation = validate_profit_balance(rows, tolerance=0)
    result["validation"] = validation

    print(
        f"模型返回 {len(rows)} 行记录，一手筹码量 per_hand_amount = {result.get('per_hand_amount')}，"
        f"总盈亏 = {validation['total_profit']}，是否平衡 = {validation['is_balanced']}"
    )

    result_to_json(result, output_path)


def main():
    parser = argparse.ArgumentParser(description="使用 OpenAI GPT-4 视觉模型识别德扑截图并输出 CSV")
    parser.add_argument("--image", type=str, required=True, help="输入截图路径，例如: ../images/dp_1.png")
    parser.add_argument("--output", type=str, required=True, help="输出 JSON 路径，例如: ../data/session_from_dp1_gpt.json")

    args = parser.parse_args()

    process_image_with_gpt(args.image, args.output)


if __name__ == "__main__":
    main()