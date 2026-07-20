"""Minimal PyWinUI counter app (Flutter-style tree).

Verified working on Windows 11 / winui3 3.2.1 / python.org Python 3.13.

    python examples/counter.py

Requires the Windows App Runtime and the winui3 wheels — if they're missing,
`_Native.load()` raises with the exact pip install list.
"""
import pywinui as ui


class CounterApp(ui.App):
    def build(self):
        count = ui.TextBlock("0", font_size=32)

        def bump(sender, args):
            count.text = str(int(count.text) + 1)

        return ui.Window(
            title="PyWinUI Counter",
            content=ui.StackPanel(
                spacing=12, padding=24,
                children=[
                    ui.TextBlock("Counter", font_size=20),
                    count,
                    ui.Button("Increment", on_click=bump),
                ],
            ),
        )


if __name__ == "__main__":
    CounterApp().run()