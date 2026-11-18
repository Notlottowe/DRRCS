"""Tkinter desktop interface. Data and business checks stay in the cloud backend."""
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, font
from datetime import datetime, timezone
from gui_data import (ROLES, SAMPLE_ACCOUNTS, SEVERITIES, pages_for, first_page, date_label,
                      form_time, api_time, coordinates, geometry, severity, severity_label, human_text, field,
                      INCIDENT_TYPES, QUALIFICATIONS, PRIORITIES, URGENCIES, REPORT_CLASSES, UPDATE_TYPES,
                      RESOURCE_CATEGORIES, RESOURCE_KINDS, SHELTER_STATES, optional_coordinates)
from cloud_api import ApiError

BG = "#f4f6f9"
NAV = "#142b40"
INK = "#193247"
MUTED = "#5b6e7e"
BLUE = "#245f88"
RED = "#a43131"
GREEN = "#24734c"


class RecordTable(ttk.Frame):
    def __init__(self, parent, columns, selected=None, height=12):
        super().__init__(parent)
        self.columns = columns
        self.rows = {}
        self.tree = ttk.Treeview(self, columns=[c[0] for c in columns], show="headings", height=height, selectmode="browse")
        for key, title, width in columns:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=60, anchor="w", stretch=key not in {"id", "severity", "occupancy", "capacity", "spaces"})
        self.tree.grid(row=0, column=0, sticky="nsew")
        ttk.Scrollbar(self, orient="vertical", command=self.tree.yview).grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.configure(yscrollcommand=self._scroll)
        self.scroll = self.grid_slaves(row=0, column=1)[0]
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.selected_callback = selected
        self.tree.bind("<<TreeviewSelect>>", lambda event: selected(self.current()) if selected else None)
        self.tree.tag_configure("critical", foreground=RED)
        self.tree.tag_configure("closed", foreground=MUTED)

    def _scroll(self, a, b):
        self.scroll.set(a, b)

    def show(self, rows, select_id=None):
        previous = self.current()
        previous_id = select_id or (previous.get("id") if previous else None)
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        chosen = None
        for index, row in enumerate(rows):
            iid = str(index)
            values = []
            for key, _, _ in self.columns:
                value = row.get(key, "")
                if key in {"created_at", "effective", "start", "end"}:
                    value = date_label(value)
                elif key == "severity":
                    value = severity_label(value)
                elif isinstance(value, list):
                    value = ", ".join(map(str, value))
                values.append(str(value).replace("\n", " "))
            tag = "critical" if row.get("priority") == "Critical" or row.get("severity", 0) in [4, 5] else "closed" if row.get("status") in {"Closed", "Inactive"} else ""
            self.tree.insert("", "end", iid=iid, values=values, tags=(tag,) if tag else ())
            self.rows[iid] = row
            if row.get("id") == previous_id:
                chosen = iid
        if self.rows:
            chosen = chosen or next(iter(self.rows))
            self.tree.selection_set(chosen)
            self.tree.focus(chosen)
            self.tree.see(chosen)
        elif self.selected_callback:
            self.selected_callback(None)

    def current(self):
        selection = self.tree.selection()
        return self.rows.get(selection[0]) if selection else None


class FormDialog(tk.Toplevel):
    def __init__(self, app, title, fields, submit, actions=None, introduction=""):
        super().__init__(app.root)
        self.app, self.submit = app, submit
        self.title(title)
        self.transient(app.root)
        self.geometry(f"760x{min(760, app.root.winfo_screenheight()-110)}")
        self.minsize(650, 500)
        self.configure(background=BG)
        self.values, self.widgets, self.specs = {}, {}, fields
        header = ttk.Frame(self, padding=(24, 20, 24, 10))
        header.pack(fill="x")
        ttk.Label(header, text=title, style="PageTitle.TLabel").pack(anchor="w")
        if introduction:
            ttk.Label(header, text=introduction, wraplength=690, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=24)
        self.body = body
        canvas = tk.Canvas(body, background=BG, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        inner = ttk.Frame(canvas)
        inner.columnconfigure(0, weight=1)
        window = canvas.create_window(0, 0, window=inner, anchor="nw")
        inner.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        for spec in fields:
            key, kind = spec["key"], spec["kind"]
            row = ttk.Frame(inner)
            row.pack(fill="x", pady=(7, 6))
            ttk.Label(row, text=spec["label"]+(" *" if spec["required"] else ""), style="Field.TLabel").pack(anchor="w", pady=(0, 5))
            if kind == "long":
                widget = tk.Text(row, height=4, wrap="word", font=("Helvetica", 14), background="white", foreground=INK, insertbackground=INK, relief="solid", borderwidth=1, padx=9, pady=7)
                widget.insert("1.0", str(spec["value"]))
                self.values[key] = widget
                widget.pack(fill="x")
            elif kind == "checks":
                widget = ttk.Frame(row)
                widget.pack(fill="x")
                variables = {}
                for choice in spec["choices"]:
                    variable = tk.BooleanVar(value=choice in spec["value"])
                    ttk.Checkbutton(widget, text=choice, variable=variable).pack(anchor="w", pady=3)
                    variables[choice] = variable
                self.values[key] = variables
            else:
                variable = tk.StringVar(value=str(spec["value"]))
                widget = ttk.Combobox(row, textvariable=variable, values=spec["choices"], state="readonly") if kind == "choice" else ttk.Entry(row, textvariable=variable, show="*" if kind == "password" else "")
                widget.pack(fill="x", ipady=5)
                self.values[key] = variable
            self.widgets[key] = widget
            if spec["hint"]:
                ttk.Label(row, text=spec["hint"], wraplength=670, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        self.error_label = ttk.Label(self, text="", foreground=RED, wraplength=700)
        self.error_label.pack(fill="x", padx=24, pady=(10, 0))
        footer = ttk.Frame(self, padding=(24, 12, 24, 20))
        footer.pack(fill="x")
        self.app.button(footer, "Cancel", self.destroy).pack(side="right", padx=(10, 0))
        for label, action, primary in reversed([("Review & save", "save", True)] if actions is None else actions):
            self.app.button(footer, label, lambda action=action: self.send(action), primary).pack(side="right", padx=(10, 0))
        self.grab_set()
        def wheel(event):
            delta = int(event.delta/120) if abs(event.delta) >= 120 else int(event.delta)
            canvas.yview_scroll(-delta*2, "units")
            return "break"
        def bind_wheel(widget):
            if not isinstance(widget, tk.Text):
                widget.bind("<MouseWheel>", wheel)
            for child in widget.winfo_children():
                bind_wheel(child)
        bind_wheel(inner)
        canvas.bind("<MouseWheel>", wheel)
        self.after(50, lambda: next(iter(self.widgets.values())).focus_set() if self.widgets else None)

    def error(self, message):
        if self.winfo_exists():
            self.error_label.configure(text=message)

    def read(self):
        result = {}
        for spec in self.specs:
            key = spec["key"]
            variable = self.values[key]
            if spec["kind"] == "long":
                value = variable.get("1.0", "end-1c").strip()
            elif spec["kind"] == "checks":
                value = [choice for choice, checked in variable.items() if checked.get()]
            else:
                value = variable.get()
                if spec["kind"] != "password":
                    value = value.strip()
            if spec["required"] and not value:
                raise ValueError("Please complete: "+spec["label"])
            result[key] = value
        return result

    def send(self, action):
        if self.app.busy:
            return
        try:
            self.error_label.configure(text="")
            self.submit(self.read(), action, self)
        except (ValueError, KeyError) as error:
            self.error(str(error))


class DesktopApp:
    def __init__(self, root, api):
        self.root, self.api = root, api
        self.user = None
        self.busy = False
        self.closed = False
        self.buttons = []
        self.jobs = queue.Queue()
        self.page = "login"
        self.page_loader = None
        self.last_saved = None
        root.title("DRRCS — Disaster Response")
        width = min(1360, root.winfo_screenwidth()-60)
        height = min(900, root.winfo_screenheight()-100)
        root.geometry(f"{width}x{height}")
        root.minsize(1030, 690)
        root.configure(background=BG)
        for name in ["TkDefaultFont", "TkTextFont"]:
            font.nametofont(name).configure(family="Helvetica", size=13)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK, font=("Helvetica", 14))
        style.configure("Muted.TLabel", foreground=MUTED, font=("Helvetica", 12))
        style.configure("Field.TLabel", font=("Helvetica", 13, "bold"))
        style.configure("PageTitle.TLabel", font=("Helvetica", 25, "bold"))
        style.configure("Section.TLabel", font=("Helvetica", 16, "bold"))
        style.configure("TButton", font=("Helvetica", 13), padding=(14, 9), background="#e7edf2", foreground=INK)
        style.configure("Primary.TButton", background=BLUE, foreground="white", bordercolor=BLUE)
        style.map("Primary.TButton", background=[("active", "#194a6d"), ("disabled", "#a8bbc9")], foreground=[("disabled", "#f0f3f5")])
        style.configure("TEntry", foreground=INK, fieldbackground="white", padding=6)
        style.configure("TCombobox", foreground=INK, fieldbackground="white", padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", "white")], foreground=[("readonly", INK)])
        style.configure("TCheckbutton", background=BG, foreground=INK, font=("Helvetica", 13))
        style.configure("Treeview", font=("Helvetica", 13), rowheight=36, foreground=INK, background="white", fieldbackground="white", borderwidth=0)
        style.configure("Treeview.Heading", font=("Helvetica", 12, "bold"), background="#e7edf2", foreground=INK, padding=10)
        style.map("Treeview", background=[("selected", "#d8e9f5")], foreground=[("selected", "#12364e")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", font=("Helvetica", 13), padding=(18, 10))
        self.container = ttk.Frame(root)
        self.container.pack(fill="both", expand=True)
        self.status = ttk.Label(root, text="Ready to sign in", padding=(22, 9), style="Muted.TLabel")
        self.status.pack(fill="x", side="bottom")
        self.login_screen()
        root.after(50, self.poll)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def button(self, parent, label, command, primary=False, enabled=True):
        widget = ttk.Button(parent, text=label, command=command, style="Primary.TButton" if primary else "TButton")
        widget.allowed = enabled
        widget.configure(state="normal" if enabled and not self.busy else "disabled")
        self.buttons.append(widget)
        return widget

    def enabled(self, button, allowed):
        button.allowed = allowed
        if button.winfo_exists():
            button.configure(state="normal" if allowed and not self.busy else "disabled")

    def busy_state(self, busy):
        self.busy = busy
        self.root.configure(cursor="watch" if busy else "")
        self.buttons = [b for b in self.buttons if b.winfo_exists()]
        for button in self.buttons:
            button.configure(state="normal" if button.allowed and not busy else "disabled")

    def run(self, work, done, message="Loading cloud records…", error_target=None):
        if self.busy:
            return
        self.busy_state(True)
        self.status.configure(text=message, foreground=MUTED)

        def task():
            try:
                self.jobs.put((done, work(), None, error_target))
            except Exception as error:
                self.jobs.put((done, None, error, error_target))
        threading.Thread(target=task, daemon=True).start()

    def poll(self):
        if self.closed:
            return
        try:
            done, result, error, target = self.jobs.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy_state(False)
            if error:
                message = str(error) if isinstance(error, (ApiError, ValueError)) else "The operation could not be completed. Please refresh and try again."
                self.status.configure(text=message, foreground=RED)
                if target and target.winfo_exists():
                    target.error(message)
                elif self.page == "login":
                    self.login_error.configure(text=message)
                else:
                    messagebox.showerror("Operation not completed", message, parent=self.root)
            else:
                self.status.configure(text="Cloud data updated at "+datetime.now().strftime("%H:%M:%S"), foreground=GREEN)
                done(result)
        self.root.after(50, self.poll)

    def close(self):
        self.closed = True
        self.root.destroy()

    def can(self, permission):
        return self.user is not None and permission in self.user["permissions"]

    def clear(self, frame):
        for child in frame.winfo_children():
            child.destroy()

    def login_screen(self):
        self.clear(self.container)
        self.page = "login"
        shell = ttk.Frame(self.container, padding=50)
        shell.place(relx=0.5, rely=0.46, anchor="center", width=920)
        left = tk.Frame(shell, background=NAV, padx=30, pady=36)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 38))
        tk.Label(left, text="DRRCS", font=("Helvetica", 33, "bold"), foreground="white", background=NAV).pack(anchor="w")
        tk.Label(left, text="Disaster Response &\nResource Coordination", font=("Helvetica", 18), justify="left", foreground="#d4e2ed", background=NAV).pack(anchor="w", pady=(14, 30))
        tk.Label(left, text="Record incidents\nCoordinate emergency operations\nTrack shelters and resources\nReview reports and audit history", font=("Helvetica", 13), justify="left", foreground="#d4e2ed", background=NAV).pack(anchor="w")
        right = ttk.Frame(shell)
        right.grid(row=0, column=1, sticky="nsew")
        shell.columnconfigure(1, weight=1)
        ttk.Label(right, text="Sign in", style="PageTitle.TLabel").pack(anchor="w", pady=(2, 7))
        ttk.Label(right, text="Use your assigned account to access cloud records.", wraplength=400, style="Muted.TLabel").pack(anchor="w", pady=(0, 24))
        ttk.Label(right, text="Username", style="Field.TLabel").pack(anchor="w")
        self.username = tk.StringVar(value="dispatcher")
        username = ttk.Combobox(right, values=list(SAMPLE_ACCOUNTS), textvariable=self.username)
        username.pack(fill="x", pady=(6, 15), ipady=5)
        ttk.Label(right, text="Password", style="Field.TLabel").pack(anchor="w")
        self.password = tk.StringVar()
        password = ttk.Entry(right, textvariable=self.password, show="*")
        password.pack(fill="x", pady=(6, 12), ipady=5)
        self.login_error = ttk.Label(right, text="", foreground=RED, wraplength=410)
        self.login_error.pack(anchor="w", pady=(0, 8))
        self.login_button = self.button(right, "Sign in", self.sign_in, True)
        self.login_button.pack(fill="x", pady=(0, 10))
        self.button(right, "Browse public shelters", lambda: self.open_session(None)).pack(fill="x")
        self.button(right, "Report an emergency (no account needed)", lambda: self.open_session(None, "citizen")).pack(fill="x", pady=(10, 0))
        self.account_hint = ttk.Label(right, text=SAMPLE_ACCOUNTS["dispatcher"], wraplength=410, style="Muted.TLabel")
        self.account_hint.pack(anchor="w", pady=(18, 0))
        self.username.trace_add("write", lambda *args: self.account_hint.configure(text=SAMPLE_ACCOUNTS.get(self.username.get(), "Sign in with your assigned account.")))
        ttk.Label(shell, text="Ask the project owner for classroom account credentials. Records are saved in Cloudflare.", style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(24, 0))
        password.bind("<Return>", lambda event: self.sign_in())
        username.bind("<Return>", lambda event: password.focus_set())
        self.root.after(100, password.focus_set)

    def sign_in(self):
        if self.busy:
            return
        username, password = self.username.get().strip(), self.password.get()
        if not username or not password:
            self.login_error.configure(text="Enter your username and password.")
            return
        self.login_error.configure(text="")

        def signed_in(result):
            self.api.token = result["token"]
            self.password.set("")
            self.open_session(result["user"])
        self.run(lambda: self.api.call("login", {"username": username, "password": password}), signed_in, "Signing in…")

    def sign_out(self):
        def logout():
            if self.api.token:
                try:
                    self.api.call("logout")
                except ApiError:
                    pass
            return None

        def finished(result):
            self.api.token = ""
            self.user = None
            self.login_screen()
            self.status.configure(text="Signed out", foreground=MUTED)
        self.run(logout, finished, "Signing out…")

    def open_session(self, user, start_page=None):
        self.user = user
        self.clear(self.container)
        nav = tk.Frame(self.container, background=NAV, width=218)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        tk.Label(nav, text="DRRCS", foreground="white", background=NAV, font=("Helvetica", 25, "bold"), anchor="w", padx=24, pady=26).pack(fill="x")
        self.nav_buttons = {}
        for page, label in pages_for(user):
            button = tk.Button(nav, text=label, anchor="w", padx=24, pady=13, relief="flat", borderwidth=0,
                               background=NAV, foreground="#e1ebf2", activebackground="#25445d", activeforeground="white",
                               font=("Helvetica", 14), command=lambda page=page: self.navigate(page))
            button.pack(fill="x", pady=1)
            self.nav_buttons[page] = button
        tk.Label(nav, text="Cloud records\nPhase 4 demonstration", justify="left", anchor="w", foreground="#9db3c5", background=NAV, font=("Helvetica", 11), padx=24, pady=22).pack(side="bottom", fill="x")
        main = ttk.Frame(self.container, padding=(26, 18, 26, 16))
        main.pack(side="left", fill="both", expand=True)
        top = ttk.Frame(main)
        top.pack(fill="x", pady=(0, 16))
        identity = "Public information" if not user else user["name"]+"  ·  "+", ".join(user["roles"])
        ttk.Label(top, text=identity, style="Muted.TLabel").pack(side="left")
        self.button(top, "Switch account" if user else "Sign in", self.sign_out).pack(side="right")
        self.button(top, "Demo guide", self.demo_guide).pack(side="right", padx=(0, 8))
        self.content = ttk.Frame(main)
        self.content.pack(fill="both", expand=True)
        self.navigate(start_page or (first_page(user) if user else "shelters"))

    def navigate(self, page):
        if self.busy:
            return
        self.page = page
        self.page_loader = None
        self.clear(self.content)
        for key, button in self.nav_buttons.items():
            button.configure(background="#2b4b65" if key == page else NAV)
        getattr(self, "page_"+page)()

    def heading(self, title, subtitle):
        ttk.Label(self.content, text=title, style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(self.content, text=subtitle, style="Muted.TLabel", wraplength=940).pack(anchor="w", pady=(5, 16))

    def toolbar(self, parent=None):
        bar = ttk.Frame(parent or self.content)
        bar.pack(fill="x", pady=(0, 12))
        return bar

    def details(self, parent, height=9):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, height=height, wrap="word", font=("Helvetica", 14), background="white", foreground=INK,
                       relief="solid", borderwidth=1, padx=16, pady=12, spacing1=3, spacing3=5, state="disabled")
        text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)
        return text

    def set_text(self, widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def show_details(self, title, value):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.geometry("900x680")
        frame = ttk.Frame(dialog, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=title, style="PageTitle.TLabel").pack(anchor="w", pady=(0, 15))
        output = self.details(frame)
        self.set_text(output, value if isinstance(value, str) else human_text(value))
        self.button(frame, "Close", dialog.destroy).pack(anchor="e", pady=(16, 0))

    def saved(self, result, dialog=None):
        self.last_saved = result
        if dialog and dialog.winfo_exists():
            dialog.destroy()
        if self.page == "incidents" and isinstance(result, dict) and result.get("id"):
            self.incident_search.set("")
        if self.page_loader:
            self.page_loader(result.get("id") if isinstance(result, dict) else None)
        self.status.configure(text="Saved to Cloudflare"+(" · "+result["id"] if isinstance(result, dict) and "id" in result else ""), foreground=GREEN)

    def review(self, title, summary, confirm, parent=None, warning="", reason_label=None, enabled=True):
        fields = [field("reason", reason_label, required=True, hint="At least 10 characters.")] if reason_label else []
        review = FormDialog(self, title, fields, lambda values, action, dialog: confirm(values.get("reason", ""), dialog),
                            actions=[("Confirm & save", "save", True)] if enabled else [], introduction=warning or "Check this summary before saving to the cloud.")
        review.body.pack_configure(expand=False)
        if not fields:
            review.body.pack_forget()
        else:
            review.body.configure(height=115)
            review.body.pack_propagate(False)
        output = tk.Text(review, height=14, wrap="word", font=("Helvetica", 14), background="white", foreground=INK, relief="flat", padx=16, pady=12)
        output.insert("1.0", human_text(summary))
        output.configure(state="disabled")
        output.pack(fill="both", expand=True, padx=24, pady=(0, 18), before=review.body if fields else review.error_label)
        if warning:
            review.error_label.configure(text=warning, foreground=RED)
        if parent:
            review.transient(parent)
            def restore_grab(event):
                if event.widget == review and parent.winfo_exists():
                    parent.grab_set()
            review.bind("<Destroy>", restore_grab)
        return review

    def demo_guide(self):
        self.show_details("Demonstration guide", "1  Sign in as dispatcher\nOpen Incidents, choose New incident, complete the form and save. Refresh the list to verify the cloud record.\n\n2  Sign in as shelter\nOpen My shelters, select Central High School and change occupancy. Try invalid or over-capacity numbers to demonstrate validation.\n\n3  Sign in as admin\nReview the Overview, manage emergency areas, authorize notifications and create reports. A finalized report cannot be edited.\n\n4  Sign in as responder\nSelect assigned incident IN-001 and submit resolution information.\n\n5  Sign in as dispatcher or admin\nSelect a Resolved incident and close it. Open items require an Emergency Administrator override.\n\n6  Sign in as sysadmin\nManage users and inspect read-only audit history. Failure scenarios demonstrate audit queue recovery.\n\nUse Switch account in the top-right corner. Ask the project owner for classroom passwords. All records come from Cloudflare.")

    def page_incidents(self):
        self.heading("Incidents", "Select an incident to review its details. New records and updates are saved to the cloud.")
        bar = self.toolbar()
        if self.can("incident_add"):
            self.button(bar, "+ New incident", self.new_incident, True).pack(side="left", padx=(0, 10))
        self.button(bar, "Refresh", lambda: self.page_loader()).pack(side="right")
        self.incident_search = tk.StringVar()
        ttk.Entry(bar, textvariable=self.incident_search, width=26).pack(side="left", padx=(0, 8), ipady=3)
        ttk.Label(bar, text="Search name, description or ID", style="Muted.TLabel").pack(side="left")
        self.incident_rows = []
        self.incident_table = RecordTable(self.content, [("id", "Incident", 120), ("type", "Type", 150), ("severity", "Severity", 135), ("status", "Status", 145), ("created_at", "Reported", 220)], self.select_incident, height=9)
        self.incident_table.pack(fill="both", expand=True, pady=(0, 14))
        actions = self.toolbar()
        self.history_button = self.button(actions, "View history", self.incident_history, enabled=False)
        self.history_button.pack(side="left", padx=(0, 8))
        self.field_buttons = {}
        for permission, label, command in [("incident_update", "Update incident", self.incident_update_form), ("assign", "Assign personnel", self.assign_form),
                                           ("progress", "Progress update", self.progress_form), ("escalate", "Escalate", self.escalate_form),
                                           ("resource_request", "Request resources", self.resource_request_form)]:
            self.field_buttons[permission] = self.button(actions, label, command, permission == "assign", enabled=False)
            if self.can(permission):
                self.field_buttons[permission].pack(side="left", padx=(0, 8))
        self.resolve_button = self.button(actions, "Submit resolution", self.resolution_form, enabled=False)
        self.close_button = self.button(actions, "Close incident", self.closure_form, enabled=False)
        if self.can("resolve"):
            self.resolve_button.pack(side="left", padx=(0, 8))
        if self.can("close"):
            self.close_button.pack(side="left")
        self.incident_detail = self.details(self.content, height=7)
        self.incident_search.trace_add("write", lambda *args: self.filter_incidents())

        def load(select_id=None):
            def loaded(rows):
                self.incident_rows = rows
                self.filter_incidents(select_id)
            self.run(lambda: self.api.call("incidents"), loaded)
        self.page_loader = load
        load()

    def filter_incidents(self, select_id=None):
        term = self.incident_search.get().strip().lower()
        rows = [r for r in self.incident_rows if term in " ".join(str(r.get(k, "")) for k in ["id", "type", "description", "area"]).lower()]
        self.incident_table.show(rows, select_id)

    def select_incident(self, row):
        self.enabled(self.history_button, bool(row))
        self.enabled(self.resolve_button, bool(row) and self.can("resolve") and row["status"] in {"Assigned", "Responding"})
        self.enabled(self.close_button, bool(row) and self.can("close") and row["status"] == "Resolved")
        active = bool(row) and row["status"] not in {"Resolved", "Closed"}
        for permission, button in self.field_buttons.items():
            self.enabled(button, active and self.can(permission))
        if not row:
            self.set_text(self.incident_detail, "No matching incidents. Refresh or change the search.")
            return
        info = {"incident": row["id"], "type": row["type"], "status": row["status"]+(" (contained)" if row.get("contained") else "")}
        if row.get("reports"):
            info["citizen_reports"] = row["reports"]
        info |= {"severity": severity_label(row["severity"]), "priority": row.get("priority", "Medium"),
                "description": row["description"], "operational_area": row.get("area"), "location": ", ".join(map(str, row["location"])),
                "needed": f"{row.get('responders_needed', 1)} responder(s), qualification: {row.get('needed_qualification') or 'Any'}"}
        if row.get("resolution"):
            info["resolution"] = row["resolution"]
        if row.get("restrictions"):
            info["transportation_warnings"] = [{"road": r["name"], "instructions": r["instructions"]} for r in row["restrictions"]]
        self.set_text(self.incident_detail, human_text(info))

    def new_incident(self):
        fields = [field("type", "Incident type", "Flood", "choice", ["Flood", "Fire", "Traffic accident", "Medical emergency", "Rescue", "Hazard", "Other"]),
                  field("description", "What happened?", kind="long", hint="Describe the emergency in at least 10 characters."),
                  field("latitude", "Latitude", "41.88"), field("longitude", "Longitude", "-87.63"),
                  field("area", "Operational area", "Central"), field("severity", "Reported severity", SEVERITIES[2], "choice", SEVERITIES)]
        def submit(values, action, dialog):
            payload = {"type": values["type"], "description": values["description"], "location": coordinates(values["latitude"], values["longitude"]),
                       "area": values["area"], "severity": severity(values["severity"])}
            if len(payload["description"]) < 10:
                raise ValueError("Describe the incident using at least 10 characters.")
            def confirm(reason, review):
                def done(result):
                    review.destroy()
                    self.saved(result, dialog)
                self.run(lambda: self.api.call("incident_add", payload), done, "Saving incident to Cloudflare…", review)
            self.review("Review new incident", {"type": payload["type"], "description": payload["description"], "location": values["latitude"]+", "+values["longitude"], "area": values["area"], "severity": values["severity"], "initial_status": "Reported"}, confirm, dialog)
        self.active_form = FormDialog(self, "New incident", fields, submit, introduction="Record a new emergency event. A unique incident ID is assigned after saving.")

    def incident_history(self):
        row = self.incident_table.current()
        if row:
            self.run(lambda: self.api.call("incident_history", {"id": row["id"]}), lambda rows: self.show_details("History · "+row["id"], [{"time": date_label(r["created_at"]), "recorded_by": r["actor"], "details": r["details"]} for r in rows]))

    def resolution_form(self):
        row = self.incident_table.current()
        if not row:
            return
        fields = [field("actions", "Actions taken", kind="long", hint="At least 10 characters."), field("outcome", "Response outcome", kind="long", hint="At least 10 characters."),
                  field("remaining", "Remaining conditions", "None", hint="Enter None if no remaining conditions exist."), field("completion", "Completed at (UTC)", form_time(), hint="YYYY-MM-DD HH:MM")]
        def submit(values, action, dialog):
            data = values | {"id": row["id"], "completion": api_time(values["completion"])}
            def previewed(preview):
                data["version"] = preview["version"]
                def confirm(reason, review):
                    self.run(lambda: self.api.call("resolve", data), lambda result: (review.destroy(), self.saved(result, dialog)), "Saving resolution…", review)
                self.review("Review resolution · "+row["id"], preview["resolution"], confirm, dialog)
            self.run(lambda: self.api.call("resolve", data | {"preview": True}), previewed, "Checking assignment and resolution…", dialog)
        self.active_form = FormDialog(self, "Submit resolution · "+row["id"], fields, submit, introduction="The backend checks your active assignment before recording the resolution.")

    def closure_form(self):
        row = self.incident_table.current()
        if not row:
            return
        def submit(values, action, dialog):
            data = {"id": row["id"], "notes": values["notes"]}
            def previewed(preview):
                data["version"] = preview["version"]
                open_items = preview["open_assignments"] or preview["open_requests"]
                allowed = not open_items or self.can("override")
                warning = "Open items require an Emergency Administrator and a documented override." if open_items else ""
                def confirm(reason, review):
                    payload = data | {"reason": reason}
                    self.run(lambda: self.api.call("close", payload), lambda result: (review.destroy(), self.saved(result, dialog)), "Closing incident and releasing assignments…", review)
                self.review("Review incident closure", {"incident": row["id"], "final_notes": values["notes"], "open_assignments": preview["open_assignments"], "open_requests": preview["open_requests"]}, confirm, dialog,
                            warning=warning, reason_label="Override reason" if open_items and allowed else None, enabled=allowed)
            self.run(lambda: self.api.call("close", data | {"preview": True}), previewed, "Checking closure prerequisites…", dialog)
        self.active_form = FormDialog(self, "Close incident · "+row["id"], [field("notes", "Final closure notes", kind="long", hint="At least 10 characters. The resolution and its history are retained.")], submit)

    def page_shelters(self):
        managed = self.can("occupancy")
        self.heading("My shelters" if managed else "Shelter search", "Choose a shelter to review capacity and access information." if managed else "Find open shelters by location and available space. Public results exclude internal contacts and supply records.")
        bar = self.toolbar()
        self.search_lat, self.search_lon, self.search_radius = tk.StringVar(value="41.88"), tk.StringVar(value="-87.63"), tk.StringVar(value="30")
        if not managed:
            for label, variable, width in [("Latitude", self.search_lat, 10), ("Longitude", self.search_lon, 10), ("Radius km", self.search_radius, 6)]:
                ttk.Label(bar, text=label).pack(side="left", padx=(0, 6))
                ttk.Entry(bar, textvariable=variable, width=width).pack(side="left", padx=(0, 12), ipady=3)
        self.button(bar, "Refresh" if managed else "Search", lambda: self.page_loader(), True).pack(side="right")
        self.search_spaces = tk.StringVar(value="1")
        self.search_status = tk.StringVar(value="Any")
        if not managed:
            filters = self.toolbar()
            ttk.Label(filters, text="Minimum spaces").pack(side="left", padx=(0, 6))
            ttk.Entry(filters, textvariable=self.search_spaces, width=6).pack(side="left", padx=(0, 18), ipady=3)
            ttk.Label(filters, text="Shelter status").pack(side="left", padx=(0, 6))
            ttk.Combobox(filters, textvariable=self.search_status, values=["Any", "Open", "Near Capacity", "Full"], state="readonly", width=18).pack(side="left", ipady=3)
        self.shelter_table = RecordTable(self.content, [("name", "Shelter", 250), ("status", "Status", 160), ("occupancy", "Occupancy", 100), ("capacity", "Capacity", 95), ("spaces", "Spaces", 90)], self.select_shelter, height=9)
        self.shelter_table.pack(fill="both", expand=True, pady=(0, 14))
        bar = self.toolbar()
        self.shelter_detail_button = self.button(bar, "View details", self.shelter_details, enabled=False)
        self.shelter_detail_button.pack(side="left", padx=(0, 10))
        self.occupancy_button = self.button(bar, "Change occupancy", self.occupancy_form, True, enabled=False)
        self.shelter_update_button = self.button(bar, "Update shelter information", self.shelter_update_form, enabled=False)
        if managed:
            self.occupancy_button.pack(side="left")
        if self.can("shelter_update"):
            self.shelter_update_button.pack(side="left", padx=(10, 0))
        self.shelter_info = self.details(self.content, height=7)
        def load(select_id=None):
            def loaded(rows):
                self.shelter_table.show([r | {"spaces": max(0, r["capacity"]-r["occupancy"])} for r in rows], select_id)
            # Read Tk variables on the UI thread before starting the network request.
            if not managed:
                try:
                    data = {"location": coordinates(self.search_lat.get(), self.search_lon.get()), "radius": self.search_radius.get(), "spaces": self.search_spaces.get(), "status": self.search_status.get()}
                except ValueError as error:
                    messagebox.showerror("Check search criteria", str(error), parent=self.root)
                    return
                self.run(lambda: self.api.call("shelters", data), loaded)
            else:
                self.run(lambda: self.api.call("dashboard").get("shelters", []), loaded)
        self.page_loader = load
        load()

    def select_shelter(self, row):
        self.enabled(self.shelter_detail_button, bool(row))
        self.enabled(self.occupancy_button, bool(row) and self.can("occupancy") and row["status"] in {"Open", "Near Capacity", "Full"})
        self.enabled(self.shelter_update_button, bool(row) and self.can("shelter_update"))
        self.set_text(self.shelter_info, human_text({"shelter": row["name"], "shelter_id": row["id"], "status": row["status"], "occupancy": f"{row['occupancy']} / {row['capacity']}",
                                                 "available_spaces": max(0, row["capacity"]-row["occupancy"]), "accessibility": row["accessibility"], "public_contact": row["public_contact"]}) if row else "No matching shelters. Try a wider search radius.")

    def shelter_details(self):
        row = self.shelter_table.current()
        if row:
            self.run(lambda: self.api.call("shelter", {"id": row["id"], "private": self.can("shelter_private")}), lambda result: self.show_details(row["name"], result))

    def occupancy_form(self):
        row = self.shelter_table.current()
        if not row:
            return
        fields = [field("direction", "Change", "Add arrivals", "choice", ["Add arrivals", "Record departures"]), field("amount", "Number of people", "5", hint="Enter a positive whole number. This is the number arriving or leaving, not the new total.")]
        def submit(values, action, dialog):
            if not values["amount"].isdigit() or int(values["amount"]) < 1:
                raise ValueError("Enter a positive whole number of people.")
            delta = int(values["amount"])*(1 if values["direction"] == "Add arrivals" else -1)
            data = {"id": row["id"], "delta": delta}
            def previewed(preview):
                data["version"] = preview["version"]
                def confirm(reason, review):
                    self.run(lambda: self.api.call("occupancy", data | {"reason": reason}), lambda result: (review.destroy(), self.saved(result, dialog)), "Saving shelter occupancy…", review)
                self.review("Review occupancy change", {"shelter": row["name"], "current_occupancy": preview["before"], "new_occupancy": preview["after"], "capacity": preview["capacity"], "new_status": preview["status"]}, confirm, dialog,
                            warning=preview["warning"], reason_label="Reason for temporary over-capacity intake" if preview["warning"] else None)
            self.run(lambda: self.api.call("occupancy", data | {"preview": True}), previewed, "Checking shelter capacity…", dialog)
        self.active_form = FormDialog(self, "Change occupancy · "+row["name"], fields, submit, introduction=f"Current occupancy: {row['occupancy']} of {row['capacity']}. The current cloud value is checked again before saving.")

    def page_overview(self):
        self.heading("Operations overview", "A live summary for your role. Unavailable sources are clearly marked.")
        bar = self.toolbar()
        self.button(bar, "Refresh overview", lambda: self.page_loader(), True).pack(side="left")
        self.overview_body = ttk.Frame(self.content)
        self.overview_body.pack(fill="both", expand=True)
        def load(select_id=None):
            self.run(lambda: self.api.call("dashboard"), self.render_overview)
        self.page_loader = load
        load()

    def render_overview(self, data):
        self.clear(self.overview_body)
        if data["unavailable_sources"]:
            ttk.Label(self.overview_body, text="Unavailable: "+", ".join(data["unavailable_sources"]), foreground=RED).pack(anchor="w", pady=(0, 15))
        stats = ttk.Frame(self.overview_body)
        stats.pack(fill="x", pady=(0, 24))
        labels = {"active_incidents": "Active incidents", "critical_incidents": "High / critical", "unassigned_incidents": "Unassigned", "pending_resource_requests": "Pending requests", "available_vehicles": "Available vehicles"}
        shown = [(key, label) for key, label in labels.items() if key in data][:4]
        for index, (key, label) in enumerate(shown):
            cell = ttk.Frame(stats, padding=(0, 5, 25, 10))
            cell.grid(row=0, column=index, sticky="nw")
            stats.columnconfigure(index, weight=1)
            ttk.Label(cell, text=str(data[key]), font=("Helvetica", 31, "bold"), foreground=RED if key == "critical_incidents" and data[key] else INK).pack(anchor="w")
            ttk.Label(cell, text=label, style="Muted.TLabel").pack(anchor="w")
        notebook = ttk.Notebook(self.overview_body)
        notebook.pack(fill="both", expand=True)
        if "shelters" in data and isinstance(data["shelters"], list):
            page = ttk.Frame(notebook, padding=15)
            notebook.add(page, text="Shelter capacity")
            table = RecordTable(page, [("name", "Shelter", 300), ("status", "Status", 160), ("occupancy", "Occupancy", 100), ("capacity", "Capacity", 100)])
            table.pack(fill="both", expand=True)
            table.show(data["shelters"])
        if "resources" in data and isinstance(data["resources"], list):
            page = ttk.Frame(notebook, padding=15)
            notebook.add(page, text="Resource inventory")
            table = RecordTable(page, [("name", "Resource", 260), ("kind", "Kind", 150), ("available", "Available", 110), ("allocated", "Allocated", 110), ("total", "Total", 100)])
            table.pack(fill="both", expand=True)
            table.show(data["resources"])
        page = ttk.Frame(notebook, padding=15)
        notebook.add(page, text="Indicators")
        output = self.details(page)
        self.set_text(output, human_text({k: v for k, v in data.items() if k not in {"resources", "shelters", "scope", "as_of"}}))

    def page_areas(self):
        self.heading("Emergency areas", "Manage zones, evacuation orders and transportation restrictions.")
        bar = self.toolbar()
        self.button(bar, "Refresh all", lambda: self.page_loader()).pack(side="right")
        notebook = ttk.Notebook(self.content)
        notebook.pack(fill="both", expand=True)
        self.area_views, self.area_rows = {}, {}
        configs = [
            ("zones", "Emergency zones", [("name", "Zone", 240), ("emergency_type", "Emergency", 140), ("severity", "Severity", 125), ("status", "Status", 120), ("end", "Ends", 200)]),
            ("orders", "Evacuation orders", [("id", "Order", 120), ("area", "Affected area", 210), ("status", "Status", 125), ("delivery_status", "Delivery", 135), ("effective", "Effective", 200)]),
            ("restrictions", "Road restrictions", [("name", "Restriction", 250), ("status", "Status", 130), ("start", "Starts", 200), ("end", "Ends", 200)]),
        ]
        for kind, title, columns in configs:
            frame = ttk.Frame(notebook, padding=14)
            notebook.add(frame, text=title)
            controls = self.toolbar(frame)
            self.button(controls, "+ New "+{"zones": "zone", "orders": "order", "restrictions": "restriction"}[kind], lambda kind=kind: self.area_form(kind), True).pack(side="left", padx=(0, 8))
            edit = self.button(controls, "Edit draft" if kind == "orders" else "Edit selected", lambda kind=kind: self.area_form(kind, self.area_views[kind]["table"].current()), enabled=False)
            edit.pack(side="left", padx=(0, 8))
            table = RecordTable(frame, columns, lambda row, kind=kind: self.select_area(kind, row), height=8)
            table.pack(fill="both", expand=True, pady=(0, 12))
            output = self.details(frame, height=7)
            self.area_views[kind] = {"table": table, "edit": edit, "output": output}
        def load(select_id=None):
            def loaded(result):
                self.area_rows = result
                for kind, rows in result.items():
                    self.area_views[kind]["table"].show(rows, select_id)
            self.run(lambda: {kind: self.api.call(kind) for kind in ["zones", "orders", "restrictions"]}, loaded)
        self.page_loader = load
        load()

    def select_area(self, kind, row):
        view = self.area_views[kind]
        self.enabled(view["edit"], bool(row) and (kind != "orders" or row["status"] == "Draft"))
        if not row:
            self.set_text(view["output"], "No records in this category.")
            return
        keys = ["id", "name", "area", "zone_id", "instructions", "boundary", "status", "delivery_status", "authorized_by", "justification"]
        self.set_text(view["output"], human_text({k: row[k] for k in keys if k in row}))

    def area_form(self, kind, old=None):
        old = dict(old or {})
        if kind == "orders":
            return self.order_form(old)
        box = old.get("boundary", [41.70, -87.90, 41.79, -87.81])
        fields = [field("name", "Zone name" if kind == "zones" else "Road / restriction name", old.get("name", ""))]
        if kind == "zones":
            fields += [field("area", "Operational area", old.get("area", "Central")), field("emergency_type", "Emergency type", old.get("emergency_type", "Flood")),
                       field("severity", "Severity", severity_label(old.get("severity", 3)), "choice", SEVERITIES)]
        fields += [field("south", "South boundary (latitude)", box[0]), field("west", "West boundary (longitude)", box[1]),
                   field("north", "North boundary (latitude)", box[2]), field("east", "East boundary (longitude)", box[3]),
                   field("start", "Starts (UTC)", form_time(old.get("start"), hours=-1), hint="YYYY-MM-DD HH:MM"),
                   field("end", "Ends (UTC)", form_time(old.get("end"), hours=24)), field("instructions", "Public / operational instructions", old.get("instructions", ""), "long", hint="At least 10 characters."),
                   field("status", "Status", old.get("status", "Active"), "choice", ["Active", "Inactive", "Closed"] if kind == "zones" else ["Active", "Inactive"])]
        command = "zone_save" if kind == "zones" else "restriction_save"
        def submit(values, action, dialog):
            data = {"name": values["name"], "boundary": geometry(values), "start": api_time(values["start"]), "end": api_time(values["end"]),
                    "instructions": values["instructions"], "status": values["status"]}
            if kind == "zones":
                data.update(area=values["area"], emergency_type=values["emergency_type"], severity=severity(values["severity"]))
            if old:
                data.update(id=old["id"], version=old["version"])
            def previewed(preview):
                conflicts = preview.get("conflicts", [])
                summary = {"name": values["name"], "instructions": values["instructions"], "boundary": data["boundary"], "starts": values["start"]+" UTC", "ends": values["end"]+" UTC",
                           "affected_operations": preview["affected_operations"]}
                def confirm(reason, review):
                    self.run(lambda: self.api.call(command, data | {"justification": reason}), lambda result: (review.destroy(), self.saved(result, dialog)), "Saving emergency area…", review)
                self.review("Review zone" if kind == "zones" else "Review restriction", summary, confirm, dialog,
                            warning="Overlaps existing zones: "+", ".join(conflicts) if conflicts else "",
                            reason_label="Justification for overlapping zones" if conflicts else None)
            self.run(lambda: self.api.call(command, data | {"preview": True}), previewed, "Validating boundary and affected operations…", dialog)
        self.active_form = FormDialog(self, ("Edit " if old else "New ")+("emergency zone" if kind == "zones" else "transportation restriction"), fields, submit,
                                      introduction="Use geographic boundaries and a valid time period. The system checks conflicts and affected operations before saving.")

    def order_form(self, old=None):
        old = old or {}
        zones = [z for z in self.area_rows.get("zones", []) if z["status"] == "Active" or z["id"] == old.get("zone_id")]
        if not zones:
            messagebox.showinfo("Create a zone first", "An evacuation order requires an emergency zone. Create an Active zone first.", parent=self.root)
            return
        choices = [z["id"]+" · "+z["name"] for z in zones]
        selected = next((choice for choice in choices if choice.startswith(old.get("zone_id", "!")+" · ")), choices[0])
        fields = [field("zone", "Emergency zone", selected, "choice", choices), field("area", "Affected area", old.get("area", "Central")),
                  field("effective", "Effective from (UTC)", form_time(old.get("effective")), hint="YYYY-MM-DD HH:MM. A future time schedules delivery."),
                  field("end", "Order ends (UTC)", form_time(old.get("end"), hours=12)),
                  field("instructions", "Evacuation instructions", old.get("instructions", ""), "long", hint="At least 10 characters. Include the route, destination or required action.")]
        def submit(values, action, dialog):
            data = {"zone_id": values["zone"].split(" · ")[0], "area": values["area"], "effective": api_time(values["effective"]),
                    "end": api_time(values["end"]), "instructions": values["instructions"], "draft": action == "draft"}
            if old:
                data.update(id=old["id"], version=old["version"])
            def previewed(preview):
                summary = {"zone": values["zone"], "affected_area": values["area"], "instructions": values["instructions"], "effective": values["effective"]+" UTC", "ends": values["end"]+" UTC",
                           "save_as": "Draft — no notification" if data["draft"] else "Authorized evacuation order", "delivery": preview["delivery_status"]}
                def confirm(reason, review):
                    self.run(lambda: self.api.call("order_save", data), lambda result: (review.destroy(), self.saved(result, dialog)), "Saving evacuation order…", review)
                self.review("Review evacuation order", summary, confirm, dialog, warning="" if data["draft"] else "Authorization creates a Critical notice for Citizens and Emergency Personnel. Delivery is simulated in this prototype.")
            self.run(lambda: self.api.call("order_save", data | {"preview": True}), previewed, "Checking zone and effective time…", dialog)
        self.active_form = FormDialog(self, "Edit evacuation draft" if old else "New evacuation order", fields, submit,
                                      actions=[("Save draft", "draft", False), ("Authorize order", "authorize", True)], introduction="A draft does not take effect or send notifications. Authorized orders are kept as history.")

    def page_notices(self):
        self.heading("Emergency notifications", "Create a draft, authorize delivery and inspect results for each recipient group.")
        bar = self.toolbar()
        self.button(bar, "+ New notification", self.notice_form, True).pack(side="left", padx=(0, 8))
        self.notice_edit = self.button(bar, "Edit / authorize draft", lambda: self.notice_form(self.notice_table.current()), enabled=False)
        self.notice_edit.pack(side="left", padx=(0, 8))
        self.notice_retry_button = self.button(bar, "Retry failed delivery", self.retry_notice, enabled=False)
        self.notice_retry_button.pack(side="left")
        self.button(bar, "Refresh", lambda: self.page_loader()).pack(side="right")
        self.notice_table = RecordTable(self.content, [("id", "Notice", 120), ("area", "Area", 160), ("priority", "Priority", 120), ("status", "Delivery status", 165), ("effective", "Effective", 210)], self.select_notice, height=10)
        self.notice_table.pack(fill="both", expand=True, pady=(0, 14))
        self.notice_detail = self.details(self.content, height=8)
        def load(select_id=None):
            self.run(lambda: self.api.call("notices"), lambda rows: self.notice_table.show(rows, select_id))
        self.page_loader = load
        load()

    def select_notice(self, row):
        self.enabled(self.notice_edit, bool(row) and row["status"] == "Draft")
        self.enabled(self.notice_retry_button, bool(row) and row["status"] in {"Failed", "Partial Failure"})
        info = {"message": row["message"], "area": row["area"], "priority": row["priority"], "status": row["status"], "recipient_groups": row["groups"],
                "group_results": row["delivery"], "authorized_by": row["authorized_by"] or "Not authorized", "effective": date_label(row["effective"])} if row else "No notifications yet. Create a draft to get started."
        self.set_text(self.notice_detail, human_text(info))

    def notice_form(self, old=None):
        old = old or {}
        fields = [field("message", "Notification message", old.get("message", ""), "long", hint="At least 10 characters."), field("area", "Affected area or population", old.get("area", "Central")),
                  field("priority", "Priority", old.get("priority", "Critical"), "choice", ["Informational", "Urgent", "Critical"]),
                  field("effective", "Effective from (UTC)", form_time(old.get("effective")), hint="A future time schedules delivery. YYYY-MM-DD HH:MM"),
                  field("groups", "Recipient groups", old.get("groups", ["Citizens", "Emergency Personnel"]), "checks", ["Citizens", "Emergency Personnel"])]
        def submit(values, action, dialog):
            data = {"message": values["message"], "area": values["area"], "priority": values["priority"], "effective": api_time(values["effective"]), "groups": values["groups"], "draft": action == "draft"}
            if old:
                data.update(id=old["id"], version=old["version"])
            def previewed(preview):
                summary = {"message": values["message"], "affected_area": values["area"], "priority": values["priority"], "recipient_groups": values["groups"], "effective": values["effective"]+" UTC",
                           "save_as": "Draft — not delivered" if data["draft"] else "Authorized notification"}
                def confirm(reason, review):
                    self.run(lambda: self.api.call("notice_save", data), lambda result: (review.destroy(), self.saved(result, dialog)), "Saving notification…", review)
                self.review("Review notification", summary, confirm, dialog, warning="" if data["draft"] else "The notification will be delivered at its effective time. Delivery is simulated in this prototype.")
            self.run(lambda: self.api.call("notice_save", data | {"preview": True}), previewed, "Validating message and audience…", dialog)
        self.active_form = FormDialog(self, "Edit notification draft" if old else "New notification", fields, submit,
                                      actions=[("Save draft", "draft", False), ("Authorize delivery", "authorize", True)])

    def retry_notice(self):
        row = self.notice_table.current()
        if row:
            self.run(lambda: self.api.call("notice_retry", {"id": row["id"]}), self.saved, "Retrying failed groups…")

    def page_reports(self):
        self.heading("Reports", "Build a draft, complete the analysis and finalize an immutable cloud record.")
        bar = self.toolbar()
        self.button(bar, "+ Operational report", lambda: self.report_form("Operational"), True).pack(side="left", padx=(0, 8))
        self.button(bar, "+ After-action report", lambda: self.report_form("After-Action")).pack(side="left")
        self.button(bar, "Refresh", lambda: self.page_loader()).pack(side="right")
        self.report_table = RecordTable(self.content, [("id", "Report", 120), ("kind", "Type", 150), ("area", "Area", 130), ("status", "Status", 130), ("created_at", "Created", 220)], self.select_report, height=9)
        self.report_table.pack(fill="both", expand=True, pady=(0, 14))
        bar = self.toolbar()
        self.report_view = self.button(bar, "View full report", self.view_report, enabled=False)
        self.report_view.pack(side="left", padx=(0, 8))
        self.report_edit = self.button(bar, "Edit draft notes", self.report_notes_form, enabled=False)
        self.report_edit.pack(side="left", padx=(0, 8))
        self.report_final = self.button(bar, "Finalize report", self.finalize_report, True, enabled=False)
        self.report_final.pack(side="left", padx=(0, 8))
        self.report_export = self.button(bar, "Export…", self.export_report, enabled=False)
        self.report_export.pack(side="left")
        self.report_detail = self.details(self.content, height=7)
        def load(select_id=None):
            self.run(lambda: self.api.call("reports"), lambda rows: self.report_table.show(rows, select_id))
        self.page_loader = load
        load()

    def select_report(self, row):
        for button in [self.report_view, self.report_export]:
            self.enabled(button, bool(row))
        for button in [self.report_edit, self.report_final]:
            self.enabled(button, bool(row) and row["status"] == "Draft")
        info = {"report": row["id"], "type": row["kind"], "status": row["status"], "period": date_label(row["start"])+" to "+date_label(row["end"]),
                "total_incidents": row["metrics"]["total_incidents"], "missing_sections": row["missing_sections"], "notes": row["notes"], "limitations": row["limitations"] or "None"} if row else "No reports yet. Choose a report type to build a draft."
        self.set_text(self.report_detail, human_text(info))

    def report_form(self, kind):
        fields = [field("start", "Period starts (UTC)", form_time(hours=-48), hint="YYYY-MM-DD HH:MM"), field("end", "Period ends (UTC)", form_time(hours=1)),
                  field("area", "Operational area", "All", hint="Use All for the full operational scope."),
                  field("notes", "Analysis / lessons learned" if kind == "After-Action" else "Operational notes", kind="long", required=False,
                        hint="After-action finalization requires at least 10 characters of analysis."), field("limitations", "Known data limitations", kind="long", required=False)]
        def submit(values, action, dialog):
            data = values | {"kind": kind, "start": api_time(values["start"]), "end": api_time(values["end"])}
            self.run(lambda: self.api.call("report_create", data), lambda result: self.saved(result, dialog), "Building report draft from cloud records…", dialog)
        self.active_form = FormDialog(self, "New "+kind.lower()+" report", fields, submit, actions=[("Build draft", "save", True)])

    def view_report(self):
        row = self.report_table.current()
        if row:
            self.run(lambda: self.api.call("report", {"id": row["id"]}), lambda report: self.show_details(report["kind"]+" report · "+report["id"], report))

    def report_notes_form(self):
        row = self.report_table.current()
        if not row:
            return
        fields = [field("notes", "Analysis / notes", row["notes"], "long", required=False), field("limitations", "Documented limitations", row["limitations"], "long", required=False)]
        def submit(values, action, dialog):
            self.run(lambda: self.api.call("report_notes", values | {"id": row["id"], "version": row["version"]}), lambda result: self.saved(result, dialog), "Saving report notes…", dialog)
        self.active_form = FormDialog(self, "Edit draft notes · "+row["id"], fields, submit, actions=[("Save draft notes", "save", True)])

    def finalize_report(self):
        row = self.report_table.current()
        if not row:
            return
        problem = ""
        if row["kind"] == "Operational" and row["missing_sections"]:
            problem = "Recover the missing sources, then build a new complete draft before finalizing."
        elif row["kind"] == "After-Action" and (len(row["notes"].strip()) < 10 or row["missing_sections"] and len(row["limitations"].strip()) < 10):
            problem = "Edit the draft notes to add analysis and document any missing sections before finalizing."
        def confirm(reason, review):
            self.run(lambda: self.api.call("report_finalize", {"id": row["id"], "version": row["version"]}), lambda result: (review.destroy(), self.saved(result)), "Finalizing cloud report…", review)
        self.review("Finalize report", {"report": row["id"], "type": row["kind"], "missing_sections": row["missing_sections"], "notes": row["notes"], "limitations": row["limitations"] or "None"}, confirm,
                    warning=problem or "Finalized reports cannot be edited. Their saved contents are retained as history.", enabled=not problem)

    def export_report(self):
        row = self.report_table.current()
        if not row:
            return
        def export(report):
            name = filedialog.asksaveasfilename(parent=self.root, title="Export saved report", initialfile=report["id"]+".txt", defaultextension=".txt", filetypes=[("Readable text", "*.txt"), ("JSON data", "*.json")])
            if name:
                from pathlib import Path
                Path(name).write_text(json.dumps(report, indent=2) if name.lower().endswith(".json") else human_text(report), encoding="utf-8")
                self.status.configure(text="Report exported to "+name, foreground=GREEN)
        self.run(lambda: self.api.call("report", {"id": row["id"]}), export)

    def page_accounts(self):
        self.heading("User accounts", "Manage account status and roles. The server checks permissions and incompatible role combinations.")
        bar = self.toolbar()
        self.button(bar, "+ New account", self.account_form, True).pack(side="left", padx=(0, 8))
        self.account_edit = self.button(bar, "Edit selected account", lambda: self.account_form(self.account_table.current()), enabled=False)
        self.account_edit.pack(side="left")
        self.button(bar, "Refresh", lambda: self.page_loader()).pack(side="right")
        self.account_table = RecordTable(self.content, [("username", "Username", 125), ("name", "Name", 180), ("roles", "Roles", 280), ("status", "Status", 100)], self.select_account, height=10)
        self.account_table.pack(fill="both", expand=True, pady=(0, 14))
        self.account_detail = self.details(self.content, height=7)
        def load(select_id=None):
            self.run(lambda: self.api.call("users"), lambda rows: self.account_table.show(rows, select_id))
        self.page_loader = load
        load()

    def select_account(self, row):
        self.enabled(self.account_edit, bool(row))
        self.set_text(self.account_detail, human_text({"username": row["username"], "name": row["name"], "agency": row["agency"], "roles": row["roles"], "status": row["status"]}) if row else "No accounts found.")

    def account_form(self, old=None):
        old = old or {}
        fields = [field("username", "Username", old.get("username", ""), hint="At least 3 characters. Letters, numbers, _, ., @ and - are allowed."),
                  field("name", "Full name", old.get("name", "")), field("agency", "Agency / organization", old.get("agency", "")),
                  field("roles", "Assigned roles", old.get("roles", []), "checks", ROLES,
                        hint="System Administrator must be separate from operational roles. First Responder cannot also be Dispatcher or Emergency Administrator."),
                  field("status", "Account status", old.get("status", "Active"), "choice", ["Active", "Inactive"]),
                  field("password", "New password" if old else "Password", kind="password", required=not old,
                        hint="12–128 characters, including a letter and a number. "+("Leave blank to keep the current password." if old else "Give this password to the account holder privately."))]
        def submit(values, action, dialog):
            data = values | ({"version": old["version"]} if old else {})
            def previewed(preview):
                summary = {k: preview[k] for k in ["username", "name", "agency", "roles", "status"]}
                summary["password"] = "Will be changed (hidden)" if old and values["password"] else "Unchanged" if old else "Set (hidden)"
                def confirm(reason, review):
                    def done(result):
                        review.destroy()
                        if result["username"] == self.user["username"]:
                            dialog.destroy()
                            self.sign_out()
                        else:
                            self.saved(result, dialog)
                    self.run(lambda: self.api.call("user_save", data), done, "Saving account and role permissions…", review)
                self.review("Review account", summary, confirm, dialog, warning="" if values["username"] != self.user["username"] else "Changing your own account signs you out. Sign in again to use the updated permissions.")
            self.run(lambda: self.api.call("user_save", data | {"preview": True}), previewed, "Checking roles and account details…", dialog)
        self.active_form = FormDialog(self, "Edit account" if old else "New account", fields, submit)
        if old:
            self.active_form.widgets["username"].configure(state="readonly")

    def page_audit(self):
        self.heading("Audit history", "Read-only records of logins, business changes and permission checks. Latest 200 records per list.")
        bar = self.toolbar()
        self.button(bar, "Refresh audit", lambda: self.page_loader(), True).pack(side="left", padx=(0, 8))
        self.button(bar, "Failure scenarios…", self.fault_form).pack(side="left")
        self.audit_count = ttk.Label(bar, text="", style="Muted.TLabel")
        self.audit_count.pack(side="right")
        notebook = ttk.Notebook(self.content)
        notebook.pack(fill="both", expand=True, pady=(0, 14))
        self.audit_tables = {}
        for key, title in [("events", "Recorded events"), ("queued", "Waiting for recovery")]:
            frame = ttk.Frame(notebook, padding=12)
            notebook.add(frame, text=title)
            table = RecordTable(frame, [("created_at", "Time", 205), ("actor", "Actor", 115), ("action", "Action", 250), ("target", "Target", 155)], self.select_audit, height=10)
            table.pack(fill="both", expand=True)
            self.audit_tables[key] = table
        self.audit_detail = self.details(self.content, height=7)
        notebook.bind("<<NotebookTabChanged>>", lambda event: self.select_audit(self.audit_tables[["events", "queued"][notebook.index("current")]].current()))
        def load(select_id=None):
            def loaded(data):
                for key, rows in data.items():
                    self.audit_tables[key].show(rows)
                self.audit_count.configure(text=f"{len(data['events'])} recorded · {len(data['queued'])} queued")
                self.select_audit(self.audit_tables[["events", "queued"][notebook.index("current")]].current())
            self.run(lambda: self.api.call("audit"), loaded)
        self.page_loader = load
        load()

    def select_audit(self, row):
        self.set_text(self.audit_detail, human_text({"time": date_label(row["created_at"]), "actor": row["actor"], "action": row["action"], "target": row["target"], "event_id": row["event_id"], "details": row["details"]}) if row else "No audit records in this list.")

    def fault_form(self):
        faults = {"Audit recorder": "audit_unavailable", "Citizen notification delivery": "delivery_failure:Citizens", "Emergency personnel notification delivery": "delivery_failure:Emergency Personnel"}
        faults.update({"Monitoring source: "+name: "source_unavailable:"+name for name in ["incidents", "responders", "assignments", "resources", "allocations", "requests", "shelters", "zones", "orders"]})
        fields = [field("source", "Component", "Audit recorder", "choice", list(faults)),
                  field("state", "Set component state", "Available / recover", "choice", ["Unavailable (simulate failure)", "Available / recover"])]
        def submit(values, action, dialog):
            data = {"name": faults[values["source"]], "enabled": values["state"].startswith("Unavailable")}
            def confirm(reason, review):
                def done(result):
                    review.destroy()
                    self.saved(result, dialog)
                self.run(lambda: self.api.call("fault", data), done, "Updating demo component state…", review)
            self.review("Review failure scenario", {"component": values["source"], "new_state": values["state"]}, confirm, dialog,
                        warning="This affects the shared classroom backend. Restore the component to Available after the demonstration.")
        self.active_form = FormDialog(self, "Failure scenarios", fields, submit, introduction="For demonstrating alternate flows: failed delivery, incomplete data and audit recovery.")

    # ------------------------------------------------------------------
    # UC-1 to UC-11 screens. Every save goes through a backend preview,
    # a review dialog and Confirm & save, like the UC-12 to UC-24 forms.
    # ------------------------------------------------------------------
    def preview_then_save(self, command, data, title, dialog, summary=None, warning=None, update=None, message="Saving to Cloudflare…", after=None):
        def previewed(preview):
            payload = data | (update(preview) if update else {})
            def confirm(reason, review):
                def done(result):
                    review.destroy()
                    self.saved(result, dialog)
                    if after:
                        after(result)
                self.run(lambda: self.api.call(command, payload), done, message, review)
            self.review(title, summary(preview) if summary else preview, confirm, dialog, warning=warning(preview) if warning else "")
        self.run(lambda: self.api.call(command, data | {"preview": True}), previewed, "Checking with the cloud backend…", dialog)

    def changes_text(self, changes):
        return {k.replace("_", " ").capitalize(): f"{v['from'] if v['from'] not in (None, '') else '—'}  →  {v['to']}" for k, v in changes.items()}

    # UC-1 Submit Emergency Report
    def page_citizen(self):
        self.heading("Report an emergency", "In a life-threatening emergency call 911 first. No account is needed. Keep the report ID to check the status later.")
        bar = self.toolbar()
        self.button(bar, "Start a report", self.citizen_report_form, True).pack(side="left", padx=(0, 10))
        self.button(bar, "Check a report status", lambda: self.navigate("report_status")).pack(side="left")
        self.citizen_output = self.details(self.content, height=10)
        self.set_text(self.citizen_output, "Tell dispatch what is happening, where and when. Coordinates are optional. Your name and phone are optional and are only visible to dispatchers.")

    def citizen_report_form(self):
        fields = [field("type", "Type of emergency", "", "choice", INCIDENT_TYPES), field("severity", "How serious is it?", "", "choice", SEVERITIES),
                  field("address", "Where is it? (address or landmark)"),
                  field("latitude", "Latitude", required=False, hint="Optional, for example 41.8810"), field("longitude", "Longitude", required=False, hint="Optional, for example -87.6290"),
                  field("occurred", "When did it happen? (UTC)", form_time(), hint="YYYY-MM-DD HH:MM"),
                  field("description", "What is happening?", kind="long", hint="At least 10 characters."),
                  field("contact_name", "Your name", required=False), field("contact_phone", "Your phone", required=False, hint="Optional, for example +1 312-555-0100")]
        def submit(values, action, dialog):
            data = {"type": values["type"], "severity": severity(values["severity"]), "address": values["address"],
                    "location": optional_coordinates(values["latitude"], values["longitude"]), "occurred_at": api_time(values["occurred"]),
                    "description": values["description"], "contact_name": values["contact_name"], "contact_phone": values["contact_phone"]}
            summary = {"type": values["type"], "severity": values["severity"], "location": values["address"], "coordinates": data["location"] or "Not given",
                       "when": values["occurred"]+" UTC", "description": values["description"], "contact": (values["contact_name"]+" "+values["contact_phone"]).strip() or "Not given"}
            def confirm(reason, review):
                def done(result):
                    review.destroy()
                    dialog.destroy()
                    self.set_text(self.citizen_output, f"Report received.\n\nYOUR REPORT ID:  {result['id']}\n\nStatus: {result['status']}\nSubmitted: {date_label(result['submitted_at'])}\n\nKeep this ID. Use Report status to follow your report.")
                    self.status.configure(text="Report saved · "+result["id"], foreground=GREEN)
                self.run(lambda: self.api.call("report_submit", data), done, "Sending your report…", review)
            self.review("Review your report", summary, confirm, dialog)
        FormDialog(self, "Report an emergency", fields, submit, actions=[("Review & send", "save", True)], introduction="Fields marked * are required.")

    # UC-2 Check Report Status
    def page_report_status(self):
        self.heading("Report status", "Enter the report ID you received, for example RPT-ABCD2345.")
        bar = self.toolbar()
        self.status_id = tk.StringVar()
        ttk.Entry(bar, textvariable=self.status_id, width=28).pack(side="left", padx=(0, 10), ipady=4)
        self.button(bar, "Check status", self.check_report_status, True).pack(side="left")
        self.status_output = self.details(self.content, height=8)
        self.set_text(self.status_output, "Only the status is shown. Contact details and dispatcher notes are never shown here.")

    def check_report_status(self):
        rid = self.status_id.get().strip()
        if not rid:
            self.set_text(self.status_output, "Enter a report ID.")
            return
        self.set_text(self.status_output, "")
        self.run(lambda: self.api.call("report_status", {"id": rid}),
                 lambda result: self.set_text(self.status_output, human_text({"report_id": result["id"], "type": result["type"], "submitted": date_label(result["submitted_at"]), "status": result["status"]})),
                 "Checking report status…")

    # UC-3 Verify Citizen Report
    def page_citizen_reports(self):
        self.heading("Citizen reports", "Reports waiting for review. Verify each one: create an incident, link it to an active incident, or classify it.")
        bar = self.toolbar()
        self.button(bar, "Refresh", lambda: self.page_loader()).pack(side="right")
        self.report_table = RecordTable(self.content, [("id", "Report", 150), ("type", "Type", 150), ("severity", "Severity", 135), ("address", "Location", 260), ("when", "Submitted", 200)], self.select_citizen_report, height=8)
        self.report_table.pack(fill="both", expand=True, pady=(0, 14))
        actions = self.toolbar()
        self.report_buttons = []
        for label, command, primary in [("Possible matches", self.report_matches, False), ("Create incident", self.report_create_form, True),
                                        ("Associate with incident", self.report_associate_form, False), ("Classify (not an incident)", self.report_classify_form, False)]:
            button = self.button(actions, label, command, primary, enabled=False)
            button.pack(side="left", padx=(0, 8))
            self.report_buttons.append(button)
        self.report_detail = self.details(self.content, height=8)
        def load(select_id=None):
            def loaded(result):
                reports, self.active_incidents = result
                self.report_table.show([r | {"when": date_label(r["submitted_at"])} for r in reports])
            self.run(lambda: (self.api.call("citizen_reports"), [i for i in self.api.call("incidents") if i["status"] not in {"Resolved", "Closed"}]), loaded)
        self.page_loader = load
        load()

    def select_citizen_report(self, row):
        for button in self.report_buttons:
            self.enabled(button, bool(row))
        if not row:
            self.set_text(self.report_detail, "No reports are waiting for review.")
            return
        self.set_text(self.report_detail, human_text({"report": row["id"], "type": row["type"], "severity": severity_label(row["severity"]), "location": row["address"],
                                                      "coordinates": ", ".join(map(str, row["location"])) if row.get("location") else "Not given",
                                                      "occurred": date_label(row["occurred_at"]), "description": row["description"],
                                                      "contact": (row.get("contact_name", "")+" "+row.get("contact_phone", "")).strip() or "Not given", "submitted": row["when"]}))

    def report_matches(self):
        row = self.report_table.current()
        if row:
            self.run(lambda: self.api.call("report_matches", {"id": row["id"]}),
                     lambda rows: self.show_details("Possible matches · "+row["id"], [{"incident": m["id"], "type": m["type"], "status": m["status"], "severity": severity_label(m["severity"]),
                                                                                     "distance": f"{m['distance_km']} km" if m["distance_km"] is not None else "No coordinates", "match_score": m["score"],
                                                                                     "description": m["description"]} for m in rows] or "No active incident matches this report."), "Ranking nearby incidents…")

    def report_create_form(self):
        row = self.report_table.current()
        if not row:
            return
        lat, lon = row["location"] if row.get("location") else ("", "")
        fields = [field("type", "Incident type", row["type"], "choice", INCIDENT_TYPES), field("description", "Description", row["description"], "long"),
                  field("latitude", "Latitude", lat), field("longitude", "Longitude", lon), field("area", "Operational area", "Central"),
                  field("severity", "Severity", severity_label(row["severity"]), "choice", SEVERITIES), field("priority", "Priority", "Medium", "choice", PRIORITIES),
                  field("qualification", "Needed qualification", "Any", "choice", QUALIFICATIONS), field("responders", "Responders needed", "1")]
        def submit(values, action, dialog):
            data = {"id": row["id"], "version": row["version"], "action": "create", "type": values["type"], "description": values["description"],
                    "location": coordinates(values["latitude"], values["longitude"]), "area": values["area"], "severity": severity(values["severity"]),
                    "priority": values["priority"], "needed_qualification": "" if values["qualification"] == "Any" else values["qualification"],
                    "responders_needed": values["responders"]}
            self.preview_then_save("report_review", data, "Review new incident from "+row["id"], dialog,
                                   summary=lambda p: {"source_report": row["id"], "type": p["incident"]["type"], "severity": severity_label(p["incident"]["severity"]), "priority": p["incident"]["priority"],
                                                      "location": p["incident"]["location"], "needed": f"{p['incident']['responders_needed']} × {p['incident']['needed_qualification'] or 'Any'}",
                                                      "report_status_after": "Verified", "incident_status": "Reported"},
                                   message="Creating incident…", after=lambda result: messagebox.showinfo("Incident created", f"{result['id']} was created from {row['id']}.", parent=self.root))
        FormDialog(self, "Create incident · "+row["id"], fields, submit, introduction="The form is pre-filled from the citizen report. The report keeps a link to the new incident.")

    def report_associate_form(self):
        row = self.report_table.current()
        if not row:
            return
        def ready(matches):
            seen = {m["id"] for m in matches}
            options = [f"{m['id']} · {m['type']} · match {m['score']}" for m in matches] + [f"{i['id']} · {i['type']} · {i['status']}" for i in self.active_incidents if i["id"] not in seen]
            if not options:
                messagebox.showinfo("No active incidents", "There is no active incident to link this report to.", parent=self.root)
                return
            def submit(values, action, dialog):
                data = {"id": row["id"], "version": row["version"], "action": "associate", "incident_id": values["incident"].split(" · ")[0]}
                self.preview_then_save("report_review", data, "Review association", dialog, summary=lambda p: p | {"report_status_after": "Associated"}, message="Linking report…")
            FormDialog(self, "Associate · "+row["id"], [field("incident", "Active incident", options[0], "choice", options, hint="Best matches are listed first.")], submit)
        self.run(lambda: self.api.call("report_matches", {"id": row["id"]}), ready, "Ranking nearby incidents…")

    def report_classify_form(self):
        row = self.report_table.current()
        if not row:
            return
        def submit(values, action, dialog):
            data = {"id": row["id"], "version": row["version"], "action": "classify", "classification": values["classification"], "reason": values["reason"]}
            self.preview_then_save("report_review", data, "Review classification", dialog, message="Saving classification…")
        FormDialog(self, "Classify · "+row["id"], [field("classification", "Classification", "Non-emergency", "choice", REPORT_CLASSES),
                                                  field("reason", "Reason", kind="long", hint="At least 10 characters. The report is kept, not deleted.")], submit)

    # UC-4 Update Incident
    def incident_update_form(self):
        row = self.incident_table.current()
        if not row:
            return
        if self.can("assign"):
            fields = [field("type", "Incident type", row["type"], "choice", INCIDENT_TYPES), field("description", "Description", row["description"], "long"),
                      field("latitude", "Latitude", row["location"][0]), field("longitude", "Longitude", row["location"][1]), field("area", "Operational area", row.get("area", "Central")),
                      field("severity", "Severity", severity_label(row["severity"]), "choice", SEVERITIES), field("priority", "Priority", row.get("priority", "Medium"), "choice", PRIORITIES),
                      field("qualification", "Needed qualification", row.get("needed_qualification") or "Any", "choice", QUALIFICATIONS),
                      field("responders", "Responders needed", row.get("responders_needed", 1))]
        else:
            fields = [field("description", "Description", row["description"], "long", hint="First Responders can update the description. Use Progress update or Escalate for other changes.")]
        def submit(values, action, dialog):
            data = {"id": row["id"], "version": row["version"], "description": values["description"]}
            if self.can("assign"):
                data.update(type=values["type"], location=coordinates(values["latitude"], values["longitude"]), area=values["area"], severity=severity(values["severity"]),
                            priority=values["priority"], needed_qualification="" if values["qualification"] == "Any" else values["qualification"], responders_needed=values["responders"])
            self.preview_then_save("incident_update", data, "Review incident changes · "+row["id"], dialog, summary=lambda p: self.changes_text(p["changes"]), message="Saving incident update…")
        FormDialog(self, "Update incident · "+row["id"], fields, submit, introduction="Changes are checked against the current cloud version before saving.")

    # UC-5 Assign Emergency Personnel
    def assign_form(self):
        row = self.incident_table.current()
        if not row:
            return
        def ready(options):
            assigned = ", ".join(a["responder"] for a in options["assigned"]) or "Nobody yet"
            if not options["candidates"]:
                def escalate(values, action, dialog):
                    self.run(lambda: self.api.call("assign_escalation", {"id": row["id"], "note": values["note"]}),
                             lambda result: (dialog.destroy(), messagebox.showinfo("Escalation sent", result["message"], parent=self.root)), "Sending escalation…", dialog)
                FormDialog(self, "No qualified responder · "+row["id"], [field("note", "Note for the Emergency Administrator", kind="long", hint="At least 10 characters.")], escalate,
                           actions=[("Send escalation request", "save", True)], introduction=f"No available responder has the qualification {options['needed_qualification']}. Already assigned: {assigned}.")
                return
            labels = {f"{c['name']} ({c['id']}) · {c['agency']} · {', '.join(c['qualifications']) or 'no qualifications'} · " + (f"{c['distance_km']} km" if c["distance_km"] is not None else "distance unknown"): c["id"] for c in options["candidates"]}
            def submit(values, action, dialog):
                chosen = [labels[label] for label in values["responders"]]
                if not chosen:
                    raise ValueError("Select at least one responder.")
                data = {"id": row["id"], "responders": chosen}
                self.preview_then_save("assign", data, "Review assignment · "+row["id"], dialog,
                                       summary=lambda p: {"incident": p["incident"], "assign": p["assign"], "incident_status_after": p["status_after"], "notification": "Each responder receives an alert."},
                                       update=lambda p: {"version": p["version"], "versions": p["versions"]}, message="Saving assignment…")
            FormDialog(self, "Assign personnel · "+row["id"], [field("responders", "Available qualified responders (best match first)", [], "checks", list(labels))], submit,
                       introduction=f"Needs {row.get('responders_needed', 1)} responder(s), qualification: {options['needed_qualification']}. Already assigned: {assigned}.")
        self.run(lambda: self.api.call("assign_options", {"id": row["id"]}), ready, "Finding available qualified responders…")

    # UC-6 Submit Field Progress Update
    def progress_form(self):
        row = self.incident_table.current()
        if not row:
            return
        def submit(values, action, dialog):
            data = {"id": row["id"], "update_type": UPDATE_TYPES[values["update_type"]], "note": values["note"]}
            self.preview_then_save("progress", data, "Review progress update · "+row["id"], dialog,
                                   summary=lambda p: {"update": p["update"], "note": p["note"], "your_assignment_after": p["assignment_after"], "incident_status_after": p["incident_after"]["status"]},
                                   update=lambda p: {"version": p["version"]}, message="Saving progress update…")
        FormDialog(self, "Progress update · "+row["id"], [field("update_type", "Update type", "Situation update", "choice", list(UPDATE_TYPES)),
                                                          field("note", "What is happening on site?", kind="long", hint="At least 5 characters.")], submit)

    # UC-7 Escalate Incident
    def escalate_form(self):
        row = self.incident_table.current()
        if not row:
            return
        def submit(values, action, dialog):
            data = {"id": row["id"], "version": row["version"], "severity": severity(values["severity"]), "priority": values["priority"], "field_note": values["note"]}
            self.preview_then_save("escalate", data, "Review escalation · "+row["id"], dialog, summary=lambda p: {k: v for k, v in p.items() if k not in {"id", "version"}},
                                   warning=lambda p: "The dispatcher receives an URGENT alert.", message="Escalating incident…")
        FormDialog(self, "Escalate · "+row["id"], [field("severity", "New severity", severity_label(row["severity"]), "choice", SEVERITIES),
                                                   field("priority", "New priority", row.get("priority", "Medium"), "choice", PRIORITIES),
                                                   field("note", "Field conditions (why escalate?)", kind="long", hint="At least 10 characters.")], submit,
                   introduction=f"Current severity {severity_label(row['severity'])}, priority {row.get('priority', 'Medium')}. Escalation can only raise them.")

    # UC-8 Request Additional Resource
    def resource_request_form(self):
        row = self.incident_table.current()
        if not row:
            return
        def submit(values, action, dialog):
            data = {"id": row["id"], "resource_type": values["type"], "quantity": values["quantity"], "urgency": values["urgency"],
                    "delivery_location": values["delivery"], "justification": values["justification"]}
            self.preview_then_save("resource_request", data, "Review resource request · "+row["id"], dialog,
                                   summary=lambda p: {"resource": p["request"]["resource_type"], "quantity": p["request"]["quantity"], "urgency": p["request"]["urgency"],
                                                      "deliver_to": p["request"]["delivery_location"], "justification": p["request"]["justification"],
                                                      "priority_score": p["request"]["priority_score"], "transportation_restrictions": p["transportation_restrictions"] or "None"},
                                   warning=lambda p: "Check the road restrictions before choosing the delivery location." if p["transportation_restrictions"] else "",
                                   message="Sending resource request…",
                                   after=lambda result: messagebox.showinfo("Request sent", f"{result['id']} was sent to the Resource Coordinator (priority score {result['priority_score']}).", parent=self.root))
        FormDialog(self, "Request resources · "+row["id"], [field("type", "Resource type", "Water", "choice", RESOURCE_CATEGORIES), field("quantity", "Quantity", "10"),
                                                            field("urgency", "Urgency", "High", "choice", URGENCIES), field("delivery", "Delivery location", row.get("area", "Incident site")+" staging area"),
                                                            field("justification", "Justification", kind="long", hint="At least 10 characters.")], submit)

    # UC-9 Allocate Resource
    def page_requests(self):
        self.heading("Resource requests", "Open requests, highest priority first. Allocate, place in Waiting, or deny with a reason.")
        bar = self.toolbar()
        self.button(bar, "Refresh", lambda: self.page_loader()).pack(side="right")
        self.request_table = RecordTable(self.content, [("id", "Request", 120), ("incident_id", "Incident", 110), ("resource_type", "Type", 150), ("quantity", "Qty", 70),
                                                        ("remaining", "Still needed", 110), ("urgency", "Urgency", 100), ("priority_score", "Priority", 90), ("status", "Status", 160)], self.select_request, height=8)
        self.request_table.pack(fill="both", expand=True, pady=(0, 14))
        actions = self.toolbar()
        self.request_buttons = [self.button(actions, "Allocate", self.allocate_form, True, enabled=False),
                                self.button(actions, "Place in Waiting", lambda: self.decision_form("wait"), enabled=False),
                                self.button(actions, "Deny", lambda: self.decision_form("deny"), enabled=False)]
        for button in self.request_buttons:
            button.pack(side="left", padx=(0, 8))
        self.request_detail = self.details(self.content, height=7)
        def load(select_id=None):
            self.run(lambda: self.api.call("requests"), lambda rows: self.request_table.show(rows, select_id))
        self.page_loader = load
        load()

    def select_request(self, row):
        for button in self.request_buttons:
            self.enabled(button, bool(row))
        if not row:
            self.set_text(self.request_detail, "No open resource requests.")
            return
        self.set_text(self.request_detail, human_text({"request": row["id"], "incident": f"{row['incident_id']} ({row['incident_status']})", "needed": f"{row['remaining']} of {row['quantity']} {row['resource_type']}",
                                                       "urgency": row["urgency"], "deliver_to": row.get("delivery_location", "Not recorded"), "justification": row.get("justification", "Not recorded"),
                                                       "requested_by": row.get("requested_by", "Not recorded"), "requested": date_label(row["created_at"]), "decision_note": row.get("decision_reason", "None")}))

    def allocate_form(self):
        row = self.request_table.current()
        if not row:
            return
        def ready(options):
            if not options["resources"]:
                messagebox.showinfo("Nothing available", f"No available {row['resource_type']} resource is in stock. Place the request in Waiting.", parent=self.root)
                return
            labels = {f"{r['id']} · {r['name']} · {r['available']} available · {r['area']}": r["id"] for r in options["resources"]}
            first = options["resources"][0]
            def submit(values, action, dialog):
                data = {"id": row["id"], "resource_id": labels[values["resource"]], "quantity": values["quantity"]}
                self.preview_then_save("allocate", data, "Review allocation · "+row["id"], dialog,
                                       summary=lambda p: {"request": p["request"], "resource": p["resource"], "quantity": p["quantity"], "request_status_after": p["request_status_after"], "inventory_after": p["inventory_after"]},
                                       update=lambda p: {"resource_version": p["resource_version"], "request_version": p["request_version"]}, message="Allocating resources…")
            FormDialog(self, "Allocate · "+row["id"], [field("resource", "Compatible resource", next(iter(labels)), "choice", list(labels)),
                                                       field("quantity", "Quantity to allocate", min(row["remaining"], first["available"]), hint=f"Still needed: {row['remaining']}.")], submit,
                       introduction="Inventory and the request are updated together. If someone else changes them first, nothing is saved.")
        self.run(lambda: self.api.call("request_options", {"id": row["id"]}), ready, "Finding compatible resources…")

    def decision_form(self, action):
        row = self.request_table.current()
        if not row:
            return
        def submit(values, act, dialog):
            data = {"id": row["id"], "version": row["version"], "action": action, "reason": values["reason"]}
            def confirm(reason, review):
                self.run(lambda: self.api.call("request_decision", data), lambda result: (review.destroy(), self.saved(result, dialog)), "Saving decision…", review)
            self.review("Review decision · "+row["id"], {"request": row["id"], "new_status": "Waiting" if action == "wait" else "Denied", "reason": values["reason"]}, confirm, dialog)
        FormDialog(self, ("Place in Waiting · " if action == "wait" else "Deny · ")+row["id"], [field("reason", "Reason", kind="long", hint="At least 10 characters. The requester is notified.")], submit)

    # UC-10 Manage Resource Inventory
    def page_inventory(self):
        self.heading("Resource inventory", "Add resources, correct quantities and status, and review each resource's change history.")
        bar = self.toolbar()
        self.button(bar, "+ Add resource", lambda: self.resource_form(), True).pack(side="left", padx=(0, 8))
        self.inventory_edit = self.button(bar, "Edit selected", lambda: self.resource_form(self.inventory_table.current()), enabled=False)
        self.inventory_edit.pack(side="left", padx=(0, 8))
        self.inventory_history = self.button(bar, "Change history", self.resource_changes, enabled=False)
        self.inventory_history.pack(side="left", padx=(0, 8))
        self.button(bar, "Refresh", lambda: self.page_loader()).pack(side="right")
        self.inventory_search = tk.StringVar()
        ttk.Entry(bar, textvariable=self.inventory_search, width=20).pack(side="left", padx=(8, 6), ipady=3)
        ttk.Label(bar, text="Search", style="Muted.TLabel").pack(side="left")
        self.inventory_rows = []
        self.inventory_table = RecordTable(self.content, [("id", "Resource", 120), ("name", "Name", 230), ("category", "Category", 150), ("kind", "Kind", 110),
                                                          ("total", "Total", 80), ("allocated", "Allocated", 95), ("available", "Available", 95), ("status", "Status", 130)], self.select_resource, height=9)
        self.inventory_table.pack(fill="both", expand=True, pady=(0, 14))
        self.inventory_detail = self.details(self.content, height=5)
        self.inventory_search.trace_add("write", lambda *args: self.filter_inventory())
        def load(select_id=None):
            def loaded(rows):
                self.inventory_rows = rows
                self.filter_inventory(select_id)
            self.run(lambda: self.api.call("inventory"), loaded)
        self.page_loader = load
        load()

    def filter_inventory(self, select_id=None):
        term = self.inventory_search.get().strip().lower()
        self.inventory_table.show([r for r in self.inventory_rows if term in " ".join(str(r.get(k, "")) for k in ["id", "name", "category", "area"]).lower()], select_id)

    def select_resource(self, row):
        self.enabled(self.inventory_edit, bool(row))
        self.enabled(self.inventory_history, bool(row))
        self.set_text(self.inventory_detail, human_text({"resource": row["id"], "name": row["name"], "quantities": f"{row['total']} total = {row['available']} available + {row['allocated']} allocated + {row.get('consumed', 0)} consumed",
                                                         "area": row.get("area", ""), "status": row["status"]}) if row else "No resources match the search.")

    def resource_changes(self):
        row = self.inventory_table.current()
        if row:
            self.run(lambda: self.api.call("resource_history", {"id": row["id"]}),
                     lambda rows: self.show_details("Change history · "+row["id"], [{"time": date_label(r["created_at"]), "changed_by": r["actor"], "details": r["details"]} for r in rows] or "No changes recorded yet."))

    def resource_form(self, old=None):
        old = old or {}
        fields = ([] if old else [field("id", "Identifier", hint="Example: WATER-003 (letters, a hyphen and 3 digits).")]) + [
            field("name", "Name", old.get("name", "")), field("category", "Category", old.get("category", "Water"), "choice", RESOURCE_CATEGORIES),
            field("kind", "Kind", old.get("kind", "Consumable"), "choice", RESOURCE_KINDS), field("total", "Total quantity", old.get("total", "")),
            field("area", "Operational area", old.get("area", "Central")), field("status", "Status", old.get("status", "Available"), "choice", ["Available", "Out of Service"])]
        def submit(values, action, dialog):
            data = {"name": values["name"], "category": values["category"], "kind": values["kind"], "total": values["total"], "area": values["area"], "status": values["status"]}
            data.update({"id": old["id"], "version": old["version"]} if old else {"id": values["id"], "new": True})
            self.preview_then_save("resource_save", data, "Review resource · "+data["id"].upper(), dialog,
                                   summary=lambda p: self.changes_text(p["changes"]) if old else p["resource"], message="Saving inventory…")
        FormDialog(self, "Edit "+old["id"] if old else "Add resource", fields, submit,
                   introduction=f"Allocated now: {old.get('allocated', 0)}, consumed: {old.get('consumed', 0)}. The total cannot be lower than these." if old else "Available quantity is calculated from the total.")

    # UC-11 Update Shelter Information
    def shelter_update_form(self):
        row = self.shelter_table.current()
        if not row:
            return
        def ready(detail):
            state = "Open" if detail["status"] in {"Open", "Near Capacity", "Full"} else detail["status"]
            fields = [field("capacity", "Capacity", detail["capacity"], hint=f"Current occupancy: {detail['occupancy']}. Capacity cannot be lower."),
                      field("state", "Operating status", state, "choice", SHELTER_STATES, hint="Open shelters become Near Capacity at 90% and Full at 100% automatically."),
                      field("public_contact", "Public phone", detail["public_contact"]), field("internal_contact", "Internal contact", detail["internal_contact"]),
                      field("accessibility", "Accessibility", detail["accessibility"]), field("medical", "Medical support", "Yes" if detail["medical_support"] else "No", "choice", ["Yes", "No"])]
            fields += [field("supply_"+item, "Supply: "+item, quantity) for item, quantity in detail["supplies"].items()]
            def submit(values, action, dialog):
                data = {"id": row["id"], "version": detail["version"], "capacity": values["capacity"], "state": values["state"], "public_contact": values["public_contact"],
                        "internal_contact": values["internal_contact"], "accessibility": values["accessibility"], "medical_support": values["medical"] == "Yes",
                        "supplies": {item: values["supply_"+item] for item in detail["supplies"]}}
                self.preview_then_save("shelter_update", data, "Review shelter changes · "+detail["name"], dialog, summary=lambda p: self.changes_text(p["changes"]),
                                       warning=lambda p: p["warning"], message="Saving shelter information…")
            FormDialog(self, "Update shelter information · "+detail["name"], fields, submit, introduction="Occupancy itself is changed with Change occupancy.")
        self.run(lambda: self.api.call("shelter", {"id": row["id"], "private": True}), ready, "Loading shelter details…")

    def page_alerts(self):
        self.heading("My alerts", "Messages addressed to your account or role, including delivery failures and operational warnings.")
        bar = self.toolbar()
        self.button(bar, "Refresh alerts", lambda: self.page_loader(), True).pack(side="left")
        self.alert_table = RecordTable(self.content, [("created_at", "Time", 210), ("recipient", "Recipient", 180), ("message", "Message", 470)], self.select_alert, height=11)
        self.alert_table.pack(fill="both", expand=True, pady=(0, 14))
        self.alert_detail = self.details(self.content, height=6)
        def load(select_id=None):
            self.run(lambda: self.api.call("alerts"), lambda rows: self.alert_table.show(rows))
        self.page_loader = load
        load()

    def select_alert(self, row):
        self.set_text(self.alert_detail, human_text({"time": date_label(row["created_at"]), "recipient": row["recipient"], "message": row["message"]}) if row else "You have no alerts.")
