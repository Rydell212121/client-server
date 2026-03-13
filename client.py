import os
import socket
import shlex


HOST = "127.0.0.1"
PORT = 8080

END_HEADERS = b"\r\n\r\n"
CHUNK = 4096
TIMEOUT = 5

KEEP_ALIVE = True

CURRENT_DIRECTORY = "/"

# Значения статусов по нашему протоколу.
STATUSES = {"200": "OK", "400": "Bad Request", "404": "Not Found"}


def get_current_server_path(path: str) -> str:
    """Преобразует относительный путь в полный, также меняя стиль с виндовского на линуксовый.
    Больше всего багов тут.

    Args:
        path: путь, который преобразовывается
    Returns:
        path: преобразованный путь."""

    global CURRENT_DIRECTORY  # без этого никак
    if (
        path.strip("/\\").lower() == "c:"
    ):  # преобразования пути из виндовского в линуксовый
        return "/"
    if path.lower().startswith("c:"):
        return "/" + path.removeprefix("c:").strip("/\\") + "/"
    elif path.strip("/\\") == "":
        return "/"
    elif path.startswith("/"):
        return "/" + path.strip("/\\") + "/"
    elif path == ".":
        return CURRENT_DIRECTORY
    elif path == "..":
        temp = CURRENT_DIRECTORY.removesuffix("/").split("/")[
            :-1
        ]  # "спуск на директорию ниже"
        temp.append("")
        temp = "/".join(temp)
        if temp != "":
            CURRENT_DIRECTORY = temp
            return CURRENT_DIRECTORY
        raise ValueError("You can't change directory while you in /")
    else:
        return f"{CURRENT_DIRECTORY}{path}/"


def logger(text: str) -> None:
    """Функция обертка для принтов, чтобы поддерживать разграничение ответственности
    + в будущем если делать настоящие логи так будет легче.

    Args:
        text: текст который отправляется в логи."""

    print(text)


#################  Отправка и чтение запросов ###############################
def receive_file(socket: socket.socket, local_path: str) -> dict:
    """Функция для принятия исключительно файла от сервера,
    чтоб не было переменной размером в мегабайты

    Args:
        socket: сокет, из которого берутся данные
        local_path: путь, по которому создается файл

    Returns:
        headers (dict): словарь с заголовками

    Raises:
        ConnectionError: если возникли ошибки при чтении данных из сокета
    """

    buffer = b""

    # Читаем заголовки тоже циклично из общего "потока" байт
    while b"\r\n\r\n" not in buffer:  # пока не встретим \r\n\r\n
        chunk = socket.recv(1024)
        if not chunk:
            raise ConnectionError("Connection closed while reading headers")
        buffer += chunk

    # Берем байты заголовков в headers, а часть самого запроса (если есть) в body.
    headers_raw, body = buffer.split(b"\r\n\r\n", 1)

    # Делаем из байт заголовка словарь.
    headers = {}
    for line in headers_raw.split(b"\r\n"):
        key, value = line.split(b"=", 1)
        headers[key.decode()] = value.decode()  # как строки удобнее вроде.

    content_length = int(headers.get("Content-Length", "0"))

    status = headers.get("Status")

    if status != "200":  # если были какие то ошибки, не записываем body в файл
        logger(f"Error: {STATUSES[status]}")
        return headers

    try:
        with open(local_path, "wb") as f:
            f.write(body[:content_length])  # Защита от "лишних" байт.

            while f.tell() < content_length:
                remaining = content_length - f.tell()
                chunk = socket.recv(
                    min(1024 * 1024, remaining)
                )  # Защита от "лишних" байт.
                if not chunk:
                    # Просто очистим содержимое временного файла,
                    # если передача не удалась. То что он пустой останется не страшно.
                    f.seek()
                    f.truncate()
                    raise ConnectionError("Connection closed while reading headers")
                f.write(chunk)
    except PermissionError as e:  # в редких случаях возникает, на всякий случай
        logger(e)
    else:
        logger(f"File saved as: {local_path}")

    return headers


def send_file(
    sock: socket.socket, command: str, *, file_path: str, remote_path: str
) -> None:
    """Отправляем файл без каких либо сложностей.
    Просто следуем протоколу и формируем правильные заголовки.

    Args:
        conn (socket.socket): Очевидно.
        file_path (str): Путь к файлу на устройстве клиента.
        remote_path (str): Путь к файлу на сервере (куда и под каким именем сохранять).
    """
    size = os.path.getsize(file_path)

    headers = (
        f"Keep-Alive=1\r\n"
        f"Content-Length={size}\r\n"
        f"Path={remote_path}\r\n"
        f"Command={command}\r\n"  # немного поменял протокол, вместо Content-Type теперь Command
        f"\r\n"
    ).encode()
    # Кидаем заголовки.
    sock.sendall(headers)
    # "Туда же" следом отправляем файл.
    with open(file_path, "rb") as f:
        sock.sendfile(f)


def send_request(sock: socket.socket, *, path: str, command: str, keep_alive=1) -> None:
    """Шлем запрос на сервер по типу get. Только заголовки, тела нет.

    Args:
        sock (socket.socket): Очевидно.
        path (str): Путь к директории или файлу на сервере.
        content_type (str, optional): Defaults to "text".
        keep_alive (int, optional): Defaults to 1.
    """
    headers = (
        f"Keep-Alive={keep_alive}\r\n"
        f"Command={command}\r\n"
        f"Path={path}\r\n"  # Или путь к директории или к файлу на сервере.
        f"\r\n"
    ).encode()

    sock.sendall(headers)


def _recv_exact(sock: socket.socket, *, n: int) -> bytes:
    """Вспомогательная функция, чтоб вынести часть кода отдельно.
    Прочитать ровно n байт или выбросить ошибку при EOF.
    Просто отдельно в функции дочитываем тело."""
    rest_of_body = b""
    while len(rest_of_body) < n:
        chunk = sock.recv(min(CHUNK, n - len(rest_of_body)))  # Защита от лишних байт.
        if not chunk:
            raise ConnectionError("Connection closed while reading body")
        rest_of_body += chunk
    return rest_of_body


def receive_response(sock: socket.socket) -> tuple[dict, bytes]:
    """Тут "как обычно", чтение как и у сервера происходит.

    Args:
        sock (socket.socket): .

    Raises:
        ConnectionError: Если сервер неожиданно закрылся.

    Returns:
        tuple[dict, bytes]: Заголовки и тело.
    """
    buffer = b""

    # читаем заголовки
    while END_HEADERS not in buffer:
        chunk = sock.recv(CHUNK)
        if not chunk:
            # Да, тут конечно тоже можно поднять ошибку, вы можете
            # проектирвоать как это будет удобно в анном случае.
            raise ConnectionError("Connection closed while reading headers")
        buffer += chunk

    headers_raw, body = buffer.split(END_HEADERS, 1)

    # Парсим заголовки точно так же как и на сервере всё в основном.
    headers = {}
    for line in headers_raw.split(b"\r\n"):
        key, value = line.split(b"=", 1)
        headers[key.decode()] = value.decode()

    content_length = int(headers.get("Content-Length", "0"))

    # Дочитываем тело когда оно есть.
    if len(body) >= content_length:
        body = body[:content_length]  # Защита от лишних байт.
    else:
        body = body + _recv_exact(sock, n=content_length - len(body))

    return headers, body


######################  Сценарии запросов ###################################
def change_current_directory(socket: socket.socket, *, path: str) -> bool:
    """Проверяет существует ли директория в которую мы хотим зайти,
    посылая запрос на сервер. Ошибки сохраняются в логах.

    Args:
        socket: наш сокет
        path: путь к директории, в которую мы хотим войти

    Returns:
        keep_alive (bool): всегда True
    """

    send_request(socket, path=path, command="cd")
    headers, _ = receive_response(socket)

    status = headers.get("Status")
    keep_alive = bool(int(headers.get("Keep-Alive", "0")))
    # Если ответ с статусом об ошибке, то выходим из функции.
    # Принтуем ошибку на основании статуса. body в таком случае нет.
    if status != "200":
        logger("This directory doesn't exist")
    else:
        global CURRENT_DIRECTORY  # без этого никак
        CURRENT_DIRECTORY = path

    return keep_alive


def remove(socket: socket.socket, *, path: str) -> bool:
    """Посылает команду удаление файла/директории по указаному пути.
    Обрабатывает ошибку просто логируя ее.

    Args:
        socket: сокет между клиентом и сервером
        path: путь к удаляемогу файлу/директории

    Returns:
        keep_alive (bool): всегда True
    """

    send_request(socket, path=path, command="rm")
    headers, _ = receive_response(socket)

    status = headers.get("Status")
    keep_alive = bool(int(headers.get("Keep-Alive", "0")))
    # Если ответ с статусом об ошибке, то выходим из функции.
    # Принтуем ошибку на основании статуса. body в таком случае нет.
    if status != "200":
        logger(f"Error: {STATUSES[status]}")
    else:
        logger(STATUSES[status])
        return keep_alive

    return keep_alive


def make_dir(socket: socket.socket, *, path: str) -> bool:
    """Посылает команду создать директорию по указанному пути. Ошибки идут в логи

    Args:
        socket: сокет между клиентом и сервером
        path: путь к директории, которую нужно создать

    Returns:
        keep_alive (bool): всегда True
    """

    send_request(socket, path=path, command="mkdir")
    headers, _ = receive_response(socket)

    status = headers.get("Status")
    keep_alive = bool(int(headers.get("Keep-Alive", "0")))
    # Если ответ с статусом об ошибке, то выходим из функции.
    # Принтуем ошибку на основании статуса. body в таком случае нет.
    if status != "200":
        logger(f"Error: {STATUSES[status]}")
    else:
        logger(STATUSES[status])
        return keep_alive

    return keep_alive


def make_file(socket: socket.socket, *, path: str) -> bool:
    """Отправляет команду создания файла по данному пути. Ошибки отправляются в логи.

    Args:
        socket: сокет между клиентом и сервером
        path: путь к файлу, который нужно создать

    Returns:
        keep_alive (bool): всегда True
    """

    send_request(socket, path=path, command="touch")
    headers, data = receive_response(socket)

    status = headers.get("Status")
    keep_alive = bool(int(headers.get("Keep-Alive", "0")))
    # Если ответ с статусом об ошибке, то выходим из функции.
    # Принтуем ошибку на основании статуса. body в таком случае нет.
    if status != "200":
        logger(f"Error: {STATUSES[status]}")
    else:
        logger(STATUSES[status])
        return keep_alive

    return keep_alive


def request_path(sock: socket.socket, *, path: str) -> bool:
    """Обработка ls <path> команды.
    Шлем запрос на сервер для получения содержимого директории и...
    - получаем ответ. Если статус ответа не 200, пишем о ошибке, иначе
    выводим в терминал ответ от сервера. Сервер присылает байты ответа (текста)
    в body.

    Args:
        sock (socket.socket): .
        path (str): Путь к директории на сервере.

    Returns:
        bool : Keep-Alive or not Keep-Alive.
    """
    # Шлем запрос в заголовке Path будет path для просмотра содержимого.
    send_request(sock, path=path, command="ls")
    # В data строка со списком содержимого path на сервере.
    headers, data = receive_response(sock)

    status = headers.get("Status")
    keep_alive = bool(int(headers.get("Keep-Alive", "0")))
    # Если ответ с статусом об ошибке, то выходим из функции.
    # Принтуем ошибку на основании статуса. body в таком случае нет.
    if status != "200":
        logger(f"Error: {STATUSES[status]}")
        return keep_alive

    # Работает если сервер прислал "список" содержимого директории.
    logger(data.decode(errors="ignore"))
    # Возвращаем значение заголовка будет ли дальше общение.
    return keep_alive


def download_file(sock: socket.socket, *, remote_path: str, local_path: str) -> bool:
    """Обработка dd <remote> <local> команды.
    Скачиваем файл с сервера.

    Args:
        sock (socket.socket): .
        remote_path (str): Путь к файлу на сервере, который скачивать.
        local_path (str): Куда и под каким именем загружать.

    Returns:
        bool : Keep-Alive or not Keep-Alive.
    """

    # Отправляем запрос "дай мне файл".
    send_request(sock, path=remote_path, command="dd")
    # Читаем ответ.
    headers = receive_file(sock, local_path)
    # Если ответ с статусом об ошибке, то выходим из функции.
    keep_alive = bool(int(headers.get("Keep-Alive", "0")))

    return keep_alive


def upload_file(sock: socket.socket, *, local_path: str, remote_path: str) -> bool:
    """Обработка команды ud <local> <remote>.

    Args:
        sock (socket.socket): .
        local_path (str): Путь к файлу который будет загружаться на сервер.
        remote_path (str): Куда грузить на сервер и как называется.

    Returns:
        bool : Keep-Alive or not Keep-Alive.
    """

    if not os.path.isfile(local_path):
        logger(f"File not found: {local_path}")
        return KEEP_ALIVE
    # Шлем файл на сервер и заголовки там же будут делаться:
    send_file(sock, "ud", file_path=local_path, remote_path=remote_path)
    # В ответе в body текстовое сообщение от сервера, пока так.
    headers, _ = receive_response(sock)  # body нет, заменил на _

    logger(STATUSES[headers.get("Status")])
    # Возвращаем значение заголовка будет ли дальше.
    return bool(int(headers.get("Keep-Alive", "0")))


#################  CLI и Установка соединения ###############################
def print_help() -> None:
    print("\033[1;32m", end="")  # Не удержался... Сила воли так себе xD
    print("Commands:")
    print("  ls [path]                    - list directory")
    print("  cd <path>                    - change current directory on server")
    print("  touch <path>                 - make empty file")
    print("  mkdir <path>                 - make empty dir")
    print("  rm <path>                    - remove dir or file")
    print("  dd <remote> <local>          - download file")
    print("  ud <local> <remote>          - upload file")
    print("  help                         - show this help")
    print("  exit                         - quit")
    print("")
    print("Paths with spaces use quotes:")
    print('  dd "/remote dir/file.txt" "/local path/file.txt"')
    print("---------------------------------------------------")
    print("\033[0m", end="")


def cli(sock: socket.socket) -> bool:
    """В зависимости от input клиента (пользователя) запускает
    соответствующие функции по взаимодействию с сервером.

    Args:
        sock (socket.socket): Будет пробрасываться выше по стеку.

    Raises:
        KeyboardInterrupt: Для команды exit. Тут это показалось удобным,\
            так как есть except в main.

    Returns:
        bool : Keep-Alive or not Keep-Alive.
    """
    line = input(f"{CURRENT_DIRECTORY}> ").strip()
    if not line:
        print("Was empty input.")
        return KEEP_ALIVE

    try:
        # Тут мы проверяем на правильность ввода вариантов команд.
        parts = shlex.split(line, posix=False)
        if parts[0] not in {
            "ls",
            "dd",
            "ud",
            "exit",
            "help",
            "touch",
            "mkdir",
            "rm",
            "cd",
        }:
            raise ValueError("Wrong command!")
        elif len(parts) > 3:
            raise ValueError("Too many args!")
        elif parts[0] == "ls" and len(parts) > 2:
            raise ValueError("Use ls <path>!")
        elif parts[0] in {"rm", "mkdir", "touch", "cd"} and len(parts) != 2:
            raise ValueError("Use <command> <path>.")
        elif parts[0] in ["exit", "help"] and len(parts) > 1:
            raise ValueError(
                "If u even can't ask for help or exit,\
                             maybe don't touch anything! ;D Joke! Just be more careful!)))"
            )
    # Тут же обрабатываем и после сообщения выходим из cli().
    except ValueError as e:
        print("\033[1;31m", end="")
        print("Parse error:", e)
        print("Type 'help'.")
        print("\033[0m", end="")
        return KEEP_ALIVE
    # Если всё ОК с вводом команды, то уже решаем что делать дальше.
    cmd = parts[0]
    try:
        path = get_current_server_path(parts[1].strip('"'))
    except IndexError:
        path = CURRENT_DIRECTORY
    except ValueError as e:
        logger(e)
        path = CURRENT_DIRECTORY

    if cmd == "exit":
        # Убрал тут raise KeyboardInterrupt так как уже рассказал про этот момент.
        print("\nBye!")
        return not KEEP_ALIVE

    elif cmd == "help":
        print_help()
        return KEEP_ALIVE

    elif cmd == "ls":
        keep_alive = request_path(sock, path=path)
        return keep_alive

    elif cmd == "cd":
        keep_alive = change_current_directory(sock, path=path)
        return keep_alive

    elif cmd == "touch":
        keep_alive = make_file(sock, path=path)
        return keep_alive

    elif cmd == "mkdir":
        keep_alive = make_dir(sock, path=path)
        return keep_alive

    elif cmd == "rm":
        keep_alive = remove(sock, path=path)
        return keep_alive

    elif cmd == "dd":
        keep_alive = download_file(
            sock, remote_path=path, local_path=parts[2].strip('"')
        )
        return keep_alive

    elif cmd == "ud":
        keep_alive = upload_file(
            sock,
            local_path=parts[1].strip('"'),
            remote_path=get_current_server_path(parts[2].strip('"')),
        )
        return keep_alive
    else:
        print("Unknown command.")  # правильно напоследок
        return KEEP_ALIVE


def main() -> bool:
    try:
        # keep_alive используется для двух целей:
        #    - продолжать ли общение на том же подключении (основная задача);
        #    - возвращается из функции main при завершении соединения как маркер,
        #      нужно ли переподключаться к серверу.
        keep_alive = KEEP_ALIVE

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT)
            sock.connect((HOST, PORT))

            logger("Connected to server.")

            while keep_alive:
                try:
                    keep_alive = cli(sock)
                except socket.timeout:
                    print("\033[1;31m", end="")
                    print("Timeout: server did not respond in time.")
                    print("\033[0m", end="")
                    keep_alive = (
                        True  # Оставляем True как маркер для переподключения, но
                    )
                    break  # рвем текущее подключение.
                except KeyboardInterrupt:
                    print("\nBye!")
                    keep_alive = False

    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        print("\033[1;31m", end="")
        print("Connection was reset.")
        print("\033[0m", end="")
    except ConnectionRefusedError as e:  # Сервер не работает, не удалось connect.
        print("\033[1;31m", end="")
        print(e)
        print("\033[0m", end="")
        keep_alive = False  # Переподключаться не нужно.
    except ConnectionError as e:
        print("\033[1;31m", end="")
        print("Connection error:", e)
        print("\033[0m", end="")

    # Возвращаем keep_alive УЖЕ КАК маркер нужно ли пытаться переподключаться к серверу.
    return keep_alive


if __name__ == "__main__":
    print_help()
    reconnect = True
    while reconnect:  # Цикл переподключения к серверу.
        reconnect = main()
