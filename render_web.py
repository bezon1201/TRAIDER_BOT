import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from main import main as bot_main  # твой основной main из main.py


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # простой healthcheck: / или /healthz → 200 OK
        if self.path not in ("/", "/healthz"):
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")


def run_bot():
    # блокирующий long polling в отдельном потоке
    bot_main()


def run_server():
    port = int(os.environ.get("PORT", "10000"))  # Render ожидает, что ты слушаешь этот порт
    httpd = HTTPServer(("0.0.0.0", port), HealthHandler)
    httpd.serve_forever()


if __name__ == "__main__":
    # запускаем бота в фоне
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()

    # а в главном потоке — HTTP-сервер для Render
    run_server()
