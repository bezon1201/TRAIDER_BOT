import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from main import main as bot_main  # твой main() из main.py


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/healthz"):
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")


def run_server():
    port = int(os.environ.get("PORT", "10000"))  # Render отдаёт порт в переменной PORT
    httpd = HTTPServer(("0.0.0.0", port), HealthHandler)
    httpd.serve_forever()


if __name__ == "__main__":
    # 1) поднимаем HTTP-сервер в отдельном потоке
    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    # 2) в главном потоке запускаем бота (тут python-telegram-bot сам создаст event loop)
    bot_main()
