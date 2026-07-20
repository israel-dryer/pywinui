"""Async PyWinUI example: an `async def` click handler that awaits a real WinRT
async operation and writes the result back into the UI.

Verified working on Windows 11 / winui3 3.2.1 / python.org Python 3.13.

Three things are happening implicitly here, all covered in AGENTS.md §2.8:
  * the handler is `async def` — it's auto-scheduled on the background loop
    instead of blocking the UI thread,
  * `await`ing a WinRT IAsyncOperation yields a normal Python value (and raises
    normal Python exceptions — a missing file surfaces as FileNotFoundError),
  * `status.text = ...` happens off the UI thread and is auto-marshalled back
    via the DispatcherQueue.

    python examples/async_file_read.py
"""
from winrt.windows.storage import FileIO, StorageFile

import pywinui as ui

HOSTS = r"C:\Windows\System32\drivers\etc\hosts"


class AsyncApp(ui.App):
    def build(self):
        status = ui.TextBlock("Press the button to read a file.", font_size=16)

        async def read_file(sender, args):
            status.text = "Reading..."
            try:
                file = await StorageFile.get_file_from_path_async(HOSTS)
                text = await FileIO.read_text_async(file)
            except OSError as exc:  # WinRT errors arrive as ordinary exceptions
                status.text = f"Failed: {exc}"
                return
            lines = len(text.splitlines())
            status.text = f"Read {file.name}: {lines} lines, {len(text)} chars"

        return ui.Window(
            title="PyWinUI Async",
            content=ui.StackPanel(
                spacing=12, padding=24,
                children=[
                    ui.TextBlock("Async file read", font_size=20),
                    status,
                    ui.Button("Read hosts file", on_click=read_file),
                ],
            ),
        )


if __name__ == "__main__":
    AsyncApp().run()