import asyncio
import random
import re
import warnings

import flet as ft
import js
from js import fetch
from thefuzz import fuzz

warnings.filterwarnings("ignore")

BACKEND_URL = "https://game-multiplayer-qfn1.onrender.com"
GITHUB_RAW_BASE_URL = "https://raw.githubusercontent.com/mechagdynia2-ai/game/main/assets/"


def make_async_click(async_callback):
    """Adapter: async handler → Flet on_click."""
    def handler(e: ft.ControlEvent):
        async def task():
            await async_callback(e)

        e.page.run_task(task)

    return handler


async def fetch_text(url: str) -> str:
    """Pobranie pliku tekstowego z GitHuba."""
    try:
        response = await fetch(url)
        return await response.text()
    except Exception as e:
        print("[FETCH ERROR]", e)
        return ""


async def parse_question_file(page: ft.Page, filename: str) -> list:
    """Parsowanie pliku z pytaniami w formacie:

    01. Treść pytania...
    prawidłowa odpowiedz = ...
    odpowiedz ABCD = A = ..., B = ..., C = ..., D = ...
    """
    url = f"{GITHUB_RAW_BASE_URL}{filename}"
    print(f"[FETCH] Pobieram: {url}")
    content = await fetch_text(url)
    if not content:
        print(f"[FETCH ERROR] {filename}: brak danych")
        return []

    parsed = []
    # Dzielenie po liniach zaczynających się od liczby i kropki (01. / 1. / 123.)
    blocks = re.split(r"\n(?=\d{1,3}\.)", content)
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # Pytanie
        q_match = re.match(r"^\d{1,3}\.\s*(.+)", block)
        if not q_match:
            print("[WARNING] Nie znaleziono pytania:", block[:50])
            continue
        question = q_match.group(1).strip()

        # Poprawna odpowiedź
        correct_match = re.search(
            r"prawidłowa\s+odpowied[zź]\s*=\s*(.+)",
            block,
            re.IGNORECASE,
        )
        if not correct_match:
            print("[WARNING] Brak prawidłowej odpowiedzi:", block[:50])
            continue
        correct = correct_match.group(1).strip()

        # Odpowiedzi ABCD
        answers_match = re.search(
            r"odpowied[zź]\s*abcd\s*=\s*A\s*=\s*(.+?),\s*B\s*=\s*(.+?),\s*C\s*=\s*(.+?),\s*D\s*=\s*(.+)",
            block,
            re.IGNORECASE,
        )
        if not answers_match:
            print("[WARNING] Brak ABCD:", block[:50])
            continue

        a = answers_match.group(1).strip()
        b = answers_match.group(2).strip()
        c = answers_match.group(3).strip()
        d = answers_match.group(4).strip()

        parsed.append(
            {
                "question": question,
                "correct": correct,
                "answers": [a, b, c, d],
            }
        )

    return parsed


def normalize_answer(text: str) -> str:
    """Normalizacja odpowiedzi gracza do porównania fuzz.ratio."""
    text = str(text).lower().strip()
    repl = {
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
    for c, r in repl.items():
        text = text.replace(c, r)
    # Zamiana 'u' → 'o' (łapiemy np. ó/u/o)
    text = text.replace("u", "o")
    # Usunięcie spacji
    return "".join(text.split())


async def main(page: ft.Page):
    page.title = "Awantura o Kasę – Singleplayer + Multiplayer (beta)"
    page.scroll = ft.ScrollMode.AUTO
    page.theme_mode = ft.ThemeMode.LIGHT
    page.vertical_alignment = ft.MainAxisAlignment.START

    # ------------------- STAN GRY SINGLEPLAYER ------------------------
    game = {
        "money": 10000,
        "current_question_index": -1,
        "base_stake": 500,
        "abcd_unlocked": False,
        "main_pot": 0,
        "spent": 0,
        "bid": 0,
        "bonus": 0,
        "max_bid": 5000,
        "questions": [],
        "total": 0,
        "set_name": "",
    }

    # ------------------- STAN MULTIPLAYER -----------------------------
    mp_state = {
        "player_id": None,
        "player_name": "",
    }

    # ------------------- UI GÓRNY PANEL SINGLEPLAYER ------------------
    txt_money = ft.Text(
        f"Twoja kasa: {game['money']} zł",
        size=16,
        weight=ft.FontWeight.BOLD,
        color="green_600",
    )
    txt_spent = ft.Text(
        "Wydano: 0 zł",
        size=14,
        color="grey_700",
        text_align=ft.TextAlign.RIGHT,
    )
    txt_counter = ft.Text(
        "Pytanie 0 / 0 (Zestaw 00)",
        size=16,
        color="grey_700",
        text_align=ft.TextAlign.CENTER,
    )
    txt_pot = ft.Text(
        "AKTUALNA PULA: 0 zł",
        size=22,
        weight=ft.FontWeight.BOLD,
        color="purple_600",
        text_align=ft.TextAlign.CENTER,
    )
    txt_bonus = ft.Text(
        "Bonus od banku: 0 zł",
        size=16,
        color="blue_600",
        text_align=ft.TextAlign.CENTER,
        visible=False,
    )
    txt_question = ft.Text(
        "Wciśnij 'Start', aby rozpocząć grę!",
        size=18,
        weight=ft.FontWeight.BOLD,
        text_align=ft.TextAlign.CENTER,
    )
    txt_feedback = ft.Text(
        "",
        size=16,
        text_align=ft.TextAlign.CENTER,
    )

    txt_answer = ft.TextField(
        label="Wpisz swoją odpowiedź...",
        width=400,
        text_align=ft.TextAlign.CENTER,
    )
    btn_submit_answer = ft.FilledButton(
        "Zatwierdź odpowiedź",
        icon=ft.Icons.CHECK,
        width=400,
    )

    answers_column = ft.Column(
        [],
        spacing=10,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        visible=False,
    )
    answer_box = ft.Column(
        [txt_answer, btn_submit_answer, answers_column],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        visible=False,
    )

    btn_bid = ft.FilledButton("...", width=400)
    btn_show_question = ft.FilledButton("Pokaż pytanie", width=400)
    bidding_panel = ft.Column(
        [btn_bid, btn_show_question],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        visible=False,
    )

    btn_5050 = ft.OutlinedButton(
        "Kup podpowiedź 50/50 (losowo 500-2500 zł)",
        width=400,
        disabled=True,
    )
    btn_buy_abcd = ft.OutlinedButton(
        "Kup opcje ABCD (losowo 1000-3000 zł)",
        width=400,
        disabled=True,
    )
    btn_next = ft.FilledButton("Następne pytanie", width=400, visible=False)
    btn_back = ft.OutlinedButton(
        "Wróć do menu",
        icon=ft.Icons.ARROW_BACK,
        width=400,
        visible=False,
        style=ft.ButtonStyle(color="red"),
    )

    game_view = ft.Column(
        [
            ft.Container(
                ft.Row(
                    [txt_money, txt_spent],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                padding=ft.padding.only(left=20, right=20, top=10, bottom=5),
            ),
            ft.Divider(height=1, color="grey_300"),
            ft.Container(txt_counter, alignment=ft.alignment.center),
            ft.Container(txt_pot, alignment=ft.alignment.center, padding=10),
            ft.Container(txt_bonus, alignment=ft.alignment.center, padding=5),
            ft.Container(
                txt_question,
                alignment=ft.alignment.center,
                padding=ft.padding.only(
                    left=20,
                    right=20,
                    top=10,
                    bottom=10,
                ),
                height=100,
            ),
            bidding_panel,
            answer_box,
            ft.Divider(height=20, color="transparent"),
            ft.Column(
                [btn_5050, btn_buy_abcd, btn_next, txt_feedback, btn_back],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=10,
            ),
        ],
        visible=False,
    )

    main_feedback = ft.Text("", color="red", visible=False)

    # ------------------- MENU ZESTAWÓW PYTAŃ --------------------------
    def menu_tile(i: int, color: str):
        filename = f"{i:02d}.txt"

        async def click(e):
            await start_game_session(e, filename)

        return ft.Container(
            content=ft.Text(
                f"{i:02d}",
                size=14,
                weight=ft.FontWeight.BOLD,
                color="black",
            ),
            width=46,
            height=46,
            alignment=ft.alignment.center,
            bgcolor=color,
            border_radius=100,
            padding=0,
            on_click=make_async_click(click),
        )

    menu_standard = [menu_tile(i, "blue_grey_50") for i in range(1, 31)]
    menu_pop = [menu_tile(i, "deep_purple_50") for i in range(31, 41)]
    menu_music = [menu_tile(i, "amber_50") for i in range(41, 51)]

    # ------------------- MULTIPLAYER UI -------------------------------
    txt_mp_title = ft.Text(
        "Tryb multiplayer (beta) – wspólna licytacja 20 s",
        size=20,
        weight=ft.FontWeight.BOLD,
    )
    txt_mp_info = ft.Text(
        "Dołącz do pokoju, licytuj +100 zł albo idź VA BANQUE.\n"
        "Pula i stawki wszystkich graczy aktualizują się na żywo.",
        size=14,
        color="grey_700",
    )
    txt_mp_name = ft.TextField(label="Twoja ksywka", width=220)
    btn_mp_join = ft.FilledButton("Dołącz do pokoju", width=180)
    txt_mp_status = ft.Text("", size=12, color="blue")

    txt_mp_timer = ft.Text("Czas: -- s", size=16, weight=ft.FontWeight.BOLD)
    txt_mp_pot = ft.Text(
        "Pula: 0 zł",
        size=18,
        weight=ft.FontWeight.BOLD,
        color="purple",
    )

    btn_mp_bid = ft.FilledButton(
        "Licytuj +100 zł (multiplayer)",
        width=250,
        disabled=True,
    )
    btn_mp_allin = ft.FilledButton(
        "VA BANQUE!",
        width=250,
        disabled=True,
    )

    col_mp_players = ft.Column([], height=160, scroll=ft.ScrollMode.AUTO)
    col_mp_chat = ft.Column([], height=160, scroll=ft.ScrollMode.AUTO)
    txt_mp_chat = ft.TextField(label="Napisz na czacie", expand=True)
    btn_mp_chat_send = ft.FilledButton(
        "Wyślij",
        width=100,
        disabled=True,
    )

    multiplayer_room = ft.Container(
        ft.Column(
            [
                txt_mp_title,
                txt_mp_info,
                ft.Row([txt_mp_name, btn_mp_join], alignment="start"),
                txt_mp_status,
                ft.Divider(),
                ft.Row(
                    [txt_mp_timer, txt_mp_pot],
                    alignment="spaceBetween",
                ),
                ft.Row([btn_mp_bid, btn_mp_allin], alignment="start"),
                ft.Row(
                    [
                        ft.Column(
                            [ft.Text("Gracze:", weight="bold"), col_mp_players],
                            width=260,
                        ),
                        ft.Column(
                            [
                                ft.Text("Czat:", weight="bold"),
                                col_mp_chat,
                                ft.Row([txt_mp_chat, btn_mp_chat_send]),
                            ],
                            expand=True,
                        ),
                    ],
                    alignment="spaceBetween",
                ),
            ],
            spacing=10,
        ),
        padding=15,
        border_radius=10,
        bgcolor="blue_50",
    )

    main_menu = ft.Column(
        [
            ft.Text("Wybierz zestaw pytań:", size=24, weight="bold"),
            ft.Text("Pliki pobierane są bezpośrednio z GitHuba", size=14),
            main_feedback,
            ft.Divider(height=15),
            ft.Row(menu_standard[:10], alignment="center", wrap=True),
            ft.Row(menu_standard[10:20], alignment="center", wrap=True),
            ft.Row(menu_standard[20:30], alignment="center", wrap=True),
            ft.Divider(height=20),
            ft.Text("Pytania popkultura:", size=22, weight="bold"),
            ft.Row(menu_pop, alignment="center", wrap=True),
            ft.Divider(height=20),
            ft.Text(
                "Pytania popkultura + muzyka:",
                size=22,
                weight="bold",
            ),
            ft.Row(menu_music, alignment="center", wrap=True),
            ft.Divider(height=30),
            multiplayer_room,
        ],
        spacing=10,
        horizontal_alignment="center",
        visible=True,
    )

    # ------------------- SINGLEPLAYER LOGIKA --------------------------

    def refresh_money():
        txt_money.value = f"Twoja kasa: {game['money']} zł"
        if game["money"] <= 0:
            txt_money.color = "red_700"
        elif game["money"] < game["base_stake"]:
            txt_money.color = "orange_600"
        else:
            txt_money.color = "green_600"
        page.update(txt_money)

    def refresh_spent():
        txt_spent.value = f"Wydano: {game['spent']} zł"
        page.update(txt_spent)

    def refresh_pot():
        txt_pot.value = f"AKTUALNA PULA: {game['main_pot']} zł"
        page.update(txt_pot)

    def refresh_bonus():
        txt_bonus.value = f"Bonus od banku: {game['bonus']} zł"
        page.update(txt_bonus)

    def refresh_counter():
        idx = game["current_question_index"] + 1
        total = game["total"]
        name = game["set_name"]
        txt_counter.value = f"Pytanie {idx} / {total} (Zestaw {name})"
        page.update(txt_counter)

    def show_game_over(msg: str):
        btn_5050.disabled = True
        btn_buy_abcd.disabled = True
        btn_next.disabled = True
        txt_answer.disabled = True
        btn_submit_answer.disabled = True
        for b in answers_column.controls:
            b.disabled = True

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Koniec gry!"),
            content=ft.Text(msg),
            actions=[
                ft.TextButton(
                    "Wróć do menu",
                    on_click=lambda e: back_to_menu(e),
                )
            ],
        )
        page.dialog = dlg
        dlg.open = True
        page.update()

    def check_answer(user_answer: str):
        txt_answer.disabled = True
        btn_submit_answer.disabled = True
        btn_5050.disabled = True
        btn_buy_abcd.disabled = True
        for b in answers_column.controls:
            b.disabled = True

        q = game["questions"][game["current_question_index"]]
        correct = q["correct"]
        pot = game["main_pot"]

        norm_user = normalize_answer(user_answer)
        norm_correct = normalize_answer(correct)
        similarity = fuzz.ratio(norm_user, norm_correct)

        if similarity >= 80:
            game["money"] += pot
            game["main_pot"] = 0
            txt_feedback.value = (
                f"DOBRZE! ({similarity}%) +{pot} zł\nPoprawna: {correct}"
            )
            txt_feedback.color = "green"
        else:
            txt_feedback.value = (
                f"ŹLE ({similarity}%) – pula przechodzi dalej.\n"
                f"Poprawna: {correct}"
            )
            txt_feedback.color = "red"

        game["bid"] = 0
        game["bonus"] = 0

        refresh_money()
        refresh_pot()
        refresh_bonus()

        btn_next.visible = True
        btn_back.visible = True
        page.update()

    def submit_answer(e):
        check_answer(txt_answer.value)

    def abcd_click(e):
        check_answer(e.control.data)

    def hint_5050(e):
        if not game["abcd_unlocked"]:
            txt_feedback.value = "50/50 działa tylko po kupnie ABCD!"
            txt_feedback.color = "orange"
            page.update(txt_feedback)
            return

        cost = random.randint(500, 2500)
        if game["money"] < cost:
            txt_feedback.value = f"Nie stać Cię ({cost} zł)"
            txt_feedback.color = "orange"
            page.update(txt_feedback)
            return

        game["money"] -= cost
        game["spent"] += cost
        refresh_money()
        refresh_spent()

        q = game["questions"][game["current_question_index"]]
        correct = q["correct"]
        wrong = [a for a in q["answers"] if a != correct]
        random.shuffle(wrong)
        to_disable = wrong[:2]

        for b in answers_column.controls:
            if b.data in to_disable:
                b.disabled = True
                b.opacity = 0.3
                b.on_click = None
                b.update()

        txt_feedback.value = (
            f"Usunięto 2 błędne odpowiedzi! (koszt {cost} zł)"
        )
        txt_feedback.color = "blue"
        page.update()

    def buy_abcd(e):
        cost = random.randint(1000, 3000)
        if game["money"] < cost:
            txt_feedback.value = f"Nie stać Cię ({cost} zł)"
            txt_feedback.color = "orange"
            page.update(txt_feedback)
            return

        game["abcd_unlocked"] = True
        game["money"] -= cost
        game["spent"] += cost
        refresh_money()
        refresh_spent()

        txt_answer.visible = False
        btn_submit_answer.visible = False
        answers_column.visible = True
        btn_buy_abcd.disabled = True
        btn_5050.disabled = False

        q = game["questions"][game["current_question_index"]]
        answers_column.controls.clear()
        shuffled = q["answers"][:]
        random.shuffle(shuffled)
        for ans in shuffled:
            answers_column.controls.append(
                ft.FilledButton(
                    ans,
                    width=400,
                    data=ans,
                    on_click=abcd_click,
                )
            )

        txt_feedback.value = f"Kupiono ABCD (koszt {cost} zł)"
        txt_feedback.color = "blue"
        page.update()

    def start_question(e):
        game["current_question_index"] += 1
        if not game["questions"] or game["current_question_index"] >= game["total"]:
            if not game["questions"]:
                show_game_over(
                    f"Błąd: Zestaw {game['set_name']} nie zawiera pytań "
                    f"w poprawnym formacie. Spróbuj innego zestawu."
                )
            else:
                show_game_over(
                    f"Ukończyłaś zestaw {game['set_name']}!\n"
                    f"Kasa: {game['money']} zł"
                )
            return

        refresh_counter()
        q = game["questions"][game["current_question_index"]]
        txt_question.value = q["question"]
        txt_question.visible = True
        bidding_panel.visible = False
        txt_bonus.visible = False
        answer_box.visible = True
        txt_answer.visible = True
        txt_answer.disabled = False
        txt_answer.value = ""
        btn_submit_answer.visible = True
        btn_submit_answer.disabled = False
        btn_buy_abcd.disabled = False
        btn_5050.disabled = True
        game["abcd_unlocked"] = False
        answers_column.visible = False
        answers_column.controls.clear()
        txt_feedback.value = "Odpowiedz na pytanie:"
        txt_feedback.color = "black"
        page.update()

    def bid_100(e):
        if game["bid"] >= game["max_bid"]:
            txt_feedback.value = (
                f"Osiągnięto limit licytacji ({game['max_bid']} zł)"
            )
            txt_feedback.color = "orange"
            page.update()
            btn_bid.disabled = True
            return

        cost = 100
        if game["money"] < cost:
            txt_feedback.value = "Nie masz już pieniędzy!"
            txt_feedback.color = "orange"
            page.update(txt_feedback)
            return

        game["money"] -= cost
        game["spent"] += cost
        game["main_pot"] += cost
        game["bid"] += cost

        bonus_target = (game["bid"] // 1000) * 50
        if bonus_target > game["bonus"]:
            diff = bonus_target - game["bonus"]
            game["bonus"] = bonus_target
            game["main_pot"] += diff
            txt_feedback.value = f"BONUS! Bank dorzuca {diff} zł"
            txt_feedback.color = "blue"
        else:
            txt_feedback.value = f"Wrzuciłaś {cost} zł."
            txt_feedback.color = "black"

        refresh_money()
        refresh_spent()
        refresh_pot()
        refresh_bonus()
        btn_bid.text = f"Licytuj +100 zł (Suma: {game['bid']} zł)"
        if game["bid"] >= game["max_bid"]:
            btn_bid.disabled = True
        page.update()

    def start_bidding(e):
        if not game["questions"]:
            show_game_over(
                f"Zestaw {game['set_name']} nie zawiera pytań. "
                f"Wybierz inny zestaw."
            )
            return

        stake = game["base_stake"]
        if game["money"] < stake:
            show_game_over(
                f"Nie masz {stake} zł na rozpoczęcie gry!"
            )
            return

        game["money"] -= stake
        game["spent"] += stake
        game["main_pot"] = stake
        game["bid"] = stake
        game["bonus"] = 0

        refresh_money()
        refresh_spent()
        refresh_pot()
        refresh_bonus()

        txt_feedback.value = f"Start! Wrzuciłaś {stake} zł."
        txt_feedback.color = "black"
        txt_question.visible = False
        answer_box.visible = False
        btn_next.visible = False
        btn_back.visible = False
        btn_5050.disabled = True
        btn_buy_abcd.disabled = True
        bidding_panel.visible = True
        txt_bonus.visible = True
        btn_bid.disabled = False
        btn_bid.text = f"Licytuj +100 zł (Suma: {game['bid']} zł)"
        btn_show_question.disabled = False
        page.update()

    def reset_game():
        game["money"] = 10000
        game["current_question_index"] = -1
        game["main_pot"] = 0
        game["spent"] = 0
        game["bid"] = 0
        game["bonus"] = 0
        txt_question.value = "Rozpoczęto grę — rozpocznij licytację!"
        txt_feedback.value = "Witaj w grze!"
        txt_feedback.color = "black"
        bidding_panel.visible = False
        answer_box.visible = False
        btn_next.visible = False
        btn_back.visible = False
        btn_5050.disabled = True
        btn_buy_abcd.disabled = True
        refresh_money()
        refresh_spent()
        refresh_pot()
        refresh_bonus()
        page.update()

    def back_to_menu(e):
        main_menu.visible = True
        game_view.visible = False
        if page.dialog:
            page.dialog.open = False
        page.update()

    async def start_game_session(e, filename: str):
        print(f"[LOAD] Pobieram zestaw: {filename}")
        questions = await parse_question_file(page, filename)
        game["questions"] = questions
        game["total"] = len(questions)
        game["set_name"] = filename.replace(".txt", "")
        reset_game()
        main_menu.visible = False
        game_view.visible = True
        main_feedback.visible = False
        page.update()
        start_bidding(None)

    # ------------------- MULTIPLAYER LOGIKA --------------------------

    async def mp_register(e):
        name = (txt_mp_name.value or "").strip()
        if not name:
            txt_mp_status.value = "Podaj ksywkę, aby dołączyć."
            txt_mp_status.color = "red"
            page.update(txt_mp_status)
            return

        try:
            payload = __import__("json").dumps({"name": name})

            resp = await fetch(
                f"{BACKEND_URL}/register",
                js.Object.fromEntries(
                    [
                        ["method", "POST"],
                        [
                            "headers",
                            js.Object.fromEntries(
                                [["Content-Type", "application/json"]]
                            ),
                        ],
                        ["body", payload],
                    ]
                ),
            )

            raw = await resp.json()
            try:
                data = raw.to_py()
            except Exception:
                data = raw

            if not isinstance(data, dict) or "id" not in data:
                print("MP REGISTER RAW RESPONSE:", data)
                txt_mp_status.value = "Błąd: backend nie zwrócił ID gracza."
                txt_mp_status.color = "red"
                page.update(txt_mp_status)
                return

            mp_state["player_id"] = data["id"]
            mp_state["player_name"] = data.get("name", name)

            txt_mp_status.value = f"Dołączono jako {mp_state['player_name']}."
            txt_mp_status.color = "green"
            btn_mp_bid.disabled = False
            btn_mp_allin.disabled = False
            btn_mp_chat_send.disabled = False
            page.update()

        except Exception as ex:
            print("MP REGISTER ERROR:", ex)
            txt_mp_status.value = "Błąd połączenia z serwerem."
            txt_mp_status.color = "red"
            page.update(txt_mp_status)

    async def mp_bid(kind: str):
        if not mp_state["player_id"]:
            txt_mp_status.value = "Najpierw dołącz do pokoju!"
            txt_mp_status.color = "red"
            page.update(txt_mp_status)
            return

        try:
            payload = __import__("json").dumps(
                {"player_id": mp_state["player_id"], "kind": kind}
            )

            resp = await fetch(
                f"{BACKEND_URL}/bid",
                js.Object.fromEntries(
                    [
                        ["method", "POST"],
                        [
                            "headers",
                            js.Object.fromEntries(
                                [["Content-Type", "application/json"]]
                            ),
                        ],
                        ["body", payload],
                    ]
                ),
            )

            raw = await resp.json()
            try:
                data = raw.to_py()
            except Exception:
                data = raw

            pot_val = 0
            if isinstance(data, dict):
                pot_val = data.get("pot", 0)

            txt_mp_status.value = (
                f"Licytacja OK → aktualna pula: {pot_val} zł"
            )
            txt_mp_status.color = "blue"
            page.update(txt_mp_status)

        except Exception as ex:
            print("MP BID ERROR:", ex)
            txt_mp_status.value = "Błąd połączenia przy licytacji!"
            txt_mp_status.color = "red"
            page.update(txt_mp_status)

    async def mp_bid_normal(e):
        await mp_bid("normal")

    async def mp_bid_allin(e):
        await mp_bid("allin")

    async def mp_send_chat(e):
        msg = (txt_mp_chat.value or "").strip()
        if not msg:
            return

        name = mp_state["player_name"] or "Anonim"

        try:
            payload = __import__("json").dumps(
                {"player": name, "message": msg}
            )

            await fetch(
                f"{BACKEND_URL}/chat",
                js.Object.fromEntries(
                    [
                        ["method", "POST"],
                        [
                            "headers",
                            js.Object.fromEntries(
                                [["Content-Type", "application/json"]]
                            ),
                        ],
                        ["body", payload],
                    ]
                ),
            )

            txt_mp_chat.value = ""
            page.update(txt_mp_chat)

        except Exception as ex:
            print("MP CHAT ERROR:", ex)

    async def mp_poll_state():
        await asyncio.sleep(1)
        while True:
            try:
                resp = await fetch(
                    f"{BACKEND_URL}/state",
                    js.Object.fromEntries([["method", "GET"]]),
                )

                raw = await resp.json()
                try:
                    data = raw.to_py()
                except Exception:
                    data = raw

                if not isinstance(data, dict):
                    print("MP POLL ERROR – response nie jest dict:", data)
                    await asyncio.sleep(1.5)
                    continue

                # TIMER + PULA (korzystamy z time_left, pot)
                t = data.get("time_left", 0)
                txt_mp_timer.value = f"Czas: {int(t)} s"
                txt_mp_pot.value = f"Pula: {data.get('pot', 0)} zł"

                # LISTA GRACZY
                col_mp_players.controls.clear()
                for p in data.get("players", []):
                    name = p.get("name", "?")
                    bid = p.get("bid", 0)
                    money = p.get("money", 0)
                    is_all_in = p.get("is_all_in", False)

                    text = f"{name}: stawka {bid} zł | saldo: {money} zł"
                    if is_all_in:
                        text += " (VA BANQUE)"

                    col_mp_players.controls.append(ft.Text(text, size=14))

                # CHAT
                col_mp_chat.controls.clear()
                for m in data.get("chat", []):
                    col_mp_chat.controls.append(
                        ft.Text(
                            f"{m.get('player', '?')}: {m.get('message', '')}",
                            size=13,
                        )
                    )

                page.update()

            except Exception as ex:
                print("MP POLL ERROR:", ex)

            await asyncio.sleep(1.5)

    # ------------------- HANDLERS PRZYCISKÓW ------------------------

    btn_submit_answer.on_click = submit_answer
    btn_5050.on_click = hint_5050
    btn_buy_abcd.on_click = buy_abcd
    btn_show_question.on_click = start_question
    btn_bid.on_click = bid_100
    btn_next.on_click = start_bidding
    btn_back.on_click = back_to_menu

    btn_mp_join.on_click = make_async_click(mp_register)
    btn_mp_bid.on_click = make_async_click(mp_bid_normal)
    btn_mp_allin.on_click = make_async_click(mp_bid_allin)
    btn_mp_chat_send.on_click = make_async_click(mp_send_chat)

    # ------------------- START STRONY ------------------------

    page.add(main_menu, game_view)
    page.update()

    # Start stałego odpytywania stanu pokoju (multiplayer)
    page.run_task(mp_poll_state)


if __name__ == "__main__":
    try:
        ft.app(target=main)
    finally:
        try:
            loop = asyncio.get_event_loop()
            loop.close()
        except Exception:
            pass
