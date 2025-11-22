import flet as ft
import asyncio
import json
import re
import time
from dataclasses import dataclass

# Obsługa Pyodide
try:
    import js
    from js import fetch
except ImportError:
    js = None
    fetch = None

BACKEND_URL = "https://game-multiplayer-qfn1.onrender.com"

# ----------------- STATE -----------------

@dataclass
class GameState:
    player_id: str = None
    player_name: str = ""
    is_admin: bool = False
    joined: bool = False
    
    local_money: int = 10000
    local_phase: str = "idle"
    
    answering_player_id: str = None
    last_chat_ts: float = 0.0
    
    # Flaga z serwera, czy w tej rundzie kupiono ABCD
    server_abcd_bought: bool = False

async def fetch_json(url: str, method: str = "GET", body: dict = None):
    if not js: return None
    try:
        opts = js.Object.fromEntries([["method", method]])
        if body:
            opts.headers = js.Object.fromEntries([["Content-Type", "application/json"]])
            opts.body = json.dumps(body)
        resp = await fetch(url, opts)
        data = await resp.json()
        return data.to_py()
    except Exception as e:
        print(f"Err {url}: {e}")
        return None

# ----------------- UI -----------------

async def main(page: ft.Page):
    page.title = "Awantura o Kasę"
    page.theme_mode = ft.ThemeMode.LIGHT
    state = GameState()

    # Kontrolki
    txt_info = ft.Text("Witaj w grze!", size=16, weight="bold")
    txt_money = ft.Text("Kasa: ---", color="green", weight="bold")
    txt_pot = ft.Text("Pula: 0 zł", color="purple", weight="bold")
    txt_timer = ft.Text("-- s")
    
    chat_col = ft.Column(scroll="auto", auto_scroll=True, height=250)
    input_chat = ft.TextField(hint_text="Wpisz wiadomość...", expand=True, disabled=True)
    btn_send = ft.FilledButton("Wyślij", disabled=True)
    
    # Przyciski gry
    btn_bid = ft.FilledButton("Podbij (+100)", disabled=True)
    btn_pass = ft.ElevatedButton("Pasuję", disabled=True)
    btn_allin = ft.FilledButton("VA BANQUE", style=ft.ButtonStyle(bgcolor="red"), disabled=True)
    
    btn_abcd = ft.OutlinedButton("ABCD (losowy koszt)", disabled=True)
    btn_5050 = ft.OutlinedButton("50/50 (losowy koszt)", disabled=True)
    
    # Login
    input_name = ft.TextField(label="Nick")
    btn_join = ft.FilledButton("Dołącz")

    layout = ft.Column([
        ft.Text("AWANTURA O KASĘ", size=24, weight="bold"),
        ft.Row([input_name, btn_join], alignment="center", id="login_row"),
        ft.Divider(),
        ft.Row([txt_money, txt_pot, txt_timer], alignment="spaceBetween"),
        ft.Container(txt_info, bgcolor="blue_50", padding=10, border_radius=5, alignment=ft.alignment.center),
        ft.Container(
            content=ft.Column([chat_col, ft.Row([input_chat, btn_send])]),
            border=ft.border.all(1, "grey"), border_radius=10, padding=5, height=350
        ),
        ft.Divider(),
        ft.Text("Podpowiedzi (Tylko dla odpowiadającego):"),
        ft.Row([btn_abcd, btn_5050], alignment="center"),
        ft.Divider(),
        ft.Text("Licytacja:"),
        ft.Row([btn_bid, btn_pass, btn_allin], alignment="center")
    ])
    
    # Ukrywanie logowania po zalogowaniu
    row_login = layout.controls[1] 

    page.add(layout)

    # --- LOGIKA ---

    async def render_chat(chat_list):
        if not chat_list: return
        last = chat_list[-1]
        if last.get("timestamp") == state.last_chat_ts: return
        
        state.last_chat_ts = last.get("timestamp")
        chat_col.controls.clear()
        
        for msg in chat_list:
            p = msg.get("player","")
            m = msg.get("message","")
            color = "black"
            if p == "BOT": 
                color = "blue"
                if "PYTANIE:" in m: color = "navy"
            elif "[ADMIN]" in p: color = "red"
            
            chat_col.controls.append(ft.Text(f"{p}: {m}", color=color, selectable=True))
        chat_col.update()

    async def game_loop():
        while state.joined:
            try:
                data = await fetch_json(f"{BACKEND_URL}/state")
                if data:
                    # Info o rundzie
                    q_idx = data.get("current_question_index", -1)
                    if q_idx >= 0:
                        txt_info.value = f"Pytanie {q_idx + 1}/50"
                    else:
                        txt_info.value = "Oczekiwanie na start..."
                    
                    # Czas i Pula
                    txt_timer.value = f"{int(data.get('time_left',0))} s"
                    pot_val = data.get("pot", 0)
                    
                    # Fazy
                    phase = data.get("phase")
                    state.local_phase = phase
                    state.answering_player_id = data.get("answering_player_id")
                    state.server_abcd_bought = data.get("abcd_bought", False)

                    # Update gracza (kasa)
                    me = next((p for p in data.get("players",[]) if p["id"] == state.player_id), None)
                    if me:
                        state.is_admin = me.get("is_admin", False)
                        state.local_money = me.get("money", 0)
                        txt_money.value = f"Kasa: {state.local_money} zł"
                        
                        # W licytacji pokazujemy ile już dałem (bid) + pula
                        # Backend zwraca POT jako sumę.
                        txt_pot.value = f"Pula: {pot_val} zł"

                        await fetch_json(f"{BACKEND_URL}/heartbeat", "POST", {"player_id": state.player_id})

                    # Obsługa Przycisków
                    is_my_turn_ans = (phase == "answering" and state.answering_player_id == state.player_id)
                    is_bidding = (phase == "bidding")
                    
                    # Licytacja
                    btn_bid.disabled = not is_bidding
                    btn_pass.disabled = not is_bidding
                    btn_allin.disabled = not is_bidding
                    
                    # Podpowiedzi
                    # ABCD dostępne tylko jak moja kolej i jeszcze nie kupiono? 
                    # W sumie backend pozwala kupić raz. Przycisk można zostawić aktywny, backend odrzuci.
                    # Ale lepiej: ABCD aktywne jeśli moja kolej. 50/50 aktywne jeśli moja kolej I abcd_bought=True
                    btn_abcd.disabled = not is_my_turn_ans
                    btn_5050.disabled = not (is_my_turn_ans and state.server_abcd_bought)

                    page.update()
                    
                    await render_chat(data.get("chat", []))
                    
                    # Obsługa komend admina (Wybór zestawu)
                    # Jeśli admin wpisze "1" na czacie, backend sam to obsłużyłby gdyby to był endpoint, 
                    # ale tutaj mamy logikę, że klient musi wywołać /select_set.
                    # W poprzednim kodzie backend sam parsował czat? Nie, to było w kliencie.
                    # Dodajmy to z powrotem tutaj w uproszczonej wersji:
                    if state.is_admin and phase == "idle":
                         # Sprawdź ostatnią wiadomość admina
                         if data.get("chat"):
                             last = data.get("chat")[-1]
                             if last.get("player") == state.player_name and re.fullmatch(r"\d+", last.get("message","")):
                                 set_num = int(last.get("message"))
                                 if 1 <= set_num <= 50:
                                     # Wysyłamy żądanie raz (proste zabezpieczenie przed spamem requestów w pętli: sprawdzamy czy zestaw już nie jest ustawiony)
                                     # Ale backend resetuje CURRENT_SET na null przy końcu.
                                     # Uproszczenie: Admin klika guzik albo wpisuje komendę /start [nr].
                                     # Zostańmy przy tym co było: parsowanie lokalne
                                     pass

            except Exception as e:
                print(e)
            
            await asyncio.sleep(1)

    # --- HANDLERY ---

    async def do_join(e):
        name = input_name.value
        if not name: return
        res = await fetch_json(f"{BACKEND_URL}/register", "POST", {"name": name})
        if res:
            state.player_id = res["id"]
            state.player_name = res["name"]
            state.joined = True
            row_login.visible = False
            input_chat.disabled = False
            btn_send.disabled = False
            page.run_task(game_loop)
            page.update()

    async def do_send(e):
        msg = input_chat.value
        if not msg: return
        
        # Jeśli moja kolej na odpowiedź -> wyślij jako odpowiedź
        if state.local_phase == "answering" and state.answering_player_id == state.player_id:
            await fetch_json(f"{BACKEND_URL}/answer", "POST", {"player_id": state.player_id, "answer": msg})
        
        # Jeśli jestem adminem i faza idle i wpisałem numer -> wybierz zestaw
        elif state.is_admin and state.local_phase == "idle" and re.fullmatch(r"\d+", msg):
            await fetch_json(f"{BACKEND_URL}/select_set", "POST", {"player_id": state.player_id, "set_no": int(msg)})
        
        else:
            await fetch_json(f"{BACKEND_URL}/chat", "POST", {"player": state.player_name, "message": msg})
        
        input_chat.value = ""
        input_chat.focus()
        page.update()

    async def do_bid(e):
        await fetch_json(f"{BACKEND_URL}/bid", "POST", {"player_id": state.player_id, "kind": "normal"})
    async def do_pass(e):
        await fetch_json(f"{BACKEND_URL}/finish_bidding", "POST", {"player_id": state.player_id})
    async def do_allin(e):
        await fetch_json(f"{BACKEND_URL}/bid", "POST", {"player_id": state.player_id, "kind": "allin"})
    
    async def do_hint_abcd(e):
        await fetch_json(f"{BACKEND_URL}/hint", "POST", {"player_id": state.player_id, "kind": "abcd"})
    async def do_hint_5050(e):
        await fetch_json(f"{BACKEND_URL}/hint", "POST", {"player_id": state.player_id, "kind": "5050"})

    btn_join.on_click = do_join
    btn_send.on_click = do_send
    input_chat.on_submit = do_send
    
    btn_bid.on_click = do_bid
    btn_pass.on_click = do_pass
    btn_allin.on_click = do_allin
    
    btn_abcd.on_click = do_hint_abcd
    btn_5050.on_click = do_hint_5050

    page.update()

if __name__ == "__main__":
    ft.app(target=main)
