import os
import shutil
import socket
import select

from typing import Optional


IPv = socket.AF_INET
SOCK_TYPE = socket.SOCK_STREAM

HOST = "127.0.0.1"
PORT = 8080

CONNECTIONS = 1  # Количество одновременных подключений (пока 1).
TIMEOUT = 5  # Таймаут во время запроса/ответа.
WAIT_FOR_REQUEST = 60  # Для select (ждем запрос или socket.timeout).

STORAGE = "c:/users/PyHS/Desktop/storage"  # Ограничим доступ клиенту этим.


def fix_path(path: str) -> None:
    """Функция приводит в понятный для сервера путь от клиента.
    Если путь содержит метку тома, вроде C:, то
    вырежет это из пути, чтоб мы могли с этим работать. Так как
    для пользователя "Файловая система сервера" ограничена STORAGE.
    И неправильного пути просто не будет существовать:
    STORAGE + путь от клиента."""

    user_path = path

    # Если есть ":" в пути, значит там написан путь от метки тома, просто
    # уберем всё до (включительно) двоеточия, и такого пути просто на сервере
    # не окажется для пользователя, пользователь получит 404 код.
    if ":" in user_path:
        user_path = user_path[
            user_path.index(":") + 1 :
        ]  # В пути может быть только 1 : после метки тома.

    return user_path


def list_directory(path: str) -> bytes:
    # Просто формируем через \n "список" того что есть в директории.
    items = []
    for item in os.scandir(path):
        # Отображение пользователю пути только внутри STORAGE:
        items.append(item.path.replace(STORAGE, ""))
    return "\n".join(items).encode()


def send_file(conn: socket.socket, file_path: str) -> None:
    # Вдруг файла не стало только что, на всякий случай.
    if not os.path.exists(file_path):
        send_response(
            conn, status=404
        )  # Без body, просто статуса в заголовках достаточно.
        return

    # Нам для Content-Length нужен размер файла.
    # Размер известен, т.к. файл отправляется без изменений!!!!!
    size = os.path.getsize(file_path)

    headers = (
        f"Keep-Alive=1\r\n"
        f"Status=200\r\n"
        f"Content-Length={size}\r\n"
        f"Command=dd\r\n"
        f"\r\n"
    ).encode()

    # Кидаем заголовки.
    conn.sendall(headers)

    # "Туда же" следом отправляем файл.
    with open(file_path, "rb") as f:
        conn.sendfile(f)


def send_response(
    conn: socket.socket, answer_for: str, data: bytes = b"", status=200, keep_alive=1
) -> None:

    headers = (
        f"Keep-Alive={keep_alive}\r\n"
        f"Status={status}\r\n"
        f"Content-Length={len(data)}\r\n"
        f"Command={answer_for}\r\n"
        f"\r\n"
    ).encode()

    conn.sendall(headers)
    # Если data = b"" то body не будет.

    # Сообщение может быть разной длины, например содержимое
    # какой-то директории модет быть большим "списком".
    # Давайте для красоты тут циклом напишем.
    start, chunk_size = 0, 1024
    while start < len(data):
        conn.sendall(data[start : start + chunk_size])
        start += chunk_size


def handle_request(
    conn: socket.socket, headers: dict, temp_path: Optional[str] = None
) -> None:
    """Здесь проверяем на валидность все заголовки, обрабатываем информацию
    и делаем все нужные действия."""

    keep_alive = True

    try:
        keep_alive = headers["Keep-Alive"]
        command = headers["Command"]
        path = headers["Path"]
    except KeyError:
        send_response(
            conn, "", status=400
        )  # если ошибки в заголовках сообщаем юзеру и идем слушать по новой
        return keep_alive

    path = fix_path(path)  # если путь виндовский, то исправляем
    path = f"{STORAGE}/{path}".strip("/\\")

    if path == temp_path:  # чтоб случайно не стереть файл
        send_response(conn, command, status=400)
        return keep_alive

    path_is_dir = os.path.isdir(path)
    path_is_file = os.path.isfile(path)

    if command == "cd":  # проверяем команды и делаем соответсвующие действия тут же
        if path_is_dir:
            status = 200
        elif path_is_file:
            status = 400
        else:
            status = 404
        send_response(conn, command, status=status)
        return keep_alive

    elif command == "ls":
        if path_is_file:
            send_response(conn, command, status=400)
        elif path_is_dir:
            listing = list_directory(path)  # здесь всегда валидный путь
            send_response(conn, command, listing)
        else:
            send_response(conn, command, status=404)
        return keep_alive

    elif command in ("touch", "ud"):  # по сути одно и то же действие
        shutil.copyfile(temp_path, path)
        send_response(conn, command, status=200)
        return keep_alive

    elif command == "mkdir":
        try:
            os.mkdir(path)
            status = 200
        except FileExistsError:
            status = 400
        send_response(conn, command, status=status)
        return keep_alive

    elif command == "rm":
        if path_is_file:
            os.remove(path)
            status = 200
        elif path_is_dir:
            shutil.rmtree(path)
            status = 200
        else:
            status = 404
        send_response(conn, command, status=status)
        return keep_alive

    elif command == "dd":
        if path_is_file:
            send_file(conn, path)
        elif path_is_dir:
            send_response(conn, command, status=400)
        else:
            send_response(conn, command, status=404)
        return keep_alive

    else:
        send_response(conn, command, status=400)
        return keep_alive


def get_request(
    conn: socket.socket,
) -> tuple[dict, bool, None] | tuple[dict, bool, str]:
    buffer = b""

    # Читаем заголовки тоже циклично из общего "потока" байт
    while b"\r\n\r\n" not in buffer:  # пока не встретим \r\n\r\n
        chunk = conn.recv(1024)
        if not chunk:
            return {}, None, True
        buffer += chunk

    # Берем байты заголовков в headers, а часть самого запроса (если есть) в body.
    headers_raw, body = buffer.split(b"\r\n\r\n", 1)

    # Делаем из байт заголовка словарь.
    headers = {}
    for line in headers_raw.split(b"\r\n"):
        key, value = line.split(b"=", 1)
        headers[key.decode()] = value.decode()  # как строки удобнее вроде.

    if not headers:
        return {}, None, True

    # Тут начинается чтение и сохранение body запроса (так как юзер шлет файл).
    # Байты файла сразу пишем на диск, во ВРЕМЕННЫЙ файл,
    # чтоб не грузить всё в переменную.
    # Код ниже можно выделить в отдельную функцию. Но в этом масштабе наверно не нужно.
    content_length = int(headers.get("Content-Length", "0"))
    if content_length > 8_589_934_592:  #  8 гб
        return headers, None, True

    temp_path = f"{STORAGE}/temp.tmp"
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

    return headers, temp_path, False


def handle_connection(conn: socket.socket, addr: tuple) -> None:
    try:
        keep_alive = True
        while keep_alive:
            # Через select стоим на паузе пока в conn не появятся байты для чтения.
            readable, _, _ = select.select([conn], [], [], WAIT_FOR_REQUEST)

            if (
                not readable
            ):  # Если прошло WAIT_FOR_REQUEST и нет запроса, закрываем соединение.
                raise socket.timeout("No request")
            # Ссылку на conn в readable дальше не используем, у нас итак она есть в conn.
            headers, temp_path, error = get_request(
                conn
            )  # добавил новую переменную error,
            if error:  # так как некоторые ошибки приходится сразу обрабатывать
                send_response(conn, "", status=400)
                continue
            keep_alive = handle_request(conn, headers, temp_path)

    except socket.timeout as e:
        print(f"{e}, client - {addr}")
    except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError) as e:
        print("Connection was reset...")
    except ConnectionError as e:
        print(e)
    finally:
        conn.close()
        print(f"This connection closed with client {addr}!")


def get_connections(sock: socket.socket) -> None:
    while True:
        conn, addr = sock.accept()
        conn.settimeout(TIMEOUT)
        handle_connection(conn, addr)


def main() -> None:
    with socket.socket(IPv, SOCK_TYPE) as sock:
        sock.bind((HOST, PORT))
        sock.listen(CONNECTIONS)
        print("Server started, waiting for connection...")
        get_connections(sock)


if __name__ == "__main__":
    main()
