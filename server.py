import os
import shutil
import socket

from typing import Optional

# Как выглядит НАШ протокол, просто пример,
# эта переменная нигде не используется в этом коде:
template = (
    b"Keep-Alive=1\r\n"  # 0 или 1
    b"Content-Length=2659\r\n"  # сколько байт данных
    b"Content-Type=text\r\n"  # text или file
    b"Path=/path/to/dir/or/file\r\n"  # Путь к чему-то
    b"Status=200\r\n\r\n"  # 200 успех, 404 - не найдено, 400 не кооректный запрос
    b"body"
)  # тело запроса
# одиночный \r\n - разделитель между разными значениями заголовков;
# двойной \r\n\r\n - разделитель заголовков, от самого тела запроса (то что передается)


IPv = socket.AF_INET
SOCK_TYPE = socket.SOCK_STREAM

HOST = "127.0.0.1"
PORT = 8080

CONNECTIONS = 1
CLIENT_TIMEOUT = 1000      # 3

STORAGE = "C:\\course_67\\internet\\storage\\"  # Ограничим доступ клиенту этим.


def fix_path_header(headers: dict[str, str]):
    """Функция приводит в понятный для сервера путь от клиента
    в словаре заголовков.
    Если путь содержит метку тома, вроде C:, то
    вырежет это из пути, чтоб мы могли с этим работать. Так как
    для пользователя "Файловая система сервера" ограничена STORAGE.
    И не правильно переданный путь будет интепретироваться нами как:
    STORAGE + путь от клиента."""

    user_path = headers.get("Path", "")
    # Если такого заголовка нет, то чистим headers и 400 код отправим.
    if not user_path:
        headers.clear()
        return
    # Если есть ":" в пути, значит там написан путь от метки тома, просто
    # уберем всё до (включительно) двоеточия, и такого пути просто на сервере
    # не окажется для пользователя, пользователь получит 404 код.
    if ":" in user_path:
        user_path = user_path[user_path.index(":") + 1 :]

    # dict - изменяемый объект, эти изменения, будут актуальны вне этой функции.
    headers["Path"] = user_path


def list_directory(path):
    # Просто формируем через \n "список" того что есть в директории.
    try:
        items = []
        for item in os.scandir(path):
            # Отображение пользователю пути только внутри STORAGE:
            items.append(item.path.replace(STORAGE, ""))
        return "\n".join(items).encode()
    except Exception as e:
        return str(e).encode()


def send_file(conn: socket.socket, file_path: str):
    # Вдруг файла не стало только что, на всякий случай.
    if not os.path.exists(file_path):
        send_response(conn, b"File not found", status=404)
        return

    # Нам для Content-Length нужен размер файла.
    # Размер известен, т.к. файл отправляется без изменений!!!!!
    size = os.path.getsize(file_path)

    headers = (
        f"Keep-Alive=1\r\n"
        f"Status=200\r\n"
        f"Content-Length={size}\r\n"
        f"Content-Type=file\r\n"
        f"\r\n"
    ).encode()

    # Кидаем заголовки.
    conn.sendall(headers)

    # "Туда же" следом отправляем файл.
    with open(file_path, "rb") as f:
        conn.sendfile(f)


def send_response(
    conn: socket.socket, data: bytes, status=200, data_type="text", keep_alive=1
):
    # Этой функцией мы шлем любые сообщения, поэтому тут data_type="text"
    headers = (
        f"Keep-Alive={keep_alive}\r\n"
        f"Status={status}\r\n"
        f"Content-Length={len(data)}\r\n"
        f"Content-Type={data_type}\r\n"
        f"\r\n"
    ).encode()

    conn.sendall(headers)

    # Сообщение может быть разной длины, например содержимое
    # какой-то директории модет быть большим "списком".
    # Давайте для красоты тут циклом напишем.
    start, chunk_size = 0, 1024
    while start < len(data):
        conn.sendall(data[start : start + chunk_size])
        start += chunk_size


def handle_request(conn: socket.socket, headers: dict, temp_path: Optional[str] = None):
    content_type = headers.get("Content-Type", "text")

    if content_type == "file":
        file = headers.get("Path", "unknown.bin").strip("/\\")
        file = os.path.join(STORAGE, file)
        # PermissionError быть не может, мы пускаем юзера только в STORAGE.
        os.makedirs(os.path.dirname(file), exist_ok=True)
        shutil.move(temp_path, file)
        send_response(conn, b"File uploaded to: " + file.encode())
        return

    # Если клиент отправляет text, а не file, мы это видим по headers.
    path = headers.get("Path", None)
    if not path:
        send_response(conn, b"Bad request", status=400)
        return
    else:
        path = os.path.join(STORAGE, path.strip("/\\"))

    if not os.path.exists(path):
        send_response(conn, b"Path not found", status=404)
        return

    # Если клиент прислал путь к директории, то отдаем ему информацию
    # о содержимом этой директории.
    if os.path.isdir(path):
        listing = list_directory(path)
        send_response(conn, listing)
        return

    # Если клиент прислал путь к файлу, то отдаем ему сам файл.
    if os.path.isfile(path):
        send_file(conn, path)
        return


def get_request(conn: socket.socket):
    buffer = b""

    # Читаем заголовки тоже циклично из общего "потока" байт
    while b"\r\n\r\n" not in buffer:  # пока не встретим \r\n\r\n
        chunk = conn.recv(1024)
        if not chunk:
            return {}, False, None
        buffer += chunk

    # Берем байты заголовков в headers, а часть самого запроса (если есть) в body.
    headers_raw, body = buffer.split(b"\r\n\r\n", 1)

    # Делаем из байт заголовка словарь.
    headers = {}
    for line in headers_raw.split(b"\r\n"):
        key, value = line.split(b"=", 1)
        headers[key.decode()] = value.decode()  # как строки удобнее вроде.

    # Добавил тут сразу обработку пути от пользователя из заголовка Path.
    # Чтоб проверить нет ли там С:\ и подобного. Если есть, то просто уберем,
    # и в итоге пользователь получит 404 NotFound. Заствляем его работать от /.
    fix_path_header(headers)
    if not headers:
        send_response(conn, b"Bad request", status=400)
        return {}, False, None

    keep_alive = bool(int(headers.get("Keep-Alive", "0")))
    content_type = headers.get("Content-Type", "text")

    if content_type == "text":
        return headers, keep_alive, None

    # Тут начинается чтение и сохранение body запроса (так как юзер шлет файл).
    # Байты файла сразу пишем на диск, во ВРЕМЕННЫЙ файл,
    # чтоб не грузить всё в переменную.
    # Код ниже можно выделить в отдельную функцию. Но в этом масштабе наверно не нужно.
    content_length = int(headers.get("Content-Length", "0"))  # Можно(нужно) ограничить max размер.

    temp_path = os.path.join(STORAGE, "temp.tmp")
    with open(temp_path, "wb") as f:
        f.write(body[:content_length])  # Защита от "лишних" байт.

        while f.tell() < content_length:
            remaining = content_length - f.tell()
            chunk = conn.recv(min(1024 * 1024, remaining))  # Защита от "лишних" байт.
            if not chunk:
                # Просто очистим содержимое временного файла,
                # если передача не удалась. То что он пустой останется не страшно.
                f.seek()
                f.truncate()
                return {}, False, None
            f.write(chunk)

    return headers, keep_alive, temp_path


def handle_connection(conn: socket.socket, addr: tuple):
    try:
        keep_alive = True
        while keep_alive:
            headers, keep_alive, temp_path = get_request(conn)
            if not headers:
                raise ConnectionError("The client suddenly shut down.")
            handle_request(conn, headers, temp_path)

    except socket.timeout as e:
        print(f"{e}, client - {addr}")
    except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError) as e:
        print("Connection was reset...")
    except ConnectionError as e:
        print(e)
    finally:
        conn.close()
        print(f"This connection closed with client {addr}!")


def get_connections(sock: socket.socket):
    while True:
        conn, addr = sock.accept()
        conn.settimeout(CLIENT_TIMEOUT)
        handle_connection(conn, addr)


def main():
    with socket.socket(IPv, SOCK_TYPE) as sock:
        sock.bind((HOST, PORT))
        sock.listen(CONNECTIONS)
        print("Server started, waiting for connection...")
        get_connections(sock)


if __name__ == "__main__":
    main()
