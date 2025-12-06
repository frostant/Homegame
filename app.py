import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime
import json
import subprocess
import sys

st.set_page_config(
    page_title="Nova88 HomeGame 战绩记录",
    page_icon="🃏",
    layout="centered",  # 居中布局对移动端更友好
    initial_sidebar_state="collapsed",  # 移动端默认收起侧边栏，主内容更清晰
)

# === 路径配置（与项目 / OCR 脚本保持一致） ===
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
PLAYERS_CSV = DATA_DIR / "players.csv"
SESSIONS_CSV = DATA_DIR / "sessions.csv"
SESSION_PLAYERS_CSV = DATA_DIR / "session_players.csv"
ALIAS_MAP_CSV = DATA_DIR / "alias_map.csv"
ADD_DATA_DIR = DATA_DIR / "add_data"
OCR_DATA_DIR = DATA_DIR / "ocr_data"
FEEDBACK_CSV = DATA_DIR / "feedback.csv"

CODE_DIR = PROJECT_ROOT / "data" / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.append(str(CODE_DIR))
try:
    from import_ocr_sessions import import_single_json
except Exception:
    import_single_json = None

# === 基础工具函数 ===

def ensure_data_dir():
    DATA_DIR.mkdir(exist_ok=True)
    ADD_DATA_DIR.mkdir(exist_ok=True)
    OCR_DATA_DIR.mkdir(exist_ok=True)
    print(f"[INIT] 确保数据目录存在：{DATA_DIR}, {ADD_DATA_DIR}, {OCR_DATA_DIR}")


def load_or_create_csv(path: Path, columns):
    """读取 CSV，如果不存在则返回给定列的空 DataFrame。

    会保证返回的 DataFrame 至少包含 columns 中的字段，多余字段会保留。
    """
    if path.exists():
        df = pd.read_csv(path)
        for col in columns:
            if col not in df.columns:
                df[col] = None
        return df
    else:
        return pd.DataFrame(columns=columns)


def save_df(df: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def get_next_id(df: pd.DataFrame, id_col: str) -> int:
    """获取下一个自增 ID（主要用于 players 表）。"""
    if df.empty or id_col not in df.columns:
        return 1
    try:
        return int(pd.to_numeric(df[id_col], errors="coerce").max()) + 1
    except Exception:
        return 1


# === alias_map 相关 ===

def load_alias_map() -> pd.DataFrame:
    if ALIAS_MAP_CSV.exists():
        df = pd.read_csv(ALIAS_MAP_CSV)
    else:
        df = pd.DataFrame(columns=["alias", "player_id", "is_primary"])
    # 规范化一列，方便匹配
    if "alias" not in df.columns:
        df["alias"] = None
    if "player_id" not in df.columns:
        df["player_id"] = None
    if "is_primary" not in df.columns:
        df["is_primary"] = 0
    df["norm_alias"] = df["alias"].astype(str).str.strip().str.lower()
    return df


def save_alias_map(df: pd.DataFrame):
    # 存盘时去掉 norm_alias
    to_save = df.copy()
    if "norm_alias" in to_save.columns:
        to_save = to_save.drop(columns=["norm_alias"])
    save_df(to_save, ALIAS_MAP_CSV)


def normalize_alias(name: str) -> str:
    return (name or "").strip().lower()


# === Git 提交工具 ===

def git_commit_changes(files, message: str):
    """使用 git 提交数据更新，方便回滚。

    如果不是 git 仓库 / git 未配置，将给出 warning，但不影响主流程。
    """
    try:
        file_paths = [Path(f) for f in files]
        existing_files = [str(f) for f in file_paths if f.exists()]
        if not existing_files:
            return
        subprocess.run(["git", "add", *existing_files], check=True, cwd=str(PROJECT_ROOT))
        subprocess.run(["git", "commit", "-m", message], check=True, cwd=str(PROJECT_ROOT))
        print(f"[GIT] 已提交变更：{message}")
        st.success("已通过 git 提交本次数据更新。")
    except Exception as e:
        print(f"[GIT] 提交失败：{e}")
        st.warning(f"Git 提交失败（可以手动处理）：{e}")


# === OCR 调用工具（调用 data/code/chatgpt_ocr.py） ===

def run_chatgpt_ocr(image_path: Path, json_path: Path) -> bool:
    script = PROJECT_ROOT / "data" / "code" / "chatgpt_ocr.py"
    if not script.exists():
        msg = "找不到 OCR 脚本：data/code/chatgpt_ocr.py，请检查文件是否存在。"
        print(f"[OCR] {msg}")
        st.error(msg)
        return False

    cmd = [
        "python3",
        str(script),
        "--image",
        str(image_path),
        "--output",
        str(json_path),
    ]

    print(f"[OCR] 即将执行命令：{' '.join(cmd)}")
    st.info(f"正在调用 GPT OCR 识别牌局：{' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
    except Exception as e:
        print(f"[OCR] 执行 OCR 脚本异常：{e}")
        st.error(f"执行 OCR 脚本时出错：{e}")
        return False

    if result.stdout:
        st.text_area("OCR 脚本输出 (stdout)", result.stdout, height=150)
    if result.stderr:
        st.text_area("OCR 脚本错误 (stderr)", result.stderr, height=150)

    if result.returncode != 0:
        print(f"[OCR] 脚本返回非 0 状态码：{result.returncode}")
        st.error(f"OCR 脚本返回非 0 状态码：{result.returncode}")
        return False

    if not json_path.exists():
        msg = "OCR 脚本执行成功但未生成 JSON 文件，请检查脚本逻辑。"
        print(f"[OCR] {msg}")
        st.error(msg)
        return False

    print(f"[OCR] 完成识别并生成 JSON：{json_path}")
    st.success("GPT OCR 识别完成，并已生成 JSON 结果。")
    return True


# === 解析 OCR JSON & 导入到 DataFrame ===
# （JSON → CSV 的逻辑已经移到 import_ocr_sessions.py 里，这里不再重复实现）


def parse_session_date_from_stem(stem: str) -> str:
    """从文件名 stem 中解析日期，例如 20250802 或 20250920_2 → 2025-08-02。"""
    s = stem
    if "_" in s:
        s = s.split("_", 1)[0]
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return ""


# === 数据加载：统一到 OCR 导入后的 schema ===

def load_all_data():
    """加载 players / sessions / session_players 三张表。"""
    ensure_data_dir()

    players = load_or_create_csv(
        PLAYERS_CSV, ["player_id", "name", "note"]
    )

    sessions = load_or_create_csv(
        SESSIONS_CSV,
        [
            "session_id",
            "session_date",
            "session_name",
            "per_hand_amount",
            "total_profit",
            "is_balanced",
            "raw_file",
        ],
    )

    session_players = load_or_create_csv(
        SESSION_PLAYERS_CSV,
        [
            "session_id",
            "player_id",
            "alias",
            "name",
            "hands",
            "amount",
            "profit",
        ],
    )

    # 类型规范化：玩家 ID
    if not players.empty:
        players["player_id"] = pd.to_numeric(
            players["player_id"], errors="coerce"
        ).astype("Int64")

    # sessions：ID & 数值列
    if not sessions.empty:
        sessions["session_id"] = pd.to_numeric(
            sessions["session_id"], errors="coerce"
        ).astype("Int64")
        sessions["per_hand_amount"] = pd.to_numeric(
            sessions["per_hand_amount"], errors="coerce"
        )
        sessions["total_profit"] = pd.to_numeric(
            sessions["total_profit"], errors="coerce"
        )

    # session_players：ID & 数值列
    if not session_players.empty:
        session_players["session_id"] = pd.to_numeric(
            session_players["session_id"], errors="coerce"
        ).astype("Int64")
        session_players["player_id"] = pd.to_numeric(
            session_players["player_id"], errors="coerce"
        ).astype("Int64")
        for col in ["hands", "amount", "profit"]:
            session_players[col] = pd.to_numeric(
                session_players[col], errors="coerce"
            )

    print(
        f"[LOAD] players={len(players)}, "
        f"sessions={len(sessions)}, "
        f"session_players={len(session_players)}"
    )
    return players, sessions, session_players


def save_all_data(players, sessions, session_players):
    save_df(players, PLAYERS_CSV)
    save_df(sessions, SESSIONS_CSV)
    save_df(session_players, SESSION_PLAYERS_CSV)


# === 反馈保存函数 ===
def save_feedback(rating: int, comment: str, contact: str):
    """把用户反馈追加写入 data/feedback.csv"""
    DATA_DIR.mkdir(exist_ok=True)
    df_row = pd.DataFrame(
        [{
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rating": rating,
            "comment": comment,
            "contact": contact,
        }]
    )
    if FEEDBACK_CSV.exists():
        try:
            old = pd.read_csv(FEEDBACK_CSV)
            df = pd.concat([old, df_row], ignore_index=True)
        except Exception:
            df = df_row
    else:
        df = df_row
    df.to_csv(FEEDBACK_CSV, index=False)
    print(f"[FEEDBACK] 收到一条反馈：rating={rating}, comment_len={len(comment)}")


# === 玩家汇总统计函数 ===
def compute_player_summary(players: pd.DataFrame, sessions: pd.DataFrame, session_players: pd.DataFrame) -> pd.DataFrame:
    """按玩家聚合战绩数据，用于排行榜和玩家详情页。

    返回字段示例：
    - player_id
    - name
    - num_sessions: 参与场次
    - total_profit: 总盈亏
    - total_hands: 总手数
    - total_amount: 总买入 / 总码量
    - win_sessions / lose_sessions / breakeven_sessions
    - win_rate: 胜率
    - avg_profit: 场均盈亏
    - max_win / max_loss: 单场最大赢/输
    """
    if session_players is None or session_players.empty:
        return pd.DataFrame(
            columns=[
                "player_id",
                "name",
                "num_sessions",
                "total_profit",
                "total_hands",
                "total_amount",
                "win_sessions",
                "lose_sessions",
                "breakeven_sessions",
                "win_rate",
                "avg_profit",
                "max_win",
                "max_loss",
            ]
        )

    # 按玩家-场次聚合单场盈亏
    sp_valid = session_players.dropna(subset=["player_id", "session_id"]).copy()
    per_session = (
        sp_valid.groupby(["player_id", "session_id"], as_index=False)["profit"]
        .sum()
    )

    session_stats = per_session.groupby("player_id").agg(
        num_sessions=("session_id", "nunique"),
        total_profit=("profit", "sum"),
        max_win=("profit", "max"),
        max_loss=("profit", "min"),
        win_sessions=("profit", lambda x: (x > 0).sum()),
        lose_sessions=("profit", lambda x: (x < 0).sum()),
        breakeven_sessions=("profit", lambda x: (x == 0).sum()),
    )

    # 按玩家聚合手数和买入码量
    ha_stats = (
        session_players.dropna(subset=["player_id"]).groupby("player_id").agg(
            total_hands=("hands", "sum"),
            total_amount=("amount", "sum"),
        )
    )

    summary = session_stats.join(ha_stats, how="left")
    summary.reset_index(inplace=True)  # player_id 变成列

    # 关联玩家正式昵称
    if players is not None and not players.empty and "player_id" in players.columns:
        player_names = players[["player_id", "name"]].copy()
        summary = summary.merge(player_names, on="player_id", how="left")
    else:
        summary["name"] = None

    # 计算胜率和场均盈亏
    summary["win_rate"] = (summary["win_sessions"] / summary["num_sessions"].replace(0, pd.NA)).round(2)
    summary["avg_profit"] = (summary["total_profit"] / summary["num_sessions"].replace(0, pd.NA)).round(0)

    return summary


# === 初始化 session_state ===

if "players" not in st.session_state:
    p, s, sp = load_all_data()
    st.session_state.players = p
    st.session_state.sessions = s
    st.session_state.session_players = sp
    print("[INIT] 首次加载数据并写入 session_state 完成")


# === 页面：排行榜 ===

def page_overview():
    st.title("🏆 排行榜")

    players = st.session_state.players
    sessions = st.session_state.sessions
    session_players = st.session_state.session_players

    # 汇总玩家维度战绩
    summary = compute_player_summary(players, sessions, session_players)

    # 顶部整体指标
    total_players = len(summary) if not summary.empty else len(players)
    total_sessions = len(sessions)
    total_profit_all = (
        summary["total_profit"].sum() if not summary.empty else (
            session_players["profit"].sum() if not session_players.empty else 0
        )
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("玩家数量", total_players)
    col2.metric("牌局场次", total_sessions)
    col3.metric("所有玩家盈亏总和", f"{total_profit_all:.0f}")

    st.markdown("---")

    if summary.empty:
        st.info("暂无战绩数据，请先通过脚本或上传页面导入 OCR 结果。")
        return

    # 为了展示更合理的排行榜，可以对部分榜单加上最小场次门槛
    summary_for_rate = summary.copy()
    summary_for_rate = summary_for_rate[summary_for_rate["num_sessions"] >= 3]

    # 盈利 Top10
    st.subheader("💰 盈利 Top 10（按总盈亏排序）")
    top_profit = summary.sort_values("total_profit", ascending=False).head(10)
    st.data_table(
        top_profit[["name", "num_sessions", "total_profit", "avg_profit"]],
        use_container_width=True,
    )

    # 亏损 Top10
    st.subheader("📉 亏损 Top 10（按总盈亏从低到高排序）")
    top_loss = summary.sort_values("total_profit", ascending=True).head(10)
    st.data_table(
        top_loss[["name", "num_sessions", "total_profit", "avg_profit"]],
        use_container_width=True,
    )

    # 参与次数 Top10
    st.subheader("🧑‍🤝‍🧑 参与次数 Top 10（按场次）")
    top_sessions = summary.sort_values("num_sessions", ascending=False).head(10)
    st.data_table(
        top_sessions[["name", "num_sessions", "total_profit", "avg_profit"]],
        use_container_width=True,
    )

    # 买入手数 Top10（这里用 total_hands 代表总手数）
    st.subheader("🃏 买入手数 Top 10（按总手数）")
    top_hands = summary.sort_values("total_hands", ascending=False).head(10)
    st.data_table(
        top_hands[["name", "num_sessions", "total_hands", "total_amount", "total_profit"]],
        use_container_width=True,
    )

    # 场次胜率 Top10（要求至少 3 场，避免极端值）
    st.subheader("✅ 场次胜率 Top 10（至少 3 场）")
    if summary_for_rate.empty:
        st.info("目前没有场次达到 3 场以上的玩家，无法计算胜率榜。")
    else:
        top_winrate = summary_for_rate.sort_values("win_rate", ascending=False).head(10)
        # 将胜率显示为百分比
        df_wr = top_winrate[["name", "num_sessions", "win_sessions", "win_rate", "avg_profit"]].copy()
        df_wr["win_rate"] = (df_wr["win_rate"] * 100).round(1)
        st.data_table(df_wr, use_container_width=True)

    # 场均盈利 Top10（同样要求至少 3 场）
    st.subheader("📈 场均盈利 Top 10（至少 3 场）")
    if summary_for_rate.empty:
        st.info("目前没有场次达到 3 场以上的玩家，无法计算场均盈利榜。")
    else:
        top_avg = summary_for_rate.sort_values("avg_profit", ascending=False).head(10)
        st.data_table(
            top_avg[["name", "num_sessions", "avg_profit", "total_profit"]],
            use_container_width=True,
        )

    st.markdown("---")
    st.subheader("📋 最近 10 场牌局概览")

    if not sessions.empty:
        sessions_display = sessions.copy()
        if "session_date" in sessions_display.columns:
            sessions_display["session_date_parsed"] = pd.to_datetime(
                sessions_display["session_date"], errors="coerce"
            )
            sessions_display = sessions_display.sort_values(
                ["session_date_parsed", "session_id"], ascending=[False, False]
            )
        else:
            sessions_display = sessions_display.sort_values(
                "session_id", ascending=False
            )

        show_cols = [
            "session_id",
            "session_date",
            "session_name",
            "per_hand_amount",
            "total_profit",
            "is_balanced",
            "raw_file",
        ]
        show_cols = [c for c in show_cols if c in sessions_display.columns]

        st.data_table(
            sessions_display[show_cols].head(10),
            use_container_width=True,
        )
    else:
        st.write("暂无牌局。")


# === 页面：玩家管理 ===

def page_players():
    st.title("👥 玩家管理")

    players = st.session_state.players

    with st.form("add_player_form"):
        st.subheader("新增玩家")
        name = st.text_input("玩家昵称 *")
        note = st.text_input("备注（可选，例如微信名、打法风格）")
        submitted = st.form_submit_button("新增玩家")

        if submitted:
            if not name.strip():
                st.error("玩家昵称不能为空。")
            elif name in players.get("name", pd.Series([], dtype=str)).values:
                st.warning("已有同名玩家。")
            else:
                new_players = players.copy()
                new_id = get_next_id(new_players, "player_id")
                new_players.loc[len(new_players)] = {
                    "player_id": new_id,
                    "name": name.strip(),
                    "note": note.strip(),
                }
                st.session_state.players = new_players
                save_all_data(
                    st.session_state.players,
                    st.session_state.sessions,
                    st.session_state.session_players,
                )
                print(f"[PLAYERS] 新增玩家：id={new_id}, name={name.strip()}")
                st.success(f"已新增玩家：{name}")

    st.markdown("---")

    st.subheader("玩家列表")
    if players.empty:
        st.info("暂无玩家，请先新增。")
    else:
        st.data_table(players, use_container_width=True)


# === 页面：牌局列表 & 详情（基于 OCR 导入） ===

def page_sessions():
    st.title("🎲 牌局列表与详情")

    players = st.session_state.players
    sessions = st.session_state.sessions
    session_players = st.session_state.session_players

    if sessions.empty:
        st.info("暂无牌局数据，请先通过脚本或上传页面导入。")
        return

    st.subheader("牌局列表")

    sessions_display = sessions.copy()
    if "session_date" in sessions_display.columns:
        sessions_display["session_date_parsed"] = pd.to_datetime(
            sessions_display["session_date"], errors="coerce"
        )
        sessions_display = sessions_display.sort_values(
            ["session_date_parsed", "session_id"], ascending=[False, False]
        )

    show_cols = [
        "session_id",
        "session_date",
        "session_name",
        "per_hand_amount",
        "total_profit",
        "is_balanced",
        "raw_file",
    ]
    show_cols = [c for c in show_cols if c in sessions_display.columns]

    st.data_table(sessions_display[show_cols], use_container_width=True)

    st.markdown("---")

    st.subheader("查看某一场牌局详情")

    label_map = {}
    for _, row in sessions_display.iterrows():
        sid = int(row["session_id"]) if pd.notna(row["session_id"]) else -1
        date_str = str(row.get("session_date", ""))
        name_str = str(row.get("session_name", ""))
        label = f"#{sid}  |  {date_str}  |  {name_str}"
        label_map[label] = sid

    if not label_map:
        st.info("当前没有可选牌局。")
        return

    selected_label = st.selectbox("选择牌局", options=list(label_map.keys()))
    selected_session_id = label_map[selected_label]

    session_row = sessions_display[sessions_display["session_id"] == selected_session_id]
    if not session_row.empty:
        sr = session_row.iloc[0]
        per_hand = sr.get("per_hand_amount", None)
        total_profit = sr.get("total_profit", None)
        is_balanced = sr.get("is_balanced", None)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Session ID", selected_session_id)
        col2.metric("一手筹码量", f"{per_hand:.0f}" if pd.notna(per_hand) else "未知")
        col3.metric(
            "牌局总盈亏",
            f"{total_profit:.0f}" if pd.notna(total_profit) else "未知",
        )
        col4.metric("盈亏是否平衡", str(is_balanced))

    st.markdown("---")

    st.subheader("该场牌局的玩家明细")
    sp_current = session_players[
        session_players["session_id"] == selected_session_id
    ].copy()

    if sp_current.empty:
        st.write("该场牌局暂无玩家记录。")
    else:
        if not players.empty and "player_id" in sp_current.columns:
            sp_current = sp_current.merge(
                players[["player_id", "name"]].rename(
                    columns={"name": "player_name_official"}
                ),
                on="player_id",
                how="left",
            )

        sum_profit = sp_current["profit"].sum()
        st.caption(f"该场牌局玩家盈亏合计：{sum_profit:.0f}")

        show_cols = [
            "alias",
            "name",
            "player_name_official",
            "hands",
            "amount",
            "profit",
        ]
        show_cols = [c for c in show_cols if c in sp_current.columns]

        st.data_table(sp_current[show_cols], use_container_width=True)


# === 页面：上传牌局（完整链路：上传→OCR→别名映射→导入→git 提交） ===

def page_add_session_upload():
    st.title("➕ 添加新牌局（请联系 腾哥 上传）")

    players = st.session_state.players

    st.markdown("### 第一步：上传牌局截图")
    uploaded_file = st.file_uploader("上传一张牌局截图", type=["jpg", "jpeg", "png"])

    if uploaded_file is not None:
        st.image(uploaded_file, caption="预览", use_column_width=True)

        if st.button("保存并识别这场牌局", type="primary"):
            # 生成保存文件名：以当天日期为前缀，如果重复则加 _2, _3
            today_str = datetime.now().strftime("%Y%m%d")
            ext = Path(uploaded_file.name).suffix or ".jpg"
            base_name = today_str
            idx = 0
            while True:
                suffix = "" if idx == 0 else f"_{idx+1}"
                file_name = f"{base_name}{suffix}{ext}"
                img_path = ADD_DATA_DIR / file_name
                if not img_path.exists():
                    break
                idx += 1

            # 保存图片
            with img_path.open("wb") as f:
                f.write(uploaded_file.getbuffer())

            print(f"[UPLOAD] 已保存上传图片到 {img_path}")
            st.success(f"已保存图片到 {img_path}")

            # 对应的 OCR JSON 路径
            json_path = OCR_DATA_DIR / f"{img_path.stem}.json"

            ok = run_chatgpt_ocr(img_path, json_path)
            if ok:
                st.session_state["new_session_image_path"] = str(img_path)
                st.session_state["new_session_json_path"] = str(json_path)
                print(f"[UPLOAD] OCR 成功，准备进入别名校验阶段：image={img_path}, json={json_path}")

    # 如果已经有本次上传/识别的 JSON，则展示后续步骤
    json_path_str = st.session_state.get("new_session_json_path")
    if not json_path_str:
        return

    json_path = Path(json_path_str)
    if not json_path.exists():
        st.warning("OCR JSON 文件不存在，请重新上传并识别。")
        return

    st.markdown("---")
    st.markdown("### 第二步：检查 OCR 结果与昵称映射")

    try:
        with json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        st.error(f"读取 OCR JSON 失败：{e}")
        return

    # 先拿到原始 rows / 校验信息
    rows = data.get("rows") or []

    st.markdown("#### 可编辑的 OCR 结果（name / hands / amount / profit）")
    if not rows:
        st.info("当前 JSON 中 rows 为空，无法编辑。")
    else:
        import pandas as _pd  # 防止与顶部 import 冲突，这里局部再导一次
        df_rows = _pd.DataFrame(rows)
        # 确保关键列存在
        for col_name in ["name", "hands", "amount", "profit"]:
            if col_name not in df_rows.columns:
                df_rows[col_name] = None

        edited_df = st.data_editor(
            df_rows[["name", "hands", "amount", "profit"]],
            num_rows="dynamic",
            key="ocr_rows_editor",
            use_container_width=True,
        )

        # 将编辑结果写回 JSON 结构
        edited_rows = edited_df.to_dict(orient="records")
        data["rows"] = edited_rows

        # 根据用户编辑后的 profit 重新计算校验字段
        total_profit_val = 0.0
        for r in edited_rows:
            try:
                v = r.get("profit", 0)
                if v is None:
                    v = 0
                total_profit_val += float(v)
            except Exception:
                # 非数字直接按 0 处理，让用户自己注意
                pass

        if "validation" not in data or not isinstance(data["validation"], dict):
            data["validation"] = {}
        data["validation"]["total_profit"] = total_profit_val
        data["validation"]["is_balanced"] = abs(total_profit_val) < 1e-6

        # 回写 JSON 文件，保证后续导入/脚本看到的是最新人工修正结果
        try:
            with json_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"[OCR-EDIT] 已将手动修改后的 rows & validation 写回 JSON：{json_path}")
        except Exception as e:
            print(f"[OCR-EDIT] 写回 JSON 失败：{e}")
            st.warning(f"写回 JSON 失败：{e}")

        # 用最新数据继续下面的统计和别名逻辑
        rows = edited_rows

    per_hand_amount = data.get("per_hand_amount")
    validation = data.get("validation") or {}
    total_profit = validation.get("total_profit")
    is_balanced = validation.get("is_balanced")

    col1, col2, col3 = st.columns(3)
    col1.metric("识别出玩家人数", len(rows))
    col2.metric("一手筹码量", per_hand_amount if per_hand_amount is not None else "未知")
    col3.metric("盈亏是否平衡", str(is_balanced))

    # 收集所有昵称（基于最新编辑后的 rows）
    all_names = []
    for r in rows:
        if isinstance(r, dict):
            name = str(r.get("name", "")).strip()
            if name:
                all_names.append(name)

    st.write("识别到的昵称：", ", ".join(sorted(set(all_names))))

    alias_df = load_alias_map()
    known_norm_aliases = set(alias_df["norm_alias"].dropna().tolist())
    unknown_aliases = sorted({n for n in all_names if normalize_alias(n) not in known_norm_aliases})

    if unknown_aliases:
        st.warning("存在尚未在 alias_map 中登记的昵称，请完成归属后再导入：")
        st.write(", ".join(unknown_aliases))

        st.markdown("#### 为新昵称选择归属玩家")
        player_names = players["name"].dropna().astype(str).tolist() if not players.empty else []

        assignments = []
        with st.form("alias_resolve_form"):
            for idx, alias in enumerate(unknown_aliases):
                st.markdown(f"**昵称：{alias}**")
                col1, col2 = st.columns(2)
                with col1:
                    options = ["<新建玩家>"] + player_names
                    choice = st.selectbox(
                        "归属玩家",
                        options=options,
                        key=f"alias_choice_{idx}",
                    )
                with col2:
                    new_name = st.text_input(
                        "若新建玩家，输入正式昵称",
                        value=alias,
                        key=f"alias_newname_{idx}",
                    )
                assignments.append((alias, choice, new_name))
                st.markdown("---")

            submitted = st.form_submit_button("保存这些昵称的归属关系")

        if submitted:
            players_updated = players.copy()
            alias_df_updated = alias_df.copy()

            for alias, choice, new_name in assignments:
                norm = normalize_alias(alias)
                if choice == "<新建玩家>":
                    # 新建玩家
                    new_players = players_updated
                    new_id = get_next_id(new_players, "player_id")
                    new_players.loc[len(new_players)] = {
                        "player_id": new_id,
                        "name": new_name.strip() or alias,
                        "note": "",
                    }
                    players_updated = new_players
                    pid = new_id
                else:
                    # 归属到已有玩家
                    matched = players_updated[players_updated["name"] == choice]
                    if matched.empty:
                        continue
                    pid = int(matched.iloc[0]["player_id"])

                # 写入 alias_map
                alias_df_updated.loc[len(alias_df_updated)] = {
                    "alias": alias,
                    "player_id": pid,
                    "is_primary": 0,
                    "norm_alias": norm,
                }

            # 保存并更新全局状态
            save_alias_map(alias_df_updated)
            save_df(players_updated, PLAYERS_CSV)
            print(
                f"[ALIAS] 更新完成：新增/修改别名数量={len(assignments)}, "
                f"players 总数={len(players_updated)}, alias_map 总数={len(alias_df_updated)}"
            )
            st.session_state.players = players_updated

            st.success("已更新 别名和玩家数据，请再次检查是否还有未登记昵称。")

            # 使用 git 提交这一步的修改
            git_commit_changes(
                [ALIAS_MAP_CSV, PLAYERS_CSV],
                f"Update alias_map & players for {json_path.name}",
            )

            # 重新计算 unknown_aliases（下次 rerun 会自动刷新）
            return

    else:
        st.success("所有昵称均已在 alias_map 中登记，可以导入这场牌局。")

    st.markdown("---")
    st.markdown("### 第三步：导入牌局并写入数据库")

    if st.button("➡️ 导入这场牌局到数据库", type="primary"):
        if import_single_json is None:
            st.error("找不到后端导入函数 import_ocr_sessions.import_single_json，请检查 data/code/import_ocr_sessions.py 是否存在且可导入。")
        else:
            try:
                # 调用后端脚本中的增量导入函数：从 JSON 写入 sessions.csv / session_players.csv
                print(f"[IMPORT] 开始导入牌局 JSON：{json_path}")
                import_single_json(json_path)
                print(f"[IMPORT] import_single_json 完成：{json_path}")

                # 导入完成后，重新加载最新数据刷新页面
                p, s, sp = load_all_data()
                st.session_state.players = p
                st.session_state.sessions = s
                st.session_state.session_players = sp
                print(
                    f"[IMPORT] 重新加载数据完成：players={len(p)}, "
                    f"sessions={len(s)}, session_players={len(sp)}"
                )

                # 使用 git 提交本次数据库变更
                img_path_str = st.session_state.get("new_session_image_path")
                files_to_commit = [
                    SESSIONS_CSV,
                    SESSION_PLAYERS_CSV,
                    json_path,
                ]
                if img_path_str:
                    files_to_commit.append(Path(img_path_str))

                git_commit_changes(
                    files_to_commit,
                    f"Add poker session from {json_path.name}",
                )

                print(f"[IMPORT] 本次牌局导入 + git 提交流程完成：{json_path}")
                st.success("导入成功，已将该牌局写入数据库并刷新战绩数据。")
            except Exception as e:
                print(f"[IMPORT] 导入失败：{e}")
                st.error(f"导入失败：{e}")


# === 页面：玩家战绩查询 ===

def page_player_stats():
    st.title("📊 玩家战绩查询")

    players = st.session_state.players
    sessions = st.session_state.sessions
    session_players = st.session_state.session_players

    summary = compute_player_summary(players, sessions, session_players)

    if players.empty or session_players.empty:
        st.info("暂无战绩数据或玩家信息。")
        return

    player_name = st.selectbox(
        "选择玩家",
        options=players["name"].tolist(),
    )

    player_row = players[players["name"] == player_name].iloc[0]
    player_id = int(player_row["player_id"])

    sp = session_players[session_players["player_id"] == player_id].copy()
    if sp.empty:
        st.info("该玩家暂无 OCR 导入战绩。")
        return

    merged = sp.merge(sessions, on="session_id", how="left")

    # 优先使用汇总好的 summary 统计
    summary_row = summary[summary["player_id"] == player_id]
    if not summary_row.empty:
        r = summary_row.iloc[0]
        num_sessions = int(r["num_sessions"])
        total_hands = r.get("total_hands", sp["hands"].sum())
        total_profit = r.get("total_profit", sp["profit"].sum())
        avg_per_session = r.get("avg_profit", 0.0)
        max_win = r.get("max_win", sp["profit"].max())
        max_loss = r.get("max_loss", sp["profit"].min())
        win_rate = r.get("win_rate", 0.0)
    else:
        # 兜底逻辑：直接按当前玩家记录重新算一次
        total_profit = sp["profit"].sum()
        max_win = sp["profit"].max()
        max_loss = sp["profit"].min()
        num_sessions = sp["session_id"].nunique()
        total_hands = sp["hands"].sum()
        avg_per_session = total_profit / num_sessions if num_sessions > 0 else 0
        per_session = (
            sp.groupby("session_id")["profit"].sum().reset_index()
        )
        win_rate = (
            (per_session["profit"] > 0).sum() / len(per_session)
            if len(per_session) > 0
            else 0.0
        )

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("总场次", int(num_sessions))
    col2.metric("总手数", f"{total_hands:.0f}")
    col3.metric("总盈亏", f"{total_profit:.0f}")
    col4.metric("场均盈亏", f"{avg_per_session:.1f}")
    col5.metric("胜率", f"{(win_rate * 100):.1f}%")

    col6, col7 = st.columns(2)
    col6.metric("最大单场赢", f"{max_win:.0f}")
    col7.metric("最大单场输", f"{max_loss:.0f}")

    st.markdown("---")

    st.subheader("每场战绩列表")
    merged_sorted = merged.copy()
    if "session_date" in merged_sorted.columns:
        merged_sorted["session_date_parsed"] = pd.to_datetime(
            merged_sorted["session_date"], errors="coerce"
        )
        merged_sorted = merged_sorted.sort_values("session_date_parsed")

    show_cols = [
        "session_date",
        "session_name",
        "per_hand_amount",
        "hands",
        "amount",
        "profit",
    ]
    show_cols = [c for c in show_cols if c in merged_sorted.columns]

    st.data_table(merged_sorted[show_cols], use_container_width=True)

    st.subheader("累计盈亏曲线")
    if not merged_sorted.empty and "session_date" in merged_sorted.columns:
        merged_sorted["session_date_parsed"] = pd.to_datetime(
            merged_sorted["session_date"], errors="coerce"
        )
        merged_sorted = merged_sorted.sort_values("session_date_parsed")
        merged_sorted["cum_profit"] = merged_sorted["profit"].cumsum()
        chart_data = merged_sorted[["session_date_parsed", "cum_profit"]].set_index(
            "session_date_parsed"
        )
        st.line_chart(chart_data)


def render_sidebar_feedback():
    """侧边栏统一的反馈 + 打赏区域（所有页面共用）。"""
    st.sidebar.markdown("---")
    with st.sidebar.expander("💌 意见反馈 & ☕ 打赏", expanded=False):
        st.markdown("**觉得 HomeGame 战绩工具好用 / 有改进建议，可以在这里告诉我 👇**")

        rating = st.slider(
            "整体满意度（1-5）",
            min_value=1,
            max_value=5,
            value=5,
            key="feedback_rating",
        )
        comment = st.text_area(
            "想说的话 / 功能建议",
            key="feedback_comment",
            height=80,
        )
        contact = st.text_input(
            "（可选）留下联系方式方便回访：微信 / email",
            key="feedback_contact",
        )

        if st.button("提交反馈", key="feedback_submit"):
            if not comment.strip():
                st.warning("至少写一句反馈，这样我才能知道要改哪里 😄")
            else:
                save_feedback(rating, comment.strip(), contact.strip())
                st.success("已收到反馈，感谢支持！")

        st.markdown("---")
        st.markdown("欢迎请我喝杯奶茶 🥤")

        # 在 data 目录下放一张微信收款码图片，例如 data/wechat_pay_qr.png
        qr_path = DATA_DIR / "wechat_pay_qr.png"
        if qr_path.exists():
            st.image(
                str(qr_path),
                caption="",
                use_container_width=True,
            )
        else:
            st.caption("（在 data/wechat_pay_qr.png 放一张微信收款码，这里会自动显示）")


# === 主入口 ===

def main():
    st.sidebar.title("导航")
    page = st.sidebar.radio(
        "",
        ("🏆 排行榜", "📊 玩家战绩查询", "👥 玩家管理", "🎲 牌局列表 / 详情", "➕ 添加牌局"),
    )

    if page == "🏆 排行榜":
        page_overview()
    elif page == "📊 玩家战绩查询":
        page_player_stats()
    elif page == "👥 玩家管理":
        page_players()
    elif page == "🎲 牌局列表 / 详情":
        page_sessions()
    elif page == "➕ 添加牌局":
        page_add_session_upload()

    # 所有页面共用的侧边栏反馈 & 打赏区域
    render_sidebar_feedback()


if __name__ == "__main__":
    main()