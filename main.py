import flet as ft
import asyncio
import js
from js import fetch
import json
import re
import random
import time
from thefuzz import fuzz
import unicodedata
from typing import Optional

# ----------------- KONFIGURACJA -----------------

BACKEND_URL = "https://game-multiplayer-qfn1.onrender.com"
GITHUB_RAW_BASE_URL = (
    "https://raw.githubusercontent.com/mechagdynia2-ai/game/main/assets/"
)

ENTRY_FEE = 500
BASE_ANSWER_TIME = 60
HINT_EXTRA_TIME = 30

# ----------------- POMOCNICZE -----------------


def make_async_click(async_callback):
    def handler(e):
        async def task():
            await async_callback(e)

        e.page.run_task(task)

    return handler


async def fetch_text(url: str) -> str:
    try:
        resp = await fetch(url)
        return await resp.text()
    except Exception as ex:
        print("[FETCH_TEXT ERROR]", ex)
        return ""


def _build_fetch_kwargs(method: str = "GET", body: Optional[dict] = None):
    m = method.upper()
    if m == "GET":
        return js.Object.fromEntries([["method", "GET"]])
    payload = json.dumps(body or {})
    headers = js.Object.fromEntries([["Content-Type", "application/json"]])
    return js.Object.fromEntries([["method", m], ["headers", headers], ["body", payload]])


async def fetch_json(url: str, method: str = "GET", body: Optional[dict] = None):
    try:
        kwargs = _build_fetch_kwargs(method, body)
        resp = await fetch(url, kwargs)
        raw = await resp.json()
        try:
            return raw.to_py()
        except Exception:
            return raw
    except Exception as ex:
        print("[FETCH_JSON ERROR]", ex, "url:", url)
        return None


def normalize_answer(text: Optional[str]) -> str:
    if not text:
        return ""
    s = str(text).lower().strip()
    trans = str.maketrans(
        {
            "ó": "o",
            "ł": "l",
            "ż": "z",
            "ź": "z",
            "ć": "c",
            "ń": "n",
            "ś": "s",
            "ą": "a",
            "ę": "e",
            "ü": "u",
        }
    )
    s = s.translate(trans)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


_parsed_questions_cache: dict[str, list[dict]] = {}


async def parse_question_file(filename: str) -> list[dict]:
    """
    Parsowanie tak, aby:
    - pytanie = tylko pierwsza linia po numerze (bez 'prawidłowa odpowiedź' i bez ABCD)
    - poprawna odpowiedź i ABCD brane z dalszej części bloku
    """
    if filename in _parsed_questions_cache:
        return _parsed_questions_cache[filename]

    url = f"{GITHUB_RAW_BASE_URL}{filename}"
    content = await fetch_text(url)
    if not content:
        return []

    parsed: list[dict] = []

    blocks = re.split(r"\n(?=\d{1,3}\.)", content)
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.splitlines()
        if not lines:
            continue

        # Pierwsza linia zawiera numer i pytanie
        first_line = lines[0].strip()
        num_match = re.match(r"^\d{1,3}\.\s*(.+)", first_line)
        if not num_match:
            continue
        question = num_match.group(1).strip()

        # Reszta bloku na poprawną odpowiedź i ABCD
        rest = "\n".join(lines[1:])

        correct_match = re.search(
            r"(praw\w*\s*odpow\w*|odpowiedzi?\.?\s*prawidłowe?|prawidłowa\s*odpowiedź)\s*[:=]\s*(.+)",
            rest,
            re.IGNORECASE,
        )
        correct = None
        if correct_match:
            correct = correct_match.group(2).strip().splitlines()[0].strip()
        if not correct:
            cm = re.search(
                r"^\s*correct\s*[:=]\s*(.+)$", rest, re.IGNORECASE | re.MULTILINE
            )
            if cm:
                correct = cm.group(1).strip()
        if not correct:
            continue

        answers = []
        ok = True
        for letter in ["A", "B", "C", "D"]:
            m = re.search(rf"\b{letter}\s*=\s*(.+?)(?:,|\n|$)", rest, re.IGNORECASE)
            if not m:
                ok = False
                break
            answers.append(m.group(1).strip())

        if not ok or len(answers) != 4:
            continue

        parsed.append(
            {
                "question": question,
                "correct": correct,
                "answers": answers,
            }
        )

    _parsed_questions_cache[filename] = parsed
    return parsed


# ----------------- GŁÓWNE UI / LOGIKA -----------------


async def main(page: ft.Page):
    page.title = "Awantura o Kasę – Multiplayer"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.scroll = ft.ScrollMode.AUTO
    page.vertical_alignment = ft.MainAxisAlignment.START

    mp_state = {
        "player_id": None,
        "player_name": "",
        "is_admin": False,
        "joined": False,
        "set_name": "",
        "questions": [],
        "current_q_index": -1,
        "carryover_pot": 0,
        "current_round_pot": 0,  # wyświetlana pula (carryover + backend + podpowiedzi)
        "backend_pot": 0,  # pula z backendu (z licytacji)
        "hints_extra": 0,  # koszt podpowiedzi w tej rundzie
        "local_phase": "idle",
        "countdown_until": 0.0,
        "answer_deadline": 0.0,
        "discussion_until": 0.0,
        "answering_player_id": None,
        "answering_player_name": "",
        "current_answer_text": "",
        "verdict_sent": False,
        "last_round_id": None,
        "current_round_id": None,
        "bidding_fee_paid_for_round": None,  # round_id, dla którego pobrano wpisowe 500
        "local_money": 10000,
        "abcd_bought_this_round": False,
    }

    name_color_cache: dict[str, str] = {}
    color_palette = [
        "#1e3a8a",
        "#4c1d95",
        "#064e3b",
        "#7c2d12",
        "#111827",
        "#075985",
        "#7f1d1d",
        "#374151",
    ]

    def name_to_color(name: str) -> str:
        if name in name_color_cache:
            return name_color_cache[name]
        idx = abs(hash(name)) % len(color_palette)
        name_color_cache[name] = color_palette[idx]
        return color_palette[idx]

    # --- UI ---

    txt_title = ft.Text(
        "AWANTURA O KASĘ – MULTIPLAYER",
        size=22,
        weight=ft.FontWeight.BOLD,
        text_align=ft.TextAlign.CENTER,
    )
    txt_status = ft.Text(
        "Podaj ksywkę i dołącz do gry.",
        size=13,
        color="blue",
    )
    txt_local_money = ft.Text(
        "Twoja kasa (lokalnie): 10000 zł",
        size=14,
        weight=ft.FontWeight.BOLD,
        color="green_700",
    )
    txt_round_info = ft.Text(
        "Brak wybranego zestawu pytań.",
        size=13,
        color="grey_700",
    )
    txt_phase_info = ft.Text(
        "",
        size=13,
        color="grey_800",
    )

    # --- CZAT ---
    col_chat = ft.Column(
        [],
        spacing=1,
        height=220,
        scroll=ft.ScrollMode.ALWAYS,
        auto_scroll=True,
    )

    txt_chat_input = ft.TextField(
        label="Napisz na czacie",
        multiline=False,
        dense=True,
        border_radius=8,
        text_size=13,
        disabled=True,
        on_submit=lambda e: btn_chat_send.on_click(e),
    )

    btn_chat_send = ft.FilledButton(
        "Wyślij",
        disabled=True,
    )

    # Ustaw proporcje 3:1 (przycisk:tekst)
    btn_chat_send.expand = 3
    txt_chat_input.expand = 1

    chat_row = ft.Row(
        [btn_chat_send, txt_chat_input],
        alignment=ft.MainAxisAlignment.START,
        spacing=4,
    )

    chat_box = ft.Container(
        content=ft.Column(
            [
                col_chat,
                chat_row,
            ],
            spacing=2,
        ),
        padding=4,
        border_radius=10,
        border=ft.border.all(1, "#e0e0e0"),
        bgcolor="white",
        height=260,
    )

    # --- PULA / TIMER / PODPOWIEDZI ---
    txt_pot = ft.Text(
        "Pula: 0 zł",
        size=14,
        weight=ft.FontWeight.BOLD,
        color="purple_700",
    )
    txt_timer = ft.Text(
        "Czas: -- s",
        size=14,
        weight=ft.FontWeight.BOLD,
    )

    btn_hint_abcd = ft.OutlinedButton(
        "Kup ABCD (1000–3000 zł)",
        width=220,
        disabled=True,
    )
    btn_hint_5050 = ft.OutlinedButton(
        "Kup 50/50 (500–2500 zł)",
        width=220,
        disabled=True,
    )
    hint_col = ft.Column(
        [btn_hint_abcd, btn_hint_5050],
        spacing=6,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # --- PRZYCISKI LICYTACJI ---
    btn_bid = ft.FilledButton(
        "Licytuj +100 zł",
        width=180,
        disabled=True,
    )
    btn_finish_bidding = ft.FilledButton(
        "Kończę licytację",
        width=180,
        disabled=True,
    )
    btn_all_in = ft.FilledButton(
        "VA BANQUE!",
        width=180,
        disabled=True,
    )
    bidding_col = ft.Column(
        [btn_bid, btn_finish_bidding, btn_all_in],
        spacing=4,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # --- DOŁĄCZANIE ---
    txt_name = ft.TextField(
        label="Twoja ksywka",
        width=220,
        dense=True,
    )
    btn_join = ft.FilledButton("Dołącz do pokoju", width=180)
    join_row = ft.Row(
        [txt_name, btn_join],
        alignment=ft.MainAxisAlignment.START,
        spacing=10,
    )

    layout = ft.Column(
        [
            txt_title,
            txt_status,
            ft.Divider(height=8),
            join_row,
            ft.Row(
                [txt_local_money, txt_pot],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            txt_timer,
            txt_round_info,
            txt_phase_info,
            ft.Divider(height=8),
            chat_box,
            ft.Divider(height=8),
            hint_col,
            ft.Divider(height=8),
            bidding_col,
        ],
        spacing=6,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    page.add(layout)
    page.update()

    # --- FUNKCJE UI ---

    def refresh_local_money():
        val = mp_state["local_money"]
        txt_local_money.value = f"Twoja kasa (lokalnie): {val} zł"
        if val <= 0:
            txt_local_money.color = "red_700"
        elif val < ENTRY_FEE:
            txt_local_money.color = "orange_700"
        else:
            txt_local_money.color = "green_700"
        txt_local_money.update()

    def refresh_pot_label():
        total = mp_state["carryover_pot"] + mp_state["backend_pot"] + mp_state["hints_extra"]
        mp_state["current_round_pot"] = total
        txt_pot.value = f"Pula: {total} zł"
        txt_pot.update()

    def set_phase_info(msg: str, color: str = "grey_800"):
        txt_phase_info.value = msg
        txt_phase_info.color = color
        txt_phase_info.update()

    # --- render czatu ---
    last_chat_len = 0

    def render_chat(chat_list: list[dict], players_state: list[dict]):
        nonlocal last_chat_len
        if not chat_list:
            return
        if len(chat_list) == last_chat_len:
            return
        last_chat_len = len(chat_list)

        admin_names = {p["name"] for p in players_state if p.get("is_admin")}
        col_chat.controls.clear()

        for m in chat_list:
            player_name = m.get("player", "?")
            msg_text = m.get("message", "")
            is_bot = player_name == "BOT"

            spans: list[ft.TextSpan] = []

            if is_bot:
                # Specjalne formatowanie PYTANIA: cała linijka ciemno-niebieska i pogrubiona
                if msg_text.startswith("PYTANIE:"):
                    spans.append(
                        ft.TextSpan(
                            f"BOT: {msg_text}",
                            ft.TextStyle(
                                color="#123499",
                                weight=ft.FontWeight.BOLD,
                                size=12,
                            ),
                        )
                    )
                else:
                    spans.append(
                        ft.TextSpan(
                            "BOT: ",
                            ft.TextStyle(
                                color="black",
                                weight=ft.FontWeight.BOLD,
                                size=12,
                            ),
                        )
                    )
                    spans.append(
                        ft.TextSpan(
                            msg_text,
                            ft.TextStyle(color="black", size=12),
                        )
                    )
            else:
                is_admin = player_name in admin_names
                if is_admin:
                    spans.append(
                        ft.TextSpan(
                            "[ADMIN] ",
                            ft.TextStyle(
                                color="red",
                                weight=ft.FontWeight.BOLD,
                                size=12,
                            ),
                        )
                    )
                name_color = name_to_color(player_name)
                spans.append(
                    ft.TextSpan(
                        f"{player_name}: ",
                        ft.TextStyle(
                            color=name_color,
                            weight=ft.FontWeight.BOLD,
                            size=12,
                        ),
                    )
                )
                spans.append(
                    ft.TextSpan(
                        msg_text,
                        ft.TextStyle(color="black", size=12),
                    )
                )

            col_chat.controls.append(
                ft.Text(
                    spans=spans,
                    max_lines=4,
                    overflow=ft.TextOverflow.ELLIPSIS,
                )
            )
        col_chat.update()

    # --- wykrywanie zniknięcia gracza ---

    async def detect_player_disconnect(players_list):
        now = time.time()
        for p in players_list:
            if "last_heartbeat" in p and now - p["last_heartbeat"] > 1.0:
                await fetch_json(
                    f"{BACKEND_URL}/chat",
                    "POST",
                    {
                        "player": "BOT",
                        "message": f"{p['name']} opuścił grę",
                    },
                )

    processed_chat_ids: set[float] = set()

    async def process_chat_commands(chat_list: list[dict], players_state: list[dict]):
        # Admin wybiera zestaw przez wpisanie numeru 1–50
        if mp_state["set_name"]:
            return
        admin_names = {p["name"] for p in players_state if p.get("is_admin")}
        if not admin_names:
            return

        for m in chat_list:
            ts = m.get("timestamp", 0.0)
            if ts in processed_chat_ids:
                continue
            processed_chat_ids.add(ts)

            player_name = m.get("player", "")
            msg_text = (m.get("message") or "").strip()
            if player_name not in admin_names:
                continue

            if not re.fullmatch(r"0?\d{1,2}", msg_text):
                continue

            num = int(msg_text)
            if num < 1 or num > 50:
                continue

            filename = f"{num:02d}.txt"
            questions = await parse_question_file(filename)
            if not questions:
                await fetch_json(
                    f"{BACKEND_URL}/chat",
                    "POST",
                    {
                        "player": "BOT",
                        "message": f"Nie udało się załadować zestawu {filename}.",
                    },
                )
                return

            if mp_state["is_admin"]:
                await fetch_json(
                    f"{BACKEND_URL}/select_set",
                    "POST",
                    {
                        "player_id": mp_state["player_id"],
                        "set_no": num,
                    },
                )

            mp_state["set_name"] = filename.replace(".txt", "")
            mp_state["questions"] = questions
            mp_state["current_q_index"] = 0
            mp_state["carryover_pot"] = 0
            mp_state["current_round_pot"] = 0
            mp_state["backend_pot"] = 0
            mp_state["hints_extra"] = 0
            mp_state["abcd_bought_this_round"] = False
            mp_state["local_phase"] = "countdown"
            mp_state["countdown_until"] = time.time() + 20.0
            mp_state["verdict_sent"] = False
            mp_state["answering_player_id"] = None
            mp_state["answering_player_name"] = ""
            mp_state["current_answer_text"] = ""
            mp_state["bidding_fee_paid_for_round"] = None
            refresh_pot_label()

            txt_round_info.value = (
                f"Pytanie 1 / {len(questions)} (Zestaw {mp_state['set_name']})"
            )
            txt_round_info.update()

            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {
                    "player": "BOT",
                    "message": f"Zestaw pytań nr {mp_state['set_name']} został wybrany.",
                },
            )
            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {
                    "player": "BOT",
                    "message": "Za 20 sekund start pierwszej licytacji!",
                },
            )

            set_phase_info(
                "Odliczanie 20 s do startu licytacji...",
                color="blue",
            )
            page.update()
            break

    async def mp_register(e):
        name = (txt_name.value or "").strip()
        if not name:
            txt_status.value = "Podaj ksywkę, aby dołączyć."
            txt_status.color = "red"
            txt_status.update()
            return

        data = await fetch_json(
            f"{BACKEND_URL}/register",
            "POST",
            {"name": name},
        )
        if not data or "id" not in data:
            txt_status.value = "Błąd rejestracji (backend)."
            txt_status.color = "red"
            txt_status.update()
            return

        mp_state["player_id"] = data["id"]
        mp_state["player_name"] = data.get("name", name)
        mp_state["is_admin"] = data.get("is_admin", False)
        mp_state["joined"] = True
        mp_state["local_money"] = 10000

        txt_status.value = f"Dołączono jako {mp_state['player_name']}."
        if mp_state["is_admin"]:
            txt_status.value += " Jesteś ADMINEM – wpisz na czacie numer 1–50, aby wybrać zestaw pytań."
        else:
            txt_status.value += " Czekamy, aż ADMIN wybierze zestaw pytań (numer 1–50 na czacie)."
        txt_status.color = "green"
        txt_status.update()

        join_row.visible = False
        txt_chat_input.disabled = False
        btn_chat_send.disabled = False
        refresh_local_money()
        refresh_pot_label()
        page.update()

        page.run_task(mp_heartbeat_loop)
        page.run_task(mp_poll_state)

    async def mp_heartbeat_loop():
        while mp_state["player_id"]:
            await asyncio.sleep(10)
            pid = mp_state["player_id"]
            if not pid:
                break
            resp = await fetch_json(
                f"{BACKEND_URL}/heartbeat",
                "POST",
                {"player_id": pid},
            )
            if not resp or resp.get("status") != "ok":
                break
            mp_state["is_admin"] = resp.get("is_admin", mp_state["is_admin"])

    async def mp_send_chat(e):
        msg = (txt_chat_input.value or "").strip()
        if not msg:
            return

        lp = mp_state["local_phase"]
        answering_id = mp_state["answering_player_id"]
        my_id = mp_state["player_id"]

        # Tylko zwycięzca może odpowiadać w fazie answering_wait
        if lp == "answering_wait" and answering_id and my_id != answering_id:
            set_phase_info(
                f"Teraz odpowiada {mp_state['answering_player_name']}. Poczekaj na swoją kolej.",
                color="red",
            )
            return

        # Wyślij do backendu (normalna wiadomość gracza)
        await fetch_json(
            f"{BACKEND_URL}/chat",
            "POST",
            {"player": mp_state["player_name"] or "Anonim", "message": msg},
        )
        txt_chat_input.value = ""
        txt_chat_input.update()

        # Jeśli zwycięzca licytacji właśnie udzielił pierwszej odpowiedzi
        if (
            lp == "answering_wait"
            and answering_id
            and my_id == answering_id
            and not mp_state["current_answer_text"]
        ):
            mp_state["current_answer_text"] = msg
            mp_state["local_phase"] = "discussion"
            mp_state["discussion_until"] = time.time() + 20.0
            mp_state["verdict_sent"] = False

            # BOT: a wy jak myślicie?
            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {
                    "player": "BOT",
                    "message": (
                        f"Gracz {mp_state['player_name']} odpowiedział: {msg}. "
                        "A wy jak myślicie? Macie 20 sekund na komentarze!"
                    ),
                },
            )

            set_phase_info(
                "Dyskusja: wszyscy mogą pisać na czacie przez 20 s.",
                color="blue",
            )
            page.update()

    async def mp_bid_normal(e):
        if not mp_state["player_id"]:
            return

        # Sprawdź kasę na +100
        if mp_state["local_money"] < 100:
            txt_status.value = "Nie masz wystarczającej kasy, aby podbić o 100 zł."
            txt_status.color = "red"
            txt_status.update()
            return

        data = await fetch_json(
            f"{BACKEND_URL}/bid",
            "POST",
            {"player_id": mp_state["player_id"], "kind": "normal"},
        )
        if not data or data.get("status") != "ok":
            txt_status.value = data.get("detail", "Błąd licytacji +100.")
            txt_status.color = "red"
            txt_status.update()
            return

        # Odejmujemy 100 zł lokalnie
        mp_state["local_money"] -= 100
        refresh_local_money()

    async def mp_bid_allin(e):
        if not mp_state["player_id"]:
            return

        if mp_state["local_money"] <= 0:
            txt_status.value = "Nie masz kasy na VA BANQUE."
            txt_status.color = "red"
            txt_status.update()
            return

        data = await fetch_json(
            f"{BACKEND_URL}/bid",
            "POST",
            {"player_id": mp_state["player_id"], "kind": "allin"},
        )
        if not data or data.get("status") != "ok":
            txt_status.value = data.get("detail", "Błąd licytacji VA BANQUE.")
            txt_status.color = "red"
            txt_status.update()
            return

        # Oddajesz całą kasę
        mp_state["local_money"] = 0
        refresh_local_money()

    async def mp_finish_bidding(e):
        if not mp_state["player_id"]:
            return

        data = await fetch_json(
            f"{BACKEND_URL}/finish_bidding",
            "POST",
            {"player_id": mp_state["player_id"]},
        )
        if not data or data.get("status") != "ok":
            txt_status.value = data.get("detail", "Błąd zakończenia licytacji.")
            txt_status.color = "red"
            txt_status.update()
            return

        txt_status.value = "Licytacja zakończona. Czekamy na wynik z backendu."
        txt_status.color = "blue"
        txt_status.update()

    async def buy_hint_abcd(e):
        # Podpowiedź ABCD tylko w answering_wait / discussion i tylko dla zwycięzcy
        if mp_state["local_phase"] not in ("answering_wait", "discussion"):
            set_phase_info(
                "Podpowiedzi są dostępne tylko podczas odpowiadania na pytanie.",
                color="red",
            )
            return
        if mp_state["player_id"] != mp_state["answering_player_id"]:
            set_phase_info(
                "Tylko gracz, który wygrał licytację, może kupować podpowiedzi.",
                color="red",
            )
            return

        cost = random.randint(1000, 3000)
        if mp_state["local_money"] < cost:
            set_phase_info(
                f"Nie stać Cię na podpowiedź ABCD ({cost} zł).",
                color="red",
            )
            return

        # Odejmujemy kasę i zwiększamy pulę o koszt podpowiedzi
        mp_state["local_money"] -= cost
        mp_state["hints_extra"] += cost
        mp_state["answer_deadline"] += HINT_EXTRA_TIME
        mp_state["abcd_bought_this_round"] = True
        refresh_local_money()
        refresh_pot_label()

        q_idx = mp_state["current_q_index"]
        if 0 <= q_idx < len(mp_state["questions"]):
            q = mp_state["questions"][q_idx]
            answers = q["answers"]
            text_abcd = (
                f"A) {answers[0]}, B) {answers[1]}, "
                f"C) {answers[2]}, D) {answers[3]}"
            )
            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {
                    "player": "BOT",
                    "message": f"Podpowiedź ABCD (koszt {cost} zł): {text_abcd}",
                },
            )
            set_phase_info(
                f"Kupiono podpowiedź ABCD za {cost} zł. Czas na odpowiedź wydłużony.",
                color="blue",
            )
            page.update()

    async def buy_hint_5050(e):
        # Podpowiedź 50/50 tylko po ABCD
        if mp_state["local_phase"] not in ("answering_wait", "discussion"):
            set_phase_info(
                "Podpowiedzi są dostępne tylko podczas odpowiadania na pytanie.",
                color="red",
            )
            return
        if mp_state["player_id"] != mp_state["answering_player_id"]:
            set_phase_info(
                "Tylko gracz, który wygrał licytację, może kupować podpowiedzi.",
                color="red",
            )
            return
        if not mp_state["abcd_bought_this_round"]:
            set_phase_info(
                "Najpierw kup podpowiedź ABCD, dopiero potem 50/50.",
                color="red",
            )
            return

        cost = random.randint(500, 2500)
        if mp_state["local_money"] < cost:
            set_phase_info(
                f"Nie stać Cię na 50/50 ({cost} zł).",
                color="red",
            )
            return

        mp_state["local_money"] -= cost
        mp_state["hints_extra"] += cost
        mp_state["answer_deadline"] += HINT_EXTRA_TIME
        refresh_local_money()
        refresh_pot_label()

        q_idx = mp_state["current_q_index"]
        if 0 <= q_idx < len(mp_state["questions"]):
            q = mp_state["questions"][q_idx]
            correct = q["correct"]
            wrong = [a for a in q["answers"] if a != correct]
            random.shuffle(wrong)
            removed = wrong[:2]
            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {
                    "player": "BOT",
                    "message": (
                        f"Podpowiedź 50/50 (koszt {cost} zł): "
                        f"to na pewno NIE są: {removed[0]} i {removed[1]}."
                    ),
                },
            )
            set_phase_info(
                f"Kupiono 50/50 za {cost} zł. Czas na odpowiedź wydłużony.",
                color="blue",
            )
            page.update()

    async def mp_poll_state():
        while mp_state["player_id"]:
            data = await fetch_json(f"{BACKEND_URL}/state", "GET")
            if not data:
                await asyncio.sleep(1.5)
                continue

            round_id = data.get("round_id")
            phase = data.get("phase")
            pot_backend = data.get("pot", 0)
            time_left_backend = int(data.get("time_left", 0))
            players_list = data.get("players", [])
            chat_list = data.get("chat", [])
            answering_player_id_backend = data.get("answering_player_id")

            mp_state["current_round_id"] = round_id
            mp_state["backend_pot"] = pot_backend
            refresh_pot_label()

            txt_timer.value = f"Czas (z backendu): {time_left_backend} s"
            txt_timer.update()

            my_id = mp_state["player_id"]

            render_chat(chat_list, players_list)
            await process_chat_commands(chat_list, players_list)

            now = time.time()

            # wykrywanie zniknięcia
            await detect_player_disconnect(players_list)

            # --- logika faz lokalnych ---
            if mp_state["local_phase"] == "countdown":
                remain = int(mp_state["countdown_until"] - now)
                if remain < 0:
                    remain = 0
                set_phase_info(
                    f"Odliczanie do licytacji: {remain} s...",
                    color="blue",
                )

                if remain <= 0:
                    mp_state["local_phase"] = "bidding"
                    # ADMIN uruchamia następną rundę w backendzie i ogłasza start licytacji
                    if mp_state["is_admin"]:
                        _ = await fetch_json(
                            f"{BACKEND_URL}/next_round",
                            "POST",
                            {},
                        )
                        await fetch_json(
                            f"{BACKEND_URL}/chat",
                            "POST",
                            {
                                "player": "BOT",
                                "message": "START LICYTACJI – licytujcie o prawo do odpowiedzi!",
                            },
                        )

                # przyciski licytacji
                btn_bid.disabled = phase != "bidding"
                btn_all_in.disabled = phase != "bidding"
                btn_finish_bidding.disabled = phase != "bidding"
                btn_bid.update()
                btn_all_in.update()
                btn_finish_bidding.update()

            elif mp_state["local_phase"] == "bidding":
                # wpisowe 500 zł na rundę (raz na round_id)
                if round_id is not None and mp_state["bidding_fee_paid_for_round"] != round_id:
                    mp_state["local_money"] -= ENTRY_FEE
                    mp_state["bidding_fee_paid_for_round"] = round_id
                    refresh_local_money()
                    await fetch_json(
                        f"{BACKEND_URL}/chat",
                        "POST",
                        {
                            "player": "BOT",
                            "message": (
                                f"Pobrano wpisowe {ENTRY_FEE} zł od gracza {mp_state['player_name']} "
                                "za udział w tej rundzie."
                            ),
                        },
                    )

                btn_bid.disabled = phase != "bidding"
                btn_all_in.disabled = phase != "bidding"
                btn_finish_bidding.disabled = phase != "bidding"
                btn_bid.update()
                btn_all_in.update()
                btn_finish_bidding.update()

                # Back-end przeszedł do fazy answering
                if phase == "answering":
                    mp_state["local_phase"] = "answering_wait"
                    mp_state["answer_deadline"] = now + BASE_ANSWER_TIME
                    mp_state["current_answer_text"] = ""
                    mp_state["verdict_sent"] = False
                    mp_state["answering_player_id"] = answering_player_id_backend
                    mp_state["hints_extra"] = 0
                    mp_state["abcd_bought_this_round"] = False
                    refresh_pot_label()

                    winner_name = "?"
                    for p in players_list:
                        if p["id"] == answering_player_id_backend:
                            winner_name = p["name"]
                            break
                    mp_state["answering_player_name"] = winner_name

                    q_idx = mp_state["current_q_index"]
                    if mp_state["is_admin"] and 0 <= q_idx < len(mp_state["questions"]):
                        q = mp_state["questions"][q_idx]
                        question_text = q["question"]

                        # Najpierw info o zwycięzcy
                        await fetch_json(
                            f"{BACKEND_URL}/chat",
                            "POST",
                            {
                                "player": "BOT",
                                "message": f"Gracz {winner_name} wygrał licytację!",
                            },
                        )
                        # Następnie PYTANIE: cała linia ciemno-niebieska (styl po stronie klienta)
                        await fetch_json(
                            f"{BACKEND_URL}/chat",
                            "POST",
                            {
                                "player": "BOT",
                                "message": f"PYTANIE: {question_text}",
                            },
                        )

                    # Podpowiedzi tylko dla zwycięzcy
                    is_me_winner = my_id == answering_player_id_backend
                    btn_hint_abcd.disabled = not is_me_winner
                    btn_hint_5050.disabled = not is_me_winner
                    btn_hint_abcd.update()
                    btn_hint_5050.update()

                    set_phase_info(
                        f"Na pytanie odpowiada: {mp_state['answering_player_name']} (ma 60 s).",
                        color="green",
                    )

            elif mp_state["local_phase"] == "answering_wait":
                remain = int(mp_state["answer_deadline"] - now)
                if remain < 0:
                    remain = 0
                set_phase_info(
                    f"Na pytanie odpowiada {mp_state['answering_player_name']} – pozostało {remain} s.",
                    color="green",
                )

                if remain <= 0 and not mp_state["current_answer_text"]:
                    mp_state["current_answer_text"] = ""
                    mp_state["local_phase"] = "discussion"
                    mp_state["discussion_until"] = now + 20.0
                    mp_state["verdict_sent"] = False

                    if mp_state["is_admin"]:
                        await fetch_json(
                            f"{BACKEND_URL}/chat",
                            "POST",
                            {
                                "player": "BOT",
                                "message": (
                                    "Czas na odpowiedź minął. "
                                    "A wy jak myślicie mistrzowie, czy brak odpowiedzi "
                                    "to poprawna odpowiedź? Macie 20 sekund na komentarze!"
                                ),
                            },
                        )

            elif mp_state["local_phase"] == "discussion":
                remain = int(mp_state["discussion_until"] - now)
                if remain < 0:
                    remain = 0
                set_phase_info(
                    f"Dyskusja – pozostało {remain} s na komentarze.",
                    color="blue",
                )

                if remain <= 0 and not mp_state["verdict_sent"]:
                    if mp_state["is_admin"]:
                        await send_verdict_and_prepare_next_round()
                    mp_state["verdict_sent"] = True
                    mp_state["local_phase"] = "verdict"

            elif mp_state["local_phase"] == "verdict":
                # czekamy na kolejne odliczanie / zakończenie gry
                pass

            await asyncio.sleep(1.0)

    async def send_verdict_and_prepare_next_round():
        q_idx = mp_state["current_q_index"]
        if not (0 <= q_idx < len(mp_state["questions"])):
            return

        # Upewniamy się, że pula jest aktualna
        refresh_pot_label()
        q = mp_state["questions"][q_idx]
        correct = q["correct"]
        user_answer = mp_state["current_answer_text"]
        norm_user = normalize_answer(user_answer)
        norm_correct = normalize_answer(correct)
        similarity = fuzz.ratio(norm_user, norm_correct)
        winner_name = mp_state["answering_player_name"]
        pot_ui = mp_state["current_round_pot"]

        if similarity >= 80:
            # DOBRA ODPOWIEDŹ
            if winner_name == mp_state["player_name"]:
                mp_state["local_money"] += pot_ui
                refresh_local_money()

            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {
                    "player": "BOT",
                    "message": (
                        f"TAK, odpowiedź prawidłowa! ({similarity}%) "
                        f"Gracz {winner_name} wygrywa {pot_ui} zł z puli!"
                    ),
                },
            )
            mp_state["carryover_pot"] = 0
        else:
            # ZŁA ODPOWIEDŹ
            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {
                    "player": "BOT",
                    "message": (
                        f"NIE, odpowiedź błędna ({similarity}%). "
                        f"Prawidłowa odpowiedź: {correct}. "
                        f"Pula {pot_ui} zł przechodzi do następnego pytania!"
                    ),
                },
            )
            mp_state["carryover_pot"] = pot_ui

        # Resetujemy zmienne rundy
        mp_state["current_round_pot"] = 0
        mp_state["backend_pot"] = 0
        mp_state["hints_extra"] = 0
        mp_state["abcd_bought_this_round"] = False
        mp_state["bidding_fee_paid_for_round"] = None
        refresh_pot_label()

        next_idx = q_idx + 1
        if next_idx >= len(mp_state["questions"]):
            await fetch_json(
                f"{BACKEND_URL}/chat",
                "POST",
                {
                    "player": "BOT",
                    "message": (
                        "To było ostatnie pytanie z tego zestawu. "
                        "Koniec gry! Dzięki za udział."
                    ),
                },
            )
            set_phase_info("Koniec zestawu pytań.", color="red")
            return

        mp_state["current_q_index"] = next_idx
        mp_state["local_phase"] = "countdown"
        mp_state["countdown_until"] = time.time() + 20.0
        mp_state["answering_player_id"] = None
        mp_state["answering_player_name"] = ""
        mp_state["current_answer_text"] = ""
        mp_state["verdict_sent"] = False

        txt_round_info.value = (
            f"Pytanie {next_idx + 1} / {len(mp_state['questions'])} "
            f"(Zestaw {mp_state['set_name']})"
        )
        txt_round_info.update()

        await fetch_json(
            f"{BACKEND_URL}/chat",
            "POST",
            {
                "player": "BOT",
                "message": (
                    "Za 20 sekund start kolejnej rundy i nowej licytacji!"
                ),
            },
        )
        set_phase_info(
            "Odliczanie do kolejnej licytacji...",
            color="blue",
        )

        btn_hint_abcd.disabled = True
        btn_hint_5050.disabled = True
        btn_hint_abcd.update()
        btn_hint_5050.update()

    # --- PODPIĘCIE HANDLERÓW ---

    btn_join.on_click = make_async_click(mp_register)
    btn_chat_send.on_click = make_async_click(mp_send_chat)
    btn_bid.on_click = make_async_click(mp_bid_normal)
    btn_all_in.on_click = make_async_click(mp_bid_allin)
    btn_finish_bidding.on_click = make_async_click(mp_finish_bidding)
    btn_hint_abcd.on_click = make_async_click(buy_hint_abcd)
    btn_hint_5050.on_click = make_async_click(buy_hint_5050)
    page.update()


if __name__ == "__main__":
    try:
        ft.app(target=main)
    finally:
        try:
            loop = asyncio.get_event_loop()
            loop.close()
        except Exception:
            pass
