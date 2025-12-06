import argparse
from pathlib import Path
from typing import List, Dict

import cv2
import numpy as np
import pandas as pd
from paddleocr import PaddleOCR


def run_ocr(image_path: str):
    """
    调用 PaddleOCR 对整张截图做文字识别
    返回结构：一张图片的所有文本行列表，每一项形如：
        [box, (text, score)]
    为了适配微信聊天保存的小图，这里先进行一次放大预处理。
    同时兼容 PaddleOCR v2/v3 不同返回格式（list 或 pipeline 对象 / dict）。
    """
    # 读入原图
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"无法读取图片：{image_path}")
    h, w = img.shape[:2]

    # 如果图片较小（微信截图经常被压缩），放大到最长边约 1600 像素，提高 OCR 识别率
    max_side = max(h, w)
    scale = 1.0
    target_max_side = 1600
    if max_side < target_max_side:
        scale = target_max_side / max_side
        new_w = int(w * scale)
        new_h = int(h * scale)
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        h, w = new_h, new_w
        print(f"已放大图片至: {w}x{h}, scale={scale:.2f}")

    # 使用中文模型（支持中英混合），保留中文识别能力
    ocr = PaddleOCR(use_angle_cls=False, lang="ch")

    # 新版 PaddleOCR 推荐直接传 numpy 数组
    result = ocr.ocr(img)

    if result is None:
        return []

    # 1）如果是 v2 老格式：result 是 list，形如 [ [ [box, (text, score)], ... ] ]
    if isinstance(result, list):
        # 常见情况：单页结果在 result[0]
        if len(result) > 0 and isinstance(result[0], list):
            return result[0]
        # 兜底：如果已经是 line 列表就直接返回
        return result

    # 2）如果是 v3 pipeline / OCRResult：尝试按属性或 dict 取字段
    lines = []
    try:
        # pipeline 对象可能有 to_dict 方法
        if hasattr(result, "to_dict"):
            result_dict = result.to_dict()
        elif isinstance(result, dict):
            result_dict = result
        else:
            # 不认识的类型，直接打印一下类型然后返回空
            print(f"OCR 返回未知类型：{type(result)}，内容：{result}")
            return []

        # 优先用 rec_polys + rec_texts + rec_scores
        boxes = result_dict.get("rec_polys") or result_dict.get("dt_polys")
        texts = result_dict.get("rec_texts")
        scores = result_dict.get("rec_scores")

        if boxes is None or texts is None:
            print("OCR 结果中缺少 rec_polys/rec_texts 字段。result_dict keys:", result_dict.keys())
            return []

        if scores is None:
            scores = [1.0] * len(texts)

        n = min(len(boxes), len(texts), len(scores))
        for i in range(n):
            box = boxes[i]
            text = texts[i]
            score = scores[i]
            lines.append([box, (text, score)])

        return lines
    except Exception as e:
        print("解析 OCR 结果为标准格式时出错：", e, "原始类型：", type(result))
        return []


def box_center(box):
    """
    PaddleOCR 的 box 是 4 个点 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
    这里返回中心点坐标 (cx, cy)
    """
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return float(sum(xs) / 4.0), float(sum(ys) / 4.0)


def is_number_like(text: str) -> bool:
    """
    判断是否是数字 / 带正负号的数字
    """
    text = text.replace(",", "").strip()
    if not text:
        return False
    # 去掉前后 + -
    if text[0] in "+-":
        text = text[1:]
    return text.isdigit()


def extract_per_hand_amount(ocr_items, img_h: int) -> int:
    """
    从上半部分区域中找“每手码量”的数字（通常在上方中间）
    策略：在图像上半部分找到第一个数字，返回它
    """
    candidates = []
    for item in ocr_items:
        # 兼容不同版本 PaddleOCR 返回格式，item 至少包含 box 和 (text, score)
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        box = item[0]
        raw = item[1]
        if isinstance(raw, (list, tuple)) and len(raw) >= 1:
            text = str(raw[0])
        else:
            text = str(raw)

        cx, cy = box_center(box)
        if cy > img_h * 0.45:  # 只看上半部
            continue
        text_clean = text.strip()
        if is_number_like(text_clean):
            candidates.append((cy, text_clean))

    if not candidates:
        return 0

    # 取最靠近顶部的数字
    candidates.sort(key=lambda x: x[0])
    val = candidates[0][1].replace(",", "").strip()
    if val and val[0] in "+-":
        val = val[1:]
    try:
        return int(val)
    except ValueError:
        return 0


def cluster_columns(ocr_items, img_h: int) -> Dict[int, List[Dict]]:
    """
    根据 x 坐标，把表格区域内的文字分成 4 列：
    昵称 / 手数 / 码量 / 盈亏

    返回：{
        0: [ {text, cx, cy, box}, ... ],  # 昵称列
        1: [...],                         # 手数列
        2: [...],                         # 码量列
        3: [...],                         # 盈亏列
    }
    """
    table_top = img_h * 0.25   # 表格上边界（比例，可以调）
    table_bottom = img_h * 0.95  # 表格下边界（适当放宽，适配微信截图）

    cells = []
    for item in ocr_items:
        # 兼容不同 PaddleOCR 结果格式
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        box = item[0]
        raw = item[1]
        if isinstance(raw, (list, tuple)) and len(raw) >= 1:
            text = str(raw[0])
        else:
            text = str(raw)

        cx, cy = box_center(box)
        if cy < table_top or cy > table_bottom:
            continue  # 过滤掉表头/底部说明文字

        text_clean = text.strip()
        if not text_clean:
            continue

        cells.append(
            {
                "text": text_clean,
                "cx": cx,
                "cy": cy,
                "box": box,
            }
        )

    if not cells:
        return {}

    # 按 x 坐标排序，然后划分为 4 份
    cells_sorted = sorted(cells, key=lambda x: x["cx"])
    xs = np.array([c["cx"] for c in cells_sorted])

    # 利用分位数作为 4 个列的边界
    # 分成 4 段： [q0, q1), [q1, q2), [q2, q3), [q3, +∞)
    q1, q2, q3 = np.quantile(xs, [0.25, 0.5, 0.75])

    columns = {0: [], 1: [], 2: [], 3: []}
    for c in cells_sorted:
        x = c["cx"]
        if x <= q1:
            col = 0
        elif x <= q2:
            col = 1
        elif x <= q3:
            col = 2
        else:
            col = 3
        columns[col].append(c)

    # 每列按 y 排序（从上到下）
    for col in columns:
        columns[col] = sorted(columns[col], key=lambda x: x["cy"])

    return columns


def drop_header_row(col_cells: List[Dict], header_texts=None) -> List[Dict]:
    """
    去掉每列顶部的“昵称 / 手数 / 码量 / 盈亏”表头
    """
    if not col_cells:
        return col_cells
    if header_texts is None:
        header_texts = {"昵称", "手数", "码量", "盈亏"}

    # 如果顶部那个单元格刚好是表头文字，则丢弃
    first = col_cells[0]
    if first["text"] in header_texts:
        return col_cells[1:]
    return col_cells


def build_rows_from_columns(columns: Dict[int, List[Dict]]):
    """
    把 4 列合并成按行的数据：
    [
      {"name": ..., "hands": ..., "amount": ..., "profit": ...},
      ...
    ]
    """
    # 依次去掉表头
    nick_col = drop_header_row(columns.get(0, []))
    hand_col = drop_header_row(columns.get(1, []))
    amount_col = drop_header_row(columns.get(2, []))
    profit_col = drop_header_row(columns.get(3, []))

    # 取最短列长度，防止某列识别少一个
    n_rows = min(len(nick_col), len(hand_col), len(amount_col), len(profit_col))

    rows = []
    for i in range(n_rows):
        name = nick_col[i]["text"].strip()
        hands_text = hand_col[i]["text"].replace(",", "").strip()
        amount_text = amount_col[i]["text"].replace(",", "").strip()
        profit_text = profit_col[i]["text"].replace(",", "").strip()

        # 处理数字
        try:
            hands = int(hands_text)
        except ValueError:
            hands = None

        try:
            amount = int(amount_text)
        except ValueError:
            amount = None

        # 盈亏可能带 + -
        sign = 1
        if profit_text.startswith("+"):
            profit_text_num = profit_text[1:]
        elif profit_text.startswith("-"):
            sign = -1
            profit_text_num = profit_text[1:]
        else:
            profit_text_num = profit_text

        try:
            profit = sign * int(profit_text_num)
        except ValueError:
            profit = None

        rows.append(
            {
                "name": name,
                "hands": hands,
                "amount": amount,
                "profit": profit,
            }
        )

    return rows


def process_image_to_csv(image_path: str, csv_path: str):
    """
    主流程：OCR + 列聚类 + 行生成 + 存 CSV
    """
    image_path = str(image_path)
    csv_path = str(csv_path)

    # 读一下图片，拿到高度用于区域过滤
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"无法读取图片：{image_path}")
    img_h, img_w = img.shape[:2]

    print(f"图片尺寸：{img_w} x {img_h}")

    # 1. OCR
    print("正在进行 OCR 识别...")
    ocr_items = run_ocr(image_path)

    print(f"识别文本条数：{len(ocr_items)}")
    # 调试：打印前若干条识别结果，便于检查 OCR 质量
    for idx, item in enumerate(ocr_items[:30]):
        try:
            box = item[0]
            raw = item[1]
            if isinstance(raw, (list, tuple)) and len(raw) >= 1:
                text = str(raw[0])
            else:
                text = str(raw)
            cx, cy = box_center(box)
            print(f"[{idx}] ({cx:.1f}, {cy:.1f}) -> {text}")
        except Exception as e:
            print(f"[{idx}] 解析识别结果时出错: {e}")

    # 2. 每手码量
    per_hand_amount = extract_per_hand_amount(ocr_items, img_h)
    print(f"识别到的每手码量：{per_hand_amount}")

    # 3. 聚类成 4 列，并变成行
    columns = cluster_columns(ocr_items, img_h)
    rows = build_rows_from_columns(columns)

    if not rows:
        print("没有解析出任何行数据，请检查截图格式或阈值设置。")
        return

    # 4. 构建 DataFrame，附加“每手码量”字段
    for r in rows:
        r["per_hand_amount"] = per_hand_amount
        # 也可以算一下总盈亏金额 = profit * 每手码量，看你之后要不要用
        if r["profit"] is not None and per_hand_amount:
            r["profit_chips"] = r["profit"]  # 当前截图盈亏已经是筹码单位
        else:
            r["profit_chips"] = None

    df = pd.DataFrame(rows, columns=[
        "name",
        "hands",
        "amount",
        "profit",
        "per_hand_amount",
        "profit_chips",
    ])

    # 5. 保存 CSV
    out_path = Path(csv_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"已保存到 CSV：{out_path}")
    print(df)


def main():
    parser = argparse.ArgumentParser(description="德扑记账截图 OCR 解析脚本")
    parser.add_argument(
        "--image",
        type=str,
        required=True,
        help="输入的截图路径，例如: ./images/dp_20250101.png",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="output/session_from_image.csv",
        help="输出的 CSV 路径",
    )
    args = parser.parse_args()

    process_image_to_csv(args.image, args.output)


if __name__ == "__main__":
    main()