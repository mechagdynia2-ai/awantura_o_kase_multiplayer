import flet as ft
import asyncio
import json
import re
from dataclasses import dataclass

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

    # --- Elementy UI (zoptymalizowane pod mobile) ---
    
    txt_money = ft.Text("Kasa: ---", color="green", weight="bold")
    txt_pot = ft.Text("Pula: 0 zł", color="purple", weight="bold")
    txt_timer = ft.Text("-- s", size=16, weight="bold")
    
    # Czat (zwiększony, bo usunęliśmy nagłówki)
    chat_col = ft.Column(scroll="auto", auto_scroll=True, height=300)
    input_chat = ft.TextField(hint_text="Wiadomość...", expand=True, disabled=True, dense=True)
    btn_send = ft.FilledButton("Wyślij", disabled=True)
    
    # Przyciski licytacji
    btn_bid = ft.FilledButton("+100", disabled=True, expand=1)
    btn_pass = ft.ElevatedButton("Pas", disabled=True, expand=1)
    btn_allin = ft.FilledButton("VA BANQUE", style=ft.ButtonStyle(bgcolor="red"), disabled=True, expand=1)
    
    # Przyciski podpowiedzi
    btn_abcd = ft.OutlinedButton("ABCD", disabled=True, expand=1)
    btn_5050 = ft.OutlinedButton("50/50", disabled=True, expand=1)
    
    # Login
    input_name = ft.TextField(label="Nick", expand=True)
    btn_join = ft.FilledButton("Dołącz")
    row_login = ft.Row([input_name, btn_join], alignment="center")

    layout = ft.Column([
        ft.Text("AWANTURA O KASĘ", size=20, weight="bold", text_align="center"),
        row_login,
        ft.Divider(height=5),
        
        # Pasek statusu
        ft.Row([txt_money, txt_pot, txt_timer], alignment="spaceBetween"),
        
        # Czat
        ft.Container(
            content=ft.Column([chat_col, ft.Row([input_chat, btn_send])]),
            border=ft.border.all(1, "grey"), border_radius=10, padding=5,
            expand=True # Rozciągnij czat, żeby zajął dostępne miejsce
        ),
        
        # Sekcja przycisków (ciasno upakowana)
        ft.Row([btn_abcd, btn_5050], spacing=5),
        ft.Row([btn_bid, btn_pass, btn_allin], spacing=5)
    ], spacing=5, expand=True) # Cały layout rozciągliwy
    
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
            
            chat_col.controls.append(ft.Text(f"{p}: {m}", color=color, selectable=True, size=12))
        chat_col.update()

    async def game_loop():
        while state.joined:
            try:
                data = await fetch_json(f"{BACKEND_URL}/state")
                if data:
                    # Czas i Pula
                    txt_timer.value = f"{int(data.get('time_left',0))} s"
                    pot_val = data.get("pot", 0)
                    
                    # Fazy
                    phase = data.get("phase")
                    state.local_phase = phase
                    state.answering_player_id = data.get("answering_player_id")
                    state.server_abcd_bought = data.get("abcd_bought", False)

                    # Update gracza
                    me = next((p for p in data.get("players",[]) if p["id"] == state.player_id), None)
                    if me:
                        state.is_admin = me.get("is_admin", False)
                        state.local_money = me.get("money", 0)
                        txt_money.value = f"{state.local_money} zł"
                        txt_pot.value = f"Pula: {pot_val} zł"
                        await fetch_json(f"{BACKEND_URL}/heartbeat", "POST", {"player_id": state.player_id})

                    # Przyciski
                    is_my_turn_ans = (phase == "answering" and state.answering_player_id == state.player_id)
                    is_bidding = (phase == "bidding")
                    
                    btn_bid.disabled = not is_bidding
                    btn_pass.disabled = not is_bidding
                    btn_allin.disabled = not is_bidding
                    
                    btn_abcd.disabled = not is_my_turn_ans
                    btn_5050.disabled = not (is_my_turn_ans and state.server_abcd_bought)

                    page.update()
                    await render_chat(data.get("chat", []))
                    
                    # Komendy admina
                    if state.is_admin and phase == "idle":
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
        
        if state.local_phase == "answering" and state.answering_player_id == state.player_id:
            await fetch_json(f"{BACKEND_URL}/answer", "POST", {"player_id": state.player_id, "answer": msg})
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
