import glob
import os
import tkinter as tk
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

import duckdb


class DataBrowser:
    def __init__(self, root):
        self.root = root
        self.root.title("Data Browser")
        self.root.geometry("1500x1000")
        self.root.configure(bg='#ffffff')

        self.conn = duckdb.connect()
        self.sample_size = 100
        self.files_data = {}
        self.setup_styles()
        self.create_widgets()

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')

        style.configure('Title.TLabel', font=('Inter', 24, 'bold'), background='#ffffff', foreground='#1f2937')
        style.configure('Header.TLabel', font=('Inter', 12, 'bold'), background='#ffffff', foreground='#374151')
        style.configure('Info.TLabel', font=('JetBrains Mono', 11), background='#ffffff', foreground='#6b7280')
        style.configure('Modern.TButton', font=('Inter', 11, 'bold'), padding=(16, 12))
        style.configure('Modern.TFrame', background='#ffffff')
        style.configure('Card.TFrame', background='#f8fafc', relief='solid', borderwidth=1)
        style.configure('Light.TEntry', font=('JetBrains Mono', 11), fieldbackground='#f9fafb', foreground='#111827',
                        borderwidth=1)
        style.configure('Light.TCheckbutton', font=('Inter', 11), background='#ffffff', foreground='#374151')
        style.configure('Light.TNotebook', background='#ffffff', borderwidth=0)
        style.configure('Light.TNotebook.Tab', background='#e5e7eb', foreground='#6b7280', padding=(16, 10),
                        borderwidth=0)

        style.map('Modern.TButton', background=[('active', '#3b82f6'), ('!active', '#2563eb')],
                  foreground=[('active', '#ffffff'), ('!active', '#ffffff')])
        style.map('Light.TNotebook.Tab', background=[('selected', '#2563eb'), ('!selected', '#e5e7eb')],
                  foreground=[('selected', '#ffffff'), ('!selected', '#6b7280')])

        style.configure('Treeview', background='#ffffff', foreground='#111827', fieldbackground='#ffffff',
                        borderwidth=1)
        style.configure('Treeview.Heading', background='#f3f4f6', foreground='#374151', font=('Inter', 11, 'bold'),
                        borderwidth=1)
        style.map('Treeview', background=[('selected', '#dbeafe')], foreground=[('selected', '#1e40af')])

    def find_column_match(self, missing_column, file_columns):
        if not file_columns:
            return None, 0.0

        best_match = None
        best_score = 0.0

        for col in file_columns:
            seq_score = SequenceMatcher(None, missing_column.lower(), col.lower()).ratio()

            substring_bonus = 0.0
            if missing_column.lower() in col.lower() or col.lower() in missing_column.lower():
                substring_bonus = 0.2

            prefix_bonus = 0.0
            if len(missing_column) > 3 and len(col) > 3:
                if missing_column[:3].lower() == col[:3].lower():
                    prefix_bonus = 0.1
                if missing_column[-3:].lower() == col[-3:].lower():
                    prefix_bonus += 0.1

            total_score = min(1.0, seq_score + substring_bonus + prefix_bonus)

            if total_score > best_score and total_score > 0.3:
                best_score = total_score
                best_match = col

        return best_match, best_score

    def create_widgets(self):
        main_frame = ttk.Frame(self.root, style='Modern.TFrame')
        main_frame.pack(fill=tk.BOTH, expand=True, padx=25, pady=25)

        ttk.Label(main_frame, text="⚡ Data Browser", style='Title.TLabel').pack(pady=(0, 30))

        controls_frame = ttk.Frame(main_frame, style='Card.TFrame')
        controls_frame.pack(fill=tk.X, pady=(0, 25), padx=5, ipady=20)

        path_frame = ttk.Frame(controls_frame, style='Card.TFrame')
        path_frame.pack(fill=tk.X, padx=20, pady=(5, 15))

        ttk.Label(path_frame, text="📁 Path", style='Header.TLabel').pack(side=tk.LEFT, padx=(0, 15))
        self.path_var = tk.StringVar()
        self.path_entry = ttk.Entry(path_frame, textvariable=self.path_var, style='Light.TEntry', width=70)
        self.path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 15))
        ttk.Button(path_frame, text="📄 Files", command=self.browse_files, style='Modern.TButton').pack(side=tk.RIGHT,
                                                                                                       padx=(5, 0))
        ttk.Button(path_frame, text="📁 Folder", command=self.browse_folder, style='Modern.TButton').pack(side=tk.RIGHT,
                                                                                                         padx=(5, 0))

        options_frame = ttk.Frame(controls_frame, style='Card.TFrame')
        options_frame.pack(fill=tk.X, padx=20, pady=(0, 15))

        self.sample_var = tk.StringVar(value="100")
        ttk.Label(options_frame, text="📊 Rows", style='Header.TLabel').pack(side=tk.LEFT, padx=(0, 10))
        ttk.Entry(options_frame, textvariable=self.sample_var, width=8, style='Light.TEntry').pack(side=tk.LEFT,
                                                                                                   padx=(0, 20))

        self.use_sample_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="🎲 Sample", variable=self.use_sample_var, style='Light.TCheckbutton').pack(
            side=tk.LEFT, padx=(0, 20))

        ttk.Button(options_frame, text="🚀 Load", command=self.load_files, style='Modern.TButton').pack(side=tk.LEFT,
                                                                                                       padx=(15, 0))
        ttk.Button(options_frame, text="🔄 Reset", command=self.reset_inputs, style='Modern.TButton').pack(side=tk.LEFT,
                                                                                                          padx=(5, 0))

        self.status_var = tk.StringVar(value="💫 Ready to load data...")
        ttk.Label(controls_frame, textvariable=self.status_var, style='Info.TLabel').pack(padx=20, pady=(0, 5))

        self.notebook = ttk.Notebook(main_frame, style='Light.TNotebook')
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.notebook.bind("<Button-3>", self.on_tab_right_click)

    def reset_inputs(self):
        self.path_var.set("")
        self.sample_var.set("100")
        self.use_sample_var.set(True)
        self.status_var.set("💫 Ready to load data...")

        for tab_id in self.notebook.tabs():
            self.notebook.forget(tab_id)
        self.files_data.clear()

    def on_tab_right_click(self, event):
        tab_index = self.notebook.index("@%d,%d" % (event.x, event.y))
        if tab_index >= 0:
            tab_id = self.notebook.tabs()[tab_index]
            tab_text = self.notebook.tab(tab_id, "text")

            if "Schema" not in tab_text:
                self.notebook.forget(tab_id)
                tab_keys_to_remove = []
                for key, data in self.files_data.items():
                    if data.get('tab_index') == tab_index:
                        tab_keys_to_remove.append(key)

                for key in tab_keys_to_remove:
                    del self.files_data[key]

    def browse_files(self):
        files = filedialog.askopenfilenames(
            title="Select Data Files",
            filetypes=[("All supported", "*.parquet *.parq *.csv *.json *.jsonl *.xlsx *.xls *.arrow"),
                       ("All files", "*.*")]
        )
        if files:
            self.path_var.set(";".join(files))

    def browse_folder(self):
        folder = filedialog.askdirectory(title="Select Folder")
        if folder:
            self.path_var.set(os.path.join(folder, "*"))

    def detect_file_type(self, file_path):
        ext = Path(file_path).suffix.lower()
        name = Path(file_path).name.lower()

        if ext in ['.parquet', '.parq']:
            return 'parquet'
        elif ext == '.csv':
            return 'csv'
        elif ext in ['.json', '.jsonl', '.ndjson']:
            return 'json'
        elif ext == '.xlsx':
            return 'excel'
        elif 'delta' in name:
            return 'delta'
        else:
            return 'parquet'

    def get_file_list(self, path_pattern):
        files = []
        patterns = [p.strip() for p in path_pattern.split(';')]

        for pattern in patterns:
            if os.path.isfile(pattern):
                files.append(pattern)
            elif '*' in pattern:
                files.extend(glob.glob(pattern))
            elif os.path.isdir(pattern):
                for ext in ['*.parquet', '*.parq', '*.csv', '*.json', '*.jsonl', '*.xlsx']:
                    files.extend(glob.glob(os.path.join(pattern, ext)))

        return list(set(files))

    def get_scan_query(self, file_path, file_type):
        scan_funcs = {
            'parquet': f"parquet_scan('{file_path}')",
            'csv': f"read_csv_auto('{file_path}', header=true)",
            'json': f"read_json_auto('{file_path}')",
            'excel': f"read_xlsx('{file_path}', header=true, ignore_errors=true)",
            'delta': f"delta_scan('{file_path}')"
        }
        return scan_funcs.get(file_type, scan_funcs['parquet'])

    def load_files(self):
        if not self.path_var.get().strip():
            messagebox.showwarning("Warning", "Enter file path")
            return

        try:
            self.sample_size = int(self.sample_var.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid sample size")
            return

        try:
            self.files_data.clear()
            for tab_id in self.notebook.tabs():
                self.notebook.forget(tab_id)

            files = self.get_file_list(self.path_var.get().strip())
            if not files:
                self.status_var.set("⚠️ No files found")
                return

            self.status_var.set(f"🔍 Found {len(files)} files...")
            self.root.update()

            self.create_all_tabs(files)
            self.create_schema_tab()
            self.populate_data_async()

        except Exception as e:
            messagebox.showerror("Error", str(e))
            self.status_var.set("❌ Error")

    def create_all_tabs(self, files):
        for i, file_path in enumerate(files):
            file_name = os.path.basename(file_path)
            file_type = self.detect_file_type(file_path)

            tab_frame = ttk.Frame(self.notebook, style='Modern.TFrame')
            self.notebook.add(tab_frame, text=f"⏳ {file_name}")

            tab_key = f"tab_{i}_{file_name}"

            loading_label = ttk.Label(tab_frame, text=f"Loading {file_type.upper()}...\n{file_name}",
                                      style='Info.TLabel', anchor=tk.CENTER)
            loading_label.pack(expand=True)

            self.files_data[tab_key] = {
                'file_path': file_path,
                'file_type': file_type,
                'frame': tab_frame,
                'loaded': False,
                'tab_index': i
            }

        self.root.update()

    def create_schema_tab(self):
        if not self.files_data:
            return

        schema_frame = ttk.Frame(self.notebook, style='Modern.TFrame')
        self.notebook.add(schema_frame, text="📋 Schema")

        all_columns = set()
        for data in self.files_data.values():
            try:
                scan_query = self.get_scan_query(data['file_path'], data['file_type'])
                result = self.conn.execute(f"DESCRIBE SELECT * FROM {scan_query} LIMIT 1").fetchall()
                for row in result:
                    all_columns.add(row[0].lower())
            except Exception:
                pass

        if not all_columns:
            ttk.Label(schema_frame, text="No columns found", style='Info.TLabel').pack(expand=True)
            return

        columns = ['Column'] + [os.path.basename(data['file_path']) for data in self.files_data.values()]
        tree = ttk.Treeview(schema_frame, columns=columns, show='headings', height=20)

        tree.tag_configure('diff_row', background='#fef2f2', foreground='#991b1b')
        tree.tag_configure('common_row', background='#ffffff', foreground='#111827')
        tree.tag_configure('separator_row', background='#f3f4f6', foreground='#6b7280')

        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=150)

        v_scroll = ttk.Scrollbar(schema_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=v_scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        file_columns_map = {}
        for data in self.files_data.values():
            try:
                scan_query = self.get_scan_query(data['file_path'], data['file_type'])
                result = self.conn.execute(f"DESCRIBE SELECT * FROM {scan_query} LIMIT 1").fetchall()
                file_columns_map[data['file_path']] = [row[0] for row in result]
            except:
                file_columns_map[data['file_path']] = []

        all_files_columns = []
        some_files_columns = []

        for col_name in sorted(all_columns):
            row_data = [col_name]
            file_count = 0

            for data in self.files_data.values():
                try:
                    file_cols = file_columns_map[data['file_path']]
                    has_column = any(c.lower() == col_name for c in file_cols)
                    if has_column:
                        file_count += 1
                        row_data.append("✓")
                    else:
                        best_match, score = self.find_column_match(col_name, file_cols)
                        if best_match and score >= 0.9:
                            certainty = int(score * 100)
                            row_data.append(f"≈{best_match} ({certainty}%)")
                        else:
                            row_data.append("")
                except Exception:
                    row_data.append("")

            total_files = len(self.files_data)
            if file_count == total_files:
                all_files_columns.append(row_data)
            else:
                some_files_columns.append(row_data)

        for row_data in some_files_columns:
            tree.insert('', tk.END, values=row_data, tags=('diff_row',))

        if some_files_columns and all_files_columns:
            separator = ["─" * 20] + ["─" * 10] * len(self.files_data)
            tree.insert('', tk.END, values=separator, tags=('separator_row',))

        for row_data in all_files_columns:
            tree.insert('', tk.END, values=row_data, tags=('common_row',))

        file_types = {}
        for data in self.files_data.values():
            file_name = os.path.basename(data['file_path'])
            file_types.setdefault(data['file_type'], []).append(file_name)

        type_text = " | ".join([f"{ftype}: {', '.join(files)}" for ftype, files in file_types.items()])
        ttk.Label(schema_frame, text=f"📊 Types: {type_text}", style='Info.TLabel').pack(side=tk.BOTTOM, fill=tk.X,
                                                                                        padx=10, pady=10)

    def populate_data_async(self):
        self.current_file_index = 0
        self.populate_next_file()

    def populate_next_file(self):
        tab_keys = list(self.files_data.keys())

        if self.current_file_index >= len(tab_keys):
            self.status_var.set("✅ All files loaded")
            return

        tab_key = tab_keys[self.current_file_index]
        data = self.files_data[tab_key]

        if not data['loaded']:
            file_name = os.path.basename(data['file_path'])
            self.status_var.set(f"📖 Loading {self.current_file_index + 1}/{len(tab_keys)}: {file_name}")

            try:
                for widget in data['frame'].winfo_children():
                    widget.destroy()

                self.create_data_view(data['frame'], data['file_path'], data['file_type'])

                tab_index = data['tab_index']
                self.notebook.tab(tab_index, text=f"📊 {file_name}")

                data['loaded'] = True
            except Exception:
                pass

        self.current_file_index += 1
        self.root.after(100, self.populate_next_file)

    def create_data_view(self, parent_frame, file_path, file_type):
        info_frame = ttk.Frame(parent_frame, style='Modern.TFrame')
        info_frame.pack(fill=tk.X, padx=10, pady=10)

        stat = os.stat(file_path)
        size = self.format_size(stat.st_size)
        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        info_text = f"📄 {file_path} • {size} • {modified} • {file_type.upper()}"
        ttk.Label(info_frame, text=info_text, style='Info.TLabel').pack(anchor=tk.W)

        data_frame = ttk.Frame(parent_frame, style='Modern.TFrame')
        data_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        try:
            scan_query = self.get_scan_query(file_path, file_type)
            columns_result = self.conn.execute(f"DESCRIBE SELECT * FROM {scan_query} LIMIT 1").fetchall()
            columns = [row[0] for row in columns_result]

            tree = ttk.Treeview(data_frame, columns=columns, show='headings', height=20)
            for col in columns:
                tree.heading(col, text=col)
                tree.column(col, width=150, anchor=tk.W)

            v_scroll = ttk.Scrollbar(data_frame, orient=tk.VERTICAL, command=tree.yview)
            h_scroll = ttk.Scrollbar(data_frame, orient=tk.HORIZONTAL, command=tree.xview)
            tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

            tree.grid(row=0, column=0, sticky='nsew')
            v_scroll.grid(row=0, column=1, sticky='ns')
            h_scroll.grid(row=1, column=0, sticky='ew')
            data_frame.grid_rowconfigure(0, weight=1)
            data_frame.grid_columnconfigure(0, weight=1)

            query_type = "USING SAMPLE" if self.use_sample_var.get() else "LIMIT"
            query = f"SELECT * FROM {scan_query} {query_type} {self.sample_size}"
            result = self.conn.execute(query).fetchall()

            for row in result:
                formatted_row = [str(val)[:100] if val is not None else "NULL" for val in row]
                tree.insert('', tk.END, values=formatted_row)

        except Exception as e:
            error_label = ttk.Label(data_frame, text=f"❌ Error: {str(e)}", style='Info.TLabel')
            error_label.grid(row=0, column=0, sticky='nsew')

    def format_size(self, size_bytes):
        if size_bytes == 0: return "0 B"
        size_names = ["B", "KB", "MB", "GB", "TB"]
        i = 0
        while size_bytes >= 1024 and i < len(size_names) - 1:
            size_bytes /= 1024.0
            i += 1
        return f"{size_bytes:.1f} {size_names[i]}"

    def on_closing(self):
        try:
            self.conn.close()
        except:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    app = DataBrowser(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    from common.utils.envsetup import init_app_using_parser

    init_app_using_parser()
    main()