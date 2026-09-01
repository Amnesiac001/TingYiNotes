from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .app import process_course
from .live import AudioDevice, LiveCourseSession, list_input_devices
from .branding import APP_NAME, APP_TITLE
from .models import Segment


class ClassNoteWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1080x760")
        self.minsize(850, 620)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.live_session: LiveCourseSession | None = None
        self.live_paused = False
        self.devices: list[AudioDevice] = []

        self.file_var = tk.StringVar()
        self.title_var = tk.StringVar(value="英语课堂")
        self.subject_var = tk.StringVar(value="通用课程")
        self.device_var = tk.StringVar()
        self.status_var = tk.StringVar(value="可以运行离线演示，或配置 API 密钥后开始实时课堂。")
        self.partial_var = tk.StringVar(value="")
        self._build()
        self._refresh_devices()
        self.after(100, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(6, weight=1)

        ttk.Label(root, text="课程名称").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(root, textvariable=self.title_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(root, text="课程领域").grid(row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Entry(root, textvariable=self.subject_var, width=24).grid(row=0, column=3, sticky="ew", padx=8)

        live_box = ttk.LabelFrame(root, text="实时课堂（麦克风）", padding=10)
        live_box.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 5))
        live_box.columnconfigure(1, weight=1)
        ttk.Label(live_box, text="输入设备").grid(row=0, column=0, sticky="w")
        self.device_combo = ttk.Combobox(live_box, textvariable=self.device_var, state="readonly")
        self.device_combo.grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(live_box, text="刷新设备", command=self._refresh_devices).grid(row=0, column=2)
        self.live_start_button = ttk.Button(live_box, text="开始实时课堂", command=self._start_live)
        self.live_start_button.grid(row=0, column=3, padx=(12, 4))
        self.pause_button = ttk.Button(live_box, text="暂停", command=self._toggle_pause, state="disabled")
        self.pause_button.grid(row=0, column=4, padx=4)
        self.stop_button = ttk.Button(live_box, text="结束并整理", command=self._stop_live, state="disabled")
        self.stop_button.grid(row=0, column=5, padx=4)

        file_box = ttk.LabelFrame(root, text="已有录音文件", padding=10)
        file_box.grid(row=2, column=0, columnspan=4, sticky="ew", pady=5)
        file_box.columnconfigure(1, weight=1)
        ttk.Label(file_box, text="文件").grid(row=0, column=0, sticky="w")
        ttk.Entry(file_box, textvariable=self.file_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(file_box, text="选择文件", command=self._choose_file).grid(row=0, column=2)
        self.process_button = ttk.Button(file_box, text="处理文件", command=lambda: self._start_file(False))
        self.process_button.grid(row=0, column=3, padx=(12, 4))
        self.demo_button = ttk.Button(file_box, text="离线演示", command=lambda: self._start_file(True))
        self.demo_button.grid(row=0, column=4, padx=4)

        ttk.Label(root, textvariable=self.status_var, foreground="#155e75").grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(8, 5)
        )
        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(0, 8))

        ttk.Label(
            root,
            textvariable=self.partial_var,
            foreground="#7c3aed",
            font=("Microsoft YaHei UI", 10),
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(0, 6))

        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=6, column=0, columnspan=4, sticky="nsew")
        self.transcript_tab = ttk.Frame(self.notebook)
        self.notes_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.transcript_tab, text="实时英中对照")
        self.notebook.add(self.notes_tab, text="整理后的笔记")
        self.transcript_tab.rowconfigure(0, weight=1)
        self.transcript_tab.columnconfigure(0, weight=1)
        self.notes_tab.rowconfigure(0, weight=1)
        self.notes_tab.columnconfigure(0, weight=1)
        self.transcript = tk.Text(
            self.transcript_tab, wrap="word", font=("Microsoft YaHei UI", 10), padx=12, pady=12
        )
        self.transcript.grid(row=0, column=0, sticky="nsew")
        self.notes = tk.Text(
            self.notes_tab, wrap="word", font=("Microsoft YaHei UI", 10), padx=12, pady=12
        )
        self.notes.grid(row=0, column=0, sticky="nsew")

    def _refresh_devices(self) -> None:
        try:
            self.devices = list_input_devices()
            labels = [device.label for device in self.devices]
            self.device_combo["values"] = labels
            if labels:
                self.device_combo.current(0)
                self.status_var.set(f"找到 {len(labels)} 个麦克风输入设备。")
            else:
                self.device_var.set("")
                self.status_var.set("没有找到麦克风，请检查 Windows 隐私权限和设备连接。")
        except Exception as exc:
            self.status_var.set(f"读取麦克风失败：{exc}")

    def _selected_device(self) -> AudioDevice | None:
        index = self.device_combo.current()
        return self.devices[index] if 0 <= index < len(self.devices) else None

    def _validate_title(self) -> bool:
        if not self.title_var.get().strip():
            messagebox.showwarning("缺少课程名称", "请填写课程名称。")
            return False
        return True

    def _start_live(self) -> None:
        if not self._validate_title():
            return
        device = self._selected_device()
        if device is None:
            messagebox.showwarning("缺少麦克风", "请选择一个麦克风输入设备。")
            return
        try:
            self.transcript.delete("1.0", "end")
            self.notes.delete("1.0", "end")
            self.live_session = LiveCourseSession(
                self.title_var.get().strip(),
                self.subject_var.get().strip() or "通用课程",
                device,
                lambda event, payload: self.events.put((event, payload)),
            )
            self.live_session.start()
            self.live_start_button.state(["disabled"])
            self.pause_button.state(["!disabled"])
            self.stop_button.state(["!disabled"])
            self.process_button.state(["disabled"])
            self.demo_button.state(["disabled"])
            self.progress.start(12)
        except Exception as exc:
            self.live_session = None
            messagebox.showerror("无法开始实时课堂", str(exc))

    def _toggle_pause(self) -> None:
        if self.live_session is None:
            return
        if self.live_paused:
            self.live_session.resume()
            self.pause_button.configure(text="暂停")
        else:
            self.live_session.pause()
            self.pause_button.configure(text="继续")
        self.live_paused = not self.live_paused

    def _stop_live(self) -> None:
        if self.live_session is None:
            return
        self.pause_button.state(["disabled"])
        self.stop_button.state(["disabled"])
        self.live_session.stop()

    def _choose_file(self) -> None:
        filename = filedialog.askopenfilename(
            title="选择课堂录音",
            filetypes=[
                ("音频和视频", "*.mp3 *.wav *.m4a *.mp4 *.mpeg *.mpga *.ogg *.webm *.flac"),
                ("所有文件", "*.*"),
            ],
        )
        if filename:
            self.file_var.set(filename)
            if self.title_var.get() == "英语课堂":
                self.title_var.set(Path(filename).stem)

    def _start_file(self, demo: bool) -> None:
        if not self._validate_title():
            return
        if not demo and not self.file_var.get().strip():
            messagebox.showwarning("缺少文件", "请先选择英文课堂音频或视频文件。")
            return
        self._set_file_busy(True)
        self.transcript.delete("1.0", "end")
        self.notes.delete("1.0", "end")
        threading.Thread(target=self._file_worker, args=(demo,), daemon=True).start()

    def _file_worker(self, demo: bool) -> None:
        try:
            audio = Path(__file__) if demo else Path(self.file_var.get())
            result, exported = process_course(
                audio,
                self.title_var.get().strip(),
                self.subject_var.get().strip() or "通用课程",
                demo=demo,
                progress=lambda message: self.events.put(("status", message)),
            )
            self.events.put(("file_done", (result, exported)))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _append_segment(self, segment: Segment) -> None:
        seconds = segment.start_ms // 1000
        stamp = f"{seconds // 60:02d}:{seconds % 60:02d}"
        self.transcript.insert("end", f"[{stamp}] English\n{segment.original_text}\n\n")
        self.transcript.insert("end", f"[{stamp}] 中文\n{segment.translated_text}\n\n")
        self.transcript.see("end")

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "status":
                    self.status_var.set(str(payload))
                elif event == "segment":
                    self._append_segment(payload)  # type: ignore[arg-type]
                elif event == "partial":
                    _item_id, partial_text = payload
                    self.partial_var.set(f"识别中：{partial_text}")
                elif event == "final_original":
                    _item_id, original, _start = payload
                    self.partial_var.set(f"正在翻译：{original}")
                elif event == "partial_clear":
                    self.partial_var.set("")
                elif event == "warning":
                    self.status_var.set(str(payload))
                    self.transcript.insert("end", f"⚠ {payload}\n\n")
                elif event == "finished":
                    result, exported = payload
                    self.notes.delete("1.0", "end")
                    self.notes.insert("1.0", result.notes_markdown)
                    self.notebook.select(self.notes_tab)
                    self.status_var.set(f"实时课程完成，笔记已导出：{exported}")
                    self._reset_live()
                elif event == "file_done":
                    result, exported = payload
                    for segment in result.segments:
                        self._append_segment(segment)
                    self.notes.insert("1.0", result.notes_markdown)
                    self.notebook.select(self.notes_tab)
                    self.status_var.set(f"处理完成，笔记已导出：{exported}")
                    self._set_file_busy(False)
                elif event == "error":
                    self.status_var.set("处理失败或未完整完成。")
                    messagebox.showerror(APP_NAME, str(payload))
                    self._set_file_busy(False)
                    self._reset_live()
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _set_file_busy(self, busy: bool) -> None:
        state = ["disabled"] if busy else ["!disabled"]
        self.process_button.state(state)
        self.demo_button.state(state)
        self.live_start_button.state(state)
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _reset_live(self) -> None:
        self.live_session = None
        self.live_paused = False
        self.partial_var.set("")
        self.progress.stop()
        self.pause_button.configure(text="暂停")
        self.pause_button.state(["disabled"])
        self.stop_button.state(["disabled"])
        self.live_start_button.state(["!disabled"])
        self.process_button.state(["!disabled"])
        self.demo_button.state(["!disabled"])

    def _close(self) -> None:
        if self.live_session is not None:
            if not messagebox.askyesno("正在录音", "关闭窗口会停止录音。确定关闭吗？"):
                return
            self.live_session.stop()
        self.destroy()


def main() -> None:
    ClassNoteWindow().mainloop()


if __name__ == "__main__":
    main()
