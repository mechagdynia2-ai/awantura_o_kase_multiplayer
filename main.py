import flet as ft
import asyncio
import json
import re
import random
import time
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from thefuzz import fuzz
import unicodedata

# Obsługa środowiska WEB (Pyodide) vs Desktop
try:
    import js
    from js import fetch
except ImportError:
    js = None
    fetch = None

# ----------------- KONFIGURACJA -----------------

BACKEND_URL = "https://game-multiplayer-qfn1.onrender.com"
GITHUB_RAW_BASE_URL = "https://raw.githubusercontent.com/mechagdynia2-ai/game/main/assets/"

ENTRY_FEE = 500
BASE_ANSWER_TIME = 60
HINT_EXTRA_TIME = 30

# ----------------- ZARZĄDZANIE STANEM -----------------

@dataclass
class GameState:
    player_id: Optional[str] = None
    player_name: str = ""
    is_admin: bool = False
    joined: bool = False
    
    # Dane o zestawie
    set_name: str = ""
    total_questions_in_set: int = 0
    
    # Finanse
    carryover_pot: int = 0
    current_round_pot: int = 0
    backend_pot: int = 0
    hints_extra: int = 0
    local_money: int = 10000
    
    # Fazy i czas
    local_phase: str = "idle"
    countdown_until: float = 0.0
    answer_deadline: float = 0.0
    discussion_until: float = 0.0
    
    # Rozgrywka
    answering_player_id: Optional[str] = None
    answering_player_name: str = ""
    current_answer_text: str = ""
    verdict_sent: bool = False
    current_round_id: Optional[str] = None
    bidding_fee_paid_for_round: Optional[str] = None
    abcd_bought_this_round: bool = False
    
    # Techniczne - timestamp ostatniej wiadomości, żeby nie odświeżać bez sensu
    last_chat_timestamp: float = 0.0

# ----------------- WARSTWA SIECIOWA -----------------

async def fetch_text(url: str) -> str:
    if not fetch: return ""
    try:
        resp = await fetch(url)
        return await resp.text()
    except Exception as ex:
        print(f"[FETCH_TEXT ERROR] {ex}")
        return ""

def _build_fetch_kwargs(method: str = "GET", body: Optional[dict] = None):
    if not js: return {}
    m = method.upper()
    if m == "GET":
        return js.Object.fromEntries([["method", "GET"]])
    
    payload = json.dumps(body or {})
    headers = js.Object.fromEntries([["Content-Type", "application/json"]])
    return js.Object.fromEntries([["method", m], ["headers", headers], ["body", payload]])

async def fetch_json(url: str, method: str = "GET", body: Optional[dict] = None):
    if not fetch: return None
    try:
        kwargs = _build_fetch_kwargs(method, body)
        resp = await fetch(url, kwargs)
        raw = await resp.json()
        try:
            return raw.to_py()
        except Exception:
            return raw
    except Exception as ex:
        print(f"[FETCH_JSON ERROR] {ex} url: {url}")
        return None

async def send_bot_message(msg: str):
    await fetch_json(f"{BACKEND_URL}/chat", "POST", {"player": "BOT", "message": msg})

# ----------------- POMOCNICZE -----------------

def name_to_color(name: str) -> str:
    colors = ["#1e3a8a", "#4c1d95", "#064e3b", "#7c2d12", "#111827", "#075985", "#7f1d1d", "#374151"]
    return colors[abs(hash(name)) % len(colors)]

# ----------------- UI -----------------

async def main(page: ft.Page):
    page.title = "Awantura o Kasę – Multiplayer"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.scroll = ft.ScrollMode.AUTO
    
    state = GameState()

    # --- Elementy UI ---
    txt_status = ft.Text("Podaj ksywkę i dołącz do gry.", size=13, color="blue")
    txt_local_money = ft.Text("Twoja kasa: 10000 zł", size=14, weight=ft.FontWeight.BOLD, color="green_700")
    txt_pot = ft.Text("Pula: 0 zł", size=14, weight=ft.FontWeight.BOLD, color="purple_700")
    txt_timer = ft.Text("Czas: -- s", size=14, weight=ft.FontWeight.BOLD)
    
    # Numer pytania na górze
    txt_round_info = ft.Text("Oczekiwanie na zestaw pytań...", size=16, weight=ft.FontWeight.BOLD, color="blue_900")
    txt_phase_info = ft.Text("", size=13, color="grey_800")

    col_chat = ft.Column([], spacing=1, height=220, scroll=ft.ScrollMode.ALWAYS, auto_scroll=True)
    txt_chat_input = ft.TextField(label="Napisz na czacie", dense=True, disabled=True, expand=1)
    btn_chat_send = ft.FilledButton("Wyślij", disabled=True) 

    btn_hint_abcd = ft.OutlinedButton("Kup ABCD (1000–3000 zł)", width=220, disabled=True)
    btn_hint_5050 = ft.OutlinedButton("Kup 50/50 (500–2500 zł)", width=220, disabled=True)
    
    btn_bid = ft.FilledButton("Licytuj +100 zł", width=180, disabled=True)
    btn_finish_bidding = ft.FilledButton("Pasuję", width=180, disabled=True)
    btn_all_in = ft.FilledButton("VA BANQUE!", width=180, disabled=True)

    txt_name = ft.TextField(label="Twoja ksywka", width=220, dense=True)
    btn_join = ft.FilledButton("Dołącz", width=150)
    join_row = ft.Row([txt_name, btn_join], alignment=ft.MainAxisAlignment.CENTER)

    layout = ft.Column([
        ft.Text("AWANTURA O KASĘ – MULTIPLAYER", size=22, weight="bold", text_align="center"),
        txt_status,
        ft.Divider(height=8),
        join_row,
        ft.Row([txt_local_money, txt_pot], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Row([txt_timer, txt_phase_info], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        
        # Sekcja z numerem pytania
        ft.Container(
            content=txt_round_info,
            alignment=ft.alignment.center,
            padding=5,
            bgcolor="blue_50",
            border_radius=5
        ),

        ft.Divider(height=8),
        ft.Container(
            content=ft.Column([col_chat, ft.Row([txt_chat_input, btn_chat_send])]),
            padding=5, border_radius=10, border=ft.border.all(1, "#e0e0e0"), bgcolor="white", height=280
        ),
        ft.Divider(height=8),
        ft.Column([btn_hint_abcd, btn_hint_5050], horizontal_alignment="center"),
        ft.Divider(height=8),
        ft.Row([btn_bid, btn_finish_bidding, btn_all_in], alignment=ft.MainAxisAlignment.CENTER, wrap=True),
    ], spacing=6)

    page.add(layout)

    # ----------------- UI UPDATE HELPERY -----------------

    def refresh_local_money():
        val = state.local_money
        txt_local_money.value = f"Twoja kasa: {val} zł"
        txt_local_money.color = "red_700" if val <= 0 else ("orange_700" if val < ENTRY_FEE else "green_700")
        txt_local_money.update()

    def refresh_pot_label():
        total = state.carryover_pot + state.backend_pot + state.hints_extra
        state.current_round_pot = total
        txt_pot.value = f"Pula: {total} zł"
        txt_pot.update()

    def set_phase_info(msg: str, color: str = "grey_800"):
        txt_phase_info.value = msg
        txt_phase_info.color = color
        txt_phase_info.update()

    async def render_chat(chat_list: list[dict], players_state: list[dict]):
        if not chat_list: return
        
        last_msg = chat_list[-1]
        last_ts = last_msg.get("timestamp", 0)
        
        if last_ts == state.last_chat_timestamp:
            return 
            
        state.last_chat_timestamp = last_ts

        col_chat.controls.clear()
        admin_names = {p["name"] for p in players_state if p.get("is_admin")}

        for m in chat_list:
            player_name = m.get("player", "?")
            msg_text = m.get("message", "")
            is_bot = player_name == "BOT"
            spans = []

            if is_bot:
                is_question = msg_text.startswith("PYTANIE:")
                color = "#123499" if is_question else "black"
                weight = "bold" if is_question else "normal"
                spans.append(ft.TextSpan(f"BOT: {msg_text}", ft.TextStyle(color=color, weight=weight, size=12)))
            else:
                if player_name in admin_names:
                    spans.append(ft.TextSpan("[ADMIN] ", ft.TextStyle(color="red", weight="bold", size=12)))
                spans.append(ft.TextSpan(f"{player_name}: ", ft.TextStyle(color=name_to_color(player_name), weight="bold", size=12)))
                spans.append(ft.TextSpan(msg_text, ft.TextStyle(color="black", size=12)))

            col_chat.controls.append(ft.Text(spans=spans, selectable=True, size=12))
        col_chat.update()

    # ----------------- OBSŁUGA KOMEND ADMINA NA CZACIE -----------------

    processed_chat_ids = set()

    async def process_chat_commands(chat_list: list[dict], players_state: list[dict]):
        if state.set_name: return # Zestaw już wybrany
        admin_names = {p["name"] for p in players_state if p.get("is_admin")}
        if not admin_names: return

        for m in chat_list:
            ts = m.get("timestamp", 0.0)
            if ts in processed_chat_ids: continue
            processed_chat_ids.add(ts)

            player = m.get("player", "")
            msg = str(m.get("message", "")).strip()
            # Jeśli admin wpisze liczbę 1-50, wybierz zestaw
            if player in admin_names and re.fullmatch(r"0?\d{1,2}", msg):
                num = int(msg)
                if 1 <= num <= 50:
                    if state.is_admin:
                        await fetch_json(f"{BACKEND_URL}/select_set", "POST", {"player_id": state.player_id, "set_no": num})
                    state.set_name = str(num)
                    break

    # ----------------- EVENT HANDLERS (AKCJE GRACZA) -----------------

    async def on_join(e):
        name = txt_name.value.strip()
        if not name:
            txt_status.value = "Podaj ksywkę!"
            txt_status.update()
            return

        res = await fetch_json(f"{BACKEND_URL}/register", "POST", {"name": name})
        if not res or "id" not in res:
            txt_status.value = "Błąd serwera przy rejestracji."
            txt_status.update()
            return

        state.player_id = res["id"]
        state.player_name = res.get("name", name)
        state.is_admin = res.get("is_admin", False)
        state.joined = True
        state.local_money = 10000

        join_row.visible = False
        join_row.update()

        txt_chat_input.disabled = False
        btn_chat_send.disabled = False
        txt_chat_input.update()
        btn_chat_send.update()
        
        txt_status.value = f"Witaj {state.player_name}!" + (" (ADMIN)" if state.is_admin else "")
        txt_status.color = "green"
        txt_status.update()
        
        refresh_local_money()
        page.run_task(game_loop)

    async def on_send_chat(e):
        msg = txt_chat_input.value.strip()
        if not msg: return

        # Logika odpowiadania na pytanie:
        # Jeśli trwa faza answering_wait (czyli serwer czeka na odpowiedź zwycięzcy licytacji)
        if state.local_phase == "answering_wait":
             if state.player_id != state.answering_player_id:
                 # Inni gracze nie mogą odpowiadać za zwycięzcę
                 # Ale mogą pisać, tylko nie jako odpowiedź na pytanie (serwer i tak to odrzuci jako /answer)
                 pass 
             else:
                 # To jest zwycięzca licytacji -> wysyłamy jako ODPOWIEDŹ do backendu
                 # Backend zmieni fazę na "discussion" i wszyscy będą mogli komentować
                 await fetch_json(f"{BACKEND_URL}/answer", "POST", {"player_id": state.player_id, "answer": msg})
                 txt_chat_input.value = ""
                 txt_chat_input.focus()
                 txt_chat_input.update()
                 return

        # Zwykła wiadomość czatu (dyskusja lub luźne rozmowy)
        await fetch_json(f"{BACKEND_URL}/chat", "POST", {"player": state.player_name, "message": msg})
        txt_chat_input.value = ""
        txt_chat_input.focus()
        txt_chat_input.update()

    async def on_bid(e, kind="normal"):
        if not state.player_id: return
        cost = 100 if kind == "normal" else state.local_money
        
        if state.local_money < cost and kind == "normal":
            return 

        res = await fetch_json(f"{BACKEND_URL}/bid", "POST", {"player_id": state.player_id, "kind": kind})
        if res and res.get("status") == "ok":
            state.local_money = 0 if kind == "allin" else (state.local_money - cost)
            refresh_local_money()
            # Komunikat o licytacji
            msg_bid = f"Gracz {state.player_name} podbija stawkę o {cost} zł!" if kind == "normal" else f"Gracz {state.player_name} wchodzi VA BANQUE!"
            await send_bot_message(msg_bid)

    async def on_finish_bidding(e):
        if not state.player_id: return
        await fetch_json(f"{BACKEND_URL}/finish_bidding", "POST", {"player_id": state.player_id})
        txt_status.value = "Pasujesz."
        txt_status.update()
        await send_bot_message(f"Gracz {state.player_name} kończy licytację.")

    async def on_buy_hint(e, hint_type="abcd"):
        # Kupno podpowiedzi - wysyłamy request do backendu
        res = await fetch_json(f"{BACKEND_URL}/hint", "POST", {"player_id": state.player_id, "kind": hint_type})
        if res and res.get("status") == "ok":
            # Backend sam aktualizuje pulę i wysyła info na czat
            # My musimy tylko pobrać kasę lokalnie, żeby UI się zgadzało zanim przyjdzie update stanu
            pass 

    # ----------------- ADMIN LOGIC (VERDICT) -----------------

    async def detect_verdict_logic():
        # Ta funkcja jest teraz w dużej mierze obsługiwana przez backend w _auto_finalize_discussion_if_needed
        # Ale Admin może nadal potrzebować ręcznego wyzwolenia, jeśli automat zawiedzie.
        # W tej wersji kodu polegamy na backendzie (auto-advancement), 
        # więc ta funkcja jest "backupem" lub wyzwalaczem.
        pass

    # ----------------- GAME LOOP -----------------

    async def game_loop():
        while state.joined:
            try:
                data = await fetch_json(f"{BACKEND_URL}/state")
                if data:
                    state.current_round_id = data.get("round_id")
                    backend_phase = data.get("phase")
                    state.backend_pot = data.get("pot", 0)
                    
                    # Pobieranie info o pytaniu z backendu
                    current_idx = data.get("current_question_index", -1)
                    total_q = data.get("total_questions", 0) # Backend może to zwracać w "current_set" lub podobnym
                    # Uproszczenie: Backend zwraca 'current_question_index', liczymy od 0
                    if current_idx >= 0:
                        txt_round_info.value = f"Pytanie {current_idx + 1} / 50" # Zakładamy max 50, lub wyciągnij z response
                        txt_round_info.update()
                    else:
                        txt_round_info.value = "Oczekiwanie na start..."
                        txt_round_info.update()

                    txt_timer.value = f"Czas serwera: {int(data.get('time_left', 0))} s"
                    txt_timer.update()
                    
                    await render_chat(data.get("chat", []), data.get("players", []))
                    await process_chat_commands(data.get("chat", []), data.get("players", []))
                    refresh_pot_label()

                    # Heartbeat
                    my_p = next((p for p in data.get("players", []) if p["id"] == state.player_id), None)
                    if my_p:
                        state.is_admin = my_p.get("is_admin", False)
                        # Aktualizacja kasy z serwera (zabezpieczenie przed desynchronizacją)
                        if state.local_phase == "idle" or backend_phase == "bidding":
                            # Synchronizuj kasę tylko w bezpiecznych momentach, żeby nie skakała przy licytacji
                            # Ale jeśli jesteśmy w 2. turze, musimy mieć pewność że mamy kasę
                            state.local_money = my_p.get("money", 0)
                            refresh_local_money()

                        await fetch_json(f"{BACKEND_URL}/heartbeat", "POST", {"player_id": state.player_id})

                    now = time.time()

                    # ----------------------------------------------------
                    # SYNCHRONIZACJA FAZ LOKALNYCH Z BACKENDEM
                    # ----------------------------------------------------

                    # 1. FAZA: BIDDING (LICYTACJA)
                    if backend_phase == "bidding":
                        state.local_phase = "bidding"
                        set_phase_info("Trwa licytacja!", "blue")
                        
                        # Odblokowanie przycisków (Fix dla problemu w 2. turze)
                        if btn_bid.disabled:
                            btn_bid.disabled = False
                            btn_all_in.disabled = False
                            btn_finish_bidding.disabled = False
                            btn_bid.update()
                            btn_all_in.update()
                            btn_finish_bidding.update()

                        # Wykrycie nowej rundy po ID, aby nie pobrać wpisowego 100 razy
                        # Uwaga: Backend już pobrał wpisowe przy _start_new_bidding_round! 
                        # Więc lokalnie tylko synchronizujemy wyświetlanie, nie odejmujemy.

                    # 2. FAZA: ANSWERING (ODPOWIADANIE)
                    elif backend_phase == "answering":
                        state.local_phase = "answering_wait"
                        state.answering_player_id = data.get("answering_player_id")
                        
                        winner_name = "?"
                        for p in data.get("players", []):
                            if p["id"] == state.answering_player_id:
                                winner_name = p["name"]
                                break
                        state.answering_player_name = winner_name
                        
                        set_phase_info(f"Odpowiada: {winner_name}", "green")

                        # Zablokowanie licytacji
                        if not btn_bid.disabled:
                            btn_bid.disabled = True
                            btn_all_in.disabled = True
                            btn_finish_bidding.disabled = True
                            btn_bid.update()
                            btn_all_in.update()
                            btn_finish_bidding.update()

                        # Odblokowanie podpowiedzi TYLKO dla zwycięzcy
                        im_winner = (state.player_id == state.answering_player_id)
                        if btn_hint_abcd.disabled == im_winner: # Jeśli stan się nie zgadza
                            btn_hint_abcd.disabled = not im_winner
                            btn_hint_5050.disabled = not im_winner
                            btn_hint_abcd.update()
                            btn_hint_5050.update()

                        # Fix podwójnych pytań: Backend wysyła pytanie na czat, my tu nic nie wysyłamy.

                    # 3. FAZA: DISCUSSION (DYSKUSJA - po udzieleniu odpowiedzi)
                    elif backend_phase == "discussion":
                        state.local_phase = "discussion"
                        set_phase_info("Dyskusja... (wszyscy mogą pisać)", "orange")
                        
                        # Tutaj czat jest odblokowany dla wszystkich (obsługiwane w on_send_chat)
                        # Podpowiedzi zablokowane
                        btn_hint_abcd.disabled = True
                        btn_hint_5050.disabled = True
                        btn_hint_abcd.update()
                        btn_hint_5050.update()

                    # 4. FAZA: IDLE / FINISHED
                    else:
                        state.local_phase = "idle"
                        set_phase_info("Czekanie na kolejną rundę...", "grey")
                        btn_bid.disabled = True
                        btn_all_in.disabled = True
                        btn_finish_bidding.disabled = True
                        btn_bid.update()
                        btn_all_in.update()
                        btn_finish_bidding.update()

            except Exception as loop_ex:
                print(f"Loop error: {loop_ex}")
            
            await asyncio.sleep(1.0)

    # --- BINDOWANIE AKCJI ---
    btn_join.on_click = on_join
    txt_name.on_submit = on_join
    txt_chat_input.on_submit = on_send_chat
    btn_chat_send.on_click = on_send_chat
    
    btn_bid.on_click = lambda e: page.run_task(on_bid, "normal")
    btn_all_in.on_click = lambda e: page.run_task(on_bid, "allin")
    btn_finish_bidding.on_click = lambda e: page.run_task(on_finish_bidding)
    btn_hint_abcd.on_click = lambda e: page.run_task(on_buy_hint, "abcd")
    btn_hint_5050.on_click = lambda e: page.run_task(on_buy_hint, "5050")

    page.update()

if __name__ == "__main__":
    ft.app(target=main)
