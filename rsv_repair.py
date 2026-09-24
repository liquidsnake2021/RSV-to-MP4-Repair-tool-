"""
Sony RSV → MP4 Repair Tool (GUI)

GUI wrapper around build_moov.py — repairs unfinalized Sony .RSV video files.
No donor file needed. Just drop the RSV and click Repair.

Requires: Python 3.8+
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk

try:
    import windnd
    HAS_WINDND = True
except ImportError:
    HAS_WINDND = False

# Import repair engine from same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_moov import build_mp4_standalone


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class RSVRepairApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Sony RSV Repair Tool")
        self.root.geometry("750x550")
        self.root.minsize(650, 450)
        self.root.configure(bg="#1e1e1e")

        self.repairing = False
        self.output_path = None

        self._build_ui()
        self._setup_drag_and_drop()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        bg = "#1e1e1e"
        fg = "#d4d4d4"
        accent = "#0078d4"
        btn_bg = "#2d2d2d"
        entry_bg = "#2d2d2d"
        font_main = ("Segoe UI", 10)
        font_title = ("Segoe UI", 16, "bold")
        font_mono = ("Consolas", 9)

        # ── Title ──
        title_frame = tk.Frame(self.root, bg=bg)
        title_frame.pack(fill=tk.X, padx=24, pady=(18, 0))
        tk.Label(
            title_frame, text="Sony RSV → MP4 Repair",
            font=font_title, fg="#ffffff", bg=bg
        ).pack(anchor=tk.W)
        tk.Label(
            title_frame,
            text="Drag & drop or browse for an .RSV file, then click Repair.",
            font=font_main, fg="#888888", bg=bg
        ).pack(anchor=tk.W, pady=(2, 0))

        # ── RSV file input ──
        rsv_frame = tk.Frame(self.root, bg=bg)
        rsv_frame.pack(fill=tk.X, padx=24, pady=(18, 0))
        tk.Label(rsv_frame, text="RSV File:", font=font_main, fg=fg, bg=bg).pack(anchor=tk.W)

        rsv_row = tk.Frame(rsv_frame, bg=bg)
        rsv_row.pack(fill=tk.X, pady=(3, 0))

        self.rsv_var = tk.StringVar()
        self.rsv_entry = tk.Entry(
            rsv_row, textvariable=self.rsv_var,
            font=font_main, bg=entry_bg, fg=fg,
            insertbackground=fg, relief=tk.FLAT,
            highlightthickness=1, highlightcolor=accent, highlightbackground="#3e3e3e"
        )
        self.rsv_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)

        self.rsv_browse_btn = tk.Button(
            rsv_row, text="Browse...", font=font_main,
            bg=btn_bg, fg=fg, relief=tk.FLAT, cursor="hand2",
            activebackground="#3e3e3e", activeforeground=fg,
            command=self._browse_rsv
        )
        self.rsv_browse_btn.pack(side=tk.RIGHT, padx=(8, 0), ipady=2)

        # ── Output folder ──
        out_frame = tk.Frame(self.root, bg=bg)
        out_frame.pack(fill=tk.X, padx=24, pady=(12, 0))
        tk.Label(out_frame, text="Output Folder:", font=font_main, fg=fg, bg=bg).pack(anchor=tk.W)

        out_row = tk.Frame(out_frame, bg=bg)
        out_row.pack(fill=tk.X, pady=(3, 0))

        self.out_var = tk.StringVar()
        self.out_entry = tk.Entry(
            out_row, textvariable=self.out_var,
            font=font_main, bg=entry_bg, fg=fg,
            insertbackground=fg, relief=tk.FLAT,
            highlightthickness=1, highlightcolor=accent, highlightbackground="#3e3e3e"
        )
        self.out_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)

        self.out_browse_btn = tk.Button(
            out_row, text="Browse...", font=font_main,
            bg=btn_bg, fg=fg, relief=tk.FLAT, cursor="hand2",
            activebackground="#3e3e3e", activeforeground=fg,
            command=self._browse_output
        )
        self.out_browse_btn.pack(side=tk.RIGHT, padx=(8, 0), ipady=2)

        tk.Label(out_frame, text="Leave empty to save next to the RSV file.",
                 font=("Segoe UI", 9), fg="#666666", bg=bg).pack(anchor=tk.W, pady=(2, 0))

        # ── Repair button ──
        btn_frame = tk.Frame(self.root, bg=bg)
        btn_frame.pack(fill=tk.X, padx=24, pady=(16, 0))

        self.repair_btn = tk.Button(
            btn_frame, text="  ▶  Repair  ", font=("Segoe UI", 12, "bold"),
            bg=accent, fg="#ffffff", relief=tk.FLAT, cursor="hand2",
            activebackground="#005a9e", activeforeground="#ffffff",
            command=self._start_repair
        )
        self.repair_btn.pack(anchor=tk.W, ipady=5, ipadx=12)

        # ── Progress bar ──
        style = ttk.Style()
        style.theme_use('default')
        style.configure("TProgressbar", troughcolor="#2d2d2d", background=accent,
                         thickness=6)

        self.progress = ttk.Progressbar(
            self.root, style="TProgressbar", mode='indeterminate'
        )
        self.progress.pack(fill=tk.X, padx=24, pady=(10, 0))

        # ── Log ──
        log_frame = tk.Frame(self.root, bg=bg)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=24, pady=(10, 0))

        tk.Label(log_frame, text="Log:", font=font_main, fg=fg, bg=bg).pack(anchor=tk.W)

        self.log_text = scrolledtext.ScrolledText(
            log_frame, font=font_mono, bg="#1a1a1a", fg="#cccccc",
            insertbackground=fg, relief=tk.FLAT, wrap=tk.WORD,
            highlightthickness=1, highlightcolor=accent, highlightbackground="#3e3e3e",
            state=tk.DISABLED
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(3, 0))

        # ── Bottom bar ──
        bottom_frame = tk.Frame(self.root, bg=bg)
        bottom_frame.pack(fill=tk.X, padx=24, pady=(6, 16))

        self.open_folder_btn = tk.Button(
            bottom_frame, text="Open Output Folder", font=font_main,
            bg=btn_bg, fg=fg, relief=tk.FLAT, cursor="hand2",
            activebackground="#3e3e3e", activeforeground=fg,
            command=self._open_output_folder, state=tk.DISABLED
        )
        self.open_folder_btn.pack(side=tk.LEFT)

        self.status_label = tk.Label(
            bottom_frame, text="Ready", font=font_main, fg="#888888", bg=bg
        )
        self.status_label.pack(side=tk.RIGHT)

    # ── Drag and drop ──

    def _setup_drag_and_drop(self):
        if not HAS_WINDND:
            return
        try:
            windnd.hook_dropfiles(self.root, func=self._on_drop)
        except Exception:
            pass

    def _on_drop(self, file_list):
        if self.repairing or not file_list:
            return
        path = file_list[0]
        if isinstance(path, bytes):
            path = path.decode("utf-8", errors="replace")
        path = path.strip()
        if os.path.isfile(path):
            self.rsv_var.set(path)
            self._log(f"File loaded: {os.path.basename(path)}")

    # ── Browse dialog ──

    def _browse_rsv(self):
        path = filedialog.askopenfilename(
            title="Select RSV File",
            filetypes=[("Sony RSV files", "*.rsv"), ("All files", "*.*")]
        )
        if path:
            self.rsv_var.set(path)

    def _browse_output(self):
        folder = filedialog.askdirectory(title="Select Output Folder")
        if folder:
            self.out_var.set(folder)

    # ── Logging ──

    def _log(self, message):
        """Thread-safe log append."""
        def _append():
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        self.root.after(0, _append)

    def _set_status(self, text):
        self.root.after(0, lambda: self.status_label.configure(text=text))

    def _set_repairing(self, active):
        def _update():
            self.repairing = active
            state = tk.DISABLED if active else tk.NORMAL
            self.repair_btn.configure(state=state)
            self.rsv_browse_btn.configure(state=state)
            self.out_browse_btn.configure(state=state)
            self.rsv_entry.configure(state="disabled" if active else tk.NORMAL)
            self.out_entry.configure(state="disabled" if active else tk.NORMAL)
            if active:
                self.progress.start(15)
            else:
                self.progress.stop()
        self.root.after(0, _update)

    # ── Repair ──

    def _start_repair(self):
        rsv_path = self.rsv_var.get().strip()

        # Validation
        if not rsv_path:
            self._log("Error: Please select an RSV file.")
            return
        if not os.path.isfile(rsv_path):
            self._log(f"Error: File not found: {rsv_path}")
            return
        if not rsv_path.lower().endswith('.rsv'):
            self._log("Warning: File does not have .rsv extension. Proceeding anyway...")
        if self.repairing:
            return

        # Clear log
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.open_folder_btn.configure(state=tk.DISABLED)
        self.output_path = None

        self._set_repairing(True)
        self._set_status("Repairing...")

        thread = threading.Thread(
            target=self._run_repair, args=(rsv_path,), daemon=True
        )
        thread.start()

    def _run_repair(self, rsv_path):
        try:
            # Determine output path
            out_dir = self.out_var.get().strip()
            if out_dir:
                if not os.path.isdir(out_dir):
                    self._log(f"Error: Output folder does not exist: {out_dir}")
                    return
                basename = os.path.splitext(os.path.basename(rsv_path))[0]
                base = os.path.join(out_dir, basename)
            else:
                base = os.path.splitext(rsv_path)[0]

            output_path = base + '_repaired.mp4'
            counter = 1
            while os.path.exists(output_path):
                output_path = f"{base}_repaired_{counter}.mp4"
                counter += 1

            build_mp4_standalone(rsv_path, output_path, log_fn=self._log)

            self.output_path = output_path
            self._log("")
            self._log("=" * 50)
            self._log("REPAIR SUCCESSFUL!")
            self._log(f"Output: {output_path}")
            self._set_status("Repair complete!")
            self.root.after(0, lambda: self.open_folder_btn.configure(state=tk.NORMAL))

        except Exception as e:
            self._log(f"\nError: {e}")
            self._set_status("Repair failed.")
        finally:
            self._set_repairing(False)

    def _open_output_folder(self):
        if self.output_path and os.path.exists(self.output_path):
            folder = os.path.dirname(self.output_path)
            os.startfile(folder)

    def _on_close(self):
        """Handle window close event to ensure clean exit with zero background processes."""
        self.repairing = False
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    root = tk.Tk()
    app = RSVRepairApp(root)
    try:
        root.mainloop()
    finally:
        os._exit(0)


if __name__ == "__main__":
    main()
