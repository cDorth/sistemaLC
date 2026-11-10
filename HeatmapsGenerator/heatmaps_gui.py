"""
Interface gráfica para geração de heatmaps.

Integrada com a estrutura modular refatorada.
VERSÃO CORRIGIDA: Remove input() para funcionar como executável GUI.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, date, timedelta
from pathlib import Path
from tkcalendar import DateEntry
from project_heatmaps.config import CONTRACTS, get_contract_city_mapping
from project_heatmaps.main import process_all_contracts
from project_heatmaps.estacionarios import get_required_vehicle_types
from project_heatmaps.geo_processing import diagnose_gps_db

def _get_icon_path():
    """Procura logo_DIE.ico em dependencias/, subindo a árvore de diretórios."""
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            p = (Path(meipass) / 'logo_DIE.ico')
            if p.exists():
                return str(p)
        start = Path(sys.executable).parent
    else:
        start = Path(__file__).resolve().parent
    for directory in ([start] + list(start.parents)[:6]):
        for candidate in (((directory / 'dependencias') / 'logo_DIE.ico'), (directory / 'logo_DIE.ico')):
            if candidate.exists():
                return str(candidate)

def show_date_picker(default_from, default_to, master=None):
    """
    Abre a janela Tkinter para escolher as datas e o modo.

    Returns:
        Tupla (date_from, date_to, single_day, historic_only,
               day_and_night, selected_contracts, generate_mobile,
               stops_mode, generate_report, secure_mode) ou None se cancelar
    """
    try:
        import tkinter as tk
        from tkinter import ttk
        from tkcalendar import DateEntry
    except Exception as e:
        print(f"Erro ao carregar interface gráfica: {e}")
        return None
    today = date.today()
    WIDTH, HEIGHT = (460, 580)
    BORDER_THICK = 8
    TITLE_HEIGHT = 28
    title_font = ('Segoe UI', 11, 'bold')
    lbl_font = ('Segoe UI', 10)
    btn_font = ('Segoe UI', 11, 'bold')
    btn_small_font = ('Segoe UI', 9)
    entry_font = ('Segoe UI', 10)
    root = tk.Toplevel(master) if master is not None else tk.Tk()
    root.title('Gerar Mapas de Execução')
    try:
        icon_path = _get_icon_path()
        if icon_path:
            root.iconbitmap(icon_path)
    except Exception:
        pass
    root.overrideredirect(False)
    root.geometry(f"{WIDTH}x{HEIGHT}")
    root.resizable(False, False)
    root.configure(bg='#000000')
    outer = tk.Frame(root, bg='#000000', bd=0, highlightthickness=BORDER_THICK, highlightbackground='#ff0000', highlightcolor='#ff0000')
    outer.pack(fill='both', expand=True)
    inner = tk.Frame(outer, bg='#000000')
    inner.pack(fill='both', expand=True)
    inner.grid_columnconfigure(0, weight=0)
    inner.grid_columnconfigure(1, weight=1)
    contracts_frame = tk.Frame(inner, bg='#000000', width=160)
    contracts_frame.grid(row=0, column=0, sticky='n', padx=12, pady=6)
    lbl_contracts = tk.Label(contracts_frame, text='Seleção de Contratos:', bg='#000000', fg='#ffffff', font=title_font)
    lbl_contracts.pack(anchor='center', pady=(0, 8))
    contracts_list = tk.Frame(contracts_frame, bg='#000000')
    contracts_list.pack(anchor='center')
    contract_vars = {c: tk.BooleanVar(value=False) for c in CONTRACTS}
    var_all = tk.BooleanVar(value=False)
    def on_toggle_all():
        val = var_all.get()
        for v in contract_vars.values():
            v.set(val)

    chk_all = tk.Checkbutton(contracts_list, text='TODOS', variable=var_all, bg='#000000', fg='#ffffff', selectcolor='#222222', font=('Segoe UI', 9, 'bold'), command=on_toggle_all)
    chk_all.pack(anchor='w')
    def on_toggle_contract():
        if all((v.get() for v in contract_vars.values())):
            var_all.set(True)
            return None
        var_all.set(False)

    for c in CONTRACTS:
        chk = tk.Checkbutton(contracts_list, text=c, variable=contract_vars[c], bg='#000000', fg='#ffffff', selectcolor='#222222', font=('Segoe UI', 9), command=on_toggle_contract)
        chk.pack(anchor='w')
    right_frame = tk.Frame(inner, bg='#000000')
    right_frame.grid(row=0, column=1, sticky='nsew', padx=(3, 6), pady=6)
    title_bar_right = tk.Frame(right_frame, bg='#000000', height=TITLE_HEIGHT)
    title_bar_right.pack(fill='x', side='top')
    title_lbl = tk.Label(title_bar_right, text='Seleção de Datas e Modos:', bg='#000000', fg='#ffffff', font=title_font)
    title_lbl.pack(anchor='center', pady=(0, 4))
    chk_day_night_var = tk.BooleanVar(value=True)
    chk_day_night = tk.Checkbutton(right_frame, text='🌓 Gerar Diurno e Noturno', variable=chk_day_night_var, bg='#000000', fg='#ffffff', selectcolor='#222222', font=lbl_font)
    chk_day_night.pack(anchor='center', pady=(2, 6))
    chk_mobile_var = tk.BooleanVar(value=True)
    chk_mobile = tk.Checkbutton(right_frame, text='📱 Gerar versão mobile (simplificada)', variable=chk_mobile_var, bg='#000000', fg='#ffffff', selectcolor='#222222', font=lbl_font)
    chk_mobile.pack(anchor='center', pady=(2, 6))
    lbl_msg = tk.Label(right_frame, text='Escolha sua data inicial:', bg='#000000', fg='#ffffff', font=lbl_font, anchor='w')
    lbl_msg.pack(fill='x', padx=10, pady=(4, 2))
    content = tk.Frame(right_frame, bg='#000000')
    content.pack(fill='both', expand=True, padx=10)
    dateentry_kwargs = {'date_pattern': 'dd.mm.yyyy', 'width': 12, 'locale': 'pt_BR', 'firstweekday': 'sunday', 'showweeknumbers': False}
    var_start = tk.StringVar()
    var_end = tk.StringVar()
    content.grid_columnconfigure(0, weight=0)
    content.grid_columnconfigure(1, weight=0)
    content.grid_columnconfigure(2, minsize=160)
    lbl_initial = tk.Label(content, text='Inicial:', bg='#000000', fg='#ffffff', font=lbl_font)
    lbl_initial.grid(row=0, column=0, sticky='w', pady=(4, 2))
    start_entry = DateEntry(content, textvariable=var_start, **dateentry_kwargs, maxdate=today)
    start_entry.grid(row=1, column=0, sticky='w', pady=(0, 6))
    start_entry.configure(font=entry_font)
    frame_buttons_start = tk.Frame(content, bg='#000000')
    frame_buttons_start.grid(row=1, column=1, sticky='w', padx=(8, 0))
    btn_apply_start = tk.Button(frame_buttons_start, text='Aplicar', bg='#222222', fg='#fff', font=btn_small_font, relief='flat')
    btn_apply_start.pack(side='left', padx=(0, 6))
    btn_clear_start = tk.Button(frame_buttons_start, text='Limpar', bg='#222222', fg='#fff', font=btn_small_font, relief='flat')
    btn_clear_start.pack(side='left')
    lbl_final = tk.Label(content, text='Final:', bg='#000000', fg='#ffffff', font=lbl_font)
    lbl_final.grid(row=2, column=0, sticky='w', pady=(4, 2))
    end_entry = DateEntry(content, textvariable=var_end, **dateentry_kwargs, state='disabled')
    end_entry.grid(row=3, column=0, sticky='w', pady=(0, 6))
    end_entry.configure(font=entry_font)
    frame_buttons_end = tk.Frame(content, bg='#000000')
    frame_buttons_end.grid(row=3, column=1, sticky='w', padx=(8, 0))
    btn_apply_end = tk.Button(frame_buttons_end, text='Aplicar', bg='#222222', fg='#fff', font=btn_small_font, relief='flat', state='disabled')
    btn_apply_end.pack(side='left', padx=(0, 6))
    btn_clear_end = tk.Button(frame_buttons_end, text='Limpar', bg='#222222', fg='#fff', font=btn_small_font, relief='flat', state='disabled')
    btn_clear_end.pack(side='left')
    modes_frame = tk.Frame(content, bg='#000000')
    modes_frame.grid(row=4, column=0, columnspan=2, pady=(8, 0))
    chk_hist_var = tk.BooleanVar(value=False)
    chk_hist = tk.Checkbutton(modes_frame, text='📊 Gerar apenas histórico', variable=chk_hist_var, bg='#000000', fg='#ffffff', selectcolor='#222222', font=lbl_font, state='disabled')
    chk_hist.grid(row=4, column=0, columnspan=3, sticky='w', pady=(6, 0))
    chk_var = tk.BooleanVar(value=False)
    chk = tk.Checkbutton(modes_frame, text='⏰ Gerar apenas um dia', variable=chk_var, bg='#000000', fg='#ffffff', selectcolor='#222222', font=lbl_font, state='disabled')
    chk.grid(row=5, column=0, columnspan=3, sticky='w', pady=(6, 0))
    chk_stops_var = tk.BooleanVar(value=False)
    chk_stops = tk.Checkbutton(modes_frame, text='🛑 Mapa de paradas críticas', variable=chk_stops_var, bg='#000000', fg='#ffffff', selectcolor='#222222', font=lbl_font, state='disabled')
    chk_stops.grid(row=6, column=0, columnspan=3, sticky='w', pady=(6, 0))
    chk_report_var = tk.BooleanVar(value=False)
    chk_report = tk.Checkbutton(modes_frame, text='📄 Gerar relatório de análise', variable=chk_report_var, bg='#000000', fg='#ffffff', selectcolor='#222222', font=lbl_font)
    chk_report.grid(row=7, column=0, columnspan=3, sticky='w', pady=(6, 0))
    chk_secure_var = tk.BooleanVar(value=False)
    chk_secure = tk.Checkbutton(modes_frame, text='🔒 Dados Seguros', variable=chk_secure_var, bg='#000000', fg='#ffffff', selectcolor='#222222', font=lbl_font)
    chk_secure.grid(row=8, column=0, columnspan=3, sticky='w', pady=(6, 0))
    chk_split_type_var = tk.BooleanVar(value=False)
    chk_split_type = tk.Checkbutton(modes_frame, text='🗂️ Separar por tipo de coleta', variable=chk_split_type_var, bg='#000000', fg='#ffffff', selectcolor='#222222', font=lbl_font)
    chk_split_type.grid(row=9, column=0, columnspan=3, sticky='w', pady=(6, 0))
    chk_estac_var = tk.BooleanVar(value=False)
    chk_estac = tk.Checkbutton(modes_frame, text='📦 Mapa de Estacionários', variable=chk_estac_var, bg='#000000', fg='#ffffff', selectcolor='#222222', font=lbl_font)
    chk_estac.grid(row=10, column=0, columnspan=3, sticky='w', pady=(6, 0))
    footer_frame = tk.Frame(inner, bg='#000000')
    footer_frame.grid(row=1, column=0, columnspan=2, sticky='ew', padx=6, pady=(6, 10))
    footer_frame.grid_columnconfigure(0, weight=1)
    footer_inner = tk.Frame(footer_frame, bg='#000000')
    footer_inner.pack(anchor='center')
    maps_btn = tk.Button(footer_inner, text='Maps Time!', bg='#006400', fg='#ffffff', font=btn_font, relief='flat', state='disabled', width=14, padx=10, pady=6)
    cancel_btn = tk.Button(footer_inner, text='Cancelar', bg='#333333', fg='#ffffff', font=btn_font, relief='flat', width=14, padx=10, pady=6)
    maps_btn.pack(side='left', padx=16)
    cancel_btn.pack(side='left', padx=16)
    STATE = {'mode': 'INIT', 'start': None, 'end': None, 'single': False}
    message_after_id = {'id': None}
    def STATE_message():
        if STATE['mode'] == 'INIT':
            return 'Escolha sua data inicial:'
        if STATE['mode'] == 'START_SELECTED':
            return 'Escolha sua data final:'
        return 'Pronto para gerar os mapas!'

    def set_border(col_hex):
        outer.config(highlightbackground=col_hex, highlightcolor=col_hex)

    def show_message_once(txt, timeout=1400):
        try:
            if message_after_id['id'] is not None:
                root.after_cancel(message_after_id['id'])
                message_after_id['id'] = None
        except Exception:
            pass
        lbl_msg.config(text=txt)
        try:
            message_after_id['id'] = root.after(timeout, (lambda: lbl_msg.config(text=STATE_message())))
        except Exception:
            message_after_id['id'] = None

    def parse_var_date(var):
        s = var.get().strip()
        if not s:
            return None
        try:
            return datetime.strptime(s, '%d.%m.%Y').date()
        except Exception:
            return None

    def to_init():
        STATE.update({'mode': 'INIT', 'start': None, 'end': None, 'single': False})
        var_start.set('')
        var_end.set('')
        start_entry.config(state='normal')
        end_entry.config(state='disabled')
        btn_apply_start.config(state='normal')
        btn_apply_end.config(state='disabled')
        btn_clear_start.config(state='normal')
        btn_clear_end.config(state='disabled')
        chk_hist_var.set(False)
        chk_hist.config(state='disabled')
        chk_var.set(False)
        chk.config(state='disabled')
        chk_stops_var.set(False)
        chk_stops.config(state='disabled')
        set_border('#ff0000')
        lbl_msg.config(text=STATE_message())
        maps_btn.config(state='disabled', bg='#555555')
        root.update_idletasks()

    def to_start_selected(dt_start):
        STATE['mode'] = 'START_SELECTED'
        STATE['start'] = dt_start
        STATE['end'] = None
        STATE['single'] = False
        min_end = (dt_start + timedelta(days=1))
        try:
            end_entry.config(state='normal', mindate=min_end, maxdate=today)
        except Exception:
            end_entry.config(state='normal')
        var_start.set(dt_start.strftime('%d.%m.%Y'))
        var_end.set('')
        start_entry.config(state='normal')
        btn_apply_start.config(state='normal')
        btn_clear_start.config(state='normal')
        if dt_start == today:
            end_entry.config(state='disabled')
            btn_apply_end.config(state='disabled')
            btn_clear_end.config(state='disabled')
            chk_hist_var.set(False)
            chk_hist.config(state='disabled')
            chk.config(state='normal')
            chk_stops.config(state='normal')
            lbl_msg.config(text="Escolha sua data final: (somente 'Gerar apenas um dia' disponível para hoje)")
        else:
            btn_apply_end.config(state='normal')
            btn_clear_end.config(state='normal')
            chk_hist_var.set(False)
            chk_hist.config(state='disabled')
            chk.config(state='normal')
            chk_stops.config(state='normal')
            lbl_msg.config(text=STATE_message())
        set_border('#ffff00')
        maps_btn.config(state='disabled', bg='#555555')
        root.update_idletasks()

    def to_ready(dt_end, single_mode):
        STATE['mode'] = 'READY'
        if STATE.get('start') is None:
            STATE['start'] = dt_end
            var_start.set(dt_end.strftime('%d.%m.%Y'))
        STATE['end'] = dt_end
        STATE['single'] = bool(single_mode)
        var_end.set(dt_end.strftime('%d.%m.%Y'))
        start_entry.config(state='disabled')
        btn_apply_start.config(state='disabled')
        btn_clear_start.config(state='disabled')
        if single_mode:
            end_entry.config(state='disabled')
            btn_apply_end.config(state='disabled')
            btn_clear_end.config(state='disabled')
            chk_hist_var.set(False)
            chk_hist.config(state='disabled')
            chk_var.set(True)
            chk.config(state='normal')
            chk_stops_var.set(False)
            chk_stops.config(state='disabled')
        else:
            end_entry.config(state='normal')
            try:
                end_entry.config(maxdate=today)
            except Exception:
                pass
            btn_apply_end.config(state='normal')
            btn_clear_end.config(state='normal')
            chk_hist.config(state='normal')
            chk_var.set(False)
            chk.config(state='disabled')
            chk_stops.config(state='normal')
        set_border('#00ff00')
        lbl_msg.config(text=STATE_message())
        maps_btn.config(state='normal', bg='#006400')
        root.update_idletasks()

    def apply_start():
        sel = parse_var_date(var_start)
        if sel is None:
            show_message_once('Data inicial inválida.')
            return None
        if sel > today:
            var_start.set('')
            show_message_once('Data inicial não pode ser posterior à data atual.')
            return None
        to_start_selected(sel)

    def apply_end():
        sel = parse_var_date(var_end)
        if sel is None:
            show_message_once('Data final inválida.')
            return None
        st = STATE.get('start')
        if st is not None and sel <= st:
            var_end.set('')
            show_message_once('Data final deve ser posterior à data inicial.')
            return None
        if sel > today:
            var_end.set('')
            show_message_once('Data final não pode ser posterior à data atual.')
            return None
        to_ready(sel, single_mode=False)

    REPORT_LABEL_NORMAL = '📄 Gerar relatório de análise'
    REPORT_LABEL_HIST = '📄 Gerar relatório de análise (XML dia a dia)'
    def _sync_report_label():
        txt = REPORT_LABEL_HIST if chk_hist_var.get() else REPORT_LABEL_NORMAL
        chk_report.config(text=txt)

    def on_chk_hist_toggle():
        if chk_hist_var.get():
            chk_var.set(False)
            chk.config(state='disabled')
        else:
            chk.config(state='normal')
        _sync_report_label()

    def on_chk_toggle():
        if chk_var.get():
            st = STATE.get('start')
            if st is None:
                chk_var.set(False)
                return None
            chk_stops_var.set(False)
            chk_stops.config(state='disabled')
            to_ready(st, single_mode=True)
            return None
        if STATE['mode'] == 'READY' and STATE.get('single'):
            to_start_selected(STATE['start'])
            return None
            return None

    def on_chk_stops_toggle():
        if chk_stops_var.get():
            chk_var.set(False)
            chk.config(state='disabled')
            chk_estac_var.set(False)
            return None
        if not chk_hist_var.get():
            chk.config(state='normal')
            return None

    def on_chk_estac_toggle():
        if chk_estac_var.get():
            chk_stops_var.set(False)
            return None

    def clear_start():
        if STATE['mode'] == 'READY':
            return None
        var_start.set('')
        to_init()

    def clear_end():
        if STATE['mode'] == 'READY' and STATE.get('single'):
            return None
        if STATE['mode'] == 'READY':
            chk_hist_var.set(False)
            chk_hist.config(state='disabled')
            chk_stops_var.set(False)
            to_start_selected(STATE['start'])
            return None
        var_end.set('')
        try:
            end_entry.set_date('')
        except Exception:
            pass
        end_entry.config(state='disabled')
        btn_apply_end.config(state='disabled')
        btn_clear_end.config(state='disabled')
        if STATE.get('start'):
            chk_hist_var.set(False)
            chk_hist.config(state='disabled')
            to_start_selected(STATE['start'])
            return None

    def ok_action():
        """Ação ao clicar em 'Maps Time!'"""
        selected_contracts = [c for c, v in contract_vars.items() if v.get()]
        if not selected_contracts:
            show_message_once('Selecione ao menos um contrato.')
            return None
        s = parse_var_date(var_start)
        if s is None:
            root.result = None
            root.destroy()
            return None
        e = (parse_var_date(var_end) or s)
        if not chk_var.get() and e <= s:
            show_message_once('Data final deve ser posterior à data inicial.')
            return None
        if e > today:
            show_message_once('Data final não pode ser posterior à data atual.')
            return None
        if e < s:
            s, e = e, s
        root.result = (s, e, bool(chk_var.get()), bool(chk_hist_var.get()), bool(chk_day_night_var.get()), selected_contracts, bool(chk_mobile_var.get()), bool(chk_stops_var.get()), bool(chk_report_var.get()), bool(chk_secure_var.get()), bool(chk_split_type_var.get()), bool(chk_estac_var.get()))
        root.destroy()

    def cancel_action():
        root.result = None
        root.destroy()

    try:
        chk_hist_var.trace_add('write', (lambda *_: _sync_report_label()))
    except AttributeError:
        chk_hist_var.trace('w', (lambda *_: _sync_report_label()))
    _sync_report_label()
    btn_apply_start.config(command=apply_start)
    btn_apply_end.config(command=apply_end)
    btn_clear_start.config(command=clear_start)
    btn_clear_end.config(command=clear_end)
    chk_hist.config(command=on_chk_hist_toggle)
    chk.config(command=on_chk_toggle)
    chk_stops.config(command=on_chk_stops_toggle)
    chk_estac.config(command=on_chk_estac_toggle)
    maps_btn.config(command=ok_action)
    cancel_btn.config(command=cancel_action)
    start_entry.bind('<Return>', (lambda e: apply_start()))
    end_entry.bind('<Return>', (lambda e: apply_end()))
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    x = ((sw - WIDTH) // 2)
    y = ((sh - HEIGHT) // 2)
    root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
    root.deiconify()
    if master is not None:
        root.grab_set()
        master.wait_window(root)
    else:
        root.mainloop()
    return getattr(root, 'result', None)

class ProgressWindow:
    """
    Janela de progresso não-bloqueante para operações longas.

    A operação pesada roda num thread daemon; o thread principal
    mantém o event loop do tkinter ativo — janela nunca trava.

    Uso:
        pw = ProgressWindow("Gerando Mapas de Execução...")
        result, error, _ = pw.run(minha_funcao, arg1, kwarg=val)
    """
    def __init__(self, title='Processando...', master=None):
        import time as _time
        import threading as _threading
        self._time = _time
        self._threading = _threading
        self._master = master
        self._lock = _threading.Lock()
        self._done = 0
        self._label_text = ''
        self._start = _time.monotonic()
        self._thread = None
        self._result = None
        self._error = None
        self._build(title)

    def _build(self, title):
        W, H = (440, 165)
        if self._master is not None:
            self.root = tk.Toplevel(self._master)
        else:
            self.root = tk.Tk()
        self.root.title(title)
        self.root.resizable(False, False)
        self.root.configure(bg='#000000')
        try:
            icon_path = _get_icon_path()
            if icon_path:
                self.root.iconbitmap(icon_path)
        except Exception:
            pass
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{((sw - W) // 2)}+{((sh - H) // 2)}")
        tk.Label(self.root, text=f"🚀 {title}", bg='#000000', fg='#00ff00', font=('Segoe UI', 13, 'bold')).pack(pady=(18, 4))
        self._lbl_item = tk.StringVar(value='Iniciando...')
        tk.Label(self.root, textvariable=self._lbl_item, bg='#000000', fg='#888888', font=('Segoe UI', 9)).pack()
        pb_frame = tk.Frame(self.root, bg='#000000')
        pb_frame.pack(fill='x', padx=24, pady=(10, 4))
        self._pb = ttk.Progressbar(pb_frame, mode='indeterminate', length=(W - 48))
        self._pb.pack(fill='x')
        self._pb.start(12)
        row = tk.Frame(self.root, bg='#000000')
        row.pack()
        self._lbl_ela = tk.StringVar(value='00:00')
        tk.Label(row, textvariable=self._lbl_ela, bg='#000000', fg='#888888', font=('Segoe UI', 8)).pack(side='left')
        self.root.protocol('WM_DELETE_WINDOW', (lambda: None))

    def increment(self, label=''):
        """Avança contador (thread-safe)."""
        with self._lock:
            self._done += 1
            if label:
                self._label_text = label

    def _poll(self):
        with self._lock:
            done, label = self._done, self._label_text
        elapsed = (self._time.monotonic() - self._start)
        self._lbl_ela.set(f"Decorrido: {self._fmt(elapsed)}")
        if label:
            self._lbl_item.set(label)
        elif done:
            self._lbl_item.set(f"{done} etapas concluídas")
        if self._thread and self._thread.is_alive():
            self.root.after(250, self._poll)
            return None
        self.root.after(400, self.root.destroy)

    @staticmethod
    def _fmt(secs):
        s = max(0, int(secs))
        return f"{(s // 60):02d}:{(s % 60):02d}"

    def run(self, fn, *args, **kwargs):
        """Executa fn em thread daemon; mantém mainloop aqui."""
        self._start = self._time.monotonic()
        def _body():
            try:
                self._result = fn(*args, **kwargs)
            except Exception as e:
                self._error = e
                import traceback
                traceback.print_exc()
            return None

        self._thread = self._threading.Thread(target=_body, daemon=True)
        self._thread.start()
        self.root.after(250, self._poll)
        if self._master is not None:
            self.root.grab_set()
            self._master.wait_window(self.root)
        else:
            self.root.mainloop()
        self._thread.join()
        return (self._result, self._error, False)

def show_result_dialog(success, message, details=None, master=None):
    """Mostra diálogo de resultado."""
    result_win = tk.Toplevel(master) if master is not None else tk.Tk()
    result_win.title('Resultado' if success else 'Erro')
    result_win.geometry('500x350')
    result_win.resizable(False, False)
    result_win.configure(bg='#000000')
    try:
        icon_path = _get_icon_path()
        if icon_path:
            result_win.iconbitmap(icon_path)
    except Exception:
        pass
    result_win.update_idletasks()
    sw = result_win.winfo_screenwidth()
    sh = result_win.winfo_screenheight()
    x = ((sw - 500) // 2)
    y = ((sh - 350) // 2)
    result_win.geometry(f"500x350+{x}+{y}")
    icon = '✅' if success else '❌'
    color = '#00ff00' if success else '#ff0000'
    tk.Label(result_win, text=f"{icon} {message}", bg='#000000', fg=color, font=('Segoe UI', 14, 'bold')).pack(pady=(30, 15))
    if details:
        details_frame = tk.Frame(result_win, bg='#111111', padx=15, pady=10)
        details_frame.pack(fill='x', padx=20, pady=10)
        tk.Label(details_frame, text=details, bg='#111111', fg='#ffffff', font=('Segoe UI', 10), justify='left', anchor='w').pack(fill='x')
    tk.Button(result_win, text='OK', bg='#333333', fg='#ffffff', font=('Segoe UI', 11, 'bold'), width=15, relief='flat', command=result_win.destroy).pack(pady=30)
    if master is not None:
        result_win.grab_set()
        master.wait_window(result_win)
        return None
    result_win.mainloop()

def main():
    """Entry point da GUI - SEM input(), funciona como executável."""
    is_frozen = getattr(sys, 'frozen', False)
    _root = tk.Tk()
    _root.withdraw()
    result = show_date_picker(date.today(), date.today(), master=_root)
    if result is None:
        _root.destroy()
        if not is_frozen:
            print('\n❌ Operação cancelada pelo usuário.')
        sys.exit(0)
    date_from, date_to, single_day, historic_only, day_and_night, selected_contracts, generate_mobile, stops_mode, generate_report, secure_mode, split_by_collection_type, estacionarios_mode = result
    if not is_frozen:
        print('\n============================================================')
        print('CONFIGURAÇÃO:')
        print('=' * 60)
        print(f"📅 Data inicial: {date_from.strftime('%d/%m/%Y')}")
        print(f"📅 Data final: {date_to.strftime('%d/%m/%Y')}")
        print(f"📋 Contratos: {', '.join(selected_contracts)}")
        print(f"⏰ Modo: {'Dia único' if single_day else 'Período'}")
        print(f"📊 Histórico apenas: {'Sim' if historic_only else 'Não'}")
        print(f"🌓 Diurno+Noturno: {'Sim' if day_and_night else 'Não'}")
        print(f"📱 Mobile: {'Sim' if generate_mobile else 'Não'}")
        print(f"🛑 Paradas: {'Sim' if stops_mode else 'Não'}")
        print(f"📄 Relatório: {'Sim' if generate_report else 'Não'}")
        print(f"🔒 Dados Seguros: {'Sim' if secure_mode else 'Não'}")
        print(f"🗂️ Separar por tipo: {'Sim' if split_by_collection_type else 'Não'}")
        print(f"📦 Mapa de Estacionários: {'Sim' if estacionarios_mode else 'Não'}")
        print('=' * 60)
        print('\n🚀 Iniciando processamento...\n')
    try:
        _cidades = sorted(set(get_contract_city_mapping(selected_contracts).values()))
        _tipos = get_required_vehicle_types(selected_contracts) if estacionarios_mode else None
    except Exception:
        _cidades, _tipos = list(selected_contracts), None
    _db_ok, _db_reason = diagnose_gps_db(date_from=date_from, date_to=date_to, contracts=_cidades, vehicle_types=_tipos)
    if not _db_ok:
        if not is_frozen:
            print(f"\n⚠️  GPS do banco indisponível: {_db_reason}\n")
        seguir = messagebox.askokcancel('GPS do banco indisponível', f"{_db_reason}\n\nSe continuar, o mapa usará SOMENTE os shapefiles — todo o GPS do gps.duckdb ficará de fora.\n\nContinuar assim mesmo?", icon='warning', parent=_root)
        if not seguir:
            _root.destroy()
            sys.exit(0)
    pw = ProgressWindow('Gerando Mapas de Execução...', master=_root)
    results, _err, _ = pw.run(process_all_contracts, profiles=selected_contracts, date_from=date_from, date_to=date_to, day_and_night_mode=day_and_night, historic_only_mode=historic_only, single_day_mode=single_day, generate_mobile=generate_mobile, stops_mode=stops_mode, generate_report=generate_report, secure_mode=secure_mode, split_by_collection_type=split_by_collection_type, estacionarios_mode=estacionarios_mode)
    if _err is not None:
        error_msg = str(_err)
        if not is_frozen:
            print('\n============================================================')
            print('❌ ERRO NO PROCESSAMENTO')
            print('=' * 60)
            print(f"Erro: {error_msg}")
            import traceback
            traceback.print_exc()
            print('============================================================\n')
        show_result_dialog(success=False, message='ERRO NO PROCESSAMENTO', details=f"Erro: {error_msg}", master=_root)
        _root.destroy()
        sys.exit(1)
    results = results if results is not None else []
    details = f"📊 Contratos processados: {len(results)}\n\n"
    for r in results:
        if r and r.get('estacionarios_mode'):
            tipo = r.get('tipo_estacionario', '?')
            vt = r.get('vehicle_type', '?')
            n = r.get('n_pontos', 0)
            nws = r.get('n_with_stops', 0)
            details += f"  📦 {r['contract']} — {tipo} ({vt}): {n} ponto(s), {nws} com paradas\n"
        elif r and r.get('stops_mode'):
            ratio = r.get('overall_stop_ratio')
            stops = r.get('total_stops', 0)
            if ratio is not None:
                details += f"  🛑 {r['contract']}: {(ratio * 100):.1f}% intensidade, {stops} paradas\n"
        elif r and 'overall_ratio' in r:
            ratio = r.get('overall_ratio')
            if ratio is not None:
                if isinstance(ratio, (tuple, list)):
                    pct_s = ratio[0] * 100
                    pct_g = (ratio[1] * 100) if len(ratio) > 1 else pct_s
                    if abs((pct_g - pct_s)) > 0.05:
                        details += f"  • {r['contract']}: {pct_s:.1f}%-{pct_g:.1f}%\n"
                    else:
                        details += f"  • {r['contract']}: {pct_s:.1f}%\n"
                        continue
                        details += f"  • {r['contract']}: {(ratio * 100):.1f}%\n"
    details += """
💾 Arquivos salvos em:
"""
    details += """  • Mapas: HEATMAPS/
"""
    details += '  • Histórico: HISTORICO/'
    if generate_report:
        details += """
  • Relatório: HEATMAPS/ (PDF + TXT)"""
    if not is_frozen:
        print('\n============================================================')
        print('✅ PROCESSAMENTO CONCLUÍDO!')
        print('=' * 60)
        print(details)
        print('============================================================\n')
    show_result_dialog(success=True, message='PROCESSAMENTO CONCLUÍDO!', details=details, master=_root)
    _root.destroy()

if __name__ == "__main__":
    main()

